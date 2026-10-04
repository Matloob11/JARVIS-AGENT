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
    for name in ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "JARVIS_AGENT_NAME"):  # no bridge route unless a test sets it
        monkeypatch.delenv(name, raising=False)
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
    assert "[WARN] No agent joined" in out and "a person is in it" in out and "the room is never deleted" in out
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
    """A dead worker's ghost that leaves on the last extra check, then a dispatch that brings no agent: every
    wait runs in full, and agent_ctl's timeout still leaves room for the API calls. The bridge path's longest
    wait (one call, then the 90 s follow-up) fits too."""
    ghost_polls = round(module.OTHER_WORKER_GHOST_WAIT_S / module.POLL_S) - 1
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=_ghost_polls(module, ghost_polls, []),
                                             dispatches=[_dispatch(_old_job())]))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 1
    _never_cut(fake)
    worst = module.OTHER_WORKER_GHOST_WAIT_S + module.SELF_REDISPATCH_WAIT_S + module.AGENT_JOIN_WAIT_S
    assert _slept(module) == pytest.approx(worst)
    assert module.BRIDGE_TIMEOUT_S + module.BRIDGE_WAIT_S + 10 <= 120
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


# --------------------------------------------------------------------------- empty room: never deleted (C-23)


async def test_an_empty_room_without_agent_after_dispatch_is_never_deleted(module, monkeypatch, capsys):
    """C-23 (K-53 v9): the v7/v8 last-resort delete is gone. A room still without an agent stays as it is, whoever
    is in it, and the run says NO AGENT."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[]], dispatches=[_dispatch(_old_job())]))
    await module.ensure_room()

    _assert_one_empty_name_dispatch(fake)
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "nobody is in it" in out and "the room is never deleted" in out
    assert "deleting" not in out and "recreat" not in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out


def test_the_file_has_no_delete_path_any_more(module):
    """No delete_room call and no recreate helper are left in ensure_room.py (tests/infrastructure/
    test_no_room_delete.py checks every file)."""
    source = ENSURE_ROOM.read_text(encoding="utf-8")
    assert "delete_room(" not in source and "DeleteRoomRequest" not in source
    assert not hasattr(module, "_recreate_empty_room")


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


async def test_unreadable_dispatches_in_an_empty_room_add_nothing_and_delete_nothing(module, monkeypatch, capsys):
    """Nobody in the room and no dispatch list: no dispatch is added (it could double a job), and the room is
    never deleted or recreated (C-23)."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[]],
                                             dispatch_list_error=RuntimeError(f"503 key {FAKE_KEY}")))
    await module.ensure_room()

    assert fake.count("create_dispatch") == 0
    _never_cut(fake)
    out = capsys.readouterr().out
    _assert_clean(out)
    assert "agent_dispatch=unknown" in out and "The dispatches could not be listed" in out
    assert f"[ROOM] Result: {module.RESULT_NO_AGENT}" in out


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
    # C-23: no agent_name in the link file (a worker before C-23): JARVIS_AGENT_NAME, else ''
    assert worker.agent_name == ""
    monkeypatch.setenv("JARVIS_AGENT_NAME", "jarvis-pc")
    assert fresh.current_worker().agent_name == "jarvis-pc"
    link["agent_name"] = ""  # what the running worker registered wins over the setting
    (run / "agent_link.json").write_text(json.dumps(link), encoding="utf-8")
    assert fresh.current_worker().agent_name == ""
    monkeypatch.delenv("JARVIS_AGENT_NAME")

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


# --------------------------------------------------------------------------- one '[ROOM] Result' line on every exit path


def _results(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("[ROOM] Result:")]


def test_vps_mode_prints_its_result_line_and_exits_0(module, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_BRAIN", "vps")
    _no_livekit(monkeypatch)
    module.main()  # returns: exit 0
    assert _results(capsys.readouterr().out) == [f"[ROOM] Result: {module.RESULT_VPS_MODE}"]
    assert module.RESULT_VPS_MODE.startswith("OK")


def test_bridge_200_prints_its_result_line_and_exits_0(module, monkeypatch, tmp_path, capsys):
    _bridge(monkeypatch, tmp_path, 200)
    _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE}"]
    # The bridge only ensures the room: the result never claims an agent (agent_ctl reads it as a warning).
    assert not module.RESULT_BRIDGE.startswith("OK")
    assert module.RESULT_BRIDGE.startswith("room ensured by the bridge (agent presence not checked")
    assert "[OK]" not in out and "agent is in the room was not checked" in out


def test_bridge_fallback_still_prints_exactly_one_result_line(module, monkeypatch, tmp_path, capsys):
    _bridge(monkeypatch, tmp_path, 503)
    _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]]))
    module.main()
    assert _results(capsys.readouterr().out) == [f"[ROOM] Result: {module.RESULT_AGENT}"]


@pytest.mark.parametrize("missing", ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"])
def test_a_missing_livekit_setting_prints_its_result_line_and_exits_1(module, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing)
    made = _no_livekit(monkeypatch)
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == 1 and not made
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_NO_SETTINGS}"]
    assert module.RESULT_NO_SETTINGS.startswith("UNKNOWN")
    _assert_clean(out)


def test_a_failed_list_rooms_prints_its_result_line_and_exits_1(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]]))
    fake.room_svc.list_rooms = AsyncMock(side_effect=RuntimeError(f"503 key {FAKE_KEY} secret {FAKE_SECRET}"))
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == 1
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_LIST_ROOMS_FAILED}"]
    assert "[ERROR] list_rooms failed: 503 key *** secret ***" in out
    _assert_clean(out)
    _never_cut(fake)
    assert fake.count("create_dispatch") == 0
    fake.aclose.assert_awaited()


def test_an_unexpected_error_still_prints_one_result_line_and_exits_1(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]]))

    async def broken(*_args: Any) -> str:
        raise ValueError(f"bug with {FAKE_SECRET}")

    monkeypatch.setattr(module, "_ensure_agent", broken)
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == 1
    out = capsys.readouterr().out
    assert _results(out) == ["[ROOM] Result: UNKNOWN: ensure_room.py stopped on an unexpected ValueError; "
                             "check the room"]
    _assert_clean(out)
    fake.aclose.assert_awaited()


def test_ctrl_c_still_prints_one_result_line_and_exits_130(module, monkeypatch, capsys):
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]]))

    async def interrupted(*_args: Any) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(module, "_ensure_agent", interrupted)
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == module.EXIT_INTERRUPTED == 130
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_INTERRUPTED}"]
    assert module.RESULT_INTERRUPTED.startswith("UNKNOWN:")
    _assert_clean(out)
    fake.aclose.assert_awaited()


def test_ctrl_c_after_the_result_line_adds_no_second_one(module, monkeypatch, capsys):
    """The run already printed its result (here the vps-mode no-op) when Ctrl+C arrives: still one line."""
    monkeypatch.setenv("JARVIS_BRAIN", "vps")

    def late_interrupt(text: str) -> None:
        original(text)
        if not calls:  # Ctrl+C once, right after the first result line
            calls.append(text)
            raise KeyboardInterrupt

    calls: list[str] = []
    original = module.print_result
    monkeypatch.setattr(module, "print_result", late_interrupt)
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == 130
    assert _results(capsys.readouterr().out) == [f"[ROOM] Result: {module.RESULT_VPS_MODE}"]


@pytest.mark.parametrize("scenario", ["absent", "present", "no_agent_person"])
def test_the_livekit_api_path_prints_exactly_one_result_line_and_exits_0(module, monkeypatch, capsys, scenario):
    """The absent-room and present-room flows through main(): unchanged, one result line each, no exit 1."""
    if scenario == "absent":
        fake = _install(monkeypatch, FakeLiveKit(exists=False))
        expected = module.RESULT_CREATED
    elif scenario == "present":
        fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]],
                                                 dispatches=[_dispatch(_job("AJ_new"))]))
        expected = module.RESULT_AGENT
    else:
        fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                                 dispatches=[_dispatch(_old_job())]))
        expected = module.RESULT_NO_AGENT
    module.main()
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {expected}"]
    assert fake.count("delete_room") == 0


def test_print_result_prints_once_per_run(module, capsys):
    module.print_result("OK, first")
    module.print_result("NO AGENT, second")
    assert _results(capsys.readouterr().out) == ["[ROOM] Result: OK, first"]


def test_the_docstring_states_the_exit_codes_as_built(module):
    doc = module.__doc__ or ""
    assert "exactly one \"[ROOM] Result: ...\" line, on every exit path" in doc
    assert "- exit 0:" in doc and "- exit 1:" in doc and "- exit 130:" in doc
    assert "So every outcome below exits 0" not in doc  # the old, untrue statement
    flat = " ".join(doc.split())
    # An error after the result line exits 1 with that (possibly OK) line kept, and the bridge's 200 is no
    # claim that an agent is in the room.
    assert "An unexpected error raised AFTER the result line was printed" in flat and "also exits 1" in flat
    assert "room ensured by the bridge (agent presence not checked)" in flat
    assert "OK, room ensured by the bridge" not in flat


def test_an_error_after_the_result_line_keeps_that_line_and_exits_1(module, monkeypatch, capsys):
    """The present-room flow prints its OK result, then closing the LiveKit client fails: still exactly that one
    result line, and exit 1 (as the docstring says)."""
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]],
                                             dispatches=[_dispatch(_job("AJ_new"))]))
    fake.aclose = AsyncMock(side_effect=OSError(f"socket closed {FAKE_SECRET}"))
    with pytest.raises(SystemExit) as info:
        module.main()
    assert info.value.code == module.EXIT_FAILED == 1
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_AGENT}"]
    assert "[ERROR] ensure_room.py stopped on an unexpected OSError." in out
    _assert_clean(out)
    fake.aclose.assert_awaited()


# --------------------------------------------------------------------------- C-23: the bridge path (K-53 v9)

BRIDGE_URL = "https://bridge.c23.invalid/vortex"


class FakeResponse:
    def __init__(self, status: int, data: Any = None, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._data = data
        self.headers = headers or {}

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("not JSON")  # an nginx error page
        return self._data


def _answer(result: str, *, in_room: bool = False, current: bool | None = False, paused: bool = False,
            voice_ready: bool | None = None) -> dict[str, Any]:
    return {"room": ROOM, "room_sid": "RM_c23", "brain": "pc", "mode": "always", "paused": paused,
            "target_agent_name": "", "result": result, "agent_in_room": in_room,
            "voice_ready": in_room if voice_ready is None else voice_ready, "agent_current_worker": current,
            "dispatch_id": None, "retry_in_s": None, "created": {"room": False, "dispatch": False}}


@pytest.fixture
def bridge(module, monkeypatch):
    """The signed /livekit/ensure route configured (throwaway values), its headers faked, httpx.post scripted and
    the bridge path's clock fake (no real sleep). answers: what each call returns (the last one repeats); an
    Exception is raised instead."""
    import httpx  # pylint: disable=import-outside-toplevel

    monkeypatch.setenv("AGENT_TOKEN", "c23-test-agent-token")
    monkeypatch.setenv("BRIDGE_HMAC_SECRET", "c23-test-hmac-secret")
    monkeypatch.setenv("UI_BRIDGE_URL", BRIDGE_URL + "/")
    state = SimpleNamespace(answers=[], posts=[], signed=[], now=1000.0, slept=[])

    def signed(body: bytes) -> dict[str, str]:
        state.signed.append(body)
        return {"X-Agent-Token": "fake", "X-Agent-Nonce": f"{len(state.signed):032x}"}

    def post(url: str, **kwargs: Any) -> Any:
        state.posts.append((url, kwargs))
        answer = state.answers.pop(0) if len(state.answers) > 1 else state.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def sleep(seconds: float) -> None:
        state.slept.append(seconds)
        state.now += seconds

    monkeypatch.setattr(module, "_signed_headers", signed)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(module, "_sleep", sleep)
    monkeypatch.setattr(module, "_monotonic", lambda: state.now)
    monkeypatch.setattr(module, "current_worker", lambda: module.Worker(time.time() - 60, WORKER_ID, ""))
    return state


def _bodies(state: Any) -> list[dict[str, Any]]:
    return [json.loads(kwargs["content"]) for _url, kwargs in state.posts]


def test_a_bridge_answer_with_the_current_workers_agent_is_ok_without_livekit(module, monkeypatch, bridge, capsys):
    bridge.answers = [FakeResponse(200, _answer("present", in_room=True, current=True))]
    made = _no_livekit(monkeypatch)
    module.main(["--reason", "agent_ctl_restart"])
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_AGENT}"] and not made
    assert module.RESULT_BRIDGE_AGENT.startswith("OK")
    [(url, kwargs)] = bridge.posts
    assert url == BRIDGE_URL + "/livekit/ensure"
    # the exact signed bytes are the body sent: {reason, agent_name, worker_id}
    assert bridge.signed == [kwargs["content"]]
    assert json.loads(kwargs["content"]) == {"reason": "agent_ctl_restart", "agent_name": "", "worker_id": WORKER_ID}
    assert kwargs["headers"]["X-Agent-Token"] == "fake"
    _assert_clean(out)  # the worker id is sent, never printed
    assert "[ROOM] Bridge: result=present agent_in_room=yes voice_ready=yes current_worker=yes" in out


def test_the_signed_body_carries_the_registered_agent_name(module, monkeypatch, bridge):
    monkeypatch.setattr(module, "current_worker", lambda: module.Worker(None, "", "jarvis-pc"))
    bridge.answers = [FakeResponse(200, _answer("present", in_room=True, current=None))]
    _no_livekit(monkeypatch)
    module.main()
    assert _bodies(bridge) == [{"reason": "manual", "agent_name": "jarvis-pc"}]  # no worker id known: none sent


def test_real_signed_headers_verify_on_the_bridge_side(module, monkeypatch, bridge):
    """The real jarvis_bridge.signed_headers over the exact body: the bridge's C-NOTIFY-1 check accepts them."""
    from services.utils import jarvis_bridge, jarvis_security  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(jarvis_bridge.config, "agent_token", "c23-test-agent-token")
    monkeypatch.setattr(jarvis_bridge.config, "bridge_hmac_secret", "c23-test-hmac-secret")
    monkeypatch.setattr(jarvis_bridge.config, "env", "development")
    monkeypatch.setattr(module, "_signed_headers", jarvis_bridge.signed_headers)
    bridge.answers = [FakeResponse(200, _answer("present", in_room=True, current=True))]
    _no_livekit(monkeypatch)
    module.main()
    [(_url, kwargs)] = bridge.posts
    headers, raw = kwargs["headers"], kwargs["content"]
    assert headers["X-Agent-Token"] == "c23-test-agent-token"
    assert jarvis_security.verify_notify(headers["X-Agent-Timestamp"], headers["X-Agent-Nonce"], raw,
                                         headers["X-Agent-Signature"])


def test_paused_by_the_owner_is_reported_at_once(module, monkeypatch, bridge, capsys):
    bridge.answers = [FakeResponse(200, _answer("paused", paused=True))]
    made = _no_livekit(monkeypatch)
    module.main(["--reason", "watchdog"])
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_PAUSED}"] and not made
    assert len(bridge.posts) == 1 and not bridge.slept
    assert module.RESULT_PAUSED.startswith("PAUSED")


def test_the_run_follows_the_bridge_until_an_agent_of_the_current_worker(module, monkeypatch, bridge, capsys):
    """A restart: the old worker's ghost is still listed (agent_in_room, but not the current worker's), then the
    bridge dispatches and the new agent arrives. Every follow-up call is reason 'wait' with fresh headers."""
    bridge.answers = [FakeResponse(200, _answer("present", in_room=True, current=False)),
                      FakeResponse(200, _answer("confirming")),
                      FakeResponse(200, _answer("dispatched")),
                      FakeResponse(200, _answer("present", in_room=True, current=True))]
    made = _no_livekit(monkeypatch)
    module.main(["--reason", "agent_ctl_restart"])
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_AGENT}"] and not made
    assert [body["reason"] for body in _bodies(bridge)] == ["agent_ctl_restart", "wait", "wait", "wait"]
    assert len(set(bridge.signed)) == 2 and len(bridge.signed) == 4  # new headers for every call
    assert bridge.slept == [module.BRIDGE_POLL_S] * 3
    assert "[ROOM] Bridge: result=dispatched" in out


def test_after_ninety_seconds_without_an_agent_the_run_says_no_agent(module, monkeypatch, bridge, capsys):
    bridge.answers = [FakeResponse(200, _answer("in_flight"))]
    made = _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    assert _results(out) == ["[ROOM] Result: " + module.RESULT_BRIDGE_NO_AGENT.format(result="in_flight")] and not made
    assert sum(bridge.slept) == pytest.approx(module.BRIDGE_WAIT_S)
    assert module.RESULT_BRIDGE_NO_AGENT.startswith("NO AGENT")


@pytest.mark.parametrize("answer", ["observe", "disabled", 404, "html502", "html503", "html504", "refused",
                                    "livekit_unavailable"])
def test_a_bridge_that_does_not_reconcile_falls_back_to_the_livekit_path(module, monkeypatch, bridge, capsys,
                                                                         answer):
    import httpx  # pylint: disable=import-outside-toplevel

    if answer in ("observe", "disabled"):
        bridge.answers = [FakeResponse(200, _answer(answer))]
    elif answer == 404:
        bridge.answers = [FakeResponse(404, {"detail": "Not Found"})]  # a bridge before C-23
    elif answer == "refused":
        bridge.answers = [httpx.ConnectError("connection refused")]
    elif answer == "livekit_unavailable":  # LS-3: the bridge could not use LiveKit, so it dispatched nothing
        bridge.answers = [FakeResponse(503, {"error": "livekit_unavailable"})]
    else:
        bridge.answers = [FakeResponse(int(answer[4:]))]  # nginx's own page: no JSON body
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human(), _new_agent()]]))
    module.main()
    out = capsys.readouterr().out
    assert fake.instances == 1 and len(bridge.posts) == 1
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_AGENT}"]
    assert "using the LiveKit API" in out
    _never_cut(fake)


@pytest.mark.parametrize("status, error", [(401, "auth_required"), (403, "forbidden"), (429, "rate_limited"),
                                           (400, "bad_agent_name"), (503, "link_unconfigured")])
def test_a_refusal_changes_nothing(module, monkeypatch, bridge, capsys, status, error):
    bridge.answers = [FakeResponse(status, {"error": error})]
    made = _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    expected = module.RESULT_BRIDGE_REFUSED.format(route="/livekit/ensure", code=status, error=error)
    assert _results(out) == [f"[ROOM] Result: {expected}"] and not made
    assert expected.startswith("UNKNOWN") and "nothing was changed" in expected
    assert len(bridge.posts) == (2 if status == 429 else 1)  # a 429 is asked once more, then it is a refusal


def test_a_429_is_asked_once_more_after_its_retry_after(module, monkeypatch, bridge, capsys):
    """SEC-6: a busy bridge (429) is asked again after its Retry-After (at most 5 s), with fresh headers."""
    bridge.answers = [FakeResponse(429, {"error": "rate_limited"}, {"Retry-After": "2"}),
                      FakeResponse(200, _answer("present", in_room=True, current=True))]
    made = _no_livekit(monkeypatch)
    module.main(["--reason", "watchdog"])
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_AGENT}"] and not made
    assert bridge.slept == [2.0] and len(bridge.posts) == 2 and len(bridge.signed) == 2  # signed anew
    assert [body["reason"] for body in _bodies(bridge)] == ["watchdog", "watchdog"]


def test_a_long_retry_after_is_capped(module, monkeypatch, bridge, capsys):
    bridge.answers = [FakeResponse(429, {"error": "rate_limited"}, {"Retry-After": "3600"})]
    made = _no_livekit(monkeypatch)
    module.main()
    assert bridge.slept == [module.BRIDGE_RETRY_MAX_S] and not made  # never longer than the cap
    expected = module.RESULT_BRIDGE_REFUSED.format(route="/livekit/ensure", code=429, error="rate_limited")
    assert _results(capsys.readouterr().out) == [f"[ROOM] Result: {expected}"]


def test_paused_holds_in_observe_mode_too(module, monkeypatch, bridge, capsys):
    """SEC-2: the owner's pause (here set with the admin CLI while the bridge observes) stops this run's LiveKit
    path too: nothing is dispatched while JARVIS is paused."""
    observing = _answer("observe", paused=True)
    observing["mode"] = "observe"
    bridge.answers = [FakeResponse(200, observing)]
    made = _no_livekit(monkeypatch)
    module.main(["--reason", "watchdog"])
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_PAUSED}"] and not made
    assert "using the LiveKit API" not in out


def test_a_bridge_timeout_changes_nothing(module, monkeypatch, bridge, capsys):
    import httpx  # pylint: disable=import-outside-toplevel

    bridge.answers = [httpx.ReadTimeout("timed out")]
    made = _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_TIMEOUT}"] and not made


def test_a_200_without_agent_fields_claims_no_agent(module, monkeypatch, bridge, capsys):
    bridge.answers = [FakeResponse(200, {"status": "ok"})]
    made = _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_UNCHECKED}"] and not made
    assert module.RESULT_BRIDGE_UNCHECKED.startswith("room ensured by the bridge (agent presence not checked")


def test_without_the_hmac_keys_the_pc_signed_route_is_used(module, monkeypatch, tmp_path, capsys):
    """No AGENT_TOKEN / BRIDGE_HMAC_SECRET: the PC-signed /vortex/desktop/ensure with an empty body; its answer
    is followed like the signed route's (no worker id: agent_in_room decides)."""
    posted = _bridge(monkeypatch, tmp_path, 200)
    import httpx  # pylint: disable=import-outside-toplevel

    answers = [FakeResponse(200, _answer("dispatched", current=None)),
               FakeResponse(200, _answer("present", in_room=True, current=None))]
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> Any:
        posted.append(url)
        calls.append(kwargs)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(module, "_sleep", lambda _s: None)
    made = _no_livekit(monkeypatch)
    module.main()
    out = capsys.readouterr().out
    assert posted == ["https://bridge.wave-h.invalid/vortex/desktop/ensure"] * 2 and not made
    assert all(kwargs["content"] == b"" for kwargs in calls)
    assert _results(out) == [f"[ROOM] Result: {module.RESULT_BRIDGE_AGENT}"]


def test_bad_arguments_print_one_result_line_and_exit_2(module, monkeypatch, capsys):
    made = _no_livekit(monkeypatch)
    with pytest.raises(SystemExit) as info:
        module.main(["--reason", "because"])
    assert info.value.code == module.EXIT_USAGE == 2 and not made
    assert _results(capsys.readouterr().out) == [f"[ROOM] Result: {module.RESULT_BAD_ARGS}"]


def test_the_livekit_path_uses_the_workers_registered_name(module, monkeypatch, capsys):
    """C-23 (K-53 v9): the LiveKit API path creates the room and dispatches with the name the worker registered
    (jarvis-pc from phase 3), never only ''."""
    monkeypatch.setattr(module, "current_worker", lambda: module.Worker(time.time() - 60, WORKER_ID, "jarvis-pc"))
    fake = _install(monkeypatch, FakeLiveKit(exists=False))
    await_run = asyncio.run(module.ensure_room())
    assert await_run is None
    request = fake.room_svc.create_room.await_args.args[0]
    assert [d.agent_name for d in request.agents] == ["jarvis-pc"]

    module._result_printed = False  # a second run, as in a fresh process
    fake = _install(monkeypatch, FakeLiveKit(exists=True, participants=[[_human()]],
                                             dispatches=[_dispatch(_old_job())],
                                             after_dispatch=[_human(), _new_agent()]))
    asyncio.run(module.ensure_room())
    request = fake.dispatch_svc.create_dispatch.await_args.args[0]
    assert (request.agent_name, request.room) == ("jarvis-pc", ROOM)
    _never_cut(fake)
    capsys.readouterr()


def test_every_new_result_is_judged_by_agent_ctl(module):
    """The bridge results keep agent_ctl's rule: only an 'OK' result is no warning (PAUSED is one, C-23)."""
    spec = importlib.util.spec_from_file_location("c23_agent_ctl", ROOT / "scripts" / "agent_ctl.py")
    agent_ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_ctl)
    for text in (module.RESULT_BRIDGE_AGENT, module.RESULT_PAUSED, module.RESULT_BRIDGE_NO_AGENT,
                 module.RESULT_BRIDGE_REFUSED, module.RESULT_BRIDGE_TIMEOUT, module.RESULT_BRIDGE_UNCHECKED,
                 module.RESULT_BAD_ARGS):
        assert agent_ctl.room_result_warning(text) is (not text.startswith("OK")), text
