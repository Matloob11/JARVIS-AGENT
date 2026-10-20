"""Plan task C-25: secret redaction for logs and tool outputs (contract K-29), swarm key out of URLs.

Every secret here is a fake value set with monkeypatch; the real ones are removed from the
environment while each test runs. No test touches the network, the UI bridge or the live agent.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.ai_core import swarm_manager
from services.utils import jarvis_logger, jarvis_security
from services.utils.jarvis_logger import (
    JSONFormatter,
    RedactingFormatter,
    UIBridgeHandler,
    UIMessageFormatter,
    redact_root_handlers,
    setup_logger,
)
from services.utils.jarvis_security import (
    REDACTED,
    SECRET_ENV_NAMES,
    VortexGuard,
    redact_secrets,
    register_runtime_secret,
)

ROOT = Path(__file__).resolve().parents[2]
FAKE_GOOGLE_KEY = "AQ.FAKE-google_key-0123456789"
FAKE_TAVILY_KEY = "tvly" + "-FAKE-0123456789abcdef"  # split so the pre-commit key pattern does not fire
GEMINI_ERROR_URL = f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={FAKE_GOOGLE_KEY}"


@pytest.fixture(autouse=True)
def _fake_secrets(monkeypatch):
    """Only fake secret values exist while a test runs; runtime secrets start empty."""
    for name in SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", FAKE_TAVILY_KEY)
    monkeypatch.setattr(jarvis_security, "_runtime_secrets", set())


@pytest.fixture(name="plugin_api")
def _plugin_api() -> SimpleNamespace:
    """Imported outside any event loop: module-level setup_logger() must not start a loop task."""
    from livekit.agents import llm  # pylint: disable=import-outside-toplevel
    from livekit.agents.llm import utils as llm_utils  # pylint: disable=import-outside-toplevel

    from services.ai_core import jarvis_plugin_manager  # pylint: disable=import-outside-toplevel
    from services.utils.jarvis_resilience import (  # pylint: disable=import-outside-toplevel
        resilience_manager,
    )
    return SimpleNamespace(llm=llm, llm_utils=llm_utils, pm=jarvis_plugin_manager,
                           resilience_manager=resilience_manager)


@pytest.fixture(name="agent_runner")
def _agent_runner() -> Any:
    from src.core import agent_runner  # pylint: disable=import-outside-toplevel
    return agent_runner


def _close(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def _record(msg: str, args: Any = None, exc_info: Any = None, **kwargs: Any) -> logging.LogRecord:
    return logging.LogRecord("JARVIS-TEST", logging.ERROR, __file__, 1, msg, args, exc_info, **kwargs)


def _exc_info(message: str) -> Any:
    try:
        raise RuntimeError(message)
    except RuntimeError:
        return sys.exc_info()


# ---------------------------------------------------------------- redact_secrets


def test_plain_message_masks_env_values():
    text = f"calling https://x.googleapis.com/v1/m?key={FAKE_GOOGLE_KEY}&cx=1 with {FAKE_TAVILY_KEY}"
    assert redact_secrets(text) == "calling https://x.googleapis.com/v1/m?key=***&cx=1 with ***"


def test_env_values_are_read_at_call_time(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_first_value_123")
    assert redact_secrets("a gsk_first_value_123") == "a ***"
    monkeypatch.setenv("GROQ_API_KEY", "gsk_second_value_456")
    assert redact_secrets("b gsk_second_value_456") == "b ***"


def test_values_shorter_than_8_chars_are_left_alone(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_1234")
    register_runtime_secret("tiny123")
    assert redact_secrets("hf_1234 and tiny123 stay") == "hf_1234 and tiny123 stay"


def test_longest_value_is_masked_first(monkeypatch):
    monkeypatch.setenv("AGENT_TOKEN", "abcdefgh")
    monkeypatch.setenv("SESSION_JWT_SECRET", "abcdefgh12345678")
    assert redact_secrets("x abcdefgh12345678 y abcdefgh z") == "x *** y *** z"


def test_url_encoded_form_of_a_value_is_masked(monkeypatch):
    monkeypatch.setenv("JARVIS_ENCRYPTION_KEY", "ab+cd/ef==ghij")
    assert redact_secrets("q=ab%2Bcd%2Fef%3D%3Dghij raw ab+cd/ef==ghij") == "q=*** raw ***"


def test_the_pc_fallback_livekit_secret_is_masked(monkeypatch):
    """C-127 review: LIVEKIT_PC_API_SECRET (the PC fallback's own LiveKit pair, in the PC root .env and the VPS .env,
    which ui-bridge reads) is a secret env name, so K-29 lists it and redact_secrets masks its value."""
    assert "LIVEKIT_PC_API_SECRET" in SECRET_ENV_NAMES and "LIVEKIT_PC_API_KEY" not in SECRET_ENV_NAMES
    monkeypatch.setenv("LIVEKIT_PC_API_SECRET", "fake-pc-livekit-secret-0123")
    assert redact_secrets("signed with fake-pc-livekit-secret-0123") == "signed with ***"


def test_every_contract_env_name_is_masked(monkeypatch):
    for i, name in enumerate(SECRET_ENV_NAMES):
        monkeypatch.setenv(name, f"fake-{name.lower()}-{i:02d}")
    text = " ".join(f"fake-{name.lower()}-{i:02d}" for i, name in enumerate(SECRET_ENV_NAMES))
    assert redact_secrets(text) == " ".join([REDACTED] * len(SECRET_ENV_NAMES))


@pytest.mark.parametrize("name", ["key", "api_key", "apikey", "appid", "token", "access_token",
                                  "sig", "KEY"])
def test_query_parameter_values_are_masked(name):
    assert redact_secrets(f"GET https://h.example/p?q=1&{name}=v4lu3-x_y&x=2") == \
        f"GET https://h.example/p?q=1&{name}=***&x=2"
    assert redact_secrets(f"https://h.example/p?{name}=v4lu3") == f"https://h.example/p?{name}=***"


def test_query_value_stops_at_the_quote_or_bracket_around_the_url():
    assert redact_secrets('<a href="https://m.example/x?a=1&amp;key=S3CR3T">') == \
        '<a href="https://m.example/x?a=1&amp;key=***">'
    assert redact_secrets("[map](https://maps.example/api?key=ZZZ999), next") == \
        "[map](https://maps.example/api?key=***), next"
    assert redact_secrets('{"url": "https://x.example/y?token=abc.def"}') == \
        '{"url": "https://x.example/y?token=***"}'


def test_code_and_prose_are_left_alone():
    text = "sorted(rows, key=len) and dict(token=value); monkey=1&keyword=2; the bearer of bad news"
    assert redact_secrets(text) == text


@pytest.mark.parametrize("text", [
    "Pakistan's flag bearer Muhammad led the team",
    "These are bearer securities and bearer instruments.",
    "standard bearer\nInternational relations",
    "Bearer abc1234 and Bearer abcdefghijklmnopqrs",  # under 8 chars / no digit and under 20
])
def test_prose_after_bearer_is_left_alone(text):
    # Search snippets reach the LLM through validate_tool_output, so ordinary words must survive.
    assert redact_secrets(text) == text


def test_bearer_token_is_masked():
    assert redact_secrets("Authorization: Bearer eyJhbGciOi.abc-DEF_123") == "Authorization: Bearer ***"
    assert redact_secrets("{'authorization': 'bearer sk-or-v1-0123456789abcdef'}") == \
        "{'authorization': 'bearer ***'}"
    assert redact_secrets("Bearer abc12345, Bearer abcdefghijklmnopqrstuvwx and BEARER dGVzdDp0ZXN0dA==") == \
        "Bearer ***, Bearer *** and BEARER ***"


def test_runtime_secret_is_masked():
    register_runtime_secret("file-server-token-XYZ123")
    register_runtime_secret(None)  # type: ignore[arg-type]  # ignored, never raises
    assert redact_secrets("http://192.168.1.5:8000/f/a.mp4?t=file-server-token-XYZ123") == \
        "http://192.168.1.5:8000/f/a.mp4?t=***"


def test_non_str_input_never_raises():
    class Unprintable:
        def __str__(self) -> str:
            raise RuntimeError("no str")

    assert redact_secrets(None) is None  # type: ignore[arg-type]
    assert redact_secrets(42) == "42"  # type: ignore[arg-type]
    assert redact_secrets(ValueError(f"400 for {GEMINI_ERROR_URL}")) == \
        "400 for https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key=***"
    assert redact_secrets(Unprintable()) == REDACTED  # type: ignore[arg-type]


# ---------------------------------------------------------------- tool output (K-28 order)


def test_tool_output_reaches_the_llm_masked():
    output = {"status": "success", "message": f"used {FAKE_GOOGLE_KEY}",
              "data": {"urls": [f"https://x.example/y?q=a&key={FAKE_GOOGLE_KEY}"]}}
    assert VortexGuard.validate_tool_output("search", output) == {
        "status": "success", "message": "used ***", "data": {"urls": ["https://x.example/y?q=a&key=***"]}}


def test_tool_output_is_filtered_then_masked_then_truncated():
    # Truncating first would leave a 10-char piece of the key that no longer matches.
    assert VortexGuard.validate_tool_output("t", "x" * 90 + FAKE_GOOGLE_KEY, max_chars=100) == \
        "x" * 90 + REDACTED
    assert VortexGuard.validate_tool_output("t", f"system prompt {FAKE_TAVILY_KEY}") == "[filtered] ***"


async def test_tool_result_reaches_the_llm_masked_through_the_wrapper(plugin_api, monkeypatch):
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())
    jarvis_tool, plugin_manager = plugin_api.pm.jarvis_tool, plugin_api.pm.plugin_manager
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        before = list(plugin_manager.tools)
    try:
        @jarvis_tool
        async def c25_leaky_search(query: str) -> dict:
            """Returns a URL that carries the key, as a careless HTTP tool would.

            Args:
                query: anything
            """
            return {"status": "success", "message": f"found https://x.example/s?q={query}&key={FAKE_GOOGLE_KEY}",
                    "raw": FAKE_GOOGLE_KEY}

        wrapper = next(t for t in plugin_manager.tools if t.__name__ == "c25_leaky_search")
        tool = plugin_api.llm.function_tool(wrapper)
        args, kwargs = plugin_api.llm_utils.prepare_function_arguments(
            fnc=tool, json_arguments=json.dumps({"query": "mausam"}))
        result = await tool(*args, **kwargs)
    finally:
        with plugin_manager._tools_lock:  # pylint: disable=protected-access
            plugin_manager.tools[:] = before
        plugin_api.resilience_manager._breakers.pop("c25_leaky_search", None)  # pylint: disable=protected-access

    assert FAKE_GOOGLE_KEY not in json.dumps(result)
    assert result["message"] == "found https://x.example/s?q=mausam&key=***"
    assert result["raw"] == REDACTED


async def test_tool_exception_reaches_the_llm_masked_through_the_wrapper(plugin_api, monkeypatch):
    # The wrapper's error dict skips validate_tool_output, so it must mask the exception text itself.
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())
    jarvis_tool, plugin_manager = plugin_api.pm.jarvis_tool, plugin_api.pm.plugin_manager
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        before = list(plugin_manager.tools)
    try:
        @jarvis_tool
        async def c25_failing_search(query: str) -> dict:
            """Fails with the request URL (and its key) in the exception text.

            Args:
                query: anything
            """
            raise RuntimeError(f"400 for https://x.example/s?q={query}&key={FAKE_GOOGLE_KEY}")

        wrapper = next(t for t in plugin_manager.tools if t.__name__ == "c25_failing_search")
        tool = plugin_api.llm.function_tool(wrapper)
        args, kwargs = plugin_api.llm_utils.prepare_function_arguments(
            fnc=tool, json_arguments=json.dumps({"query": "mausam"}))
        result = await tool(*args, **kwargs)
    finally:
        with plugin_manager._tools_lock:  # pylint: disable=protected-access
            plugin_manager.tools[:] = before
        plugin_api.resilience_manager._breakers.pop("c25_failing_search", None)  # pylint: disable=protected-access

    assert result["status"] == "error"
    assert FAKE_GOOGLE_KEY not in json.dumps(result)
    assert result["message"] == "400 for https://x.example/s?q=mausam&key=***"


# ---------------------------------------------------------------- logging


def test_logged_traceback_is_masked_in_file_and_console(tmp_path):
    log_file = tmp_path / "jarvis.log"
    logger = setup_logger("JARVIS-TEST-C25-TRACEBACK", log_file)
    console = next(h for h in logger.handlers if type(h) is logging.StreamHandler)  # pylint: disable=unidiomatic-typecheck
    stream = io.StringIO()
    console.setStream(stream)
    for handler in [h for h in logger.handlers if isinstance(h, UIBridgeHandler)]:
        logger.removeHandler(handler)
    try:
        try:
            raise httpx.HTTPError(f"Client error for url {GEMINI_ERROR_URL}")
        except httpx.HTTPError:
            logger.error("x", exc_info=True)
    finally:
        _close(logger)

    for written in (log_file.read_text(encoding="utf-8"), stream.getvalue()):
        assert "Traceback (most recent call last)" in written
        assert "generateContent?key=***" in written
        assert FAKE_GOOGLE_KEY not in written


def test_formatter_masks_message_args_and_stack_info():
    record = _record("using %s", (FAKE_TAVILY_KEY,),
                     sinfo=f"Stack (most recent call last):\n  token={FAKE_GOOGLE_KEY}")
    assert RedactingFormatter("%(levelname)s %(message)s").format(record) == \
        "ERROR using ***\nStack (most recent call last):\n  token=***"


def test_json_formatter_masks_message_and_traceback():
    record = _record("with %s", (FAKE_TAVILY_KEY,), _exc_info(f"bad ?key={FAKE_GOOGLE_KEY}"))
    data = json.loads(JSONFormatter().format(record))
    assert data["message"] == "with ***"
    assert "RuntimeError: bad ?key=***" in data["exception"]
    assert FAKE_GOOGLE_KEY not in data["exception"]


@pytest.mark.parametrize("structured", ["false", "true"])
def test_every_setup_logger_handler_uses_redacting_formatter(tmp_path, monkeypatch, structured):
    monkeypatch.setenv("LOG_STRUCTURED", structured)
    logger = setup_logger(f"JARVIS-TEST-C25-HANDLERS-{structured}", tmp_path / "h.log")
    try:
        handlers = logger.handlers
        assert any(type(h) is logging.StreamHandler for h in handlers)  # pylint: disable=unidiomatic-typecheck
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in handlers)  # the shared one (C-64)
        assert any(isinstance(h, UIBridgeHandler) for h in handlers)
        assert all(isinstance(h.formatter, RedactingFormatter) for h in logger.handlers)
        assert any(isinstance(h.formatter, JSONFormatter) for h in logger.handlers) is (structured == "true")
    finally:
        _close(logger)


def test_ui_bridge_handler_sends_only_the_masked_message():
    shipper = jarvis_logger.UiLogShipper()  # C-64: the handler only queues; the shipper's thread posts
    handler = UIBridgeHandler(shipper=shipper, min_level=logging.INFO)
    assert isinstance(handler.formatter, UIMessageFormatter)
    record = logging.LogRecord("JARVIS-SWARM", logging.INFO, __file__, 1, "Bearer %s for %s",
                               ("abcDEF123456xyz", FAKE_GOOGLE_KEY), _exc_info("boom"))
    handler.emit(record)
    [item] = shipper.snapshot()
    assert (item["text"], item["category"], item["level"]) == ("Bearer *** for ***", "SWARM", "INFO")
    assert FAKE_GOOGLE_KEY not in json.dumps(item) and "Traceback" not in item["text"]


def test_wrapping_formatter_keeps_layout_and_caches_a_masked_traceback():
    wrapper = RedactingFormatter(inner=logging.Formatter("%(levelname)s|%(message)s", datefmt="%H"))
    record = _record("k=%s", (FAKE_GOOGLE_KEY,), _exc_info(f"bad ?key={FAKE_GOOGLE_KEY}"))
    out = wrapper.format(record)
    cached = record.exc_text or ""
    assert wrapper.datefmt == "%H"
    assert out.startswith("ERROR|k=***\nTraceback")
    # Handlers that print record.exc_text themselves (LiveKit's console) reuse the masked text.
    assert "RuntimeError: bad ?key=***" in cached
    assert FAKE_GOOGLE_KEY not in out and FAKE_GOOGLE_KEY not in cached


def test_redact_root_handlers_wraps_every_root_handler_once():
    root = logging.getLogger()
    plain = logging.StreamHandler(io.StringIO())
    plain.setFormatter(logging.Formatter("%(levelname)s|%(message)s"))
    bare = logging.StreamHandler(io.StringIO())
    saved = [(h, h.formatter) for h in [*root.handlers, logging.lastResort] if h is not None]
    root.addHandler(plain)
    root.addHandler(bare)
    try:
        redact_root_handlers()
        first = plain.formatter
        redact_root_handlers()
        assert plain.formatter is first  # no double wrapping
        assert all(isinstance(h.formatter, RedactingFormatter) for h in [*root.handlers, logging.lastResort])
        record = _record("k=%s", (FAKE_GOOGLE_KEY,))
        assert plain.format(record) == "ERROR|k=***"
        assert bare.format(record) == "k=***"
    finally:
        root.removeHandler(plain)
        root.removeHandler(bare)
        for handler, formatter in saved:
            handler.setFormatter(formatter)


# ---------------------------------------------------------------- swarm (no real Gemini call)


async def test_swarm_calls_go_through_llm_text(monkeypatch):
    """C-43: the swarm no longer builds its own Gemini URL (its gemini-2.5-flash 404ed); K-30 llm_text picks a live
    model and keeps the key out of URLs."""
    from services.ai_core.llm_text import LLMResult

    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_generate_text(prompt: str, **kwargs: Any) -> LLMResult:
        calls.append((prompt, kwargs))
        return LLMResult("ok", "gemini", "gemini-3.5-flash")

    monkeypatch.setattr(swarm_manager, "generate_text", fake_generate_text)
    assert await swarm_manager._call_gemini("hi") == "ok"  # pylint: disable=protected-access
    assert calls == [("hi", {"max_output_tokens": 2048, "temperature": 0.7})]
    assert not hasattr(swarm_manager, "_GEMINI_URL") and not hasattr(swarm_manager, "httpx")


async def test_swarm_llm_error_text_carries_no_key(monkeypatch):
    from services.ai_core.llm_text import LLMUnavailableError

    async def failing(_prompt: str, **_kwargs: Any) -> Any:
        raise LLMUnavailableError(["gemini gemini-3.5-flash: 404 NOT_FOUND", "groq: 429"])

    monkeypatch.setattr(swarm_manager, "generate_text", failing)
    with pytest.raises(LLMUnavailableError) as caught:
        await swarm_manager._call_gemini("hi")  # pylint: disable=protected-access
    assert "key=" not in str(caught.value) and FAKE_GOOGLE_KEY not in str(caught.value)


async def test_failing_swarm_logs_and_returns_only_masked_errors(monkeypatch):
    async def failing_call(_prompt: str) -> str:
        raise httpx.ConnectError(f"connect failed for {GEMINI_ERROR_URL}")

    monkeypatch.setattr(swarm_manager, "_call_gemini", failing_call)
    monkeypatch.setattr(swarm_manager, "notify_transcription", AsyncMock())
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())
    raw = io.StringIO()
    capture = logging.StreamHandler(raw)  # not redacting: shows what the code itself passes in
    swarm_manager.logger.addHandler(capture)
    try:
        coordinator = swarm_manager.SwarmCoordinator()
        sub_tasks = await coordinator._decompose_task("do things")  # pylint: disable=protected-access
        result = await coordinator._run_sub_agent(sub_tasks[0])  # pylint: disable=protected-access
        report = await coordinator._aggregate_results("do things", [result])  # pylint: disable=protected-access
        await asyncio.sleep(0)
    finally:
        swarm_manager.logger.removeHandler(capture)

    masked = "connect failed for https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key=***"
    assert result["result"] == f"Execution failed: {masked}"
    assert masked in report
    logged = raw.getvalue()
    assert logged.count(masked) == 3
    assert FAKE_GOOGLE_KEY not in logged + report


# ---------------------------------------------------------------- agent_runner


def test_agent_runner_import_masks_root_handlers_and_quiets_httpx():
    """
    A fresh interpreter, because importing agent_runner reconfigures the root logger. It then runs
    LiveKit's real _configure_logger the way 'start' does when the worker starts (a JSON handler on
    stdout), long before any job, and logs a fake key through a third-party logger.
    """
    probe = (
        "import io, json, logging, os, sys\n"
        "import src.core.agent_runner\n"
        "from livekit.agents.cli import cli as livekit_cli\n"
        "from services.utils.jarvis_security import SECRET_ENV_NAMES\n"
        "def show(tag):\n"
        "    root = logging.getLogger()\n"
        "    print(tag, len(root.handlers),"
        " sorted({type(h.formatter).__name__ for h in [*root.handlers, logging.lastResort]}))\n"
        "show('ROOT')\n"
        # Their own level: root sits at ERROR here, and the CLI later sets only unset levels.
        "print('LEVELS', logging.getLogger('httpx').level >= logging.WARNING,"
        " logging.getLogger('httpcore').level >= logging.WARNING)\n"
        "for name in SECRET_ENV_NAMES:\n"
        "    os.environ.pop(name, None)\n"
        f"os.environ['GOOGLE_API_KEY'] = {FAKE_GOOGLE_KEY!r}\n"
        "real_stdout, sys.stdout = sys.stdout, io.StringIO()\n"
        "try:\n"
        "    livekit_cli._configure_logger(None, 'INFO')\n"
        "    try:\n"
        "        raise RuntimeError('400 for ?key=' + os.environ['GOOGLE_API_KEY'])\n"
        "    except RuntimeError:\n"
        "        logging.getLogger('google_genai.probe').error("
        "'used %s', os.environ['GOOGLE_API_KEY'], exc_info=True)\n"
        "    written = sys.stdout.getvalue()\n"
        "finally:\n"
        "    sys.stdout = real_stdout\n"
        "show('CLI')\n"
        "line = next(json.loads(text) for text in written.splitlines() if 'google_genai.probe' in text)\n"
        "print('JSON', line['message'], 'RuntimeError: 400 for ?key=***' in line['exc_info'],"
        " os.environ['GOOGLE_API_KEY'] in written)\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=240, check=False)
    lines = [line for line in result.stdout.splitlines()
             if line.startswith(("ROOT ", "LEVELS ", "CLI ", "JSON "))]
    assert lines == ["ROOT 1 ['RedactingFormatter']", "LEVELS True True",
                     "CLI 2 ['RedactingFormatter']", "JSON used *** True False"], \
        redact_secrets(result.stderr[-1500:])


def test_cli_logger_hook_is_installed_once(agent_runner):
    from livekit.agents.cli import cli as livekit_cli  # pylint: disable=import-outside-toplevel

    hooked = livekit_cli._configure_logger  # pylint: disable=protected-access
    assert getattr(hooked, "masks_secrets", False)
    agent_runner._mask_cli_log_handler()  # pylint: disable=protected-access
    # Running the module body again (runpy in test_agent_lock) must not wrap it twice.
    assert livekit_cli._configure_logger is hooked  # pylint: disable=protected-access


async def test_entrypoint_masks_root_handlers_before_connecting(agent_runner, monkeypatch):
    order: list[str] = []

    async def connect() -> None:
        order.append("connect")
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_runner, "redact_root_handlers", lambda: order.append("redact"))
    monkeypatch.setattr(agent_runner, "_cleanup_session_resources", AsyncMock())
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())
    ctx = MagicMock()
    ctx.connect = connect
    await agent_runner.entrypoint(ctx)
    assert order == ["redact", "connect"]
