"""C-48: vision tools on current models, the K-30 provider chain and fresh camera frames only.

Covered: analyze_screen / analyze_camera through llm_text (Gemini 3.x, then Groq qwen3.8, then OpenRouter),
an openai.APIError that no longer escapes the Groq tier, the 'Vision providers failed' error, the longest-side
1024 px downscale (2026-10-06: portrait frames shrink too, so Groq's 'Request too large' 429 stays away), the
single short Gemini retry on a timeout or network blip, the 60 s tool timeouts, frame timestamps from the UI,
stale frames that are never sent (the cv2 camera or 'camera off' instead), VisionHandler.analyze_current_frame
and analyze_surroundings' error status.

Nothing real runs: the Gemini and OpenAI-compatible clients, the TLS context, cv2 and pyautogui are fakes;
no key, model download or network call is used.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import base64
import time
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import openai
import pytest
from google.genai import errors as genai_errors
from PIL import Image

from services.ai_core import agent_loops, jarvis_vision, llm_text
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.ai_core.llm_text import LLMUnavailableError
from services.utils.jarvis_resilience import resilience_manager
from src.core import agent_core, vision_handler
from src.core.vision_handler import VisionHandler

pytestmark = pytest.mark.asyncio

GROQ_VISION = "qwen/qwen3.8-27b"


def _jpeg(width: int = 32, height: int = 24, color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _data_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as image:
        return image.size


# ---------------------------------------------------------------- fakes for the llm_text tiers


class Providers:
    """Scripted Gemini / Groq / OpenRouter answers; records every request (model and image)."""

    def __init__(self) -> None:
        self.gemini: list[Any] = []      # per call: an exception or answer text
        self.groq: Any = "groq answer"
        self.openrouter: Any = "openrouter answer"
        self.calls: list[tuple[str, str]] = []
        self.images: list[bytes] = []

    def gemini_client(self, api_key: str, timeout: float, tls: Any) -> Any:
        assert api_key == "test-google-key"

        async def generate_content(*, model: str, contents: Any, config: Any) -> Any:
            self.calls.append(("gemini", model))
            self.images.append(contents[0].inline_data.data)
            outcome = self.gemini.pop(0) if self.gemini else RuntimeError("503 UNAVAILABLE")
            if isinstance(outcome, BaseException):
                raise outcome
            return SimpleNamespace(text=outcome, candidates=[], prompt_feedback=None)

        return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                                                   aclose=AsyncMock()),
                               close=MagicMock())

    def openai_client(self, base_url: str, api_key: str, timeout: float, tls: Any,
                      headers: dict[str, str] | None = None) -> Any:
        provider = "groq" if "groq" in base_url else "openrouter"

        async def create(*, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            self.calls.append((provider, model))
            url = messages[-1]["content"][1]["image_url"]["url"]
            self.images.append(base64.b64decode(url.split(",", 1)[1]))
            outcome = self.groq if provider == "groq" else self.openrouter
            if isinstance(outcome, BaseException):
                raise outcome
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                               close=AsyncMock())


@pytest.fixture
def providers(monkeypatch) -> Providers:
    fake = Providers()
    monkeypatch.setattr(llm_text, "_gemini_client", fake.gemini_client)
    monkeypatch.setattr(llm_text, "_openai_client", fake.openai_client)
    monkeypatch.setattr(llm_text, "_shared_tls_context", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_text, "_dead_models", set())
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    for name in ("JARVIS_VISION_MODEL", "JARVIS_GROQ_VISION_MODEL", "JARVIS_OPENROUTER_MODEL",
                 "JARVIS_FRAME_MAX_AGE_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jarvis_vision.vision_system, "model_name", "gemini-3.8-flash")
    return fake


@pytest.fixture
def vision_log(monkeypatch) -> MagicMock:
    log = MagicMock()
    monkeypatch.setattr(jarvis_vision, "logger", log)
    return log


@pytest.fixture
def screen(monkeypatch) -> Image.Image:
    """pyautogui returns a 2400x1200 screenshot."""
    shot = Image.new("RGB", (2400, 1200), "blue")
    monkeypatch.setattr(jarvis_vision, "pyautogui", SimpleNamespace(
        screenshot=lambda: shot, ImageNotFoundException=type("ImageNotFound", (Exception,), {})))
    return shot


@pytest.fixture
def frames(monkeypatch):
    """The global vision_system starts without a UI frame and is restored afterwards."""
    monkeypatch.setattr(jarvis_vision.vision_system, "_latest_frame", None)
    monkeypatch.setattr(jarvis_vision.vision_system, "latest_frame_ts", None)
    return jarvis_vision.vision_system


class FakeCv2:
    """OpenCV stand-in: camera 0 returns one green 64x48 frame."""
    COLOR_BGR2RGB = 4

    def __init__(self, opened: bool = True) -> None:
        self.opened = opened
        self.reads = 0

    def VideoCapture(self, index: int) -> Any:  # noqa: N802 (cv2's name)
        assert index == 0
        cv2 = self

        class Capture:
            def isOpened(self) -> bool:  # noqa: N802
                return cv2.opened

            def read(self) -> tuple[bool, Any]:
                cv2.reads += 1
                return True, np.full((48, 64, 3), (0, 255, 0), dtype=np.uint8)

            def release(self) -> None:
                pass

        return Capture()

    def cvtColor(self, frame: Any, code: int) -> Any:  # noqa: N802
        assert code == self.COLOR_BGR2RGB
        return frame


def _no_cv2(monkeypatch) -> None:
    def import_module(name: str) -> Any:
        raise NotImplementedError(f"no {name} on this host")
    monkeypatch.setattr(jarvis_vision, "cv2", None)
    monkeypatch.setattr(jarvis_vision, "importlib", SimpleNamespace(import_module=import_module))


# ---------------------------------------------------------------- provider chain (steps 1-3, 7)


def test_default_model_is_the_current_gemini_not_the_404ing_one(monkeypatch):
    monkeypatch.delenv("JARVIS_VISION_MODEL", raising=False)
    assert jarvis_vision.ScreenPerceiver().model_name == "gemini-3.8-flash"
    monkeypatch.setenv("JARVIS_VISION_MODEL", "gemini-3.5-flash")
    assert jarvis_vision.ScreenPerceiver().model_name == "gemini-3.5-flash"
    assert jarvis_vision.ScreenPerceiver("gemini-x").model_name == "gemini-x"


async def test_gemini_fails_and_groq_answers_the_screen(providers, vision_log, screen):
    providers.gemini = [RuntimeError("429 RESOURCE_EXHAUSTED")]
    providers.groq = "A code editor with a test file."

    result = await jarvis_vision.analyze_screen("meri screen par kya hai?")

    assert result["status"] == "success"
    assert "A code editor with a test file." in result["message"]
    assert providers.calls == [("gemini", "gemini-3.8-flash"), ("groq", GROQ_VISION)]
    vision_log.info.assert_any_call("Vision answered: provider=%s model=%s in %.1fs", "groq", GROQ_VISION,
                                    pytest.approx(0, abs=30))
    # Step 3 (2026-10-06): the 2400x1200 screenshot reached the providers as a JPEG whose longest
    # side is capped at VISION_MAX_SIDE (1024) px.
    assert all(image.startswith(b"\xff\xd8\xff") and max(_size(image)) == 1024 for image in providers.images)


async def test_a_404_tries_the_next_gemini_model_and_logs_it(providers, vision_log, screen):
    not_found = genai_errors.ClientError(404, {"error": {"code": 404, "message": "model not found",
                                                         "status": "NOT_FOUND"}})
    providers.gemini = [not_found, "Gemini sees a browser."]

    result = await jarvis_vision.analyze_screen("what is on my screen?")

    assert result["status"] == "success" and "Gemini sees a browser." in result["message"]
    assert providers.calls == [("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.5-flash")]
    vision_log.info.assert_any_call("Vision answered: provider=%s model=%s in %.1fs", "gemini",
                                    "gemini-3.5-flash", pytest.approx(0, abs=30))


async def test_without_a_google_key_the_screen_is_answered_by_groq(providers, screen, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY")
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "success" and "groq answer" in result["message"]
    assert providers.calls == [("groq", GROQ_VISION)]


async def test_a_groq_not_found_error_no_longer_escapes_and_openrouter_is_tried(providers, screen, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", "example/paid-vision-model")
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    providers.groq = openai.NotFoundError("model not found", response=httpx.Response(404, request=request),
                                          body=None)
    providers.openrouter = "OpenRouter sees a terminal."

    result = await jarvis_vision.analyze_screen()

    assert result["status"] == "success" and "OpenRouter sees a terminal." in result["message"]
    assert [call[0] for call in providers.calls] == ["gemini", "groq", "openrouter"]


async def test_analyze_via_groq_turns_an_api_error_into_an_error_string(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    error = openai.NotFoundError("gone", response=httpx.Response(404, request=request), body=None)
    monkeypatch.setattr(llm_text, "generate_vision", AsyncMock(side_effect=error))
    answer = await jarvis_vision.ScreenPerceiver().analyze_via_groq("q", _jpeg())
    assert answer.startswith("Error: Groq failed")


async def test_images_never_go_to_a_free_openrouter_model(providers, screen, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")  # default model: openrouter/free
    providers.groq = RuntimeError("503")
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "error"
    assert [call[0] for call in providers.calls] == ["gemini", "groq"]
    assert "':free'" in result["message"]


async def test_every_provider_failing_gives_vision_providers_failed(providers, screen, frames):
    providers.groq = RuntimeError("503 Service Unavailable")

    screen_result = await jarvis_vision.analyze_screen()
    assert screen_result["status"] == "error"
    assert screen_result["message"].startswith("Vision providers failed: ")
    assert "gemini" in screen_result["message"] and "groq" in screen_result["message"]

    frames.update_webcam_frame(_data_url(_jpeg()), ts=time.time() * 1000)
    camera_result = await jarvis_vision.analyze_camera()
    assert camera_result["status"] == "error"
    assert camera_result["message"].startswith("Vision providers failed: ")


async def test_analyze_image_raises_with_every_reason(providers):
    providers.groq = RuntimeError("down")
    with pytest.raises(LLMUnavailableError) as caught:
        await jarvis_vision.vision_system.analyze_image("q", _jpeg())
    reasons = " ".join(caught.value.reasons)
    assert "gemini" in reasons and "groq" in reasons and "openrouter" in reasons


async def test_secrets_never_reach_the_error_message(providers, screen):
    providers.gemini = [RuntimeError("bad key test-google-key")]
    providers.groq = RuntimeError("Authorization: Bearer gsk_" + "a" * 40)
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "error"
    assert "gsk_" + "a" * 40 not in result["message"]


def test_the_vision_tools_get_60_seconds():
    assert plugin_manager.get_tool_meta("analyze_screen")["timeout"] == 60.0
    assert plugin_manager.get_tool_meta("analyze_camera")["timeout"] == 60.0


async def test_three_hanging_tiers_still_fit_the_60_second_tool_budget(monkeypatch):
    # Each tier is bounded by VISION_TIMEOUT_S and a hanging Gemini gets exactly one extra short
    # retry (2026-10-06), so the worst case still ends in 'Vision providers failed' before the 60 s
    # plugin timeout turns it into TOOL_TIMEOUT.
    assert 3 * jarvis_vision.VISION_TIMEOUT_S + jarvis_vision.VISION_RETRY_TIMEOUT_S < 60.0
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    spy = AsyncMock(side_effect=LLMUnavailableError(["gemini[gemini-3.8-flash]: timeout after 18s"]))
    monkeypatch.setattr(llm_text, "generate_vision", spy)
    with pytest.raises(LLMUnavailableError):
        await jarvis_vision.ScreenPerceiver("gemini-3.8-flash").analyze_image("q", _jpeg())
    # The Gemini tier retried exactly once (short budget); the provider order is unchanged.
    assert [call.kwargs["providers"] for call in spy.await_args_list] == [
        ("gemini",), ("gemini",), ("groq",), ("openrouter",)]
    assert [call.kwargs["timeout"] for call in spy.await_args_list] == [
        jarvis_vision.VISION_TIMEOUT_S, jarvis_vision.VISION_RETRY_TIMEOUT_S,
        jarvis_vision.VISION_TIMEOUT_S, jarvis_vision.VISION_TIMEOUT_S]


async def test_a_gemini_answer_that_starts_with_error_is_kept(providers, screen):
    providers.gemini = ["Error message box saying the disk is full."]
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "success" and "Error message box saying the disk is full." in result["message"]
    assert providers.calls == [("gemini", "gemini-3.8-flash")]


async def test_a_groq_answer_that_starts_with_error_is_kept(providers, screen):
    providers.gemini = [RuntimeError("503 UNAVAILABLE")]
    providers.groq = "Error dialog: file not found."
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "success" and "Error dialog: file not found." in result["message"]
    assert [call[0] for call in providers.calls] == ["gemini", "groq"]


async def test_a_groq_wrapper_error_string_still_moves_on_to_openrouter(providers, screen, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", "example/paid-vision-model")
    monkeypatch.delenv("GROQ_API_KEY")  # analyze_via_groq answers "Error: Groq client not initialized."
    providers.openrouter = "OpenRouter sees a terminal."
    result = await jarvis_vision.analyze_screen()
    assert result["status"] == "success" and "OpenRouter sees a terminal." in result["message"]
    assert [call[0] for call in providers.calls] == ["gemini", "openrouter"]


def test_prepare_image_downscales_and_keeps_small_jpegs():
    small = _jpeg(800, 600)
    assert jarvis_vision.prepare_image(small) == small  # a JPEG within 1024 px passes unchanged
    big = jarvis_vision.prepare_image(Image.new("RGBA", (3200, 1000)))
    with Image.open(BytesIO(big)) as image:
        assert image.format == "JPEG" and image.size == (1024, 320)
    png = BytesIO()
    Image.new("RGB", (100, 50)).save(png, format="PNG")
    assert jarvis_vision.prepare_image(png.getvalue()).startswith(b"\xff\xd8\xff")


def test_prepare_image_caps_the_longest_side_of_a_portrait_frame():
    # 2026-10-06: a 1080x1920 portrait camera frame slipped through the old 1600 px WIDTH cap and hit
    # Groq's 'Request too large for model' 429; the cap is the LONGEST side now.
    portrait = _jpeg(1080, 1920)
    prepared = jarvis_vision.prepare_image(portrait)
    assert prepared != portrait
    assert _size(prepared) == (576, 1024)


def test_a_large_noisy_frame_shrinks_well_under_the_groq_request_limit():
    # A noisy full-HD JPEG (least compressible content) must come out far below the request size
    # that drew Groq's 'Request too large for model' 429 during the dashboard test.
    rng = np.random.default_rng(42)
    noise = Image.fromarray(rng.integers(0, 256, size=(1080, 1920, 3), dtype=np.uint8))
    buffer = BytesIO()
    noise.save(buffer, format="JPEG", quality=95)
    source = buffer.getvalue()

    prepared = jarvis_vision.prepare_image(source)

    assert max(_size(prepared)) == 1024
    assert len(prepared) < len(source)
    assert len(prepared) < 1_000_000  # ~250 KB base64-encodes to well under Groq's request cap


# ---------------------------------------------------------------- Gemini transient retry (2026-10-06)


@pytest.mark.parametrize("blip", [
    TimeoutError(),
    ConnectionError("Cannot connect to host generativelanguage.googleapis.com:443 ssl:default "
                    "[getaddrinfo failed]"),
])
async def test_a_gemini_timeout_or_dns_blip_is_retried_once_and_succeeds(providers, screen, blip):
    providers.gemini = [blip, "Gemini sees the desk after the retry."]

    result = await jarvis_vision.analyze_screen("kya hai?")

    assert result["status"] == "success" and "Gemini sees the desk after the retry." in result["message"]
    # Exactly one retry, still on the Gemini tier; Groq was never needed.
    assert providers.calls == [("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.8-flash")]


async def test_the_gemini_retry_fires_only_once_then_the_chain_moves_on(providers, screen):
    providers.gemini = [TimeoutError(), TimeoutError(), "never reached"]
    providers.groq = "Groq answers after two Gemini timeouts."

    result = await jarvis_vision.analyze_screen()

    assert result["status"] == "success" and "Groq answers after two Gemini timeouts." in result["message"]
    assert providers.calls == [("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.8-flash"),
                               ("groq", GROQ_VISION)]
    assert providers.gemini == ["never reached"]  # no second retry


async def test_a_non_transient_gemini_failure_is_not_retried(providers, screen):
    providers.gemini = [RuntimeError("400 INVALID_ARGUMENT"), "never reached"]
    providers.groq = "Groq takes over at once."

    result = await jarvis_vision.analyze_screen()

    assert result["status"] == "success" and "Groq takes over at once." in result["message"]
    assert providers.calls == [("gemini", "gemini-3.8-flash"), ("groq", GROQ_VISION)]


# ---------------------------------------------------------------- fresh frames only (step 4)


def test_frame_timestamps_from_the_ui(monkeypatch):
    now = 1_700_000_000.0
    assert jarvis_vision.frame_timestamp(now * 1000 - 3000, received_at=now) == pytest.approx(now - 3)
    assert jarvis_vision.frame_timestamp(now - 5, received_at=now) == pytest.approx(now - 5)
    assert jarvis_vision.frame_timestamp(None, received_at=now) == now
    assert jarvis_vision.frame_timestamp("junk", received_at=now) == now
    assert jarvis_vision.frame_timestamp(True, received_at=now) == now
    # A sender clock ahead of ours cannot keep a frame fresh forever.
    assert jarvis_vision.frame_timestamp((now + 3600) * 1000, received_at=now) == now
    monkeypatch.setenv("JARVIS_FRAME_MAX_AGE_S", "4")
    assert jarvis_vision.frame_max_age_s() == 4.0
    assert jarvis_vision.is_fresh(now - 3, now=now) and not jarvis_vision.is_fresh(now - 5, now=now)
    monkeypatch.setenv("JARVIS_FRAME_MAX_AGE_S", "nonsense")
    assert jarvis_vision.frame_max_age_s() == 10.0


async def test_update_webcam_frame_stores_the_ui_timestamp(frames):
    ui_frame = _jpeg()
    frames.update_webcam_frame(_data_url(ui_frame), ts=(time.time() - 2) * 1000)
    assert frames.latest_frame == ui_frame
    assert time.time() - frames.latest_frame_ts == pytest.approx(2, abs=1)
    assert await frames.capture_webcam() == ui_frame


async def test_a_stale_ui_frame_takes_the_cv2_path(frames, monkeypatch):
    stale = _jpeg(color="red")
    frames.update_webcam_frame(_data_url(stale), ts=(time.time() - 30) * 1000)
    cv2 = FakeCv2()
    monkeypatch.setattr(jarvis_vision, "cv2", cv2)

    data = await frames.capture_webcam()

    assert cv2.reads == 1 and data != stale
    with Image.open(BytesIO(data)) as image:
        assert image.size == (64, 48) and image.getpixel((5, 5))[1] > 200  # the green cv2 frame


async def test_analyze_camera_never_sends_a_stale_frame(frames, monkeypatch):
    stale = _jpeg(color="red")
    frames.update_webcam_frame(_data_url(stale), ts=(time.time() - 11) * 1000)
    monkeypatch.setattr(jarvis_vision, "cv2", FakeCv2())
    analyze = AsyncMock(return_value="A green wall.")
    monkeypatch.setattr(frames, "analyze_image", analyze)

    result = await jarvis_vision.analyze_camera("camera mein kya dikh raha hai?")

    assert result["status"] == "success" and "A green wall." in result["message"]
    sent = analyze.await_args.args[1]
    assert sent != stale


async def test_a_stale_frame_without_a_local_camera_means_camera_off(frames, monkeypatch):
    frames.update_webcam_frame(_data_url(_jpeg()), ts=(time.time() - 60) * 1000)
    _no_cv2(monkeypatch)
    analyze = AsyncMock(return_value="must not run")
    monkeypatch.setattr(frames, "analyze_image", analyze)

    with pytest.raises(OSError, match="Camera frame purana hai ya camera off hai"):
        await frames.capture_webcam()
    result = await jarvis_vision.analyze_camera()

    assert result["status"] == "error" and "purana" in result["message"]
    analyze.assert_not_awaited()


async def test_a_closed_local_camera_after_a_stale_frame_means_camera_off(frames, monkeypatch):
    frames.update_webcam_frame(_data_url(_jpeg()), ts=(time.time() - 60) * 1000)
    monkeypatch.setattr(jarvis_vision, "cv2", FakeCv2(opened=False))
    with pytest.raises(OSError, match="camera off"):
        await frames.capture_webcam()


async def test_without_any_frame_or_opencv_the_camera_is_unavailable(frames, monkeypatch):
    _no_cv2(monkeypatch)
    result = await jarvis_vision.analyze_camera()
    assert result["status"] == "unavailable"


async def test_a_frame_set_directly_counts_as_received_now(frames, monkeypatch):
    _no_cv2(monkeypatch)
    frames.latest_frame = b"jpeg"
    assert await frames.capture_webcam() == b"jpeg"
    frames.latest_frame_ts = time.time() - 11
    with pytest.raises(OSError, match="purana"):
        await frames.capture_webcam()


# ---------------------------------------------------------------- agent_loops.on_vision_frame (step 5)


async def test_on_vision_frame_passes_the_ui_timestamp(frames):
    handler = VisionHandler(MagicMock(), mode="remote")
    assistant = SimpleNamespace(vision_handler=handler, _active_session=None)
    listener = agent_loops.UIBridgeListener(assistant)  # type: ignore[arg-type]
    sent_at_ms = (time.time() - 4) * 1000
    url = _data_url(_jpeg())

    await listener.on_vision_frame({"frame": url, "ts": sent_at_ms})

    assert handler.last_vision_frame == url
    assert handler.last_vision_frame_ts == pytest.approx(sent_at_ms / 1000)
    assert frames.latest_frame_ts == pytest.approx(sent_at_ms / 1000)


async def test_on_vision_frame_without_a_handler_stamps_the_assistant(frames):
    assistant = SimpleNamespace(_active_session=None)
    listener = agent_loops.UIBridgeListener(assistant)  # type: ignore[arg-type]
    await listener.on_vision_frame(_data_url(_jpeg()))  # a legacy plain frame: arrival time
    assert time.time() - assistant.last_vision_frame_ts < 5
    assert time.time() - frames.latest_frame_ts < 5


# ---------------------------------------------------------------- VisionHandler.analyze_current_frame (step 6)


@pytest.fixture
def handler_vision(monkeypatch) -> SimpleNamespace:
    fake = SimpleNamespace(analyze_image=AsyncMock(return_value="A desk with a laptop."),
                           capture_webcam=AsyncMock(return_value=_jpeg(color="green")))
    monkeypatch.setattr(vision_handler, "vision_system", fake)
    return fake


async def test_the_fresh_socket_frame_is_analyzed(handler_vision):
    handler = VisionHandler(MagicMock(), mode="remote")
    frame = _jpeg(color="red")
    handler.on_vision_frame(_data_url(frame), ts=time.time() * 1000)

    assert await handler.analyze_current_frame("q") == "A desk with a laptop."
    assert handler_vision.analyze_image.await_args.args == ("q", frame)
    handler_vision.capture_webcam.assert_not_awaited()


async def test_a_stale_socket_frame_is_replaced_by_capture_webcam(handler_vision):
    handler = VisionHandler(MagicMock(), mode="remote")
    stale = _jpeg(color="red")
    handler.on_vision_frame(_data_url(stale), ts=(time.time() - 30) * 1000)

    await handler.analyze_current_frame("q")

    handler_vision.capture_webcam.assert_awaited_once()
    assert handler_vision.analyze_image.await_args.args[1] != stale


async def test_the_livekit_frame_wins_when_it_is_fresh(handler_vision):
    handler = VisionHandler(MagicMock(), mode="remote")
    livekit = _jpeg(color="blue")
    handler.on_vision_frame(_data_url(_jpeg(color="red")), ts=(time.time() - 2) * 1000)
    assert handler.on_livekit_frame(livekit, ts=time.time() * 1000)
    assert not handler.on_livekit_frame(_jpeg(color="white"), ts=time.time() * 1000)  # 1 frame a second

    await handler.analyze_current_frame("q")
    assert handler_vision.analyze_image.await_args.args[1] == livekit

    handler.last_livekit_frame_ts = time.time() - 60  # a stale LiveKit frame is skipped too
    await handler.analyze_current_frame("q")
    assert handler_vision.analyze_image.await_args.args[1] != livekit


async def test_failures_return_a_string_starting_with_error(handler_vision):
    handler = VisionHandler(MagicMock(), mode="remote")
    handler_vision.capture_webcam.side_effect = OSError("Camera frame purana hai ya camera off hai")
    no_camera = await handler.analyze_current_frame("q")
    assert no_camera.startswith("Error") and "purana" in no_camera
    handler_vision.analyze_image.assert_not_awaited()

    handler.on_vision_frame(_data_url(_jpeg()), ts=time.time() * 1000)
    handler_vision.analyze_image.side_effect = LLMUnavailableError(["gemini: 503", "groq: 503"])
    failed = await handler.analyze_current_frame("q")
    assert failed.startswith("Error: Vision providers failed: ") and "groq: 503" in failed


async def test_a_stale_frame_is_not_injected_into_the_chat():
    handler = VisionHandler(SimpleNamespace(), mode="remote")
    handler.on_vision_frame(_data_url(_jpeg()), ts=(time.time() - 30) * 1000)
    message = SimpleNamespace(content=["camera dekh kar batao"])
    turn_ctx = SimpleNamespace(chat_ctx=SimpleNamespace(messages=[]))

    await handler.handle_vision_query("camera dekh kar batao", message, turn_ctx)

    assert message.content == ["camera dekh kar batao"] and turn_ctx.chat_ctx.messages == []


async def test_an_assistant_frame_without_a_timestamp_ages_out(monkeypatch):
    assistant = SimpleNamespace(last_vision_frame=_data_url(_jpeg()))
    handler = VisionHandler(assistant, mode="remote")
    assert handler.freshest_frame() is not None  # first seen now
    seen_key, _ = handler._assistant_frame_seen
    handler._assistant_frame_seen = (seen_key, time.time() - 30)
    assert handler.freshest_frame() is None


# ---------------------------------------------------------------- analyze_surroundings (step 8)


async def test_analyze_surroundings_returns_error_status_on_failure():
    brain = SimpleNamespace(vision_handler=SimpleNamespace(
        analyze_current_frame=AsyncMock(return_value="Error: Vision providers failed: gemini: 503")))
    result = await agent_core.BrainAssistant.tool_analyze_surroundings(brain, "kya dikh raha hai?")
    assert result["status"] == "error"
    assert "Vision providers failed" in result["message"]


async def test_analyze_surroundings_success():
    brain = SimpleNamespace(vision_handler=SimpleNamespace(
        analyze_current_frame=AsyncMock(return_value="A desk with a laptop.")))
    result = await agent_core.BrainAssistant.tool_analyze_surroundings(brain)
    assert result["status"] == "success" and result["analysis"] == "A desk with a laptop."


async def test_the_no_vision_stand_in_is_an_error_too():
    brain = SimpleNamespace(vision_handler=agent_core._NoVisionHandler())
    result = await agent_core.BrainAssistant.tool_analyze_surroundings(brain)
    assert result["status"] == "error" and "available nahi" in result["message"]


async def test_a_camera_answer_about_an_error_dialog_stays_a_success(handler_vision):
    handler_vision.analyze_image.return_value = "Error 404 page is open on the laptop screen."
    brain = SimpleNamespace(vision_handler=VisionHandler(MagicMock(), mode="remote"))
    result = await agent_core.BrainAssistant.tool_analyze_surroundings(brain)
    assert result["status"] == "success"
    assert result["analysis"] == "Error 404 page is open on the laptop screen."

    handler_vision.capture_webcam.side_effect = OSError("Camera frame purana hai ya camera off hai")
    failed = await agent_core.BrainAssistant.tool_analyze_surroundings(brain)
    assert failed["status"] == "error" and "purana" in failed["message"]


def test_analysis_failed_judges_recorded_strings_by_their_record():
    answer = jarvis_vision.record_analysis("Error dialog: disk full.", failed=False)
    assert not jarvis_vision.analysis_failed(answer)
    failure = jarvis_vision.record_analysis("Error: Vision analysis failed: boom", failed=True)
    assert jarvis_vision.analysis_failed(failure)
    # A string that was never recorded (an older or patched caller) falls back to the 'Error' prefix.
    assert jarvis_vision.analysis_failed("Error: something else")
    assert not jarvis_vision.analysis_failed("A desk with a laptop.")


async def test_analyze_surroundings_gets_a_60_second_breaker(monkeypatch):
    monkeypatch.setattr("livekit.agents.Agent.__init__", lambda self, **kwargs: None)
    monkeypatch.setattr(agent_core.jarvis_id, "get_context", lambda: "identity")
    monkeypatch.setattr(agent_core, "JarvisPluginManager", MagicMock())
    monkeypatch.setattr(agent_core, "MemoryExtractor", MagicMock())
    monkeypatch.setattr(agent_core, "task_manager", MagicMock(get_due_tasks=MagicMock(return_value=[])))
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")  # no task monitor, no window poller
    breaker = resilience_manager.get_breaker("analyze_surroundings")
    monkeypatch.setattr(breaker, "execution_timeout", 15.0)

    brain = agent_core.BrainAssistant(chat_ctx=MagicMock())

    assert resilience_manager.get_breaker("analyze_surroundings") is breaker
    assert breaker.execution_timeout == 60.0
    await brain.vision_handler.stop_loop()
