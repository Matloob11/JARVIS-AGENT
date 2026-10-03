# pylint: disable=redefined-outer-name, unused-variable, import-outside-toplevel
import asyncio
import hashlib
import hmac
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from services.utils.jarvis_bridge import notify_ui
from src.core.agent_runner import perform_startup_diagnostics, _start_background_tasks, _cleanup_session_resources


@pytest.fixture
def mock_runner_deps():
    with patch("services.utils.jarvis_logger.setup_logger"):
        with patch("services.utils.jarvis_diagnostics.diagnostics.run_all", new_callable=AsyncMock) as mock_diag:
            yield mock_diag


# C-138: throwaway keys (never the real .env values), so both notify paths run the same on every machine.
_AGENT_TOKEN = "c138-test-agent-token"
_HMAC_SECRET = "c138-test-bridge-hmac-secret"
_LEGACY_TOKEN = "c138-test-legacy-vortex-token"


@pytest.fixture
def bridge_keys(monkeypatch):
    """Sets the agent link keys of one link mode on the bridge config: 'v2' (signed) or 'legacy'."""
    from services.utils import jarvis_bridge

    def apply(mode):
        v2 = mode == "v2"
        monkeypatch.setattr(jarvis_bridge.config, "agent_token", _AGENT_TOKEN if v2 else None)
        monkeypatch.setattr(jarvis_bridge.config, "bridge_hmac_secret", _HMAC_SECRET if v2 else None)
        monkeypatch.setattr(jarvis_bridge.config, "security_token", _LEGACY_TOKEN)
        monkeypatch.setattr(jarvis_bridge.config, "env", "development")
        monkeypatch.setattr(jarvis_bridge, "_link_logged", set())

    return apply


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["v2", "legacy"])
async def test_notify_ui(bridge_keys, mode):
    bridge_keys(mode)
    with patch("services.utils.jarvis_bridge.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        await notify_ui("START")
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
    headers = kwargs["headers"]
    if mode == "v2":
        # C-15: the signed body goes as content= (agent link v2), signed over exactly the bytes sent
        raw = kwargs["content"]
        body = json.loads(raw)
        assert headers["X-Agent-Token"] == _AGENT_TOKEN
        signed = f"{headers['X-Agent-Timestamp']}.{headers['X-Agent-Nonce']}.".encode() + raw
        assert headers["X-Agent-Signature"] == hmac.new(_HMAC_SECRET.encode(), signed, hashlib.sha256).hexdigest()
        assert "json" not in kwargs
    else:
        # the legacy body goes as json=, signed over its sorted JSON with the legacy token
        body = kwargs["json"]
        assert headers["X-Vortex-Token"] == _LEGACY_TOKEN
        expected = hmac.new(_LEGACY_TOKEN.encode(), json.dumps(body, sort_keys=True).encode(),
                            hashlib.sha256).hexdigest()
        assert headers["X-Vortex-Signature"] == expected
        assert "content" not in kwargs
    assert body == {"type": "status", "payload": "START"}


@pytest.mark.asyncio
async def test_perform_startup_diagnostics(mock_runner_deps):
    mock_diag_internal = mock_runner_deps
    mock_diag_internal.return_value = ["Health check ok"]

    await perform_startup_diagnostics()
    mock_diag_internal.assert_called_once()


@pytest.mark.asyncio
async def test_start_background_tasks(mock_runner_deps):
    session = MagicMock()
    assistant = MagicMock()
    assistant.memory_extractor = MagicMock()

    with patch("src.core.agent_runner.autonomous_protector.run_protected", new_callable=AsyncMock) as mock_run_protected:
        with patch("asyncio.create_task") as mock_create:
            def close_task_coro(coro):
                if hasattr(coro, "close"):
                    coro.close()
                return MagicMock()

            mock_create.side_effect = close_task_coro
            with patch("src.core.agent_runner.ClipboardMonitor"):
                tasks = await _start_background_tasks(session, assistant)
                assert mock_run_protected.call_count == 8
                assert mock_create.call_count == 1


@pytest.mark.asyncio
async def test_cleanup_session_resources(mock_runner_deps):
    # pylint: disable=unused-argument
    session = AsyncMock()

    # Create a real but simple task
    async def simple_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(simple_task())

    await _cleanup_session_resources(session, [task])
    # In Python 3.11, task.cancelling() returns True if cancel was called
    assert task.cancelling() or task.cancelled()
    session.stop.assert_called_once()

    # Final cleanup of the task to avoid warnings
    await asyncio.gather(task, return_exceptions=True)


def test_print_startup_banner():
    with patch("builtins.print") as mock_print:
        from src.core.agent_runner import _print_startup_banner
        _print_startup_banner()
        assert mock_print.call_count >= 5


@pytest.mark.asyncio
async def test_start_memory_loop(mock_runner_deps):
    assistant = MagicMock()
    assistant.chat_ctx = MagicMock()
    assistant.chat_ctx.messages = [
        MagicMock(role="user", content="hello jarvis"),
        MagicMock(role="assistant", content="hi there")
    ]

    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        from src.core.agent_runner import start_memory_loop
        extractor = AsyncMock()
        try:
            await start_memory_loop(assistant, extractor)
        except asyncio.CancelledError:
            pass
        extractor.run.assert_called()


@pytest.mark.asyncio
async def test_on_clipboard_detected_logic():
    session = MagicMock()
    assistant = MagicMock()
    assistant.chat_ctx.messages = []

    with patch("asyncio.create_task"):
        with patch("src.core.agent_runner.ClipboardMonitor.start") as mock_start:
            await _start_background_tasks(session, assistant)
            # The callback is the first positional argument to start()
            assert mock_start.called
            callback = mock_start.call_args[0][0]

            # Trigger callback
            await callback("solution text")
            assert len(assistant.chat_ctx.messages) > 0
            assert "CLIPBOARD ERROR DETECTED" in assistant.chat_ctx.messages[0].content[0]


# ------------------------------------------------------------------ C-138: the test isolation reaches children

_CHILD_PROBE = (
    "import json, os\n"
    "from services.utils.jarvis_config import config\n"
    "print('RESULT ' + json.dumps({\n"
    "    'agent_token': config.agent_token, 'bridge_hmac_secret': config.bridge_hmac_secret,\n"
    "    'encryption_key_set': bool(os.environ.get('JARVIS_ENCRYPTION_KEY')),\n"
    "    'google_api_key_set': bool(os.environ.get('GOOGLE_API_KEY')),\n"
    "}))\n"
)


def _root_conftest():
    """The tests/conftest.py module pytest loaded (looked up, never imported a second time)."""
    import sys
    return next(module for name, module in sys.modules.items()
                if name.endswith("conftest") and hasattr(module, "HIDDEN_SECRET_NAMES"))


def _run_child_probe(env):
    """A fresh interpreter in the project root that imports jarvis_config, as the subprocess tests do; it
    prints only throwaway values and yes/no flags, never a real value."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-c", _CHILD_PROBE], cwd=root, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=300, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("RESULT ")]
    assert result.returncode == 0 and lines, result.stderr[-2000:]
    return json.loads(lines[-1][len("RESULT "):])


@pytest.fixture
def isolated_session():
    if _root_conftest().LIVE_PROBES:
        pytest.skip("JARVIS_LIVE_BRIDGE_URL is set: the live probes use the real credentials on purpose")


@pytest.mark.parametrize("fresh_env", [False, True], ids=["copied_env", "fresh_env"])
def test_a_child_interpreter_never_reads_the_real_secrets(isolated_session, monkeypatch, fresh_env):
    """A child that reloads the root .env through jarvis_config's own load_dotenv gets no secret from it."""
    import os

    monkeypatch.delenv("JARVIS_ENCRYPTION_KEY", raising=False)  # the autouse fixture's throwaway key
    if fresh_env:
        keep = ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "LOG_FILE", "LOG_DIR",
                "UI_BRIDGE_URL")
        env = {name: os.environ[name] for name in keep if name in os.environ}
    else:
        env = {**os.environ}
    assert _run_child_probe(env) == {"agent_token": None, "bridge_hmac_secret": None,
                                     "encryption_key_set": False, "google_api_key_set": False}


def test_a_child_gets_the_throwaway_secret_a_test_sets(isolated_session, monkeypatch):
    import os

    monkeypatch.setenv("AGENT_TOKEN", _AGENT_TOKEN)
    monkeypatch.setenv("BRIDGE_HMAC_SECRET", _HMAC_SECRET)
    probe = _run_child_probe({**os.environ})
    assert (probe["agent_token"], probe["bridge_hmac_secret"]) == (_AGENT_TOKEN, _HMAC_SECRET)


def test_the_link_keys_are_hidden_in_this_process_and_in_children(isolated_session):
    import os

    conftest = _root_conftest()
    assert {"AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "JARVIS_ENCRYPTION_KEY", "WHATSAPP_TEST_NUMBERS"} <= \
        conftest.HIDDEN_SECRET_NAMES
    child = conftest._child_env({"PATH": os.environ.get("PATH", ""), "AGENT_TOKEN": _AGENT_TOKEN})
    assert child["AGENT_TOKEN"] == _AGENT_TOKEN and "BRIDGE_HMAC_SECRET" not in child
    assert "AGENT_TOKEN" not in os.environ and "BRIDGE_HMAC_SECRET" not in os.environ


def test_a_child_env_built_by_the_caller_keeps_exactly_its_names(isolated_session):
    """The isolation adds only its sitecustomize folder (at the end of PYTHONPATH): a scrubbed env stays scrubbed."""
    import os

    conftest = _root_conftest()
    site_dir = str(conftest._CHILD_SITE_DIR)
    assert (conftest._CHILD_SITE_DIR / "sitecustomize.py").is_file()
    scrubbed = {"PATH": "x", "JARVIS_NODE_SUPERVISED": "1"}
    assert conftest._child_env(scrubbed) == {**scrubbed, "PYTHONPATH": site_dir}
    assert scrubbed == {"PATH": "x", "JARVIS_NODE_SUPERVISED": "1"}  # the caller's dict is not changed
    first = os.path.join("C:" + os.sep if os.name == "nt" else os.sep, "snapshot")
    child = conftest._child_env({"PATH": "x", "PYTHONPATH": first})
    assert child["PYTHONPATH"] == os.pathsep.join([first, site_dir])  # the caller's entries keep priority
    assert conftest._child_env(child) == child  # added once


def test_popen_keeps_its_signature_under_the_isolation(isolated_session):
    """net_guard.install_child_env_guard inspects Popen.__init__ and stacks on top of the test wrapper."""
    import inspect
    import subprocess

    names = list(inspect.signature(subprocess.Popen.__init__).parameters)
    assert names[1:2] == ["args"] and names.index("env") == 11


def test_a_child_keeps_a_scrubbed_env_and_still_skips_the_real_secrets(isolated_session):
    """No secret name is added to a child's env (the Desktop Node supervisor checks for absence), and the
    child's own dotenv still drops the real .env's secrets; a grandchild with a fresh env does the same."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME", "LOG_FILE", "LOG_DIR",
            "UI_BRIDGE_URL")
    probe = (
        "import json, os, subprocess, sys\n"
        "absent = 'GOOGLE_API_KEY' not in os.environ and 'AGENT_TOKEN' not in os.environ\n"
        "inner = subprocess.run([sys.executable, '-c', " + repr(_CHILD_PROBE) + "], cwd=os.getcwd(),\n"
        "                       env={k: os.environ[k] for k in " + repr(keep) + " if k in os.environ},\n"
        "                       capture_output=True, encoding='utf-8', errors='replace', check=False)\n"
        "lines = [l for l in inner.stdout.splitlines() if l.startswith('RESULT ')]\n"
        "print('OUTER ' + json.dumps({'absent': absent, 'inner': json.loads(lines[-1][7:]) if lines else None}))\n"
    )
    env = {name: os.environ[name] for name in keep if name in os.environ}
    result = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=300, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("OUTER ")]
    assert result.returncode == 0 and lines, result.stderr[-2000:]
    assert json.loads(lines[-1][len("OUTER "):]) == {
        "absent": True,
        "inner": {"agent_token": None, "bridge_hmac_secret": None, "encryption_key_set": False,
                  "google_api_key_set": False}}


def test_only_files_the_running_worker_writes_are_excused_at_session_end():
    import os

    conftest = _root_conftest()
    changed = [(name, f"logs/{name}") for name in ("agent_stderr.log", "agent_stderr.log.1", "telemetry.jsonl",
                                                    "agent_ctl.log", os.path.join("qa_reports", "x.json"),
                                                    "new_from_a_test.log")]
    failing_with_worker = ["logs/agent_ctl.log", f"logs/{os.path.join('qa_reports', 'x.json')}",
                           "logs/new_from_a_test.log"]
    assert conftest._failing_growth(changed, strict=False, worker_running=True) == failing_with_worker
    assert conftest._failing_growth(changed, strict=False, worker_running=False) == [line for _n, line in changed]
    assert conftest._failing_growth(changed, strict=True, worker_running=True) == [line for _n, line in changed]
