"""X-36: search chain hardening (mocked HTTP + DDGS)."""

import pytest
from unittest.mock import MagicMock, patch

import services.info.jarvis_search as js


@pytest.fixture(autouse=True)
def reset_search_providers():
    js._tavily_disabled_until = 0.0
    js._GOOGLE_DISABLED_REASON = None
    yield


@pytest.mark.asyncio
async def test_tavily_uses_bearer_not_body_key():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [{"title": "T1", "content": "C1", "url": "https://example.com/1"}]
    }
    mock_response.raise_for_status = MagicMock()

    secret = "TAVILY_TEST_STUB_KEY_001"
    with patch.object(js, "TAVILY_API_KEY", secret):
        with patch("services.info.jarvis_search.requests.post", return_value=mock_response) as post:
            result = await js.search_tavily("pakistan cricket")
            assert result["status"] == "success"
            assert result["provider"] == "tavily"
            _, kwargs = post.call_args
            assert "api_key" not in kwargs.get("json", {})
            assert kwargs["headers"]["Authorization"] == f"Bearer {secret}"
            assert secret not in result["message"]


@pytest.mark.asyncio
async def test_tavily_401_disables_and_chain_falls_through():
    tavily_resp = MagicMock()
    tavily_resp.status_code = 401
    tavily_resp.raise_for_status.side_effect = js.requests.HTTPError(response=tavily_resp)

    ddg_instance = MagicMock()
    ddg_instance.text.return_value = [{"title": "D1", "body": "B1", "href": "https://d.example/1"}]
    ddg_instance.__enter__.return_value = ddg_instance

    secret = "TAVILY_TEST_STUB_KEY_001"
    with patch.object(js, "TAVILY_API_KEY", secret):
        with patch.object(js, "GOOGLE_SEARCH_API_KEY", None):
            with patch("services.info.jarvis_search.requests.post", return_value=tavily_resp):
                with patch("services.info.jarvis_search.DDGS", return_value=ddg_instance):
                    result = await js.search_internet("latest news")
                    assert result["status"] == "success"
                    assert result["provider"] == "duckduckgo"
                    assert "tavily" in result["providers_tried"]
                    assert js._tavily_disabled_until > 0


@pytest.mark.asyncio
async def test_google_403_disables_after_one_attempt():
    google_resp = MagicMock()
    google_resp.status_code = 403
    google_resp.raise_for_status.side_effect = js.requests.HTTPError(response=google_resp)

    ddg_instance = MagicMock()
    ddg_instance.text.return_value = [{"title": "D2", "body": "B2", "href": "https://d.example/2"}]
    ddg_instance.__enter__.return_value = ddg_instance

    gkey = "google-search-api-key-abcdefgh"
    with patch.object(js, "TAVILY_API_KEY", None):
        with patch.object(js, "GOOGLE_SEARCH_API_KEY", gkey):
            with patch.object(js, "SEARCH_ENGINE_ID", "cx-123"):
                with patch("services.info.jarvis_search.requests.get", return_value=google_resp) as get:
                    with patch("services.info.jarvis_search.DDGS", return_value=ddg_instance):
                        first = await js.search_internet("query one")
                        assert first["provider"] == "duckduckgo"
                        assert get.call_count == 1
                        assert js._GOOGLE_DISABLED_REASON is not None

                        second = await js.search_internet("query two")
                        assert second["provider"] == "duckduckgo"
                        assert get.call_count == 1
                        url_used = get.call_args[0][0]
                        assert "key=" not in url_used
                        assert gkey not in second["message"]


@pytest.mark.asyncio
async def test_google_key_in_header_not_url():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "items": [{"title": "G1", "snippet": "S1", "link": "https://g.example/1"}]
    }
    mock_response.raise_for_status = MagicMock()

    gkey = "google-search-api-key-abcdefgh"
    with patch.object(js, "GOOGLE_SEARCH_API_KEY", gkey):
        with patch.object(js, "SEARCH_ENGINE_ID", "cx-999"):
            with patch("services.info.jarvis_search.requests.get", return_value=mock_response) as get:
                result = await js.search_google("test")
                assert result["status"] == "success"
                url_used = get.call_args[0][0]
                assert "key=" not in url_used
                assert get.call_args[1]["headers"]["x-goog-api-key"] == gkey
                assert gkey not in result["message"]


@pytest.mark.asyncio
async def test_search_internet_message_capped_and_providers_tried():
    long_content = "x" * 7000
    with patch.object(js, "TAVILY_API_KEY", "TAVILY_TEST_STUB_KEY_001"):
        with patch(
            "services.info.jarvis_search.search_tavily",
            return_value={
                "status": "success",
                "provider": "tavily",
                "results": [],
                "message": long_content,
            },
        ):
            result = await js.search_internet("cap test")
            assert len(result["message"]) <= js.SEARCH_MESSAGE_MAX_CHARS + len("...[truncated]")
            assert result["providers_tried"] == ["tavily"]


@pytest.mark.asyncio
async def test_search_urls_ddg_fallback():
    ddg_instance = MagicMock()
    ddg_instance.text.return_value = [
        {"title": "U1", "body": "snippet", "href": "https://u.example/1"},
    ]
    ddg_instance.__enter__.return_value = ddg_instance

    with patch.object(js, "TAVILY_API_KEY", None):
        with patch.object(js, "GOOGLE_SEARCH_API_KEY", None):
            with patch("services.info.jarvis_search.DDGS", return_value=ddg_instance):
                rows = await js.search_urls("lahore", count=3)
                assert len(rows) == 1
                assert rows[0]["url"] == "https://u.example/1"


@pytest.mark.asyncio
async def test_search_images_prefers_ddg():
    ddg_instance = MagicMock()
    ddg_instance.images.return_value = [{"image": "https://img.example/a.jpg"}]
    ddg_instance.__enter__.return_value = ddg_instance

    with patch("services.info.jarvis_search.DDGS", return_value=ddg_instance):
        urls = await js.search_images("cats", 2)
        assert urls == ["https://img.example/a.jpg"]
