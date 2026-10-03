"""
Hybrid launcher: run LiveKit agent on Windows against remote VPS bridge/LiveKit.
Does NOT start a local UI bridge — VPS owns frontend + bridge + LiveKit.
The worker is started through scripts/agent_ctl.py (single worker, K-53); the root .env is the
settings source and is never overwritten (K-47).
The PC reaches the VPS only through the domain over https/wss (C-16, K-48): the bridge's health must
answer 200 and LiveKit's /rtc/validate 401 (LiveKit answers it without a token with 401).
Usage: python deploy/windows-local/start_hybrid_agent.py [start|dev|check]   (default: start)
       check: only those two checks (start-agent.bat runs it); exit 0 when both answer as wanted, else 1.

C-123 (VPS brain): this starts the pc-mode worker. While JARVIS_BRAIN=vps the laptop target is the
Desktop Node and agent_ctl refuses this worker start, so the delegated 'agent_ctl start' below surfaces
that refusal; switch with 'agent_ctl brain pc' first, or run the node with 'agent_ctl node start'.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HYBRID_ENV = Path(__file__).resolve().parent / ".env"
AGENT_CTL = ROOT / "scripts" / "agent_ctl.py"
VENV_PYTHON = ROOT / ".venv_312" / "Scripts" / "python.exe"
UI_URL = "https://jarvis.srv1802669.hstgr.cloud"
# (label, url, wanted HTTP status)
VPS_CHECKS = (
    ("VPS bridge", f"{UI_URL}/vortex/health", 200),
    ("VPS LiveKit", f"{UI_URL}/rtc/validate", 401),
)


def _status(url: str, timeout: float = 8) -> int:
    """HTTP status of a GET; an error status is an answer too. Raises when no answer comes."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        exc.close()
        return int(exc.code)


def check_vps() -> bool:
    """One line per check; True when each answered with its wanted status."""
    ok = True
    for label, url, want in VPS_CHECKS:
        try:
            code = _status(url)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ERROR] {label} unreachable ({url}): {exc}")
            ok = False
            continue
        if code == want:
            print(f"[OK] {label}: {url} -> {code}")
        else:
            print(f"[ERROR] {label}: {url} -> HTTP {code}, want {want}")
            ok = False
    return ok


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode not in ("start", "dev", "check"):
        print("Usage: python deploy/windows-local/start_hybrid_agent.py [start|dev|check]")
        sys.exit(2)
    if mode == "check":
        sys.exit(0 if check_vps() else 1)

    if not (ROOT / ".env").is_file() and not HYBRID_ENV.is_file():
        print(f"[ERROR] Missing {ROOT / '.env'} (run deploy\\windows-local\\setup.bat)")
        sys.exit(1)

    print("JARVIS hybrid agent — local Windows executor")
    if not check_vps():
        sys.exit(1)
    print(f"UI: {UI_URL}")
    print("Starting agent worker via scripts/agent_ctl.py...")

    python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
    cmd = [str(python), str(AGENT_CTL), "start", "--by", "user", "--reason", "start_hybrid_agent.py",
           "--mode", mode, "--foreground"]
    sys.exit(subprocess.call(cmd, cwd=ROOT))


if __name__ == "__main__":
    main()
