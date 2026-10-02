"""
# llm_text.py
Shared LLM access (contract K-30) for vision, research, swarm, bug hunter and self-repair.

generate_text() and generate_vision() try three tiers in this order and return the first answer:
  1. Gemini (google-genai, GOOGLE_API_KEY): the model argument or JARVIS_TEXT_MODEL /
     JARVIS_VISION_MODEL, then GEMINI_FALLBACK_MODELS. A 404 marks that id dead for the rest of the
     process and tries the next id; a 429, a 5xx, a timeout or any other error goes to tier 2.
  2. Groq (OpenAI-compatible, GROQ_API_KEY): JARVIS_GROQ_TEXT_MODEL / JARVIS_GROQ_VISION_MODEL,
     with reasoning kept 'low' on gpt-oss and switched off on qwen3 (review R31).
  3. OpenRouter (OpenAI-compatible, OPENROUTER_API_KEY): JARVIS_OPENROUTER_MODEL. Images never go
     to a ':free' model (docs/ARCHITECTURE_VPS_BRAIN.md, M9), so with the default openrouter/free a
     vision request ends after Groq.
When every tier fails, LLMUnavailableError carries one short reason per failed attempt.

K-29: keys travel only in the headers the SDKs set (x-goog-api-key, Authorization: Bearer), never
in a URL; every reason is masked with redact_secrets() and capped at 200 chars; prompts, images and
answers are never logged. Nothing touches the network at import time: clients are built per call
(cheap, they share one TLS context) and closed afterwards. The first call builds that context and
imports the SDK modules that load on first use in a worker thread, so it does not block the loop.
"""

import asyncio
import base64
import dataclasses
import importlib
import inspect
import io
import os
import re
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import certifi
import openai
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from PIL import Image

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

logger = setup_logger("JARVIS-LLM-TEXT")

PROVIDERS: tuple[str, ...] = ("gemini", "groq", "openrouter")
DEFAULT_TEXT_MODEL = "gemini-3.5-flash"
DEFAULT_VISION_MODEL = "gemini-3.8-flash"
GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash",
)
DEFAULT_GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.6-27b"
DEFAULT_OPENROUTER_MODEL = "openrouter/free"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Optional OpenRouter app attribution; the key itself goes in the Authorization header.
OPENROUTER_HEADERS: dict[str, str] = {
    "HTTP-Referer": "https://github.com/Matloob11/JARVIS-AGENT",
    "X-Title": "JARVIS Agent",
}
REASON_MAX_CHARS = 200
JPEG_QUALITY = 85

_JPEG_MAGIC = b"\xff\xd8\xff"
# Reasoning a model writes before its answer. Only a leading block is reasoning: a '<think>' later
# in an answer is content (an answer about these tags, a review of this file).
_LEADING_THINK = re.compile(r"\A\s*(?:<think>.*?</think>\s*)+", re.IGNORECASE | re.DOTALL)
_LEADING_THINK_OPEN = re.compile(r"\A\s*<think>", re.IGNORECASE)
# Gemini ids that still take temperature; Google deprecated sampling parameters on Gemini 3.x.
_GEMINI_SAMPLING_MODEL = re.compile(r"gemini-[12][.-]")
# What the SDKs import on their first client or request (httpx's transport stack, anyio's asyncio
# backend, openai's chat resources): about 0.5 s that would block the event loop on the first call.
_LAZY_SDK_MODULES: tuple[str, ...] = (
    "httpcore", "anyio._backends._asyncio", "openai.resources.chat",
)

# Gemini ids that answered 404 in this process; they are not tried again until a restart.
_dead_models: set[str] = set()
_tls_context: ssl.SSLContext | None = None


@dataclass(frozen=True)
class LLMResult:
    """One answer, with the provider ('gemini', 'groq' or 'openrouter') and model that wrote it."""
    text: str
    provider: str
    model: str


class LLMUnavailableError(RuntimeError):
    """Every tier failed. reasons: one masked line (at most 200 chars) per failed attempt."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons) or "no LLM provider available")


@dataclass(frozen=True)
class _Request:
    prompt: str
    system: str | None
    max_output_tokens: int
    temperature: float
    timeout: float
    image_jpeg: bytes | None = None

    @property
    def kind(self) -> str:
        return "text" if self.image_jpeg is None else "vision"


async def generate_text(prompt: str, *, system: str | None = None, model: str | None = None,
                        max_output_tokens: int = 2048, temperature: float = 0.4,
                        timeout: float = 60.0,
                        providers: str | Iterable[str] | None = None) -> LLMResult:
    """
    K-30: answers prompt with the first tier that works (Gemini, then Groq, then OpenRouter).
    model is a Gemini id tried first (default JARVIS_TEXT_MODEL). timeout bounds each provider
    attempt, in seconds. temperature reaches Gemini 2.x, Groq and OpenRouter; Gemini 3.x models
    run at their own default. providers limits the call to some tiers, still in that order (for
    example ('groq',)). Raises LLMUnavailableError when every tier fails, ValueError for an empty
    prompt or a bad option.
    """
    request = _make_request(prompt, system, max_output_tokens, temperature, timeout)
    return await _run(request, model, _selected(providers))


async def generate_vision(prompt: str, image: Image.Image | bytes, *, model: str | None = None,
                          max_output_tokens: int = 2048, temperature: float = 0.4,
                          timeout: float = 60.0,
                          providers: str | Iterable[str] | None = None) -> LLMResult:
    """
    K-30: answers prompt about image (a PIL image or encoded image bytes) with the first tier that
    works. Every provider gets JPEG: JPEG bytes as they are, anything else re-encoded off the event
    loop. model is a Gemini id tried first (default JARVIS_VISION_MODEL); the other options are
    those of generate_text(). The OpenRouter tier is skipped while JARVIS_OPENROUTER_MODEL is a
    ':free' model, because laptop images never go to one. Raises LLMUnavailableError when every
    tier fails; ValueError, TypeError or PIL's OSError for an unusable prompt or image.
    """
    request = _make_request(prompt, None, max_output_tokens, temperature, timeout)
    selected = _selected(providers)
    jpeg = await asyncio.to_thread(_to_jpeg, image)
    return await _run(dataclasses.replace(request, image_jpeg=jpeg), model, selected)


async def list_gemini_models(timeout: float = 30.0) -> list[str]:
    """
    Diagnostics: the Gemini model ids this GOOGLE_API_KEY can call with generateContent, sorted and
    without the 'models/' prefix. Never raises: without a key, or when the listing fails, it logs
    the masked reason and returns [].
    """
    api_key = _env("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("llm_text: cannot list Gemini models, GOOGLE_API_KEY is not set")
        return []
    client: genai.Client | None = None
    try:
        client = _gemini_client(api_key, timeout, await _shared_tls_context())
        return await asyncio.wait_for(_gemini_model_ids(client), timeout=timeout)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("llm_text: listing Gemini models failed: %s",
                       redact_secrets(_describe(e, timeout))[:REASON_MAX_CHARS])
        return []
    finally:
        if client is not None:
            await _close_gemini(client)


def _make_request(prompt: str, system: str | None, max_output_tokens: int, temperature: float,
                  timeout: float) -> _Request:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if max_output_tokens < 1 or timeout <= 0:
        raise ValueError("max_output_tokens and timeout must be positive")
    return _Request(prompt=prompt, system=system or None, max_output_tokens=int(max_output_tokens),
                    temperature=float(temperature), timeout=float(timeout))


def _selected(providers: str | Iterable[str] | None) -> tuple[str, ...]:
    if providers is None:
        return PROVIDERS
    names = [providers] if isinstance(providers, str) else list(providers)
    wanted = {str(name).strip().lower() for name in names}
    if not wanted or not wanted <= set(PROVIDERS):
        raise ValueError(f"providers must be a non-empty subset of {PROVIDERS}")
    return tuple(name for name in PROVIDERS if name in wanted)


async def _run(request: _Request, model: str | None, providers: tuple[str, ...]) -> LLMResult:
    reasons: list[str] = []
    for provider in providers:
        if provider == "gemini":
            result = await _gemini_tier(request, model, reasons)
        elif provider == "groq":
            result = await _groq_tier(request, reasons)
        else:
            result = await _openrouter_tier(request, reasons)
        if result is not None:
            return result
    logger.warning("llm_text %s: every provider failed (%d attempts)", request.kind, len(reasons))
    raise LLMUnavailableError(reasons)


# ---------------------------------------------------------------- tier 1: Gemini


async def _gemini_tier(request: _Request, model: str | None,
                       reasons: list[str]) -> LLMResult | None:
    api_key = _env("GOOGLE_API_KEY")
    candidates = _gemini_candidates(request.image_jpeg is not None, model)
    if not api_key or not candidates:
        _fail(reasons, "gemini", None, "GOOGLE_API_KEY not set" if not api_key
              else "every candidate model answered 404 earlier (a restart retries them)")
        return None
    try:
        client = _gemini_client(api_key, request.timeout, await _shared_tls_context())
    except Exception as e:  # pylint: disable=broad-exception-caught
        _fail(reasons, "gemini", None, _describe(e, request.timeout))
        return None
    try:
        return await _try_gemini_models(client, request, candidates, reasons)
    finally:
        await _close_gemini(client)


async def _try_gemini_models(client: genai.Client, request: _Request, candidates: list[str],
                             reasons: list[str]) -> LLMResult | None:
    for candidate in candidates:
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=candidate, contents=_gemini_contents(request),
                    config=_gemini_config(request, candidate)),
                timeout=request.timeout)
            text = _clean(response.text)
        except Exception as e:  # pylint: disable=broad-exception-caught
            _fail(reasons, "gemini", candidate, _describe(e, request.timeout))
            if not _is_not_found(e):
                return None
            _dead_models.add(candidate)
            continue
        if text:
            return _answered(request, "gemini", candidate, text, started)
        _fail(reasons, "gemini", candidate, f"empty response ({_gemini_finish(response)})")
        return None
    return None


def _gemini_candidates(vision: bool, model: str | None) -> list[str]:
    """The model argument (else the env model), then the fallbacks: deduplicated, dead ids out."""
    first = _model_id(model or "") or _model_id(
        _env("JARVIS_VISION_MODEL", DEFAULT_VISION_MODEL) if vision
        else _env("JARVIS_TEXT_MODEL", DEFAULT_TEXT_MODEL))
    candidates: list[str] = []
    for raw in (first, *GEMINI_FALLBACK_MODELS):
        name = _model_id(raw)
        if name and name not in candidates and name not in _dead_models:
            candidates.append(name)
    return candidates


def _model_id(name: str) -> str:
    return name.strip().removeprefix("models/")


def _gemini_client(api_key: str, timeout: float, tls: ssl.SSLContext) -> genai.Client:
    """Developer API client: the key goes in the x-goog-api-key header and the SDK does not retry.
    Each client would otherwise load the CA bundle three times (about 1 s), so all share one."""
    return genai.Client(
        vertexai=False, api_key=api_key,
        http_options=genai_types.HttpOptions(
            timeout=max(1, round(timeout * 1000)),  # milliseconds
            client_args={"verify": tls},
            async_client_args={"verify": tls, "ssl": tls},
        ),
    )


def _gemini_contents(request: _Request) -> Any:
    if request.image_jpeg is None:
        return request.prompt
    return [genai_types.Part.from_bytes(data=request.image_jpeg, mime_type="image/jpeg"),
            request.prompt]


def _gemini_config(request: _Request, model: str) -> genai_types.GenerateContentConfig:
    return genai_types.GenerateContentConfig(
        system_instruction=request.system,
        max_output_tokens=request.max_output_tokens,
        temperature=request.temperature if _GEMINI_SAMPLING_MODEL.match(model) else None,
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
    )


def _is_not_found(error: BaseException) -> bool:
    return isinstance(error, genai_errors.APIError) and (
        error.code == 404 or str(error.status or "").upper() == "NOT_FOUND")


def _gemini_finish(response: Any) -> str:
    """Why Gemini sent no text: the finish reason (SAFETY, MAX_TOKENS, ...) or the prompt block."""
    candidates = getattr(response, "candidates", None) or []
    finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    blocked = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
    reason = finish or blocked
    return str(getattr(reason, "value", reason)) if reason else "no text"


async def _gemini_model_ids(client: genai.Client) -> list[str]:
    ids: set[str] = set()
    async for model in await client.aio.models.list(config={"page_size": 100}):
        actions = model.supported_actions
        if actions is None or "generateContent" in actions:
            ids.add(_model_id(model.name or ""))
    ids.discard("")
    return sorted(ids)


async def _close_gemini(client: genai.Client) -> None:
    await _close_quietly(client.aio.aclose)
    await _close_quietly(client.close)


# ---------------------------------------------------------------- tiers 2 and 3: Groq, OpenRouter


async def _groq_tier(request: _Request, reasons: list[str]) -> LLMResult | None:
    if request.image_jpeg is None:
        model = _env("JARVIS_GROQ_TEXT_MODEL", DEFAULT_GROQ_TEXT_MODEL)
    else:
        model = _env("JARVIS_GROQ_VISION_MODEL", DEFAULT_GROQ_VISION_MODEL)
    return await _chat_tier("groq", model, request, reasons, base_url=GROQ_BASE_URL,
                            key_name="GROQ_API_KEY", token_param="max_completion_tokens",
                            extra=_groq_reasoning(model))


async def _openrouter_tier(request: _Request, reasons: list[str]) -> LLMResult | None:
    model = _env("JARVIS_OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    if request.image_jpeg is not None and _is_free_model(model):
        _fail(reasons, "openrouter", model, "skipped, images never go to a ':free' model")
        return None
    return await _chat_tier("openrouter", model, request, reasons, base_url=OPENROUTER_BASE_URL,
                            key_name="OPENROUTER_API_KEY", token_param="max_tokens",
                            headers=OPENROUTER_HEADERS)


def _groq_reasoning(model: str) -> dict[str, Any]:
    """Review R31: qwen3 reasons by default, so reasoning is switched off; gpt-oss always reasons,
    so it is kept low. Other Groq models do not take the parameter."""
    name = model.lower()
    if "gpt-oss" in name:
        return {"reasoning_effort": "low"}
    if "qwen3" in name:
        return {"extra_body": {"reasoning_effort": "none"}}
    return {}


def _is_free_model(model: str) -> bool:
    """OpenRouter's free routes: the openrouter/free router and every '<id>:free' variant."""
    name = model.strip().lower()
    return name == "openrouter/free" or name.endswith(":free")


async def _chat_tier(provider: str, model: str, request: _Request, reasons: list[str], *,
                     base_url: str, key_name: str, token_param: str,
                     extra: dict[str, Any] | None = None,
                     headers: dict[str, str] | None = None) -> LLMResult | None:
    api_key = _env(key_name)
    if not api_key:
        _fail(reasons, provider, None, f"{key_name} not set")
        return None
    started = time.monotonic()
    client: openai.AsyncOpenAI | None = None
    try:
        client = _openai_client(base_url, api_key, request.timeout, await _shared_tls_context(),
                                headers)
        completion = await asyncio.wait_for(
            client.chat.completions.create(
                model=model, messages=_chat_messages(request), temperature=request.temperature,
                **{token_param: request.max_output_tokens}, **(extra or {})),
            timeout=request.timeout)
        text = _clean(completion.choices[0].message.content)
    except Exception as e:  # pylint: disable=broad-exception-caught
        _fail(reasons, provider, model, _describe(e, request.timeout))
        return None
    finally:
        if client is not None:
            await _close_quietly(client.close)
    if not text:
        _fail(reasons, provider, model, "empty response")
        return None
    return _answered(request, provider, model, text, started)


def _openai_client(base_url: str, api_key: str, timeout: float, tls: ssl.SSLContext,
                   headers: dict[str, str] | None = None) -> openai.AsyncOpenAI:
    """OpenAI-compatible client: the key goes in 'Authorization: Bearer'; no SDK retries, because
    the next tier is the retry."""
    return openai.AsyncOpenAI(
        api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0,
        default_headers=headers, http_client=openai.DefaultAsyncHttpxClient(verify=tls),
    )


def _chat_messages(request: _Request) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    if request.image_jpeg is None:
        messages.append({"role": "user", "content": request.prompt})
    else:
        data_url = "data:image/jpeg;base64," + base64.b64encode(request.image_jpeg).decode("ascii")
        messages.append({"role": "user", "content": [
            {"type": "text", "text": request.prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]})
    return messages


# ---------------------------------------------------------------- shared helpers


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


async def _shared_tls_context() -> ssl.SSLContext:
    """One verifying TLS context for every client, built once and off the event loop (loading the
    CA bundle takes about 0.3 s); SSL_CERT_FILE / SSL_CERT_DIR are honoured like google-genai."""
    global _tls_context  # pylint: disable=global-statement
    if _tls_context is None:
        _tls_context = await asyncio.to_thread(_prepare_sdks)
    return _tls_context


def _prepare_sdks() -> ssl.SSLContext:
    """Worker thread, once per process: imports _LAZY_SDK_MODULES, then builds the TLS context."""
    for name in _LAZY_SDK_MODULES:
        try:
            importlib.import_module(name)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Only a head start: should an SDK move the module, the first call imports it instead.
            logger.debug("llm_text: could not preload %s (%s)", name, type(e).__name__)
    return ssl.create_default_context(
        cafile=os.environ.get("SSL_CERT_FILE") or certifi.where(),
        capath=os.environ.get("SSL_CERT_DIR") or None,
    )


def _clean(text: Any) -> str:
    """The answer without the reasoning a model may write before it: leading '<think>...</think>'
    blocks are cut, and an answer still inside a leading unclosed '<think>' is empty."""
    if not isinstance(text, str):
        return ""
    text = _LEADING_THINK.sub("", text, count=1)
    return "" if _LEADING_THINK_OPEN.match(text) else text.strip()


def _describe(error: BaseException, timeout: float) -> str:
    """One unmasked line about a failed attempt; _fail() masks and caps it."""
    if isinstance(error, TimeoutError):
        return f"timeout after {timeout:g}s"
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__


def _fail(reasons: list[str], provider: str, model: str | None, detail: str) -> None:
    """Records a failed attempt. Masked before it is cut, so a cut never leaves part of a key."""
    where = f"{provider}[{model}]" if model else provider
    reason = redact_secrets(f"{where}: {detail}")[:REASON_MAX_CHARS]
    reasons.append(reason)
    logger.warning("llm_text: %s", reason)


def _answered(request: _Request, provider: str, model: str, text: str,
              started: float) -> LLMResult:
    logger.info("llm_text %s answered by %s (%s) in %.1fs", request.kind, provider, model,
                time.monotonic() - started)
    return LLMResult(text=text, provider=provider, model=model)


async def _close_quietly(close: Callable[[], Any]) -> None:
    """Closing a client must never hide the answer or the real failure."""
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug("llm_text: closing a client failed: %s", type(e).__name__)


def _to_jpeg(image: Image.Image | bytes) -> bytes:
    """JPEG bytes pass through; other image bytes and PIL images are re-encoded as RGB JPEG."""
    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
        if not data:
            raise ValueError("image is empty")
        if data.startswith(_JPEG_MAGIC):
            return data
        with Image.open(io.BytesIO(data)) as opened:
            return _encode_jpeg(opened)
    if isinstance(image, Image.Image):
        return _encode_jpeg(image)
    raise TypeError(f"image must be a PIL image or image bytes, not {type(image).__name__}")


def _encode_jpeg(image: Image.Image) -> bytes:
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()
