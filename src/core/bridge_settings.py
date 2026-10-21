"""
# src/core/bridge_settings.py
The bridge's copy of the owner's settings (plan task C-60; contracts K-46 C-SETTINGS-1, K-03 and K-61, v51).

Pure functions: no sockets and no database (src/core/ui_bridge.py calls them, src/core/bridge_store.py runs the merges
inside one transaction), so every rule here is tested without a bridge.

    parse_settings_update(payload)       ui_command settings_update: known keys and types only (K-46 and K-61 fields,
                                         K-24's automation block, K-05's vision_mode), aliases mapped; junk raises
                                         SettingsRejected with a reason and the field
    parse_persona_change(payload)        legacy persona_change ('jarvis'|'anna' in any case, or {persona})
    wake_word_partial(payload, current)  legacy wake_word_toggle (a bool is the mode, no payload toggles)
    ui_merge(current, meta, partial)     a UI change: merged, one new revision, each key stamped by 'ui'
    agent_merge(current, meta, incoming, echo)
                                         the agent's settings_sync: the counter first catches up with an echo above it
                                         (caught_up); a key the UI (or the other brain's agent) changed at a revision
                                         newer than the one this agent says it applied is kept (never overwritten by
                                         its older value); a changed value is stamped by 'agent' with the agent's
                                         instance
    parse_echo(payload)                  what the agent says it applied (K-61 v51), or nothing (an older agent)
    caught_up(meta, echo)                the Lamport catch-up: the bridge's newest revision after this echo
    settings_view(stored, meta, legacy)  init_state.settings and GET /settings: the K-46 defaults, the stored object,
                                         revision and revision_epoch (and the per-key revisions for an agent)
    bootstrap_payload(stored, meta)      settings_bootstrap: the stored object plus revision, revision_epoch and
                                         revisions ({key: {rev, by, src?}}); None while nothing is stored
    relay_agent_sync(payload, kept)      the agent's settings_sync as the UI gets it: the owner's newer values for
                                         the keys the bridge kept, and no revision noise from an older agent

Revisions: the bridge owns one integer counter per database. Meta 'epoch' names the database (bridge_store.
KV_SETTINGS_REV): made once, it never changes, so a bridge restart changes nothing and a restored backup keeps it.
Every UI change and every agent value that changes the copy takes the next revision, so a brain switch never depends on
an agent's clock (K-46 v39 known gap). An agent keeps the revision of every field it took (K-61 v51): a settings_update
or bootstrap of the same epoch is applied only for the fields newer than that, one of another epoch (another or a
recreated database) is followed for every field with a revision, and its own changes coming back (src = its instance)
are never taken again. The counter only grows and is never reset: a restored or copied bridge.db keeps its epoch while
its counter went back below what an agent took, so that agent's next settings_sync echoes revisions above the bridge's
newest, and the bridge raises its counter to the highest of them first, in the same transaction (caught_up). After that
no revision is handed out twice and every later change outranks anything an agent has seen; the agent's newer values
heal the restored copy, while an owner change the restored copy holds and the agent never took is still kept. Known
gap: an owner change made on a restored bridge.db before the first agent sync after the restore can reuse a revision
that agent took, and then loses against that agent's value. Values are never logged here (K-29); key names and counts
only.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# K-46: what a settings view shows while nothing else says otherwise ('voice' only for JARVIS: ANNA's own voice is
# the agent's to pick, C-50).
DEFAULTS: dict[str, Any] = {"voice_id": True, "wake_word": True, "persona": "jarvis", "voice": "Charon"}
DEFAULT_VOICE_PERSONA = "jarvis"
ALIASES = {"emotionalIntelligence": "emotional_intelligence", "wake_word_active": "wake_word"}
PERSONAS = ("jarvis", "anna")
# muted is a control (K-61: applied, never saved); the agent reports the others about itself. Stored as sent, but they
# get no revision of their own and the bridge never keeps them against the agent.
CONTROL_KEYS = frozenset({"muted"})
AGENT_OUTPUT_KEYS = frozenset({"saved", "updated_ts", "voice_id_enrolled"})
UNSTAMPED_KEYS = CONTROL_KEYS | AGENT_OUTPUT_KEYS
# the bridge's revision fields: in a view, a bootstrap and a forwarded settings_update; never stored as settings
REVISION_KEYS = ("revision", "revision_epoch", "revisions")
# the agent's echo (K-61 v51), top level of its settings_sync
ECHO_KEYS = (*REVISION_KEYS, "instance")
BY_UI, BY_AGENT = "ui", "agent"
PENDING_QUEUED = "queued"  # settings_sync.pending[field] while the change waits for an agent
# an echoed revision above this is no revision of this bridge (a UI reads revisions as JavaScript numbers, exact up
# to 2**53 - 1), so it never raises the counter
MAX_REVISION = 2**53 - 1

MAX_KEYS = 32
VOICE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}")
LANGUAGE_RE = re.compile(r"[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8}){0,3}")
# the agent's own pattern (agent_settings._QUIET_HOURS): the groups are checked as times below (C-60 review); the
# match is linear, so it needs no length bound of its own (the agent has none either)
QUIET_HOURS_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*")
INSTANCE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
STYLE_MAX = 40
FOLLOWUP_MAX_S = 60.0
THRESHOLD_MIN, THRESHOLD_MAX = 0.2, 0.9
CHOICES: dict[str, tuple[str, ...]] = {
    "voice_id_reject": ("silent", "polite"),
    "turn_mode": ("gated", "fast"),
    "transcript_script": ("roman", "native"),
    "speak_typed_replies": ("auto", "always", "never"),
    "confirm_level": ("normal", "strict"),
    "vision_mode": ("live", "on_request"),       # K-05
    "persona": PERSONAS,
}
BOOL_KEYS = frozenset({"wake_word", "voice_id", "emotional_intelligence", "muted"})
AUTOMATION_KEYS = ("desktop_control", "clipboard_helper", "clipboard_speak", "voice_confirm_high")  # K-03 / K-24
KNOWN_KEYS = frozenset({"voice", "language", "style", "wake_followup_s", "voice_id_threshold", "quiet_hours",
                        "automation", *CHOICES, *BOOL_KEYS})


class SettingsRejected(ValueError):
    """A settings command the bridge refuses: reason 'bad_payload' | 'unknown_setting' | 'bad_value', the field
    (None for the whole payload) and a short message for settings_sync.errors (never the value itself)."""

    def __init__(self, reason: str, field_name: str | None, message: str) -> None:
        super().__init__(message)
        self.reason, self.field, self.message = reason, field_name, message


def _bad(field_name: str, message: str) -> SettingsRejected:
    return SettingsRejected("bad_value", field_name, message)


def _number(value: Any) -> float | None:
    """A finite number from an int, a float or a short numeric string (the agent takes these too); never a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and len(value) > 16:
        return None
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _tidy(number: float) -> int | float:
    return int(number) if number.is_integer() else number


# Each check returns the value a known key keeps, or None when it is no such value (then the message below is the
# refusal). Types and plain ranges only: the agent's own validation stays the final word (an alias voice, a BCP-47
# language and the like are forwarded as given and the agent maps them). quiet_hours, which the dashboard takes as
# free text, follows the agent's rule exactly (C-60 review).
def _check_voice(value: Any) -> Any:
    text = value.strip() if isinstance(value, str) else ""
    return text if VOICE_RE.fullmatch(text) else None


def _check_language(value: Any) -> Any:
    text = value.strip() if isinstance(value, str) else ""
    return text if text.lower() == "auto" or LANGUAGE_RE.fullmatch(text) else None


def _check_style(value: Any) -> Any:
    text = "".join(ch for ch in value if ch.isprintable()).strip() if isinstance(value, str) else None
    return text if text is not None and len(text) <= STYLE_MAX else None


def _check_followup(value: Any) -> Any:
    number = _number(value)
    return _tidy(number) if number is not None and 0.0 <= number <= FOLLOWUP_MAX_S else None


def _check_threshold(value: Any) -> Any:
    number = _number(value)
    return round(number, 3) if number is not None and THRESHOLD_MIN <= number <= THRESHOLD_MAX else None


def _check_quiet_hours(value: Any) -> Any:
    """The agent's rule (agent_settings._check_quiet_hours, C-60 review): '' for none, else 'HH:MM-HH:MM' with hours
    below 24, minutes below 60 and start and end apart, kept zero-padded as the agent saves it. Digit shapes alone
    ('25:00-07:00', '9:75-10:00', '10:00-10:00') were stored as the owner's value although the agent refuses them."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str):
        return None
    match = QUIET_HOURS_RE.fullmatch(value)
    if match is None:
        return None
    start_h, start_m, end_h, end_m = (int(part) for part in match.groups())
    if max(start_h, end_h) >= 24 or max(start_m, end_m) >= 60 or (start_h, start_m) == (end_h, end_m):
        return None
    return f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}"


def _check_automation(value: Any) -> Any:
    if not isinstance(value, Mapping) or not value:
        return None
    if any(sub not in AUTOMATION_KEYS or not isinstance(flag, bool) for sub, flag in value.items()):
        return None
    return dict(value)


_VALUE_CHECKS: dict[str, tuple[Any, str]] = {
    "voice": (_check_voice, "voice must be a voice name"),
    "language": (_check_language, "language must be auto or a language code such as ur or ur-PK"),
    "style": (_check_style, f"style must be text of at most {STYLE_MAX} characters"),
    "wake_followup_s": (_check_followup, f"wake_followup_s must be a number from 0 to {int(FOLLOWUP_MAX_S)}"),
    "voice_id_threshold": (_check_threshold,
                           f"voice_id_threshold must be a number from {THRESHOLD_MIN} to {THRESHOLD_MAX}"),
    "quiet_hours": (_check_quiet_hours, "quiet_hours must be 'HH:MM-HH:MM' (start and end differ) or empty"),
    "automation": (_check_automation,
                   f"automation must be an object of {', '.join(AUTOMATION_KEYS)}, each true or false"),
}


def _check(key: str, value: Any) -> Any:
    """The value a known key keeps; SettingsRejected('bad_value', key) for anything else."""
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return value
        raise _bad(key, f"{key} must be true or false")
    if key in CHOICES:
        text = value.strip().lower() if isinstance(value, str) else None
        if text in CHOICES[key]:
            return text
        raise _bad(key, f"{key} must be one of: {', '.join(CHOICES[key])}")
    check, message = _VALUE_CHECKS[key]
    clean = check(value)
    if clean is None:
        raise _bad(key, message)
    return clean


def parse_settings_update(payload: Any) -> dict[str, Any]:
    """K-46/K-03 settings_update: {canonical key: checked value}. The aliases emotionalIntelligence and wake_word_active
    are mapped (the canonical key wins when both come); the agent's output keys (saved, updated_ts) are dropped as the
    UI's own reader does; anything else unknown, a wrong type or an empty update raises SettingsRejected."""
    if not isinstance(payload, Mapping):
        raise SettingsRejected("bad_payload", None, "settings_update needs an object of settings")
    if len(payload) > MAX_KEYS:
        raise SettingsRejected("bad_payload", None, "settings_update carries too many keys")
    clean: dict[str, Any] = {}
    canonical_given = {key for key in payload if isinstance(key, str) and key not in ALIASES}
    for raw_key, value in payload.items():
        if not isinstance(raw_key, str):
            raise SettingsRejected("unknown_setting", None, "settings keys must be names")
        key = ALIASES.get(raw_key, raw_key)
        if raw_key in AGENT_OUTPUT_KEYS or raw_key in REVISION_KEYS:
            continue
        if key not in KNOWN_KEYS:
            raise SettingsRejected("unknown_setting", raw_key[:64], f"{raw_key[:64]} is no setting")
        if raw_key in ALIASES and key in canonical_given:
            continue  # the canonical key wins
        clean[key] = _check(key, value)
    if not clean:
        raise SettingsRejected("bad_payload", None, "settings_update changes nothing")
    return clean


def parse_persona_change(payload: Any) -> dict[str, Any]:
    """Legacy persona_change (K-03 v39): 'jarvis' or 'anna' in any case, or an old client's {persona: ...}."""
    value = payload.get("persona") if isinstance(payload, Mapping) else payload
    text = value.strip().lower() if isinstance(value, str) else None
    if text in PERSONAS:
        return {"persona": text}
    raise _bad("persona", "persona must be jarvis or anna")


def wake_word_partial(payload: Any, current: bool) -> dict[str, Any]:
    """Legacy wake_word_toggle (K-03 v39): a bool is the mode, no payload toggles the stored mode."""
    if payload is None:
        return {"wake_word": not current}
    if isinstance(payload, bool):
        return {"wake_word": payload}
    raise _bad("wake_word", "wake_word_toggle takes true, false or nothing")


def current_wake_word(stored: Mapping[str, Any], legacy: Mapping[str, Any]) -> bool:
    """The wake-word mode a toggle starts from: the stored setting, else the agent's last wake_word_sync, else K-46's
    default (on)."""
    for value in (stored.get("wake_word"), legacy.get("wake_word_active")):
        if isinstance(value, bool):
            return value
    return True


def strip_meta(settings: Mapping[str, Any]) -> dict[str, Any]:
    """A settings object without the bridge's revision fields (they are never stored as settings)."""
    return {str(key): value for key, value in settings.items() if key not in REVISION_KEYS}


def _merged(old: Any, new: Any) -> Any:
    """A nested object merges key by key (the automation block); anything else is replaced."""
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        out = dict(old)
        for key, value in new.items():
            out[key] = _merged(out.get(key), value)
        return out
    return new


def ui_merge(current: Mapping[str, Any], meta: Mapping[str, Any],
             partial: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, int]:
    """A UI change (bridge_store.transact_settings fn): every key of partial merged into the copy, one new revision,
    each key except the unstamped ones stamped {rev, by 'ui'}. Returns (writes, new meta, 'ui', the revision). Every
    accepted change takes a new revision, even when a value equals the copy, so the agent never takes it for one it
    applied already."""
    revision = int(meta["revision"]) + 1
    keys = {key: dict(entry) for key, entry in meta["keys"].items()}
    writes: dict[str, Any] = {}
    for key, value in partial.items():
        writes[key] = _merged(current.get(key), value)
        if key not in UNSTAMPED_KEYS:
            keys[key] = {"rev": revision, "by": BY_UI, "src": ""}
    return writes, {**meta, "revision": revision, "keys": keys}, BY_UI, revision


@dataclass(frozen=True)
class Echo:
    """What an agent's settings_sync says it applied (K-61 v51). kind: 'per_key' (revisions per field), 'single' (one
    revision for every field) or 'none' (an agent without the echo); epoch: the database it names (another epoch
    applied nothing of this one); instance: the agent's settings file id, '' when unknown."""
    kind: str = "none"
    epoch: str = ""
    revision: int = 0
    revisions: dict[str, int] = field(default_factory=dict)
    instance: str = ""

    def applied(self, key: str, meta: Mapping[str, Any]) -> int:
        """The newest revision of key this agent applied, as far as the bridge can tell. Without an echo: the newest
        revision the bridge sent to an agent socket (an older agent applies what it gets and answers with its state).
        An echo of another epoch (another or a recreated database) applied nothing of this one (0)."""
        if self.kind == "none":
            return int(meta.get("delivered", 0))
        if self.epoch != meta.get("epoch"):
            return 0
        if self.kind == "per_key":
            return self.revisions.get(key, 0)
        return self.revision

    def newest(self) -> int:
        """The highest revision the echo names (0 for none)."""
        return max(self.revision, *self.revisions.values()) if self.revisions else self.revision


def _revision(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_REVISION:
        return value
    return None


def parse_echo(payload: Mapping[str, Any]) -> Echo:
    """The echo at the top level of a settings_sync: revisions {field: rev} wins over a single revision; an agent that
    sends neither is an older one (Echo kind 'none'). A revision that is no int from 0 to MAX_REVISION is left out."""
    instance = payload.get("instance")
    instance = instance if isinstance(instance, str) and INSTANCE_RE.fullmatch(instance) else ""
    epoch = payload.get("revision_epoch")
    epoch = epoch if isinstance(epoch, str) else ""
    raw = payload.get("revisions")
    if isinstance(raw, Mapping):
        revisions = {str(key): rev for key, value in raw.items()
                     if isinstance(key, str) and (rev := _revision(value)) is not None}
        return Echo("per_key", epoch, max(revisions.values(), default=0), revisions, instance)
    single = _revision(payload.get("revision"))
    if single is not None:
        return Echo("single", epoch, single, {}, instance)
    return Echo("none", "", 0, {}, instance)


def caught_up(meta: Mapping[str, Any], echo: Echo) -> int:
    """The Lamport catch-up (K-46 v51): the bridge's newest revision after this agent's echo. Every revision an agent
    took came from this bridge's counter, which only grows, so an echo of this database (the same epoch) that names a
    revision above the bridge's newest means bridge.db went back (restored from a backup or copied, keeping its epoch).
    The counter is raised to that revision (agent_merge, stored in the same transaction), so none of those revisions is
    ever handed out again and every later change outranks anything an agent has seen. Otherwise the newest revision as
    it is (an echo of another epoch, or none at all, raises nothing)."""
    newest = int(meta["revision"])
    if echo.kind == "none" or not echo.epoch or echo.epoch != meta.get("epoch"):
        return newest
    return max(newest, echo.newest())


def agent_merge(current: Mapping[str, Any], meta: Mapping[str, Any], incoming: Mapping[str, Any],
                echo: Echo) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    """The agent's settings object (bridge_store.transact_settings fn). The counter first catches up with the echo
    (caught_up). The unstamped keys are stored as sent. A key the UI (or another agent: the other brain) set at a
    revision newer than this agent applied (echo) is kept as it was set; this agent's own change (src = its instance)
    never is. Any other value that changes the copy is written and stamped {rev, by 'agent', src: the agent's
    instance}, all under one new revision past the caught-up counter. Returns (writes, new meta, 'agent', kept {key:
    the stored value the bridge kept})."""
    newest = caught_up(meta, echo)
    writes: dict[str, Any] = {}
    kept: dict[str, Any] = {}
    changed: list[str] = []
    for raw_key, value in incoming.items():
        key = str(raw_key)
        if key in ECHO_KEYS:
            continue
        new = _merged(current.get(key), value)
        if key in UNSTAMPED_KEYS:
            writes[key] = new
            continue
        if key in current and new == current.get(key):
            continue  # the copy says so already: nothing to write, the key keeps its stamp
        entry = meta["keys"].get(key)
        # this agent's own change (an older agent has no instance: what such agents wrote counts as its own, as before)
        own = entry is not None and entry["by"] == BY_AGENT and entry["src"] == echo.instance
        if entry is not None and not own and entry["rev"] > echo.applied(key, meta):
            kept[key] = current.get(key)
            continue
        writes[key] = new
        changed.append(key)
    new_meta: dict[str, Any] = {**meta, "revision": newest}
    if changed:
        revision = newest + 1
        keys = {name: dict(entry) for name, entry in meta["keys"].items()}
        for key in changed:
            keys[key] = {"rev": revision, "by": BY_AGENT, "src": echo.instance}
        new_meta.update(revision=revision, keys=keys)
    return writes, new_meta, BY_AGENT, kept


def public_revisions(meta: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """{key: {rev, by, src?}} for an agent (src only for an agent's change: the agent skips its own)."""
    out: dict[str, dict[str, Any]] = {}
    for key, entry in meta["keys"].items():
        item: dict[str, Any] = {"rev": entry["rev"], "by": entry["by"]}
        if entry.get("src"):
            item["src"] = entry["src"]
        out[key] = item
    return out


def settings_view(stored: Mapping[str, Any], meta: Mapping[str, Any], legacy: Mapping[str, Any] | None = None, *,
                  with_revisions: bool = False) -> dict[str, Any]:
    """init_state.settings and GET /settings (K-46 v51): the K-46 defaults, under the agent's last persona_change,
    wake_word_sync and mute_sync (legacy kv: active_persona, wake_word_active, muted), under the stored object; then
    revision and revision_epoch (and, for an agent, revisions). The default voice is shown for JARVIS only."""
    legacy = legacy or {}
    view: dict[str, Any] = {key: value for key, value in DEFAULTS.items() if key != "voice"}
    persona = legacy.get("active_persona")
    if isinstance(persona, str) and persona in PERSONAS:
        view["persona"] = persona
    if isinstance(legacy.get("wake_word_active"), bool):
        view["wake_word"] = legacy["wake_word_active"]
    if isinstance(legacy.get("muted"), bool):
        view["muted"] = legacy["muted"]
    view.update(strip_meta(stored))
    if "voice" not in view and view.get("persona") == DEFAULT_VOICE_PERSONA:
        view["voice"] = DEFAULTS["voice"]
    view["revision"] = int(meta["revision"])
    view["revision_epoch"] = str(meta["epoch"])
    if with_revisions:
        view["revisions"] = public_revisions(meta)
    return view


def bootstrap_payload(stored: Mapping[str, Any], meta: Mapping[str, Any]) -> dict[str, Any] | None:
    """settings_bootstrap (K-46 v51): the stored object as the agents and the UI left it (no defaults: a default is
    never a saved value), plus revision, revision_epoch and revisions; None while nothing is stored."""
    settings = strip_meta(stored)
    if not settings:
        return None
    return {**settings, "revision": int(meta["revision"]), "revision_epoch": str(meta["epoch"]),
            "revisions": public_revisions(meta)}


def forwarded_update(partial: Mapping[str, Any], revision: int, epoch: str) -> dict[str, Any]:
    """The settings_update an agent gets: the checked fields with the bridge's revision (K-61 v51)."""
    return {"type": "settings_update", "payload": {**partial, "revision": revision, "revision_epoch": epoch}}


def bridge_sync(view: Mapping[str, Any], *, pending: Any = (), errors: Mapping[str, str] | None = None,
                ts: float) -> dict[str, Any]:
    """A settings_sync the bridge answers with itself (no agent can): the view, source 'bridge', pending {field:
    'queued'} for the fields that wait for an agent and errors for a refused command. No 'applied': the UI keeps the
    agent's last word on what applied."""
    payload: dict[str, Any] = {"settings": dict(view), "errors": dict(errors or {}), "source": "bridge",
                               "ts": round(ts, 3)}
    pending = sorted(pending)
    if pending:
        payload["pending"] = {name: PENDING_QUEUED for name in pending}
    return payload


def _without_revision_noise(report: Any) -> Any:
    """An older agent reports the forwarded revision fields as unknown settings ('unsupported'): never shown."""
    if isinstance(report, Mapping) and any(key in report for key in REVISION_KEYS):
        return {key: value for key, value in report.items() if key not in REVISION_KEYS}
    return report


def relay_agent_sync(payload: Any, kept: Mapping[str, Any]) -> Any:
    """The agent's settings_sync as the UI gets it: as sent, except that a key the bridge kept shows the owner's newer
    value, and applied/errors carry no revision field of an older agent. The same object when nothing changes."""
    if not isinstance(payload, Mapping):
        return payload
    out = dict(payload)
    changed = False
    for name in ("applied", "errors"):
        cleaned = _without_revision_noise(out.get(name))
        if cleaned is not out.get(name):
            out[name], changed = cleaned, True
    if kept:
        settings = out.get("settings")
        if isinstance(settings, Mapping):
            out["settings"] = {**settings, **kept}
        else:
            out.update(kept)
        changed = True
    return out if changed else payload
