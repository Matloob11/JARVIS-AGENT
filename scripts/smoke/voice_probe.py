"""
scripts/smoke/voice_probe.py: a headless voice and chat probe of the JARVIS agent (plan task C-40, K-57 CT-VC-PROBE).

    $PY scripts/smoke/voice_probe.py --text "Jarvis, what is 2 plus 2?" --expect-contains 4 [--json-out logs/probe_text.json]
    $PY scripts/smoke/voice_probe.py --wav tests/integration/fixtures/audio/tts_jarvis_time.wav --expect reply
    $PY scripts/smoke/voice_probe.py --wav tts_jarvis_secret.wav --wav tts_recall.wav --expect-contains pineapple

The probe creates its own room 'jarvis-probe-<epoch>' (empty_timeout 120 s; dispatched to --agent-name or
JARVIS_AGENT_NAME when one is set, else by LiveKit's automatic dispatch), joins it as 'probe-<epoch>' with
auto_subscribe (RoomIO plays audio only to a subscriber) and waits up to --agent-wait (20 s) for an agent-* participant
and as long again for its session ('lk.agent.state' listening). Each turn, in the order given (at most 20), is typed
text on 'lk.chat' (attribute msg_id) or a 16 kHz mono 16-bit WAV played as a microphone in real time (10 ms frames),
after which the microphone stays open and silent. The answer is read from 'lk.transcription' (the agent's final
segments; what the agent heard is reported too) and, for a WAV turn, from the agent's audio: first_audio_latency_ms
runs from the end of the WAV to the first agent frame with RMS > 500. A run with a WAV turn publishes its microphone
from the start (voice on); a text-only run publishes none, so the agent answers in text (C-41).

--expect reply (default): every turn needs an answer within --timeout (30 s) of its end, a WAV turn also agent audio;
--expect silence: no turn may get one; --expect-contains: the last turn's answer holds that text (any case; a number
also matches its English word). Exit 0 PASS, 2 FAIL; JARVIS_PROBE_DISABLED=1 exits 0 SKIP. One probe or canary room
at a time (the Gemini quota): a live canary room or a running probe fails the run as busy, and a probe room that a
crashed run left behind (no probe participant, older than 60 s) is deleted. The run always deletes its own room, and no
room whose name does not match ^jarvis-probe-\\d+$ is ever deleted.

In the agent the room is a probe job (K-57): nothing reaches the bridge, the UI log, memory or identity, and the probe
identity is the person that job serves (K-56 v29). Keys come from the root .env and are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from livekit import api, rtc  # noqa: E402  pylint: disable=wrong-import-position

from services.utils.probe_context import is_probe_room  # noqa: E402  pylint: disable=wrong-import-position

PROBE_ROOM_RE = re.compile(r"^jarvis-probe-\d+$")  # the only rooms this script ever deletes
EXIT_PASS, EXIT_FAIL = 0, 2
DISABLED_ENV = "JARVIS_PROBE_DISABLED"
ROOM_EMPTY_TIMEOUT_S = 120
ROOM_MAX_PARTICIPANTS = 4
TOKEN_TTL = timedelta(minutes=30)
MAX_TURNS = 20
SAMPLE_RATE, CHANNELS = 16000, 1
FRAME_SAMPLES = SAMPLE_RATE // 100  # 10 ms
FRAME_BYTES = FRAME_SAMPLES * 2 * CHANNELS
SILENCE = bytes(FRAME_BYTES)
RMS_LOUD = 500.0
LISTEN_SETTLE_S = 0.5  # the answer is complete once the agent listens again this long after its last word...
QUIET_SETTLE_S = 5.0  # ...or after this long without a word in any state (a slow tool keeps it 'thinking')
OPENING_QUIET_S = 1.5  # before the first turn the agent is quiet this long (an opening line is no answer)
OPENING_WAIT_S = 15.0
STALE_PROBE_S = 60
POLL_S = 0.05
READY_STATES = ("listening", "thinking", "speaking")
ATTR_STATE = "lk.agent.state"
ATTR_FINAL = "lk.transcription_final"
ATTR_TRACK = "lk.transcribed_track_id"
ATTR_LINKED = "jarvis.linked_identity"
TOPIC_CHAT, TOPIC_TRANSCRIPTION = "lk.chat", "lk.transcription"
NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                "sixteen seventeen eighteen nineteen twenty").split()


class ProbeError(Exception):
    """A failed step: the run ends as FAIL with this message."""


# ------------------------------------------------------------------ pure helpers (unit-tested)

def api_url(ws_url: str) -> str:
    """The HTTP form of LIVEKIT_URL for the server API (wss -> https, ws -> http)."""
    url = (ws_url or "").strip()
    for ws_scheme, http_scheme in (("ws://", "http://"), ("wss://", "https://")):
        if url.startswith(ws_scheme):
            return http_scheme + url[len(ws_scheme):]
    return url


def load_wav(path: Path) -> bytes:
    """The PCM of a 16 kHz mono 16-bit WAV; ProbeError for anything else."""
    try:
        with wave.open(str(path), "rb") as wav:
            shape = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype())
            if shape != (SAMPLE_RATE, CHANNELS, 2, "NONE"):
                raise ProbeError(f"{path.name}: need a 16 kHz mono 16-bit PCM WAV, got {shape[0]} Hz, "
                                 f"{shape[1]} channel(s), {8 * shape[2]}-bit")
            pcm = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise ProbeError(f"{path.name}: cannot read the WAV ({type(exc).__name__})") from exc
    if not pcm:
        raise ProbeError(f"{path.name}: the WAV holds no audio")
    return pcm


def split_frames(pcm: bytes) -> list[bytes]:
    """10 ms frames; the last one is padded with silence."""
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    if frames and len(frames[-1]) < FRAME_BYTES:
        frames[-1] = frames[-1] + bytes(FRAME_BYTES - len(frames[-1]))
    return frames


def frame_rms(data: Any) -> float:
    """RMS of int16 samples."""
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


def contains_text(answer: str, needle: str) -> bool:
    """Case-insensitive substring; a number 0..20 also matches its English word ('4' in 'two plus two is four')."""
    answer_l, needle_l = answer.lower(), needle.strip().lower()
    if needle_l in answer_l:
        return True
    if needle_l.isdigit() and int(needle_l) < len(NUMBER_WORDS):
        return re.search(rf"\b{NUMBER_WORDS[int(needle_l)]}\b", answer_l) is not None
    return False


def ms(start: float | None, end: float | None) -> int | None:
    return None if start is None or end is None else max(0, round((end - start) * 1000))


@dataclass
class Turn:  # pylint: disable=too-many-instance-attributes
    """One probe turn and what the agent did after it."""

    kind: str  # 'text' | 'wav'
    value: str  # the text, or the WAV path
    started: float = 0.0  # monotonic
    ended: float | None = None  # text sent / last WAV frame played
    answer: list[str] = field(default_factory=list)  # the agent's final segments
    heard: list[str] = field(default_factory=list)  # the agent's transcript of the probe's own speech
    first_text_at: float | None = None
    first_audio_at: float | None = None
    last_word_at: float | None = None  # the agent's latest text chunk or loud frame
    spoke: bool = False  # any agent text chunk or loud frame after the turn started
    done_at: float | None = None

    def report(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "input": Path(self.value).name if self.kind == "wav" else self.value,
            "heard": " ".join(self.heard),
            "answer": " ".join(self.answer),
            "first_text_latency_ms": ms(self.ended, self.first_text_at),
            "first_audio_latency_ms": ms(self.ended, self.first_audio_at),
            "duration_ms": ms(self.started, self.done_at),
        }


def verdict(turns: list[Turn], expect: str, contains: str | None) -> tuple[bool, str]:
    """(passed, reason) for the finished turns."""
    for number, turn in enumerate(turns, 1):
        if expect == "silence":
            if turn.spoke or turn.answer:
                return False, f"turn {number}: the agent answered, silence was expected"
            continue
        if not turn.answer:
            return False, f"turn {number}: no answer within the timeout"
        if turn.kind == "wav" and turn.first_audio_at is None:
            return False, f"turn {number}: an answer in text but no agent audio"
    if contains and not contains_text(" ".join(turns[-1].answer), contains):
        return False, f"the last answer does not contain {contains!r}"
    return True, "ok"


def delete_allowed(name: str) -> bool:
    return PROBE_ROOM_RE.fullmatch(name or "") is not None


def room_age_s(room: Any, now: float) -> float:
    """Seconds since the room was created (its creation_time, else the epoch in its name)."""
    created = float(getattr(room, "creation_time", 0) or 0)
    if created <= 0:
        match = re.search(r"-(\d+)$", getattr(room, "name", "") or "")
        created = float(match.group(1)) if match else now
    return now - created


# ------------------------------------------------------------------ LiveKit server API

async def delete_probe_room(lk: Any, name: str) -> None:
    """Delete a probe room; any other name is refused before the API is called."""
    if not delete_allowed(name):
        raise ValueError(f"refusing to delete {name!r}: not a probe room")
    await lk.room.delete_room(api.DeleteRoomRequest(room=name))


async def check_busy(lk: Any, own: str, now: float | None = None) -> tuple[str | None, list[str]]:
    """(why the run must not start, or None; the stale probe rooms deleted). One probe or canary session at a time."""
    now = time.time() if now is None else now
    deleted: list[str] = []
    rooms = (await lk.room.list_rooms(api.ListRoomsRequest())).rooms
    for room in rooms:
        name = room.name
        if name == own or not is_probe_room(name):
            continue
        if not delete_allowed(name):
            return f"busy: canary room {name} is live", deleted
        people = (await lk.room.list_participants(api.ListParticipantsRequest(room=name))).participants
        if any(str(p.identity).startswith("probe-") for p in people) or room_age_s(room, now) < STALE_PROBE_S:
            return f"busy: probe room {name} is running", deleted
        await delete_probe_room(lk, name)  # left by a crashed run: nobody probes there any more
        deleted.append(name)
    return None, deleted


def mint_token(key: str, secret: str, room: str, identity: str) -> str:
    return (api.AccessToken(key, secret)
            .with_identity(identity)
            .with_name("JARVIS probe")
            .with_ttl(TOKEN_TTL)
            .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True,
                                         can_publish_data=True))
            .to_jwt())


# ------------------------------------------------------------------ the room side

class Mic:
    """The probe's microphone: 10 ms frames in real time; silence whenever no WAV plays (an open mic, quiet room)."""

    def __init__(self, source: Any) -> None:
        self.source = source
        self._pending: deque[Any] = deque()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    async def play(self, pcm: bytes) -> float:
        """Queue a WAV; returns the monotonic time its last frame went out."""
        done: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        self._pending.extend(split_frames(pcm))
        self._pending.append(done)
        return await done

    async def _run(self) -> None:
        start, sent = time.monotonic(), 0
        while True:
            item = self._pending.popleft() if self._pending else SILENCE
            if isinstance(item, asyncio.Future):
                if not item.done():
                    item.set_result(time.monotonic())
                continue
            await self.source.capture_frame(rtc.AudioFrame(item, SAMPLE_RATE, CHANNELS, FRAME_SAMPLES))
            sent += 1
            delay = start + sent * 0.01 - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -0.5:  # a stall: restart the clock instead of bursting to catch up
                start, sent = time.monotonic(), 0

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def is_agent(participant: Any) -> bool:
    identity = str(getattr(participant, "identity", "") or "")
    return getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT or \
        identity.startswith("agent-")


class Probe:  # pylint: disable=too-many-instance-attributes
    """The probe participant: who the agent is, what it says and when."""

    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.room = rtc.Room()
        self.mic: Mic | None = None
        self.mic_sid: str | None = None
        self.turn: Turn | None = None
        self.opening: list[str] = []  # agent segments before the first turn
        self.last_agent_word = 0.0
        self._tasks: set[asyncio.Task[Any]] = set()
        self.room.on("track_subscribed", self._on_track_subscribed)
        self.room.register_text_stream_handler(TOPIC_TRANSCRIPTION, self._on_transcription)

    # -- who is there
    def agent(self) -> Any:
        return next((p for p in self.room.remote_participants.values() if is_agent(p)), None)

    def agent_attr(self, name: str) -> str | None:
        agent = self.agent()
        return None if agent is None else (agent.attributes or {}).get(name)

    # -- events
    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _agent_word(self, turn: Turn | None, audio: bool) -> None:
        now = time.monotonic()
        self.last_agent_word = now
        if turn is None:
            return
        turn.spoke, turn.last_word_at = True, now
        if turn.ended is None or now < turn.ended:
            return
        if audio and turn.first_audio_at is None:
            turn.first_audio_at = now
        if not audio and turn.first_text_at is None:
            turn.first_text_at = now

    def _on_transcription(self, reader: Any, sender: str) -> None:
        self._spawn(self._read_segment(reader, sender))

    async def _read_segment(self, reader: Any, sender: str) -> None:
        turn = self.turn  # a segment belongs to the turn it started in
        start_attrs = dict(reader.info.attributes or {})
        mine = sender == self.identity or (self.mic_sid is not None and start_attrs.get(ATTR_TRACK) == self.mic_sid)
        parts: list[str] = []
        async for chunk in reader:
            parts.append(chunk)
            if not mine and chunk.strip():
                self._agent_word(turn, audio=False)
        text = "".join(parts).strip()
        if not text or (reader.info.attributes or {}).get(ATTR_FINAL) != "true":
            return
        if mine:
            if turn is not None:
                turn.heard.append(text)
        elif turn is None:
            self.opening.append(text)
        else:
            turn.answer.append(text)
            self._agent_word(turn, audio=False)

    def _on_track_subscribed(self, track: Any, _publication: Any, participant: Any) -> None:
        if is_agent(participant) and track.kind == rtc.TrackKind.KIND_AUDIO:
            self._spawn(self._read_audio(track))

    async def _read_audio(self, track: Any) -> None:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
        try:
            async for event in stream:
                if frame_rms(event.frame.data) > RMS_LOUD:
                    self._agent_word(self.turn, audio=True)
        finally:
            await stream.aclose()

    # -- steps
    async def connect(self, url: str, token: str, with_mic: bool) -> None:
        await self.room.connect(url, token, options=rtc.RoomOptions(auto_subscribe=True))
        if with_mic:
            source = rtc.AudioSource(SAMPLE_RATE, CHANNELS)
            track = rtc.LocalAudioTrack.create_audio_track("probe-mic", source)
            publication = await self.room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            self.mic_sid = publication.sid
            self.mic = Mic(source)
            self.mic.start()

    async def wait_for(self, condition: Any, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(POLL_S)
        return True

    async def wait_opening_quiet(self) -> None:
        """Before the first turn: the agent listens and has been quiet a moment (bounded; a busy agent just goes on)."""
        await self.wait_for(lambda: self.agent_attr(ATTR_STATE) == "listening"
                            and time.monotonic() - self.last_agent_word >= OPENING_QUIET_S, OPENING_WAIT_S)

    async def run_turn(self, turn: Turn, pcm: bytes | None, expect: str, timeout: float) -> None:
        turn.started = time.monotonic()
        self.turn = turn
        if turn.kind == "text":
            await self.room.local_participant.send_text(turn.value, topic=TOPIC_CHAT,
                                                        attributes={"msg_id": str(uuid.uuid4())})
            turn.ended = time.monotonic()
        else:
            assert self.mic is not None and pcm is not None
            turn.ended = await self.mic.play(pcm)
        needs_audio = turn.kind == "wav"

        def finished() -> bool:
            if expect == "silence":
                return turn.spoke  # fail fast: the agent spoke
            if not turn.answer or (needs_audio and turn.first_audio_at is None) or turn.last_word_at is None:
                return False
            quiet = time.monotonic() - turn.last_word_at
            return (self.agent_attr(ATTR_STATE) == "listening" and quiet >= LISTEN_SETTLE_S) or quiet >= QUIET_SETTLE_S

        await self.wait_for(finished, timeout)
        turn.done_at = time.monotonic()

    async def aclose(self) -> None:
        if self.mic is not None:
            await self.mic.aclose()
        try:
            await self.room.disconnect()
        finally:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)


# ------------------------------------------------------------------ the run

def _load_env() -> None:
    try:
        from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel
    except ImportError:
        return
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "deploy" / "windows-local" / ".env", override=False)  # the root .env wins (K-47)


def _say(text: str) -> None:
    print(text, flush=True)


async def run(args: argparse.Namespace) -> dict[str, Any]:  # pylint: disable=too-many-locals,too-many-statements
    """One probe run; returns the report (result 'pass' | 'fail' | 'skip')."""
    started_wall, started = datetime.now(timezone.utc), time.monotonic()
    report: dict[str, Any] = {"result": "fail", "reason": "", "started_at": started_wall.isoformat(timespec="seconds"),
                              "room": None, "identity": None, "dispatch": None, "agent": None, "agent_join_ms": None,
                              "session_ready_ms": None, "linked": None, "opening": [], "turns": [],
                              "first_audio_latency_ms": None, "room_deleted": False, "stale_rooms_deleted": []}

    def finish(result: str, reason: str) -> dict[str, Any]:
        report.update(result=result, reason=reason, duration_s=round(time.monotonic() - started, 1))
        return report

    if os.getenv(DISABLED_ENV) == "1":
        return finish("skip", f"{DISABLED_ENV}=1")
    turns = [Turn(kind, value) for kind, value in args.turns]
    try:
        pcm = {turn.value: load_wav(Path(turn.value)) for turn in turns if turn.kind == "wav"}
    except ProbeError as exc:
        return finish("fail", str(exc))
    _load_env()
    url, key, secret = (os.getenv(name, "").strip() for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY",
                                                                  "LIVEKIT_API_SECRET"))
    missing = [name for name, value in (("LIVEKIT_URL", url), ("LIVEKIT_API_KEY", key),
                                        ("LIVEKIT_API_SECRET", secret)) if not value]
    if missing:
        return finish("fail", f"missing env: {', '.join(missing)}")

    epoch = int(time.time())
    name, identity = f"jarvis-probe-{epoch}", f"probe-{epoch}"
    agent_name = (args.agent_name if args.agent_name is not None else os.getenv("JARVIS_AGENT_NAME", "")).strip()
    report.update(room=name, identity=identity, dispatch=agent_name or "automatic")
    lk = api.LiveKitAPI(api_url(url), key, secret)
    probe: Probe | None = None
    created = False
    try:
        busy, stale = await check_busy(lk, name)
        report["stale_rooms_deleted"] = stale
        for room_name in stale:
            _say(f"PROBE deleted stale probe room {room_name}")
        if busy:
            return finish("fail", busy)
        await lk.room.create_room(api.CreateRoomRequest(
            name=name, empty_timeout=ROOM_EMPTY_TIMEOUT_S, max_participants=ROOM_MAX_PARTICIPANTS,
            agents=[api.RoomAgentDispatch(agent_name=agent_name)] if agent_name else []))
        created = True
        _say(f"PROBE room={name} identity={identity} dispatch={report['dispatch']}")
        probe = Probe(identity)
        joined_at = time.monotonic()
        await probe.connect(url, mint_token(key, secret, name, identity), with_mic=bool(pcm))
        if not await probe.wait_for(lambda: probe.agent() is not None, args.agent_wait):
            return finish("fail", f"no agent joined within {args.agent_wait:g} s")
        report["agent"] = probe.agent().identity
        report["agent_join_ms"] = ms(joined_at, time.monotonic())
        if not await probe.wait_for(lambda: probe.agent_attr(ATTR_STATE) in READY_STATES, args.agent_wait):
            return finish("fail", f"the agent's session did not start within {args.agent_wait:g} s")
        report["session_ready_ms"] = ms(joined_at, time.monotonic())
        await probe.wait_opening_quiet()
        report["linked"] = probe.agent_attr(ATTR_LINKED) == identity
        report["opening"] = list(probe.opening)
        _say(f"PROBE agent joined in {report['agent_join_ms']} ms, session ready in {report['session_ready_ms']} ms "
             f"(linked: {'yes' if report['linked'] else 'no'})")
        for number, turn in enumerate(turns, 1):
            await probe.run_turn(turn, pcm.get(turn.value), args.expect, args.timeout)
            row = turn.report()
            report["turns"].append(row)
            heard = f" heard {row['heard']!r}" if row["heard"] else ""
            latency = (f"first audio {row['first_audio_latency_ms']} ms" if turn.kind == "wav"
                       else f"first text {row['first_text_latency_ms']} ms")
            _say(f"TURN {number} {turn.kind} {row['input']!r}{heard} -> {row['answer'] or '(nothing)'!r} ({latency})")
        first_wav = next((row for row in report["turns"] if row["kind"] == "wav"), None)
        if first_wav is not None:
            report["first_audio_latency_ms"] = first_wav["first_audio_latency_ms"]
            _say(f"first_audio_latency_ms={first_wav['first_audio_latency_ms']}")
        passed, reason = verdict(turns, args.expect, args.expect_contains)
        return finish("pass" if passed else "fail", reason)
    except ProbeError as exc:
        return finish("fail", str(exc))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # a LiveKit or network failure is a FAIL
        return finish("fail", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if probe is not None:
            await asyncio.gather(probe.aclose(), return_exceptions=True)
        if created:
            try:
                await delete_probe_room(lk, name)
                report["room_deleted"] = True
            except Exception as exc:  # pylint: disable=broad-exception-caught  # empty_timeout still removes it
                _say(f"PROBE could not delete {name} ({type(exc).__name__}); LiveKit drops it when empty")
        await lk.aclose()


class _TurnAction(argparse.Action):
    """--text and --wav append (kind, value) to one ordered list of turns."""

    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Any,
                 option_string: str | None = None) -> None:
        turns = list(getattr(namespace, "turns", None) or [])
        turns.append((self.const, values))
        setattr(namespace, "turns", turns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Headless voice/chat probe of the JARVIS agent (C-40).")
    parser.add_argument("--text", action=_TurnAction, const="text", dest="turns", metavar="TEXT",
                        help="a typed turn on lk.chat (repeatable)")
    parser.add_argument("--wav", action=_TurnAction, const="wav", dest="turns", metavar="PATH",
                        help="a spoken turn: a 16 kHz mono 16-bit WAV (repeatable)")
    parser.add_argument("--expect", choices=("reply", "silence"), default="reply")
    parser.add_argument("--expect-contains", metavar="TEXT", help="text the last answer must contain")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds per turn for the answer (default 30)")
    parser.add_argument("--agent-wait", type=float, default=20.0,
                        help="seconds for the agent to join, and again for its session (default 20)")
    parser.add_argument("--agent-name", help="dispatch to this worker name (default: JARVIS_AGENT_NAME, else "
                                             "automatic dispatch)")
    parser.add_argument("--json-out", metavar="PATH", help="write the report as JSON")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.turns = list(args.turns or [])
    if not args.turns:
        parser.error("give at least one --text or --wav turn")
    if len(args.turns) > MAX_TURNS:
        parser.error(f"at most {MAX_TURNS} turns per run")
    if args.expect == "silence" and args.expect_contains:
        parser.error("--expect-contains needs --expect reply")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _say(f"{report['result'].upper()} {report['reason']}"
         + (f" (room deleted: {'yes' if report['room_deleted'] else 'no'})" if report["room"] else ""))
    return EXIT_FAIL if report["result"] == "fail" else EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
