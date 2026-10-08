"""Plan task C-40: scripts/smoke/voice_probe.py, the headless voice/chat probe (K-57 CT-VC-PROBE).

No network: the LiveKit server API is a fake; the room side runs only live (deploy acceptance).
"""
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
import importlib.util
import json
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("c40_voice_probe", ROOT / "scripts" / "smoke" / "voice_probe.py")
voice_probe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = voice_probe  # dataclasses look their module up while the class is built
spec.loader.exec_module(voice_probe)


def _wav(path: Path, rate: int = 16000, channels: int = 1, samples: int = 1600) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x01\x00" * samples * channels)
    return path


class _FakeLiveKit:
    """lk.room with list_rooms / list_participants / delete_room; records every delete."""

    def __init__(self, rooms: list[tuple[str, int, list[str]]]) -> None:
        self.rooms = rooms  # (name, creation_time, identities)
        self.deleted: list[str] = []
        self.room = self

    async def list_rooms(self, _req: Any) -> Any:
        return SimpleNamespace(rooms=[SimpleNamespace(name=n, creation_time=t) for n, t, _ in self.rooms])

    async def list_participants(self, req: Any) -> Any:
        people = next(ids for n, _, ids in self.rooms if n == req.room)
        return SimpleNamespace(participants=[SimpleNamespace(identity=i) for i in people])

    async def delete_room(self, req: Any) -> None:
        self.deleted.append(req.room)


NOW = 1_791_460_000


def test_frames_are_10_ms_and_the_last_is_padded():
    frames = voice_probe.split_frames(b"\x01" * (320 * 2 + 10))
    assert [len(f) for f in frames] == [320, 320, 320]
    assert frames[-1] == b"\x01" * 10 + bytes(310)
    assert voice_probe.split_frames(b"") == []


def test_rms():
    assert voice_probe.frame_rms(bytes(320)) == 0.0
    assert voice_probe.frame_rms(struct.pack("<4h", 1000, -1000, 1000, -1000)) == pytest.approx(1000.0)
    assert voice_probe.frame_rms(b"") == 0.0


def test_only_a_16k_mono_16_bit_wav_is_accepted(tmp_path):
    assert len(voice_probe.load_wav(_wav(tmp_path / "ok.wav"))) == 3200
    with pytest.raises(voice_probe.ProbeError, match="44100 Hz, 2 channel"):
        voice_probe.load_wav(_wav(tmp_path / "cd.wav", rate=44100, channels=2))
    with pytest.raises(voice_probe.ProbeError, match="cannot read"):
        voice_probe.load_wav(tmp_path / "missing.wav")
    with pytest.raises(voice_probe.ProbeError, match="no audio"):
        voice_probe.load_wav(_wav(tmp_path / "empty.wav", samples=0))


@pytest.mark.parametrize("answer, needle, hit", [
    ("2 plus 2 is 4.", "4", True),
    ("Two plus two is four.", "4", True),
    ("Two plus two is FOUR", "4", True),
    ("It is fourteen.", "4", False),          # a word match is a whole word
    ("The word was Pineapple.", "pineapple", True),
    ("I do not know.", "4", False),
])
def test_contains(answer, needle, hit):
    assert voice_probe.contains_text(answer, needle) is hit


def _turn(kind="text", answer=(), audio=None, spoke=None) -> Any:
    turn = voice_probe.Turn(kind, "x.wav" if kind == "wav" else "hi")
    turn.answer = list(answer)
    turn.first_audio_at = audio
    turn.spoke = bool(answer) if spoke is None else spoke
    return turn


def test_the_verdict():
    assert voice_probe.verdict([_turn(answer=["4"])], "reply", "4") == (True, "ok")
    assert voice_probe.verdict([_turn()], "reply", None) == (False, "turn 1: no answer within the timeout")
    assert voice_probe.verdict([_turn("wav", ["hi"])], "reply", None)[1] == \
        "turn 1: an answer in text but no agent audio"
    assert voice_probe.verdict([_turn("wav", ["hi"], audio=1.0)], "reply", None) == (True, "ok")
    assert voice_probe.verdict([_turn(answer=["5"])], "reply", "4")[1] == "the last answer does not contain '4'"
    assert voice_probe.verdict([_turn("wav")], "silence", None) == (True, "ok")
    assert voice_probe.verdict([_turn("wav", spoke=True)], "silence", None)[0] is False
    assert voice_probe.verdict([_turn(answer=["a"]), _turn(answer=["b 4"])], "reply", "4") == (True, "ok")


def test_turn_latencies_run_from_the_end_of_the_turn():
    turn = voice_probe.Turn("wav", "C:/x/tts_jarvis_time.wav", started=10.0, ended=13.0)
    turn.first_audio_at, turn.first_text_at, turn.done_at = 14.25, 14.5, 18.0
    row = turn.report()
    assert row["input"] == "tts_jarvis_time.wav"
    assert (row["first_audio_latency_ms"], row["first_text_latency_ms"], row["duration_ms"]) == (1250, 1500, 8000)


def test_turns_keep_their_order_and_are_capped():
    args = voice_probe.parse_args(["--wav", "a.wav", "--text", "hello", "--wav", "b.wav"])
    assert args.turns == [("wav", "a.wav"), ("text", "hello"), ("wav", "b.wav")]
    assert (args.expect, args.timeout, args.agent_wait) == ("reply", 30.0, 20.0)
    with pytest.raises(SystemExit):
        voice_probe.parse_args(["--text", "x"] * (voice_probe.MAX_TURNS + 1))
    with pytest.raises(SystemExit):
        voice_probe.parse_args([])
    with pytest.raises(SystemExit):
        voice_probe.parse_args(["--text", "x", "--expect", "silence", "--expect-contains", "4"])


def test_the_api_url():
    assert voice_probe.api_url("wss://example.test/lk") == "https://example.test/lk"
    assert voice_probe.api_url("ws://127.0.0.1:7880") == "http://127.0.0.1:7880"
    assert voice_probe.api_url("https://x") == "https://x"


def test_only_probe_rooms_can_be_deleted():
    lk = _FakeLiveKit([])
    for name in ("jarvis-room", "jarvis-canary-1", "jarvis-probe-1x", "jarvis-probe-", "xjarvis-probe-1"):
        with pytest.raises(ValueError):
            asyncio.run(voice_probe.delete_probe_room(lk, name))
    asyncio.run(voice_probe.delete_probe_room(lk, "jarvis-probe-17"))
    assert lk.deleted == ["jarvis-probe-17"]


@pytest.mark.parametrize("rooms, busy, deleted", [
    ([("jarvis-room", NOW - 9999, ["owner-ab12cd34", "agent-AJ_1"])], None, []),          # the user's room: ignored
    ([("jarvis-canary-5", NOW - 9999, ["agent-AJ_2"])], "busy: canary room jarvis-canary-5 is live", []),
    ([("jarvis-probe-5", NOW - 9999, ["probe-5", "agent-AJ_3"])], "busy: probe room jarvis-probe-5 is running", []),
    ([("jarvis-probe-5", NOW - 10, [])], "busy: probe room jarvis-probe-5 is running", []),  # just created
    ([("jarvis-probe-5", NOW - 600, ["agent-AJ_4"])], None, ["jarvis-probe-5"]),            # left by a crashed run
    ([("jarvis-probe-7", 0, [])], None, ["jarvis-probe-7"]),                                 # age from the name
])
def test_one_probe_or_canary_at_a_time(rooms, busy, deleted):
    lk = _FakeLiveKit(rooms)
    assert asyncio.run(voice_probe.check_busy(lk, "jarvis-probe-999", now=NOW)) == (busy, deleted)
    assert lk.deleted == deleted


def test_disabled_skips_before_any_network(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_PROBE_DISABLED", "1")

    def _no_network(*_a: Any, **_k: Any) -> None:
        raise AssertionError("no LiveKit call may happen")

    monkeypatch.setattr(voice_probe.api, "LiveKitAPI", _no_network)
    out = tmp_path / "probe.json"
    assert voice_probe.main(["--text", "hi", "--json-out", str(out)]) == voice_probe.EXIT_PASS
    report = json.loads(out.read_text(encoding="utf-8"))
    assert (report["result"], report["reason"], report["room"]) == ("skip", "JARVIS_PROBE_DISABLED=1", None)


def test_a_bad_wav_fails_before_any_room_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_PROBE_DISABLED", raising=False)
    monkeypatch.setattr(voice_probe.api, "LiveKitAPI", lambda *_a, **_k: pytest.fail("no room for a bad WAV"))
    bad = _wav(tmp_path / "cd.wav", rate=44100)
    assert voice_probe.main(["--wav", str(bad)]) == voice_probe.EXIT_FAIL


def test_missing_keys_fail_without_printing_any_value(monkeypatch, capsys):
    monkeypatch.delenv("JARVIS_PROBE_DISABLED", raising=False)
    monkeypatch.setattr(voice_probe, "_load_env", lambda: None)
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "key-value-not-printed")
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    report = asyncio.run(voice_probe.run(voice_probe.parse_args(["--text", "hi"])))
    assert (report["result"], report["reason"]) == ("fail", "missing env: LIVEKIT_API_SECRET")
    assert "key-value-not-printed" not in json.dumps(report) + capsys.readouterr().out
