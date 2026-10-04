"""
Make sure the LiveKit room has exactly one JARVIS agent, so the local Windows worker actually gets a job.
Without a job the worker stays idle and UI chat never reaches the agent.

agent_ctl start/restart runs this once the new worker is healthy and linked (--reason agent_ctl_start /
agent_ctl_restart, or brain_switch from 'agent_ctl brain pc'), and the PC worker's room watchdog runs it when the
room loses its agent (--reason watchdog). agent_ctl reports its exit code, and any non-zero code marks the start as
failed. It never deletes a room (C-23, K-53 v9).

Every run prints exactly one "[ROOM] Result: ..." line, on every exit path, and exits as follows:
- exit 0: the vps-mode no-op ("OK, nothing to do in vps mode ..."); every answer of the bridge path below ("OK, an
  agent of the current worker is in the room (bridge)", "PAUSED by the owner ...", "NO AGENT ...", or an
  "UNKNOWN: the bridge refused ... / did not answer in time, nothing was changed"); a bridge that answers 200
  without agent fields ("room ensured by the bridge (agent presence not checked) ...": this run does not know
  whether an agent is in the room, and agent_ctl reports it as a warning); and every outcome of the LiveKit API
  path below ("OK, ...", "NO AGENT ...", "UNKNOWN: the room could not be read ..." or "an agent of an earlier
  worker ..."): a room left without the worker's agent is reported by its result line, not by the exit code.
- exit 1: a missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET, a failed list_rooms, or an
  unexpected error, each with an "UNKNOWN: ..." result line; nothing was changed in the first two cases.
  An unexpected error raised AFTER the result line was printed (for example while closing the LiveKit
  client) also exits 1: the result line already printed stays the run's only one, so a run can print an
  "OK, ..." or "NO AGENT ..." result line and still exit 1. Its "[ERROR] ... stopped on an unexpected ..."
  line says so.
- exit 2: bad arguments ("UNKNOWN: ensure_room.py was started with bad arguments ..."; nothing was changed).
- exit 130: Ctrl+C, with an "UNKNOWN: ensure_room.py was interrupted ..." result line (or, after the result
  line, with that line kept). Only a hard kill (agent_ctl's or the room watchdog's timeout) ends a run
  without a result line; both report that themselves.

C-123 (VPS brain): this runs in pc mode ONLY. In vps mode the VPS dispatches its own job, so this exits as a no-op.

The bridge path (C-23, K-39/K-53 v9): the bridge in the VPS container owns the room and dispatches its agent, so
this asks it first.
1. With AGENT_TOKEN and BRIDGE_HMAC_SECRET: POST <UI_BRIDGE_URL>/livekit/ensure with jarvis_bridge.signed_headers
   over the exact body {reason, agent_name, worker_id?}: agent_name is the name the running worker registered
   (run/agent_link.json agent_name, else JARVIS_AGENT_NAME, else ''), worker_id its LiveKit worker id when known.
2. Else, with JARVIS_LINK_URL and the node's PC key: the PC-signed POST /vortex/desktop/ensure with an empty body.
3. Else the LiveKit API path below.
A 200 that shows the bridge reconciles is followed by the same call every 3 s (reason 'wait', fresh headers) for
up to 90 s, until it shows paused, or an agent of the current worker (agent_current_worker true; agent_in_room
when that is null). A 200 that says paused is "PAUSED by the owner" in every mode: the LiveKit path never runs
while the owner paused JARVIS. The LiveKit API path runs only when the bridge provably does not reconcile: no
connection at all (refused, DNS), HTTP 404, HTTP 502/503/504 without a JSON body (nginx: the bridge is down), HTTP
503 {error: 'livekit_unavailable'} (the bridge could not use LiveKit, so it dispatched nothing), or a 200 with
result 'observe' or 'disabled'. A 429 is asked once more after its Retry-After (at most 5 s). A timeout, any other
4xx or a JSON error answer changes nothing ("UNKNOWN: ...").

What the self-hosted LiveKit server v1.13.7 does (read in its source at that tag: pkg/rtc/room.go,
pkg/agent/worker.go, pkg/service/agentservice.go). The LiveKit API path is built around it:
- A room's agents= dispatch is launched only when CreateRoom creates the room. A CreateRoom on an existing
  room changes nothing.
- Nothing re-dispatches a job when its agent or its worker goes away; the room only terminates the job.
  This server has no automatic re-dispatch (the LiveKit docs that describe one cover LiveKit Cloud).
- The job states that list_dispatch returns are a copy taken when the job was assigned and never updated
  (deregisterJob leaves the dispatch copy unchanged; its source marks that as still to do), so every job
  a room ever had reads JS_RUNNING forever. A pending/running job therefore counts as live only when it
  belongs to the worker running now (its worker_id is the one in run/agent_link.json) and started less
  than JOB_FRESH_S ago, which covers the agent's 60 s connect timeout. Without a known worker id, only
  the job's age decides.
- agent_ctl stops a worker with Ctrl+Break, so its agent leaves without a leave message and stays listed
  as a participant until the server notices the dropped connection (about 15 s).

The LiveKit API path. It never deletes a room: a delete cuts live voice/chat sessions (plan section 3).
1. Room absent: CreateRoom with the single agents=[RoomAgentDispatch(agent_name=<the worker's name>)] dispatch.
   No create_dispatch is added, because that would launch a second job.
2. Room present with an agent of the current worker: done, nothing changes. An agent belongs to the
   current worker when its identity is a job of that worker; when the jobs cannot tell, when it joined
   after the worker started. When neither can be checked, any agent counts (never a second agent).
3. Room present with only agents of an earlier worker: wait up to GHOST_WAIT_S for the server to drop
   them. When every agent still listed is a job of another LiveKit worker (both worker ids known and
   different: the worker that ran it is gone, since only one worker may run), check once more, polling up
   to OTHER_WORKER_GHOST_WAIT_S in all, and go on to step 4 if it left. An agent still listed after that
   is treated as live: nothing is added, and a warning says so.
4. Room present without an agent: a short safety wait (SELF_REDISPATCH_WAIT_S; nothing on this server
   re-dispatches by itself). Then, unless a live job is pending/running (it only gets the join wait),
   ONE create_dispatch(agent_name=<the worker's name>, room) and a wait of up to AGENT_JOIN_WAIT_S for its agent.
5. Still no agent: the room is left as it is, whoever is in it (NO AGENT). The v7/v8 last-resort delete is gone.
A participant or dispatch list that cannot be read never leads to an added dispatch.
Worst case, the waits add up to OTHER_WORKER_GHOST_WAIT_S + SELF_REDISPATCH_WAIT_S + AGENT_JOIN_WAIT_S (72 s); the
bridge path waits at most BRIDGE_RETRY_MAX_S + BRIDGE_TIMEOUT_S + BRIDGE_WAIT_S (105 s; a 429 answers at once).
Both fit agent_ctl's ENSURE_ROOM_TIMEOUT_S (120 s) with room for the start-up.

C-137: the console (agent_ctl start/restart shows it) gets only the room's name, sid, empty and departure
timeouts, whether an agent dispatch exists and counts/states, and the bridge's result, flags, mode and target. It
never gets the Room protobuf, which carries turn_password, nor participant identities, worker ids, the API key or
secret (exception text goes through _masked).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit import api

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
# The root .env is the single settings source (K-47): this file only fills keys it lacks.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

ROOM_NAME = os.getenv("JARVIS_ROOM_NAME", "jarvis-hybrid")

POLL_S = 2.0
# A stopped worker's agent stays listed until LiveKit notices the dropped connection (about 15 s after the
# stop; agent_ctl runs this 10-17 s after it), so this covers it with room to spare.
GHOST_WAIT_S = 20.0
# An agent whose job belongs to another LiveKit worker id (a dead worker's) is a ghost for sure, and LiveKit
# can take longer than GHOST_WAIT_S to drop it: it gets one more check, up to this long in all.
OTHER_WORKER_GHOST_WAIT_S = 44.0
# Only a safety margin: LiveKit v1.13.7 never re-dispatches a job by itself.
SELF_REDISPATCH_WAIT_S = 8.0
AGENT_JOIN_WAIT_S = 20.0
# A pending/running job younger than this may still bring its agent (the agent's connect timeout is 60 s).
JOB_FRESH_S = 90.0
# Server and PC clocks: how far apart they may be when an agent's join time is compared with the worker start.
CLOCK_SLACK_S = 5.0
ACTIVE_JOB_STATUSES = (api.JobStatus.JS_PENDING, api.JobStatus.JS_RUNNING)
AGENT_KIND = api.ParticipantInfo.Kind.AGENT
GONE_STATE = api.ParticipantInfo.State.DISCONNECTED

# The bridge path (C-23, K-53 v9)
REASONS = ("agent_ctl_start", "agent_ctl_restart", "watchdog", "brain_switch", "manual")
BRIDGE_TIMEOUT_S = 10.0   # one bridge call
BRIDGE_WAIT_S = 90.0      # how long the bridge's answer is followed for an agent of the current worker
BRIDGE_POLL_S = 3.0
BRIDGE_DOWN_STATUSES = (502, 503, 504)   # without a JSON body: nginx's own page, the bridge is down
BRIDGE_NO_LIVEKIT = "livekit_unavailable"  # a 503 with this error: the bridge could not use LiveKit at all
BRIDGE_RETRY_MAX_S = 5.0  # a 429 is asked once more after its Retry-After, at most this long
ROUTE_LIVEKIT, ROUTE_DESKTOP = "/livekit/ensure", "/desktop/ensure"
NO_RECONCILE_RESULTS = ("observe", "disabled")

RESULT_AGENT = "OK, an agent of the current worker is in the room"
RESULT_CREATED = "OK, room created with its agent dispatch (the worker gets the job)"
RESULT_NO_AGENT = ("NO AGENT in the room: JARVIS may stay silent until ensure_room.py runs again (the room "
                   "watchdog) or Voice reconnects through the bridge")
RESULT_EARLIER_AGENT = ("an agent of an earlier worker is still in the room and was left alone (no second "
                        "agent was added); run ensure_room.py again if JARVIS does not answer")
RESULT_UNKNOWN = "UNKNOWN: the room could not be read, nothing was changed"
RESULT_VPS_MODE = "OK, nothing to do in vps mode (JARVIS_BRAIN=vps: the VPS dispatches its own job)"
# A bridge that answers 200 without agent fields only made sure the room exists, so this result never claims an
# agent: it does not start with "OK" and agent_ctl treats it as a warning.
RESULT_BRIDGE = ("room ensured by the bridge (agent presence not checked; PC-signed /vortex/desktop/ensure "
                 "answered 200)")
RESULT_BRIDGE_UNCHECKED = ("room ensured by the bridge (agent presence not checked; signed /vortex/livekit/ensure "
                           "answered 200)")
RESULT_BRIDGE_AGENT = "OK, an agent of the current worker is in the room (bridge)"
RESULT_PAUSED = "PAUSED by the owner: the bridge dispatches no agent until Wake (ui_command start)"
RESULT_BRIDGE_NO_AGENT = ("NO AGENT in the room: the bridge reports {result} after 90 s; it keeps retrying "
                          "(backoff)")
RESULT_BRIDGE_REFUSED = "UNKNOWN: the bridge refused {route} (HTTP {code} {error}), nothing was changed"
RESULT_BRIDGE_TIMEOUT = "UNKNOWN: the bridge did not answer in time, nothing was changed"
RESULT_NO_SETTINGS = ("UNKNOWN: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing, nothing was "
                      "changed")
RESULT_LIST_ROOMS_FAILED = "UNKNOWN: list_rooms failed (the room could not be read), nothing was changed"
RESULT_ERROR = "UNKNOWN: ensure_room.py stopped on an unexpected {error}; check the room"
RESULT_INTERRUPTED = "UNKNOWN: ensure_room.py was interrupted (Ctrl+C); check the room"
RESULT_BAD_ARGS = "UNKNOWN: ensure_room.py was started with bad arguments, nothing was changed"
RESULT_PREFIX = "[ROOM] Result:"
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130  # 128 + SIGINT, as agent_ctl uses it

_result_printed = False
# Clocks of the bridge path (synchronous); the tests replace them.
_sleep: Callable[[float], None] = time.sleep
_monotonic: Callable[[], float] = time.monotonic


def print_result(text: str) -> None:
    """Prints the run's one "[ROOM] Result: ..." line; a second call in the same run prints nothing."""
    global _result_printed  # pylint: disable=global-statement
    if _result_printed:
        return
    _result_printed = True
    print(f"{RESULT_PREFIX} {text}", flush=True)


def current_brain() -> str:
    """'vps' or 'pc' (default pc); only agent_ctl brain writes JARVIS_BRAIN (K-72)."""
    value = (os.getenv("JARVIS_BRAIN") or "pc").strip().lower()
    return value if value in ("vps", "pc") else "pc"


def _http_url(ws_url: str) -> str:
    u = (ws_url or "").strip()
    if u.startswith("ws://"):
        return "http://" + u[len("ws://") :]
    if u.startswith("wss://"):
        return "https://" + u[len("wss://") :]
    return u


def _masked(text: object, *secrets: str) -> str:
    """text with each of the given credential values replaced by ***."""
    out = str(text)
    for value in secrets:
        if value:
            out = out.replace(value, "***")
    return out


def _now() -> float:
    return time.time()


class Worker:
    """The PC worker that holds run/agent.lock now: its start time (epoch seconds, PC clock), its LiveKit worker id
    from run/agent_link.json and the lk.agent.name it registered (C-23: link file, else JARVIS_AGENT_NAME, else '').
    started_at/worker_id are None/"" when unknown (no worker, or an older worker)."""

    def __init__(self, started_at: float | None = None, worker_id: str = "", agent_name: str = "") -> None:
        self.started_at = started_at
        self.worker_id = worker_id
        self.agent_name = agent_name

    def describe(self) -> str:
        known = {True: "yes", False: "no"}
        name = self.agent_name or "''"
        return (f"start time known={known[self.started_at is not None]} "
                f"LiveKit worker id known={known[bool(self.worker_id)]} agent name={name}")


def configured_agent_name() -> str:
    """JARVIS_AGENT_NAME as this PC's worker reads it ('' = automatic dispatch, K-69 v9)."""
    return (os.getenv("JARVIS_AGENT_NAME") or "").strip()


def current_worker() -> Worker:
    """The running worker as run/ describes it; a Worker with only the configured agent name when no worker holds
    the lock or run/ cannot be read. Both modules are stdlib only (agent_ctl imports them the same way)."""
    fallback = Worker(agent_name=configured_agent_name())
    try:
        from src.core import agent_lock, livekit_link  # pylint: disable=import-outside-toplevel
    except ImportError:
        return fallback
    try:
        if not agent_lock.is_locked():
            return fallback
        info = agent_lock.read_pid_info() or {}
        try:
            started: float | None = datetime.fromisoformat(str(info.get("started_at"))).timestamp()
        except (TypeError, ValueError):
            started = None
        link = livekit_link.read_link(info) or {}
        name = link.get("agent_name")
        return Worker(started, str(link.get("worker_id") or ""),
                      name if isinstance(name, str) else configured_agent_name())
    except OSError:
        return fallback


# --------------------------------------------------------------------------- the bridge path (C-23, K-53 v9)


def _signed_headers(body: bytes) -> dict[str, str]:
    """C-NOTIFY-1 headers over exactly body (services/utils/jarvis_bridge.signed_headers), imported when needed."""
    from services.utils.jarvis_bridge import signed_headers  # pylint: disable=import-outside-toplevel
    return signed_headers(body)


def ensure_body(reason: str, worker: Worker) -> bytes:
    """The exact bytes of a POST /livekit/ensure body: {reason, agent_name, worker_id when known}."""
    body: dict[str, Any] = {"reason": reason, "agent_name": worker.agent_name}
    if worker.worker_id:
        body["worker_id"] = worker.worker_id
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def bridge_route(worker: Worker) -> tuple[str, str, Callable[[str], tuple[bytes, dict[str, str]]]] | None:
    """(route, URL, request builder) of the first configured bridge route, else None. The builder makes a new body
    and new signed headers for every call (the bridge refuses a nonce it has seen)."""
    base = (os.getenv("UI_BRIDGE_URL") or "").strip().rstrip("/")
    if base and (os.getenv("AGENT_TOKEN") or "").strip() and (os.getenv("BRIDGE_HMAC_SECRET") or "").strip():
        def signed(reason: str) -> tuple[bytes, dict[str, str]]:
            body = ensure_body(reason, worker)
            return body, _signed_headers(body)

        return ROUTE_LIVEKIT, f"{base}/livekit/ensure", signed
    link_url = (os.getenv("JARVIS_LINK_URL") or "").strip().rstrip("/")
    if not link_url:
        return None
    try:
        from services.utils import desktop_protocol as dp  # pylint: disable=import-outside-toplevel
        from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    key_file = node_settings.node_home() / node_settings.KEYS_FILE
    if not key_file.is_file():
        return None

    def pc_signed(_reason: str) -> tuple[bytes, dict[str, str]]:
        key = dp.load_pc_key_file(key_file)
        return b"", {**dp.sign_http_request(key, method="POST", path="/vortex/desktop/ensure", body=b""),
                     "Content-Type": "application/json"}

    return ROUTE_DESKTOP, f"{link_url}/vortex/desktop/ensure", pc_signed


class BridgeAnswer:
    """One call's outcome: kind 'http' (status, data = the JSON object or None, retry_after = a 429's Retry-After in
    seconds), 'unreachable' (no connection at all: refused, DNS), 'timeout' or 'error' (any other failure: the bridge
    may have acted)."""

    def __init__(self, kind: str, status: int = 0, data: dict[str, Any] | None = None, note: str = "",
                 retry_after: float | None = None) -> None:
        self.kind, self.status, self.data, self.note, self.retry_after = kind, status, data, note, retry_after


def _retry_after_s(value: Any) -> float | None:
    """A Retry-After header in seconds (the bridge sends whole seconds), else None; the caller caps it."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 and seconds != float("inf") else None


def _call_bridge(url: str, request: tuple[bytes, dict[str, str]], timeout: float) -> BridgeAnswer:
    import httpx  # pylint: disable=import-outside-toplevel

    body, headers = request
    try:
        response = httpx.post(url, headers=headers, content=body, timeout=httpx.Timeout(timeout))
    except httpx.ConnectError as exc:  # refused, DNS: the bridge was never reached
        return BridgeAnswer("unreachable", note=type(exc).__name__)
    except httpx.TimeoutException as exc:
        return BridgeAnswer("timeout", note=type(exc).__name__)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a dropped answer: it may have acted
        return BridgeAnswer("error", note=type(exc).__name__)
    try:
        data = response.json()
    except Exception:  # pylint: disable=broad-exception-caught  # no JSON body (nginx's own page)
        data = None
    headers_in = getattr(response, "headers", None) or {}
    return BridgeAnswer("http", int(response.status_code), data if isinstance(data, dict) else None,
                        retry_after=_retry_after_s(headers_in.get("Retry-After")))


def _flag(value: Any) -> str:
    return "unknown" if value is None else ("yes" if value is True else "no")


def describe_bridge(data: dict[str, Any]) -> str:
    """The bridge's answer for the console: result, flags, mode and target; never an identity or a worker id."""
    target = data.get("target_agent_name")
    return (f"result={data.get('result')} agent_in_room={_flag(data.get('agent_in_room'))} "
            f"voice_ready={_flag(data.get('voice_ready'))} current_worker={_flag(data.get('agent_current_worker'))} "
            f"mode={data.get('mode')} paused={_flag(data.get('paused'))} "
            f"target={target if target else repr('') if target == '' else '?'}")


def _bridge_done(data: dict[str, Any]) -> str | None:
    """The result once the answer shows paused or an agent of the current worker, else None."""
    if data.get("paused") is True:
        return RESULT_PAUSED
    current = data.get("agent_current_worker")
    if current is True or (current is None and data.get("agent_in_room") is True):
        return RESULT_BRIDGE_AGENT
    return None


def _wait_for_agent(url: str, build: Callable[[str], tuple[bytes, dict[str, str]]],
                    first: dict[str, Any]) -> str:
    """Follows the bridge every BRIDGE_POLL_S (reason 'wait', fresh headers) for up to BRIDGE_WAIT_S."""
    deadline = _monotonic() + BRIDGE_WAIT_S
    last, shown, noted = first, describe_bridge(first), set()
    while True:
        done = _bridge_done(last)
        if done is not None:
            return done
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return RESULT_BRIDGE_NO_AGENT.format(result=last.get("result"))
        _sleep(min(BRIDGE_POLL_S, remaining))
        timeout = max(1.0, min(BRIDGE_TIMEOUT_S, deadline - _monotonic()))
        answer = _call_bridge(url, build("wait"), timeout)
        if answer.kind == "http" and answer.status == 200 and answer.data is not None and "result" in answer.data:
            last = answer.data
            line = describe_bridge(last)
            if line != shown:
                shown = line
                print(f"[ROOM] Bridge: {line}", flush=True)
        elif answer.kind + str(answer.status) not in noted:
            noted.add(answer.kind + str(answer.status))
            what = f"HTTP {answer.status}" if answer.kind == "http" else f"{answer.kind} ({answer.note})"
            print(f"[ROOM] Bridge poll: {what}; still waiting", flush=True)


# pylint: disable-next=too-many-return-statements  # one answer per bridge outcome (K-53 v9)
def ask_bridge(reason: str, worker: Worker) -> str | None:
    """The bridge path (K-53 v9). The result text when the bridge reconciles, refused or did not answer in time;
    None when it provably does not reconcile, and the LiveKit API path follows."""
    route = bridge_route(worker)
    if route is None:
        return None
    name, url, build = route
    try:
        request = build(reason)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a key that cannot sign: no bridge call
        print(f"[ROOM] {name} could not be signed ({type(exc).__name__}); using the LiveKit API.")
        return None
    print(f"[ROOM] Asking the bridge: POST {name} (reason {reason})", flush=True)
    answer = _call_bridge(url, request, BRIDGE_TIMEOUT_S)
    if answer.kind == "http" and answer.status == 429:
        wait = min(BRIDGE_RETRY_MAX_S, answer.retry_after if answer.retry_after is not None else BRIDGE_POLL_S)
        print(f"[ROOM] {name} is busy (HTTP 429); asking once more in {wait:.0f} s.", flush=True)
        _sleep(wait)
        try:
            request = build(reason)  # new headers: the bridge spends a nonce per call
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a key that cannot sign any more
            return RESULT_BRIDGE_REFUSED.format(route=name, code=429, error=type(exc).__name__)
        answer = _call_bridge(url, request, BRIDGE_TIMEOUT_S)
    if answer.kind == "unreachable":
        print(f"[ROOM] The bridge cannot be reached ({answer.note}); using the LiveKit API.")
        return None
    if answer.kind == "timeout":
        print(f"[ROOM] {name} did not answer within {BRIDGE_TIMEOUT_S:.0f} s ({answer.note}).")
        return RESULT_BRIDGE_TIMEOUT
    if answer.kind == "error":
        return RESULT_BRIDGE_REFUSED.format(route=name, code="-", error=answer.note)
    status, data = answer.status, answer.data
    if status == 404 or (status in BRIDGE_DOWN_STATUSES and data is None):
        print(f"[ROOM] {name} returned HTTP {status}; using the LiveKit API.")
        return None
    if status == 503 and (data or {}).get("error") == BRIDGE_NO_LIVEKIT:
        # it read nothing and dispatched nothing in this call; the LiveKit path checks agents and live jobs itself
        print(f"[ROOM] {name}: the bridge cannot use LiveKit ({BRIDGE_NO_LIVEKIT}); using the LiveKit API.")
        return None
    if status != 200:
        error = str((data or {}).get("error") or "-")[:40]
        return RESULT_BRIDGE_REFUSED.format(route=name, code=status, error=error)
    if data is None or "result" not in data or "agent_in_room" not in data:
        signed = "PC-signed" if name == ROUTE_DESKTOP else "signed"
        print(f"[ROOM] Room ensured through the bridge ({signed} /vortex{name} answered 200); whether an agent is "
              "in the room was not checked.")
        return RESULT_BRIDGE if name == ROUTE_DESKTOP else RESULT_BRIDGE_UNCHECKED
    print(f"[ROOM] Bridge: {describe_bridge(data)}", flush=True)
    if data.get("paused") is True:  # the owner's pause holds in every mode: nothing is dispatched, here neither
        return RESULT_PAUSED
    if data.get("result") in NO_RECONCILE_RESULTS:
        print(f"[ROOM] The bridge does not dispatch (result {data.get('result')}); using the LiveKit API.")
        return None
    return _wait_for_agent(url, build, data)


# --------------------------------------------------------------------------- the LiveKit API path


def _epoch_s(value: int | float) -> float:
    """A LiveKit timestamp as epoch seconds, whatever its unit (v1.13.7 job states use nanoseconds)."""
    number = float(value or 0)
    if number <= 0:
        return 0.0
    if number > 1e17:
        return number / 1e9
    if number > 1e14:
        return number / 1e6
    if number > 1e11:
        return number / 1e3
    return number


def describe_room(room: Any, dispatches: int | None) -> str:
    """The console summary of a LiveKit Room: chosen fields only, never turn_password or metadata."""
    dispatch = "unknown" if dispatches is None else f"yes ({dispatches})" if dispatches else "no"
    return (f"name={room.name} sid={room.sid} empty_timeout={room.empty_timeout}s "
            f"departure_timeout={room.departure_timeout}s agent_dispatch={dispatch}")


class People:
    """Who is in the room, as counts only (identities are never printed). current: agents of the running
    worker; earlier: agents of an earlier worker (a stopped worker's agent stays listed for a while), of
    which other_worker were told apart by their job's worker id rather than by their join time. A plain
    class, not a dataclass: the tests load this file by path, without a sys.modules entry, which dataclasses
    need."""

    def __init__(self) -> None:
        self.current = 0
        self.earlier = 0
        self.other_worker = 0
        self.others = 0
        self.kinds: Counter[str] = Counter()

    @property
    def agents(self) -> int:
        return self.current + self.earlier

    def describe(self) -> str:
        kinds = " ".join(f"{name}={count}" for name, count in sorted(self.kinds.items())) or "none"
        return (f"agents={self.agents} others={self.others} earlier_worker_agents={self.earlier} "
                f"other_worker_id_agents={self.other_worker} (by kind: {kinds})")


class Dispatches:
    """The room's agent dispatches: how many, the states of their jobs, and how many pending/running jobs
    are live (see _job_is_live) or stale. owners maps each job's agent identity to the job's worker id; it
    is only compared, never printed."""

    def __init__(self) -> None:
        self.count = 0
        self.live_jobs = 0
        self.stale_jobs = 0
        self.job_states: Counter[str] = Counter()
        self.owners: dict[str, str] = {}

    def describe(self) -> str:
        states = " ".join(f"{name}={count}" for name, count in sorted(self.job_states.items())) or "none"
        return (f"dispatches={self.count} live_jobs={self.live_jobs} stale_jobs={self.stale_jobs} "
                f"(job states: {states})")


def _kind_name(kind: int) -> str:
    try:
        return str(api.ParticipantInfo.Kind.Name(kind))
    except ValueError:
        return f"KIND_{kind}"


def _job_status_name(status: int) -> str:
    try:
        return str(api.JobStatus.Name(status))
    except ValueError:
        return f"STATUS_{status}"


def _job_is_live(state: Any, worker: Worker, now: float) -> bool:
    """A pending/running job that may still bring an agent. list_dispatch never updates a job's state on
    v1.13.7, so the state alone proves nothing: the job must be the current worker's (when both worker ids
    are known) and younger than JOB_FRESH_S."""
    if state.status not in ACTIVE_JOB_STATUSES:
        return False
    started = max(_epoch_s(state.started_at), _epoch_s(state.updated_at))
    recent = started >= now - JOB_FRESH_S
    if worker.worker_id and state.worker_id:
        return state.worker_id == worker.worker_id and (not started or recent)
    return bool(started) and recent


async def _dispatches(lk: api.LiveKitAPI, worker: Worker, *secrets: str) -> Dispatches | None:
    """The room's agent dispatches and their jobs, or None when the server cannot list them."""
    try:
        listed = await lk.agent_dispatch.list_dispatch(room_name=ROOM_NAME)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ROOM] list_dispatch note: {_masked(exc, *secrets)}")
        return None
    now = _now()
    summary = Dispatches()
    for dispatch in listed:
        if dispatch.state.deleted_at:
            continue
        summary.count += 1
        for job in dispatch.state.jobs:
            state = job.state
            summary.job_states[_job_status_name(state.status)] += 1
            # livekit-agents names a job's agent "agent-<job id>" unless the worker chose another identity.
            for identity in (state.participant_identity, f"agent-{job.id}" if job.id else ""):
                if identity:
                    summary.owners[identity] = state.worker_id
            if state.status in ACTIVE_JOB_STATUSES:
                if _job_is_live(state, worker, now):
                    summary.live_jobs += 1
                else:
                    summary.stale_jobs += 1
    return summary


OWNER_CURRENT = "current"
OWNER_OTHER_WORKER = "other_worker"  # its job's worker id is known and is not the running worker's
OWNER_EARLIER = "earlier"  # it joined before the running worker started


def _agent_owner(participant: Any, dispatches: Dispatches | None, worker: Worker) -> str:
    """Whose the AGENT participant is: OWNER_CURRENT, OWNER_OTHER_WORKER or OWNER_EARLIER. Its job's worker
    id decides when both ids are known; else its join time against the worker start; when neither can be
    checked it counts as current, so an agent that might be live is never doubled."""
    owner = dispatches.owners.get(participant.identity, "") if dispatches is not None else ""
    if worker.worker_id and owner:
        return OWNER_CURRENT if owner == worker.worker_id else OWNER_OTHER_WORKER
    joined = participant.joined_at_ms / 1000 if participant.joined_at_ms else float(participant.joined_at)
    if worker.started_at is not None and 0 < joined < worker.started_at - CLOCK_SLACK_S:
        return OWNER_EARLIER
    return OWNER_CURRENT


async def _look(lk: api.LiveKitAPI, worker: Worker, *secrets: str) -> tuple[People | None, Dispatches | None]:
    """The room's connected participants as counts, or None when the server cannot list them. The
    dispatches are listed too (to tell the current worker's agent from an earlier one) only when an agent is
    in the room, else None."""
    try:
        response = await lk.room.list_participants(api.ListParticipantsRequest(room=ROOM_NAME))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ROOM] list_participants note: {_masked(exc, *secrets)}")
        return None, None
    present = [p for p in response.participants if p.state != GONE_STATE]
    dispatches = None
    if any(p.kind == AGENT_KIND for p in present):
        dispatches = await _dispatches(lk, worker, *secrets)
    people = People()
    for participant in present:
        people.kinds[_kind_name(participant.kind)] += 1
        if participant.kind != AGENT_KIND:
            people.others += 1
            continue
        owner = _agent_owner(participant, dispatches, worker)
        if owner == OWNER_CURRENT:
            people.current += 1
        else:
            people.earlier += 1
            if owner == OWNER_OTHER_WORKER:
                people.other_worker += 1
    return people, dispatches


async def _find_room(lk: api.LiveKitAPI) -> Any:
    rooms = await lk.room.list_rooms(api.ListRoomsRequest(names=[ROOM_NAME]))
    return next((r for r in rooms.rooms if r.name == ROOM_NAME), None)


async def _create_room(lk: api.LiveKitAPI, agent_name: str, *secrets: str) -> bool:
    """CreateRoom with the single agents= dispatch to the worker's name (LiveKit launches it only for a room it
    creates)."""
    try:
        # departure_timeout default is ~20s — if the browser briefly drops,
        # LiveKit closes the room and kills the agent job (chat+voice die).
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=ROOM_NAME,
                empty_timeout=3600,
                departure_timeout=3600,
                max_participants=10,
                agents=[api.RoomAgentDispatch(agent_name=agent_name)],
            )
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ROOM] create_room note: {_masked(exc, *secrets)}")
        return False
    print(f"[ROOM] Created room '{ROOM_NAME}' with single agent dispatch")
    return True


async def _wait(lk: api.LiveKitAPI, worker: Worker, wait_s: float, done: Callable[[People], bool],
                *secrets: str) -> People | None:
    """Poll the participants every POLL_S for up to wait_s; returns as soon as done(people) holds. None when
    the participants could not be listed."""
    people: People | None = None
    for _ in range(max(1, round(wait_s / POLL_S))):
        await asyncio.sleep(POLL_S)
        people, _ = await _look(lk, worker, *secrets)
        if people is None or done(people):
            return people
    return people


def _any_agent(people: People) -> bool:
    return people.agents > 0


def _ghosts_settled(people: People) -> bool:
    """The ghost wait is over: the current worker's agent is in, or no earlier worker's agent is left."""
    return bool(people.current or not people.earlier)


def _only_other_worker_ghosts(people: People | None) -> bool:
    """After GHOST_WAIT_S, only agents of another LiveKit worker id are still listed (none of the running
    worker's), so they get one more check."""
    return bool(people is not None and not people.current and people.earlier
                and people.other_worker == people.earlier)


def _no_agent_end(people: People | None) -> str | None:
    """The result when the poll ended with an agent in the room or unreadable participants, else None."""
    if people is None:
        print("[WARN] The room's participants could not be listed: the room was left unchanged.")
        return RESULT_UNKNOWN
    if people.current:
        print(f"[OK] Agent joined the room ({people.describe()}).")
        return RESULT_AGENT
    if people.earlier:
        print(f"[WARN] An agent that is not the current worker's is in the room ({people.describe()}): it is "
              "treated as live, so no dispatch is added (a second agent would answer twice).")
        return RESULT_EARLIER_AGENT
    return None


# pylint: disable-next=too-many-return-statements
async def _ensure_agent(lk: api.LiveKitAPI, worker: Worker, *secrets: str) -> str:
    """An existing room: add a job only when it has no agent, never a second agent, and never delete the room.
    Returns the result line's text."""
    people, _ = await _look(lk, worker, *secrets)
    if people is None:
        print("[WARN] The room's participants could not be listed: the room was left unchanged.")
        return RESULT_UNKNOWN
    print(f"[ROOM] Participants: {people.describe()}")
    if people.current:
        print("[OK] Room ready — an agent is already in the room; nothing changed.")
        return RESULT_AGENT

    if people.earlier:
        print(f"[ROOM] {people.earlier} agent(s) in the room belong to an earlier worker (a stopped worker's "
              f"agent stays listed until LiveKit notices): waiting up to {GHOST_WAIT_S:.0f} s for them to "
              "leave.")
        people = await _wait(lk, worker, GHOST_WAIT_S, _ghosts_settled, *secrets)
        if _only_other_worker_ghosts(people):
            extra_s = OTHER_WORKER_GHOST_WAIT_S - GHOST_WAIT_S
            print(f"[ROOM] The agent(s) still listed belong to another LiveKit worker, which no longer runs: "
                  f"checking once more, up to {extra_s:.0f} s, before treating them as live.")
            people = await _wait(lk, worker, extra_s, _ghosts_settled, *secrets)
        ended = _no_agent_end(people)
        if ended is not None:
            return ended
        print("[ROOM] The earlier worker's agent left the room.")

    print(f"[ROOM] No agent in the room: waiting {SELF_REDISPATCH_WAIT_S:.0f} s before adding a dispatch "
          "(a safety margin: LiveKit v1.13.7 does not re-dispatch a job by itself).")
    people = await _wait(lk, worker, SELF_REDISPATCH_WAIT_S, _any_agent, *secrets)
    ended = _no_agent_end(people)
    if ended is not None:
        return ended

    dispatches = await _dispatches(lk, worker, *secrets)
    if dispatches is None:
        print("[ROOM] The dispatches could not be listed: no dispatch is added (it could double a job).")
    elif dispatches.live_jobs:
        print(f"[ROOM] {dispatches.describe()}: a job of the current worker started less than "
              f"{JOB_FRESH_S:.0f} s ago, so no dispatch is added; waiting for its agent.")
        people = await _wait(lk, worker, AGENT_JOIN_WAIT_S, _any_agent, *secrets)
    else:
        print(f"[ROOM] {dispatches.describe()}: no live job (on LiveKit v1.13.7 list_dispatch keeps an ended "
              "job as running), so one agent dispatch is added.")
        try:
            await lk.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(agent_name=worker.agent_name, room=ROOM_NAME))
            print("[ROOM] Agent dispatch created; waiting for the agent to join.")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ROOM] create_dispatch note: {_masked(exc, *secrets)}")
        people = await _wait(lk, worker, AGENT_JOIN_WAIT_S, _any_agent, *secrets)
    ended = _no_agent_end(people)
    if ended is not None:
        return ended

    if people is None:  # _no_agent_end already answered None; this keeps the type narrow
        return RESULT_UNKNOWN
    who = "a person is in it" if people.others else "nobody is in it"
    print(f"[WARN] No agent joined room '{ROOM_NAME}' and {who} ({people.describe()}): the room is never deleted "
          "(C-23). Reconnect from the UI, or run ensure_room.py again.")
    return RESULT_NO_AGENT


async def ensure_room() -> None:
    """The LiveKit API path (also used when the bridge provably does not reconcile)."""
    livekit_url = os.getenv("LIVEKIT_URL", "")
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not livekit_url or not api_key or not api_secret:
        print("[ERROR] LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
        print_result(RESULT_NO_SETTINGS)
        sys.exit(EXIT_FAILED)

    http_url = _http_url(livekit_url)
    print(f"[ROOM] LiveKit API: {http_url}")
    print(f"[ROOM] Ensuring room: {ROOM_NAME}")
    worker = current_worker()
    print(f"[ROOM] Current worker: {worker.describe()}")

    secrets = (api_key, api_secret)
    lk = api.LiveKitAPI(http_url, api_key, api_secret)
    try:
        try:
            room = await _find_room(lk)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ERROR] list_rooms failed: {_masked(exc, *secrets)}")
            print_result(RESULT_LIST_ROOMS_FAILED)
            sys.exit(EXIT_FAILED)
        if room is None:
            # A fresh room: its agents= dispatch is the single dispatch path (no create_dispatch here).
            await _create_room(lk, worker.agent_name, *secrets)
            room = await _find_room(lk)
            if room is None:
                print(f"[ROOM] Listed: room '{ROOM_NAME}' not found")
                print("[WARN] The room is missing: the agent worker gets no job until it exists.")
                print_result(RESULT_NO_AGENT)
                return
            dispatches = await _dispatches(lk, worker, *secrets)
            print(f"[ROOM] Listed: {describe_room(room, None if dispatches is None else dispatches.count)}")
            print("[OK] Room ready — agent worker should receive the job now.")
            print_result(RESULT_CREATED)
            return
        dispatches = await _dispatches(lk, worker, *secrets)
        print(f"[ROOM] Listed: {describe_room(room, None if dispatches is None else dispatches.count)}")
        if dispatches is not None:
            print(f"[ROOM] {dispatches.describe()}")
        result = await _ensure_agent(lk, worker, *secrets)
        print_result(result)
    finally:
        await lk.aclose()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ensure_room.py",
                                     description="Make sure the JARVIS room has one agent of this PC's worker.")
    parser.add_argument("--reason", choices=REASONS, default="manual",
                        help="who asks (the bridge logs it with its dispatch); default manual")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> None:
    """One run: the vps-mode no-op, the bridge, else the LiveKit API path. Exactly one "[ROOM] Result: ..."
    line on every exit path (see the module docstring for the exit codes). argv: the arguments after the
    script name (none: --reason manual)."""
    try:
        try:
            args = parse_args([] if argv is None else argv)
        except SystemExit as exc:  # argparse: a usage error (2) or --help (0)
            if exc.code not in (0, None):
                print_result(RESULT_BAD_ARGS)
                sys.exit(EXIT_USAGE)
            raise
        if current_brain() == "vps":
            print("[ROOM] JARVIS_BRAIN=vps: the VPS dispatches its own job; ensure_room.py is a no-op in vps "
                  "mode.")
            print_result(RESULT_VPS_MODE)
            return
        bridge_result = ask_bridge(args.reason, current_worker())
        if bridge_result is not None:
            print_result(bridge_result)
            return
        asyncio.run(ensure_room())  # its own exit 1 (SystemExit) comes after its result line and passes through
    except KeyboardInterrupt:  # Ctrl+C: still one result line (unless the run already printed its own)
        print("[ROOM] Interrupted (Ctrl+C).")
        print_result(RESULT_INTERRUPTED)
        sys.exit(EXIT_INTERRUPTED)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # still one result line, then exit 1
        print(f"[ERROR] ensure_room.py stopped on an unexpected {type(exc).__name__}.")
        if not _result_printed:
            print_result(RESULT_ERROR.format(error=type(exc).__name__))
        sys.exit(EXIT_FAILED)
    if not _result_printed:  # every path above prints one; this keeps that true if a path is ever added
        print_result(RESULT_UNKNOWN)


if __name__ == "__main__":
    main(sys.argv[1:])
