import pytest
import asyncio
from io import BytesIO
from unittest.mock import MagicMock, patch, AsyncMock
from types import SimpleNamespace
from PIL import Image
import jarvis_vision
from services.ai_core import llm_text
from jarvis_vision import ScreenPerceiver, analyze_screen


@pytest.fixture
def mock_genai():
    with patch("jarvis_vision.genai.Client") as mock:
        yield mock


@pytest.fixture
def mock_pg():
    with patch("jarvis_vision.pyautogui") as mock:
        yield mock


@pytest.fixture
def mock_requests():
    with patch("jarvis_vision.requests") as mock:
        yield mock


@pytest.mark.asyncio
async def test_capture_screen(mock_pg):
    perceiver = ScreenPerceiver()
    mock_img = Image.new('RGB', (100, 100))
    mock_pg.screenshot.return_value = mock_img

    with patch("asyncio.to_thread", side_effect=asyncio.to_thread) as mock_thread:
        res = await perceiver.capture_screen()
        assert isinstance(res, bytes)
        assert len(res) > 0


class _FakeVisionClients:
    """Stand-ins for llm_text's Gemini and OpenAI-compatible clients (C-48: vision goes through K-30
    llm_text.generate_vision, so no real client, key or network request is used)."""

    def __init__(self, gemini_text="", openai_text=""):
        self.gemini_text = gemini_text
        self.openai_text = openai_text
        self.calls = []

    def gemini_client(self, api_key, timeout, tls):
        async def generate_content(*, model, contents, config):
            self.calls.append(("gemini", model))
            return SimpleNamespace(text=self.gemini_text, candidates=[], prompt_feedback=None)

        return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                                                   aclose=AsyncMock()),
                               close=MagicMock())

    def openai_client(self, base_url, api_key, timeout, tls, headers=None):
        provider = "groq" if "groq" in base_url else "openrouter"

        async def create(*, model, messages, **kwargs):
            self.calls.append((provider, model))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.openai_text))])

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                               close=AsyncMock())


@pytest.fixture
def fake_clients(monkeypatch):
    fake = _FakeVisionClients()
    monkeypatch.setattr(llm_text, "_gemini_client", fake.gemini_client)
    monkeypatch.setattr(llm_text, "_openai_client", fake.openai_client)
    monkeypatch.setattr(llm_text, "_shared_tls_context", AsyncMock(return_value=None))
    monkeypatch.setattr(llm_text, "_dead_models", set())
    for name in ("GOOGLE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "JARVIS_VISION_MODEL",
                 "JARVIS_GROQ_VISION_MODEL", "JARVIS_OPENROUTER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return fake


@pytest.mark.asyncio
async def test_analyze_via_google(fake_clients, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    fake_clients.gemini_text = "I see a desktop."
    perceiver = ScreenPerceiver(model_name="gemini-3.8-flash")

    mock_img = Image.new('RGB', (10, 10))
    res = await perceiver.analyze_via_google("test prompt", mock_img)
    assert res == "I see a desktop."
    assert fake_clients.calls == [("gemini", "gemini-3.8-flash")]


@pytest.mark.asyncio
async def test_analyze_via_openrouter(fake_clients, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    # Images never go to a ':free' OpenRouter model, so the fallback needs a paid id.
    monkeypatch.setenv("JARVIS_OPENROUTER_MODEL", "google/gemini-3.8-flash")
    fake_clients.openai_text = "Fallback analysis"
    perceiver = ScreenPerceiver()

    mock_img = Image.new('RGB', (10, 10))
    res = await perceiver.analyze_via_openrouter("test prompt", mock_img)
    assert res == "Fallback analysis"
    assert fake_clients.calls == [("openrouter", "google/gemini-3.8-flash")]


@pytest.mark.asyncio
async def test_analyze_content_success(mock_pg, mock_genai):
    perceiver = ScreenPerceiver()
    mock_pg.screenshot.return_value = Image.new('RGB', (10, 10))

    with patch.object(ScreenPerceiver, 'analyze_via_google', new_callable=AsyncMock) as m_google:
        m_google.return_value = "Gemini result"
        res = await perceiver.analyze_content()
        assert res == "Gemini result"


@pytest.mark.asyncio
async def test_analyze_content_fallback(mock_pg, mock_genai):
    perceiver = ScreenPerceiver()
    mock_pg.screenshot.return_value = Image.new('RGB', (10, 10))

    with patch.object(ScreenPerceiver, 'analyze_via_google', side_effect=ValueError("RESOURCE_EXHAUSTED")):
        with patch.object(ScreenPerceiver, 'analyze_via_groq', new_callable=AsyncMock) as m_groq:
            with patch.object(ScreenPerceiver, 'analyze_via_openrouter', new_callable=AsyncMock) as m_fallback:
                m_groq.return_value = "Error: Groq unavailable"
                m_fallback.return_value = "Fallback result"
                res = await perceiver.analyze_content()
                assert res == "Fallback result"


@pytest.mark.asyncio
async def test_analyze_screen_tool():
    with patch("jarvis_vision.vision_system.analyze_content", new_callable=AsyncMock) as m_analyze:
        m_analyze.return_value = "Everything looks good."
        res = await analyze_screen("What is this?")
        assert res['status'] == "success"
        assert "Everything looks good" in res['message']
