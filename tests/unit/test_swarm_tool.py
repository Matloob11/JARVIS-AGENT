"""C-81: Swarm mode as the voice tool start_swarm_mode and a K-20 background job, with a spoken summary.

Contracts K-11 (decorator), K-18 (announcer), K-20/K-31 (jobs and their tool_sync events), K-30 (llm_text), K-58
(chat messages), K-71 (placement). No network, no bridge, no live agent: llm_text (or its tiers), the bridge
notifies and the announcer are fakes, and every key is a throwaway test value.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents.llm import function_tool
from livekit.agents.llm.utils import prepare_function_arguments

from services.ai_core import agent_loops, llm_text, swarm_manager
from services.ai_core import jarvis_plugin_manager as pm_module
from services.ai_core import tool_placement as tp
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.ai_core.llm_text import LLMResult, LLMUnavailableError
from services.utils import jarvis_announcer, jarvis_bridge, jarvis_jobs, jarvis_logger
from services.utils.ui_command_registry import CommandContext

FAKE_GOOGLE_KEY = "test-only-c81-google-not-a-key-0123456789"
FAKE_GROQ_KEY = "test-only-c81-groq-not-a-key-0123456789"
TASK = "coding ke liye 150k PKR se kam 3 laptops compare karo"
PLAN = json.dumps([{"id": "agent_1", "role": "Researcher", "instruction": "find laptops under 150k PKR"},
                   {"id": "agent_2", "role": "Analyst", "instruction": "compare them for coding"}])
REPORT = "Sir Matloob, Swarm execution complete ho gayi hai. Yeh rahi final report:\n\n## Laptops\n| a | b |"
SUMMARY = "Lenovo LOQ coding ke liye sab se behtar hai. Acer Nitro doosre number par hai."


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    """Only throwaway provider keys exist while a test runs (the real ones never reach a test)."""
    for name in swarm_manager.PROVIDER_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)


@pytest.fixture(name="chat", autouse=True)
def _chat(monkeypatch) -> list[dict[str, Any]]:
    """K-58 chat messages the swarm and the swarm ui_command post (the bridge client is replaced)."""
    messages: list[dict[str, Any]] = []

    async def fake_notify_transcription(role: str, text: str, **kwargs: Any) -> None:
        messages.append({"role": role, "text": text, **kwargs})

    monkeypatch.setattr(swarm_manager, "notify_transcription", fake_notify_transcription)
    monkeypatch.setattr(agent_loops, "notify_transcription", fake_notify_transcription)
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())  # no UI log forwarding
    return messages


@pytest.fixture(name="events", autouse=True)
def _events(monkeypatch) -> list[dict[str, Any]]:
    """tool_sync payloads (the tool wrapper's and the job's), in the order they were posted."""
    sent: list[dict[str, Any]] = []

    async def fake_notify_event(event_type: str, payload: Any) -> None:
        if event_type == "tool_sync":
            sent.append(dict(payload))

    monkeypatch.setattr(jarvis_bridge, "notify_event", fake_notify_event)
    monkeypatch.setattr(jarvis_jobs, "PROGRESS_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(jarvis_jobs, "_jobs", {})
    return sent


@pytest.fixture(name="announced", autouse=True)
def _announced(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    """K-18 announce calls (shown text, keywords)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_announce(text: str, **kwargs: Any) -> None:
        calls.append((text, kwargs))

    monkeypatch.setattr(jarvis_announcer, "announce", fake_announce)
    return calls


def _fake_llm(monkeypatch, *, plan: str = PLAN, fail: bool = False,
              gate: asyncio.Event | None = None) -> list[str]:
    """swarm_manager.generate_text answers by prompt: the plan, a sub report, the merged report, the summary.
    fail: every call raises LLMUnavailableError; gate: sub-agent calls wait for it."""
    prompts: list[str] = []

    async def fake_generate_text(prompt: str, **_kwargs: Any) -> LLMResult:
        prompts.append(prompt)
        if fail:
            raise LLMUnavailableError(["gemini[gemini-3.5-flash]: ServerError: 503 UNAVAILABLE",
                                       "groq[openai/gpt-oss-120b]: RateLimitError: 429"])
        if "ultra-planner" in prompt:
            return LLMResult(plan, "gemini", "gemini-3.5-flash")
        if prompt.startswith(swarm_manager.SUMMARY_PROMPT):
            return LLMResult(f"## Khulasa\n**{SUMMARY}** Teesra HP Victus hai.", "gemini", "gemini-3.5-flash")
        if "Synthesize" in prompt:
            return LLMResult(REPORT, "gemini", "gemini-3.5-flash")
        if gate is not None:
            await gate.wait()
        return LLMResult("sub report", "gemini", "gemini-3.5-flash")

    monkeypatch.setattr(swarm_manager, "generate_text", fake_generate_text)
    return prompts


def _wrapper(name: str) -> Any:
    found = [tool for tool in plugin_manager.get_tools() if tool.__name__ == name]
    assert len(found) == 1, f"{name}: {len(found)} registrations"
    return found[0]


async def _call_like_livekit(name: str, arguments: dict[str, Any]) -> Any:
    """The tool called as LiveKit calls it: its FunctionTool with the arguments bound from JSON."""
    lk_tool = function_tool(_wrapper(name))
    args, kwargs = prepare_function_arguments(fnc=lk_tool, json_arguments=json.dumps(arguments))
    return await lk_tool(*args, **kwargs)


async def _wait(job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    for _ in range(int(timeout / 0.01)):
        job = jarvis_jobs.get_job(job_id)
        if job and job["status"] not in jarvis_jobs.ACTIVE:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def _drain() -> None:
    """Lets this loop's background tool_sync posts and [SWARM] status lines run."""
    loop = asyncio.get_running_loop()
    for _ in range(20):
        pending = [t for t in (*pm_module._sync_tasks, *swarm_manager._status_tasks)
                   if not t.done() and t.get_loop() is loop]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def _job_events(events: list[dict[str, Any]], job_id: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("job") is True and e["id"] == job_id]


@pytest.fixture(name="logs")
def _logs() -> Iterator[io.StringIO]:
    """Everything the swarm path logs at DEBUG and above, unmasked (shows what the code itself passes in)."""
    raw = io.StringIO()
    handler = logging.StreamHandler(raw)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    loggers = [swarm_manager.logger, llm_text.logger, pm_module.logger, agent_loops.logger,
               logging.getLogger("JARVIS-JOBS"), logging.getLogger("JARVIS-ANNOUNCER")]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    try:
        yield raw
    finally:
        for lg, level in zip(loggers, levels):
            lg.removeHandler(handler)
            lg.setLevel(level)


# ---------------------------------------------------------------- the voice tool and its job


async def test_start_swarm_mode_answers_started_and_the_job_reports_and_announces(monkeypatch, chat, events,
                                                                                  announced):
    prompts = _fake_llm(monkeypatch)
    began = time.monotonic()
    result = await _call_like_livekit("start_swarm_mode", {"task": TASK})
    assert time.monotonic() - began < 3.0
    assert result == {"status": "started", "job_id": result["job_id"], "message": swarm_manager.STARTED_MESSAGE}

    job = await _wait(result["job_id"])
    await _drain()
    assert job["status"] == "done" and job["tool"] == "swarm" and job["title"] == f"Swarm: {TASK[:60]}"
    assert job["message"] == SUMMARY  # two plain sentences: Markdown marks and the third sentence are gone
    assert len(prompts) == 5  # plan, two sub-agents, merge, summary
    assert TASK in prompts[0] and prompts[-1].startswith(swarm_manager.SUMMARY_PROMPT)

    # K-58: every swarm message is an assistant message with source 'system'; the report follows the status lines
    assert all(m["role"] == "assistant" and m["source"] == "system" for m in chat)
    assert chat[-1]["text"] == REPORT
    assert all(m["text"].startswith("[SWARM] ") for m in chat[:-1]) and len(chat) >= 6

    # K-18: one announcement, shown and spoken, after the report
    ((shown, kwargs),) = announced
    assert shown == f"Swarm khulasa: {SUMMARY}"
    assert kwargs["speak"] is True and kwargs["kind"] == "swarm" and SUMMARY in kwargs["spoken_text"]

    # K-31: the job's own tool_sync rows carry job: true and walk 5 -> 95 before the end
    rows = _job_events(events, result["job_id"])
    assert (rows[0]["phase"], rows[0]["status"]) == ("start", "queued")
    assert (rows[-1]["phase"], rows[-1]["status"]) == ("end", "success") and rows[-1]["progress"] == 100.0
    steps = [e["progress"] for e in rows if e["phase"] == "progress" and "progress" in e]
    assert steps == [5.0, 20.0, 50.0, 80.0, 85.0, 95.0]
    assert all(e["action"] == job["title"] for e in rows)
    # K-13: the tool call itself is one start and one end row ('started')
    tool_rows = [(e["phase"], e["status"]) for e in events if e["tool"] == "start_swarm_mode"]
    assert tool_rows == [("start", "running"), ("end", "started")]


async def test_a_repeated_start_of_the_same_task_joins_the_running_job(monkeypatch, announced):
    """Gemini Live may repeat a tool call: the same task (any case or spacing) while its job runs is that job."""
    gate = asyncio.Event()
    _fake_llm(monkeypatch, gate=gate)
    first = swarm_manager.start_swarm(TASK)
    again = await _call_like_livekit("start_swarm_mode", {"task": f"  {TASK.upper()}  "})
    assert again == {"status": "started", "job_id": first["job_id"],
                     "message": swarm_manager.ALREADY_RUNNING_MESSAGE}
    other = swarm_manager.start_swarm("do laptops compare karo")
    assert other["job_id"] != first["job_id"] and other["message"] == swarm_manager.STARTED_MESSAGE
    gate.set()
    await _wait(first["job_id"])
    await _wait(other["job_id"])
    later = swarm_manager.start_swarm(TASK)  # the first one finished: a new run
    assert later["job_id"] not in (first["job_id"], other["job_id"])
    assert later["message"] == swarm_manager.STARTED_MESSAGE
    await _wait(later["job_id"])
    assert len(jarvis_jobs.list_jobs()) == 3
    assert sum(text.startswith("Swarm khulasa: ") for text, _ in announced) == 3


def test_the_tool_is_registered_with_its_k11_options_and_placed_on_the_vps():
    meta = plugin_manager.get_tool_meta("start_swarm_mode")
    assert meta is not None
    assert (meta["timeout"], meta["risk"], meta["arg_policies"]) == (15.0, "low", {"task": "multiline"})
    assert tp.PLACEMENTS["start_swarm_mode"].kind == tp.VPS
    assert not tp.laptop_capable(tp.PLACEMENTS["start_swarm_mode"])


@pytest.mark.parametrize("name", ["GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"])
def test_any_provider_key_makes_the_swarm_active(monkeypatch, name):
    for other in swarm_manager.PROVIDER_KEY_NAMES:
        monkeypatch.delenv(other, raising=False)
    assert swarm_manager.SwarmCoordinator().is_active is False
    monkeypatch.setenv(name, "  ")
    assert swarm_manager.SwarmCoordinator().is_active is False  # blank is not a key
    monkeypatch.setenv(name, "test-only-value")
    assert swarm_manager.SwarmCoordinator().is_active is True


async def test_without_a_key_or_a_task_nothing_starts(monkeypatch, chat, announced):
    prompts = _fake_llm(monkeypatch)
    assert swarm_manager.start_swarm("   ") == {"status": "error", "error_code": "EMPTY_TASK",
                                               "message": swarm_manager.EMPTY_TASK_MESSAGE}
    assert swarm_manager.start_swarm({"task": TASK})["error_code"] == "EMPTY_TASK"  # only a str is a task
    monkeypatch.delenv("GOOGLE_API_KEY")
    result = await _call_like_livekit("start_swarm_mode", {"task": TASK})
    assert result == {"status": "error", "error_code": "LLM_NOT_CONFIGURED",
                      "message": swarm_manager.OFFLINE_MESSAGE}
    await _drain()
    assert not jarvis_jobs.list_jobs() and not prompts and not chat and not announced


async def test_each_instruction_is_capped_and_at_most_three_agents_run(monkeypatch, chat):
    long_instruction = "x" * 10_000
    plan = json.dumps([{"id": f"agent_{n}", "role": f"Role {n}", "instruction": long_instruction}
                       for n in range(1, 6)])
    prompts = _fake_llm(monkeypatch, plan=plan)
    outcome = await swarm_manager.SwarmCoordinator().run(TASK)
    assert outcome.ok and outcome.merged and outcome.report == REPORT
    sub_prompts = [p for p in prompts if p.startswith("You are a specialized JARVIS sub-agent")]
    assert len(sub_prompts) == swarm_manager.SwarmCoordinator.MAX_AGENTS == 3
    for prompt in sub_prompts:
        instruction = prompt.split("Instruction: ", 1)[1]
        assert instruction == "x" * swarm_manager.MAX_INSTRUCTION_CHARS == "x" * 4000
    await _drain()
    assert any("Resource cap: 5 tasks ko 3 tak" in m["text"] for m in chat)


async def test_the_task_itself_is_capped_before_any_prompt(monkeypatch):
    prompts = _fake_llm(monkeypatch)
    await swarm_manager.SwarmCoordinator().run("y" * 9000)
    assert "y" * 4000 in prompts[0] and "y" * 4001 not in prompts[0]


@pytest.mark.parametrize("plan", ['{"id": "agent_1"}', '[1, "two", {"role": "no instruction"}]', "[]",
                                  "not json at all"])
async def test_an_unusable_plan_falls_back_to_one_agent_with_the_task(monkeypatch, plan):
    prompts = _fake_llm(monkeypatch, plan=plan)
    outcome = await swarm_manager.SwarmCoordinator().run(TASK)
    sub_prompts = [p for p in prompts if p.startswith("You are a specialized JARVIS sub-agent")]
    assert outcome.ok and len(sub_prompts) == 1
    assert "Role: Generalist" in sub_prompts[0] and sub_prompts[0].endswith(f"Instruction: {TASK}")


async def test_plan_fields_are_cleaned_to_short_strings(monkeypatch):
    plan = json.dumps([{"id": 7, "role": "  Deep\n Researcher " + "r" * 100, "instruction": " find "},
                       {"instruction": "compare"}])
    _fake_llm(monkeypatch, plan=plan)
    tasks = await swarm_manager.SwarmCoordinator()._decompose_task(TASK)
    assert tasks[0]["id"] == "agent_1" and tasks[0]["instruction"] == "find"
    assert tasks[0]["role"].startswith("Deep Researcher r") and len(tasks[0]["role"]) == 60
    assert tasks[1] == {"id": "agent_2", "role": "Worker", "instruction": "compare"}


# ---------------------------------------------------------------- K-30: Gemini failing, keys never logged


async def test_with_gemini_failing_the_swarm_completes_through_groq_and_logs_no_key(monkeypatch, chat, announced,
                                                                                    logs):
    """The real llm_text.generate_text with its tiers replaced: every Gemini attempt fails (a 503) and Groq
    answers, so the swarm, its report and the spoken summary all come from Groq. Nothing logs 'key=' or a key."""
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ_KEY)
    tiers: list[str] = []

    async def gemini_down(_request: Any, _model: Any, reasons: list[str]) -> None:
        tiers.append("gemini")
        llm_text._fail(reasons, "gemini", "gemini-3.5-flash", "ServerError: 503 UNAVAILABLE")
        return None

    async def groq_up(request: Any, _reasons: list[str]) -> LLMResult:
        tiers.append("groq")
        prompt = request.prompt
        if "ultra-planner" in prompt:
            text = PLAN
        elif prompt.startswith(swarm_manager.SUMMARY_PROMPT):
            text = SUMMARY
        elif "Synthesize" in prompt:
            text = REPORT
        else:
            text = "sub report"
        return LLMResult(text, "groq", "openai/gpt-oss-120b")

    monkeypatch.setattr(llm_text, "_gemini_tier", gemini_down)
    monkeypatch.setattr(llm_text, "_groq_tier", groq_up)
    result = await _call_like_livekit("start_swarm_mode", {"task": TASK})
    job = await _wait(result["job_id"])
    await _drain()

    assert job["status"] == "done" and job["message"] == SUMMARY
    assert tiers.count("gemini") == tiers.count("groq") == 5  # every call fell through to Groq
    assert chat[-1]["text"] == REPORT
    assert announced[0][0] == f"Swarm khulasa: {SUMMARY}"
    logged = logs.getvalue()
    assert "503 UNAVAILABLE" in logged and "SWARM tool: started" in logged  # the capture did see the run
    assert "key=" not in logged.lower()
    assert FAKE_GOOGLE_KEY not in logged and FAKE_GROQ_KEY not in logged
    assert TASK not in logged  # Sir's words never reach a log line at INFO (C-64 SP-1)


# ---------------------------------------------------------------- failures, timeout and cancel


async def test_a_swarm_whose_llm_calls_all_fail_announces_why_without_a_summary(monkeypatch, chat, announced):
    prompts = _fake_llm(monkeypatch, fail=True)
    started = swarm_manager.start_swarm(TASK)
    job = await _wait(started["job_id"])
    await _drain()
    assert len(prompts) == 3  # plan, the single fallback agent, merge; no summary call for raw reports
    assert chat[-1]["text"].startswith(swarm_manager.MERGE_FAILED_PREFIX)  # the raw reports are still shown
    assert job["status"] == "error" and job["message"] == swarm_manager.MERGE_FAILED_LINE
    assert announced == [(swarm_manager.MERGE_FAILED_LINE,
                          {"speak": True, "kind": "swarm", "spoken_text": None})]


async def test_a_summary_that_fails_speaks_the_fixed_line(monkeypatch):
    _fake_llm(monkeypatch)

    async def failing(_prompt: str, **_kwargs: Any) -> LLMResult:
        raise LLMUnavailableError(["groq[openai/gpt-oss-120b]: timeout after 20s"])

    assert await swarm_manager.summarize_report(REPORT) != swarm_manager.DONE_LINE
    monkeypatch.setattr(swarm_manager, "generate_text", failing)
    assert await swarm_manager.summarize_report(REPORT) == swarm_manager.DONE_LINE


async def test_a_swarm_past_its_time_is_stopped_and_announced(monkeypatch, announced):
    gate = asyncio.Event()  # never set: the sub-agents hang
    _fake_llm(monkeypatch, gate=gate)
    monkeypatch.setattr(swarm_manager, "SWARM_RUN_TIMEOUT_S", 0.2)
    job = await _wait(swarm_manager.start_swarm(TASK)["job_id"])
    assert job["status"] == "error" and "mukammal nahi hua" in job["message"]
    assert [text for text, _ in announced] == [job["message"]]


async def test_cancel_from_the_jobs_panel_stops_the_swarm_without_an_announcement(monkeypatch, chat, events,
                                                                                  announced):
    gate = asyncio.Event()
    prompts = _fake_llm(monkeypatch, gate=gate)
    job_id = swarm_manager.start_swarm(TASK)["job_id"]
    for _ in range(500):
        if any(p.startswith("You are a specialized JARVIS sub-agent") for p in prompts):
            break
        await asyncio.sleep(0.01)
    listener = agent_loops.UIBridgeListener(MagicMock())
    await listener._handle_command({"type": "job_cancel", "payload": {"job_id": job_id}})
    job = await _wait(job_id)
    await _drain()
    assert job["status"] == "cancelled" and not announced
    assert chat[-1]["text"] == f"[SWARM] {swarm_manager.CANCELLED_LINE}"
    assert not any(m["text"] == REPORT for m in chat)
    assert _job_events(events, job_id)[-1]["status"] == "cancelled"


async def test_without_the_jobs_module_the_swarm_still_runs_and_announces(monkeypatch, chat, announced):
    _fake_llm(monkeypatch)
    monkeypatch.setattr(swarm_manager, "_jobs_module", lambda: None)
    result = swarm_manager.start_swarm(TASK)
    assert result == {"status": "started", "job_id": None, "message": swarm_manager.STARTED_MESSAGE}
    runs = list(swarm_manager._background_runs)
    assert len(runs) == 1
    assert (await runs[0]) == {"status": "success", "message": SUMMARY}
    assert chat[-1]["text"] == REPORT and announced[0][0] == f"Swarm khulasa: {SUMMARY}"


# ---------------------------------------------------------------- the ui_command 'swarm' (typed '/swarm' too)


@pytest.mark.parametrize("payload", [{"task": f"  {TASK} "}, TASK])
async def test_the_swarm_ui_command_runs_the_same_job(monkeypatch, announced, payload):
    prompts = _fake_llm(monkeypatch)
    ran = await agent_loops.dispatch("swarm", payload, CommandContext(assistant=MagicMock()))
    assert ran is True
    (job,) = jarvis_jobs.list_jobs()
    assert job["tool"] == "swarm" and job["title"] == f"Swarm: {TASK[:60]}"
    finished = await _wait(job["job_id"])
    assert finished["status"] == "done"
    assert f"Task: {TASK}\n" in prompts[0]  # the task itself, never the payload dict
    assert announced[0][0] == f"Swarm khulasa: {SUMMARY}"


@pytest.mark.parametrize("payload", [None, "", "   ", {"task": ""}, {"text": TASK}, {"task": 5}, ["x"]])
async def test_a_swarm_ui_command_without_a_task_is_ignored(monkeypatch, chat, payload):
    prompts = _fake_llm(monkeypatch)
    assert agent_loops.swarm_task_input(payload) is None
    await agent_loops.dispatch("swarm", payload, CommandContext(assistant=MagicMock()))
    assert not jarvis_jobs.list_jobs() and not prompts and not chat


async def test_the_swarm_ui_command_without_a_key_says_so_in_the_chat(monkeypatch, chat):
    prompts = _fake_llm(monkeypatch)
    monkeypatch.delenv("GOOGLE_API_KEY")
    await agent_loops.dispatch("swarm", {"task": TASK}, CommandContext(assistant=MagicMock()))
    assert not jarvis_jobs.list_jobs() and not prompts
    assert chat == [{"role": "assistant", "text": f"[SWARM] {swarm_manager.OFFLINE_MESSAGE}", "source": "system"}]


# ---------------------------------------------------------------- helpers


def test_the_spoken_summary_is_two_short_plain_sentences():
    text = "## Natija\n- **Lenovo LOQ** sab se behtar hai.\n- Acer `Nitro` doosra hai!\n3. HP Victus teesra hai."
    assert swarm_manager._spoken_summary(text) == "Lenovo LOQ sab se behtar hai. Acer Nitro doosra hai!"
    long_text = "lafz " * 200
    short = swarm_manager._spoken_summary(long_text)
    assert len(short) <= swarm_manager.SUMMARY_MAX_CHARS + 3 and short.endswith("...")
    assert swarm_manager._spoken_summary("  ") == ""


# ---------------------------------------------------------------- other sessions: an event loop per job thread
# LiveKit's default executor on Windows (the PC worker) is THREAD: each session runs in a job thread with an event
# loop of its own, and a session that ends stops that loop for good (proc_client never runs it again and nothing
# cancels the jarvis_jobs tasks left on it). The swarm's module state is shared by all of these loops.


def _session_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """Another session's event loop, running in a thread of its own."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _end_session(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    """The session ends: its loop stops and, as on the worker, nothing left on it runs again."""
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


def _discard_session_loop(loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    """Test cleanup only: stops the loop if it still runs, cancels what is left on it (in a thread of its own, since
    this thread runs the test's loop) and closes it."""
    if thread.is_alive():
        _end_session(loop, thread)
    if loop.is_closed():
        return

    async def cancel_the_rest() -> None:
        rest = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in rest:
            task.cancel()
        await asyncio.gather(*rest, return_exceptions=True)

    cleanup = threading.Thread(target=loop.run_until_complete, args=(cancel_the_rest(),))
    cleanup.start()
    cleanup.join(5)
    loop.close()


def _stall_llm_on(monkeypatch, loop: asyncio.AbstractEventLoop) -> threading.Event:
    """The fake LLM of _fake_llm, except that it never answers on loop, so that session's swarm stays running. The
    event is set at its first call there."""
    _fake_llm(monkeypatch)
    answer = swarm_manager.generate_text
    stalled = threading.Event()

    async def stall_there(prompt: str, **kwargs: Any) -> LLMResult:
        if asyncio.get_running_loop() is loop:
            stalled.set()
            await loop.create_future()  # never resolves
        return await answer(prompt, **kwargs)

    monkeypatch.setattr(swarm_manager, "generate_text", stall_there)
    return stalled


def _start_on(loop: asyncio.AbstractEventLoop, task: str) -> dict[str, Any]:
    """start_swarm(task) on that session's loop, as its tool call or ui_command runs it."""
    async def start() -> dict[str, Any]:
        return swarm_manager.start_swarm(task)

    return asyncio.run_coroutine_threadsafe(start(), loop).result(5)


async def test_a_swarm_left_running_by_an_ended_session_never_blocks_a_new_start(monkeypatch, announced):
    """Session A starts the swarm and ends mid-run: its loop stops, so the job stays 'running' for good (job_cancel
    cannot end it either). The same task started later on another loop (the next session) is a new swarm that runs
    and reports, and a repeat on that loop joins the new job."""
    session_a, thread = _session_loop()
    stalled = _stall_llm_on(monkeypatch, session_a)
    try:
        first = _start_on(session_a, TASK)
        assert first["message"] == swarm_manager.STARTED_MESSAGE and stalled.wait(5)
        _end_session(session_a, thread)
        assert jarvis_jobs.get_job(first["job_id"])["status"] == "running"  # and so it stays

        again = swarm_manager.start_swarm(TASK)
        assert again["job_id"] != first["job_id"] and again["message"] == swarm_manager.STARTED_MESSAGE
        repeat = swarm_manager.start_swarm(TASK)
        assert repeat == {"status": "started", "job_id": again["job_id"],
                          "message": swarm_manager.ALREADY_RUNNING_MESSAGE}
        job = await _wait(again["job_id"])
        assert job["status"] == "done" and job["message"] == SUMMARY
        assert [text for text, _ in announced] == [f"Swarm khulasa: {SUMMARY}"]
    finally:
        _discard_session_loop(session_a, thread)


async def test_two_live_sessions_each_run_their_own_swarm_and_a_repeat_joins_its_own(monkeypatch):
    """Session A's swarm still runs when this session starts the same task: this session gets its own swarm (its
    report and spoken summary belong here), and a repeat in session A still joins A's job."""
    session_a, thread = _session_loop()
    _stall_llm_on(monkeypatch, session_a)
    try:
        first = _start_on(session_a, TASK)
        here = swarm_manager.start_swarm(TASK)
        assert here["job_id"] != first["job_id"] and here["message"] == swarm_manager.STARTED_MESSAGE
        assert _start_on(session_a, TASK) == {"status": "started", "job_id": first["job_id"],
                                              "message": swarm_manager.ALREADY_RUNNING_MESSAGE}
        assert (await _wait(here["job_id"]))["status"] == "done"
    finally:
        _discard_session_loop(session_a, thread)


@pytest.mark.parametrize("kind", ["pending on a running loop", "pending on a stopped loop", "done on a stopped loop",
                                  "done on a closed loop"])
async def test_status_lines_of_another_event_loop_never_delay_or_break_the_report(monkeypatch, chat, kind):
    """The report waits only for this loop's status lines. Waiting for another loop's task cost up to the full 5 s (a
    stopped loop never finishes it), and a done task of a closed loop made asyncio.wait raise ('Event loop is
    closed'), which ended the swarm as 'critical error' without its report. A done task leaves the set: its discard
    callback never runs when its loop stopped first."""
    _fake_llm(monkeypatch)
    loop, thread = _session_loop()
    pending = kind.startswith("pending")

    async def new_task() -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(asyncio.sleep(30 if pending else 0))
        if not pending:
            await task
        return task

    stranger = asyncio.run_coroutine_threadsafe(new_task(), loop).result(5)
    if not kind.endswith("running loop"):
        _end_session(loop, thread)
    if kind.endswith("closed loop"):
        loop.close()
    swarm_manager._status_tasks.add(stranger)
    try:
        report = await asyncio.wait_for(swarm_manager.SwarmCoordinator().execute_swarm(TASK), 3.0)
        assert report == REPORT and chat[-1]["text"] == REPORT
        assert (stranger in swarm_manager._status_tasks) is pending
    finally:
        swarm_manager._status_tasks.discard(stranger)
        _discard_session_loop(loop, thread)


async def test_other_threads_changing_the_status_set_never_break_the_flush():
    """Other sessions' threads add and drop their status tasks while this loop flushes, so the flush walks a snapshot:
    walking the live set raised 'Set changed size during iteration', which ended a merged swarm as 'critical error'.
    A probe: the thread switch interval is cut to 1 microsecond, so the two threads interleave all the time."""
    loop, thread = _session_loop()

    async def new_tasks() -> list[asyncio.Task[Any]]:
        return [asyncio.get_running_loop().create_task(asyncio.sleep(30)) for _ in range(12)]

    strangers = asyncio.run_coroutine_threadsafe(new_tasks(), loop).result(5)
    resident, churned = strangers[:4], strangers[4:]
    swarm_manager._status_tasks.update(resident)
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            for task in churned:
                swarm_manager._status_tasks.add(task)
            for task in churned:
                swarm_manager._status_tasks.discard(task)

    churner = threading.Thread(target=churn, daemon=True)
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    churner.start()
    try:
        async with asyncio.timeout(10):
            for _ in range(2000):
                await swarm_manager._flush_status_lines()
    finally:
        stop.set()
        churner.join(5)
        sys.setswitchinterval(interval)
        swarm_manager._status_tasks.difference_update(strangers)
        _discard_session_loop(loop, thread)
