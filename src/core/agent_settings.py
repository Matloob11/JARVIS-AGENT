"""
# src/core/agent_settings.py
The agent's settings service (plan task C-54; contract K-61 CT-VC-SETTINGS, with K-03, K-46 and K-60).

The agent is the single source of truth for its settings: data/settings/agent_settings.json (gitignored), ASCII JSON
written atomically (a temp file in the same folder, fsync, os.replace) under one process-wide threading.Lock. The file
holds only what was set; every other field has its K-61 default (some from the env, below). A missing or broken file
(invalid JSON, bytes that are no UTF-8/16/32 text, or containers nested deeper than MAX_FILE_DEPTH) gives the defaults,
and the next save keeps a broken one as <name>.corrupt. A file that is there but cannot be read (after _READ_TRIES
tries) gives the defaults too and is never written over: that save fails. A failed save (that, or an os.replace refused
_REPLACE_TRIES times) leaves its changes pending: they hold for the job, the next save writes them with its own fields
(persist_live_state at the job's end at the latest), and settings_sync leaves them out of settings.saved until then.
agent_runner starts a job with the defaults whatever load() hits. Probe and canary jobs (K-57) read the file but keep
their own in-memory copy: they never write it and never send settings_sync.

    AgentSettings                         the K-61 defaults plus the saved values: get(field), voice, values(),
                                          saved_names() (what was set; the rest are defaults)
    validate(partial) -> (clean, errors)  aliases mapped (emotionalIntelligence, wake_word_active), BCP-47 language
                                          codes mapped (ur-PK -> ur), voice aliases resolved (verse -> Puck)
    load(path=None) / save(changes, by, path=None)
    register_settings_section(name, validate_fn, apply_fn, default=None)   another area's own settings block
    SettingsService(runtime, settings, probe=False)                          one per job, as runtime.settings
        await update(partial, source)  validate -> apply (the C-50/C-51 paths) -> persist -> notify settings_sync
        await bootstrap(payload)       the bridge's settings_bootstrap: a validated update, never a blind overwrite;
                                       the live state is saved first, then only a copy strictly newer than this
                                       agent's last save is taken, and of it only the fields its `saved` names (a
                                       persona switch in it carries its voice, as a settings_update does)
        await sync(reason)             settings_sync with the live state (bridge connect, request_state, mute/unmute)
        note_live_change(reason)       the assistant's on_live_settings_change hook (a tool or a spoken wake word
                                       changed persona, voice or wake word): saved and synced at once, in a task
        await apply_at_start()         saved persona and wake word (and the audio-out policy) before session.start,
                                       the instructions composed once (one persona_change; wake_word_sync when off)
        await persist_live_state()     at the job's end: a voice, persona or wake word changed by a tool is kept, and
                                       a change a failed save left pending is written
    settings_service_of(ctx)              the job's service from a ui_command's CommandContext (ctx.runtime.settings)

settings_sync.settings carries every field as in force (defaults from the env included) plus muted, voice_id_enrolled,
updated_ts and saved (the names of the fields that were set and are in the file). The bridge stores it whole and sends
it back as settings_bootstrap, so a default never comes back as a saved value and an echo of this agent's own state
(the same updated_ts) never undoes a live change.

Bridge revisions (C-60, K-61 v51): the bridge stamps every settings change with a revision of its own counter (never
an agent clock), and revision_epoch names its database (made once; a bridge restart or a restored backup keeps it).
Its settings_update carries revision and revision_epoch, its settings_bootstrap also revisions {field: {rev, by,
src?}}. This agent keeps the revision of every field it took, for one epoch, in the file's 'bridge' block, and the
file's random 'instance' id; each settings_sync echoes revision, revision_epoch, revisions and instance at its top
level. With the epoch of its record, a field is taken only at a revision newer than the one taken before (a queued
copy of a change the bootstrap brought already is left out, and an update left out whole sends no settings_sync) and
never when it is this agent's own change coming back (src = instance), so a PC/VPS brain switch does not depend on the
agents' clocks. A copy of another epoch comes from another or a recreated bridge database: every field it has a
revision for is followed (the same own-change, control and saved-list rules apply) and the record starts again with
that epoch and those revisions. A copy without revisions (an older bridge) keeps the clock rule. This agent never
lowers its record within one epoch: when the bridge's database went back (restored or copied), the echo above the
bridge's newest revision makes the bridge catch its counter up (bridge_settings.caught_up), so its later changes are
newer than this record again.

How each field applies (update):
    voice                 BrainAssistant.request_voice: the live session gets it at the next safe point (C-50), never
                          in the middle of a reply; a persona change in the same update carries it (one reconnect)
    persona               persona_manager.set_persona: instructions and voice at the next safe point, persona_change
                          once the switch reached the session (C-50); the saved voice then follows the persona
    wake_word             BrainAssistant.set_wake_word_mode (the prompt follows it, wake_word_sync still goes out);
                          only when it changes (an unchanged mode would cost a fresh Gemini reconnect)
    voice_id, voice_id_threshold, voice_id_reject, wake_followup_s (GATE_FIELDS)
                          saved, reported 'unsupported' while there is no gated runtime (C-66: nothing in this build
                          reads them; the legacy voice-ID check keeps its fixed 0.65 threshold), then 'applied': that
                          runtime's turn gate reads them live (K-62) through get(); `enforced` tells the UI they are
                          enforced only in gated mode (voice_id after enrollment too)
    turn_mode             'pending_restart' while it differs from the running mode (until VR-30)
    speak_typed_replies   the job's VoiceLink audio-out policy (C-41), decided again at once
    muted                 (settings_update only) BrainAssistant.set_muted; never saved
    language, transcript_script, quiet_hours, confirm_level, style, emotional_intelligence
                          saved, reported 'unsupported' until a consumer reads them (gated STT, C-56 quiet hours, ...)

The ui_command handlers settings_update, settings_bootstrap and request_state are registered here (K-60: an area
registers its commands from its own module); agent_loops routes wake_word_toggle and persona_change through update()
(a payload that is no persona or no bool reaches validation as it came: errors, no change) and sends settings_sync
after mute/unmute; agent_core's toggle_persona, change_voice and set_wake_word_mode tools and its spoken wake-word
persona switch call the on_live_settings_change hook.
"""
# pylint: disable=too-many-lines  # one service: the K-61 fields, the file, the bridge revisions and the handlers
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from secrets import token_hex
from typing import Any

from services.ai_core.jarvis_prompt import normalize_turn_mode
from services.utils.probe_context import is_probe_job
from services.utils.ui_command_registry import register_ui_command
from src.core.roomio_link import SPEAK_POLICIES, SPEAK_TYPED_ENV, speak_policy
from src.core.voices import GEMINI_VOICES, persona_voice, resolve_voice, voices_help

logger = logging.getLogger("JARVIS-SETTINGS")

# ------------------------------------------------------------------ the fields (K-61)
FIELDS: tuple[str, ...] = (
    "voice", "persona", "wake_word", "wake_followup_s", "voice_id", "voice_id_threshold", "voice_id_reject",
    "turn_mode", "transcript_script", "language", "speak_typed_replies", "quiet_hours", "confirm_level", "style",
    "emotional_intelligence",
)
ALIASES = {"emotionalIntelligence": "emotional_intelligence", "wake_word_active": "wake_word"}
CONTROL_FIELDS = ("muted",)                        # settings_update only: applied, never saved
# The agent's own output, dropped from any input. 'saved' lists the fields that were set (every other value in
# settings_sync is a default, the env's included): a settings_bootstrap brings back only those.
READ_ONLY_FIELDS = ("voice_id_enrolled", "updated_ts", "saved")
PERSONAS = ("jarvis", "anna")
LANGUAGES = ("auto", "ur", "hi", "en")
VOICE_ID_REJECTS = ("silent", "polite")
TRANSCRIPT_SCRIPTS = ("roman", "native")
CONFIRM_LEVELS = ("normal", "strict")  # a confirm_level may only make confirmation stricter (K-45)
TURN_MODES = ("fast", "gated")
WAKE_FOLLOWUP_MAX_S = 60.0
THRESHOLD_MIN, THRESHOLD_MAX, THRESHOLD_DEFAULT = 0.2, 0.9, 0.45
THRESHOLD_ENV = "JARVIS_VOICE_ID_THRESHOLD"  # K-69
TURN_MODE_ENV = "JARVIS_TURN_MODE"           # K-69
STYLE_MAX_CHARS = 40
# Saved and shown, but nothing in this build reads them yet: 'unsupported' until a consumer moves them out
# (language and transcript_script: the gated STT, C-66; quiet_hours: speak_proactive, C-56).
UNSUPPORTED_FIELDS = frozenset({"language", "transcript_script", "quiet_hours", "confirm_level", "style",
                                "emotional_intelligence"})
# The turn gate's fields (K-62): only the gated runtime (C-66) has a gate that reads them, so they are saved and shown
# but 'unsupported' while persona_manager.GATED_RUNTIME_AVAILABLE is False (C-54 review: reported 'applied', though the
# legacy voice-ID check keeps its fixed 0.65 threshold and nothing else reads them).
GATE_FIELDS = frozenset({"voice_id", "voice_id_threshold", "voice_id_reject", "wake_followup_s"})
RESTART_FIELDS = frozenset({"turn_mode"})  # take effect with a later job (VR-30 makes turn_mode switchable)
APPLIED, PENDING_RESTART, UNSUPPORTED = "applied", "pending_restart", "unsupported"
# The order fields are applied in: the wake word first (the persona's instructions are composed with it), the persona
# before the voice (a persona change sets that persona's voice, an explicit voice then wins).
_APPLY_ORDER = {name: index for index, name in enumerate(("wake_word", "persona", "voice", "muted"))}

# ------------------------------------------------------------------ the file
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = _PROJECT_ROOT / "data" / "settings" / "agent_settings.json"  # tests point this elsewhere
FILE_VERSION = 1
_FILE_LOCK = threading.Lock()  # one writer and reader at a time in this process (Windows runs jobs as threads)
_REPLACE_TRIES = 3
_READ_TRIES = 3  # a file that is there but cannot be read is read again (a Windows sharing violation is brief)
# Containers (objects, arrays) nested deeper than this make the file broken: no K-61 field or section comes close, and
# copy.deepcopy (every update and sync copies the settings), the json encoder and a section's check all recurse.
MAX_FILE_DEPTH = 32
REMOVE: Any = object()  # in save() changes: drop a saved field (its default applies again)
# C-60 (K-61 v51): the bridge's settings revisions. A settings_update and a settings_bootstrap carry revision (an int
# the bridge owns) and revision_epoch (its database); this agent keeps the revision of every field it took, for one
# epoch, in the file's 'bridge' block ({epoch, revs}), and its settings_sync echoes them with its 'instance' (the
# file's id).
REVISION_FIELDS = ("revision", "revision_epoch", "revisions")
_EPOCH_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_INSTANCE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_BRIDGE_REVS = 64  # fields with a revision kept (the K-61 fields and sections are far fewer)


def _revision(value: Any) -> int | None:
    """A bridge revision: a non-negative int, never a bool."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _clean_bridge(raw: Any) -> tuple[str, dict[str, int]]:
    """(epoch, {field: revision}) of a file's 'bridge' block; ('', {}) when there is none or it is not usable."""
    if not isinstance(raw, Mapping):
        return "", {}
    epoch = raw.get("epoch")
    if not (isinstance(epoch, str) and _EPOCH_RE.fullmatch(epoch)):
        return "", {}
    revs_raw = raw.get("revs")
    revs: dict[str, int] = {}
    for name, value in (revs_raw.items() if isinstance(revs_raw, Mapping) else ()):
        rev = _revision(value)
        if isinstance(name, str) and 0 < len(name) <= 64 and rev is not None and len(revs) < MAX_BRIDGE_REVS:
            revs[name] = rev
    return epoch, revs


def _merge_bridge(file_block: Any, given: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The 'bridge' block a save writes: the given one, merged field by field (the higher revision) with the file's
    when both name the same epoch (another job of this file may have taken other fields; within one epoch a revision
    only grows); else the given one (another epoch replaces the file's record)."""
    file_epoch, file_revs = _clean_bridge(file_block)
    if given is None:
        return {"epoch": file_epoch, "revs": file_revs} if file_epoch else None
    epoch, revs = _clean_bridge(given)
    if not epoch:
        return {"epoch": file_epoch, "revs": file_revs} if file_epoch else None
    if epoch == file_epoch:
        for name, rev in file_revs.items():
            if rev > revs.get(name, -1) and (name in revs or len(revs) < MAX_BRIDGE_REVS):
                revs[name] = rev
    return {"epoch": epoch, "revs": revs}


def _printable(value: Any, limit: int) -> str:
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def _number(value: Any) -> float | None:
    """A finite number from an int, a float or a numeric string; None for anything else (bool included)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _tidy(number: float) -> int | float:
    return int(number) if float(number).is_integer() else round(number, 1)


# ------------------------------------------------------------------ validation
Check = Callable[[Any], tuple[Any, str | None]]


def _bool_check(name: str) -> Check:
    def check(value: Any) -> tuple[Any, str | None]:
        if isinstance(value, bool):
            return value, None
        return None, f"{name} must be true or false"
    return check


def _choice_check(name: str, choices: tuple[str, ...]) -> Check:
    def check(value: Any) -> tuple[Any, str | None]:
        text = value.strip().lower() if isinstance(value, str) else None
        if text in choices:
            return text, None
        return None, f"{name} must be one of: {', '.join(choices)}"
    return check


def _check_voice(value: Any) -> tuple[Any, str | None]:
    voice = resolve_voice(value)
    if voice:
        return voice, None
    return None, f"Voice '{_printable(value, 40)}' nahi mili. {voices_help()}"  # K-69 v35's change_voice answer


def _check_followup(value: Any) -> tuple[Any, str | None]:
    number = _number(value)
    if number is None or not 0.0 <= number <= WAKE_FOLLOWUP_MAX_S:
        return None, f"wake_followup_s must be a number from 0 to {int(WAKE_FOLLOWUP_MAX_S)}"
    return _tidy(number), None


def _check_threshold(value: Any) -> tuple[Any, str | None]:
    number = _number(value)
    if number is None or not THRESHOLD_MIN <= number <= THRESHOLD_MAX:
        return None, f"voice_id_threshold must be a number from {THRESHOLD_MIN} to {THRESHOLD_MAX}"
    return round(number, 3), None


def _check_language(value: Any) -> tuple[Any, str | None]:
    """'auto' | 'ur' | 'hi' | 'en'; a BCP-47 tag maps to its primary language (ur-PK -> ur, en_US -> en)."""
    text = value.strip() if isinstance(value, str) else ""
    primary = re.split(r"[-_]", text, maxsplit=1)[0].lower() if text else ""
    if primary in LANGUAGES:
        return primary, None
    return None, "language must be auto, ur, hi or en (a BCP-47 code such as ur-PK or en-US is mapped)"


_QUIET_HOURS = re.compile(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*")


def _check_quiet_hours(value: Any) -> tuple[Any, str | None]:
    """'HH:MM-HH:MM' (start and end differ; zero-padded when saved) or '' (none)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", None
    match = _QUIET_HOURS.fullmatch(value) if isinstance(value, str) else None
    if match:
        start_h, start_m, end_h, end_m = (int(part) for part in match.groups())
        times_ok = max(start_h, end_h) < 24 and max(start_m, end_m) < 60
        if times_ok and (start_h, start_m) != (end_h, end_m):
            return f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}", None
    return None, "quiet_hours must be 'HH:MM-HH:MM' (start and end differ) or empty"


def _check_style(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, str):
        text = "".join(ch for ch in value if ch.isprintable()).strip()
        if len(text) <= STYLE_MAX_CHARS:
            return text, None
    return None, f"style must be text of at most {STYLE_MAX_CHARS} characters"


_CHECKS: dict[str, Check] = {
    "voice": _check_voice,
    "persona": _choice_check("persona", PERSONAS),
    "wake_word": _bool_check("wake_word"),
    "wake_followup_s": _check_followup,
    "voice_id": _bool_check("voice_id"),
    "voice_id_threshold": _check_threshold,
    "voice_id_reject": _choice_check("voice_id_reject", VOICE_ID_REJECTS),
    "turn_mode": _choice_check("turn_mode", TURN_MODES),
    "transcript_script": _choice_check("transcript_script", TRANSCRIPT_SCRIPTS),
    "language": _check_language,
    "speak_typed_replies": _choice_check("speak_typed_replies", SPEAK_POLICIES),
    "quiet_hours": _check_quiet_hours,
    "confirm_level": _choice_check("confirm_level", CONFIRM_LEVELS),
    "style": _check_style,
    "emotional_intelligence": _bool_check("emotional_intelligence"),
    "muted": _bool_check("muted"),
}


@dataclass(frozen=True)
class SettingsSection:
    """Another area's settings block (register_settings_section): validate_fn(value) -> (clean, error | None) and
    apply_fn(clean, service) -> 'applied' | 'pending_restart' | 'unsupported' (or an awaitable of one)."""
    name: str
    validate: Callable[[Any], tuple[Any, str | None]]
    apply: Callable[[Any, "SettingsService"], Any]
    default: Any = None


_SECTIONS: dict[str, SettingsSection] = {}


def register_settings_section(name: str, validate_fn: Callable[[Any], tuple[Any, str | None]],
                              apply_fn: Callable[[Any, "SettingsService"], Any], default: Any = None) -> None:
    """K-61 extension: a block of settings another area owns (e.g. the automation flags, K-24). Its value is validated,
    applied, saved and shown in settings_sync like a field; default is what settings_sync shows while none is saved
    (None: the block is left out until set). Re-registering a name replaces it."""
    taken = (*FIELDS, *ALIASES, *CONTROL_FIELDS, *READ_ONLY_FIELDS)
    if not isinstance(name, str) or not name or name in taken:
        raise ValueError(f"settings section name {name!r} is empty or taken by a K-61 field")
    if name in _SECTIONS:
        logger.info("settings section %s re-registered", name)
    _SECTIONS[name] = SettingsSection(name, validate_fn, apply_fn, default)


def unregister_settings_section(name: str) -> None:
    """Removes a section (tests, an area that goes away)."""
    _SECTIONS.pop(name, None)


def _canonical(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    return ALIASES.get(key, key)


def _is_known(key: str) -> bool:
    return key in _CHECKS or key in _SECTIONS


def validate(partial: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """
    K-61: (clean, errors) for a partial settings object. Keys are mapped first (emotionalIntelligence ->
    emotional_intelligence, wake_word_active -> wake_word; the canonical key wins when both are sent); every K-61 field,
    the control field 'muted' and each registered section is checked, clean holding the canonical value (a voice alias
    resolved, a BCP-47 language mapped) and errors {field: message} the rest. Read-only fields (voice_id_enrolled,
    updated_ts) and unknown keys are in neither (unknown_keys() names the unknown ones).
    """
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if not isinstance(partial, Mapping):
        return clean, {"settings": "settings must be an object"}
    items = sorted(partial.items(), key=lambda item: item[0] in ALIASES, reverse=True)  # aliases first: canonical wins
    for raw_key, raw_value in items:
        key = _canonical(raw_key)
        if key is None or key in READ_ONLY_FIELDS or not _is_known(key):
            continue
        section = _SECTIONS.get(key) if key not in _CHECKS else None
        try:
            value, error = section.validate(raw_value) if section is not None else _CHECKS[key](raw_value)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a section's own check failed
            value, error = None, f"{key} could not be checked ({type(exc).__name__})"
        if error:
            errors[key] = str(error)
            clean.pop(key, None)
        else:
            clean[key] = value
            errors.pop(key, None)
    return clean, errors


def unknown_keys(partial: Any) -> list[str]:
    """The keys of a partial that are no K-61 field, alias, control or read-only field and no registered section."""
    if not isinstance(partial, Mapping):
        return []
    found = []
    for raw_key in partial:
        key = _canonical(raw_key)
        if key is None:
            found.append(_printable(raw_key, 64))
        elif key not in READ_ONLY_FIELDS and not _is_known(key):
            found.append(_printable(key, 64))
    return found


# ------------------------------------------------------------------ the settings object
def _env_threshold() -> float:
    """JARVIS_VOICE_ID_THRESHOLD (K-69) when it is a number in 0.2..0.9, else 0.45."""
    number = _number(os.getenv(THRESHOLD_ENV))
    if number is None or not THRESHOLD_MIN <= number <= THRESHOLD_MAX:
        return THRESHOLD_DEFAULT
    return round(number, 3)


def default_value(name: str, persona: str = "jarvis") -> Any:
    """The K-61 default of a field: the env where K-69 names one (JARVIS_VOICE_ID_THRESHOLD, JARVIS_TURN_MODE,
    JARVIS_SPEAK_TYPED), the persona's voice (JARVIS_VOICE_JARVIS / JARVIS_VOICE_ANNA, C-50) for voice, a section's
    own default, else the fixed value."""
    if name == "voice":
        return persona_voice(persona if persona in PERSONAS else "jarvis")
    if name == "voice_id_threshold":
        return _env_threshold()
    if name == "turn_mode":
        return normalize_turn_mode(os.getenv(TURN_MODE_ENV))
    if name == "speak_typed_replies":
        return speak_policy(os.getenv(SPEAK_TYPED_ENV))
    if name in _SECTIONS:
        return copy.deepcopy(_SECTIONS[name].default)
    return copy.deepcopy(_FIXED_DEFAULTS.get(name))


_FIXED_DEFAULTS: dict[str, Any] = {
    "persona": "jarvis", "wake_word": True, "wake_followup_s": 0, "voice_id": True, "voice_id_reject": "silent",
    "transcript_script": "roman", "language": "auto", "quiet_hours": "", "confirm_level": "normal", "style": "",
    "emotional_intelligence": True,
}


@dataclass
class AgentSettings:
    """The K-61 settings of this agent: `saved` holds the values that were set (validated), every other field has its
    default (default_value). `updated` records who set each saved field and when ({field: {by, ts}}); updated_ts is
    the time of the last save (0.0 when nothing was ever saved). `extra` keeps saved blocks no section is registered
    for yet, unread."""
    saved: dict[str, Any] = field(default_factory=dict)
    updated: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_ts: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)
    # C-60 (K-61 v51): this settings file's id (echoed as 'instance': the bridge marks this agent's own changes with
    # it, so a settings_bootstrap never brings them back), and the bridge revision of every field taken, for one epoch
    instance: str = ""
    bridge_epoch: str = ""
    bridge_revs: dict[str, int] = field(default_factory=dict)

    validate = staticmethod(validate)

    @classmethod
    def from_data(cls, data: Any) -> "AgentSettings":
        """The settings in a file's JSON object; an invalid saved value is logged and its default used."""
        data = data if isinstance(data, Mapping) else {}
        raw = data.get("settings")
        raw = dict(raw) if isinstance(raw, Mapping) else {}
        extra = {key: value for key, value in raw.items() if isinstance(key, str) and key not in _CHECKS
                 and key not in _SECTIONS and key not in ALIASES and key not in READ_ONLY_FIELDS}
        clean, errors = validate({key: value for key, value in raw.items() if key not in extra})
        for name in sorted(errors):
            logger.warning("SETTINGS the saved %s is not valid; its default is used", name)
        saved = {key: value for key, value in clean.items() if key not in CONTROL_FIELDS}
        updated_raw = data.get("updated")
        updated = {key: dict(value) for key, value in (updated_raw.items() if isinstance(updated_raw, Mapping) else ())
                   if key in saved and isinstance(value, Mapping)}
        instance = data.get("instance")
        epoch, revs = _clean_bridge(data.get("bridge"))
        return cls(saved=saved, updated=updated, updated_ts=_number(data.get("updated_ts")) or 0.0, extra=extra,
                   instance=instance if isinstance(instance, str) and _INSTANCE_RE.fullmatch(instance) else "",
                   bridge_epoch=epoch, bridge_revs=revs)

    def copy(self) -> "AgentSettings":
        return AgentSettings(saved=copy.deepcopy(self.saved), updated=copy.deepcopy(self.updated),
                             updated_ts=self.updated_ts, extra=copy.deepcopy(self.extra), instance=self.instance,
                             bridge_epoch=self.bridge_epoch, bridge_revs=dict(self.bridge_revs))

    def get(self, name: str) -> Any:
        """The value of a field or section: the saved one, else its default (a saved voice, else the persona's)."""
        if name in self.saved:
            return copy.deepcopy(self.saved[name])
        section = _SECTIONS.get(name)
        if section is not None and name in self.extra:  # saved before its section was registered
            try:
                value, error = section.validate(self.extra[name])
            except Exception:  # pylint: disable=broad-exception-caught
                value, error = None, "invalid"
            if not error:
                return value
        return default_value(name, self.saved.get("persona", "jarvis"))

    @property
    def voice(self) -> str:
        """The voice the job starts with: the saved one, else the saved persona's (C-50)."""
        return str(self.get("voice"))

    def values(self) -> dict[str, Any]:
        """Every K-61 field, then each registered section that has a value (saved or a default)."""
        values = {name: self.get(name) for name in FIELDS}
        for name in _SECTIONS:
            value = self.get(name)
            if value is not None:
                values[name] = value
        return values

    def saved_names(self) -> list[str]:
        """The fields and sections that were set, sorted; every other value is a default (the env's included).
        settings_sync carries them as settings.saved, so a settings_bootstrap brings back only these (K-61)."""
        names = set(self.saved)
        for name, section in _SECTIONS.items():
            if name in self.extra and name not in names:  # saved before its section was registered, as get() reads
                try:
                    error = section.validate(self.extra[name])[1]
                except Exception:  # pylint: disable=broad-exception-caught
                    error = "invalid"
                if not error:
                    names.add(name)
        return sorted(names)

    def apply_changes(self, changes: Mapping[str, Any], by: str, ts: float | None = None) -> None:
        """In memory, what save() writes: REMOVE drops a saved field."""
        when = round(time.time() if ts is None else ts, 3)
        for name, value in changes.items():
            if value is REMOVE:
                self.saved.pop(name, None)
                self.updated.pop(name, None)
            else:
                self.saved[name] = copy.deepcopy(value)
                self.updated[name] = {"by": by, "ts": when}
                self.extra.pop(name, None)


def _read_bytes(path: Path) -> bytes | None:
    """The file's bytes, or None when it is missing. Any other OSError (a sharing violation from an editor, a scanner
    or a sync client on Windows; EACCES on a root-owned file in the VPS data volume) is tried _READ_TRIES times, then
    raised."""
    attempt = 0
    while True:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            attempt += 1
            if attempt >= _READ_TRIES:
                raise
            time.sleep(0.05 * attempt)


def _nested_deeper_than(data: Any, limit: int) -> bool:
    """True when the containers (dicts, lists) in data nest more than limit levels (data itself is level 1). Counted
    level by level, without recursion."""
    level, current = 0, [data]
    while current:
        containers = [value for value in current if isinstance(value, (dict, list))]
        if not containers:
            return False
        level += 1
        if level > limit:
            return True
        current = [child for value in containers for child in (value.values() if isinstance(value, dict) else value)]
    return False


def _read_file(path: Path) -> tuple[dict[str, Any], bool]:
    """(the file's JSON object, False), ({}, False) when it is missing, or ({}, True) when it is broken: not a JSON
    object, or containers nested deeper than MAX_FILE_DEPTH (C-54 review: a value nested 500 to 1500 levels deep passed
    the parser, and copying the settings then raised RecursionError in every update and sync). save() writes ASCII JSON;
    json also reads a hand edit with a BOM or in UTF-16/32 (Windows PowerShell 5.1's '>' and Out-File write UTF-16).
    Bytes that decode as none of these (an ANSI 'é', a damaged block) count as broken, like invalid JSON, so neither
    load() nor save() raises for them (C-54 review: a UnicodeDecodeError here stopped every job). A file that is there
    but cannot be read raises its OSError (_read_bytes): load() gives the defaults, save() writes nothing (C-54 review:
    it wrote its changes over an empty object, and every other saved field was lost)."""
    raw = _read_bytes(path)
    if raw is None:
        return {}, False
    try:
        data = json.loads(raw)
    except UnicodeDecodeError:
        logger.warning("SETTINGS %s is not UTF-8 text; defaults are used", path.name)
        return {}, True
    except (ValueError, RecursionError):  # RecursionError: nested too deep for the parser
        logger.warning("SETTINGS %s is not valid JSON; defaults are used", path.name)
        return {}, True
    if not isinstance(data, dict):
        logger.warning("SETTINGS %s holds no settings object; defaults are used", path.name)
        return {}, True
    if _nested_deeper_than(data, MAX_FILE_DEPTH):
        logger.warning("SETTINGS %s is nested deeper than %d levels; defaults are used", path.name, MAX_FILE_DEPTH)
        return {}, True
    return data, False


def load(path: str | os.PathLike[str] | None = None) -> AgentSettings:
    """The saved settings (K-61), read under the process-wide lock; a missing, broken or unreadable file gives the
    defaults (never an error). Only reads, so a probe job may call it too."""
    target = Path(path) if path is not None else Path(SETTINGS_PATH)
    with _FILE_LOCK:
        try:
            data = _read_file(target)[0]
        except OSError as exc:
            logger.warning("SETTINGS %s not readable (%s); defaults are used", target.name, type(exc).__name__)
            data = {}
    return AgentSettings.from_data(data)


def _write_atomic(path: Path, body: Mapping[str, Any]) -> None:
    """The JSON text into a temp file next to path (fsync), then os.replace; a reader never sees half a file. The text
    is ASCII: every other character is a \\u escape, so a lone surrogate a hand edit brought in ('\\ud800', valid JSON)
    is written back as it came and never makes a save raise UnicodeEncodeError (C-54 review: with ensure_ascii=False
    every later save failed, and each change held only for its job)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(body, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: a reader holds the file for a moment
                if attempt == _REPLACE_TRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def save(changes: Mapping[str, Any], by: str, path: str | os.PathLike[str] | None = None, *,  # noqa: PLR0913
         now: float | None = None, by_field: Mapping[str, str] | None = None,
         bridge: Mapping[str, Any] | None = None, instance: str | None = None) -> float:
    """
    Merges changes ({field: value}; REMOVE drops a saved field) into the file under the process-wide lock: only these
    fields change, so another job's newer fields are kept. The folder is created when missing; a broken file (no JSON
    object, undecodable text, nested too deep: _read_file) is kept as <name>.corrupt. A file that is there but cannot
    be read raises its OSError and is left as it is, never written over (SettingsService._commit logs it; the change
    holds for the job and stays pending for its next save). Each field is recorded as set by `by`, or by its
    by_field entry (a change an earlier failed save left pending keeps who made it). Writes atomically (ASCII JSON)
    and returns the new updated_ts (epoch seconds).
    C-60 (K-61 v51): bridge {epoch, revs} is merged into the file's 'bridge' block (_merge_bridge: field by field for
    the same epoch, replacing it for another) and instance is written when the file has none (the file's own id
    wins). A save with no changes writes only those and keeps the file's updated_ts, which it returns.
    """
    target = Path(path) if path is not None else Path(SETTINGS_PATH)
    when = round(time.time() if now is None else now, 3)
    by_field = by_field or {}
    with _FILE_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        data, corrupt = _read_file(target)  # unreadable: raises, so its other saved fields are never lost
        if corrupt:
            try:
                os.replace(target, target.with_name(target.name + ".corrupt"))
            except OSError:
                pass
        saved = data.get("settings")
        saved = dict(saved) if isinstance(saved, Mapping) else {}
        updated = data.get("updated")
        updated = dict(updated) if isinstance(updated, Mapping) else {}
        for name, value in changes.items():
            if value is REMOVE:
                saved.pop(name, None)
                updated.pop(name, None)
            else:
                saved[name] = value
                updated[name] = {"by": by_field.get(name, by), "ts": when}
        if not changes:  # only the bridge block or the instance: the last save of settings keeps its stamp
            when = _number(data.get("updated_ts")) or 0.0
        body: dict[str, Any] = {"version": FILE_VERSION, "updated_ts": when, "settings": saved, "updated": updated}
        file_instance = data.get("instance")
        if isinstance(file_instance, str) and _INSTANCE_RE.fullmatch(file_instance):
            body["instance"] = file_instance
        elif isinstance(instance, str) and _INSTANCE_RE.fullmatch(instance):
            body["instance"] = instance
        bridge_block = _merge_bridge(data.get("bridge"), bridge)
        if bridge_block is not None:
            body["bridge"] = bridge_block
        _write_atomic(target, body)
    return when


# ------------------------------------------------------------------ the service
def running_turn_mode() -> str:
    """The turn mode this job runs in (persona_manager.runtime_turn_mode: 'gated' only once the gated runtime exists)."""
    try:
        from src.core.persona_manager import runtime_turn_mode  # pylint: disable=import-outside-toplevel
        return runtime_turn_mode()
    except Exception:  # pylint: disable=broad-exception-caught  # a host without livekit: the env's mode, never gated
        return "fast"


def gated_runtime_available() -> bool:
    """persona_manager.GATED_RUNTIME_AVAILABLE (C-66): only the gated runtime has the turn gate (K-62) that reads
    GATE_FIELDS, so until then they are 'unsupported'."""
    try:
        from src.core import persona_manager  # pylint: disable=import-outside-toplevel
        return persona_manager.GATED_RUNTIME_AVAILABLE is True
    except Exception:  # pylint: disable=broad-exception-caught  # a host without livekit: no gated runtime either
        return False


async def _maybe_await(result: Any) -> Any:
    return await result if inspect.isawaitable(result) else result


async def _default_notify(event_type: str, payload: Any) -> None:
    from services.utils.jarvis_bridge import notify_event  # pylint: disable=import-outside-toplevel
    await notify_event(event_type, payload)


class SettingsService:
    """
    One per job (agent_runner: runtime.settings). The runtime gives it the job's assistant (runtime.assistant) and
    VoiceLink (runtime.voice_link). Updates run one at a time; each ends in one settings_sync whose snapshot is taken
    right before it is sent, so the last one the bridge gets always carries the newest state. A change whose save
    failed stays pending until a later save writes it (_commit). A probe job's service applies in memory only (K-57).
    The assistant's on_live_settings_change hook is set to note_live_change: a voice, persona or wake word a tool or a
    spoken wake word changed is saved and synced at once.
    """

    def __init__(self, runtime: Any, settings: AgentSettings | None = None, *, probe: bool = False,
                 path: str | os.PathLike[str] | None = None,
                 notify: Callable[[str, Any], Any] | None = None) -> None:
        self.runtime = runtime
        self.settings = settings if settings is not None else AgentSettings()
        self.probe = bool(probe)
        self.path = path
        self._notify = notify
        self._lock = asyncio.Lock()
        self._live_tasks: set[asyncio.Task[Any]] = set()
        # {field: (value or REMOVE, by)}: changes in force here that the file does not hold yet (a failed save, or the
        # save in flight); changed only by _commit, under the service lock
        self._pending: dict[str, tuple[Any, str]] = {}
        # C-60 (K-61 v51): the file's id (made here for a new file, written with its next save), and whether a bridge
        # revision was taken that the file does not hold yet
        if not self.settings.instance:
            self.settings.instance = token_hex(8)
        self._bridge_dirty = False
        assistant = self.assistant
        if assistant is not None:
            try:
                assistant.on_live_settings_change = self.note_live_change
            except (AttributeError, TypeError):  # a stand-in that takes no attributes: saved at sync and job end
                logger.debug("SETTINGS the assistant takes no live-change hook")

    # ---- what the job has
    @property
    def assistant(self) -> Any:
        return getattr(self.runtime, "assistant", None)

    @property
    def voice_link(self) -> Any:
        return getattr(self.runtime, "voice_link", None)

    def get(self, name: str) -> Any:
        """A setting as in force now (the live persona, wake word and voice; the turn gate reads its fields here)."""
        live = self._live_values()
        return live[name] if name in live else self.settings.get(name)

    def _live_values(self) -> dict[str, Any]:
        """The assistant's own state where it is known: persona, wake_word and the last voice asked for (by a setting,
        a persona switch or the change_voice tool). Anything that is not a real value (a stand-in) is left out."""
        assistant = self.assistant
        live: dict[str, Any] = {}
        if assistant is None:
            return live
        persona = getattr(getattr(assistant, "persona_manager", None), "active_persona_name", None)
        if isinstance(persona, str) and persona in PERSONAS:
            live["persona"] = persona
        wake = getattr(assistant, "wake_word_mode", None)
        if isinstance(wake, bool):
            live["wake_word"] = wake
        voice_id = getattr(getattr(assistant, "_session_options", None), "voice_id", None)
        voice = resolve_voice(voice_id) if isinstance(voice_id, str) else None
        if voice:
            live["voice"] = voice
        return live

    def _muted(self) -> bool:
        muted = getattr(self.assistant, "muted", False)
        return muted if isinstance(muted, bool) else False

    def _enrolled(self) -> bool:
        enrolled = getattr(getattr(self.assistant, "voice_id_engine", None), "is_enrolled", False)
        return enrolled if isinstance(enrolled, bool) else False

    def _live_changes(self) -> dict[str, Any]:
        """What the assistant runs with that the settings do not say yet (a tool changed it): the changes that make the
        settings say it. A persona change drops the saved voice first (the persona's own voice applies, C-50)."""
        live = self._live_values()
        probe = self.settings.copy()
        changes: dict[str, Any] = {}
        persona = live.get("persona")
        if persona is not None and persona != probe.get("persona"):
            changes["persona"] = persona
            if "voice" in probe.saved:
                changes["voice"] = REMOVE
            probe.apply_changes(changes, "live")
        wake = live.get("wake_word")
        if wake is not None and wake != probe.get("wake_word"):
            changes["wake_word"] = wake
        voice = live.get("voice")
        if voice is not None and voice != probe.get("voice"):
            changes["voice"] = voice
        return changes

    # ---- persistence and the event
    async def _commit(self, changes: Mapping[str, Any], by: str, *, retry: bool = False) -> bool:
        """
        The changes in memory, then in the file (never in a probe job), together with every change an earlier failed
        save left pending (a newer change of the same field wins). A failed save is logged and its changes stay
        pending: they hold for this job, settings_sync leaves them out of settings.saved, and the next save writes
        them; with retry (persist_live_state, the job's end) they are written even with no change of this call's own
        (C-54 review: each save wrote only its own fields, so the change of a failed save was never written while
        settings.saved listed it, and the next job started with the old value). True when the file was written.
        """
        if changes:
            self.settings.apply_changes(changes, by)
        if self.probe or is_probe_job() or not (changes or (retry and self._pending) or self._bridge_dirty):
            return False
        pending = {name: entry for name, entry in self._pending.items() if name not in changes}
        pending.update((name, (value, by)) for name, value in changes.items())
        self._pending = pending  # until the file holds them, even while the save is in flight
        try:
            when = await asyncio.to_thread(
                save, {name: value for name, (value, _by) in pending.items()}, by, self.path,
                by_field={name: who for name, (_value, who) in pending.items()}, bridge=self._bridge_block(),
                instance=self.settings.instance)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SETTINGS not saved (%s); kept for this job and the next save: %s", type(exc).__name__,
                           ",".join(sorted(pending)) or "bridge revisions")
            return False
        if pending:
            self.settings.updated_ts = when
        self._pending = {}
        self._bridge_dirty = False
        return True

    # ---- the bridge's revisions (C-60, K-61 v51)
    def _bridge_block(self) -> dict[str, Any] | None:
        settings = self.settings
        return {"epoch": settings.bridge_epoch, "revs": dict(settings.bridge_revs)} if settings.bridge_epoch else None

    def _bridge_rev(self, name: str, epoch: str) -> int:
        """The bridge revision of a field this agent took with that epoch; -1 when none (or another epoch: another or
        a recreated bridge database, whose every field is new here)."""
        if epoch != self.settings.bridge_epoch:
            return -1
        return self.settings.bridge_revs.get(name, -1)

    def _note_bridge(self, epoch: str, revs: Mapping[str, int]) -> None:
        """Records that this agent took (or saw as its own) these fields at these revisions of the bridge's database;
        another epoch starts the record again with these revisions (never lowered within one epoch). Saved with the
        next save (_commit)."""
        settings = self.settings
        if epoch != settings.bridge_epoch:
            settings.bridge_epoch, settings.bridge_revs = epoch, {}
            self._bridge_dirty = True
        for name, rev in revs.items():
            if rev > settings.bridge_revs.get(name, -1) and (name in settings.bridge_revs
                                                            or len(settings.bridge_revs) < MAX_BRIDGE_REVS):
                settings.bridge_revs[name] = rev
                self._bridge_dirty = True

    @staticmethod
    def _split_revision(payload: Mapping[str, Any]) -> tuple[dict[str, Any], int | None, str]:
        """(the payload without the bridge's revision fields, the revision, its epoch); (payload, None, '') when it
        carries no usable revision (an older bridge, a legacy command)."""
        rest = {key: value for key, value in payload.items() if key not in REVISION_FIELDS}
        revision, epoch = _revision(payload.get("revision")), payload.get("revision_epoch")
        if revision is None or not (isinstance(epoch, str) and _EPOCH_RE.fullmatch(epoch)):
            return rest, None, ""
        return rest, revision, epoch

    def _echo(self) -> dict[str, Any]:
        """K-61 v51: what this agent applied of the bridge's revisions, at the top level of every settings_sync:
        revision (the newest), revision_epoch, revisions {field: rev} and instance (this file's id). A fresh agent
        echoes an empty record, so the bridge keeps every owner change it has not taken yet; a record above the
        bridge's newest revision (its database went back) makes the bridge catch its counter up."""
        settings = self.settings
        return {"revision": max(settings.bridge_revs.values(), default=0) if settings.bridge_epoch else 0,
                "revision_epoch": settings.bridge_epoch, "revisions": dict(settings.bridge_revs),
                "instance": settings.instance}

    def snapshot(self, applied: Mapping[str, str] | None = None,
                 errors: Mapping[str, str] | None = None) -> dict[str, Any]:
        """The K-61 settings_sync payload: settings (every field as in force, plus muted, voice_id_enrolled,
        updated_ts and saved: the names of the fields that were set and are in the file, the others being defaults or
        a change still pending after a failed save), applied, errors, pending_restart (with K-02's restart_required
        beside it), enforced, the voices and languages the UI may offer, ts. The bridge stores settings whole and
        sends it back as settings_bootstrap (bootstrap() reads saved)."""
        values = self.settings.values()
        values.update(self._live_values())
        saved = [name for name in self.settings.saved_names() if name in values and name not in self._pending]
        values["muted"] = self._muted()
        values["voice_id_enrolled"] = self._enrolled()
        values["updated_ts"] = self.settings.updated_ts
        values["saved"] = saved
        running = running_turn_mode()
        pending = [name for name in sorted(RESTART_FIELDS) if values.get(name) != running]
        gated = running == "gated"
        return {
            "settings": values,
            "applied": dict(applied or {}),
            "errors": dict(errors or {}),
            "pending_restart": pending,
            "restart_required": list(pending),
            "enforced": {"wake_word": gated and bool(values.get("wake_word")),
                         "voice_id": gated and bool(values.get("voice_id")) and values["voice_id_enrolled"]},
            "available_voices": list(GEMINI_VOICES),
            "available_languages": list(LANGUAGES),
            "ts": round(time.time(), 3),
            **self._echo(),
        }

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.probe:
            return
        notify = self._notify or _default_notify
        try:
            await _maybe_await(notify("settings_sync", payload))
        except Exception as exc:  # pylint: disable=broad-exception-caught  # notify_event never raises; a stand-in may
            logger.debug("settings_sync not sent: %s", type(exc).__name__)

    async def _send_snapshot(self, applied: Mapping[str, str] | None = None,
                             errors: Mapping[str, str] | None = None) -> dict[str, Any]:
        payload = self.snapshot(applied, errors)  # taken right before the send: no await in between
        await self._send(payload)
        return payload

    # ---- the API
    async def update(self, partial: Any, source: str = "ui", *, changes_only: bool = False) -> dict[str, Any] | None:
        """
        K-61 settings_update: validate -> apply -> persist -> settings_sync. An invalid field is reported in errors and
        changes nothing; the valid ones of the same update still apply. An unknown key is 'unsupported'. With
        changes_only (a bootstrap) only fields that differ from the settings in force apply, nothing is sent when none
        does, and errors are only logged. Returns the settings_sync payload (None when nothing was sent).
        C-60 (K-61 v51): the bridge's settings_update carries revision and revision_epoch (never settings). A field this
        agent took at that revision or a newer one already with the same epoch (the settings_bootstrap of its connect,
        a queued copy of the same change) is left out; every other field of the update (all of them with another epoch:
        another or a recreated bridge database, whose revisions start the record again) is recorded at that revision,
        whatever its outcome, and the settings_sync echoes it. An update whose every field was left out is no news:
        nothing is sent (None), so the outcomes the bootstrap reported stay the UI's last word (it replaces its applied
        and errors maps with every settings_sync), and the echo of those revisions went out with the bootstrap's or goes
        with the next settings_sync.
        """
        if not isinstance(partial, Mapping):
            logger.info("SETTINGS_UPDATE source=%s ignored: the payload is no object", source)
            if changes_only:
                return None
            return await self._send_snapshot(errors={"settings": "settings_update needs an object of settings"})
        partial, revision, epoch = self._split_revision(partial)
        async with self._lock:
            skipped: list[str] = []
            if revision is not None:
                names = {_canonical(key) for key in partial} - {None}
                skipped = sorted(name for name in names if self._bridge_rev(name, epoch) >= revision)
                partial = {key: value for key, value in partial.items() if _canonical(key) not in skipped}
                self._note_bridge(epoch, {name: revision for name in names if name not in skipped})
            clean, errors = validate(partial)
            applied: dict[str, str] = {} if changes_only else {key: UNSUPPORTED for key in unknown_keys(partial)}
            clean = await self._update_locked(clean, applied, errors, source, changes_only=changes_only)
        if skipped:
            logger.info("SETTINGS_UPDATE revision %d: %s taken at that revision or a newer one already%s", revision,
                        ",".join(skipped), "" if partial else " (nothing else in it: no settings_sync)")
            if not partial:
                return None
        return await self._report(clean, applied, errors, source, changes_only=changes_only)

    async def _update_locked(self, clean: dict[str, Any], applied: dict[str, str], errors: dict[str, str],
                             source: str, *, changes_only: bool) -> dict[str, Any]:
        """update()'s apply -> persist, run with the service lock held (bootstrap() holds it already). With
        changes_only only the fields that differ from the settings in force apply, and a persona that does keeps the
        voice beside it: a persona switch resets the voice to that persona's own (C-50), so the voice goes into the
        same switch even when it equals the voice in force now, as in a settings_update (C-54 review: it was dropped,
        the switch gave the persona's default voice and this agent's saved voice was removed). Returns the fields
        taken."""
        if changes_only:
            taken = {key: value for key, value in clean.items() if key not in CONTROL_FIELDS
                     and value != self.get(key)}
            if "persona" in taken and "voice" in clean:
                taken["voice"] = clean["voice"]
            clean = taken
        changes = await self._apply_all(clean, applied, errors, source)
        changes.update({key: value for key, value in self._live_changes().items() if key not in changes})
        await self._commit(changes, source)
        return clean

    async def _report(self, clean: Mapping[str, Any], applied: Mapping[str, str], errors: Mapping[str, str],
                      source: str, *, changes_only: bool) -> dict[str, Any] | None:
        """update()'s log line and settings_sync; a changes_only update that took nothing sends nothing, and its
        errors are only logged."""
        logger.log(logging.INFO if applied or errors or not changes_only else logging.DEBUG,
                   "SETTINGS_UPDATE source=%s applied=%s errors=%s", source,
                   ",".join(f"{key}:{status}" for key, status in sorted(applied.items())) or "-",
                   ",".join(sorted(errors)) or "-")
        if changes_only:
            if errors:
                logger.info("SETTINGS bootstrap fields not taken: %s", ",".join(sorted(errors)))
            if not clean:
                return None
            return await self._send_snapshot(applied)
        return await self._send_snapshot(applied, errors)

    async def _apply_all(self, clean: dict[str, Any], applied: dict[str, str], errors: dict[str, str],
                         source: str) -> dict[str, Any]:
        """Applies each clean field (wake word, persona, voice, muted, then the rest); returns the changes to save."""
        changes: dict[str, Any] = {}
        voice_done = False
        for name in sorted(clean, key=lambda key: (_APPLY_ORDER.get(key, len(_APPLY_ORDER)), key)):
            value = clean[name]
            if name == "voice" and voice_done:  # the persona change carried it
                applied[name] = APPLIED
                changes[name] = value
                continue
            try:
                if name == "persona":
                    outcome = await self._apply_persona(value, clean.get("voice"))
                    status = None if outcome is None else APPLIED
                else:
                    outcome, status = None, await self._apply(name, value, source)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # never breaks the other fields
                logger.warning("SETTINGS %s not applied (%s)", name, type(exc).__name__)
                errors[name] = f"{name} could not be applied ({type(exc).__name__})"
                continue
            if status is None:  # nothing in this job can take it
                applied[name] = UNSUPPORTED
                continue
            applied[name] = status
            if name in CONTROL_FIELDS:
                continue
            if outcome is not None:  # the persona: (changed, voice carried)
                changed, voice_done = outcome
                if changed and not voice_done and "voice" in self.settings.saved:
                    changes["voice"] = REMOVE  # the persona's own voice applies now (C-50)
            changes[name] = value
        return changes

    async def _apply(self, name: str, value: Any, source: str) -> str | None:
        """One field (not the persona) into the job; returns its status, or None when nothing here can take it."""
        handler = {"wake_word": self._apply_wake_word, "voice": self._apply_voice, "muted": self._apply_muted}.get(name)
        if handler is not None:
            return await handler(value)
        if name == "speak_typed_replies":
            self._apply_speak_policy(value, "settings")
            return APPLIED
        if name in RESTART_FIELDS:
            return APPLIED if value == running_turn_mode() else PENDING_RESTART
        if name in GATE_FIELDS:  # saved; only the gated runtime's turn gate reads them (K-62)
            return APPLIED if gated_runtime_available() else UNSUPPORTED
        section = _SECTIONS.get(name)
        if section is not None:
            status = await _maybe_await(section.apply(value, self))
            return status if status in (APPLIED, PENDING_RESTART, UNSUPPORTED) else APPLIED
        logger.debug("SETTINGS %s saved (source %s)", name, source)
        return UNSUPPORTED if name in UNSUPPORTED_FIELDS else APPLIED

    async def _apply_wake_word(self, active: bool) -> str | None:
        """set_wake_word_mode (the prompt follows it in the background and wake_word_sync goes out), only for a change:
        the same mode again would cost a fresh Gemini reconnect for nothing."""
        set_mode = getattr(self.assistant, "set_wake_word_mode", None)
        if not callable(set_mode):
            return None
        if getattr(self.assistant, "wake_word_mode", None) != active:
            set_mode(active)
        return APPLIED

    async def _apply_voice(self, voice: str) -> str | None:
        """request_voice (C-50): the live session gets it at the next safe point, never in the middle of a reply."""
        request_voice = getattr(self.assistant, "request_voice", None)
        if not callable(request_voice):
            return None
        await _maybe_await(request_voice(voice))
        return APPLIED

    async def _apply_muted(self, muted: bool) -> str | None:
        """set_muted (C-51): the session's microphone input; never saved."""
        set_muted = getattr(self.assistant, "set_muted", None)
        if not callable(set_muted):
            return None
        set_muted(muted)
        return APPLIED

    async def _apply_persona(self, persona: str, voice: str | None) -> tuple[bool, bool] | None:
        """persona_manager.set_persona (C-50): a real change carries the update's voice, so instructions and voice go
        out in one reconnect; the persona that is active already is told again (persona_update), as C-50 does.
        Returns (changed, voice carried), or None when the job has no persona manager."""
        set_persona = getattr(getattr(self.assistant, "persona_manager", None), "set_persona", None)
        if not callable(set_persona):
            return None
        changed = persona != self._live_values().get("persona", self.settings.get("persona"))
        if changed and voice:
            await _maybe_await(set_persona(persona == "anna", voice=voice))
            return True, True
        await _maybe_await(set_persona(persona == "anna"))
        return changed, False

    def _apply_speak_policy(self, value: Any, reason: str) -> None:
        """C-41: the job's VoiceLink audio-out policy, and its decision again at once (a job without one: nothing)."""
        link = self.voice_link
        if link is None or not hasattr(link, "speak_policy"):
            return
        link.speak_policy = speak_policy(value)
        update = getattr(link, "update_audio_out", None)
        if callable(update):
            try:
                update(reason)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug("audio-out update failed: %s", type(exc).__name__)

    async def bootstrap(self, payload: Any) -> dict[str, Any] | None:
        """
        The bridge's settings_bootstrap (K-46, K-61): its stored copy applied as a validated update, never a blind
        overwrite. Under the service lock, a voice, persona or wake word only the live session has (a tool, a spoken
        wake word) is saved first, so nothing below can undo it. The copy is then taken only when it is strictly newer
        than this agent's own last save (updated_ts): the bridge sends it before the socket's CONNECT, so before this
        agent's settings_sync at connect, and an equal stamp is this agent's own last settings_sync coming back; an
        older or unstamped copy is not taken either. Of a newer copy (another brain's) only the fields its `saved`
        names count: its other values are that agent's defaults (its env's included), never saved values here. Those
        are validated (read-only, control and unknown keys dropped) and only the ones that differ from the settings in
        force apply, plus the copy's voice beside a persona that differs (a persona switch resets the voice, so it goes
        into the same switch: _update_locked).
        C-60 (K-46/K-61 v51): a copy with revision, revision_epoch and revisions ({field: {rev, by, src?}}) is judged by
        the bridge's revisions instead of the clocks: a field is taken when its revision is newer than the one this
        agent took of it with that epoch (any revision, with another epoch: another or a recreated bridge database) and
        it is not this agent's own change coming back (src = this file's instance); every field's revision is recorded
        either way (another epoch starts the record again). Fields the copy has no revision for (stored before C-60)
        still follow the clock rule above. So a brain switch between agents whose clocks differ never drops the owner's
        change.
        """
        if not isinstance(payload, Mapping) or not payload:
            logger.debug("settings_bootstrap without settings ignored")
            return None
        async with self._lock:
            await self._commit(self._live_changes(), "live")
            partial = self._bootstrap_fields(payload)
            if not partial:
                if self._bridge_dirty:
                    await self._commit({}, "bootstrap")  # the revisions it recorded
                return None
            clean, errors = validate(partial)
            applied: dict[str, str] = {}
            clean = await self._update_locked(clean, applied, errors, "bootstrap", changes_only=True)
        return await self._report(clean, applied, errors, "bootstrap", changes_only=True)

    def _bootstrap_fields(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The fields of a settings_bootstrap copy this agent may take: by the bridge's revisions when the copy carries
        them (_revision_fields), else by the clock rule (_clock_fields)."""
        revision, epoch = self._split_revision(payload)[1:]
        revisions = payload.get("revisions")
        if revision is None or not isinstance(revisions, Mapping):
            return self._clock_fields(payload)
        return self._revision_fields(payload, epoch, revisions)

    def _revision_fields(self, payload: Mapping[str, Any], epoch: str, revisions: Mapping[str, Any]) -> dict[str, Any]:
        """C-60: the fields newer than this agent took them and not its own, plus the clock rule's fields among those
        the copy has no revision for; every revision in the copy is recorded. A field the owner set (by 'ui') counts
        whatever the copy's saved list says; another agent's value only when that list names it (C-54 review: its other
        values are that agent's defaults, its env's included, never saved values here). With another epoch than this
        agent's record (another or a recreated bridge database) every such field is followed and the record starts
        again with the copy's epoch and revisions; with the same epoch the record never goes down (a copy older than
        it, from a bridge.db that went back, takes only its newer fields)."""
        another_epoch = bool(self.settings.bridge_epoch) and epoch != self.settings.bridge_epoch
        taken: dict[str, Any] = {}
        noted: dict[str, int] = {}
        own: list[str] = []
        names = payload.get("saved")
        saved = {_canonical(name) for name in names if isinstance(name, str)} if isinstance(names, (list, tuple)) \
            else set()
        for raw_key, entry in revisions.items():
            key = _canonical(raw_key)
            rev = _revision(entry.get("rev")) if isinstance(entry, Mapping) else None
            if key is None or rev is None:
                continue
            noted[key] = rev
            if key in CONTROL_FIELDS or key in READ_ONLY_FIELDS or raw_key not in payload:
                continue
            if entry.get("src") == self.settings.instance:
                own.append(key)  # this agent's own change coming back: never taken again
                continue
            if entry.get("by") != "ui" and key not in saved:
                continue  # another agent's default
            if rev > self._bridge_rev(key, epoch):
                taken[key] = payload[raw_key]
        clock = {key: value for key, value in self._clock_fields(payload, quiet=True).items()
                 if _canonical(key) not in noted}
        self._note_bridge(epoch, noted)
        logger.info("SETTINGS bootstrap revision %s%s: %d field(s) newer here, %d of this agent's own%s",
                    payload.get("revision"), " of another bridge database (the record starts again)" if another_epoch
                    else "", len(taken), len(own), f", {len(clock)} by the clock rule" if clock else "")
        return {**clock, **taken}

    def _clock_fields(self, payload: Mapping[str, Any], *, quiet: bool = False) -> dict[str, Any]:
        """The clock rule (C-54): {} unless the copy is strictly newer than this agent's own last save, then the
        fields the copy's `saved` names (no `saved` list: none), without control and read-only keys."""
        theirs, mine = _number(payload.get("updated_ts")), self.settings.updated_ts
        log = logger.debug if quiet else logger.info
        if theirs is None or theirs <= mine:
            if theirs == mine:
                logger.debug("settings_bootstrap not taken: the copy is this agent's own last settings_sync")
            else:
                log("SETTINGS bootstrap not taken: the bridge's copy is %s", "unstamped" if theirs is None
                    else "older than this agent's saved settings")
            return {}
        names = payload.get("saved")
        if not isinstance(names, (list, tuple)):
            log("SETTINGS bootstrap not taken: the bridge's copy does not say which fields were saved")
            return {}
        saved = {_canonical(name) for name in names if isinstance(name, str)}
        return {key: value for key, value in payload.items()
                if _canonical(key) in saved and _canonical(key) not in CONTROL_FIELDS
                and _canonical(key) not in READ_ONLY_FIELDS}

    async def sync(self, reason: str = "state", applied: Mapping[str, str] | None = None) -> dict[str, Any]:
        """settings_sync with the state in force now (bridge connect, request_state, mute/unmute); a voice, persona or
        wake word a tool changed is saved first."""
        async with self._lock:
            await self._commit(self._live_changes(), "live")
        logger.debug("SETTINGS sync (%s)", reason)
        return await self._send_snapshot(applied)

    def note_live_change(self, reason: str) -> None:
        """
        The assistant's on_live_settings_change hook: a tool (toggle_persona, change_voice, set_wake_word_mode) or a
        spoken wake word ('Anna ...', 'Jarvis ...') changed the live session only. The change is saved and sent as
        settings_sync in a task of its own (the tool and the turn never wait for it), so the bridge's copy never falls
        behind the live state. A probe job saves nothing; persist_live_state still runs at the job's end.
        """
        if self.probe:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop (a caller outside the job): the next sync or the job's end saves it
            return
        task = loop.create_task(self._save_live_change(reason))
        self._live_tasks.add(task)
        task.add_done_callback(self._live_tasks.discard)

    async def _save_live_change(self, reason: str) -> dict[str, Any] | None:
        """note_live_change's task: the live state saved (by 'live') and, when that changed anything, settings_sync
        with those fields 'applied'. Never raises."""
        try:
            async with self._lock:
                changes = self._live_changes()
                written = await self._commit(changes, "live")
            if not changes:
                return None
            logger.info("SETTINGS live change %s (%s): %s", "saved" if written else "kept", reason,
                        ",".join(sorted(changes)))
            return await self._send_snapshot({name: APPLIED for name in changes})
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("SETTINGS live change not saved: %s", type(exc).__name__)
            return None

    async def apply_at_start(self) -> None:
        """
        C-54 step 6, before session.start (agent_runner builds the RealtimeModel with settings.voice): the saved wake
        word and persona (instructions and the persona's saved voice are stored for the session), and the saved
        audio-out policy for VoiceLink.prepare. The instructions are composed once, with both: a saved wake word that
        is off is set without a refresh of its own (wake_word_sync still tells the bridge, which kept the last job's),
        then ANNA's switch, or for JARVIS one refresh, composes them. So the dashboard gets one persona_change, the
        persona the job runs as (C-54 review: 'jarvis' and then 'anna' raced, and the bridge could keep JARVIS).
        Never raises: a failure leaves the job's defaults.
        """
        settings, assistant = self.settings, self.assistant
        try:
            compose = False  # the wake word changed and the instructions do not say so yet
            if settings.get("wake_word") is False:
                set_mode = getattr(assistant, "set_wake_word_mode", None)
                if callable(set_mode):
                    await _maybe_await(set_mode(False, refresh=False))
                    compose = True
            if settings.get("persona") == "anna":
                set_persona = getattr(getattr(assistant, "persona_manager", None), "set_persona", None)
                if callable(set_persona):
                    # a job starts as JARVIS, so this is a real switch: composed with the wake word set above
                    await _maybe_await(set_persona(True, voice=settings.voice))
                    compose = False
            refresh = getattr(assistant, "refresh_instructions", None)
            if compose and callable(refresh):
                await _maybe_await(refresh())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("SETTINGS not applied at start (%s); the job keeps its defaults", type(exc).__name__)
        self._apply_speak_policy(settings.get("speak_typed_replies"), "start")
        logger.info("SETTINGS start persona=%s voice=%s wake_word=%s speak_typed=%s%s", settings.get("persona"),
                    settings.voice, settings.get("wake_word"), settings.get("speak_typed_replies"),
                    " (probe: never saved)" if self.probe else "")

    async def persist_live_state(self) -> bool:
        """At the job's end: a voice, persona or wake word a tool changed is saved, so the next job starts with it, and
        so is every change a failed save left pending. Never raises; True when the file was written."""
        if self.probe:
            return False
        try:
            async with self._lock:
                return await self._commit(self._live_changes(), "live", retry=True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("SETTINGS live state not saved: %s", type(exc).__name__)
            return False


def settings_service_of(ctx: Any) -> SettingsService | None:
    """The job's SettingsService from a ui_command's CommandContext (ctx.runtime.settings), else None."""
    service = getattr(getattr(ctx, "runtime", None), "settings", None)
    return service if isinstance(service, SettingsService) else None


# ------------------------------------------------------------------ ui_command handlers (K-03, K-60)
@register_ui_command("settings_update")
async def _settings_update_command(payload: Any, ctx: Any) -> None:
    service = settings_service_of(ctx)
    if service is None:
        logger.debug("settings_update ignored: this job has no settings service")
        return
    await service.update(payload, source="ui")


@register_ui_command("settings_bootstrap")
async def _settings_bootstrap_command(payload: Any, ctx: Any) -> None:
    service = settings_service_of(ctx)
    if service is None:
        logger.debug("settings_bootstrap ignored: this job has no settings service")
        return
    await service.bootstrap(payload)


@register_ui_command("request_state")
async def _request_state_command(_payload: Any, ctx: Any) -> None:
    service = settings_service_of(ctx)
    if service is None:
        logger.debug("request_state ignored: this job has no settings service")
        return
    await service.sync(reason="request_state")
