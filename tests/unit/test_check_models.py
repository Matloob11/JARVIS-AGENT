"""C-126 (K-75): scripts/models/check_models.py, the offline model check of the VPS agent image and its MiniLM bake.

No real model is loaded and nothing is downloaded: the checks get fake loaders, the bake a fake snapshot_download
that writes the hub cache layout under tmp_path. One test asks the installed huggingface_hub itself, offline,
whether the baked cache resolves the default revision, which is what the agent's SentenceTransformer call needs.
"""
# pylint: disable=protected-access
from __future__ import annotations

import importlib.util
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "models" / "check_models.py"


def _load():
    spec = importlib.util.spec_from_file_location("jarvis_check_models", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cm = _load()


def fake_snapshot_download(calls: list[dict] | None = None, skip: tuple[str, ...] = ()):
    """A snapshot_download that writes MINILM_REQUIRED (minus skip) where the hub cache keeps a snapshot."""
    def download(*, repo_id, revision, allow_patterns, cache_dir):
        if calls is not None:
            calls.append({"repo_id": repo_id, "revision": revision, "allow_patterns": allow_patterns,
                          "cache_dir": cache_dir})
        folder = Path(cache_dir) / ("models--" + repo_id.replace("/", "--")) / "snapshots" / revision
        for name in cm.MINILM_REQUIRED:
            if name not in skip:
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_text("{}" if name.endswith(".json") else "x", encoding="utf-8")
        return str(folder)
    return download


# ---------------------------------------------------------------- bake


def test_bake_downloads_the_pinned_revision_and_points_main_at_it(tmp_path):
    calls: list[dict] = []
    folder = cm.bake_minilm(tmp_path, download=fake_snapshot_download(calls))
    assert calls == [{"repo_id": "sentence-transformers/all-MiniLM-L6-v2", "revision": cm.MINILM_REVISION,
                      "allow_patterns": list(cm.MINILM_PATTERNS), "cache_dir": str(tmp_path)}]
    assert re.fullmatch(r"[0-9a-f]{40}", cm.MINILM_REVISION)
    ref = tmp_path / "models--sentence-transformers--all-MiniLM-L6-v2" / "refs" / "main"
    assert ref.read_bytes() == cm.MINILM_REVISION.encode("ascii")  # no newline: huggingface_hub reads it raw
    assert folder == ref.parent.parent / "snapshots" / cm.MINILM_REVISION


def test_bake_fetches_no_onnx_openvino_tf_or_rust_copy():
    from fnmatch import fnmatch  # pylint: disable=import-outside-toplevel
    unwanted = ("onnx/model.onnx", "onnx/model_qint8_avx512.onnx", "openvino/openvino_model.bin",
                "pytorch_model.bin", "tf_model.h5", "rust_model.ot", "train_script.py")
    for path in unwanted:
        assert not any(fnmatch(path, pattern) for pattern in cm.MINILM_PATTERNS), path
    for path in cm.MINILM_REQUIRED:
        assert any(fnmatch(path, pattern) for pattern in cm.MINILM_PATTERNS), path


def test_bake_fails_when_a_needed_file_did_not_arrive(tmp_path):
    with pytest.raises(cm.ModelCheckError, match="model.safetensors"):
        cm.bake_minilm(tmp_path, download=fake_snapshot_download(skip=("model.safetensors",)))


def test_the_baked_cache_resolves_offline_with_the_real_huggingface_hub(tmp_path):
    huggingface_hub = pytest.importorskip("huggingface_hub")
    folder = cm.bake_minilm(tmp_path, download=fake_snapshot_download())
    # what sentence-transformers and transformers do for revision=None (main), with no network
    path = huggingface_hub.hf_hub_download(cm.MINILM_REPO_ID, "modules.json", cache_dir=str(tmp_path),
                                           local_files_only=True)
    assert Path(path) == folder / "modules.json"
    assert Path(huggingface_hub.snapshot_download(cm.MINILM_REPO_ID, cache_dir=str(tmp_path),
                                                  local_files_only=True)) == folder


def test_bake_needs_no_jarvis_code(tmp_path):
    """The Dockerfile bakes before src/ and services/ are copied: the bake path imports neither."""
    code = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('cm', {str(SCRIPT)!r})\n"
        "cm = importlib.util.module_from_spec(spec); spec.loader.exec_module(cm)\n"
        "def download(*, repo_id, revision, allow_patterns, cache_dir):\n"
        "    import pathlib\n"
        "    folder = pathlib.Path(cache_dir) / 'models--x' / 'snapshots' / revision\n"
        "    for name in cm.MINILM_REQUIRED:\n"
        "        (folder / name).parent.mkdir(parents=True, exist_ok=True); (folder / name).write_text('x')\n"
        "    return str(folder)\n"
        f"cm.bake_minilm({str(tmp_path)!r}, download=download)\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in ('services', 'src'))))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=120,
                          check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []


def test_main_bake_refuses_offline_and_reports_the_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert cm.main(["--bake"], bake=lambda: tmp_path) == cm.EXIT_USAGE
    monkeypatch.delenv("HF_HUB_OFFLINE")
    assert cm.main(["--bake"], bake=lambda: tmp_path) == cm.EXIT_OK
    assert f"MiniLM baked: {cm.MINILM_REPO_ID} at {cm.MINILM_REVISION}" in capsys.readouterr().out

    def broken() -> Path:
        raise OSError("network down")

    assert cm.main(["--bake"], bake=broken) == cm.EXIT_FAILED
    assert "MiniLM bake FAIL: OSError: network down" in capsys.readouterr().out


# ---------------------------------------------------------------- the check


def ok_check() -> str:
    return "fine"


def test_check_refuses_without_offline_mode_unless_allowed(monkeypatch, capsys):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert cm.main([], checks=[("A", ok_check)], getuid=lambda: 10001) == cm.EXIT_USAGE
    assert "HF_HUB_OFFLINE=1" in capsys.readouterr().err
    assert cm.main(["--allow-online"], checks=[("A", ok_check)], getuid=lambda: 10001) == cm.EXIT_OK


@pytest.mark.parametrize("value", ["1", "true", "YES", "On"])
def test_offline_mode_values(monkeypatch, value):
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    assert cm.offline_mode()


def test_every_check_runs_and_one_failure_fails_the_run(monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    def broken() -> str:
        raise cm.ModelCheckError("ECAPA engine state model_missing: not installed")

    assert cm.main([], checks=[("ECAPA", broken), ("MiniLM", ok_check)], getuid=lambda: 10001) == cm.EXIT_FAILED
    out = capsys.readouterr().out
    assert "check_models: uid=10001 HF_HUB_OFFLINE=1" in out
    assert "ECAPA FAIL: ModelCheckError: ECAPA engine state model_missing" in out
    assert "MiniLM ok: fine" in out and out.rstrip().endswith("MODELS_FAIL")
    assert cm.main([], checks=[("MiniLM", ok_check)], getuid=lambda: 10001) == cm.EXIT_OK
    assert capsys.readouterr().out.rstrip().endswith("MODELS_OK")


def test_expect_uid(monkeypatch, capsys):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert cm.main(["--expect-uid", "10001"], checks=[("A", ok_check)], getuid=lambda: 0) == cm.EXIT_FAILED
    assert "uid FAIL: running as 0, expected 10001" in capsys.readouterr().out
    assert cm.main(["--expect-uid", "10001"], checks=[("A", ok_check)], getuid=lambda: 10001) == cm.EXIT_OK

    def no_posix() -> int:
        raise cm.ModelCheckError("--expect-uid needs a POSIX system")

    assert cm.main(["--expect-uid", "10001"], checks=[("A", ok_check)], getuid=no_posix) == cm.EXIT_FAILED
    assert cm.main([], checks=[("A", ok_check)], getuid=no_posix) == cm.EXIT_OK  # without the flag only shown


def test_check_minilm_wants_384_finite_numbers():
    assert cm.check_minilm(lambda: lambda texts: [[0.1] * 384]) == "all-MiniLM-L6-v2, 384 dimensions"
    with pytest.raises(cm.ModelCheckError, match="10 numbers"):
        cm.check_minilm(lambda: lambda texts: [[0.1] * 10])
    with pytest.raises(cm.ModelCheckError, match="non-finite"):
        cm.check_minilm(lambda: lambda texts: [[math.nan] * 384])
    with pytest.raises(cm.ModelCheckError, match="zero vector"):
        cm.check_minilm(lambda: lambda texts: [[0.0] * 384])


class FakeEngine:
    def __init__(self, state: str, vector: list[float], error: str | None = None) -> None:
        self.state, self.error, self._vector, self.calls = state, error, vector, []

    def _embed_signal_sync(self, signal, sample_rate):
        self.calls.append((len(signal), sample_rate))
        return self._vector


def test_check_ecapa_wants_a_loaded_engine_and_a_unit_vector():
    unit = [1.0] + [0.0] * 191
    engine = FakeEngine("not_enrolled", unit)
    assert cm.check_ecapa(lambda: engine, tone=lambda: [0.0] * 16000) == \
        "spkrec-ecapa-voxceleb, 192 dimensions, engine state not_enrolled"
    assert engine.calls == [(16000, 16000)]
    assert cm.check_ecapa(lambda: FakeEngine("ready", unit), tone=lambda: [0.0]).endswith("engine state ready")
    with pytest.raises(cm.ModelCheckError, match="model_missing: Voice-ID model not installed"):
        cm.check_ecapa(lambda: FakeEngine("model_missing", unit, "Voice-ID model not installed"), tone=lambda: [0.0])
    with pytest.raises(cm.ModelCheckError, match="engine_error"):
        cm.check_ecapa(lambda: FakeEngine("engine_error", unit), tone=lambda: [0.0])
    with pytest.raises(cm.ModelCheckError, match="not a unit vector"):
        cm.check_ecapa(lambda: FakeEngine("ready", [2.0] + [0.0] * 191), tone=lambda: [0.0])
    with pytest.raises(cm.ModelCheckError, match="10 numbers"):
        cm.check_ecapa(lambda: FakeEngine("ready", [1.0] + [0.0] * 9), tone=lambda: [0.0])


def test_the_tone_is_one_second_at_16_khz():
    pytest.importorskip("numpy")
    samples = cm.tone_samples()
    assert len(samples) == 16000 and float(abs(samples).max()) <= 0.5 and str(samples.dtype) == "float32"


def test_the_checked_names_are_the_agents():
    vector_memory = (ROOT / "services" / "ai_core" / "jarvis_vector_memory.py").read_text(encoding="utf-8")
    assert f'MODEL_NAME = "{cm.MINILM_NAME}"' in vector_memory
    assert cm.MINILM_REPO_ID.endswith("/" + cm.MINILM_NAME)
    voice = (ROOT / "services" / "ai_core" / "voice_fingerprint.py").read_text(encoding="utf-8")
    assert f'MODEL_DIR_NAME = "{cm.ECAPA_NAME}"' in voice
    assert "def _embed_signal_sync(self" in voice and "class VoiceFingerprintEngine" in voice
    assert re.search(r'STATE_NOT_ENROLLED = "not_enrolled"', voice) and re.search(r'STATE_READY = "ready"', voice)
