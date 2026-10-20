"""
tests/core/test_session_policy.py - C-114: 24/7 voice session safety (src/core/session_policy.py and its wiring in
src/core/agent_runner.py and src/core/agent_core.py).

Covered: the context window compression (env bounds, the window always below the trigger) on the job's RealtimeModel;
the real truncation (every 20 items to the last 40, at a quiet point of the agent's live session, never two at once,
never behind the plugin's reconnect, whose private session lock is pinned on the real plugin class; remove-only on
the realtime side, so nothing reaches Gemini again, checked with the pinned plugin's own update_chat_ctx and livekit's
compute_chat_ctx_diff, a clipboard tip and a barge-in included; the agent's own copy set the way livekit sets it,
without the push; the re-seeded turns cut whole with their memory note or kept whole, in both copies) and the one
plugin WARNING it causes being filtered; the re-seed from memory (filtering, only when the newest saved turn is at
most 60 minutes old and only the 30 minutes before it, after a '[MEMORY]' note, in the google turn format; no
unanswered user turn at the end, ids, no second seed, never saved again, no text in the logs); the instructions
refresh on a real date or city change only (the PC's location loop, the VPS brain's cached device location; a city
counts after 5 agreeing readings, a city that flaps never refreshes, a city change refreshes at most once an hour
while a new day refreshes at once); the idle close (a linked participant, any other person the bridge counts, or
conversation resets it, in both brains; agents and probes never; speaking, a tool call or a background job holds it;
the 24 h recycle, people in the room or not; 0 turns both off; off by default on the PC brain; never in console mode)
with jarvis.session 'idle' and the job end 'idle';
jarvis.session_ts in the same attribute write as every jarvis.session (the bridge ignores it); probe jobs skip the
re-seed and the policy; and the C-33 error path (a dead session is rebuilt in place or the job ends as
'session_error', never with a mute agent left in the room).

Nothing real runs: LiveKit, Gemini, the bridge and loops are stand-ins (the truncation tests run livekit's own
Agent/AgentActivity.update_chat_ctx and the google plugin's RealtimeSession.update_chat_ctx on stand-ins without a
connection); one test uses a real encrypted memory file in a temporary folder.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import functools
import logging
import time
import types
import weakref
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit import rtc
from livekit.agents import NOT_GIVEN, Agent, llm
from livekit.agents.llm.utils import compute_chat_ctx_diff
from livekit.agents.voice.agent_activity import AgentActivity

from services.utils import jarvis_jobs, jarvis_location
from src.core import session_policy as sp

RESEED_DEFAULT = sp.RESEED_ENABLED  # the module's own value, before any fixture


@pytest.fixture(autouse=True)
def _reseed_switched_on(monkeypatch):
    """The re-seed is off in production since 20 Oct (sp.RESEED_ENABLED); these tests keep its rules checked."""
    monkeypatch.setattr(sp, "RESEED_ENABLED", True)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REAL_SLEEP = asyncio.sleep  # the entry fixture swaps asyncio.sleep for one that only yields


class _Records(logging.Handler):
    """The session policy logger's records (setup_logger loggers do not propagate to caplog)."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [record.getMessage() for record in self.records]


@pytest.fixture
def records():
    handler = _Records()
    sp.logger.addHandler(handler)
    yield handler
    sp.logger.removeHandler(handler)


@pytest.fixture
def debug_records(records):
    """records with the policy logger at DEBUG for the test (setup_logger leaves it at INFO, so no DEBUG record)."""
    level = sp.logger.level
    sp.logger.setLevel(logging.DEBUG)
    yield records
    sp.logger.setLevel(level)


# ---------------------------------------------------------------- 1. context window compression


def test_the_compression_starts_at_24000_tokens_and_keeps_16000(monkeypatch):
    from google.genai import types as genai_types

    monkeypatch.delenv(sp.CTX_TRIGGER_ENV, raising=False)
    config = sp.compression_config()
    assert isinstance(config, genai_types.ContextWindowCompressionConfig)
    assert config.trigger_tokens == 24000
    assert isinstance(config.sliding_window, genai_types.SlidingWindow)
    assert config.sliding_window.target_tokens == 16000


@pytest.mark.parametrize(("raw", "trigger"), [
    ("40000", 40000), (" 30000 ", 30000), ("12000", 12000), ("", 24000), ("lots", 24000), ("24000.5", 24000),
    ("1000", sp.CTX_TRIGGER_MIN), ("-5", sp.CTX_TRIGGER_MIN), ("9999999", sp.CTX_TRIGGER_MAX),
])
def test_the_trigger_comes_from_the_env_within_bounds_and_the_window_stays_below_it(monkeypatch, raw, trigger):
    monkeypatch.setenv(sp.CTX_TRIGGER_ENV, raw)
    config = sp.compression_config()
    assert config.trigger_tokens == trigger
    assert 0 < config.sliding_window.target_tokens < config.trigger_tokens
    assert config.sliding_window.target_tokens <= sp.CTX_TARGET_TOKENS


def test_a_bad_trigger_names_the_variable_never_its_value(monkeypatch, records):
    monkeypatch.setenv(sp.CTX_TRIGGER_ENV, "zebra-tokens")
    assert sp.ctx_trigger_tokens() == sp.CTX_TRIGGER_DEFAULT
    monkeypatch.setenv(sp.CTX_TRIGGER_ENV, "987654321")
    assert sp.ctx_trigger_tokens() == sp.CTX_TRIGGER_MAX
    warnings = [m for r, m in zip(records.records, records.messages) if r.levelno == logging.WARNING]
    assert len(warnings) == 2 and all(sp.CTX_TRIGGER_ENV in m for m in warnings)
    assert not any("zebra" in m or "987654321" in m for m in records.messages)


def test_the_jobs_realtime_model_is_built_with_the_compression_and_the_resume_guard_right_after():
    """Source check like tests/unit/test_gemini_resume_guard.py (importing the runner's model build needs LiveKit):
    the compression is an argument of the one RealtimeModel build and the guard still follows within 1200 chars."""
    source = (PROJECT_ROOT / "src" / "core" / "agent_runner.py").read_text(encoding="utf-8")
    build = source.index("google.realtime.RealtimeModel(")
    compression = source.index("context_window_compression=session_policy.compression_config()")
    voice = source.index('voice=job_settings.voice or persona_voice("jarvis")')  # C-54: the saved voice first
    arm = source.index("gemini_resume_guard.guard_realtime_model(llm_model)")
    assert build < voice < compression < arm < build + 1200
    assert source.count("google.realtime.RealtimeModel(") == 1  # one model per job, in every mode


# ---------------------------------------------------------------- 2. real truncation


def _msg(item_id: str, role: str = "user", text: str | None = None) -> llm.ChatMessage:
    return llm.ChatMessage(role=role, content=[text or f"text of {item_id}"], id=item_id)


def _turns(start: int, stop: int) -> list[llm.ChatMessage]:
    """Conversation items item-<start> .. item-<stop - 1>, user and assistant in turn."""
    return [_msg(f"item-{i}", "user" if i % 2 == 0 else "assistant", f"turn {i}") for i in range(start, stop)]


def _ids(items: Iterable[Any]) -> list[str]:
    return [item.id for item in items]


def _plugin_update_chat_ctx() -> Callable[..., Any]:
    """livekit-plugins-google 1.4.6's own RealtimeSession.update_chat_ctx (pinned, K-65)."""
    from livekit.plugins.google.realtime import realtime_api

    return realtime_api.RealtimeSession.update_chat_ctx


class _RtSession:
    """
    The google plugin's RealtimeSession as the cut meets it: its own chat context (what Gemini was sent and what a
    reconnect replays; its chat_ctx property returns a copy, as the plugin's does) and the plugin's REAL
    update_chat_ctx, run on this stand-in: no Gemini connection, and every client event it would send to Gemini lands
    in `sent`. Each update also records livekit's real compute_chat_ctx_diff between the copy it replaces and what it
    got (after the plugin's own filter); `gate` holds an update open.
    """

    def __init__(self, items: Iterable[Any] = ()) -> None:
        self._chat_ctx = llm.ChatContext(list(items))
        self._session_lock = asyncio.Lock()
        self._active_session = object()  # connected: the plugin diffs, sends what is new and keeps the rest
        self._opts = types.SimpleNamespace(model="gemini-c114-test", vertexai=False,
                                           tool_response_scheduling=NOT_GIVEN)
        self.sent: list[Any] = []
        self.diffs: list[Any] = []
        self.gate: asyncio.Event | None = None
        self.in_update = 0
        self.max_in_update = 0

    @property
    def chat_ctx(self) -> llm.ChatContext:
        return self._chat_ctx.copy()

    def _send_client_event(self, event: Any) -> None:
        self.sent.append(event)

    async def update_chat_ctx(self, chat_ctx: llm.ChatContext) -> None:
        self.in_update += 1
        self.max_in_update = max(self.max_in_update, self.in_update)
        try:
            if self.gate is not None:
                await self.gate.wait()
            seen = chat_ctx.copy(exclude_handoff=True, exclude_instructions=True, exclude_empty_message=True,
                                 exclude_config_update=True)  # what the plugin diffs against its own copy
            self.diffs.append(compute_chat_ctx_diff(self._chat_ctx, seen))
            await _plugin_update_chat_ctx()(self, chat_ctx)
        finally:
            self.in_update -= 1

    def ids(self) -> list[str]:
        return _ids(self._chat_ctx.items)


class _TruncAgent:
    """
    What the cut touches on a BrainAssistant: its own copy (a real livekit ChatContext; chat_ctx returns it, as
    BrainAssistant's does), its live session (session) and livekit's own Agent.update_chat_ctx. live (the default) adds
    what a running job has: an activity (a stand-in) whose realtime session is an _RtSession and whose update_chat_ctx
    is livekit-agents 1.4.6's own AgentActivity.update_chat_ctx, so agent.update_chat_ctx(ctx) pushes the agent's copy
    to the plugin exactly as in a job (the path a cut must not take). add() appends to both copies: the plugin records
    every turn as well.
    """

    update_chat_ctx = Agent.update_chat_ctx  # livekit's own: with an activity it pushes through AgentActivity

    def __init__(self, n: int = 0, *, live: bool = True, tools: Iterable[Any] = ()) -> None:
        self._tools = list(tools)
        self._chat_ctx = llm.ChatContext.empty()
        self._activity: Any = None
        self.instructions = "c114 instructions"
        self.session: Any = None
        self.rt: _RtSession | None = None
        if live:
            self.rt = _RtSession()
            activity = types.SimpleNamespace(_agent=self, _rt_session=self.rt, realtime_llm_session=self.rt,
                                             tools=list(self._tools))
            activity.update_chat_ctx = functools.partial(AgentActivity.update_chat_ctx, activity)
            self._activity = activity
        self._next = 0
        self.add(n)

    @property
    def chat_ctx(self) -> llm.ChatContext:
        return self._chat_ctx

    def add(self, n: int) -> None:
        for item in _turns(self._next, self._next + n):
            self._chat_ctx.items.append(item)
            if self.rt is not None:
                self.rt._chat_ctx.items.append(item)
        self._next += n

    def ids(self) -> list[str]:
        return _ids(self._chat_ctx.items)


async def _settle(n: int = 10) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_every_20_items_both_copies_are_cut_to_their_last_40(records):
    agent = _TruncAgent(70)
    truncator = sp.ChatTruncator(agent)
    for _ in range(19):
        truncator.note_item()
    await _settle()
    assert agent.rt.diffs == [] and len(agent.ids()) == 70 and not truncator.running  # 19 items: nothing yet
    truncator.note_item()
    await _settle()
    assert agent.ids() == agent.rt.ids() == [f"item-{i}" for i in range(30, 70)]
    (diff,) = agent.rt.diffs
    assert diff.to_create == [] and diff.to_remove == [f"item-{i}" for i in range(30)]  # removals only
    assert agent.rt.sent == []  # nothing went to Gemini
    assert "SESSION_POLICY chat context cut 70 -> 40 items (realtime 70 -> 40, remove only)" in records.messages

    agent.add(20)  # 20 more conversation items
    for _ in range(20):
        truncator.note_item()
    await _settle()
    assert agent.ids() == agent.rt.ids() == [f"item-{i}" for i in range(50, 90)] and truncator.truncations == 2
    assert agent.rt.sent == []

    between = _TruncAgent(50, live=False)  # between C-33 rebuilds: no session, only the agent's own copy
    assert await sp.ChatTruncator(between).truncate() is True
    assert between.ids() == [f"item-{i}" for i in range(10, 50)]
    assert "SESSION_POLICY chat context cut 50 -> 40 items" in records.messages


async def test_never_two_truncations_at_once():
    agent = _TruncAgent(80)
    agent.rt.gate = asyncio.Event()
    truncator = sp.ChatTruncator(agent)
    for _ in range(20):
        truncator.note_item()
    await _settle()
    assert truncator.running and agent.rt.in_update == 1  # the first cut is held inside the realtime update
    first_task = truncator._task
    for _ in range(25):  # the next 20 items arrive meanwhile: no second task
        truncator.note_item()
    assert truncator._task is first_task
    direct = asyncio.ensure_future(truncator.truncate())  # prune_chat_context's path waits for the lock
    await _settle()
    assert agent.rt.max_in_update == 1
    agent.rt.gate.set()
    assert await asyncio.wait_for(direct, 2) is False  # it waited for the first cut and found nothing left to cut
    await _settle()
    assert not truncator.running and len(agent.rt.diffs) == 1 and len(agent.ids()) == 40
    agent.add(30)
    truncator.note_item()  # the next item schedules the cut that came due while the first one ran
    await _settle()
    assert len(agent.rt.diffs) == 2 and len(agent.ids()) == len(agent.rt.ids()) == 40
    assert agent.rt.max_in_update == 1 and agent.rt.sent == []


async def test_a_cut_waits_for_a_quiet_point():
    agent = _TruncAgent(60)
    busy = {"now": True}
    truncator = sp.ChatTruncator(agent, busy=lambda: busy["now"], quiet_poll_s=0)
    for _ in range(20):
        truncator.note_item()
    await _settle(30)
    assert agent.rt.diffs == [] and len(agent.ids()) == 60 and truncator.running  # the agent speaks or thinks
    busy["now"] = False
    await _settle(30)
    assert len(agent.rt.diffs) == 1 and len(agent.ids()) == len(agent.rt.ids()) == 40


async def test_the_default_quiet_point_is_the_agents_live_session():
    """ChatTruncator(agent) as BrainAssistant builds it: no busy= given, so it reads agent.session (the session
    attach_session got) at every poll. A reply being spoken holds the cut, and so does a speech that is not done
    while the state already says 'listening' (a realtime tool call)."""
    agent = _TruncAgent(60)
    speech = MagicMock()
    speech.done.return_value = False
    agent.session = types.SimpleNamespace(agent_state="speaking", current_speech=speech)
    truncator = sp.ChatTruncator(agent, quiet_poll_s=0)
    for _ in range(20):
        truncator.note_item()
    await _settle(30)
    assert agent.rt.diffs == [] and len(agent.ids()) == 60 and truncator.running  # speaking
    agent.session.agent_state = "listening"
    await _settle(30)
    assert agent.rt.diffs == [] and truncator.running  # listening, but the current speech still runs
    speech.done.return_value = True
    await _settle(30)
    assert len(agent.rt.diffs) == 1 and len(agent.ids()) == 40 and not truncator.running


@pytest.mark.parametrize(("state", "busy"), [
    (dict(agent_state="speaking"), True), (dict(agent_state="thinking"), True), (dict(user_state="speaking"), True),
    (dict(agent_state="listening"), False), (dict(agent_state="idle"), False), ({}, False),
])
def test_the_quiet_point_rule(state, busy):
    assert sp.session_busy(types.SimpleNamespace(**state)) is busy
    assert sp.session_busy(None) is False


def test_a_running_speech_or_tool_call_is_not_a_quiet_point():
    speech = MagicMock()
    speech.done.return_value = False
    assert sp.session_busy(types.SimpleNamespace(agent_state="listening", current_speech=speech)) is True
    speech.done.return_value = True
    assert sp.session_busy(types.SimpleNamespace(agent_state="listening", current_speech=speech)) is False


async def test_nothing_to_cut_changes_nothing_and_a_failed_cut_never_raises(records):
    small = _TruncAgent(30)
    assert await sp.ChatTruncator(small).truncate() is False
    assert small.rt.diffs == [] and len(small.ids()) == 30

    failing = _TruncAgent(50)

    async def gone(_ctx: Any) -> None:
        raise RuntimeError("realtime session gone: secret-ish detail")

    failing.rt.update_chat_ctx = gone
    assert await sp.ChatTruncator(failing).truncate() is True  # the agent's own copy is still cut
    assert len(failing.ids()) == 40 and len(failing.rt.ids()) == 50
    assert "SESSION_POLICY chat truncation failed (realtime copy: RuntimeError)" in records.messages
    assert not any("secret-ish" in message for message in records.messages)
    assert await sp.ChatTruncator(types.SimpleNamespace()).truncate() is False  # a stand-in without a context
    assert await sp.ChatTruncator(MagicMock()).truncate() is False  # a mock assistant: nothing to count


def _clipboard_tip() -> llm.ChatMessage:
    """agent_runner._on_clipboard_detected's item: quoted desktop data, appended to the agent's copy only."""
    from src.core import vision_handler

    return vision_handler.desktop_data_message("CLIPBOARD ERROR DETECTED", "error: ZEBRA failed\ntip: pip install x")


def _barge_in(*, agent_side: bool) -> list[llm.ChatMessage]:
    """A question answered and then interrupted. livekit appends the interrupted reply at once and the user's
    transcript only at the plugin's turn_complete; the plugin adds both at turn_complete, the question first."""
    question = _msg("GI_1", "user", "ZEBRA purana sawal")
    reply = _msg("GR_1", "assistant", "jawab jo beech mein kata")
    return [reply, question] if agent_side else [question, reply]


@pytest.mark.parametrize("case", ["clipboard_tip", "barge_in"])
async def test_a_cut_never_sends_gemini_an_old_turn_again(case):
    """
    C-114 review: the cut went through agent.update_chat_ctx, so the plugin diffed its own copy against the agent's
    (livekit's compute_chat_ctx_diff, an LCS of the ids) and sent every item the agent held that it lacked, or held in
    another order, to Gemini again as a new client turn at the end of its live context: a clipboard tip (agent_runner
    appends it to the agent's copy only) or, after a barge-in, the old question (the agent holds [reply, question],
    the plugin [question, reply]). Now the plugin gets its own copy back, cut: removals only. The agent's own copy is
    cut to its own last 40.
    """
    agent = _TruncAgent(48)
    if case == "clipboard_tip":
        agent._chat_ctx.items.append(_clipboard_tip())
    else:
        agent._chat_ctx.items.extend(_barge_in(agent_side=True))
        agent.rt._chat_ctx.items.extend(_barge_in(agent_side=False))
    agent.add(2)
    agent_items, plugin_items = list(agent.chat_ctx.items), list(agent.rt._chat_ctx.items)
    old_push = llm.ChatContext(agent_items[-40:])  # what agent.update_chat_ctx(the agent's cut copy) handed the plugin
    resent = [("item-47", agent_items[48].id)] if case == "clipboard_tip" else [("GR_1", "GI_1")]
    assert compute_chat_ctx_diff(llm.ChatContext(plugin_items), old_push).to_create == resent  # the old hazard

    assert await sp.ChatTruncator(agent).truncate() is True
    (diff,) = agent.rt.diffs  # the real diff between the plugin's copy and what the cut handed it
    assert diff.to_create == [] and diff.to_remove == _ids(plugin_items[:-40])
    assert agent.rt.sent == []  # no client content and no tool result: Gemini got nothing again
    assert agent.rt.ids() == _ids(plugin_items[-40:])  # the plugin's own last 40
    assert agent.ids() == _ids(agent_items[-40:])  # the agent's own last 40 (the tip, its order, kept)


def _sent_texts(rt: _RtSession) -> list[str]:
    """Every text the plugin sent Gemini as client content."""
    return [part.text for event in rt.sent for turn in (getattr(event, "turns", None) or []) for part in turn.parts]


async def test_a_cut_never_queues_behind_the_plugins_reconnect():
    """
    The pinned plugin holds its session lock across a reconnect's awaits, and every update_chat_ctx waits for it. A
    typed message of that moment (livekit's realtime_reply_task: the plugin's copy read, the user item added,
    update_chat_ctx) then waits with a copy read before the cut: a cut that queued first would be undone by it, and the
    plugin would send every item the cut removed to Gemini as new turns. The cut waits until the lock is free.
    """
    agent = _TruncAgent(50)
    lock = agent.rt._session_lock
    await lock.acquire()  # a reconnect is under way (the old session closing, or the history being sent)
    truncator = sp.ChatTruncator(agent, quiet_poll_s=0)
    for _ in range(20):
        truncator.note_item()
    await _settle(30)
    assert truncator.running and len(agent.ids()) == 50 and agent.rt.in_update == 0  # waiting, not queued
    assert await truncator.truncate() is True and len(agent.ids()) == 40  # a direct cut: the agent's copy only
    assert len(agent.rt.ids()) == 50 and agent.rt.in_update == 0
    typed = agent.rt.chat_ctx.copy()
    typed_item = typed.add_message(role="user", content="[typed] naya sawal", id="typed-1")
    agent._chat_ctx.items.append(typed_item)
    pending = asyncio.ensure_future(agent.rt.update_chat_ctx(typed))
    await _settle()
    lock.release()
    await asyncio.wait_for(pending, 2)
    await _settle(30)
    assert not truncator.running
    assert _sent_texts(agent.rt) == ["[typed] naya sawal"]  # the new message only, never an old turn again
    assert agent.rt.ids() == [f"item-{i}" for i in range(11, 50)] + ["typed-1"]
    assert sp.realtime_lock_busy(agent.rt) is False and sp.realtime_lock_busy(MagicMock()) is False
    assert sp.realtime_lock_busy(None) is False


def test_the_reconnect_guard_reads_the_pinned_plugins_own_session_lock():
    """
    C-114 review: realtime_lock_busy reads the pinned google plugin's private RealtimeSession._session_lock and answers
    False for a session without one, and realtime_session reads livekit-agents' AgentActivity.realtime_llm_session
    through the agent's _activity. Every other test builds its own lock on a stand-in, so an upgrade that renamed
    either would turn the reconnect guard off silently with every test still green: pinned here on the real classes
    (livekit-plugins-google and livekit-agents 1.4.6, K-65), the stdlib lock the isinstance check and the waiter rule
    rely on included.
    """
    import inspect

    from livekit.plugins.google.realtime import realtime_api

    session_class = realtime_api.RealtimeSession
    assert "self._session_lock = asyncio.Lock()" in inspect.getsource(session_class.__init__)
    assert "async with self._session_lock" in inspect.getsource(session_class.update_chat_ctx)
    assert "async with self._session_lock" in inspect.getsource(session_class._main_task)  # the reconnect's replay
    assert realtime_api.asyncio is asyncio and hasattr(asyncio.Lock(), "_waiters")
    assert isinstance(inspect.getattr_static(AgentActivity, "realtime_llm_session"), property)
    assert "self._activity: AgentActivity | None = None" in inspect.getsource(Agent.__init__)


async def _open_app(name: str) -> str:
    """Opens an app (a stand-in tool)."""
    return name


async def test_a_cut_never_leaves_a_function_output_without_its_call():
    """The quiet point keeps a running tool call out of a cut; at the cut's edge livekit's truncate drops a leading
    function call or output, so neither copy keeps an output whose call was cut away."""
    tool = llm.function_tool(_open_app, name="open_app")
    agent = _TruncAgent(9, tools=[tool])
    call = llm.FunctionCall(call_id="call-1", name="open_app", arguments='{"name": "x"}', id="fc-1")
    output = llm.FunctionCallOutput(call_id="call-1", name="open_app", output="x", is_error=False, id="fo-1")
    agent._chat_ctx.items.extend([call, output])
    agent.rt._chat_ctx.items.append(output)  # the plugin gets the output only (AgentActivity, livekit-agents 1.4.6)
    agent.add(39)
    assert await sp.ChatTruncator(agent).truncate() is True
    assert agent.ids() == agent.rt.ids() == [f"item-{i}" for i in range(9, 48)]
    assert agent.rt.diffs[0].to_create == [] and agent.rt.sent == []


@pytest.mark.parametrize("live", [True, False])
async def test_the_agents_copy_is_set_as_livekit_sets_it_and_never_pushed(live):
    """livekit's AgentActivity.update_chat_ctx minus its realtime push: the copy keeps only the function items of the
    activity's tools and, while a realtime session runs, no instructions item; without an activity,
    Agent.update_chat_ctx's filter by the agent's own tools. agent.update_chat_ctx itself is never called."""
    tool = llm.function_tool(_open_app, name="open_app")
    agent = _TruncAgent(0, live=live, tools=[tool])
    agent._chat_ctx.items.append(llm.ChatMessage(role="system", content=["old"], id="lk.agent_task.instructions"))
    agent.add(45)
    agent._chat_ctx.items.extend([
        llm.FunctionCall(call_id="call-2", name="removed_tool", arguments="{}", id="fc-2"),
        llm.FunctionCallOutput(call_id="call-2", name="removed_tool", output="-", is_error=False, id="fo-2")])
    agent.update_chat_ctx = AsyncMock()
    assert await sp.ChatTruncator(agent).truncate() is True
    agent.update_chat_ctx.assert_not_awaited()
    kept = [f"item-{i}" for i in range(7, 45)]  # the last 40 (instructions put back first), unknown tool items out
    assert agent.ids() == (kept if live else ["lk.agent_task.instructions"] + kept)
    if live:
        assert agent.rt.ids() == [f"item-{i}" for i in range(5, 45)] and agent.rt.sent == []


async def test_prune_chat_context_is_a_thin_wrapper_over_the_assistants_truncator(monkeypatch):
    from src.core import agent_core

    monkeypatch.setattr("livekit.agents.Agent.__init__", lambda self, **_kwargs: None)
    monkeypatch.setattr(agent_core.jarvis_id, "get_context", lambda: "identity")
    monkeypatch.setattr(agent_core, "JarvisPluginManager", MagicMock())
    monkeypatch.setattr(agent_core, "MemoryExtractor", MagicMock())
    brain = agent_core.BrainAssistant(chat_ctx=MagicMock(), probe=True)  # probe: no task monitor
    assert isinstance(brain.chat_truncator, sp.ChatTruncator) and brain.chat_truncator.agent is brain
    brain.chat_truncator.truncate = AsyncMock(return_value=True)
    await brain.prune_chat_context()
    brain.chat_truncator.truncate.assert_awaited_once_with()
    import inspect

    turn = inspect.getsource(agent_core.BrainAssistant.on_user_turn_completed)
    assert "prune_chat_context()" not in turn  # the job's truncator cuts in every mode, never a second path


# ---------------------------------------------------------------- 2b. the plugin's WARNING on every cut


def _plugin_record(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(sp.GOOGLE_PLUGIN_LOGGER, level, __file__, 1, message, None, None)


def test_the_filter_drops_exactly_the_truncation_warning():
    sp.install_truncate_warning_filter()
    sp.install_truncate_warning_filter()  # once per process, whoever calls
    plugin_logger = logging.getLogger(sp.GOOGLE_PLUGIN_LOGGER)
    assert sum(isinstance(f, sp._TruncateWarningFilter) for f in plugin_logger.filters) == 1
    assert not plugin_logger.filter(_plugin_record(logging.WARNING, sp.TRUNCATE_WARNING))
    assert plugin_logger.filter(_plugin_record(logging.WARNING, "Gemini Realtime API connection failed, retrying"))
    assert plugin_logger.filter(_plugin_record(logging.ERROR, sp.TRUNCATE_WARNING))  # another level passes
    assert plugin_logger.filter(_plugin_record(logging.WARNING, sp.TRUNCATE_WARNING + " (3 items)"))
    assert plugin_logger.filter(_plugin_record(logging.WARNING, "Gemini Realtime model 'x' does not support "
                                                                "'system' roles. Dropping 1 system message(s)"))


def test_the_pinned_plugin_still_logs_that_exact_warning_on_its_own_logger():
    """livekit-plugins-google 1.4.6 (pinned, K-65): an upgrade that rewords the WARNING must fail here, not flood the
    alerts again."""
    import importlib
    import inspect

    module = importlib.import_module("livekit.plugins.google.realtime.realtime_api")
    assert f'logger.warning("{sp.TRUNCATE_WARNING}")' in inspect.getsource(module.RealtimeSession.update_chat_ctx)
    assert module.logger.name == sp.GOOGLE_PLUGIN_LOGGER


def test_the_resume_guards_filter_keeps_working_beside_it(monkeypatch):
    from src.core import gemini_resume_guard as guard

    class Session:
        def __init__(self) -> None:
            self._session_resumption_handle = "stale-handle"

    monkeypatch.setattr(guard, "_sessions", weakref.WeakSet())
    monkeypatch.setattr(guard, "_dropped_total", 0)
    monkeypatch.setattr(guard, "logger", MagicMock())
    guard.install_log_hook()
    sp.install_truncate_warning_filter()
    rt_session = Session()
    guard.watch_session(rt_session)
    plugin_logger = logging.getLogger(guard.PLUGIN_LOGGER_NAME)
    stale = _plugin_record(logging.ERROR, "Gemini Realtime API error: 1008 Requested entity was not found")
    assert plugin_logger.filter(stale)  # it still passes to the handlers ...
    assert rt_session._session_resumption_handle is None  # ... and the guard dropped the stale handle
    assert not plugin_logger.filter(_plugin_record(logging.WARNING, sp.TRUNCATE_WARNING))


# ---------------------------------------------------------------- 3. re-seed from memory

MEMORY = [
    {"role": "system", "content": "[LONG-TERM MEMORY CONTEXT]: older facts"},
    {"role": "user", "content": "[typed] ZEBRA mera naam kya hai"},
    {"role": "assistant", "content": "Aap ka naam ZEBRA Matloob hai, Sir."},
    {"role": "user", "content": "   "},
    {"role": "user", "content": "[SYSTEM PROACTIVE] reminder ka waqt"},
    {"role": "assistant", "content": "System error detected (tafseel): [LOG DATA, untrusted] boom [END LOG DATA]"},
    {"role": "user", "content": "[LOG DATA, untrusted] raw"},
    {"role": "user", "content": "[JARVIS CONTEXT] legacy note"},
    {"role": "user", "content": '[DESKTOP DATA, untrusted] window title: "x" [END DESKTOP DATA]'},
    {"role": "user", "content": sp.RESEED_NOTE.format(minutes=5)},  # a re-seed note that got saved: never a turn
    {"role": "tool", "content": '{"status": "success"}'},
    {"role": "developer", "content": "be brief"},
    {"role": "unknown", "content": "a function call"},
    "not a message",
    {"role": "user", "content": ["ZEBRA kal ka weather", {"text": "batao"}]},
    {"role": "user", "content": ["ZEBRA kal ka weather", {"text": "batao"}]},  # saved twice: replayed once
    {"role": "assistant", "content": "Kal ZEBRA dhoop hogi, Sir."},
]
MEMORY_TURNS = [
    ("user", "ZEBRA mera naam kya hai"),
    ("assistant", "Aap ka naam ZEBRA Matloob hai, Sir."),
    ("user", "ZEBRA kal ka weather batao"),
    ("assistant", "Kal ZEBRA dhoop hogi, Sir."),
]


def _saved(messages: list[Any], *, ago_s: float = 60.0, now: float | None = None) -> list[dict[str, Any]]:
    """Messages as ConversationMemory keeps them after MemoryExtractor.run: one conversation per message, with the
    local-time ISO 'timestamp' it was saved at (ago_s seconds ago)."""
    stamp = datetime.fromtimestamp((time.time() if now is None else now) - ago_s).isoformat()
    return [{"messages": [message], "timestamp": stamp, "user_id": "c114-test"} for message in messages]


def test_only_real_user_and_assistant_turns_are_replayed_with_the_typed_mark_stripped():
    assert sp.reseed_turns(MEMORY) == MEMORY_TURNS


def test_the_last_20_turns_are_replayed_in_order_and_long_ones_are_cut():
    memory = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(50)]
    turns = sp.reseed_turns(memory)
    assert [text for _role, text in turns] == [f"turn {i}" for i in range(30, 50)]
    assert [role for role, _text in turns][:2] == ["user", "assistant"]
    long_text = sp.reseed_turns([{"role": "assistant", "content": "x" * 5000}])[0][1]
    assert len(long_text) == sp.RESEED_TEXT_MAX + 3 and long_text.endswith("...")


def test_a_user_turn_at_the_end_with_no_reply_is_never_replayed():
    """The model would take it for a request still waiting and could act on it at its next turn."""
    memory = [{"role": "user", "content": "pehla sawal"}, {"role": "assistant", "content": "pehla jawab"},
              {"role": "user", "content": "ZEBRA saari files delete kar do"}, {"role": "user", "content": "jaldi"}]
    assert sp.reseed_turns(memory) == [("user", "pehla sawal"), ("assistant", "pehla jawab")]
    assert sp.reseed_turns(memory[2:]) == []


def test_recent_messages_keep_the_save_order_skip_unreadable_times_and_stop_at_the_first_old_one():
    now = time.time()

    def saved(text: str, ago_s: float) -> dict[str, Any]:
        return _saved([{"role": "user", "content": text}], ago_s=ago_s, now=now)[0]

    def contents(found: tuple[list[Any], float]) -> list[str]:
        return [message["content"] for message in found[0]]

    utc = {"messages": [{"role": "user", "content": "utc"}],
           "timestamp": datetime.fromtimestamp(now - 30, timezone.utc).isoformat()}
    conversations = [
        saved("before the old one", 120), saved("old", sp.RESEED_WINDOW_S + 31), saved("a", 600),
        {"messages": [{"role": "user", "content": "no time"}]},
        {"messages": [{"role": "user", "content": "garbled"}], "timestamp": "kal"},
        saved("ahead of the clock", -3600), "not a conversation", saved("b", 300), utc,
    ]
    found = sp.recent_messages(conversations, now=now)
    assert contents(found) == ["a", "b", "utc"] and found[1] == pytest.approx(30, abs=1)  # newest: 30 s ago
    assert contents(sp.recent_messages(conversations, read=2, now=now)) == ["b", "utc"]
    assert sp.recent_messages([], now=now) == ([], 0.0)
    assert sp.recent_messages(conversations, read=0, now=now) == ([], 0.0)


def test_the_window_hangs_on_the_newest_real_turn_never_on_an_injected_line():
    """A proactive or bug-hunter line saved a minute ago is no turn of the conversation: the hour and the 30 minutes
    count from the newest real turn (here 20 minutes old)."""
    now = time.time()
    conversations = (
        _saved([{"role": "user", "content": "45 minute pehle"}], ago_s=45 * 60, now=now)
        + _saved([{"role": "user", "content": "sawal"}, {"role": "assistant", "content": "jawab"}], ago_s=20 * 60,
                 now=now)
        + _saved([{"role": "user", "content": "[SYSTEM PROACTIVE] reminder ka waqt"}], ago_s=60, now=now))
    messages, age_s = sp.recent_messages(conversations, now=now)
    assert [m["content"] for m in messages] == ["45 minute pehle", "sawal", "jawab"]
    assert age_s == pytest.approx(20 * 60, abs=1)
    injected_only = _saved([{"role": "user", "content": "[SYSTEM PROACTIVE] x"}], ago_s=60, now=now)
    assert sp.recent_messages(injected_only, now=now) == ([], 0.0)


class _SeedAgent:
    """What reseed_chat_ctx touches on a BrainAssistant before its first session.start."""

    def __init__(self, memory: list[Any] | None = None, *, conversations: list[Any] | None = None) -> None:
        self._chat_ctx = llm.ChatContext.empty()
        self._activity = None
        self.reads = 0
        self.updates: list[llm.ChatContext] = []
        saved = conversations if conversations is not None else _saved(memory or [])

        async def load_memory() -> list[Any]:
            self.reads += 1
            return saved

        self.memory_extractor = types.SimpleNamespace(memory=types.SimpleNamespace(load_memory=load_memory))

    @property
    def chat_ctx(self) -> llm.ChatContext:
        return self._chat_ctx

    async def update_chat_ctx(self, ctx: llm.ChatContext) -> None:
        self.updates.append(ctx)
        self._chat_ctx = ctx.copy()


async def test_the_reseed_is_off_by_default_and_changes_nothing(records, monkeypatch):
    """20 Oct live: replayed turns made Gemini Live ignore every typed message of the job, so the re-seed is off
    until the recall moves into the instructions (C-53)."""
    assert RESEED_DEFAULT is False
    monkeypatch.setattr(sp, "RESEED_ENABLED", RESEED_DEFAULT)
    agent = _SeedAgent(MEMORY)
    assert await sp.reseed_chat_ctx(agent) == 0
    assert agent.reads == 0 and agent.updates == [] and agent.chat_ctx.items == []
    assert "SESSION_POLICY re-seed off (RESEED_ENABLED)" in records.messages


async def test_the_reseed_puts_a_memory_note_and_the_turns_before_the_first_start_with_reseed_ids(records):
    agent = _SeedAgent(MEMORY)  # saved a minute ago
    agent._chat_ctx.add_message(role="user", content="already here", id="item-live")
    assert await sp.reseed_chat_ctx(agent) == 4
    assert agent.reads == 1 and len(agent.updates) == 1
    items = agent.chat_ctx.items
    # the note, then the replay, sort before what the context already had
    note = "[MEMORY] Pichli baat-cheet ka hissa (taqreeban 1 minute purana), sirf context ke liye."
    assert [(item.role, item.text_content) for item in items] == (
        [("user", note)] + MEMORY_TURNS + [("user", "already here")])
    assert all(item.id.startswith(sp.RESEED_ID_PREFIX) for item in items[:5]) and items[5].id == "item-live"
    assert len({item.id for item in items}) == 6
    assert "SESSION_POLICY re-seeded 4 turns (2 user, 2 assistant), about 1 min old" in records.messages
    assert not any("ZEBRA" in message or "Matloob" in message or "MEMORY" in message
                   for message in records.messages)  # counts only


async def test_nothing_is_replayed_when_the_newest_saved_turn_is_over_an_hour_old(records):
    """A conversation whose newest saved turn is over an hour old is never replayed as the latest exchange (C-114's
    bound; C-52 keeps it, though since C-52 every message is saved within about 10 s and at the job's end)."""
    assert sp.RESEED_MAX_AGE_S == 3600
    old = _saved([{"role": "user", "content": "ZEBRA purana sawal"}, {"role": "assistant", "content": "purana jawab"}],
                 ago_s=70 * 60)
    agent = _SeedAgent(conversations=old)
    assert await sp.reseed_chat_ctx(agent) == 0 and agent.updates == [] and agent.chat_ctx.items == []
    assert "SESSION_POLICY re-seed: no turns saved in the last 60 min to replay" in records.messages


async def test_only_the_30_minutes_before_the_newest_saved_turn_are_replayed_after_the_note():
    """A conversation saved over 2 hours, the newest turn 10 minutes ago: only what was saved in the 30 minutes before
    that newest turn comes back, after one '[MEMORY]' note that dates it."""
    now = time.time()
    conversations: list[dict[str, Any]] = []
    for minutes in (125, 110, 95, 80, 65, 50, 35, 25, 10):
        conversations += _saved([{"role": "user", "content": f"sawal {minutes}"},
                                 {"role": "assistant", "content": f"jawab {minutes}"}], ago_s=minutes * 60, now=now)
    agent = _SeedAgent(conversations=conversations)
    assert await sp.reseed_chat_ctx(agent) == 6
    items = agent.chat_ctx.items
    assert [item.text_content for item in items] == [
        "[MEMORY] Pichli baat-cheet ka hissa (taqreeban 10 minute purana), sirf context ke liye.",
        "sawal 35", "jawab 35", "sawal 25", "jawab 25", "sawal 10", "jawab 10"]
    assert [item.role for item in items] == ["user"] + ["user", "assistant"] * 3
    assert all(sp.is_reseeded(item) for item in items)  # the note too: never saved, never seeded twice


async def test_the_memory_note_keeps_the_google_turn_format():
    """What the plugin sends Gemini at connect (its chat context in the google format, the way realtime_api builds
    the first client content): the note joins the first replayed user turn as one more part, or stands as its own user
    turn before a replayed model turn; turns still alternate."""
    from google.genai import types as genai_types

    for memory, roles, first_parts in (
            ([{"role": "user", "content": "sawal"}, {"role": "assistant", "content": "jawab"}],
             ["user", "model"], 2),
            ([{"role": "assistant", "content": "pehle ka jawab"}, {"role": "user", "content": "sawal"},
              {"role": "assistant", "content": "jawab"}], ["user", "model", "user", "model"], 1)):
        agent = _SeedAgent(memory)
        assert await sp.reseed_chat_ctx(agent) == len(memory)
        turns = agent.chat_ctx.copy(
            exclude_function_call=True, exclude_handoff=True, exclude_instructions=True, exclude_empty_message=True,
            exclude_config_update=True).to_provider_format(format="google", inject_dummy_user_message=False)[0]
        contents = [genai_types.Content.model_validate(turn) for turn in turns]
        assert [content.role for content in contents] == roles
        assert contents[0].parts[0].text.startswith("[MEMORY] ") and len(contents[0].parts) == first_parts


async def test_the_reseed_replays_what_the_memory_extractor_saved(tmp_path, monkeypatch):
    """A real ConversationMemory (an encrypted file in a temporary folder) filled by MemoryExtractor.run: the save
    times it writes are the ones the bounds read; an older conversation in the same file stays out."""
    from services.ai_core import agent_memory
    from services.ai_core.memory_store import ConversationMemory

    monkeypatch.setattr(agent_memory, "notify_memory", AsyncMock())
    memory = ConversationMemory("c114-test", storage_path=str(tmp_path))
    monkeypatch.setattr(memory, "_sync_to_vector_db", AsyncMock())
    old = (datetime.now() - timedelta(hours=5)).isoformat()
    assert await memory.save_conversation({"messages": [{"role": "user", "content": "paanch ghante purana"}],
                                           "timestamp": old, "user_id": "c114-test"})
    extractor = agent_memory.MemoryExtractor("c114-test")
    extractor.memory = memory
    chat = llm.ChatContext.empty()
    chat.add_message(role="user", content="kal subah meeting hai")
    await extractor.run(list(chat.items))
    # The reply is saved by a later pass here. Before C-52, ConversationMemory took a one-message conversation whose save
    # time equaled an earlier one's for a duplicate (the Windows wall clock moves in steps of up to 15.6 ms), so a reply
    # saved in the same pass was dropped now and then; C-52 dedupes by message id (tests/core/test_memory.py saves
    # both in one pass).
    await asyncio.sleep(0.1)
    chat.add_message(role="assistant", content="Theek hai Sir, yaad rahega.")
    await extractor.run(list(chat.items))

    agent = _SeedAgent()
    agent.memory_extractor = types.SimpleNamespace(memory=memory)
    assert await sp.reseed_chat_ctx(agent) == 2
    assert [item.text_content for item in agent.chat_ctx.items] == [
        sp.reseed_note(0), "kal subah meeting hai", "Theek hai Sir, yaad rahega."]
    assert sp.reseed_note(0) == sp.reseed_note(89) == sp.RESEED_NOTE.format(minutes=1)  # never '0 minute'


async def test_the_reseed_never_seeds_twice_and_never_into_a_running_session():
    agent = _SeedAgent(MEMORY)
    assert await sp.reseed_chat_ctx(agent) == 4
    assert await sp.reseed_chat_ctx(agent) == 0  # a C-33 rebuild or a retried start
    assert agent.reads == 1 and len(agent.updates) == 1

    running = _SeedAgent(MEMORY)
    running._activity = object()  # a session already runs: its context belongs to livekit
    assert await sp.reseed_chat_ctx(running) == 0
    assert running.reads == 0 and running.updates == []


async def test_a_slow_or_broken_memory_never_holds_or_fails_the_job_start(records):
    slow = _SeedAgent([])

    async def never() -> list[Any]:
        await asyncio.sleep(30)
        return []

    slow.memory_extractor.memory.load_memory = never
    assert await sp.reseed_chat_ctx(slow, timeout_s=0.01) == 0 and slow.updates == []

    broken = _SeedAgent([])

    async def boom() -> list[Any]:
        raise OSError("memory file unreadable")

    broken.memory_extractor.memory.load_memory = boom
    assert await sp.reseed_chat_ctx(broken) == 0
    assert "SESSION_POLICY re-seed skipped (TimeoutError)" in records.messages
    assert "SESSION_POLICY re-seed skipped (OSError)" in records.messages
    assert await sp.reseed_chat_ctx(types.SimpleNamespace(_activity=None)) == 0  # a stand-in without memory
    assert await sp.reseed_chat_ctx(MagicMock()) == 0  # a mock assistant: its MagicMock activity counts as running


async def test_a_reseeded_turn_is_never_saved_again(monkeypatch):
    """The memory loop drops 'reseed-*' items before MemoryExtractor.run, so every new job and every idle recycle saves
    only the new turns (the extractor itself is C-52's)."""
    from services.ai_core import agent_memory
    from src.core import agent_runner as ar

    saved: list[str] = []

    class Store:
        async def validate_integrity(self) -> bool:
            return True

        async def archive_stale_context(self) -> int:
            return 0

        async def save_conversation(self, conversation: dict[str, Any]) -> bool:
            saved.extend(message["content"] for message in conversation["messages"])
            return True

    monkeypatch.setattr(agent_memory, "notify_memory", AsyncMock())
    extractor = agent_memory.MemoryExtractor("c114-test")
    extractor.memory = Store()
    agent = _SeedAgent(MEMORY)
    agent.user_id = "c114-test"
    await sp.reseed_chat_ctx(agent)
    agent._chat_ctx.add_message(role="user", content="naya sawal")
    agent._chat_ctx.add_message(role="assistant", content="naya jawab")

    async def stop(*_args: Any, **_kwargs: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(ar.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await ar.start_memory_loop(agent, extractor)
    assert saved == ["naya sawal", "naya jawab"]

    only_replayed = _SeedAgent(MEMORY)  # a fresh job before anybody spoke: the extractor never even runs
    only_replayed.user_id = "c114-test"
    await sp.reseed_chat_ctx(only_replayed)
    extractor.run = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await ar.start_memory_loop(only_replayed, extractor)
    extractor.run.assert_not_awaited()


async def test_a_cut_takes_the_reseeded_turns_with_their_memory_note_or_keeps_them_all(records):
    """
    C-114 review: each copy was cut to its own last 40, so the first cut after a re-seed removed the '[MEMORY]' note
    (put in first) and kept the replayed turns: the plugin's copy went 41 -> 40 and the agent's 42 -> 40, the note gone
    and 19-20 turns of up to about 90 minutes ago left. A later fresh reconnect (a C-51 stop, a persona, voice or
    wake-word switch, the midnight refresh) or a C-33 rebuild would then hand them to Gemini as the plain start of this
    conversation, nothing marking them as old memory. The re-seeded block is one unit in both cuts now: once its note
    is cut away, every 'reseed-*' item goes with it (removals only on the realtime side: nothing reaches Gemini), and
    until then it stays whole.
    """
    memory = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"purani baat {i}"} for i in range(24)]
    seeded = _SeedAgent(memory)  # the job's assistant before its first session.start
    assert await sp.reseed_chat_ctx(seeded) == sp.RESEED_TURNS
    block = _ids(seeded.chat_ctx.items)  # the note, then the 20 replayed turns
    assert len(block) == sp.RESEED_TURNS + 1 and seeded.chat_ctx.items[0].text_content.startswith(sp.MEMORY_TAG)
    agent = _TruncAgent(0)  # the running job: session.start handed the re-seeded context to the plugin too
    for item in seeded.chat_ctx.items:
        agent._chat_ctx.items.append(item)
        agent.rt._chat_ctx.items.append(item)
    agent.add(10)
    tip = _clipboard_tip()  # one item more in the agent's copy (agent_runner appends a tip there only)
    agent._chat_ctx.items.append(tip)
    truncator = sp.ChatTruncator(agent)
    assert await truncator.truncate() is False  # nothing to cut yet: the block stays whole in both copies
    assert [i for i in agent.ids() if i in block] == [i for i in agent.rt.ids() if i in block] == block

    agent.add(10)  # ten exchanges in all: the plugin's copy holds 41 items, the agent's 42
    for _ in range(20):
        truncator.note_item()
    await _settle()
    conversation = [f"item-{i}" for i in range(20)]
    assert agent.rt.ids() == conversation  # the note and all 20 replayed turns went together
    assert agent.ids() == conversation[:10] + [tip.id] + conversation[10:]
    assert not any(sp.is_reseeded(item) for item in [*agent.chat_ctx.items, *agent.rt._chat_ctx.items])
    (diff,) = agent.rt.diffs  # the real diff between the plugin's copy and what the cut handed it
    assert diff.to_create == [] and diff.to_remove == block and agent.rt.sent == []  # removals only
    assert "SESSION_POLICY chat context cut 42 -> 21 items (realtime 41 -> 20, remove only)" in records.messages
    assert "SESSION_POLICY re-seeded turns cut with their memory note (realtime copy: 20 items)" in records.messages
    assert "SESSION_POLICY re-seeded turns cut with their memory note (agent copy: 19 items)" in records.messages
    assert not any("purani" in message or "MEMORY" in message for message in records.messages)  # counts only


def test_the_reseeded_block_goes_only_without_its_note_and_the_order_stays():
    ctx = llm.ChatContext.empty()
    note = ctx.add_message(role="user", content=sp.reseed_note(300), id="reseed-n1")
    ctx.add_message(role="user", content="purana sawal", id="reseed-t1")
    ctx.add_message(role="user", content="naya sawal", id="item-1")
    ctx.add_message(role="assistant", content="purana jawab", id="reseed-t2")  # order as it comes, whatever it is
    ctx.add_message(role="assistant", content="naya jawab", id="item-2")
    assert sp.keep_reseed_block_whole(ctx) == 0 and len(ctx.items) == 5  # the note is there: the block stays whole
    del ctx.items[0]  # a cut took the note
    assert sp.keep_reseed_block_whole(ctx) == 2 and _ids(ctx.items) == ["item-1", "item-2"]
    assert sp.keep_reseed_block_whole(ctx) == 0 and sp.keep_reseed_block_whole(llm.ChatContext.empty()) == 0
    assert sp.is_reseed_note(note) and not sp.is_reseed_note(_msg("reseed-t3", text="purana"))
    assert not sp.is_reseed_note(_msg("item-3", text=sp.reseed_note(60)))  # the tag alone is not the note


def test_reseeded_items_are_recognised_as_objects_and_dicts():
    assert sp.is_reseeded(types.SimpleNamespace(id="reseed-abc")) and sp.is_reseeded({"id": "reseed-1"})
    assert not sp.is_reseeded(types.SimpleNamespace(id="item_abc")) and not sp.is_reseeded(MagicMock())
    assert not sp.is_reseeded({"role": "user"})
    keep = types.SimpleNamespace(id="item_1")
    assert sp.without_reseeded([types.SimpleNamespace(id="reseed-x"), keep]) == [keep]


# ---------------------------------------------------------------- 4. date and city


class _CtxAssistant:
    def __init__(self, date: str = "Thursday, November 13, 2025 - 11:58 PM", city: str = "Rawalpindi") -> None:
        self.current_date, self.current_city = date, city
        self.refreshed: list[tuple[str, str]] = []

    async def refresh_instructions(self) -> None:
        self.refreshed.append((self.current_date, self.current_city))


class _Clock:
    def __init__(self, formatted: str) -> None:
        self.formatted = formatted

    async def __call__(self) -> dict[str, str]:
        return {"formatted": self.formatted}


def _reading(city: str) -> dict[str, Any]:
    return {"city": city, "lat": 33.6, "lng": 73.0}


async def test_only_a_new_day_refreshes_the_instructions_never_the_minute(records):
    assistant = _CtxAssistant()
    clock = _Clock("Thursday, November 13, 2025 - 11:59 PM")
    policy = sp.SessionPolicy(assistant, now_formatted=clock)
    assert await policy.check_context() == [] and assistant.refreshed == []
    clock.formatted = "Friday, November 14, 2025 - 12:00 AM"
    assert await policy.check_context() == ["date"]
    assert assistant.current_date == "Friday, November 14, 2025 - 12:00 AM"
    assert assistant.refreshed == [("Friday, November 14, 2025 - 12:00 AM", "Rawalpindi")]
    clock.formatted = "Friday, November 14, 2025 - 12:01 AM"
    assert await policy.check_context() == [] and len(assistant.refreshed) == 1
    assert records.messages.count("SESSION_POLICY context changed date") == 1


async def test_the_date_part_is_compared_as_a_day_whatever_its_format():
    assistant = _CtxAssistant(date="2025-11-14")  # BrainAssistant's own default format
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock("Friday, November 14, 2025 - 09:00 AM"))
    assert await policy.check_context() == [] and assistant.refreshed == []
    assert sp.date_changed("Friday, November 14, 2025 - 09:00 AM", "Saturday, November 15, 2025 - 12:00 AM")
    assert not sp.date_changed("Friday, November 14, 2025 - 09:00 AM", "Friday, November 14, 2025 - 11:00 PM")


async def test_a_city_change_needs_five_agreeing_readings_and_refreshes_once(records):
    assert sp.CITY_READINGS == 5  # 60 s apart: about 5 minutes in the new city
    assistant = _CtxAssistant()
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=True)
    for _ in range(sp.CITY_READINGS - 1):
        policy.note_location(_reading("Islamabad"))
        assert await policy.check_context() == [] and assistant.refreshed == []  # not a move yet
    policy.note_location(_reading("Islamabad"))
    assert await policy.check_context() == ["city"]
    assert assistant.current_city == "Islamabad" and len(assistant.refreshed) == 1
    policy.note_location(_reading("islamabad "))  # the same city, other case and spacing
    assert await policy.check_context() == [] and len(assistant.refreshed) == 1
    for city in ("Lahore", "Lahore", "Lahore", "Lahore", "Islamabad"):  # four in a row are not five
        policy.note_location(_reading(city))
        assert await policy.check_context() == []
    assert len(assistant.refreshed) == 1
    assert records.messages.count("SESSION_POLICY context changed city") == 1
    assert not any("Islamabad" in message for message in records.messages)  # never the location itself


async def test_a_city_that_flaps_in_pairs_never_refreshes_and_the_skip_is_logged_at_debug(debug_records):
    """An IP-geolocated city on a mobile hotspot or a VPN, read every 60 s: A, A, B, B, A, A ... Two agreeing readings
    used to count as a move, so every pair cost a refresh and with it a fresh Gemini reconnect (C-49)."""
    assistant = _CtxAssistant()  # Rawalpindi
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=True)
    for index in range(60):  # an hour of flapping pairs, one check after every reading
        policy.note_location(_reading("Islamabad" if index % 4 < 2 else "Rawalpindi"))
        assert await policy.check_context() == []
    assert assistant.refreshed == [] and policy.refreshes == 0 and assistant.current_city == "Rawalpindi"
    skipped = [message for message in debug_records.messages if message.startswith("SESSION_POLICY city flap")]
    assert skipped[:2] == ["SESSION_POLICY city flap skipped: 1 of 5 readings in a row agree",
                           "SESSION_POLICY city flap skipped: 2 of 5 readings in a row agree"]
    assert len(skipped) == 30  # each reading of the other city; a reading of the current one is no change at all
    assert all(record.levelno == logging.DEBUG for record in debug_records.records
               if record.getMessage().startswith("SESSION_POLICY city flap"))
    assert not any("Islamabad" in message or "Rawalpindi" in message for message in debug_records.messages)


async def test_a_city_change_refreshes_at_most_once_an_hour_while_a_new_day_refreshes_at_once(debug_records):
    """A city that flaps in runs of 5 or more: the job's first city change comes at once, the next one only an hour
    after it. A new day still refreshes at once, and an agreed city comes along with it (no extra reconnect)."""
    now = {"s": 0.0}
    assistant = _CtxAssistant()  # Rawalpindi, Thursday
    clock = _Clock(assistant.current_date)
    policy = sp.SessionPolicy(assistant, now_formatted=clock, watch_city=True, clock=lambda: now["s"])

    async def settle(city: str, minute: float) -> list[str]:
        """CITY_READINGS readings of the city at that minute, a check after each; what the last check changed."""
        now["s"] = minute * 60.0
        changed: list[str] = []
        for _ in range(sp.CITY_READINGS):
            policy.note_location(_reading(city))
            changed = await policy.check_context()
        return changed

    assert await settle("Islamabad", 5) == ["city"]  # the job's first city change: at once
    assert await settle("Rawalpindi", 20) == [] and assistant.current_city == "Islamabad"  # 15 minutes later: held
    held = "SESSION_POLICY city change held: the last one was 15 min ago (one an hour at most)"
    assert held in debug_records.messages
    now["s"] = 64.9 * 60.0
    assert await policy.check_context() == []  # still under an hour since the last city change
    now["s"] = 65 * 60.0
    assert await policy.check_context() == ["city"] and assistant.current_city == "Rawalpindi"

    assert await settle("Murree", 70) == []  # 5 minutes after the last city change: held
    clock.formatted = "Friday, November 14, 2025 - 12:00 AM"
    now["s"] = 71 * 60.0
    assert await policy.check_context() == ["date", "city"]  # the new day's refresh takes the agreed city along
    assert assistant.current_city == "Murree"
    clock.formatted = "Saturday, November 15, 2025 - 12:00 AM"
    now["s"] = 72 * 60.0
    assert await policy.check_context() == ["date"]  # the hourly bound is the city's alone: a new day, at once
    assert await settle("Lahore", 100) == [] and assistant.current_city == "Murree"  # 29 minutes after Murree came
    assert [city for _date, city in assistant.refreshed] == ["Islamabad", "Rawalpindi", "Murree", "Murree"]
    assert not any(city in message for message in debug_records.messages
                   for city in ("Islamabad", "Rawalpindi", "Murree", "Lahore"))


async def test_a_failed_lookup_never_moves_the_city_and_without_watch_city_nothing_does():
    assistant = _CtxAssistant()
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=True)
    fallback = {"city": "Lahore", "lat": jarvis_location.DEFAULT_LAT, "lng": jarvis_location.DEFAULT_LNG}
    for reading in (fallback, fallback, {"city": "Lahore", "source": "user_city"}, {"city": ""}, None, "Lahore"):
        policy.note_location(reading)
    assert await policy.check_context() == [] and assistant.current_city == "Rawalpindi"

    unwatched = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=False)
    for _ in range(sp.CITY_READINGS):
        unwatched.note_location(_reading("Karachi"))
    assert await unwatched.check_context() == [] and assistant.refreshed == []


async def test_a_new_day_and_a_new_city_together_refresh_once(records):
    assistant = _CtxAssistant()
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock("Friday, November 14, 2025 - 12:00 AM"),
                              watch_city=True)
    for _ in range(sp.CITY_READINGS):
        policy.note_location(_reading("Murree"))
    assert await policy.check_context() == ["date", "city"]
    assert assistant.refreshed == [("Friday, November 14, 2025 - 12:00 AM", "Murree")]
    assert "SESSION_POLICY context changed date+city" in records.messages


async def test_an_assistant_without_refresh_is_left_alone():
    stand_in = types.SimpleNamespace(current_date="x", current_city="y")
    policy = sp.SessionPolicy(stand_in, now_formatted=_Clock("Friday, November 14, 2025 - 12:00 AM"))
    assert await policy.check_context() == [] and stand_in.current_date == "x"


async def test_the_location_loop_hands_every_reading_to_the_jobs_policy(monkeypatch):
    """The PC brain's city check rides the existing ipinfo loop: on_location (the job's SessionPolicy.note_location)
    gets every reading the UI gets, so a move reaches the instructions without a second lookup."""
    from src.core import agent_runner as ar

    readings = [_reading("Murree")] + [_reading("Islamabad")] * sp.CITY_READINGS
    feed = iter(readings)
    sent: list[Any] = []
    noted: list[Any] = []
    waits: list[float] = []

    async def city_data() -> dict[str, Any]:
        return next(feed)

    async def notify(loc: Any) -> None:
        sent.append(loc)

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == len(readings):
            raise asyncio.CancelledError  # the job's teardown

    assistant = _CtxAssistant()
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=True)

    def on_location(loc: Any) -> None:
        noted.append(loc)
        policy.note_location(loc)

    monkeypatch.setattr(ar, "get_current_city_data", city_data)
    monkeypatch.setattr(ar.asyncio, "sleep", wait)
    with pytest.raises(asyncio.CancelledError):
        await ar._location_loop(notify, on_location)
    assert noted == readings and sent == readings and waits == [60] * len(readings)
    assert await policy.check_context() == ["city"] and assistant.current_city == "Islamabad"


async def test_the_vps_brain_reads_the_cached_device_location_at_each_check(monkeypatch, tmp_path, records):
    """Remote mode has no location loop: the policy reads jarvis_location.get_current_city_data itself (the Desktop
    Node's location that agent-svc caches in conversations/location.json; no network call). A missing or stale cache
    answers with USER_CITY, which is never a reading."""
    cache = tmp_path / "location.json"
    monkeypatch.setattr(jarvis_location, "location_path", lambda: str(cache))
    assistant = _CtxAssistant()
    policy = sp.SessionPolicy(assistant, now_formatted=_Clock(assistant.current_date), watch_city=True,
                              read_location=jarvis_location.get_current_city_data)
    assert await policy.check_context() == []  # no cache yet: USER_CITY
    jarvis_location.save_device_location("Islamabad", 33.68, 73.04, path=str(cache))
    for _ in range(sp.CITY_READINGS - 1):
        assert await policy.check_context() == []  # not a move yet
    assert await policy.check_context() == ["city"] and assistant.current_city == "Islamabad"
    jarvis_location.save_device_location("Murree", 33.9, 73.39, ts=time.time() - 2 * jarvis_location.MAX_AGE_S,
                                         path=str(cache))
    for _ in range(3):  # a stale cache answers with USER_CITY: never a move
        assert await policy.check_context() == []
    assert assistant.current_city == "Islamabad" and len(assistant.refreshed) == 1
    assert not any("Islamabad" in message or "Murree" in message for message in records.messages)


async def test_a_failing_location_read_still_lets_the_date_check_run():
    assistant = _CtxAssistant()

    async def broken() -> dict[str, Any]:
        raise OSError("cache unreadable")

    policy = sp.SessionPolicy(assistant, now_formatted=_Clock("Friday, November 14, 2025 - 12:00 AM"),
                              watch_city=True, read_location=broken)
    assert await policy.check_context() == ["date"] and assistant.current_city == "Rawalpindi"


# ---------------------------------------------------------------- 5. idle close and the 24 h recycle


class _Link:
    """The job's VoiceLink as the policy reads it: the identity it linked, and its room's remote participants."""

    def __init__(self) -> None:
        self.identity: str | None = None
        self.room = types.SimpleNamespace(remote_participants={})

    def linked_identity(self) -> str | None:
        return self.identity


def _participant(identity: str, kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD) -> Any:
    """A remote participant as the job's rtc.Room holds it (livekit's ParticipantKind: STANDARD, SIP, AGENT ...)."""
    return types.SimpleNamespace(identity=identity, kind=kind, track_publications={})


class _Policy:
    """A SessionPolicy on a fake clock (minutes), with its hooks recorded."""

    def __init__(self, idle_close_min: float | None = 30, **kwargs: Any) -> None:
        self.now = 1000.0
        self.link = _Link()
        self.session = types.SimpleNamespace(agent_state="listening")
        self.ends: list[str] = []
        self.attrs: list[str] = []
        self.policy = sp.SessionPolicy(
            types.SimpleNamespace(), voice_link=self.link, session=lambda: self.session,
            request_end=self.ends.append, set_session_attr=self.attrs.append, idle_close_min=idle_close_min,
            clock=lambda: self.now, **kwargs)

    def at(self, minutes: float) -> bool:
        self.now = 1000.0 + minutes * 60.0
        return self.policy.check_idle()

    def activity(self, minutes: float) -> None:
        """A conversation item, a user transcription or an agent state change at that minute."""
        self.now = 1000.0 + minutes * 60.0
        self.policy.note_activity()


def test_nobody_linked_for_30_minutes_ends_the_job_as_idle_with_jarvis_session_idle_first(records):
    p = _Policy()
    assert not p.at(0) and not p.at(15) and not p.at(29.9)
    assert p.at(30)
    assert p.attrs == ["idle"] and p.ends == ["idle"]  # K-68 'idle', then the C-23 end with reason 'idle'
    assert "SESSION_POLICY idle close after 30 min" in records.messages
    assert not p.at(90) and p.ends == ["idle"]  # asked once


def test_a_linked_participant_resets_the_idle_count():
    p = _Policy()
    p.at(0)
    p.at(25)
    p.link.identity = "owner-1a2b3c4d"
    assert not p.at(26)
    p.link.identity = None
    assert not p.at(27) and not p.at(56)  # counted again from 27
    assert p.at(57) and p.ends == ["idle"]


def test_any_person_the_bridge_counts_keeps_the_job_like_a_linked_one(records):
    """
    C-114 review: the VPS brain's voice link (strict_owner) links only 'owner-*', while the bridge's on_demand demand
    counts any STANDARD person that is not 'agent-*' or 'probe-*' (bridge_rooms.is_person). A non-owner person left in
    the room (an older client, a CLI or test participant) ended the job as 'idle' after 30 quiet minutes and the bridge
    dispatched a new one at once: a new job, re-seed and Gemini session every half hour. Every person the bridge counts
    keeps the job now; an agent, a probe or another kind never does, and the 24 h recycle stays as it was.
    """
    from src.core import bridge_rooms

    kinds = rtc.ParticipantKind
    person = _participant("web-1a2b3c4d")
    others = [_participant("agent-AJ_other", kinds.PARTICIPANT_KIND_AGENT), _participant("agent-cli"),
              _participant("probe-1791460000"), _participant("sip-caller", kinds.PARTICIPANT_KIND_SIP),
              _participant("egress-1", kinds.PARTICIPANT_KIND_EGRESS)]
    assert bridge_rooms.is_person(person) and not any(bridge_rooms.is_person(other) for other in others)

    nobody = _Policy()  # only participants the bridge never counts: closed after 30 minutes, as before
    nobody.link.room.remote_participants.update({other.identity: other for other in others})
    nobody.at(0)
    assert not nobody.at(29.9) and nobody.at(30) and nobody.ends == ["idle"]

    p = _Policy()
    p.link.room.remote_participants.update({each.identity: each for each in [person, *others]})
    p.at(0)
    for minutes in (30, 90, 600, 23 * 60):
        assert not p.at(minutes)  # never linked (not 'owner-*') but in the room: the bridge would dispatch again
    del p.link.room.remote_participants[person.identity]
    assert not p.at(23 * 60 + 1)  # the person left: the 30 minutes count from here
    assert not p.at(23 * 60 + 30.9)
    assert p.at(23 * 60 + 31) and p.ends == ["idle"] and p.attrs == ["idle"]
    assert "SESSION_POLICY idle close after 30 min" in records.messages
    assert not any("web-" in message for message in records.messages)  # never an identity

    recycled = _Policy()  # a job 24 h old still ends at its first quiet 10 minutes, a person in the room or not
    recycled.link.room.remote_participants[person.identity] = person
    assert not recycled.at(24 * 60 - 1) and recycled.at(24 * 60) and recycled.ends == ["idle"]


def test_conversation_without_a_linked_participant_keeps_the_job():
    """Typed chat from a client whose LiveKit join failed (nobody linked) is use too: each message starts the 30
    minutes again."""
    p = _Policy()
    p.at(0)
    p.activity(25)
    assert not p.at(30) and not p.at(54)
    p.activity(54.5)
    assert not p.at(84)
    assert p.at(84.5) and p.ends == ["idle"]


@pytest.mark.parametrize("busy_session", [
    types.SimpleNamespace(agent_state="speaking"), types.SimpleNamespace(agent_state="thinking"),
    types.SimpleNamespace(agent_state="listening", current_speech=types.SimpleNamespace(done=lambda: False)),
])
def test_never_while_the_agent_speaks_or_thinks(busy_session):
    p = _Policy()
    p.session = busy_session
    p.at(0)
    assert not p.at(30) and not p.at(45) and p.ends == []
    p.session = types.SimpleNamespace(agent_state="listening")
    assert p.at(46) and p.ends == ["idle"]


def test_never_while_a_tool_call_or_a_background_job_runs():
    """A YouTube download (a background job of the job's loop) or a tool call still waiting for its Allow: the close
    waits until it is over."""
    work = {"running": True}
    p = _Policy(background_busy=lambda: work["running"])
    p.at(0)
    assert not p.at(30) and not p.at(90) and p.ends == []
    work["running"] = False
    assert p.at(91) and p.ends == ["idle"]

    def broken() -> bool:
        raise RuntimeError("registry gone")

    unsure = _Policy(background_busy=broken)  # an error counts as busy: the next tick looks again
    unsure.at(0)
    assert not unsure.at(45) and unsure.ends == []


async def test_background_work_counts_this_loops_jobs_and_running_tool_calls(monkeypatch):
    from services.utils import jarvis_bridge

    monkeypatch.setattr(jarvis_jobs, "_jobs", {})
    monkeypatch.setattr(jarvis_bridge, "notify_event", AsyncMock())
    release = asyncio.Event()

    async def download(_ctx: Any) -> dict[str, Any]:
        await release.wait()
        return {"status": "success", "message": "saved"}

    assert sp.background_work(set) is False
    job_id = jarvis_jobs.start_job("download_youtube_media", "YouTube download", download, announce=False)
    await _settle()
    assert sp.background_work(set) is True  # ending the job now would cancel it, and its result would be lost
    release.set()
    for _ in range(100):
        if jarvis_jobs.get_job(job_id)["status"] == jarvis_jobs.DONE:
            break
        await asyncio.sleep(0.01)
    assert sp.background_work(set) is False
    assert sp.background_work(lambda: {"open_app"}) is True  # a tool call of this process
    assert sp.background_work(None) is False


def test_a_24_hour_job_is_recycled_at_its_first_quiet_10_minutes_even_with_the_dashboard_open(records):
    """The dashboard stays open (a linked participant all along) and the owner talks every 8 minutes: once the job is
    24 h old it still ends at its first 10 minutes without a message, transcription or agent state change."""
    p = _Policy()
    p.link.identity = "owner-1a2b3c4d"
    for minutes in range(0, 24 * 60, 8):
        p.activity(minutes)
        assert not p.at(minutes + 7.9)
    assert not p.at(24 * 60 + 1.9)  # 24 h old, 9.9 quiet minutes since the last activity (23:52)
    assert p.at(24 * 60 + 2)        # 10 minutes without conversation
    assert p.ends == ["idle"] and p.attrs == ["idle"]
    assert "SESSION_POLICY idle close after 10 min (the job is older than 24 h)" in records.messages

    young = _Policy()
    young.link.identity = "owner-1a2b3c4d"
    young.activity(0)
    assert not young.at(10) and not young.at(600) and young.ends == []  # quiet, but not 24 h old


def test_typed_chat_that_kept_an_unlinked_job_for_a_day_still_gets_the_24_hour_recycle():
    p = _Policy()
    for minutes in range(0, 24 * 60, 20):  # nobody linked, a typed message every 20 minutes: no idle close
        p.activity(minutes)
        assert not p.at(minutes + 19)
    assert p.at(24 * 60) and p.ends == ["idle"]  # 24 h old, 20 quiet minutes: recycled before the idle close (30)


def test_zero_turns_both_idle_rules_off():
    p = _Policy(idle_close_min=0)
    for minutes in (0, 30, 600, 24 * 60 + 30, 48 * 60):
        assert not p.at(minutes)
    assert p.ends == [] and p.attrs == []


@pytest.mark.parametrize(("raw", "setting"), [
    (None, None), ("", None), ("45", 45.0), ("2.5", 2.5), ("0", 0.0), ("-3", 0.0), ("soon", None), ("inf", None),
    ("nan", None),
])
def test_the_idle_setting_comes_from_the_env_and_empty_takes_the_brains_default(monkeypatch, raw, setting):
    if raw is None:
        monkeypatch.delenv(sp.IDLE_CLOSE_ENV, raising=False)
    else:
        monkeypatch.setenv(sp.IDLE_CLOSE_ENV, raw)
    assert sp.idle_close_setting() == setting
    vps = sp.SessionPolicy(types.SimpleNamespace())
    pc = sp.SessionPolicy(types.SimpleNamespace(), idle_close_default_min=0)
    if setting is None:  # empty: 30 on the VPS brain, off on the PC brain; the 24 h recycle in both
        assert vps.idle_close_s == 30 * 60.0 and pc.idle_close_s == 0 and vps.recycle and pc.recycle
    else:  # set: the same in both brains; 0 turns both rules off
        assert vps.idle_close_s == pc.idle_close_s == setting * 60.0
        assert vps.recycle is pc.recycle is (setting > 0)


def test_a_bad_idle_setting_names_the_variable_never_its_value(monkeypatch, records):
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "zebra-minutes")
    assert sp.idle_close_setting() is None
    warnings = [m for r, m in zip(records.records, records.messages) if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and sp.IDLE_CLOSE_ENV in warnings[0]
    assert not any("zebra" in message for message in records.messages)


def test_the_pc_brain_default_never_idle_closes_but_still_recycles(monkeypatch):
    """JARVIS_IDLE_CLOSE_MIN empty on the PC brain (agent_runner passes idle_close_default_min=0): its bridge ('always'
    dispatch) would put a fresh job into the empty room at once, so nobody linked never ends the job there; the 24 h
    recycle does, once a day."""
    monkeypatch.delenv(sp.IDLE_CLOSE_ENV, raising=False)
    p = _Policy(idle_close_min=None, idle_close_default_min=0)
    assert p.policy.idle_close_s == 0 and p.policy.recycle
    for minutes in (0, 31, 61, 600, 24 * 60 - 1):
        assert not p.at(minutes)
    assert p.at(24 * 60) and p.ends == ["idle"]  # quiet since it started: recycled at 24 h
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "45")  # set explicitly: the PC brain gets the idle close too
    assert _Policy(idle_close_min=None, idle_close_default_min=0).policy.idle_close_s == 45 * 60.0


def test_without_a_voice_link_only_the_24_hour_recycle_ends_a_job():
    ends: list[str] = []
    now = {"t": 0.0}
    policy = sp.SessionPolicy(types.SimpleNamespace(), request_end=ends.append, idle_close_min=1,
                              clock=lambda: now["t"])
    now["t"] = 23 * 3600.0
    assert not policy.check_idle() and ends == []  # nothing to judge a link by: no idle close
    now["t"] = sp.RECYCLE_AGE_S
    assert policy.check_idle() and ends == ["idle"]


async def test_the_loop_ticks_until_it_ended_the_job():
    p = _Policy(idle_close_min=2)
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        p.now += 60.0  # one tick of the clock per wait

    p.policy._sleep = sleep
    await asyncio.wait_for(p.policy.run(), 2)
    assert p.ends == ["idle"] and sleeps == [sp.POLICY_TICK_S] * 3  # idle from the 1st tick, closed at the 3rd


# ---------------------------------------------------------------- 6. the wiring in agent_runner.entrypoint


class _FakeLocal:
    def __init__(self) -> None:
        self.attributes: list[str] = []
        self.calls: list[dict[str, str]] = []  # every set_attributes call as it came

    async def set_attributes(self, attributes: dict[str, str]) -> None:
        self.calls.append(dict(attributes))
        if "jarvis.session" in attributes:
            self.attributes.append(attributes["jarvis.session"])


class _FakeRoom:
    name = "jarvis-hybrid"

    def __init__(self) -> None:
        self.local_participant = _FakeLocal()
        self.remote_participants: dict[str, Any] = {}

    def on(self, _event, callback=None):  # decorator or (event, callback), like rtc.Room
        return (lambda fn: fn) if callback is None else callback

    def off(self, *_args):
        return None


class _FakeJobContext:
    def __init__(self) -> None:
        self.room = _FakeRoom()
        self.shutdowns: list[str] = []

    async def connect(self) -> None:
        return None

    def shutdown(self, reason: str = "") -> None:
        self.shutdowns.append(reason)

    def is_fake_job(self) -> bool:
        return False

    def add_shutdown_callback(self, _callback) -> None:
        return None


@pytest.fixture
def entry(monkeypatch):
    """agent_runner.entrypoint with the light stand-ins of tests/core/test_agent_runner.py's 'job' fixture, plus an
    assistant with a real ChatContext, update_chat_ctx and a memory to re-seed from."""
    from services.ai_core import jarvis_plugin_manager
    from src.core import agent_core, agent_runner as ar

    state = types.SimpleNamespace(sessions=[], assistants=[], models=[], protectors=[], starts=[], memory=[],
                                  on_start=None, tasks=[], ar=ar)

    class FakeAssistant:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._stop_event = asyncio.Event()
            self.end_reason = None
            self.request_end = agent_core.BrainAssistant.request_end.__get__(self)
            self._chat_ctx = llm.ChatContext.empty()
            self._activity = None
            self.reads = 0
            self.updates: list[list[str]] = []
            self.session = None

            async def load_memory() -> list[Any]:
                self.reads += 1
                return _saved(list(state.memory))

            self.memory_extractor = types.SimpleNamespace(memory=types.SimpleNamespace(load_memory=load_memory))
            state.assistants.append(self)

        @property
        def chat_ctx(self) -> llm.ChatContext:
            return self._chat_ctx

        async def update_chat_ctx(self, ctx: llm.ChatContext) -> None:
            self.updates.append([item.id for item in ctx.items])
            self._chat_ctx = ctx.copy()

        def attach_router(self, _router) -> None:
            return None

        def attach_session(self, session) -> None:
            self.session = session

    class FakeSession:
        def __init__(self, **_kwargs):
            self.handlers: dict[str, list[Any]] = {}
            self.closed = False
            state.sessions.append(self)

        def on(self, event, callback=None):
            def register(fn):
                self.handlers.setdefault(event, []).append(fn)
                return fn
            return register if callback is None else register(callback)

        def off(self, event, callback):
            if callback in self.handlers.get(event, []):
                self.handlers[event].remove(callback)

        def fire(self, event, *args):
            for handler in list(self.handlers.get(event, [])):
                handler(*args)

        async def aclose(self):
            self.closed = True

        async def start(self, room, agent, room_options, record=None):  # pylint: disable=unused-argument
            state.starts.append([item.id for item in agent.chat_ctx.items])
            if state.on_start is not None:
                state.on_start(agent)

    class FakeRouter:
        text_input_cb = None

        def __init__(self, *_args, **_kwargs):
            self.items: list[Any] = []

        def attach(self, _session):
            return None

        def detach(self):
            return None

        def on_conversation_item_added(self, event):
            self.items.append(event)

    class FakePlugins:
        running_tools = staticmethod(set)

        def discover_plugins(self, _path):
            return None

        def get_livekit_tools(self):
            return []

    class FakeListener:
        def __init__(self, assistant, runtime=None):
            self.assistant, self.runtime = assistant, runtime

        async def aclose(self):
            return None

    async def nothing(*_args, **_kwargs):
        return None

    async def no_tasks(*_args, **kwargs):
        state.protectors.append(kwargs.get("protector"))
        return []

    async def returning(result):
        return result

    def realtime_model(**kwargs):
        state.models.append(kwargs)
        return types.SimpleNamespace(voice="Charon")

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay, result=None):
        await real_sleep(0)
        return result

    for name, value in (("_prewarm_vector_memory", lambda *_a: None), ("_prewarm_voice_id", lambda *_a: None),
                        ("get_formatted_datetime", lambda: returning({"formatted": "c114-day"})),
                        ("get_current_city", lambda: returning("Lahore")),
                        ("silero", types.SimpleNamespace(VAD=types.SimpleNamespace(load=lambda **_k: object()))),
                        ("google", types.SimpleNamespace(realtime=types.SimpleNamespace(RealtimeModel=realtime_model))),
                        ("_resolve_user_id", lambda _room: "c114-user"), ("BrainAssistant", FakeAssistant),
                        ("ConversationRouter", FakeRouter), ("AgentSession", FakeSession),
                        ("harden_room_input", lambda *_a, **_k: []), ("_location_loop", nothing),
                        ("_pc_vitals_loop", nothing), ("_print_startup_banner", lambda: None),
                        ("_start_loop_watchdog", lambda *_a: None), ("_start_background_tasks", no_tasks),
                        ("_rag_warm_delay", lambda: None), ("_cleanup_session_resources", nothing),
                        ("_quiet_livekit_agents_logger", lambda: None), ("UIBridgeListener", FakeListener)):
        monkeypatch.setattr(ar, name, value)
    monkeypatch.setattr(jarvis_plugin_manager, "JarvisPluginManager", FakePlugins)
    monkeypatch.setattr(ar.asyncio, "sleep", fast_sleep)  # the C-33 rebuild waits (2/4/8 s) take no time
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    monkeypatch.delenv(sp.CTX_TRIGGER_ENV, raising=False)
    monkeypatch.delenv(sp.IDLE_CLOSE_ENV, raising=False)
    return state


class _SimClock:
    """Simulated time for a job's SessionPolicy: each of its waits (one tick) moves the clock on by the wait and
    yields once, so hours of ticks take milliseconds."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.ticks = 0
        self.policies: list[sp.SessionPolicy] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.ticks += 1
        await _REAL_SLEEP(0)


@pytest.fixture
def sim(monkeypatch) -> _SimClock:
    """Every SessionPolicy agent_runner builds runs on one _SimClock."""
    clock = _SimClock()

    class SimPolicy(sp.SessionPolicy):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.update(clock=clock, sleep=clock.sleep)
            super().__init__(*args, **kwargs)
            clock.policies.append(self)

    monkeypatch.setattr(sp, "SessionPolicy", SimPolicy)
    return clock


def _when(entry, condition: Callable[[], bool], action: Callable[[], Any]) -> None:
    """Runs action once condition() holds (checked once per loop pass), as a task of its own."""
    async def wait() -> None:
        while not condition():
            await _REAL_SLEEP(0)
        action()

    entry.tasks.append(asyncio.ensure_future(wait()))


def _remote(entry, monkeypatch, location: Callable[[], Any] | None = None) -> None:
    """The VPS brain's job (remote mode): remote_compat's discovery hooks stubbed (these jobs have no tools), and
    agent_runner's get_current_city_data, which remote_compat binds to jarvis_location's, replaced."""
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.setattr(entry.ar.remote_compat, "self_test", lambda: None)
    monkeypatch.setattr(entry.ar.remote_compat, "install_arg_hooks", lambda *_a, **_k: None)
    monkeypatch.setattr(entry.ar, "get_current_city_data",
                        location or AsyncMock(return_value=jarvis_location.user_city_location()))


async def _run(entry, ctx) -> None:
    await asyncio.wait_for(asyncio.ensure_future(entry.ar.entrypoint(ctx)), 10)  # its own task, like livekit
    for _ in range(5):  # let the best-effort attribute tasks finish
        await asyncio.sleep(0)
    assert all(task.done() for task in entry.tasks)


def _end_after(delay: float):
    return lambda agent: asyncio.get_running_loop().call_later(delay, agent.request_end, "paused_by_owner")


async def test_the_job_compresses_and_starts_with_the_remembered_turns(entry):
    entry.memory = [{"role": "user", "content": "[typed] pehla sawal"},
                    {"role": "assistant", "content": "pehla jawab"}]
    entry.on_start = _end_after(0.02)
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    (model,) = entry.models
    assert model["context_window_compression"].trigger_tokens == 24000
    assert model["context_window_compression"].sliding_window.target_tokens == 16000
    (assistant,) = entry.assistants
    (first_start,) = entry.starts
    assert len(first_start) == 3 and all(i.startswith(sp.RESEED_ID_PREFIX) for i in first_start)  # before start
    assert [item.text_content for item in assistant.chat_ctx.items] == [
        sp.reseed_note(60), "pehla sawal", "pehla jawab"]
    assert ctx.shutdowns == ["paused_by_owner"]


async def test_a_rebuilt_session_is_never_seeded_twice(entry):
    entry.memory = [{"role": "user", "content": "yaad rakhna"}, {"role": "assistant", "content": "zaroor, Sir"}]

    def on_start(agent):
        session = entry.sessions[-1]
        loop = asyncio.get_running_loop()
        if len(entry.sessions) == 1:
            loop.call_later(0.02, lambda: session.fire("close", types.SimpleNamespace(reason="error")))
        else:
            loop.call_later(0.02, agent.request_end, "paused_by_owner")

    entry.on_start = on_start
    await _run(entry, _FakeJobContext())
    (assistant,) = entry.assistants
    assert len(entry.sessions) == 2 and assistant.reads == 1 and len(assistant.updates) == 1
    assert entry.starts[0] == entry.starts[1] and len(entry.starts[1]) == 3  # the rebuild reuses the one seed


async def test_a_probe_job_is_never_seeded_and_runs_no_policy(entry):
    entry.memory = [{"role": "user", "content": "kuch bhi"}, {"role": "assistant", "content": "ji"}]
    entry.on_start = lambda agent: asyncio.get_running_loop().call_later(0.02, agent.request_end, "probe_done")
    ctx = _FakeJobContext()
    ctx.job = types.SimpleNamespace(id="AJ_probe1", room=types.SimpleNamespace(name="jarvis-probe-1791460000"))
    await _run(entry, ctx)
    (assistant,) = entry.assistants
    assert assistant.reads == 0 and assistant.updates == [] and entry.starts == [[]]
    assert entry.protectors == []  # no protector, so no session_policy loop (K-57)
    assert ctx.shutdowns == ["probe_done"]


async def test_the_policy_runs_on_the_jobs_own_protector(entry):
    from services.utils.jarvis_autonomous import autonomous_protector

    running: list[str] = []

    def on_start(agent):
        loop = asyncio.get_running_loop()
        loop.call_later(0.03, lambda: running.extend(entry.protectors[0].task_names))  # while the job runs
        loop.call_later(0.05, agent.request_end, "paused_by_owner")

    entry.on_start = on_start
    await _run(entry, _FakeJobContext())
    (protector,) = entry.protectors
    assert protector is not autonomous_protector and running == ["session_policy"]
    assert "session_policy" not in autonomous_protector.task_names
    assert protector.task_names == []  # the job's teardown cancelled it with the protector


async def test_twenty_conversation_items_cut_the_jobs_chat_context_to_forty(entry):
    rt = _RtSession()

    def on_start(agent):
        session = entry.sessions[-1]
        for item in _turns(0, 60):
            agent._chat_ctx.items.append(item)
            rt._chat_ctx.items.append(item)
        # the running activity's realtime session (livekit sets agent._activity in session.start)
        agent._activity = types.SimpleNamespace(realtime_llm_session=rt, tools=[], session=None)

        def talk():
            for _ in range(20):
                session.fire("conversation_item_added", types.SimpleNamespace(item=None))

        loop = asyncio.get_running_loop()
        loop.call_later(0.01, talk)
        loop.call_later(0.1, agent.request_end, "paused_by_owner")

    entry.on_start = on_start
    await _run(entry, _FakeJobContext())
    (assistant,) = entry.assistants
    assert _ids(assistant.chat_ctx.items) == rt.ids() == [f"item-{i}" for i in range(20, 60)]  # both copies, once
    assert len(rt.diffs) == 1 and rt.diffs[0].to_create == [] and rt.sent == []  # remove-only, nothing re-sent
    assert assistant.updates == []  # never through agent.update_chat_ctx


async def test_an_idle_job_ends_as_idle_with_jarvis_session_idle_before_closed(entry, monkeypatch):
    """Nobody joins the room (the VoiceLink links nobody): with JARVIS_IDLE_CLOSE_MIN set (here 30 ms, on the real
    clock) the job's policy ends it through request_end('idle'); the C-23 end block writes 'closed' after 'idle' and
    calls ctx.shutdown('idle')."""
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "0.0005")
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    assert ctx.shutdowns == ["idle"]
    assert ctx.room.local_participant.attributes == ["starting", "running", "idle", "closed"]


async def test_a_pc_brain_job_with_nobody_linked_is_never_idle_closed_by_default(entry, sim):
    """The PC brain's bridge ('always' dispatch) refills an empty room at once: an idle close there would only start
    a fresh job (a memory read, a new Gemini session) every half hour while nobody uses JARVIS."""
    entry.on_start = lambda agent: _when(entry, lambda: sim.ticks >= 6 * 60,
                                         lambda: agent.request_end("paused_by_owner"))
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    (policy,) = sim.policies
    assert policy.idle_close_s == 0 and policy.recycle  # the 24 h recycle stays
    assert sim.ticks >= 6 * 60 and ctx.shutdowns == ["paused_by_owner"]  # six quiet hours, never 'idle'
    assert "idle" not in ctx.room.local_participant.attributes


async def test_a_vps_brain_job_with_nobody_linked_ends_as_idle_after_30_minutes(entry, sim, monkeypatch):
    _remote(entry, monkeypatch)
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    (policy,) = sim.policies
    assert policy.idle_close_s == 30 * 60.0 and policy.recycle
    assert ctx.shutdowns == ["idle"] and sim.ticks == 31  # idle from the 1st tick, closed 30 minutes later
    assert ctx.room.local_participant.attributes == ["starting", "running", "idle", "closed"]


@pytest.mark.parametrize("brain", ["vps", "pc"])
async def test_a_person_the_bridge_counts_keeps_the_job_in_both_brains(entry, sim, monkeypatch, brain):
    """The job's voice link links only 'owner-*' on the VPS brain (and on the PC with JARVIS_RPC_OWNER_ONLY), while the
    bridge counts any STANDARD person that is not 'agent-*' or 'probe-*' as demand. Such a person keeps the job in both
    brains (the PC brain's idle close is on here, JARVIS_IDLE_CLOSE_MIN set); another agent and a probe never do, and
    the job ends 30 minutes after the person left."""
    if brain == "vps":
        _remote(entry, monkeypatch)
    else:
        monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "30")  # off by default on the PC brain
        monkeypatch.setenv("JARVIS_RPC_OWNER_ONLY", "1")
    ctx = _FakeJobContext()
    room = ctx.room.remote_participants
    room["web-1a2b3c4d"] = _participant("web-1a2b3c4d")  # a person the bridge counts, never linked by this job
    room["agent-AJ_other"] = _participant("agent-AJ_other", rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    room["probe-1791460000"] = _participant("probe-1791460000")
    entry.on_start = lambda _agent: _when(entry, lambda: sim.ticks >= 3 * 60, lambda: room.pop("web-1a2b3c4d"))
    await _run(entry, ctx)
    (policy,) = sim.policies
    assert policy.idle_close_s == 30 * 60.0 and policy.voice_link.linked_identity() is None
    assert ctx.shutdowns == ["idle"] and 3 * 60 + 30 <= sim.ticks <= 3 * 60 + 31  # 30 minutes after it left
    assert ctx.room.local_participant.attributes == ["starting", "running", "idle", "closed"]


async def test_a_vps_brain_job_takes_a_new_city_from_the_cached_device_location(entry, sim, monkeypatch):
    """The job starts with its process's prewarm-time city; the Desktop Node's location (agent-svc's cache, read
    through jarvis_location) moves it after five agreeing ticks, with one instructions refresh."""
    reads: list[int] = []

    async def device_location() -> dict[str, Any]:
        reads.append(1)
        return {"city": "Islamabad", "lat": 33.68, "lng": 73.04, "source": jarvis_location.SOURCE_DEVICE,
                "ts": time.time()}

    _remote(entry, monkeypatch, device_location)
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "0")  # only the context check here

    def on_start(agent):
        agent.current_date, agent.current_city = "c114-day", "Lahore"
        agent.refresh_instructions = AsyncMock()
        _when(entry, lambda: agent.refresh_instructions.await_count > 0,
              lambda: agent.request_end("paused_by_owner"))

    entry.on_start = on_start
    await _run(entry, _FakeJobContext())
    (assistant,) = entry.assistants
    assert assistant.current_city == "Islamabad" and assistant.refresh_instructions.await_count == 1
    assert len(reads) >= sp.CITY_READINGS


async def test_a_console_job_is_never_idle_closed(entry, sim, monkeypatch):
    """livekit-agents' console mode keeps its job (C-23: _end_job never shuts it down), so an idle close would only
    leave the console without a session: no idle rules there, even with JARVIS_IDLE_CLOSE_MIN set."""
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "30")
    ctx = _FakeJobContext()
    ctx.is_fake_job = lambda: True
    entry.on_start = lambda agent: _when(entry, lambda: sim.ticks >= 25 * 60,
                                         lambda: agent.request_end("paused_by_owner"))
    await _run(entry, ctx)
    (policy,) = sim.policies
    assert policy.idle_close_s == 0 and not policy.recycle
    assert sim.ticks >= 25 * 60 and ctx.shutdowns == []  # console mode keeps its job
    assert "idle" not in ctx.room.local_participant.attributes


async def test_a_running_tool_call_keeps_an_idle_job_until_it_ends(entry, sim, monkeypatch):
    from services.ai_core import jarvis_plugin_manager

    running = {"download_youtube_media"}

    class Plugins:
        def discover_plugins(self, _path):
            return None

        def get_livekit_tools(self):
            return []

        def running_tools(self):
            return set(running)

    monkeypatch.setattr(jarvis_plugin_manager, "JarvisPluginManager", Plugins)
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "30")
    entry.on_start = lambda _agent: _when(entry, lambda: sim.ticks >= 90, running.clear)
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    assert ctx.shutdowns == ["idle"] and 90 <= sim.ticks <= 91  # due at tick 31, held while the tool ran


async def test_messages_transcriptions_and_agent_states_restart_the_idle_count(entry, sim, monkeypatch):
    """The session handlers feed the policy: a typed message (nobody linked: a client whose LiveKit join failed), a
    user transcription and an agent state change each start the 30 minutes again."""
    from services.utils import jarvis_bridge

    monkeypatch.setattr(jarvis_bridge, "notify_ui", AsyncMock())
    monkeypatch.setattr(jarvis_bridge, "notify_thinking", AsyncMock())
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "30")

    def on_start(_agent):
        session = entry.sessions[-1]
        _when(entry, lambda: sim.ticks >= 20,
              lambda: session.fire("conversation_item_added", types.SimpleNamespace(item=None)))
        _when(entry, lambda: sim.ticks >= 45,
              lambda: session.fire("user_input_transcribed", types.SimpleNamespace(transcript="", is_final=False)))
        _when(entry, lambda: sim.ticks >= 70,
              lambda: session.fire("agent_state_changed", types.SimpleNamespace(new_state="listening")))

    entry.on_start = on_start
    ctx = _FakeJobContext()
    await _run(entry, ctx)
    assert ctx.shutdowns == ["idle"] and 100 <= sim.ticks <= 101  # 30 minutes after the last one (tick 70)


# ---------------------------------------------------------------- 6b. jarvis.session with its time (step 6, K-68)


async def test_every_jarvis_session_write_carries_jarvis_session_ts_in_the_same_call(entry, monkeypatch):
    """Plan step 6: jarvis.session 'with ts'. Every write of a job (the runtime's 'starting' and 'running', the
    policy's 'idle', the end block's 'closed') sets jarvis.session_ts in the same set_attributes call: the write's time
    in whole epoch seconds, as text (LiveKit attributes are strings)."""
    monkeypatch.setenv(sp.IDLE_CLOSE_ENV, "0.0005")
    ctx = _FakeJobContext()
    before = int(time.time())
    await _run(entry, ctx)
    after = int(time.time())
    writes = [call for call in ctx.room.local_participant.calls if "jarvis.session" in call]
    assert [write["jarvis.session"] for write in writes] == ["starting", "running", "idle", "closed"]
    for write in writes:
        assert set(write) == {"jarvis.session", "jarvis.session_ts"}
        stamp = write["jarvis.session_ts"]
        assert isinstance(stamp, str) and stamp.isdigit() and before <= int(stamp) <= after


async def test_the_session_time_rides_on_the_one_best_effort_write():
    from src.core import agent_runner as ar

    local = _FakeLocal()
    ctx = types.SimpleNamespace(room=types.SimpleNamespace(local_participant=local))
    assert await ar._write_session_attr(ctx, "restarting") is True
    task = ar._set_session_attr(ctx, "running")  # the never-blocking hook the runtime and the policy use
    assert task is not None and await asyncio.wait_for(task, 2) is True
    assert [sorted(call) for call in local.calls] == [[ar.SESSION_ATTR, ar.SESSION_TS_ATTR]] * 2
    assert ar.SESSION_TS_ATTR == "jarvis.session_ts"

    class Gone:
        async def set_attributes(self, _attributes: dict[str, str]) -> None:
            raise RuntimeError("room gone")

    gone = types.SimpleNamespace(room=types.SimpleNamespace(local_participant=Gone()))
    assert await ar._write_session_attr(gone, "closed") is False  # best effort as before: never raises


def test_the_bridge_reads_jarvis_session_and_ignores_jarvis_session_ts():
    """The bridge's room owner reads only lk.agent.name and jarvis.session of an agent participant: one that also
    carries jarvis.session_ts is voice-ready exactly as before, and the status form never shows the extra key."""
    from livekit.protocol import models
    from src.core import bridge_rooms

    manager = bridge_rooms.RoomManager(bridge_rooms.RoomSettings(), api_factory=lambda: None)
    kind, active = models.ParticipantInfo.Kind.AGENT, models.ParticipantInfo.State.ACTIVE  # pylint: disable=no-member
    for attributes in ({"jarvis.session": "running"},
                       {"jarvis.session": "running", "jarvis.session_ts": str(int(time.time()))}):
        participant = models.ParticipantInfo(identity="agent-AJ_c114", kind=kind, state=active, attributes=attributes)
        info = manager._agent_info(participant, {})
        assert info.session == "running" and info.voice_ready and info.job_id == "AJ_c114"
        assert info.public()["jarvis.session"] == "running" and "jarvis.session_ts" not in info.public()
    closed = models.ParticipantInfo(identity="agent-AJ_c114", kind=kind, state=active,
                                    attributes={"jarvis.session": "closed", "jarvis.session_ts": "1791460000"})
    assert not manager._agent_info(closed, {}).voice_ready  # the state still decides, never the time


# ---------------------------------------------------------------- 7. the C-33 error path (steps 5 and 6)


async def test_a_dead_gemini_session_is_rebuilt_in_place_or_the_job_ends_as_session_error():
    """A forced Gemini block ends either resumed (rebuilt in place) or with the job ending as 'session_error' (the
    bridge dispatches a fresh one); never a mute agent left in the room. The reason stays 'session_error' (K-60 v16),
    not the plan's 'session_dead'."""
    from src.core.jarvis_runtime import END_SESSION_ERROR, RESTART_CAP, JarvisRuntime

    class Session:
        def __init__(self) -> None:
            self.handlers: dict[str, list[Any]] = {}
            self.aclosed = 0

        async def start(self) -> None:
            return None

        async def aclose(self) -> None:
            self.aclosed += 1

        def on(self, event: str, handler: Any) -> None:
            self.handlers.setdefault(event, []).append(handler)

        def off(self, event: str, handler: Any) -> None:
            if handler in self.handlers.get(event, []):
                self.handlers[event].remove(handler)

        def emit(self, event: str, payload: Any) -> None:
            for handler in list(self.handlers.get(event, [])):
                handler(payload)

    sessions: list[Session] = []
    attrs: list[str] = []
    shutdowns: list[str] = []
    ctx = types.SimpleNamespace(room=MagicMock(), job=types.SimpleNamespace(id="AJ_c114"),
                                connect=AsyncMock(), shutdown=lambda reason="": shutdowns.append(reason))

    def factory() -> Session:
        sessions.append(Session())
        return sessions[-1]

    async def no_wait(_seconds: float) -> None:
        return None

    runtime = JarvisRuntime(ctx, session_factory=factory, set_session_attr=attrs.append, sleep=no_wait)
    assert await runtime.start()
    gemini_died = types.SimpleNamespace(error=types.SimpleNamespace(recoverable=False))
    sessions[-1].emit("error", gemini_died)  # livekit closes the session itself once the old activity drained
    sessions[-1].emit("close", types.SimpleNamespace(reason="error"))
    await _settle()
    assert len(sessions) == 2 and runtime.session is sessions[1] and not runtime.closed.is_set()  # resumed
    assert attrs == ["starting", "running", "restarting", "running"]

    for _ in range(RESTART_CAP):  # Gemini stays blocked: every rebuild dies again
        sessions[-1].emit("error", gemini_died)
        sessions[-1].emit("close", types.SimpleNamespace(reason="error"))
        await _settle()
    assert await asyncio.wait_for(runtime.wait_closed(), 2) == END_SESSION_ERROR == "session_error"
    await runtime.aclose(runtime.reason)
    assert shutdowns == ["session_error"]  # the agent leaves; nothing named 'session_dead'
