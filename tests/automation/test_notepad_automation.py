"""X-27: Notepad automation integration tests (fully mocked desktop)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.automation.jarvis_notepad_automation as nap


def _safe_tmp_dir() -> Path:
    base = Path.home() / "Desktop"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=str(base)))


@pytest.fixture(autouse=True)
def _reset_session() -> None:
    nap.notepad_automation.clear_session()
    yield
    nap.notepad_automation.clear_session()


@pytest.mark.asyncio
async def test_open_notepad_simple():
    with patch("services.automation.jarvis_notepad_automation.subprocess.Popen") as mock_popen:
        res = await nap.open_notepad_simple()
        mock_popen.assert_called()
        assert res["status"] == "success"


@pytest.mark.asyncio
async def test_simulate_typing_uses_run_cancellable():
    with patch("services.automation.jarvis_notepad_automation.run_cancellable", new_callable=AsyncMock) as mock_rc:
        mock_rc.return_value = None
        ok = await nap.notepad_automation.simulate_typing("Hello\nWorld")
        assert ok is True
        mock_rc.assert_awaited_once()


@pytest.mark.asyncio
async def test_simulate_typing_catches_failsafe() -> None:
    fs = type("FailSafeException", (Exception,), {})
    pg = MagicMock()
    pg.FailSafeException = fs
    nap._pyautogui = pg
    with patch.object(nap, "run_cancellable", new_callable=AsyncMock, side_effect=fs("corner")):
        assert await nap.notepad_automation.simulate_typing("fail") is False
    nap._pyautogui = None


@pytest.mark.asyncio
async def test_save_file_safely(monkeypatch):
    tmp = _safe_tmp_dir()
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp))
    ok, path = await nap.notepad_automation.save_file_safely("content", "test.txt")
    assert ok is True
    assert path.endswith("test.txt")
    assert (tmp / "test.txt").read_text(encoding="utf-8") == "content"


@pytest.mark.asyncio
async def test_create_template_code():
    with patch("services.automation.jarvis_notepad_automation.get_template_content", return_value=("<html></html>", "test.html")):
        with patch("services.automation.jarvis_notepad_automation._process_notepad_automation", new_callable=AsyncMock) as mock_proc:
            mock_proc.return_value = {"status": "success", "message": "ok"}
            res = await nap.create_template_code("html", "test.html", auto_run=False)
            assert res["status"] == "success"
            mock_proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_custom_code():
    with patch("services.automation.jarvis_notepad_automation._process_notepad_automation", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = {"status": "success", "message": "ok"}
        await nap.write_custom_code("Custom Code", "custom.py", auto_run=False)
        mock_proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_notepad_automation_flow():
    with patch.object(nap, "desktop_control_enabled", return_value=True):
        with patch.object(nap.notepad_automation, "save_file_safely", new_callable=AsyncMock, return_value=(True, "/tmp/x.py")):
            with patch.object(nap.notepad_automation, "launch_notepad_for_file", new_callable=AsyncMock, return_value=(True, MagicMock(hwnd=1), 42)):
                with patch.object(nap.notepad_automation, "simulate_typing", new_callable=AsyncMock, return_value=True):
                    with patch.object(nap.notepad_automation, "verify_saved_content", new_callable=AsyncMock, return_value=(True, "verified")):
                        with patch.object(nap.notepad_automation, "close_active_notepad", new_callable=AsyncMock) as mock_close:
                            mock_pg = MagicMock()
                            with patch.object(nap, "_pyautogui_mod", return_value=mock_pg):
                                mock_hotkey = mock_pg.hotkey
                                with patch("services.automation.jarvis_notepad_automation.subprocess.Popen") as mock_popen:
                                    out = _safe_tmp_dir()
                                    fake = out / "x.py"
                                    fake.write_text("", encoding="utf-8")
                                    with patch.object(nap, "_outputs_dir", return_value=str(out)):
                                        with patch.object(nap, "safe_child_path", return_value=fake):
                                            res = await nap._process_notepad_automation("print(1)", "x.py", auto_run=True)
    assert res["status"] == "success"
    mock_hotkey.assert_called_with("ctrl", "s")
    mock_close.assert_awaited()
    mock_popen.assert_called()


@pytest.mark.asyncio
async def test_run_cmd_command_error():
    with patch("services.automation.jarvis_notepad_automation.subprocess.Popen", side_effect=OSError("CMD fail")):
        res = await nap.run_cmd_command("invalid")
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_get_template_content():
    content, filename = nap.get_template_content("html_login", "login.html")
    assert "<!DOCTYPE html>" in content
    assert filename == "login.html"

    _content2, filename2 = nap.get_template_content("unknown", "default.txt")
    assert filename2 == "default.txt"


@pytest.mark.asyncio
async def test_ensure_notepad_focus_refuses_without_pid():
    ok = await nap.notepad_automation.ensure_notepad_focus()
    assert ok is False


@pytest.mark.asyncio
async def test_close_notepad_tool_force_uses_kill_all():
    with patch.object(nap.get_notepad_mgr(), "close_active_notepad", new_callable=AsyncMock, return_value=True) as mock_close:
        await nap.close_notepad_tool(force=True)
        mock_close.assert_awaited_with(kill_all_im=True)
