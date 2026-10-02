"""Desktop Node tool executor (plan task C-120 step 5; K-11 v4, K-16, K-70).

Every tool here is a fake coroutine or a fake K-11 wrapper; no desktop automation module is imported and
nothing touches the mouse, the keyboard or a window. Blocking 'tool threads' wait on threading.Events that the
test releases.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Mapping
from typing import Any

import pytest

from services.ai_core import tool_placement
from src.core.desktop import executor as ex
from src.core.desktop.executor import ExecCall, Executor, ToolLoop, ToolSpec


@pytest.fixture
def tool_loop():
    loop = ToolLoop(max_workers=4).start()
    yield loop
    loop.stop()


def _spec(name: str, invoke: Any, *, input_lock: bool = True, cls: str = "input", timeout_s: float = 30.0) -> ToolSpec:
    return ToolSpec(name=name, cls=cls, param_schema_sha256="0" * 64, invoke=invoke,
                    exclusive=ex.INPUT_LOCK if input_lock else None, timeout_s=timeout_s)


def _call(call_id: str, spec: ToolSpec, *, lock_wait_s: float = 5.0, timeout_s: float | None = None) -> ExecCall:
    return ExecCall(call_id=call_id, tool=spec.name, args={}, spec=spec, timeout_s=timeout_s or spec.timeout_s,
                    lock_wait_s=lock_wait_s, cls=spec.cls)


class ThreadTool:
    """A tool whose real work is a thread (as a K-25 typing tool) that keeps going after a cancel until
    release() (or ignores the cancel for hold_s)."""

    def __init__(self) -> None:
        self.release_event = threading.Event()
        self.thread_started = threading.Event()
        self.thread_ended_at: float | None = None

    def work(self) -> str:
        self.thread_started.set()
        self.release_event.wait(10.0)
        self.thread_ended_at = time.monotonic()
        return "typed"

    async def invoke(self, _args: Mapping[str, Any]) -> dict[str, Any]:
        return {"status": "success", "tool": "type_text_tool", "message": await asyncio.to_thread(self.work)}

    def release(self) -> None:
        self.release_event.set()


async def _quick(_args: Mapping[str, Any]) -> dict[str, Any]:
    return {"status": "success", "tool": "quick", "message": "done"}


async def _wait_until(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


# --- counting and the input lock -----------------------------------------------------------------------------

async def test_thread_work_is_counted_against_its_call(tool_loop):
    tool = ThreadTool()
    executor = Executor(tool_loop)
    handle = executor.start(_call("call-count", _spec("type_text_tool", tool.invoke)))
    await asyncio.to_thread(tool.thread_started.wait, 5.0)
    assert tool_loop.pool.pending("call-count") == 1 and tool_loop.pool.total() == 1
    tool.release()
    outcome = await executor.wait(handle, 5.0)
    assert outcome.status == ex.COMPLETED and outcome.effect == ex.EFFECT_DONE and outcome.started
    assert outcome.result["message"] == "typed"
    await _wait_until(lambda: tool_loop.pool.pending("call-count") == 0)


async def test_the_input_lock_is_held_until_the_cancelled_thread_work_ends(tool_loop):
    tool = ThreadTool()
    ran_at: list[float] = []

    async def second(_args: Mapping[str, Any]) -> dict[str, Any]:
        ran_at.append(time.monotonic())
        return {"status": "success", "tool": "press_key_tool", "message": "pressed"}

    executor = Executor(tool_loop, degrade_after_s=5.0)
    first = executor.start(_call("call-a", _spec("type_text_tool", tool.invoke)))
    await asyncio.to_thread(tool.thread_started.wait, 5.0)
    executor.cancel("call-a", "STOPPED_BY_USER")
    outcome = await executor.wait(first, 2.0)  # the coroutine ends at once; its thread still types
    assert outcome.status == ex.CANCELLED and outcome.code == "STOPPED_BY_USER"
    assert outcome.effect == ex.EFFECT_POSSIBLE and outcome.result["status"] == "cancelled"

    later = executor.start(_call("call-b", _spec("press_key_tool", second), lock_wait_s=5.0))
    await asyncio.sleep(0.3)
    assert not ran_at and not later.running  # waiting for the input lock, not running
    tool.release()
    assert (await executor.wait(later, 5.0)).status == ex.COMPLETED
    assert ran_at[0] >= tool.thread_ended_at  # it started only after the first call's thread work ended


async def test_thread_work_past_the_cap_degrades_input_tools_until_it_ends(tool_loop):
    tool = ThreadTool()
    executor = Executor(tool_loop, degrade_after_s=0.2)
    first = executor.start(_call("call-stuck", _spec("type_text_tool", tool.invoke)))
    await asyncio.to_thread(tool.thread_started.wait, 5.0)
    executor.cancel("call-stuck", "CANCELLED")
    await executor.wait(first, 2.0)
    await _wait_until(lambda: executor.degraded)

    refused = await executor.wait(executor.start(_call("call-in", _spec("press_key_tool", _quick))), 2.0)
    assert refused.status == ex.NOT_STARTED and refused.code == "DESKTOP_DEGRADED" and refused.effect == "none"
    other = await executor.wait(executor.start(_call("call-read", _spec("get_laptop_info", _quick,
                                                                        input_lock=False, cls="read"))), 2.0)
    assert other.status == ex.COMPLETED  # only input-lock tools are refused
    assert executor.stats()["degraded"] is True

    tool.release()
    await _wait_until(lambda: not executor.degraded)
    assert (await executor.wait(executor.start(_call("call-again", _spec("press_key_tool", _quick))), 2.0)).status \
        == ex.COMPLETED


async def test_a_busy_lock_gives_desktop_busy_without_running(tool_loop):
    release = asyncio.Event()

    async def holder(_args: Mapping[str, Any]) -> dict[str, Any]:
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(release.wait(), main_loop))
        return {"status": "success", "tool": "holder", "message": "done"}

    main_loop = asyncio.get_running_loop()
    executor = Executor(tool_loop)
    first = executor.start(_call("call-hold", _spec("type_text_tool", holder)))
    await _wait_until(lambda: first.running)
    busy = await executor.wait(executor.start(_call("call-busy", _spec("press_key_tool", _quick),
                                                    lock_wait_s=0.1)), 2.0)
    assert busy.status == ex.NOT_STARTED and busy.code == "DESKTOP_BUSY" and not busy.started
    release.set()
    assert (await executor.wait(first, 2.0)).status == ex.COMPLETED


async def test_lock_wait_zero_takes_a_free_lock_and_refuses_a_held_one_at_once(tool_loop):
    executor = Executor(tool_loop)
    free = await executor.wait(executor.start(_call("call-zero", _spec("press_key_tool", _quick), lock_wait_s=0.0)),
                               2.0)
    assert free.status == ex.COMPLETED  # 0 is 'do not wait', never 'refuse'

    tool = ThreadTool()
    holder = executor.start(_call("call-holder", _spec("type_text_tool", tool.invoke)))
    await asyncio.to_thread(tool.thread_started.wait, 5.0)
    started = time.monotonic()
    busy = await executor.wait(executor.start(_call("call-zero-busy", _spec("press_key_tool", _quick),
                                                    lock_wait_s=0.0)), 2.0)
    assert busy.status == ex.NOT_STARTED and busy.code == "DESKTOP_BUSY" and time.monotonic() - started < 0.5
    tool.release()
    assert (await executor.wait(holder, 2.0)).status == ex.COMPLETED


async def test_a_call_cancelled_before_its_task_runs_still_gets_an_outcome(tool_loop):
    ran: list[str] = []

    async def tool(_args: Mapping[str, Any]) -> dict[str, Any]:
        ran.append("ran")
        return {"status": "success", "tool": "press_key_tool", "message": "pressed"}

    executor = Executor(tool_loop)
    busy = threading.Event()
    tool_loop.call_soon(busy.wait, 5.0)  # the tool loop is busy: the start and the cancel queue up behind it
    handle = executor.start(_call("call-early", _spec("press_key_tool", tool)))
    executor.cancel("call-early", "STOPPED_BY_USER")
    executor.cancel("call-early", "CANCELLED")  # once: the first reason stands
    busy.set()
    outcome = await executor.wait(handle, 2.0)
    assert outcome is not None and outcome.status == ex.NOT_STARTED and outcome.effect == ex.EFFECT_NONE
    assert outcome.code == "STOPPED_BY_USER" and not ran
    assert executor.stats()["in_flight"] == 0


async def test_timeout_failure_and_idempotent_start(tool_loop):
    async def slow(_args: Mapping[str, Any]) -> None:
        await asyncio.sleep(5)

    async def broken(_args: Mapping[str, Any]) -> None:
        raise RuntimeError("secret detail C:\\Users\\x\\file.txt")

    executor = Executor(tool_loop)
    spec = _spec("type_text_tool", slow)
    handle = executor.start(_call("call-slow", spec, timeout_s=0.1))
    assert executor.start(_call("call-slow", spec)) is handle  # one call per call_id while it runs
    timed_out = await executor.wait(handle, 3.0)
    assert timed_out.status == ex.TIMEOUT and timed_out.code == "TIMEOUT" and timed_out.effect == "possible"

    failed = await executor.wait(executor.start(_call("call-err", _spec("mouse_click_tool", broken))), 3.0)
    assert failed.status == ex.FAILED and failed.code == "TOOL_FAILED"
    assert "secret detail" not in str(failed.result)  # exception text never reaches the agent
    executor.forget("call-err")
    assert executor.get("call-err") is None


async def test_no_running_record_means_no_run(tool_loop):
    ran: list[str] = []

    async def tool(_args: Mapping[str, Any]) -> dict[str, Any]:
        ran.append("ran")
        return {"status": "success", "tool": "t", "message": "m"}

    def broken_wal(_handle: Any) -> None:
        raise OSError("disk full")

    executor = Executor(tool_loop, on_running=broken_wal)
    outcome = await executor.wait(executor.start(_call("call-nowal", _spec("type_text_tool", tool))), 2.0)
    assert outcome.status == ex.NOT_STARTED and outcome.code == "DESKTOP_DEGRADED" and not outcome.started
    assert not ran
    seen: list[str] = []
    executor = Executor(tool_loop, on_running=lambda handle: seen.append(handle.phase))
    assert (await executor.wait(executor.start(_call("call-wal", _spec("press_key_tool", tool))), 2.0)).status \
        == ex.COMPLETED
    assert seen == ["waiting_lock"] and ran == ["ran"]  # recorded before the tool ran


async def test_cancel_before_the_lock_is_not_started(tool_loop):
    tool = ThreadTool()
    executor = Executor(tool_loop)
    first = executor.start(_call("call-1", _spec("type_text_tool", tool.invoke)))
    await asyncio.to_thread(tool.thread_started.wait, 5.0)
    waiting = executor.start(_call("call-2", _spec("press_key_tool", _quick), lock_wait_s=5.0))
    await asyncio.sleep(0.1)
    assert waiting.phase == "waiting_lock"
    executor.cancel("call-2", "STOPPED_BY_USER")
    outcome = await executor.wait(waiting, 2.0)
    assert outcome.status == ex.NOT_STARTED and outcome.effect == "none" and outcome.code == "STOPPED_BY_USER"
    assert [h.call_id for h in executor.cancel_where(lambda h: h.call.cls == "input", "DESKTOP_OFFLINE")] \
        == ["call-1"]
    tool.release()
    assert (await executor.wait(first, 2.0)).code == "DESKTOP_OFFLINE"


# --- ordering ------------------------------------------------------------------------------------------------

async def test_seq_gate_keeps_the_order_of_one_reply():
    gate = ex.SeqGate(hold_s=1.0)
    order: list[int] = []

    async def arrive(n: int, delay: float, accept_s: float) -> None:
        await asyncio.sleep(delay)
        seq = {"speech_id": "sp-1", "n": n}
        await gate.wait_turn(seq)
        await asyncio.sleep(accept_s)  # still being accepted (its WAL write): the next one must wait
        order.append(n)
        gate.done(seq)

    await asyncio.gather(arrive(2, 0.0, 0.0), arrive(1, 0.05, 0.0), arrive(0, 0.1, 0.2))
    assert order == [0, 1, 2]


async def test_seq_gate_holds_a_gap_once_and_at_most_hold_s():
    gate = ex.SeqGate(hold_s=0.2)
    started = time.monotonic()
    one = {"speech_id": "sp-2", "n": 1}
    assert await gate.wait_turn(one) is False  # n=0 never comes
    assert 0.15 <= time.monotonic() - started < 1.0
    gate.done(one)
    started = time.monotonic()
    assert await gate.wait_turn({"speech_id": "sp-2", "n": 2}) is True  # the gap is not waited for again
    assert await gate.wait_turn({"speech_id": "sp-2", "n": 0}) is True  # a late one runs at once
    assert time.monotonic() - started < 0.1
    assert await gate.wait_turn(None) is True and await gate.wait_turn({"speech_id": 5, "n": 1}) is True
    gate.done({"speech_id": "sp-3", "n": 0})
    assert await gate.wait_turn({"speech_id": "sp-3", "n": 1}) is True


async def test_seq_gate_never_holds_past_the_start_deadline():
    gate = ex.SeqGate(hold_s=5.0)
    started = time.monotonic()
    assert await gate.wait_turn({"speech_id": "sp-4", "n": 3}, deadline=time.monotonic() + 0.1) is False
    assert time.monotonic() - started < 1.0


# --- providers -----------------------------------------------------------------------------------------------

def test_the_registry_provider_runs_the_k11_wrapper_with_confirmed():
    calls: list[dict[str, Any]] = []
    imported: list[str] = []

    async def type_text_tool(text: str, delay: float = 0.0) -> dict[str, Any]:
        """Types text (a fake K-11 wrapper)."""
        return {"status": "success", "tool": "type_text_tool", "message": text}

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        return await type_text_tool(*args, **{k: v for k, v in kwargs.items() if k != "_confirmed"})

    wrapper.__name__ = "type_text_tool"
    wrapper.__signature__ = inspect.signature(type_text_tool)  # type: ignore[attr-defined]
    wrapper.__jarvis_meta__ = {"timeout": 60.0, "risk": "low", "exclusive": "desktop_input"}  # type: ignore[attr-defined]

    async def get_weather(city: str) -> str:  # VPS-placed: never served by the node
        return city

    registry = type("Registry", (), {"get_tools": staticmethod(lambda: [wrapper, get_weather])})()
    manifest = {"tools": [{"name": "type_text_tool", "module": "fake.keyboard"},
                          {"name": "get_weather", "module": "fake.weather"}]}
    provider = ex.RegistryToolProvider(import_module=imported.append, registry=lambda: registry,
                                       manifest=lambda: manifest)

    async def run() -> None:
        spec = await provider.describe("type_text_tool")
        assert spec is not None and spec.cls == tool_placement.INPUT and spec.needs_input_lock
        assert spec.timeout_s == 60.0 and spec.call_risk({"text": "x"}) == "low"
        assert spec.param_schema_sha256 == tool_placement.live_param_schema_sha256(wrapper)
        assert await provider.describe("get_weather") is None
        assert await provider.describe("no_such_tool") is None
        result = await spec.invoke({"text": "hello"})
        assert result["message"] == "hello"

    asyncio.run(run())
    assert imported == ["fake.keyboard"]  # only modules of laptop-capable tools
    assert calls == [{"text": "hello", "_confirmed": True}]


def test_the_registry_provider_survives_a_broken_module():
    def boom(name: str) -> None:
        raise ImportError(name)

    empty_registry = type("R", (), {"get_tools": staticmethod(list)})()
    provider = ex.RegistryToolProvider(
        import_module=boom, registry=lambda: empty_registry,
        manifest=lambda: {"tools": [{"name": "type_text_tool", "module": "fake.keyboard"}]})
    assert not provider.load() and provider.problems == ["fake.keyboard: ImportError"]


def test_pc_operations_and_risk():
    ops = ex.OperationRegistry()
    spec = ops.register("pc.ping", _quick)
    assert spec.cls == tool_placement.READ and not spec.needs_input_lock and spec.idempotent
    entry = next(op for op in tool_placement.PC_OPERATIONS if op.name == "pc.ping").manifest_entry()
    assert spec.param_schema_sha256 == entry["param_schema_sha256"]
    with pytest.raises(ValueError):
        ops.register("pc.format_disk", _quick)
    assert ToolSpec("t", "input", "", _quick, risk=lambda args: "medium").call_risk({}) == "medium"
    assert ToolSpec("t", "input", "", _quick, risk=lambda args: 1 / 0).call_risk({}) == "high"
    assert ToolSpec("t", "input", "", _quick, risk="weird").call_risk({}) == "high"


def test_result_body_shape():
    outcome = ex.CallOutcome(status=ex.COMPLETED, result={"status": "success", "tool": "t", "message": "m"},
                             started=True, effect=ex.EFFECT_DONE, duration_ms=12)
    assert ex.result_body(outcome) == {"status": "completed", "result": {"status": "success", "tool": "t",
                                                                         "message": "m"},
                                       "duration_ms": 12, "effect": "done", "blobs": []}
