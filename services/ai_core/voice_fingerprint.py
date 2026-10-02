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
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
import wave
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


def _preflight_state(save_dir: str, master_voice_path: str) -> tuple[str, str | None]:
    """File-only view of the engine state for callers that must not load the model."""
    missing = _missing_model_files(save_dir)
    if missing:
        return STATE_MODEL_MISSING, _model_missing_message(save_dir, missing)
    if not os.path.exists(master_voice_path):
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


class VoiceFingerprintEngine:
    def __init__(self, master_voice_path: str) -> None:
        """
        Initializes the Speaker Recognition model and enrolls the master voice.

        Never raises and never downloads the model: the outcome is reported through ``state``
        ('model_missing' | 'not_enrolled' | 'ready' | 'engine_error') and ``error``.
        """
        self.master_voice_path: str = master_voice_path
        self.model_source: str = MODEL_SOURCE
        self.save_dir: str = _model_dir()
        self._pending_enroll: str | None = None
        self.master_embedding: Any | None = None
        self.verification: Any | None = None
        self._state: str = STATE_ENGINE_ERROR
        self.error: str | None = None

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

        if not os.path.exists(self.master_voice_path):
            logger.warning("Master voice file not found at %s", self.master_voice_path)
            self._set_state(STATE_NOT_ENROLLED)
            return

        logger.info("Enrolling Master Voice from %s", self.master_voice_path)
        self._pending_enroll = self.master_voice_path
        logger.info("Master voice enrollment deferred until first use.")
        self._set_state(STATE_READY)

    async def _get_embedding(self, audio_path: str) -> Any | None:
        """Extracts speaker embedding from audio file with automatic format conversion if needed."""
        try:
            _ensure_audio_deps()

            is_valid_wav = False
            try:
                with wave.open(audio_path, "rb"):
                    is_valid_wav = True
            except (OSError, wave.Error):
                is_valid_wav = False

            if not is_valid_wav:
                logger.info("Non-WAV format detected for %s. Attempting FFmpeg conversion...", audio_path)
                temp_wav = audio_path + ".converted.wav"

                def _run_ffmpeg() -> subprocess.CompletedProcess[bytes]:
                    return subprocess.run(
                        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", temp_wav],
                        check=True,
                        capture_output=True,
                    )

                try:
                    await asyncio.to_thread(_run_ffmpeg)
                    audio_path = temp_wav
                except (OSError, subprocess.SubprocessError, RuntimeError) as e:
                    logger.error("FFmpeg conversion failed: %s", e)
                    return None

            def _load_audio() -> tuple[Any, int]:
                with wave.open(audio_path, "rb") as wf:
                    fs = wf.getframerate()
                    n_channels = wf.getnchannels()
                    n_frames = wf.getnframes()
                    sampwidth = wf.getsampwidth()

                    if sampwidth != 2:
                        data, fs_sf = sf.read(audio_path)
                        signal = torch.from_numpy(data.copy()).float()
                        return signal, int(fs_sf)

                    frames = wf.readframes(n_frames)
                    data = np.frombuffer(frames, dtype=np.int16)
                    signal = torch.from_numpy(data.copy()).float() / 32768.0

                    if n_channels > 1:
                        signal = signal.view(-1, n_channels).transpose(0, 1)
                    return signal, fs

            signal, fs = await asyncio.to_thread(_load_audio)

            if ".converted.wav" in audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

            if len(signal.shape) == 1:
                signal = signal.unsqueeze(0)
            elif len(signal.shape) == 2 and signal.shape[1] < signal.shape[0]:
                signal = signal.transpose(0, 1)

            if fs != 16000:
                resampler = torchaudio.transforms.Resample(fs, 16000)
                signal = resampler(signal)

            if signal.shape[0] > 1:
                signal = torch.mean(signal, dim=0, keepdim=True)

            logger.info("Signal loaded for %s: Shape=%s, SampleRate=%d", audio_path, signal.shape, fs)

            if self.verification is None:
                return None

            embedding = await asyncio.to_thread(self.verification.encode_batch, signal)
            return embedding
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Embedding extraction failed for %s: %s", audio_path, e)
            return None

    async def verify_segment(self, segment_path: str, threshold: float = 0.65) -> tuple[bool, float]:
        """
        Verifies if the audio segment matches the master voice.
        Returns: (is_match, score)
        """
        if self.verification is None or self.master_embedding is None:
            logger.warning("Voice ID Engine not initialized correctly. Denying access.")
            return False, 0.0

        try:
            test_embedding = await self._get_embedding(segment_path)
            if test_embedding is None:
                return False, 0.0

            try:
                m_emb = self.master_embedding
                t_emb = test_embedding

                if m_emb is None or t_emb is None:
                    logger.error("Embedding is None during comparison.")
                    return False, 0.0

                master_emb = m_emb.squeeze()
                test_emb = t_emb.squeeze()

                if len(master_emb.shape) == 1:
                    master_emb = master_emb.unsqueeze(0)
                if len(test_emb.shape) == 1:
                    test_emb = test_emb.unsqueeze(0)

                similarity = torch.nn.functional.cosine_similarity(master_emb, test_emb)
                score_val = float(similarity[0].item())
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("Similarity calculation failed: %s", e)
                return False, 0.0

            is_match = score_val >= threshold

            from services.utils.jarvis_bridge import notify_voice_match  # pylint: disable=import-outside-toplevel

            await notify_voice_match(score_val)

            logger.info("Voice Security Check: Score=%.4f | Threshold=%.2f | Match=%s", score_val, threshold, is_match)
            return is_match, score_val

        except (OSError, RuntimeError, ValueError) as e:
            logger.error("Verification logic failed: %s", e)
            return False, 0.0

    async def verify_bytes(self, audio_bytes: bytes, sample_rate: int = 16000, threshold: float = 0.70) -> tuple[bool, float]:
        """Verifies raw audio bytes (16-bit PCM)."""
        if self.master_embedding is None and getattr(self, "_pending_enroll", None):
            logger.info("Lazy enrolling master voice...")
            pending = self._pending_enroll
            if pending:
                self.master_embedding = await self._get_embedding(pending)
                self._pending_enroll = None
            if self.master_embedding is not None:
                logger.info("Master Voice Identity Loaded (lazy).")
            else:
                logger.error("Lazy enrollment failed.")
                self._set_state(STATE_ENGINE_ERROR, f"Master voice enrollment failed for {pending}")

        if len(audio_bytes) < (sample_rate * 1.0 * 2):
            return False, 0.0

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = tmp.name
            tmp.close()

        try:
            with wave.open(temp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)

            result = await self.verify_segment(temp_path, threshold)
            return result
        except (OSError, ValueError, RuntimeError) as e:
            if "Padding size" in str(e):
                logger.debug("Segment too short for model padding.")
            else:
                logger.error("Bytes verification logic failed: %s", e)
            return False, 0.0
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


class LazyVoiceFingerprintEngine:
    """Defers SpeechBrain model loading until voice verification is actually used."""

    def __init__(self, master_voice_path: str) -> None:
        self.master_voice_path = master_voice_path
        self._engine: VoiceFingerprintEngine | None = None
        self._engine_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        """True once the real engine has been constructed (model load attempted)."""
        return self._engine is not None

    @property
    def state(self) -> str:
        """Engine state without forcing a model load.

        Until the engine is loaded this is a file check: 'model_missing', 'not_enrolled', or
        'ready' (model files and master voice present); loading can still end in 'engine_error'.
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

    def _load_engine_sync(self) -> VoiceFingerprintEngine:
        with self._engine_lock:
            if self._engine is None:
                logger.info("Lazy loading Speaker Identification Engine...")
                self._engine = VoiceFingerprintEngine(self.master_voice_path)
            return self._engine

    async def _get_engine(self) -> VoiceFingerprintEngine:
        return await asyncio.to_thread(self._load_engine_sync)

    async def verify_bytes(
        self,
        audio_bytes: bytes,
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

    def __getattr__(self, name: str) -> Any:
        return getattr(self._load_engine_sync(), name)


MASTER_VOICE_PATH = os.path.join(config.project_root, "data", "identity", "master_voice.wav")
voice_id_engine = LazyVoiceFingerprintEngine(MASTER_VOICE_PATH)
