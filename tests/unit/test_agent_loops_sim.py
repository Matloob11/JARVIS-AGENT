"""Plan task C-79: SIM ui_command payload {query, purpose} on the agent (K-03 / K-34 / K-45).

The dashboard's sim_lookup reaches UIBridgeListener._handle_command as the dialog's {query, purpose}
or the legacy plain string. Both shapes run through the SAME guarded lookup path voice uses (the
plugin-manager wrapper of lookup_sim_data, so the K-45 HIGH ConfirmFlow gate applies; no wrapper
means a refusal, never the raw function). A needs_confirmation answer is never relayed: the task
awaits confirmation_broker.wait_outcome and answers a Deny/expiry as a masked sim_data_result with
a fixed message. Only the masked query is ever logged (K-34, never the purpose). Fake digits only.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.ai_core import agent_loops

FAKE_PHONE = "03001234567"          # fake digits only
FAKE_PHONE_MASKED = "030*****567"
FAKE_CNIC = "3520212345671"         # fake digits only
FAKE_CNIC_MASKED = "352*******671"


@pytest.fixture
def sim(monkeypatch):
    """A listener with a recorded wrapper, broker wait, notify helpers and logger; nothing reaches
    a live bridge or the real ConfirmFlow."""
    from services.ai_core import jarvis_confirm  # pylint: disable=import-outside-toplevel
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", MagicMock(return_value=MagicMock()))
    assistant = MagicMock()
    assistant._spawned_tasks = set()
    listener = agent_loops.UIBridgeListener(assistant)
    wrapper = AsyncMock(return_value={"status": "success", "records": [], "message": "ok"})
    wrapper.__name__ = "lookup_sim_data"
    monkeypatch.setattr(agent_loops, "_sim_lookup_tool", lambda: wrapper)
    wait_outcome = AsyncMock(return_value=None)
    monkeypatch.setattr(jarvis_confirm.confirmation_broker, "wait_outcome", wait_outcome)
    loading = AsyncMock()
    monkeypatch.setattr(agent_loops, "notify_sim_loading", loading)
    event = AsyncMock()
    monkeypatch.setattr(agent_loops, "notify_event", event)
    log = MagicMock()
    monkeypatch.setattr(agent_loops, "logger", log)
    return SimpleNamespace(listener=listener, assistant=assistant, wrapper=wrapper,
                           wait_outcome=wait_outcome, loading=loading, event=event, log=log)


async def _send(sim: Any, payload: Any) -> None:
    await sim.listener._handle_command({"type": "sim_lookup", "payload": payload})
    await asyncio.gather(*list(sim.assistant._spawned_tasks))


def _logged_text(sim: Any) -> str:
    return json.dumps([str(call) for call in sim.log.mock_calls])


# ---------------------------------------------------------------- both payload shapes reach the lookup


@pytest.mark.asyncio
async def test_the_dialogs_dict_payload_reaches_the_guarded_lookup_with_its_purpose(sim):
    await _send(sim, {"query": FAKE_PHONE, "purpose": "identity verification"})

    sim.wrapper.assert_awaited_once_with(FAKE_PHONE, purpose="identity verification")
    sim.loading.assert_awaited_once_with({"query_masked": FAKE_PHONE_MASKED})
    sim.event.assert_not_awaited()  # the tool body ran (result has records): it emitted its own events


@pytest.mark.asyncio
async def test_a_legacy_string_payload_still_works_with_an_empty_purpose(sim):
    await _send(sim, FAKE_PHONE)
    sim.wrapper.assert_awaited_once_with(FAKE_PHONE, purpose="")


@pytest.mark.asyncio
async def test_a_cnic_dict_payload_works_too(sim):
    await _send(sim, {"query": FAKE_CNIC, "purpose": "record check"})
    sim.wrapper.assert_awaited_once_with(FAKE_CNIC, purpose="record check")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "", "   ", 42, {"purpose": "only"}, {"query": ""},
                                     {"query": 5}, ["0300"]])
async def test_a_sim_lookup_without_a_usable_query_is_ignored(sim, payload):
    await _send(sim, payload)
    sim.wrapper.assert_not_awaited()
    sim.loading.assert_not_awaited()
    sim.event.assert_not_awaited()


# ---------------------------------------------------------------- K-34: masked logs, never the purpose


@pytest.mark.asyncio
async def test_only_the_masked_query_is_logged_and_the_purpose_never_is(sim):
    await _send(sim, {"query": FAKE_PHONE, "purpose": "mera secret reason"})

    logged = _logged_text(sim)
    assert FAKE_PHONE_MASKED in logged
    assert FAKE_PHONE not in logged
    assert "mera secret reason" not in logged


@pytest.mark.asyncio
async def test_a_failing_lookup_logs_only_the_exception_type(sim):
    sim.wrapper.side_effect = RuntimeError(f"boom {FAKE_PHONE}")
    await _send(sim, FAKE_PHONE)

    assert FAKE_PHONE not in _logged_text(sim)
    sim.event.assert_awaited_once()
    _name, payload = sim.event.await_args.args
    assert payload["status"] == "error" and payload["phone"] == FAKE_PHONE_MASKED


# ---------------------------------------------------------------- the K-45 gate: wait, then answer


def _needs_confirmation(confirm_id: str = "cid-1") -> dict[str, Any]:
    """The REAL shape the wrapper returns at once with a configured source (jarvis_confirm
    _deliver): the broker's ask-message may carry argument text, so it must never be relayed."""
    return {"status": "needs_confirmation", "tool": "lookup_sim_data", "confirm_id": confirm_id,
            "risk": "high",
            "message": f"Sir, confirm karein: lookup_sim_data (phone_number={FAKE_PHONE}, "
                       f"purpose=mera secret reason) chalana hai. Screen par Allow dabayein."}


@pytest.mark.asyncio
async def test_a_denied_confirmation_is_awaited_and_answered_masked_with_a_fixed_message(sim):
    sim.wrapper.return_value = _needs_confirmation("cid-deny")
    sim.wait_outcome.return_value = {"status": "cancelled", "reason": "denied",
                                     "tool": "lookup_sim_data", "confirm_id": "cid-deny",
                                     "message": "Theek hai Sir, yeh kaam nahi kiya."}
    await _send(sim, {"query": FAKE_PHONE, "purpose": "mera secret reason"})

    sim.wait_outcome.assert_awaited_once_with("cid-deny")
    sim.event.assert_awaited_once()
    event_name, payload = sim.event.await_args.args
    assert event_name == "sim_data_result"
    assert payload["records"] == [] and payload["phone"] == FAKE_PHONE_MASKED
    assert payload["status"] == "denied" and payload["message"] and "timestamp" in payload
    # C79-R1: the broker's ask-message (raw args preview) is NEVER relayed to the dialog
    emitted = json.dumps([str(call) for call in sim.event.mock_calls])
    assert FAKE_PHONE not in emitted and "mera secret reason" not in emitted
    assert "chalana hai" not in emitted


@pytest.mark.asyncio
async def test_an_expired_confirmation_answers_the_dialog_too(sim):
    sim.wrapper.return_value = _needs_confirmation("cid-exp")
    sim.wait_outcome.return_value = {"status": "cancelled", "reason": "expired",
                                     "tool": "lookup_sim_data", "confirm_id": "cid-exp"}
    await _send(sim, FAKE_PHONE)

    sim.event.assert_awaited_once()
    _name, payload = sim.event.await_args.args
    assert payload["status"] == "expired" and payload["phone"] == FAKE_PHONE_MASKED


@pytest.mark.asyncio
async def test_an_approved_confirmation_emits_nothing_here_the_rerun_answers(sim):
    sim.wrapper.return_value = _needs_confirmation("cid-ok")
    sim.wait_outcome.return_value = {"status": "started", "reason": "approved",
                                     "tool": "lookup_sim_data", "confirm_id": "cid-ok"}
    await _send(sim, {"query": FAKE_PHONE, "purpose": "identity verification"})

    sim.wait_outcome.assert_awaited_once_with("cid-ok")
    sim.event.assert_not_awaited()  # the approved re-run emits its own sim_data_* events


@pytest.mark.asyncio
async def test_a_lost_outcome_still_answers_the_dialog_as_cancelled(sim):
    sim.wrapper.return_value = _needs_confirmation("cid-lost")
    sim.wait_outcome.return_value = None  # unknown id / broker restarted
    await _send(sim, FAKE_PHONE)

    _name, payload = sim.event.await_args.args
    assert payload["status"] == "cancelled" and payload["records"] == []


@pytest.mark.asyncio
async def test_without_a_registered_wrapper_the_lookup_is_refused_never_run_raw(sim, monkeypatch):
    # C79-R4: the K-45 gate fails closed; nothing runs without the guarded wrapper.
    monkeypatch.setattr(agent_loops, "_sim_lookup_tool", lambda: None)
    await _send(sim, {"query": FAKE_PHONE, "purpose": "identity verification"})

    sim.wrapper.assert_not_awaited()
    sim.event.assert_awaited_once()
    _name, payload = sim.event.await_args.args
    assert payload["status"] == "error" and payload["records"] == []
    assert payload["phone"] == FAKE_PHONE_MASKED and FAKE_PHONE not in json.dumps(payload)


@pytest.mark.asyncio
async def test_a_no_records_message_gets_its_digit_runs_masked(sim):
    # K-34 last line of defense: a broker/guard message carrying a number is masked, not relayed raw.
    sim.wrapper.return_value = {"status": "error", "tool": "lookup_sim_data",
                                "message": f"gate refused {FAKE_PHONE} today"}
    await _send(sim, FAKE_PHONE)

    _name, payload = sim.event.await_args.args
    assert FAKE_PHONE not in payload["message"] and FAKE_PHONE_MASKED in payload["message"]


@pytest.mark.asyncio
async def test_a_result_with_records_is_not_answered_twice(sim):
    sim.wrapper.return_value = {"status": "blocked", "records": [], "message": "provider not configured"}
    await _send(sim, FAKE_PHONE)
    sim.event.assert_not_awaited()  # lookup_sim_data already emitted sim_data_result itself


# ---------------------------------------------------------------- the wrapper really is the guarded path


def test_the_registered_lookup_wrapper_is_the_guarded_one_not_the_raw_function():
    sim_module = importlib.import_module("services.info.jarvis_sim_lookup")
    tool = agent_loops._sim_lookup_tool()
    assert tool is not None, "lookup_sim_data must be registered with the plugin manager"
    assert tool is not sim_module.lookup_sim_data  # the raw function would bypass ConfirmFlow
    meta = getattr(tool, "__jarvis_meta__", None)
    assert meta is not None and callable(meta["risk"])  # 'high' with a configured source (K-45)


def test_mask_sim_query_follows_the_k34_rule():
    assert agent_loops._mask_sim_query(FAKE_PHONE) == FAKE_PHONE_MASKED
    assert agent_loops._mask_sim_query("0300-123 4567") == FAKE_PHONE_MASKED
    assert agent_loops._mask_sim_query(FAKE_CNIC) == FAKE_CNIC_MASKED
    assert agent_loops._mask_sim_query("042") == "***"


def test_mask_sim_message_masks_digit_runs_and_keeps_the_rest():
    masked = agent_loops._mask_sim_message(f"no record for {FAKE_PHONE} ya 35202-1234567-1 aaj")
    assert FAKE_PHONE not in masked and "1234567" not in masked
    assert FAKE_PHONE_MASKED in masked and FAKE_CNIC_MASKED in masked
    assert masked.startswith("no record for ") and masked.endswith(" aaj")
    assert agent_loops._mask_sim_message("sirf text, 5 records") == "sirf text, 5 records"
    assert agent_loops._mask_sim_message(None) == ""


def test_sim_lookup_input_parses_both_shapes():
    assert agent_loops.sim_lookup_input({"query": f" {FAKE_PHONE} ", "purpose": " x y "}) == (FAKE_PHONE, "x y")
    assert agent_loops.sim_lookup_input({"query": FAKE_PHONE, "purpose": 5}) == (FAKE_PHONE, "")
    assert agent_loops.sim_lookup_input(FAKE_PHONE) == (FAKE_PHONE, "")
    assert agent_loops.sim_lookup_input({"query": None}) is None
    assert agent_loops.sim_lookup_input(None) is None


# ---------------------------------------------------------------- the bridge validator (C-79 step 3/4)


def test_the_bridge_validator_rejects_a_25_char_query(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    bridge = importlib.import_module("src.core.ui_bridge")

    clean, masked, reason, _msg = bridge.parse_sim_lookup("0" * 25, False)
    assert (clean, reason) == (None, "bad_payload") and masked
    clean, _masked, reason, _msg = bridge.parse_sim_lookup({"query": "0" * 25, "purpose": ""}, False)
    assert (clean, reason) == (None, "bad_payload")
    # ... and accepts the two K-34 patterns at <= 20 chars
    assert bridge.parse_sim_lookup(FAKE_PHONE, False)[2] == ""
    assert bridge.parse_sim_lookup({"query": FAKE_CNIC, "purpose": "record check"}, True)[2] == ""
