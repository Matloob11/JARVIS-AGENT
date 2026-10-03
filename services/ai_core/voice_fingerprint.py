"""
# voice_fingerprint.py
Speaker Identification engine for JARVIS using SpeechBrain.

The engine never downloads its model: scripts/models/download_voice_model.py is the only
download path for pretrained_models/spkrec-ecapa-voxceleb. The outcome of loading is exposed as
``state`` (with a human-readable ``error`` for the failure states):

* ``model_missing`` - the model files are not installed; run the download script.
* ``not_enrolled``  - the model is loaded but no master voice is enrolled yet.
* ``ready``         - the model is loaded and the master voice is available for verification.
* ``engine_error``  - importing, loading or enrolling failed.

Engine API (K-64 CT-VC-VOICE-ID-STORE): ``verify_pcm(pcm, sample_rate, threshold=None)`` scores
16-bit PCM in memory (no temp files, no torchaudio.load) against the L2-normalized master embedding;
``enroll(samples)`` writes data/identity/master_embedding.npy, samples/*.wav and master_voice.wav;
``is_enrolled``; ``reload()``. ``verify_bytes`` and ``verify_segment`` are wrappers of verify_pcm.
``voice_id_engine.prewarm()`` loads the model in a background thread and never blocks.
"""

from __future__ import annotations

import array
import asyncio
import concurrent.futures
import contextlib
import io
import math
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("VOICE-ID")

np: Any = None
sf: Any = None
torch: Any = None
torchaudio: Any = None
huggingface_hub: Any = None
SpeakerRecognition: Any = None
_AUDIO_DEPS_READY = False
_AUDIO_DEPS_LOCK = threading.Lock()

# Engine states (CT-VC-VOICE-ID-STORE).
STATE_MODEL_MISSING = "model_missing"
STATE_NOT_ENROLLED = "not_enrolled"
STATE_READY = "ready"
STATE_ENGINE_ERROR = "engine_error"
VOICE_ID_STATES = (STATE_MODEL_MISSING, STATE_NOT_ENROLLED, STATE_READY, STATE_ENGINE_ERROR)

MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
MODEL_DIR_NAME = "spkrec-ecapa-voxceleb"
MODEL_DOWNLOAD_HINT = "Run scripts/models/download_voice_model.py to install it."

# Identity store (CT-VC-VOICE-ID-STORE): next to master_voice.wav in data/identity/.
MASTER_VOICE_FILE = "master_voice.wav"
EMBEDDING_FILE = "master_embedding.npy"
SAMPLES_DIR_NAME = "samples"

TARGET_SAMPLE_RATE = 16000
MIN_VERIFY_SECONDS = 0.5  # verify_pcm; verify_bytes keeps its own 1 s minimum
MIN_ENROLL_SECONDS = 1.0
THRESHOLD_ENV = "JARVIS_VOICE_ID_THRESHOLD"
DEFAULT_THRESHOLD = 0.45  # K-69
_THRESHOLD_WARNED = False

# Torch thread calibration in prewarm: verify_pcm's work on 3 s of 48 kHz audio, 10 timed runs each.
THREAD_CHOICES = (1, 2)
BENCH_SAMPLE_RATE = 48000
BENCH_SECONDS = 3.0
BENCH_RUNS = 10

# Files SpeakerRecognition.from_hparams loads from the local model folder. Each entry lists the
# accepted names: snapshot_download ships label_encoder.txt, SpeechBrain's collected copy is
# label_encoder.ckpt.
_MODEL_FILES: tuple[tuple[str, ...], ...] = (
    ("hyperparams.yaml",),
    ("embedding_model.ckpt",),
    ("mean_var_norm_emb.ckpt",),
    ("classifier.ckpt",),
    ("label_encoder.txt", "label_encoder.ckpt"),
)


def _model_dir() -> str:
    return os.path.join(config.pretrained_models_dir, MODEL_DIR_NAME)


def _missing_model_files(save_dir: str) -> list[str]:
    """Required model files absent from ``save_dir`` (file checks only, no heavy imports)."""
    return [
        " or ".join(names)
        for names in _MODEL_FILES
        if not any(os.path.isfile(os.path.join(save_dir, name)) for name in names)
    ]


def _model_missing_message(save_dir: str, missing: list[str]) -> str:
    return f"Voice-ID model not installed in {save_dir} (missing: {', '.join(missing)}). {MODEL_DOWNLOAD_HINT}"


def _embedding_path(master_voice_path: str) -> str:
    """data/identity/master_embedding.npy beside master_voice.wav."""
    return os.path.join(os.path.dirname(os.path.abspath(master_voice_path)), EMBEDDING_FILE)


def _has_enrollment_files(master_voice_path: str) -> bool:
    return os.path.isfile(_embedding_path(master_voice_path)) or os.path.exists(master_voice_path)


def _preflight_state(save_dir: str, master_voice_path: str) -> tuple[str, str | None]:
    """File-only view of the engine state for callers that must not load the model."""
    missing = _missing_model_files(save_dir)
    if missing:
        return STATE_MODEL_MISSING, _model_missing_message(save_dir, missing)
    if not _has_enrollment_files(master_voice_path):
        return STATE_NOT_ENROLLED, None
    return STATE_READY, None


def _local_model_overrides(save_dir: str) -> dict[str, str]:
    """Point the hparams ``pretrained_path`` at the local model folder.

    The published hyperparams.yaml sets ``pretrained_path: speechbrain/spkrec-ecapa-voxceleb``, so
    SpeechBrain's pretrainer would fetch any file it cannot find in ``savedir`` (for example
    label_encoder.txt, which it stores as label_encoder.ckpt) from the Hugging Face Hub.
    SpeechBrain 1.0 rejects overrides for keys the file does not declare, so the key is only set
    when the file has it.
    """
    try:
        with open(os.path.join(save_dir, "hyperparams.yaml"), encoding="utf-8", errors="replace") as handle:
            hparams_text = handle.read()
    except OSError:
        return {}
    if re.search(r"^pretrained_path\s*:", hparams_text, flags=re.MULTILINE):
        return {"pretrained_path": Path(save_dir).as_posix()}
    return {}


def _shim_list_audio_backends() -> list[str]:
    return ["soundfile"]


def _shim_get_audio_backend() -> str:
    return "soundfile"


def _shim_set_audio_backend(backend: str | None = None) -> None:
    """No-op: soundfile is the only backend these shims report."""


def _install_torchaudio_backend_shims(torchaudio_module: Any) -> None:
    """Restore the global-backend API that torchaudio 2.10 removed but SpeechBrain 1.0.x still calls.

    speechbrain/dataio/dataio.py runs check_torchaudio_backend() -> torchaudio.list_audio_backends()
    while it is being imported, so this must run before anything from ``speechbrain`` is imported.
    """
    if not hasattr(torchaudio_module, "list_audio_backends"):
        torchaudio_module.list_audio_backends = _shim_list_audio_backends
    if not hasattr(torchaudio_module, "get_audio_backend"):
        torchaudio_module.get_audio_backend = _shim_get_audio_backend
    if not hasattr(torchaudio_module, "set_audio_backend"):
        torchaudio_module.set_audio_backend = _shim_set_audio_backend


def _ensure_audio_deps() -> None:
    """Load heavy voice-id dependencies only when voice verification is used."""
    if _AUDIO_DEPS_READY:
        return
    with _AUDIO_DEPS_LOCK:
        if not _AUDIO_DEPS_READY:
            _load_audio_deps()


def _load_audio_deps() -> None:
    global np, sf, torch, torchaudio, huggingface_hub, SpeakerRecognition, _AUDIO_DEPS_READY

    import huggingface_hub as _huggingface_hub  # pylint: disable=import-outside-toplevel
    import numpy as _np  # pylint: disable=import-outside-toplevel
    import soundfile as _sf  # pylint: disable=import-outside-toplevel
    import torch as _torch  # pylint: disable=import-outside-toplevel,import-error
    import torchaudio as _torchaudio  # pylint: disable=import-outside-toplevel,import-error
    import torchaudio.transforms  # pylint: disable=import-outside-toplevel,import-error,unused-import

    # Must come BEFORE the SpeechBrain import below, never after it.
    _install_torchaudio_backend_shims(_torchaudio)

    from speechbrain.inference.speaker import (  # pylint: disable=import-outside-toplevel,import-error
        SpeakerRecognition as _SpeakerRecognition,
    )

    original_torchaudio_load = _torchaudio.load

    def _safe_torchaudio_load(filepath: str, *args: Any, **kwargs: Any) -> tuple[Any, int]:
        try:
            return original_torchaudio_load(filepath, *args, **kwargs)  # type: ignore
        except Exception:  # pylint: disable=broad-exception-caught
            data, sample_rate = _sf.read(filepath, dtype="float32", always_2d=True)
            tensor = _torch.from_numpy(data.T)
            return tensor, sample_rate

    _torchaudio.load = _safe_torchaudio_load

    original_hf_hub_download = _huggingface_hub.hf_hub_download

    # SpeechBrain 1.0 still passes use_auth_token; huggingface_hub 1.x accepts neither that nor
    # local_dir_use_symlinks (a TypeError), so the first is renamed and the second dropped.
    def _patched_hf_hub_download(*args: Any, **kwargs: Any) -> str:
        if "use_auth_token" in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        kwargs.pop("local_dir_use_symlinks", None)
        return cast(str, original_hf_hub_download(*args, **kwargs))

    _huggingface_hub.hf_hub_download = _patched_hf_hub_download  # type: ignore

    original_snapshot_download = _huggingface_hub.snapshot_download

    def _patched_snapshot_download(*args: Any, **kwargs: Any) -> str:
        if "use_auth_token" in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        kwargs.pop("local_dir_use_symlinks", None)
        return cast(str, original_snapshot_download(*args, **kwargs))

    _huggingface_hub.snapshot_download = _patched_snapshot_download  # type: ignore

    if os.name == "nt":
        def _patched_symlink(src: str, dst: str, target_is_directory: bool = False, **kwargs: Any) -> None:
            try:
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.copy2(src, dst)
            except OSError:
                pass

        os.symlink = _patched_symlink  # type: ignore

    np = _np
    sf = _sf
    torch = _torch
    torchaudio = _torchaudio
    huggingface_hub = _huggingface_hub
    SpeakerRecognition = _SpeakerRecognition
    _AUDIO_DEPS_READY = True


def _limit_torch_threads() -> None:
    """Keep SpeechBrain on one CPU thread so inference cannot starve the event loop."""
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError as e:
        # Settable once per process and only before inter-op work starts (another torch model,
        # e.g. the memory embedder, may already have run); the intra-op limit above still applies.
        logger.debug("Torch inter-op threads already fixed: %s", e)


@contextlib.contextmanager
def _torch_thread_scope(count: int) -> Iterator[None]:
    """Run the enclosed torch work in the calling thread with ``count`` intra-op threads.

    torch.set_num_threads is per OS thread (OpenMP): a count set in the prewarm thread never reaches the
    executor threads verify_pcm runs on, and a thread that already ran torch (vector memory shares the
    default executor) keeps its own count. So every inference applies the engine's count in its own
    thread and restores that thread's previous count afterwards, leaving other torch users unchanged.
    """
    previous = int(torch.get_num_threads())
    changed = previous != count
    if changed:
        torch.set_num_threads(count)
    try:
        yield
    finally:
        if changed:
            torch.set_num_threads(previous)


def resolve_threshold(threshold: float | None = None) -> float:
    """The acceptance threshold: ``threshold`` when given, else JARVIS_VOICE_ID_THRESHOLD (K-69), else 0.45."""
    global _THRESHOLD_WARNED  # pylint: disable=global-statement
    if threshold is not None:
        return float(threshold)
    raw = (os.getenv(THRESHOLD_ENV) or "").strip()
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        value = float(raw)
        if not -1.0 <= value <= 1.0:
            raise ValueError("outside [-1, 1]")
        return value
    except ValueError:
        if not _THRESHOLD_WARNED:
            _THRESHOLD_WARNED = True
            logger.warning("%s is not a cosine score in [-1, 1]; using %.2f", THRESHOLD_ENV, DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD


def _pcm_to_float32(pcm: Any) -> Any:
    """Mono float32 samples in [-1, 1) from 16-bit PCM, entirely in memory.

    Accepts little-endian int16 bytes (bytes, bytearray, a byte memoryview), an int16 buffer
    (a memoryview cast to 'h' such as livekit AudioFrame.data, array('h')), a numpy array (integer
    arrays are int16 samples, float arrays are already scaled) or any iterable of int16 sample values
    (e.g. a deque filled from AudioFrame.data, whose negative values bytes() rejects). A 2-D array is
    averaged over its channel axis (the shorter one).
    """
    import numpy as _np  # pylint: disable=import-outside-toplevel

    if isinstance(pcm, (bytes, bytearray)):
        raw = bytes(pcm)
        arr = _np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype="<i2")
    elif isinstance(pcm, memoryview):
        if pcm.itemsize == 2 and pcm.format in ("h", "<h", "=h", "@h"):
            arr = _np.frombuffer(pcm, dtype=_np.int16)
        else:
            raw = pcm.cast("B").tobytes() if pcm.contiguous else pcm.tobytes()
            arr = _np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype="<i2")
    elif isinstance(pcm, array.array):
        if pcm.typecode == "h":
            arr = _np.frombuffer(pcm, dtype=_np.int16)
        else:
            arr = _np.asarray(pcm.tolist())
    elif isinstance(pcm, _np.ndarray):
        arr = pcm
    elif isinstance(pcm, (str, dict)) or pcm is None:
        raise TypeError(f"PCM must be bytes, an int16 buffer or a sequence of samples, not {type(pcm).__name__}")
    else:
        try:
            # C-level iteration (no per-sample Python call); a deque cannot change meanwhile (same thread).
            arr = _np.frombuffer(array.array("h", pcm), dtype=_np.int16)
        except OverflowError as e:
            raise ValueError("PCM sample values are outside the int16 range") from e
        except TypeError:
            # Non-integer sample objects (e.g. floats): the slower per-sample conversion, as before.
            arr = _np.fromiter((int(sample) for sample in pcm), dtype=_np.int64)

    if arr.ndim == 2:
        arr = arr.mean(axis=1 if arr.shape[1] <= arr.shape[0] else 0)
    elif arr.ndim != 1:
        raise ValueError(f"PCM must be mono or 2-D multi-channel, got {arr.ndim} dimensions")

    if arr.dtype.kind == "f":
        return _np.ascontiguousarray(arr, dtype=_np.float32)
    if arr.dtype.kind not in "iu":
        raise TypeError(f"PCM samples must be integers or floats, not {arr.dtype}")
    if arr.dtype != _np.int16:
        if arr.size and (int(arr.min()) < -32768 or int(arr.max()) > 32767):
            raise ValueError("PCM sample values are outside the int16 range")
        arr = arr.astype(_np.int16)
    return arr.astype(_np.float32) / 32768.0


def _float_to_int16(signal: Any) -> Any:
    """float32 samples in [-1, 1] as int16 (clipped)."""
    return (np.clip(signal, -1.0, 32767.0 / 32768.0) * 32768.0).round().astype(np.int16)


def _wav_bytes(samples_int16: Any, sample_rate: int) -> bytes:
    """A mono 16-bit WAV file of the samples, built in memory."""
    buffer = io.BytesIO()
    with wave.Wave_write(buffer) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(np.ascontiguousarray(samples_int16, dtype="<i2").tobytes())
    return buffer.getvalue()


def _atomic_write(path: str, data: bytes) -> None:
    """Write data to path through a sibling file and os.replace, so readers never see half a file."""
    tmp_path = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _restrict_dir(path: str) -> None:
    """Owner-only access (700) where POSIX permissions exist; Windows keeps the profile ACLs."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
    except OSError as e:
        logger.debug("chmod 700 failed for %s: %s", path, e)


def _l2_normalize(vector: Any) -> Any:
    """A flat float64 copy of vector with unit L2 norm; ValueError for an empty, zero or non-finite one."""
    flat = np.asarray(vector, dtype=np.float64).reshape(-1)
    if flat.size == 0:
        raise ValueError("embedding is empty")
    norm = float(np.linalg.norm(flat))
    if not np.isfinite(norm) or norm <= 0.0:
        raise ValueError("embedding has no direction (zero or non-finite norm)")
    return flat / norm


def _to_numpy(value: Any) -> Any:
    """A numpy view of a torch tensor (detached, on the CPU) or of any array-like."""
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def _p95_ms(durations_ms: list[float]) -> int:
    """Nearest-rank 95th percentile of the durations, in whole milliseconds."""
    ordered = sorted(durations_ms)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return int(round(ordered[index]))


def _read_audio_file(audio_path: str) -> tuple[Any, int]:
    """(mono float32 samples, sample rate) of an audio file, decoded in memory.

    16-bit WAV is read with the wave module; other WAV encodings and formats libsndfile knows go
    through soundfile; anything else is decoded by FFmpeg to 16 kHz PCM on a pipe (no temp file).
    """
    try:
        with wave.open(audio_path, "rb") as wf:
            sample_rate = wf.getframerate()
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        if sampwidth == 2:
            data = np.frombuffer(frames[: len(frames) - (len(frames) % (2 * n_channels))], dtype="<i2")
            signal = data.astype(np.float32) / 32768.0
            if n_channels > 1:
                signal = signal.reshape(-1, n_channels).mean(axis=1)
            return np.ascontiguousarray(signal, dtype=np.float32), int(sample_rate)
    except (OSError, EOFError, wave.Error):
        pass  # not a PCM WAV: soundfile or FFmpeg below

    try:
        data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        return np.ascontiguousarray(data.mean(axis=1), dtype=np.float32), int(sample_rate)
    except (OSError, RuntimeError, ValueError, TypeError):
        pass  # libsndfile cannot decode it (soundfile.LibsndfileError is a RuntimeError)

    logger.info("Non-WAV format detected for %s. Decoding with FFmpeg...", audio_path)
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", audio_path, "-f", "s16le", "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE), "pipe:1"],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = np.frombuffer(completed.stdout[: len(completed.stdout) - (len(completed.stdout) % 2)], dtype="<i2")
    return data.astype(np.float32) / 32768.0, TARGET_SAMPLE_RATE


async def _notify_score(score: float) -> None:
    """Push the match confidence to the UI; a bridge failure never affects the verdict."""
    try:
        from services.utils.jarvis_bridge import notify_voice_match  # pylint: disable=import-outside-toplevel

        await notify_voice_match(score)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("voice_id_sync not sent: %s", e)


def _enroll_failure(error: str) -> dict[str, Any]:
    return {"ok": False, "self_score": 0.0, "samples": 0, "error": error}


class VoiceFingerprintEngine:
    def __init__(self, master_voice_path: str) -> None:
        """
        Initializes the Speaker Recognition model and loads the enrolled master voice.

        Never raises and never downloads the model: the outcome is reported through ``state``
        ('model_missing' | 'not_enrolled' | 'ready' | 'engine_error') and ``error``.
        """
        self.master_voice_path: str = master_voice_path
        self.model_source: str = MODEL_SOURCE
        self.save_dir: str = _model_dir()
        self._pending_enroll: str | None = None
        # The L2-normalized master embedding (flat numpy vector) once loaded or computed.
        self.master_embedding: Any | None = None
        self.verification: Any | None = None
        self._state: str = STATE_ENGINE_ERROR
        self.error: str | None = None
        self._identity_lock = threading.RLock()
        self.torch_threads: int | None = None
        self.p95_ms: int | None = None

        logger.info("Initializing Speaker Identification Engine...")
        try:
            self._initialize()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Includes AttributeError from torchaudio/SpeechBrain API drift: never raise into the turn path.
            logger.error("Failed to initialize Voice ID Engine: %s: %s", type(e).__name__, e)
            self.verification = None
            self.master_embedding = None
            self._pending_enroll = None
            self._set_state(STATE_ENGINE_ERROR, f"{type(e).__name__}: {e}")

    @property
    def state(self) -> str:
        """'model_missing' | 'not_enrolled' | 'ready' | 'engine_error'."""
        return self._state

    @property
    def identity_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.master_voice_path))

    @property
    def embedding_path(self) -> str:
        return _embedding_path(self.master_voice_path)

    @property
    def samples_dir(self) -> str:
        return os.path.join(self.identity_dir, SAMPLES_DIR_NAME)

    @property
    def is_enrolled(self) -> bool:
        """True when a master voice exists: in memory, pending from master_voice.wav, or on disk."""
        return (
            self.master_embedding is not None
            or self._pending_enroll is not None
            or _has_enrollment_files(self.master_voice_path)
        )

    def _set_state(self, state: str, error: str | None = None) -> None:
        self._state = state
        self.error = error
        logger.info("VOICE_ID state=%s", state)

    def _initialize(self) -> None:
        missing = _missing_model_files(self.save_dir)
        if missing:
            # No implicit download: scripts/models/download_voice_model.py is the only download path.
            message = _model_missing_message(self.save_dir, missing)
            logger.warning("%s", message)
            self._set_state(STATE_MODEL_MISSING, message)
            return
        logger.info("Model files found in %s", self.save_dir)

        _ensure_audio_deps()

        # Limit Torch to single thread to prevent CPU saturation and event loop lag.
        _limit_torch_threads()

        self.verification = SpeakerRecognition.from_hparams(
            source=self.save_dir,
            savedir=self.save_dir,
            run_opts={"device": "cpu"},
            overrides=_local_model_overrides(self.save_dir),
        )
        self._load_identity()

    def _load_identity(self) -> None:
        """Load the master voice: master_embedding.npy first, else master_voice.wav (embedded at first use)."""
        with self._identity_lock:
            self.master_embedding = None
            self._pending_enroll = None
            embedding_path = self.embedding_path
            embedding_error: str | None = None
            if os.path.isfile(embedding_path):
                try:
                    self.master_embedding = _l2_normalize(np.load(embedding_path, allow_pickle=False))
                    logger.info("Master voice embedding loaded from %s", embedding_path)
                    self._set_state(STATE_READY)
                    return
                except (OSError, ValueError, EOFError) as e:
                    embedding_error = f"Master voice embedding unreadable at {embedding_path}: {e}"
                    logger.warning("%s", embedding_error)

            if os.path.exists(self.master_voice_path):
                logger.info("Enrolling Master Voice from %s", self.master_voice_path)
                self._pending_enroll = self.master_voice_path
                logger.info("Master voice enrollment deferred until first use.")
                self._set_state(STATE_READY)
                return

            if embedding_error:
                self._set_state(STATE_ENGINE_ERROR, embedding_error)
                return
            logger.warning("Master voice file not found at %s", self.master_voice_path)
            self._set_state(STATE_NOT_ENROLLED)

    def reload(self) -> str:
        """Re-read the enrolled identity from disk (e.g. after a CLI enrollment); returns the new state.

        When the model never loaded (model installed afterwards, or a failed start) the whole engine
        initializes again. Blocking file and model work: call it off the event loop.
        """
        with self._identity_lock:
            if self.verification is None:
                self.master_embedding = None
                self._pending_enroll = None
                self.save_dir = _model_dir()
                try:
                    self._initialize()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error("Voice ID reload failed: %s: %s", type(e).__name__, e)
                    self.verification = None
                    self._set_state(STATE_ENGINE_ERROR, f"{type(e).__name__}: {e}")
            else:
                try:
                    self._load_identity()
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.error("Voice ID identity reload failed: %s: %s", type(e).__name__, e)
                    self._set_state(STATE_ENGINE_ERROR, f"{type(e).__name__}: {e}")
            return self.state

    # ------------------------------------------------------------------ embeddings (worker threads)

    def _resample_16k(self, signal: Any, sample_rate: int) -> Any:
        """float32 mono samples at 16 kHz (torchaudio.functional.resample, in memory)."""
        signal = np.ascontiguousarray(signal, dtype=np.float32)
        if int(sample_rate) == TARGET_SAMPLE_RATE:
            return signal
        tensor = torch.from_numpy(signal).unsqueeze(0)
        resampled = torchaudio.functional.resample(tensor, int(sample_rate), TARGET_SAMPLE_RATE)
        return np.ascontiguousarray(_to_numpy(resampled).reshape(-1), dtype=np.float32)

    def _embed_16k(self, signal_16k: Any) -> Any:
        """The L2-normalized ECAPA embedding of 16 kHz mono float32 samples."""
        if self.verification is None:
            raise RuntimeError("speaker model not loaded")
        tensor = torch.from_numpy(np.ascontiguousarray(signal_16k, dtype=np.float32)).unsqueeze(0)
        with torch.no_grad():
            embedding = self.verification.encode_batch(tensor)
        return _l2_normalize(_to_numpy(embedding))

    def _inference_threads(self, threads: int | None = None) -> int:
        """Torch threads for one inference: ``threads`` (calibration), else the calibrated count, else 1."""
        return max(1, int(threads or self.torch_threads or 1))

    def _embed_signal_sync(self, signal: Any, sample_rate: int, threads: int | None = None) -> Any:
        with _torch_thread_scope(self._inference_threads(threads)):
            return self._embed_16k(self._resample_16k(signal, sample_rate))

    def _score_sync(self, signal: Any, sample_rate: int) -> float:
        """Cosine similarity of the signal's embedding with the L2-normalized master embedding."""
        master = self.master_embedding
        if master is None:
            raise RuntimeError("no master embedding")
        test = self._embed_signal_sync(signal, sample_rate)
        return float(np.clip(np.dot(test, master), -1.0, 1.0))

    def _embed_file_sync(self, audio_path: str) -> Any | None:
        if self.verification is None:
            return None
        signal, sample_rate = _read_audio_file(audio_path)
        if signal.size == 0:
            raise ValueError("audio file has no samples")
        logger.info("Signal loaded for %s: samples=%d, SampleRate=%d", audio_path, signal.size, sample_rate)
        return self._embed_signal_sync(signal, sample_rate)

    async def _get_embedding(self, audio_path: str) -> Any | None:
        """L2-normalized speaker embedding of an audio file (decoded in memory), or None on failure."""
        try:
            _ensure_audio_deps()
            return await asyncio.to_thread(self._embed_file_sync, audio_path)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Embedding extraction failed for %s: %s", audio_path, e)
            return None

    # ------------------------------------------------------------------ lazy enrollment from master_voice.wav

    def _finish_lazy_enroll(self, pending: str, embedding: Any | None) -> None:
        with self._identity_lock:
            if self._pending_enroll != pending or self.master_embedding is not None:
                return  # another thread finished first, or reload()/enroll() replaced the identity
            self._pending_enroll = None
            if embedding is not None:
                self.master_embedding = embedding
                logger.info("Master Voice Identity Loaded (lazy).")
            else:
                logger.error("Lazy enrollment failed.")
                self._set_state(STATE_ENGINE_ERROR, f"Master voice enrollment failed for {pending}")

    async def _ensure_master_embedding(self) -> None:
        pending = self._pending_enroll
        if self.master_embedding is None and pending:
            logger.info("Lazy enrolling master voice...")
            embedding = await self._get_embedding(pending)
            self._finish_lazy_enroll(pending, embedding)

    def _ensure_master_embedding_sync(self) -> None:
        """The same lazy enrollment, for a background thread (prewarm)."""
        pending = self._pending_enroll
        if self.master_embedding is None and pending:
            logger.info("Lazy enrolling master voice...")
            try:
                embedding = self._embed_file_sync(pending)
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Embedding extraction failed for %s: %s", pending, e)
                embedding = None
            self._finish_lazy_enroll(pending, embedding)

    # ------------------------------------------------------------------ verification

    async def verify_pcm(self, pcm: Any, sample_rate: int, threshold: float | None = None) -> tuple[bool, float]:
        """
        Verifies 16-bit PCM against the master voice, in memory (no temp files, no torchaudio.load).
        threshold=None uses JARVIS_VOICE_ID_THRESHOLD (default 0.45). Never raises.
        Returns: (accepted, score)
        """
        limit = resolve_threshold(threshold)
        await self._ensure_master_embedding()
        if self.verification is None or self.master_embedding is None:
            logger.warning("Voice ID Engine not initialized correctly. Denying access.")
            return False, 0.0

        try:
            rate = int(sample_rate)
            if rate <= 0:
                raise ValueError(f"sample rate must be positive, got {rate}")
            signal = _pcm_to_float32(pcm)
        except (TypeError, ValueError, OverflowError) as e:
            logger.error("Voice ID input rejected: %s", e)
            return False, 0.0

        if signal.size < rate * MIN_VERIFY_SECONDS:
            logger.debug("Segment too short for verification (%d samples at %d Hz).", signal.size, rate)
            return False, 0.0

        try:
            score = await asyncio.to_thread(self._score_sync, signal, rate)
        except Exception as e:  # pylint: disable=broad-exception-caught
            if "Padding size" in str(e):
                logger.debug("Segment too short for model padding.")
            else:
                logger.error("Verification logic failed: %s: %s", type(e).__name__, e)
            return False, 0.0

        is_match = score >= limit
        await _notify_score(score)
        logger.info("Voice Security Check: Score=%.4f | Threshold=%.2f | Match=%s", score, limit, is_match)
        return is_match, score

    async def verify_segment(self, segment_path: str, threshold: float = 0.65) -> tuple[bool, float]:
        """
        Verifies if the audio file matches the master voice (decoded in memory, then verify_pcm).
        Returns: (is_match, score)
        """
        if self.verification is None or (self.master_embedding is None and not self._pending_enroll):
            logger.warning("Voice ID Engine not initialized correctly. Denying access.")
            return False, 0.0
        try:
            signal, sample_rate = await asyncio.to_thread(_read_audio_file, segment_path)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
            logger.error("Verification logic failed: %s", e)
            return False, 0.0
        return await self.verify_pcm(signal, sample_rate, threshold)

    async def verify_bytes(self, audio_bytes: Any, sample_rate: int = 16000, threshold: float = 0.70) -> tuple[bool, float]:
        """Verifies raw audio bytes (16-bit PCM); at least 1 s is required."""
        await self._ensure_master_embedding()

        if len(audio_bytes) < (sample_rate * 1.0 * 2):
            return False, 0.0

        return await self.verify_pcm(audio_bytes, sample_rate, threshold)

    # ------------------------------------------------------------------ enrollment

    async def enroll(self, samples: list[tuple[Any, int]]) -> dict[str, Any]:
        """
        Enrolls the master voice from (pcm, sample_rate) samples (16-bit PCM, >= 1 s each).

        Writes data/identity/master_embedding.npy (L2-normalized mean embedding), samples/*.wav and the
        concatenated master_voice.wav (16 kHz mono int16), after backing up the previous files to
        backups/identity/<ts>/. Returns {ok, self_score, samples} (+ error when not ok); never raises.
        """
        if self.verification is None:
            return _enroll_failure(f"Voice-ID engine is {self.state}: {self.error or 'model not loaded'}")
        if not samples:
            return _enroll_failure("no enrollment samples")
        signals: list[tuple[Any, int]] = []
        try:
            for index, sample in enumerate(samples, start=1):
                pcm, sample_rate = sample
                rate = int(sample_rate)
                if rate <= 0:
                    raise ValueError(f"sample {index}: sample rate must be positive")
                signal = _pcm_to_float32(pcm)
                if signal.size < rate * MIN_ENROLL_SECONDS:
                    raise ValueError(f"sample {index} is shorter than {MIN_ENROLL_SECONDS:.1f} s")
                signals.append((signal, rate))
        except (TypeError, ValueError, OverflowError) as e:
            return _enroll_failure(str(e))

        try:
            return await asyncio.to_thread(self._enroll_sync, signals)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Voice ID enrollment failed: %s: %s", type(e).__name__, e)
            return _enroll_failure(f"{type(e).__name__}: {e}")

    def _enroll_sync(self, signals: list[tuple[Any, int]]) -> dict[str, Any]:
        with _torch_thread_scope(self._inference_threads()):
            clips = [self._resample_16k(signal, rate) for signal, rate in signals]
            embeddings = [self._embed_16k(clip) for clip in clips]
        master = _l2_normalize(np.mean(np.stack(embeddings), axis=0))
        scores = [float(np.dot(embedding, master)) for embedding in embeddings]
        self_score = float(np.mean(scores))

        with self._identity_lock:
            backup = self._backup_identity()
            try:
                self._write_identity(clips, master)
            except BaseException:
                self._restore_identity(backup)
                raise
            self.master_embedding = master
            self._pending_enroll = None
            self._set_state(STATE_READY)
        logger.info("VOICE_ID enrolled samples=%d self_score=%.3f", len(clips), self_score)
        return {"ok": True, "self_score": self_score, "samples": len(clips)}

    def _backup_identity(self) -> str | None:
        """Copy the current identity files to backups/identity/<ts>/; None when there is nothing to keep."""
        files = [path for path in (self.embedding_path, self.master_voice_path) if os.path.isfile(path)]
        has_samples = os.path.isdir(self.samples_dir) and any(os.scandir(self.samples_dir))
        if not files and not has_samples:
            return None
        root = os.path.join(config.backups_dir, "identity")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = os.path.join(root, stamp)
        suffix = 1
        while os.path.exists(destination):
            destination = os.path.join(root, f"{stamp}-{suffix}")
            suffix += 1
        os.makedirs(destination, mode=0o700)
        _restrict_dir(destination)
        for path in files:
            shutil.copy2(path, os.path.join(destination, os.path.basename(path)))
        if has_samples:
            shutil.copytree(self.samples_dir, os.path.join(destination, SAMPLES_DIR_NAME))
        logger.info("VOICE_ID previous identity backed up to %s", destination)
        return destination

    def _write_identity(self, clips_16k: list[Any], master: Any) -> None:
        """Write the new identity. Every file is staged to a sibling file first; only when all of them are
        written are the old samples removed and the staged files moved into place (.npy last)."""
        os.makedirs(self.identity_dir, mode=0o700, exist_ok=True)
        _restrict_dir(self.identity_dir)
        os.makedirs(self.samples_dir, mode=0o700, exist_ok=True)
        _restrict_dir(self.samples_dir)

        int16_clips = [_float_to_int16(clip) for clip in clips_16k]
        buffer = io.BytesIO()
        np.save(buffer, np.asarray(master, dtype=np.float32), allow_pickle=False)
        targets: list[tuple[str, bytes]] = [
            (os.path.join(self.samples_dir, f"sample_{index:02d}.wav"), _wav_bytes(clip, TARGET_SAMPLE_RATE))
            for index, clip in enumerate(int16_clips, start=1)
        ]
        targets.append((self.master_voice_path, _wav_bytes(np.concatenate(int16_clips), TARGET_SAMPLE_RATE)))
        targets.append((self.embedding_path, buffer.getvalue()))  # last: loading prefers it

        staged: list[tuple[str, str]] = []
        try:
            for path, data in targets:
                tmp_path = f"{path}.{os.getpid()}.staged"
                staged.append((tmp_path, path))
                with open(tmp_path, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            keep = {os.path.basename(path) for _, path in staged}
            for entry in os.scandir(self.samples_dir):
                if entry.is_file() and entry.name.lower().endswith(".wav") and entry.name not in keep:
                    os.remove(entry.path)
            for tmp_path, path in staged:
                os.replace(tmp_path, path)
        finally:
            for tmp_path, _ in staged:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def _restore_identity(self, backup: str | None) -> None:
        """After a failed write, put back the identity _backup_identity saved (or remove a partial first
        enrollment), so the .npy, master_voice.wav and samples on disk describe one identity."""
        try:
            if os.path.isdir(self.samples_dir):
                for entry in os.scandir(self.samples_dir):
                    if entry.is_file() and entry.name.lower().endswith(".wav"):
                        os.remove(entry.path)
            for path in (self.embedding_path, self.master_voice_path):
                saved = os.path.join(backup, os.path.basename(path)) if backup else None
                if saved and os.path.isfile(saved):
                    with open(saved, "rb") as handle:
                        _atomic_write(path, handle.read())
                elif os.path.isfile(path):
                    os.remove(path)
            saved_samples = os.path.join(backup, SAMPLES_DIR_NAME) if backup else None
            if saved_samples and os.path.isdir(saved_samples):
                os.makedirs(self.samples_dir, mode=0o700, exist_ok=True)
                for entry in os.scandir(saved_samples):
                    if entry.is_file():
                        shutil.copy2(entry.path, os.path.join(self.samples_dir, entry.name))
            logger.warning("VOICE_ID enrollment write failed; the previous identity files were restored")
        except OSError as e:
            logger.error("VOICE_ID identity restore failed (backup: %s): %s", backup, e)

    # ------------------------------------------------------------------ torch threads (prewarm)

    def _bench_once(self, pcm: bytes, sample_rate: int, threads: int) -> float:
        """Milliseconds for the work verify_pcm does per call (PCM conversion, embedding, cosine)."""
        started = time.perf_counter()
        signal = _pcm_to_float32(pcm)
        embedding = self._embed_signal_sync(signal, sample_rate, threads)
        master = self.master_embedding
        if master is not None:
            float(np.dot(embedding, master))
        return (time.perf_counter() - started) * 1000.0

    def calibrate_threads(self) -> tuple[int, int] | None:
        """
        Times verify_pcm's work with 1 and 2 torch threads, keeps the faster (ties keep 1) and logs
        'VOICE_ID threads=%d p95_ms=%d'. The choice is stored in ``torch_threads`` and every later
        inference applies it in its own thread (torch thread counts are per thread). Blocking: runs in
        the prewarm thread. None when the model is not loaded.
        """
        if self.verification is None:
            return None
        choices = [count for count in THREAD_CHOICES if count <= max(1, os.cpu_count() or 1)]
        rng = np.random.default_rng(0)
        signal = (rng.standard_normal(int(BENCH_SAMPLE_RATE * BENCH_SECONDS)) * 0.1).astype(np.float32)
        pcm = _float_to_int16(signal).astype("<i2").tobytes()
        timings: dict[int, int] = {}
        for count in choices:
            self._bench_once(pcm, BENCH_SAMPLE_RATE, count)  # warm-up
            timings[count] = _p95_ms([self._bench_once(pcm, BENCH_SAMPLE_RATE, count) for _ in range(BENCH_RUNS)])
            logger.debug("VOICE_ID bench threads=%d p95_ms=%d", count, timings[count])
        best = min(timings, key=lambda count: (timings[count], count))
        self.torch_threads = best
        self.p95_ms = timings[best]
        logger.info("VOICE_ID threads=%d p95_ms=%d", best, timings[best])
        return best, timings[best]


def _done_future(result: bool) -> concurrent.futures.Future[bool]:
    future: concurrent.futures.Future[bool] = concurrent.futures.Future()
    future.set_running_or_notify_cancel()
    future.set_result(result)
    return future


class LazyVoiceFingerprintEngine:
    """Defers SpeechBrain model loading until voice verification is used or prewarm() starts it."""

    def __init__(self, master_voice_path: str) -> None:
        self.master_voice_path = master_voice_path
        self._engine: VoiceFingerprintEngine | None = None
        self._engine_lock = threading.Lock()
        self._prewarm_lock = threading.Lock()
        self._prewarm_future: concurrent.futures.Future[bool] | None = None
        self._missing_logged = False

    @property
    def is_loaded(self) -> bool:
        """True once the real engine has been constructed (model load attempted)."""
        return self._engine is not None

    @property
    def state(self) -> str:
        """Engine state without forcing a model load.

        Until the engine is loaded this is a file check: 'model_missing', 'not_enrolled', or
        'ready' (model files and an enrolled master voice present); loading can still end in 'engine_error'.
        """
        engine = self._engine
        if engine is not None:
            return engine.state
        return _preflight_state(_model_dir(), self.master_voice_path)[0]

    @property
    def error(self) -> str | None:
        """Reason for a non-ready state (same rules as ``state``), else None."""
        engine = self._engine
        if engine is not None:
            return engine.error
        return _preflight_state(_model_dir(), self.master_voice_path)[1]

    @property
    def is_enrolled(self) -> bool:
        """True when a master voice is enrolled (file check until the engine is loaded)."""
        engine = self._engine
        if engine is not None:
            return engine.is_enrolled
        return _has_enrollment_files(self.master_voice_path)

    def _load_engine_sync(self) -> VoiceFingerprintEngine:
        with self._engine_lock:
            if self._engine is None:
                logger.info("Lazy loading Speaker Identification Engine...")
                self._engine = VoiceFingerprintEngine(self.master_voice_path)
            return self._engine

    async def _get_engine(self) -> VoiceFingerprintEngine:
        return await asyncio.to_thread(self._load_engine_sync)

    def prewarm(self) -> concurrent.futures.Future[bool]:
        """
        Loads the model (and the master embedding) in a background thread, logs 'VOICE_ID ready in %.1fs'
        only when the engine can verify (else 'VOICE_ID model loaded in %.1fs state=%s'), then picks the
        faster torch thread count. The future's result is True only for a ready engine. Never blocks, so
        it is safe on an event loop; once per process (a later call returns the same future). Without
        the model files it logs once and returns a finished False future: verification then behaves
        exactly as before (lazy, denied).
        """
        with self._prewarm_lock:
            future = self._prewarm_future
            if future is not None and not (future.done() and not future.result() and self._engine is None):
                return future
            state, error = _preflight_state(_model_dir(), self.master_voice_path)
            if state == STATE_MODEL_MISSING and self._engine is None:
                if not self._missing_logged:
                    self._missing_logged = True
                    logger.warning("VOICE_ID prewarm skipped: %s", error)
                future = self._prewarm_future = _done_future(False)
                return future
            future = concurrent.futures.Future()
            future.set_running_or_notify_cancel()
            self._prewarm_future = future
            try:
                threading.Thread(
                    target=self._prewarm_sync, args=(future,), name="jarvis-voice-id-prewarm", daemon=True
                ).start()
            except RuntimeError as e:  # no new thread now (interpreter shutdown)
                logger.error("VOICE_ID prewarm thread not started: %s", e)
                future.set_result(False)
            return future

    def _prewarm_sync(self, future: concurrent.futures.Future[bool]) -> None:
        started = time.monotonic()
        ready = False
        try:
            engine = self._load_engine_sync()
            if engine.verification is None:
                logger.warning("VOICE_ID not ready: state=%s", engine.state)
                return
            engine._ensure_master_embedding_sync()  # pylint: disable=protected-access
            elapsed = time.monotonic() - started
            if engine.state == STATE_READY and engine.master_embedding is not None:
                logger.info("VOICE_ID ready in %.1fs", elapsed)
                ready = True
            else:
                logger.warning("VOICE_ID model loaded in %.1fs state=%s", elapsed, engine.state)
            try:
                engine.calibrate_threads()
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning("VOICE_ID thread calibration failed: %s: %s", type(e).__name__, e)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("VOICE_ID prewarm failed: %s: %s", type(e).__name__, e)
        finally:
            future.set_result(ready)

    async def verify_pcm(self, pcm: Any, sample_rate: int, threshold: float | None = None) -> tuple[bool, float]:
        try:
            engine = await self._get_engine()
            return await engine.verify_pcm(pcm, sample_rate, threshold)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Voice-ID must never raise into the turn path; an unverifiable turn is denied.
            logger.error("Voice ID verification failed: %s: %s", type(e).__name__, e)
            return False, 0.0

    async def verify_segment(self, segment_path: str, threshold: float = 0.65) -> tuple[bool, float]:
        try:
            engine = await self._get_engine()
            return await engine.verify_segment(segment_path, threshold)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Voice ID verification failed: %s: %s", type(e).__name__, e)
            return False, 0.0

    async def verify_bytes(
        self,
        audio_bytes: Any,
        sample_rate: int = 16000,
        threshold: float = 0.70,
    ) -> tuple[bool, float]:
        try:
            engine = await self._get_engine()
            return await engine.verify_bytes(audio_bytes, sample_rate=sample_rate, threshold=threshold)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Voice-ID must never raise into the turn path; an unverifiable turn is denied.
            logger.error("Voice ID verification failed: %s: %s", type(e).__name__, e)
            return False, 0.0

    async def enroll(self, samples: list[tuple[Any, int]]) -> dict[str, Any]:
        try:
            engine = await self._get_engine()
            return await engine.enroll(samples)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Voice ID enrollment failed: %s: %s", type(e).__name__, e)
            return _enroll_failure(f"{type(e).__name__}: {e}")

    def reload(self) -> str:
        """Re-read the identity files (blocking; call off the event loop). Unloaded: the next load reads them."""
        engine = self._engine
        if engine is None:
            return self.state
        return engine.reload()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load_engine_sync(), name)


MASTER_VOICE_PATH = os.path.join(config.project_root, "data", "identity", MASTER_VOICE_FILE)
voice_id_engine = LazyVoiceFingerprintEngine(MASTER_VOICE_PATH)
