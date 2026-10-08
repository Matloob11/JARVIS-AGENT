"""C-84 (K-23): scripts/pc/start_pinchtab.ps1, its start-agent.bat hook and scripts/pc/install_desktop_deps.ps1.

The static checks run everywhere. The functional checks run the real script in Windows PowerShell 5.1 (the
shell start-agent.bat uses) against a fake PinchTab on 127.0.0.1 and a tmp_path env file (-EnvFile), so the
real .env is never read and no real PinchTab is started.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START_PS1 = ROOT / "scripts" / "pc" / "start_pinchtab.ps1"
INSTALL_PS1 = ROOT / "scripts" / "pc" / "install_desktop_deps.ps1"
START_BAT = ROOT / "deploy" / "windows-local" / "start-agent.bat"
HOOK = r"powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pc\start_pinchtab.ps1"
FAKE_TOKEN = "c84-test-only-fake-pinchtab-token"


def _windows_powershell() -> str | None:
    if os.name != "nt":
        return None
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return str(exe) if exe.is_file() else None


POWERSHELL = _windows_powershell()
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell 5.1")


# --------------------------------------------------------------------------------------------- static


def test_start_agent_bat_starts_pinchtab_before_the_worker_and_never_stops_on_it():
    lines = START_BAT.read_text(encoding="utf-8").splitlines()
    hooks = [i for i, line in enumerate(lines) if line.strip() == HOOK]
    assert len(hooks) == 1
    vps_check = next(i for i, line in enumerate(lines) if "start_hybrid_agent.py check" in line)
    worker = next(i for i, line in enumerate(lines) if line.startswith('start "JARVIS-Agent-Worker"'))
    assert vps_check < hooks[0] < worker
    between = [line.strip().lower() for line in lines[hooks[0] + 1:worker]]
    assert not any(line.startswith("if errorlevel") or "exit /b" in line for line in between)  # non-fatal


def test_start_agent_bat_keeps_crlf_line_endings():
    assert b"\n" not in START_BAT.read_bytes().replace(b"\r\n", b"")


@pytest.mark.parametrize("script", [START_PS1, INSTALL_PS1], ids=lambda path: path.name)
def test_pinchtab_scripts_are_ascii(script):
    """Windows PowerShell 5.1 reads a script without a BOM as ANSI, so non-ASCII text would be garbled."""
    assert script.read_bytes().isascii()


def test_start_pinchtab_passes_the_token_only_through_the_environment():
    text = START_PS1.read_text(encoding="ascii")
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if "Start-Process" in line and "$bin" in line)
    call = [lines[i]]
    while call[-1].rstrip().endswith("`"):
        i += 1
        call.append(lines[i])
    call_text = " ".join(call)
    assert "token" not in call_text.lower()
    assert "-ArgumentList @('server', '--bind', $bind, '--port'" in call_text
    assert "-WindowStyle Minimized" in call_text
    assert "[Environment]::SetEnvironmentVariable('PINCHTAB_TOKEN', $script:Token, 'Process')" in text
    assert "$bind = '127.0.0.1'" in text
    assert text.rstrip().endswith("exit 0")


def test_install_script_pins_pinchtab_skips_its_postinstall_and_hides_config_init_output():
    text = INSTALL_PS1.read_text(encoding="ascii")
    assert re.search(r"^\$PinchTabVersion = '\d+\.\d+\.\d+'", text, re.M)
    assert "'--ignore-scripts'" in text and ".ensureBinary()" in text
    assert "@('config', 'init') -Quiet" in text  # the new config holds a generated token
    assert "'--accept-source-agreements', '--accept-package-agreements'" in text


# ----------------------------------------------------------------------------------------- functional


class _FakePinchTab(http.server.BaseHTTPRequestHandler):
    status = 200  # the answer to the right Bearer token; any other request gets 401
    seen: list[tuple[str, str | None]] = []

    def do_GET(self):  # noqa: N802 (http.server's naming)
        auth = self.headers.get("Authorization")
        type(self).seen.append((self.path, auth))
        self.send_response(type(self).status if auth == f"Bearer {FAKE_TOKEN}" else 401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass


@pytest.fixture
def fake_pinchtab():
    handler = type("FakePinchTab", (_FakePinchTab,), {"seen": [], "status": 200})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, handler
    finally:
        server.shutdown()
        server.server_close()


def _env_file(tmp_path: Path, url: str, bin_path: Path) -> Path:
    path = tmp_path / "pinchtab.env"
    path.write_text(f"# test env\nPINCHTAB_URL={url}\nPINCHTAB_TOKEN='{FAKE_TOKEN}'\n"
                    f"PINCHTAB_BIN={bin_path}\nOTHER_SECRET=never-read\n", encoding="utf-8")
    return path


def _marker_bin(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in PINCHTAB_BIN that only leaves a marker file, so a test can prove nothing was started."""
    marker = tmp_path / "started.txt"
    cmd = tmp_path / "pinchtab.cmd"
    cmd.write_text(f'@echo started> "{marker}"\r\n', encoding="ascii")
    return cmd, marker


def _run(env_file: Path) -> str:
    env = {name: value for name, value in os.environ.items() if not name.upper().startswith("PINCHTAB_")}
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(START_PS1), "-EnvFile", str(env_file)],
        capture_output=True, text=True, timeout=90, env=env, stdin=subprocess.DEVNULL, check=False)
    assert FAKE_TOKEN not in proc.stdout + proc.stderr
    assert proc.returncode == 0
    return proc.stdout.strip()


@needs_powershell
def test_start_pinchtab_reports_ok_when_health_accepts_the_token(tmp_path, fake_pinchtab):
    server, handler = fake_pinchtab
    cmd, marker = _marker_bin(tmp_path)
    out = _run(_env_file(tmp_path, f"http://127.0.0.1:{server.server_port}", cmd))
    assert out.startswith("PINCHTAB_OK"), out
    assert handler.seen == [("/health", f"Bearer {FAKE_TOKEN}")]
    assert not marker.exists()


@needs_powershell
def test_start_pinchtab_starts_no_second_server_when_the_token_is_refused(tmp_path, fake_pinchtab):
    server, handler = fake_pinchtab
    handler.status = 401
    cmd, marker = _marker_bin(tmp_path)
    out = _run(_env_file(tmp_path, f"http://127.0.0.1:{server.server_port}", cmd))
    assert out.startswith("PINCHTAB_AUTH_FAILED"), out
    assert not marker.exists()


@needs_powershell
def test_start_pinchtab_skips_a_url_that_is_not_on_this_pc(tmp_path):
    cmd, marker = _marker_bin(tmp_path)
    out = _run(_env_file(tmp_path, "http://192.0.2.10:9867", cmd))  # TEST-NET-1: never contacted
    assert out.startswith("PINCHTAB_SKIPPED"), out
    assert not marker.exists()


_FAKE_SERVER = r'''
import http.server, json, os, sys, threading
record, args = sys.argv[1], sys.argv[2:]
token = os.environ.get("PINCHTAB_TOKEN", "")


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = bool(token) and self.headers.get("Authorization") == "Bearer " + token
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if ok:  # done: the script saw its 200
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *a):
        pass


with open(record, "w", encoding="utf-8") as fh:
    json.dump({"args": args, "env_token": token}, fh)
server = http.server.HTTPServer((args[args.index("--bind") + 1], int(args[args.index("--port") + 1])), Health)
timer = threading.Timer(30, server.shutdown)  # never outlives the test by long
timer.daemon = True
timer.start()
server.serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@needs_powershell
def test_start_pinchtab_starts_the_server_with_the_token_only_in_its_environment(tmp_path):
    port = _free_port()
    record = tmp_path / "record.json"
    fake = tmp_path / "fake_pinchtab.py"
    fake.write_text(_FAKE_SERVER, encoding="utf-8")
    cmd = tmp_path / "pinchtab.cmd"
    cmd.write_text(f'@"{sys.executable}" -I "{fake}" "{record}" %*\r\n', encoding="ascii")
    out = _run(_env_file(tmp_path, f"http://127.0.0.1:{port}", cmd))
    assert out.startswith("PINCHTAB_STARTED"), out
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["args"] == ["server", "--bind", "127.0.0.1", "--port", str(port)]
    assert data["env_token"] == FAKE_TOKEN  # reached the server through its environment ...
    assert not any(FAKE_TOKEN in arg for arg in data["args"])  # ... and never as an argument
