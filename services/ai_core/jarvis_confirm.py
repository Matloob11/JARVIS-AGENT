"""
# jarvis_confirm.py
ConfirmFlow (contract K-14): the one non-blocking confirmation broker for every risky action, and
the confirm_action tool.

A medium/high LLM tool call becomes a PendingAction and returns needs_confirmation at once, so the
model never waits. Sir answers on screen (ui_command confirm_response -> resolve(), routed by
C-29) or by voice (confirm_action). An approved action runs exactly once, in a task that re-enters
the tool wrapper (timeout, exclusive lock, kill switch, tool_sync and audit still apply), and its
result reaches Sir through on_result. Denied and expired actions never run. While the run goes,
approved_action holds the action as its dialog showed it (id, tool, summary), so a tool body can
tell which Allow it runs under (C-83).

C-119 (VPS brain, K-14 v4, K-76): create_pending(desktop=True, desktop_class=...) marks a laptop action. A spoken yes
approves one only at medium risk, never code_exec, and only when the turn that says yes is voice-ID verified (D20:
voice_id_verified() is False until enrollment, so laptop actions are dashboard-only for now). A laptop action keeps
the args_sha256 (K-70 canonical form) of the arguments it was asked with; any other item has none (no approval of it
can cover a laptop send) and never touches the K-70 protocol library, so the PC brain never loads it here. An
approved run sees its decision through
current_approval(), from which services/ai_core/desktop_proxy.py builds the agent-signed approval reference
{confirm_id, channel, risk, args_sha256}: an Approval covers one exec of its own tool with those arguments, sent while
its run is in progress, so a task the run started (it inherits the context) never gets a laptop action approved.
Both views of one approved run (approved_action for the dialog, current_approval() for the laptop send) are set and
cleared together, by _approved_run only.
"""
# pylint: disable=too-many-lines  # one broker: delivery, decisions, voice rules, C-119 approvals and the selftest

import asyncio
import contextlib
import contextvars
import functools
import hashlib
import hmac
import inspect
import json
import os
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Coroutine, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# _run_confirmed is the exact seam every real approved call re-enters the tool wrapper through
# (K-14: timeout, lock, tool_sync, audit); the selftest's approved leg uses the same one.
from services.ai_core.jarvis_plugin_manager import (
    CONFIRM_RISKS,
    CONFIRM_SUMMARY_MAX,
    SYNC_DETAILS_MAX,
    _run_confirmed,
    jarvis_tool,
    one_line,
    plugin_manager,
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
# What Jarvis says after an approved run (on_result): done, failed, and C-119's other ends of a laptop action.
_SPOKEN_DONE = "Sir, {tool} ka kaam ho gaya, tafseel chat mein hai."
_SPOKEN_FAILED = "Sir, {tool} ka kaam nahi ho saka, wajah chat mein hai."
_SPOKEN_UNKNOWN = "Sir, {tool} ka nateeja maloom nahi ho saka; main ise dobara nahi kar raha, tafseel chat mein hai."
_SPOKEN_ASKED_AGAIN = "Sir, {tool} ke liye nayi ijazat chahiye, tafseel chat mein hai."
_SPOKEN_STOPPED = "Sir, {tool} rok diya gaya; agar rokne se pehle ho chuka tha to bata dunga, tafseel chat mein hai."
# Outcomes of a laptop action that may have run: never spoken as a failure (that would invite a second run).
_UNKNOWN_OUTCOME_CODES = frozenset({"DESKTOP_RESULT_UNKNOWN", "DESKTOP_TIMEOUT"})
# Sir's own stop ('ruko', the dashboard's Stop, 'desktop band karo'): a call in flight then may still have run, and
# the link announces it later if it had ('rokne se pehle ... ho chuka tha'), so it is never spoken as a failure either.
_STOPPED_CODE = "STOPPED_BY_USER"
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

# C-119 (K-76): the classes whose laptop actions a spoken yes never approves.
_VOICE_NEVER_CLASSES = frozenset({"code_exec"})


class _Use:
    """Whether an Approval is used up; one object shared by every context copy of that Approval."""

    __slots__ = ("spent",)

    def __init__(self) -> None:
        self.spent = False


@dataclass(frozen=True)
class Approval:
    """Sir's decision an approved run carries (C-119): which pending action of which tool, on which channel ('ui' for
    the dashboard Allow, 'voice' for a spoken yes) and at which risk. args_sha256 is the K-70 sha256 of the arguments
    a laptop action was asked with (None for any other item, or when they have no canonical form); voice_id_verified
    marks a spoken yes on a voice-ID-verified turn (D20). It covers exactly one laptop exec: its own tool with those
    arguments, sent while its run is in progress. The first send spends it, and so does the end of its run, so a task
    the run started, which keeps a copy of the context, can never send under it."""

    confirm_id: str
    channel: str
    risk: str
    args_sha256: str | None = None
    voice_id_verified: bool = False
    tool: str | None = None
    use: _Use = field(default_factory=_Use, init=False, repr=False, compare=False)

    @property
    def spent(self) -> bool:
        return self.use.spent

    def spend(self) -> None:
        """Used up: by the laptop exec sent under it, or by the end of its run."""
        self.use.spent = True

    def covers(self, tool: str, args_sha256: str | None) -> bool:
        """True only for the approved call itself: this tool, these arguments (their K-70 sha256), not spent."""
        if self.spent or self.tool is None or self.tool != tool:
            return False
        if not isinstance(self.args_sha256, str) or not isinstance(args_sha256, str):
            return False
        return hmac.compare_digest(self.args_sha256.lower().encode("utf-8"), args_sha256.lower().encode("utf-8"))

    def reference(self, args_sha256: str) -> dict[str, Any]:
        """The approval reference a laptop exec carries inside its signed, encrypted body (K-70 exec, K-76)."""
        ref: dict[str, Any] = {"confirm_id": self.confirm_id, "channel": self.channel, "risk": self.risk,
                               "args_sha256": args_sha256}
        if self.channel == "voice" and self.voice_id_verified:
            ref["voice_id_verified"] = True
        return ref


_approval_var: contextvars.ContextVar[Approval | None] = contextvars.ContextVar("jarvis_confirm_approval",
                                                                                default=None)


def current_approval() -> Approval | None:
    """The decision of the approved action whose run is in progress in this context (None outside one)."""
    return _approval_var.get()


def _no_voice_id() -> bool:
    """D20: no voice-ID-verified turn exists before voice enrollment, so voice never approves a laptop action."""
    return False


def _args_sha256(arguments: Any) -> str | None:
    """The K-70 args_sha256 of a laptop action's bound arguments, which its Approval is bound to (C-119); None when
    they are not a mapping or have no canonical form. Only a laptop action (create_pending desktop=True) passes here,
    so the PC brain (local mode, where none exists) never loads the protocol library for a confirmation. Nothing it
    meets may fail the item: a protocol library that cannot load (its native crypto blocked) gives None as well, and
    no laptop send can then be approved (fail closed)."""
    if not isinstance(arguments, Mapping):
        return None
    try:
        from services.utils.desktop_protocol import args_sha256  # pylint: disable=import-outside-toplevel
        return args_sha256(arguments)
    except (ValueError, TypeError):
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught  # ImportError (a blocked DLL) and the rest
        logger.warning("args_sha256 not computed (%s); this item cannot approve a laptop send", type(e).__name__)
        return None


@dataclass(frozen=True)
class ApprovedAction:
    """The pending action an approved run executes for, as its Allow dialog showed it (K-14)."""
    id: str
    tool: str
    summary: str


# Set around the one run of an approved action (_run_approved), so the tool body can read the
# very dialog Sir allowed: C-83 autonomous_self_repair writes only the diff that summary names.
# None outside an approved run.
approved_action: contextvars.ContextVar[ApprovedAction | None] = contextvars.ContextVar(
    "approved_action", default=None)


@contextlib.contextmanager
def _approved_run(action: "PendingAction", reason: str) -> Iterator[Approval]:
    """
    The one run of an approved action (K-14): approved_action shows the dialog Sir allowed (C-83) and
    current_approval() the decision a laptop send is checked against (C-119); both are set here and
    cleared together when the run ends, on any exit. The Approval is spent then too, so a task the
    run started, which keeps a copy of the context, can never send a laptop exec under it.
    """
    channel = "voice" if reason == "voice" else "ui"
    approval = Approval(action.id, channel, action.risk, action.args_sha256,
                        voice_id_verified=channel == "voice" and action.voice_id_verified,
                        tool=action.tool)
    shown = approved_action.set(ApprovedAction(action.id, action.tool, action.summary))
    decided = _approval_var.set(approval)
    try:
        yield approval
    finally:
        approval.spend()
        _approval_var.reset(decided)
        approved_action.reset(shown)


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


def _audit_clamp(action: "PendingAction", what: str) -> None:
    """TOOL_CONFIRM_CLAMPED line in logs/security.log: the broker honored the bridge's K-45 clamp
    (risk raised to the static floor) — at clamp time when it closed the voice path, and again
    when a spoken haan was refused because of it."""
    try:
        jarvis_audit.JarvisAudit.log_event(
            "TOOL_CONFIRM_CLAMPED",
            f"TOOL={action.tool} | RISK={action.risk} | ID={action.id} | {what}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Audit of clamp %s failed: %s", action.id, e)


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
    """Default on_result (C-55, K-18): the approved action's result through the announcer: the full message in the UI
    chat, spoken (priority high) as a fixed line, because the message can carry tool output that must never reach the
    model as instructions. C-119: a laptop action whose outcome is unknown (DESKTOP_RESULT_UNKNOWN, DESKTOP_TIMEOUT)
    is never spoken as a failure, nor is one Sir stopped (STOPPED_BY_USER: a call in flight may have run, and its
    late outcome says so), and a run that asked again (needs_confirmation) says a new approval is pending."""
    from services.utils import jarvis_announcer  # pylint: disable=import-outside-toplevel
    tool = str(result.get("tool") or "kaam")
    status = result.get("status")
    code = result.get("error_code")
    if status in ("success", "started"):
        spoken = _SPOKEN_DONE
    elif status == "needs_confirmation":
        spoken = _SPOKEN_ASKED_AGAIN
    elif code in _UNKNOWN_OUTCOME_CODES:
        spoken = _SPOKEN_UNKNOWN
    elif code == _STOPPED_CODE:
        spoken = _SPOKEN_STOPPED
    else:
        spoken = _SPOKEN_FAILED
    await jarvis_announcer.announce(str(result.get("message") or ""), priority="high", kind="confirm_result",
                                    spoken_text=spoken.format(tool=tool))


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
    clamped: bool = False  # the bridge raised the risk to its K-45 floor (C-29 part 2)
    desktop: bool = False  # C-119: a laptop action (voice only at medium, never code_exec, D20)
    desktop_class: str | None = None
    args_sha256: str | None = None  # C-119: a laptop action's arguments (K-70 form); its Approval is bound to them
    voice_id_verified: bool = False  # C-119 (D20): set by a spoken yes on a voice-ID-verified turn, never at creation
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
        # C79-V2-1: approved runs still going, and each finished run's final status, by confirm
        # id — what wait_result_status() reads. Kept OUT of the outcome dicts so a repeated
        # resolve() keeps returning the first outcome unchanged.
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._run_finals: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # C-119 (D20): whether the current voice turn is voice-ID verified; False until enrollment wires it.
        self.voice_id_verified: Callable[[], bool] = _no_voice_id

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
                             ttl_s: float | None = None, desktop: bool = False,
                             desktop_class: str | None = None) -> dict[str, Any]:
        """
        Registers a risky action and asks Sir (confirm_request) instead of running it. Returns a
        K-12 ToolResult: needs_confirmation with the confirm_id (the same id while an identical
        call is pending), else CONFIRM_UI_UNREACHABLE or UI_CONFIRM_REQUIRED; nothing runs.
        desktop=True (C-119) marks a laptop action of the K-76 class desktop_class: a spoken yes
        approves it only at medium risk, never code_exec, and only while voice_id_verified() (D20),
        on this turn (voice_allowed) and again on the turn that says yes (confirm_by_voice). Only
        such an item keeps the args_sha256 of its arguments (its Approval covers that laptop send).
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
        voice = voice_allowed_for(risk, voice_confirm)
        if desktop:  # D20: offered only on a voice-ID-verified turn; the turn that says yes is checked again
            voice = (voice and risk == "medium" and desktop_class not in _VOICE_NEVER_CLASSES
                     and self._voice_id_ok())
        action = PendingAction(
            id=uuid.uuid4().hex, tool=tool, summary=one_line(summary or tool, CONFIRM_SUMMARY_MAX),
            risk=risk, voice_allowed=voice,
            args_preview=one_line(args_preview or "", SYNC_DETAILS_MAX),
            expires_at=time.time() + ttl, run=run, key=key, ttl_s=max(1, round(ttl)), loop=loop,
            delivered=loop.create_future(), decision=loop.create_future(), desktop=bool(desktop),
            desktop_class=desktop_class if desktop else None,
            args_sha256=_args_sha256(arguments) if desktop else None)  # only a laptop send checks it (C-119)
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
        # C-119 (K-76, D20): a laptop action takes a spoken yes only at medium risk, whatever voice_confirm_high says
        desktop_blocked = action.desktop and action.risk != "medium"
        if high_blocked or desktop_blocked or not action.voice_allowed:
            if action.clamped:
                # K-45: the trail shows the clamp (not Sir) refused this haan; the action stays
                # pending, so the dashboard Allow can still decide it.
                _audit_clamp(action, "VOICE_APPROVAL=blocked (spoken haan refused above the "
                                     "clamped gate)")
            return _refusal(TOOL_NAME, "UI_CONFIRM_REQUIRED", _UI_ONLY_MESSAGE, confirm_id=key)
        if action.desktop and not self._voice_id_ok():
            # C-119 (D20): judged on the turn that says yes, not on the turn that asked; the action stays pending,
            # so the dashboard Allow can still decide it
            return _refusal(TOOL_NAME, "UI_CONFIRM_REQUIRED", _UI_ONLY_MESSAGE, confirm_id=key)
        if not self._user_affirmed(action):
            return _refusal(TOOL_NAME, "NO_USER_AFFIRMATION", _NO_AFFIRMATION_MESSAGE,
                            confirm_id=key)
        if action.desktop:
            action.voice_id_verified = True  # this yes came on a voice-ID-verified turn: its approval says so
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

    async def wait_outcome(self, confirm_id: str) -> dict[str, Any] | None:
        """
        Waits until the pending action confirm_id is decided (UI, voice or expiry) and returns
        its final outcome, the same dict a repeated resolve() returns: status 'started' only for
        an approval, else 'cancelled' — or the stored refusal (status 'error', e.g.
        CONFIRM_UI_UNREACHABLE / UI_CONFIRM_REQUIRED) when delivery itself was refused — each
        with the K-14 reason. None for an unknown id. The wait never outlives the action's own
        expiry by more than a second (C-29 confirm_selftest).
        """
        key = str(confirm_id or "").strip().lower()
        action = self._pending.get(key)
        if action is None:
            done = self._resolved.get(key)
            return dict(done) if done is not None else None
        if action.outcome is None and action.decision is not None:
            try:
                await asyncio.wait_for(asyncio.shield(action.decision),
                                       max(0.0, action.expires_at - time.time()) + 1.0)
            except TimeoutError:
                self._finish(action, allow=False, reason="expired")
        return dict(action.outcome) if action.outcome is not None else None

    async def wait_result_status(self, confirm_id: str,
                                 timeout_s: float = _DEFAULT_TIMEOUT_S) -> dict[str, Any] | None:
        """
        C79-V2-1: waits (at most timeout_s) for the APPROVED run of confirm_id to finish and
        returns {'result_status', 'result_records'}: result_status is the same K-13 end status
        confirm_resolved already carries as result_status (K-14), and result_records says the
        run's result carried a 'records' key — i.e. the tool body ran to completion (every
        lookup_sim_data return bears 'records'; the wrapper's own timeout/error dicts never
        do). None while the run is still going after timeout_s, or for an id that is unknown
        or never started a run.
        """
        key = str(confirm_id or "").strip().lower()
        task = self._run_tasks.get(key)
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=max(0.0, float(timeout_s)))
        final = self._run_finals.get(key)
        return dict(final) if final is not None else None

    def cancel(self, confirm_id: str, source: str = "broker") -> dict[str, Any] | None:
        """
        C-51: cancels ONE pending action whose asker gave up on it (the job whose guarded UI
        command asked it ended, so no Allow can reach that job any more). It is decided as
        'cancelled' like the actions shutdown() drops: confirm_resolved {allow: false, reason:
        'cancelled'} closes its dialog, its waiters wake and nothing runs, so a late Allow finds
        it decided. Returns the outcome (an action decided already keeps its first one), None
        for an unknown id.
        """
        key = str(confirm_id or "").strip().lower()
        action = self._pending.get(key)
        if action is None:
            done = self._resolved.get(key)
            return dict(done) if done is not None else None
        return self._finish(action, allow=False, reason="cancelled", source=source)

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
        # C-29 part 2 (K-45): the bridge may have clamped the risk UP to the tool's static floor;
        # the broker honors the stricter gate it answered with, so the level enforced here is the
        # level the dialog shows and a spoken haan can never approve above it (K-14: high is
        # screen-only unless config.voice_confirm_high). Never loosened: a lower or unknown
        # answer risk changes nothing.
        clamped_risk = answer.get("risk")
        if clamped_risk in CONFIRM_RISKS and \
                CONFIRM_RISKS.index(clamped_risk) > CONFIRM_RISKS.index(action.risk):
            was_voice = action.voice_allowed
            action.risk = clamped_risk
            action.clamped = True
            action.voice_allowed = (action.voice_allowed and voice_allowed_for(clamped_risk)
                                    and answer.get("voice_allowed") is not False
                                    and not (action.desktop and clamped_risk != "medium"))
            logger.warning("confirm_request %s for %s: the bridge clamped the risk to %s (K-45 "
                           "floor); voice approval %s", action.id, action.tool, clamped_risk,
                           "stays open" if action.voice_allowed else "is closed")
            if was_voice and not action.voice_allowed:
                _audit_clamp(action, "VOICE=closed (K-45 floor; high is UI-only unless "
                                     "JARVIS_VOICE_CONFIRM_HIGH=1)")
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
        # C-29 part 2: the K-14 reason travels with the outcome, so a waiter (wait_outcome,
        # confirm_selftest) can tell denied from expired; refusal outcomes keep their own fields.
        outcome.setdefault("reason", reason)
        action.outcome = outcome
        self._resolved[action.id] = outcome
        while len(self._resolved) > _RESOLVED_KEEP:
            self._resolved.popitem(last=False)
        _settle(action.decision, allow)
        logger.info("Confirmation %s for %s: %s (%s)", action.id, action.tool, reason, source)
        _audit_decision(action, allow, reason, source)
        self._emit_resolved(action, allow, reason)
        if allow and action.run is not None:
            run_task = _spawn(self._run_approved(action, reason))
            if run_task is not None:
                # C79-V2-1: kept by id so wait_result_status() can await this very run.
                self._run_tasks[action.id] = run_task
                run_task.add_done_callback(
                    lambda _task, key=action.id: self._run_tasks.pop(key, None))
        return dict(outcome)

    async def _run_approved(self, action: PendingAction, reason: str) -> None:
        """The approved action's one run; confirm_resolved gets its status, on_result its result."""
        run = action.run
        if run is None:
            return
        try:
            with _approved_run(action, reason):
                result = await run()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # The wrapper turns tool errors into results itself; this is the last resort.
            logger.error("Approved %s (%s) failed: %s", action.tool, action.id, e, exc_info=True)
            result = {"status": "error", "tool": action.tool,
                      "message": f"Sir, {action.tool} nahi ho saka."}
        status = result_status(result)
        logger.info("Approved %s (%s) finished: %s", action.tool, action.id, status)
        # C79-V2-1: the run's final status and whether its result bore 'records' (the tool body
        # ran to completion), for wait_result_status(); recorded BEFORE anything is posted, so a
        # waiter that sees this task done always finds it.
        self._run_finals[action.id] = {
            "result_status": status,
            "result_records": isinstance(result, dict) and "records" in result}
        while len(self._run_finals) > _RESOLVED_KEEP:
            self._run_finals.popitem(last=False)
        self._emit_resolved(action, True, reason, status)
        text = result.get("message") if isinstance(result, dict) else result
        message = one_line(text, RESULT_MESSAGE_MAX) if isinstance(text, str) else ""
        if not message:
            message = (f"Sir, {action.tool} ho gaya." if status in ("success", "started")
                       else f"Sir, {action.tool} nahi ho saka.")
        outcome: dict[str, Any] = {"status": status, "tool": action.tool, "confirm_id": action.id,
                                   "message": message}
        code = result.get("error_code") if isinstance(result, dict) else None
        if isinstance(code, str) and code:
            outcome["error_code"] = code  # C-119: an unknown laptop outcome is never spoken as a failure
        try:
            delivered = self.on_result(outcome)
            if inspect.isawaitable(delivered):
                await delivered
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Result of %s (%s) not delivered: %s", action.tool, action.id, e)

    def _voice_id_ok(self) -> bool:
        """C-119 (D20): the voice-ID hook's answer; a hook that fails counts as not verified (fail closed)."""
        try:
            return bool(self.voice_id_verified())
        except Exception:  # pylint: disable=broad-exception-caught
            return False

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


# ---------------------------------------------------------------- confirm_selftest (C-29 part 2, for C-30)

SELFTEST_ENV = "JARVIS_CONFIRM_SELFTEST"
SELFTEST_TOOL = "confirm_selftest"
SELFTEST_MARKER_TOOL = "confirm_selftest_marker"
# The dummy pending action is MEDIUM, so the dashboard Allow AND a spoken haan both close the loop.
SELFTEST_RISK = "medium"
SELFTEST_MARKER_NAME = "confirm_selftest.marker"
SELFTEST_RUN_WAIT_S = 10.0
# The tool body waits for Sir's decision, so its own timeout outlives the 60 s confirm window.
SELFTEST_TOOL_TIMEOUT_S = 90
SELFTEST_MARKER_TIMEOUT_S = 15
_SELFTEST_SUMMARY = ("ConfirmFlow selftest: sirf logs/confirm_selftest.marker file likhni hai, "
                     "Allow ya Deny dono se test poora hota hai")
_SELFTEST_BUSY_MESSAGE = ("Sir, aik confirm selftest pehle se chal raha hai, pehle us ka faisla "
                          "karein.")


def selftest_enabled() -> bool:
    """K-45: the confirm_selftest tools are registered only while JARVIS_CONFIRM_SELFTEST=1 —
    exactly '1', matching the contract line (C-30 sets it for one agent run and removes it)."""
    return os.getenv(SELFTEST_ENV, "").strip() == "1"


def selftest_marker_path() -> Path:
    """<project_root>/logs/confirm_selftest.marker: Allow creates it exactly once, Deny never (C-30)."""
    return Path(getattr(config, "project_root", ".")) / "logs" / SELFTEST_MARKER_NAME


@dataclass
class _SelftestRun:
    """The one in-flight selftest: where its marker goes and whether the approved leg ran."""
    marker: Path
    nonce: str
    ran: asyncio.Event = field(default_factory=asyncio.Event)


# One selftest at a time (SEC-6): a second call is refused while one is in flight, so its
# stale-marker cleanup can never delete the marker a just-approved first run wrote. The
# single-threaded event loop makes the check-and-set race-free; the finally in
# run_confirm_selftest always clears the slot.
_selftest_run: _SelftestRun | None = None


def _marker_wrapper() -> Callable[..., Awaitable[Any]] | None:
    """The registered tool wrapper of confirm_selftest_marker (the plugin manager keeps only the
    wrapper; the decorator hands back the plain function)."""
    for tool in plugin_manager.get_tools():
        if getattr(tool, "__name__", "") == SELFTEST_MARKER_TOOL:
            return tool
    return None


async def _write_selftest_marker(nonce: str) -> dict[str, Any]:
    """The approved leg's body: writes the marker (no secrets, just the nonce and a time)."""
    run = _selftest_run
    marker = run.marker if run is not None and run.nonce == nonce else selftest_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"nonce": nonce, "ts": round(time.time(), 3)}) + "\n",
                          encoding="utf-8")
        return {"status": "success", "tool": SELFTEST_MARKER_TOOL,
                "message": "Sir, confirm selftest poora hua: marker likh diya."}
    except OSError as e:
        return _refusal(SELFTEST_MARKER_TOOL, "SELFTEST_MARKER_FAILED",
                        "Sir, selftest marker likha nahi ja saka.", marker_error=type(e).__name__)
    finally:
        if run is not None and run.nonce == nonce:
            run.ran.set()


async def run_confirm_selftest(broker: "ConfirmationBroker | None" = None,
                               marker_path: Path | None = None,
                               ttl_s: float | None = None) -> dict[str, Any]:
    """
    One dummy medium-risk ConfirmFlow round trip, end to end (C-29 part 2): confirm_request to
    the bridge -> Sir's decision on the dashboard (Allow/Deny) or by voice -> broker resolve ->
    only an approval runs the confirm_selftest_marker tool through the normal wrapper (K-14:
    timeout, semaphore, tool_sync and the K-45 execution audit), which writes the marker exactly
    once. Returns a clear ToolResult with the decision, the marker state and the timings
    (request_ms: delivery of the dialog, decision_ms: Sir's answer, total_ms), so the C-30 live
    selftest reads one result. Only the marker file is written; no other tool runs, and only one
    selftest runs at a time (SELFTEST_ALREADY_RUNNING for a second call).
    """
    global _selftest_run  # pylint: disable=global-statement
    broker = broker if broker is not None else confirmation_broker
    marker = marker_path if marker_path is not None else selftest_marker_path()
    if _selftest_run is not None:
        return _refusal(SELFTEST_TOOL, "SELFTEST_ALREADY_RUNNING", _SELFTEST_BUSY_MESSAGE)
    wrapper = _marker_wrapper()
    if wrapper is None:
        return _refusal(SELFTEST_TOOL, "SELFTEST_NOT_REGISTERED",
                        "Sir, selftest ka marker tool register nahi hai; JARVIS_CONFIRM_SELFTEST=1 "
                        "ke saath agent dobara start karein.")
    stale_removed = False
    try:
        if marker.exists():
            marker.unlink()
            stale_removed = True
    except OSError as e:
        return _refusal(SELFTEST_TOOL, "SELFTEST_MARKER_LOCKED",
                        "Sir, purana selftest marker hat nahi saka, pehle use hata dein.",
                        marker_error=type(e).__name__)
    run = _SelftestRun(marker=marker, nonce=uuid.uuid4().hex[:8])
    _selftest_run = run
    try:
        t0 = time.monotonic()
        asked = await broker.create_pending(
            SELFTEST_TOOL, _SELFTEST_SUMMARY, SELFTEST_RISK,
            args_preview=f"marker={SELFTEST_MARKER_NAME} nonce={run.nonce}",
            arguments={"nonce": run.nonce},
            run=functools.partial(_run_confirmed, wrapper, (run.nonce,), {}), ttl_s=ttl_s)
        request_ms = round((time.monotonic() - t0) * 1000, 1)
        if asked.get("status") != "needs_confirmation":
            # CONFIRM_UI_UNREACHABLE or UI_CONFIRM_REQUIRED (no_ui): the refusal itself is the finding.
            return {**asked, "tool": SELFTEST_TOOL, "request_ms": request_ms,
                    "total_ms": round((time.monotonic() - t0) * 1000, 1),
                    "marker": "absent", "stale_marker_removed": stale_removed}
        confirm_id = str(asked.get("confirm_id") or "")
        outcome = await broker.wait_outcome(confirm_id)
        decision_ms = round((time.monotonic() - t0) * 1000 - request_ms, 1)
        approved = isinstance(outcome, dict) and outcome.get("status") == "started"
        reason = str(outcome.get("reason", "")) if isinstance(outcome, dict) else ""
        if approved:  # give the spawned wrapper run a moment to write the marker before reporting it
            try:
                async with asyncio.timeout(SELFTEST_RUN_WAIT_S):
                    await run.ran.wait()
            except TimeoutError:
                logger.warning("confirm_selftest %s: the approved run did not finish in %.0f s",
                               confirm_id, SELFTEST_RUN_WAIT_S)
        total_ms = round((time.monotonic() - t0) * 1000, 1)
        marker_state = "created" if marker.exists() else "absent"
        result: dict[str, Any] = {
            "tool": SELFTEST_TOOL, "confirm_id": confirm_id, "risk": SELFTEST_RISK,
            "decision": ("allowed" if approved else "expired" if reason == "expired"
                         else "cancelled" if reason == "cancelled" else "denied"),
            "reason": reason, "marker": marker_state, "stale_marker_removed": stale_removed,
            "request_ms": request_ms, "decision_ms": decision_ms, "total_ms": total_ms}
        if approved and marker_state != "created":
            return {**result, "status": "error", "error_code": "SELFTEST_MARKER_MISSING",
                    "message": "Sir, Allow to mila magar selftest marker nahi bana."}
        if reason == "expired":
            return {**result, "status": "error", "error_code": "CONFIRM_EXPIRED",
                    "message": "Sir, selftest ki confirmation ka waqt khatam ho gaya, koi faisla nahi aaya."}
        if reason == "cancelled" and not approved:
            # Broker shutdown mid-wait: nobody denied it, so it is not reported as Sir's Deny.
            return {**result, "status": "error", "error_code": "CONFIRM_CANCELLED",
                    "message": "Sir, selftest ki confirmation band ho gayi, koi faisla nahi aaya."}
        if approved:
            return {**result, "status": "success",
                    "message": f"Sir, confirm selftest kamyab: Allow mila aur marker ban gaya "
                               f"(request {request_ms:.0f} ms, faisla {decision_ms:.0f} ms)."}
        return {**result, "status": "success",
                "message": f"Sir, confirm selftest kamyab: Deny mila aur koi marker nahi bana "
                           f"(request {request_ms:.0f} ms, faisla {decision_ms:.0f} ms)."}
    finally:
        # A run approved but still unfinished after SELFTEST_RUN_WAIT_S falls back to the real
        # marker path when it finally writes; the next selftest removes that file as stale.
        _selftest_run = None


def _register_selftest_tools() -> None:
    """Registers confirm_selftest and its marker tool with the plugin manager (idempotent: a
    duplicate name is skipped there). The module gate below calls it only while
    JARVIS_CONFIRM_SELFTEST=1; tests call it against a scratch registry."""

    @jarvis_tool(timeout=SELFTEST_TOOL_TIMEOUT_S)
    async def confirm_selftest() -> dict[str, Any]:
        """
        Runs the ConfirmFlow selftest: ONE dummy medium-risk confirmation round trip (dialog on
        the dashboard, Sir's Allow/Deny or a spoken haan, broker resolve, the approved leg
        through the normal tool wrapper) and reports the timings. Allow writes
        logs/confirm_selftest.marker exactly once; Deny writes nothing. It is safe: only that
        marker file is written. Use it only when Sir asks for the confirm selftest.
        """
        return await run_confirm_selftest()

    @jarvis_tool(timeout=SELFTEST_MARKER_TIMEOUT_S, risk="medium")
    async def confirm_selftest_marker(nonce: str) -> dict[str, Any]:
        """
        INTERNAL: the approved leg of the ConfirmFlow selftest; it only writes
        logs/confirm_selftest.marker. Never call it yourself: confirm_selftest drives it
        through ConfirmFlow.

        Args:
            nonce: The selftest run's nonce, written into the marker file.
        """
        return await _write_selftest_marker(nonce)


if selftest_enabled():
    _register_selftest_tools()
