"""Plan task C-45: llm_text, the shared Gemini -> Groq -> OpenRouter helper (contract K-30).

Every provider is mocked: the client factories return fakes, the real google-genai and openai SDKs
talk to an httpx.MockTransport, or they reach a server on 127.0.0.1. Keys are fake values set with
monkeypatch (the real ones are removed while a test runs). No test touches the internet, the UI
bridge or the live agent.
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import ssl
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from aiohttp import web
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from openai.types.chat import ChatCompletion
from PIL import Image

from services.ai_core import llm_text
from services.ai_core.llm_text import (
    LLMResult,
    LLMUnavailableError,
    generate_text,
    generate_vision,
    list_gemini_models,
)
from services.utils import jarvis_logger, jarvis_security
from services.utils.jarvis_security import SECRET_ENV_NAMES

ROOT = Path(__file__).resolve().parents[2]
# Shaped so the pre-commit key patterns never fire; 8+ chars, so redact_secrets() masks them.
FAKE_GOOGLE_KEY = "AQ.FAKE-google_key-0123456789"
FAKE_GROQ_KEY = "gsk-FAKE-groq_key-0123456789"
FAKE_OPENROUTER_KEY = "or-FAKE-openrouter_key-0123456789"
FAKE_KEYS = (FAKE_GOOGLE_KEY, FAKE_GROQ_KEY, FAKE_OPENROUTER_KEY)
MODEL_ENV_NAMES = ("JARVIS_TEXT_MODEL", "JARVIS_VISION_MODEL", "JARVIS_GROQ_TEXT_MODEL",
                   "JARVIS_GROQ_VISION_MODEL", "JARVIS_OPENROUTER_MODEL")
GEMINI_TEXT_ORDER = ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite",
                     "gemini-2.5-flash"]
GEMINI_VISION_ORDER = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite",
                       "gemini-2.5-flash"]
GROQ = llm_text.GROQ_BASE_URL
OPENROUTER = llm_text.OPENROUTER_BASE_URL
REAL_GEMINI_FACTORY = llm_text._gemini_client
REAL_OPENAI_FACTORY = llm_text._openai_client


class UnexpectedClient(BaseException):
    """A BaseException, so the tiers' 'except Exception' cannot turn it into a mere reason."""


def _unexpected(what: str) -> Callable[..., Any]:
    def factory(*_args: Any, **_kwargs: Any) -> Any:
        raise UnexpectedClient(f"{what} client built without a fake")
    return factory


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Only fake keys, no model overrides, no dead models, no bridge calls, no real clients."""
    for name in (*SECRET_ENV_NAMES, *MODEL_ENV_NAMES, "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_GOOGLE_KEY)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
    monkeypatch.setattr(llm_text, "_dead_models", set())
    monkeypatch.setattr(jarvis_security, "_runtime_secrets", set())
    monkeypatch.setattr(jarvis_logger, "_BRIDGE_NOTIFY_LOG", AsyncMock())
    monkeypatch.setattr(llm_text, "_gemini_client", _unexpected("gemini"))
    monkeypatch.setattr(llm_text, "_openai_client", _unexpected("openai"))


@pytest.fixture(name="raw_log")
def _raw_log() -> Iterator[io.StringIO]:
    """What llm_text itself hands its logger, before any redacting formatter runs."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    llm_text.logger.addHandler(handler)
    try:
        yield stream
    finally:
        llm_text.logger.removeHandler(handler)


# ---------------------------------------------------------------- fakes


def _gemini_response(text: str | None, finish: str = "STOP") -> genai_types.GenerateContentResponse:
    parts = [] if text is None else [genai_types.Part(text=text)]
    return genai_types.GenerateContentResponse(candidates=[genai_types.Candidate(
        content=genai_types.Content(role="model", parts=parts), finish_reason=finish)])


def _gemini_error(code: int, status: str, message: str = "boom") -> genai_errors.APIError:
    body = {"error": {"code": code, "message": message, "status": status}}
    return (genai_errors.ServerError if code >= 500 else genai_errors.ClientError)(code, body)


def _completion(text: str | None) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
    })


def _openai_error(cls: type[openai.APIStatusError], status: int,
                  message: str = "boom") -> openai.APIStatusError:
    body = {"error": {"message": message}}
    request = httpx.Request("POST", "https://api.example.invalid/v1/chat/completions")
    return cls(f"Error code: {status} - {body}", response=httpx.Response(status, request=request,
                                                                        json=body), body=body)


def _image_bytes(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeGemini:
    """genai.Client stand-in. outcomes maps a model id to a response, an exception or an async
    callable; an id without an outcome answers 404 like a retired model."""

    def __init__(self, outcomes: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.closed: list[str] = []
        self.listing: list[genai_types.Model] | BaseException = []
        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=self._generate, list=self._list),
            aclose=self._aclose)

    def factory(self, api_key: str, timeout: float, tls: Any) -> FakeGemini:
        self.created.append({"api_key": api_key, "timeout": timeout, "tls": tls})
        return self

    @property
    def models_called(self) -> list[str]:
        return [call["model"] for call in self.calls]

    async def _generate(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.get(model, _gemini_error(404, "NOT_FOUND", f"models/{model} is not found"))
        if callable(outcome):
            outcome = await outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def _list(self, *, config: Any) -> Any:
        assert config == {"page_size": 100}
        if isinstance(self.listing, BaseException):
            raise self.listing

        async def pages() -> Any:
            for model in self.listing:
                yield model
        return pages()

    async def _aclose(self) -> None:
        self.closed.append("aio")

    def close(self) -> None:
        self.closed.append("sync")


class FakeChat:
    """AsyncOpenAI stand-in for one base URL: outcome is a completion, an exception, a str (what
    the SDK returns for a non-JSON body) or an async callable."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcome
        if callable(outcome):
            outcome = await outcome()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(name="providers")
def _providers(monkeypatch) -> Callable[..., SimpleNamespace]:
    """Installs fake Gemini, Groq and OpenRouter clients; an unscripted tier fails the test."""
    def install(gemini: dict[str, Any] | None = None, groq: Any = None,
                openrouter: Any = None) -> SimpleNamespace:
        fake_gemini = FakeGemini(gemini or {})
        chats = {GROQ: FakeChat(groq or UnexpectedClient("groq was called")),
                 OPENROUTER: FakeChat(openrouter or UnexpectedClient("openrouter was called"))}
        created: list[dict[str, Any]] = []

        def openai_factory(base_url: str, api_key: str, timeout: float, tls: Any,
                           headers: dict[str, str] | None = None) -> FakeChat:
            assert isinstance(tls, ssl.SSLContext)
            created.append({"base_url": base_url, "api_key": api_key, "timeout": timeout,
                            "headers": headers})
            return chats[base_url]

        monkeypatch.setattr(llm_text, "_gemini_client", fake_gemini.factory)
        monkeypatch.setattr(llm_text, "_openai_client", openai_factory)
        return SimpleNamespace(gemini=fake_gemini, groq=chats[GROQ], openrouter=chats[OPENROUTER],
                               chat_created=created)
    return install


# ---------------------------------------------------------------- tier 1: Gemini


def test_gemini_candidates_follow_the_contract_order(monkeypatch):
    assert llm_text._gemini_candidates(False, None) == GEMINI_TEXT_ORDER
    assert llm_text._gemini_candidates(True, None) == GEMINI_VISION_ORDER
    assert llm_text._gemini_candidates(False, " models/gemini-9-pro ") == ["gemini-9-pro",
                                                                            *GEMINI_VISION_ORDER]
    monkeypatch.setenv("JARVIS_TEXT_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("JARVIS_VISION_MODEL", "gemini-x-vision")
    assert llm_text._gemini_candidates(False, None) == [
        "gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
    assert llm_text._gemini_candidates(True, None) == ["gemini-x-vision", *GEMINI_VISION_ORDER]
    llm_text._dead_models.update({"gemini-x-vision", "gemini-3.8-flash"})
    assert llm_text._gemini_candidates(True, None) == [
        "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"]


async def test_text_answer_comes_from_the_first_gemini_candidate(providers, raw_log):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_response("  PRIVATE-ANSWER  ")})
    result = await generate_text("PRIVATE-PROMPT", system="be brief")
    assert result == LLMResult(text="PRIVATE-ANSWER", provider="gemini", model="gemini-3.5-flash")
    created = fakes.gemini.created
    assert [(c["api_key"], c["timeout"]) for c in created] == [(FAKE_GOOGLE_KEY, 60.0)]
    assert isinstance(created[0]["tls"], ssl.SSLContext)
    call = fakes.gemini.calls[0]
    assert call["contents"] == "PRIVATE-PROMPT"
    config = call["config"]
    assert config.system_instruction == "be brief"
    assert config.max_output_tokens == 2048
    assert config.temperature is None  # Google: no sampling parameters on Gemini 3.x
    assert config.automatic_function_calling.disable is True
    assert fakes.gemini.closed == ["aio", "sync"]
    logged = raw_log.getvalue()
    assert "llm_text text answered by gemini (gemini-3.5-flash)" in logged
    assert "PRIVATE-PROMPT" not in logged and "PRIVATE-ANSWER" not in logged


async def test_model_argument_goes_first_and_temperature_reaches_gemini_2(providers):
    fakes = providers(gemini={"gemini-2.5-flash": _gemini_response("OK")})
    result = await generate_text("hi", model="models/gemini-2.5-flash", temperature=0.1)
    assert result == LLMResult("OK", "gemini", "gemini-2.5-flash")
    assert fakes.gemini.models_called == ["gemini-2.5-flash"]
    assert fakes.gemini.calls[0]["config"].temperature == 0.1


async def test_404_marks_the_model_dead_and_tries_the_next_candidate(providers, raw_log):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_error(404, "NOT_FOUND"),
                              "gemini-3.8-flash": _gemini_response("OK")})
    assert await generate_text("hi") == LLMResult("OK", "gemini", "gemini-3.8-flash")
    assert llm_text._dead_models == {"gemini-3.5-flash"}
    assert await generate_text("hi again") == LLMResult("OK", "gemini", "gemini-3.8-flash")
    # The dead id is not asked again; each call closes its own client.
    assert fakes.gemini.models_called == ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.8-flash"]
    assert fakes.gemini.closed == ["aio", "sync", "aio", "sync"]
    assert "gemini[gemini-3.5-flash]: ClientError: 404 NOT_FOUND" in raw_log.getvalue()


async def test_gemini_tier_is_skipped_once_every_candidate_is_dead(providers, raw_log):
    fakes = providers(gemini={}, groq=_completion("OK"))
    assert (await generate_text("hi")).provider == "groq"
    assert fakes.gemini.models_called == GEMINI_TEXT_ORDER
    assert llm_text._dead_models == set(GEMINI_TEXT_ORDER)
    assert (await generate_text("hi")).provider == "groq"
    assert len(fakes.gemini.created) == 1  # nothing left to try: no client the second time
    assert "gemini: every candidate model answered 404 earlier" in raw_log.getvalue()


@pytest.mark.parametrize("error", [
    _gemini_error(429, "RESOURCE_EXHAUSTED", "quota exceeded"),
    _gemini_error(503, "UNAVAILABLE", "overloaded"),
    _gemini_error(500, "INTERNAL", "internal"),
    _gemini_error(403, "PERMISSION_DENIED", "denied"),
    httpx.ConnectError("connection refused"),
], ids=["429", "503", "500", "403", "connect"])
async def test_other_gemini_errors_move_to_groq_without_more_gemini_models(providers, error):
    fakes = providers(gemini={"gemini-3.5-flash": error}, groq=_completion("OK"))
    assert await generate_text("hi") == LLMResult("OK", "groq", "openai/gpt-oss-120b")
    assert fakes.gemini.models_called == ["gemini-3.5-flash"]
    assert not llm_text._dead_models
    assert fakes.gemini.closed == ["aio", "sync"]


async def test_gemini_timeout_moves_to_groq(providers, raw_log):
    async def hang() -> None:
        await asyncio.sleep(30)

    fakes = providers(gemini={"gemini-3.5-flash": hang}, groq=_completion("OK"))
    assert (await generate_text("hi", timeout=0.05)).provider == "groq"
    assert fakes.gemini.models_called == ["gemini-3.5-flash"]
    assert "gemini[gemini-3.5-flash]: timeout after 0.05s" in raw_log.getvalue()
    assert fakes.chat_created[0]["timeout"] == 0.05


async def test_empty_gemini_answer_moves_to_groq_with_the_finish_reason(providers, raw_log):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_response(None, finish="MAX_TOKENS")},
                      groq=_completion("OK"))
    assert (await generate_text("hi")).provider == "groq"
    assert fakes.gemini.models_called == ["gemini-3.5-flash"]
    assert "gemini[gemini-3.5-flash]: empty response (MAX_TOKENS)" in raw_log.getvalue()


# ---------------------------------------------------------------- tiers 2 and 3


async def test_groq_text_uses_gpt_oss_with_low_reasoning(providers):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_error(503, "UNAVAILABLE")},
                      groq=_completion("OK"))
    result = await generate_text("hi", system="be brief", max_output_tokens=300,
                                 temperature=0.2, timeout=12)
    assert result == LLMResult("OK", "groq", "openai/gpt-oss-120b")
    assert fakes.groq.calls == [{
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        "temperature": 0.2, "max_completion_tokens": 300, "reasoning_effort": "low",
    }]
    assert fakes.chat_created == [{"base_url": GROQ, "api_key": FAKE_GROQ_KEY, "timeout": 12.0,
                                   "headers": None}]
    assert fakes.groq.closed


async def test_groq_vision_turns_qwen_reasoning_off(providers):
    jpeg = _image_bytes("JPEG")
    fakes = providers(gemini={"gemini-3.8-flash": _gemini_error(429, "RESOURCE_EXHAUSTED")},
                      groq=_completion("a red square"))
    result = await generate_vision("what is this?", jpeg)
    assert result == LLMResult("a red square", "groq", "qwen/qwen3.6-27b")
    call = fakes.groq.calls[0]
    assert call["model"] == "qwen/qwen3.6-27b"
    assert call["extra_body"] == {"reasoning_effort": "none"}
    assert "reasoning_effort" not in call
    assert call["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url",
         "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}},
    ]}]


def test_groq_reasoning_follows_the_model_family():
    assert llm_text._groq_reasoning("openai/gpt-oss-120b") == {"reasoning_effort": "low"}
    assert llm_text._groq_reasoning("qwen/qwen3.6-27b") == {"extra_body": {"reasoning_effort": "none"}}
    assert llm_text._groq_reasoning("qwen/qwen3-32b") == {"extra_body": {"reasoning_effort": "none"}}
    assert not llm_text._groq_reasoning("llama-3.3-70b-versatile")


async def test_openrouter_is_the_last_tier_with_attribution_headers(providers):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_error(500, "INTERNAL")},
                      groq=_openai_error(openai.RateLimitError, 429, "slow down"),
                      openrouter=_completion("OK"))
    assert await generate_text("hi") == LLMResult("OK", "openrouter", "openrouter/free")
    call = fakes.openrouter.calls[0]
    assert call["model"] == "openrouter/free" and call["max_tokens"] == 2048
    assert "reasoning_effort" not in call and "extra_body" not in call
    assert fakes.chat_created[-1] == {
        "base_url": OPENROUTER, "api_key": FAKE_OPENROUTER_KEY, "timeout": 60.0,
        "headers": {"HTTP-Referer": "https://github.com/Matloob11/JARVIS-AGENT",
                    "X-Title": "JARVIS Agent"}}
    assert fakes.groq.closed and fakes.openrouter.closed


async def test_model_names_come_from_the_k35_env_keys(providers, monkeypatch):
    monkeypatch.setenv("JARVIS_TEXT_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("JARVIS_GROQ_TEXT_MODEL", "qwen/qwen3-32b")
    monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", "vendor/some-model:free")
    fakes = providers(gemini={"gemini-2.5-flash": _gemini_error(429, "RESOURCE_EXHAUSTED")},
                      groq=_openai_error(openai.InternalServerError, 500),
                      openrouter=_completion("OK"))
    # Text may use a ':free' model; only images may not.
    assert await generate_text("hi") == LLMResult("OK", "openrouter", "vendor/some-model:free")
    assert fakes.gemini.models_called == ["gemini-2.5-flash"]
    assert fakes.gemini.calls[0]["config"].temperature == 0.4
    groq_call = fakes.groq.calls[0]
    assert groq_call["model"] == "qwen/qwen3-32b"
    assert groq_call["extra_body"] == {"reasoning_effort": "none"}


def test_clean_cuts_only_the_reasoning_before_the_answer():
    assert llm_text._clean("<think>step 1</think>\n\nOK") == "OK"
    assert llm_text._clean(" <THINK>\n</THINK>\n<think>b</think> OK ") == "OK"
    assert llm_text._clean("<think>never closed") == ""
    assert llm_text._clean("\n<think>plan</think><think>cut off by the token limit") == ""
    assert llm_text._clean("<think>plan</think>The tag is <think>.") == "The tag is <think>."
    assert llm_text._clean(None) == ""
    # A tag anywhere else is content: an answer about the tags, or a review of llm_text itself.
    for answer in ("Use <think> only for reasoning.",
                   "R1 opens its reasoning with <think> and closes it with </think>; then answers.",
                   'Line 68 is `_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)`; it is fine.',
                   "OK <think>half a thought"):
        assert llm_text._clean(answer) == answer


async def test_a_think_tag_inside_an_answer_reaches_the_caller(providers):
    diff = ('-_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)\n'
            '+_LEADING_THINK_OPEN = re.compile(r"\\A\\s*<think>", re.IGNORECASE)')
    providers(gemini={"gemini-3.5-flash": _gemini_response(diff)})
    assert await generate_text("review this diff") == LLMResult(diff, "gemini", "gemini-3.5-flash")


async def test_reasoning_never_reaches_the_caller(providers):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_error(429, "RESOURCE_EXHAUSTED")},
                      groq=_completion("<think>only thinking, cut off"),
                      openrouter=_completion("<think>plan</think>\nOK"))
    assert await generate_text("hi") == LLMResult("OK", "openrouter", "openrouter/free")
    assert fakes.groq.calls


@pytest.mark.parametrize("bad", [
    json.JSONDecodeError("Expecting value", "<html>", 0),
    "<html>bad gateway</html>",
    _completion(None),
    openai.APITimeoutError(request=httpx.Request("POST", "https://api.example.invalid")),
], ids=["bad-json", "non-json-body", "no-content", "sdk-timeout"])
async def test_unusable_groq_answers_fall_to_openrouter(providers, bad):
    fakes = providers(gemini={"gemini-3.5-flash": _gemini_error(503, "UNAVAILABLE")},
                      groq=bad, openrouter=_completion("OK"))
    assert (await generate_text("hi")).provider == "openrouter"
    assert fakes.groq.closed


# ---------------------------------------------------------------- keys, failures, redaction


async def test_missing_keys_skip_their_tiers_without_building_clients(providers, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY")
    fakes = providers(groq=_completion("OK"))
    assert (await generate_text("hi")).provider == "groq"
    assert not fakes.gemini.created
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    with pytest.raises(LLMUnavailableError) as caught:
        await generate_text("hi")
    assert caught.value.reasons == ["gemini: GOOGLE_API_KEY not set", "groq: GROQ_API_KEY not set",
                                    "openrouter: OPENROUTER_API_KEY not set"]
    assert len(fakes.chat_created) == 1  # only the first, successful Groq call built a client


async def test_total_failure_raises_masked_capped_reasons(providers, raw_log):
    leaky_url = ("https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"
                 f"?key={FAKE_GOOGLE_KEY}")
    fakes = providers(
        gemini={"gemini-3.5-flash": _gemini_error(400, "INVALID_ARGUMENT", f"rejected {leaky_url}")},
        groq=_openai_error(openai.AuthenticationError, 401,
                           f"bad header Bearer {FAKE_GROQ_KEY} " + "x" * 3000),
        openrouter=httpx.ConnectError(
            f"connect failed: {OPENROUTER}/chat/completions?api_key={FAKE_OPENROUTER_KEY}"),
    )
    with pytest.raises(LLMUnavailableError) as caught:
        await generate_text("PRIVATE-PROMPT")
    reasons = caught.value.reasons
    assert [reason.split(":")[0] for reason in reasons] == [
        "gemini[gemini-3.5-flash]", "groq[openai/gpt-oss-120b]", "openrouter[openrouter/free]"]
    assert all(len(reason) <= llm_text.REASON_MAX_CHARS for reason in reasons)
    assert "key=***" in reasons[0] and "api_key=***" in reasons[2]
    everything = "\n".join([*reasons, str(caught.value), raw_log.getvalue()])
    assert not [key for key in FAKE_KEYS if key in everything]
    assert "PRIVATE-PROMPT" not in raw_log.getvalue()
    assert fakes.gemini.models_called == ["gemini-3.5-flash"]  # a 400 is not a 404
    assert fakes.groq.closed and fakes.openrouter.closed


def test_reasons_are_masked_before_they_are_cut():
    reasons: list[str] = []
    llm_text._fail(reasons, "gemini", "m", "x" * 180 + FAKE_GOOGLE_KEY + "y" * 50)
    assert len(reasons[0]) == llm_text.REASON_MAX_CHARS
    assert "***" in reasons[0] and "AQ.FAKE" not in reasons[0]  # no tail of the key survives


async def test_error_carries_reasons_and_is_a_runtime_error(providers):
    providers(gemini={"gemini-3.5-flash": _gemini_error(429, "RESOURCE_EXHAUSTED")},
              groq=_openai_error(openai.RateLimitError, 429),
              openrouter=_openai_error(openai.InternalServerError, 502))
    with pytest.raises(RuntimeError) as caught:
        await generate_text("hi")
    error = caught.value
    assert isinstance(error, LLMUnavailableError) and len(error.reasons) == 3
    assert str(error) == "; ".join(error.reasons)


# ---------------------------------------------------------------- vision


@pytest.mark.parametrize("kind", ["jpeg-bytes", "png-bytes", "pil-rgba"])
async def test_every_image_reaches_gemini_as_jpeg(providers, kind):
    jpeg = _image_bytes("JPEG")
    image = {"jpeg-bytes": jpeg, "png-bytes": _image_bytes("PNG"),
             "pil-rgba": Image.new("RGBA", (6, 4), (0, 0, 255, 100))}[kind]
    fakes = providers(gemini={"gemini-3.8-flash": _gemini_response("blue")})
    assert await generate_vision("what is this?", image) == LLMResult("blue", "gemini",
                                                                       "gemini-3.8-flash")
    part, prompt = fakes.gemini.calls[0]["contents"]
    assert prompt == "what is this?"
    assert part.inline_data.mime_type == "image/jpeg"
    assert part.inline_data.data.startswith(b"\xff\xd8\xff")
    if kind == "jpeg-bytes":
        assert part.inline_data.data == jpeg  # JPEG passes through untouched


@pytest.mark.parametrize("free_model", ["", "openrouter/free", "vendor/vision-model:free"])
async def test_vision_never_sends_an_image_to_a_free_openrouter_model(providers, monkeypatch,
                                                                     free_model):
    if free_model:
        monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", free_model)
    fakes = providers(gemini={}, groq=_openai_error(openai.InternalServerError, 500))
    with pytest.raises(LLMUnavailableError) as caught:
        await generate_vision("what is this?", _image_bytes("JPEG"))
    assert caught.value.reasons[-1] == (
        f"openrouter[{free_model or 'openrouter/free'}]: skipped, images never go to a ':free' model")
    assert [created["base_url"] for created in fakes.chat_created] == [GROQ]


async def test_vision_uses_a_paid_openrouter_model(providers, monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", "vendor/paid-vision")
    fakes = providers(gemini={"gemini-3.8-flash": _gemini_error(503, "UNAVAILABLE")},
                      groq=_openai_error(openai.InternalServerError, 500),
                      openrouter=_completion("a cat"))
    result = await generate_vision("what is this?", _image_bytes("PNG"))
    assert result == LLMResult("a cat", "openrouter", "vendor/paid-vision")
    content = fakes.openrouter.calls[0]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,/9j/")


async def test_bad_arguments_raise_before_any_provider_is_called():
    for call in (generate_text("   "), generate_text("hi", timeout=0),
                 generate_text("hi", max_output_tokens=0),
                 generate_text("hi", providers=("gemini", "claude")),
                 generate_text("hi", providers=()), generate_vision("hi", b"")):
        with pytest.raises(ValueError):
            await call
    with pytest.raises(TypeError):
        await generate_vision("hi", 42)  # type: ignore[arg-type]
    with pytest.raises(OSError):  # PIL.UnidentifiedImageError
        await generate_vision("hi", b"definitely not an image")


async def test_providers_limit_the_tiers_and_keep_the_fixed_order(providers):
    fakes = providers(groq=_completion("from groq"), openrouter=_completion("from openrouter"))
    assert (await generate_text("hi", providers=("openrouter", "groq"))).provider == "groq"
    assert (await generate_text("hi", providers="openrouter")).provider == "openrouter"
    assert not fakes.gemini.created
    fakes.groq.outcome = _openai_error(openai.RateLimitError, 429)
    with pytest.raises(LLMUnavailableError) as caught:
        await generate_text("hi", providers=["GROQ"])
    assert len(caught.value.reasons) == 1 and caught.value.reasons[0].startswith("groq[")


# ---------------------------------------------------------------- list_gemini_models


async def test_list_gemini_models_returns_generate_content_ids(providers):
    fakes = providers()
    fakes.gemini.listing = [
        genai_types.Model(name="models/gemini-3.8-flash", supported_actions=["generateContent"]),
        genai_types.Model(name="models/text-embedding-004", supported_actions=["embedContent"]),
        genai_types.Model(name="models/gemini-2.5-flash",
                          supported_actions=["countTokens", "generateContent"]),
        genai_types.Model(name="models/gemini-3.8-flash", supported_actions=["generateContent"]),
    ]
    assert await list_gemini_models() == ["gemini-2.5-flash", "gemini-3.8-flash"]
    assert fakes.gemini.closed == ["aio", "sync"]


async def test_list_gemini_models_never_raises(providers, monkeypatch, raw_log):
    fakes = providers()
    fakes.gemini.listing = httpx.ConnectError(
        f"connect failed: https://generativelanguage.googleapis.com/v1beta/models?key={FAKE_GOOGLE_KEY}")
    assert await list_gemini_models() == []
    logged = raw_log.getvalue()
    assert "listing Gemini models failed: ConnectError" in logged and "key=***" in logged
    assert FAKE_GOOGLE_KEY not in logged
    assert fakes.gemini.closed == ["aio", "sync"]
    monkeypatch.delenv("GOOGLE_API_KEY")
    assert await list_gemini_models() == []
    assert len(fakes.gemini.created) == 1


# ---------------------------------------------------------------- the real SDKs, mocked HTTP


@pytest.fixture(name="http")
def _http(monkeypatch) -> SimpleNamespace:
    """The real google-genai and openai SDKs, sending to a scripted httpx.MockTransport."""
    routes: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for marker, respond in routes.items():
            if marker in str(request.url):
                return respond(request)
        return httpx.Response(418, json={"error": {"message": "unscripted request"}})

    transport = httpx.MockTransport(handler)
    real_genai_client = llm_text.genai.Client
    real_async_openai = llm_text.openai.AsyncOpenAI

    def genai_client(**kwargs: Any) -> Any:
        options = kwargs["http_options"]
        options.async_client_args = {**(options.async_client_args or {}), "transport": transport}
        return real_genai_client(**kwargs)

    def async_openai(**kwargs: Any) -> Any:
        kwargs["http_client"] = httpx.AsyncClient(transport=transport)
        return real_async_openai(**kwargs)

    monkeypatch.setattr(llm_text, "_gemini_client", REAL_GEMINI_FACTORY)
    monkeypatch.setattr(llm_text, "_openai_client", REAL_OPENAI_FACTORY)
    monkeypatch.setattr(llm_text.genai, "Client", genai_client)
    monkeypatch.setattr(llm_text.openai, "AsyncOpenAI", async_openai)
    return SimpleNamespace(routes=routes, seen=seen)


def _reply(status: int, body: Any) -> Callable[[httpx.Request], httpx.Response]:
    if isinstance(body, bytes):
        return lambda _request: httpx.Response(status, content=body,
                                               headers={"content-type": "application/json"})
    return lambda _request: httpx.Response(status, json=body)


def _gemini_body(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                            "finishReason": "STOP"}]}


def _chat_body(text: str) -> dict[str, Any]:
    return {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}]}


async def test_real_sdks_send_keys_only_in_headers(http):
    http.routes.update({
        "gemini-3.5-flash:generateContent": _reply(404, {"error": {
            "code": 404, "message": "models/gemini-3.5-flash is not found", "status": "NOT_FOUND"}}),
        "gemini-3.8-flash:generateContent": _reply(200, _gemini_body("OK")),
        "api.groq.com": _reply(429, {"error": {"message": "rate limited", "type": "tokens"}}),
        "openrouter.ai": _reply(200, _chat_body("<think>plan</think>OK")),
    })
    assert await generate_text("hi") == LLMResult("OK", "gemini", "gemini-3.8-flash")
    assert await generate_text("hi", providers=("groq", "openrouter")) == LLMResult(
        "OK", "openrouter", "openrouter/free")
    gemini, gemini_next, groq, openrouter = http.seen  # no SDK retried anything
    for request in http.seen:
        assert "key=" not in str(request.url)
        assert not [key for key in FAKE_KEYS if key in str(request.url)]
    for request in (gemini, gemini_next):
        assert request.headers["x-goog-api-key"] == FAKE_GOOGLE_KEY
        assert "authorization" not in request.headers
    assert gemini.url.path == "/v1beta/models/gemini-3.5-flash:generateContent"
    assert json.loads(gemini.content)["generationConfig"] == {"maxOutputTokens": 2048}
    assert str(groq.url) == f"{GROQ}/chat/completions"
    assert groq.headers["authorization"] == f"Bearer {FAKE_GROQ_KEY}"
    body = json.loads(groq.content)
    assert (body["model"], body["reasoning_effort"], body["max_completion_tokens"]) == (
        "openai/gpt-oss-120b", "low", 2048)
    assert str(openrouter.url) == f"{OPENROUTER}/chat/completions"
    assert openrouter.headers["authorization"] == f"Bearer {FAKE_OPENROUTER_KEY}"
    assert openrouter.headers["http-referer"] == "https://github.com/Matloob11/JARVIS-AGENT"
    assert openrouter.headers["x-title"] == "JARVIS Agent"


async def test_real_sdk_vision_requests(http):
    jpeg = _image_bytes("JPEG")
    http.routes.update({
        "gemini-3.8-flash:generateContent": _reply(500, {"error": {
            "code": 500, "message": "internal", "status": "INTERNAL"}}),
        "api.groq.com": _reply(200, _chat_body("a red square")),
    })
    assert await generate_vision("what is this?", jpeg) == LLMResult(
        "a red square", "groq", "qwen/qwen3.6-27b")
    gemini, groq = http.seen
    image_part, text_part = json.loads(gemini.content)["contents"][0]["parts"]
    assert image_part["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.urlsafe_b64decode(image_part["inlineData"]["data"]) == jpeg  # the SDK's encoding
    assert text_part == {"text": "what is this?"}
    body = json.loads(groq.content)
    assert body["model"] == "qwen/qwen3.6-27b" and body["reasoning_effort"] == "none"
    assert body["messages"][0]["content"][1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"))


async def test_real_sdk_failures_fall_through_every_tier(http):
    http.routes.update({
        "gemini-3.5-flash:generateContent": _reply(429, {"error": {
            "code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}),
        "api.groq.com": _reply(200, b"{not json"),
        "openrouter.ai": _reply(503, {"error": {"message": "no capacity"}}),
    })
    with pytest.raises(LLMUnavailableError) as caught:
        await generate_text("hi")
    gemini, groq, openrouter = caught.value.reasons
    assert gemini.startswith("gemini[gemini-3.5-flash]: ClientError: 429 RESOURCE_EXHAUSTED")
    assert groq.startswith("groq[openai/gpt-oss-120b]: JSONDecodeError")
    assert openrouter.startswith("openrouter[openrouter/free]: InternalServerError: Error code: 503")
    assert len(http.seen) == 3  # a 429 skips the other Gemini models, and nothing was retried
    assert not llm_text._dead_models


async def test_real_list_gemini_models_uses_the_header(http):
    http.routes["/v1beta/models"] = _reply(200, {"models": [
        {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
    ]})
    assert await list_gemini_models() == ["gemini-3.8-flash"]
    (request,) = http.seen
    assert request.method == "GET" and "key=" not in str(request.url)
    assert request.headers["x-goog-api-key"] == FAKE_GOOGLE_KEY


@pytest.fixture(name="gemini_api")
async def _gemini_api(monkeypatch) -> AsyncIterator[SimpleNamespace]:
    """An aiohttp server on 127.0.0.1 in place of the Gemini API. With no httpx transport injected,
    the real google-genai client reaches it over aiohttp, the transport production uses."""
    replies: dict[str, tuple[int, dict[str, Any]]] = {}
    seen: list[SimpleNamespace] = []
    clients: list[Any] = []

    async def handle(request: web.Request) -> web.Response:
        seen.append(SimpleNamespace(method=request.method, path=request.path, url=str(request.url),
                                    headers={k.lower(): v for k, v in request.headers.items()}))
        status, body = replies.get(request.path, (418, {"error": {"message": "unscripted"}}))
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base_url = f"http://127.0.0.1:{runner.addresses[0][1]}/"
    real_genai_client = llm_text.genai.Client

    def genai_client(**kwargs: Any) -> Any:
        kwargs["http_options"].base_url = base_url
        clients.append(real_genai_client(**kwargs))
        return clients[-1]

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(llm_text, "_gemini_client", REAL_GEMINI_FACTORY)
    monkeypatch.setattr(llm_text.genai, "Client", genai_client)
    try:
        yield SimpleNamespace(replies=replies, seen=seen, clients=clients)
    finally:
        await runner.cleanup()


async def test_real_gemini_sdk_over_aiohttp(gemini_api, monkeypatch):
    gemini_api.replies.update({
        "/v1beta/models/gemini-3.5-flash:generateContent": (404, {"error": {
            "code": 404, "message": "models/gemini-3.5-flash is not found", "status": "NOT_FOUND"}}),
        "/v1beta/models/gemini-3.8-flash:generateContent": (200, _gemini_body("OK")),
        "/v1beta/models": (200, {"models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]}),
    })
    await llm_text._shared_tls_context()  # built once per process, before the count starts
    contexts_built: list[str] = []
    real_create_default_context = ssl.create_default_context

    def counting_create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        contexts_built.append("ssl.create_default_context")
        return real_create_default_context(*args, **kwargs)

    monkeypatch.setattr(ssl, "create_default_context", counting_create_default_context)
    assert await generate_text("hi", providers="gemini") == LLMResult("OK", "gemini",
                                                                      "gemini-3.8-flash")
    assert await list_gemini_models() == ["gemini-3.8-flash"]
    # Without "ssl": tls google-genai builds two contexts per client, on the event loop.
    assert not contexts_built
    assert llm_text._dead_models == {"gemini-3.5-flash"}
    assert [(request.method, request.path) for request in gemini_api.seen] == [
        ("POST", "/v1beta/models/gemini-3.5-flash:generateContent"),
        ("POST", "/v1beta/models/gemini-3.8-flash:generateContent"),
        ("GET", "/v1beta/models")]  # nothing retried
    for request in gemini_api.seen:
        assert request.headers["x-goog-api-key"] == FAKE_GOOGLE_KEY
        assert "key=" not in request.url and FAKE_GOOGLE_KEY not in request.url
    assert len(gemini_api.clients) == 2
    for client in gemini_api.clients:
        assert client._api_client._use_aiohttp()  # not the httpx fallback the other tests take
        assert client._api_client._aiohttp_session.closed


# ---------------------------------------------------------------- import


def test_import_builds_nothing_and_opens_no_connection():
    """A fresh interpreter with every socket entry point refused: importing llm_text must not
    try one, and must not build a TLS context or a client."""
    probe = (
        "import socket\n"
        "calls = []\n"
        "def refuse(*args, **kwargs):\n"
        "    calls.append(1)\n"
        "    raise OSError('network refused by the probe')\n"
        "socket.socket.connect = refuse\n"
        "socket.socket.connect_ex = refuse\n"
        "socket.create_connection = refuse\n"
        "socket.getaddrinfo = refuse\n"
        "import services.ai_core.llm_text as llm_text\n"
        "print('PROBE', len(calls), llm_text._tls_context is None, len(llm_text._dead_models))\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=240, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE ")]
    assert lines == ["PROBE 0 True 0"], jarvis_security.redact_secrets(result.stderr[-1500:])


# What the first calls must not import on the event loop thread (review of C-45: openai's chat
# resources, httpx's transport stack and anyio's backend took about 0.5 s there).
SDK_PACKAGES = frozenset({"aiohttp", "anyio", "google", "h11", "h2", "hpack", "httpcore", "httpx",
                          "hyperframe", "jiter", "openai", "pydantic", "pydantic_core", "socksio"})
FIRST_CALL_PROBE = textwrap.dedent("""
    import asyncio, json, sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    CHAT = {"id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "OK"}}]}
    GEMINI = {"candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]},
                              "finishReason": "STOP"}]}

    class Api(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            body = json.dumps(CHAT if self.path.endswith("/chat/completions") else GEMINI).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    from services.ai_core import llm_text
    from services.utils import jarvis_logger

    async def no_bridge(*args):
        return None

    jarvis_logger._BRIDGE_NOTIFY_LOG = no_bridge  # the worker builds its bridge client long before
    llm_text.GROQ_BASE_URL = base + "/openai/v1"
    real_genai_client = llm_text.genai.Client

    def local_genai_client(**kwargs):
        kwargs["http_options"].base_url = base + "/"
        return real_genai_client(**kwargs)

    llm_text.genai.Client = local_genai_client
    loop_thread = threading.get_ident()
    report = {"chat_loaded_before": "openai.resources.chat" in sys.modules}

    class OnLoopImports:
        current = None

        @classmethod
        def find_spec(cls, name, path=None, target=None):
            if cls.current is not None and threading.get_ident() == loop_thread:
                cls.current.append(name)
            return None

    sys.meta_path.insert(0, OnLoopImports)

    async def main():
        for provider in sys.argv[1:]:
            OnLoopImports.current = report.setdefault(provider, [])
            result = await llm_text.generate_text("hi", providers=provider, timeout=30)
            OnLoopImports.current = None
            report[provider + " answer"] = [result.provider, result.text]

    asyncio.run(main())
    server.shutdown()
    print("PROBE " + json.dumps(report))
""")


@pytest.mark.parametrize("order", [("groq", "gemini"), ("gemini", "groq")],
                         ids=["groq-first", "gemini-first"])
def test_first_calls_import_no_sdk_module_on_the_event_loop(order):
    """A fresh interpreter, a server on 127.0.0.1 and fake keys: the SDK modules that load on
    first use must load in the worker thread that builds the TLS context, whichever tier is first."""
    env = {name: value for name, value in os.environ.items()
           if name not in SECRET_ENV_NAMES and not name.endswith("_API_KEY")}
    env.update({"PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1,localhost",
                "GOOGLE_API_KEY": FAKE_GOOGLE_KEY, "GROQ_API_KEY": FAKE_GROQ_KEY,
                "OPENROUTER_API_KEY": FAKE_OPENROUTER_KEY})
    result = subprocess.run([sys.executable, "-c", FIRST_CALL_PROBE, *order], cwd=ROOT, env=env,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=240,
                            check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE ")]
    assert len(lines) == 1, jarvis_security.redact_secrets(result.stderr[-1500:])
    report = json.loads(lines[0].removeprefix("PROBE "))
    assert [report[f"{provider} answer"] for provider in order] == [[p, "OK"] for p in order]
    assert not report["chat_loaded_before"], "the probe no longer covers openai's lazy import"
    on_loop = {provider: [name for name in report[provider] if name.split(".")[0] in SDK_PACKAGES]
               for provider in order}
    assert on_loop == {provider: [] for provider in order}
