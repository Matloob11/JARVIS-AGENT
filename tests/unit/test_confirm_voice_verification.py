"""Plan task C-75 (TD-19): spoken confirmations count only with Sir's own final voice transcript.

The broker side (note_user_transcript, enable_transcript_check, a yes newer than the request, used up once) is
tested in tests/unit/test_confirm_broker.py. This file covers the agent wiring in src/core/agent_runner.py: which
transcripts reach the broker, and that the check is switched on when a job's session runs (fail closed).
"""
from __future__ import annotations

import asyncio
import time
import types
from typing import Any

import pytest

from services.ai_core import jarvis_confirm
from src.core import agent_runner as ar


class _Broker:
    def __init__(self) -> None:
        self.noted: list[str] = []
        self.enabled = 0

    def note_user_transcript(self, text: str, ts: float | None = None) -> None:
        self.noted.append(text)

    def enable_transcript_check(self) -> None:
        self.enabled += 1


@pytest.fixture(name="broker")
def _fake_broker(monkeypatch) -> _Broker:
    broker = _Broker()
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", broker)
    return broker


def _event(text: Any, final: bool = True) -> Any:
    return types.SimpleNamespace(transcript=text, is_final=final)


def test_only_final_non_empty_unmuted_transcripts_reach_the_broker(broker):
    speaking = types.SimpleNamespace(muted=False)
    ar._note_final_transcript(_event("haan bhej do"), speaking)
    ar._note_final_transcript(_event("haan", final=False), speaking)  # a partial result never counts
    ar._note_final_transcript(_event("   "), speaking)
    ar._note_final_transcript(_event(None), speaking)
    ar._note_final_transcript(_event("haan"), types.SimpleNamespace(muted=True))  # heard while muted
    assert broker.noted == ["haan bhej do"]


def test_a_broken_broker_never_breaks_the_transcript_handler(monkeypatch):
    class Boom:
        def note_user_transcript(self, *_a, **_k):
            raise RuntimeError("down")

        def enable_transcript_check(self):
            raise RuntimeError("down")

    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", Boom())
    ar._note_final_transcript(_event("haan"), types.SimpleNamespace(muted=False))
    ar._enable_voice_confirm_check()


async def test_the_real_broker_refuses_a_model_only_yes_once_the_job_switched_the_check_on():
    """End to end on a fresh broker: the job switches the check on, so a confirm without Sir's speech is refused,
    an older yes does not count, and a newer final 'haan' approves exactly once."""
    broker = jarvis_confirm.ConfirmationBroker()
    action = jarvis_confirm.PendingAction(
        id="c75", tool="automate_whatsapp", summary="WhatsApp bhejna", risk="medium", voice_allowed=True, args_preview="",
        expires_at=time.time() + 60, run=None)
    broker._check_transcripts = False  # pylint: disable=protected-access  # a job not started yet
    broker.note_user_transcript("haan", ts=time.time() - 30)  # said long before the request
    broker._check_transcripts = False  # pylint: disable=protected-access
    broker.enable_transcript_check()  # what _enable_voice_confirm_check does at session start
    assert broker._user_affirmed(action) is False  # pylint: disable=protected-access
    await asyncio.sleep(0.01)
    broker.note_user_transcript("ji haan bhej do")
    assert broker._user_affirmed(action) is True  # pylint: disable=protected-access
    assert broker._user_affirmed(action) is False  # pylint: disable=protected-access  # the yes is used up
