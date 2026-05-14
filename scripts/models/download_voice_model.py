"""
Download the local speaker-recognition model outside Git history.

Run:
    python scripts/models/download_voice_model.py
"""

from pathlib import Path

from huggingface_hub import snapshot_download


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_REPO_ID = "speechbrain/spkrec-ecapa-voxceleb"
TARGET_DIR = PROJECT_ROOT / "pretrained_models" / "spkrec-ecapa-voxceleb"


def main() -> None:
    """Download the voice fingerprint model into pretrained_models/."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPO_ID,
        local_dir=str(TARGET_DIR),
        local_dir_use_symlinks=False,
    )
    print(f"Voice model ready at: {TARGET_DIR}")


if __name__ == "__main__":
    main()
