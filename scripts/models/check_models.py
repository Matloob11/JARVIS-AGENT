"""
Offline load check of the two models baked into the VPS brain image, and the MiniLM bake step (plan task C-126,
contract K-75).

Check (the default) loads both models the way the agent prewarms them, never downloading, and prints one line per
model, then MODELS_OK (exit 0) or MODELS_FAIL (exit 1). Both checks always run.
- MiniLM (sentence-transformers/all-MiniLM-L6-v2): the embedding function of services/ai_core/jarvis_vector_memory.py
  (Chroma's, which loads SentenceTransformer('all-MiniLM-L6-v2')) embeds one sentence: 384 finite numbers. Chroma
  itself is never opened, so the check is safe beside a running agent.
- ECAPA (speechbrain/spkrec-ecapa-voxceleb): services/ai_core/voice_fingerprint.py loads it from
  pretrained_models/spkrec-ecapa-voxceleb (engine state not_enrolled or ready) and embeds one second of a test tone:
  a finite unit vector of 192 numbers.
It refuses to start (exit 2) unless HF_HUB_OFFLINE is on (1, true, yes or on): the point is to prove the offline
load. --allow-online lifts that, e.g. on the PC. --expect-uid N fails the check when this process is not uid N (the
image build runs it as the runtime user 10001).

--bake (the image build only, with network): downloads MiniLM at the pinned revision MINILM_REVISION into the
Hugging Face hub cache (HF_HOME/hub) and points the cache's 'main' ref at that revision, because the agent asks for
the default revision and an offline load can only resolve it through that ref. Only the files a sentence-transformers
load reads are fetched (no ONNX, OpenVINO, TensorFlow or Rust copies). ECAPA has its own fixed download script,
scripts/models/download_voice_model.py. The bake needs only huggingface_hub, so it runs before the code is copied.

Run (deploy/hostinger/RUNBOOK.md, "Agent image"):
    docker compose -p jarvis run --rm --no-deps -e HF_HUB_OFFLINE=1 agent python scripts/models/check_models.py
    python scripts/models/check_models.py --bake              # Dockerfile.agent, as root
    python scripts/models/check_models.py --expect-uid 10001  # Dockerfile.agent, as the runtime user
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MINILM_REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_NAME = "all-MiniLM-L6-v2"  # services/ai_core/jarvis_vector_memory.MODEL_NAME
# The commit 'main' pointed at on 2026-10-08 (last changed upstream on 2026-06-01). A new one is a deliberate change.
MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
# What a sentence-transformers load of this model reads: the module list and configs (1_Pooling/config.json too),
# the safetensors weights, the tokenizer and the model card
MINILM_PATTERNS = ("*.json", "model.safetensors", "vocab.txt", "README.md")
MINILM_REQUIRED = ("modules.json", "config.json", "config_sentence_transformers.json", "sentence_bert_config.json",
                   "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                   "vocab.txt", "1_Pooling/config.json")
MINILM_DIM = 384
ECAPA_NAME = "spkrec-ecapa-voxceleb"
ECAPA_DIM = 192
ECAPA_STATES_OK = ("not_enrolled", "ready")
TONE_RATE = 16000
TONE_SECONDS = 1.0
TONE_HZ = 220.0
MIN_SIGNAL = 1e-6
OFFLINE_ENV = "HF_HUB_OFFLINE"
_TRUE = frozenset({"1", "true", "yes", "on"})

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


class ModelCheckError(RuntimeError):
    """A model did not load, or loaded but gave a wrong result."""


def offline_mode(env: dict[str, str] | None = None) -> bool:
    """True when HF_HUB_OFFLINE is on (1, true, yes or on, in any case)."""
    source = os.environ if env is None else env
    return (source.get(OFFLINE_ENV) or "").strip().lower() in _TRUE


# ---------------------------------------------------------------- bake (image build)


def hub_cache() -> Path:
    """The hub cache huggingface_hub uses: HF_HUB_CACHE, else HF_HOME/hub, else ~/.cache/huggingface/hub."""
    from huggingface_hub import constants  # pylint: disable=import-outside-toplevel
    return Path(constants.HF_HUB_CACHE)


def bake_minilm(cache_dir: Path | None = None, *, download: Callable[..., str] | None = None) -> Path:
    """Download MiniLM's files at MINILM_REVISION into the hub cache and write refs/main = MINILM_REVISION, so an
    offline load of the default revision finds them. Returns the snapshot folder; ModelCheckError when a file the
    load needs is missing afterwards."""
    if download is None:
        from huggingface_hub import snapshot_download  # pylint: disable=import-outside-toplevel
        download = snapshot_download
    cache = Path(cache_dir) if cache_dir is not None else hub_cache()
    folder = Path(download(repo_id=MINILM_REPO_ID, revision=MINILM_REVISION, allow_patterns=list(MINILM_PATTERNS),
                           cache_dir=str(cache)))
    if folder.name != MINILM_REVISION or folder.parent.name != "snapshots":
        raise ModelCheckError(f"unexpected snapshot folder {folder} for revision {MINILM_REVISION}")
    # huggingface_hub writes no ref for a download by commit id, and reads a ref without stripping it: no newline
    ref = folder.parent.parent / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(MINILM_REVISION, encoding="ascii")
    missing = [name for name in MINILM_REQUIRED if not (folder / name).is_file()]
    if missing:
        raise ModelCheckError(f"MiniLM download incomplete (missing: {', '.join(missing)})")
    return folder


# ---------------------------------------------------------------- the checks


def load_minilm() -> Callable[[list[str]], Any]:
    """The agent's MiniLM embedding function, loaded now (the class and name VectorMemory._load_model uses)."""
    from services.ai_core import jarvis_vector_memory as jvm  # pylint: disable=import-outside-toplevel
    return jvm.SentenceTransformerEmbeddingFunction(model_name=jvm.MODEL_NAME)


def check_minilm(load: Callable[[], Callable[[list[str]], Any]] = load_minilm) -> str:
    """Loads MiniLM and embeds one sentence; the result line, or ModelCheckError."""
    embed = load()
    vectors = embed(["JARVIS offline model check"])
    vector = [float(value) for value in vectors[0]]
    if len(vector) != MINILM_DIM:
        raise ModelCheckError(f"MiniLM gave {len(vector)} numbers, not {MINILM_DIM}")
    if not all(math.isfinite(value) for value in vector):
        raise ModelCheckError("MiniLM gave a non-finite number")
    if math.sqrt(sum(value * value for value in vector)) < MIN_SIGNAL:
        raise ModelCheckError("MiniLM gave a zero vector")
    return f"{MINILM_NAME}, {len(vector)} dimensions"


def tone_samples() -> Any:
    """One second of a 220 Hz tone at 16 kHz, float32 in [-0.5, 0.5]."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    t = np.arange(int(TONE_RATE * TONE_SECONDS), dtype=np.float32) / TONE_RATE
    return (0.5 * np.sin(2.0 * np.pi * TONE_HZ * t)).astype(np.float32)


def load_ecapa() -> Any:
    """The agent's voice-ID engine with no enrolled voice (a master voice path that does not exist), loaded now."""
    from services.ai_core import voice_fingerprint as vf  # pylint: disable=import-outside-toplevel
    master = Path(tempfile.gettempdir()) / "jarvis-model-check" / vf.MASTER_VOICE_FILE
    return vf.VoiceFingerprintEngine(str(master))


def check_ecapa(load: Callable[[], Any] = load_ecapa, tone: Callable[[], Any] = tone_samples) -> str:
    """Loads ECAPA and embeds the test tone; the result line, or ModelCheckError."""
    engine = load()
    state = getattr(engine, "state", None)
    if state not in ECAPA_STATES_OK:
        raise ModelCheckError(f"ECAPA engine state {state}: {getattr(engine, 'error', None) or 'no detail'}")
    # The path enroll and verify use: resample, the engine's torch thread count, L2 normalisation
    embedding = engine._embed_signal_sync(tone(), TONE_RATE)  # pylint: disable=protected-access
    vector = [float(value) for value in embedding]
    if len(vector) != ECAPA_DIM:
        raise ModelCheckError(f"ECAPA gave {len(vector)} numbers, not {ECAPA_DIM}")
    if not all(math.isfinite(value) for value in vector):
        raise ModelCheckError("ECAPA gave a non-finite number")
    norm = math.sqrt(sum(value * value for value in vector))
    if abs(norm - 1.0) > 1e-3:
        raise ModelCheckError(f"ECAPA embedding is not a unit vector (norm {norm:.4f})")
    return f"{ECAPA_NAME}, {len(vector)} dimensions, engine state {state}"


DEFAULT_CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (("MiniLM", check_minilm), ("ECAPA", check_ecapa))


def _current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise ModelCheckError("--expect-uid needs a POSIX system")
    return int(getuid())


def run_checks(checks: Sequence[tuple[str, Callable[[], str]]]) -> bool:
    """Runs every check, prints one line each; True when all passed."""
    ok = True
    for label, check in checks:
        started = time.monotonic()
        try:
            detail = check()
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a model failure is a result here
            ok = False
            print(f"{label} FAIL: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            continue
        print(f"{label} ok: {detail} ({time.monotonic() - started:.1f} s)", flush=True)
    return ok


def main(argv: Sequence[str] | None = None, *, checks: Sequence[tuple[str, Callable[[], str]]] | None = None,
         bake: Callable[[], Path] | None = None, getuid: Callable[[], int] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline load check of the agent image's MiniLM and ECAPA models.")
    parser.add_argument("--bake", action="store_true",
                        help="image build only: download MiniLM at the pinned revision into the hub cache")
    parser.add_argument("--allow-online", action="store_true",
                        help="run the check although HF_HUB_OFFLINE is not on")
    parser.add_argument("--expect-uid", type=int, default=None, help="fail unless this process runs as this uid")
    args = parser.parse_args(argv)

    if args.bake:
        if offline_mode():
            print(f"check_models --bake needs the network, but {OFFLINE_ENV} is on", file=sys.stderr)
            return EXIT_USAGE
        try:
            folder = (bake or bake_minilm)()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"MiniLM bake FAIL: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
            return EXIT_FAILED
        print(f"MiniLM baked: {MINILM_REPO_ID} at {MINILM_REVISION} in {folder}", flush=True)
        return EXIT_OK

    if not offline_mode() and not args.allow_online:
        print(f"check_models proves the offline load: set {OFFLINE_ENV}=1 (or pass --allow-online)", file=sys.stderr)
        return EXIT_USAGE
    ok = True
    try:
        uid: int | str = (getuid or _current_uid)()
    except ModelCheckError as exc:
        uid = "n/a"
        if args.expect_uid is not None:
            print(f"uid FAIL: {exc}", flush=True)
            ok = False
    print(f"check_models: uid={uid} {OFFLINE_ENV}={'1' if offline_mode() else '0'}", flush=True)
    if args.expect_uid is not None and isinstance(uid, int) and uid != args.expect_uid:
        print(f"uid FAIL: running as {uid}, expected {args.expect_uid}", flush=True)
        ok = False
    ok = run_checks(DEFAULT_CHECKS if checks is None else checks) and ok
    print("MODELS_OK" if ok else "MODELS_FAIL", flush=True)
    return EXIT_OK if ok else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
