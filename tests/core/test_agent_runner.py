# pylint: disable=redefined-outer-name, unused-variable, import-outside-toplevel
import asyncio
import hashlib
import hmac
import json
import threading
import types
from typing import Any

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


# ------------------------------------------------------------------ C-23 (K-60/K-68 v9): how a job ends


class _FakeLocal:
    def __init__(self) -> None:
        self.attributes: list[str] = []

    async def set_attributes(self, attributes):
        self.attributes.append(attributes["jarvis.session"])


class _FakeRoom:
    name = "jarvis-hybrid"

    def __init__(self) -> None:
        self.local_participant = _FakeLocal()
        self.remote_participants = {}

    def on(self, _event):
        return lambda fn: fn


class _FakeJobContext:
    def __init__(self, *, connect_error=None, fake_job=False) -> None:
        self.room = _FakeRoom()
        self.connects = 0
        self.connect_error = connect_error
        self.shutdowns: list[str] = []
        self.fake_job = fake_job

    async def connect(self):
        self.connects += 1
        if self.connect_error is not None:
            raise self.connect_error

    def shutdown(self, reason=""):
        self.shutdowns.append(reason)

    def is_fake_job(self):
        return self.fake_job

    def delete_room(self, *_args, **_kwargs):
        raise AssertionError("the agent never deletes the room (K-60 v9)")

    def add_shutdown_callback(self, _callback):
        return None


@pytest.fixture
def job(monkeypatch):
    """agent_runner.entrypoint with light stand-ins for everything it builds (no LiveKit, Gemini, plugins or
    loops); the assistant is a stand-in with the real C-23 stop event and request_end."""
    import types

    from src.core import agent_core, agent_runner as ar

    state = types.SimpleNamespace(sessions=[], assistants=[], room_options=[], on_start=None, cleanups=0)

    class FakeAssistant:
        def __init__(self, **_kwargs):
            self._stop_event = asyncio.Event()
            self.end_reason = None
            self.request_end = agent_core.BrainAssistant.request_end.__get__(self)
            state.assistants.append(self)

        def attach_router(self, _router):
            return None

        def attach_session(self, _session):
            return None

    class FakeSession:
        def __init__(self, **_kwargs):
            state.sessions.append(self)

        def on(self, _event):
            return lambda fn: fn

        async def start(self, room, agent, room_options):
            state.room_options.append(room_options)
            if state.on_start is not None:
                state.on_start(agent)

    class FakeRouter:
        text_input_cb = None

        def __init__(self, *_args, **_kwargs):
            pass

        def attach(self, _session):
            return None

        def detach(self):
            return None

    class FakePlugins:
        running_tools = staticmethod(set)

        def discover_plugins(self, _path):
            return None

        def get_livekit_tools(self):
            return []

    async def nothing(*_args, **_kwargs):
        return None

    async def no_tasks(*_args, **_kwargs):
        return []

    async def returning(result):
        return result

    async def cleanup(*_args, **_kwargs):
        state.cleanups += 1

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay, result=None):
        await real_sleep(0)
        return result

    from services.ai_core import jarvis_plugin_manager

    for name, value in (("_prewarm_vector_memory", lambda *_a: None), ("_prewarm_voice_id", lambda *_a: None),
                        ("get_formatted_datetime", lambda: returning({"formatted": "c23-day"})),
                        ("get_current_city", lambda: returning("Lahore")),
                        ("silero", types.SimpleNamespace(VAD=types.SimpleNamespace(load=lambda **_k: object()))),
                        ("google", types.SimpleNamespace(realtime=types.SimpleNamespace(
                            RealtimeModel=lambda **_k: types.SimpleNamespace(voice="Charon")))),
                        ("_resolve_user_id", lambda _room: "c23-user"), ("BrainAssistant", FakeAssistant),
                        ("ConversationRouter", FakeRouter), ("AgentSession", FakeSession),
                        ("harden_room_input", lambda *_a, **_k: []), ("follow_owner_reconnects", lambda *_a: None),
                        ("_location_loop", nothing), ("_pc_vitals_loop", nothing),
                        ("_print_startup_banner", lambda: None), ("_start_loop_watchdog", lambda *_a: None),
                        ("_start_background_tasks", no_tasks), ("_rag_warm_delay", lambda: None),
                        ("_cleanup_session_resources", cleanup), ("_quiet_livekit_agents_logger", lambda: None)):
        monkeypatch.setattr(ar, name, value)
    monkeypatch.setattr(jarvis_plugin_manager, "JarvisPluginManager", FakePlugins)
    monkeypatch.setattr(ar.asyncio, "sleep", fast_sleep)  # the retry waits (5, 10, 15, 20 s) take no time
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    state.ar = ar
    return state


async def _run_entrypoint(job, ctx):
    await asyncio.wait_for(job.ar.entrypoint(ctx), 10)
    for _ in range(5):  # let the best-effort attribute tasks finish
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_pause_agent_ends_the_job_with_paused_by_owner(job):
    job.on_start = lambda agent: asyncio.get_running_loop().call_later(0.05, agent.request_end, "paused_by_owner")
    ctx = _FakeJobContext()
    await _run_entrypoint(job, ctx)
    assert len(job.sessions) == 1  # no second session in this job
    assert ctx.shutdowns == ["paused_by_owner"]
    assert ctx.room.local_participant.attributes == ["starting", "running", "paused"]
    assert job.cleanups == 1


@pytest.mark.asyncio
async def test_a_pause_that_arrives_before_the_wait_is_not_lost(job):
    """The stop event comes with the assistant: a pause_agent during session start (before the entrypoint waits)
    still ends the job."""
    job.on_start = lambda agent: agent.request_end("paused_by_owner")
    ctx = _FakeJobContext()
    await _run_entrypoint(job, ctx)
    assert len(job.sessions) == 1 and ctx.shutdowns == ["paused_by_owner"]


@pytest.mark.asyncio
async def test_a_stop_event_without_a_reason_ends_the_job_too(job):
    job.on_start = lambda agent: agent._stop_event.set()  # an older sender that sets the event itself
    ctx = _FakeJobContext()
    await _run_entrypoint(job, ctx)
    assert ctx.shutdowns == ["ended_by_command"] and ctx.room.local_participant.attributes[-1] == "closed"


@pytest.mark.asyncio
async def test_five_failed_attempts_end_the_job_as_session_failed(job):
    ctx = _FakeJobContext(connect_error=RuntimeError("room connect failed"))
    await _run_entrypoint(job, ctx)
    assert ctx.connects == 5
    assert ctx.shutdowns == ["session_failed"]  # the bridge can dispatch a fresh job instead of a session-less one
    assert ctx.room.local_participant.attributes == ["restarting"] * 4 + ["closed"]


@pytest.mark.asyncio
async def test_a_cancelled_job_is_not_shut_down_again(job):
    ctx = _FakeJobContext()
    task = asyncio.ensure_future(job.ar.entrypoint(ctx))
    for _ in range(50):
        if job.sessions:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert ctx.shutdowns == []


@pytest.mark.asyncio
async def test_a_job_end_is_reported_to_the_bridge_before_the_shutdown(job, monkeypatch):
    """notify job_end {job_id, reason} (K-40/K-60 v9): the bridge's room owner stops counting the job as in flight
    (LiveKit v1.13.7 never marks a job ended); a failing notify never delays the end beyond its timeout."""
    from services.utils import jarvis_bridge

    events: list[tuple[str, Any]] = []

    async def notify_event(event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr(jarvis_bridge, "notify_event", notify_event)
    job.on_start = lambda agent: agent.request_end("paused_by_owner")
    ctx = _FakeJobContext()
    ctx.job = types.SimpleNamespace(id="AJ_c23job")
    await _run_entrypoint(job, ctx)
    assert [event for event in events if event[0] == "job_end"] == [
        ("job_end", {"job_id": "AJ_c23job", "reason": "paused_by_owner"})]  # (log lines may travel too)
    assert ctx.shutdowns == ["paused_by_owner"]

    async def unreachable(event_type, _payload):
        if event_type == "job_end":  # (log lines forwarded meanwhile are not this test's business)
            raise ConnectionError("bridge down")

    monkeypatch.setattr(jarvis_bridge, "notify_event", unreachable)
    failed = _FakeJobContext(connect_error=RuntimeError("room connect failed"))
    failed.job = types.SimpleNamespace(id="AJ_c23failed")
    await _run_entrypoint(job, failed)
    assert failed.shutdowns == ["session_failed"]


@pytest.mark.asyncio
async def test_a_job_waits_only_on_its_own_attribute_writes():
    """R7: livekit-agents runs each Windows job in its own thread and loop; ending one job never waits on another
    job's jarvis.session write (another loop's task)."""
    from src.core import agent_runner as ar

    other_loop = asyncio.new_event_loop()
    started = threading.Event()

    class SlowParticipant:
        async def set_attributes(self, _attributes):
            started.set()
            await asyncio.sleep(30)  # another job's write that hangs

    slow_ctx = types.SimpleNamespace(room=types.SimpleNamespace(local_participant=SlowParticipant()))
    thread = threading.Thread(target=other_loop.run_forever, daemon=True)
    thread.start()
    try:
        other_loop.call_soon_threadsafe(ar._set_session_attr, slow_ctx, "running")
        assert started.wait(5)
        assert ar._pending_session_attr_tasks() == []  # this loop has none of its own
        own = ar._set_session_attr(_FakeJobContext(), "starting")
        assert ar._pending_session_attr_tasks() == [own]
        await asyncio.wait_for(own, 5)
        loop_started = asyncio.get_running_loop().time()
        await ar._end_job(_FakeJobContext(), "paused_by_owner")
        assert asyncio.get_running_loop().time() - loop_started < ar.SESSION_ATTR_TIMEOUT_S
    finally:
        async def cancel_all() -> None:
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(cancel_all(), other_loop).result(5)
        other_loop.call_soon_threadsafe(other_loop.stop)
        thread.join(5)
        other_loop.close()


@pytest.mark.asyncio
async def test_console_mode_keeps_its_fake_job(job):
    job.on_start = lambda agent: agent.request_end("paused_by_owner")
    ctx = _FakeJobContext(fake_job=True)
    await _run_entrypoint(job, ctx)
    assert ctx.shutdowns == []


def test_session_room_options_never_delete_the_room():
    from src.core import agent_runner as ar

    assert ar.session_room_options().delete_room_on_close is False
    assert ar.session_room_options(lambda *_a: None).delete_room_on_close is False
    source = __import__("pathlib").Path(ar.__file__).read_text(encoding="utf-8")
    assert "delete_room(" not in source
