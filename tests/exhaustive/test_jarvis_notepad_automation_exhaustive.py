"""
Plan C-61: exhaustive tests of services/automation/jarvis_notepad_automation.py (X-27 behaviour).

tests/exhaustive/conftest.py keeps the K-27 guard on: no real Notepad, taskkill, keystroke,
clipboard or browser call can happen. Automated flows close only JARVIS's own Notepad (WM_CLOSE to
its window, then 'taskkill /PID <pid>'); 'taskkill /IM notepad.exe' is left only for
close_notepad_tool(force=True), which waits for Sir's confirmation. Jarvis_Outputs lives in
tmp_path, the bridge, the confirmation UI and security.log are fakes, and asyncio.sleep returns at
once. pyautogui is patched on its own module, never as nap.pyautogui: since X-65 the module loads
it lazily through _pyautogui_mod(), which hands back that same module object.
"""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import types
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pyautogui
import pytest

import services.automation.jarvis_notepad_automation as nap
from services.ai_core import jarvis_confirm
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.system import jarvis_desktop_common as desktop_common
from services.system.jarvis_desktop_common import WindowInfo
from services.utils import jarvis_audit, jarvis_bridge
from services.utils.jarvis_resilience import resilience_manager

LIVE = os.environ.get("JARVIS_LIVE_GUI_TESTS", "").strip() == "1"
OUR_HWND, OUR_PID = 77, 4242
KILL_OURS = call([nap.TASKKILL_EXE, "/PID", str(OUR_PID), "/F"], check=False, capture_output=True)
KILL_ALL = call([nap.TASKKILL_EXE, "/F", "/IM", "notepad.exe"], check=False, capture_output=True)


@pytest.fixture(name="outputs", autouse=True)
def _outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, instant_sleep) -> Path:
    """Jarvis_Outputs in tmp_path, no bridge posts, desktop control on, no Notepad session."""
    outputs = tmp_path / "Jarvis_Outputs"
    monkeypatch.setattr(nap.config, "outputs_dir", str(outputs), raising=False)
    base = tmp_path.resolve()
    real_is_sensitive = desktop_common.is_sensitive_path

    def is_sensitive_below_tmp(path: Any) -> bool:
        # pytest's tmp_path lies under AppData, which is_sensitive_path refuses: judge the part
        # below tmp_path only, so '.env' and the other rules still apply inside it.
        try:
            inside = Path(path).resolve(strict=False).relative_to(base)
        except (TypeError, ValueError, OSError):
            return real_is_sensitive(path)
        return real_is_sensitive(inside)

    monkeypatch.setattr(desktop_common, "is_sensitive_path", is_sensitive_below_tmp)
    monkeypatch.setattr(nap, "notify_thinking", AsyncMock())
    monkeypatch.setattr(nap, "notify_tool_action", AsyncMock())
    monkeypatch.setattr(nap, "desktop_control_enabled", lambda: True)
    nap.notepad_automation.clear_session()
    nap.notepad_automation.current_file_path = None
    yield outputs
    nap.notepad_automation.clear_session()


@pytest.fixture(name="gui")
def _gui(block_exhaustive_desktop_gaps) -> Any:
    """Our Notepad is OUR_HWND / OUR_PID; every window, key, process and browser call is a mock."""
    session = nap.notepad_automation

    async def launch(full_path: str) -> tuple[bool, WindowInfo, int]:
        session._session_hwnd, session._session_pid = OUR_HWND, OUR_PID
        window = WindowInfo(hwnd=OUR_HWND, title=f"{Path(full_path).name} - Notepad", pid=OUR_PID,
                            process_name="notepad.exe")
        return True, window, OUR_PID

    with patch.object(session, "launch_notepad_for_file", AsyncMock(side_effect=launch)) as launch_, \
            patch.object(session, "simulate_typing", AsyncMock(return_value=True)) as typing, \
            patch.object(nap, "close_windows_gracefully",
                         AsyncMock(return_value={"closed": [OUR_HWND], "still_open": []})) as close, \
            patch.object(pyautogui, "hotkey") as hotkey, \
            patch.object(pyautogui, "press") as press, \
            patch.object(nap.subprocess, "run") as run, \
            patch.object(nap.subprocess, "Popen") as popen, \
            patch.object(webbrowser, "open") as browser:
        yield types.SimpleNamespace(launch=launch_, typing=typing, close=close, hotkey=hotkey,
                                    press=press, run=run, popen=popen, browser=browser)


@pytest.fixture(name="win")
def _win(block_exhaustive_desktop_gaps) -> Any:
    """Window lookup, focus and Popen under the focus and launch code; Notepad gets pid 999."""
    with patch.object(nap, "wait_for_window", new_callable=AsyncMock) as wait, \
            patch.object(nap, "force_foreground", new_callable=AsyncMock, return_value=True) as fg, \
            patch.object(nap.subprocess, "Popen") as popen:
        popen.return_value.pid = 999
        yield types.SimpleNamespace(wait=wait, fg=fg, popen=popen)


class _FakeBridge:
    """The bridge's /notify answer to ConfirmFlow events: delivered, one UI connected."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((event_type, dict(payload)))
        return {"status": "ok", "ui_clients": 1}


@pytest.fixture(name="confirm")
async def _confirm(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A fresh K-14 broker on a fake bridge; tool_sync posts and security.log rows are dropped."""
    bridge = _FakeBridge()
    results: list[dict[str, Any]] = []

    async def on_result(result: dict[str, Any]) -> None:
        results.append(result)

    broker = jarvis_confirm.ConfirmationBroker(sender=bridge, on_result=on_result)
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", broker)
    monkeypatch.setattr(jarvis_confirm, "NOTIFY_RETRY_DELAY_S", 0)
    monkeypatch.setattr(jarvis_bridge, "notify_event", AsyncMock())
    monkeypatch.setattr(jarvis_audit, "log_tool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(jarvis_audit.JarvisAudit, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(resilience_manager, "_breakers", dict(resilience_manager._breakers))
    yield types.SimpleNamespace(broker=broker, bridge=bridge, results=results)
    await broker.shutdown()


def _registered(name: str) -> Callable[..., Any]:
    """The plugin manager's wrapper of a tool (risk, confirmation, audit), as the LLM calls it."""
    wrapper = next(tool for tool in plugin_manager.tools if tool.__name__ == name)
    assert wrapper.__wrapped__ is getattr(nap, name), "imported under a second module name"
    return wrapper


async def _until(predicate: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0)


def _kills_by_image_name(run: MagicMock) -> bool:
    """Any run() command with taskkill's /IM switch, in any case: taskkill reads '/im' the same."""
    for c in run.call_args_list:
        command = c.args[0] if c.args else c.kwargs.get("args", "")
        words = command.split() if isinstance(command, str) else command
        if any(str(word).upper() == "/IM" for word in words):
            return True
    return False


# --------------------------------------------------------------------------- guard


@pytest.mark.skipif(LIVE, reason="JARVIS_LIVE_GUI_TESTS=1 turns the guard off")
def test_guard_blocks_processes_and_keystrokes() -> None:
    """The arguments are harmless in case the guard is ever missing."""
    for attempt in (lambda: subprocess.run([sys.executable, "-c", "pass"], check=False),
                    lambda: pyautogui.write(""), lambda: pyautogui.press([]),
                    pyautogui.hotkey):
        with pytest.raises(AssertionError, match="REAL SIDE EFFECT"):
            attempt()


# --------------------------------------------------------------------------- focus and launch


async def test_focus_refuses_without_pid_or_hwnd(win: Any) -> None:
    assert await nap.notepad_automation.ensure_notepad_focus() is False
    win.wait.assert_not_awaited()
    win.fg.assert_not_awaited()


async def test_focus_by_hwnd(win: Any) -> None:
    assert await nap.notepad_automation.ensure_notepad_focus(hwnd=42, timeout=1) is True
    win.fg.assert_awaited_once_with(42)
    win.wait.assert_not_awaited()
    assert nap.notepad_automation._session_hwnd == 42


async def test_focus_by_pid_finds_only_that_process(win: Any) -> None:
    win.wait.return_value = WindowInfo(hwnd=7, title="a.py - Notepad", pid=321,
                                       process_name="notepad.exe")
    assert await nap.notepad_automation.ensure_notepad_focus(pid=321, timeout=1) is True
    win.wait.assert_awaited_once_with(pid=321, timeout_s=0.25)
    win.fg.assert_awaited_once_with(7)
    assert (nap.notepad_automation._session_hwnd, nap.notepad_automation._session_pid) == (7, 321)


async def test_focus_times_out(win: Any) -> None:
    win.fg.return_value = False
    assert await nap.notepad_automation.ensure_notepad_focus(hwnd=42, timeout=0.05) is False
    assert nap.notepad_automation._session_hwnd is None


async def test_launch_binds_to_the_new_process(win: Any, outputs: Path) -> None:
    target = str(outputs / "hello.py")
    window = WindowInfo(hwnd=42, title="hello.py - Notepad", pid=999, process_name="notepad.exe")
    win.wait.return_value = window
    assert await nap.notepad_automation.launch_notepad_for_file(target) == (True, window, 999)
    win.popen.assert_called_once_with([nap.NOTEPAD_EXE, target])
    assert win.wait.await_args_list[0] == call(pid=999, timeout_s=10.0)
    win.fg.assert_awaited_with(42)
    assert (nap.notepad_automation._session_hwnd, nap.notepad_automation._session_pid) == (42, 999)


async def test_launch_falls_back_to_the_window_title(win: Any, outputs: Path) -> None:
    window = WindowInfo(hwnd=43, title="x.py - Notepad", pid=1001, process_name="notepad.exe")
    win.wait.side_effect = [None, window]
    ok, found, pid = await nap.notepad_automation.launch_notepad_for_file(str(outputs / "x.py"))
    assert (ok, found, pid) == (True, window, 999)
    assert win.wait.await_args_list == [call(pid=999, timeout_s=10.0),
                                        call(title_suffix="x.py - Notepad", timeout_s=5.0)]


async def test_launch_reports_a_missing_window(win: Any, outputs: Path) -> None:
    win.wait.return_value = None
    assert await nap.notepad_automation.launch_notepad_for_file(str(outputs / "x.py")) == \
        (False, None, 999)
    win.fg.assert_not_awaited()


# --------------------------------------------------------------------------- typing


def test_pyautogui_is_loaded_lazily_with_the_failsafe_on() -> None:
    """X-65: the module imports without pyautogui; the loader returns that module, FAILSAFE on."""
    assert nap._pyautogui_mod() is pyautogui
    assert pyautogui.FAILSAFE is True
    assert nap._pyautogui_mod() is pyautogui


async def test_simulate_typing_keeps_lines_and_indentation() -> None:
    with patch.object(pyautogui, "write") as write, patch.object(pyautogui, "press") as press:
        assert await nap.notepad_automation.simulate_typing("def f():\n    return 1\n") is True
    assert write.call_args_list == [call("def f():", interval=0.005),
                                    call("    return 1", interval=0.005)]
    assert press.call_args_list == [call("enter"), call("enter")]


def test_typing_pastes_non_ascii_lines_and_the_remainder() -> None:
    text = "print('hi')\nسلام\n" + "z" * 30
    pause = pyautogui.PAUSE
    with patch.object(pyautogui, "write") as write, \
            patch.object(pyautogui, "press") as press, \
            patch.object(nap, "_clipboard_paste_text_sync") as paste:
        nap._type_text(threading.Event(), text, visible_limit=20)
    assert [c.args[0] for c in write.call_args_list] == ["print('hi')", "zzz"]
    assert paste.call_args_list == [call("سلام"), call("z" * 27)]
    assert press.call_count == 2
    assert pyautogui.PAUSE == pause


def test_typing_stops_when_cancelled() -> None:
    cancel = threading.Event()
    cancel.set()
    with patch.object(pyautogui, "write") as write, \
            patch.object(pyautogui, "press") as press, \
            patch.object(nap, "_clipboard_paste_text_sync") as paste:
        nap._type_text(cancel, "a\nb" + "c" * 2000)
    write.assert_not_called()
    press.assert_not_called()
    paste.assert_not_called()


@pytest.mark.parametrize("error", [
    RuntimeError("Fail"),
    AttributeError("no window"),
    pytest.param(pyautogui.FailSafeException(), marks=pytest.mark.xfail(
        raises=pyautogui.FailSafeException, strict=False,
        reason="X-65 regression reported to Cursor: simulate_typing no longer catches "
               "pyautogui.FailSafeException (the except tuple lost it with the lazy import)")),
])
async def test_simulate_typing_failure_returns_false(error: Exception) -> None:
    with patch.object(pyautogui, "write", side_effect=error), patch.object(pyautogui, "press"):
        assert await nap.notepad_automation.simulate_typing("fail") is False


# --------------------------------------------------------------------------- save and verify


async def test_save_file_safely_writes_into_jarvis_outputs(outputs: Path) -> None:
    ok, path = await nap.notepad_automation.save_file_safely("content", "test.txt")
    assert ok is True
    assert Path(path) == (outputs / "test.txt").resolve()
    assert (outputs / "test.txt").read_text(encoding="utf-8") == "content"
    assert nap.notepad_automation.current_file_path == path


async def test_save_file_safely_keeps_the_name_only_and_refuses_secrets(outputs: Path) -> None:
    ok, path = await nap.notepad_automation.save_file_safely("x", "..\\..\\evil.txt")
    assert ok is True
    assert Path(path) == (outputs / "evil.txt").resolve()
    assert await nap.notepad_automation.save_file_safely("KEY=1", ".env") == \
        (False, "PATH_NOT_ALLOWED")
    assert not (outputs / ".env").exists()


async def test_save_file_safely_into_a_given_folder(tmp_path: Path) -> None:
    folder = tmp_path / "custom"
    ok, path = await nap.notepad_automation.save_file_safely("c", "f.py", folder_path=str(folder))
    assert ok is True
    assert (folder / "f.py").read_text(encoding="utf-8") == "c"
    assert Path(path) == (folder / "f.py").resolve()


async def test_save_file_safely_reports_os_errors() -> None:
    with patch.object(nap.os, "makedirs", side_effect=OSError("Perm Denied")):
        assert await nap.notepad_automation.save_file_safely("c", "f.txt") == (False, "Perm Denied")


def test_outputs_dir_follows_the_config(monkeypatch: pytest.MonkeyPatch, outputs: Path,
                                        tmp_path: Path) -> None:
    assert nap._outputs_dir() == str(outputs)
    monkeypatch.setattr(nap.config, "outputs_dir", "", raising=False)
    monkeypatch.setattr(nap.config, "project_root", str(tmp_path))
    assert nap._outputs_dir() == os.path.join(str(tmp_path), "Jarvis_Outputs")


async def test_verify_accepts_notepad_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "crlf.py"
    path.write_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n")
    assert await nap.notepad_automation.verify_saved_content(str(path), "line1\nline2") == \
        (True, "verified")
    assert path.read_bytes() == b"\xef\xbb\xbfline1\r\nline2\r\n"


async def test_verify_rewrites_a_wrong_save(tmp_path: Path) -> None:
    path = tmp_path / "half.py"
    path.write_text("half typed", encoding="utf-8")
    assert await nap.notepad_automation.verify_saved_content(str(path), "full\ncontent\n") == \
        (True, "fallback")
    assert path.read_text(encoding="utf-8") == "full\ncontent\n"


async def test_verify_writes_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    assert await nap.notepad_automation.verify_saved_content(str(path), "body") == \
        (True, "fallback")
    assert path.read_text(encoding="utf-8") == "body"


# --------------------------------------------------------------------------- closing


async def test_automated_close_is_pid_bound_and_graceful(gui: Any) -> None:
    """Replaces the old 'taskkill /f /im notepad.exe' expectation of force=True."""
    assert await nap.notepad_automation.close_active_notepad(hwnd=1, pid=555,
                                                             force_kill_pid=True) is True
    gui.close.assert_awaited_once_with([1], wait_s=2.0)
    assert gui.run.call_args_list == [
        call([nap.TASKKILL_EXE, "/PID", "555", "/F"], check=False, capture_output=True)]
    assert not _kills_by_image_name(gui.run)


async def test_close_sends_wm_close_to_our_window_and_never_alt_f4(gui: Any) -> None:
    """Replaces the old pyautogui.hotkey('alt', 'f4') expectation."""
    gui.close.return_value = {"closed": [], "still_open": [5]}  # Notepad shows its Save prompt
    nap.notepad_automation._session_hwnd = 5
    assert await nap.notepad_automation.close_active_notepad() is True
    gui.close.assert_awaited_once_with([5], wait_s=2.0)
    gui.hotkey.assert_not_called()
    gui.press.assert_not_called()
    gui.run.assert_not_called()
    assert nap.notepad_automation._session_hwnd is None


async def test_close_uses_the_launched_session(gui: Any, outputs: Path) -> None:
    await nap.notepad_automation.launch_notepad_for_file(str(outputs / "a.py"))
    assert await nap.notepad_automation.close_active_notepad(force_kill_pid=True) is True
    gui.close.assert_awaited_once_with([OUR_HWND], wait_s=2.0)
    assert gui.run.call_args_list == [KILL_OURS]


async def test_close_without_a_session_touches_nothing(gui: Any) -> None:
    assert await nap.notepad_automation.close_active_notepad(force_kill_pid=True) is False
    gui.close.assert_not_awaited()
    gui.run.assert_not_called()
    gui.hotkey.assert_not_called()


async def test_close_error_is_reported(gui: Any) -> None:
    gui.close.side_effect = RuntimeError("WM_CLOSE failed")
    assert await nap.notepad_automation.close_active_notepad(hwnd=9, pid=11,
                                                             force_kill_pid=True) is False
    gui.run.assert_not_called()


async def test_kill_all_runs_only_when_asked_for(gui: Any) -> None:
    assert await nap.notepad_automation.close_active_notepad(kill_all_im=True) is True
    assert gui.run.call_args_list == [KILL_ALL]
    gui.close.assert_not_awaited()


async def test_close_notepad_tool_default_is_graceful() -> None:
    manager = nap.get_notepad_mgr()
    with patch.object(manager, "close_active_notepad", new_callable=AsyncMock,
                      return_value=True) as close:
        assert await nap.close_notepad_tool() == {"status": "success", "message": "Notepad closed."}
        close.return_value = False
        assert await nap.close_notepad_tool() == {"status": "error",
                                                  "message": "Notepad close nahi ho saka."}
    assert close.await_args_list == [call(force_kill_pid=False), call(force_kill_pid=False)]


async def test_close_notepad_tool_force_kills_every_notepad_once_run() -> None:
    with patch.object(nap.get_notepad_mgr(), "close_active_notepad", new_callable=AsyncMock,
                      return_value=True) as close:
        assert (await nap.close_notepad_tool(force=True))["status"] == "success"
    close.assert_awaited_once_with(kill_all_im=True)


def test_close_notepad_risk() -> None:
    assert nap._close_notepad_risk({"force": True}) == "medium"
    assert nap._close_notepad_risk({"force": False}) == "low"
    assert nap._close_notepad_risk({}) == "low"
    meta = plugin_manager.get_tool_meta("close_notepad_tool")
    assert meta is not None and meta["risk"]({"force": True}) == "medium"


async def test_close_notepad_tool_force_waits_for_confirmation(confirm: Any) -> None:
    tool = _registered("close_notepad_tool")
    with patch.object(nap.subprocess, "run") as run:
        asked = await tool(force=True)
        assert asked["status"] == "needs_confirmation"
        assert asked["risk"] == "medium"
        run.assert_not_called()
        event, payload = confirm.bridge.sent[0]
        assert event == "confirm_request"
        assert (payload["tool"], payload["risk"]) == ("close_notepad_tool", "medium")
        assert (await confirm.broker.resolve(asked["confirm_id"], True))["status"] == "started"
        await _until(lambda: confirm.results)
    assert confirm.results[0]["status"] == "success"
    assert run.call_args_list == [KILL_ALL]


async def test_close_notepad_tool_force_denied_kills_nothing(confirm: Any) -> None:
    tool = _registered("close_notepad_tool")
    with patch.object(nap.subprocess, "run") as run:
        asked = await tool(force=True)
        assert (await confirm.broker.resolve(asked["confirm_id"], False))["status"] == "cancelled"
        for _ in range(20):
            await asyncio.sleep(0)
    run.assert_not_called()
    assert confirm.results == []


async def test_close_notepad_tool_default_needs_no_confirmation(confirm: Any, gui: Any) -> None:
    nap.notepad_automation._session_hwnd = OUR_HWND
    result = await _registered("close_notepad_tool")()
    assert result["status"] == "success"
    assert confirm.bridge.sent == []
    gui.close.assert_awaited_once_with([OUR_HWND], wait_s=2.0)
    gui.run.assert_not_called()


# --------------------------------------------------------------------------- automated flows


async def test_template_flow_types_saves_closes_ours_and_opens_the_page(gui: Any,
                                                                        outputs: Path) -> None:
    result = await nap.create_template_code("html_login", "login.html", auto_run=True)
    full = (outputs / "login.html").resolve()
    assert result["status"] == "success"
    # The mock Notepad saved nothing, so the read-back falls back to a programmatic save.
    assert result["data"] == {"filename": "login.html", "path": str(full), "save_mode": "fallback"}
    assert full.read_text(encoding="utf-8") == nap.HTML_LOGIN_TEMPLATE
    gui.launch.assert_awaited_once_with(str(full))
    gui.typing.assert_awaited_once_with(nap.HTML_LOGIN_TEMPLATE)
    gui.hotkey.assert_called_once_with("ctrl", "s")
    gui.close.assert_awaited_once_with([OUR_HWND], wait_s=2.0)
    assert gui.run.call_args_list == [KILL_OURS]
    gui.browser.assert_called_once_with(full.as_uri())
    gui.popen.assert_not_called()


async def test_python_autorun_uses_the_venv_python(gui: Any, outputs: Path) -> None:
    with patch.object(nap.notepad_automation, "verify_saved_content", new_callable=AsyncMock,
                      return_value=(True, "verified")):
        result = await nap.write_custom_code("print(1)", "run_me.py", auto_run=True)
    full = str((outputs / "run_me.py").resolve())
    assert result["status"] == "success"
    assert result["message"] == ("Code written to 'run_me.py' (save: verified).\n"
                                 "Python script run ho rahi hai.")
    gui.popen.assert_called_once_with(["cmd", "/c", "start", "cmd", "/k", sys.executable, full])
    gui.browser.assert_not_called()
    assert gui.run.call_args_list == [KILL_OURS]
    assert not _kills_by_image_name(gui.run)


async def test_no_autorun_runs_nothing(gui: Any) -> None:
    result = await nap.write_custom_code("print(1)", "custom.py", auto_run=False)
    assert result["status"] == "success"
    gui.typing.assert_awaited_once_with("print(1)")
    gui.popen.assert_not_called()
    gui.browser.assert_not_called()


async def test_focus_failure_saves_the_code_without_typing(gui: Any, outputs: Path) -> None:
    gui.launch.side_effect = None
    gui.launch.return_value = (False, None, OUR_PID)
    result = await nap.write_custom_code("x = 1\n", "x.py", auto_run=False)
    assert result["status"] == "success"
    assert result["message"] == "Notepad focus failed. File saved programmatically."
    assert result["data"]["save_mode"] == "programmatic"
    assert (outputs / "x.py").read_text(encoding="utf-8") == "x = 1\n"
    gui.typing.assert_not_awaited()
    gui.hotkey.assert_not_called()


async def test_failed_verification_saves_programmatically(gui: Any, outputs: Path) -> None:
    with patch.object(nap.notepad_automation, "verify_saved_content", new_callable=AsyncMock,
                      return_value=(False, "fallback_failed")):
        result = await nap.write_custom_code("body", "v.txt", auto_run=False)
    assert result["data"]["save_mode"] == "fallback"
    assert (outputs / "v.txt").read_text(encoding="utf-8") == "body"


async def test_typing_failure_is_an_error_and_runs_nothing(gui: Any) -> None:
    gui.typing.return_value = False
    result = await nap.write_custom_code("print(1)", "t.py", auto_run=True)
    assert result == {"status": "error", "message": "Error: Typing failed"}
    gui.hotkey.assert_not_called()
    gui.popen.assert_not_called()
    gui.browser.assert_not_called()


async def test_template_errors(gui: Any) -> None:
    assert await nap.create_template_code("invalid", "", auto_run=False) == {
        "status": "error", "message": "Unsupported code type. Use 'html_login' or 'python_hello'."}
    no_extension = await nap.create_template_code("python_hello", "notes", auto_run=False)
    assert no_extension["error_code"] == "PATH_NOT_ALLOWED"
    with patch.object(nap.notepad_automation, "save_file_safely", new_callable=AsyncMock,
                      return_value=(False, "Err")):
        assert await nap.create_template_code("html_login", "f.html", auto_run=False) == {
            "status": "error", "message": "File initialization failed: Err"}
    gui.launch.assert_not_awaited()


@pytest.mark.parametrize("filename", ["", "..\\..\\.env", "..\\secret.txt", "sub\\..\\..\\x.py",
                                      "C:\\Windows\\win.ini", "C:x.py", "tool.exe", "notes",
                                      # X-65: basename only, even for a sub-folder of the outputs
                                      "sub/x.py", "sub\\x.py", ".", ".."])
async def test_unsafe_file_names_are_refused(gui: Any, filename: str) -> None:
    result = await nap.write_custom_code("x", filename, auto_run=True)
    assert result["status"] == "error"
    assert result["error_code"] == "PATH_NOT_ALLOWED"
    gui.launch.assert_not_awaited()
    gui.popen.assert_not_called()


async def test_unexpected_errors_become_error_results(gui: Any) -> None:
    with patch.object(nap.notepad_automation, "save_file_safely",
                      side_effect=RuntimeError("disk gone")):
        assert await nap.write_custom_code("c", "f.txt", auto_run=False) == {
            "status": "error", "message": "Error: disk gone"}
    with patch.object(nap, "get_template_content", side_effect=ValueError("Critical")):
        assert await nap.create_template_code("python_hello", "f.py") == {
            "status": "error", "message": "Error: Critical"}
    gui.launch.assert_not_awaited()


async def test_gui_tools_refuse_while_desktop_control_is_off(gui: Any, outputs: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nap, "desktop_control_enabled", desktop_common.desktop_control_enabled)
    monkeypatch.setenv("JARVIS_DESKTOP_CONTROL", "0")
    outputs.mkdir(parents=True)
    (outputs / "a.txt").write_text("old", encoding="utf-8")
    results = [await nap.write_custom_code("x", "a.py", auto_run=True),
               await nap.create_template_code("python_hello", "b.py"),
               await nap.edit_notepad_file_tool("a.txt", "new"),
               await nap.append_notepad_file_tool("a.txt", "more")]
    assert [r["error_code"] for r in results] == ["DESKTOP_CONTROL_DISABLED"] * 4
    gui.launch.assert_not_awaited()
    gui.popen.assert_not_called()
    assert (outputs / "a.txt").read_text(encoding="utf-8") == "old"
    assert not (outputs / "a.py").exists()


def test_write_custom_code_risk_and_summary() -> None:
    for name in ("x.py", "x.PY", "x.bat", "x.cmd", "x.ps1", "x.js"):
        assert nap._write_custom_code_risk({"filename": name}) == "medium", name
    assert nap._write_custom_code_risk({"filename": "x.py", "auto_run": False}) == "low"
    assert nap._write_custom_code_risk({"filename": "page.html", "auto_run": True}) == "low"
    assert nap._write_custom_code_summary({"filename": "a.py", "content": "abc"}) == \
        "a.py Notepad mein likh kar RUN karna hai (3 chars)"


# --------------------------------------------------------------------------- edit and append


async def test_edit_backs_up_and_replaces_through_our_window(gui: Any, outputs: Path) -> None:
    outputs.mkdir(parents=True)
    note = outputs / "note.txt"
    note.write_text("old", encoding="utf-8")
    with patch.object(nap, "_clipboard_paste_text_sync") as paste:
        result = await nap.edit_notepad_file_tool("note.txt", "new body")
    assert result["status"] == "success"
    assert (outputs / "note.txt.bak").read_text(encoding="utf-8") == "old"
    assert note.read_text(encoding="utf-8") == "new body"
    paste.assert_called_once_with("new body")
    assert gui.hotkey.call_args_list == [call("ctrl", "a"), call("ctrl", "s")]
    assert gui.run.call_args_list == [KILL_OURS]


async def test_append_types_at_the_end_and_saves(gui: Any, outputs: Path) -> None:
    outputs.mkdir(parents=True)
    note = outputs / "log.txt"
    note.write_text("line1", encoding="utf-8")
    result = await nap.append_notepad_file_tool("log.txt", "line2")
    assert result == {"status": "success", "message": "Text successfully append kar diya gaya hai."}
    assert gui.hotkey.call_args_list == [call("ctrl", "end"), call("ctrl", "s")]
    gui.press.assert_called_once_with("enter")
    gui.typing.assert_awaited_once_with("line2")
    content = note.read_text(encoding="utf-8")
    assert content.startswith("line1") and "line2" in content
    assert gui.run.call_args_list == [KILL_OURS]


async def test_edit_and_append_refuse_unsafe_or_missing_files(gui: Any) -> None:
    for result in (await nap.edit_notepad_file_tool("..\\..\\.env", "x"),
                   await nap.append_notepad_file_tool("..\\secret.txt", "x")):
        assert result["error_code"] == "PATH_NOT_ALLOWED"
    assert await nap.edit_notepad_file_tool("nope.txt", "x") == {
        "status": "error", "message": "File 'nope.txt' nahi mili."}
    gui.launch.assert_not_awaited()


# --------------------------------------------------------------------------- other tools


async def test_run_cmd_command(gui: Any) -> None:
    """X-31: a command outside the READ_ONLY allow-list (approved first, K-45) opens one visible 'cmd /k' window
    with the command as ONE argument, as typed; a launch failure and an empty command are errors."""
    assert (await nap.run_cmd_command('echo "hi there"'))["status"] == "success"
    gui.popen.assert_called_once_with(["cmd", "/c", "start", "cmd", "/k", 'echo "hi there"'])
    gui.popen.side_effect = OSError("Boom")
    assert (await nap.run_cmd_command("dir"))["status"] == "error"
    gui.popen.side_effect = None
    assert (await nap.run_cmd_command("   "))["status"] == "error"


async def test_open_notepad_simple(gui: Any) -> None:
    assert await nap.open_notepad_simple() == {"status": "success",
                                               "message": "Notepad opened successfully"}
    gui.popen.side_effect = [FileNotFoundError("no system32 notepad"), MagicMock()]
    assert (await nap.open_notepad_simple())["status"] == "success"
    assert gui.popen.call_args_list == [call([nap.NOTEPAD_EXE]), call([nap.NOTEPAD_EXE]),
                                        call(["notepad"])]
    gui.popen.side_effect = OSError("Boom")
    assert (await nap.open_notepad_simple())["status"] == "error"


async def test_list_notepad_files_newest_first(outputs: Path) -> None:
    assert (await nap.list_notepad_files_tool())["status"] == "empty"
    outputs.mkdir(parents=True)
    assert await nap.list_notepad_files_tool() == {"status": "empty",
                                                   "message": "Jarvis_Outputs folder khali hai."}
    old = outputs / "old.py"
    old.write_text("1", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    (outputs / "new.txt").write_text("2", encoding="utf-8")
    result = await nap.list_notepad_files_tool()
    assert result["status"] == "success"
    assert [f["name"] for f in result["data"]["files"]] == ["new.txt", "old.py"]


def test_template_contents_and_default_names() -> None:
    content, name = nap.get_template_content("html_login", "")
    assert "JARVIS LOGIN" in content and name.startswith("login_") and name.endswith(".html")
    content, name = nap.get_template_content("Python_Hello", "my.py")
    assert 'print("Hello World from JARVIS!")' in content and name == "my.py"
    content, name = nap.get_template_content("python_hello", "")
    assert name.startswith("hello_") and name.endswith(".py")
    for kind in ("amazing_code", "heart_animation"):
        content, name = nap.get_template_content(kind, "")
        assert "heart animation" in content and name.startswith("amazing_heart_")
    assert nap.get_template_content("invalid", "x.py") == ("", "x.py")
