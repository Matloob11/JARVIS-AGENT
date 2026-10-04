"""
scripts/smoke/room_check.py: a read-only look at the bridge's room owner (plan task C-23, K-43/K-44 v9).

    $PY scripts/smoke/room_check.py status [--json]
    $PY scripts/smoke/room_check.py command <type> [--wait paused|absent|voice_ready|present] [--watch S]
                                                   [--timeout S]

status: an agent-signed GET <UI_BRIDGE_URL>/status (the C-NOTIFY-1 headers over an empty body; AGENT_TOKEN and
BRIDGE_HMAC_SECRET from the root .env); prints the agent block and the livekit block (room_sid, the agents by name,
session and state, the people count, mode, paused). --json prints those two blocks as JSON.

command: one UI Socket.IO session (role ui) with the owner-session cookie from the environment variable C23_COOKIE
(what 'python -m src.core.bridge_auth mint-session --ttl 600 --label c23-probe' prints inside the bridge container:
'jarvis_session=<JWT>'), on <origin>/vortex/socket.io with the canonical Origin. It waits for init_state, sends
ui_command {type, payload: {}}, prints the ack (a refused command, such as pause_agent in observe mode, exits 1 at
once), then follows agent_status (and, with the agent keys, polls /status every 5 s) until --wait holds (exit 0) or
--timeout passes (exit 1). --watch S follows it for S seconds instead and exits 0 only when voice_ready stayed true
the whole time (the 'stop' check: no session restart).

It changes nothing but what the command itself does, and never prints a cookie, key, token, worker id or a person's
identity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COOKIE_ENV = "C23_COOKIE"
COOKIE_NAME = "jarvis_session"
WAITS = ("paused", "absent", "voice_ready", "present")
STATUS_POLL_S = 5.0
HTTP_TIMEOUT_S = 10.0
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel
    except ImportError:
        return
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "deploy" / "windows-local" / ".env", override=False)  # the root .env wins (K-47)


def bridge_base() -> str:
    """UI_BRIDGE_URL without a trailing slash (https://<domain>/vortex on the PC)."""
    return (os.getenv("UI_BRIDGE_URL") or "").strip().rstrip("/")


def canonical_origin(base: str) -> str:
    parts = urlsplit(base)
    return f"{parts.scheme}://{parts.netloc}"


def cookie_header(value: str) -> str:
    """'jarvis_session=<JWT>' from C23_COOKIE (that form, or the bare JWT)."""
    value = value.strip()
    return value if value.startswith(f"{COOKIE_NAME}=") else f"{COOKIE_NAME}={value}"


def _flag(value: Any) -> str:
    return "?" if value is None else ("yes" if value is True else "no" if value is False else str(value))


def _name(value: Any) -> str:
    return "''" if value == "" else str(value) if value is not None else "?"


def summarize(status: Mapping[str, Any]) -> list[str]:
    """The agent and livekit blocks as lines: flags, names, states and ids of jobs and dispatches only."""
    agent = status.get("agent") if isinstance(status.get("agent"), Mapping) else {}
    livekit = status.get("livekit") if isinstance(status.get("livekit"), Mapping) else {}
    lines = [
        f"status: {status.get('status', '?')} (brain {status.get('brain', '?')})",
        (f"agent: online={_flag(agent.get('online'))} in_room={_flag(agent.get('in_room'))} "
         f"voice_ready={_flag(agent.get('voice_ready'))} paused={_flag(agent.get('paused'))} "
         f"restarting={_flag(agent.get('restarting'))} session={_name(agent.get('session'))} "
         f"target={_name(agent.get('agent_name'))}"),
        (f"livekit: ok={_flag(livekit.get('ok'))} mode={livekit.get('mode', '?')} paused={_flag(livekit.get('paused'))} "
         f"room_sid={livekit.get('room_sid') or '-'} people={livekit.get('participants', '?')} "
         f"decision={livekit.get('decision') or '-'} last_job={livekit.get('last_job_state', '?')} "
         f"dispatch={livekit.get('dispatch_id') or '-'}"),
    ]
    for entry in livekit.get("agents") or []:
        if isinstance(entry, Mapping):
            lines.append(f"  agent {_name(entry.get('lk.agent.name'))} session {entry.get('jarvis.session') or '-'} "
                         f"state {entry.get('state', '?')} job {entry.get('job_id') or '-'} "
                         f"bridge_link {_flag(entry.get('bridge_link'))}")
    return lines


def public_blocks(status: Mapping[str, Any]) -> dict[str, Any]:
    """--json: the agent block without host details and the livekit block as the bridge sends it (no identity)."""
    agent = dict(status.get("agent") or {})
    agent.pop("host", None)
    return {"status": status.get("status"), "agent": agent, "livekit": status.get("livekit")}


def condition_met(wait: str, agent_status: Mapping[str, Any]) -> bool:
    if wait == "paused":
        return agent_status.get("paused") is True
    if wait == "absent":
        return agent_status.get("in_room") is False
    if wait == "voice_ready":
        return agent_status.get("voice_ready") is True
    return agent_status.get("in_room") is True


def signed_status(base: str) -> dict[str, Any] | None:
    """GET <base>/status with the agent's C-NOTIFY-1 headers; None when the keys are missing or it fails."""
    if not ((os.getenv("AGENT_TOKEN") or "").strip() and (os.getenv("BRIDGE_HMAC_SECRET") or "").strip()):
        return None
    import httpx  # pylint: disable=import-outside-toplevel

    from services.utils.jarvis_bridge import signed_headers  # pylint: disable=import-outside-toplevel
    try:
        response = httpx.get(f"{base}/status", headers=signed_headers(b""), timeout=HTTP_TIMEOUT_S)
        data = response.json()
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a fixed note, never a header
        print(f"[room_check] /status failed: {type(exc).__name__}", file=sys.stderr)
        return None
    if response.status_code != 200 or not isinstance(data, dict):
        print(f"[room_check] /status answered HTTP {response.status_code}", file=sys.stderr)
        return None
    return data


def cmd_status(args: argparse.Namespace) -> int:
    base = bridge_base()
    if not base:
        print("[room_check] UI_BRIDGE_URL is not set", file=sys.stderr)
        return EXIT_USAGE
    status = signed_status(base)
    if status is None:
        print("[room_check] no /status (AGENT_TOKEN / BRIDGE_HMAC_SECRET missing or the bridge refused)",
              file=sys.stderr)
        return EXIT_FAILED
    if args.json:
        print(json.dumps(public_blocks(status), indent=2, sort_keys=True))
    else:
        for line in summarize(status):
            print(line)
    return EXIT_OK


def _status_key(status: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(status.get(name) for name in ("online", "in_room", "voice_ready", "paused", "restarting",
                                               "session"))


def _describe(status: Mapping[str, Any]) -> str:
    return " ".join(f"{name}={_flag(status.get(name))}" for name in ("online", "in_room", "voice_ready", "paused",
                                                                    "restarting")) + \
        f" session={_name(status.get('session'))}"


async def follow(command: str, *, wait: str | None, watch: float | None, timeout: float, base: str, cookie: str,
                 client_factory: Callable[[], Any] | None = None,
                 poll: Callable[[str], dict[str, Any] | None] = signed_status,
                 clock: Callable[[], float] = time.monotonic) -> int:
    """The 'command' run (see the module docstring). client_factory makes the python-socketio AsyncClient (tests
    inject a fake)."""
    if client_factory is None:
        import socketio  # pylint: disable=import-outside-toplevel

        def client_factory() -> Any:
            return socketio.AsyncClient(reconnection=False)

    client = client_factory()
    latest: dict[str, Any] = {}
    ready = asyncio.Event()
    changed = asyncio.Event()

    async def on_init_state(data: Any) -> None:
        if isinstance(data, dict) and isinstance(data.get("agent_status"), dict):
            latest.update(data["agent_status"])
        ready.set()
        changed.set()

    async def on_agent_status(data: Any) -> None:
        if isinstance(data, dict):
            latest.clear()
            latest.update(data)
            print(f"[room_check] agent_status: {_describe(latest)}", flush=True)
            changed.set()

    client.on("init_state", on_init_state)
    client.on("agent_status", on_agent_status)
    origin = canonical_origin(base)
    path = urlsplit(base).path.strip("/")
    await client.connect(origin, headers={"Cookie": cookie, "Origin": origin}, transports=["websocket"],
                         socketio_path=f"{path}/socket.io" if path else "socket.io", wait_timeout=15)
    try:
        await asyncio.wait_for(ready.wait(), 15)
        print(f"[room_check] init_state: {_describe(latest)}", flush=True)
        ack = await client.call("ui_command", {"type": command, "payload": {}}, timeout=15)
        print(f"[room_check] ui_command {command}: ack {ack}", flush=True)
        if isinstance(ack, dict) and ack.get("status") == "rejected":
            # e.g. pause_agent in observe mode (K-03 v9): the bridge holds no pause there, so nothing changed
            print(f"[room_check] the bridge refused {command} ({ack.get('reason') or '-'}); nothing changed",
                  flush=True)
            return EXIT_FAILED
        started = clock()
        deadline = started + (watch if watch is not None else timeout)
        held = bool(latest.get("voice_ready"))
        next_poll = started
        while clock() < deadline:
            if watch is None and wait is not None and condition_met(wait, latest):
                print(f"[room_check] {wait} after {clock() - started:.0f} s", flush=True)
                return EXIT_OK
            if watch is not None and latest.get("voice_ready") is not True:
                held = False
            if clock() >= next_poll:
                next_poll = clock() + STATUS_POLL_S
                status = await asyncio.to_thread(poll, base)
                agent = status.get("agent") if isinstance(status, dict) else None
                if isinstance(agent, dict) and _status_key(agent) != _status_key(latest):
                    latest.clear()
                    latest.update(agent)
                    print(f"[room_check] /status agent: {_describe(latest)}", flush=True)
                    continue
            changed.clear()
            try:
                await asyncio.wait_for(changed.wait(), max(0.1, min(1.0, deadline - clock())))
            except asyncio.TimeoutError:
                pass
        if watch is not None:
            outcome = "held" if held and latest.get("voice_ready") is True else "did NOT hold"
            print(f"[room_check] voice_ready {outcome} for {watch:.0f} s", flush=True)
            return EXIT_OK if outcome == "held" else EXIT_FAILED
        print(f"[room_check] {wait or 'the condition'} not reached within {timeout:.0f} s: {_describe(latest)}",
              flush=True)
        return EXIT_FAILED
    finally:
        await client.disconnect()


def cmd_command(args: argparse.Namespace) -> int:
    base = bridge_base()
    raw_cookie = os.getenv(COOKIE_ENV) or ""
    if not base or not raw_cookie.strip():
        print(f"[room_check] needs UI_BRIDGE_URL and {COOKIE_ENV} (an owner-session cookie from mint-session)",
              file=sys.stderr)
        return EXIT_USAGE
    if args.wait is None and args.watch is None:
        print("[room_check] give --wait or --watch", file=sys.stderr)
        return EXIT_USAGE
    try:
        return asyncio.run(follow(args.type, wait=args.wait, watch=args.watch, timeout=args.timeout, base=base,
                                  cookie=cookie_header(raw_cookie)))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # never a traceback with headers in it
        print(f"[room_check] {args.type} failed: {type(exc).__name__}", file=sys.stderr)
        return EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="room_check.py", description="Read-only room owner checks (C-23).")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="the agent and livekit blocks of /vortex/status")
    status.add_argument("--json", action="store_true")
    command = commands.add_parser("command", help="send one ui_command as the owner's UI and follow agent_status")
    command.add_argument("type", choices=("pause_agent", "start", "stop"))
    command.add_argument("--wait", choices=WAITS)
    command.add_argument("--watch", type=float)
    command.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    _load_env()
    return cmd_status(args) if args.command == "status" else cmd_command(args)


if __name__ == "__main__":
    sys.exit(main())
