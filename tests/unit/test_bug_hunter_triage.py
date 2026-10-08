"""Plan task C-82: grouped errors and LLM triage in tool_investigate_recent_bugs (services/utils/jarvis_bug_hunter.py).

The recent error records are grouped by signature (logger name, level and message with every digit run as '#', first
160 chars) with a count and the first and last time seen. The tool lists the 5 most frequent and asks llm_text (K-30,
a fast Gemini id first) once for a summary and a suggested fix of the top 2, each group in an equal share of the
prompt, so a long traceback line in one never pushes the other out. analyze_error makes at most one LLM call per
minute in a process (a fake clock here), masks its input with redact_secrets first (and before any cut), and without
an answer returns the grouped raw errors; the tool then reuses its last answer about the same groups, if it has one.
monitor_logs keeps its posts and never calls the LLM.

No network: llm_text.generate_text is a fake, and every log file lives in tmp_path.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import json
import re
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.ai_core.llm_text import DEFAULT_TEXT_MODEL, GEMINI_FALLBACK_MODELS, LLMResult, LLMUnavailableError
from services.utils import jarvis_bug_hunter as hunter

CLIPBOARD = ("Clipboard loop runtime error: Error calling OpenClipboard ([WinError 0] The operation completed "
             "successfully.)")
TRACEBACK = ["Traceback (most recent call last):\n", '  File "x.py", line 1, in <module>\n',
             "    enroll(sample)\n", "    ^^^^^^^^^^^^^^\n", "KeyError: 'k'\n"]
ANSWER = ("1. Summary: Another process holds the clipboard open.\n"
          "   Fix: Retry OpenClipboard with a short backoff.\n"
          "2. Summary: The Voice ID model did not load.\n"
          "   Fix: Download the model files again.")


def _record(clock: str, name: str, level: str, message: str) -> str:
    return f"2026-10-04 {clock},101 - {name} - {level} - {message}\n"


def _clock(second: int) -> str:
    return f"10:{second // 60:02d}:{second % 60:02d}"


def _clipboard_burst(count: int = 50) -> list[str]:
    """count clipboard errors, 5 s apart from 10:00:00 (the 50th at 10:04:05)."""
    return [_record(_clock(5 * n), "JARVIS-CLIPBOARD", "ERROR", CLIPBOARD) for n in range(count)]


def _write(path: Path, *lines: str) -> None:
    """Written in text mode, as jarvis_logger writes the real log (CRLF on Windows)."""
    with path.open("w", encoding="utf-8") as f:
        f.write("".join(lines))


def _append(path: Path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(lines))


def _block(report: str) -> list[str]:
    """The report's grouped lines (its one code block)."""
    return report.split("```\n", 1)[1].split("\n```", 1)[0].split("\n")


def _triage_part(report: str) -> str:
    """What follows the code block, without the closing line."""
    return report.split("\n```\n\n", 1)[1].rsplit("\n\nMain inhein", 1)[0]


def _prompt_errors(prompt: str) -> str:
    """The error text between <log> and </log> in a triage prompt."""
    return prompt.split("<log>\n", 1)[1].split("\n</log>", 1)[0]


class FakeLLM:
    """llm_text.generate_text as the hunter calls it: records each call and answers ANSWER as Groq, or raises
    error, or (hang set) sets that event and never answers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = ANSWER
        self.error: BaseException | None = None
        self.hang: asyncio.Event | None = None

    async def __call__(self, prompt: str, **kwargs: Any) -> LLMResult:
        self.calls.append({"prompt": prompt, **kwargs})
        if self.error is not None:
            raise self.error
        if self.hang is not None:
            self.hang.set()
            await asyncio.Event().wait()
        return LLMResult(self.answer, "groq", "openai/gpt-oss-120b")


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """The triage rate limit on a fake clock, with no call made and no answer kept yet."""
    now = [1000.0]
    monkeypatch.setattr(hunter, "_triage_limit", hunter.TriageRateLimit(clock=lambda: now[0]))
    monkeypatch.setattr(hunter, "_last_triage", {})
    return now


@pytest.fixture
def llm(monkeypatch, clock) -> FakeLLM:  # pylint: disable=unused-argument  # clock: a fresh rate limit
    fake = FakeLLM()
    monkeypatch.setattr(hunter, "generate_text", fake)
    return fake


@pytest.fixture
def log(monkeypatch, tmp_path) -> Path:
    """LOG_FILE (config.log_file, logs/jarvis_hybrid_local.log on the PC) in tmp_path."""
    path = tmp_path / "jarvis_hybrid_local.log"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(hunter, "LOG_FILE", path)
    return path


# ---------------------------------------------------------------- grouping


def test_fifty_identical_clipboard_errors_are_one_group_of_fifty(log):
    burst = _clipboard_burst()
    _write(log, _record("09:59:59", "AGENT-RUNNER", "INFO", "Connected; 0 ERROR so far"),
           _record("10:00:01", "JARVIS-LOOPS", "CRITICAL", "SYSTEM OVERLOAD: Throttling high-load services..."),
           *burst[:25], _record("10:02:01", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK, *burst[25:])
    groups, exists, has_text = hunter.recent_error_groups()
    assert exists and has_text
    assert [(group.signature, group.count) for group in groups] == [
        ("JARVIS-CLIPBOARD - ERROR - Clipboard loop runtime error: Error calling OpenClipboard ([WinError #] The "
         "operation completed successfully.)", 50),
        ("VOICE-ID - ERROR - Lazy enrollment failed.", 1)]
    assert (groups[0].first_seen, groups[0].last_seen) == ("2026-10-04 10:00:00", "2026-10-04 10:04:05")
    assert "KeyError: 'k'" in groups[1].latest  # the traceback block stays with its record


def test_a_signature_is_name_level_and_message_with_digits_as_hash_and_at_most_160_chars():
    message = "POST to 127.0.0.1:5001 failed after 3 tries (id 812)"
    signature = hunter.error_signature(_record("10:00:00", "JARVIS-BRIDGE", "ERROR", message))
    assert signature == "JARVIS-BRIDGE - ERROR - POST to #.#.#.#:# failed after # tries (id #)"
    assert hunter.error_signature(_record(
        "23:59:59", "JARVIS-BRIDGE", "ERROR", "POST to 10.0.0.12:443 failed after 12 tries (id 7)")) == signature
    assert hunter.error_signature(_record("10:00:00", "JARVIS-BRIDGE", "CRITICAL", message)) != signature
    assert hunter.error_signature(_record("10:00:00", "JARVIS-VISION", "ERROR", message)) != signature
    assert hunter.error_signature(_record("10:00:00", "WORKER-3", "ERROR", "x")) == hunter.error_signature(
        _record("10:00:00", "WORKER-12", "ERROR", "x"))
    long = hunter.error_signature(_record("10:00:00", "X", "ERROR", "y" * 500))
    assert len(long) == 160 and long == ("X - ERROR - " + "y" * 500)[:160]
    as_json = json.dumps({"timestamp": "2026-10-04T10:00:00", "level": "ERROR", "name": "JARVIS-BRIDGE",
                          "message": message}) + "\n"
    assert hunter.error_signature(as_json) == signature  # LOG_STRUCTURED records group the same way


def test_groups_rank_by_count_then_by_the_latest_record():
    names = ["A", "B", "C", "A", "B", "C", "C", "D"]
    lines = [_record(_clock(n), name, "ERROR", f"failure {n}") for n, name in enumerate(names)]
    groups = hunter.group_errors(hunter.error_entries(lines))
    assert [(group.signature.split(" - ")[0], group.count) for group in groups] == [
        ("C", 3), ("B", 2), ("A", 2), ("D", 1)]  # B's last record is newer than A's


# ---------------------------------------------------------------- the report


async def test_recent_bugs_lists_fifty_clipboard_errors_as_one_line_with_a_suggested_fix(log, llm):
    _write(log, *_clipboard_burst(), _record("10:05:00", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK)
    report = await hunter.tool_investigate_recent_bugs()
    assert report.startswith("Sir, maine recent logs analyze kiye hain. 51 errors 2 groups mein mile")
    assert _block(report) == [
        "1. 50x [2026-10-04 10:00:00 se 2026-10-04 10:04:05] JARVIS-CLIPBOARD - ERROR - " + CLIPBOARD,
        "2. 1x [2026-10-04 10:05:00] VOICE-ID - ERROR - Lazy enrollment failed. (KeyError: 'k')"]
    assert _triage_part(report).splitlines() == [
        "AI triage (top 2):", "1. Summary: Another process holds the clipboard open.",
        "Fix: Retry OpenClipboard with a short backoff.", "2. Summary: The Voice ID model did not load.",
        "Fix: Download the model files again."]
    assert len(llm.calls) == 1


async def test_the_top_five_are_listed_and_the_top_two_triaged_in_one_call(log, llm):
    lines: list[str] = []
    second = 0
    for count, name in zip(range(7, 0, -1), "ABCDEFG"):
        for n in range(count):
            lines.append(_record(_clock(second), f"MOD-{name}", "ERROR", f"failure {name.lower()} {n}"))
            second += 1
    _write(log, *lines)
    report = await hunter.tool_investigate_recent_bugs()
    assert report.startswith("Sir, maine recent logs analyze kiye hain. 28 errors 7 groups mein mile")
    assert [re.match(r"(\d)\. (\d+)x \[[^]]*\] (MOD-\w) ", line).groups() for line in _block(report)] == [
        ("1", "7", "MOD-A"), ("2", "6", "MOD-B"), ("3", "5", "MOD-C"), ("4", "4", "MOD-D"), ("5", "3", "MOD-E")]
    assert "MOD-F" not in report and "MOD-G" not in report
    assert "AI triage (top 2):" in report
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert "1. MOD-A - ERROR - failure a 6\n   count 7, first seen 2026-10-04 10:00:00, last seen 2026-10-04 " \
           "10:00:06" in call["prompt"]
    assert "2. MOD-B - ERROR - failure b 5" in call["prompt"] and "MOD-C" not in call["prompt"]
    assert call["max_output_tokens"] <= 300
    assert "untrusted" in call["system"] and "<log>" in call["prompt"]


async def test_the_prompt_carries_the_traceback_tail_and_json_records_group_too(log, llm):
    exception = "".join(TRACEBACK).rstrip("\n")
    records = [json.dumps({"timestamp": f"2026-10-04T10:00:0{n}", "level": "ERROR", "name": "VOICE-ID",
                           "message": f"Voice ID verification failed: attempt {n}", "exception": exception}) + "\n"
               for n in (1, 2, 3)]
    _write(log, *records)
    report = await hunter.tool_investigate_recent_bugs()
    assert _block(report) == ["1. 3x [2026-10-04 10:00:01 se 2026-10-04 10:00:03] VOICE-ID - ERROR - Voice ID "
                              "verification failed: attempt 3 (KeyError: 'k')"]
    prompt = llm.calls[0]["prompt"]
    assert '   File "x.py", line 1, in <module>\n       enroll(sample)\n   KeyError: \'k\'' in prompt
    assert "^^^" not in prompt  # Python's marker lines only cost tokens


async def test_a_long_exception_line_never_pushes_the_second_group_out_of_the_prompt(log, llm):
    # A google-genai ClientError ends its traceback with the JSON body of the answer (1-3 KB); 3.5 KB here.
    body = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                 "message": "Request contains an invalid argument. " + "x" * 3450}})
    client_error = ["Traceback (most recent call last):\n", '  File "swarm.py", line 9, in decompose\n',
                    "    response = await client.aio.models.generate_content(model=model, contents=prompt)\n",
                    f"google.genai.errors.ClientError: 400 INVALID_ARGUMENT. {body}\n"]
    assert len(client_error[-1]) > 3500
    _write(log, *[line for n in range(3) for line in (
        _record(_clock(n), "JARVIS-SWARM", "ERROR", f"Swarm decomposition failed (try {n + 1})"), *client_error)],
           _record("10:05:00", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK)
    report = await hunter.tool_investigate_recent_bugs()
    assert "AI triage (top 2):" in report
    errors = _prompt_errors(llm.calls[0]["prompt"])
    assert len(errors) <= hunter.TRIAGE_INPUT_CHARS
    assert errors.startswith("1. JARVIS-SWARM - ERROR - Swarm decomposition failed (try 3)\n   count 3, ")
    exception = [line for line in errors.splitlines() if "ClientError" in line]
    assert len(exception) == 1 and exception[0].startswith("   google.genai.errors.ClientError: 400 INVALID")
    assert len(exception[0]) <= 3 + hunter.TRACEBACK_LINE_CHARS and exception[0].endswith("...")  # cut, not dropped
    assert "\n2. VOICE-ID - ERROR - Lazy enrollment failed.\n   count 1, " in errors
    assert errors.endswith("\n   KeyError: 'k'")


async def test_each_group_keeps_an_equal_share_of_the_prompt_and_its_exception_line(log, llm):
    deep = "\\deep" * 60
    frames = [line for n in range(8) for line in (
        f'  File "C:\\JARVIS\\services{deep}\\step_{n}.py", line {n + 1}, in step_{n}\n',
        f"    step_{n + 1}(context, {'a' * 250})\n")]
    exception = "RecursionError: maximum recursion depth exceeded while calling a Python object " + "q" * 200
    traceback = ["Traceback (most recent call last):\n", *frames, exception + "\n"]
    _write(log, *[line for n in range(2) for line in (
        _record(_clock(n), "JARVIS-PLANNER", "ERROR", f"Plan {n} failed: " + "p" * 400), *traceback)],
           _record("10:05:00", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK)
    await hunter.tool_investigate_recent_bugs()
    first, second = _prompt_errors(llm.calls[0]["prompt"]).split("\n2. ", 1)
    assert len(first) <= (hunter.TRIAGE_INPUT_CHARS - 1) // 2  # half, less the newline between the two
    lines = first.splitlines()
    assert lines[0].startswith("1. JARVIS-PLANNER - ERROR - Plan 1 failed: ppp") and lines[0].endswith("...")
    assert all(len(line) <= 3 + hunter.TRACEBACK_LINE_CHARS for line in lines[2:])
    assert 2 < len(lines) < 2 + hunter.TRACEBACK_TAIL_LINES  # the oldest of the six tail lines went first...
    assert lines[-1].startswith("   RecursionError: maximum recursion depth exceeded")  # ...the exception stays
    assert second.startswith("VOICE-ID - ERROR - Lazy enrollment failed.\n   count 1, ")
    assert second.endswith("\n   KeyError: 'k'")


# ---------------------------------------------------------------- at most one LLM call a minute


def test_the_rate_limit_allows_one_call_per_minute():
    now = [0.0]
    limit = hunter.TriageRateLimit(clock=lambda: now[0])
    assert limit.acquire() is True
    now[0] = 59.999
    assert limit.acquire() is False
    now[0] = 60.0
    assert limit.acquire() is True
    assert limit.acquire() is False


async def test_analyze_error_makes_at_most_one_llm_call_a_minute(llm, clock):
    bug_hunter = hunter.BugHunter()
    assert await bug_hunter.analyze_error("1. X - ERROR - boom") == ANSWER
    clock[0] += 59.0
    assert await bug_hunter.analyze_error("1. Y - ERROR - other") == "1. Y - ERROR - other"  # the raw error
    assert len(llm.calls) == 1
    clock[0] += 1.0  # a minute after the call
    assert await bug_hunter.analyze_error("1. Y - ERROR - other") == ANSWER
    assert len(llm.calls) == 2


async def test_a_failed_call_counts_against_the_minute_too(llm, clock):
    llm.error = LLMUnavailableError(["gemini: 429", "groq: 503", "openrouter: timeout after 6s"])
    bug_hunter = hunter.BugHunter()
    assert await bug_hunter.analyze_error("1. X - ERROR - boom") == "1. X - ERROR - boom"
    llm.error = None
    clock[0] += 30.0
    assert await bug_hunter.analyze_error("1. X - ERROR - boom") == "1. X - ERROR - boom"
    assert len(llm.calls) == 1  # the providers are not asked again within the minute


async def test_requests_within_a_minute_make_one_call_and_reuse_its_triage(log, llm, clock):
    _write(log, *_clipboard_burst())
    first = await hunter.tool_investigate_recent_bugs()
    clock[0] += 20.0
    second = await hunter.tool_investigate_recent_bugs()
    assert len(llm.calls) == 1
    assert _triage_part(first).startswith("AI triage (top 1):") and _triage_part(second) == _triage_part(first)

    # Another error leads within the same minute: no second call; its raw details stand instead.
    _append(log, *[_record(f"10:06:{n:02d}", "JARVIS-VISION", "ERROR", f"Vision API error: {n} NOT_FOUND")
                   for n in range(55)])
    third = await hunter.tool_investigate_recent_bugs()
    assert len(llm.calls) == 1
    assert _block(third)[0].startswith("1. 55x [2026-10-04 10:06:00 se 2026-10-04 10:06:54] JARVIS-VISION")
    assert _triage_part(third).splitlines()[:3] == [
        "AI triage ek minute mein ek hi dafa hota hai, is liye abhi nahi hua. Top 2 ki details:",
        "1. JARVIS-VISION - ERROR - Vision API error: 54 NOT_FOUND",
        "count 55, first seen 2026-10-04 10:06:00, last seen 2026-10-04 10:06:54"]

    clock[0] += 40.0  # a minute after the first call
    fourth = await hunter.tool_investigate_recent_bugs()
    assert len(llm.calls) == 2 and _triage_part(fourth).startswith("AI triage (top 2):")


# ---------------------------------------------------------------- masking


async def test_the_llm_gets_the_errors_masked(log, llm, monkeypatch):
    secret = "test-only-google-value-0123456789"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    _write(log,
           _record("10:00:00", "JARVIS-VISION", "ERROR", f"GET https://vision.example/v1?key={secret} failed: 403"),
           _record("10:00:01", "JARVIS-SEARCH", "ERROR",
                   "Search failed for https://search.example/q?q=x&api_key=plain-query-value-42 (timeout)"),
           "Traceback (most recent call last):\n",
           r'  File "C:\Users\someone\Desktop\JARVIS-AGENT-main\services\x.py", line 3, in run' + "\n",
           "RuntimeError: header Authorization: Bearer abcdef0123456789xyz refused\n")
    report = await hunter.tool_investigate_recent_bugs()
    prompt = llm.calls[0]["prompt"]
    for value in (secret, "plain-query-value-42", "abcdef0123456789xyz", "someone"):
        assert value not in prompt
    assert "?key=***" in prompt and "api_key=***" in prompt and "Bearer ***" in prompt
    assert r"C:\Users\<user>\Desktop\JARVIS-AGENT-main\services\x.py" in prompt
    assert secret not in report and "plain-query-value-42" not in report


async def test_a_cut_never_leaves_part_of_a_secret(log, llm, monkeypatch):
    secret = "test-only-google-value-0123456789"
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    # The secret straddles each cut: the message's (MESSAGE_CHARS) and the exception line's (TRACEBACK_LINE_CHARS in
    # the prompt, EXCEPTION_CHARS in the report line). Cut first, its first 6 chars would stay unmasked.
    message = "m" * (hunter.MESSAGE_CHARS - 10) + f" {secret} rejected"
    exception = "ValueError: " + "v" * (hunter.TRACEBACK_LINE_CHARS - 22) + f" {secret} " + "w" * 100
    _write(log, _record("10:00:00", "JARVIS-VISION", "ERROR", message), "Traceback (most recent call last):\n",
           '  File "vision.py", line 4, in look\n', exception + "\n")
    report = await hunter.tool_investigate_recent_bugs()
    prompt = llm.calls[0]["prompt"]
    for text in (prompt, report):
        assert secret[:6] not in text and "*** re" in text
    assert "   ValueError: " + "v" * (hunter.TRACEBACK_LINE_CHARS - 22) + " *** w" in prompt


async def test_the_answer_is_masked_and_clipped(llm, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-openrouter-value-0123")
    llm.answer = "Summary: the key test-only-openrouter-value-0123 leaked. Fix: " + "x" * 3000
    triage = await hunter.BugHunter().triage("1. X - ERROR - boom")
    assert triage.provider == "groq" and triage.reason == ""
    assert "test-only-openrouter-value-0123" not in triage.text and "the key *** leaked" in triage.text
    assert len(triage.text) == hunter.TRIAGE_ANSWER_CHARS and triage.text.endswith("...")


# ---------------------------------------------------------------- without an answer


async def test_analyze_error_returns_the_grouped_raw_error_masked_when_every_provider_fails(llm, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-only-groq-value-0123456789")
    llm.error = LLMUnavailableError(["groq: 401"])
    text = "1. JARVIS - ERROR - token test-only-groq-value-0123456789 rejected\n   count 3, first seen a, last seen b"
    assert await hunter.BugHunter().analyze_error(text) == (
        "1. JARVIS - ERROR - token *** rejected\n   count 3, first seen a, last seen b")


async def test_without_an_llm_the_report_keeps_the_grouped_raw_errors(log, llm):
    llm.error = LLMUnavailableError(["gemini: quota-exhausted", "groq: GROQ_API_KEY not set", "openrouter: 429"])
    _write(log, *_clipboard_burst(), _record("10:05:00", "VOICE-ID", "ERROR", "Lazy enrollment failed."), *TRACEBACK)
    report = await hunter.tool_investigate_recent_bugs()
    assert len(llm.calls) == 1
    assert _block(report)[0].startswith("1. 50x [2026-10-04 10:00:00 se 2026-10-04 10:04:05] JARVIS-CLIPBOARD")
    part = _triage_part(report).splitlines()
    assert part[:3] == ["AI triage abhi nahi ho saka, koi LLM provider jawab nahi de raha. Top 2 ki details:",
                        "1. JARVIS-CLIPBOARD - ERROR - " + CLIPBOARD,
                        "count 50, first seen 2026-10-04 10:00:00, last seen 2026-10-04 10:04:05"]
    assert "2. VOICE-ID - ERROR - Lazy enrollment failed." in part and part[-1] == "KeyError: 'k'"
    assert "AI triage (top" not in report and "quota-exhausted" not in report  # the reasons stay in the log


async def test_a_failed_call_about_the_same_groups_keeps_the_last_answer(log, llm, clock):
    _write(log, *_clipboard_burst())
    first = await hunter.tool_investigate_recent_bugs()
    clock[0] += 120.0
    llm.error = LLMUnavailableError(["gemini: 503", "groq: 503", "openrouter: 503"])
    second = await hunter.tool_investigate_recent_bugs()
    assert len(llm.calls) == 2  # the minute was over, so the providers were asked again
    assert _triage_part(second) == _triage_part(first) and _triage_part(first).startswith("AI triage (top 1):")


async def test_the_triage_asks_a_fast_gemini_model_first(llm):
    await hunter.BugHunter().triage("1. X - ERROR - boom")
    call = llm.calls[0]
    # JARVIS_TEXT_MODEL took 7-17 s per text answer on 8 Oct, past TRIAGE_ATTEMPT_TIMEOUT_S: the triage names an id
    # K-30 knows (one of its fallbacks) and keeps every tier, so Groq and OpenRouter still follow it.
    assert call["model"] == hunter.TRIAGE_MODEL != DEFAULT_TEXT_MODEL and hunter.TRIAGE_MODEL in GEMINI_FALLBACK_MODELS
    assert "providers" not in call
    assert call["timeout"] == hunter.TRIAGE_ATTEMPT_TIMEOUT_S and call["max_output_tokens"] <= 300


async def test_a_slow_llm_is_given_up_within_the_tools_own_timeout(log, llm, monkeypatch):
    meta = plugin_manager.get_tool_meta("tool_investigate_recent_bugs")
    assert meta is not None and hunter.TRIAGE_ATTEMPT_TIMEOUT_S < hunter.TRIAGE_BUDGET_S < meta["timeout"]
    monkeypatch.setattr(hunter, "TRIAGE_BUDGET_S", 0.05)
    llm.hang = asyncio.Event()
    _write(log, *_clipboard_burst(3))
    report = await asyncio.wait_for(hunter.tool_investigate_recent_bugs(), 5)
    assert _triage_part(report).startswith("AI triage waqt par nahi aaya. Top 1 ki details:")
    assert llm.calls[0]["timeout"] == hunter.TRIAGE_ATTEMPT_TIMEOUT_S


async def test_a_cancel_during_the_triage_is_passed_on(llm):
    llm.hang = asyncio.Event()
    task = asyncio.ensure_future(hunter.BugHunter().analyze_error("1. X - ERROR - boom"))
    await asyncio.wait_for(llm.hang.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------- the background monitor is unchanged


async def test_the_monitor_posts_as_before_and_never_calls_the_llm(log, llm, monkeypatch):
    monkeypatch.setattr(hunter, "_cursor", None)
    monkeypatch.setattr(hunter, "_throttle", hunter.ErrorThrottle())
    steps = [lambda: _append(log, *_clipboard_burst(5))]

    async def fake_sleep(_delay: float) -> None:
        if not steps:
            raise asyncio.CancelledError
        steps.pop(0)()

    monkeypatch.setattr(hunter, "asyncio", types.SimpleNamespace(
        sleep=fake_sleep, to_thread=asyncio.to_thread, Lock=asyncio.Lock, CancelledError=asyncio.CancelledError))
    posts: list[str] = []
    with pytest.raises(asyncio.CancelledError):  # C-33: the hunter passes its cancel on
        await hunter.monitor_logs(AsyncMock(side_effect=posts.append))
    assert len(posts) == 1 and CLIPBOARD in posts[0]  # the repeats within 10 min are throttled (C-64)
    assert llm.calls == []
