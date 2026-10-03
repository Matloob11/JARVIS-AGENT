"""
Download the local speaker-recognition model outside Git history (K-64 CT-VC-VOICE-ID-STORE).

This is the only download path for the Voice-ID model: services/ai_core/voice_fingerprint.py never
downloads it, at import or at runtime. The target folder comes from the JARVIS config
(config.pretrained_models_dir / spkrec-ecapa-voxceleb), so the PC and the VPS image use the same
layout. After the download the required model files are checked; a missing one exits 1.

Run:
    python scripts/models/download_voice_model.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MODEL_REPO_ID = "speechbrain/spkrec-ecapa-voxceleb"
MODEL_DIR_NAME = "spkrec-ecapa-voxceleb"

# Files SpeakerRecognition.from_hparams needs; each entry lists the accepted names (the same list as
# voice_fingerprint._MODEL_FILES, used when that module cannot be imported, e.g. a bare build stage).
_FALLBACK_MODEL_FILES: tuple[tuple[str, ...], ...] = (
    ("hyperparams.yaml",),
    ("embedding_model.ckpt",),
    ("mean_var_norm_emb.ckpt",),
    ("classifier.ckpt",),
    ("label_encoder.txt", "label_encoder.ckpt"),
)


def _pretrained_models_dir() -> Path:
    """config.pretrained_models_dir, or <project>/pretrained_models when the config cannot load."""
    try:
        from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel
        return Path(config.pretrained_models_dir)
    except Exception:  # pylint: disable=broad-exception-caught
        return PROJECT_ROOT / "pretrained_models"


def _required_model_files() -> tuple[tuple[str, ...], ...]:
    try:
        from services.ai_core import voice_fingerprint  # pylint: disable=import-outside-toplevel
        return voice_fingerprint._MODEL_FILES  # pylint: disable=protected-access
    except Exception:  # pylint: disable=broad-exception-caught
        return _FALLBACK_MODEL_FILES


TARGET_DIR = _pretrained_models_dir() / MODEL_DIR_NAME


def missing_model_files(target_dir: Path) -> list[str]:
    """Required model files absent from target_dir."""
    return [
        " or ".join(names)
        for names in _required_model_files()
        if not any((target_dir / name).is_file() for name in names)
    ]


def main() -> int:
    """Download the voice fingerprint model into pretrained_models/; 0 when every file is present."""
    target_dir = Path(TARGET_DIR)
    if (os.getenv("HF_HUB_OFFLINE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        print("HF_HUB_OFFLINE is set: the download can only reuse files already cached.")  # noqa: T201
    target_dir.mkdir(parents=True, exist_ok=True)
    # local_dir already gets real files; huggingface_hub 1.x rejects local_dir_use_symlinks.
    snapshot_download(
        repo_id=MODEL_REPO_ID,
        local_dir=str(target_dir),
    )
    missing = missing_model_files(target_dir)
    if missing:
        print(f"Voice model incomplete in {target_dir} (missing: {', '.join(missing)})", file=sys.stderr)  # noqa: T201
        return 1
    print(f"Voice model ready at: {target_dir}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
