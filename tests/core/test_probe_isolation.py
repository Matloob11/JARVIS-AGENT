"""
tests/core/test_probe_isolation.py — plan C-39, contract K-57 (CT-VC-PROBE).

A probe or canary job ('jarvis-probe-<epoch>', 'jarvis-canary-<epoch>') sets services.utils.jarvis_bridge.
bridge_suppressed before it creates any task. In that context nothing reaches the bridge (notify, confirm, the UI log
panel), memory and identity writes are no-ops, and the value never leaks into the user's own job's context.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any

import pytest

from services.utils import jarvis_bridge, jarvis_logger, probe_context


@pytest.fixture
def suppressed():
    """Runs the test body's assertions inside a suppressed context, then resets it."""
    token = probe_context.bridge_suppressed.set(True)
    yield
    probe_context.bridge_suppressed.reset(token)


@pytest.mark.parametrize("name, probe", [
    ("jarvis-probe-1791460000", True), ("jarvis-canary-1791460000", True), ("jarvis-hybrid", False),
    ("jarvis-probe-abc", False), ("xjarvis-probe-1", False), ("jarvis-probe-1-x", False), ("", False), (None, False),
])
def test_only_probe_and_canary_rooms_are_isolated(name: Any, probe: bool):
    assert probe_context.is_probe_room(name) is probe
    assert jarvis_bridge.is_probe_room is probe_context.is_probe_room  # the K-57 name
    assert jarvis_bridge.bridge_suppressed is probe_context.bridge_suppressed


async def test_a_suppressed_context_sends_no_notify_and_answers_no_confirm(monkeypatch, suppressed):
    sent: list[Any] = []

    async def record(*args: Any, **_kwargs: Any) -> None:
        sent.append(args)

    monkeypatch.setattr(jarvis_bridge, "_notify_signed", record)
    monkeypatch.setattr(jarvis_bridge, "_notify_legacy", record)
    monkeypatch.setattr(jarvis_bridge, "get_http_client", lambda: pytest.fail("no HTTP from a probe"))
    await jarvis_bridge.notify_event("transcription", {"role": "user", "text": "probe"})
    await jarvis_bridge.notify_transcription("assistant", "probe reply")
    assert sent == []
    answer = await jarvis_bridge.notify_confirm_request({"id": "c" * 32, "tool": "run_cmd_command"})
    assert answer == {"status": "suppressed", "ui_clients": 0}  # no UI: the broker denies


async def test_outside_a_probe_notify_still_goes_out(monkeypatch):
    sent: list[Any] = []

    async def record(*args: Any, **_kwargs: Any) -> None:
        sent.append(args[0])

    monkeypatch.setattr(jarvis_bridge, "_notify_signed", record)
    monkeypatch.setattr(jarvis_bridge, "_notify_legacy", record)
    await jarvis_bridge.notify_event("transcription", {"role": "user", "text": "hi"})
    assert sent == ["transcription"]


def test_the_ui_log_handler_ships_nothing_from_a_probe():
    offered: list[Any] = []
    shipper = type("Shipper", (), {"offer": lambda self, item, high=False: offered.append(item)})()
    handler = jarvis_logger.UIBridgeHandler(shipper=shipper, min_level=logging.WARNING)
    record = logging.LogRecord("JARVIS-CORE", logging.WARNING, __file__, 1, "probe warning", None, None)
    token = probe_context.bridge_suppressed.set(True)
    try:
        handler.emit(record)
    finally:
        probe_context.bridge_suppressed.reset(token)
    assert offered == []
    handler.emit(record)
    assert len(offered) == 1  # the user's own lines still reach the panel


async def test_memory_and_identity_writes_are_no_ops_in_a_probe(tmp_path, monkeypatch, suppressed):
    from services.ai_core import agent_memory, jarvis_identity, memory_store

    memory = memory_store.ConversationMemory("probe-user", storage_path=str(tmp_path / "probe_memory.json"))
    assert await memory.save_conversation({"messages": [{"role": "user", "content": "x"}]}) is False
    assert not (tmp_path / "probe_memory.json").exists()

    extractor = agent_memory.MemoryExtractor("probe-user")
    monkeypatch.setattr(extractor.memory, "save_conversation", lambda *_a: pytest.fail("no memory write"))
    await extractor.run([type("Msg", (), {"role": "user", "content": "x"})()])

    identity = jarvis_identity.IdentityManager.__new__(jarvis_identity.IdentityManager)
    identity.data = {"facts": [], "relationships": {}}
    identity.lock = asyncio.Lock()
    identity.storage_path = str(tmp_path / "identity.json")
    assert "Probe" in await identity.add_fact("probe fact")
    assert identity.data["facts"] == [] and not (tmp_path / "identity.json").exists()
    await identity.save()
    assert not (tmp_path / "identity.json").exists()


async def test_the_suppression_reaches_the_jobs_tasks_and_threads_but_never_another_context():
    seen: dict[str, bool] = {}
    other = contextvars.copy_context()  # the user's job: its context exists before the probe sets anything

    async def probe_job() -> None:
        probe_context.bridge_suppressed.set(True)
        seen["task"] = await asyncio.create_task(_check())
        seen["thread"] = await asyncio.to_thread(probe_context.is_probe_job)

    async def _check() -> bool:
        return probe_context.is_probe_job()

    await asyncio.create_task(probe_job())  # its own context copy, like a job's entrypoint task
    assert seen == {"task": True, "thread": True}
    assert other.run(probe_context.is_probe_job) is False
    assert probe_context.is_probe_job() is False  # this test's own context never changed
