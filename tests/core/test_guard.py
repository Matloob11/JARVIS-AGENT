"""services/ai_core/jarvis_guard.py: the K-45 policy in the LLM tool wrapper (plan task C-29).

Before this guard a K-45 HIGH tool whose decorator set no risk counted as 'low' and ran with no confirmation on the
PC brain (shutdown, run_cmd_command, automate_whatsapp, ...). Tools registered here use unique 'c29_' names and are
removed after every test, so the shared plugin_manager stays as other tests expect it.
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any

import pytest
from livekit.agents import llm
from livekit.agents.llm import utils as llm_utils

from services.ai_core import jarvis_guard
from services.ai_core import jarvis_plugin_manager as pm_module
from services.ai_core.jarvis_plugin_manager import jarvis_tool, plugin_manager
from src.core import confirm_policy


@pytest.fixture(autouse=True)
def _clean_env_and_registry(monkeypatch):
    monkeypatch.delenv(jarvis_guard.EXTRA_ENV, raising=False)
    monkeypatch.delenv(jarvis_guard.SKIP_ENV, raising=False)
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        before = list(plugin_manager.tools)
    yield
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        plugin_manager.tools[:] = before


# ---------------------------------------------------------------- the policy


def test_k45_high_tools_are_raised_to_high_and_nothing_is_lowered():
    assert jarvis_guard.high_tools() == confirm_policy.K45_HIGH
    for tool in ("shutdown_system", "run_cmd_command", "automate_whatsapp", "start_file_access_server",
                 "empty_recycle_bin", "lookup_sim_data"):
        assert jarvis_guard.effective_risk(tool, "low") == "high"
        assert jarvis_guard.effective_risk(tool, "medium") == "high"
        assert jarvis_guard.effective_risk(tool, "high") == "high"
    assert jarvis_guard.effective_risk("get_weather", "low") == "low"
    assert jarvis_guard.effective_risk("get_weather", "high") == "high"  # never lowered
    assert jarvis_guard.effective_risk("type_text_tool", "low") == "low"  # MEDIUM tools are not asked (K-45)


def test_extra_adds_and_skip_removes_except_the_three_that_always_ask(monkeypatch, caplog):
    monkeypatch.setenv(jarvis_guard.EXTRA_ENV, "c29_custom , get_weather")
    monkeypatch.setenv(jarvis_guard.SKIP_ENV, "lookup_sim_data,shutdown_system,run_cmd_command")
    with caplog.at_level(logging.WARNING, logger="JARVIS-GUARD"):
        tools = jarvis_guard.high_tools()
    assert {"c29_custom", "get_weather"} <= tools
    assert "lookup_sim_data" not in tools
    assert {"shutdown_system", "restart_system", "run_cmd_command"} <= tools
    assert jarvis_guard.effective_risk("shutdown_system", "low") == "high"
    assert jarvis_guard.effective_risk("lookup_sim_data", "low") == "low"


def test_medium_tools_are_audited_as_medium_without_being_asked():
    assert jarvis_guard.audit_risk("type_text_tool", "low") == "medium"
    assert jarvis_guard.audit_risk("create_folder", "low") == "medium"
    assert jarvis_guard.audit_risk("type_text_tool", "high") == "high"
    assert jarvis_guard.audit_risk("get_weather", "low") == "low"


# ---------------------------------------------------------------- in the real wrapper


async def _call_like_livekit(name: str, arguments: dict[str, Any]) -> Any:
    [wrapper] = [t for t in plugin_manager.tools if t.__name__ == name]
    tool = llm.function_tool(wrapper)
    args, kwargs = llm_utils.prepare_function_arguments(fnc=tool, json_arguments=json.dumps(arguments))
    return await tool(*args, **kwargs)


def _fake_broker(monkeypatch) -> dict[str, Any]:
    broker = importlib.import_module("services.ai_core.jarvis_confirm").confirmation_broker
    asked: dict[str, Any] = {}

    async def fake_create_pending(tool: str, summary: str, risk: str, **kwargs: Any) -> dict[str, Any]:
        asked.update(tool=tool, risk=risk, **kwargs)
        return {"status": "needs_confirmation", "tool": tool, "confirm_id": "cid-c29",
                "message": "Sir, dashboard par Allow karein."}

    monkeypatch.setattr(broker, "create_pending", fake_create_pending)
    monkeypatch.setattr(pm_module, "_emit_tool_sync", lambda payload, after=None: None)
    return asked


async def test_a_bare_decorated_high_tool_asks_instead_of_running(monkeypatch):
    monkeypatch.setenv(jarvis_guard.EXTRA_ENV, "c29_bare_high")  # stands in for an undecorated K-45 HIGH tool
    asked = _fake_broker(monkeypatch)
    ran: list[str] = []

    @jarvis_tool
    async def c29_bare_high(target: str) -> dict:
        """A tool whose decorator sets no risk."""
        ran.append(target)
        return {"status": "success"}

    result = await _call_like_livekit("c29_bare_high", {"target": "x"})
    assert result["status"] == "needs_confirmation"
    assert asked["risk"] == "high" and asked["tool"] == "c29_bare_high"
    assert ran == []  # its body never ran without Sir's Allow


async def test_a_medium_listed_tool_runs_and_is_audited_as_medium(monkeypatch):
    monkeypatch.setattr(confirm_policy, "K45_MEDIUM", confirm_policy.K45_MEDIUM | {"c29_medium_tool"})
    monkeypatch.setattr(jarvis_guard, "K45_MEDIUM", confirm_policy.K45_MEDIUM)
    asked = _fake_broker(monkeypatch)
    audits: list[tuple[str, str]] = []
    monkeypatch.setattr(pm_module, "_audit_tool_call",
                        lambda tool, risk, status, arguments, **_k: audits.append((tool, risk)))

    @jarvis_tool
    async def c29_medium_tool(text: str) -> dict:
        """A MEDIUM-listed tool."""
        return {"status": "success", "echo": text}

    result = await _call_like_livekit("c29_medium_tool", {"text": "salam"})
    assert result["status"] == "success" and not asked  # never asked (K-45: toast + audit row)
    assert audits == [("c29_medium_tool", "medium")]


async def test_an_unlisted_low_tool_is_unchanged(monkeypatch):
    asked = _fake_broker(monkeypatch)
    audits: list[Any] = []
    monkeypatch.setattr(pm_module, "_audit_tool_call", lambda *a, **_k: audits.append(a))

    @jarvis_tool
    async def c29_plain(value: str) -> dict:
        """An ordinary low tool."""
        return {"status": "success", "value": value}

    result = await _call_like_livekit("c29_plain", {"value": "v"})
    assert result["status"] == "success" and not asked and audits == []
