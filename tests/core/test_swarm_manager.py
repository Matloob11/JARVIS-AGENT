"""C-43: '/swarm' runs end to end; its status lines and final report reach the chat (K-58 messages, source 'system').

No network: llm_text.generate_text and the bridge notify are fakes.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.ai_core import swarm_manager
from services.ai_core.llm_text import LLMResult, LLMUnavailableError

PLAN = '[{"id":"agent_1","role":"Researcher","instruction":"a"},{"id":"agent_2","role":"Writer","instruction":"b"}]'


@pytest.fixture
def sent(monkeypatch) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def fake_notify_transcription(role: str, text: str, **kwargs: Any) -> None:
        messages.append({"role": role, "text": text, **kwargs})

    monkeypatch.setenv("GOOGLE_API_KEY", "test-only-not-a-key")
    monkeypatch.setattr(swarm_manager, "notify_transcription", fake_notify_transcription)
    return messages


def _llm(monkeypatch, *, fail_merge: bool = False) -> list[str]:
    prompts: list[str] = []

    async def fake_generate_text(prompt: str, **_kwargs: Any) -> LLMResult:
        prompts.append(prompt)
        if "ultra-planner" in prompt:
            return LLMResult(PLAN, "gemini", "gemini-3.5-flash")
        if "Synthesize" in prompt:
            if fail_merge:
                raise LLMUnavailableError(["gemini: 503"])
            return LLMResult("FINAL REPORT", "gemini", "gemini-3.5-flash")
        return LLMResult("sub report", "gemini", "gemini-3.5-flash")

    monkeypatch.setattr(swarm_manager, "generate_text", fake_generate_text)
    return prompts


async def test_the_final_report_is_the_last_chat_message(monkeypatch, sent):
    prompts = _llm(monkeypatch)
    assert await swarm_manager.SwarmCoordinator().execute_swarm("latest AI news summary") == "FINAL REPORT"
    assert len(prompts) == 4  # decompose, two sub-agents, merge
    assert sent[-1] == {"role": "assistant", "text": "FINAL REPORT", "source": "system"}
    assert all(m["role"] == "assistant" and m["source"] == "system" for m in sent)
    assert [m["text"] for m in sent[:-1]][0] == "[SWARM] Task breakdown shuru kar raha hun..."
    assert sum("complete ho gaya" in m["text"] for m in sent) == 2


async def test_a_failed_merge_still_sends_the_raw_reports(monkeypatch, sent):
    _llm(monkeypatch, fail_merge=True)
    report = await swarm_manager.SwarmCoordinator().execute_swarm("x")
    assert report.startswith("Sir, final merge nahi ho saki") and "sub report" in report
    assert sent[-1]["text"] == report


def test_no_dead_agent_message_event_is_left():
    import inspect

    source = inspect.getsource(swarm_manager)
    assert "notify_ui(" not in source and '"agent_message"' not in source
