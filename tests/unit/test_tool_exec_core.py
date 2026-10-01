"""Plan task C-27: tool execution core (contracts K-11, K-12, K-13) on the real plugin manager.

Tools are registered under unique 'c27_' names and removed again after every test. LLM calls are
made exactly like LiveKit makes them (function_tool + prepare_function_arguments). The bridge's
notify_event and jarvis_audit.log_tool are replaced by recorders and a security.log written by a
test goes to tmp_path, so nothing reaches the network, the desktop, the live agent or logs/.
Since C-28 a medium/high call only asks for confirmation, so the medium/high calls here are made
as the approved re-run (_confirmed=True); test_confirm_broker.py covers the confirmation itself.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from livekit.agents.llm import function_tool
from livekit.agents.llm.utils import prepare_function_arguments

from services.ai_core import jarvis_plugin_manager as pm_module
from services.ai_core.jarvis_plugin_manager import jarvis_tool, plugin_manager
from services.utils import jarvis_audit, jarvis_bridge, jarvis_security
from services.utils.jarvis_config import config
from services.utils.jarvis_resilience import resilience_manager
from services.utils.jarvis_security import VortexGuard


@pytest.fixture(autouse=True)
def _restore_registry_and_breakers():
    tools_lock = plugin_manager._tools_lock  # pylint: disable=protected-access
    with tools_lock:
        saved = plugin_manager.tools.copy()
    yield
    with tools_lock:
        plugin_manager.tools[:] = saved
    breakers = resilience_manager._breakers  # pylint: disable=protected-access
    for stale in [name for name in list(breakers) if name.startswith("c27_")]:
        breakers.pop(stale, None)


@pytest.fixture(name="events", autouse=True)
def _events(monkeypatch):
    """tool_sync payloads in the order they were posted (the bridge client is replaced)."""
    sent: list[dict[str, Any]] = []

    async def fake_notify_event(event_type: str, payload: Any) -> None:
        if event_type == "tool_sync":
            sent.append(payload)

    monkeypatch.setattr(jarvis_bridge, "notify_event", fake_notify_event)
    return sent


@pytest.fixture(name="audits", autouse=True)
def _audits(monkeypatch):
    """jarvis_audit.log_tool calls (nothing is written to logs/security.log)."""
    rows: list[tuple[Any, ...]] = []
    monkeypatch.setattr(jarvis_audit, "log_tool", lambda *args: rows.append(args))
    return rows


@pytest.fixture(autouse=True)
def _desktop_control_on(monkeypatch):
    monkeypatch.setattr(config, "desktop_control_enabled", True, raising=False)


def _wrapper(name: str) -> Any:
    """The wrapper LiveKit receives for the tool called name."""
    found = [registered for registered in plugin_manager.tools if registered.__name__ == name]
    assert len(found) == 1, f"{name}: {len(found)} registrations"
    return found[0]


async def _call_like_livekit(name: str, arguments: dict[str, Any], *,
                             confirmed: bool = False) -> Any:
    """
    Calls the tool as LiveKit does (prepare_function_arguments binds arguments positionally);
    confirmed=True makes it the approved re-run of a medium/high call (C-28, K-14).
    """
    lk_tool = function_tool(_wrapper(name))
    args, kwargs = prepare_function_arguments(fnc=lk_tool, json_arguments=json.dumps(arguments))
    if confirmed:
        kwargs = {**kwargs, "_confirmed": True}
    return await lk_tool(*args, **kwargs)


async def _drain_tool_sync() -> None:
    """Waits until every background tool_sync post has been handed to notify_event."""
    for _ in range(100):
        pending = [t for t in pm_module._sync_tasks if not t.done()]  # pylint: disable=protected-access
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _phases(events: list[dict[str, Any]], tool: str) -> list[tuple[str, str]]:
    return [(e["phase"], e["status"]) for e in events if e["tool"] == tool]


def _security_handlers() -> list[logging.Handler]:
    return [h for h in jarvis_audit.security_logger.handlers
            if getattr(h, "jarvis_security_log", False)]


@contextlib.contextmanager
def _security_log_at(path: Any) -> Iterator[None]:
    """Points the security.log handler at path for the duration of the block."""
    real = _security_handlers()
    for handler in real:
        jarvis_audit.security_logger.removeHandler(handler)
    temporary = jarvis_audit.security_log_handler(str(path))
    jarvis_audit.security_logger.addHandler(temporary)
    try:
        yield
    finally:
        jarvis_audit.security_logger.removeHandler(temporary)
        temporary.close()
        for handler in real:
            jarvis_audit.security_logger.addHandler(handler)


# ---------------------------------------------------------------- bound arguments and risk


async def test_defaults_reach_the_risk_callable(events, audits):
    seen: list[dict[str, Any]] = []

    def write_risk(args: dict[str, Any]) -> str:
        seen.append(dict(args))
        return "medium" if args["auto_run"] and args["filename"].endswith(".py") else "low"

    @jarvis_tool(risk=write_risk, raw_args=("content",))
    async def c27_write_code(content: str, filename: str, auto_run: bool = True) -> dict:
        """Writes code to a file.

        Args:
            content: the code
            filename: file name
            auto_run: run it after writing
        """
        return {"status": "success", "message": f"{filename} likh di"}

    assert await _call_like_livekit("c27_write_code", {"content": "print(1)\n", "filename": "a.py"},
                                    confirmed=True) \
        == {"status": "success", "message": "a.py likh di"}
    await _wrapper("c27_write_code")(content="x = 1", filename="b.py", _confirmed=True)
    await _wrapper("c27_write_code")("y", "c.py", False)

    assert seen == [
        {"content": "print(1)\n", "filename": "a.py", "auto_run": True},
        {"content": "x = 1", "filename": "b.py", "auto_run": True},
        {"content": "y", "filename": "c.py", "auto_run": False},
    ]
    await _drain_tool_sync()
    assert [e["risk"] for e in events if e["phase"] == "start"] == ["medium", "medium", "low"]
    assert [row[:3] for row in audits] == [("c27_write_code", "medium", "success")] * 2


async def test_a_failing_or_invalid_risk_callable_counts_as_high(events, audits):
    @jarvis_tool(risk=lambda args: 1 / 0)
    async def c27_risk_raises() -> str:
        """Risk callable that raises."""
        return "ran"

    @jarvis_tool(risk=lambda args: "critical")
    async def c27_risk_invalid() -> str:
        """Risk callable with an unknown answer."""
        return "ran"

    assert await _call_like_livekit("c27_risk_raises", {}, confirmed=True) == "ran"
    assert await _call_like_livekit("c27_risk_invalid", {}, confirmed=True) == "ran"
    await _drain_tool_sync()
    assert {e["risk"] for e in events} == {"high"}
    assert [row[:3] for row in audits] == [("c27_risk_raises", "high", "success"),
                                           ("c27_risk_invalid", "high", "success")]


async def test_positional_args_without_a_policy_reach_the_tool_unchanged():
    received: dict[str, Any] = {}

    def message_risk(args: dict[str, Any]) -> str:
        received["risk_args"] = dict(args)
        return "low"

    @jarvis_tool(risk=message_risk, exclusive="desktop_input", timeout=5)
    async def c27_send_message(contact: str, message: str, urgent: bool = False) -> str:
        """Sends a message.

        Args:
            contact: who
            message: text
            urgent: mark it urgent
        """
        received.update(contact=contact, message=message)
        return "sent"

    message = "Salam!\nKal 5 baje; a & b | `x` $HOME <tag> 100%\r\nOK"
    # K-11 v2 transition: LiveKit passes every argument positionally and an argument without a
    # declared policy keeps the pre-v2 behaviour, so newlines and ;&|`$<> arrive unchanged.
    assert await _call_like_livekit("c27_send_message",
                                    {"contact": "Ali <Office>", "message": message}) == "sent"
    assert (received["contact"], received["message"]) == ("Ali <Office>", message)
    assert received["risk_args"] == {"contact": "Ali <Office>", "message": message, "urgent": False}

    # A keyword argument without a policy still gets the old plain sanitizing.
    assert await _wrapper("c27_send_message")("Ali", message=message) == "sent"
    assert received["message"] == VortexGuard.sanitize_input(message)


# ---------------------------------------------------------------- exclusive lock


async def test_exclusive_lock_serializes_two_desktop_calls():
    timeline: list[str] = []

    async def body(tag: str) -> str:
        timeline.append(f"{tag}-in")
        await asyncio.sleep(0.15)
        timeline.append(f"{tag}-out")
        return tag

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_type_one(text: str) -> str:
        """First typing tool.

        Args:
            text: text
        """
        return await body("one")

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_type_two(text: str) -> str:
        """Second typing tool.

        Args:
            text: text
        """
        return await body("two")

    @jarvis_tool(timeout=5)
    async def c27_free(text: str) -> str:
        """A tool without the lock.

        Args:
            text: text
        """
        return await body("free")

    results = await asyncio.gather(_call_like_livekit("c27_type_one", {"text": "a"}),
                                   _call_like_livekit("c27_type_two", {"text": "b"}),
                                   _call_like_livekit("c27_free", {"text": "c"}))
    assert results == ["one", "two", "free"]
    desktop = [step for step in timeline if not step.startswith("free")]
    assert desktop in (["one-in", "one-out", "two-in", "two-out"],
                       ["two-in", "two-out", "one-in", "one-out"])
    # A tool without the lock does not wait for it.
    assert timeline.index("free-in") < timeline.index(desktop[2])


async def test_lock_stays_held_until_a_timed_out_tools_worker_returns(events):
    stamps: dict[str, float] = {}

    async def run_cancellable(fn: Any) -> Any:
        """Contract K-25 run_cancellable: on cancel, set the event and wait up to 5 s for the worker."""
        cancel_event = threading.Event()
        future = asyncio.get_running_loop().run_in_executor(None, fn, cancel_event)
        try:
            # Shielded: a cancel would otherwise cancel the executor future itself and the wait
            # below would return at once while the thread keeps running.
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            cancel_event.set()
            await asyncio.wait({future}, timeout=5.0)
            raise

    def typing_worker(cancel_event: threading.Event) -> str:
        cancel_event.wait(5)
        time.sleep(0.3)  # keystrokes that were already queued
        stamps["worker_done"] = time.monotonic()
        return "stopped"

    @jarvis_tool(exclusive="desktop_input", timeout=0.2)
    async def c27_slow_typer(text: str) -> str:
        """Types slowly.

        Args:
            text: text
        """
        return await run_cancellable(typing_worker)

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_next_typer(text: str) -> str:
        """Types next.

        Args:
            text: text
        """
        stamps["next_start"] = time.monotonic()
        return "typed"

    first = asyncio.create_task(_call_like_livekit("c27_slow_typer", {"text": "a"}))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(_call_like_livekit("c27_next_typer", {"text": "b"}))
    timed_out, typed = await asyncio.gather(first, second)

    assert timed_out["status"] == "error" and timed_out["message"].startswith("TOOL_TIMEOUT")
    assert typed == "typed"
    assert stamps["next_start"] >= stamps["worker_done"]
    await _drain_tool_sync()
    assert _phases(events, "c27_slow_typer") == [("start", "running"), ("end", "timeout")]
    assert _phases(events, "c27_next_typer") == [("start", "running"), ("end", "success")]


async def test_sync_tool_runs_in_a_thread_and_keeps_the_lock_until_it_returns():
    stamps: dict[str, Any] = {}

    @jarvis_tool(exclusive="desktop_input", timeout=0.2)
    def c27_sync_typer(text: str) -> str:
        """A blocking tool that outlives its timeout.

        Args:
            text: text
        """
        stamps["thread"] = threading.get_ident()
        time.sleep(0.5)
        stamps["sync_done"] = time.monotonic()
        return "late"

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    def c27_sync_quick(text: str) -> str:
        """A quick blocking tool.

        Args:
            text: text
        """
        stamps["quick_start"] = time.monotonic()
        return f"quick {text}"

    first = asyncio.create_task(_call_like_livekit("c27_sync_typer", {"text": "a"}))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(_call_like_livekit("c27_sync_quick", {"text": "b"}))
    timed_out, quick = await asyncio.gather(first, second)

    assert timed_out["message"].startswith("TOOL_TIMEOUT")
    assert quick == "quick b"
    assert stamps["thread"] != threading.get_ident()
    assert stamps["quick_start"] >= stamps["sync_done"]


async def test_a_call_that_cannot_get_the_lock_returns_desktop_busy(monkeypatch, events):
    monkeypatch.setattr(pm_module, "EXCLUSIVE_WAIT_S", 0.1)
    release = asyncio.Event()
    ran: list[str] = []

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_holder(text: str) -> str:
        """Holds the desktop.

        Args:
            text: text
        """
        await release.wait()
        return "held"

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_waiter(text: str) -> str:
        """Wants the desktop.

        Args:
            text: text
        """
        ran.append(text)
        return "ran"

    holder = asyncio.create_task(_call_like_livekit("c27_holder", {"text": "a"}))
    await asyncio.sleep(0.02)
    busy = await _call_like_livekit("c27_waiter", {"text": "b"})
    assert set(busy) == {"status", "tool", "error_code", "message"}
    assert (busy["status"], busy["tool"], busy["error_code"]) == ("error", "c27_waiter", "DESKTOP_BUSY")
    assert isinstance(busy["message"], str) and busy["message"]
    assert not ran

    release.set()
    assert await holder == "held"
    assert await _call_like_livekit("c27_waiter", {"text": "c"}) == "ran"
    await _drain_tool_sync()
    assert _phases(events, "c27_waiter") == [("start", "running"), ("end", "error"),
                                             ("start", "running"), ("end", "success")]


async def test_a_cancelled_call_ends_cancelled_and_frees_the_lock(events):
    entered = asyncio.Event()

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_forever(text: str) -> str:
        """Never finishes.

        Args:
            text: text
        """
        entered.set()
        await asyncio.sleep(10)
        return "never"

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_after(text: str) -> str:
        """Runs after.

        Args:
            text: text
        """
        return "after"

    task = asyncio.create_task(_call_like_livekit("c27_forever", {"text": "a"}))
    await entered.wait()
    assert "c27_forever" in plugin_manager.running_tools()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "c27_forever" not in plugin_manager.running_tools()
    assert await asyncio.wait_for(_call_like_livekit("c27_after", {"text": "b"}), 1.0) == "after"
    await _drain_tool_sync()
    assert _phases(events, "c27_forever") == [("start", "running"), ("end", "cancelled")]
    assert _phases(events, "c27_after") == [("start", "running"), ("end", "success")]


async def test_a_tool_awaiting_another_tools_wrapper_does_not_deadlock(monkeypatch, events):
    monkeypatch.setattr(pm_module, "EXCLUSIVE_WAIT_S", 0.3)

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_inner_click(x: int) -> str:
        """Clicks.

        Args:
            x: position
        """
        return f"clicked {x}"

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_outer_macro(x: int) -> str:
        """Runs the click tool through its wrapper.

        Args:
            x: position
        """
        return await _wrapper("c27_inner_click")(x)

    assert await _call_like_livekit("c27_outer_macro", {"x": 3}) == "clicked 3"
    # Internal Python callers get the raw function and bypass the wrapper (K-11).
    assert await c27_inner_click(4) == "clicked 4"
    await _drain_tool_sync()
    assert _phases(events, "c27_outer_macro") == [("start", "running"), ("end", "success")]
    assert _phases(events, "c27_inner_click") == [("start", "running"), ("end", "success")]


def test_exclusive_locks_work_in_more_than_one_event_loop():
    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_loop_tool(text: str) -> str:
        """Briefly holds the desktop.

        Args:
            text: text
        """
        await asyncio.sleep(0.02)
        return text

    async def two_calls() -> list[Any]:
        results = await asyncio.gather(_call_like_livekit("c27_loop_tool", {"text": "a"}),
                                       _call_like_livekit("c27_loop_tool", {"text": "b"}))
        await _drain_tool_sync()
        return list(results)

    # The second call waits for the lock in each loop; one shared asyncio.Lock would fail in the
    # second loop with 'bound to a different event loop'.
    assert asyncio.run(two_calls()) == ["a", "b"]
    assert asyncio.run(two_calls()) == ["a", "b"]


# ---------------------------------------------------------------- kill switch


async def test_kill_switch_blocks_only_desktop_input_tools(monkeypatch, events):
    monkeypatch.setattr(config, "desktop_control_enabled", False, raising=False)
    ran: list[str] = []

    @jarvis_tool(exclusive="desktop_input")
    async def c27_keyboard(text: str) -> str:
        """Types.

        Args:
            text: text
        """
        ran.append("keyboard")
        return "typed"

    @jarvis_tool(exclusive="browser")
    async def c27_browser_tab(url: str) -> str:
        """Opens a tab.

        Args:
            url: address
        """
        ran.append("browser")
        return "opened"

    @jarvis_tool
    async def c27_battery() -> str:
        """Battery level."""
        ran.append("battery")
        return "80%"

    blocked = await _call_like_livekit("c27_keyboard", {"text": "hello"})
    assert set(blocked) == {"status", "tool", "error_code", "message"}
    assert (blocked["status"], blocked["tool"], blocked["error_code"]) == \
        ("error", "c27_keyboard", "DESKTOP_CONTROL_DISABLED")
    assert await _call_like_livekit("c27_browser_tab", {"url": "https://example.com"}) == "opened"
    assert await _call_like_livekit("c27_battery", {}) == "80%"
    assert ran == ["browser", "battery"]
    await _drain_tool_sync()
    assert _phases(events, "c27_keyboard") == [("start", "running"), ("end", "blocked")]


async def test_kill_switch_also_stops_a_call_that_waited_for_the_lock(monkeypatch):
    release = asyncio.Event()
    ran: list[str] = []

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_first_input(text: str) -> str:
        """Holds the desktop.

        Args:
            text: text
        """
        await release.wait()
        return "first"

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c27_second_input(text: str) -> str:
        """Queued behind the first.

        Args:
            text: text
        """
        ran.append(text)
        return "second"

    first = asyncio.create_task(_call_like_livekit("c27_first_input", {"text": "a"}))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(_call_like_livekit("c27_second_input", {"text": "b"}))
    await asyncio.sleep(0.02)
    monkeypatch.setattr(config, "desktop_control_enabled", False, raising=False)
    release.set()
    assert await first == "first"
    assert (await second)["error_code"] == "DESKTOP_CONTROL_DISABLED"
    assert not ran


# ---------------------------------------------------------------- tool_sync events


async def test_each_call_emits_one_start_and_one_end_event_with_the_same_id(monkeypatch, events):
    monkeypatch.setattr(jarvis_security, "redact_secrets",
                        lambda text: text.replace("TOPSECRET-123", "***"), raising=False)

    @jarvis_tool(raw_args=("text",), risk="medium")
    async def c27_type_text(text: str, window: str = "Notepad", times: int = 1) -> dict:
        """Types text.

        Args:
            text: what to type
            window: target window
            times: how often
        """
        return {"status": "success", "message": "Likh diya TOPSECRET-123 " + "x" * 400}

    secret_text = "password TOPSECRET-123\nline two"
    for _ in range(2):
        await _call_like_livekit("c27_type_text", {"text": secret_text, "window": "Word"},
                                 confirmed=True)
    await _drain_tool_sync()

    assert len(events) == 4
    by_id: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_id.setdefault(event["id"], []).append(event)
    assert len(by_id) == 2
    for call_id, (start, end) in by_id.items():
        assert re.fullmatch(r"[0-9a-f]{32}", call_id)
        assert (start["phase"], start["status"]) == ("start", "running")
        assert (end["phase"], end["status"]) == ("end", "success")
        for event in (start, end):
            assert (event["tool"], event["action"], event["risk"], event["source"]) == \
                ("c27_type_text", "c27_type_text", "medium", "plugin")
            assert event["details"] == f"text=<{len(secret_text)} chars>, window=Word"
            assert isinstance(event["timestamp"], float)
        assert "duration_ms" not in start and "result_summary" not in start
        assert isinstance(end["duration_ms"], int) and end["duration_ms"] >= 0
        assert end["result_summary"].startswith("Likh diya *** x")
        assert len(end["result_summary"]) <= 200
    assert "TOPSECRET" not in json.dumps(events)


async def test_an_end_event_is_posted_only_after_a_slow_start_post(monkeypatch):
    posted: list[tuple[str, str]] = []
    release_start = asyncio.Event()

    async def slow_start_bridge(event_type: str, payload: Any) -> None:
        if event_type == "tool_sync":
            if payload["phase"] == "start":
                await release_start.wait()
            posted.append((payload["phase"], payload["status"]))

    monkeypatch.setattr(jarvis_bridge, "notify_event", slow_start_bridge)

    @jarvis_tool
    async def c27_quick_answer() -> str:
        """Answers at once."""
        return "80%"

    async def spin() -> None:
        for _ in range(10):
            await asyncio.sleep(0)

    assert await _call_like_livekit("c27_quick_answer", {}) == "80%"
    await spin()
    # The bridge relays events in arrival order and the UI keeps the last one per id, so an end
    # posted before a slow start would show the finished call as running again. The loop clock
    # jumps an hour ahead: no time limit on the start post may let the end go first.
    loop = asyncio.get_running_loop()
    real_time = loop.time
    with pytest.MonkeyPatch.context() as clock:
        clock.setattr(loop, "time", lambda: real_time() + 3600.0)
        await spin()
    assert not posted
    release_start.set()
    await _drain_tool_sync()
    assert posted == [("start", "running"), ("end", "success")]


async def test_details_are_capped_and_on_one_line(events):
    @jarvis_tool
    async def c27_long_args(query: str, note: str = "") -> str:
        """Long arguments.

        Args:
            query: search
            note: note
        """
        return "ok"

    await _call_like_livekit("c27_long_args", {"query": "a\nb " + "q" * 300, "note": "n" * 300})
    await _drain_tool_sync()
    details = events[0]["details"]
    assert len(details) <= 160 and "\n" not in details
    assert details.startswith("query=a b qqq")


async def test_running_tools_and_current_tool_call(events):
    seen: dict[str, Any] = {}

    @jarvis_tool
    async def c27_battery_status() -> str:
        """Battery level."""
        seen["running"] = plugin_manager.running_tools()
        seen["call"] = pm_module.current_tool_call.get()
        return "80%"

    assert await _call_like_livekit("c27_battery_status", {}) == "80%"
    assert "c27_battery_status" in seen["running"]
    assert "c27_battery_status" not in plugin_manager.running_tools()
    assert pm_module.current_tool_call.get() is None
    await _drain_tool_sync()
    assert seen["call"] == {"id": events[0]["id"], "tool": "c27_battery_status"}
    assert {e["id"] for e in events} == {seen["call"]["id"]}


async def test_a_timeout_ends_with_status_timeout(events):
    @jarvis_tool(timeout=0.05)
    async def c27_hangs() -> str:
        """Hangs."""
        await asyncio.sleep(5)
        return "late"

    result = await _call_like_livekit("c27_hangs", {})
    assert result["status"] == "error" and result["message"].startswith("TOOL_TIMEOUT")
    await _drain_tool_sync()
    assert _phases(events, "c27_hangs") == [("start", "running"), ("end", "timeout")]
    assert events[1]["result_summary"].startswith("TOOL_TIMEOUT")


async def test_end_status_follows_the_tool_result(events):
    @jarvis_tool
    async def c27_not_found() -> dict:
        """Finds nothing."""
        return {"status": "not_found", "message": "Koi file nahi mili."}

    @jarvis_tool
    async def c27_raises() -> str:
        """Fails."""
        raise ValueError("boom")

    @jarvis_tool(arg_policies={"target": "path"})
    async def c27_open_path(target: str) -> str:
        """Opens a path.

        Args:
            target: path
        """
        return "opened"

    @jarvis_tool(permissions=["system:control"])
    async def c27_needs_control() -> str:
        """Needs a permission this module does not have."""
        return "ran"

    @jarvis_tool
    async def c27_minimize_window(window_name: str) -> str:
        """Minimizes a window the legacy way (a failure is a plain '❌ ...' string).

        Args:
            window_name: window title
        """
        return f"❌ '{window_name}' naam ki koi window nahi mili."

    await _call_like_livekit("c27_not_found", {})
    assert (await _call_like_livekit("c27_raises", {}))["error_type"] == "ValueError"
    assert (await _call_like_livekit("c27_open_path", {"target": "..\\..\\Windows"}))["error_code"] \
        == "PATH_NOT_ALLOWED"
    assert (await _call_like_livekit("c27_needs_control", {})).startswith("SECURITY BLOCK")
    missing = "❌ 'xyz' naam ki koi window nahi mili."
    assert await _call_like_livekit("c27_minimize_window", {"window_name": "xyz"}) == missing
    await _drain_tool_sync()
    ends = {e["tool"]: e for e in events if e["phase"] == "end"}
    assert {tool: end["status"] for tool, end in ends.items()} == {
        "c27_not_found": "not_found", "c27_raises": "error", "c27_open_path": "error",
        "c27_needs_control": "blocked", "c27_minimize_window": "error"}
    assert ends["c27_minimize_window"]["result_summary"] == missing
    assert len(events) == 10


@pytest.mark.parametrize(("result", "expected"), [
    ("plain text", "success"), (None, "success"), ({"battery": 80}, "success"),
    # Legacy tools report a failure as plain text that starts with '❌' or 'Error'.
    ("❌ 'xyz' naam ki koi window nahi mili.", "error"), ("\n  ❌ Invalid key: f13", "error"),
    ("Error: Vision analysis failed: timeout", "error"), ("Error setting reminder: kal", "error"),
    ("ERROR: Undecipherable tool output.", "error"), ("Errors: 0, sab theek hai", "success"),
    ("Koi error nahi aaya.", "success"),
    ({"status": "success"}, "success"), ({"status": "not_found"}, "not_found"),
    ({"status": "degraded"}, "degraded"), ({"status": "started"}, "started"),
    ({"status": "needs_confirmation", "confirm_id": "x"}, "needs_confirmation"),
    ({"status": "cancelled"}, "cancelled"), ({"status": "empty"}, "success"),
    ({"status": "partial_success"}, "degraded"), ({"status": "error"}, "error"),
    ({"status": "something_new"}, "error"), ({"error": "TOOL_PAYLOAD_TOO_LARGE"}, "error"),
    ({"status": "error", "error_code": "DESKTOP_CONTROL_DISABLED"}, "blocked"),
    ({"status": "error", "error_code": "TIMEOUT"}, "timeout"),
])
def test_end_status_mapping(result, expected):
    assert pm_module._sync_status(result) == expected  # pylint: disable=protected-access


# ---------------------------------------------------------------- audit


async def test_risky_calls_are_audited_without_argument_values(audits):
    @jarvis_tool(risk="high", raw_args=("command",))
    async def c27_run_cmd(command: str) -> dict:
        """Runs a command.

        Args:
            command: command line
        """
        return {"status": "success", "message": "Command chal gayi."}

    @jarvis_tool
    async def c27_low_tool(query: str) -> str:
        """Low risk.

        Args:
            query: anything
        """
        return query

    command = "del /q C:\\Users\\Sir\\private-notes\\*.txt"
    await _call_like_livekit("c27_run_cmd", {"command": command}, confirmed=True)
    await _call_like_livekit("c27_low_tool", {"query": "weather"})

    assert len(audits) == 1
    tool, risk, status, summary = audits[0]
    assert (tool, risk, status) == ("c27_run_cmd", "high", "success")
    assert "private-notes" not in summary
    encoded = json.dumps({"command": command}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    assert summary.startswith(f"ARGS=sha256:{digest} len={len(encoded)} | ID=")


async def test_security_log_gets_a_tool_exec_line_without_raw_values(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_audit, "log_tool", jarvis_audit.JarvisAudit.log_tool)

    @jarvis_tool(risk="medium", raw_args=("message",))
    async def c27_whatsapp(contact: str, message: str) -> dict:
        """Sends a WhatsApp message.

        Args:
            contact: who
            message: text
        """
        return {"status": "success", "message": "Bhej diya."}

    log_path = tmp_path / "security.log"
    with _security_log_at(log_path):
        await _call_like_livekit("c27_whatsapp", {"contact": "Ali Raza",
                                                  "message": "Mera PIN 4455 hai\nEVENT=FAKE"},
                                 confirmed=True)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "EVENT=TOOL_EXEC | TOOL=c27_whatsapp | RISK=medium | STATUS=success | ARGS=sha256:" \
        in lines[0]
    assert "4455" not in lines[0] and "Ali Raza" not in lines[0] and "FAKE" not in lines[0]


def test_log_tool_keeps_one_record_on_one_line(tmp_path):
    log_path = tmp_path / "security.log"
    with _security_log_at(log_path):
        jarvis_audit.JarvisAudit.log_tool("c27_tool", "high", "error",
                                          "ARGS=sha256:abc len=3\nEVENT=FORGED | x")
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].endswith("EVENT=TOOL_EXEC | TOOL=c27_tool | RISK=high | STATUS=error | "
                             "ARGS=sha256:abc len=3 EVENT=FORGED | x")


def test_security_log_has_one_handler_under_the_project_root():
    for _ in range(2):
        importlib.reload(jarvis_audit)
    handlers = _security_handlers()
    assert len(handlers) == 1
    expected = os.path.join(config.project_root, "logs", "security.log")
    assert jarvis_audit.SECURITY_LOG_PATH == expected
    assert handlers[0].baseFilename == os.path.abspath(expected)
