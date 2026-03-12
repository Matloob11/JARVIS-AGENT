import pytest
import os
import asyncio
import requests
from unittest.mock import MagicMock, patch, mock_open, AsyncMock
from services.multimedia.jarvis_image_gen import (
    JarvisImageGenerator,
    generate_image,
    image_gen
)


@pytest.fixture
def mock_hf_client():
    with patch("services.multimedia.jarvis_image_gen.InferenceClient") as mock:
        yield mock


@pytest.fixture
def mock_requests():
    with patch("requests.get") as mock:
        yield mock


def test_generate_image_hf_success(mock_hf_client):
    with patch("os.getenv", return_value="fake_token"):
        with patch("os.makedirs"):
            mock_client_inst = mock_hf_client.return_value
            mock_img = MagicMock()
            mock_client_inst.text_to_image.return_value = mock_img

            # Create generator instance
            gen = JarvisImageGenerator()
            # We need to mock the save method on the returned image
            # Since we can't easily mock the save method on the object returned by text_to_image in a thread
            # we will mock the save call itself if possible or just check it was called.
            
            # Using asyncio.run to test the async method
            res = asyncio.run(gen.generate_image("test prompt"))
            assert res['status'] == "success"
            mock_img.save.assert_called()


def test_generate_image_no_token():
    with patch("os.getenv", return_value=None):
        gen = JarvisImageGenerator()
        assert gen.hf_token is None
        assert gen.client is None


@pytest.mark.asyncio
async def test_generate_image_fallback_poll(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'fake_image_data'
    mock_requests.return_value = mock_resp

    with patch("os.getenv", return_value=None):
        with patch("builtins.open", mock_open()):
            gen = JarvisImageGenerator()
            res = await gen.generate_image("test prompt")
            assert res['status'] == "success"
            assert "fallback" in res['message']


@pytest.mark.asyncio
async def test_global_generate_image():
    with patch("services.multimedia.jarvis_image_gen.image_gen.generate_image", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {"status": "success", "message": "Done"}
        res = await generate_image("test")
        assert res['status'] == "success"
        mock_gen.assert_called_once_with("test", "1:1")
