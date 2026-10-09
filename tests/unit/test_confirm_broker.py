"""Plan task C-28: the non-blocking ConfirmFlow broker and the confirm_action tool (contract K-14).

Risky mock tools are registered under 'c28_' names in a scratch copy of the real plugin manager's
registry; LLM calls are made exactly like LiveKit makes them (function_tool +
prepare_function_arguments). Each test gets a fresh broker whose sender is a fake bridge and whose
on_result is a recorder; tool_sync posts and the security audit are recorded too, so nothing
reaches the network, the desktop, the live agent or logs/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import pytest
from livekit.agents.llm import function_tool
from livekit.agents.llm.utils import build_legacy_openai_schema, prepare_function_arguments

from services.ai_core import confirmation, jarvis_confirm, jarvis_plugin_manager as pm_module
from services.utils import jarvis_audit, jarvis_bridge, jarvis_security
from services.utils.jarvis_config import config
from services.utils.jarvis_resilience import resilience_manager

jarvis_tool = pm_module.jarvis_tool
plugin_manager = pm_module.plugin_manager

ASK = "Sir, confirm karein: {}. Screen par Allow dabayein."
ASK_VOICE = "Sir, confirm karein: {}. Screen par Allow dabayein ya haan bolein."


class FakeBridge:
    """The bridge's /notify for ConfirmFlow events, answering like C-29: {status, ui_clients}."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.answers: list[Any] = []  # queued answers: an exception is raised, 'hang' never ends
        self.default: Any = {"status": "ok", "ui_clients": 1}

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> Any:
        self.sent.append((event_type, dict(payload)))
        answer = self.answers.pop(0) if self.answers else self.default
        if answer == "hang":
            await asyncio.sleep(3600)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [payload for kind, payload in self.sent if kind == event_type]


class Recorder:
    """The plugin manager's tool_sync posts and the audit rows of one test."""

    def __init__(self) -> None:
        self.sync: list[dict[str, Any]] = []
        self.audit: list[tuple[Any, ...]] = []

    async def notify_event(self, event_type: str, payload: Any) -> None:
        if event_type == "tool_sync":
            self.sync.append(payload)

    def steps(self, tool: str) -> list[tuple[str, str]]:
        """(phase, status) of every tool_sync event of tool, in posting order."""
        return [(event["phase"], event["status"]) for event in self.sync if event["tool"] == tool]


@pytest.fixture(autouse=True)
def _scratch_registry_and_settings(monkeypatch):
    """Tools and breakers made by a test live in copies that are dropped afterwards."""
    monkeypatch.setattr(plugin_manager, "tools", list(plugin_manager.tools))
    monkeypatch.setattr(resilience_manager, "_breakers",
                        dict(resilience_manager._breakers))  # pylint: disable=protected-access
    monkeypatch.setattr(config, "desktop_control_enabled", True, raising=False)
    monkeypatch.setattr(config, "voice_confirm_high", False, raising=False)
    monkeypatch.setattr(config, "confirm_timeout_s", 60, raising=False)
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_RETRY_DELAY_S", 0)


@pytest.fixture(name="rec", autouse=True)
def _rec(monkeypatch) -> Recorder:
    """Replaces the bridge client and the security.log writers (nothing reaches logs/)."""
    rec = Recorder()
    monkeypatch.setattr(jarvis_bridge, "notify_event", rec.notify_event)
    monkeypatch.setattr(jarvis_audit, "log_tool", lambda *row: rec.audit.append(("TOOL_EXEC", *row)))
    monkeypatch.setattr(jarvis_audit.JarvisAudit, "log_event",
                        lambda event, details, severity="INFO": rec.audit.append((event, details)))
    return rec


@pytest.fixture(name="bridge")
def _bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture(name="results")
def _results() -> list[dict[str, Any]]:
    """What on_result received: the results of approved runs."""
    return []


@pytest.fixture(name="broker")
async def _broker(monkeypatch, bridge, results):
    """A fresh broker in place of the module's one; shut down after the test, leaving no task."""
    async def on_result(result: dict[str, Any]) -> None:
        results.append(result)

    broker = jarvis_confirm.ConfirmationBroker(sender=bridge, on_result=on_result)
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", broker)
    yield broker
    await broker.shutdown()
    background = jarvis_confirm._background_tasks  # pylint: disable=protected-access
    assert not [task for task in background if not task.done()]


@pytest.fixture(name="broker_warnings")
def _broker_warnings():
    """WARNING records of the CONFIRM-BROKER logger (it does not propagate to the root logger)."""
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    jarvis_confirm.logger.addHandler(handler)
    yield records
    jarvis_confirm.logger.removeHandler(handler)


def _lk_tool(tool_name: str) -> Any:
    """The registered wrapper of tool_name as LiveKit wraps it."""
    return function_tool(next(t for t in plugin_manager.tools if t.__name__ == tool_name))


async def _llm_call(tool_name: str, arguments: dict[str, Any]) -> Any:
    """One call the way LiveKit makes it: arguments bound positionally from the model's JSON."""
    tool = _lk_tool(tool_name)
    call_args, call_kwargs = prepare_function_arguments(fnc=tool,
                                                        json_arguments=json.dumps(arguments))
    return await tool(*call_args, **call_kwargs)


async def _confirm(confirm_id: str, allow: bool = True) -> dict[str, Any]:
    """The model calling confirm_action."""
    return await _llm_call("confirm_action", {"confirm_id": confirm_id, "allow": allow})


async def _until(predicate: Callable[[], Any], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met in time"
        await asyncio.sleep(0.01)


async def _flush_tool_sync() -> None:
    """Waits until the plugin manager's background tool_sync posts are done."""
    while pending := {task for task in pm_module._sync_tasks  # pylint: disable=protected-access
                      if not task.done()}:
        await asyncio.wait(pending)


# ---------------------------------------------------------------- approval runs once


async def test_a_high_risk_tool_runs_only_after_approval_and_exactly_once(broker, bridge, results,
                                                                         rec):
    ran: list[int] = []

    @jarvis_tool(risk="high", timeout=5)
    async def c28_shutdown(delay: int = 30) -> dict:
        """Shuts the PC down.

        Args:
            delay: seconds before shutdown
        """
        ran.append(delay)
        return {"status": "success", "message": "PC 5 second mein band ho raha hai."}

    asked = await _llm_call("c28_shutdown", {"delay": 5})
    confirm_id = asked["confirm_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", confirm_id)
    assert {key: asked[key] for key in ("status", "tool", "risk", "message")} == {
        "status": "needs_confirmation", "tool": "c28_shutdown", "risk": "high",
        "message": ASK.format("c28_shutdown (delay=5) chalana hai")}
    assert "confirm_action" in asked["hint"]
    [request] = bridge.of("confirm_request")
    assert request.pop("expires_at") == pytest.approx(time.time() + 60, abs=5)
    assert request == {"id": confirm_id, "tool": "c28_shutdown", "risk": "high",
                       "summary": "c28_shutdown (delay=5) chalana hai", "voice_allowed": False,
                       "ttl_s": 60, "args_preview": "delay=5"}
    await asyncio.sleep(0.05)
    assert not ran and not results
    assert [p["id"] for p in broker.pending()] == [confirm_id]

    started = await broker.resolve(confirm_id, True, source="ui")
    assert {key: started[key] for key in ("status", "tool", "confirm_id")} == {
        "status": "started", "tool": "c28_shutdown", "confirm_id": confirm_id}
    await _until(lambda: results)
    assert ran == [5]
    assert results == [{"status": "success", "tool": "c28_shutdown", "confirm_id": confirm_id,
                        "message": "PC 5 second mein band ho raha hai."}]

    # A second Allow (another tab, a double click) or a late Deny changes nothing.
    assert await broker.resolve(confirm_id, True, source="ui") == started
    assert await broker.resolve(confirm_id, False, source="ui") == started
    await asyncio.sleep(0.05)
    assert ran == [5] and len(results) == 1 and broker.pending() == []

    await _until(lambda: len(bridge.of("confirm_resolved")) == 2)
    resolved = bridge.of("confirm_resolved")
    assert {"id": confirm_id, "allow": True, "reason": "approved"} in resolved
    assert {"id": confirm_id, "allow": True, "reason": "approved",
            "result_status": "success"} in resolved

    # tool_sync: the asking call ends needs_confirmation; the approved run is a call of its own.
    await _flush_tool_sync()
    assert rec.steps("c28_shutdown") == [("start", "running"), ("end", "needs_confirmation"),
                                         ("start", "running"), ("end", "success")]
    ids = [event["id"] for event in rec.sync if event["tool"] == "c28_shutdown"]
    assert ids[0] == ids[1] != ids[2] == ids[3]
    # Audit: the request, Sir's decision and the run.
    assert [row[:4] for row in rec.audit if row[0] == "TOOL_EXEC"] == [
        ("TOOL_EXEC", "c28_shutdown", "high", "needs_confirmation"),
        ("TOOL_EXEC", "c28_shutdown", "high", "success")]
    [decision] = [row[1] for row in rec.audit if row[0] == "TOOL_CONFIRM"]
    assert decision == (f"TOOL=c28_shutdown | RISK=high | ID={confirm_id} | ALLOW=True | "
                        f"REASON=approved | SOURCE=ui")


async def test_wait_result_status_reports_the_approved_runs_end_without_touching_the_outcome(
        broker, results):
    """C79-V2-1: a waiter learns the approved run's K-13 end status and whether its result bore
    'records' (the tool body ran to completion); the stored outcome itself stays unchanged, so a
    repeated resolve() still returns the first outcome."""
    @jarvis_tool(risk="high", timeout=5)
    async def c28_sim_probe(q: str) -> dict:
        """Probes.

        Args:
            q: what
        """
        return {"status": "success", "records": [], "message": "ok"}

    asked = await _llm_call("c28_sim_probe", {"q": "x"})
    confirm_id = asked["confirm_id"]
    assert await broker.wait_result_status(confirm_id, 0.1) is None  # nothing ran yet

    started = await broker.resolve(confirm_id, True, source="ui")
    final = await broker.wait_result_status(confirm_id, 3.0)
    assert final == {"result_status": "success", "result_records": True}
    await _until(lambda: results)
    # The first outcome is untouched: no result_status/result_records leaked into it.
    assert await broker.resolve(confirm_id, True, source="ui") == started
    assert await broker.wait_result_status("unknown-id", 0.1) is None


async def test_wait_result_status_is_none_for_a_denied_action(broker, results):
    @jarvis_tool(risk="high", timeout=5)
    async def c28_sim_probe_deny(q: str) -> dict:
        """Probes.

        Args:
            q: what
        """
        return {"status": "success", "records": [], "message": "ok"}

    asked = await _llm_call("c28_sim_probe_deny", {"q": "x"})
    await broker.resolve(asked["confirm_id"], False, source="ui")
    assert await broker.wait_result_status(asked["confirm_id"], 0.2) is None
    await asyncio.sleep(0.05)
    assert not results  # denied: nothing ran


async def test_concurrent_decisions_run_the_action_at_most_once(broker, results):
    ran: list[str] = []

    @jarvis_tool(risk="medium")
    async def c28_send(contact: str) -> str:
        """Sends a message.

        Args:
            contact: who
        """
        ran.append(contact)
        await asyncio.sleep(0.05)
        return "sent"

    asked = await _llm_call("c28_send", {"contact": "Ali"})
    confirm_id = asked["confirm_id"]
    outcomes = await asyncio.gather(broker.resolve(confirm_id, True, source="ui"),
                                    broker.resolve(confirm_id, True, source="ui"),
                                    _confirm(confirm_id),
                                    broker.resolve(confirm_id, False, source="ui"))
    assert {outcome["status"] for outcome in outcomes} == {"started"}
    await _until(lambda: results)
    await asyncio.sleep(0.1)
    assert ran == ["Ali"] and len(results) == 1
    assert results[0]["message"] == "sent"


# ---------------------------------------------------------------- deny and expiry


async def test_denied_or_expired_actions_never_run(broker, bridge, results, monkeypatch):
    monkeypatch.setattr(jarvis_confirm, "SWEEP_INTERVAL_S", 0.05)
    ran: list[str] = []

    @jarvis_tool(risk="high")
    async def c28_empty_bin() -> str:
        """Empties the recycle bin."""
        ran.append("bin")
        return "emptied"

    denied = await _llm_call("c28_empty_bin", {})
    assert denied["message"] == ASK.format("c28_empty_bin chalana hai")
    outcome = await broker.resolve(denied["confirm_id"], False, source="ui")
    assert (outcome["status"], outcome["confirm_id"]) == ("cancelled", denied["confirm_id"])

    monkeypatch.setattr(config, "confirm_timeout_s", 0.2, raising=False)
    expiring = await _llm_call("c28_empty_bin", {})
    assert expiring["confirm_id"] != denied["confirm_id"]
    await _until(lambda: {"id": expiring["confirm_id"], "allow": False, "reason": "expired"}
                 in bridge.of("confirm_resolved"))
    assert broker.pending() == []
    late = await broker.resolve(expiring["confirm_id"], True, source="ui")
    assert late["status"] == "cancelled" and "waqt khatam" in late["message"]
    assert (await _confirm(expiring["confirm_id"]))["status"] == "cancelled"

    await asyncio.sleep(0.1)
    assert not ran and not results
    assert {"id": denied["confirm_id"], "allow": False, "reason": "denied"} \
        in bridge.of("confirm_resolved")
    # The sweeper ends by itself once nothing waits.
    await _until(lambda: broker._sweeper is None or broker._sweeper.done())  # pylint: disable=protected-access


async def test_a_spoken_no_cancels_and_an_unknown_id_is_not_found(broker, bridge, results):
    ran: list[str] = []

    @jarvis_tool(risk="medium")
    async def c28_volume_max() -> str:
        """Sets the volume to 100."""
        ran.append("max")
        return "done"

    asked = await _llm_call("c28_volume_max", {})
    denied = await _confirm(asked["confirm_id"], allow=False)
    assert denied["status"] == "cancelled"
    await _until(lambda: bridge.of("confirm_resolved"))
    assert bridge.of("confirm_resolved") == [
        {"id": asked["confirm_id"], "allow": False, "reason": "voice"}]
    missing = await _confirm("0" * 32)
    assert (missing["status"], missing["tool"]) == ("not_found", "confirm_action")
    await asyncio.sleep(0.05)
    assert not ran and not results


# ---------------------------------------------------------------- dedupe


async def test_identical_calls_share_one_pending_action(broker, bridge, results):
    ran: list[str] = []

    @jarvis_tool(risk="medium", raw_args=("message",))
    async def c28_whatsapp(contact: str, message: str) -> str:
        """Sends a WhatsApp message.

        Args:
            contact: who
            message: text
        """
        ran.append(message)
        return "Bhej diya."

    same = {"contact": "Ali", "message": "Salam"}
    first, second = await asyncio.gather(_llm_call("c28_whatsapp", same),
                                         _llm_call("c28_whatsapp", same))
    third = await _llm_call("c28_whatsapp", same)
    assert first["confirm_id"] == second["confirm_id"] == third["confirm_id"]
    assert first == second == third
    other = await _llm_call("c28_whatsapp", {"contact": "Ali", "message": "Kal milte hain"})
    assert other["confirm_id"] != first["confirm_id"]
    assert len(bridge.of("confirm_request")) == 2 and len(broker.pending()) == 2

    await broker.resolve(first["confirm_id"], True, source="ui")
    await _until(lambda: results)
    assert ran == ["Salam"]
    # Once decided, the same call asks again with a new id.
    again = await _llm_call("c28_whatsapp", same)
    assert again["confirm_id"] not in (first["confirm_id"], other["confirm_id"])
    assert ran == ["Salam"]


# ---------------------------------------------------------------- voice


async def test_confirm_action_refuses_high_risk_unless_voice_confirm_high(broker, results,
                                                                          monkeypatch):
    ran: list[str] = []

    @jarvis_tool(risk="high", voice_confirm=True)  # the tool's own flag never opens high risk alone
    async def c28_restart() -> str:
        """Restarts the PC."""
        ran.append("restart")
        return "Restart ho raha hai."

    asked = await _llm_call("c28_restart", {})
    assert asked["message"] == ASK.format("c28_restart chalana hai")
    refused = await _confirm(asked["confirm_id"])
    assert (refused["status"], refused["error_code"]) == ("error", "UI_CONFIRM_REQUIRED")
    assert [p["id"] for p in broker.pending()] == [asked["confirm_id"]]  # still waits for the UI
    # Switching voice_confirm_high on later does not open an action asked as screen-only.
    monkeypatch.setattr(config, "voice_confirm_high", True, raising=False)
    assert (await _confirm(asked["confirm_id"]))["error_code"] == "UI_CONFIRM_REQUIRED"
    await broker.resolve(asked["confirm_id"], False, source="ui")

    voiced = await _llm_call("c28_restart", {})
    assert voiced["message"] == ASK_VOICE.format("c28_restart chalana hai")
    # Switched off again before Sir answers: high risk is screen-only again.
    monkeypatch.setattr(config, "voice_confirm_high", False, raising=False)
    assert (await _confirm(voiced["confirm_id"]))["error_code"] == "UI_CONFIRM_REQUIRED"
    monkeypatch.setattr(config, "voice_confirm_high", True, raising=False)
    assert (await _confirm(voiced["confirm_id"]))["status"] == "started"
    await _until(lambda: results)
    assert ran == ["restart"]


async def test_voice_confirm_false_makes_a_medium_action_screen_only(broker, results):
    ran: list[str] = []

    @jarvis_tool(risk="medium", voice_confirm=False)
    async def c28_share_files() -> str:
        """Starts the file server."""
        ran.append("share")
        return "Server chal gaya."

    asked = await _llm_call("c28_share_files", {})
    assert asked["message"] == ASK.format("c28_share_files chalana hai")
    assert (await _confirm(asked["confirm_id"]))["error_code"] == "UI_CONFIRM_REQUIRED"
    await broker.resolve(asked["confirm_id"], True, source="ui")
    await _until(lambda: results)
    assert ran == ["share"]


async def test_confirm_action_returns_at_once_and_the_result_arrives_as_an_assistant_message(
        bridge, monkeypatch):
    messages: list[tuple[str, str]] = []

    async def fake_transcription(role: str, text: str, msg_id: str | None = None,
                                 is_final: bool = True, **_kwargs: Any) -> None:  # C-55: source='system'
        messages.append((role, text))

    monkeypatch.setattr(jarvis_bridge, "notify_transcription", fake_transcription)
    broker = jarvis_confirm.ConfirmationBroker(sender=bridge)  # default on_result
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", broker)
    try:
        finished = asyncio.Event()

        @jarvis_tool(risk="medium", timeout=5)
        async def c28_slow_send(contact: str) -> dict:
            """Sends a message slowly.

            Args:
                contact: who
            """
            await asyncio.sleep(0.4)
            finished.set()
            return {"status": "success", "message": f"{contact} ko message bhej diya."}

        asked = await _llm_call("c28_slow_send", {"contact": "Ali"})
        began = time.monotonic()
        answer = await _confirm(asked["confirm_id"])
        assert time.monotonic() - began < 1.0
        assert answer["status"] == "started"
        assert not finished.is_set() and not messages
        await _until(lambda: messages)
        assert messages == [("assistant", "Ali ko message bhej diya.")]
    finally:
        await broker.shutdown()


async def test_with_the_transcript_check_on_voice_approval_needs_a_newer_yes(broker, results):
    ran: list[str] = []

    @jarvis_tool(risk="medium")
    async def c28_send_whatsapp(contact: str) -> str:
        """Sends a WhatsApp message.

        Args:
            contact: who
        """
        ran.append(contact)
        return "Bhej diya."

    broker.enable_transcript_check()  # agent_runner subscribes to final transcripts (plan C-75)
    asked = await _llm_call("c28_send_whatsapp", {"contact": "Ali"})
    # Nothing heard yet (a typed or injected request): the model's call alone approves nothing.
    assert (await _confirm(asked["confirm_id"]))["error_code"] == "NO_USER_AFFIRMATION"
    broker.note_user_transcript("haan bilkul", ts=time.time() - 30)  # said before the request
    assert (await _confirm(asked["confirm_id"]))["error_code"] == "NO_USER_AFFIRMATION"
    await asyncio.sleep(0.05)
    broker.note_user_transcript("nahi abhi nahi")
    assert (await _confirm(asked["confirm_id"]))["error_code"] == "NO_USER_AFFIRMATION"
    broker.note_user_transcript("Haan ji, bhej do")
    assert (await _confirm(asked["confirm_id"]))["status"] == "started"
    await _until(lambda: results)

    # The yes is used up: the next action needs its own.
    second = await _llm_call("c28_send_whatsapp", {"contact": "Sara"})
    assert (await _confirm(second["confirm_id"]))["error_code"] == "NO_USER_AFFIRMATION"
    assert (await _confirm(second["confirm_id"], allow=False))["status"] == "cancelled"
    await asyncio.sleep(0.05)
    assert ran == ["Ali"]


async def test_until_the_check_is_on_the_model_is_trusted_with_one_warning(broker, results,
                                                                           broker_warnings):
    @jarvis_tool(risk="medium")
    async def c28_send_sms() -> str:
        """Sends an SMS."""
        return "SMS chala gaya."

    for _ in range(2):
        asked = await _llm_call("c28_send_sms", {})
        assert (await _confirm(asked["confirm_id"]))["status"] == "started"
    await _until(lambda: len(results) == 2)
    assert len([message for message in broker_warnings if "TD-19" in message]) == 1
    broker.note_user_transcript("kal ka mausam kaisa hai")  # a noted transcript switches it on too
    third = await _llm_call("c28_send_sms", {})
    assert (await _confirm(third["confirm_id"]))["error_code"] == "NO_USER_AFFIRMATION"


@pytest.mark.parametrize(("text", "expected"), [
    ("haan", True), ("Haan ji, bhej do", True), ("ji haan", True), ("theek hai", True),
    ("yes", True), ("ہاں جی", True), ("kar do", True),
    ("nahi", False), ("haan nahi", False), ("yes, cancel it", False), ("mat bhejo", False),
    ("kal ka mausam kaisa hai", False), ("johan", False), ("", False),
])
def test_affirmative_words(text, expected):
    assert jarvis_confirm.is_affirmative(text) is expected


# ---------------------------------------------------------------- arguments and risk


async def test_an_omitted_auto_run_counts_as_true(broker, results):
    ran: list[tuple[str, bool]] = []

    def write_risk(args: dict[str, Any]) -> str:
        return "medium" if args["auto_run"] and args["filename"].endswith(".py") else "low"

    @jarvis_tool(risk=write_risk, raw_args=("content",),
                 confirm_summary="Code likh kar {filename} chalaun?")
    async def c28_write_code(content: str, filename: str, auto_run: bool = True) -> dict:
        """Writes code to a file.

        Args:
            content: the code
            filename: file name
            auto_run: run it after writing
        """
        ran.append((filename, auto_run))
        return {"status": "success", "message": f"{filename} likh di"}

    asked = await _llm_call("c28_write_code", {"content": "print(1)", "filename": "a.py"})
    assert (asked["status"], asked["risk"]) == ("needs_confirmation", "medium")
    assert asked["message"] == ASK_VOICE.format("Code likh kar a.py chalaun")
    assert not ran
    direct = await _llm_call("c28_write_code",
                             {"content": "print(1)", "filename": "b.py", "auto_run": False})
    assert direct == {"status": "success", "message": "b.py likh di"}
    assert (await _confirm(asked["confirm_id"]))["status"] == "started"
    await _until(lambda: results)
    assert ran == [("b.py", False), ("a.py", True)]


async def test_positional_arguments_reach_the_approved_tool_unchanged(broker, results):
    received: dict[str, Any] = {}

    @jarvis_tool(risk="medium")
    async def c28_message(contact: str, message: str) -> str:
        """Sends a message.

        Args:
            contact: who
            message: text
        """
        received.update(contact=contact, message=message)
        return "sent"

    message = "Salam!\nKal 5 baje; a & b | `x` $HOME <tag> 100%\r\nOK"
    asked = await _llm_call("c28_message", {"contact": "Ali <Office>", "message": message})
    await broker.resolve(asked["confirm_id"], True, source="ui")
    await _until(lambda: results)
    # K-11 v2 transition: no declared policy, passed positionally, so the value is unchanged.
    assert received == {"contact": "Ali <Office>", "message": message}


async def test_a_refused_call_is_not_sent_for_confirmation(broker, bridge, monkeypatch):
    ran: list[str] = []

    @jarvis_tool(risk="high", arg_policies={"target": "path"})
    async def c28_delete_path(target: str) -> str:
        """Deletes a path.

        Args:
            target: path
        """
        ran.append(target)
        return "deleted"

    @jarvis_tool(risk="medium", exclusive="desktop_input")
    async def c28_press_keys(keys: str) -> str:
        """Presses keys.

        Args:
            keys: keys
        """
        ran.append(keys)
        return "pressed"

    @jarvis_tool(risk="high", permissions=["system:control"])
    async def c28_needs_permission() -> str:
        """Needs a permission this module does not have."""
        ran.append("perm")
        return "ran"

    rejected = await _llm_call("c28_delete_path", {"target": "..\\..\\Windows"})
    assert rejected["error_code"] == "PATH_NOT_ALLOWED"
    monkeypatch.setattr(config, "desktop_control_enabled", False, raising=False)
    assert (await _llm_call("c28_press_keys", {"keys": "alt+f4"}))["error_code"] \
        == "DESKTOP_CONTROL_DISABLED"
    assert (await _llm_call("c28_needs_permission", {})).startswith("SECURITY BLOCK")
    assert bridge.sent == [] and broker.pending() == [] and not ran


async def test_an_approved_run_still_obeys_the_kill_switch_and_its_timeout(broker, results, rec,
                                                                          monkeypatch):
    ran: list[str] = []

    @jarvis_tool(risk="medium", exclusive="desktop_input", timeout=5)
    async def c28_type_text(text: str) -> str:
        """Types text.

        Args:
            text: text
        """
        ran.append(text)
        return "typed"

    @jarvis_tool(risk="medium", timeout=0.05)
    async def c28_hangs() -> str:
        """Hangs."""
        await asyncio.sleep(5)
        ran.append("late")
        return "late"

    typing = await _llm_call("c28_type_text", {"text": "hello"})
    hanging = await _llm_call("c28_hangs", {})
    monkeypatch.setattr(config, "desktop_control_enabled", False, raising=False)
    await broker.resolve(typing["confirm_id"], True, source="ui")
    await broker.resolve(hanging["confirm_id"], True, source="ui")
    await _until(lambda: len(results) == 2)
    by_tool = {result["tool"]: result for result in results}
    assert by_tool["c28_type_text"]["status"] == "blocked"
    assert by_tool["c28_type_text"]["message"] == pm_module._DESKTOP_DISABLED_MESSAGE  # pylint: disable=protected-access
    assert by_tool["c28_hangs"]["status"] == "timeout"
    assert by_tool["c28_hangs"]["message"].startswith("TOOL_TIMEOUT")
    assert not ran
    await _flush_tool_sync()
    assert rec.steps("c28_type_text")[-1] == ("end", "blocked")
    assert rec.steps("c28_hangs")[-1] == ("end", "timeout")


async def test_an_approved_run_waits_for_the_desktop_lock(broker, results):
    timeline: list[str] = []
    release = asyncio.Event()

    @jarvis_tool(exclusive="desktop_input", timeout=5)
    async def c28_holder(text: str) -> str:
        """Holds the desktop.

        Args:
            text: text
        """
        timeline.append("holder-in")
        await release.wait()
        timeline.append("holder-out")
        return "held"

    @jarvis_tool(risk="medium", exclusive="desktop_input", timeout=5)
    async def c28_click(x: int) -> str:
        """Clicks.

        Args:
            x: position
        """
        timeline.append("click")
        return "clicked"

    asked = await _llm_call("c28_click", {"x": 3})  # asking takes no lock
    holder = asyncio.create_task(_llm_call("c28_holder", {"text": "a"}))
    await _until(lambda: timeline)
    await broker.resolve(asked["confirm_id"], True, source="ui")
    await asyncio.sleep(0.1)
    assert timeline == ["holder-in"]
    release.set()
    assert await holder == "held"
    await _until(lambda: results)
    assert timeline == ["holder-in", "holder-out", "click"]


async def test_confirm_summary_template_and_callables(broker, bridge, monkeypatch):
    monkeypatch.setattr(pm_module, "CONFIRM_SUMMARY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(jarvis_security, "redact_secrets",
                        lambda text: text.replace("TOPSECRET-1", "***"), raising=False)

    def sync_summary(args: dict[str, Any]) -> str:
        return f"{args['name']} ko delete karun?"

    async def async_summary(args: dict[str, Any]) -> str:
        await asyncio.sleep(0)
        return f"{args['count']} files zip karun?"

    async def slow_summary(args: dict[str, Any]) -> str:
        await asyncio.sleep(1)
        return "never"

    def broken_summary(args: dict[str, Any]) -> str:
        raise KeyError("nope")

    specs: dict[str, Any] = {
        "c28_close_window": "'{window}' band karun? {unknown}", "c28_delete": sync_summary,
        "c28_zip": async_summary, "c28_slow_summary": slow_summary,
        "c28_broken_summary": broken_summary,
        "c28_long_summary": "Line one\nLine two TOPSECRET-1 " + "x" * 400,
    }
    for name, spec in specs.items():
        async def tool(window: str = "", name: str = "", count: int = 0) -> str:
            """A tool.

            Args:
                window: window
                name: name
                count: count
            """
            return "ok"
        tool.__name__ = tool.__qualname__ = name
        jarvis_tool(risk="medium", confirm_summary=spec)(tool)

    await _llm_call("c28_close_window", {"window": "Notepad"})
    await _llm_call("c28_delete", {"name": "notes.txt"})
    await _llm_call("c28_zip", {"count": 3})
    await _llm_call("c28_slow_summary", {"count": 2})
    await _llm_call("c28_broken_summary", {"count": 4})
    long_answer = await _llm_call("c28_long_summary", {})
    summaries = {p["tool"]: p["summary"] for p in bridge.of("confirm_request")}
    assert summaries["c28_close_window"] == "'Notepad' band karun? {unknown}"
    assert summaries["c28_delete"] == "notes.txt ko delete karun?"
    assert summaries["c28_zip"] == "3 files zip karun?"
    assert summaries["c28_slow_summary"] == "c28_slow_summary (count=2) chalana hai"
    assert summaries["c28_broken_summary"] == "c28_broken_summary (count=4) chalana hai"
    long_summary = summaries["c28_long_summary"]
    assert long_summary.startswith("Line one Line two *** xxx") and len(long_summary) <= 300
    assert "TOPSECRET" not in json.dumps(bridge.sent) and "TOPSECRET" not in json.dumps(long_answer)


# ---------------------------------------------------------------- delivery


@pytest.mark.parametrize("failure", [
    [OSError("connection refused"), OSError("connection refused")],
    [{"status": "ignored", "reason": "unknown type"}, {"status": "ignored", "reason": "unknown type"}],
    [RuntimeError("401 bad_token"), None],
    ["hang", "hang"],
])
async def test_an_unreachable_bridge_drops_the_action(broker, bridge, broker_warnings, monkeypatch,
                                                      failure):
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_TIMEOUT_S", 0.05)
    bridge.answers = list(failure)
    ran: list[str] = []

    @jarvis_tool(risk="high")
    async def c28_shutdown_now() -> str:
        """Shuts the PC down now."""
        ran.append("now")
        return "bye"

    result = await _llm_call("c28_shutdown_now", {})
    assert (result["status"], result["error_code"]) == ("error", "CONFIRM_UI_UNREACHABLE")
    requests = bridge.of("confirm_request")
    assert len(requests) == 2 and requests[0] == requests[1]  # two tries of the same request
    assert broker.pending() == []
    assert any("CONFIRM_UI_UNREACHABLE" in message for message in broker_warnings)
    # The dropped action can never be approved later.
    assert (await broker.resolve(requests[0]["id"], True, source="ui"))["status"] == "error"
    await asyncio.sleep(0.05)
    assert not ran


async def test_no_ui_client_refuses_a_screen_only_action_at_once(broker, bridge):
    bridge.default = {"status": "ok", "ui_clients": 0}

    @jarvis_tool(risk="high")
    async def c28_format_disk() -> str:
        """Formats a disk."""
        return "formatted"

    @jarvis_tool(risk="medium")
    async def c28_mute_all() -> str:
        """Mutes everything."""
        return "muted"

    refused = await _llm_call("c28_format_disk", {})
    assert (refused["status"], refused["error_code"]) == ("error", "UI_CONFIRM_REQUIRED")
    request_id = bridge.of("confirm_request")[0]["id"]
    await _until(lambda: {"id": request_id, "allow": False, "reason": "no_ui"}
                 in bridge.of("confirm_resolved"))
    assert broker.pending() == []
    # Voice can still approve a medium action without a screen.
    assert (await _llm_call("c28_mute_all", {}))["status"] == "needs_confirmation"
    # A bridge that does not report ui_clients (before C-29) keeps the request pending.
    bridge.default = {"status": "ok"}
    assert (await _llm_call("c28_format_disk", {}))["status"] == "needs_confirmation"


async def test_the_default_sender_posts_like_notify_event_and_prefers_the_c29_helpers(monkeypatch):
    posted: list[tuple[str, Any, dict[str, str], Any]] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self) -> Any:
            return {"status": "ok", "ui_clients": 2}

    class FakeClient:
        status_code = 200

        async def post(self, url: str, **options: Any) -> FakeResponse:
            posted.append((url, options["json"], options["headers"], options["timeout"]))
            return FakeResponse(self.status_code)

    client = FakeClient()

    async def fake_get_http_client() -> FakeClient:
        return client

    monkeypatch.setattr(jarvis_bridge, "get_http_client", fake_get_http_client)
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test/vortex", raising=False)
    monkeypatch.setattr(config, "security_token", "c28-test-token", raising=False)
    send = jarvis_confirm._post_to_bridge  # pylint: disable=protected-access
    payload = {"id": "a" * 32, "tool": "c28_tool", "summary": "Kaam karun?"}

    assert await send("confirm_request", payload) == {"status": "ok", "ui_clients": 2}
    assert len(posted) == 1
    url, body, headers, timeout = posted[0]
    assert url == "http://bridge.test/vortex/notify"
    assert body == {"type": "confirm_request", "payload": payload}
    assert timeout == jarvis_confirm.NOTIFY_TIMEOUT_S
    # The same token and signature notify_event sends, which the bridge verifies.
    assert headers["X-Vortex-Token"] == "c28-test-token"
    assert jarvis_security.security_manager.verify_signature(json.dumps(body, sort_keys=True),
                                                             headers["X-Vortex-Signature"])
    client.status_code = 401
    with pytest.raises(RuntimeError):
        await send("confirm_request", payload)

    helper_calls: list[dict[str, Any]] = []

    async def notify_confirm_request(p: dict[str, Any]) -> dict[str, Any]:
        helper_calls.append(p)
        return {"status": "ok", "ui_clients": 0}

    monkeypatch.setattr(jarvis_bridge, "notify_confirm_request", notify_confirm_request,
                        raising=False)
    assert await send("confirm_request", payload) == {"status": "ok", "ui_clients": 0}
    assert helper_calls == [payload] and len(posted) == 2


async def test_a_cancelled_call_drops_its_pending_action(broker, bridge):
    bridge.answers = ["hang"]

    @jarvis_tool(risk="high")
    async def c28_cancel_me() -> str:
        """Never asked to the end."""
        return "ran"

    task = asyncio.create_task(_llm_call("c28_cancel_me", {}))
    await _until(lambda: bridge.of("confirm_request"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert broker.pending() == []


async def test_the_events_of_one_action_reach_the_bridge_in_order(broker, bridge, results,
                                                                  monkeypatch):
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_TIMEOUT_S", 0.2)
    # The UI gets the request but its answer is lost; the decision post's first try is lost too.
    bridge.answers = ["hang", "hang"]

    @jarvis_tool(risk="medium")
    async def c28_send_mail() -> str:
        """Sends a mail."""
        return "Mail chali gayi."

    asking = asyncio.create_task(_llm_call("c28_send_mail", {}))
    await _until(lambda: bridge.of("confirm_request"))
    confirm_id = bridge.of("confirm_request")[0]["id"]
    # Sir clicks Allow before the agent's first try gives up.
    assert (await broker.resolve(confirm_id, True, source="ui"))["status"] == "started"
    assert (await asking)["status"] == "started"
    await _until(lambda: results and len(bridge.of("confirm_resolved")) == 3)
    # No confirm_request after the decision (it would bring back a dead Allow dialog), and the
    # result goes last because the bridge and the UI keep the last event per id.
    assert [kind for kind, _ in bridge.sent] == ["confirm_request"] + ["confirm_resolved"] * 3
    decision = {"id": confirm_id, "allow": True, "reason": "approved"}
    assert bridge.of("confirm_resolved") == [decision, decision,
                                             {**decision, "result_status": "success"}]


async def test_shutdown_tells_the_ui_about_the_actions_it_drops(broker, bridge, results,
                                                                monkeypatch):
    @jarvis_tool(risk="high")
    async def c28_left_pending() -> str:
        """Still waits for Sir when the session ends."""
        return "ran"

    asked = await _llm_call("c28_left_pending", {})
    await broker.shutdown()
    assert bridge.of("confirm_resolved") == [
        {"id": asked["confirm_id"], "allow": False, "reason": "cancelled"}]
    assert broker.pending() == []
    assert (await broker.resolve(asked["confirm_id"], True, source="ui"))["status"] == "cancelled"

    # A bridge that never answers holds shutdown for NOTIFY_TIMEOUT_S at most.
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_TIMEOUT_S", 0.1)
    again = await _llm_call("c28_left_pending", {})
    bridge.default = "hang"
    began = time.monotonic()
    await broker.shutdown()
    assert time.monotonic() - began < 1.0
    assert {"id": again["confirm_id"], "allow": False, "reason": "cancelled"} \
        in bridge.of("confirm_resolved")
    assert not results


async def test_cancel_drops_one_pending_action_and_tells_the_ui(broker, bridge, results, rec):
    """C-51: cancel() drops ONE action whose asker gave up (its job ended): confirm_resolved
    'cancelled', its waiter wakes and a late Allow runs nothing, while another pending action
    stays and a decided one keeps its first outcome."""
    @jarvis_tool(risk="high")
    async def c28_asked_by_an_ended_job() -> str:
        """Its job ends before Sir answers."""
        return "ran"

    @jarvis_tool(risk="high")
    async def c28_still_waiting() -> str:
        """Still waits for Sir."""
        return "ran"

    asked = await _llm_call("c28_asked_by_an_ended_job", {})
    other = await _llm_call("c28_still_waiting", {})
    waiting = asyncio.create_task(broker.wait_outcome(asked["confirm_id"]))
    await asyncio.sleep(0)

    outcome = broker.cancel(asked["confirm_id"], source="job_end")
    assert (outcome["status"], outcome["reason"]) == ("cancelled", "cancelled")
    assert (await asyncio.wait_for(waiting, 2))["reason"] == "cancelled"
    await _until(lambda: bridge.of("confirm_resolved"))
    assert bridge.of("confirm_resolved") == [
        {"id": asked["confirm_id"], "allow": False, "reason": "cancelled"}]
    assert [action["id"] for action in broker.pending()] == [other["confirm_id"]]
    assert (await broker.resolve(asked["confirm_id"], True, source="ui"))["status"] == "cancelled"
    assert broker.cancel(asked["confirm_id"])["status"] == "cancelled"  # decided: the first outcome
    assert broker.cancel("c51-unknown-id") is None
    assert ("TOOL_CONFIRM", f"TOOL=c28_asked_by_an_ended_job | RISK=high | ID={asked['confirm_id']} | "
            "ALLOW=False | REASON=cancelled | SOURCE=job_end") in rec.audit
    assert len(bridge.of("confirm_resolved")) == 1 and not results


# ---------------------------------------------------------------- the bridge's K-45 clamp is enforced (C-29 part 2)


async def test_a_bridge_clamp_to_high_is_enforced_not_just_shown(broker, bridge, rec, results):
    # The bridge clamped a medium request to its K-45 HIGH floor and said so in its answer: the
    # broker's own gate must match the HIGH dialog, so a spoken haan approves nothing.
    bridge.default = {"status": "ok", "ui_clients": 1, "risk": "high", "voice_allowed": False}
    ran: list[str] = []

    @jarvis_tool(risk="medium", timeout=5)
    async def c28_sim_lookup(number: str = "0300") -> dict:
        """Looks a SIM number up.

        Args:
            number: the number to look up
        """
        ran.append(number)
        return {"status": "success", "message": "mil gaya"}

    asked = await _llm_call("c28_sim_lookup", {"number": "0300"})

    assert asked["status"] == "needs_confirmation" and asked["risk"] == "high"
    assert "ya haan bolein" not in asked["message"]  # the clamped dialog never advertises voice
    assert asked["hint"] == jarvis_confirm._UI_HINT  # pylint: disable=protected-access
    [pending] = broker.pending()
    assert (pending["risk"], pending["voice_allowed"]) == ("high", False)

    voiced = await _confirm(asked["confirm_id"])
    assert (voiced["status"], voiced["error_code"]) == ("error", "UI_CONFIRM_REQUIRED")
    assert ran == []  # the haan approved nothing above the clamped gate
    clamp_rows = [row for row in rec.audit if row[0] == "TOOL_CONFIRM_CLAMPED"]
    assert len(clamp_rows) == 2  # voice closed at delivery + the blocked haan (both audited)

    started = await broker.resolve(asked["confirm_id"], True, source="ui")  # the dashboard Allow still works
    assert started["status"] == "started"
    await _until(lambda: ran)
    assert ran == ["0300"]  # ran exactly once
    await _until(lambda: results)  # and its result was announced


async def test_an_answer_that_does_not_raise_the_risk_changes_nothing(broker, bridge, rec):
    # Never loosened: a lower answer risk is ignored; so is junk.
    bridge.default = {"status": "ok", "ui_clients": 1, "risk": "medium", "voice_allowed": True}

    @jarvis_tool(risk="high", timeout=5)
    async def c28_wipe() -> str:
        """Wipes everything."""
        return "ok"

    asked = await _llm_call("c28_wipe", {})
    assert asked["risk"] == "high"
    [pending] = broker.pending()
    assert (pending["risk"], pending["voice_allowed"]) == ("high", False)  # the K-14 high rule holds

    bridge.default = {"status": "ok", "ui_clients": 1, "risk": "junk"}

    @jarvis_tool(risk="medium", timeout=5)
    async def c28_tidy() -> str:
        """Tidies up."""
        return "ok"

    asked = await _llm_call("c28_tidy", {})
    assert asked["risk"] == "medium" and "ya haan bolein" in asked["message"]
    assert not [row for row in rec.audit if row[0] == "TOOL_CONFIRM_CLAMPED"]


async def test_the_high_override_keeps_a_clamped_actions_voice_path(broker, bridge, monkeypatch):
    # JARVIS_VOICE_CONFIRM_HIGH=1: K-14 allows voice at high, so the clamp does not close it.
    monkeypatch.setattr(config, "voice_confirm_high", True, raising=False)
    bridge.default = {"status": "ok", "ui_clients": 1, "risk": "high", "voice_allowed": True}

    @jarvis_tool(risk="medium", timeout=5)
    async def c28_sim_peek() -> dict:
        """Peeks at a SIM."""
        return {"status": "success", "message": "ok"}

    asked = await _llm_call("c28_sim_peek", {})
    assert asked["risk"] == "high" and "ya haan bolein" in asked["message"]
    [pending] = broker.pending()
    assert pending["voice_allowed"] is True


async def test_a_clamped_screen_only_action_with_no_ui_is_refused_at_once(broker, bridge):
    # SEC-1: after the clamp closes the voice path, the no_ui rule sees the truth and refuses.
    bridge.default = {"status": "ok", "ui_clients": 0, "risk": "high", "voice_allowed": False}

    @jarvis_tool(risk="medium", timeout=5)
    async def c28_sim_offline() -> dict:
        """Looks a SIM up offline."""
        return {"status": "success", "message": "ok"}

    asked = await _llm_call("c28_sim_offline", {})
    assert (asked["status"], asked["error_code"]) == ("error", "UI_CONFIRM_REQUIRED")
    assert broker.pending() == []


# ---------------------------------------------------------------- non-LLM callers


async def test_request_helper_for_callers_outside_the_llm(broker, bridge):
    async def answer_when_asked(allow: bool) -> None:
        await _until(broker.pending)
        await broker.resolve(broker.pending()[0]["id"], allow, source="ui")

    approver = asyncio.create_task(answer_when_asked(True))
    assert await broker.request("scheduled_shutdown", "Raat 11 baje PC band karun?", "high") is True
    await approver
    denier = asyncio.create_task(answer_when_asked(False))
    assert await broker.request("scheduled_shutdown", "Raat 11 baje PC band karun?", "high") is False
    await denier

    assert await broker.request("scheduled_cleanup", "Temp files saaf karun?", "medium",
                                timeout=0.1) is False
    expired = bridge.of("confirm_request")[-1]
    assert (expired["tool"], expired["risk"], expired["voice_allowed"]) == \
        ("scheduled_cleanup", "medium", True)
    await _until(lambda: {"id": expired["id"], "allow": False, "reason": "expired"}
                 in bridge.of("confirm_resolved"))

    bridge.answers = [OSError("down"), OSError("down")]
    assert await broker.request("scheduled_shutdown", "PC band karun?", "high") is False
    assert broker.pending() == []


# ---------------------------------------------------------------- schema and import paths


async def test_the_confirmed_flag_is_internal_only(broker):
    ran: list[str] = []

    @jarvis_tool(risk="high")
    async def c28_wipe(target: str) -> str:
        """Wipes something.

        Args:
            target: what
        """
        ran.append(target)
        return "wiped"

    schema = build_legacy_openai_schema(_lk_tool("c28_wipe"))
    assert "_confirmed" not in json.dumps(schema)
    # A model that sends _confirmed itself is still asked: LiveKit drops names outside the schema.
    forged = await _llm_call("c28_wipe", {"target": "x", "_confirmed": True})
    assert forged["status"] == "needs_confirmation" and not ran

    confirm_schema = build_legacy_openai_schema(_lk_tool("confirm_action"))
    assert set(confirm_schema["function"]["parameters"]["properties"]) == {"confirm_id", "allow"}
    assert "_confirmed" not in json.dumps(confirm_schema)
    assert plugin_manager.get_tool_meta("confirm_action")["timeout"] == 10.0
    assert plugin_manager.get_tool_meta("confirm_action")["risk"] == "low"

    seen: dict[str, Any] = {}

    @jarvis_tool(risk="medium")
    async def c28_options(**options: Any) -> str:
        """Takes options."""
        seen.update(options)
        return "ok"

    # The approved re-run's flag never reaches the tool itself.
    options_wrapper = next(t for t in plugin_manager.tools if t.__name__ == "c28_options")
    assert await options_wrapper(mode="fast", _confirmed=True) == "ok"
    assert seen == {"mode": "fast"}


def test_both_import_paths_give_the_same_broker():
    assert confirmation.confirmation_broker is jarvis_confirm.confirmation_broker
    assert isinstance(confirmation.confirmation_broker, jarvis_confirm.ConfirmationBroker)
    assert confirmation.confirm_action is jarvis_confirm.confirm_action
    assert confirmation.ConfirmationBroker is jarvis_confirm.ConfirmationBroker
