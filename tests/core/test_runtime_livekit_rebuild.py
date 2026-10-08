"""
tests/core/test_runtime_livekit_rebuild.py — C-33 step B review: JarvisRuntime and agent_runner._release_session
against REAL livekit-agents 1.4.6 objects (Agent, AgentSession, AgentActivity; no room, no model).

The job keeps ONE assistant (a livekit Agent) for every session it builds, so two livekit behaviours matter:
- a failed start leaves the agent bound to a dead activity (AgentSession.aclose() is a no-op for a session that
  never started), so the next attempt fails with 'an activity is already running' unless it is released;
- a closing session's activity clears agent._activity only when its drain (in-flight tools included) has ended, so
  a rebuild that starts before that has its new activity nulled later (a silent JARVIS).
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import types

from livekit.agents import Agent, AgentSession, llm
from livekit.agents.voice import agent_activity

from src.core import agent_runner as ar
from src.core.jarvis_runtime import END_SESSION_ERROR, JarvisRuntime


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _runtime(agent: Agent, **kwargs) -> JarvisRuntime:
    """The entrypoint's wiring in miniature: a fresh AgentSession per start, always the SAME assistant, and the
    close_session hook the entrypoint passes (no room here, so its room steps find nothing)."""
    return JarvisRuntime(types.SimpleNamespace(), session_factory=AgentSession,
                         start_session=lambda session: session.start(agent),
                         close_session=lambda session: ar._release_session(session, agent, None),
                         sleep=_no_sleep, **kwargs)


def _kill_realtime(session: AgentSession) -> None:
    """What the realtime plugin does when the Gemini session dies: an unrecoverable error on the activity."""
    session._activity._on_error(llm.RealtimeModelError(timestamp=0.0, label="test", error=Exception("gemini died"),
                                                       recoverable=False))


def _hold_the_drain_of(monkeypatch, old: dict, release: asyncio.Event) -> None:
    """The old session's drain waits for `release`, like a function tool still running in it."""
    real_drain = agent_activity.AgentActivity.drain

    async def held_drain(self):
        if self._session is old.get("session"):
            await release.wait()
        return await real_drain(self)

    monkeypatch.setattr(agent_activity.AgentActivity, "drain", held_drain)


async def _until(condition, tries: int = 300) -> bool:
    for _ in range(tries):
        if condition():
            return True
        await asyncio.sleep(0.01)
    return condition()


async def test_a_failed_start_leaves_the_shared_assistant_free_for_the_next_attempt(monkeypatch):
    real_start_session = agent_activity.AgentActivity._start_session
    calls = {"n": 0}

    async def flaky(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure after agent._activity was set")
        return await real_start_session(self)

    monkeypatch.setattr(agent_activity.AgentActivity, "_start_session", flaky)
    agent = Agent(instructions="t")
    rt = _runtime(agent)
    assert await rt.start() is True  # attempt 2 started the same assistant
    assert calls["n"] == 2 and agent._activity._session is rt.session
    await rt.aclose("test")


async def test_a_rebuild_waits_for_the_old_drain_and_never_nulls_the_new_activity(monkeypatch):
    old: dict = {}
    release = asyncio.Event()
    _hold_the_drain_of(monkeypatch, old, release)
    agent = Agent(instructions="t")
    rt = _runtime(agent)
    assert await rt.start()
    old["session"] = rt.session
    _kill_realtime(rt.session)
    await asyncio.sleep(0.1)
    assert rt.session is old["session"] and not rt._restarts  # no rebuild, and no cap burnt, while it drains
    release.set()
    assert await _until(lambda: rt.session is not old["session"])
    assert not rt.closed.is_set()
    await asyncio.sleep(0.1)  # anything late from the old session has run by now
    assert agent._get_activity_or_raise()._session is rt.session  # the live session still owns the assistant
    await rt.aclose("test")


async def test_an_old_session_that_never_closes_ends_the_job(monkeypatch):
    old: dict = {}
    release = asyncio.Event()
    _hold_the_drain_of(monkeypatch, old, release)
    agent = Agent(instructions="t")
    rt = _runtime(agent, error_close_wait_s=0.1, session_close_timeout_s=0.05)
    assert await rt.start()
    old["session"] = rt.session
    _kill_realtime(rt.session)
    assert await asyncio.wait_for(rt.wait_closed(), 3) == END_SESSION_ERROR
    assert rt.session is old["session"]  # never rebuilt on top of the draining session
    await rt.aclose(rt.reason)
    release.set()  # let livekit's own close task finish
    await _until(lambda: agent._activity is None)
