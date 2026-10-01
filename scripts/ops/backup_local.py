"""PC backups for JARVIS (plan task C-11 / OP-07).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/backup_local.py --now        # data zip + rotation (keep 30)
    .venv_312/Scripts/python.exe scripts/ops/backup_local.py --full-tree  # tar.gz of the whole repo
    .venv_312/Scripts/python.exe scripts/ops/backup_local.py --list       # local backups + VPS mirrors

--now        zips .env, deploy/windows-local/.env, conversations/, chroma_db/, data/identity/,
             Jarvis_Outputs/, backups/checkpoints/, logs/*.log, logs/telemetry.jsonl, plan.md and
             done_highlight.md into backup_<ts>.zip, then keeps the newest --keep (default 30)
             backup_<ts>.zip files. It never deletes any other file.
--full-tree  writes tree_<ts>.tar.gz of the repo without .venv_312, stonix_ui/node_modules,
             pretrained_models and caches (--no-git also leaves out .git). Rotation never deletes it.
--list       lists backup_*.zip / tree_*.tar.gz, other archives in JARVIS-backups/ and the VPS
             snapshot mirrors in JARVIS-backups/vps/.

Target folder: --dest, else JARVIS_BACKUP_DIR, else %USERPROFILE%/JARVIS-backups/local.
Restore steps: deploy/hostinger/RUNBOOK.md ("Backup & Restore").
Exit code: 0 ok, 1 a backup failed, 2 usage error.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHIVE_SUFFIXES = (".tgz", ".tar.gz", ".zip")


def _human(size: float) -> str:
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB"):
        size /= 1024
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"


def _when(mtime: float) -> str:
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def backups_home() -> Path:
    """%USERPROFILE%/JARVIS-backups (home of local/, vps/ and the older one-off archives)."""
    return Path(os.getenv("USERPROFILE") or os.path.expanduser("~")) / "JARVIS-backups"


def _dir_stats(path: Path) -> tuple[int, int]:
    files = size = 0
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            size += item.stat().st_size
    return files, size


def print_listing(backup) -> None:
    rows = backup.list_backups()
    print(f"Local backups in {backup.backup_root} ({len(rows)})")
    for row in rows:
        print(f"  {_when(row['mtime'])}  {row['kind']:<4}  {_human(row['size']):>9}  {row['name']}")
    if not rows:
        print("  (none yet)")

    home = backups_home()
    others = sorted(p for p in home.glob("*") if p.is_file() and p.name.endswith(ARCHIVE_SUFFIXES)) \
        if home.is_dir() else []
    if others:
        print(f"Other PC archives in {home} ({len(others)})")
        for item in others:
            stat = item.stat()
            print(f"  {_when(stat.st_mtime)}  {_human(stat.st_size):>9}  {item.name}")

    mirror = home / "vps"
    snapshots = sorted(p for p in mirror.iterdir() if p.is_dir() and not p.name.startswith(".")) \
        if mirror.is_dir() else []
    print(f"VPS snapshot mirrors in {mirror} ({len(snapshots)})")
    for snap in snapshots:
        files, size = _dir_stats(snap)
        sums = "  SHA256SUMS" if (snap / "SHA256SUMS").is_file() else ""
        print(f"  {snap.name:<40} {files:>3} files  {_human(size):>9}{sums}")
    if not snapshots:
        print("  (none yet)")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backup_local.py",
        description="JARVIS PC backups: data zip (--now), full repo tarball (--full-tree), listing (--list).",
    )
    parser.add_argument("--now", action="store_true",
                        help="zip the runtime data to backup_<ts>.zip, then rotate (keep --keep)")
    parser.add_argument("--full-tree", action="store_true",
                        help="tar.gz of the repo without .venv_312, stonix_ui/node_modules, pretrained_models")
    parser.add_argument("--list", action="store_true", help="list local backups and VPS snapshot mirrors")
    parser.add_argument("--keep", type=int, default=30,
                        help="backup_<ts>.zip files kept by rotation after --now (default 30)")
    parser.add_argument("--dest", help="target folder (default JARVIS_BACKUP_DIR or %%USERPROFILE%%/JARVIS-backups/local)")
    parser.add_argument("--no-git", action="store_true", help="with --full-tree: leave out .git")
    args = parser.parse_args(argv)
    if not (args.now or args.full_tree or args.list):
        parser.error("choose at least one of --now, --full-tree, --list")
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest = os.path.abspath(args.dest) if args.dest else None
    previous_cwd = os.getcwd()
    os.chdir(ROOT)  # jarvis_logger opens logs/*.log relative to the working directory
    try:
        from services.utils import backup_system  # pylint: disable=import-outside-toplevel

        backup = backup_system.JarvisBackup(backup_root=dest)
        ok = True
        if args.now:
            path = backup.perform_backup()
            if path:
                print(f"BACKUP_OK {path} ({_human(os.path.getsize(path))}, "
                      f"{len(backup.last_warnings)} warnings)")
                removed = backup.rotate_backups(keep=args.keep)
                print(f"ROTATION kept the newest {args.keep} backup_<ts>.zip; removed {len(removed)}")
            else:
                print("BACKUP_FAILED (see the log lines above)", file=sys.stderr)
                ok = False
        if args.full_tree:
            path = backup.perform_tree_backup(include_git=not args.no_git)
            if path:
                print(f"TREE_OK {path} ({_human(os.path.getsize(path))}, "
                      f"{len(backup.last_warnings)} warnings)")
            else:
                print("TREE_FAILED (see the log lines above)", file=sys.stderr)
                ok = False
        if args.list:
            print_listing(backup)
        return 0 if ok else 1
    finally:
        os.chdir(previous_cwd)


if __name__ == "__main__":
    sys.exit(main())
