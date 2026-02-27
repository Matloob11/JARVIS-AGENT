import pytest
import os
import requests
from unittest.mock import MagicMock, patch, mock_open
from jarvis_image_gen import (
    generate_via_hf,
    generate_via_pollinations,
    generate_image,
    tool_generate_image
)


@pytest.fixture
def mock_hf_client():
    with patch("jarvis_image_gen.InferenceClient") as mock:
        yield mock


@pytest.fixture
def mock_requests():
    with patch("jarvis_image_gen.requests") as mock:
        yield mock


def test_generate_via_hf_success(mock_hf_client):
    with patch("os.getenv", return_value="fake_token"):
        with patch("os.makedirs"):
            with patch("os.startfile"):
                mock_client_inst = mock_hf_client.return_value
                mock_img = MagicMock()
                mock_client_inst.text_to_image.return_value = mock_img

                res = generate_via_hf("test prompt")
                assert "Success" in res
                mock_img.save.assert_called()


def test_generate_via_hf_no_token():
    with patch("os.getenv", return_value=None):
        res = generate_via_hf("test prompt")
        assert res is None


def test_generate_via_pollinations_success(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'\x89PNG\r\n\x1a\n'  # Valid PNG header
    mock_requests.get.return_value = mock_resp
    mock_requests.utils.quote = requests.utils.quote

    with patch("os.makedirs"):
        with patch("builtins.open", mock_open()):
            with patch("os.startfile"):
                res = generate_via_pollinations("test prompt")
                assert "Success" in res


def test_generate_image_hf_first(mock_hf_client):
    with patch("jarvis_image_gen.generate_via_hf", return_value="Success HF"):
        res = generate_image("test prompt")
        assert res['status'] == "success"
        assert "Success HF" in res['message']


def test_generate_image_fallback_poll(mock_hf_client):
    with patch("jarvis_image_gen.generate_via_hf", return_value=None):
        with patch("jarvis_image_gen.generate_via_pollinations", return_value="Success Poll"):
            res = generate_image("test prompt")
            assert res['status'] == "success"
            assert "Success Poll" in res['message']


@pytest.mark.asyncio
async def test_tool_generate_image():
    with patch("jarvis_image_gen.generate_image", return_value={"status": "success", "message": "Done"}) as mock_gen:
        res = await tool_generate_image("test")
        assert res['status'] == "success"
        mock_gen.assert_called_once_with("test")
