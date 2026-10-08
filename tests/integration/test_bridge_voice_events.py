"""Plan task C-44: the bridge passes the voice events through to room 'ui' instead of 'UNKNOWN EVENT', merges message
fields on update and lets the voice commands through its allow-list.

turn_rejected (K-62), voice_id_status (K-63, the last one also in init_state), voice_enroll_result (K-63) and
memory_cleared (K-58, which also empties the stored short-term memories). The notify runs on the internal listener
against a temporary store with a recorded sio.emit.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib
from typing import Any

from src.core import bridge_security
from tests.integration.test_job_events import _events, _notify, bridge  # noqa: F401  # the shared fixture


async def test_turn_rejected_and_enroll_results_reach_the_ui(bridge, caplog):  # noqa: F811
    rejected = {"id": "t-1", "reason": "voice_id", "score": 0.41, "threshold": 0.6, "ts": 1.0}
    await _notify(bridge, "turn_rejected", rejected)
    await _notify(bridge, "voice_enroll_result", {"ok": True, "samples": 6, "self_score": 0.93})
    assert _events(bridge, "turn_rejected") == [rejected]
    assert _events(bridge, "voice_enroll_result") == [{"ok": True, "samples": 6, "self_score": 0.93}]
    assert "UNKNOWN EVENT" not in caplog.text


async def test_the_last_voice_id_status_is_in_init_state(bridge):  # noqa: F811
    await _notify(bridge, "voice_id_status", {"enrolled": True, "enforced": False, "last_score": 0.7, "ts": 1})
    await _notify(bridge, "voice_id_status", {"enrolled": True, "enforced": True, "last_score": 0.8, "ts": 2})
    assert len(_events(bridge, "voice_id_status")) == 2
    state = await bridge.module.build_init_state()
    assert state["voice_id_status"]["ts"] == 2 and state["voice_id_status"]["enforced"] is True


async def test_memory_cleared_reaches_the_ui_and_empties_the_stored_memories(bridge):  # noqa: F811
    await _notify(bridge, "memory_update", {"text": "Sir likes tea", "ts": 1})
    assert (await bridge.module.build_init_state())["memories"]
    await _notify(bridge, "memory_cleared", {"scope": "short_term", "ts": 2})
    assert _events(bridge, "memory_cleared") == [{"scope": "short_term", "ts": 2}]
    assert (await bridge.module.build_init_state())["memories"] == []


async def test_an_update_keeps_every_field_and_agent_becomes_assistant(bridge):  # noqa: F811
    await _notify(bridge, "transcription", {"id": "m-1", "role": "agent", "text": "Ji", "final": False,
                                            "turn_id": "u-1", "text_native": "جی", "score": 0.9})
    await _notify(bridge, "transcription", {"id": "m-1", "role": "agent", "text": "Ji Sir", "final": True})
    updated = _events(bridge, "update_message")[-1]
    assert updated["role"] == "assistant" and updated["text"] == "Ji Sir"
    assert updated["turn_id"] == "u-1" and updated["text_native"] == "جی" and updated["score"] == 0.9
    stored = await bridge.store.get_message("m-1")
    assert stored["text_native"] == "جی"  # survives a page reload (init_state reads the store)


def test_the_voice_commands_are_allow_listed_and_the_voice_path_is_critical():
    module = importlib.import_module("src.core.ui_bridge")
    assert {"shutdown_agent", "voice_enroll", "voice_enroll_start", "voice_take_over", "settings_update",
            "request_state"} <= module.UI_COMMAND_TYPES
    for event_type in ("transcription", "turn_rejected", "voice_id_status", "settings_sync", "task_alert"):
        assert bridge_security.notify_class(event_type) == bridge_security.NOTIFY_CLASS_CRITICAL, event_type


def _unused(_: Any) -> None:  # keeps the fixture import visible to linters
    return None
