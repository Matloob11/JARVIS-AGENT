"""
Make sure the LiveKit room has exactly one JARVIS agent, so the local Windows worker actually gets a job.
Without a job the worker stays idle and UI chat never reaches the agent.

agent_ctl start/restart runs this once the new worker is healthy and linked. agent_ctl reports its exit
code, and any non-zero code marks the start as failed. So every outcome below exits 0 and ends with one
"[ROOM] Result: ..." line that says whether an agent is in the room. The worker registers with no
agent_name (automatic dispatch).

C-123 (VPS brain): this runs in pc mode ONLY. In vps mode the VPS dispatches its own job, so this exits as
a no-op. When a PC signing key and JARVIS_LINK_URL are configured it asks the bridge's PC-signed
/vortex/desktop/ensure first and falls back to the LiveKit API below when that does not answer 200.

What the self-hosted LiveKit server v1.13.7 does (read in its source at that tag: pkg/rtc/room.go,
pkg/agent/worker.go, pkg/service/agentservice.go). This file is built around it:
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

The LiveKit API path. It never deletes a live room: a delete cuts live voice/chat sessions (plan section 3).
1. Room absent: CreateRoom with the single agents=[RoomAgentDispatch(agent_name="")] dispatch. No
   create_dispatch is added, because that would launch a second job.
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
   ONE create_dispatch(agent_name="", room) and a wait of up to AGENT_JOIN_WAIT_S for its agent. The empty
   agent_name is the one the room's own default dispatch uses, and it reaches automatic-dispatch workers.
5. Still no agent and nobody else in the room: delete and recreate the room with agents= (the known-good
   path; nobody is cut off). A person in the room: never delete. The warning and the result line say that
   JARVIS may stay silent until the UI reconnects (the bridge's token mint recreates the room).
A participant list that cannot be read never leads to an added dispatch or a delete. A dispatch list that
cannot be read never leads to an added dispatch, and never to a delete while anyone is in the room: with
nobody in it, step 5 may still recreate the room (that cuts nobody).
Worst case, the waits add up to OTHER_WORKER_GHOST_WAIT_S + SELF_REDISPATCH_WAIT_S + 2 * AGENT_JOIN_WAIT_S
(92 s), inside agent_ctl's ENSURE_ROOM_TIMEOUT_S (120 s) with room for the API calls and the start-up.

C-137: the console (agent_ctl start/restart shows it) gets only the room's name, sid, empty and departure
timeouts, whether an agent dispatch exists and counts/states. It never gets the Room protobuf, which
carries turn_password, nor participant identities, worker ids, the API key or secret (exception text goes
through _masked).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
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

RESULT_AGENT = "OK, an agent of the current worker is in the room"
RESULT_CREATED = "OK, room created with its agent dispatch (the worker gets the job)"
RESULT_NO_AGENT = ("NO AGENT in the room: JARVIS may stay silent until the UI reconnects or ensure_room.py "
                   "runs again")
RESULT_EARLIER_AGENT = ("an agent of an earlier worker is still in the room and was left alone (no second "
                        "agent was added); run ensure_room.py again if JARVIS does not answer")
RESULT_UNKNOWN = "UNKNOWN: the room could not be read, nothing was changed"


def current_brain() -> str:
    """'vps' or 'pc' (default pc); only agent_ctl brain writes JARVIS_BRAIN (K-72)."""
    value = (os.getenv("JARVIS_BRAIN") or "pc").strip().lower()
    return value if value in ("vps", "pc") else "pc"


def ensure_via_bridge() -> bool:
    """Ask the bridge's PC-signed /vortex/desktop/ensure to create the room (never delete). True when the
    bridge handled it; False when no PC key is configured or the call did not succeed, so the caller falls
    back to the LiveKit API path."""
    link_url = (os.getenv("JARVIS_LINK_URL") or "").strip()
    if not link_url:
        return False
    try:
        import httpx  # pylint: disable=import-outside-toplevel
        from services.utils import desktop_protocol as dp  # pylint: disable=import-outside-toplevel
        from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False
    key_file = node_settings.node_home() / node_settings.KEYS_FILE
    if not key_file.is_file():
        return False
    try:
        key = dp.load_pc_key_file(key_file)
        headers = {**dp.sign_http_request(key, method="POST", path="/vortex/desktop/ensure", body=b""),
                   "Content-Type": "application/json"}
        response = httpx.post(f"{link_url.rstrip('/')}/vortex/desktop/ensure", headers=headers, content=b"",
                              timeout=httpx.Timeout(10.0))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # fall back to the LiveKit path
        print(f"[ROOM] PC-signed /vortex/desktop/ensure note: {type(exc).__name__}")
        return False
    if response.status_code == 200:
        print("[OK] Room ensured through the bridge (PC-signed /vortex/desktop/ensure).")
        return True
    print(f"[ROOM] /vortex/desktop/ensure returned HTTP {response.status_code}; using the LiveKit API.")
    return False


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
    """The PC worker that holds run/agent.lock now: its start time (epoch seconds, PC clock) and its LiveKit
    worker id from run/agent_link.json. Either is None/"" when unknown (no worker, or an older worker)."""

    def __init__(self, started_at: float | None = None, worker_id: str = "") -> None:
        self.started_at = started_at
        self.worker_id = worker_id

    def describe(self) -> str:
        known = {True: "yes", False: "no"}
        return (f"start time known={known[self.started_at is not None]} "
                f"LiveKit worker id known={known[bool(self.worker_id)]}")


def current_worker() -> Worker:
    """The running worker as run/ describes it; an empty Worker when no worker holds the lock or run/ cannot
    be read. Both modules are stdlib only (agent_ctl imports them the same way)."""
    try:
        from src.core import agent_lock, livekit_link  # pylint: disable=import-outside-toplevel
    except ImportError:
        return Worker()
    try:
        if not agent_lock.is_locked():
            return Worker()
        info = agent_lock.read_pid_info() or {}
        try:
            started: float | None = datetime.fromisoformat(str(info.get("started_at"))).timestamp()
        except (TypeError, ValueError):
            started = None
        link = livekit_link.read_link(info) or {}
        return Worker(started, str(link.get("worker_id") or ""))
    except OSError:
        return Worker()


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


async def _create_room(lk: api.LiveKitAPI, *secrets: str) -> bool:
    """CreateRoom with the single agents= dispatch (LiveKit launches it only for a room it creates)."""
    try:
        # departure_timeout default is ~20s — if the browser briefly drops,
        # LiveKit closes the room and kills the agent job (chat+voice die).
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=ROOM_NAME,
                empty_timeout=3600,
                departure_timeout=3600,
                max_participants=10,
                agents=[api.RoomAgentDispatch(agent_name="")],
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


async def _recreate_empty_room(lk: api.LiveKitAPI, worker: Worker, *secrets: str) -> str:
    """Last resort for a room with nobody in it: delete it and create it again with agents=."""
    people, _ = await _look(lk, worker, *secrets)  # re-checked right before the delete
    if people is None or people.agents or people.others:
        state = "unknown" if people is None else people.describe()
        print(f"[WARN] Room '{ROOM_NAME}' is no longer empty ({state}); it was not recreated.")
        return RESULT_AGENT if people is not None and people.current else RESULT_NO_AGENT
    print(f"[ROOM] Room '{ROOM_NAME}' is empty and has no agent: deleting and recreating it with agents=.")
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=ROOM_NAME))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[ROOM] delete_room note: {_masked(exc, *secrets)}")
        print("[WARN] The empty room could not be recreated: the agent worker may get no job.")
        return RESULT_NO_AGENT
    if not await _create_room(lk, *secrets):
        print("[WARN] The room was deleted but not recreated: the agent worker gets no job until it exists.")
        return RESULT_NO_AGENT
    people = await _wait(lk, worker, AGENT_JOIN_WAIT_S, _any_agent, *secrets)
    if people is not None and people.current:
        print(f"[OK] Agent joined the recreated room ({people.describe()}).")
        return RESULT_AGENT
    print("[WARN] The room was recreated but no agent joined yet: check logs/agent_stderr.log.")
    return RESULT_NO_AGENT


# pylint: disable-next=too-many-return-statements
async def _ensure_agent(lk: api.LiveKitAPI, worker: Worker, *secrets: str) -> str:
    """An existing room: add a job only when it has no agent, never a second agent, and never cut a live
    session. Returns the result line's text."""
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
                api.CreateAgentDispatchRequest(agent_name="", room=ROOM_NAME))
            print("[ROOM] Agent dispatch created; waiting for the agent to join.")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[ROOM] create_dispatch note: {_masked(exc, *secrets)}")
        people = await _wait(lk, worker, AGENT_JOIN_WAIT_S, _any_agent, *secrets)
    ended = _no_agent_end(people)
    if ended is not None:
        return ended

    if people is None:  # _no_agent_end already answered None; this keeps the type narrow
        return RESULT_UNKNOWN
    if people.others:
        print(f"[WARN] No agent joined room '{ROOM_NAME}' and a person is in it ({people.describe()}): "
              "the room is never deleted under a live session. Reconnect from the UI, or run "
              "ensure_room.py again once the room is empty.")
        return RESULT_NO_AGENT
    return await _recreate_empty_room(lk, worker, *secrets)


async def ensure_room() -> None:
    livekit_url = os.getenv("LIVEKIT_URL", "")
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not livekit_url or not api_key or not api_secret:
        print("[ERROR] LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET missing")
        sys.exit(1)

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
            sys.exit(1)
        if room is None:
            # A fresh room: its agents= dispatch is the single dispatch path (no create_dispatch here).
            await _create_room(lk, *secrets)
            room = await _find_room(lk)
            if room is None:
                print(f"[ROOM] Listed: room '{ROOM_NAME}' not found")
                print("[WARN] The room is missing: the agent worker gets no job until it exists.")
                print(f"[ROOM] Result: {RESULT_NO_AGENT}")
                return
            dispatches = await _dispatches(lk, worker, *secrets)
            print(f"[ROOM] Listed: {describe_room(room, None if dispatches is None else dispatches.count)}")
            print("[OK] Room ready — agent worker should receive the job now.")
            print(f"[ROOM] Result: {RESULT_CREATED}")
            return
        dispatches = await _dispatches(lk, worker, *secrets)
        print(f"[ROOM] Listed: {describe_room(room, None if dispatches is None else dispatches.count)}")
        if dispatches is not None:
            print(f"[ROOM] {dispatches.describe()}")
        result = await _ensure_agent(lk, worker, *secrets)
        print(f"[ROOM] Result: {result}")
    finally:
        await lk.aclose()


def main() -> None:
    if current_brain() == "vps":
        print("[ROOM] JARVIS_BRAIN=vps: the VPS dispatches its own job; ensure_room.py is a no-op in vps mode.")
        return
    if ensure_via_bridge():
        return
    asyncio.run(ensure_room())


if __name__ == "__main__":
    main()
