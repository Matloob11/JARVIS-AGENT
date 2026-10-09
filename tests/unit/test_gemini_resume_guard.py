"""2026-10-06: stale Gemini Live resumption handles are dropped on a 1008 'Requested entity was not
found' failure, so the plugin's next reconnect starts a fresh session instead of replaying the stale
handle (the dashboard test burst: 8x agent_stderr APIError/ConnectionClosedError 1008).

The guard works at our layer only: it wraps RealtimeModel.session on the instance and watches the
'livekit.plugins.google' logger, where livekit-plugins-google 1.4.6 logs every failed attempt before
its own retry sleep. No site-packages edit, no new restart loop; a non-1008 error changes nothing.
"""
# pylint: disable=protected-access,redefined-outer-name

import logging
import weakref
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core import gemini_resume_guard as guard

PLUGIN_LOGGER = logging.getLogger(guard.PLUGIN_LOGGER_NAME)

# The two shapes seen in the live log (agent_stderr burst, 2026-10-06 15:5x PKT).
STALE_API_ERROR = "Gemini Realtime API error: 1008 Requested entity was not found"
STALE_CLOSE_ERROR = ("Gemini Realtime API error: received 1008 (policy violation); "
                     "then sent 1008 (policy violation)")


class FakeRealtimeSession:
    """Only what the guard touches: the pinned private handle attribute (plan.md section 12)."""

    def __init__(self, handle: str | None = "stale-handle") -> None:
        self._session_resumption_handle = handle


@pytest.fixture
def clean_guard(monkeypatch):
    """Fresh watch set, a recorded guard logger, and the log hook installed on the real plugin logger."""
    monkeypatch.setattr(guard, "_sessions", weakref.WeakSet())
    monkeypatch.setattr(guard, "_dropped_total", 0)
    log = MagicMock()
    monkeypatch.setattr(guard, "logger", log)
    guard.install_log_hook()
    return log


def test_is_stale_handle_error_matches_the_observed_failures():
    assert guard.is_stale_handle_error(STALE_API_ERROR)
    assert guard.is_stale_handle_error(STALE_CLOSE_ERROR)
    assert guard.is_stale_handle_error("APIError: 1008 something")
    # A non-1008 failure, and 1008 with no error context at all, change nothing.
    assert not guard.is_stale_handle_error("Gemini Realtime API error: 503 UNAVAILABLE")
    assert not guard.is_stale_handle_error("job 1008 finished")
    assert not guard.is_stale_handle_error("")


def test_a_simulated_1008_clears_the_handle_for_the_next_build(clean_guard):
    rt = FakeRealtimeSession()
    guard.watch_session(rt)

    PLUGIN_LOGGER.error(STALE_API_ERROR)

    # The next _connect_config() reads None, so the rebuild omits the stale handle entirely.
    assert rt._session_resumption_handle is None
    clean_guard.warning.assert_called_once()
    assert "SESSION_RESUME_DROPPED" in clean_guard.warning.call_args.args[0]
    assert "reason=stale_handle" in clean_guard.warning.call_args.args[0]
    assert guard.dropped_total() == 1
    # The handle value itself is never logged.
    assert "stale-handle" not in " ".join(str(a) for a in clean_guard.warning.call_args.args)


def test_the_exception_on_the_record_counts_too(clean_guard):
    """The plugin's retry WARNING carries the error only as exc_info, not in the message."""
    rt = FakeRealtimeSession()
    guard.watch_session(rt)
    try:
        raise RuntimeError("received 1008 (policy violation) Requested entity was not found")
    except RuntimeError:
        PLUGIN_LOGGER.warning("Gemini Realtime API connection failed, retrying in 2s", exc_info=True)

    assert rt._session_resumption_handle is None


def test_a_non_1008_error_keeps_the_stored_handle(clean_guard):
    rt = FakeRealtimeSession()
    guard.watch_session(rt)

    PLUGIN_LOGGER.error("Gemini Realtime API error: 503 Service Unavailable")
    PLUGIN_LOGGER.warning("Gemini Realtime API connection failed, retrying in 2s")

    assert rt._session_resumption_handle == "stale-handle"
    clean_guard.warning.assert_not_called()
    assert guard.dropped_total() == 0


def test_a_session_without_a_handle_is_not_counted(clean_guard):
    rt = FakeRealtimeSession(handle=None)
    guard.watch_session(rt)

    assert guard.drop_stale_handles() == 0
    clean_guard.warning.assert_not_called()


def test_guard_realtime_model_watches_every_session_it_builds(clean_guard):
    rt = FakeRealtimeSession()
    model = SimpleNamespace(session=lambda *args, **kwargs: rt)

    assert guard.guard_realtime_model(model) is model
    assert model.session("agent_session") is rt  # the wrapper hands back the plugin's session

    PLUGIN_LOGGER.error(STALE_API_ERROR)
    assert rt._session_resumption_handle is None


def test_guard_realtime_model_is_idempotent(clean_guard):
    rt = FakeRealtimeSession()
    calls = []

    def session():
        calls.append(1)
        return rt

    model = SimpleNamespace(session=session)
    guard.guard_realtime_model(model)
    first_wrapper = model.session
    guard.guard_realtime_model(model)

    assert model.session is first_wrapper  # not wrapped twice
    assert model.session() is rt and calls == [1]


def test_the_filter_never_suppresses_or_breaks_a_record(clean_guard):
    """The hook is observe-only: every record passes, even when drop raises internally."""
    flt = guard._StaleHandleFilter()
    record = logging.LogRecord("livekit.plugins.google", logging.ERROR, __file__, 1,
                               STALE_API_ERROR, None, None)
    assert flt.filter(record) is True

    broken = MagicMock()
    broken.getMessage.side_effect = RuntimeError("boom")
    assert flt.filter(broken) is True


def test_agent_runner_arms_the_guard_on_its_realtime_model():
    """The entrypoint wires the guard right where the RealtimeModel is built (source-level check,
    because importing src.core.agent_runner pulls the whole LiveKit CLI stack into a unit test)."""
    from pathlib import Path

    runner_py = Path(__file__).resolve().parents[2] / "src" / "core" / "agent_runner.py"
    source = runner_py.read_text(encoding="utf-8")
    build = source.index("google.realtime.RealtimeModel(")
    arm = source.index("gemini_resume_guard.guard_realtime_model(llm_model)")
    assert build < arm < build + 1200  # armed immediately after the model is built


def test_the_pinned_plugin_internals_still_exist():
    """The guard leans on two livekit-plugins-google 1.4.6 internals (plan.md section 12): the
    _session_resumption_handle attribute and failures logged on the plugin's own logger. An
    upgrade that drops either must fail HERE, not silently kill the guard."""
    import importlib
    import inspect

    mod = importlib.import_module("livekit.plugins.google.realtime.realtime_api")
    assert "_session_resumption_handle" in inspect.getsource(mod)
    log = getattr(mod, "logger", None)
    assert log is not None and log.name.startswith("livekit.plugins.google")
    # C-51: 'stop' reads the session's open generation (BrainAssistant._cut_server_reply), and the reason it has to:
    # interrupt() sends the server nothing while activity detection is automatic
    source = inspect.getsource(mod.RealtimeSession)
    assert "self._current_generation" in source and "._done" in source
    assert "if not self._manual_activity_detection:" in inspect.getsource(mod.RealtimeSession.start_user_activity)


# ---------------------------------------------------------------- C-49: restart_fresh


class RestartableSession(FakeRealtimeSession):
    def __init__(self, fail: bool = False) -> None:
        super().__init__("live-handle")
        self.restarts, self.fail = 0, fail

    def _mark_restart_needed(self) -> None:
        if self.fail:
            raise RuntimeError("closing")
        self.restarts += 1


def test_restart_fresh_drops_the_handle_and_reconnects(monkeypatch):
    monkeypatch.setattr(guard, "logger", MagicMock())
    rt = RestartableSession()
    assert guard.restart_fresh(rt, "instructions") is True
    assert rt._session_resumption_handle is None and rt.restarts == 1
    guard.logger.info.assert_called_once_with("SESSION_RESTART_FRESH reason=%s", "instructions")


def test_restart_fresh_without_a_restartable_session_changes_nothing(monkeypatch):
    monkeypatch.setattr(guard, "logger", MagicMock())
    plain = FakeRealtimeSession("keep")
    assert guard.restart_fresh(None, "x") is False and guard.restart_fresh(plain, "x") is False
    assert plain._session_resumption_handle == "keep"
    failing = RestartableSession(fail=True)
    assert guard.restart_fresh(failing, "x") is False
    guard.logger.warning.assert_called_once()


def test_a_fresh_reconnect_carries_the_new_instruction_and_replays_the_history():
    """restart_fresh relies on livekit-plugins-google 1.4.6: _mark_restart_needed exists, the connect config takes the
    instruction from the options and the handle from _session_resumption_handle, and a connect replays the chat
    history. An upgrade that changes any of these must fail HERE."""
    import inspect

    from livekit.plugins.google.realtime import realtime_api

    session = realtime_api.RealtimeSession
    assert callable(getattr(session, "_mark_restart_needed", None))
    config = inspect.getsource(session._build_connect_config)
    assert "self._opts.instructions" in config and "handle=self._session_resumption_handle" in config
    main = inspect.getsource(session._main_task)
    assert "self._build_connect_config()" in main and "send_client_content(" in main and "self._chat_ctx" in main
    assert "LiveClientContent(" in inspect.getsource(session.update_instructions)  # still an appended turn
