"""X-62: PinchTab Chrome tools (mocked HTTP, no live PinchTab)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import services.automation.jarvis_browser_pinchtab as pt


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
        self.content = b"1" if payload is not None else b""

    def json(self) -> Any:
        if self._payload is None:
            return {}
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            err = __import__("requests").HTTPError(response=self)
            raise err


@pytest.fixture(autouse=True)
def _reset_session() -> None:
    pt.reset_pinchtab_session_for_tests()
    yield
    pt.reset_pinchtab_session_for_tests()


@pytest.fixture
def mock_config() -> MagicMock:
    cfg = MagicMock()
    cfg.pinchtab_url = "http://127.0.0.1:9867"
    cfg.pinchtab_token = "test-token-abc"
    cfg.pinchtab_bin = r"C:\fake\pinchtab.cmd"
    cfg.pinchtab_autostart = True
    cfg.pinchtab_allowed_domains = ["*"]
    cfg.chrome_path = r"C:\fake\chrome.exe"
    return cfg


def _route(
    responses: dict[tuple[str, str], _FakeResponse],
    *,
    calls: list[dict[str, Any]] | None = None,
) -> MagicMock:
    def _request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if calls is not None:
            calls.append({"method": method, "url": url, **kwargs})
        path = url.replace("http://127.0.0.1:9867", "")
        key = (method.upper(), path.split("?", 1)[0])
        for (m, p), resp in responses.items():
            if m == key[0] and path.startswith(p):
                return resp
        return _FakeResponse(404, {"error": "not mocked"})

    return MagicMock(side_effect=_request)


@pytest.mark.asyncio
async def test_auth_header_on_requests(mock_config: MagicMock) -> None:
    calls: list[dict[str, Any]] = []

    def _request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"method": method, "url": url, **kwargs})
        return _FakeResponse(200, {"status": "ok"})

    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt.requests, "request", side_effect=_request),
    ):
        await pt._api_call("GET", "/health")

    auth = calls[0].get("headers") or {}
    assert auth.get("Authorization") == "Bearer test-token-abc"


@pytest.mark.asyncio
async def test_profile_409_reread(mock_config: MagicMock) -> None:
    profile_reads = {"n": 0}

    async def _fake_api(
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if method == "GET" and path == "/profiles":
            profile_reads["n"] += 1
            if profile_reads["n"] == 1:
                return [{"name": "other", "id": "x"}]
            return [{"name": "jarvis_default", "id": "p99"}]
        if method == "POST" and path == "/profiles":
            return {"status_code": 409, "error": "exists"}
        return {"error": "unexpected"}

    with patch.object(pt, "_api_call", side_effect=_fake_api):
        pid = await pt._get_or_create_profile()

    assert pid == "p99"
    assert profile_reads["n"] >= 2


@pytest.mark.asyncio
async def test_instance_status_polling(mock_config: MagicMock) -> None:
    state = {"n": 0}

    async def _poll(instance_id: str) -> dict[str, Any] | None:
        state["n"] += 1
        if state["n"] < 2:
            return None
        return {"status": "running", "id": instance_id}

    with patch.object(pt, "_poll_instance_running", side_effect=_poll):
        out = await pt._poll_instance_running("i1")
    assert out is None or out.get("status") == "running"


@pytest.mark.asyncio
async def test_read_page_uses_text_and_untrusted(mock_config: MagicMock) -> None:
    pt._session.tab_id = "tab-1"
    responses = {
        ("GET", "/health"): _FakeResponse(200, {"status": "ok"}),
        ("GET", "/tabs/tab-1/text"): _FakeResponse(
            200,
            {"text": "Hello Pakistan", "title": "Wiki", "url": "https://x.test"},
        ),
        ("GET", "/tabs/tab-1/snapshot"): _FakeResponse(
            200,
            {
                "elements": [{"ref": "e1", "role": "link", "name": "Home"}],
            },
        ),
    }
    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt.requests, "request", _route(responses)),
    ):
        result = await pt.chrome_browser_read_page()

    assert result["status"] == "success"
    assert result["data"]["untrusted"] is True
    assert "Hello Pakistan" in result["data"]["text"]
    assert result["message"].startswith(pt.PAGE_PREFIX)
    assert result["data"]["elements"][0]["ref"] == "e1"


@pytest.mark.asyncio
async def test_press_action_maps_key(mock_config: MagicMock) -> None:
    pt._session.tab_id = "tab-2"
    calls: list[dict[str, Any]] = []
    responses = {
        ("GET", "/health"): _FakeResponse(200, {"status": "ok"}),
        ("POST", "/tabs/tab-2/action"): _FakeResponse(200, {"status": "ok"}),
        ("GET", "/tabs/tab-2/snapshot"): _FakeResponse(200, {"elements": []}),
    }
    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt.requests, "request", _route(responses, calls=calls)),
    ):
        await pt.chrome_browser_perform_action("press", "e5", None)

    action_calls = [c for c in calls if c["method"] == "POST" and "/action" in c["url"]]
    assert action_calls
    payload = action_calls[0]["json"]
    assert payload == {"kind": "press", "ref": "e5", "key": "Enter"}


@pytest.mark.asyncio
async def test_fill_falls_back_to_type_on_400(mock_config: MagicMock) -> None:
    pt._session.tab_id = "tab-3"
    action_n = {"n": 0}

    def _request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if method == "GET" and "/health" in url:
            return _FakeResponse(200, {"status": "ok"})
        if method == "POST" and "/action" in url:
            action_n["n"] += 1
            if action_n["n"] == 1:
                return _FakeResponse(400, {"error": "bad fill"})
            return _FakeResponse(200, {"status": "ok"})
        if method == "GET" and "/snapshot" in url:
            return _FakeResponse(200, {"elements": []})
        return _FakeResponse(404, {"error": "x"})

    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt.requests, "request", side_effect=_request),
    ):
        result = await pt.chrome_browser_perform_action("fill", "e9", "Pakistan")

    assert result["status"] == "success"
    assert action_n["n"] == 2


@pytest.mark.asyncio
async def test_degraded_when_server_down(mock_config: MagicMock) -> None:
    mock_config.pinchtab_autostart = False
    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt, "_health_ok", return_value=False),
        patch.object(pt, "_open_url_in_chrome", return_value=None) as open_chrome,
    ):
        result = await pt.automate_chrome_browser("example.com")

    open_chrome.assert_awaited_once()
    assert result["status"] == "degraded"
    assert result["error_code"] == "PINCHTAB_UNAVAILABLE"
    assert "https://example.com" in open_chrome.call_args[0][0]


@pytest.mark.asyncio
async def test_autostart_spawns_and_polls_health(mock_config: MagicMock) -> None:
    health_checks = {"n": 0}

    async def _health() -> bool:
        health_checks["n"] += 1
        return health_checks["n"] >= 2

    with (
        patch.object(pt, "config", mock_config),
        patch.object(pt, "_health_ok", side_effect=_health),
        patch.object(pt.os.path, "isfile", return_value=True),
        patch.object(pt.asyncio, "to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k)),
        patch.object(pt.subprocess, "Popen") as popen,
    ):
        ok = await pt._ensure_server()

    assert ok is True
    popen.assert_called_once()
    args = popen.call_args[0][0]
    assert args[0] == mock_config.pinchtab_bin
    assert args[1] == "server"
