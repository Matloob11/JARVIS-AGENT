import pytest
import asyncio
import os
import subprocess
from unittest.mock import MagicMock, patch, AsyncMock, mock_open
from jarvis_notepad_automation import NotepadAutomation, get_template_content, create_template_code, write_custom_code, run_cmd_command, open_notepad_simple


@pytest.fixture
def notepad_automation():
    return NotepadAutomation()


@pytest.mark.asyncio
async def test_ensure_notepad_focus_success(notepad_automation):
    with patch("jarvis_notepad_automation.gw") as mock_gw, \
            patch("jarvis_notepad_automation.win32gui") as mock_win32gui, \
            patch("asyncio.sleep", AsyncMock()):

        mock_win = MagicMock()
        mock_win.title = "Untitled - Notepad"
        mock_win.isMinimized = True
        mock_win.isActive = True
        mock_gw.getWindowsWithTitle.return_value = [mock_win]

        res = await notepad_automation.ensure_notepad_focus(timeout=1)
        assert res is True
        mock_win.restore.assert_called()
        mock_win.activate.assert_called()
        mock_win32gui.ShowWindow.assert_called()
        mock_win32gui.SetForegroundWindow.assert_called()


@pytest.mark.asyncio
async def test_ensure_notepad_focus_timeout(notepad_automation):
    with patch("jarvis_notepad_automation.gw") as mock_gw, \
            patch("asyncio.sleep", AsyncMock()):

        mock_gw.getWindowsWithTitle.return_value = []
        res = await notepad_automation.ensure_notepad_focus(timeout=0.1)
        assert res is False


@pytest.mark.asyncio
async def test_ensure_notepad_focus_no_gw(notepad_automation):
    with patch("jarvis_notepad_automation.gw", None), \
            patch("asyncio.sleep", AsyncMock()):
        res = await notepad_automation.ensure_notepad_focus()
        assert res is True


@pytest.mark.asyncio
async def test_simulate_typing_success(notepad_automation):
    with patch("pyautogui.write") as mock_write, \
            patch("pyautogui.press") as mock_press:
        res = await notepad_automation.simulate_typing("hello\nworld")
        assert res is True
        assert mock_write.call_count == 2
        assert mock_press.call_count == 2


@pytest.mark.asyncio
async def test_simulate_typing_failure(notepad_automation):
    with patch("pyautogui.write", side_effect=RuntimeError("Fail")):
        res = await notepad_automation.simulate_typing("fail")
        assert res is False


@pytest.mark.asyncio
async def test_save_file_safely_success(notepad_automation):
    with patch("os.makedirs") as mock_mkdir, \
            patch("builtins.open", mock_open()) as mock_file, \
            patch("os.path.exists", return_value=True), \
            patch("os.environ.get", return_value="C:\\Users\\Test"):

        success, path = await notepad_automation.save_file_safely("content", "test.txt")
        assert success is True
        assert "test.txt" in path
        mock_mkdir.assert_called()
        mock_file().write.assert_called_with("content")


@pytest.mark.asyncio
async def test_save_file_safely_failure(notepad_automation):
    with patch("os.makedirs", side_effect=OSError("Perm Denied")):
        success, msg = await notepad_automation.save_file_safely("c", "f")
        assert success is False
        assert "Perm Denied" in msg


@pytest.mark.asyncio
async def test_close_active_notepad_force(notepad_automation):
    with patch("subprocess.run") as mock_run:
        res = await notepad_automation.close_active_notepad(force=True)
        assert res is True
        mock_run.assert_called_with(
            ["taskkill", "/f", "/im", "notepad.exe"], check=False, capture_output=True)


@pytest.mark.asyncio
async def test_close_active_notepad_normal(notepad_automation):
    with patch("jarvis_notepad_automation.win32gui") as mock_win32gui, \
            patch("asyncio.sleep", AsyncMock()):

        # Test win32 callback
        def mock_enum(cb, param):
            cb(123, None)
        mock_win32gui.EnumWindows.side_effect = mock_enum
        mock_win32gui.IsWindowVisible.return_value = True
        mock_win32gui.GetWindowText.return_value = "Untitled - Notepad"

        res = await notepad_automation.close_active_notepad(force=False)
        assert res is True
        mock_win32gui.PostMessage.assert_called()


def test_get_template_content():
    c, f = get_template_content("html_login", "")
    assert "JARVIS LOGIN" in c
    assert f.endswith(".html")

    c, f = get_template_content("python_hello", "my.py")
    assert "print(\"Hello World" in c
    assert f == "my.py"

    c, f = get_template_content("amazing_code", "")
    assert "heart animation" in c
    assert f.endswith(".py")

    c, f = get_template_content("invalid", "")
    assert c == ""


@pytest.mark.asyncio
async def test_ensure_notepad_focus_exceptions(notepad_automation):
    class MockException(Exception):
        pass

    with patch("jarvis_notepad_automation.gw") as mock_gw, \
            patch("asyncio.sleep", AsyncMock()):

        mock_gw.PyGetWindowException = MockException
        mock_win = MagicMock()
        mock_win.title = "Notepad"
        mock_win.isMinimized = False
        mock_win.isActive = False
        # Trigger AttributeError or PyGetWindowException
        mock_win.activate.side_effect = AttributeError("Fail")
        mock_gw.getWindowsWithTitle.return_value = [mock_win]

        res = await notepad_automation.ensure_notepad_focus(timeout=0.1)
        assert res is False


@pytest.mark.asyncio
async def test_save_file_safely_desktop_fallback(notepad_automation):
    # Test line 114: Desktop doesn't exist, use expanduser
    with patch("os.path.exists", side_effect=[False, True]), \
            patch("os.path.expanduser", return_value="C:\\Home"), \
            patch("os.makedirs"), \
            patch("builtins.open", mock_open()):

        success, path = await notepad_automation.save_file_safely("c", "f", folder_path=None)
        assert success is True
        assert "C:\\Home" in path


@pytest.mark.asyncio
async def test_close_active_notepad_no_win32(notepad_automation):
    with patch("jarvis_notepad_automation.win32gui", None), \
            patch("pyautogui.hotkey") as mock_hotkey, \
            patch("asyncio.sleep", AsyncMock()):
        res = await notepad_automation.close_active_notepad(force=False)
        assert res is True
        mock_hotkey.assert_called_with('alt', 'f4')


@pytest.mark.asyncio
async def test_close_active_notepad_exception(notepad_automation):
    with patch("jarvis_notepad_automation.win32gui.EnumWindows", side_effect=AttributeError("Fail")):
        res = await notepad_automation.close_active_notepad(force=False)
        assert res is False


@pytest.mark.asyncio
async def test_create_template_code_errors():
    # Test line 242: Unsupported code type
    res = await create_template_code("invalid", "", auto_run=False)
    assert res["status"] == "error"

    # Test line 249: Failed to initialize
    with patch("jarvis_notepad_automation.notepad_automation.save_file_safely", AsyncMock(return_value=(False, "Err"))):
        res = await create_template_code("html_login", "f", auto_run=False)
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_create_template_code_gui_fail_fallback():
    # Test lines 268-270: GUI automation failure
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen"):

        mock_auto.save_file_safely = AsyncMock(
            return_value=(True, "C:\\test.py"))
        mock_auto.ensure_notepad_focus = AsyncMock(
            side_effect=RuntimeError("Focus crash"))

        res = await create_template_code("python_hello", "test.py", auto_run=True)
        assert res["status"] == "success"
        assert "GUI automation failed" in res["message"]


@pytest.mark.asyncio
async def test_write_custom_code_errors():
    # Test line 302: Filename required
    res = await write_custom_code("c", "", auto_run=False)
    assert res["status"] == "error"

    # Test line 308: Save fail
    with patch("jarvis_notepad_automation.notepad_automation.save_file_safely", AsyncMock(return_value=(False, "Err"))):
        res = await write_custom_code("c", "f", auto_run=False)
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_write_custom_code_focus_fail():
    # Test lines 325-330
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen"), \
            patch("os.startfile"):

        mock_auto.save_file_safely = AsyncMock(
            return_value=(True, "C:\\f.html"))
        mock_auto.ensure_notepad_focus = AsyncMock(return_value=False)

        res = await write_custom_code("c", "f.html", auto_run=True)
        assert res["status"] == "success"
        assert "Focus failed" in res["message"]


@pytest.mark.asyncio
async def test_write_custom_code_gui_fail():
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen"):

        mock_auto.save_file_safely = AsyncMock(return_value=(True, "C:\\f.py"))
        mock_auto.ensure_notepad_focus = AsyncMock(return_value=True)
        mock_auto.simulate_typing = AsyncMock(
            side_effect=RuntimeError("Type fail"))

        res = await write_custom_code("c", "f.py", auto_run=True)
        assert res["status"] == "success"
        assert "GUI automation failed" in res["message"]


@pytest.mark.asyncio
async def test_run_cmd_command_failure():
    with patch("subprocess.Popen", side_effect=OSError("Boom")):
        res = await run_cmd_command("dir")
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_open_notepad_simple_failure():
    with patch("subprocess.Popen", side_effect=OSError("Boom")):
        res = await open_notepad_simple()
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_create_template_code_full_flow():
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen") as mock_popen, \
            patch("os.startfile") as mock_startfile, \
            patch("pyautogui.hotkey") as mock_hotkey:

        mock_auto.save_file_safely = AsyncMock(
            return_value=(True, "C:\\test.html"))
        mock_auto.ensure_notepad_focus = AsyncMock(return_value=True)
        mock_auto.simulate_typing = AsyncMock(return_value=True)
        mock_auto.close_active_notepad = AsyncMock(return_value=True)

        res = await create_template_code("html_login", "test.html", auto_run=True)
        assert res["status"] == "success"
        mock_auto.simulate_typing.assert_called()
        mock_startfile.assert_called()
        mock_hotkey.assert_called_with('ctrl', 's')


@pytest.mark.asyncio
async def test_create_template_code_focus_fail():
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen"):

        mock_auto.save_file_safely = AsyncMock(
            return_value=(True, "C:\\test.py"))
        mock_auto.ensure_notepad_focus = AsyncMock(return_value=False)
        mock_auto.save_file_safely.side_effect = [
            (True, "C:\\test.py"), (True, "C:\\test.py")]

        res = await create_template_code("python_hello", "test.py", auto_run=True)
        assert res["status"] == "success"
        assert "Notepad focus failed" in res["message"]
        # In case of focus fail, it calls save_file_safely again
        assert mock_auto.save_file_safely.call_count >= 1


@pytest.mark.asyncio
async def test_write_custom_code_success():
    with patch("jarvis_notepad_automation.notepad_automation") as mock_auto, \
            patch("subprocess.Popen"):

        mock_auto.save_file_safely = AsyncMock(
            return_value=(True, "C:\\custom.py"))
        mock_auto.ensure_notepad_focus = AsyncMock(return_value=True)
        mock_auto.simulate_typing = AsyncMock(return_value=True)
        mock_auto.close_active_notepad = AsyncMock(return_value=True)

        res = await write_custom_code("print(1)", "custom.py", auto_run=False)
        assert res["status"] == "success"
        mock_auto.simulate_typing.assert_called_with("print(1)")


@pytest.mark.asyncio
async def test_run_cmd_command_success():
    with patch("subprocess.Popen") as mock_popen:
        res = await run_cmd_command("dir")
        assert res["status"] == "success"
        mock_popen.assert_called()


@pytest.mark.asyncio
async def test_open_notepad_simple_success():
    with patch("subprocess.Popen") as mock_popen:
        res = await open_notepad_simple()
        assert res["status"] == "success"
        mock_popen.assert_called_with(['notepad.exe'])


@pytest.mark.asyncio
async def test_run_cmd_command_exception():
    # Test line 375
    with patch("subprocess.Popen", side_effect=OSError("Log this")):
        res = await run_cmd_command("dir")
        assert res["status"] == "error"


@pytest.mark.asyncio
async def test_create_template_code_critical_error():
    # Test lines 292-294
    with patch("jarvis_notepad_automation.get_template_content", side_effect=ValueError("Critical")):
        res = await create_template_code("python_hello", "f")
        assert res["status"] == "error"
        assert "Critical" in res["message"]


@pytest.mark.asyncio
async def test_write_custom_code_exception():
    # Test lines 353-356
    with patch("jarvis_notepad_automation.notepad_automation.save_file_safely", side_effect=RuntimeError("Log this")):
        res = await write_custom_code("c", "f")
        assert res["status"] == "error"


def test_get_template_content_default_filename():
    # Test line 228
    c, f = get_template_content("python_hello", "")
    assert f.startswith("hello_")
    assert f.endswith(".py")


def test_notepad_automation_no_win32_class():
    # Test lines 31-33
    import sys
    from importlib import reload
    import jarvis_notepad_automation

    with patch.dict(sys.modules, {'win32gui': None, 'win32con': None, 'pywintypes': None}):
        reload(jarvis_notepad_automation)
        # Should run without error
        assert jarvis_notepad_automation.win32gui is None

    # Reload again to restore
    reload(jarvis_notepad_automation)
