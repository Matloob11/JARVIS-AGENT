"""The PC worker never reports itself full to LiveKit (2026-10-03 20:14 incident).

livekit-agents marks a worker full when its load reaches load_threshold (0.7 in production) and the default
load is the CPU use, so a busy laptop never got the owner's job and JARVIS stayed out of its room. The PC
worker therefore reports a constant 0.0; the VPS (remote mode) keeps the library default.
"""
import inspect

from src.core import agent_runner
from services.utils.jarvis_config import DESKTOP_MODE_LOCAL, DESKTOP_MODE_REMOTE


def _options(monkeypatch, mode):
    monkeypatch.setattr(agent_runner, "check_runtime_profile", lambda: None)
    monkeypatch.setattr(agent_runner, "desktop_mode", lambda: mode)
    return agent_runner.build_worker_options(lambda ctx: None)


def test_pc_worker_reports_zero_load_whatever_the_cpu(monkeypatch):
    options = _options(monkeypatch, DESKTOP_MODE_LOCAL)

    assert options.load_fnc is agent_runner.pc_worker_load
    assert agent_runner.pc_worker_load() == 0.0
    # livekit-agents calls a load_fnc without parameters as load_fnc() (worker._invoke_load_fnc)
    assert not inspect.signature(agent_runner.pc_worker_load).parameters


def test_remote_worker_keeps_the_library_default_load(monkeypatch):
    options = _options(monkeypatch, DESKTOP_MODE_REMOTE)

    assert options.load_fnc is not agent_runner.pc_worker_load


def test_the_other_worker_options_are_unchanged(monkeypatch):
    monkeypatch.delenv("JARVIS_WORKER_HOST", raising=False)
    monkeypatch.delenv("JARVIS_WORKER_PORT", raising=False)
    monkeypatch.delenv("JARVIS_AGENT_NAME", raising=False)
    options = _options(monkeypatch, DESKTOP_MODE_LOCAL)

    assert (options.host, options.port, options.agent_name) == ("127.0.0.1", 8081, "")
