"""
# services/ai_core/control_commands.py
C-51 (contract K-60 CT-VC-CONTROL): what the dashboard's control commands do. services/ai_core/agent_loops registers
the handlers (C-34) and calls these flows.

    await stop_speaking(interrupt) -> bool                  'stop': interrupt speech, never end the job
    await run_shutdown_agent(payload, ctx) -> bool          'shutdown_agent': end the job after the owner confirmed
    await run_clear_memory(payload, ctx) -> dict | None     'clear_memory': back up and clear after the confirmation
    await confirm_control(command, payload, ...)            the owner's decision on one guarded command
    await close_open_flows(ctx) -> int                      the job's end: the cards still open are cancelled

shutdown_agent and clear_memory run only after the owner confirmed them: a K-14 confirm card through the broker when it
is registered (dashboard Allow only), else payload confirm:true, which only the bridge's authenticated UI sockets can
send (role ui, or the legacy token while LEGACY_TOKEN_AUTH=1; K-03). Each decision and result is audited in
logs/security.log (jarvis_audit), never with user text. A flow waits for the owner in its own task (agent_loops spawns
it), so the job's listener stays free for the confirm_response. A card never outlives its job: the job's listener
cancels the open ones when it closes (close_open_flows), and a flow whose task is cancelled cancels its own.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from services.ai_core.jarvis_vector_memory import jarvis_vector_db
from services.utils import jarvis_announcer, jarvis_audit
from services.utils.jarvis_bridge import notify_event, notify_ui
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.ui_command_registry import CommandContext

logger = setup_logger("JARVIS-CONTROL")

SHUTDOWN_REASON = "terminated_by_owner"  # job_end / ctx.shutdown reason of a shutdown_agent the owner confirmed
# The cards (K-14): a job end and the vector wipe (the vector store has no backup) are high, the short-term clear is
# medium. A UI command's card never reaches the model, so no spoken yes can answer it: voice_confirm is off and only the
# dashboard's Allow decides.
SHUTDOWN_RISK = "high"
CLEAR_MEMORY_RISK = "medium"
CLEAR_VECTOR_RISK = "high"
_SHUTDOWN_SUMMARY = "JARVIS ka maujooda session band karna hai; bridge naya session shuru karega"
_SHUTDOWN_NO_RESTART_SUMMARY = ("JARVIS ka maujooda session band karna hai (restart=false: bridge abhi phir bhi naya "
                                "session shuru kar sakta hai)")
_CLEAR_MEMORY_SUMMARY = ("Short-term memory (guftagu ki history) saaf karni hai; purani file backups/memory mein "
                         "mehfooz rahegi")
_CLEAR_VECTOR_SUMMARY = "Long-term (vector) memory bhi poori mitani hai; is ka koi backup nahi banta"
_CONFIRM_REFUSALS = {"CONFIRM_UI_UNREACHABLE": "unreachable", "UI_CONFIRM_REQUIRED": "no_ui"}
# CommandContext.extras (one per job: the job's listener builds it)
_BUSY_KEY = "control_busy"      # {command: its flow's task}: the guarded commands of this job under way
_OPEN_KEY = "control_open"      # {confirm_id: command}: the cards this job's flows wait on
_CLOSED_KEY = "control_closed"  # True once the job's listener closed (close_open_flows): nobody can answer any more
JOB_END_SOURCE = "job_end"      # the audit source of a card the job's end cancelled
FLOWS_CLOSE_WAIT_S = 2.0        # close_open_flows waits at most this long for the flows to finish


def _audit(event: str, details: str, severity: str = "INFO") -> None:
    """One logs/security.log line for a control command: decisions and results, never user text; never raises."""
    try:
        jarvis_audit.JarvisAudit.log_event(event, details, severity)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Audit of %s failed: %s", event, type(e).__name__)


async def _tell(ctx: CommandContext, message: str) -> None:
    """One dashboard log line about a control command (ctx.notify_log); best effort, never raises."""
    notify = getattr(ctx, "notify_log", None)
    if not callable(notify):
        return
    try:
        result = notify(message, "SYSTEM")
        if inspect.isawaitable(result):
            await result
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("notify_log failed: %s", type(e).__name__)


def _job_ended(ctx: CommandContext | None) -> bool:
    """True once the job's listener closed (close_open_flows): no Allow can reach this job any more."""
    return ctx is not None and bool(ctx.extras.get(_CLOSED_KEY))


def _claim(ctx: CommandContext, command: str) -> bool:
    """One flow per guarded command and job at a time: a second click while its card is open is ignored, and so is
    one that arrives after the job's listener closed."""
    if _job_ended(ctx):
        logger.info("%s: the job is ending; ignored", command)
        return False
    busy = ctx.extras.setdefault(_BUSY_KEY, {})
    if command in busy:
        logger.info("%s: its confirmation is already open; this one is ignored", command)
        return False
    busy[command] = asyncio.current_task()
    return True


def _release(ctx: CommandContext, command: str) -> None:
    ctx.extras.get(_BUSY_KEY, {}).pop(command, None)


def _confirm_broker() -> Any:
    """The K-14 confirm broker, or None when it is not registered (services.ai_core.jarvis_confirm cannot load)."""
    try:
        from services.ai_core.jarvis_confirm import confirmation_broker  # pylint: disable=import-outside-toplevel
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("confirm broker not registered (%s): guarded commands need payload confirm:true",
                       type(e).__name__)
        return None
    return confirmation_broker


async def confirm_control(command: str, payload: Any, summary: str, risk: str, *, args_preview: str = "",
                          arguments: Any = None, ctx: CommandContext | None = None) -> tuple[bool, str, str]:
    """
    The owner's decision on one guarded control command (K-60, K-14), awaited in the caller's task. With the broker
    registered: a confirm_request card (tool = the command, risk medium|high, dashboard Allow only) and the owner's
    answer; a payload confirm:true never replaces the card then. Without the broker: payload confirm:true only.
    Returns (approved, reason, source): source 'broker' with the K-14 reason ('approved', 'denied', 'expired',
    'cancelled', 'no_ui', 'unreachable', 'refused', 'error'), 'ui_payload' with 'ui_confirm' / 'no_confirmation', or
    'job_end' with 'cancelled' when ctx's job ended before a card could be asked. With ctx the open card is the job's:
    its end cancels it (close_open_flows). The caller audits the decision.
    """
    broker = _confirm_broker()
    if broker is None:
        approved = isinstance(payload, dict) and payload.get("confirm") is True
        return approved, ("ui_confirm" if approved else "no_confirmation"), "ui_payload"
    if _job_ended(ctx):  # e.g. clear_memory's vector card: nobody could answer it any more
        return False, "cancelled", JOB_END_SOURCE
    if isinstance(payload, dict) and payload.get("confirm") is True:
        logger.info("%s: payload confirm ignored, the confirm broker asks the owner (K-60)", command)
    try:
        asked = await broker.create_pending(command, summary, risk, voice_confirm=False, args_preview=args_preview,
                                            arguments=arguments)
        if not isinstance(asked, dict):
            return False, "refused", "broker"
        if asked.get("status") == "needs_confirmation":
            outcome = await _await_owner(broker, str(asked.get("confirm_id") or ""), command, ctx)
        elif asked.get("confirm_id") and not asked.get("error_code"):
            # decided while its card was still being delivered (the owner answered before the delivery returned): the
            # broker hands back that decision, an Allow included
            outcome = asked
        else:
            code = str(asked.get("error_code") or "")
            return False, _CONFIRM_REFUSALS.get(code, "refused"), "broker"
    except Exception as e:  # pylint: disable=broad-exception-caught  # a broken broker never approves (a cancel passes)
        logger.error("%s: confirmation failed (%s); nothing runs", command, type(e).__name__)
        return False, "error", "broker"
    approved = isinstance(outcome, dict) and outcome.get("status") == "started"
    reason = str(outcome.get("reason") or "") if isinstance(outcome, dict) else ""
    return approved, reason or ("approved" if approved else "cancelled"), "broker"


async def _await_owner(broker: Any, confirm_id: str, command: str, ctx: CommandContext | None) -> Any:
    """The broker's outcome of one open card. The card is registered as the job's while it waits, so the job's end
    cancels it (close_open_flows); a job that ended while the card was still being delivered cancels it at once, and
    a cancel of this flow's task cancels it too (then the cancel passes on)."""
    if _job_ended(ctx):
        return _cancel_card(broker, confirm_id, command, JOB_END_SOURCE)
    open_cards: dict[str, str] = ctx.extras.setdefault(_OPEN_KEY, {}) if ctx is not None else {}
    open_cards[confirm_id] = command
    try:
        return await broker.wait_outcome(confirm_id)
    except asyncio.CancelledError:
        _cancel_card(broker, confirm_id, command, "flow_cancelled")
        raise
    finally:
        open_cards.pop(confirm_id, None)


def _cancel_card(broker: Any, confirm_id: str, command: str, source: str) -> dict[str, Any] | None:
    """Cancels one card through the broker (K-14 confirm_resolved {allow: false, reason: 'cancelled'} closes it on the
    dashboard; a late Allow finds it decided and nothing runs). Returns the broker's outcome; never raises."""
    if not confirm_id:  # a card without an id: there is nothing to cancel
        return None
    cancel = getattr(broker, "cancel", None)
    if not callable(cancel):
        logger.warning("%s: its confirmation could not be cancelled (the broker has no cancel)", command)
        return None
    try:
        outcome = cancel(confirm_id, source=source)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("%s: its confirmation could not be cancelled (%s)", command, type(e).__name__)
        return None
    logger.info("%s: its confirmation was cancelled (%s)", command, source)
    return outcome if isinstance(outcome, dict) else None


async def close_open_flows(ctx: CommandContext, wait_s: float | None = None) -> int:
    """
    C-51, the job's end (the job's UIBridgeListener.aclose, before its socket closes): no Allow can reach this job any
    more, so every card its guarded commands still wait on is cancelled through the broker. confirm_resolved {allow:
    false, reason: 'cancelled'} closes it on the dashboard, a late Allow (another job's listener) finds it decided and
    nothing runs, and each flow ends through its 'not confirmed' path (audited). A card still being delivered is
    cancelled as soon as its delivery returns, and a guarded command that arrives afterwards is ignored. A flow past
    its confirmation is left to finish. Waits at most wait_s (default FLOWS_CLOSE_WAIT_S) for the flows; returns how
    many cards it cancelled. Never raises (a cancel of the caller passes).
    """
    ctx.extras[_CLOSED_KEY] = True
    cards = list((ctx.extras.get(_OPEN_KEY) or {}).items())
    broker = _confirm_broker() if cards else None
    cancelled = sum(1 for confirm_id, command in cards
                    if _cancel_card(broker, confirm_id, command, JOB_END_SOURCE) is not None)
    if cards:
        logger.warning("JOB END: %d open confirmation(s) of guarded commands cancelled", cancelled)
    current = asyncio.current_task()
    flows = [task for task in list((ctx.extras.get(_BUSY_KEY) or {}).values())
             if isinstance(task, asyncio.Task) and task is not current and not task.done()]
    if flows:
        await asyncio.wait(flows, timeout=FLOWS_CLOSE_WAIT_S if wait_s is None else max(0.0, wait_s))
    return cancelled


# ------------------------------------------------------------------ stop


def _drop_pending_announcements() -> int:
    """K-60/K-66: 'stop' also drops the announcements still waiting to be spoken (the K-18 announcer); never raises."""
    try:
        return int(jarvis_announcer.cancel_pending() or 0)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("pending announcements not dropped: %s", type(e).__name__)
        return 0


async def stop_speaking(interrupt: Callable[[], Awaitable[bool]],
                        forget_typed: Callable[[], int] | None = None) -> bool:
    """
    'stop' (K-60): the announcements still waiting to be spoken are dropped first (one waiting for JARVIS to stop
    speaking would otherwise start as soon as the interrupt lands), and so is the typed-message rescue
    (forget_typed: ConversationRouter.cancel_rescues; a typed reply stopped before its first audio would otherwise be
    asked for again), then interrupt() cuts the live session's current and queued speech (the listener's
    BrainAssistant.interrupt_speech: forced only for a speech that does not allow interruptions), the dashboard's
    speaking state is cleared (notify_ui('STOP')) and STOP_SPEAKING is audited. JARVIS keeps listening: the job never
    ends here and the runtime is never closed. Returns whether speech was interrupted.
    """
    dropped = _drop_pending_announcements()
    typed = 0
    if callable(forget_typed):
        try:
            typed = int(forget_typed() or 0)
        except Exception as e:  # pylint: disable=broad-exception-caught  # never blocks the stop
            logger.debug("STOP: typed rescue not cancelled (%s)", type(e).__name__)
    interrupted = bool(await interrupt())
    await notify_ui("STOP")
    _audit("STOP_SPEAKING", f"SOURCE=ui | INTERRUPTED={interrupted} | ANNOUNCEMENTS_DROPPED={dropped} | "
                            f"TYPED_RESCUES_CANCELLED={typed}")
    logger.info("STOP: speech interrupted=%s, %d pending announcement(s) and %d typed rescue(s) dropped; still "
                "listening (K-60)", interrupted, dropped, typed)
    return interrupted


# ------------------------------------------------------------------ shutdown_agent


def _request_job_end(ctx: CommandContext, reason: str) -> bool:
    """Ends this job the C-23 way: the assistant's request_end (the runtime watches its stop event and then closes the
    session, reports job_end and calls ctx.shutdown; the first reason wins), else the runtime's request_close. False
    without either."""
    request_end = getattr(ctx.assistant, "request_end", None)
    if callable(request_end):
        request_end(reason)
        return True
    request_close = getattr(ctx.runtime, "request_close", None)
    if callable(request_close):
        request_close(reason)
        return True
    logger.error("SHUTDOWN: this job has no end path (no request_end, no runtime)")
    return False


async def run_shutdown_agent(payload: Any, ctx: CommandContext) -> bool:
    """
    ui_command shutdown_agent {restart?: bool = true, confirm?, confirm_id?} (K-60): ends this job only after the owner
    confirmed it (confirm_control: the broker's high-risk card, else payload confirm:true), the C-23 way:
    request_end('terminated_by_owner'), after which the runtime closes the session, reports job_end and calls
    ctx.shutdown, and the bridge's room owner dispatches a fresh job. Without the confirmation nothing happens.
    restart=false would ask the bridge to keep the agent out, which no bridge offers an agent yet: it is logged and
    told. Audited either way (SHUTDOWN_AGENT). Returns True when the job end was requested.
    """
    restart = not (isinstance(payload, dict) and payload.get("restart") is False)
    if not _claim(ctx, "shutdown_agent"):
        return False
    try:
        approved, reason, source = await confirm_control(
            "shutdown_agent", payload, _SHUTDOWN_SUMMARY if restart else _SHUTDOWN_NO_RESTART_SUMMARY, SHUTDOWN_RISK,
            args_preview=f"restart={str(restart).lower()}", arguments={"restart": restart}, ctx=ctx)
    finally:
        _release(ctx, "shutdown_agent")
    _audit("SHUTDOWN_AGENT", f"ALLOW={approved} | SOURCE={source} | REASON={reason} | RESTART={restart}",
           "WARNING" if approved else "INFO")
    if not approved and _job_ended(ctx):
        logger.info("SHUTDOWN: the job ended before the owner answered; shutdown_agent cancelled (%s)", reason)
        return False
    if not approved:
        logger.info("SHUTDOWN: shutdown_agent not confirmed (%s); the session keeps running", reason)
        await _tell(ctx, f"Shutdown not confirmed ({reason}); the session keeps running.")
        return False
    if not restart:
        logger.warning("SHUTDOWN: restart=false, but the bridge has no agent-side pause yet: its room owner may "
                       "dispatch a fresh job (the dashboard's Pause keeps JARVIS out)")
        await _tell(ctx, "restart=false is not supported by the bridge yet; it may start a fresh session (use Pause).")
    logger.warning("SHUTDOWN: the owner confirmed shutdown_agent (%s); this job ends (%s)", source, SHUTDOWN_REASON)
    return _request_job_end(ctx, SHUTDOWN_REASON)


# ------------------------------------------------------------------ clear_memory


def _backup_shown(path: str | None) -> str | None:
    """A backup path for the dashboard and the audit: relative to the project root (backups/memory/<file>), else only
    the file name."""
    if not path:
        return None
    try:
        relative = os.path.relpath(path, config.project_root)
    except ValueError:  # another drive (Windows)
        return os.path.basename(path)
    return os.path.basename(path) if relative.startswith("..") else relative.replace(os.sep, "/")


async def run_clear_memory(payload: Any, ctx: CommandContext) -> dict[str, Any] | None:
    """
    ui_command clear_memory {include_vector?: bool = false, confirm?} (K-60): after the owner's medium-risk confirmation
    the conversation memory is cleared WITHOUT a delete (ConversationMemory.clear moves the file to
    backups/memory/<UTC ts>_<user>_memory.json and empties its cache; C-52: through the job's
    MemoryExtractor.clear_memory, so the messages of this job its memory loop has not written yet never reach the
    fresh file) and memory_cleared {scope: 'short_term', backup, ts} goes out (never memory_sync). include_vector then
    asks a second, high-risk confirmation (the vector store has no backup) and wipes it (jarvis_vector_db.clear_memory),
    followed by memory_cleared {scope: 'all', ...}. Without a confirmation nothing is cleared. Audited (CLEAR_MEMORY).
    Returns the last memory_cleared payload, else None.
    """
    if not _claim(ctx, "clear_memory"):
        return None
    try:
        return await _clear_short_term_memory(payload, ctx)
    finally:
        _release(ctx, "clear_memory")


async def _clear_short_term_memory(payload: Any, ctx: CommandContext) -> dict[str, Any] | None:
    include_vector = isinstance(payload, dict) and payload.get("include_vector") is True
    approved, reason, source = await confirm_control(
        "clear_memory", payload, _CLEAR_MEMORY_SUMMARY, CLEAR_MEMORY_RISK,
        args_preview=f"scope=short_term include_vector={str(include_vector).lower()}",
        arguments={"scope": "short_term", "include_vector": include_vector}, ctx=ctx)
    if not approved:
        _audit("CLEAR_MEMORY", f"ALLOW=False | SOURCE={source} | REASON={reason} | SCOPE=short_term")
        logger.info("MEMORY: clear_memory not confirmed (%s); nothing was cleared", reason)
        await _tell(ctx, f"Clear memory not confirmed ({reason}); nothing was cleared.")
        return None
    try:
        backup = _backup_shown(await _clear_memory_file(getattr(ctx.assistant, "memory_extractor", None)))
    except Exception as e:  # pylint: disable=broad-exception-caught  # OSError: not moved, the file is kept as it was
        _audit("CLEAR_MEMORY", f"ALLOW=True | SOURCE={source} | REASON={reason} | SCOPE=short_term | "
                               f"RESULT=failed ({type(e).__name__})", "WARNING")
        logger.error("MEMORY: clear_memory failed (%s); nothing was deleted", type(e).__name__)
        await _tell(ctx, "Clear memory failed: the memory file could not be moved; nothing was deleted.")
        return None
    _audit("CLEAR_MEMORY", f"ALLOW=True | SOURCE={source} | REASON={reason} | SCOPE=short_term | "
                           f"BACKUP={backup or 'none'}", "WARNING")
    cleared: dict[str, Any] = {"scope": "short_term", "backup": backup, "ts": time.time()}
    await notify_event("memory_cleared", dict(cleared))
    logger.info("🧠 Short-term memory cleared (backup %s)", backup or "none: there was no memory file")
    if not include_vector:
        return cleared
    return await _clear_vector_memory(payload, ctx, source, cleared)


async def _clear_memory_file(extractor: Any) -> str | None:
    """C-52: the job's MemoryExtractor clears (clear_memory: what this job said before the clear and the memory loop has
    not written yet is forgotten, then ConversationMemory.clear moves the file), so the loop's next pass never writes
    it into the fresh file. An extractor without clear_memory: its memory's clear() alone (C-51)."""
    clear = getattr(extractor, "clear_memory", None)
    result = clear() if callable(clear) else None
    if inspect.isawaitable(result):
        return await result
    return await getattr(extractor, "memory", None).clear()


async def _clear_vector_memory(payload: Any, ctx: CommandContext, first_source: str,
                               cleared: dict[str, Any]) -> dict[str, Any]:
    """include_vector: a second, high-risk confirmation, then the vector wipe. One payload confirm:true cannot be that
    second confirmation, so without the broker the wipe never runs."""
    if first_source != "broker":
        _audit("CLEAR_MEMORY", "ALLOW=False | SOURCE=ui_payload | REASON=needs_broker | SCOPE=vector")
        logger.warning("MEMORY: the vector wipe needs a second confirmation through the confirm broker; not run")
        await _tell(ctx, "Long-term memory not cleared: it needs a second confirmation on the dashboard.")
        return cleared
    approved, reason, source = await confirm_control(
        "clear_memory", payload, _CLEAR_VECTOR_SUMMARY, CLEAR_VECTOR_RISK, args_preview="scope=vector (no backup)",
        arguments={"scope": "vector"}, ctx=ctx)
    if not approved:
        _audit("CLEAR_MEMORY", f"ALLOW=False | SOURCE={source} | REASON={reason} | SCOPE=vector")
        logger.info("MEMORY: the vector wipe was not confirmed (%s); long-term memory kept", reason)
        await _tell(ctx, f"Long-term memory not cleared ({reason}); it is kept.")
        return cleared
    try:
        wiped = bool(await jarvis_vector_db.clear_memory())
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("MEMORY: the vector wipe raised %s", type(e).__name__)
        wiped = False
    _audit("CLEAR_MEMORY", f"ALLOW=True | SOURCE={source} | REASON={reason} | SCOPE=vector | "
                           f"RESULT={'cleared' if wiped else 'failed'}", "WARNING")
    if not wiped:
        logger.error("MEMORY: the vector wipe failed; long-term memory kept")
        await _tell(ctx, "Long-term memory could not be cleared.")
        return cleared
    cleared = {"scope": "all", "backup": cleared.get("backup"), "ts": time.time()}
    await notify_event("memory_cleared", dict(cleared))
    logger.info("🧠 Long-term (vector) memory cleared too")
    return cleared
