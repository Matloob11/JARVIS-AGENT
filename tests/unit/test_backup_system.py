"""Tests for services/utils/backup_system.py and scripts/ops/backup_local.py (plan C-11 / OP-07)."""
from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import pytest

from services.utils import backup_system as bs
from services.utils.backup_system import JarvisBackup

PROJECT_ROOT = Path(bs.config.project_root)


@pytest.fixture(name="fake_project")
def fixture_fake_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    files = {
        ".env": "A=1\n",
        "deploy/windows-local/.env": "B=2\n",
        "conversations/chat.json": "{}",
        "conversations/__pycache__/x.cpython-312.pyc": "\0",
        "logs/jarvis.log": "line\n",
        "logs/other.txt": "not a .log file\n",
        "plan.md": "# plan\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _load_cli():
    path = PROJECT_ROOT / "scripts" / "ops" / "backup_local.py"
    spec = importlib.util.spec_from_file_location("backup_local_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ defaults
def test_default_sources_cover_the_runtime_data(monkeypatch):
    monkeypatch.delenv("JARVIS_BACKUP_DIR", raising=False)
    backup = JarvisBackup()
    expected = [
        ".env", "deploy/windows-local/.env", "conversations", "chroma_db", "data/identity",
        "Jarvis_Outputs", "backups/checkpoints", "logs/*.log", "logs/telemetry.jsonl",
        "plan.md", "done_highlight.md",
    ]
    assert backup.source_dirs == [os.path.abspath(os.path.join(PROJECT_ROOT, rel)) for rel in expected]
    assert all(os.path.isabs(p) for p in backup.source_dirs)


def test_default_backup_root_is_absolute_under_userprofile(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_BACKUP_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    backup = JarvisBackup()
    assert backup.backup_root == os.path.abspath(tmp_path / "JARVIS-backups" / "local")
    assert os.path.isabs(backup.backup_root)
    assert not os.path.exists(backup.backup_root)  # the constructor creates nothing


def test_jarvis_backup_dir_wins_and_relative_paths_become_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_BACKUP_DIR", str(tmp_path / "custom"))
    assert JarvisBackup().backup_root == os.path.abspath(tmp_path / "custom")
    monkeypatch.setenv("JARVIS_BACKUP_DIR", "rel_backups")
    assert JarvisBackup(project_root=str(tmp_path)).backup_root == os.path.abspath(tmp_path / "rel_backups")
    # an explicit relative backup_root (old API default "backups") stays under the project root
    assert JarvisBackup(backup_root="backups", project_root=str(tmp_path)).backup_root == \
        os.path.abspath(tmp_path / "backups")


# ------------------------------------------------------------------ zip backup
def test_perform_backup_zip_has_env_and_conversations(fake_project, tmp_path):
    dest = tmp_path / "out"
    backup = JarvisBackup(backup_root=str(dest), project_root=str(fake_project))
    path = backup.perform_backup()
    assert path and re.fullmatch(r"backup_\d{8}_\d{6}\.zip", os.path.basename(path))
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert {".env", "deploy/windows-local/.env", "conversations/chat.json", "logs/jarvis.log", "plan.md"} <= names
    assert "logs/other.txt" not in names  # only logs/*.log
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert any("identity" in w for w in backup.last_warnings)  # a missing source only warns
    assert sorted(os.listdir(dest)) == [os.path.basename(path)]  # no .partial / temp leftovers


def test_sqlite_is_copied_consistently_without_wal_files(fake_project, tmp_path):
    db_dir = fake_project / "chroma_db"
    db_dir.mkdir()
    writer = sqlite3.connect(db_dir / "chroma.sqlite3")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE t (x INTEGER)")
        writer.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
        writer.commit()  # rows sit in the -wal file while this connection stays open
        assert (db_dir / "chroma.sqlite3-wal").exists()
        path = JarvisBackup(backup_root=str(tmp_path / "out"), project_root=str(fake_project)).perform_backup()
    finally:
        writer.close()
    assert path
    restored = tmp_path / "restored.sqlite3"
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert "chroma_db/chroma.sqlite3" in names
        assert not any(n.endswith(("-wal", "-shm")) for n in names)
        restored.write_bytes(zf.read("chroma_db/chroma.sqlite3"))
    con = sqlite3.connect(restored)
    try:
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 100
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_backup_root_inside_a_source_is_never_copied_into_itself(fake_project):
    backup = JarvisBackup(backup_root=str(fake_project / "conversations" / "bk"), project_root=str(fake_project))
    assert backup.perform_backup()
    second = backup.perform_backup()
    assert second
    with zipfile.ZipFile(second) as zf:
        assert not any(n.startswith("conversations/bk") for n in zf.namelist())


def test_source_inside_the_backup_root_is_skipped_with_a_warning(fake_project):
    (fake_project / "bk" / "checkpoints").mkdir(parents=True)
    (fake_project / "bk" / "checkpoints" / "cp.json").write_text("{}")
    backup = JarvisBackup(source_dirs=["bk/checkpoints", "plan.md"], backup_root="bk", project_root=str(fake_project))
    path = backup.perform_backup()
    assert path
    assert any("inside the backup folder" in w and "checkpoints" in w for w in backup.last_warnings)
    with zipfile.ZipFile(path) as zf:
        assert "plan.md" in zf.namelist()
        assert not any(n.startswith("bk/") for n in zf.namelist())


def test_backup_without_any_existing_source_fails(tmp_path):
    backup = JarvisBackup(source_dirs=["missing.txt"], backup_root=str(tmp_path / "out"), project_root=str(tmp_path))
    assert backup.perform_backup() is None
    assert not list((tmp_path / "out").iterdir())


# ------------------------------------------------------------------ rotation
def test_rotation_deletes_only_old_backup_zips(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    stamps = [f"202609{day:02d}_120000" for day in range(1, 31)] + [f"202610{day:02d}_120000" for day in range(1, 6)]
    for stamp in stamps:
        (root / f"backup_{stamp}.zip").write_bytes(b"zip")
    (root / "backup_20260901_120000_2.zip").write_bytes(b"zip")  # same-second suffix
    others = ["other.zip", "backup_notes.zip", "backup_20250101.zip", "mybackup_20250101_000000.zip",
              "tree_20250101_000000.tar.gz", "backup_20250101_000000.zip.partial", "BACKUP_20250101_000000.ZIP",
              "JARVIS-AGENT-main_2026-09-30_2340.tgz"]
    for name in others:
        (root / name).write_bytes(b"keep")
    (root / "backup_20200101_000000.zip").mkdir()  # a folder, not a file
    (root / "nested").mkdir()
    (root / "nested" / "backup_20200101_000000.zip").write_bytes(b"keep")

    removed = JarvisBackup(backup_root=str(root)).rotate_backups(keep=30)

    expected_removed = {"backup_20260901_120000.zip", "backup_20260901_120000_2.zip", "backup_20260902_120000.zip",
                        "backup_20260903_120000.zip", "backup_20260904_120000.zip", "backup_20260905_120000.zip"}
    assert {os.path.basename(p) for p in removed} == expected_removed
    assert len([p for p in root.glob("backup_2026*.zip") if p.is_file()]) == 30
    for name in others:
        assert (root / name).exists(), name
    assert (root / "backup_20200101_000000.zip").is_dir()
    assert (root / "nested" / "backup_20200101_000000.zip").exists()


def test_rotation_refuses_keep_below_one_and_tolerates_a_missing_root(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    (root / "backup_20260101_000000.zip").write_bytes(b"x")
    assert not JarvisBackup(backup_root=str(root)).rotate_backups(keep=0)
    assert (root / "backup_20260101_000000.zip").exists()
    assert not JarvisBackup(backup_root=str(tmp_path / "missing")).rotate_backups()


# ------------------------------------------------------------------ full tree + listing
def test_tree_backup_skips_venv_node_modules_models_and_caches(tmp_path):
    root = tmp_path / "JARVIS-AGENT-main"
    for rel in ("src/app.py", ".venv_312/lib/x.py", "stonix_ui/node_modules/pkg/index.js", "stonix_ui/src/main.tsx",
                "pretrained_models/m.bin", "services/__pycache__/a.pyc", ".git/HEAD", "Jarvis_Outputs/.keep"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    (root / "empty_dir").mkdir()
    backup = JarvisBackup(backup_root=str(tmp_path / "out"), project_root=str(root))

    path = backup.perform_tree_backup()
    assert path and re.fullmatch(r"tree_\d{8}_\d{6}\.tar\.gz", os.path.basename(path))
    with tarfile.open(path) as tar:
        names = set(tar.getnames())
    top = "JARVIS-AGENT-main"
    assert {f"{top}/src/app.py", f"{top}/stonix_ui/src/main.tsx", f"{top}/.git/HEAD", f"{top}/empty_dir"} <= names
    assert not any(n.startswith((f"{top}/.venv_312", f"{top}/stonix_ui/node_modules", f"{top}/pretrained_models"))
                   for n in names)
    assert not any("__pycache__" in n for n in names)

    without_git = backup.perform_tree_backup(include_git=False)
    assert without_git and without_git != path
    with tarfile.open(without_git) as tar:
        assert not any(n.startswith(f"{top}/.git/") or n == f"{top}/.git" for n in tar.getnames())

    backup.rotate_backups(keep=1)  # tree archives are never rotated
    assert os.path.exists(path) and os.path.exists(without_git)


def test_list_backups_newest_first(tmp_path):
    root = tmp_path / "b"
    root.mkdir()
    for name in ("backup_20260101_000000.zip", "backup_20260102_000000.zip", "tree_20260103_000000.tar.gz", "notes.txt"):
        (root / name).write_bytes(b"x")
    rows = JarvisBackup(backup_root=str(root)).list_backups()
    assert [r["name"] for r in rows] == ["tree_20260103_000000.tar.gz", "backup_20260102_000000.zip",
                                         "backup_20260101_000000.zip"]
    assert [r["kind"] for r in rows] == ["tree", "data", "data"]


# ------------------------------------------------------------------ CLI wiring
def test_cli_list_now_and_exit_codes(monkeypatch, tmp_path, capsys):
    cli = _load_cli()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("JARVIS_BACKUP_DIR", raising=False)

    assert cli.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "Local backups in" in out and "VPS snapshot mirrors in" in out

    class FakeOk(bs.JarvisBackup):
        def perform_backup(self):
            os.makedirs(self.backup_root, exist_ok=True)
            path = os.path.join(self.backup_root, "backup_20260101_000000.zip")
            Path(path).write_bytes(b"zip")
            return path

    class FakeFail(bs.JarvisBackup):
        def perform_backup(self):
            return None

    monkeypatch.setattr(bs, "JarvisBackup", FakeOk)
    assert cli.main(["--now", "--dest", str(tmp_path / "dest")]) == 0
    assert "BACKUP_OK" in capsys.readouterr().out
    monkeypatch.setattr(bs, "JarvisBackup", FakeFail)
    assert cli.main(["--now", "--dest", str(tmp_path / "dest")]) == 1


def test_cli_requires_an_action():
    cli = _load_cli()
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
