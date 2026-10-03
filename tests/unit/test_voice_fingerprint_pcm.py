# pylint: disable=redefined-outer-name, protected-access
"""C-72: the Voice-ID engine API (K-64 CT-VC-VOICE-ID-STORE): in-memory verify_pcm, enroll, reload, prewarm.

The SpeechBrain model is never loaded or downloaded here: SpeakerRecognition is a fake whose
encode_batch returns a small embedding computed from the signal, so scores are real cosine values.
"""

from __future__ import annotations

import array
import asyncio
import collections
import importlib.util
import os
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy
import pytest

from services.ai_core import voice_fingerprint as vf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HEAVY_DEPS_MISSING = any(importlib.util.find_spec(name) is None for name in ("torch", "torchaudio", "soundfile"))
needs_torch = pytest.mark.skipif(HEAVY_DEPS_MISSING, reason="torch/torchaudio/soundfile not installed")

MODEL_FILES = ("hyperparams.yaml", "embedding_model.ckpt", "mean_var_norm_emb.ckpt", "classifier.ckpt", "label_encoder.txt")
NEGATIVE_SAMPLES = [-32768, -12000, -1, 0, 1, 12000, 32767]


def _tone(seconds: float, rate: int, freq: float, seed: int = 0) -> numpy.ndarray:
    """int16 tone with a little noise; contains negative samples."""
    rng = numpy.random.default_rng(seed)
    t = numpy.arange(int(seconds * rate)) / rate
    signal = 0.4 * numpy.sin(2 * numpy.pi * freq * t) + 0.02 * rng.standard_normal(t.size)
    return (signal * 32767).astype(numpy.int16)


class FakeEncoder:
    """Stands in for SpeakerRecognition: an embedding from simple signal statistics."""

    def __init__(self) -> None:
        self.lengths: list[int] = []
        self.threads: list[int] = []  # torch.get_num_threads() seen by each call, in its own thread
        self.single_thread_delay = 0.0  # seconds added when called with one torch thread
        self.calls = 0

    def encode_batch(self, wavs):  # noqa: ANN001 - torch tensor
        import torch  # pylint: disable=import-outside-toplevel

        self.calls += 1
        self.lengths.append(int(wavs.shape[-1]))
        self.threads.append(int(torch.get_num_threads()))
        if self.single_thread_delay and self.threads[-1] == 1:
            time.sleep(self.single_thread_delay)
        x = wavs.reshape(-1).double()
        crossings = (x[1:] * x[:-1] < 0).double().mean()
        feats = torch.stack([x.abs().mean(), x.std(), crossings * 10, x.max() - x.min(), torch.tensor(0.05, dtype=x.dtype)])
        return feats.reshape(1, 1, -1).float()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """A fake installed model, real numpy/torch/torchaudio, identity and backups under tmp_path."""
    if HEAVY_DEPS_MISSING:
        pytest.skip("torch/torchaudio/soundfile not installed")
    import soundfile  # pylint: disable=import-outside-toplevel
    import torch  # pylint: disable=import-outside-toplevel
    import torchaudio  # pylint: disable=import-outside-toplevel
    import torchaudio.functional  # pylint: disable=import-outside-toplevel,unused-import

    models = tmp_path / "pretrained_models"
    save_dir = models / vf.MODEL_DIR_NAME
    save_dir.mkdir(parents=True)
    for name in MODEL_FILES:
        (save_dir / name).write_bytes(b"fake")
    identity = tmp_path / "data" / "identity"
    backups = tmp_path / "backups"
    encoder = FakeEncoder()
    speaker_recognition = MagicMock()
    speaker_recognition.from_hparams.return_value = encoder
    notify = AsyncMock()
    with patch.object(vf.config, "pretrained_models_dir", str(models)), patch.object(
        vf.config, "backups_dir", str(backups)
    ), patch.object(vf, "_ensure_audio_deps"), patch.object(vf, "_limit_torch_threads"), patch.object(
        vf, "_notify_score", notify
    ), patch.multiple(
        vf, np=numpy, torch=torch, torchaudio=torchaudio, sf=soundfile, SpeakerRecognition=speaker_recognition
    ):
        yield SimpleNamespace(
            encoder=encoder,
            identity=identity,
            master=identity / "master_voice.wav",
            backups=backups,
            notify=notify,
            torch=torch,
        )


def _write_wav(path: Path, samples: numpy.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.astype("<i2").tobytes())


# ---------------------------------------------------------------- PCM conversion (negative int16)


def test_negative_int16_samples_convert_from_every_buffer_shape() -> None:
    expected = numpy.array(NEGATIVE_SAMPLES, dtype=numpy.float32) / 32768.0
    as_array = array.array("h", NEGATIVE_SAMPLES)
    as_bytes = as_array.tobytes()
    frame_view = memoryview(as_bytes).cast("B").cast("h")  # what livekit AudioFrame.data returns
    as_deque: collections.deque[int] = collections.deque(maxlen=96000)
    as_deque.extend(frame_view)  # the agent_core audio buffer

    with pytest.raises(ValueError):
        bytes(as_deque)  # the old payload conversion: bytes must be in range(0, 256)

    for pcm in (as_bytes, bytearray(as_bytes), memoryview(as_bytes), frame_view, as_array, as_deque,
                list(NEGATIVE_SAMPLES), numpy.array(NEGATIVE_SAMPLES, dtype=numpy.int16)):
        converted = vf._pcm_to_float32(pcm)
        assert converted.dtype == numpy.float32
        numpy.testing.assert_allclose(converted, expected)
        assert converted.min() == -1.0


def test_pcm_conversion_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        vf._pcm_to_float32([0, 40000])
    with pytest.raises(ValueError):
        vf._pcm_to_float32(collections.deque([0, -40000]))
    numpy.testing.assert_allclose(vf._pcm_to_float32([0.0, 16384.0]), numpy.array([0.0, 0.5], dtype=numpy.float32))
    with patch.object(numpy, "fromiter", side_effect=AssertionError("per-sample Python loop")):
        assert vf._pcm_to_float32(collections.deque(range(-5, 5))).size == 10  # C-level conversion
    with pytest.raises(TypeError):
        vf._pcm_to_float32("not audio")
    odd = array.array("h", [-5, 5]).tobytes() + b"\x01"  # a trailing half sample is dropped
    numpy.testing.assert_allclose(vf._pcm_to_float32(odd), numpy.array([-5, 5], dtype=numpy.float32) / 32768.0)
    stereo = numpy.array([[1000, -1000], [2000, -2000], [3000, -3000]], dtype=numpy.int16)
    numpy.testing.assert_allclose(vf._pcm_to_float32(stereo), numpy.zeros(3, dtype=numpy.float32))


def test_threshold_defaults_to_the_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(vf.THRESHOLD_ENV, raising=False)
    assert vf.resolve_threshold() == 0.45
    monkeypatch.setenv(vf.THRESHOLD_ENV, "0.6")
    assert vf.resolve_threshold() == 0.6
    assert vf.resolve_threshold(0.7) == 0.7
    monkeypatch.setenv(vf.THRESHOLD_ENV, "high")
    assert vf.resolve_threshold() == vf.DEFAULT_THRESHOLD


# ---------------------------------------------------------------- enroll


@needs_torch
def test_enroll_writes_the_npy_samples_and_master_voice(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert engine.state == "not_enrolled"
    assert engine.is_enrolled is False

    samples = [(_tone(1.5, 48000, 180 + 20 * i, seed=i).tobytes(), 48000) for i in range(3)]
    result = asyncio.run(engine.enroll(samples))

    assert result["ok"] is True and result["samples"] == 3
    assert isinstance(result["self_score"], float) and 0.9 < result["self_score"] <= 1.0
    stored = numpy.load(env.identity / "master_embedding.npy")
    assert stored.ndim == 1
    assert abs(float(numpy.linalg.norm(stored)) - 1.0) < 1e-5
    assert sorted(p.name for p in (env.identity / "samples").iterdir()) == ["sample_01.wav", "sample_02.wav", "sample_03.wav"]
    with wave.open(str(env.master), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)
        assert wf.getnframes() == 3 * 24000  # 1.5 s each, resampled to 16 kHz and concatenated
    assert engine.state == "ready" and engine.is_enrolled is True
    assert not env.backups.exists()  # nothing to back up the first time
    assert set(env.encoder.lengths) == {24000}  # every sample was resampled to 16 kHz before encoding


@needs_torch
def test_second_enroll_backs_up_the_previous_identity(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    first = [(_tone(1.2, 16000, 200).tobytes(), 16000)]
    second = [(_tone(1.2, 16000, 320, seed=5).tobytes(), 16000)]
    assert asyncio.run(engine.enroll(first))["ok"]
    old_embedding = numpy.load(env.identity / "master_embedding.npy")
    assert asyncio.run(engine.enroll(second))["ok"]

    backups = list((env.backups / "identity").iterdir())
    assert len(backups) == 1
    numpy.testing.assert_allclose(numpy.load(backups[0] / "master_embedding.npy"), old_embedding)
    assert (backups[0] / "master_voice.wav").is_file()
    assert (backups[0] / "samples" / "sample_01.wav").is_file()


@needs_torch
def test_enroll_rejects_short_samples_and_a_missing_model(env: SimpleNamespace, tmp_path: Path) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    result = asyncio.run(engine.enroll([(_tone(0.4, 16000, 200).tobytes(), 16000)]))
    assert result["ok"] is False and "shorter" in result["error"]
    assert asyncio.run(engine.enroll([]))["ok"] is False
    assert not env.identity.exists()

    with patch.object(vf.config, "pretrained_models_dir", str(tmp_path / "empty")):
        missing = vf.VoiceFingerprintEngine(str(env.master))
    result = asyncio.run(missing.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))
    assert result["ok"] is False and "model_missing" in result["error"]


@needs_torch
def test_loading_prefers_the_npy_and_reload_picks_up_new_files(env: SimpleNamespace) -> None:
    enroller = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(enroller.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]

    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert engine.state == "ready"
    assert engine._pending_enroll is None  # the .npy was loaded; master_voice.wav is not re-embedded
    numpy.testing.assert_allclose(engine.master_embedding, enroller.master_embedding, atol=1e-6)

    (env.identity / "master_embedding.npy").unlink()
    assert engine.reload() == "ready"
    assert engine._pending_enroll == str(env.master)  # falls back to master_voice.wav

    env.master.unlink()
    for sample in (env.identity / "samples").iterdir():
        sample.unlink()
    assert engine.reload() == "not_enrolled"


@needs_torch
def test_unreadable_npy_without_master_voice_is_engine_error(env: SimpleNamespace) -> None:
    env.identity.mkdir(parents=True)
    (env.identity / "master_embedding.npy").write_bytes(b"not a numpy file")
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert engine.state == "engine_error"
    assert "unreadable" in (engine.error or "")


# ---------------------------------------------------------------- verify_pcm


@needs_torch
def test_verify_pcm_scores_negative_samples_in_memory(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(vf.THRESHOLD_ENV, raising=False)
    engine = vf.VoiceFingerprintEngine(str(env.master))
    voice = _tone(1.5, 48000, 210, seed=1)
    assert asyncio.run(engine.enroll([(voice.tobytes(), 48000)]))["ok"]
    env.encoder.lengths.clear()

    frame_view = memoryview(voice.tobytes()).cast("B").cast("h")
    buffer: collections.deque[int] = collections.deque(frame_view)
    with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("no temp files")), patch.object(
        env.torch, "load", side_effect=AssertionError("no torch.load")
    ), patch.object(vf.torchaudio, "load", side_effect=AssertionError("no torchaudio.load")):
        accepted, score = asyncio.run(engine.verify_pcm(buffer, 48000))
        other_accepted, other_score = asyncio.run(
            engine.verify_pcm(_tone(1.5, 16000, 3000, seed=9).tobytes(), 16000, threshold=0.999)
        )

    assert isinstance(score, float) and isinstance(accepted, bool)
    assert accepted is True and score > 0.99
    assert isinstance(other_score, float) and other_score < score and other_accepted is False
    assert env.encoder.lengths == [24000, 24000]  # 48 kHz and 16 kHz input both reach the model at 16 kHz
    assert env.notify.await_count == 2


@needs_torch
def test_verify_pcm_denies_without_enrollment_or_with_short_audio(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(engine.verify_pcm(_tone(1.5, 16000, 200).tobytes(), 16000)) == (False, 0.0)
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]
    assert asyncio.run(engine.verify_pcm(_tone(0.2, 16000, 200).tobytes(), 16000)) == (False, 0.0)
    assert asyncio.run(engine.verify_pcm(b"\0\0" * 16000, 0)) == (False, 0.0)


@needs_torch
def test_verify_pcm_never_raises_when_the_model_fails(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]
    with patch.object(env.encoder, "encode_batch", side_effect=RuntimeError("boom")):
        assert asyncio.run(engine.verify_pcm(_tone(1.2, 16000, 200).tobytes(), 16000)) == (False, 0.0)


@needs_torch
def test_lazy_enrollment_from_master_voice_wav_uses_memory_decoding(env: SimpleNamespace) -> None:
    voice = _tone(2.0, 16000, 190, seed=3)
    _write_wav(env.master, voice, 16000)
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert engine._pending_enroll == str(env.master)

    accepted, score = asyncio.run(engine.verify_bytes(voice.tobytes(), 16000, threshold=0.65))

    assert accepted is True and score > 0.99
    assert engine._pending_enroll is None and engine.master_embedding is not None
    assert sorted(p.name for p in env.identity.iterdir()) == ["master_voice.wav"]  # no .converted.wav or temp file


# ---------------------------------------------------------------- wrappers


@needs_torch
def test_verify_bytes_and_verify_segment_wrap_verify_pcm(env: SimpleNamespace, tmp_path: Path) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]
    voice = _tone(1.5, 16000, 200)
    segment = tmp_path / "segment.wav"
    _write_wav(segment, voice, 16000)

    with patch.object(engine, "verify_pcm", AsyncMock(return_value=(True, 0.9))) as verify_pcm:
        assert asyncio.run(engine.verify_bytes(voice.tobytes(), sample_rate=16000, threshold=0.65)) == (True, 0.9)
        args = verify_pcm.await_args.args
        assert args == (voice.tobytes(), 16000, 0.65)

        assert asyncio.run(engine.verify_segment(str(segment), threshold=0.5)) == (True, 0.9)
        signal, rate, threshold = verify_pcm.await_args.args
        assert (rate, threshold) == (16000, 0.5)
        numpy.testing.assert_allclose(signal, voice.astype(numpy.float32) / 32768.0)

        # verify_bytes keeps its 1 s minimum before reaching verify_pcm.
        assert asyncio.run(engine.verify_bytes(voice[:8000].tobytes(), sample_rate=16000)) == (False, 0.0)
        assert verify_pcm.await_count == 2


@needs_torch
def test_verify_bytes_default_threshold_is_unchanged(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    with patch.object(engine, "verify_pcm", AsyncMock(return_value=(False, 0.1))) as verify_pcm:
        asyncio.run(engine.verify_bytes(b"\0\0" * 16000))
    assert verify_pcm.await_args.args[2] == 0.70


# ---------------------------------------------------------------- lazy engine, prewarm, threads


def test_prewarm_without_the_model_logs_once_and_keeps_the_lazy_path(tmp_path: Path) -> None:
    lazy = vf.LazyVoiceFingerprintEngine(str(tmp_path / "master_voice.wav"))
    with patch.object(vf.config, "pretrained_models_dir", str(tmp_path / "none")), patch.object(
        vf, "VoiceFingerprintEngine"
    ) as engine_cls, patch.object(vf.logger, "warning") as warning:
        first = lazy.prewarm()
        second = lazy.prewarm()
    assert first.done() and first.result() is False
    assert second.done() and second.result() is False
    assert warning.call_count == 1 and "VOICE_ID prewarm skipped" in warning.call_args.args[0]
    engine_cls.assert_not_called()
    assert lazy.is_loaded is False


@needs_torch
def test_prewarm_loads_in_a_background_thread_and_logs_ready(env: SimpleNamespace) -> None:
    _write_wav(env.master, _tone(1.5, 16000, 200), 16000)
    lazy = vf.LazyVoiceFingerprintEngine(str(env.master))
    gate = threading.Event()
    real_engine_cls = vf.VoiceFingerprintEngine
    thread_names: list[str] = []

    def slow_engine(path: str) -> vf.VoiceFingerprintEngine:
        thread_names.append(threading.current_thread().name)
        assert gate.wait(10)
        return real_engine_cls(path)

    with patch.object(vf, "VoiceFingerprintEngine", side_effect=slow_engine), patch.object(
        real_engine_cls, "calibrate_threads", return_value=(1, 50)
    ) as calibrate, patch.object(vf.logger, "info") as info:
        future = lazy.prewarm()
        assert lazy.prewarm() is future
        assert not future.done()  # prewarm returned while the model is still loading
        gate.set()
        assert future.result(timeout=30) is True

    assert thread_names == ["jarvis-voice-id-prewarm"]
    assert any(call.args[0] == "VOICE_ID ready in %.1fs" for call in info.call_args_list)
    calibrate.assert_called_once()
    assert lazy.is_loaded and lazy.state == "ready"
    assert lazy._engine.master_embedding is not None  # the wav enrollment happened off the loop too


@needs_torch
def test_prewarm_without_enrollment_does_not_log_ready(env: SimpleNamespace) -> None:
    lazy = vf.LazyVoiceFingerprintEngine(str(env.master))  # model present, nothing enrolled
    with patch.object(vf.VoiceFingerprintEngine, "calibrate_threads", return_value=(1, 50)), patch.object(
        vf.logger, "info"
    ) as info, patch.object(vf.logger, "warning") as warning:
        assert lazy.prewarm().result(timeout=30) is False
    assert not any(str(call.args[0]).startswith("VOICE_ID ready") for call in info.call_args_list)
    warning.assert_any_call("VOICE_ID model loaded in %.1fs state=%s", pytest.approx(0.0, abs=30), "not_enrolled")
    assert lazy.state == "not_enrolled"


@needs_torch
def test_failed_identity_write_restores_the_previous_identity(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)] * 2))["ok"]
    files = [env.identity / "master_embedding.npy", env.master, env.identity / "samples" / "sample_01.wav"]
    before = {path: path.read_bytes() for path in files}
    old_master = engine.master_embedding.copy()
    real_replace = os.replace
    failed: list[str] = []

    def replace(src, dst):  # noqa: ANN001 - os.replace signature
        if str(dst).endswith(".npy") and not failed:  # the new .npy fails once, after the wavs moved in
            failed.append(str(dst))
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    with patch.object(vf.os, "replace", side_effect=replace):
        result = asyncio.run(engine.enroll([(_tone(1.5, 16000, 330, seed=4).tobytes(), 16000)]))

    assert result["ok"] is False and "No space left" in result["error"]
    assert {path: path.read_bytes() for path in files} == before  # wav, samples and .npy agree again
    assert sorted(p.name for p in (env.identity / "samples").iterdir()) == ["sample_01.wav", "sample_02.wav"]
    assert not [p for p in env.identity.rglob("*") if p.name.endswith((".staged", ".tmp"))]
    numpy.testing.assert_allclose(engine.master_embedding, old_master)  # memory kept the old identity too


@needs_torch
def test_first_enroll_write_failure_leaves_no_partial_identity(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    with patch.object(vf.os, "fsync", side_effect=OSError(28, "No space left on device")):
        result = asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))
    assert result["ok"] is False
    assert not env.master.exists() and not (env.identity / "master_embedding.npy").exists()
    assert not list((env.identity / "samples").iterdir())
    assert engine.state == "not_enrolled" and engine.master_embedding is None


@needs_torch
def test_calibrate_threads_keeps_the_faster_setting(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    durations = {1: 120.0, 2: 80.0}

    def bench(_pcm: bytes, _rate: int, threads: int) -> float:
        return durations[threads]

    with patch.object(engine, "_bench_once", side_effect=bench), patch("os.cpu_count", return_value=4), patch.object(
        vf.logger, "info"
    ) as info:
        assert engine.calibrate_threads() == (2, 80)
    assert engine.torch_threads == 2 and engine.p95_ms == 80
    info.assert_any_call("VOICE_ID threads=%d p95_ms=%d", 2, 80)

    durations.update({1: 80.0, 2: 80.0})  # a tie keeps one thread
    with patch.object(engine, "_bench_once", side_effect=bench), patch("os.cpu_count", return_value=4):
        assert engine.calibrate_threads() == (1, 80)
    assert engine.torch_threads == 1


@needs_torch
def test_calibrated_thread_count_reaches_verification_on_a_used_executor_thread(env: SimpleNamespace) -> None:
    """torch thread counts are per thread: the count chosen in the prewarm thread must still be the one
    ECAPA runs with on an executor thread that already used torch (vector memory shares that executor)."""
    torch = env.torch
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]

    # Calibrate in its own thread, as prewarm does; one thread is made slow so two threads win.
    env.encoder.single_thread_delay = 0.02
    env.encoder.threads.clear()
    result: list = []
    prewarm_counts: list[tuple[int, int]] = []

    def calibrate() -> None:
        before = torch.get_num_threads()
        result.append(engine.calibrate_threads())
        prewarm_counts.append((before, torch.get_num_threads()))

    with patch("os.cpu_count", return_value=4):
        worker = threading.Thread(target=calibrate)
        worker.start()
        worker.join(60)
    assert result and result[0][0] == 2 and engine.torch_threads == 2
    assert set(env.encoder.threads) == {1, 2}  # each candidate really ran with its own count
    assert prewarm_counts[0][0] == prewarm_counts[0][1]  # the calibrating thread keeps its count
    env.encoder.single_thread_delay = 0.0
    env.encoder.threads.clear()

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shared-default")
    used_count = 3

    def touch_torch() -> int:  # e.g. a vector-memory recall that ran before calibration finished
        torch.set_num_threads(used_count)
        torch.ones(8).sum()
        return threading.get_ident()

    try:
        executor_thread = pool.submit(touch_torch).result(10)

        async def verify_on_the_shared_executor() -> tuple[tuple[bool, float], int, int]:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(pool)
            verdict = await engine.verify_pcm(_tone(1.5, 16000, 200).tobytes(), 16000)
            ident, after = await loop.run_in_executor(None, lambda: (threading.get_ident(), torch.get_num_threads()))
            return verdict, ident, after

        verdict, ident, after = asyncio.run(verify_on_the_shared_executor())
    finally:
        pool.shutdown(wait=True)

    assert ident == executor_thread  # verification ran on the pre-initialized thread
    assert verdict[0] is True
    assert env.encoder.threads == [engine.torch_threads]  # ECAPA ran with the calibrated count there
    assert after == used_count  # and that thread's own count was restored for its other torch users


@needs_torch
def test_inference_uses_one_thread_before_calibration(env: SimpleNamespace) -> None:
    engine = vf.VoiceFingerprintEngine(str(env.master))
    assert engine.torch_threads is None
    assert asyncio.run(engine.enroll([(_tone(1.2, 16000, 200).tobytes(), 16000)]))["ok"]
    assert asyncio.run(engine.verify_pcm(_tone(1.2, 16000, 200).tobytes(), 16000))[0] is True
    assert env.encoder.threads and set(env.encoder.threads) == {1}


def test_p95_is_nearest_rank() -> None:
    assert vf._p95_ms([10.0] * 19 + [500.0]) == 10
    assert vf._p95_ms([float(v) for v in range(1, 11)]) == 10


@needs_torch
def test_lazy_engine_api_never_raises(tmp_path: Path) -> None:
    lazy = vf.LazyVoiceFingerprintEngine(str(tmp_path / "master_voice.wav"))
    with patch.object(vf, "VoiceFingerprintEngine", side_effect=AttributeError("speechbrain exploded")):
        assert asyncio.run(lazy.verify_pcm(b"\0\0" * 16000, 16000)) == (False, 0.0)
        assert asyncio.run(lazy.verify_segment(str(tmp_path / "x.wav"))) == (False, 0.0)
        result = asyncio.run(lazy.enroll([(b"\0\0" * 16000, 16000)]))
    assert result["ok"] is False and "AttributeError" in result["error"]
    assert lazy.reload() == lazy.state  # nothing loaded: a file check only


def test_lazy_is_enrolled_counts_the_npy_without_loading(tmp_path: Path) -> None:
    master = tmp_path / "identity" / "master_voice.wav"
    lazy = vf.LazyVoiceFingerprintEngine(str(master))
    with patch.object(vf, "VoiceFingerprintEngine") as engine_cls:
        assert lazy.is_enrolled is False
        master.parent.mkdir(parents=True)
        numpy.save(master.parent / "master_embedding.npy", numpy.ones(4, dtype=numpy.float32))
        assert lazy.is_enrolled is True
        engine_cls.assert_not_called()


# ---------------------------------------------------------------- download script


def _load_download_script():
    spec = importlib.util.spec_from_file_location(
        "jarvis_download_voice_model_c72", PROJECT_ROOT / "scripts" / "models" / "download_voice_model.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_download_script_targets_the_config_folder_and_checks_the_files(tmp_path: Path) -> None:
    module = _load_download_script()
    assert module.TARGET_DIR == Path(vf.config.pretrained_models_dir) / vf.MODEL_DIR_NAME

    target = tmp_path / vf.MODEL_DIR_NAME
    calls: list[dict] = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return kwargs["local_dir"]

    with patch.object(module, "snapshot_download", side_effect=fake_download), patch.object(module, "TARGET_DIR", target):
        assert module.main() == 1  # nothing was downloaded: the check reports the missing files
        for name in MODEL_FILES:
            (target / name).write_bytes(b"x")
        assert module.main() == 0
    assert calls == [{"repo_id": "speechbrain/spkrec-ecapa-voxceleb", "local_dir": str(target)}] * 2


def test_importing_the_engine_downloads_nothing() -> None:
    source = (PROJECT_ROOT / "services" / "ai_core" / "voice_fingerprint.py").read_text(encoding="utf-8")
    assert "snapshot_download(repo_id" not in source and "hf_hub_download(repo_id" not in source
    assert "tempfile" not in source and "NamedTemporaryFile" not in source
    assert os.path.basename(vf.MASTER_VOICE_PATH) == "master_voice.wav"


def test_job_start_hook_prewarms_only_the_jarvis_room_job_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import agent_runner  # pylint: disable=import-outside-toplevel

    calls: list[str] = []
    monkeypatch.setattr(vf, "voice_id_engine", SimpleNamespace(prewarm=lambda: calls.append("prewarm")))
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.delenv("JARVIS_ROOM_NAME", raising=False)
    agent_runner._prewarm_voice_id("jarvis-canary-1700000000")  # canary/probe jobs on the VPS: no ECAPA
    assert calls == []
    agent_runner._prewarm_voice_id("jarvis-hybrid")
    assert calls == ["prewarm"]
    monkeypatch.delenv("JARVIS_DESKTOP_MODE")
    agent_runner._prewarm_voice_id("jarvis-probe-1700000000")  # the PC brain: every job, as the lazy path
    assert calls == ["prewarm", "prewarm"]

    def boom() -> None:
        raise RuntimeError("no thread")

    monkeypatch.setattr(vf, "voice_id_engine", SimpleNamespace(prewarm=boom))
    with patch.object(agent_runner.logger, "warning") as warning:
        agent_runner._prewarm_voice_id("jarvis-hybrid")  # the job start goes on
    assert warning.call_args[0][0] == "VOICE_ID prewarm not started: %s"
