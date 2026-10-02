"""X-65: VPS-safe desktop modules (side-effect guard on)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.automation import jarvis_notepad_automation as nap
from services.multimedia import jarvis_advanced_tools as adv
from services.multimedia import jarvis_qr_gen as qr_mod
from services.multimedia import jarvis_youtube_downloader as yt_dl_mod
from services.system import jarvis_window_ctrl as win_mod
from services.system.jarvis_desktop_common import is_protected_window, WindowInfo
from services.system.jarvis_system_ctrl import restart_system, shutdown_system, sleep_system


@pytest.mark.asyncio
async def test_shutdown_on_linux_returns_desktop_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.system.jarvis_system_ctrl.sys.platform", "linux")
    res = await shutdown_system()
    assert res["status"] == "error"
    assert res["error_code"] == "DESKTOP_ONLY"


@pytest.mark.asyncio
async def test_restart_and_sleep_desktop_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.system.jarvis_system_ctrl.sys.platform", "linux")
    for fn in (restart_system, sleep_system):
        res = await fn()
        assert res["error_code"] == "DESKTOP_ONLY"


def test_youtube_downloader_uses_delivery_not_startfile() -> None:
    src = inspect.getsource(yt_dl_mod.YouTubeDownloader.download)
    assert "os.startfile" not in src
    assert "jarvis_delivery.open_artifact" in src


def test_qr_gen_uses_delivery_not_startfile() -> None:
    src = inspect.getsource(qr_mod.generate_qr_code)
    assert "os.startfile" not in src
    assert "jarvis_delivery.open_artifact" in src


@pytest.mark.asyncio
async def test_send_email_missing_attachment_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_USER", "user@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "secret")
    res = await adv.send_email("to@example.com", "hi", "body", attachment_path=r"C:\missing.zip")
    assert res["status"] == "error"
    assert res.get("error_code") == "ATTACHMENT_MISSING"


@pytest.mark.asyncio
async def test_notepad_edit_rejects_subpath(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp_path))
    res = await nap.edit_notepad_file_tool("nested/note.txt", "x")
    assert res["error_code"] == "PATH_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_notepad_append_rejects_subpath(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp_path))
    res = await nap.append_notepad_file_tool(r"foo\bar.txt", "x")
    assert res["error_code"] == "PATH_NOT_ALLOWED"


def test_open_app_risk_unmapped_is_high() -> None:
    risk = win_mod._open_app_risk({"full_command": "open totally_unknown_xyz_app"})
    assert risk == "high"


def test_open_app_risk_mapped_low() -> None:
    risk = win_mod._open_app_risk({"full_command": "open notepad"})
    assert risk == "low"


def test_protected_window_title_prefix() -> None:
    info = WindowInfo(hwnd=1, title="JARVIS-AGENT · debug", pid=2, process_name="electron.exe")
    assert is_protected_window(info) is True


@pytest.mark.asyncio
async def test_youtube_fallback_errors_when_browser_open_fails() -> None:
    dl = yt_dl_mod.YouTubeDownloader()
    with patch.object(yt_dl_mod.yt_bot, "open_url_in_app", new_callable=AsyncMock, return_value=False):
        res = await dl._fallback_to_browser("https://youtube.com/watch?v=abc12345678", "fail")
    assert res["status"] == "error"
    assert "Browser fallback" in res["message"]
