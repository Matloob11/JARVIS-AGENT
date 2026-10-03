"""deploy/windows-local/ensure_room.py: one agent in the room, never a deleted live session.

A fake LiveKitAPI answers with real protobufs and records every call, so no test reaches the network;
asyncio.sleep is an AsyncMock, so no test waits. Settings are throwaway values and no .env is read.

The fake models LiveKit server v1.13.7 as it really answers: list_dispatch keeps every job a room ever had
as JS_RUNNING (the state copied at assignment is never updated), nothing re-dispatches a job by itself,
and a stopped worker's agent stays listed for a while. A job's worker_id says which worker got it.
"""
# pylint: disable=protected-access,redefined-outer-name,no-member  # protobuf members are generated
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import dotenv
import pytest
from livekit import api as livekit_api
from livekit.protocol import agent, agent_dispatch, models
from livekit.protocol import room as livekit_room

ROOT = Path(__file__).resolve().parents[2]
ENSURE_ROOM = ROOT / "deploy" / "windows-local" / "ensure_room.py"
ROOM = "wave-h-room"
FAKE_KEY = "APIwaveHfakeKey"
FAKE_SECRET = "waveHfakeSecretValue0123456789"
FAKE_TURN = "waveHturnPasswordValue"
WORKER_ID = "AW_waveHcurrent"
OLD_WORKER_ID = "AW_waveHearlier"

AGENT = models.ParticipantInfo.Kind.AGENT
STANDARD = models.ParticipantInfo.Kind.STANDARD
ACTIVE = models.ParticipantInfo.State.ACTIVE
DISCONNECTED = models.ParticipantInfo.State.DISCONNECTED
RUNNING = agent.JobStatus.JS_RUNNING
PENDING = agent.JobStatus.JS_PENDING
LIVE_ROOM_AGE_S = 6255  # the live probe's room: one JS_RUNNING job as old as the room, no agent


def _participant(identity: str, kind: int, state: int = ACTIVE, joined_age_s: float = 3.0) -> models.ParticipantInfo:
    joined = time.time() - joined_age_s
    return models.ParticipantInfo(identity=identity, kind=kind, state=state, joined_at=int(joined),
                                  joined_at_ms=int(joined * 1000))


def _human() -> models.ParticipantInfo:
    return _participant("owner-browser", STANDARD, joined_age_s=600)


def _new_agent() -> models.ParticipantInfo:
    """The current worker's agent: livekit-agents names it agent-<job id>."""
    return _participant("agent-AJ_new", AGENT)


def _ghost() -> models.ParticipantInfo:
    """The stopped worker's agent, still listed: it joined long before the current worker started."""
    return _participant("agent-AJ_old", AGENT, joined_age_s=LIVE_ROOM_AGE_S)


def _job(job_id: str, status: int = RUNNING, *, worker: str = WORKER_ID, age_s: float = 3.0,
         identity: str | None = None) -> agent.Job:
    ns = int((time.time() - age_s) * 1e9)  # v1.13.7 job states carry nanoseconds
    return agent.Job(id=job_id, room=models.Room(name=ROOM),
                     state=agent.JobState(status=status, started_at=ns, updated_at=ns, worker_id=worker,
                                          participant_identity=f"agent-{job_id}" if identity is None else identity))


def _old_job(job_id: str = "AJ_old", status: int = RUNNING) -> agent.Job:
    """The live v1.13.7 shape: an earlier worker's job, still JS_RUNNING, as old as the room."""
    return _job(job_id, status, worker=OLD_WORKER_ID, age_s=LIVE_ROOM_AGE_S)


def _dispatch(*jobs: agent.Job, dispatch_id: str = "AD_wave_h") -> agent_dispatch.AgentDispatch:
    return agent_dispatch.AgentDispatch(id=dispatch_id, room=ROOM,
                                        state=agent_dispatch.AgentDispatchState(jobs=list(jobs)))


def _room() -> models.Room:
    return models.Room(name=ROOM, sid="RM_wave_h", empty_timeout=3600, departure_timeout=3600,
                       max_participants=10, num_participants=1, turn_password=FAKE_TURN,
                       metadata="wave-h-metadata")


class FakeLiveKit:
    """The LiveKit server as ensure_room.py sees it. participants: the answers to list_participants in
    order (the last one repeats); after_dispatch / after_recreate: what list_participants answers once
    create_dispatch ran / the deleted room was created again (each also adds the job of the current
    worker that brings agent-AJ_new)."""

    def __init__(self, *, exists: bool, participants: list[list[models.ParticipantInfo]] | None = None,
                 dispatches: list[agent_dispatch.AgentDispatch] | None = None,
                 after_dispatch: list[models.ParticipantInfo] | None = None,
                 after_recreate: list[models.ParticipantInfo] | None = None,
                 participants_error: Exception | None = None,
                 dispatch_list_error: Exception | None = None,
                 create_dispatch_error: Exception | None = None) -> None:
        self.room: models.Room | None = _room() if exists else None
        self.answers = list(participants or [[]])
        self.dispatch_list = list(dispatches or [])
        self.after_dispatch = after_dispatch
        self.after_recreate = after_recreate
        self.instances = 0
        self.calls: list[str] = []

        async def list_rooms(req: Any) -> Any:
            self.calls.append("list_rooms")
            assert list(req.names) == [ROOM]
            return livekit_room.ListRoomsResponse(rooms=[self.room] if self.room is not None else [])

        async def create_room(req: Any) -> Any:
            self.calls.append("create_room")
            if self.room is None:
                self.room = _room()
                if self.after_recreate is not None:
                    self.answers = [self.after_recreate]
                    self.dispatch_list = [_dispatch(_job("AJ_new"), dispatch_id="AD_room")]
            return self.room

        async def delete_room(req: Any) -> Any:
            self.calls.append("delete_room")
            assert req.room == ROOM
            self.room = None
            self.answers = [[]]
            self.dispatch_list = []
            return livekit_room.DeleteRoomResponse()

        async def list_participants(req: Any) -> Any:
            self.calls.append("list_participants")
            assert req.room == ROOM
            if participants_error is not None:
                raise participants_error
            current = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            return livekit_room.ListParticipantsResponse(participants=current)

        async def list_dispatch(room_name: str) -> Any:
            self.calls.append("list_dispatch")
            assert room_name == ROOM
            if dispatch_list_error is not None:
                raise dispatch_list_error
            return list(self.dispatch_list)

        async def create_dispatch(req: Any) -> Any:
            self.calls.append("create_dispatch")
            if create_dispatch_error is not None:
                raise create_dispatch_error
            created = _dispatch(_job("AJ_new"), dispatch_id="AD_new")
            self.dispatch_list.append(created)
            if self.after_dispatch is not None:
                self.answers = [self.after_dispatch]
            return created

        self.room_svc = SimpleNamespace(
            list_rooms=AsyncMock(side_effect=list_rooms), create_room=AsyncMock(side_effect=create_room),
            delete_room=AsyncMock(side_effect=delete_room),
            list_participants=AsyncMock(side_effect=list_participants))
        self.dispatch_svc = SimpleNamespace(list_dispatch=AsyncMock(side_effect=list_dispatch),
                                            create_dispatch=AsyncMock(side_effect=create_dispatch))
        self.aclose = AsyncMock()

    def api_class(self) -> Any:
        fake = self

        class FakeLiveKitAPI:
            def __init__(self, url: str, key: str, secret: str) -> None:
                assert (url, key, secret) == ("https://livekit.wave-h.invalid", FAKE_KEY, FAKE_SECRET)
                fake.instances += 1
                self.room = fake.room_svc
                self.agent_dispatch = fake.dispatch_svc
                self.aclose = fake.aclose

        return FakeLiveKitAPI

    def count(self, name: str) -> int:
        return self.calls.count(name)


@pytest.fixture
def module(monkeypatch, tmp_path) -> Any:
    """ensure_room.py as a fresh module: throwaway settings, no .env read, no real sleeps, and a current
    worker that started a minute ago with LiveKit worker id WORKER_ID."""
    monkeypatch.setenv("LIVEKIT_URL", "wss://livekit.wave-h.invalid")
    monkeypatch.setenv("LIVEKIT_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", FAKE_SECRET)
    monkeypatch.setenv("JARVIS_ROOM_NAME", ROOM)
    monkeypatch.setenv("JARVIS_RUN_DIR", str(tmp_path / "run"))  # never the live worker's run/
    monkeypatch.delenv("JARVIS_BRAIN", raising=False)
    monkeypatch.delenv("JARVIS_LINK_URL", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    spec = importlib.util.spec_from_file_location("wave_h_ensure_room", ENSURE_ROOM)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded, "asyncio", SimpleNamespace(sleep=AsyncMock(), run=asyncio.run))
    monkeypatch.setattr(loaded, "current_worker", lambda: loaded.Worker(time.time() - 60, WORKER_ID))
    return loaded


def _install(monkeypatch, fake: FakeLiveKit) -> FakeLiveKit:
    monkeypatch.setattr(livekit_api, "LiveKitAPI", fake.api_class())
    return fake


def _slept(module: Any) -> float:
    return sum(call.args[0] for call in module.asyncio.sleep.await_args_list)


def _assert_clean(out: str) -> None:
    for secret in (FAKE_KEY, FAKE_SECRET, FAKE_TURN, "turn_password", "owner-browser", "wave-h-metadata",
                   WORKER_ID, OLD_WORKER_ID, "agent-AJ_"):
        assert secret not in out


def _assert_one_empty_name_dispatch(fake: FakeLiveKit) -> None:
    assert fake.count("create_dispatch") == 1
    request = fake.dispatch_svc.create_dispatch.await_args.args[0]
    assert (request.agent_name, request.room) == ("", ROOM)


def _never_cut(fake: FakeLiveKit) -> None:
    assert fake.count("delete_room") == 0 and fake.count("create_room") == 0


# --------------------------------------------------------------------------- room absent


async def test_absent_room_is_created_with_the_single_agents_dispatch(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=False))
    await module.ensure_room()

    assert fake.count("create_room") == 1
    assert fake.count("create_dispatch") == 0 and fake.count("delete_room") == 0
    request = fake.room_svc.create_room.await_args.args[0]
    assert request.name == ROOM and (request.empty_timeout, request.departure_timeout) == (3600, 3600)
    assert [d.agent_name for d in request.agents] == [""]
    out = capsys.readouterr().out
    _assert_clean(out)
    assert f"[ROOM] Created room '{ROOM}' with single agent dispatch" in out
    assert "[OK] Room ready" in out and f"[ROOM] Result: {module.RESULT_CREATED}" in out
    fake.aclose.assert_awaited_once()


# --------------------------------------------------------------------------- an agent is in the room


async def test_the_current_workers_agent_changes_nothing(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]],
                                             dispatches=[_dispatch(_old_job(), _job("AJ_new"))]))
    await module.ensure_room()

    for call in ("create_room", "delete_room", "create_dispatch"):
        assert fake.count(call) == 0
    module.asyncio.sleep.assert_not_awaited()
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "agents=1 others=1 earlier_worker_agents=0" in out and "nothing changed" in out
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in out


async def test_an_agent_without_a_known_job_that_joined_after_the_worker_start_counts(module, monkeypatch):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]]))
    await module.ensure_room()
    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    module.asyncio.sleep.assert_not_awaited()


async def test_an_agent_that_cannot_be_checked_counts_as_present(module, monkeypatch):
    monkeypatch.setattr(module, "current_worker", module.Worker)  # no lock: start and worker id unknown
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _ghost()]],
                                             dispatches=[_dispatch(_old_job())]))
    await module.ensure_room()
    assert fake.count("create_dispatch") == 0
    _never_cut(fake)


async def test_a_disconnected_agent_does_not_count(module, monkeypatch, capsys):
    gone = _participant("agent-AJ_gone", AGENT, DISCONNECTED)
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[gone, _human()]],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()
    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    assert "[OK] Agent joined the room" in capsys.readouterr().out


# --------------------------------------------------------------------------- the live v1.13.7 restart shape


async def test_a_stale_running_job_with_a_person_present_gets_one_dispatch(module, monkeypatch, capsys):
    """The owner's UI is connected during a restart: the old job still reads JS_RUNNING, its agent is gone
    and LiveKit re-dispatches nothing. Exactly one dispatch brings the new worker's agent; nothing is cut."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_old_job())],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    assert _slept(module) <= module.SELF_REDISPATCH_WAIT_S + module.AGENT_JOIN_WAIT_S
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "live_jobs=0 stale_jobs=1 (job states: JS_RUNNING=1)" in out
    assert "one agent dispatch is added" in out and "[OK] Agent joined the room" in out
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in out


async def test_a_stale_running_job_and_no_agent_joining_warns_and_never_deletes(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_old_job())]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "[WARN] No agent joined" in out and "never deleted under a live session" in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out


async def test_an_old_running_job_counts_as_stale_when_the_worker_id_is_unknown(module, monkeypatch):
    monkeypatch.setattr(module, "current_worker", lambda: module.Worker(time.time() - 60, ""))
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_old_job())],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()
    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)


async def test_an_old_job_of_the_current_worker_whose_agent_left_is_stale(module, monkeypatch):
    old_own = _job("AJ_mine", age_s=module.JOB_FRESH_S + 60)
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(old_own)],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()
    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)


@pytest.mark.parametrize("status", [PENDING, RUNNING])
async def test_a_fresh_job_of_the_current_worker_gets_no_extra_dispatch(module, monkeypatch, capsys, status):
    """A job launched for this worker seconds ago may still be connecting its agent: never doubled."""
    fake = _install(monkeypatch, FakeLiveKit(
        exists=True, participants=[[_human()], [_human()], [_human()], [_human()], [_human()],
                                   [_human(), _new_agent()]],
        dispatches=[_dispatch(_old_job(), _job("AJ_new", status, age_s=4))]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "live_jobs=1 stale_jobs=1" in out and "so no dispatch is added; waiting for its agent" in out
    assert "[OK] Agent joined the room" in out


async def test_a_fresh_job_counts_by_age_when_the_worker_id_is_unknown(module, monkeypatch, capsys):
    monkeypatch.setattr(module, "current_worker", module.Worker)
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_job("AJ_new", age_s=10, worker=""))]))
    await module.ensure_room()
    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in capsys.readouterr().out


async def test_a_job_of_another_worker_is_stale_however_young(module, monkeypatch):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_job("AJ_x", worker=OLD_WORKER_ID, age_s=5))],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()
    _assert_one_empty_name_dispatch(fake)


# --------------------------------------------------------------------------- a stopped worker's agent


async def test_a_ghost_agent_is_waited_out_then_one_dispatch_is_added(module, monkeypatch, capsys):
    """agent_ctl's Ctrl+Break leaves the old agent listed for ~15 s; it must not count as present."""
    fake = _install(monkeypatch, FakeLiveKit(
        exists=True, participants=[[_human(), _ghost()], [_human(), _ghost()], [_human(), _ghost()], [_human()]],
        dispatches=[_dispatch(_old_job())], after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "agents=1 others=1 earlier_worker_agents=1" in out
    assert "belong to an earlier worker" in out and "The earlier worker's agent left the room" in out
    assert "nothing changed" not in out and "[OK] Agent joined the room" in out
    assert _slept(module) <= module.GHOST_WAIT_S + module.SELF_REDISPATCH_WAIT_S + module.AGENT_JOIN_WAIT_S


async def test_a_ghost_agent_is_judged_by_its_join_time_without_a_job(module, monkeypatch):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _ghost()], [_human()]],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()
    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)


async def test_an_earlier_agent_that_stays_is_never_doubled(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _ghost()]],
                                             dispatches=[_dispatch(_old_job())]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    # Its job names another worker id: it gets the one extra check, never more than OTHER_WORKER_GHOST_WAIT_S.
    assert _slept(module) == pytest.approx(module.OTHER_WORKER_GHOST_WAIT_S)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "belong to another LiveKit worker" in out and "checking once more" in out
    assert "treated as live, so no dispatch is added" in out
    assert f"[ROOM] Result: {module.RESULT_EARLIER_AGENT}" in out


def _ghost_polls(module: Any, ghost_polls: int, then: list[models.ParticipantInfo]) -> list[list[Any]]:
    """list_participants answers: the first look and ghost_polls polls list the ghost, then `then` repeats."""
    assert ghost_polls * module.POLL_S < module.OTHER_WORKER_GHOST_WAIT_S
    return [[*then, _ghost()] for _ in range(1 + ghost_polls)] + [then]


async def test_a_dead_workers_ghost_outlasting_the_first_wait_is_checked_again(module, monkeypatch, capsys):
    """Both worker ids are known and differ: after GHOST_WAIT_S the agent is checked once more, and once it
    is gone the dispatch path runs as usual."""
    ghost_polls = round(module.GHOST_WAIT_S / module.POLL_S) + 3  # still listed 6 s after the first wait
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=_ghost_polls(module, ghost_polls, [_human()]),
                                             dispatches=[_dispatch(_old_job())],
                                             after_dispatch=[_human(), _new_agent()]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "other_worker_id_agents=1" in out and "checking once more" in out
    assert "The earlier worker's agent left the room" in out and "[OK] Agent joined the room" in out
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in out
    slept = _slept(module)
    assert module.GHOST_WAIT_S < slept <= (module.OTHER_WORKER_GHOST_WAIT_S + module.SELF_REDISPATCH_WAIT_S
                                           + module.AGENT_JOIN_WAIT_S)


async def test_a_ghost_judged_only_by_its_join_time_gets_no_extra_check(module, monkeypatch, capsys):
    """Without a job naming its worker, an earlier agent keeps the plain GHOST_WAIT_S."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _ghost()]]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    assert _slept(module) == pytest.approx(module.GHOST_WAIT_S)
    out = capsys.readouterr().out
    assert "other_worker_id_agents=0" in out and "checking once more" not in out
    assert f"[ROOM] Result: {module.RESULT_EARLIER_AGENT}" in out


async def test_the_longest_path_stays_inside_agent_ctls_budget(module, monkeypatch, capsys):
    """A dead worker's ghost that leaves on the last extra check, then a dispatch and a recreated room that
    bring no agent: every wait runs in full, and agent_ctl's timeout still leaves room for the API calls."""
    ghost_polls = round(module.OTHER_WORKER_GHOST_WAIT_S / module.POLL_S) - 1
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=_ghost_polls(module, ghost_polls, []),
                                             dispatches=[_dispatch(_old_job())], after_recreate=[]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 1 and fake.count("delete_room") == 1
    worst = (module.OTHER_WORKER_GHOST_WAIT_S + module.SELF_REDISPATCH_WAIT_S + 2 * module.AGENT_JOIN_WAIT_S)
    assert _slept(module) == pytest.approx(worst)
    ctl = (ROOT / "scripts" / "agent_ctl.py").read_text(encoding="utf-8")
    budget = float(re.search(r"^ENSURE_ROOM_TIMEOUT_S = (\d+)", ctl, re.MULTILINE).group(1))
    assert worst + 20 <= budget
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in capsys.readouterr().out


async def test_the_new_agent_replacing_a_ghost_ends_the_wait(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(
        exists=True, participants=[[_human(), _ghost()], [_human(), _ghost(), _new_agent()]],
        dispatches=[_dispatch(_old_job(), _job("AJ_new"))]))
    await module.ensure_room()
    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in capsys.readouterr().out


# --------------------------------------------------------------------------- empty room: last resort


async def test_empty_room_without_agent_after_dispatch_is_deleted_and_recreated(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[]], dispatches=[_dispatch(_old_job())],
                                             after_recreate=[_new_agent()]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    assert fake.count("delete_room") == 1 and fake.count("create_room") == 1
    assert fake.calls.index("create_dispatch") < fake.calls.index("delete_room") < fake.calls.index("create_room")
    request = fake.room_svc.create_room.await_args.args[0]
    assert [d.agent_name for d in request.agents] == [""]
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "deleting and recreating it with agents=" in out and "[OK] Agent joined the recreated room" in out
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in out


async def test_a_person_joining_before_the_last_resort_stops_the_delete(module, monkeypatch, capsys):
    polls = [[]] * (1 + round(module.SELF_REDISPATCH_WAIT_S / module.POLL_S))
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=polls))
    fake.after_dispatch = []  # the dispatch brings no agent: every poll after it sees an empty room...
    original = fake.room_svc.list_participants.side_effect
    joins_at = 1 + round(module.SELF_REDISPATCH_WAIT_S / module.POLL_S) + \
        round(module.AGENT_JOIN_WAIT_S / module.POLL_S)

    async def list_participants(req: Any) -> Any:
        if fake.count("list_participants") >= joins_at:  # ...until the owner joins just before the delete
            fake.calls.append("list_participants")
            return livekit_room.ListParticipantsResponse(participants=[_human()])
        return await original(req)

    fake.room_svc.list_participants.side_effect = list_participants
    await module.ensure_room()
    assert fake.count("delete_room") == 0 and fake.count("create_dispatch") == 1
    out = capsys.readouterr().out
    assert "is no longer empty" in out and f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out


# --------------------------------------------------------------------------- unreadable lists, console


async def test_unreadable_participants_change_nothing(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(
        exists=True, participants_error=RuntimeError(f"401 for key {FAKE_KEY} secret {FAKE_SECRET}")))
    await module.ensure_room()
    for call in ("create_room", "delete_room", "create_dispatch"):
        assert fake.count(call) == 0
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "[ROOM] list_participants note: 401 for key *** secret ***" in out
    assert "left unchanged" in out and f"[ROOM] Result: {module.RESULT_UNKNOWN}" in out


async def test_unreadable_dispatches_add_no_dispatch_and_keep_a_person(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(
        exists=True, participants=[[_human()]], dispatch_list_error=RuntimeError(f"404 key {FAKE_KEY}")))
    await module.ensure_room()
    assert fake.count("create_dispatch") == 0 and fake.count("delete_room") == 0
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "agent_dispatch=unknown" in out and "[ROOM] list_dispatch note: 404 key ***" in out


async def test_unreadable_dispatches_in_an_empty_room_still_recreate_it(module, monkeypatch, capsys):
    """Nobody in the room and no dispatch list: no dispatch is added (it could double a job), but the empty
    room is recreated with agents= (that cuts nobody); the new agent counts by its join time."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[]],
                                             dispatch_list_error=RuntimeError(f"503 key {FAKE_KEY}"),
                                             after_recreate=[_new_agent()]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 0
    assert fake.count("delete_room") == 1 and fake.count("create_room") == 1
    assert fake.calls.index("delete_room") < fake.calls.index("create_room")
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "agent_dispatch=unknown" in out and "The dispatches could not be listed" in out
    assert "deleting and recreating it with agents=" in out and "[OK] Agent joined the recreated room" in out
    assert f"[ROOM] Result: {module.RESULT_AGENT}" in out


async def test_console_never_shows_credentials_or_the_turn_password(module, monkeypatch, capsys):
    error = RuntimeError(f"unauthenticated: key {FAKE_KEY} secret {FAKE_SECRET}")
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]], create_dispatch_error=error))
    assert FAKE_TURN in str(livekit_room.ListRoomsResponse(rooms=[fake.room]))  # what a raw print would leak
    await module.ensure_room()
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "[ROOM] create_dispatch note: unauthenticated: key *** secret ***" in out
    assert ("[ROOM] Listed: name=wave-h-room sid=RM_wave_h empty_timeout=3600s departure_timeout=3600s "
            "agent_dispatch=no") in out


# --------------------------------------------------------------------------- helpers: worker, timestamps


@pytest.mark.usefixtures("module")  # its JARVIS_RUN_DIR points run/ at tmp_path
def test_current_worker_reads_the_lock_holder_and_its_link(monkeypatch, tmp_path):
    from src.core import agent_lock  # pylint: disable=import-outside-toplevel

    run = tmp_path / "run"
    run.mkdir()
    info = {"pid": 4242, "started_at": "2026-10-03T08:42:29+05:00"}
    (run / "agent.pid.json").write_text(json.dumps(info), encoding="utf-8")
    link = {"state": "connected", "worker_id": WORKER_ID, "lock_pid": 4242,
            "lock_started_at": info["started_at"]}
    (run / "agent_link.json").write_text(json.dumps(link), encoding="utf-8")
    # The fixture replaced current_worker, so the real one comes from a second load of the file.
    spec = importlib.util.spec_from_file_location("wave_h_ensure_room_worker", ENSURE_ROOM)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    monkeypatch.setattr(agent_lock, "is_locked", lambda: True)
    worker = fresh.current_worker()
    assert worker.worker_id == WORKER_ID
    assert worker.started_at == pytest.approx(
        datetime.fromisoformat(info["started_at"]).timestamp())
    assert "yes" in worker.describe() and WORKER_ID not in worker.describe()

    link["lock_pid"] = 1  # written by an earlier worker: its id is not the current one
    (run / "agent_link.json").write_text(json.dumps(link), encoding="utf-8")
    assert fresh.current_worker().worker_id == ""

    monkeypatch.setattr(agent_lock, "is_locked", lambda: False)
    unknown = fresh.current_worker()
    assert (unknown.started_at, unknown.worker_id) == (None, "")


@pytest.mark.parametrize("value, seconds", [(1_759_463_000, 1_759_463_000.0),
                                            (1_759_463_000_500, 1_759_463_000.5),
                                            (1_759_463_000_500_000, 1_759_463_000.5),
                                            (1_759_463_000_500_000_000, 1_759_463_000.5), (0, 0.0)])
def test_livekit_timestamps_in_any_unit_become_seconds(module, value, seconds):
    assert module._epoch_s(value) == pytest.approx(seconds)


# --------------------------------------------------------------------------- main(): vps mode and bridge


def _no_livekit(monkeypatch) -> list[str]:
    made: list[str] = []

    class Forbidden:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            made.append("LiveKitAPI")
            raise AssertionError("no LiveKit API call expected")

    monkeypatch.setattr(livekit_api, "LiveKitAPI", Forbidden)
    return made


def test_vps_mode_makes_no_livekit_call(module, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    made = _no_livekit(monkeypatch)
    module.main()
    assert not made
    assert "no-op in vps mode" in capsys.readouterr().out


def _bridge(monkeypatch, tmp_path, status: int) -> list[str]:
    import httpx  # pylint: disable=import-outside-toplevel
    from services.utils import desktop_protocol as dp  # pylint: disable=import-outside-toplevel
    from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setenv("JARVIS_LINK_URL", "https://bridge.wave-h.invalid/")
    (tmp_path / node_settings.KEYS_FILE).write_bytes(b"not-a-real-key")
    monkeypatch.setattr(node_settings, "node_home", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(dp, "load_pc_key_file", lambda path: "pc-key")
    monkeypatch.setattr(dp, "sign_http_request", lambda key, **kwargs: {"X-Sig": "signed"})
    posted: list[str] = []

    def post(url: str, **kwargs: Any) -> Any:
        posted.append(url)
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr(httpx, "post", post)
    return posted


def test_bridge_200_makes_no_livekit_call(module, monkeypatch, tmp_path, capsys):
    posted = _bridge(monkeypatch, tmp_path, 200)
    made = _no_livekit(monkeypatch)
    module.main()
    assert posted == ["https://bridge.wave-h.invalid/vortex/desktop/ensure"] and not made
    assert "Room ensured through the bridge" in capsys.readouterr().out


def test_bridge_not_200_falls_back_to_livekit(module, monkeypatch, tmp_path, capsys):
    posted = _bridge(monkeypatch, tmp_path, 404)
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]]))
    module.main()
    assert len(posted) == 1 and fake.instances == 1
    for call in ("create_room", "delete_room", "create_dispatch"):
        assert fake.count(call) == 0
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "returned HTTP 404; using the LiveKit API" in out
