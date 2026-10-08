"""X-31: run_cmd_command — strict READ_ONLY allow-list captured, everything else UI-confirmed."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import services.automation.jarvis_notepad_automation as nap
from services.ai_core import jarvis_plugin_manager as pm


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd", "/c", "x"], returncode=returncode, stdout=stdout, stderr=stderr)


# --- Step 5 risk-classification cases ------------------------------------------------------


def test_ipconfig_is_low() -> None:
    assert nap._run_cmd_risk({"command": "ipconfig"}) == "low"
    assert nap._run_cmd_risk({"command": "ipconfig /all"}) == "low"


def test_echo_env_var_is_high() -> None:
    # Shell metachar '%' alone disqualifies it, even before the allow-list check.
    assert nap._run_cmd_risk({"command": "echo %GOOGLE_API_KEY%"}) == "high"


def test_python_inline_code_is_high() -> None:
    assert nap._run_cmd_risk({"command": "python -c print(1)"}) == "high"


def test_dir_path_is_low_and_keeps_backslashes() -> None:
    command = r"dir C:\Users\Matloob"
    assert nap._run_cmd_risk({"command": command}) == "low"


def test_git_status_low_git_push_high() -> None:
    assert nap._run_cmd_risk({"command": "git status"}) == "low"
    assert nap._run_cmd_risk({"command": "git push"}) == "high"


def test_del_is_high_with_exact_command_in_summary() -> None:
    assert nap._run_cmd_risk({"command": "del x"}) == "high"
    assert nap._run_cmd_summary({"command": "del x"}) == "del x"


def test_empty_command_is_high() -> None:
    assert nap._run_cmd_risk({"command": ""}) == "high"
    assert nap._run_cmd_risk({"command": "   "}) == "high"


def test_control_character_is_high() -> None:
    assert nap._run_cmd_risk({"command": "ipconfig\n/all"}) == "high"


def test_unc_path_is_high_even_on_allow_list() -> None:
    assert nap._run_cmd_risk({"command": r"dir \\fileserver\public"}) == "high"
    assert nap._run_cmd_risk({"command": "where //intranet/bin/tool.exe"}) == "high"


def test_equals_in_token_is_high_even_on_allow_list() -> None:
    assert nap._run_cmd_risk({"command": "git diff --output=patch.txt"}) == "high"
    assert nap._run_cmd_risk({"command": "git status --porcelain=v1"}) == "high"


# --- Step 1 metadata (signature, raw_args, timeout) ----------------------------------------


def test_run_cmd_tool_metadata() -> None:
    meta = pm.plugin_manager.get_tool_meta("run_cmd_command")
    assert meta is not None
    assert meta["arg_policies"].get("command") == "raw"
    assert meta["timeout"] == 30
    assert callable(meta["risk"])
    assert callable(meta["confirm_summary"])


# --- Step 3: low risk runs captured with a scrubbed env -------------------------------------


@pytest.mark.asyncio
async def test_ipconfig_captured_with_scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "super-secret-value")
    monkeypatch.setenv("PATH", r"C:\Windows\System32")

    fake = _completed(stdout="IPv4 Address. . . . . . . . . . . : 192.168.1.5\n", returncode=0)
    with patch.object(nap.subprocess, "run", return_value=fake) as mock_run:
        result = await nap.run_cmd_command("ipconfig")

    assert result["status"] == "success"
    assert "192.168.1.5" in result["data"]["output"]
    assert result["data"]["returncode"] == 0

    _args, kwargs = mock_run.call_args
    assert mock_run.call_args[0][0] == ["cmd", "/c", "ipconfig"]
    env = kwargs["env"]
    assert "GOOGLE_API_KEY" not in env
    assert set(env.keys()) <= set(nap._RUN_CMD_ENV_KEYS)
    assert kwargs["timeout"] == 20
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


@pytest.mark.asyncio
async def test_dir_command_keeps_backslashes_in_subprocess_call() -> None:
    command = r"dir C:\Users\Matloob"
    fake = _completed(stdout="dir listing\n", returncode=0)
    with patch.object(nap.subprocess, "run", return_value=fake) as mock_run:
        result = await nap.run_cmd_command(command)

    assert result["status"] == "success"
    passed_cmd = mock_run.call_args[0][0]
    assert passed_cmd == ["cmd", "/c", command]
    assert passed_cmd[2] == command  # no shlex/posix mangling of the backslashes


@pytest.mark.asyncio
async def test_output_capped_at_4000_chars() -> None:
    fake = _completed(stdout="A" * 5000, returncode=0)
    with patch.object(nap.subprocess, "run", return_value=fake):
        result = await nap.run_cmd_command("whoami")

    assert len(result["data"]["output"]) == 4000


# --- Step 4: a confirmed high-risk command opens the visible 'cmd /k' window ----------------


@pytest.mark.asyncio
async def test_high_risk_no_capture_opens_visible_window_only() -> None:
    with patch.object(nap.subprocess, "Popen") as mock_popen:
        with patch.object(nap.subprocess, "run") as mock_run:
            result = await nap.run_cmd_command("del x", capture=False)

    assert result["status"] == "success"
    mock_popen.assert_called_once_with(["cmd", "/c", "start", "cmd", "/k", "del x"])
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_high_risk_with_capture_also_runs_captured() -> None:
    fake = _completed(stdout="done\n", returncode=0)
    with patch.object(nap.subprocess, "Popen") as mock_popen:
        with patch.object(nap.subprocess, "run", return_value=fake) as mock_run:
            result = await nap.run_cmd_command("del x", capture=True)

    assert result["status"] == "success"
    assert "done" in result["data"]["output"]
    mock_popen.assert_called_once_with(["cmd", "/c", "start", "cmd", "/k", "del x"])
    mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_empty_command_is_rejected() -> None:
    result = await nap.run_cmd_command("   ")
    assert result["status"] == "error"
    assert result["error_code"] == "EMPTY_COMMAND"


@pytest.mark.asyncio
async def test_timeout_returns_error() -> None:
    with patch.object(
        nap.subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="ipconfig", timeout=20)
    ):
        result = await nap.run_cmd_command("ipconfig")
    assert result["status"] == "error"
    assert result["error_code"] == "TIMEOUT"
