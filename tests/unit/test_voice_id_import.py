# pylint: disable=redefined-outer-name, protected-access
"""C-38: the Voice-ID engine imports on torchaudio 2.10, reports its state and never downloads."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.ai_core import voice_fingerprint as vf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_FILES = (
    "hyperparams.yaml",
    "embedding_model.ckpt",
    "mean_var_norm_emb.ckpt",
    "classifier.ckpt",
    "label_encoder.txt",
)
PCM_2S = b"\0\0" * 16000 * 2  # 2 s of 16 kHz int16 silence: long enough to reach verification
HEAVY_DEPS_MISSING = any(importlib.util.find_spec(name) is None for name in ("torch", "torchaudio", "speechbrain"))

# Runs in a fresh interpreter: _ensure_audio_deps() patches process globals (torchaudio, huggingface_hub,
# os.symlink on Windows) that must not leak into the rest of the test session.
IMPORT_PROBE = textwrap.dedent(
    """
    import torchaudio
    native_api = hasattr(torchaudio, "list_audio_backends")
    from services.ai_core import voice_fingerprint as vf
    vf._ensure_audio_deps()
    vf._ensure_audio_deps()  # second call is a no-op
    import speechbrain.dataio.dataio  # already loaded: its import-time list_audio_backends() call succeeded
    assert vf._AUDIO_DEPS_READY
    assert vf.SpeakerRecognition.__name__ == "SpeakerRecognition"
    assert isinstance(torchaudio.list_audio_backends(), list)
    if not native_api:
        assert torchaudio.list_audio_backends is vf._shim_list_audio_backends
    print("torchaudio", torchaudio.__version__, "native_backend_api", native_api)
    print("ok")
    """
)


def _install_fake_model(models_dir: Path, *, declares_pretrained_path: bool = True) -> Path:
    save_dir = models_dir / vf.MODEL_DIR_NAME
    save_dir.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        (save_dir / name).write_bytes(b"fake")
    hparams = "pretrained_path: speechbrain/spkrec-ecapa-voxceleb\n" if declares_pretrained_path else "n_mels: 80\n"
    (save_dir / "hyperparams.yaml").write_text(hparams, encoding="utf-8")
    return save_dir


@pytest.fixture
def models_dir(tmp_path: Path) -> Iterator[Path]:
    """An empty pretrained_models/ folder wired into config."""
    path = tmp_path / "pretrained_models"
    path.mkdir()
    with patch.object(vf.config, "pretrained_models_dir", str(path)):
        yield path


@pytest.fixture
def hub_raises() -> Iterator[tuple[MagicMock, MagicMock]]:
    """huggingface_hub patched to raise: any download attempt fails loudly and is recorded."""
    import huggingface_hub  # pylint: disable=import-outside-toplevel

    snapshot = MagicMock(side_effect=AssertionError("snapshot_download must not be called"))
    single_file = MagicMock(side_effect=AssertionError("hf_hub_download must not be called"))
    with patch.object(huggingface_hub, "snapshot_download", snapshot), patch.object(
        huggingface_hub, "hf_hub_download", single_file
    ):
        yield snapshot, single_file


@pytest.mark.skipif(HEAVY_DEPS_MISSING, reason="torch/torchaudio/speechbrain not installed")
def test_ensure_audio_deps_imports_speechbrain_with_installed_torchaudio() -> None:
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert "ok" in result.stdout.splitlines(), result.stdout[-2000:]


def test_torchaudio_shims_are_installed_before_speechbrain_is_imported() -> None:
    fake_torchaudio = types.ModuleType("torchaudio")  # like torchaudio 2.10: no global-backend API
    fake_transforms = types.ModuleType("torchaudio.transforms")
    setattr(fake_torchaudio, "transforms", fake_transforms)
    original_load = MagicMock()
    setattr(fake_torchaudio, "load", original_load)
    fake_modules = {
        "huggingface_hub": types.ModuleType("huggingface_hub"),
        "numpy": types.ModuleType("numpy"),
        "soundfile": types.ModuleType("soundfile"),
        "torch": types.ModuleType("torch"),
        "torchaudio": fake_torchaudio,
        "torchaudio.transforms": fake_transforms,
        # SpeechBrain is blocked, so the import fails exactly where it would call list_audio_backends().
        "speechbrain": None,
        "speechbrain.inference": None,
        "speechbrain.inference.speaker": None,
    }
    module_globals = {
        "_AUDIO_DEPS_READY": False,
        "np": None,
        "sf": None,
        "torch": None,
        "torchaudio": None,
        "huggingface_hub": None,
        "SpeakerRecognition": None,
    }
    with patch.dict(sys.modules, fake_modules), patch.multiple(vf, **module_globals):
        with pytest.raises(ImportError):
            vf._ensure_audio_deps()
        assert vf._AUDIO_DEPS_READY is False  # a later call retries

    # The attributes below exist only at runtime, added by the shims.
    # pylint: disable=no-member
    assert fake_torchaudio.list_audio_backends() == ["soundfile"]
    assert fake_torchaudio.get_audio_backend() == "soundfile"
    assert fake_torchaudio.set_audio_backend("soundfile") is None
    assert fake_torchaudio.load is original_load  # nothing else was patched after the failed import


def test_shims_keep_a_native_torchaudio_backend_api() -> None:
    def native() -> list[str]:
        return ["ffmpeg"]

    module = types.SimpleNamespace(list_audio_backends=native)
    vf._install_torchaudio_backend_shims(module)
    assert module.list_audio_backends is native
    assert module.get_audio_backend() == "soundfile"


@pytest.mark.parametrize("folder_exists", [True, False], ids=["empty-folder", "no-folder"])
def test_empty_pretrained_models_is_model_missing_and_never_downloads(
    tmp_path: Path, hub_raises: tuple[MagicMock, MagicMock], folder_exists: bool
) -> None:
    models_dir = tmp_path / "pretrained_models"
    if folder_exists:
        models_dir.mkdir()
    master_voice = tmp_path / "master_voice.wav"
    master_voice.write_bytes(b"RIFF")

    with patch.object(vf.config, "pretrained_models_dir", str(models_dir)):
        engine = vf.VoiceFingerprintEngine(str(master_voice))
        verdict = asyncio.run(engine.verify_bytes(PCM_2S))

    assert engine.state == vf.STATE_MODEL_MISSING == "model_missing"
    assert engine.verification is None
    assert "download_voice_model.py" in (engine.error or "")
    assert verdict == (False, 0.0)
    for download in hub_raises:
        download.assert_not_called()
    assert not (models_dir / vf.MODEL_DIR_NAME).exists()  # the old code created it before downloading


def test_partial_model_is_model_missing(models_dir: Path, hub_raises: tuple[MagicMock, MagicMock]) -> None:
    save_dir = _install_fake_model(models_dir)
    (save_dir / "classifier.ckpt").unlink()

    engine = vf.VoiceFingerprintEngine(str(models_dir / "master_voice.wav"))

    assert engine.state == "model_missing"
    assert "classifier.ckpt" in (engine.error or "")
    for download in hub_raises:
        download.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        AttributeError("module 'torchaudio' has no attribute 'list_audio_backends'"),
        KeyError("pretrained_path"),
        TypeError("unexpected keyword argument"),
    ],
    ids=["attribute-error", "key-error", "type-error"],
)
def test_init_failure_is_engine_error_not_raised(models_dir: Path, failure: Exception) -> None:
    _install_fake_model(models_dir)

    with patch.object(vf, "_ensure_audio_deps", side_effect=failure):
        engine = vf.VoiceFingerprintEngine(str(models_dir / "master_voice.wav"))

    assert engine.state == "engine_error"
    assert type(failure).__name__ in (engine.error or "")
    assert engine.verification is None
    assert asyncio.run(engine.verify_bytes(PCM_2S)) == (False, 0.0)


@pytest.mark.parametrize("enrolled, expected", [(False, "not_enrolled"), (True, "ready")])
def test_installed_model_loads_from_the_local_folder_only(
    models_dir: Path, tmp_path: Path, hub_raises: tuple[MagicMock, MagicMock], enrolled: bool, expected: str
) -> None:
    save_dir = _install_fake_model(models_dir)
    master_voice = tmp_path / "master_voice.wav"
    if enrolled:
        master_voice.write_bytes(b"RIFF")
    fake_torch = MagicMock()
    # Already fixed by another torch user in the same process: must not stop the engine from loading.
    fake_torch.set_num_interop_threads.side_effect = RuntimeError("cannot set number of interop threads")
    fake_speaker_recognition = MagicMock()

    with patch.object(vf, "_ensure_audio_deps"), patch.multiple(
        vf, torch=fake_torch, SpeakerRecognition=fake_speaker_recognition
    ):
        engine = vf.VoiceFingerprintEngine(str(master_voice))

    assert engine.state == expected
    assert engine.error is None
    assert Path(engine.save_dir) == save_dir
    assert engine.verification is fake_speaker_recognition.from_hparams.return_value
    fake_speaker_recognition.from_hparams.assert_called_once_with(
        source=engine.save_dir,
        savedir=engine.save_dir,
        run_opts={"device": "cpu"},
        overrides={"pretrained_path": save_dir.as_posix()},
    )
    fake_torch.set_num_threads.assert_called_once_with(1)
    assert engine._pending_enroll == (str(master_voice) if enrolled else None)
    for download in hub_raises:
        download.assert_not_called()


def test_pretrained_path_override_only_when_hparams_declare_it(models_dir: Path) -> None:
    save_dir = _install_fake_model(models_dir, declares_pretrained_path=False)
    assert not vf._local_model_overrides(str(save_dir))

    (save_dir / "hyperparams.yaml").write_text("pretrained_path: speechbrain/spkrec-ecapa-voxceleb\n", encoding="utf-8")
    assert vf._local_model_overrides(str(save_dir)) == {"pretrained_path": save_dir.as_posix()}


def test_failed_lazy_enrollment_turns_ready_into_engine_error(models_dir: Path, tmp_path: Path) -> None:
    _install_fake_model(models_dir)
    master_voice = tmp_path / "master_voice.wav"
    master_voice.write_bytes(b"RIFF")
    with patch.object(vf, "_ensure_audio_deps"), patch.multiple(vf, torch=MagicMock(), SpeakerRecognition=MagicMock()):
        engine = vf.VoiceFingerprintEngine(str(master_voice))
    assert engine.state == "ready"

    with patch.object(engine, "_get_embedding", return_value=None):
        assert asyncio.run(engine.verify_bytes(PCM_2S)) == (False, 0.0)

    assert engine.state == "engine_error"
    assert "enrollment failed" in (engine.error or "")


def test_lazy_engine_reports_state_without_loading_the_model(models_dir: Path, tmp_path: Path) -> None:
    master_voice = tmp_path / "master_voice.wav"
    lazy = vf.LazyVoiceFingerprintEngine(str(master_voice))

    with patch.object(vf, "VoiceFingerprintEngine") as engine_cls:
        assert lazy.state == "model_missing"
        assert "download_voice_model.py" in (lazy.error or "")
        _install_fake_model(models_dir)
        assert lazy.state == "not_enrolled"
        master_voice.write_bytes(b"RIFF")
        assert lazy.state == "ready"
        assert lazy.error is None
        assert lazy.is_loaded is False
        engine_cls.assert_not_called()


def test_lazy_engine_with_empty_models_folder_denies_without_download(
    models_dir: Path, tmp_path: Path, hub_raises: tuple[MagicMock, MagicMock]
) -> None:
    lazy = vf.LazyVoiceFingerprintEngine(str(tmp_path / "master_voice.wav"))

    assert asyncio.run(lazy.verify_bytes(PCM_2S)) == (False, 0.0)

    assert lazy.is_loaded is True
    assert lazy.state == "model_missing"
    for download in hub_raises:
        download.assert_not_called()


def test_lazy_verify_bytes_never_raises_into_the_turn_path(models_dir: Path, tmp_path: Path) -> None:
    lazy = vf.LazyVoiceFingerprintEngine(str(tmp_path / "master_voice.wav"))

    with patch.object(vf, "VoiceFingerprintEngine", side_effect=AttributeError("speechbrain exploded")):
        assert asyncio.run(lazy.verify_bytes(PCM_2S)) == (False, 0.0)
