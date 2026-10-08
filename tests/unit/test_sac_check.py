"""scripts/pc/sac_check.py: the Smart App Control check for the venv (C-33 SAC note, 08 Oct 2026)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("sac_check", ROOT / "scripts" / "pc" / "sac_check.py")
sac_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sac_check)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pyarrow").mkdir()
    for name in ("pkg/good.pyd", "pkg/bad.dll", "pkg/odd.pyd", "pyarrow/_dataset_parquet_encryption.cp312-win_amd64.pyd",
                 "pkg/readme.txt"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_scan_sorts_files_into_mapped_blocked_and_other_errors(tmp_path):
    root = _tree(tmp_path)
    codes = {"good.pyd": 0, "bad.dll": 4551, "odd.pyd": 193, "_dataset_parquet_encryption.cp312-win_amd64.pyd": 4551}
    ok, blocked, other = sac_check.scan([root], lambda path: codes[Path(path).name])
    assert ok == 1
    assert sorted(blocked) == ["pkg/bad.dll", "pyarrow/_dataset_parquet_encryption.cp312-win_amd64.pyd"]
    assert other == {193: 1}  # a non-policy failure (e.g. a 32-bit DLL) is counted, never reported as blocked


def test_main_fails_only_for_files_jarvis_may_load(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path)
    monkeypatch.setattr(sac_check.sys, "platform", "win32")
    monkeypatch.setattr(sac_check, "_loader", lambda: (lambda path: 4551 if "parquet" in path else 0))
    assert sac_check.main([str(root)]) == 0  # only the known-unused pyarrow module is blocked
    assert "UNUSED   pyarrow/_dataset_parquet_encryption.cp312-win_amd64.pyd" in capsys.readouterr().out

    monkeypatch.setattr(sac_check, "_loader", lambda: (lambda path: 4551 if path.endswith("bad.dll") else 0))
    assert sac_check.main([str(root)]) == 1
    assert "BLOCKED  pkg/bad.dll" in capsys.readouterr().out


def test_main_refuses_a_missing_path_and_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(sac_check.sys, "platform", "win32")
    assert sac_check.main([str(tmp_path / "nope")]) == 2
    monkeypatch.setattr(sac_check.sys, "platform", "linux")
    assert sac_check.main([str(tmp_path)]) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows loader")
def test_the_real_loader_maps_pythons_own_dll():
    dll = Path(sys.base_prefix) / "python3.dll"
    if not dll.exists():
        pytest.skip("python3.dll not next to the interpreter")
    assert sac_check._loader()(str(dll)) == 0
