"""One-shot Socket.IO chat probe against the UI bridge (K-38).

C-34: it sends ui_command chat {id, text} (default 'Jarvis, 7 times 8?') and passes only when exactly one user
message with that id and exactly one assistant reply to it (turn_id) come back: a second reply means two listeners
answered the same message. After the first final reply it waits --settle seconds for a duplicate. --expect 56
also requires that text in the reply.

Two modes:

- legacy (the default, also --legacy): UI_BRIDGE_URL and VORTEX_SECURITY_TOKEN come from the root .env;
  deploy/windows-local/.env only fills keys it lacks (K-47). A URL ending in /vortex (the domain's nginx prefix,
  C-16) is reached at its origin with socketio_path 'vortex/socket.io', as the agent does. Unchanged from before.
- --cookie-via-ssh (C-19, C-AUTH-1): runs 'python -m src.core.bridge_auth mint-session' inside the jarvis-bridge
  container over ssh (JARVIS_VPS_SSH, else JARVIS_VPS_HOST; key JARVIS_SSH_KEY, as agent_ctl uses them), connects to
  https://jarvis.srv1802669.hstgr.cloud (or --url) with socketio_path 'vortex/socket.io' and the session in the
  Cookie header, sends ui_command chat {id, text} and logs the probe session out at the end.
  The minted session is short (--ttl, default 300 s) and never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import socketio
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://jarvis.srv1802669.hstgr.cloud/vortex"
BRIDGE_CONTAINER = "jarvis-bridge"
COOKIE_NAME = "jarvis_session"
PROBE_TEXT = "Jarvis, 7 times 8?"


def socket_target(url: str) -> tuple[str, str]:
    """(connect URL, socketio_path) as services/ai_core/agent_loops.py bridge_socket_target computes them:
    a URL ending in /vortex gives 'vortex/socket.io', any other URL 'socket.io'."""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    base = urlunsplit((parts.scheme, parts.netloc, "", parts.query, ""))
    if path == "vortex" or path.endswith("/vortex"):
        return base, f"{path}/socket.io"
    return base, "socket.io"


def session_cookie_from_output(stdout: str) -> str | None:
    """The JWT of the 'jarvis_session=<JWT>' line that mint-session prints, or None."""
    for line in stdout.splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name == COOKIE_NAME and value.count(".") == 2:
            return value
    return None


def mint_command(ttl: int, label: str) -> list[str]:
    """The remote argv: mint-session inside the bridge container."""
    return ["docker", "exec", BRIDGE_CONTAINER, "python", "-m", "src.core.bridge_auth", "mint-session",
            "--ttl", str(ttl), "--label", label]


def ssh_argv(remote: list[str]) -> list[str] | None:
    """ssh to the VPS without prompts (BatchMode), or None when no target is configured."""
    target = (os.getenv("JARVIS_VPS_SSH") or os.getenv("JARVIS_VPS_HOST") or "").strip()
    if not target:
        return None
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    key = (os.getenv("JARVIS_SSH_KEY") or "").strip()
    if key:
        options = ["-i", os.path.expanduser(key), *options]
    return ["ssh", *options, target, shlex.join(remote)]


def mint_session_via_ssh(ttl: int) -> str | None:
    argv = ssh_argv(mint_command(ttl, "probe"))
    if argv is None:
        print("FAIL JARVIS_VPS_SSH / JARVIS_VPS_HOST is not set")
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL ssh did not run: {type(exc).__name__}")
        return None
    token = session_cookie_from_output(done.stdout) if done.returncode == 0 else None
    if token is None:
        # stderr of mint-session names the missing setting only; it never holds a secret
        print(f"FAIL mint-session exit {done.returncode}: {done.stderr.strip()[:200]}")
    return token


async def _logout(origin: str, http_base: str, token: str) -> None:
    """Revokes the probe session (POST /auth/logout with the cookie and a same-origin Origin header)."""
    try:
        import httpx  # pylint: disable=import-outside-toplevel
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.post(f"{http_base}/auth/logout", content=b"{}",
                                    headers={"Cookie": f"{COOKIE_NAME}={token}", "Origin": origin,
                                             "Content-Type": "application/json"})
        print(f"probe session logout: HTTP {res.status_code}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"probe session logout failed ({type(exc).__name__}); it expires on its own")


async def run_probe(url: str, *, auth: dict | None, headers: dict | None, payload: object,
                    wait_s: float, settle_s: float = 8.0) -> list[dict]:
    """Sends the chat and returns every new_message/update_message seen: up to wait_s for the first final reply to
    it, then settle_s more, so a duplicate reply has time to arrive."""
    sio = socketio.AsyncClient()
    got: list[dict] = []
    msg_id = payload.get("id") if isinstance(payload, dict) else None
    replied = asyncio.Event()

    def note(kind: str, data: dict) -> None:
        print(kind, data.get("role"), str(data.get("text", ""))[:120])
        got.append(dict(data))
        is_reply = data.get("role") in ("assistant", "agent") and (msg_id is None or data.get("turn_id") == msg_id)
        if is_reply and data.get("final", data.get("is_final", True)):
            replied.set()

    @sio.on("new_message")
    async def on_msg(data):  # type: ignore[no-untyped-def]
        note("MSG", data)

    @sio.on("update_message")
    async def on_upd(data):  # type: ignore[no-untyped-def]
        note("UPD", data)

    base_url, socketio_path = socket_target(url)
    await sio.connect(base_url, auth=auth, headers=headers or {}, socketio_path=socketio_path, wait_timeout=10)
    print(f"connected ({base_url}, path /{socketio_path})")
    await sio.emit("ui_command", {"type": "chat", "payload": payload})
    try:
        await asyncio.wait_for(replied.wait(), wait_s)
        await asyncio.sleep(settle_s)
    except asyncio.TimeoutError:
        pass
    await sio.disconnect()
    return got


def evaluate(got: list[dict], msg_id: str, expect: str | None = None) -> tuple[bool, str]:
    """C-34: (passed, detail): exactly one user message with msg_id and exactly one assistant reply to it (turn_id;
    its update_message events count as the same reply), and with expect, that text in the reply."""
    users = {g.get("id") for g in got if g.get("role") == "user" and g.get("id") == msg_id}
    replies: dict[str, str] = {}
    for g in got:
        if g.get("role") in ("assistant", "agent") and g.get("turn_id") == msg_id:
            replies[str(g.get("id"))] = str(g.get("text") or "")
    detail = f"user messages {len(users)}, assistant replies {len(replies)}"
    passed = len(users) == 1 and len(replies) == 1
    if passed and expect:
        found = expect in next(iter(replies.values()))
        detail += f", '{expect}' {'found' if found else 'missing'} in the reply"
        passed = found
    return passed, detail


def chat_payload(text: str) -> dict:
    return {"id": f"probe-{uuid.uuid4().hex[:12]}", "text": text}


def report(got: list[dict], payload: dict, expect: str | None) -> int:
    passed, detail = evaluate(got, payload["id"], expect)
    print(("PASS " if passed else "FAIL ") + detail)
    return 0 if passed else 2


async def main_legacy(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    url = os.getenv("UI_BRIDGE_URL")
    token = os.getenv("VORTEX_SECURITY_TOKEN")
    if not url or not token:
        print("FAIL missing UI_BRIDGE_URL / VORTEX_SECURITY_TOKEN")
        return 1
    payload = chat_payload(args.text)
    got = await run_probe(url, auth={"token": token}, headers=None, payload=payload, wait_s=args.wait,
                          settle_s=args.settle)
    return report(got, payload, args.expect)


async def main_cookie(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    token = mint_session_via_ssh(max(30, min(args.ttl, 3600)))
    if token is None:
        return 1
    url = args.url
    parts = urlsplit(url.strip())
    origin = f"{parts.scheme}://{parts.netloc}"
    http_base = url.strip().rstrip("/")
    payload = chat_payload(args.text)
    try:
        got = await run_probe(url, auth=None, headers={"Cookie": f"{COOKIE_NAME}={token}", "Origin": origin},
                              payload=payload, wait_s=args.wait, settle_s=args.settle)
    finally:
        await _logout(origin, http_base, token)
    return report(got, payload, args.expect)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-shot chat probe against the UI bridge")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--legacy", action="store_true", help="legacy token mode (the default)")
    mode.add_argument("--cookie-via-ssh", action="store_true",
                      help="mint an owner session over ssh and connect with the cookie (C-19)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"bridge URL for --cookie-via-ssh (default {DEFAULT_URL})")
    parser.add_argument("--ttl", type=int, default=300, help="probe session lifetime in seconds (default 300)")
    parser.add_argument("--text", default=PROBE_TEXT, help=f"the chat text (default {PROBE_TEXT!r})")
    parser.add_argument("--expect", default=None, help="text the reply must contain, e.g. 56")
    parser.add_argument("--wait", type=float, default=25.0, help="seconds to wait for the reply (default 25)")
    parser.add_argument("--settle", type=float, default=8.0,
                        help="seconds to keep listening after the reply, for a duplicate (default 8)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cookie_via_ssh:
        return asyncio.run(main_cookie(args))
    return asyncio.run(main_legacy(args))


if __name__ == "__main__":
    sys.exit(main())
