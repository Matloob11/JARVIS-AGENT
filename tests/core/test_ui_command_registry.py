"""
tests/core/test_ui_command_registry.py — C-34 registry (K-60 CT-VC-CONTROL).

Stdlib-only module: register/dispatch, (type,id) dedupe with LRU-512 eviction, id-less commands
never deduped, unknown type warns + notify_log, re-registration replaces, handler errors never
propagate, and the job-scoped JobListenerHandle's aclose contract.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from services.utils import ui_command_registry as reg
from services.utils.ui_command_registry import (
    DEDUPE_MAX,
    CommandContext,
    JobListenerHandle,
    UI_COMMANDS,
    dispatch,
    register_ui_command,
    reset_dedupe,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test sees a fresh dedupe LRU and leaves UI_COMMANDS as it found it (voice_link's
    'voice_take_over' registration must survive this file)."""
    saved = dict(UI_COMMANDS)
    reset_dedupe()
    yield
    UI_COMMANDS.clear()
    UI_COMMANDS.update(saved)
    reset_dedupe()


def _ctx(**kwargs: Any) -> CommandContext:
    return CommandContext(**kwargs)


# ---- register + dispatch ------------------------------------------------------------------------


async def test_dispatch_runs_async_handler_with_payload_and_ctx():
    calls: list[tuple[Any, Any]] = []

    @register_ui_command("t_async")
    async def _handler(payload, ctx):
        calls.append((payload, ctx))

    ctx = _ctx(assistant="A")
    assert await dispatch("t_async", {"x": 1}, ctx) is True
    assert calls == [({"x": 1}, ctx)]


async def test_dispatch_runs_sync_handler():
    calls: list[Any] = []

    @register_ui_command("t_sync")
    def _handler(payload, _ctx):
        calls.append(payload)

    assert await dispatch("t_sync", "hello", _ctx()) is True
    assert calls == ["hello"]


async def test_reregistration_replaces_and_logs(caplog):
    @register_ui_command("t_re")
    def _first(_payload, _ctx):
        raise AssertionError("replaced handler must not run")

    ran: list[str] = []
    with caplog.at_level(logging.INFO, logger="JARVIS-UI-REGISTRY"):

        @register_ui_command("t_re")
        def _second(_payload, _ctx):
            ran.append("second")

    assert UI_COMMANDS["t_re"] is _second
    assert any("re-registered" in r.message for r in caplog.records)
    await dispatch("t_re", None, _ctx())
    assert ran == ["second"]


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        register_ui_command("")


async def test_handler_exception_logged_never_raised(caplog):
    @register_ui_command("t_boom")
    async def _handler(_payload, _ctx):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="JARVIS-UI-REGISTRY"):
        assert await dispatch("t_boom", {"id": "e1"}, _ctx()) is True
    assert any("t_boom" in r.message or "t_boom" in str(r.args) for r in caplog.records)


# ---- unknown type -------------------------------------------------------------------------------


async def test_unknown_type_warns_and_notifies_sync_and_async(caplog):
    notes: list[tuple[str, str]] = []

    with caplog.at_level(logging.WARNING, logger="JARVIS-UI-REGISTRY"):
        assert await dispatch("no_such_cmd", {}, _ctx(notify_log=lambda m, lvl: notes.append((m, lvl)))) is False

    async def _async_note(message: str, level: str) -> None:
        notes.append((message, level))

    assert await dispatch("no_such_cmd", {}, _ctx(notify_log=_async_note)) is False
    assert notes == [("Unknown UI command: no_such_cmd", "SYSTEM")] * 2
    assert any("Unknown UI command" in r.getMessage() for r in caplog.records)


async def test_unknown_type_without_notify_log_is_fine():
    assert await dispatch("no_such_cmd", {}, _ctx()) is False
    assert await dispatch(None, {}, _ctx()) is False
    assert await dispatch(123, {}, _ctx()) is False


async def test_unknown_type_notify_log_errors_are_swallowed():
    def _broken(_m: str, _lvl: str) -> None:
        raise RuntimeError("bridge down")

    assert await dispatch("no_such_cmd", {}, _ctx(notify_log=_broken)) is False


# ---- dedupe -------------------------------------------------------------------------------------


def _counting_handler(name: str) -> list[Any]:
    calls: list[Any] = []

    @register_ui_command(name)
    def _handler(payload, _ctx):
        calls.append(payload)

    return calls


async def test_duplicate_type_and_id_dropped(caplog):
    calls = _counting_handler("t_dupe")
    assert await dispatch("t_dupe", {"id": "m1"}, _ctx()) is True
    with caplog.at_level(logging.DEBUG, logger="JARVIS-UI-REGISTRY"):
        assert await dispatch("t_dupe", {"id": "m1"}, _ctx()) is False
    assert len(calls) == 1
    assert any("duplicate ui_command ignored" in r.getMessage() for r in caplog.records)


async def test_same_id_different_type_both_dispatch():
    a = _counting_handler("t_a")
    b = _counting_handler("t_b")
    assert await dispatch("t_a", {"id": "m1"}, _ctx()) is True
    assert await dispatch("t_b", {"id": "m1"}, _ctx()) is True
    assert len(a) == 1 and len(b) == 1


async def test_client_msg_id_fallback_deduped():
    calls = _counting_handler("t_cmi")
    assert await dispatch("t_cmi", {"client_msg_id": "c1", "text": "hi"}, _ctx()) is True
    assert await dispatch("t_cmi", {"client_msg_id": "c1", "text": "hi"}, _ctx()) is False
    assert len(calls) == 1


async def test_idless_commands_never_deduped():
    calls = _counting_handler("t_mute")
    for payload in (None, None, {}, {}, "text", "text", {"other": 1}, {"other": 1}):
        assert await dispatch("t_mute", payload, _ctx()) is True
    assert len(calls) == 8


@pytest.mark.parametrize("bad_id", [123, 4.5, True, "", "x" * 129, "a\nb", ["l"], {"d": 1}])
async def test_unusable_id_means_no_dedupe(bad_id):
    calls = _counting_handler("t_bad")
    assert await dispatch("t_bad", {"id": bad_id, "client_msg_id": None}, _ctx()) is True
    assert await dispatch("t_bad", {"id": bad_id, "client_msg_id": None}, _ctx()) is True
    assert len(calls) == 2


async def test_lru_eviction_at_512():
    calls = _counting_handler("t_lru")
    assert await dispatch("t_lru", {"id": "first"}, _ctx()) is True
    # DEDUPE_MAX fresh ids push 'first' out of the LRU.
    for n in range(DEDUPE_MAX):
        assert await dispatch("t_lru", {"id": f"fill-{n}"}, _ctx()) is True
    assert await dispatch("t_lru", {"id": "first"}, _ctx()) is True  # evicted: runs again
    # The newest fill id is still in the window and stays deduped.
    assert await dispatch("t_lru", {"id": f"fill-{DEDUPE_MAX - 1}"}, _ctx()) is False
    assert len(calls) == DEDUPE_MAX + 2


# ---- JobListenerHandle ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *, raise_on_disconnect: bool = False, hang_s: float = 0.0) -> None:
        self.disconnects = 0
        self._raise = raise_on_disconnect
        self._hang_s = hang_s

    async def disconnect(self) -> None:
        self.disconnects += 1
        if self._raise:
            raise RuntimeError("socket already dead")
        if self._hang_s:
            await asyncio.sleep(self._hang_s)


async def test_aclose_sets_closing_and_disconnects_once():
    client = _FakeClient()
    handle = JobListenerHandle(client)
    assert handle.closing is False
    await handle.aclose()
    await handle.aclose()  # idempotent: the double-close never disconnects twice
    assert handle.closing is True
    assert handle.closed.is_set()
    assert client.disconnects == 1


async def test_aclose_never_raises_on_disconnect_error():
    handle = JobListenerHandle(_FakeClient(raise_on_disconnect=True))
    await handle.aclose()
    assert handle.closed.is_set()


async def test_aclose_bounded_on_hung_disconnect():
    client = _FakeClient(hang_s=30.0)
    handle = JobListenerHandle(client, disconnect_timeout=0.05)
    await asyncio.wait_for(handle.aclose(), 2.0)  # the 30 s hang must not hold aclose
    assert handle.closed.is_set()
    assert client.disconnects == 1


async def test_aclose_without_client_and_late_attach():
    handle = JobListenerHandle()
    await handle.aclose()
    assert handle.closed.is_set()

    client = _FakeClient()
    handle2 = JobListenerHandle()
    handle2.attach_client(client)
    await handle2.aclose()
    assert client.disconnects == 1


def test_default_registry_has_no_import_side_commands_removed():
    """voice_link registers 'voice_take_over' from its own module (K-60) — importing it must have
    put the command in the shared registry."""
    import src.core.voice_link  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel

    assert "voice_take_over" in UI_COMMANDS
