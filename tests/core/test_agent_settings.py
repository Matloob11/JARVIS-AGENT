"""C-54 (VR-18): the agent's settings service, contract K-61 (CT-VC-SETTINGS) with K-03, K-46 and K-60.

settings_update had no agent handler, nothing was saved and three areas proposed different payloads. Now
src/core/agent_settings.py validates a partial, applies it through the C-50/C-51 paths (a voice at the next safe point,
the persona through persona_manager.set_persona, the wake word through set_wake_word_mode), saves it atomically in
data/settings/agent_settings.json (here a tmp_path file) and answers settings_sync. No LiveKit, Gemini or bridge: fakes
stand in, and one test runs agent_runner.entrypoint with light stand-ins to see the saved voice reach the job's
RealtimeModel before session.start.
"""
# pylint: disable=protected-access,redefined-outer-name,import-outside-toplevel
from __future__ import annotations

import asyncio
import json
import logging
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from services.ai_core import agent_loops
from services.utils.probe_context import bridge_suppressed
from services.utils.ui_command_registry import CommandContext, dispatch, reset_dedupe
from src.core import agent_settings, voices
from src.core.agent_settings import AgentSettings, SettingsService, validate

K61_FIELDS = {"voice", "persona", "wake_word", "wake_followup_s", "voice_id", "voice_id_threshold", "voice_id_reject",
              "turn_mode", "transcript_script", "language", "speak_typed_replies", "quiet_hours", "confirm_level",
              "style", "emotional_intelligence"}
ENV_NAMES = ("JARVIS_TURN_MODE", "JARVIS_SPEAK_TYPED", "JARVIS_VOICE_ID_THRESHOLD", "JARVIS_VOICE_JARVIS",
             "JARVIS_VOICE_ANNA")


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """Every test saves into its own tmp_path file (never the project's data folder) with the K-69 names unset."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "data" / "settings" / "agent_settings.json"
    monkeypatch.setattr(agent_settings, "SETTINGS_PATH", path)
    reset_dedupe()
    return path


class FakePersonas:
    """persona_manager as BrainAssistant has it: set_persona switches and asks for the persona's voice (or the one
    given), the persona that is active already is only told again."""

    def __init__(self, owner: "FakeAssistant") -> None:
        self.owner, self.is_anna, self.calls = owner, False, []

    @property
    def active_persona_name(self) -> str:
        return "anna" if self.is_anna else "jarvis"

    async def set_persona(self, is_anna: bool, *, voice: str | None = None) -> None:
        self.calls.append((is_anna, voice))
        if self.is_anna == is_anna:
            return
        self.is_anna = is_anna
        self.owner._session_options.voice_id = voice or voices.persona_voice(self.active_persona_name)


class FakeAssistant:
    """The parts of BrainAssistant the settings service uses, recording what it was asked to do."""

    def __init__(self) -> None:
        self.persona_manager = FakePersonas(self)
        self._session_options = SimpleNamespace(voice_id="jarvis_v2")  # what BrainAssistant starts with
        self.wake_word_mode = True
        self.muted = False
        self.voice_id_engine = SimpleNamespace(is_enrolled=False)
        self._spawned_tasks: set[asyncio.Task[Any]] = set()
        self.calls: list[tuple[str, Any]] = []

    async def request_voice(self, voice: str) -> None:
        self.calls.append(("voice", voice))
        self._session_options.voice_id = voice

    def set_wake_word_mode(self, active: bool, *, refresh: bool = True) -> None:
        self.calls.append(("wake_word", active) if refresh else ("wake_word", active, "no_refresh"))
        self.wake_word_mode = active

    async def tool_set_wake_word_mode(self, active: bool) -> dict[str, Any]:
        self.calls.append(("wake_word_tool", active))
        self.wake_word_mode = active
        return {"status": "success"}

    async def refresh_instructions(self) -> None:
        self.calls.append(("refresh", self.persona_manager.active_persona_name, self.wake_word_mode))

    def set_muted(self, active: bool) -> None:
        self.calls.append(("muted", active))
        self.muted = active


class FakeLink:
    """VoiceLink's audio-out policy (C-41)."""

    def __init__(self) -> None:
        self.speak_policy, self.updates = "auto", []

    def update_audio_out(self, reason: str) -> None:
        self.updates.append(reason)


@pytest.fixture
def sent() -> list[tuple[str, Any]]:
    return []


def _service(sent: list, assistant: Any = None, *, probe: bool = False, link: Any = None,
             settings: AgentSettings | None = None) -> SettingsService:
    runtime = SimpleNamespace(assistant=assistant if assistant is not None else FakeAssistant(), voice_link=link)

    async def notify(event_type: str, payload: Any) -> None:
        sent.append((event_type, json.loads(json.dumps(payload))))  # what the bridge would get

    service = SettingsService(runtime, settings if settings is not None else agent_settings.load(), probe=probe,
                              notify=notify)
    runtime.settings = service
    return service


def _syncs(sent: list) -> list[dict[str, Any]]:
    return [payload for event, payload in sent if event == "settings_sync"]


def _file(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- defaults and validation


def test_the_defaults_are_k61s(monkeypatch):
    assert AgentSettings().values() == {
        "voice": "Charon", "persona": "jarvis", "wake_word": True, "wake_followup_s": 0, "voice_id": True,
        "voice_id_threshold": 0.45, "voice_id_reject": "silent", "turn_mode": "fast", "transcript_script": "roman",
        "language": "auto", "speak_typed_replies": "auto", "quiet_hours": "", "confirm_level": "normal", "style": "",
        "emotional_intelligence": True}
    # the K-69 names give their defaults: turn mode, the typed-reply audio policy, the voice-ID threshold, the voices
    for name, value in (("JARVIS_TURN_MODE", "Gated"), ("JARVIS_SPEAK_TYPED", "always"),
                        ("JARVIS_VOICE_ID_THRESHOLD", "0.6"), ("JARVIS_VOICE_JARVIS", "kore")):
        monkeypatch.setenv(name, value)
    settings = AgentSettings()
    assert (settings.get("turn_mode"), settings.get("speak_typed_replies"), settings.get("voice_id_threshold"),
            settings.voice) == ("gated", "always", 0.6, "Kore")
    monkeypatch.setenv("JARVIS_VOICE_ID_THRESHOLD", "1.5")  # outside 0.2..0.9: the K-61 default
    assert AgentSettings().get("voice_id_threshold") == 0.45
    assert AgentSettings(saved={"persona": "anna"}).voice == "Aoede"  # nothing saved for the voice: the persona's


def test_validate_maps_names_and_languages_and_rejects_bad_values():
    clean, errors = validate({"voice": "verse", "language": "ur-PK", "emotionalIntelligence": False,
                              "quiet_hours": "1:05 - 7:00", "voice_id_threshold": "0.5", "persona": " ANNA ",
                              "wake_word_active": False, "wake_followup_s": 12.0, "muted": True})
    assert not errors
    assert clean == {"voice": "Puck", "language": "ur", "emotional_intelligence": False, "quiet_hours": "01:05-07:00",
                     "voice_id_threshold": 0.5, "persona": "anna", "wake_word": False, "wake_followup_s": 12,
                     "muted": True}
    assert validate({"language": "en_US"})[0] == {"language": "en"}
    assert validate({"language": "hi-IN"})[0] == {"language": "hi"}
    bad = {"persona": "bob", "wake_word": "yes", "wake_followup_s": 61, "voice_id_threshold": 0.1,
           "voice_id_reject": "loud", "turn_mode": "slow", "language": "fr-FR", "speak_typed_replies": "sometimes",
           "quiet_hours": "25:00-01:00", "confirm_level": "none", "style": "x" * 41, "emotional_intelligence": 1,
           "voice_id": None}
    clean, errors = validate(bad)
    assert not clean and set(errors) == set(bad)
    assert validate({"quiet_hours": "07:00-07:00"})[1]  # start and end must differ
    # the canonical key wins over its alias; read-only and unknown keys are in neither
    assert validate({"emotionalIntelligence": False, "emotional_intelligence": True})[0] == {
        "emotional_intelligence": True}
    assert validate({"voice_id_enrolled": True, "updated_ts": 5, "saved": ["voice"],
                     "automation": {"desktop_control": True}}) == ({}, {})
    assert agent_settings.unknown_keys({"automation": {}, "voice": "x", "updated_ts": 1, "saved": []}) == [
        "automation"]
    assert validate("not an object") == ({}, {"settings": "settings must be an object"})


# ---------------------------------------------------------------- update: validate -> apply -> persist -> sync


@pytest.mark.asyncio
async def test_a_bad_voice_gives_errors_listing_the_valid_names_and_changes_nothing(sent, settings_file):
    service = _service(sent)
    payload = await service.update({"voice": "alloyx"})
    assert payload["errors"]["voice"].startswith("Voice 'alloyx' nahi mili. Valid voices: Achernar")
    assert "Puck" in payload["errors"]["voice"] and "alloy=Zephyr" in payload["errors"]["voice"]
    assert payload["applied"] == {} and payload["settings"]["voice"] == "Charon"
    assert service.assistant.calls == [] and not settings_file.exists()  # nothing applied, nothing saved
    assert _syncs(sent) == [payload]  # the UI still gets its answer
    # an invalid field never stops the valid ones of the same update
    payload = await service.update({"voice": "alloyx", "wake_word": False})
    assert set(payload["errors"]) == {"voice"} and payload["applied"] == {"wake_word": "applied"}
    assert service.assistant.calls == [("wake_word", False)] and _file(settings_file)["settings"] == {
        "wake_word": False}


@pytest.mark.asyncio
async def test_a_round_trip_through_the_file(sent, settings_file):
    service = _service(sent)
    payload = await service.update({"voice": "verse", "wake_word": False, "language": "en-US",
                                    "quiet_hours": "22:00-07:00", "automation": {"desktop_control": True}})
    assert payload["applied"] == {"voice": "applied", "wake_word": "applied", "language": "unsupported",
                                  "quiet_hours": "unsupported", "automation": "unsupported"}
    saved = _file(settings_file)
    assert saved["version"] == 1 and saved["updated_ts"] == payload["settings"]["updated_ts"] > 0
    assert saved["settings"] == {"voice": "Puck", "wake_word": False, "language": "en", "quiet_hours": "22:00-07:00"}
    assert {entry["by"] for entry in saved["updated"].values()} == {"ui"}
    assert sorted(path.name for path in settings_file.parent.iterdir()) == ["agent_settings.json"]  # no temp file left
    loaded = agent_settings.load()
    assert (loaded.voice, loaded.get("wake_word"), loaded.get("language"), loaded.get("quiet_hours")) == (
        "Puck", False, "en", "22:00-07:00")
    assert loaded.updated_ts == saved["updated_ts"] and loaded.get("persona") == "jarvis"  # the rest: defaults
    # a save merges: another job's (or an earlier update's) fields are kept
    agent_settings.save({"style": "formal"}, "test")
    assert _file(settings_file)["settings"]["voice"] == "Puck" and agent_settings.load().get("style") == "formal"
    agent_settings.save({"style": agent_settings.REMOVE}, "test")
    assert "style" not in _file(settings_file)["settings"]


def test_a_broken_file_gives_the_defaults_and_is_kept_aside_on_the_next_save(settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text("{not json", encoding="utf-8")
    assert agent_settings.load().values() == AgentSettings().values()
    agent_settings.save({"voice": "Kore"}, "ui")
    assert _file(settings_file)["settings"] == {"voice": "Kore"}
    assert (settings_file.parent / "agent_settings.json.corrupt").read_text(encoding="utf-8") == "{not json"
    # a hand-edited bad value is left out (its default applies), the good ones stay
    settings_file.write_text(json.dumps({"settings": {"voice": "alloyx", "wake_word": False, "muted": True}}),
                             encoding="utf-8")
    loaded = agent_settings.load()
    assert loaded.voice == "Charon" and loaded.get("wake_word") is False and "muted" not in loaded.saved


def test_a_file_that_is_no_utf8_text_never_makes_load_or_save_raise(settings_file):
    """C-54 review: a hand edit in UTF-16 (Windows PowerShell 5.1's '>' or Out-File), an ANSI 'é' or a damaged block
    raised UnicodeDecodeError out of load() and save(), so every job failed before its session existed and the file
    was never kept aside. Now json reads UTF-16 and a UTF-8 BOM as they are, and bytes that decode as no text count as
    broken: the defaults, then <name>.corrupt at the next save."""
    settings_file.parent.mkdir(parents=True)
    body = json.dumps({"settings": {"voice": "Kore", "wake_word": False}})
    for raw in (body.encode("utf-16"), body.encode("utf-8-sig")):  # PowerShell 5.1 / Notepad's UTF-8 with a BOM
        settings_file.write_bytes(raw)
        loaded = agent_settings.load()
        assert (loaded.voice, loaded.get("wake_word")) == ("Kore", False)
        agent_settings.save({"style": "formal"}, "ui")  # merged and written back as plain UTF-8
        assert _file(settings_file)["settings"] == {"voice": "Kore", "wake_word": False, "style": "formal"}
        assert not (settings_file.parent / "agent_settings.json.corrupt").exists()
    ansi = json.dumps({"settings": {"style": "café", "voice": "Kore"}}, ensure_ascii=False).encode("cp1252")
    for raw in (ansi, b'{"settings": {"voice": "Kore", "style": "\xff\xfe\x00"}}', b"[" * 200_000):
        settings_file.write_bytes(raw)  # ANSI text, a damaged block, nesting too deep for the parser
        assert agent_settings.load().values() == AgentSettings().values()
        agent_settings.save({"voice": "Puck"}, "ui")
        assert _file(settings_file)["settings"] == {"voice": "Puck"}
        assert (settings_file.parent / "agent_settings.json.corrupt").read_bytes() == raw  # kept for the owner


@pytest.mark.asyncio
async def test_a_file_that_is_there_but_cannot_be_read_is_never_written_over(sent, settings_file, monkeypatch,
                                                                             caplog):
    """C-54 review: _read_file gave {} for every OSError but a missing file (a Windows sharing violation from an
    editor, a scanner or a sync client; EACCES on a root-owned file in the VPS data volume), so save() wrote its changes
    over an empty object: every other saved field was lost, no .corrupt copy was kept, and the next job started as
    JARVIS with Charon and the wake word on. A brief lock is read again; one that stays makes save() raise and leaves
    the file as it was (the settings service logs it and keeps the change for its job), and load() gives the
    defaults."""
    agent_settings.save({"persona": "anna", "voice": "Puck", "wake_word": False}, "ui")
    real_read_bytes = Path.read_bytes
    failures = {"left": 0}

    def read_bytes(self: Path) -> bytes:
        if self == settings_file and failures["left"]:
            failures["left"] -= 1
            raise PermissionError(13, "The process cannot access the file: it is being used by another process")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    failures["left"] = 1  # a brief sharing violation: read again, then merged as usual
    agent_settings.save({"style": "formal"}, "ui")
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Puck", "wake_word": False,
                                                "style": "formal"}
    before = real_read_bytes(settings_file)
    failures["left"] = 10_000  # held for good (or EACCES)
    with pytest.raises(PermissionError):
        agent_settings.save({"style": "casual"}, "ui")
    assert real_read_bytes(settings_file) == before
    assert not (settings_file.parent / "agent_settings.json.corrupt").exists()
    assert agent_settings.load().values() == AgentSettings().values()  # the defaults, never an error
    service = _service(sent, settings=AgentSettings())
    with caplog.at_level(logging.WARNING, logger="JARVIS-SETTINGS"):
        payload = await service.update({"voice": "Kore"})
    assert payload["applied"] == {"voice": "applied"} and service.get("voice") == "Kore"  # it holds for this job
    assert real_read_bytes(settings_file) == before
    assert "SETTINGS not saved (PermissionError); kept for this job and the next save: voice" in caplog.messages
    assert payload["settings"]["saved"] == []  # the file does not hold it
    failures["left"] = 0  # readable again: the next save writes the pending voice with its own field, over nothing
    payload = await service.update({"wake_word": True})
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Kore", "wake_word": True,
                                                "style": "formal"}
    assert payload["settings"]["saved"] == ["voice", "wake_word"] and agent_settings.load().voice == "Kore"


@pytest.mark.asyncio
async def test_a_change_whose_save_failed_is_written_by_the_next_save(sent, settings_file, monkeypatch):
    """C-54 review: a save that failed (os.replace refused its 3 tries: a reader held the file) kept its change in
    memory only. Each save writes only its own fields and persist_live_state found no difference, so the change was
    never written, while settings_sync listed it in settings.saved; the next job loaded the old voice. A failed change
    now stays pending: settings.saved leaves it out until the file holds it, the next save writes it with its own
    fields (keeping who made it), and persist_live_state at the job's end writes what is still pending."""
    real_replace = agent_settings.os.replace
    refusals = {"left": 0}

    def replace(src: Any, dst: Any) -> None:
        if Path(dst) == settings_file and refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(agent_settings.os, "replace", replace)
    service = _service(sent)
    refusals["left"] = agent_settings._REPLACE_TRIES  # one save's tries
    payload = await service.update({"voice": "Puck"})
    assert payload["applied"] == {"voice": "applied"} and payload["settings"]["voice"] == "Puck"  # it holds ...
    assert payload["settings"]["saved"] == [] and not settings_file.exists()  # ... but is not saved
    payload = await service.update({"wake_word": False})  # the next save writes both
    assert _file(settings_file)["settings"] == {"voice": "Puck", "wake_word": False}
    assert payload["settings"]["saved"] == ["voice", "wake_word"] and agent_settings.load().voice == "Puck"
    # a persona switch (it also drops the saved voice) whose save fails: pending until the job's end
    refusals["left"] = agent_settings._REPLACE_TRIES
    payload = await service.update({"persona": "anna"})
    assert payload["settings"]["saved"] == ["wake_word"] and payload["settings"]["voice"] == "Aoede"
    assert _file(settings_file)["settings"] == {"voice": "Puck", "wake_word": False}
    assert (await service.sync("request_state"))["settings"]["saved"] == ["wake_word"]  # a sync changes nothing
    assert await service.persist_live_state() is True
    saved = _file(settings_file)
    assert saved["settings"] == {"persona": "anna", "wake_word": False} and agent_settings.load().voice == "Aoede"
    assert saved["updated"]["persona"]["by"] == "ui"  # who made the change, not the save that wrote it
    assert await service.persist_live_state() is False  # nothing left
    assert (await service.sync("request_state"))["settings"]["saved"] == ["persona", "wake_word"]


@pytest.mark.asyncio
async def test_a_hand_edited_surrogate_escape_never_stops_a_later_save(sent, settings_file):
    """C-54 review: json reads a hand edit's escape of a lone surrogate ('\\ud800' is valid JSON), but with
    ensure_ascii=False every later save raised UnicodeEncodeError, so each settings change (the UI's, a tool's, a spoken
    wake word's) held only for its job, one WARNING each, until the owner fixed the file. The file is written as ASCII
    JSON now (any other character as a \\u escape): such a value is kept as it came, and so is every other one."""
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text('{"settings": {"voice": "Kore", "note\\ud800": "x\\ud800", "style": "caf\\u00e9"}}',
                             encoding="utf-8")
    service = _service(sent)
    assert (await service.update({"wake_word": False}))["applied"] == {"wake_word": "applied"}
    assert settings_file.read_bytes().isascii()
    assert _file(settings_file)["settings"] == {"voice": "Kore", "note\ud800": "x\ud800", "style": "café",
                                                "wake_word": False}
    loaded = agent_settings.load()  # the next job starts with the change
    assert (loaded.voice, loaded.get("wake_word"), loaded.get("style")) == ("Kore", False, "café")


@pytest.mark.asyncio
async def test_a_file_nested_too_deep_counts_as_broken(sent, settings_file):
    """C-54 review: a value nested 500 to 1500 levels deep under an unknown key passed json (the parser's own limit is
    deeper) and was kept in extra; copying the settings (every update, sync and bootstrap) then raised RecursionError,
    after an update had already reached the live session, so the UI never got its settings_sync. A file whose
    containers nest deeper than MAX_FILE_DEPTH counts as broken: the defaults, and <name>.corrupt at the next save."""
    settings_file.parent.mkdir(parents=True)

    def nested(levels: int) -> bytes:  # the file's object and its settings are two of the levels
        inner = levels - 2
        return ('{"settings": {"voice": "Kore", "deep": ' + "[" * inner + "]" * inner + "}}").encode()

    for depth in (600, 1400):
        raw = nested(depth)
        settings_file.write_bytes(raw)
        assert agent_settings.load().values() == AgentSettings().values()
        service = _service(sent)
        assert (await service.update({"wake_word": False}))["applied"] == {"wake_word": "applied"}
        assert (await service.sync("t"))["settings"]["wake_word"] is False
        assert _file(settings_file)["settings"] == {"wake_word": False}
        assert (settings_file.parent / "agent_settings.json.corrupt").read_bytes() == raw  # kept for the owner
    limit = agent_settings.MAX_FILE_DEPTH  # up to the limit the file is read as it is
    for levels, kept in ((limit, True), (limit + 1, False)):
        settings_file.write_bytes(nested(levels))
        assert (agent_settings.load().voice == "Kore") is kept


@pytest.mark.asyncio
async def test_the_settings_sync_payload_matches_k61(sent):
    service = _service(sent)
    payload = await service.sync("test")
    assert set(payload) == {"settings", "applied", "errors", "pending_restart", "restart_required", "enforced",
                            "available_voices", "available_languages", "ts"}
    assert set(payload["settings"]) == K61_FIELDS | {"muted", "voice_id_enrolled", "updated_ts", "saved"}
    assert payload["available_voices"] == list(voices.GEMINI_VOICES) and len(payload["available_voices"]) == 30
    assert payload["available_languages"] == ["auto", "ur", "hi", "en"]
    assert payload["enforced"] == {"wake_word": False, "voice_id": False}  # fast mode: no hard gate (K-61)
    assert payload["pending_restart"] == payload["restart_required"] == [] and payload["applied"] == {}
    assert (payload["settings"]["muted"], payload["settings"]["voice_id_enrolled"]) == (False, False)
    assert payload["settings"]["saved"] == []  # nothing set yet: every value is a default
    assert _syncs(sent) == [payload]
    # turn_mode takes a restart (until VR-30): pending_restart, and K-02's restart_required beside it
    payload = await service.update({"turn_mode": "gated"})
    assert payload["applied"] == {"turn_mode": "pending_restart"}
    assert payload["pending_restart"] == payload["restart_required"] == ["turn_mode"]
    assert payload["settings"]["saved"] == ["turn_mode"]
    assert (await service.update({"turn_mode": "fast"}))["applied"] == {"turn_mode": "applied"}


@pytest.mark.asyncio
async def test_a_persona_change_carries_the_voice_or_drops_the_saved_one(sent, settings_file):
    """C-50: a persona switch sends that persona's voice; with a voice in the same update it goes in the same switch
    (one reconnect), without one the saved voice gives way to the persona's."""
    service = _service(sent)
    await service.update({"voice": "Puck"})
    personas = service.assistant.persona_manager
    payload = await service.update({"persona": "anna"})
    assert personas.calls == [(True, None)] and payload["settings"]["voice"] == "Aoede"
    assert "voice" not in _file(settings_file)["settings"] and agent_settings.load().voice == "Aoede"
    calls_before = list(service.assistant.calls)
    payload = await service.update({"persona": "jarvis", "voice": "kore"})
    assert personas.calls[-1] == (False, "Kore") and service.assistant.calls == calls_before  # no second voice call
    assert payload["applied"] == {"persona": "applied", "voice": "applied"}
    assert _file(settings_file)["settings"] == {"persona": "jarvis", "voice": "Kore"}
    # the persona that is active already is told again (persona_update for a waiting UI, C-50)
    await service.update({"persona": "jarvis"})
    assert personas.calls[-1] == (False, None)


@pytest.mark.asyncio
async def test_an_unchanged_wake_word_costs_no_reconnect_and_speak_typed_switches_the_audio_gate(sent):
    link = FakeLink()
    service = _service(sent, link=link)
    payload = await service.update({"wake_word": True, "speak_typed_replies": "never"})
    assert payload["applied"] == {"wake_word": "applied", "speak_typed_replies": "applied"}
    assert service.assistant.calls == []  # set_wake_word_mode would recompose and reconnect for nothing
    assert link.speak_policy == "never" and link.updates == ["settings"]
    assert (await service.update({"muted": True}))["settings"]["muted"] is True  # a control field: applied ...
    assert "muted" not in agent_settings.load().saved  # ... never saved


@pytest.mark.asyncio
async def test_the_turn_gate_fields_are_unsupported_until_the_gated_runtime_exists(sent, settings_file, monkeypatch):
    """C-54 review: voice_id, voice_id_threshold, voice_id_reject and wake_followup_s were reported 'applied', but
    nothing in this build reads them: there is no gated runtime (persona_manager.GATED_RUNTIME_AVAILABLE is False,
    C-66) and the legacy voice-ID check keeps its fixed 0.65 threshold. Like the other unsupported fields they are
    saved and shown but reported 'unsupported'; once the gated runtime exists its turn gate reads them (K-62)."""
    from src.core import persona_manager

    gate = {"voice_id": False, "voice_id_threshold": 0.6, "voice_id_reject": "polite", "wake_followup_s": 10}
    assert persona_manager.GATED_RUNTIME_AVAILABLE is False
    service = _service(sent)
    payload = await service.update(gate)
    assert payload["applied"] == dict.fromkeys(gate, "unsupported") and payload["errors"] == {}
    assert {name: payload["settings"][name] for name in gate} == gate  # shown ...
    assert _file(settings_file)["settings"] == gate and payload["settings"]["saved"] == sorted(gate)  # ... and saved
    assert agent_settings.load().get("voice_id_threshold") == 0.6
    monkeypatch.setattr(persona_manager, "GATED_RUNTIME_AVAILABLE", True)  # C-66: the turn gate reads them
    payload = await service.update(dict(gate, voice_id=True))
    assert payload["applied"] == dict.fromkeys(gate, "applied")
    assert agent_settings.GATE_FIELDS == set(gate)


@pytest.mark.asyncio
async def test_a_probe_never_writes_or_syncs(sent, settings_file):
    service = _service(sent, probe=True)
    payload = await service.update({"voice": "Puck", "persona": "anna"})
    assert service.assistant.persona_manager.calls == [(True, "Puck")]  # applied in its own job ...
    assert service.settings.get("voice") == "Puck" and payload["settings"]["persona"] == "anna"  # ... in memory
    assert not settings_file.exists() and sent == []
    await service.sync("test")
    assert await service.persist_live_state() is False
    assert not settings_file.exists() and sent == []
    # the K-57 context alone keeps a write out too (a probe job's tasks inherit it)
    token = bridge_suppressed.set(True)
    try:
        await _service([], probe=False).update({"voice": "Kore"})
    finally:
        bridge_suppressed.reset(token)
    assert not settings_file.exists()


# ---------------------------------------------------------------- the ui_commands (K-03, K-60)


@pytest.mark.asyncio
async def test_the_legacy_commands_map_onto_the_service(sent, settings_file):
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    ctx = CommandContext(assistant=assistant, runtime=service.runtime, notify_log=None, extras={})

    await dispatch("wake_word_toggle", False, ctx)
    assert assistant.calls[-1] == ("wake_word", False)  # set_wake_word_mode: wake_word_sync still goes out
    assert _syncs(sent)[-1]["settings"]["wake_word"] is False and _syncs(sent)[-1]["applied"] == {
        "wake_word": "applied"}
    await dispatch("wake_word_toggle", None, ctx)  # no bool: a toggle
    assert assistant.calls[-1] == ("wake_word", True)

    await dispatch("persona_change", "anna", ctx)
    assert assistant.persona_manager.calls[-1] == (True, None) and _syncs(sent)[-1]["settings"]["persona"] == "anna"
    await dispatch("persona_change", "jarvis", ctx)  # K-03: 'jarvis' | 'anna' (a malformed payload: the next test)
    assert assistant.persona_manager.calls[-1] == (False, None)

    await dispatch("mute", {}, ctx)
    await asyncio.gather(*assistant._spawned_tasks)
    assert assistant.calls[-1] == ("muted", True)
    assert _syncs(sent)[-1]["settings"]["muted"] is True and _syncs(sent)[-1]["applied"] == {"muted": "applied"}
    await dispatch("unmute", {}, ctx)
    await asyncio.gather(*assistant._spawned_tasks)
    assert _syncs(sent)[-1]["settings"]["muted"] is False

    await dispatch("settings_update", {"voice": "Puck"}, ctx)
    assert assistant.calls[-1] == ("voice", "Puck") and _syncs(sent)[-1]["settings"]["voice"] == "Puck"
    count = len(_syncs(sent))
    await dispatch("request_state", {}, ctx)
    assert len(_syncs(sent)) == count + 1 and _syncs(sent)[-1]["applied"] == {}
    assert _file(settings_file)["settings"] == {"wake_word": True, "persona": "jarvis", "voice": "Puck"}

    # a job without a settings service (an old integration, a test): the legacy path, nothing sent
    plain, before = FakeAssistant(), len(sent)
    await dispatch("wake_word_toggle", False, CommandContext(assistant=plain, extras={}))
    await dispatch("settings_update", {"voice": "Kore"}, CommandContext(assistant=plain, extras={}))
    assert plain.calls == [("wake_word", False)] and len(sent) == before


@pytest.mark.asyncio
async def test_a_malformed_legacy_command_reports_an_error_and_changes_nothing(sent, settings_file):
    """C-54 review: persona_change made every payload but the exact string 'anna' a 'jarvis' before validation, so a
    malformed one ('ANNA', {persona: 'anna'}, null) switched ANNA to JARVIS, saved persona 'jarvis', dropped her saved
    voice, and settings_sync carried no error; wake_word_toggle took any payload that is no bool (the string 'false')
    as a toggle, saved too. Now 'jarvis' and 'anna' count in any case, also as {persona: ...}, and anything else
    reaches validation as it came: errors.persona, nothing changes. wake_word_toggle takes a bool as the mode and no
    payload as the toggle; any other payload is errors.wake_word, no change."""
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    ctx = CommandContext(assistant=assistant, runtime=service.runtime, notify_log=None, extras={})
    personas = assistant.persona_manager
    await service.update({"persona": "anna", "voice": "Kore"})
    for payload in ("ANNA", " Anna ", {"persona": "anna"}, {"persona": "ANNA"}):
        await dispatch("persona_change", payload, ctx)  # ANNA already: only told again (C-50)
        assert personas.calls[-1] == (True, None) and _syncs(sent)[-1]["errors"] == {}
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Kore"}  # her voice kept
    calls, count = list(personas.calls), len(_syncs(sent))
    for payload in (None, "bob", "", {"persona": "bob"}, {}, 1, ["anna"]):
        await dispatch("persona_change", payload, ctx)
        sync = _syncs(sent)[-1]
        assert sync["errors"] == {"persona": "persona must be one of: jarvis, anna"} and sync["applied"] == {}
        assert (sync["settings"]["persona"], sync["settings"]["voice"]) == ("anna", "Kore")
    assert personas.calls == calls and len(_syncs(sent)) == count + 7
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Kore"}
    await dispatch("persona_change", {"persona": "Jarvis"}, ctx)  # a real switch: JARVIS's own voice (C-50)
    assert personas.calls[-1] == (False, None) and _file(settings_file)["settings"] == {"persona": "jarvis"}

    assistant.calls.clear()
    for payload in ("false", "true", 0, {"active": False}, {}, [False]):
        await dispatch("wake_word_toggle", payload, ctx)
        sync = _syncs(sent)[-1]
        assert sync["errors"] == {"wake_word": "wake_word must be true or false"} and sync["applied"] == {}
        assert sync["settings"]["wake_word"] is True
    assert assistant.calls == [] and "wake_word" not in _file(settings_file)["settings"]
    await dispatch("wake_word_toggle", None, ctx)  # no payload: the toggle
    await dispatch("wake_word_toggle", True, ctx)  # a bool: the mode
    assert assistant.calls == [("wake_word", False), ("wake_word", True)]
    assert _file(settings_file)["settings"]["wake_word"] is True

    # a job without a settings service: the same payloads, nothing switched for a malformed one
    plain = FakeAssistant()
    plain.tool_toggle_gf_mode = AsyncMock()
    plain_ctx = CommandContext(assistant=plain, extras={})
    for payload in ("ANNA", {"persona": "jarvis"}, None, "bob", {}):
        await dispatch("persona_change", payload, plain_ctx)
    for payload in ("false", None):
        await dispatch("wake_word_toggle", payload, plain_ctx)
    assert [call.args for call in plain.tool_toggle_gf_mode.await_args_list] == [(True,), (False,)]
    assert plain.calls == [("wake_word", False)]


@pytest.mark.asyncio
async def test_settings_bootstrap_is_a_validated_update_never_a_blind_overwrite(sent, settings_file):
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    await service.update({"voice": "Puck"})
    saved_ts = service.settings.updated_ts
    sent.clear()
    ctx = CommandContext(assistant=assistant, runtime=service.runtime, extras={})
    await dispatch("settings_bootstrap", {
        "voice": "Kore", "persona": "jarvis", "wake_word": True, "muted": True, "voice_id_enrolled": True,
        "style": "formal", "updated_ts": saved_ts + 10, "automation": {"desktop_control": False}, "language": "xx",
        "saved": ["voice", "persona", "wake_word", "muted", "automation", "language"]}, ctx)
    assert assistant.calls == [("voice", "Puck"), ("voice", "Kore")]  # only what differs; muted is never taken
    assert not assistant.persona_manager.calls and not assistant.muted
    [payload] = _syncs(sent)
    assert payload["applied"] == {"voice": "applied"} and payload["errors"] == {}  # its bad language is only logged
    assert _file(settings_file)["settings"] == {"voice": "Kore"}  # style is not in its saved list: a default there
    assert _file(settings_file)["updated"]["voice"]["by"] == "bootstrap"
    # a copy older than this agent's own save, or with no stamp, is not taken at all; one that changes nothing sends
    # nothing
    sent.clear()
    await service.bootstrap({"voice": "Leda", "updated_ts": service.settings.updated_ts - 1, "saved": ["voice"]})
    await service.bootstrap({"voice": "Leda", "saved": ["voice"]})
    await service.bootstrap({"voice": "Kore", "wake_word": True, "updated_ts": service.settings.updated_ts + 10,
                             "saved": ["voice", "wake_word"]})
    await service.bootstrap("not settings")
    assert sent == [] and assistant.calls[-1] == ("voice", "Kore")


@pytest.mark.asyncio
async def test_a_bootstrap_at_a_reconnect_never_undoes_a_live_change(sent, settings_file):
    """C-54 review: a spoken 'Anna ...', toggle_persona, change_voice or the set_wake_word_mode tool changes the live
    session only. At a mid-job reconnect (a bridge redeploy, a network blip) the bridge sends its copy, this agent's own
    last settings_sync with the same updated_ts, before the socket's CONNECT and so before the agent's own sync: the
    live change is saved first and the copy, now older, changes nothing (it reverted all three before)."""
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    await service.update({"voice": "Puck", "persona": "jarvis", "wake_word": True})
    copy = _syncs(sent)[-1]["settings"]  # what the bridge stored and sends back as settings_bootstrap
    assert copy["updated_ts"] == service.settings.updated_ts and copy["saved"] == ["persona", "voice", "wake_word"]
    await assistant.persona_manager.set_persona(True)  # 'Anna ...' or toggle_persona
    await assistant.request_voice("Leda")  # change_voice
    await assistant.tool_set_wake_word_mode(False)  # the set_wake_word_mode tool
    assistant.calls.clear()
    assistant.persona_manager.calls.clear()
    sent.clear()

    assert await service.bootstrap(copy) is None
    assert assistant.calls == [] and assistant.persona_manager.calls == [] and sent == []  # nothing reverted
    assert service._live_values() == {"persona": "anna", "voice": "Leda", "wake_word": False}
    saved = _file(settings_file)
    assert saved["settings"] == {"persona": "anna", "voice": "Leda", "wake_word": False}
    assert {entry["by"] for entry in saved["updated"].values()} == {"live"}  # saved first
    assert service.settings.updated_ts == saved["updated_ts"] >= copy["updated_ts"]


@pytest.mark.asyncio
async def test_a_newer_bootstrap_copy_never_undoes_a_live_change_either(sent, settings_file):
    """C-54 review: a copy newer than this agent's last save (the other brain's) but older than a live change: the
    live change is saved first, so this agent's save is the newest and the copy is not taken."""
    now = time.time()
    agent_settings.save({"voice": "Puck"}, "ui", now=now - 100)
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    await assistant.persona_manager.set_persona(True)  # no settings_sync told the bridge about it yet
    other = {"persona": "jarvis", "voice": "Kore", "updated_ts": round(now - 50, 3), "saved": ["persona", "voice"]}

    assert await service.bootstrap(other) is None
    assert assistant.persona_manager.calls == [(True, None)] and assistant.calls == []
    assert _file(settings_file)["settings"] == {"persona": "anna"}  # ANNA's own voice: the saved Puck gave way
    assert service._live_values() == {"persona": "anna", "voice": "Aoede", "wake_word": True}


@pytest.mark.asyncio
async def test_a_bootstrap_brings_back_saved_values_only_never_an_earlier_jobs_defaults(sent, settings_file,
                                                                                         monkeypatch):
    """C-54 review: settings_sync carries every value in force, the env's defaults included, and the bridge stores it
    whole. The next job's settings_bootstrap must not turn those defaults into saved settings: an echo at the same
    updated_ts (0.0 while nothing was ever saved) is not taken, and of a newer copy (the other brain's) only the
    fields its saved list names are."""
    job1 = _service(sent)
    copy = (await job1.sync("bridge_connect"))["settings"]  # what the bridge stores
    assert (copy["voice"], copy["speak_typed_replies"], copy["updated_ts"], copy["saved"]) == (
        "Charon", "auto", 0.0, [])
    # the owner sets other defaults in the env and restarts: job 2 starts with them and gets job 1's copy
    for name, value in (("JARVIS_VOICE_JARVIS", "kore"), ("JARVIS_SPEAK_TYPED", "always"),
                        ("JARVIS_VOICE_ID_THRESHOLD", "0.6"), ("JARVIS_TURN_MODE", "gated")):
        monkeypatch.setenv(name, value)
    link = FakeLink()
    job2 = _service(sent, link=link)
    await job2.apply_at_start()
    sent.clear()

    assert await job2.bootstrap(copy) is None
    assert job2.assistant.calls == [] and link.speak_policy == "always" and sent == []
    assert not settings_file.exists()  # nothing became a saved setting
    assert (job2.get("voice"), job2.get("voice_id_threshold"), job2.get("turn_mode")) == ("Kore", 0.6, "gated")
    # a newer copy (the other brain's): only what it saved is taken; its other values are that agent's defaults
    other = dict(copy, voice="Puck", style="formal", updated_ts=round(time.time(), 3), saved=["voice"])
    payload = await job2.bootstrap(other)
    assert payload["applied"] == {"voice": "applied"} and job2.assistant.calls == [("voice", "Puck")]
    assert link.speak_policy == "always" and _file(settings_file)["settings"] == {"voice": "Puck"}
    assert (job2.get("voice_id_threshold"), job2.get("turn_mode"), job2.get("style")) == (0.6, "gated", "")
    # a copy that does not say which fields were saved changes nothing
    unmarked = {key: value for key, value in other.items() if key != "saved"}
    assert await job2.bootstrap(dict(unmarked, voice="Leda", updated_ts=time.time() + 60)) is None
    assert job2.assistant.calls == [("voice", "Puck")] and _file(settings_file)["settings"] == {"voice": "Puck"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "copied"), [
    ({}, {"persona": "anna", "voice": "Charon"}),  # a fresh JARVIS agent (no file yet: a machine move, a new brain)
    ({"persona": "anna", "voice": "Kore"}, {"persona": "jarvis", "voice": "Kore"}),  # ANNA with Kore saved
])
async def test_a_bootstrap_persona_switch_carries_the_copys_voice_even_when_it_is_the_voice_in_force(
        sent, settings_file, start, copied):
    """C-54 review: a bootstrap takes only the fields that differ from the settings in force, but a persona switch
    resets the voice to that persona's own (C-50). A newer copy naming persona and voice, whose voice equalled this
    agent's voice now, lost that voice: the switch gave the persona's default, this agent's saved voice was removed and
    its next settings_sync overwrote the bridge's copy, so the owner's choice was gone on both brains. The copy's voice
    now goes into the same switch (one reconnect), exactly as the same partial does through settings_update."""
    now, persona, voice = time.time(), copied["persona"], copied["voice"]

    async def job_with(path: Any, notify: Any) -> SettingsService:
        if start:
            agent_settings.save(start, "ui", path, now=now - 100)
        service = SettingsService(SimpleNamespace(assistant=FakeAssistant(), voice_link=None),
                                  agent_settings.load(path), path=path, notify=notify)
        await service.apply_at_start()  # a saved ANNA starts as ANNA, with her saved voice
        return service

    async def notify(event_type: str, payload: Any) -> None:
        sent.append((event_type, json.loads(json.dumps(payload))))

    service = await job_with(settings_file, notify)
    personas = service.assistant.persona_manager
    payload = await service.bootstrap(dict(copied, updated_ts=round(now, 3), saved=["persona", "voice"]))
    assert personas.calls[-1] == (persona == "anna", voice) and service.assistant.calls == []  # one switch
    assert payload["applied"] == {"persona": "applied", "voice": "applied"}
    assert (payload["settings"]["persona"], payload["settings"]["voice"], payload["settings"]["saved"]) == (
        persona, voice, ["persona", "voice"])
    assert service._live_values() == {"persona": persona, "voice": voice, "wake_word": True}
    assert _file(settings_file)["settings"] == {"persona": persona, "voice": voice}
    # the same partial as a settings_update gives the same job and the same file
    other_path = settings_file.with_name("through_update.json")
    other = await job_with(other_path, lambda *_args: None)
    await other.update(copied)
    assert other._live_values() == service._live_values()
    assert _file(other_path)["settings"] == _file(settings_file)["settings"]


@pytest.mark.asyncio
async def test_the_listener_sends_settings_sync_once_the_bridge_is_connected(sent, monkeypatch):
    monkeypatch.setattr(agent_loops.socketio, "AsyncClient", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(agent_loops, "wake_retry_queue", MagicMock())
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    listener = agent_loops.UIBridgeListener(assistant, runtime=service.runtime)
    await listener.on_connect()
    await asyncio.gather(*assistant._spawned_tasks)
    assert len(_syncs(sent)) == 1 and _syncs(sent)[0]["settings"]["persona"] == "jarvis"


@pytest.mark.asyncio
async def test_a_registered_section_is_validated_applied_saved_and_shown(sent, settings_file):
    seen: list[Any] = []

    def check(value: Any) -> tuple[Any, str | None]:
        if isinstance(value, dict) and all(isinstance(flag, bool) for flag in value.values()):
            return dict(value), None
        return None, "c54_flags must map names to true or false"

    async def apply(value: Any, svc: SettingsService) -> str:
        seen.append((value, svc))
        return "applied"

    agent_settings.register_settings_section("c54_flags", check, apply, default={"x": False})
    try:
        service = _service(sent)
        assert (await service.sync("t"))["settings"]["c54_flags"] == {"x": False}  # its default until set
        payload = await service.update({"c54_flags": {"x": True}})
        assert payload["applied"] == {"c54_flags": "applied"} and seen == [({"x": True}, service)]
        assert payload["settings"]["c54_flags"] == {"x": True} and agent_settings.load().get("c54_flags") == {
            "x": True}
        assert (await service.update({"c54_flags": "on"}))["errors"] == {
            "c54_flags": "c54_flags must map names to true or false"}
        with pytest.raises(ValueError):
            agent_settings.register_settings_section("voice", check, apply)
    finally:
        agent_settings.unregister_settings_section("c54_flags")
    assert "c54_flags" not in AgentSettings().values()


@pytest.mark.asyncio
async def test_a_change_made_by_a_tool_is_saved_at_the_jobs_end(sent, settings_file):
    """The model's toggle_persona or change_voice changes the live session only; persist_live_state (the job's end)
    and every later sync save it, so the next job starts with it."""
    assistant = FakeAssistant()
    service = _service(sent, assistant)
    await assistant.persona_manager.set_persona(True)  # toggle_persona, not through the service
    assert (await service.sync("t"))["settings"]["persona"] == "anna"
    assert _file(settings_file)["settings"] == {"persona": "anna"}  # ANNA's own voice: nothing more to save
    await assistant.request_voice("Leda")  # change_voice
    assert await service.persist_live_state() is True
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Leda"}
    assert await service.persist_live_state() is False  # nothing new


# ---------------------------------------------------------------- the safe point (a real BrainAssistant)


class FakeRealtimeSession:
    """The google RealtimeSession's voice handling (livekit-plugins-google 1.4.6): it shares the model's options and
    restarts only on a change."""

    def __init__(self, opts: SimpleNamespace) -> None:
        self._opts = opts
        self._session_resumption_handle = "resume-handle"
        self.voice_changes = self.restarts = 0

    def update_options(self, *, voice: str | None = None) -> None:
        if voice is not None and self._opts.voice != voice:
            self._opts.voice = voice
            self.voice_changes += 1
            self._mark_restart_needed()

    def _mark_restart_needed(self) -> None:
        self.restarts += 1


class FakeModel:
    def __init__(self) -> None:
        self._opts = SimpleNamespace(voice="Charon", instructions=None)
        self.option_updates: list[str] = []

    def session(self) -> FakeRealtimeSession:
        return FakeRealtimeSession(self._opts)

    def update_options(self, *, voice: str) -> None:
        self.option_updates.append(voice)
        self._opts.voice = voice


class _Speech:
    def __init__(self) -> None:
        self._done, self.callbacks = False, []

    def done(self) -> bool:
        return self._done

    def add_done_callback(self, callback) -> None:
        self.callbacks.append(callback)

    def finish(self) -> None:
        self._done = True
        for callback in self.callbacks:
            callback(self)


@pytest.fixture
def brain():
    from src.core.agent_core import BrainAssistant

    model = FakeModel()
    with patch("livekit.agents.Agent.__init__", return_value=None) as agent_init, \
            patch("src.core.agent_core.jarvis_id.get_context", return_value="Test Context"), \
            patch("src.core.agent_core.JarvisPluginManager"), \
            patch("src.core.agent_core.MemoryExtractor"), \
            patch("livekit.agents.Agent.llm", new_callable=PropertyMock) as llm_prop:
        llm_prop.return_value = model
        assistant = BrainAssistant(chat_ctx=MagicMock(), llm_instance=model, tools=[])
        assistant._llm = model
        assistant._activity = None
        assistant.bridge_notifier = AsyncMock()  # no bridge POST from a test
        assistant.vision_handler = MagicMock()
        tools = {tool.info.name: tool for tool in agent_init.call_args.kwargs["tools"]}  # what the model calls
        yield SimpleNamespace(assistant=assistant, model=model, tools=tools)


async def _settle(assistant: Any) -> None:
    for _ in range(10):
        if not assistant._background_tasks:
            return
        await asyncio.wait_for(asyncio.gather(*assistant._background_tasks), 5)


@pytest.mark.asyncio
async def test_a_voice_change_during_a_reply_waits_for_the_safe_point(brain, sent, settings_file):
    """settings_update {voice:'Puck'} while JARVIS answers: settings_sync at once, no restart in the middle of the reply,
    the next reply in Puck (one fresh reconnect once the speech ended, C-50)."""
    assistant, model = brain.assistant, brain.model
    speech = _Speech()
    rt = model.session()
    assistant._activity = SimpleNamespace(update_instructions=AsyncMock(), realtime_llm_session=rt)
    assistant._active_session = SimpleNamespace(agent_state="speaking", current_speech=speech)
    service = _service(sent, assistant)

    payload = await service.update({"voice": "Puck"})
    assert payload["applied"] == {"voice": "applied"} and payload["settings"]["voice"] == "Puck"
    assert _syncs(sent) == [payload] and _file(settings_file)["settings"] == {"voice": "Puck"}
    assert rt._opts.voice == "Charon" and rt.restarts == 0  # still inside the reply: nothing restarted
    assert assistant._pending_voice == "Puck"

    assistant._active_session.agent_state = "listening"
    speech.finish()
    await _settle(assistant)
    assert rt._opts.voice == "Puck" and rt.voice_changes == 1 and assistant._pending_voice is None
    assert model.option_updates == []  # through the session's own update_options, never the shared model first


@pytest.mark.asyncio
async def test_a_tool_or_a_spoken_wake_word_change_is_saved_and_synced_at_once(brain, sent, settings_file):
    """C-54 review: the model's change_voice, set_wake_word_mode and toggle_persona tools and a spoken 'Anna ...' change
    the live session only; the assistant's on_live_settings_change hook has the settings service save each one and
    send settings_sync at once (in a task of its own), so the bridge's copy never falls behind the live state."""
    assistant, tools = brain.assistant, brain.tools
    service = _service(sent, assistant)
    hook = assistant.on_live_settings_change
    assert hook.__self__ is service and hook.__name__ == "note_live_change"  # set by the service

    async def settle() -> None:
        await asyncio.gather(*service._live_tasks)
        await _settle(assistant)

    assert (await tools["wrap_change_voice"](voice_name="leda"))["status"] == "success"
    await settle()
    assert _file(settings_file)["settings"] == {"voice": "Leda"}
    assert _syncs(sent)[-1]["applied"] == {"voice": "applied"} and _syncs(sent)[-1]["settings"]["saved"] == ["voice"]
    assert (await tools["wrap_set_wake_word_mode"](active=False))["status"] == "success"
    await settle()
    assert _file(settings_file)["settings"] == {"voice": "Leda", "wake_word": False}
    assert _syncs(sent)[-1]["settings"]["wake_word"] is False
    assert await assistant._handle_wake_word("anna, kaisi ho?") == (True, True)  # the spoken switch
    await settle()
    assert _file(settings_file)["settings"] == {"persona": "anna", "wake_word": False}  # ANNA's own voice now
    assert _syncs(sent)[-1]["settings"]["persona"] == "anna" and _syncs(sent)[-1]["settings"]["voice"] == "Aoede"
    await tools["wrap_toggle_gf_mode"](active=False)
    await settle()
    assert _file(settings_file)["settings"] == {"persona": "jarvis", "wake_word": False}
    assert {entry["by"] for entry in _file(settings_file)["updated"].values()} == {"live"}
    count = len(_syncs(sent))
    assert (await tools["wrap_change_voice"](voice_name="nobody"))["status"] == "error"
    await assistant._handle_wake_word("jarvis, time kya hai?")  # JARVIS already: no switch
    await settle()
    assert len(_syncs(sent)) == count  # nothing changed: nothing saved or sent
    # a job without a settings service: the tools work as before, nothing is saved
    assistant.on_live_settings_change = None
    await tools["wrap_change_voice"](voice_name="Puck")
    await settle()
    assert len(_syncs(sent)) == count and "voice" not in _file(settings_file)["settings"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("persona", "wake_word"), [("anna", False), ("jarvis", False), ("anna", True)])
async def test_a_job_start_tells_the_dashboard_only_the_persona_it_runs_as(brain, sent, persona, wake_word):
    """C-54 review: a saved ANNA with the wake word off started with the wake-word tool's own refresh, which announced
    JARVIS (persona_change 'jarvis'), and then ANNA's switch announced 'anna': two notifies posted side by side, so the
    bridge's active_persona could end as JARVIS while the job ran as ANNA, and no wake_word_sync went out. Now the
    instructions are composed once with both, the one persona_change names the persona the job runs as, and a saved
    wake word that is off goes out as wake_word_sync."""
    agent_settings.save({"persona": persona, "wake_word": wake_word}, "ui")
    assistant, model = brain.assistant, brain.model
    service = _service(sent, assistant)
    composed: list[dict[str, Any]] = []
    real_update_session = assistant.update_session

    async def counting_update_session(**kwargs: Any) -> None:
        composed.append(kwargs)
        await real_update_session(**kwargs)

    assistant.update_session = counting_update_session
    await service.apply_at_start()
    await _settle(assistant)
    notifier = assistant.bridge_notifier
    assert [call.args for call in notifier.notify_persona.await_args_list] == [(persona,)]
    assert [call.args for call in notifier.notify_wake_word.await_args_list] == ([] if wake_word else [(False,)])
    assert len(composed) == 1 and composed[0]["instructions"] is not None  # stored for the session, composed once
    assert f"- Persona: {persona.upper()}" in assistant._instructions
    assert f"- Wake word: {'ON' if wake_word else 'OFF'}" in assistant._instructions
    assert (assistant.persona_manager.active_persona_name, assistant.wake_word_mode) == (persona, wake_word)
    assert model.option_updates == (["Aoede"] if persona == "anna" else [])  # ANNA's voice, in the same switch
    assistant.announce_persona()  # the session start's announce (agent_runner): the persona was told already
    await _settle(assistant)
    assert len(notifier.notify_persona.await_args_list) == 1 and sent == []  # settings_sync waits for the connect


# ---------------------------------------------------------------- the job start (agent_runner.entrypoint)


class _FakeLocal:
    def __init__(self) -> None:
        self.attributes: list[str] = []

    async def set_attributes(self, attributes):
        self.attributes.append(attributes.get("jarvis.session"))


class _FakeRoom:
    name = "jarvis-hybrid"

    def __init__(self) -> None:
        self.local_participant = _FakeLocal()
        self.remote_participants: dict[str, Any] = {}
        self.handlers: dict[str, list[Any]] = {}

    def on(self, event, callback=None):
        def register(fn):
            self.handlers.setdefault(event, []).append(fn)
            return fn
        return register if callback is None else register(callback)

    def off(self, event, callback):
        if callback in self.handlers.get(event, []):
            self.handlers[event].remove(callback)


class _FakeJobContext:
    def __init__(self) -> None:
        self.room = _FakeRoom()
        self.shutdowns: list[str] = []

    async def connect(self):
        return None

    def shutdown(self, reason=""):
        self.shutdowns.append(reason)

    def is_fake_job(self):
        return False

    def add_shutdown_callback(self, _callback):
        return None


@pytest.fixture
def job(monkeypatch):
    """agent_runner.entrypoint with light stand-ins (no LiveKit, Gemini, plugins or loops); records the RealtimeModel
    it builds and what the assistant had been told when session.start ran."""
    from services.ai_core import jarvis_plugin_manager
    from src.core import agent_core, agent_runner as ar

    state = types.SimpleNamespace(models=[], starts=[], assistants=[], on_start=None)

    class JobAssistant(FakeAssistant):
        def __init__(self, **_kwargs: Any) -> None:
            super().__init__()
            self._stop_event = asyncio.Event()
            self.end_reason = None
            self.request_end = types.MethodType(agent_core.BrainAssistant.request_end, self)
            state.assistants.append(self)

        def attach_router(self, _router):
            return None

        def attach_session(self, _session):
            return None

    class FakeSession:
        def __init__(self, **_kwargs: Any) -> None:
            self.handlers: dict[str, list[Any]] = {}

        def on(self, event, callback=None):
            def register(fn):
                self.handlers.setdefault(event, []).append(fn)
                return fn
            return register if callback is None else register(callback)

        def off(self, event, callback):
            if callback in self.handlers.get(event, []):
                self.handlers[event].remove(callback)

        async def aclose(self):
            return None

        async def start(self, room, agent, room_options, record=None):  # pylint: disable=unused-argument
            state.starts.append({"model_voice": state.models[-1]["voice"],
                                 "persona_calls": list(agent.persona_manager.calls), "calls": list(agent.calls)})
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

    class FakeListener:
        def __init__(self, assistant, runtime=None):
            self.assistant, self.runtime = assistant, runtime

        async def aclose(self):
            return None

    async def nothing(*_args, **_kwargs):
        return None

    async def no_tasks(*_args, **_kwargs):
        return []

    async def returning(result):
        return result

    def realtime_model(**kwargs: Any) -> Any:
        state.models.append(kwargs)
        return SimpleNamespace(_opts=SimpleNamespace(voice=kwargs.get("voice")))

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay, result=None):
        await real_sleep(0)
        return result

    for name, value in (("_prewarm_vector_memory", lambda *_a: None), ("_prewarm_voice_id", lambda *_a: None),
                        ("get_formatted_datetime", lambda: returning({"formatted": "c54-day"})),
                        ("get_current_city", lambda: returning("Lahore")),
                        ("silero", SimpleNamespace(VAD=SimpleNamespace(load=lambda **_k: object()))),
                        ("google", SimpleNamespace(realtime=SimpleNamespace(RealtimeModel=realtime_model))),
                        ("_resolve_user_id", lambda _room: "c54-user"), ("BrainAssistant", JobAssistant),
                        ("ConversationRouter", FakeRouter), ("AgentSession", FakeSession),
                        ("harden_room_input", lambda *_a, **_k: []), ("_location_loop", nothing),
                        ("_pc_vitals_loop", nothing), ("_print_startup_banner", lambda: None),
                        ("_start_loop_watchdog", lambda *_a: None), ("_start_background_tasks", no_tasks),
                        ("_rag_warm_delay", lambda: None), ("_cleanup_session_resources", nothing),
                        ("_quiet_livekit_agents_logger", lambda: None), ("UIBridgeListener", FakeListener)):
        monkeypatch.setattr(ar, name, value)
    monkeypatch.setattr(jarvis_plugin_manager, "JarvisPluginManager", FakePlugins)
    monkeypatch.setattr(ar.asyncio, "sleep", fast_sleep)
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    state.ar = ar
    return state


async def _run_job(job, ctx) -> None:
    job.on_start = job.on_start or (lambda agent: asyncio.get_running_loop().call_later(
        0.05, agent.request_end, "paused_by_owner"))
    await asyncio.wait_for(job.ar.entrypoint(ctx), 10)
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_restart_loads_the_saved_voice_before_the_session_starts(job, settings_file):
    agent_settings.save({"voice": "Puck"}, "ui")
    ctx = _FakeJobContext()
    await _run_job(job, ctx)
    assert [model["voice"] for model in job.models] == ["Puck"]  # the job's RealtimeModel(voice=...)
    assert job.starts == [{"model_voice": "Puck", "persona_calls": [], "calls": []}]  # before session.start
    assert ctx.shutdowns == ["paused_by_owner"]
    assert _file(settings_file)["settings"] == {"voice": "Puck"}  # nothing changed during the job: no new save


@pytest.mark.asyncio
async def test_a_restart_restores_persona_and_wake_word_and_keeps_a_tools_voice(job, settings_file):
    agent_settings.save({"persona": "anna", "voice": "Kore", "wake_word": False}, "ui")

    def on_start(agent):
        agent._session_options.voice_id = "Leda"  # the model's change_voice during the job
        asyncio.get_running_loop().call_later(0.05, agent.request_end, "paused_by_owner")

    job.on_start = on_start
    await _run_job(job, _FakeJobContext())
    assert job.models[0]["voice"] == "Kore"
    [start] = job.starts
    # the wake word without a refresh of its own: ANNA's switch composes the instructions with it (one persona_change)
    assert start["persona_calls"] == [(True, "Kore")] and start["calls"] == [("wake_word", False, "no_refresh")]
    assert _file(settings_file)["settings"] == {"persona": "anna", "voice": "Leda", "wake_word": False}
    assert _file(settings_file)["updated"]["voice"]["by"] == "live"  # saved at the job's end (persist_live_state)


class _Records(logging.Handler):
    """The records of a logger that does not propagate (setup_logger), so caplog never sees them."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.asyncio
@pytest.mark.parametrize(("case", "voice"), [("utf16", "Kore"), ("ansi", "Charon"), ("load_raises", "Charon")])
async def test_a_settings_file_load_cannot_read_never_stops_the_job(job, settings_file, monkeypatch, case, voice):
    """C-54 review: agent_settings.load runs before the job's session exists, outside the runtime's retries, so an
    exception there (a UTF-16 or ANSI hand edit raised UnicodeDecodeError) ended every job as 'session_failed' and
    JARVIS stayed offline. A UTF-16 file is read as it is, an undecodable one gives the defaults, and anything else
    load() raises gives them too, with one WARNING."""
    settings_file.parent.mkdir(parents=True)
    body = json.dumps({"settings": {"voice": "Kore"}})
    if case == "utf16":
        settings_file.write_bytes(body.encode("utf-16"))
    elif case == "ansi":
        settings_file.write_bytes(json.dumps({"settings": {"voice": "Kore", "style": "café"}},
                                             ensure_ascii=False).encode("cp1252"))
    else:
        def broken_load(*_args: Any, **_kwargs: Any) -> AgentSettings:
            raise RuntimeError("the settings could not be read")
        monkeypatch.setattr(agent_settings, "load", broken_load)
    records = _Records()
    job.ar.logger.addHandler(records)
    try:
        ctx = _FakeJobContext()
        await _run_job(job, ctx)
    finally:
        job.ar.logger.removeHandler(records)
    assert [model["voice"] for model in job.models] == [voice]  # the job's RealtimeModel was built ...
    assert len(job.starts) == 1 and ctx.shutdowns == ["paused_by_owner"]  # ... and its session started
    warnings = [record.getMessage() for record in records.records if "SETTINGS not loaded" in record.getMessage()]
    assert warnings == (["SETTINGS not loaded (RuntimeError); the job starts with the defaults"]
                        if case == "load_raises" else [])
