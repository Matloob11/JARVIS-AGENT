"""Plan task C-23: the bridge's room owner (src/core/bridge_rooms.py, K-44 v9) and its routes in ui_bridge.

FakeLiveKit answers like LiveKit server v1.13.7 with real protobufs: dispatch states carry created_at in ns, a job
copied at assignment keeps its state (an ended job still reads JS_RUNNING), a new room created without an agents list
gets LiveKit's implicit '' dispatch, and nothing launches a job by itself. delete_room and delete_dispatch fail the
test. The clocks are fake (one mono and one wall that move together), bridge.db is temporary, and every credential
is a throwaway value made here, so nothing reaches the live bridge, LiveKit or a real env file.
"""
# pylint: disable=protected-access,redefined-outer-name,too-many-lines,no-member  # protobuf members are generated
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import logging
import secrets
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.protobuf.json_format import MessageToJson
from livekit import api as lk_api
from livekit.protocol import agent, agent_dispatch, models, webhook
from livekit.protocol import room as lk_room

from services.utils import desktop_protocol as dp
from services.utils import jarvis_security
from services.utils.jarvis_config import config
from src.core import bridge_rooms
from src.core.bridge_desktop import INTERNAL_PORT, DesktopLink, LinkKeys
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import KV_PAUSED, KV_PC_AGENT, KV_ROOM_DISPATCH, KV_ROOM_MODE, BridgeStore

ROOM = "c23-room"
LK_KEY, LK_SECRET = "c23-test-lk-key", "c23-test-lk-secret-" + "f" * 32
AGENT_TOKEN = "c23-rooms-agent-token-" + "a" * 24
HMAC_SECRET = "c23-rooms-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c23-rooms-legacy-token-" + "c" * 24
WORKER_ID, OLD_WORKER_ID = "AW_c23current", "AW_c23earlier"
OWNER = "owner-c23aaaa1"  # a person's identity: never in a log, the status or the CLI output
AGENT_KIND, STANDARD = models.ParticipantInfo.Kind.AGENT, models.ParticipantInfo.Kind.STANDARD
ACTIVE, JOINED = models.ParticipantInfo.State.ACTIVE, models.ParticipantInfo.State.JOINED
DISCONNECTED = models.ParticipantInfo.State.DISCONNECTED
RUNNING, PENDING = agent.JobStatus.JS_RUNNING, agent.JobStatus.JS_PENDING
T0 = 1_791_000_000.0
PC_KEY, FORGER_KEY, AGENT_LINK_KEY = dp.generate_signing_key(), dp.generate_signing_key(), dp.generate_signing_key()


class Clock:
    """One fake monotonic and one fake wall clock that move together."""

    def __init__(self) -> None:
        self.wall, self.mono = T0, 500.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


def _ns(epoch_s: float) -> int:
    return int(epoch_s * 1e9)


def person(identity: str = OWNER, state: int = ACTIVE) -> models.ParticipantInfo:
    return models.ParticipantInfo(identity=identity, kind=STANDARD, state=state, joined_at=int(T0 - 600))


def agent_participant(job_id: str, clock: Clock, *, name: str = "", session: str | None = None,
                      state: int = ACTIVE, age_s: float = 10.0, kind: int = AGENT_KIND) -> models.ParticipantInfo:
    """An agent as livekit-agents makes it: identity agent-<job id>, lk.agent.name (LiveKit drops an empty
    attribute, so '' means none), jarvis.session when the build sets it."""
    attributes = {}
    if name:
        attributes["lk.agent.name"] = name
    if session is not None:
        attributes["jarvis.session"] = session
    joined = clock.wall - age_s
    return models.ParticipantInfo(identity=f"agent-{job_id}", kind=kind, state=state, attributes=attributes,
                                  joined_at=int(joined), joined_at_ms=int(joined * 1000))


def job(job_id: str, clock: Clock, *, status: int = RUNNING, age_s: float = 5.0,
        worker: str = WORKER_ID) -> agent.Job:
    started = clock.wall - age_s
    return agent.Job(id=job_id, room=models.Room(name=ROOM), state=agent.JobState(
        status=status, started_at=_ns(started), updated_at=_ns(started), worker_id=worker,
        participant_identity=f"agent-{job_id}"))


def dispatch(dispatch_id: str, name: str, created_s: float, *jobs: agent.Job) -> agent_dispatch.AgentDispatch:
    return agent_dispatch.AgentDispatch(id=dispatch_id, agent_name=name, room=ROOM, state=agent_dispatch.AgentDispatchState(
        jobs=list(jobs), created_at=_ns(created_s)))


class FakeLiveKit:
    """LiveKit's room and dispatch services as the room owner sees them. fail[name] makes that call raise;
    on_dispatch(req, created) runs after create_dispatch (to add a job or an agent)."""

    def __init__(self, clock: Clock, *, exists: bool = True) -> None:
        self.clock = clock
        self.room_info: models.Room | None = models.Room(name=ROOM, sid="RM_c23") if exists else None
        self.participants: list[models.ParticipantInfo] = []
        self.dispatches: list[agent_dispatch.AgentDispatch] = []
        self.calls: list[str] = []
        self.requests: dict[str, list[Any]] = {}
        self.fail: dict[str, Exception] = {}
        self.on_dispatch: Any = None
        self.room = self
        self.agent_dispatch = self
        self._ids = 0

    def _record(self, name: str, request: Any = None) -> None:
        self.calls.append(name)
        self.requests.setdefault(name, []).append(request)
        if name in self.fail:
            raise self.fail[name]

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def dispatched_names(self) -> list[str]:
        return [req.agent_name for req in self.requests.get("create_dispatch", [])]

    async def list_rooms(self, req: Any) -> Any:
        self._record("list_rooms", req)
        assert list(req.names) == [ROOM]
        return lk_room.ListRoomsResponse(rooms=[self.room_info] if self.room_info is not None else [])

    async def create_room(self, req: Any) -> Any:
        self._record("create_room", req)
        if self.room_info is None:  # v1.13.7: a room created without agents gets an implicit '' dispatch
            self.room_info = models.Room(name=req.name, sid="RM_c23new")
            if not list(req.agents):
                self.dispatches.append(dispatch("AD_implicit", "", self.clock.wall))
        return self.room_info

    async def list_participants(self, req: Any) -> Any:
        self._record("list_participants", req)
        assert req.room == ROOM
        return lk_room.ListParticipantsResponse(participants=list(self.participants))

    async def remove_participant(self, req: Any) -> Any:
        self._record("remove_participant", req)
        assert req.room == ROOM
        self.participants = [p for p in self.participants if p.identity != req.identity]
        return lk_room.RemoveParticipantResponse()

    async def delete_room(self, *_args: Any) -> Any:
        raise AssertionError("the room owner never deletes a room (only reset-room does)")

    async def list_dispatch(self, room_name: str) -> list[Any]:
        self._record("list_dispatch", room_name)
        assert room_name == ROOM
        return list(self.dispatches)

    async def create_dispatch(self, req: Any) -> Any:
        self._record("create_dispatch", req)
        assert req.room == ROOM and json.loads(req.metadata)["by"] == "bridge"
        self._ids += 1
        created = dispatch(f"AD_c23_{self._ids}", req.agent_name, self.clock.wall)
        self.dispatches.append(created)
        if self.on_dispatch is not None:
            self.on_dispatch(req, created)
        return created

    async def delete_dispatch(self, *_args: Any) -> Any:
        raise AssertionError("the room owner never deletes a dispatch (it terminates live jobs)")

    async def aclose(self) -> None:
        self.calls.append("aclose")


@pytest.fixture
async def env(tmp_path):
    """make(brain=..., env_mode=...) builds a RoomManager on FakeLiveKit, a temporary store and fake clocks; the
    callables record what it sends."""
    clock = Clock()
    fake = FakeLiveKit(clock)
    store = BridgeStore(tmp_path / "bridge.db")
    state = SimpleNamespace(clock=clock, fake=fake, store=store, emitted=[], commands=[], sockets={},
                            ui=(0, None), status_calls=0, active_jobs=[], path=tmp_path / "bridge.db")

    async def emit_ui(event: str, data: Any) -> None:
        state.emitted.append((event, data))

    async def send_agent_command(data: Any, sid: Any = None) -> None:
        state.commands.append((data, sid))

    def on_status() -> None:
        state.status_calls += 1

    async def set_active_job(job_id: Any) -> None:
        state.active_jobs.append(job_id)

    def make(*, brain: str = "pc", env_mode: str | None = None, store_fn: Any = None,
             **settings: Any) -> bridge_rooms.RoomManager:
        room_settings = bridge_rooms.RoomSettings(api_url="http://livekit.c23.invalid:7880", key=LK_KEY,
                                                  secret=LK_SECRET, room=ROOM, brain=brain, env_mode=env_mode,
                                                  role_ok=True, **settings)
        return bridge_rooms.RoomManager(
            room_settings, store=store_fn or (lambda: store), emit_ui=emit_ui, send_agent_command=send_agent_command,
            agent_job_sockets=lambda: state.sockets, ui_presence=lambda: state.ui, on_status=on_status,
            desktop=lambda: SimpleNamespace(set_active_job=set_active_job), api_factory=lambda: state.fake,
            mono=lambda: clock.mono, wall=lambda: clock.wall)

    state.make = make
    yield state
    await store.close()


async def _confirmed(manager: bridge_rooms.RoomManager, clock: Clock, reason: str = "loop") -> Any:
    """'no agent' seen twice, 8 s apart: the first pass only confirms."""
    first = await manager.ensure(reason)
    assert first.result == bridge_rooms.RESULT_CONFIRMING
    clock.advance(bridge_rooms.CONFIRM_S)
    return await manager.ensure(reason)


async def _cli(argv: list[str]) -> int:
    """The admin CLI as 'docker exec ... python -m src.core.bridge_rooms' runs it: its own asyncio.run, so here in a
    worker thread."""
    return await asyncio.to_thread(bridge_rooms.main, argv)


async def _audit(store: BridgeStore, action: str) -> list[dict[str, Any]]:
    return [json.loads(row["detail"]) for row in await store.recent_audit(action_prefix=action)]


# ---------------------------------------------------------------- the room


async def test_a_missing_room_is_created_without_an_agents_list(env):
    # the PC worker reported JARVIS_PC_AGENT_NAME (C-23 phase 3)
    await env.store.kv_set(KV_PC_AGENT, {"name": "jarvis-pc", "worker_id": WORKER_ID, "source": "report", "ts": T0})
    env.fake.room_info = None
    manager = env.make()
    result = await manager.ensure("loop")

    [req] = env.fake.requests["create_room"]
    assert (req.name, req.empty_timeout, req.departure_timeout, req.max_participants) == (ROOM, 3600, 3600, 10)
    assert not list(req.agents)
    assert result.created_room and result.room_sid == "RM_c23new"
    # LiveKit's implicit '' dispatch cannot reach a worker named jarvis-pc, so it is dispatched in the same pass
    assert (result.result, result.created_dispatch, env.fake.dispatched_names()) == ("dispatched", True, ["jarvis-pc"])
    assert await _audit(env.store, "room_created") == [{"reason": "loop"}]
    assert [row["name"] for row in await _audit(env.store, "agent_dispatched")] == ["jarvis-pc"]


async def test_with_nothing_reported_or_seen_the_pc_target_is_the_empty_name(env):
    """LS-4: the PC worker registers '' until C-23 phase 3, so '' is the default target (never a name nobody
    serves); a new room's implicit '' dispatch is then the dispatch."""
    env.fake.room_info = None
    manager = env.make()
    assert manager.target_agent_name == ""
    result = await manager.ensure("loop")
    assert (result.created_room, result.result, env.fake.count("create_dispatch")) == (True, "in_flight", 0)


async def test_a_new_room_while_the_worker_registers_the_empty_name_waits_for_the_implicit_dispatch(env):
    await env.store.kv_set(KV_PC_AGENT, {"name": "", "worker_id": WORKER_ID, "source": "report", "ts": T0})
    env.fake.room_info = None
    manager = env.make()
    result = await manager.ensure("token")
    assert result.created_room and result.result == "in_flight"  # the implicit '' dispatch is the dispatch
    assert env.fake.count("create_dispatch") == 0
    marker = await env.store.kv_get(KV_ROOM_DISPATCH)
    assert (marker["id"], marker["name"]) == ("implicit", "")


async def test_one_dispatch_when_no_agent_and_none_while_the_marker_is_fresh(env):
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "dispatched"
    for _ in range(5):  # the next 29 s: the marker (and the new dispatch) are in flight
        env.clock.advance(5)
        assert (await manager.ensure("loop")).result == "in_flight"
    assert env.fake.count("create_dispatch") == 1
    marker = await env.store.kv_get(KV_ROOM_DISPATCH)
    assert marker["id"] == "AD_c23_1" and marker["reason"] == "loop"


async def test_another_partys_fresh_dispatch_blocks(env):
    env.fake.dispatches = [dispatch("AD_ensure_room", "", env.clock.wall - 10)]  # ensure_room.py's, 10 s old
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "in_flight"
    assert env.fake.count("create_dispatch") == 0


async def test_a_fresh_job_blocks_and_an_old_running_job_does_not(env):
    env.fake.dispatches = [dispatch("AD_old", "", env.clock.wall - 600, job("AJ_fresh", env.clock, age_s=60))]
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "in_flight"
    # v1.13.7 never ends a job: 90 s after it started a 'running' job counts as ended
    env.fake.dispatches = [dispatch("AD_old", "", env.clock.wall - 600, job("AJ_old", env.clock, age_s=200))]
    assert (await manager.ensure("loop")).result == "dispatched"


async def test_a_pending_job_blocks_too(env):
    env.fake.dispatches = [dispatch("AD_p", "jarvis-pc", env.clock.wall - 600,
                                    job("AJ_p", env.clock, status=PENDING, age_s=20))]
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "in_flight"


async def test_a_ghost_blocks_until_it_is_disconnected(env):
    ghost = agent_participant("AJ_ghost", env.clock, age_s=6000)  # a stopped worker's agent, still listed
    env.fake.participants = [person(), ghost]
    manager = env.make()
    for _ in range(4):
        assert (await manager.ensure("loop")).result == "present"
        env.clock.advance(20)
    assert env.fake.count("create_dispatch") == 0 and env.fake.count("remove_participant") == 0
    env.fake.participants = [person(), agent_participant("AJ_ghost", env.clock, state=DISCONNECTED)]
    assert (await _confirmed(manager, env.clock)).result == "dispatched"


def test_the_agent_kind_is_compared_as_a_number():
    """The old token route compared str() of the enum number with 'AGENT', which never matched."""
    custom = models.ParticipantInfo(identity="custom-agent", kind=AGENT_KIND)
    assert str(custom.kind) == "4" and not str(custom.kind).endswith("AGENT")
    assert bridge_rooms.is_agent_participant(custom)
    assert bridge_rooms.is_agent_participant(models.ParticipantInfo(identity="agent-AJ_1", kind=STANDARD))
    assert not bridge_rooms.is_agent_participant(person())
    assert bridge_rooms.is_person(person()) and not bridge_rooms.is_person(person("probe-1"))
    assert not bridge_rooms.is_person(person(state=DISCONNECTED))


async def test_an_int_kind_agent_counts_as_present(env):
    env.fake.participants = [models.ParticipantInfo(identity="custom-agent", kind=AGENT_KIND, state=ACTIVE)]
    manager = env.make()
    result = await manager.ensure("loop")
    assert (result.result, result.agent_in_room, result.voice_ready) == ("present", True, True)


# ---------------------------------------------------------------- confirmation, backoff, explicit triggers


async def test_the_eight_second_confirmation_and_start_skips_it(env):
    manager = env.make()
    assert (await manager.ensure("loop")).result == "confirming"
    env.clock.advance(bridge_rooms.CONFIRM_S - 1)
    assert (await manager.ensure("loop")).result == "confirming"
    other = env.make()  # a fresh owner: start (Wake) needs no confirmation
    result = await other.ensure("start", explicit=True, skip_confirm=True)
    assert result.result == "dispatched" and env.fake.count("create_dispatch") == 1


async def test_backoff_steps_grow_to_five_minutes(env):
    """Every dispatch that brings no agent: 15, 30, 60, 120, 240 s, then every 300 s (the 30 s in-flight rule
    covers the first step)."""
    manager = env.make()
    await _confirmed(manager, env.clock)
    dispatched_at = [env.clock.mono]
    while len(dispatched_at) < 8:
        env.clock.advance(5)
        result = await manager.ensure("loop")
        if result.result == "dispatched":
            dispatched_at.append(env.clock.mono)
        elif result.result == "backoff":
            assert result.retry_in_s is not None and result.retry_in_s > 0
    gaps = [round(b - a) for a, b in zip(dispatched_at, dispatched_at[1:])]
    assert gaps == [30, 30, 60, 120, 240, 300, 300]


async def test_a_voice_ready_agent_resets_the_backoff(env):
    manager = env.make()
    await _confirmed(manager, env.clock)                           # dispatch 1
    for _ in range(3):                                             # dispatches 2-4: the backoff grows to 120 s
        env.clock.advance(61)
        assert (await manager.ensure("loop")).result == "dispatched"
    assert manager._failures == 4
    env.fake.participants = [agent_participant("AJ_4", env.clock, name="jarvis-pc", session="running")]
    env.clock.advance(1)
    assert (await manager.ensure("loop")).voice_ready
    assert manager._failures == 0
    env.fake.participants = []  # the agent leaves 31 s later: its dispatch is no longer in flight
    env.clock.advance(31)
    assert (await manager.ensure("loop")).result == "confirming"
    env.clock.advance(bridge_rooms.CONFIRM_S)
    # 40 s after dispatch 4, inside its 120 s backoff: only the reset lets this through
    assert (await manager.ensure("loop")).result == "dispatched"


async def test_an_explicit_trigger_skips_the_backoff_but_never_the_in_flight_rule(env):
    manager = env.make()
    await _confirmed(manager, env.clock)                         # dispatch 1
    env.clock.advance(5)
    assert (await manager.ensure("ensure", explicit=True)).result == "in_flight"
    env.clock.advance(26)
    assert (await manager.ensure("loop")).result == "dispatched"  # dispatch 2: backoff 30 s from here
    env.clock.advance(31)
    assert (await manager.ensure("loop")).result == "dispatched"  # dispatch 3: backoff 60 s
    env.clock.advance(31)
    blocked = await manager.ensure("loop")
    assert blocked.result == "backoff" and blocked.retry_in_s == pytest.approx(29, abs=0.5)
    assert (await manager.ensure("ensure", explicit=True)).result == "dispatched"  # skipped once
    env.clock.advance(5)
    assert (await manager.ensure("ensure", explicit=True)).result == "in_flight"   # never in flight


# ---------------------------------------------------------------- the dispatch target


async def test_pc_target_follows_the_report(env):
    manager = env.make()
    result = await manager.ensure("agent_ctl_start", explicit=True,
                                  report=bridge_rooms.Report(agent_name="", worker_id=WORKER_ID))
    assert result.target_agent_name == ""
    stored = await env.store.kv_get(KV_PC_AGENT)
    assert (stored["name"], stored["worker_id"], stored["source"]) == ("", WORKER_ID, "report")
    result = await manager.ensure("agent_ctl_restart", explicit=True,
                                  report=bridge_rooms.Report(agent_name="jarvis-pc", worker_id=WORKER_ID))
    assert result.target_agent_name == "jarvis-pc"
    await manager.ensure("x", explicit=True, report=bridge_rooms.Report(agent_name="jarvis"))  # not accepted
    assert manager.target_agent_name == "jarvis-pc"


def test_an_unknown_agent_name_is_a_bad_body():
    settings = bridge_rooms.RoomSettings(role_ok=True)
    with pytest.raises(bridge_rooms.EnsureBodyError) as info:
        bridge_rooms.parse_ensure_body(b'{"agent_name":"jarvis"}', settings, internal=False)
    assert info.value.error == "bad_agent_name"
    for body in (b"[]", b'{"reason":"Bad Reason"}', b'{"worker_id":"x y"}', b"{", b'{"want_voice":"yes"}'):
        with pytest.raises(bridge_rooms.EnsureBodyError) as info:
            bridge_rooms.parse_ensure_body(body, settings, internal=True)
        assert info.value.error == "bad_body"
    reason, report, want_voice = bridge_rooms.parse_ensure_body(
        b'{"reason":"wait","agent_name":"jarvis-pc","worker_id":"AW_1","want_voice":true}', settings, internal=False)
    assert (reason, report.agent_name, report.worker_id, want_voice) == ("wait", "jarvis-pc", "AW_1", False)
    assert bridge_rooms.parse_ensure_body(b"", settings, internal=True)[0] == "ensure"


async def test_pc_target_follows_the_observed_agent_unless_a_fresh_report_says_otherwise(env):
    manager = env.make()
    assert manager.target_agent_name == ""  # nothing known: '' (the PC worker's name until phase 3)
    env.fake.participants = [agent_participant("AJ_1", env.clock, name="jarvis-pc")]
    await manager.ensure("loop")
    assert manager.target_agent_name == "jarvis-pc"
    assert (await env.store.kv_get(KV_PC_AGENT))["source"] == "observed"
    env.fake.participants = [agent_participant("AJ_1", env.clock)]  # registered '' (no lk.agent.name)
    await manager.ensure("loop")
    assert manager.target_agent_name == ""
    await manager.ensure("x", explicit=True, report=bridge_rooms.Report(agent_name="jarvis-pc"))
    await manager.ensure("loop")
    assert manager.target_agent_name == "jarvis-pc"  # the report wins for 10 min
    env.clock.advance(bridge_rooms.REPORT_FRESH_S + 1)
    await manager.ensure("loop")
    assert manager.target_agent_name == ""


async def test_a_name_that_gets_no_job_alternates_with_the_other_accepted_name(env):
    """The phase-3 switch can never leave JARVIS silent: a dispatch to '' (the name last seen, an hour ago) that got
    no job within 30 s (no worker under that name) is followed, after the backoff, by one to jarvis-pc, and back."""
    await env.store.kv_set(KV_PC_AGENT, {"name": "", "worker_id": "", "source": "observed", "ts": T0 - 3600})
    manager = env.make()
    await _confirmed(manager, env.clock)
    env.clock.advance(31)
    await manager.ensure("loop")
    env.clock.advance(31)
    await manager.ensure("loop")
    assert env.fake.dispatched_names() == ["", "jarvis-pc", ""]


async def test_a_name_the_pc_worker_reported_or_that_was_seen_since_is_never_switched_away_from(env):
    """LS-4: a dispatch without a job right after a restart is LiveKit's (its worker cache), not a wrong name: a
    fresh report of the name, or an agent of that name present after that dispatch, keeps the target."""
    manager = env.make()
    await manager.ensure("agent_ctl_restart", explicit=True, report=bridge_rooms.Report(agent_name="", worker_id=WORKER_ID))
    await _confirmed(manager, env.clock)
    for _ in range(2):
        env.clock.advance(31)
        await manager.ensure("loop")
    assert env.fake.dispatched_names() == ["", "", ""]  # the fresh report pins ''
    env.clock.advance(bridge_rooms.REPORT_FRESH_S)       # the report is old now: only an observation keeps ''
    env.fake.participants = [agent_participant("AJ_seen", env.clock)]  # an agent of '' (from another dispatch)
    await manager.ensure("loop")
    env.fake.participants = []
    env.clock.advance(bridge_rooms.CONFIRM_S + 31)
    await manager.ensure("loop")
    env.clock.advance(bridge_rooms.CONFIRM_S)
    await manager.ensure("loop")
    assert env.fake.dispatched_names()[-1] == "" and "jarvis-pc" not in env.fake.dispatched_names()


async def test_a_name_whose_dispatch_got_a_job_is_kept(env):
    manager = env.make()
    await manager.ensure("x", explicit=True, report=bridge_rooms.Report(agent_name=""))

    def assign(req: Any, created: Any) -> None:  # the worker takes the job; its agent never joins
        created.state.jobs.append(job("AJ_taken", env.clock, worker=WORKER_ID))

    env.fake.on_dispatch = assign
    await _confirmed(manager, env.clock)
    env.clock.advance(100)  # the job is no longer in flight, and it had a job: the name is right
    await manager.ensure("loop")
    assert env.fake.dispatched_names() == ["", ""]


async def test_vps_mode_targets_jarvis_and_ignores_pc_reports(env):
    env.fake.room_info = None
    env.ui = (1, None)
    manager = env.make(brain="vps")
    assert manager.mode == "on_demand" and manager.accepted == frozenset({"jarvis"})
    result = await manager.ensure("ensure", explicit=True, report=bridge_rooms.Report(agent_name="", worker_id="AW_x"))
    assert result.created_room and env.fake.dispatched_names() == ["jarvis"]  # a new room: dispatched at once
    assert not list(env.fake.requests["create_room"][0].agents)
    assert await env.store.kv_get(KV_PC_AGENT) is None
    assert result.agent_current_worker is None and result.target_agent_name == "jarvis"


async def test_vps_on_demand_is_idle_without_demand(env):
    manager = env.make(brain="vps")
    assert (await manager.ensure("loop")).result == "idle"
    env.clock.advance(60)
    assert (await manager.ensure("loop")).result == "idle"
    env.ui = (0, env.clock.wall - 300)  # a UI left five minutes ago: still demand ('no agent' was seen twice)
    assert (await manager.ensure("loop")).result == "dispatched"
    env.clock.advance(31)
    env.ui = (0, env.clock.wall - bridge_rooms.DEMAND_WINDOW_S - 1)  # past the window: idle again
    assert (await manager.ensure("loop")).result == "idle"


async def test_vps_on_demand_dispatches_for_want_voice_and_a_person(env):
    manager = env.make(brain="vps")
    await manager.ensure("ensure", explicit=True, want_voice=True)
    env.clock.advance(bridge_rooms.CONFIRM_S)
    assert (await manager.ensure("ensure", explicit=True)).result == "dispatched"
    assert manager._want_voice_at is not None
    env.clock.advance(bridge_rooms.DEMAND_WINDOW_S + 31)  # want_voice is 10 min old: no demand
    assert (await manager.ensure("loop")).result == "idle"
    env.fake.participants = [person()]                      # a person in the room is demand
    assert (await manager.ensure("loop")).result == "dispatched"


async def test_vps_mode_announces_the_kept_agents_job_to_the_node(env):
    env.fake.participants = [agent_participant("AJ_vps", env.clock, name="jarvis", session="running")]
    manager = env.make(brain="vps", env_mode="always")
    await manager.ensure("loop")
    await manager.ensure("loop")
    env.fake.participants = []
    await manager.ensure("loop")
    assert env.active_jobs == ["AJ_vps", None]  # on change only; forgotten (never loosened) without an agent


# ---------------------------------------------------------------- pause


async def test_pause_dispatches_nothing_sends_pause_agent_and_removes_an_agent_that_stays(env):
    agent_p = agent_participant("AJ_old_build", env.clock)  # an old build ignores pause_agent
    env.fake.participants = [agent_p]
    manager = env.make()
    await manager.set_paused(True, by="ui")
    assert env.commands == [(dict(bridge_rooms.PAUSE_COMMAND), None)]
    assert await env.store.kv_get(KV_PAUSED) is True
    assert await _audit(env.store, "agent_paused") == [{"by": "ui"}]
    assert (await manager.ensure("loop")).result == "paused"
    env.clock.advance(bridge_rooms.PAUSE_REMOVE_S - 1)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0
    env.clock.advance(1)
    await manager.ensure("loop")
    assert [req.identity for req in env.fake.requests["remove_participant"]] == ["agent-AJ_old_build"]
    assert (await _audit(env.store, "agent_removed"))[0] == {"why": "paused", "name": "", "job_id": "AJ_old_build"}
    env.clock.advance(60)
    assert (await manager.ensure("loop")).result == "paused" and env.fake.count("create_dispatch") == 0


async def test_unpause_dispatches_at_once(env):
    manager = env.make()
    await manager.set_paused(True, by="ui")
    await manager.ensure("loop")
    await manager.set_paused(False, by="ui")
    kick = manager._take_kick()
    assert kick == ("start", True, True)  # explicit, without the 8 s confirmation
    result = await manager.ensure(kick[0], explicit=kick[1], skip_confirm=kick[2])
    assert result.result == "dispatched" and not result.paused
    assert await _audit(env.store, "agent_unpaused") == [{"by": "ui"}]


# ---------------------------------------------------------------- removals


async def test_a_closed_session_is_removed_after_sixty_seconds(env):
    env.fake.participants = [agent_participant("AJ_closed", env.clock, session="closed")]
    manager = env.make()
    await manager.ensure("loop")
    env.clock.advance(59)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0
    env.clock.advance(1)
    await manager.ensure("loop")
    assert (await _audit(env.store, "agent_removed"))[0]["why"] == "session_closed"


async def test_a_wrong_name_is_removed_only_after_the_target_was_voice_ready_ten_seconds(env):
    env.fake.participants = [agent_participant("AJ_vps_brain", env.clock, name="jarvis", session="running"),
                             agent_participant("AJ_pc", env.clock, name="jarvis-pc", session="starting")]
    manager = env.make()
    for _ in range(3):
        await manager.ensure("loop")
        env.clock.advance(10)
    assert env.fake.count("remove_participant") == 0  # the accepted agent is not voice-ready yet
    env.fake.participants[1] = agent_participant("AJ_pc", env.clock, name="jarvis-pc", session="running")
    await manager.ensure("loop")
    env.clock.advance(9)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0
    env.clock.advance(1)
    await manager.ensure("loop")
    assert [req.identity for req in env.fake.requests["remove_participant"]] == ["agent-AJ_vps_brain"]
    assert (await _audit(env.store, "agent_removed"))[0] == {"why": "wrong_name", "name": "jarvis",
                                                             "job_id": "AJ_vps_brain"}


async def test_a_duplicate_without_a_bridge_link_is_removed_after_sixty_seconds(env):
    env.fake.participants = [agent_participant("AJ_live", env.clock, age_s=30),
                             agent_participant("AJ_dup", env.clock, age_s=20)]
    env.sockets = {"AJ_live": 1.0}  # only this job's agent has its Socket.IO link
    manager = env.make()
    await manager.ensure("loop")
    env.clock.advance(59)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0
    env.clock.advance(1)
    await manager.ensure("loop")
    assert [req.identity for req in env.fake.requests["remove_participant"]] == ["agent-AJ_dup"]
    [(event, alert)] = [item for item in env.emitted if item[0] == "system_alert"]
    assert (alert["severity"], alert["code"]) == ("warning", "DUPLICATE_AGENT")


async def test_a_ghost_and_a_new_agent_inside_sixty_seconds_remove_nothing(env):
    env.fake.participants = [agent_participant("AJ_ghost", env.clock, age_s=900),
                             agent_participant("AJ_new", env.clock, age_s=2)]
    env.sockets = {"AJ_new": 1.0}
    manager = env.make()
    for _ in range(5):
        await manager.ensure("loop")
        env.clock.advance(10)
    env.fake.participants = [env.fake.participants[1]]  # LiveKit drops the ghost after 50 s
    await manager.ensure("loop")
    env.clock.advance(30)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0 and env.fake.count("create_dispatch") == 0


async def test_duplicates_without_any_bridge_link_remove_nothing(env, caplog, monkeypatch):
    # the bridge's logger may not propagate (setup_logger, once ui_bridge was imported): caplog listens on the root
    monkeypatch.setattr(logging.getLogger("UI-BRIDGE"), "propagate", True)
    env.fake.participants = [agent_participant("AJ_a", env.clock), agent_participant("AJ_b", env.clock)]
    manager = env.make()
    await manager.ensure("loop")
    env.clock.advance(61)
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0
    assert any("none has an agent socket" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------- observe mode


async def test_observe_never_dispatches_or_removes_and_creates_a_room_only_for_a_token(env):
    env.fake.room_info = None
    manager = env.make(env_mode="observe")
    assert (await manager.ensure("loop")).result == "observe"
    assert env.fake.count("create_room") == 0
    result = await manager.ensure("token")
    # the token's heal: the new room's implicit '' dispatch reaches the PC worker, nothing is added beside it
    assert result.created_room and result.result == "in_flight" and env.fake.count("create_dispatch") == 0
    env.fake.participants = [agent_participant("AJ_c", env.clock, session="closed"),
                             agent_participant("AJ_w", env.clock, name="jarvis", session="running")]
    for _ in range(4):
        env.clock.advance(60)
        assert (await manager.ensure("loop")).result == "observe"
    await manager.set_paused(True, by="admin_cli")  # the flag may change, the room never does
    env.clock.advance(60)
    await manager.ensure("loop")
    assert env.fake.count("create_dispatch") == 0 and env.fake.count("remove_participant") == 0
    assert env.commands == []  # observe tells no agent about a pause (R3)


# ---------------------------------------------------------------- errors


async def test_an_unreadable_room_keeps_the_last_presence_and_says_livekit_is_not_ok(env):
    env.fake.participants = [agent_participant("AJ_1", env.clock, session="running")]
    manager = env.make()
    await manager.ensure("loop")
    env.fake.fail["list_rooms"] = RuntimeError(f"503 for key {LK_KEY}")
    env.clock.advance(5)
    with pytest.raises(bridge_rooms.LiveKitUnavailable):
        await manager.ensure("loop")
    snapshot = manager.snapshot()
    assert snapshot["ok"] is False and snapshot["agent_in_room"] is True
    assert manager.presence()["voice_ready"] is True
    assert LK_KEY not in json.dumps(snapshot)


async def test_a_failed_create_dispatch_keeps_the_marker(env):
    env.fake.fail["create_dispatch"] = RuntimeError("timeout")
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "dispatch_error"
    marker = await env.store.kv_get(KV_ROOM_DISPATCH)
    assert (marker["id"], marker["name"]) == ("pending", "")  # it may have happened
    env.fake.fail.clear()
    env.clock.advance(10)
    assert (await manager.ensure("loop")).result == "in_flight"


# ---------------------------------------------------------------- kv, the CLI


async def test_the_persisted_state_survives_a_restart_and_the_cli_reaches_the_running_owner(env, monkeypatch, capsys):
    manager = env.make()
    await manager.set_paused(True, by="ui")
    await manager.ensure("x", explicit=True, report=bridge_rooms.Report(agent_name="", worker_id=WORKER_ID))
    restarted = env.make()
    await restarted.ensure("loop")
    assert restarted.paused and restarted.target_agent_name == ""

    monkeypatch.setenv("LIVEKIT_URL", "")  # the CLI's status makes no live read here
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    assert await _cli(["--db", str(env.path), "mode", "observe"]) == 0
    assert await _cli(["--db", str(env.path), "unpause"]) == 0
    capsys.readouterr()
    env.commands.clear()
    await restarted._poll_kv()  # the running owner reads the kv every 5 s
    assert restarted.mode == "observe" and restarted.mode_source == "kv" and not restarted.paused
    assert restarted._take_kick() is not None
    assert await _cli(["--db", str(env.path), "pause"]) == 0
    await restarted._poll_kv()
    # R3: observe changes nothing: the pause is kept and reported, but no agent is told
    assert restarted.paused and env.commands == []
    assert await _cli(["--db", str(env.path), "mode", "clear"]) == 0
    await restarted._poll_kv()
    assert restarted.mode == "always" and restarted.mode_source == "default"
    # leaving observe while paused: the pause takes effect now
    assert env.commands == [(dict(bridge_rooms.PAUSE_COMMAND), None)]
    rows = await _audit(env.store, "room_mode")
    assert {row["mode"] for row in rows} == {"observe", "clear"}


async def test_the_cli_status_names_no_identity_key_or_worker_id(env, monkeypatch, capsys):
    await env.store.kv_set(KV_PC_AGENT, {"name": "jarvis-pc", "worker_id": WORKER_ID, "source": "report", "ts": T0})
    await env.store.kv_set(KV_ROOM_DISPATCH, {"id": "AD_1", "name": "jarvis-pc", "reason": "loop", "ts": T0})
    await env.store.kv_set(KV_ROOM_MODE, "on_demand")

    class LiveRead:  # the live read with keys: the room's agents by name, session and state only
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.room = self

        async def list_rooms(self, _req: Any) -> Any:
            return lk_room.ListRoomsResponse(rooms=[models.Room(name=ROOM, sid="RM_cli")])

        async def list_participants(self, _req: Any) -> Any:
            return lk_room.ListParticipantsResponse(participants=[
                person(), agent_participant("AJ_cli", Clock(), name="jarvis-pc", session="running")])

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(lk_api, "LiveKitAPI", LiveRead)
    for name, value in (("LIVEKIT_URL", "ws://livekit.c23.invalid:7880"), ("LIVEKIT_API_KEY", LK_KEY),
                        ("LIVEKIT_API_SECRET", LK_SECRET), ("JARVIS_ROOM_NAME", ROOM)):
        monkeypatch.setenv(name, value)
    assert await _cli(["--db", str(env.path), "status"]) == 0
    out = capsys.readouterr().out
    assert "mode: on_demand (kv)" in out and "paused: no" in out
    assert "pc_agent: jarvis-pc (source report, worker id known yes" in out
    assert "last dispatch: jarvis-pc (loop, AD_1" in out
    assert "live: room exists, sid RM_cli, people 1, agents 1" in out
    assert "agent jarvis-pc session running state active" in out
    for secret in (WORKER_ID, LK_KEY, LK_SECRET, OWNER, "agent-AJ_cli"):
        assert secret not in out


async def test_reset_room_needs_the_rooms_name_and_writes_its_audit_row(env, monkeypatch, capsys):
    deleted: list[str] = []

    class Deleter:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.room = self

        async def delete_room(self, req: Any) -> Any:
            deleted.append(req.room)
            return lk_room.DeleteRoomResponse()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(lk_api, "LiveKitAPI", Deleter)
    await env.store.open()  # the bridge's database exists
    for name, value in (("LIVEKIT_URL", "ws://livekit.c23.invalid:7880"), ("LIVEKIT_API_KEY", LK_KEY),
                        ("LIVEKIT_API_SECRET", LK_SECRET), ("JARVIS_ROOM_NAME", ROOM)):
        monkeypatch.setenv(name, value)
    assert await _cli(["--db", str(env.path), "reset-room", "--confirm", "jarvis-hybrid"]) == 2
    assert not deleted and "--confirm must be the room's name" in capsys.readouterr().err
    assert await _cli(["--db", str(env.path), "reset-room", "--confirm", ROOM]) == 0
    assert deleted == [ROOM] and await _audit(env.store, "room_reset") == [{"room": ROOM}]
    assert "every session in it was cut" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        await _cli(["--db", str(env.path), "reset-room"])  # --confirm is required


# ---------------------------------------------------------------- the loop and webhooks


async def test_the_loop_runs_at_start_wakes_on_a_kick_and_survives_errors(env, monkeypatch):
    for name in ("KV_POLL_S", "LOOP_FAST_S", "LOOP_SLOW_S"):
        monkeypatch.setattr(bridge_rooms, name, 30.0)  # nothing runs by the clock in this test
    env.fake.participants = [agent_participant("AJ_1", env.clock, session="running")]
    manager = env.make()
    task = asyncio.create_task(manager.run())
    try:
        for _ in range(200):
            if env.fake.count("list_rooms") >= 1:
                break
            await asyncio.sleep(0.01)
        assert env.fake.count("list_rooms") == 1  # the startup ensure
        env.fake.fail["list_rooms"] = RuntimeError("LiveKit down")
        manager.kick("agent_connect")
        for _ in range(200):
            if env.fake.count("list_rooms") >= 2:
                break
            await asyncio.sleep(0.01)
        assert env.fake.count("list_rooms") == 2
        env.fake.fail.clear()
        manager.kick("agent_hello")
        for _ in range(200):
            if env.fake.count("list_rooms") >= 3:
                break
            await asyncio.sleep(0.01)
        assert env.fake.count("list_rooms") == 3 and not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_loop_runs_every_5_s_while_someone_waits_for_an_agent_else_every_20_s(env, monkeypatch):
    """Scaled down: LOOP_FAST_S 0.05 s, LOOP_SLOW_S 30 s, the kv poll every 0.05 s (real clocks in this test)."""
    monkeypatch.setattr(bridge_rooms, "KV_POLL_S", 0.05)
    monkeypatch.setattr(bridge_rooms, "LOOP_FAST_S", 0.05)
    monkeypatch.setattr(bridge_rooms, "LOOP_SLOW_S", 30.0)
    settings = bridge_rooms.RoomSettings(api_url="http://livekit.c23.invalid:7880", key=LK_KEY, secret=LK_SECRET,
                                         room=ROOM, env_mode="observe", role_ok=True)
    manager = bridge_rooms.RoomManager(settings, store=lambda: env.store, api_factory=lambda: env.fake,
                                       ui_presence=lambda: env.ui)
    task = asyncio.create_task(manager.run())
    try:
        for _ in range(200):
            if env.fake.count("list_rooms"):
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.4)
        assert env.fake.count("list_rooms") == 1  # nobody there: the next pass is 30 s away
        env.ui = (1, None)  # a UI connects and no agent is voice-ready: every 0.05 s, judged on the next pass
        await asyncio.sleep(0.4)
        assert env.fake.count("list_rooms") >= 3
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _webhook(event: str, *, room: str = ROOM, identity: str | None = None, kind: int = STANDARD) -> bytes:
    message = webhook.WebhookEvent(event=event, room=models.Room(name=room, sid="RM_c23"), id="EV_1",
                                   created_at=int(T0))
    if identity is not None:
        message.participant.CopyFrom(models.ParticipantInfo(identity=identity, kind=kind, state=ACTIVE))
    return MessageToJson(message).encode("utf-8")


def _webhook_token(body: bytes, *, secret: str = LK_SECRET) -> str:
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return lk_api.AccessToken(LK_KEY, secret).with_sha256(digest).to_jwt()


async def test_a_verified_webhook_wakes_the_loop(env):
    manager = env.make(brain="vps")
    body = _webhook("participant_joined", identity=OWNER)
    assert await manager.handle_webhook(body, _webhook_token(body)) == 200
    assert manager._take_kick() == ("webhook_participant_joined", True, False)  # a person joining, on_demand
    body = _webhook("participant_left", identity="agent-AJ_1", kind=AGENT_KIND)
    assert await manager.handle_webhook(body, "Bearer " + _webhook_token(body)) == 200
    assert manager._take_kick() == ("webhook_participant_left", False, False)
    other = _webhook("room_finished", room="jarvis-probe-1")
    assert await manager.handle_webhook(other, _webhook_token(other)) == 200
    assert manager._take_kick() is None  # another room: ignored


async def test_a_bad_or_oversized_webhook_is_refused(env):
    manager = env.make()
    body = _webhook("room_started")
    assert await manager.handle_webhook(body, _webhook_token(body, secret="w" * 40)) == 401
    assert await manager.handle_webhook(body, None) == 401
    assert await manager.handle_webhook(body + b" ", _webhook_token(body)) == 401  # sha256 over other bytes
    assert await manager.handle_webhook(b"x" * (bridge_rooms.WEBHOOK_MAX_BYTES + 1), _webhook_token(body)) == 413
    assert manager._take_kick() is None
    assert len(await _audit(env.store, "webhook_rejected")) == 1  # at most one row a minute


# ---------------------------------------------------------------- review fixes: when a job is over (R1, LS-5)


def _jobs_per_dispatch(env: Any, worker: str = WORKER_ID) -> None:
    """on_dispatch: the worker takes each dispatch's job at once (AJ_1, AJ_2 ...); its agent joins only when a test
    lists it."""
    taken = [0]

    def assign(_req: Any, created: Any) -> None:
        taken[0] += 1
        created.state.jobs.append(job(f"AJ_{taken[0]}", env.clock, age_s=0, worker=worker))

    env.fake.on_dispatch = assign


async def test_wake_soon_after_a_job_started_dispatches_at_once(env):
    """R1: a job whose agent the bridge saw in the room and that has left (paused_by_owner) is over, although
    v1.13.7 still lists it as running: Wake 35 s after that job started dispatches at once, not 90 s after it."""
    env.fake.participants = [person()]
    _jobs_per_dispatch(env)
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "dispatched"              # D1, job AJ_1
    env.clock.advance(5)
    env.fake.participants = [person(), agent_participant("AJ_1", env.clock, session="running", age_s=0)]
    assert (await manager.ensure("loop")).result == "present"                         # its agent is seen
    env.clock.advance(15)
    await manager.set_paused(True, by="ui")
    env.clock.advance(5)
    env.fake.participants = [person()]                                                # the job ended
    assert (await manager.ensure("loop")).result == "paused"
    env.clock.advance(10)
    await manager.set_paused(False, by="ui")                                          # Wake
    kick = manager._take_kick()
    result = await manager.ensure(kick[0], explicit=kick[1], skip_confirm=kick[2])
    assert result.result == "dispatched" and env.fake.dispatched_names() == ["", ""]


async def test_a_restart_soon_after_the_last_job_started_dispatches_once_the_ghost_drops(env):
    """R1: agent_ctl restart 30 s after the old job started: the stopped worker's agent stays listed (a ghost, seen
    by the bridge); once LiveKit drops it, its job is over and the new worker gets its dispatch after the 8 s
    confirmation, inside ensure_room.py's 90 s wait."""
    env.fake.dispatches = [dispatch("AD_old", "", env.clock.wall - 30,
                                    job("AJ_old", env.clock, age_s=30, worker=OLD_WORKER_ID))]
    env.fake.participants = [person(), agent_participant("AJ_old", env.clock, age_s=25)]
    manager = env.make()
    report = bridge_rooms.Report(agent_name="", worker_id=WORKER_ID)
    for _ in range(3):
        result = await manager.ensure("agent_ctl_restart", explicit=True, report=report, route=True)
        assert (result.result, result.agent_current_worker) == ("present", False)  # the ghost: never doubled
        env.clock.advance(3)
    env.fake.participants = [person()]  # the ghost dropped, 39 s after the old job started
    assert (await manager.ensure("wait", explicit=True, report=report, route=True)).result == "confirming"
    env.clock.advance(bridge_rooms.CONFIRM_S)
    result = await manager.ensure("wait", explicit=True, report=report, route=True)
    assert result.result == "dispatched" and env.fake.dispatched_names() == [""]


async def test_a_job_whose_end_its_agent_reported_is_not_in_flight(env):
    """R1/LS-5: notify job_end ends a job the bridge never saw an agent of."""
    env.fake.dispatches = [dispatch("AD_1", "", env.clock.wall - 40, job("AJ_never", env.clock, age_s=40))]
    manager = env.make()
    assert (await _confirmed(manager, env.clock)).result == "in_flight"  # 48 s old, its agent never appeared
    assert manager.note_job_end("AJ_never") and manager._take_kick() == ("job_end", False, False)
    assert not manager.note_job_end("not a job id") and not manager.note_job_end(None)
    assert (await manager.ensure("loop")).result == "dispatched"
    assert manager.snapshot()["last_job_state"] == "ended_or_stale"  # the status agrees: AJ_never is over


async def test_the_reported_workers_job_counts_as_in_flight_for_the_agents_connect_budget(env):
    """LS-5: the agent's own connect budget (C-33 step B: one connect of up to 60 s, 150 s with start-up): a job of
    the PC worker that reported itself last, whose agent never appeared, is in flight for that budget (no second job
    beside a slow one); another worker's job, or one that started before the last Wake, only for 90 s."""
    manager = env.make()
    report = bridge_rooms.Report(agent_name="", worker_id=WORKER_ID)
    await manager.ensure("agent_ctl_start", explicit=True, report=report)
    env.fake.dispatches = [dispatch("AD_1", "", env.clock.wall - 100, job("AJ_slow", env.clock, age_s=100))]
    env.clock.advance(bridge_rooms.CONFIRM_S)
    assert (await manager.ensure("loop")).result == "in_flight"                       # 108 s: still connecting
    env.clock.advance(bridge_rooms.JOB_CONNECT_BUDGET_S - 108 - 1)
    assert (await manager.ensure("loop")).result == "in_flight"
    env.fake.dispatches = [dispatch("AD_2", "", env.clock.wall - 100,
                                    job("AJ_dead", env.clock, age_s=100, worker=OLD_WORKER_ID))]
    assert (await manager.ensure("loop")).result == "dispatched"                      # another worker's: 90 s
    await manager.set_paused(True, by="ui")
    await manager.set_paused(False, by="ui")                                          # Wake
    manager._take_kick()
    env.clock.advance(bridge_rooms.DISPATCH_FRESH_S + 1)
    env.fake.dispatches = [dispatch("AD_3", "", env.clock.wall - 100, job("AJ_paused", env.clock, age_s=100))]
    assert (await manager.ensure("start", explicit=True, skip_confirm=True)).result == "dispatched"


# ---------------------------------------------------------------- review fixes: pause (R2, R3, R4, SEC-1, SEC-4)


class _GatedStore:
    """The real store; while gate is set up, kv_get_many reads at once but answers only when gate opens: a kv read
    in flight (the store runs in a worker thread)."""

    def __init__(self, store: BridgeStore) -> None:
        self._inner = store
        self.gate: asyncio.Event | None = None
        self.reading = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def kv_get_many(self, keys: Any) -> Any:
        values = await self._inner.kv_get_many(keys)
        if self.gate is not None:
            self.reading.set()
            await self.gate.wait()
        return values


async def _poll_with_a_change_during_the_read(manager: Any, gated: _GatedStore, change: Any) -> None:
    gated.gate, gated.reading = asyncio.Event(), asyncio.Event()
    poll = asyncio.create_task(manager._poll_kv())
    await asyncio.wait_for(gated.reading.wait(), 5)
    await change()  # the UI's pause or Wake lands while the read is in flight
    gated.gate.set()
    await asyncio.wait_for(poll, 5)
    gated.gate = None


async def test_a_ui_pause_during_the_polls_kv_read_is_never_undone(env):
    """R2/SEC-3: the 5 s kv poll read 'not paused', then the owner paused before its answer arrived: the older read
    never unpauses (no 'unpaused by admin_cli', no unpause ensure)."""
    gated = _GatedStore(env.store)
    manager = env.make(store_fn=lambda: gated)
    await manager._load_kv()
    await _poll_with_a_change_during_the_read(manager, gated, lambda: manager.set_paused(True, by="ui"))
    assert manager.paused and await env.store.kv_get(KV_PAUSED) is True
    assert manager._take_kick() is None and env.commands == [(dict(bridge_rooms.PAUSE_COMMAND), None)]
    await manager._poll_kv()  # the next read agrees
    assert manager.paused and await _audit(env.store, "agent_unpaused") == []


async def test_a_wake_during_the_polls_kv_read_is_never_undone(env):
    gated = _GatedStore(env.store)
    manager = env.make(store_fn=lambda: gated)
    await manager._load_kv()
    await manager.set_paused(True, by="ui")
    manager._take_kick()
    env.commands.clear()
    await _poll_with_a_change_during_the_read(manager, gated, lambda: manager.set_paused(False, by="ui"))
    assert not manager.paused and await env.store.kv_get(KV_PAUSED) is False
    assert env.commands == []  # no pause_agent to the agent the owner just woke
    assert manager._take_kick() == ("start", True, True)


async def test_while_paused_a_missing_room_is_created_only_for_a_token(env):
    """R4/SEC-1/LS-6: in pc mode a new room is LiveKit's implicit '' dispatch to the PC worker, so while paused the
    loop and the routes never create one; only a person about to join (token) gets the room (LiveKit would create it
    on the join anyway), and nothing is dispatched into it."""
    env.fake.room_info = None
    manager = env.make()
    await manager.set_paused(True, by="ui")
    assert (await manager.ensure("loop")).result == "paused"
    env.clock.advance(60)
    assert (await manager.ensure("agent_ctl_start", explicit=True, route=True)).result == "paused"
    assert env.fake.count("create_room") == 0 and not env.fake.dispatched_names()
    result = await manager.ensure("token")
    assert result.created_room and result.result == "paused" and env.fake.count("create_dispatch") == 0


async def test_while_paused_an_agent_that_joins_later_is_removed_at_once(env):
    """SEC-1: an agent present when the pause took effect has 20 s to end its own job; one that joins afterwards
    (a new room's implicit '' dispatch) is removed on the first pass that sees it, before its session can listen."""
    env.fake.participants = [person(), agent_participant("AJ_old", env.clock, session="running")]
    manager = env.make()
    await manager.ensure("loop")
    await manager.set_paused(True, by="ui")
    await manager.ensure("loop")
    assert env.fake.count("remove_participant") == 0  # AJ_old: told, and given PAUSE_REMOVE_S
    env.fake.participants = [person()]                 # it ended its job
    env.clock.advance(5)
    await manager.ensure("loop")
    env.fake.participants = [person(), agent_participant("AJ_new", env.clock, age_s=1)]
    env.clock.advance(5)
    await manager.ensure("loop")
    assert [req.identity for req in env.fake.requests["remove_participant"]] == ["agent-AJ_new"]
    assert (await _audit(env.store, "agent_removed"))[0] == {"why": "paused", "name": "", "job_id": "AJ_new"}


async def test_a_paused_flag_lost_with_a_corrupt_bridge_db_counts_as_paused(env):
    """SEC-4: as the Desktop Link's switch: a flag missing after the store renamed a corrupt bridge.db aside fails
    closed (written again, audited); the owner wakes JARVIS."""
    env.store.corrupt_renamed = 1
    manager = env.make()
    assert (await manager.ensure("loop")).result == "paused"
    assert manager.paused and await env.store.kv_get(KV_PAUSED) is True
    assert await _audit(env.store, "agent_paused") == [{"by": "bridge", "reason": "lost_with_corrupt_db"}]
    assert env.fake.count("create_dispatch") == 0


async def test_a_stored_flag_that_vanishes_while_running_never_unpauses(env):
    manager = env.make()
    await manager.set_paused(True, by="ui")
    await manager.ensure("loop")
    await env.store.kv_delete(KV_PAUSED)  # lost: a corrupt bridge.db renamed aside while the bridge runs
    await manager._poll_kv()
    assert manager.paused and await env.store.kv_get(KV_PAUSED) is True
    assert await _audit(env.store, "agent_unpaused") == []


# ---------------------------------------------------------------- review fixes: observe (LS-2, LS-3, LS-4, LS-7)


async def test_observe_heals_an_agentless_room_only_for_the_owners_token_or_wake(env):
    """LS-2: the old token route's heal without its delete. In observe mode the owner's own voice connect (token)
    or Wake (start) makes one create_dispatch to an existing room without an agent, at once; the loop and the
    routes never do, and nothing is added while one is in flight, an agent is present or JARVIS is paused."""
    manager = env.make(env_mode="observe")
    for _ in range(3):
        assert (await manager.ensure("loop")).result == "observe"
        env.clock.advance(10)
    assert (await manager.ensure("agent_ctl_start", explicit=True, route=True)).result == "observe"
    assert env.fake.count("create_dispatch") == 0
    result = await manager.ensure("token")
    assert (result.result, env.fake.dispatched_names()) == ("dispatched", [""])  # no 8 s confirmation
    env.clock.advance(5)
    assert (await manager.ensure("start", explicit=True, skip_confirm=True)).result == "in_flight"
    env.fake.participants = [agent_participant("AJ_1", env.clock, session="running")]
    env.clock.advance(60)
    assert (await manager.ensure("token")).result == "present"
    env.fake.participants = []
    await manager.set_paused(True, by="admin_cli")
    env.clock.advance(60)
    assert (await manager.ensure("token")).result == "paused"
    assert env.fake.dispatched_names() == [""] and env.fake.count("remove_participant") == 0
    await env.store.kv_set(KV_PAUSED, False)  # the admin CLI's unpause, read by the running bridge
    await manager._poll_kv()
    kick = manager._take_kick()
    assert kick == ("unpause", True, True)
    assert (await manager.ensure(kick[0], explicit=kick[1], skip_confirm=kick[2])).result == "dispatched"
    assert env.fake.dispatched_names() == ["", ""]


async def test_observe_answers_the_ensure_routes_from_memory(env):
    """LS-3: in observe mode an ensure route makes no LiveKit call (a slow or failing LiveKit can never turn the
    PC's fallback into an UNKNOWN) and still records the PC worker's report."""
    manager = env.make(env_mode="observe")
    env.fake.fail["list_rooms"] = RuntimeError("LiveKit slow or down")
    result = await manager.ensure("agent_ctl_restart", explicit=True, route=True,
                                  report=bridge_rooms.Report(agent_name="", worker_id=WORKER_ID))
    assert result.result == "observe" and result.mode == "observe" and env.fake.calls == []
    stored = await env.store.kv_get(KV_PC_AGENT)
    assert (stored["name"], stored["source"]) == ("", "report")


async def test_observe_learns_the_pc_workers_agent_name(env):
    """LS-4: the name seen on the present agent is learnt in observe mode too, so a later 'mode always' targets the
    worker that really runs."""
    env.fake.participants = [agent_participant("AJ_1", env.clock, name="jarvis-pc", session="running")]
    manager = env.make(env_mode="observe")
    await manager.ensure("loop")
    assert manager.target_agent_name == "jarvis-pc"
    assert (await env.store.kv_get(KV_PC_AGENT))["source"] == "observed"


async def test_a_kv_mode_that_overrides_the_env_is_logged_at_start(env, caplog, monkeypatch):
    """LS-7: the admin CLI's mode survives a rollback and a redeploy: the bridge says so when it starts."""
    monkeypatch.setattr(logging.getLogger("UI-BRIDGE"), "propagate", True)
    for name in ("KV_POLL_S", "LOOP_FAST_S", "LOOP_SLOW_S"):
        monkeypatch.setattr(bridge_rooms, name, 30.0)
    await env.store.kv_set(KV_ROOM_MODE, "always")
    manager = env.make(env_mode="observe")
    task = asyncio.create_task(manager.run())
    try:
        for _ in range(200):
            if env.fake.count("list_rooms"):
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert manager.mode == "always"
    assert any("overrides AGENT_DISPATCH_MODE=observe" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------- review fixes: load (SEC-7, SEC-8)


async def test_concurrent_token_ensures_share_one_pass(env):
    """SEC-7: ten tabs reconnecting at once while LiveKit is slow make one pass, not ten queued behind the lock."""
    manager = env.make()
    gate = asyncio.Event()
    real_list_rooms = env.fake.list_rooms

    async def slow_list_rooms(req: Any) -> Any:
        await gate.wait()
        return await real_list_rooms(req)

    env.fake.list_rooms = slow_list_rooms
    tasks = [asyncio.create_task(manager.ensure("token", reuse=True)) for _ in range(10)]
    for _ in range(5):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert env.fake.count("list_rooms") == 1 and len({id(result) for result in results}) == 1


async def test_a_burst_of_plain_kicks_runs_one_pass(env, monkeypatch):
    """SEC-8: agent connects, hellos and webhooks in a burst run one LiveKit pass; an explicit kick (Wake) is never
    held back."""
    for name in ("KV_POLL_S", "LOOP_FAST_S", "LOOP_SLOW_S"):
        monkeypatch.setattr(bridge_rooms, name, 30.0)
    monkeypatch.setattr(bridge_rooms, "KICK_MIN_S", 0.3)
    env.fake.participants = [agent_participant("AJ_1", env.clock, session="running")]
    manager = env.make()
    task = asyncio.create_task(manager.run())
    try:
        for _ in range(200):
            if env.fake.count("list_rooms"):
                break
            await asyncio.sleep(0.01)
        for _ in range(8):
            manager.kick("agent_hello")
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.6)
        assert env.fake.count("list_rooms") == 2  # the startup pass, then one for the whole burst
        manager.kick("start", explicit=True, skip_confirm=True)
        for _ in range(20):
            if env.fake.count("list_rooms") >= 3:
                break
            await asyncio.sleep(0.01)
        assert env.fake.count("list_rooms") == 3  # at once: within 0.2 s, never after the 0.3 s spacing
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------- the routes in ui_bridge


@pytest.fixture
async def bridge(env, monkeypatch, tmp_path):
    """ui_bridge with its room owner replaced by an enabled one on FakeLiveKit (the callables are the bridge's own),
    a temporary store and throwaway credentials."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setattr(config, "env", "development")
    module = importlib.import_module("src.core.ui_bridge")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    for name, value in (("store", env.store), ("bridge_manager", module.BridgeStateManager()), ("_clients", {}),
                        ("_notify_nonces", NonceCache()), ("_refusal_audit", TokenBucket(1.0, 30.0)),
                        ("_ensure_buckets", {name: TokenBucket(2.0, 10.0) for name in ("internal", "agent", "pc")}),
                        ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    for name, value in (("agent_token", AGENT_TOKEN), ("bridge_hmac_secret", HMAC_SECRET),
                        ("security_token", LEGACY_TOKEN), ("legacy_token_auth", True)):
        monkeypatch.setattr(config, name, value)
    settings = bridge_rooms.RoomSettings(api_url="http://livekit.c23.invalid:7880", key=LK_KEY, secret=LK_SECRET,
                                         room=ROOM, role_ok=True)
    rooms = bridge_rooms.RoomManager(
        settings, store=lambda: module.store, emit_ui=module.emit_ui,
        send_agent_command=lambda data, sid=None: module._send_agent_command(data, sid),
        agent_job_sockets=module._agent_job_sockets, ui_presence=module._ui_presence,
        on_status=module._publish_agent_status, desktop=lambda: module.desktop, api_factory=lambda: env.fake,
        mono=lambda: env.clock.mono, wall=lambda: env.clock.wall)
    monkeypatch.setattr(module, "rooms", rooms)
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    return SimpleNamespace(module=module, rooms=rooms, emitted=emitted, env=env)


def http(ns: Any, *, internal: bool = False) -> httpx.AsyncClient:
    base = f"http://bridge.test:{INTERNAL_PORT}" if internal else "http://bridge.test"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=ns.module.app), base_url=base)


def signed(raw: bytes) -> dict[str, str]:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts,
            "X-Agent-Nonce": nonce, "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw)}


ENSURE_KEYS = {"room", "room_sid", "brain", "mode", "paused", "target_agent_name", "result", "agent_in_room",
               "voice_ready", "agent_current_worker", "dispatch_id", "retry_in_s", "created"}


async def test_livekit_ensure_takes_only_the_signed_body(bridge):
    bridge.env.fake.participants = [agent_participant("AJ_cur", bridge.env.clock, session="running")]
    bridge.env.fake.dispatches = [dispatch("AD_1", "", bridge.env.clock.wall - 60,
                                           job("AJ_cur", bridge.env.clock, age_s=60, worker=WORKER_ID))]
    raw = json.dumps({"reason": "agent_ctl_restart", "agent_name": "", "worker_id": WORKER_ID},
                     separators=(",", ":")).encode()
    headers = signed(raw)
    async with http(bridge) as client:
        unsigned = await client.post("/livekit/ensure", content=raw, headers={"Content-Type": "application/json"})
        ok = await client.post("/livekit/ensure", content=raw, headers=headers)
        replay = await client.post("/livekit/ensure", content=raw, headers=headers)
        tampered = await client.post("/livekit/ensure", content=raw.replace(b"restart", b"start__"),
                                     headers=signed(raw))
    assert (unsigned.status_code, unsigned.json()) == (401, {"error": "auth_required"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert set(body) == ENSURE_KEYS and body["created"] == {"room": False, "dispatch": False}
    assert (body["result"], body["agent_in_room"], body["voice_ready"], body["agent_current_worker"]) == (
        "present", True, True, True)
    assert (body["room"], body["room_sid"], body["brain"], body["mode"], body["target_agent_name"]) == (
        ROOM, "RM_c23", "pc", "always", "")
    assert replay.status_code == 401 and tampered.status_code == 401


@pytest.mark.parametrize("raw, status, error", [
    (b'{"agent_name":"jarvis"}', 400, "bad_agent_name"),
    (b'{"reason":"BAD"}', 400, "bad_body"),
    (b"[1]", 400, "bad_body"),
    (b'{"reason":"x","pad":"' + b"y" * 1100 + b'"}', 413, "too_large"),
])
async def test_livekit_ensure_refuses_a_bad_body(bridge, raw, status, error):
    async with http(bridge) as client:
        resp = await client.post("/livekit/ensure", content=raw, headers=signed(raw))
    assert (resp.status_code, resp.json()) == (status, {"error": error})


async def test_livekit_ensure_is_rate_limited(bridge):
    raw = b"{}"
    async with http(bridge) as client:
        answers = [await client.post("/livekit/ensure", content=raw, headers=signed(raw)) for _ in range(11)]
    assert [a.status_code for a in answers[:10]] == [200] * 10
    assert answers[10].status_code == 429 and answers[10].json() == {"error": "rate_limited"}
    assert int(answers[10].headers["Retry-After"]) >= 1


async def test_internal_ensure_needs_no_headers_and_exists_only_on_the_internal_listener(bridge):
    async with http(bridge, internal=True) as internal, http(bridge) as public:
        ok = await internal.post("/internal/livekit/ensure", json={"want_voice": True})
        hidden = await public.post("/internal/livekit/ensure", json={})
        hook = await public.post("/internal/livekit/webhook", content=b"{}")
    assert ok.status_code == 200 and set(ok.json()) == ENSURE_KEYS
    assert bridge.rooms._want_voice_at is not None
    assert hidden.status_code == 404 and hook.status_code == 404


async def test_ensure_without_livekit_answers_503(bridge, monkeypatch):
    bridge.env.fake.fail["list_rooms"] = RuntimeError("unreachable")
    async with http(bridge, internal=True) as internal:
        down = await internal.post("/internal/livekit/ensure", json={})
    assert (down.status_code, down.json()) == (503, {"error": "livekit_unavailable"})
    keyless = bridge_rooms.RoomManager(bridge_rooms.RoomSettings(role_ok=True, room=ROOM))
    monkeypatch.setattr(bridge.module, "rooms", keyless)
    async with http(bridge, internal=True) as internal:
        no_keys = await internal.post("/internal/livekit/ensure", json={})
    assert (no_keys.status_code, no_keys.json()) == (503, {"error": "livekit_unavailable"})
    off = bridge_rooms.RoomManager(bridge_rooms.RoomSettings(room=ROOM))  # the PC's local bridge: role agent
    monkeypatch.setattr(bridge.module, "rooms", off)
    async with http(bridge, internal=True) as internal:
        disabled = await internal.post("/internal/livekit/ensure", json={})
    assert disabled.status_code == 200 and disabled.json()["result"] == "disabled"


class _FakeDesktopSio:
    async def emit(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def disconnect(self, *_args: Any, **_kwargs: Any) -> None:
        return None


async def test_desktop_ensure_is_pc_signed(bridge, monkeypatch, tmp_path):
    async with http(bridge) as client:
        off = await client.post("/desktop/ensure", content=b"")
    assert (off.status_code, off.json()) == (503, {"error": "link_unconfigured"})

    link = DesktopLink(sio=_FakeDesktopSio(), store=lambda: bridge.module.store, emit_ui=bridge.module.emit_ui,
                       keys=LinkKeys(agent=AGENT_LINK_KEY.public_key(), pc=PC_KEY.public_key()),
                       seen_path=str(tmp_path / "desktop_seen.db"))
    await link.start()
    link._task.cancel()
    await asyncio.gather(link._task, return_exceptions=True)
    link._task = None
    monkeypatch.setattr(bridge.module, "desktop", link)
    try:
        async with http(bridge) as client:
            good = await client.post("/desktop/ensure", content=b"",
                                     headers=dp.sign_http_request(PC_KEY, method="POST", path="/vortex/desktop/ensure"))
            forged = await client.post("/desktop/ensure", content=b"", headers=dp.sign_http_request(
                FORGER_KEY, method="POST", path="/vortex/desktop/ensure"))
        assert good.status_code == 200, good.text
        assert set(good.json()) == ENSURE_KEYS and good.json()["agent_current_worker"] is None
        assert (forged.status_code, forged.json()) == (401, {"error": "auth_required"})
    finally:
        await link.stop()


async def test_the_webhook_route_is_verified_on_the_internal_listener(bridge):
    body = _webhook("room_started")
    async with http(bridge, internal=True) as internal:
        ok = await internal.post("/internal/livekit/webhook", content=body,
                                 headers={"Authorization": _webhook_token(body)})
        bad = await internal.post("/internal/livekit/webhook", content=body, headers={"Authorization": "x.y.z"})
        big = await internal.post("/internal/livekit/webhook", content=b"x" * (bridge_rooms.WEBHOOK_MAX_BYTES + 1),
                                  headers={"Authorization": _webhook_token(body)})
    assert (ok.status_code, bad.status_code, big.status_code) == (200, 401, 413)
    assert bridge.rooms._take_kick() == ("webhook_room_started", False, False)


async def test_the_token_route_never_deletes_and_creates_a_missing_room(bridge, monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", LK_SECRET)
    bridge.env.fake.room_info = None
    async with http(bridge) as client:
        resp = await client.get("/livekit/token", headers={"X-Vortex-Token": LEGACY_TOKEN})
    assert resp.status_code == 200 and resp.json()["token"]
    assert bridge.env.fake.count("create_room") == 1  # FakeLiveKit fails the test on any delete


# ---------------------------------------------------------------- agent_status and /status


async def test_agent_status_is_the_whole_object_emitted_once_per_change(bridge):
    module, env_ = bridge.module, bridge.env
    auth = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "c23sha", "room": ROOM, "job_id": "AJ_s"}
    assert await module.handle_connect("sid-agent", {}, auth) is True
    env_.fake.participants = [agent_participant("AJ_s", env_.clock, session="running")]
    await bridge.rooms.ensure("loop")
    await bridge.rooms.ensure("loop")  # nothing changed: nothing emitted
    statuses = [data for event, data, _room in bridge.emitted if event == "agent_status"]
    assert [(s["online"], s["in_room"], s["voice_ready"]) for s in statuses] == [(True, False, False),
                                                                                  (True, True, True)]
    last = statuses[-1]
    assert set(last) == {"online", "in_room", "voice_ready", "paused", "restarting", "since", "last_heartbeat",
                         "host", "version", "brain", "agent_name", "session"}
    assert (last["session"], last["agent_name"], last["brain"], last["host"]) == ("running", "", "pc", "pc-host")
    assert module._agent_job_sockets() == {"AJ_s": module._clients["sid-agent"].since}
    assert await module.handle_connect("sid-ui", {}, {"token": LEGACY_TOKEN}) is True
    [state] = [data for event, data, room in bridge.emitted if event == "init_state" and room == "sid-ui"]
    assert state["agent_status"] == last

    async with http(bridge, internal=True) as internal:
        status = (await internal.get("/status")).json()
    assert status["status"] == "ok" and status["agent"]["voice_ready"] is True
    livekit = status["livekit"]
    assert (livekit["ok"], livekit["room_sid"], livekit["agent_in_room"], livekit["mode"]) == (
        True, "RM_c23", True, "always")
    assert livekit["agents"] == [{"lk.agent.name": "", "jarvis.session": "running", "state": "active",
                                  "joined_at": pytest.approx(env_.clock.wall - 10, abs=1), "job_id": "AJ_s",
                                  "bridge_link": True}]
    assert OWNER not in json.dumps(status) and WORKER_ID not in json.dumps(status)


async def test_status_is_degraded_without_a_voice_ready_agent_unless_paused(bridge):
    await bridge.rooms.ensure("loop")
    async with http(bridge, internal=True) as internal:
        assert (await internal.get("/status")).json()["status"] == "degraded"
        await bridge.rooms.set_paused(True, by="ui")
        paused = (await internal.get("/status")).json()
    assert paused["status"] == "ok" and paused["agent"]["paused"] is True


async def test_status_without_the_room_owner_keeps_the_v8_rule(bridge, monkeypatch):
    """R5: a bridge without the room owner (the PC's local bridge) never reads LiveKit: 'ok' while an owner-room
    agent socket is connected (v8), livekit enabled false with ok null, and presence null (unknown), never a false
    'absent'."""
    off = bridge_rooms.RoomManager(bridge_rooms.RoomSettings(room=ROOM))  # JARVIS_ROLE is not bridge
    monkeypatch.setattr(bridge.module, "rooms", off)
    async with http(bridge, internal=True) as internal:
        before = (await internal.get("/status")).json()
    assert before["status"] == "degraded" and before["agent"]["online"] is False
    auth = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "c23sha", "room": ROOM, "job_id": "AJ_s"}
    assert await bridge.module.handle_connect("sid-agent", {}, auth) is True
    async with http(bridge, internal=True) as internal:
        status = (await internal.get("/status")).json()
    assert status["status"] == "ok"
    livekit = status["livekit"]
    assert (livekit["enabled"], livekit["ok"], livekit["agent_in_room"], livekit["voice_ready"], livekit["mode"]) == (
        False, None, None, None, "disabled")
    agent = status["agent"]
    assert (agent["online"], agent["in_room"], agent["voice_ready"], agent["paused"], agent["restarting"]) == (
        True, None, None, False, False)
    [emitted] = [data for event, data, _room in bridge.emitted if event == "agent_status"]
    assert (emitted["in_room"], emitted["voice_ready"]) == (None, None)


async def test_each_ensure_route_has_its_own_rate_bucket(bridge):
    """SEC-6: a jarvis-network client flooding the internal route (its only credential is the network) never starves
    the PC's signed route."""
    raw = b"{}"
    async with http(bridge, internal=True) as internal, http(bridge) as public:
        flood = [await internal.post("/internal/livekit/ensure", json={}) for _ in range(11)]
        signed_ok = await public.post("/livekit/ensure", content=raw, headers=signed(raw))
    assert [a.status_code for a in flood[:10]] == [200] * 10 and flood[10].status_code == 429
    assert signed_ok.status_code == 200 and set(signed_ok.json()) == ENSURE_KEYS


async def test_the_agents_job_end_notify_reaches_the_room_owner(bridge):
    """notify job_end (K-40/K-60 v9): the room owner stops counting that job as in flight; nothing goes to the UI."""
    bridge.env.fake.dispatches = [dispatch("AD_1", "", bridge.env.clock.wall - 40,
                                           job("AJ_gone", bridge.env.clock, age_s=40))]
    body = json.dumps({"type": "job_end", "payload": {"job_id": "AJ_gone", "reason": "session_failed"}}).encode()
    async with http(bridge, internal=True) as internal:
        resp = await internal.post("/notify", content=body, headers={"Content-Type": "application/json"})
    assert (resp.status_code, resp.json()) == (200, {"status": "ok"})
    assert "AJ_gone" in bridge.rooms._ended_jobs and bridge.rooms._take_kick() == ("job_end", False, False)
    assert [event for event, _data, _room in bridge.emitted] == []
    result = await bridge.rooms.ensure("start", explicit=True, skip_confirm=True)
    assert result.result == "dispatched"  # AJ_gone (40 s old, no agent ever seen) no longer blocks
