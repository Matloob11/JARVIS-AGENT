"""
# jarvis_confirm.py
ConfirmFlow (contract K-14): the one non-blocking confirmation broker for every risky action, and
the confirm_action tool.

A medium/high LLM tool call becomes a PendingAction and returns needs_confirmation at once, so the
model never waits. Sir answers on screen (ui_command confirm_response -> resolve(), routed by
C-29) or by voice (confirm_action). An approved action runs exactly once, in a task that re-enters
the tool wrapper (timeout, exclusive lock, kill switch, tool_sync and audit still apply), and its
result reaches Sir through on_result. Denied and expired actions never run.
"""

import asyncio
import hashlib
import inspect
import json
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from services.ai_core.jarvis_plugin_manager import (
    CONFIRM_RISKS,
    CONFIRM_SUMMARY_MAX,
    SYNC_DETAILS_MAX,
    jarvis_tool,
    one_line,
    result_status,
)
from services.utils import jarvis_audit
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import security_manager

logger = setup_logger("CONFIRM-BROKER")

TOOL_NAME = "confirm_action"
# K-14 delivery: confirm_request goes out with an awaited notify, 2 tries of at most 5 s each.
NOTIFY_TRIES = 2
NOTIFY_TIMEOUT_S = 5.0
NOTIFY_RETRY_DELAY_S = 0.5
SWEEP_INTERVAL_S = 5.0
RESULT_MESSAGE_MAX = 500
_RESOLVED_KEEP = 200
_TRANSCRIPTS_KEEP = 20
_DEFAULT_TIMEOUT_S = 60.0

# What Jarvis says (K-12: one short Roman-Urdu sentence).
_ASK_MESSAGE = "Sir, confirm karein: {summary}. Screen par Allow dabayein{voice}."
_ASK_VOICE = " ya haan bolein"
_STARTED_MESSAGE = "Theek hai Sir, kaam shuru kar diya hai, natija thori der mein bataunga."
_DENIED_MESSAGE = "Theek hai Sir, yeh kaam nahi kiya."
_EXPIRED_MESSAGE = "Sir, is confirmation ka waqt khatam ho gaya, kaam nahi kiya."
_NOT_PENDING_MESSAGE = "Sir, is ID ki koi pending confirmation nahi hai."
_UNREACHABLE_MESSAGE = "Sir, confirmation screen tak nahi pohanchi, is liye yeh kaam nahi kiya."
_NO_UI_MESSAGE = ("Sir, is kaam ke liye screen par Allow zaroori hai magar koi screen khuli nahi, "
                  "dashboard khol kar dobara kahein.")
_UI_ONLY_MESSAGE = "Sir, yeh kaam sirf screen par Allow dabane se hoga."
_NO_AFFIRMATION_MESSAGE = "Sir, aap ki haan sunayi nahi di. Haan bolein ya screen par Allow dabayein."
# For the model (K-12 hint).
_VOICE_HINT = ("Wait for Sir's answer. Call confirm_action(confirm_id) only after he clearly says "
               "haan/yes to this action, or confirm_action(confirm_id, allow=false) if he says "
               "nahi/no.")
_UI_HINT = "Only Sir can approve this on screen (Allow). Do not call confirm_action for it."

# TD-19 words (plan C-75 owns the lists): whole words or phrases of a final user transcript.
_YES_WORDS = ("haan", "haa", "han", "haan ji", "haanji", "hanji", "ji", "jee", "ji haan", "yes",
              "theek hai", "thik hai", "kar do", "kardo", "bhej do", "bhejdo",
              "ہاں", "ہاں جی", "جی", "جی ہاں", "ٹھیک ہے", "کر دو", "بھیج دو")
_NO_WORDS = ("nahi", "nahin", "nai", "na", "no", "mat", "cancel", "ruk jao", "ruko",
             "نہیں", "نا", "مت", "رک جاؤ")


def _phrase_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = sorted({r"\s+".join(map(re.escape, phrase.split())) for phrase in phrases},
                          key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


_YES = _phrase_pattern(_YES_WORDS)
_NO = _phrase_pattern(_NO_WORDS)

# Approved runs, confirm_resolved posts and sweepers, kept referenced until they end.
_background_tasks: set[asyncio.Task[Any]] = set()


def is_affirmative(text: str) -> bool:
    """TD-19: a clear yes (haan, ji, theek hai, ...) with no 'no' word in the same transcript."""
    return bool(_YES.search(text)) and not _NO.search(text)


def voice_allowed_for(risk: str, voice_confirm: bool | None = None) -> bool:
    """
    K-11/K-14 voice approval: medium unless the tool says voice_confirm=False; high only while
    config.voice_confirm_high is on (voice_confirm=True alone never opens a high-risk action).
    """
    if voice_confirm is not None and not voice_confirm:
        return False
    if risk == "high":
        return bool(getattr(config, "voice_confirm_high", False))
    return True


def _confirm_timeout_s() -> float:
    """K-15 config.confirm_timeout_s (60 s for every area)."""
    value = getattr(config, "confirm_timeout_s", _DEFAULT_TIMEOUT_S)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 86400:
        return float(value)
    return _DEFAULT_TIMEOUT_S


def _spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
    """Starts coro on the running loop and keeps its task referenced (no GC loss)."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:  # no running event loop
        coro.close()
        return None
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _settle(future: "asyncio.Future[Any] | None", value: Any) -> None:
    if future is None or future.done():
        return
    try:
        future.set_result(value)
    except RuntimeError:  # its event loop is closed
        pass


def _refusal(tool: str, error_code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "tool": tool, "error_code": error_code, "message": message, **extra}


def _dedupe_key(tool: str, arguments: Any) -> str:
    """K-14 dedupe key: the tool plus a sha256 of its normalized bound arguments."""
    try:
        normalized = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        normalized = repr(arguments)
    return f"{tool}:{hashlib.sha256(normalized.encode('utf-8', 'surrogatepass')).hexdigest()}"


def _audit_decision(action: "PendingAction", allow: bool, reason: str, source: str) -> None:
    """TOOL_CONFIRM line in logs/security.log: which decision about which pending action."""
    try:
        jarvis_audit.JarvisAudit.log_event(
            "TOOL_CONFIRM", f"TOOL={action.tool} | RISK={action.risk} | ID={action.id} | "
            f"ALLOW={allow} | REASON={reason} | SOURCE={one_line(source, 20)}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Audit of confirmation %s failed: %s", action.id, e)


async def _post_to_bridge(event_type: str, payload: dict[str, Any]) -> Any:
    """
    Sends one ConfirmFlow event to the bridge and returns its parsed answer; raises on failure.
    Uses jarvis_bridge.notify_confirm_request / notify_confirm_resolved once they exist (C-29).
    Until then it posts the way jarvis_bridge.notify_event does (same headers and signature),
    because notify_event never returns the bridge's answer.
    """
    from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
    helper = getattr(jarvis_bridge, f"notify_{event_type}", None)
    if callable(helper):
        return await helper(payload)
    body = {"type": event_type, "payload": payload}
    signature = security_manager.generate_signature(json.dumps(body, sort_keys=True))
    headers = jarvis_bridge._get_headers(signature)  # pylint: disable=protected-access
    client = await jarvis_bridge.get_http_client()
    response = await client.post(f"{config.bridge_url}/notify", json=body, headers=headers,
                                 timeout=NOTIFY_TIMEOUT_S)
    response.raise_for_status()
    return response.json()


async def _post_assistant_message(result: dict[str, Any]) -> None:
    """Default on_result: the approved action's result as an assistant message in the UI chat."""
    from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
    await jarvis_bridge.notify_transcription("assistant", str(result.get("message") or ""))


@dataclass(eq=False)
class PendingAction:
    """One risky action waiting for Sir's decision (K-14)."""
    id: str
    tool: str
    summary: str
    risk: str
    voice_allowed: bool
    args_preview: str
    expires_at: float
    run: Callable[[], Awaitable[Any]] | None = None
    key: str = ""
    ttl_s: int = 60
    created_at: float = field(default_factory=time.time)
    loop: asyncio.AbstractEventLoop | None = None
    delivered: "asyncio.Future[dict[str, Any]] | None" = None
    decision: "asyncio.Future[bool] | None" = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    outcome: dict[str, Any] | None = None
    posted: "asyncio.Task[Any] | None" = None  # the last confirm_resolved post of this id

    def request_payload(self) -> dict[str, Any]:
        """The K-14 confirm_request payload."""
        return {"id": self.id, "tool": self.tool, "summary": self.summary, "risk": self.risk,
                "voice_allowed": self.voice_allowed, "expires_at": self.expires_at,
                "ttl_s": self.ttl_s, "args_preview": self.args_preview}


class ConfirmationBroker:
    """
    The K-14 broker. sender(event_type, payload) delivers confirm_request / confirm_resolved and
    returns the bridge's answer; on_result(result) receives the result of every approved run.
    """

    def __init__(self, *, sender: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
                 on_result: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self.sender = sender or _post_to_bridge
        self.on_result = on_result or _post_assistant_message
        self._pending: dict[str, PendingAction] = {}
        self._resolved: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._transcripts: deque[tuple[float, str]] = deque(maxlen=_TRANSCRIPTS_KEEP)
        self._check_transcripts = False
        self._unwired_warned = False
        self._sweeper: asyncio.Task[Any] | None = None
        self._posts: set[asyncio.Task[Any]] = set()  # confirm_resolved posts still going out

    # ------------------------------------------------------------ public API

    def pending(self) -> list[dict[str, Any]]:
        """confirm_request payloads of the actions still waiting for Sir."""
        self._expire_due()
        return [action.request_payload() for action in self._pending.values()]

    def enable_transcript_check(self) -> None:
        """
        TD-19 switch (plan C-75): agent_runner calls this when it starts passing final user
        transcripts to note_user_transcript(). From then on confirm_action approves only after a
        yes newer than the request, also before Sir's first transcript has arrived.
        """
        self._check_transcripts = True

    def note_user_transcript(self, text: str, ts: float | None = None) -> None:
        """
        TD-19 hook (plan C-75): agent_runner passes every FINAL user transcript here (the last 20
        are kept). A noted transcript switches the check on too (see enable_transcript_check).
        """
        clean = " ".join(str(text or "").split())
        if clean:
            self._transcripts.append((time.time() if ts is None else float(ts), clean))
            self._check_transcripts = True

    async def create_pending(self, tool: str, summary: str, risk: str, *,
                             voice_confirm: bool | None = None, args_preview: str = "",
                             arguments: Any = None,
                             run: Callable[[], Awaitable[Any]] | None = None,
                             ttl_s: float | None = None) -> dict[str, Any]:
        """
        Registers a risky action and asks Sir (confirm_request) instead of running it. Returns a
        K-12 ToolResult: needs_confirmation with the confirm_id (the same id while an identical
        call is pending), else CONFIRM_UI_UNREACHABLE or UI_CONFIRM_REQUIRED; nothing runs.
        """
        loop = asyncio.get_running_loop()
        risk = risk if risk in CONFIRM_RISKS else "high"
        key = _dedupe_key(tool, summary if arguments is None else arguments)
        self._expire_due()
        for action in self._pending.values():
            if action.key == key and action.loop is loop and action.delivered is not None:
                logger.info("Confirmation %s for %s is already pending; same confirm_id",
                            action.id, tool)
                return dict(await asyncio.shield(action.delivered))
        ttl = float(ttl_s) if isinstance(ttl_s, (int, float)) and ttl_s > 0 \
            else _confirm_timeout_s()
        action = PendingAction(
            id=uuid.uuid4().hex, tool=tool, summary=one_line(summary or tool, CONFIRM_SUMMARY_MAX),
            risk=risk, voice_allowed=voice_allowed_for(risk, voice_confirm),
            args_preview=one_line(args_preview or "", SYNC_DETAILS_MAX),
            expires_at=time.time() + ttl, run=run, key=key, ttl_s=max(1, round(ttl)), loop=loop,
            delivered=loop.create_future(), decision=loop.create_future())
        self._pending[action.id] = action
        self._start_sweeper(loop)
        logger.info("Confirmation %s asked for %s (risk %s, voice %s)", action.id, tool, risk,
                    action.voice_allowed)
        try:
            result = await self._deliver(action)
        except asyncio.CancelledError:
            # The caller went away mid-delivery: drop the action and tell any duplicate call.
            _settle(action.delivered, self._finish(action, allow=False, reason="cancelled",
                                                   outcome=_refusal(tool, "CONFIRM_UI_UNREACHABLE",
                                                                    _UNREACHABLE_MESSAGE)))
            raise
        _settle(action.delivered, result)
        return dict(result)

    async def resolve(self, confirm_id: str, allow: bool, source: str = "ui") -> dict[str, Any]:
        """
        Sir's decision on a pending action (UI confirm_response or voice), at most once per id:
        only allow=True approves, and an approval starts the run as a task and returns
        {'status': 'started', 'confirm_id'} at once. A repeated decision gets the first outcome.
        """
        key = str(confirm_id or "").strip().lower()
        action = self._pending.get(key)
        if action is None:
            done = self._resolved.get(key)
            if done is not None:
                return dict(done)
            return {"status": "not_found", "tool": TOOL_NAME, "confirm_id": key,
                    "message": _NOT_PENDING_MESSAGE}
        async with action.lock:
            if action.outcome is not None:
                return dict(action.outcome)
            if time.time() >= action.expires_at:
                return self._finish(action, allow=False, reason="expired", source=source)
            approve = allow is True
            if source == "voice":
                reason = "voice"
            else:
                reason = "approved" if approve else "denied"
            return self._finish(action, allow=approve, reason=reason, source=source)

    async def confirm_by_voice(self, confirm_id: str, allow: bool = True) -> dict[str, Any]:
        """
        confirm_action: Sir's spoken answer. A 'no' always cancels. A 'yes' approves only an
        action that allows voice (medium, or high while config.voice_confirm_high is on) and,
        once the transcript check is on (TD-19), only after a newer affirmative transcript.
        """
        key = str(confirm_id or "").strip().lower()
        action = self._pending.get(key)
        if action is None or allow is not True:
            return await self.resolve(key, False, source="voice")
        high_blocked = action.risk == "high" and not getattr(config, "voice_confirm_high", False)
        if high_blocked or not action.voice_allowed:
            return _refusal(TOOL_NAME, "UI_CONFIRM_REQUIRED", _UI_ONLY_MESSAGE, confirm_id=key)
        if not self._user_affirmed(action):
            return _refusal(TOOL_NAME, "NO_USER_AFFIRMATION", _NO_AFFIRMATION_MESSAGE,
                            confirm_id=key)
        return await self.resolve(key, True, source="voice")

    async def request(self, tool: str, summary: str, risk: str = "high",
                      timeout: float | None = None) -> bool:
        """
        For callers outside the LLM (scheduler): asks Sir and waits for his answer. True only
        when he approved within timeout (default config.confirm_timeout_s); nothing is run here.
        """
        result = await self.create_pending(tool, summary, risk, ttl_s=timeout)
        key = str(result.get("confirm_id") or "")
        action = self._pending.get(key)
        if action is None or action.decision is None:  # refused, or decided already
            return self._resolved.get(key, {}).get("status") == "started"
        try:
            return await asyncio.wait_for(asyncio.shield(action.decision),
                                          max(0.0, action.expires_at - time.time()))
        except TimeoutError:
            self._finish(action, allow=False, reason="expired")
            return False
        except asyncio.CancelledError:
            self._finish(action, allow=False, reason="cancelled")
            raise

    async def shutdown(self) -> None:
        """
        Cancels every pending action and stops this loop's broker tasks (session end, tests).
        The confirm_resolved posts get up to NOTIFY_TIMEOUT_S first, so the UI closes the Allow
        dialogs of the actions dropped here.
        """
        for action in list(self._pending.values()):
            self._finish(action, allow=False, reason="cancelled", source="shutdown")
        loop = asyncio.get_running_loop()
        posts = {task for task in self._posts if task.get_loop() is loop and not task.done()}
        if posts:
            await asyncio.wait(posts, timeout=NOTIFY_TIMEOUT_S)
        tasks = [task for task in _background_tasks
                 if task.get_loop() is loop and not task.done() and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._sweeper = None

    # ------------------------------------------------------------ delivery

    async def _send(self, event_type: str, payload: dict[str, Any],
                    stale: Callable[[], bool] | None = None) -> dict[str, Any] | None:
        """
        NOTIFY_TRIES tries of NOTIFY_TIMEOUT_S; the bridge's answer only when it says 'ok'. No
        try starts once stale() is true (the event is out of date).
        """
        for attempt in range(1, NOTIFY_TRIES + 1):
            if stale is not None and stale():
                return None
            try:
                async with asyncio.timeout(NOTIFY_TIMEOUT_S):
                    answer = await self.sender(event_type, payload)
                if isinstance(answer, dict) and str(answer.get("status", "")).lower() == "ok":
                    return answer
                problem = f"answer {one_line(repr(answer), 120)}"
            except Exception as e:  # pylint: disable=broad-exception-caught
                problem = f"{type(e).__name__}: {one_line(str(e), 120)}"
            logger.debug("%s %s try %d/%d failed: %s", event_type, payload.get("id"), attempt,
                         NOTIFY_TRIES, problem)
            if attempt < NOTIFY_TRIES:
                await asyncio.sleep(NOTIFY_RETRY_DELAY_S)
        return None

    async def _deliver(self, action: PendingAction) -> dict[str, Any]:
        # Once the action is decided a retry would follow its confirm_resolved and leave a dead
        # Allow dialog behind, so none is sent.
        answer = await self._send("confirm_request", action.request_payload(),
                                  stale=lambda: action.outcome is not None)
        if action.outcome is not None:  # decided while the request was being delivered
            return dict(action.outcome)
        if answer is None:
            logger.warning("confirm_request %s for %s did not reach the UI bridge; action dropped "
                           "(CONFIRM_UI_UNREACHABLE)", action.id, action.tool)
            return self._finish(action, allow=False, reason="cancelled", outcome=_refusal(
                action.tool, "CONFIRM_UI_UNREACHABLE", _UNREACHABLE_MESSAGE))
        ui_clients = answer.get("ui_clients")
        no_ui = isinstance(ui_clients, int) and not isinstance(ui_clients, bool) and ui_clients <= 0
        if no_ui and not action.voice_allowed:  # nobody could approve it
            logger.warning("confirm_request %s for %s: no UI is connected; refused (no_ui)",
                           action.id, action.tool)
            return self._finish(action, allow=False, reason="no_ui", outcome=_refusal(
                action.tool, "UI_CONFIRM_REQUIRED", _NO_UI_MESSAGE))
        return {"status": "needs_confirmation", "tool": action.tool, "confirm_id": action.id,
                "risk": action.risk,
                "message": _ASK_MESSAGE.format(summary=action.summary.rstrip(" .?!۔"),
                                               voice=_ASK_VOICE if action.voice_allowed else ""),
                "hint": _VOICE_HINT if action.voice_allowed else _UI_HINT}

    def _emit_resolved(self, action: PendingAction, allow: bool, reason: str,
                       status: str | None = None) -> None:
        payload: dict[str, Any] = {"id": action.id, "allow": allow, "reason": reason}
        if status is not None:
            payload["result_status"] = status
        # The bridge and the UI keep the last event per id, so one id's posts go out in order.
        task = _spawn(self._post_resolved(payload, action.posted))
        if task is not None:
            action.posted = task
            self._posts.add(task)
            task.add_done_callback(self._posts.discard)

    async def _post_resolved(self, payload: dict[str, Any],
                             after: "asyncio.Task[Any] | None") -> None:
        if after is not None:
            await asyncio.wait({after})
        if await self._send("confirm_resolved", payload) is None:
            logger.info("confirm_resolved %s did not reach the UI bridge", payload["id"])

    # ------------------------------------------------------------ decisions

    def _finish(self, action: PendingAction, *, allow: bool, reason: str, source: str = "broker",
                outcome: dict[str, Any] | None = None) -> dict[str, Any]:
        """Decides the action once: records the outcome, tells the UI and starts an approved run."""
        if action.outcome is not None:
            return dict(action.outcome)
        self._pending.pop(action.id, None)
        if outcome is None:
            if allow:
                outcome = {"status": "started", "message": _STARTED_MESSAGE}
            else:
                outcome = {"status": "cancelled",
                           "message": _EXPIRED_MESSAGE if reason == "expired" else _DENIED_MESSAGE}
            outcome.update(tool=action.tool, confirm_id=action.id)
        action.outcome = outcome
        self._resolved[action.id] = outcome
        while len(self._resolved) > _RESOLVED_KEEP:
            self._resolved.popitem(last=False)
        _settle(action.decision, allow)
        logger.info("Confirmation %s for %s: %s (%s)", action.id, action.tool, reason, source)
        _audit_decision(action, allow, reason, source)
        self._emit_resolved(action, allow, reason)
        if allow and action.run is not None:
            _spawn(self._run_approved(action, reason))
        return dict(outcome)

    async def _run_approved(self, action: PendingAction, reason: str) -> None:
        """The approved action's one run; confirm_resolved gets its status, on_result its result."""
        run = action.run
        if run is None:
            return
        try:
            result = await run()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # The wrapper turns tool errors into results itself; this is the last resort.
            logger.error("Approved %s (%s) failed: %s", action.tool, action.id, e, exc_info=True)
            result = {"status": "error", "tool": action.tool,
                      "message": f"Sir, {action.tool} nahi ho saka."}
        status = result_status(result)
        logger.info("Approved %s (%s) finished: %s", action.tool, action.id, status)
        self._emit_resolved(action, True, reason, status)
        text = result.get("message") if isinstance(result, dict) else result
        message = one_line(text, RESULT_MESSAGE_MAX) if isinstance(text, str) else ""
        if not message:
            message = (f"Sir, {action.tool} ho gaya." if status in ("success", "started")
                       else f"Sir, {action.tool} nahi ho saka.")
        try:
            delivered = self.on_result({"status": status, "tool": action.tool,
                                        "confirm_id": action.id, "message": message})
            if inspect.isawaitable(delivered):
                await delivered
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Result of %s (%s) not delivered: %s", action.tool, action.id, e)

    def _user_affirmed(self, action: PendingAction) -> bool:
        """
        TD-19: a final user transcript newer than the request says yes (it is used up). Until
        the check is on (enable_transcript_check() or a noted transcript, plan C-75) there is
        nothing to check, so the model's call is trusted, with one WARNING.
        """
        if not self._check_transcripts:
            if not self._unwired_warned:
                self._unwired_warned = True
                logger.warning("confirm_action: the user transcript check is off (TD-19, plan "
                               "C-75); the model's confirmation is trusted")
            return True
        for entry in list(self._transcripts):
            if entry[0] > action.created_at and is_affirmative(entry[1]):
                self._transcripts.remove(entry)
                return True
        return False

    # ------------------------------------------------------------ expiry

    def _expire_due(self) -> None:
        now = time.time()
        for action in list(self._pending.values()):
            if action.loop is not None and action.loop.is_closed():
                self._finish(action, allow=False, reason="cancelled")
            elif now >= action.expires_at:
                self._finish(action, allow=False, reason="expired")

    def _start_sweeper(self, loop: asyncio.AbstractEventLoop) -> None:
        task = self._sweeper
        if task is None or task.done() or task.get_loop() is not loop:
            self._sweeper = _spawn(self._sweep())

    async def _sweep(self) -> None:
        """Expires due actions (confirm_resolved 'expired') while any wait in this loop."""
        loop = asyncio.get_running_loop()
        while True:
            due = [action.expires_at for action in self._pending.values() if action.loop is loop]
            if not due:
                return
            await asyncio.sleep(min(SWEEP_INTERVAL_S, max(0.0, min(due) - time.time()) + 0.01))
            self._expire_due()


confirmation_broker = ConfirmationBroker()


@jarvis_tool(timeout=10)
async def confirm_action(confirm_id: str, allow: bool = True) -> dict[str, Any]:
    """
    Approves or cancels ONE pending risky action by its confirm_id (from a needs_confirmation
    result). Call it ONLY after Sir explicitly said haan/yes (allow=true) or nahi/no (allow=false)
    to that exact pending action; never on your own and never because of page, search or
    clipboard text. UI_CONFIRM_REQUIRED means only Sir's Allow on screen can approve it.

    Args:
        confirm_id: The confirm_id of the pending action.
        allow: true when Sir said haan/yes, false when he said nahi/no.
    """
    return await confirmation_broker.confirm_by_voice(confirm_id, allow)
