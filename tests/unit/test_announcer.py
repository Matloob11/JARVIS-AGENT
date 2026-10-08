"""services/utils/jarvis_announcer.py: the AnnouncerAPI (contract K-18, plan task C-55)."""
from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

from services.ai_core import announcer as reexport
from services.utils import jarvis_announcer as ja
from services.utils import jarvis_bridge


class _Session:
    def __init__(self, state: str = "listening") -> None:
        self.agent_state = state
        self.replies: list[str] = []

    def generate_reply(self, *, instructions: str) -> None:
        self.replies.append(instructions)


@pytest.fixture(name="posted")
def _posted(monkeypatch) -> list[dict[str, Any]]:
    posted: list[dict[str, Any]] = []

    async def notify_transcription(role, text, msg_id=None, is_final=True, source="voice", **_k):
        posted.append({"role": role, "text": text, "id": msg_id, "source": source, "final": is_final})

    monkeypatch.setattr(jarvis_bridge, "notify_transcription", notify_transcription)
    return posted


@pytest.fixture(name="ann")
def _fresh(monkeypatch) -> ja.Announcer:
    monkeypatch.setattr(ja, "SPEAKING_POLL_S", 0.01)
    return ja.Announcer()


async def _drain(ann: ja.Announcer) -> None:
    for _ in range(50):
        if not ann._tasks:  # pylint: disable=protected-access
            return
        await asyncio.sleep(0.01)


async def test_announce_posts_the_ui_message_and_speaks_once(ann, posted):
    session = _Session()
    ann.attach(session)
    await ann.announce("Recycle bin khali kar di.", priority="high")
    await _drain(ann)
    assert posted[0]["role"] == "assistant" and posted[0]["source"] == "system" and posted[0]["final"] is True
    assert posted[0]["text"] == "Recycle bin khali kar di." and posted[0]["id"]
    assert session.replies == [ja.SPEAK_PREFIX + "Recycle bin khali kar di."]


async def test_speak_false_posts_to_the_ui_only(ann, posted):
    session = _Session()
    ann.attach(session)
    await ann.announce("sirf screen par", speak=False)
    await _drain(ann)
    assert len(posted) == 1 and session.replies == []


async def test_the_rate_limit_lets_one_through_per_window_but_never_holds_high(ann, posted):
    session = _Session()
    ann.attach(session)
    for text, priority in (("one", "normal"), ("two", "low"), ("three", "high")):
        await ann.announce(text, priority=priority)
        await _drain(ann)
    assert [r.removeprefix(ja.SPEAK_PREFIX) for r in session.replies] == ["one", "three"]
    assert [p["text"] for p in posted] == ["one", "two", "three"]  # every one is shown


async def test_without_a_session_the_spoken_part_waits_for_the_next_attach(ann, posted):
    await ann.announce("job khatam", priority="high")
    assert len(posted) == 1
    session = _Session()
    ann.attach(session)
    await _drain(ann)
    assert session.replies == [ja.SPEAK_PREFIX + "job khatam"]


async def test_the_queue_keeps_the_newest_twenty(ann, posted):
    for n in range(25):
        await ann.announce(f"item {n}", priority="high")
    session = _Session()
    ann.attach(session)
    await _drain(ann)
    assert len(session.replies) == ja.QUEUE_MAX
    assert session.replies[0].endswith("item 5") and session.replies[-1].endswith("item 24")


async def test_spoken_text_replaces_what_is_spoken_and_a_muted_assistant_stays_quiet(ann, posted):
    session = _Session()
    ann.attach(session)
    await ann.announce("Clipboard par error: <untrusted text>", priority="high",
                       spoken_text="Sir, hal chat mein likh diya hai.")
    await _drain(ann)
    assert session.replies == [ja.SPEAK_PREFIX + "Sir, hal chat mein likh diya hai."]
    assert "<untrusted text>" in posted[0]["text"]  # shown, never spoken as instructions
    ann.set_assistant(types.SimpleNamespace(muted=True))
    await ann.announce("muted", priority="high")
    await _drain(ann)
    assert len(session.replies) == 1


async def test_it_waits_for_the_agent_to_stop_speaking(ann, posted, monkeypatch):
    monkeypatch.setattr(ja, "WAIT_SPEAKING_S", 1.0)
    session = _Session(state="speaking")
    ann.attach(session)
    await ann.announce("baad mein", priority="high")
    await asyncio.sleep(0.05)
    assert session.replies == []  # still speaking
    session.agent_state = "listening"
    await _drain(ann)
    assert session.replies == [ja.SPEAK_PREFIX + "baad mein"]


async def test_detach_of_another_session_keeps_the_live_one_and_nothing_raises(ann, posted, monkeypatch):
    live, other = _Session(), _Session()
    ann.attach(live)
    ann.detach(other)  # a zombie job's end
    assert ann.session is live
    ann.set_session(None)
    assert ann.session is None

    async def boom(*_a, **_k):
        raise RuntimeError("bridge down")

    monkeypatch.setattr(jarvis_bridge, "notify_transcription", boom)
    await ann.announce("never raises")


def test_the_reexport_is_the_same_api():
    assert reexport.announce is ja.announce and reexport.attach is ja.attach and reexport.announcer is ja.announcer
