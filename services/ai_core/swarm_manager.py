"""
# swarm_manager.py
Implements Task Swarming / Coordinator Mode (Ultraplan) for JARVIS.
Spawns parallel LLM sub-agents to achieve tasks autonomously and concurrently.
Every LLM call goes through services/ai_core/llm_text.generate_text (K-30): Gemini with its model fallback (a
retired id such as gemini-2.5-flash, which 404ed for every swarm call on 2026-10-08, is skipped), then Groq and
OpenRouter. The key never appears in a URL or an error text (K-29). The swarm is active when any of their three
keys is set (C-81).

Status lines and the final report reach the chat as assistant messages with source 'system' (K-58). The old
notify 'agent_message' was handled by no bridge, so every final report was lost (C-43, 2026-10-08).

C-81: the voice tool start_swarm_mode and the ui_command 'swarm' (a typed '/swarm <task>' too) share one start
path, start_swarm(task). It answers 'started' at once and runs the swarm as a K-20 background job 'swarm', so the
Jobs panel shows its progress (K-31) and job_cancel stops it. When the report is in the chat, a 1-2 sentence summary
is announced (K-18): shown in the chat and spoken. Without the jobs module the swarm runs as a plain task (no
progress, no cancel), because an inline run would be cut off by the tool's 15 s timeout.
"""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.ai_core.llm_text import generate_text
from services.utils import jarvis_announcer
from services.utils.jarvis_bridge import notify_transcription
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

logger = setup_logger("SWARM-MANAGER")

SWARM_SOURCE = "system"  # K-58: the swarm's messages are neither voice nor typed
# K-30's three tiers: llm_text falls back across them, so any one key makes the swarm usable (C-81 step 1).
PROVIDER_KEY_NAMES: tuple[str, ...] = ("GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
MAX_INSTRUCTION_CHARS = 4000  # C-81 step 2: each sub-agent instruction, and the task itself, is cut to this
SWARM_JOB_TOOL = "swarm"  # the K-20 job's tool name (Jobs panel, K-31)
# The whole swarm: three K-30 calls in a row (plan, sub-agents in parallel, merge) that may each fall through every
# tier (60 s per attempt) stay inside it.
SWARM_RUN_TIMEOUT_S = 600.0
SUMMARY_TIMEOUT_S = 60.0  # the spoken summary's own LLM call, every tier included
SUMMARY_ATTEMPT_TIMEOUT_S = 20.0
SWARM_JOB_TIMEOUT_S = SWARM_RUN_TIMEOUT_S + SUMMARY_TIMEOUT_S + 60.0  # jarvis_jobs' own cap, only a backstop
SUMMARY_INPUT_CHARS = 8000
SUMMARY_MAX_CHARS = 300

STARTED_MESSAGE = ("Sir, swarm mode shuru ho gaya: sub-agents kaam kar rahe hain. Progress chat mein [SWARM] lines "
                   "mein aayegi aur report tayyar hote hi chat mein aa jayegi.")
ALREADY_RUNNING_MESSAGE = "Sir, yahi swarm pehle se chal raha hai; report tayyar hote hi chat mein aa jayegi."
EMPTY_TASK_MESSAGE = "Sir, swarm ke liye kaam batayein."
OFFLINE_MESSAGE = ("Sir, Swarm mode offline hai: GOOGLE_API_KEY, GROQ_API_KEY ya OPENROUTER_API_KEY .env mein "
                   "set karein.")
DECOMPOSE_FAILED_MESSAGE = "Sir, task decomposition fail ho gayi."
CRITICAL_MESSAGE = "Sir, swarm coordination mein critical error aa gayi hai."
MERGE_FAILED_PREFIX = "Sir, final merge nahi ho saki."  # the raw-reports fallback of _aggregate_results
MERGE_FAILED_LINE = ("Sir, swarm ke sub-agents ki reports chat mein hain, magar unhein ek report mein merge nahi "
                     "kar saka.")
DONE_LINE = "Sir, swarm ka kaam mukammal ho gaya, poori report chat mein hai."
CANCELLED_LINE = "Swarm cancel kar diya gaya."
SUMMARY_PROMPT = (
    "Summarize the report below for Sir Matloob in one or two short sentences of Roman Urdu that can be spoken "
    "aloud (at most 40 words): only the main answer or recommendation, with no Markdown, no list and no "
    "greeting.\n\nReport:\n"
)

ProgressFn = Callable[[float | None, str], Awaitable[None]]  # JobContext.progress (K-20)

_status_tasks: set[asyncio.Task[None]] = set()  # status sends in flight (a bare create_task can be collected)
_background_runs: set[asyncio.Task[Any]] = set()  # swarms started without the jobs module
# (event loop, normalized task) -> id of its swarm job: a repeated start on the same loop joins a live one
_swarm_jobs: dict[tuple[asyncio.AbstractEventLoop, str], str] = {}

_HEADING = re.compile(r"(?m)^[ \t]*#.*$")
_LIST_MARK = re.compile(r"(?m)^\s*(?:[-+]|\d+[.)])\s+")
_MARKDOWN = re.compile(r"[*_`#>|~]+")
_SENTENCE_END = re.compile(r"(?<=[.!?" + chr(0x06D4) + r"])\s+")  # chr(0x06D4): the Urdu full stop


def _provider_key_set() -> bool:
    """True when a K-30 provider key has a value; read at call time, so dotenv is already loaded."""
    return any((os.getenv(name) or "").strip() for name in PROVIDER_KEY_NAMES)


def _clip(text: Any, limit: int = MAX_INSTRUCTION_CHARS) -> str:
    """text stripped and cut to limit characters ('' for no text)."""
    return str(text or "").strip()[:limit]


def _label(value: Any, default: str, limit: int) -> str:
    """A plan item's id or role as one short line; default when it is empty or not text."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    return text[:limit] or default


def _plan_from(raw: Any) -> list[dict[str, str]]:
    """The decomposed plan as {id, role, instruction} strings. The plan is model output, not a schema: an item that
    is not an object or has no instruction is dropped."""
    if not isinstance(raw, list):
        return []
    plan: list[dict[str, str]] = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        instruction = item.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            continue
        plan.append({"id": _label(item.get("id"), f"agent_{number}", 40),
                     "role": _label(item.get("role"), "Worker", 60),
                     "instruction": instruction.strip()})
    return plan


async def _report_progress(progress: ProgressFn | None, percent: float, message: str) -> None:
    """One Jobs-panel step (K-31); a failing callback never stops the swarm."""
    if progress is None:
        return
    try:
        await progress(percent, message)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("swarm progress not sent: %s", type(e).__name__)


async def _flush_status_lines() -> None:
    """Waits (at most 5 s) for this loop's status lines in flight, so the report follows them in the chat. The set is
    shared by every session's loop (on the PC worker each runs in a job thread of its own, which adds and drops its
    tasks meanwhile), so a snapshot of it is walked. Only this loop's tasks are waited for: waiting for another loop's
    task costs up to the full 5 s (a stopped loop never finishes it), and a done task of a closed loop makes
    asyncio.wait raise ('Event loop is closed'). A done task leaves the set here: its discard callback never runs when
    its loop stopped first."""
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Task[None]] = set()
    for task in tuple(_status_tasks):
        if task.done():
            _status_tasks.discard(task)
        elif task.get_loop() is loop:
            pending.add(task)
    if pending:
        await asyncio.wait(pending, timeout=5.0)


async def _call_gemini(prompt: str) -> str:
    """One prompt through llm_text (K-30); its text. Raises LLMUnavailableError (masked reasons) when every tier
    fails."""
    result = await generate_text(prompt, max_output_tokens=2048, temperature=0.7)
    return result.text


@dataclass(frozen=True)
class SwarmOutcome:
    """One swarm run. ok: a report reached the chat (merged by the LLM, else the raw sub-agent reports); otherwise
    report is the fixed Roman-Urdu line that says why nothing ran."""
    ok: bool
    report: str

    @property
    def merged(self) -> bool:
        return self.ok and not self.report.startswith(MERGE_FAILED_PREFIX)


class SwarmCoordinator:
    """
    Coordinates multi-agent swarming.
    Spawns background clones (sub-agents) for parallel task processing.
    Architecture:
      1. Decompose  → LLM splits task into 2-3 isolated sub-tasks
      2. Execute    → asyncio.gather runs them in parallel (max 3)
      3. Aggregate  → LLM synthesizes results into a final Markdown report
    """

    MAX_AGENTS = 3

    @property
    def is_active(self) -> bool:
        """True when any LLM provider key (Gemini, Groq or OpenRouter) is set: llm_text falls back across them."""
        return _provider_key_set()

    async def _send_ui_update(self, msg: str) -> None:
        """Pushes a real-time status line to the chat without waiting for the bridge."""
        logger.info("Swarm UI Update: %s", msg)
        task = asyncio.create_task(notify_transcription("assistant", f"[SWARM] {msg}", source=SWARM_SOURCE))
        _status_tasks.add(task)
        task.add_done_callback(_status_tasks.discard)

    async def _decompose_task(self, user_intent: str) -> list[dict[str, str]]:
        """Uses the LLM to split the user request into 2-3 parallel sub-tasks."""
        await self._send_ui_update("Task breakdown shuru kar raha hun...")

        prompt = (
            "You are an ultra-planner. Decompose the following user task into exactly "
            "2 or 3 parallel sub-tasks that can be executed independently by specialist agents.\n"
            f"Task: {user_intent}\n\n"
            "Output ONLY a valid JSON array, no markdown fences:\n"
            '[{"id":"agent_1","role":"Search Specialist","instruction":"..."},'
            '{"id":"agent_2","role":"Code Writer","instruction":"..."}]'
        )
        try:
            text = await _call_gemini(prompt)
            # Strip possible markdown fences
            text = text.strip()
            for fence in ("```json", "```"):
                if text.startswith(fence):
                    text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            tasks = _plan_from(json.loads(text.strip()))
            if not tasks:
                raise ValueError("the plan holds no sub-task with an instruction")
            logger.info("Decomposed into %d sub-tasks.", len(tasks))
            return tasks
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Task decomposition failed: %s", redact_secrets(str(e)))
            await self._send_ui_update("Task decomposition mein error aagaya. Single-agent fallback activated.")
            return [{"id": "agent_1", "role": "Generalist", "instruction": user_intent}]

    async def _run_sub_agent(self, task: dict[str, str]) -> dict[str, str]:
        """Runs one isolated sub-agent and returns its result dict."""
        agent_id = task.get("id", "agent_X")
        agent_role = task.get("role", "Worker")
        instruction = _clip(task.get("instruction", ""))  # C-81: at most MAX_INSTRUCTION_CHARS reach the LLM

        await self._send_ui_update(
            f"Sub-agent '{agent_role}' ({agent_id}) start ho gaya..."
        )

        prompt = (
            f"You are a specialized JARVIS sub-agent. Role: {agent_role}.\n"
            "Execute the instruction below and return a detailed, accurate report.\n"
            f"Instruction: {instruction}"
        )
        try:
            result_text = await _call_gemini(prompt)
            await self._send_ui_update(
                f"Sub-agent '{agent_role}' ({agent_id}) complete ho gaya."
            )
            return {"id": agent_id, "role": agent_role, "result": result_text}
        except Exception as e:  # pylint: disable=broad-exception-caught
            error = redact_secrets(str(e))
            logger.error("Sub-agent %s failed: %s", agent_id, error)
            await self._send_ui_update(
                f"Sub-agent '{agent_role}' ({agent_id}) failed — API error."
            )
            return {"id": agent_id, "role": agent_role, "result": f"Execution failed: {error}"}

    async def _aggregate_results(
        self, original_task: str, results: list[dict[str, str]]
    ) -> str:
        """Synthesizes all sub-agent reports into one final Markdown response."""
        await self._send_ui_update("Tamam reports compile aur merge kar raha hun...")

        combined = "\n\n".join(
            f"--- {r.get('role')} ({r.get('id')}) ---\n{r.get('result')}"
            for r in results
        )

        prompt = (
            f"Original Task: {original_task}\n\n"
            f"Sub-agent Reports:\n{combined}\n\n"
            "Synthesize these into a single, cohesive Markdown response. "
            'Begin with: "Sir Matloob, Swarm execution complete ho gayi hai. Yeh rahi final report:"\n'
            "Format code blocks properly. Highlight key information. "
            "Do NOT repeat sub-agent names unnecessarily."
        )
        try:
            return await _call_gemini(prompt)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Aggregation failed: %s", redact_secrets(str(e)))
            return f"{MERGE_FAILED_PREFIX} Raw output:\n\n{combined}"

    async def run(self, user_intent: str, progress: ProgressFn | None = None) -> SwarmOutcome:
        """
        Decompose, run the sub-agents in parallel, merge: the final report goes to the chat (K-58) and comes back with
        ok=True. progress (a JobContext.progress, K-20) gets the stages for the Jobs panel. A run without a task or a
        provider key, or one that fails as a whole, posts no report and returns ok=False with the reason. Cancelling
        it cancels the sub-agents' LLM calls.
        """
        task = _clip(user_intent)  # C-81: the task is also the single-agent fallback's instruction
        if not task:
            return SwarmOutcome(False, EMPTY_TASK_MESSAGE)
        if not self.is_active:
            return SwarmOutcome(False, OFFLINE_MESSAGE)

        try:
            # Stage 1: Decompose
            await _report_progress(progress, 5, "Task breakdown")
            sub_tasks = await self._decompose_task(task)
            if not sub_tasks:
                return SwarmOutcome(False, DECOMPOSE_FAILED_MESSAGE)

            # Enforce hard cap on parallel agents
            if len(sub_tasks) > self.MAX_AGENTS:
                await self._send_ui_update(
                    f"Resource cap: {len(sub_tasks)} tasks ko {self.MAX_AGENTS} tak limit kar diya."
                )
                sub_tasks = sub_tasks[: self.MAX_AGENTS]

            # Stage 2: Run all sub-agents in parallel; each one that ends moves the Jobs panel on
            total, finished = len(sub_tasks), 0

            async def tracked(sub_task: dict[str, str]) -> dict[str, str]:
                nonlocal finished
                result = await self._run_sub_agent(sub_task)
                finished += 1
                await _report_progress(progress, 20 + 60 * finished / total,
                                       f"Sub-agent {finished}/{total} mukammal")
                return result

            await _report_progress(progress, 20, f"{total} sub-agents kaam kar rahe hain")
            results: list[dict[str, str]] = list(await asyncio.gather(*(tracked(st) for st in sub_tasks)))

            # Stage 3: Aggregate
            await _report_progress(progress, 85, "Reports merge ho rahi hain")
            final_report = await self._aggregate_results(task, results)

            # the final report is a chat message, after every status line
            await _flush_status_lines()
            await notify_transcription("assistant", final_report, source=SWARM_SOURCE)
            return SwarmOutcome(True, final_report)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("SwarmCoordinator.execute_swarm critical failure: %s",
                         redact_secrets(str(e)), exc_info=True)
            return SwarmOutcome(False, CRITICAL_MESSAGE)

    async def execute_swarm(self, user_intent: str, progress: ProgressFn | None = None) -> str:
        """
        Main entry point for Coordinator Mode: run() and its report (or the line that says why there is none).
        The swarm job (start_swarm, C-81) calls run() itself for the outcome.
        """
        return (await self.run(user_intent, progress)).report


# Singleton — imported by agent_loops.py
swarm_coordinator = SwarmCoordinator()


# ------------------------------------------------------------------ C-81: the job, its summary and the voice tool


def _spoken_summary(text: str) -> str:
    """At most two sentences and SUMMARY_MAX_CHARS of plain text: heading lines, list marks, Markdown marks and line
    breaks out."""
    plain = " ".join(_MARKDOWN.sub(" ", _LIST_MARK.sub("", _HEADING.sub("", text or ""))).split())
    short = " ".join(_SENTENCE_END.split(plain)[:2]).strip()
    if len(short) > SUMMARY_MAX_CHARS:
        short = short[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return short


async def summarize_report(report: str) -> str:
    """The report in one or two short spoken sentences (K-30, plain text); DONE_LINE when no provider answers in
    time."""
    try:
        async with asyncio.timeout(SUMMARY_TIMEOUT_S):
            result = await generate_text(SUMMARY_PROMPT + report[:SUMMARY_INPUT_CHARS], max_output_tokens=1024,
                                         temperature=0.3, timeout=SUMMARY_ATTEMPT_TIMEOUT_S)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # llm_text already logged each failed attempt (masked); the fixed line is spoken instead
        logger.info("Swarm summary unavailable (%s): the fixed line is announced", type(e).__name__)
        return DONE_LINE
    return _spoken_summary(result.text) or DONE_LINE


async def _announce(shown: str, spoken: str | None = None) -> None:
    """K-18: shown in the chat, then spoken (never raises)."""
    await jarvis_announcer.announce(shown, speak=True, kind="swarm", spoken_text=spoken)


async def run_swarm_job(task: str, ctx: Any = None) -> dict[str, Any]:
    """
    The body of a swarm job (C-81), and of a run without the jobs module (ctx None): the swarm, whose report goes to
    the chat, then the announcement (K-18): a 1-2 sentence summary of the report, or the fixed line that says why
    there is none. Returns the K-20 job result {status, message}. A cancel by Sir posts one [SWARM] line and is
    passed on.
    """
    progress: ProgressFn | None = getattr(ctx, "progress", None)
    try:
        async with asyncio.timeout(SWARM_RUN_TIMEOUT_S):
            outcome = await swarm_coordinator.run(task, progress)
    except TimeoutError:
        minutes = max(1, round(SWARM_RUN_TIMEOUT_S / 60))
        outcome = SwarmOutcome(False, f"Sir, swarm {minutes} minute mein mukammal nahi hua, is liye rok diya.")
    except asyncio.CancelledError:
        if getattr(ctx, "cancelled", False):  # job_cancel, not a shutdown
            await swarm_coordinator._send_ui_update(CANCELLED_LINE)  # pylint: disable=protected-access
        raise
    if not outcome.ok:
        await _announce(outcome.report)
        return {"status": "error", "message": outcome.report}
    if not outcome.merged:
        await _announce(MERGE_FAILED_LINE)
        return {"status": "error", "message": MERGE_FAILED_LINE}
    await _report_progress(progress, 95, "Khulasa tayyar ho raha hai")
    summary = await summarize_report(outcome.report)
    if summary == DONE_LINE:
        await _announce(DONE_LINE)
    else:
        await _announce(f"Swarm khulasa: {summary}", f"Swarm ki report chat mein aa gayi hai. {summary}")
    return {"status": "success", "message": summary}


def _jobs_module() -> Any | None:
    """services.utils.jarvis_jobs (K-20) when it can start a job, else None (C-81 step 3 fallback)."""
    try:
        from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    return jarvis_jobs if callable(getattr(jarvis_jobs, "start_job", None)) else None


def _job_title(task: str) -> str:
    return redact_secrets("Swarm: " + " ".join(task.split())[:60])


def _live_swarm_job(jobs: Any, loop: asyncio.AbstractEventLoop, key: str) -> str | None:
    """
    The id of this loop's queued or running swarm job for the same task, else None. Each session has its own event
    loop (on the PC worker a job thread each: LiveKit's THREAD executor, the Windows default), and a session that ends
    mid-swarm stops its loop for good: that job then stays 'running' and job_cancel cannot end it. So only a start on
    the same loop joins a job (Gemini Live repeats a tool call in its own session). Forgotten here: this loop's
    finished jobs and every entry of a stopped loop; another live session's entries are left to that session.
    """
    active = getattr(jobs, "ACTIVE", ("queued", "running"))
    for entry, job_id in list(_swarm_jobs.items()):
        owner = entry[0]
        if owner is loop:
            job = jobs.get_job(job_id)
            if not job or job.get("status") not in active:
                _swarm_jobs.pop(entry, None)
        elif owner.is_closed() or not owner.is_running():
            _swarm_jobs.pop(entry, None)
    return _swarm_jobs.get((loop, key))


def start_swarm(task: Any) -> dict[str, Any]:
    """
    C-81: the one start path of the voice tool start_swarm_mode and the ui_command 'swarm'. On the running event loop
    it starts the swarm as the K-20 background job 'swarm' (Jobs panel progress, job_cancel stops it) and returns at
    once {'status': 'started', 'job_id', 'message'}; the same task started again on the same loop (the same session)
    while its job is still queued or running gets that job's id instead of a second swarm (Gemini Live may repeat a
    tool call). Without a task or a provider key nothing starts: {'status': 'error', 'error_code', 'message'}.
    Without the jobs module the swarm runs as a plain task (job_id None).
    """
    clean = _clip(task) if isinstance(task, str) else ""
    if not clean:
        return {"status": "error", "error_code": "EMPTY_TASK", "message": EMPTY_TASK_MESSAGE}
    if not swarm_coordinator.is_active:
        return {"status": "error", "error_code": "LLM_NOT_CONFIGURED", "message": OFFLINE_MESSAGE}
    jobs = _jobs_module()
    loop = asyncio.get_running_loop()
    if jobs is None:
        logger.warning("SWARM without the jobs module: no Jobs-panel progress or cancel for this run")
        run = loop.create_task(run_swarm_job(clean), name="swarm_run")
        _background_runs.add(run)
        run.add_done_callback(_background_runs.discard)
        return {"status": "started", "job_id": None, "message": STARTED_MESSAGE}

    key = " ".join(clean.lower().split())
    running = _live_swarm_job(jobs, loop, key)
    if running is not None:
        logger.info("SWARM start repeated: job %s already runs this task", running)
        return {"status": "started", "job_id": running, "message": ALREADY_RUNNING_MESSAGE}

    async def factory(ctx: Any) -> dict[str, Any]:
        return await run_swarm_job(clean, ctx)

    job_id = jobs.start_job(SWARM_JOB_TOOL, _job_title(clean), factory, timeout=SWARM_JOB_TIMEOUT_S,
                            announce=False)  # run_swarm_job announces the summary itself
    _swarm_jobs[(loop, key)] = job_id
    return {"status": "started", "job_id": job_id, "message": STARTED_MESSAGE}


@jarvis_tool(execution_timeout=15, arg_policies={"task": "multiline"})
async def start_swarm_mode(task: str) -> dict[str, Any]:
    """
    Starts Swarm mode for a bigger task: 2-3 AI sub-agents work on its parts in parallel (research, comparison,
    planning, code) and one merged Markdown report lands in the chat. It runs in the background and answers
    'started' at once; progress lines start with [SWARM] and a short summary is spoken when the report is ready.
    Use it when Sir says 'swarm mode' or asks for a multi-part comparison or plan, for example 'swarm mode mein
    coding ke liye 150k PKR se kam 3 laptops compare karo'.

    Args:
        task: The whole task in Sir's words.
    """
    result = start_swarm(task)
    # the task is Sir's text: its length at INFO, never the words (C-64 SP-1)
    logger.info("SWARM tool: %s (%d chars)", result.get("error_code") or result["status"], len(task or ""))
    return result
