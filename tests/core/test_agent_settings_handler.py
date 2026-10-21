"""C-60 (OP-25): the agent's settings handler with the bridge's revisions, contract K-61 v51 (with K-46 and K-03).

The bridge now stamps every settings change with a revision of its own counter (never an agent clock); revision_epoch
names its database. Its settings_update carries revision and revision_epoch, its settings_bootstrap also revisions
{field: {rev, by, src?}}. src/core/agent_settings.py keeps the revision of every field it took (for one epoch, in the
settings file's 'bridge' block), echoes them in every settings_sync with the file's instance id, never takes a field
again at an older or equal revision of the same epoch, follows a copy of another epoch (another or a recreated bridge
database) and starts its record again with it, and never takes its own change back. These tests drive SettingsService
with fakes (no LiveKit, Gemini or bridge) and a tmp_path settings file. The per-field outcomes of a settings_update
(applied / pending_restart / unsupported) are checked too.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from services.utils.ui_command_registry import CommandContext, dispatch, reset_dedupe
from src.core import agent_settings, voices
from src.core.agent_settings import SettingsService

EPOCH, OTHER_EPOCH = "c60epoch0001", "c60epoch0002"
ENV_NAMES = ("JARVIS_TURN_MODE", "JARVIS_SPEAK_TYPED", "JARVIS_VOICE_ID_THRESHOLD", "JARVIS_VOICE_JARVIS",
             "JARVIS_VOICE_ANNA")


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """Every test saves into its own tmp_path file with the K-69 names unset (fast turn mode, the default voices)."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "data" / "settings" / "agent_settings.json"
    monkeypatch.setattr(agent_settings, "SETTINGS_PATH", path)
    reset_dedupe()
    return path


class FakePersonas:
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
        self._session_options = SimpleNamespace(voice_id="jarvis_v2")
        self.wake_word_mode = True
        self.muted = False
        self.voice_id_engine = SimpleNamespace(is_enrolled=False)
        self._spawned_tasks: set[asyncio.Task[Any]] = set()
        self.calls: list[tuple[Any, ...]] = []

    async def request_voice(self, voice: str) -> None:
        self.calls.append(("voice", voice))
        self._session_options.voice_id = voice

    def set_wake_word_mode(self, active: bool, *, refresh: bool = True) -> None:
        self.calls.append(("wake_word", active))
        self.wake_word_mode = active

    async def refresh_instructions(self) -> None:
        self.calls.append(("refresh",))

    def set_muted(self, active: bool) -> None:
        self.calls.append(("muted", active))
        self.muted = active


def _service(sent: list, path: Any = None, *, probe: bool = False) -> SettingsService:
    runtime = SimpleNamespace(assistant=FakeAssistant(), voice_link=None)

    async def notify(event_type: str, payload: Any) -> None:
        sent.append((event_type, json.loads(json.dumps(payload))))  # what the bridge would get

    service = SettingsService(runtime, agent_settings.load(path), probe=probe, path=path, notify=notify)
    runtime.settings = service
    return service


def _file(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _update(fields: dict[str, Any], revision: int, epoch: str = EPOCH) -> dict[str, Any]:
    """The settings_update payload the bridge forwards (K-61 v51)."""
    return {**fields, "revision": revision, "revision_epoch": epoch}


# ---------------------------------------------------------------- settings_update: outcomes per field, the echo


@pytest.mark.asyncio
async def test_a_bridge_update_reports_each_fields_outcome_and_echoes_its_revision(settings_file):
    """Persona ANNA + language Urdu (the C-60 acceptance) and the other kinds of field: persona and voice apply,
    turn_mode waits for a restart, language and the turn-gate fields are saved but nothing reads them yet (K-61 v39,
    'unsupported'), the automation block has no section yet ('unsupported'). The revision fields are never settings."""
    sent: list = []
    service = _service(sent)
    payload = await service.update(_update({"persona": "anna", "language": "ur-PK", "turn_mode": "gated",
                                            "voice_id": False, "automation": {"desktop_control": False}}, 7))
    assert payload["applied"] == {"persona": "applied", "language": "unsupported", "turn_mode": "pending_restart",
                                  "voice_id": "unsupported", "automation": "unsupported"}
    assert payload["errors"] == {}
    assert "revision" not in payload["applied"] and "revision_epoch" not in payload["applied"]
    assert service.assistant.persona_manager.calls == [(True, None)]
    assert (payload["settings"]["persona"], payload["settings"]["language"]) == ("anna", "ur")
    # the echo: every field of the update at revision 7 of this epoch, the newest revision, the file's id
    assert payload["revisions"] == {"persona": 7, "language": 7, "turn_mode": 7, "voice_id": 7, "automation": 7}
    assert (payload["revision"], payload["revision_epoch"]) == (7, EPOCH)
    assert payload["instance"] == service.settings.instance and len(payload["instance"]) == 16
    saved = _file(settings_file)
    assert saved["bridge"] == {"epoch": EPOCH, "revs": payload["revisions"]}
    assert saved["instance"] == payload["instance"]
    assert saved["settings"] == {"persona": "anna", "language": "ur", "turn_mode": "gated", "voice_id": False}


@pytest.mark.asyncio
async def test_voice_wake_word_and_mute_outcomes_through_the_registry(settings_file):
    sent: list = []
    service = _service(sent)
    ctx = CommandContext(assistant=service.assistant, runtime=service.runtime, extras={})
    await dispatch("settings_update", _update({"voice": "puck", "wake_word": False, "muted": True,
                                               "emotionalIntelligence": False, "style": "formal"}, 3), ctx)
    [payload] = [p for event, p in sent if event == "settings_sync"]
    assert payload["applied"] == {"voice": "applied", "wake_word": "applied", "muted": "applied",
                                  "emotional_intelligence": "unsupported", "style": "unsupported"}
    assert ("voice", "Puck") in service.assistant.calls and ("wake_word", False) in service.assistant.calls
    assert payload["revisions"] == {"voice": 3, "wake_word": 3, "muted": 3, "emotional_intelligence": 3, "style": 3}
    assert "muted" not in _file(settings_file)["settings"]  # a control: applied, never saved (K-61)


@pytest.mark.asyncio
async def test_a_field_with_an_error_is_still_recorded_at_its_revision(settings_file):
    """The bridge checks types only; the agent's own check refuses a voice it does not know. The revision counts as
    processed, so the bridge stops holding the owner's value against this agent's answer."""
    sent: list = []
    service = _service(sent)
    payload = await service.update(_update({"voice": "notavoice"}, 4))
    assert set(payload["errors"]) == {"voice"} and payload["revisions"] == {"voice": 4}
    assert _file(settings_file) == {"version": 1, "updated_ts": 0.0, "settings": {}, "updated": {},
                                    "instance": service.settings.instance,
                                    "bridge": {"epoch": EPOCH, "revs": {"voice": 4}}}  # only the revisions saved


@pytest.mark.asyncio
async def test_a_queued_copy_of_a_change_taken_already_is_never_applied_twice(settings_file):
    """The bootstrap of the agent's connect brings the stored value at revision 5; the queued settings_update of the
    same change (revision 5) follows: it is left out, so the voice is not requested twice (no second reconnect), and
    as it brings nothing new, no settings_sync answers it."""
    sent: list = []
    service = _service(sent)
    await service.bootstrap({"voice": "Kore", "revision": 5, "revision_epoch": EPOCH,
                             "revisions": {"voice": {"rev": 5, "by": "ui"}}})
    assert service.assistant.calls == [("voice", "Kore")]
    sent.clear()
    assert await service.update(_update({"voice": "Kore"}, 5)) is None
    assert service.assistant.calls == [("voice", "Kore")] and sent == []
    # an older revision of that field is left out too, while a field the agent never took applies
    payload = await service.update(_update({"voice": "Leda", "wake_word": False}, 4))
    assert service.assistant.calls == [("voice", "Kore"), ("wake_word", False)]
    assert payload["applied"] == {"wake_word": "applied"} and payload["revisions"] == {"voice": 5, "wake_word": 4}
    # an unknown key beside the left-out field is still answered ('unsupported'): that update is not left out whole
    payload = await service.update(_update({"voice": "Kore", "volume": 3}, 5))
    assert payload["applied"] == {"volume": "unsupported"}


@pytest.mark.asyncio
async def test_the_bootstraps_outcomes_stay_the_last_word_after_its_queued_copy(settings_file):
    """C-60 review: the UI replaces its applied and errors maps with every settings_sync. The agent comes back to the
    owner's Persona ANNA + language Urdu: its bootstrap reports persona 'applied' and language 'unsupported'; the
    queued settings_update of the same change follows and is left out whole. It must not answer with applied {} and
    errors {}, which blanked those labels."""
    sent: list = []
    service = _service(sent)
    revisions = {"persona": {"rev": 3, "by": "ui"}, "language": {"rev": 3, "by": "ui"}}
    await service.bootstrap({"persona": "anna", "language": "ur", "revision": 3, "revision_epoch": EPOCH,
                             "revisions": revisions})
    answer = await service.update(_update({"persona": "anna", "language": "ur"}, 3))
    syncs = [payload for event, payload in sent if event == "settings_sync"]
    ui_applied, ui_errors = syncs[-1]["applied"], syncs[-1]["errors"]  # what the panel shows now
    assert ui_applied == {"persona": "applied", "language": "unsupported"} and ui_errors == {}
    assert len(syncs) == 1 and answer is None  # the bootstrap's sync only
    assert syncs[-1]["revisions"] == {"persona": 3, "language": 3}  # the echo went out with the bootstrap's sync
    assert service.assistant.persona_manager.calls == [(True, None)]  # one switch


@pytest.mark.asyncio
async def test_an_update_of_another_epoch_is_taken_and_the_record_follows_it(settings_file):
    """A bridge whose database was replaced (or the PC's local bridge) has another epoch: its revisions are never
    compared with the old epoch's, the update is taken, and the record starts again with it."""
    sent: list = []
    service = _service(sent)
    await service.update(_update({"voice": "Kore", "wake_word": False}, 50))
    payload = await service.update(_update({"voice": "Leda"}, 1, OTHER_EPOCH))
    calls = service.assistant.calls
    assert sorted(calls[:2]) == [("voice", "Kore"), ("wake_word", False)] and calls[2:] == [("voice", "Leda")]
    assert (payload["revision_epoch"], payload["revisions"], payload["revision"]) == (OTHER_EPOCH, {"voice": 1}, 1)
    assert _file(settings_file)["bridge"] == {"epoch": OTHER_EPOCH, "revs": {"voice": 1}}  # replaced, not merged


@pytest.mark.asyncio
async def test_an_update_without_a_revision_works_as_before(settings_file):
    """An older bridge (or the legacy wake_word_toggle / persona_change routes inside the agent): no revision, no
    bridge block, the echo says nothing was taken."""
    sent: list = []
    service = _service(sent)
    payload = await service.update({"voice": "Kore", "revision": "7", "revision_epoch": EPOCH})
    assert payload["applied"] == {"voice": "applied"} and payload["errors"] == {}  # a junk revision is dropped
    assert (payload["revision"], payload["revision_epoch"], payload["revisions"]) == (0, "", {})
    assert "bridge" not in _file(settings_file)


@pytest.mark.asyncio
async def test_the_revisions_and_the_instance_survive_a_restart(settings_file):
    sent: list = []
    first = _service(sent)
    await first.update(_update({"persona": "anna"}, 9))
    second = _service(sent)  # the next job of this worker reads the same file
    assert second.settings.instance == first.settings.instance
    assert (second.settings.bridge_epoch, second.settings.bridge_revs) == (EPOCH, {"persona": 9})
    echo = (await second.sync("bridge_connect"))
    assert (echo["revision"], echo["revisions"], echo["instance"]) == (9, {"persona": 9}, first.settings.instance)


@pytest.mark.asyncio
async def test_two_jobs_of_one_file_keep_each_others_revisions(settings_file):
    sent: list = []
    job1, job2 = _service(sent), _service(sent)
    await job1.update(_update({"voice": "Kore"}, 3))
    await job2.update(_update({"wake_word": False}, 4))
    assert _file(settings_file)["bridge"] == {"epoch": EPOCH, "revs": {"voice": 3, "wake_word": 4}}


@pytest.mark.asyncio
async def test_a_probe_job_never_writes_the_revisions(settings_file):
    sent: list = []
    probe = _service(sent, probe=True)
    await probe.update(_update({"voice": "Kore"}, 3))
    await probe.bootstrap({"voice": "Leda", "revision": 4, "revision_epoch": EPOCH,
                           "revisions": {"voice": {"rev": 4, "by": "ui"}}})
    assert not settings_file.exists() and sent == []


# ---------------------------------------------------------------- settings_bootstrap by revision


@pytest.mark.asyncio
async def test_a_bootstrap_takes_newer_fields_and_never_its_own_change_back(settings_file):
    sent: list = []
    service = _service(sent)
    await service.update(_update({"voice": "Kore"}, 2))
    service.assistant.calls.clear()
    sent.clear()
    me = service.settings.instance
    copy = {"voice": "Leda", "persona": "anna", "wake_word": False, "muted": True, "style": "formal",
            "turn_mode": "gated", "saved": ["voice", "persona", "wake_word", "style"], "updated_ts": 1.0,
            "revision": 6, "revision_epoch": EPOCH,
            "revisions": {"voice": {"rev": 2, "by": "ui"},                  # taken already at revision 2
                          "persona": {"rev": 5, "by": "ui"},                # the owner's change: newer
                          "wake_word": {"rev": 6, "by": "agent", "src": me},  # this agent's own: never back
                          "muted": {"rev": 6, "by": "ui"},                  # a control: never from a bootstrap
                          "style": {"rev": 4, "by": "agent", "src": "otheragent01"},  # the other brain saved it
                          # the other brain's env default (not in its saved list): never a saved value here
                          "turn_mode": {"rev": 3, "by": "agent", "src": "otheragent01"}}}
    payload = await service.bootstrap(copy)
    assert service.assistant.persona_manager.calls == [(True, None)]
    assert ("wake_word", False) not in service.assistant.calls and not service.assistant.muted
    assert payload["applied"] == {"persona": "applied", "style": "unsupported"}
    assert "turn_mode" not in _file(settings_file)["settings"] and service.get("turn_mode") == "fast"
    assert payload["revisions"] == {"voice": 2, "persona": 5, "wake_word": 6, "muted": 6, "style": 4, "turn_mode": 3}
    assert _file(settings_file)["bridge"]["revs"] == payload["revisions"]
    # the same copy again changes nothing and sends nothing
    sent.clear()
    assert await service.bootstrap(copy) is None and sent == []


@pytest.mark.asyncio
async def test_clock_skew_never_drops_a_newer_revision(settings_file):
    """K-46 v39 known gap: this agent's clock runs an hour ahead, so its last save is 'newer' than the copy the other
    brain wrote with its own (correct) clock. The clock rule took nothing; the revision is newer, so it is taken."""
    sent: list = []
    agent_settings.save({"voice": "Kore"}, "ui", settings_file, now=time.time() + 3600)
    service = _service(sent)
    copy = {"voice": "Puck", "saved": ["voice"], "updated_ts": round(time.time(), 3)}
    assert await service.bootstrap(copy) is None  # the clock rule: the copy looks older
    assert service.assistant.calls == []
    payload = await service.bootstrap({**copy, "revision": 3, "revision_epoch": EPOCH,
                                       "revisions": {"voice": {"rev": 3, "by": "agent", "src": "otheragent01"}}})
    assert service.assistant.calls == [("voice", "Puck")] and payload["applied"] == {"voice": "applied"}


@pytest.mark.asyncio
async def test_fields_without_a_revision_keep_the_clock_rule(settings_file):
    """A copy stored partly before C-60: the stamped fields follow their revisions, the others the clock rule."""
    sent: list = []
    service = _service(sent)
    copy = {"voice": "Puck", "persona": "anna", "saved": ["voice", "persona"], "updated_ts": time.time() + 60,
            "revision": 1, "revision_epoch": EPOCH, "revisions": {"voice": {"rev": 1, "by": "ui"}}}
    payload = await service.bootstrap(copy)
    assert payload["applied"] == {"persona": "applied", "voice": "applied"}
    stale = {**copy, "persona": "jarvis", "updated_ts": 1.0}  # older than this agent's save now: not by the clock
    assert await service.bootstrap(stale) is None
    assert service.assistant.persona_manager.active_persona_name == "anna"


@pytest.mark.asyncio
async def test_a_bootstrap_older_than_the_record_of_its_epoch_takes_only_its_newer_fields(settings_file):
    """bridge.db restored from a backup (or copied) keeps its epoch while its counter goes back (50) below what this
    agent took (persona 80, voice 70). The agent never lowers its record within one epoch: the bootstrap's persona
    (revision 40, older than the persona this agent took) is left out, its wake_word (45, never taken) is taken, and the
    record keeps persona 80 and voice 70. Its settings_sync echoes them, so the bridge catches its counter up past 80
    (tests/infrastructure/test_bridge_settings.py) and the owner's next change is newer than the record again."""
    sent: list = []
    agent_settings.save({"persona": "anna"}, "ui", settings_file,
                        bridge={"epoch": EPOCH, "revs": {"persona": 80, "voice": 70}})
    service = _service(sent, settings_file)
    service.assistant.persona_manager.is_anna = True  # the job runs as its file says
    payload = await service.bootstrap({"persona": "jarvis", "wake_word": False, "revision": 50, "revision_epoch": EPOCH,
                                       "revisions": {"persona": {"rev": 40, "by": "ui"},
                                                     "wake_word": {"rev": 45, "by": "ui"}}})
    assert payload["applied"] == {"wake_word": "applied"} and service.get("persona") == "anna"
    assert (payload["revision"], payload["revisions"]) == (80, {"persona": 80, "voice": 70, "wake_word": 45})
    assert _file(settings_file)["bridge"] == {"epoch": EPOCH, "revs": {"persona": 80, "voice": 70, "wake_word": 45}}
    assert service.assistant.persona_manager.calls == []  # never switched back to the restored copy's persona
    payload = await service.update(_update({"persona": "jarvis"}, 81))  # the owner's change after the catch-up
    assert payload["applied"] == {"persona": "applied"} and service.get("persona") == "jarvis"


@pytest.mark.asyncio
async def test_a_queued_update_older_than_the_record_is_left_out(settings_file):
    """A queued settings_update flushed after the bootstrap may be older than the record (a field changed again since,
    or a row a restored backup still held): it is left out by revision order, and the record stays."""
    sent: list = []
    service = _service(sent, settings_file)
    await service.bootstrap({"voice": "Kore", "persona": "anna", "revision": 4, "revision_epoch": EPOCH,
                             "revisions": {"voice": {"rev": 3, "by": "ui"}, "persona": {"rev": 4, "by": "ui"}}})
    assert await service.update(_update({"voice": "Kore"}, 3)) is None
    assert service.settings.bridge_revs == {"voice": 3, "persona": 4}


@pytest.mark.asyncio
async def test_a_bootstrap_that_takes_nothing_still_records_its_revisions(settings_file):
    sent: list = []
    service = _service(sent)
    copy = {"voice": "Charon", "revision": 2, "revision_epoch": EPOCH,
            "revisions": {"voice": {"rev": 2, "by": "ui"}}}
    assert await service.bootstrap(copy) is None  # Charon is in force already
    assert _file(settings_file)["bridge"] == {"epoch": EPOCH, "revs": {"voice": 2}} and sent == []


# ---------------------------------------------------------------- another bridge database (another epoch)


@pytest.mark.asyncio
async def test_a_bootstrap_of_another_epoch_is_followed_and_the_record_starts_again(settings_file):
    """A bootstrap of another epoch comes from another or a recreated bridge database. Its revisions say nothing about
    this agent's record (persona 70 and voice 90 of the old epoch are higher numbers, never newer): every field it has a
    revision for is followed (the owner's persona and voice, another agent's saved style), except this agent's own
    change coming back, a control and another agent's default, and the record starts again with the copy's epoch and
    revisions, in the file too (replaced, never merged). The agent's own change after it is its own again
    (tests/infrastructure/test_bridge_settings.py: it lands at the bridge)."""
    sent: list = []
    agent_settings.save({"persona": "jarvis", "voice": "Kore"}, "ui", settings_file, instance="c60agentpc001",
                        bridge={"epoch": OTHER_EPOCH, "revs": {"persona": 70, "voice": 90}})
    service = _service(sent, settings_file)
    assert service.settings.instance == "c60agentpc001"
    copy = {"persona": "anna", "voice": "Puck", "style": "formal", "wake_word": False, "muted": True,
            "turn_mode": "gated", "saved": ["style"], "revision": 6, "revision_epoch": EPOCH,
            "revisions": {"persona": {"rev": 1, "by": "ui"}, "voice": {"rev": 2, "by": "ui"},
                          "style": {"rev": 3, "by": "agent", "src": "otheragent01"},
                          "wake_word": {"rev": 4, "by": "agent", "src": "c60agentpc001"},  # its own: never back
                          "muted": {"rev": 5, "by": "ui"},                                  # a control
                          "turn_mode": {"rev": 6, "by": "agent", "src": "otheragent01"}}}   # a default there
    payload = await service.bootstrap(copy)
    assert payload["applied"] == {"persona": "applied", "voice": "applied", "style": "unsupported"}
    assert (service.get("persona"), service.get("voice"), service.get("style")) == ("anna", "Puck", "formal")
    assert service.get("wake_word") is True and not service.assistant.muted and service.get("turn_mode") == "fast"
    revisions = {"persona": 1, "voice": 2, "style": 3, "wake_word": 4, "muted": 5, "turn_mode": 6}
    assert (payload["revision_epoch"], payload["revisions"], payload["revision"]) == (EPOCH, revisions, 6)
    assert _file(settings_file)["bridge"] == {"epoch": EPOCH, "revs": revisions}
    # the same copy again changes nothing; a field the owner changes later is taken by its revision
    sent.clear()
    assert await service.bootstrap(copy) is None and sent == []
    payload = await service.update(_update({"voice": "Leda"}, 7))
    assert payload["applied"] == {"voice": "applied"} and payload["revisions"]["voice"] == 7
