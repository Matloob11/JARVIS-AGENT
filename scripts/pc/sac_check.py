"""Smart App Control / App Control check for the project venv (Windows only).

Windows 11 Smart App Control (enforcing on the Hp laptop since Oct 2026) refuses to load native modules that have no
cloud reputation yet. A blocked .pyd/.dll makes an import fail with "DLL load failed ... An Application Control policy
has blocked this file", and when the blocked file belongs to livekit, av or grpc the agent worker cannot start at all.

This script maps every .dll/.pyd under a folder with LoadLibraryExW(DONT_RESOLVE_DLL_REFERENCES): the image is mapped
(that is where Code Integrity decides) but DllMain never runs and no imports are resolved, so no third-party code runs.

Usage (from the repo root):
    .venv_312/Scripts/python.exe scripts/pc/sac_check.py                  # scans .venv_312/Lib/site-packages
    .venv_312/Scripts/python.exe scripts/pc/sac_check.py PATH [PATH ...]  # other folders or files

Exit codes: 0 nothing blocked (or only files in KNOWN_UNUSED), 1 a blocked file JARVIS may load, 2 not Windows or a
bad path.

What to do with a blocked file (08 Oct 2026 findings):
- A block can be a cached verdict on that one file (taken while the laptop was offline): the same bytes freshly
  written load fine. Reinstall the SAME version first:
      .venv_312/Scripts/python.exe -m pip install --force-reinstall --no-deps <package>==<installed version>
  (stop the worker with scripts/agent_ctl.py first; never pip install while it runs).
- If the fresh copy is still blocked, that build has no reputation yet: pin the newest version this script allows
  (requirements.txt and deploy/windows-local/requirements-hybrid.txt carry the current pins and the note).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DONT_RESOLVE_DLL_REFERENCES = 0x00000001
# 0x11C7 Application Control policy, 0x11C9 blocked driver, 1260 blocked by group policy
BLOCKED_CODES = frozenset({4551, 4553, 1260})
# Blocked files no JARVIS code path imports (they stay listed, but do not fail the check).
KNOWN_UNUSED = frozenset({
    "pyarrow/_dataset_parquet_encryption.cp312-win_amd64.pyd",  # parquet encryption: never used by JARVIS
})
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = ROOT / ".venv_312" / "Lib" / "site-packages"


def _loader():
    import ctypes  # pylint: disable=import-outside-toplevel
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]

    def probe(path: str) -> int:
        """0 when the image maps, else the Win32 error code."""
        handle = kernel32.LoadLibraryExW(path, None, DONT_RESOLVE_DLL_REFERENCES)
        if handle:
            kernel32.FreeLibrary(handle)
            return 0
        return ctypes.get_last_error()

    return probe


def native_files(target: Path) -> list[Path]:
    """Every .dll/.pyd at or under target, sorted."""
    if target.is_file():
        return [target] if target.suffix.lower() in (".dll", ".pyd") else []
    found = []
    for dirpath, _dirs, files in os.walk(target):
        found.extend(Path(dirpath) / name for name in files if name.lower().endswith((".dll", ".pyd")))
    return sorted(found)


def scan(targets: list[Path], probe) -> tuple[int, list[str], dict[int, int]]:
    """(mapped count, blocked paths relative to their target, other error counts)."""
    ok, blocked, other = 0, [], {}
    for target in targets:
        base = target if target.is_dir() else target.parent
        for path in native_files(target):
            code = probe(str(path))
            if code == 0:
                ok += 1
            elif code in BLOCKED_CODES:
                blocked.append(path.relative_to(base).as_posix())
            else:
                other[code] = other.get(code, 0) + 1
    return ok, blocked, other


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform != "win32":
        print("sac_check: Windows only (Smart App Control)")
        return 2
    targets = [Path(a) for a in args] or [DEFAULT_TARGET]
    missing = [str(t) for t in targets if not t.exists()]
    if missing:
        print("sac_check: path not found: " + ", ".join(missing))
        return 2
    ok, blocked, other = scan(targets, _loader())
    relevant = [b for b in blocked if b not in KNOWN_UNUSED]
    print(f"SAC_CHECK mapped={ok} blocked={len(blocked)} relevant={len(relevant)} other_errors={other or '-'}")
    for path in blocked:
        print(("BLOCKED  " if path in relevant else "UNUSED   ") + path)
    return 1 if relevant else 0


if __name__ == "__main__":
    sys.exit(main())
