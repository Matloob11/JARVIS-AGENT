"""X-40: HF FLUX provider path and Pollinations fallback/error handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from services.multimedia import jarvis_image_gen as img_mod
from services.multimedia.jarvis_image_gen import JarvisImageGenerator


@pytest.fixture
def env_hf(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_token_12345678")
    monkeypatch.setenv("JARVIS_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
    monkeypatch.setenv("JARVIS_IMAGE_PROVIDER", "auto")


@pytest.mark.asyncio
async def test_flux_hf_success(env_hf):
    mock_img = MagicMock()
    mock_client = MagicMock()
    mock_client.text_to_image.return_value = mock_img

    with patch.object(img_mod, "InferenceClient", return_value=mock_client):
        with patch("os.makedirs"):
            with patch.object(
                JarvisImageGenerator, "_lan_image_url", return_value="http://127.0.0.1:8000/x.png"
            ):
                with patch.object(
                    JarvisImageGenerator, "_emit_image_update", new_callable=AsyncMock
                ):
                    gen = JarvisImageGenerator()
                    res = await gen.generate_image("Lahore sunset")

    assert res["status"] == "success"
    assert res["model"] == "black-forest-labs/FLUX.1-schnell"
    assert res["provider"] == "auto"
    mock_client.text_to_image.assert_called_once_with(
        "Lahore sunset", model="black-forest-labs/FLUX.1-schnell"
    )
    mock_img.save.assert_called_once()


@pytest.mark.asyncio
async def test_no_provider_mapping_falls_back_to_pollinations(env_hf):
    mock_client = MagicMock()
    mock_client.text_to_image.side_effect = ValueError("No provider mapping for model")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "image/jpeg"}
    mock_resp.content = b"jpeg-bytes"

    with patch.object(img_mod, "InferenceClient", return_value=mock_client):
        with patch("os.makedirs"):
            with patch("requests.get", return_value=mock_resp):
                with patch("builtins.open", mock_open()):
                    with patch.object(
                        JarvisImageGenerator, "_lan_image_url", return_value="http://127.0.0.1:8000/p.jpg"
                    ):
                        with patch.object(
                            JarvisImageGenerator, "_emit_image_update", new_callable=AsyncMock
                        ):
                            gen = JarvisImageGenerator()
                            res = await gen.generate_image("test prompt")

    assert res["status"] == "success"
    assert res["provider"] == "pollinations"


@pytest.mark.asyncio
async def test_pollinations_http_402_returns_error_with_code(env_hf):
    mock_client = MagicMock()
    mock_client.text_to_image.side_effect = RuntimeError("HF credits exhausted")

    mock_resp = MagicMock()
    mock_resp.status_code = 402
    mock_resp.headers = {"Content-Type": "text/plain"}

    with patch.object(img_mod, "InferenceClient", return_value=mock_client):
        with patch("os.makedirs"):
            with patch("requests.get", return_value=mock_resp):
                gen = JarvisImageGenerator()
                res = await gen.generate_image("paid only")

    assert res["status"] == "error"
    assert "402" in res["message"]
    assert res["provider"] == "pollinations"


def test_no_hf_token_skips_inference_client(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with patch.object(img_mod, "InferenceClient") as mock_cls:
        with patch("os.makedirs"):
            gen = JarvisImageGenerator()
    assert gen.client is None
    mock_cls.assert_not_called()
