"""
X-30: File access server — token gate, traversal/sensitive-path guard, TTL auto-stop, audit.

Windows note: pytest's tmp_path lives under %TEMP% = .../AppData/Local/Temp, and
is_sensitive_path() (K-25) rejects any path with an 'AppData' segment on purpose. So, like
tests/tools/test_notepad_tools.py, the served root here is a throwaway folder under
~/Desktop instead of tmp_path.
"""

from __future__ import annotations

import http.client
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import AsyncMock

import pytest

import services.system.jarvis_file_server as jfs


def _safe_tmp_dir() -> Path:
    base = Path.home() / "Desktop"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=str(base), prefix="jarvis_x30_"))


def _get(conn: http.client.HTTPConnection, path: str) -> int:
    conn.request("GET", path)
    resp = conn.getresponse()
    resp.read()
    return resp.status


@pytest.fixture
def served_root() -> Iterator[Path]:
    root = _safe_tmp_dir()
    (root / "hello.txt").write_text("hi there", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    sub = root / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested", encoding="utf-8")
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """Every test starts and ends with no running server (module state is a singleton)."""
    jfs._stop_server_sync()
    yield
    jfs._stop_server_sync()


# --------------------------------------------------------------------------- _start_server (raw)


@pytest.fixture
def running_server(served_root: Path) -> Iterator[tuple[Any, str, int]]:
    token = "tok_" + ("x" * 20)
    server = jfs._start_server(served_root, "127.0.0.1", 0, token)
    port = server.server_address[1]
    try:
        yield server, token, port
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass


def test_no_token_is_rejected(running_server: tuple[Any, str, int]) -> None:
    _server, _token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        assert _get(conn, "/") == 403
        assert _get(conn, "/hello.txt") == 403
    finally:
        conn.close()


def test_token_serves_a_listing(running_server: tuple[Any, str, int]) -> None:
    _server, token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", f"/{token}/")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert b"hello.txt" in body
    finally:
        conn.close()


def test_token_serves_a_file(running_server: tuple[Any, str, int]) -> None:
    _server, token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", f"/{token}/hello.txt")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert body == b"hi there"
    finally:
        conn.close()


def test_traversal_is_rejected(running_server: tuple[Any, str, int]) -> None:
    _server, token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        status = _get(conn, f"/{token}/..%2F..%2FWindows")
        assert status in (403, 404)
        status2 = _get(conn, f"/{token}/subdir/../../hello.txt")
        assert status2 in (403, 404)
    finally:
        conn.close()


def test_dotenv_is_rejected(running_server: tuple[Any, str, int]) -> None:
    _server, token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        assert _get(conn, f"/{token}/.env") == 403
    finally:
        conn.close()


def test_write_verbs_are_rejected(running_server: tuple[Any, str, int]) -> None:
    _server, token, port = running_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", f"/{token}/hello.txt", body="x")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403
    finally:
        conn.close()


def test_bind_failure_raises_oserror(served_root: Path) -> None:
    token = "tok_" + ("y" * 20)
    first = jfs._start_server(served_root, "127.0.0.1", 0, token)
    port = first.server_address[1]
    try:
        with pytest.raises(OSError):
            jfs._start_server(served_root, "127.0.0.1", port, "tok_" + ("z" * 20))
    finally:
        first.shutdown()
        first.server_close()


# --------------------------------------------------------------------------- the tool wrapper


@pytest.fixture
def tool_env(monkeypatch: pytest.MonkeyPatch, served_root: Path) -> dict[str, AsyncMock]:
    monkeypatch.setattr(jfs.config, "file_server_root", str(served_root), raising=False)
    # A real bindable (non-127.x) address: 0.0.0.0 is valid to bind() on Windows and still
    # answers on 127.0.0.1, unlike a made-up LAN IP that belongs to no local interface.
    monkeypatch.setattr(jfs, "get_local_ip", lambda: "0.0.0.0")
    qr_mock = AsyncMock(return_value={"status": "success",
                                      "file_path": str(served_root / "qr.png")})
    notify_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(jfs, "generate_qr_code", qr_mock)
    monkeypatch.setattr(jfs, "notify_event", notify_mock)
    return {"qr": qr_mock, "notify": notify_mock}


async def test_missing_root_errors(monkeypatch: pytest.MonkeyPatch,
                                   tool_env: dict[str, AsyncMock]) -> None:
    monkeypatch.setattr(jfs.config, "file_server_root", "", raising=False)
    res = await jfs.start_file_access_server(port=0)
    assert res["status"] == "error"
    assert res["error_code"] == jfs.ERROR_ROOT_MISSING


async def test_no_lan_errors(monkeypatch: pytest.MonkeyPatch,
                             tool_env: dict[str, AsyncMock]) -> None:
    monkeypatch.setattr(jfs, "get_local_ip", lambda: "127.0.0.1")
    res = await jfs.start_file_access_server(port=0)
    assert res["status"] == "error"
    assert res["error_code"] == jfs.ERROR_NO_LAN


async def test_start_url_has_token_and_is_reachable(tool_env: dict[str, AsyncMock]) -> None:
    res = await jfs.start_file_access_server(port=0)
    assert res["status"] == "success"
    token = jfs._server_token
    assert token and token in res["url"]
    assert token not in res["masked_url"]

    port = jfs._server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        assert _get(conn, f"/{token}/") == 200
        assert _get(conn, "/") == 403
    finally:
        conn.close()


async def test_notify_event_payload_never_has_the_token(tool_env: dict[str, AsyncMock]) -> None:
    await jfs.start_file_access_server(port=0)
    notify_mock = tool_env["notify"]
    notify_mock.assert_awaited()
    token = jfs._server_token
    _event_type, payload = notify_mock.await_args.args
    assert token not in str(payload)
    assert payload["data"]["url"] == jfs._server_masked_url


async def test_already_running_is_reported_without_a_second_bind(
    tool_env: dict[str, AsyncMock],
) -> None:
    res1 = await jfs.start_file_access_server(port=0)
    server_after_first = jfs._server
    res2 = await jfs.start_file_access_server(port=0)
    assert res1["status"] == "success"
    assert res2["status"] == "info"
    assert jfs._server is server_after_first  # no second server was started


async def test_stop_frees_the_port(tool_env: dict[str, AsyncMock]) -> None:
    await jfs.start_file_access_server(port=0)
    port = jfs._server.server_address[1]

    stop_res = await jfs.stop_file_access_server()
    assert stop_res["status"] == "success"
    assert jfs._server is None
    assert jfs._server_token is None

    time.sleep(0.1)
    with pytest.raises(OSError):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.connect()
        conn.close()


async def test_stop_with_nothing_running_is_informational(
    tool_env: dict[str, AsyncMock],
) -> None:
    res = await jfs.stop_file_access_server()
    assert res["status"] == "info"


async def test_ttl_auto_stop(monkeypatch: pytest.MonkeyPatch,
                             tool_env: dict[str, AsyncMock]) -> None:
    monkeypatch.setattr(jfs, "_ttl_seconds", lambda: 0.2)
    res = await jfs.start_file_access_server(port=0)
    assert res["status"] == "success"
    port = jfs._server.server_address[1]

    deadline = time.monotonic() + 5.0
    while jfs._server is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert jfs._server is None, "TTL timer never auto-stopped the server"

    with pytest.raises(OSError):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.connect()
        conn.close()
