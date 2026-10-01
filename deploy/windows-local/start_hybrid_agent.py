"""
Hybrid launcher: run LiveKit agent on Windows against remote VPS bridge/LiveKit.
Does NOT start a local UI bridge — VPS owns frontend + bridge + LiveKit.
The worker is started through scripts/agent_ctl.py (single worker, K-53); the root .env is the
settings source and is never overwritten (K-47).
Usage: python deploy/windows-local/start_hybrid_agent.py [start|dev]   (default: start)
"""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HYBRID_ENV = Path(__file__).resolve().parent / ".env"
AGENT_CTL = ROOT / "scripts" / "agent_ctl.py"
VENV_PYTHON = ROOT / ".venv_312" / "Scripts" / "python.exe"
UI_URL = "https://jarvis.srv1802669.hstgr.cloud"


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
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode not in ("start", "dev"):
        print("Usage: python deploy/windows-local/start_hybrid_agent.py [start|dev]")
        sys.exit(2)

    if not (ROOT / ".env").is_file() and not HYBRID_ENV.is_file():
        print(f"[ERROR] Missing {ROOT / '.env'} (run deploy\\windows-local\\setup.bat)")
        sys.exit(1)

    print("JARVIS hybrid agent — local Windows executor")
    _check("http://187.127.123.235:5101/health", "VPS bridge")
    _check("http://187.127.123.235:7880", "VPS LiveKit")
    print(f"UI: {UI_URL}")
    print("Starting agent worker via scripts/agent_ctl.py...")

    python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
    cmd = [str(python), str(AGENT_CTL), "start", "--by", "user", "--reason", "start_hybrid_agent.py",
           "--mode", mode, "--foreground"]
    sys.exit(subprocess.call(cmd, cwd=ROOT))


if __name__ == "__main__":
    main()
