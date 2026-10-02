"""
Writes or checks the K-71 desktop manifest (services/ai_core/desktop_manifest.json) from the live
tool registry. The manifest is baked into the agent image; the VPS brain builds its laptop-tool
proxies from it, and the Desktop Node compares its parameter-schema hashes at exec time.

Run on Windows from the repo root (the laptop imports every tool module):
    .venv_312/Scripts/python.exe scripts/desktop/export_manifest.py           # write when changed
    .venv_312/Scripts/python.exe scripts/desktop/export_manifest.py --check   # exit 1 when stale

The manifest describes the committed tree (K-71), but discovery imports the working tree, which
Cursor edits at the same time. A write is therefore refused while a registry file (a module that
registers a tool, the placement table or the plugin manager) has uncommitted changes, unless
--allow-dirty says those changes go into the same commit as the manifest. --check only warns.

Discovery runs like the PC brain (local mode) with JARVIS_TEST_MODE=true, a dead bridge URL, a temp
log file and no placement overrides, so nothing is sent anywhere and the live agent's logs are not
touched. git_sha records HEAD when the content last changed; it is left out of manifest_sha256 and
of the --check comparison, so an unchanged registry never rewrites the file.

Exit codes: 0 up to date or written, 1 stale (--check), a tool the manifest cannot describe or a
write refused for uncommitted registry files, 2 the registry could not be built (not Windows, or
discovery failed).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# pylint: disable=wrong-import-position
from services.ai_core import tool_placement  # noqa: E402  (stdlib only: no settings are loaded)

DEAD_BRIDGE_URL = "http://127.0.0.1:9"
# Besides the tool modules, these shape every manifest entry (cls and placement, meta defaults).
SHAPING_MODULES = ("services.ai_core.tool_placement", "services.ai_core.jarvis_plugin_manager")


def _isolate_environment() -> None:
    """Offline discovery in local mode before any services module loads the settings."""
    os.environ["JARVIS_TEST_MODE"] = "true"
    os.environ["UI_BRIDGE_URL"] = DEAD_BRIDGE_URL
    log_dir = os.path.join(tempfile.gettempdir(), "jarvis_tests")
    os.makedirs(log_dir, exist_ok=True)
    os.environ["LOG_FILE"] = os.path.join(log_dir, "export_manifest.log")
    # Set, not removed: the settings loader never overrides a variable that already exists.
    os.environ["JARVIS_DESKTOP_MODE"] = "local"
    os.environ[tool_placement.OVERRIDES_ENV] = ""


def head_sha() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def live_manifest(git_sha: str) -> dict[str, Any]:
    """The manifest of the registry a local-mode discovery builds in this process."""
    _isolate_environment()
    # pylint: disable=import-outside-toplevel
    from services.ai_core.jarvis_plugin_manager import plugin_manager
    plugin_manager.discover_plugins(os.path.join(ROOT, "services"))
    return tool_placement.build_manifest(plugin_manager.get_tools(), git_sha=git_sha)


def repo_relative(path: str) -> str | None:
    """path relative to the repo root with forward slashes; None for a path outside the repo."""
    try:
        rel = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:  # another drive
        return None
    return None if rel == ".." or rel.startswith(".." + os.sep) else rel.replace(os.sep, "/")


def registry_files(live: dict[str, Any]) -> list[str]:
    """The repo files that shape the manifest: every module that registers a tool, the placement
    table and the plugin manager."""
    files = set()
    for name in {tool["module"] for tool in live["tools"]} | set(SHAPING_MODULES):
        path = getattr(sys.modules.get(name), "__file__", None)
        rel = repo_relative(path or os.path.join(ROOT, *name.split(".")) + ".py")
        if rel:
            files.add(rel)
    return sorted(files)


def uncommitted(paths: list[str]) -> list[str] | None:
    """'git status --porcelain' lines (untracked files included) for the paths that differ from
    HEAD; None when git cannot tell."""
    if not paths:
        return []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *paths], cwd=ROOT,
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def differences(path: str, live: dict[str, Any]) -> list[str]:
    """Why the file at path is not the live manifest (empty when it is, apart from git_sha)."""
    try:
        committed = tool_placement.load_manifest(path)
    except tool_placement.ManifestError as e:
        return [str(e)]
    return tool_placement.manifest_differences(committed, live)


def write_manifest(path: str, live: dict[str, Any]) -> bool:
    """Writes live to path unless only git_sha would change; True when the file was written."""
    if os.path.exists(path) and not differences(path, live):
        return False
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(live, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="compare with the committed file and write nothing")
    parser.add_argument("--path", default=tool_placement.MANIFEST_PATH,
                        help="manifest file (default: services/ai_core/desktop_manifest.json)")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="write although registry files have uncommitted changes (only when "
                             "they go into the same commit as the manifest)")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("export_manifest: run it on Windows, where every tool module imports (K-71).")
        return 2
    try:
        live = live_manifest(head_sha())
    except tool_placement.ManifestError as e:
        print("export_manifest: the registry cannot be exported:")
        for problem in str(e).split("; "):
            print(f"  - {problem}")
        return 1
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"export_manifest: discovery failed: {type(e).__name__}: {e}")
        return 2
    summary = f"{len(live['tools'])} tools, manifest_sha256 {live['manifest_sha256'][:16]}"
    if args.check:
        return _check(args.path, live, summary)
    return _write(args.path, live, summary, args.allow_dirty)


def _write(path: str, live: dict[str, Any], summary: str, allow_dirty: bool) -> int:
    if os.path.exists(path) and not differences(path, live):
        print(f"desktop manifest unchanged ({summary})")
        return 0
    dirty = uncommitted(registry_files(live))
    if (dirty is None or dirty) and not allow_dirty:
        _refuse_write(dirty)
        return 1
    if dirty:
        print("export_manifest: written from uncommitted registry files; commit them together with "
              "the manifest: " + ", ".join(line[3:] for line in dirty))
    write_manifest(path, live)
    print(f"desktop manifest written ({summary})")
    return 0


def _check(path: str, live: dict[str, Any], summary: str) -> int:
    rel = repo_relative(path)
    dirty = uncommitted(registry_files(live) + ([rel] if rel else []))
    if dirty:
        print("export_manifest: note: uncommitted changes, so this checks the working tree rather "
              "than the commit (K-71): " + ", ".join(line[3:] for line in dirty))
    diffs = differences(path, live)
    if diffs:
        print(f"desktop manifest STALE ({len(diffs)} differences; live: {summary}):")
        for diff in diffs:
            print(f"  - {diff}")
        print("Run: .venv_312/Scripts/python.exe scripts/desktop/export_manifest.py")
        return 1
    print(f"desktop manifest up to date ({summary})")
    return 0


def _refuse_write(dirty: list[str] | None) -> None:
    if dirty is None:
        print("export_manifest: NOT written: git cannot tell whether the registry files are "
              "committed, and the manifest must describe the committed tree (K-71).")
    else:
        print(f"export_manifest: NOT written: {len(dirty)} registry files have uncommitted "
              "changes, and the manifest must describe the committed tree (K-71):")
        for line in dirty:
            print(f"  - {line}")
    print("Commit them first, or pass --allow-dirty when these changes go into the same commit as "
          "the manifest.")


if __name__ == "__main__":
    sys.exit(main())
