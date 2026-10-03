"""X-27: Notepad tools — safe paths, PID focus, typing, verified save."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _safe_tmp_dir() -> Path:
    base = Path.home() / "Desktop"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=str(base)))

import services.automation.jarvis_notepad_automation as nap
from services.ai_core import jarvis_plugin_manager as pm


@pytest.fixture(autouse=True)
def _reset_notepad_session() -> None:
    nap.notepad_automation.clear_session()
    yield
    nap.notepad_automation.clear_session()


@pytest.mark.asyncio
async def test_traversal_and_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = _safe_tmp_dir()
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp))
    res = await nap.write_custom_code("x", r"..\..\..\.env", auto_run=False)
    assert res["status"] == "error"
    assert res["error_code"] == "PATH_NOT_ALLOWED"

    res2 = await nap.edit_notepad_file_tool(r"..\..\.env", "hack")
    assert res2["error_code"] == "PATH_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_pid_focus_launch_uses_wait_for_window(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = _safe_tmp_dir()
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp))
    target = tmp / "hello.py"
    target.write_text("", encoding="utf-8")

    fake_win = nap.WindowInfo(hwnd=42, title="hello.py - Notepad", pid=999, process_name="notepad.exe")
    mock_popen = MagicMock()
    mock_popen.pid = 999

    with patch.object(nap, "wait_for_window", new_callable=AsyncMock, return_value=fake_win) as mock_wait:
        with patch.object(nap, "force_foreground", new_callable=AsyncMock, return_value=True):
            with patch.object(nap.subprocess, "Popen", return_value=mock_popen):
                ok, win, pid = await nap.notepad_automation.launch_notepad_for_file(str(target))

    assert ok is True
    assert win is fake_win
    assert pid == 999
    mock_wait.assert_awaited()
    first_kwargs = mock_wait.await_args_list[0].kwargs
    assert first_kwargs.get("pid") == 999


@pytest.mark.asyncio
async def test_automated_close_never_uses_im_notepad_exe(tmp_path: Path) -> None:
    with patch.object(nap, "close_windows_gracefully", new_callable=AsyncMock, return_value={"closed": [1], "still_open": []}):
        with patch.object(nap.subprocess, "run") as mock_run:
            await nap.notepad_automation.close_active_notepad(hwnd=1, pid=555, force_kill_pid=True)
            for call in mock_run.call_args_list:
                args = call[0][0] if call[0] else call.kwargs.get("args", [])
                assert "/IM" not in args
                if args:
                    assert args[0].endswith("taskkill.exe")
                    assert "/PID" in args


def test_type_text_preserves_newlines() -> None:
    cancel = MagicMock()
    cancel.is_set.return_value = False
    lines_seen: list[str] = []

    def capture(line: str, interval: float = 0) -> None:
        lines_seen.append(line)

    mock_pg = MagicMock()
    mock_pg.write.side_effect = capture
    with patch.object(nap, "_pyautogui_mod", return_value=mock_pg):
        nap._type_text(cancel, "line1\nline2\n", visible_limit=1500)
    assert lines_seen == ["line1", "line2"]
    assert mock_pg.press.call_args_list[0][0][0] == "enter"


@pytest.mark.asyncio
async def test_edit_creates_bak(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = _safe_tmp_dir()
    monkeypatch.setattr(nap, "_outputs_dir", lambda: str(tmp))
    src = tmp / "note.txt"
    src.write_text("old", encoding="utf-8")

    with patch.object(nap, "desktop_control_enabled", return_value=True):
        with patch.object(
            nap.notepad_automation,
            "launch_notepad_for_file",
            new_callable=AsyncMock,
            return_value=(True, None, 1),
        ):
            with patch.object(nap, "_pyautogui_mod", return_value=MagicMock()):
                with patch.object(nap, "_clipboard_paste_text_sync"):
                    with patch.object(
                        nap.notepad_automation,
                        "verify_saved_content",
                        new_callable=AsyncMock,
                        return_value=(True, "verified"),
                    ):
                        with patch.object(
                            nap.notepad_automation,
                            "close_active_notepad",
                            new_callable=AsyncMock,
                            return_value=True,
                        ):
                            res = await nap.edit_notepad_file_tool("note.txt", "new body")

    assert res["status"] == "success"
    assert (tmp / "note.txt.bak").is_file()
    assert (tmp / "note.txt.bak").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_verify_saved_content_normalizes_crlf(tmp_path: Path) -> None:
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"\xef\xbb\xbfline1\r\nline2\r\n")

    ok, mode = await nap.notepad_automation.verify_saved_content(str(path), "line1\nline2\n")
    assert ok is True
    assert mode == "verified"


@pytest.mark.asyncio
async def test_write_custom_code_raw_content_policy() -> None:
    meta = pm.plugin_manager.get_tool_meta("write_custom_code")
    assert meta is not None
    assert meta["arg_policies"].get("content") == "raw"
    assert meta["exclusive"] == "desktop_input"
    assert meta["timeout"] == 120


def test_write_custom_code_risk_medium_for_autorun_py() -> None:
    assert nap._write_custom_code_risk({"filename": "x.py", "auto_run": True}) == "medium"
    assert nap._write_custom_code_risk({"filename": "x.py", "auto_run": False}) == "low"
    assert nap._write_custom_code_risk({"filename": "x.txt", "auto_run": True}) == "low"
