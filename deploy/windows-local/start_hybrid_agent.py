"""
Hybrid launcher: run LiveKit agent on Windows against remote VPS bridge/LiveKit.
Does NOT start a local UI bridge — VPS owns frontend + bridge + LiveKit.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HYBRID_ENV = Path(__file__).resolve().parent / ".env"


def _check(url: str, label: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"{label} returned HTTP {resp.status}")
        print(f"[OK] {label}: {url}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ERROR] {label} unreachable ({url}): {exc}")
        sys.exit(1)


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if not HYBRID_ENV.is_file():
        print(f"[ERROR] Missing {HYBRID_ENV}")
        sys.exit(1)

    shutil.copyfile(HYBRID_ENV, ROOT / ".env")
    os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ["JARVIS_HYBRID"] = "1"

    print("JARVIS hybrid agent — local Windows executor")
    _check("http://187.127.123.235:5101/health", "VPS bridge")
    _check("http://187.127.123.235:7880", "VPS LiveKit")
    print("UI: http://187.127.123.235:5100")
    print("Starting agent worker...")

    sys.argv = ["src.core.agent", "dev"]
    runpy.run_module("src.core.agent", run_name="__main__")


if __name__ == "__main__":
    main()
