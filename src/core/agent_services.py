"""
# src/core/agent_services.py
jarvis-agent-svc, the always-on service of the VPS brain (plan task C-125; contracts K-77, K-74, K-70, K-72/K-75).

    python -m src.core.agent_services          (JARVIS_ROLE=svc; the compose service agent-svc, K-75)

A LiveKit room closes when nobody is in it, so jobs are conversation-only in vps mode (C-112) and this process owns
everything that must run around the clock. It calls net_guard.harden_process() first (PR_SET_DUMPABLE=0, the child
env allow-list and, with JARVIS_NET_GUARD=1, the egress guard: python is PID 1 with init false) and handles SIGTERM
and SIGINT itself: the loops are cancelled, a running snapshot is aborted (its partial file removed), the Desktop
Link is retired and the process exits 0, well inside the compose stop grace (60 s).

Active only with JARVIS_BRAIN=vps. With any other brain (pc, today's live mode) it is in standby and does nothing but
answer GET http://127.0.0.1:8083/health, so the PC brain keeps working exactly as before. /health answers
{status, role, brain, state, pid, uptime_s, git_sha, due {reminders, tasks}, due_pending, undelivered,
last_snapshot_ts, snapshot {enabled, next_ts}, loops {name: {alive, last_tick_age_s}}, link {started, online}, ts}:
200 while healthy, 503 when a loop died or stalled or the configuration is unusable. It never carries a secret or a
reminder, task or location text.

Active duties (K-77):
- Due checks: reminders every 30 s through Cursor's X-66 functions (list_due_reminders, read under the file lock;
  mark_reminder_delivered, called only after a delivery, and only while load_reminders shows the delivered one as
  the first pending reminder with its id in file order, the row that mark takes), tasks.db every 45 s through
  Cursor's TaskManager
  (get_due_tasks marks rows triggered, busy_timeout 5 s; a row is marked completed only after its delivery, the C-56
  rule; rows already triggered are picked up again after a restart).
- Delivery ladder, recorded in conversations/delivery_ledger.json under services.utils.jarvis_filelock.locked:
  1. the live job: svc_deliver step 'announce' -> the bridge's agent_command 'announce' -> the job's announce_result
     (delivered only while a participant is linked; ack = delivered true);
  2. the laptop: pc.speak_local (it speaks and shows a local notice) through this process's DesktopLink (peer 'svc',
     origin 'internal'; ack = the node's success result, also a late one);
  3. the bridge: a stored UI notification plus the 'reminder_due' event (ack = ui_command notification_ack);
  4. optionally, JARVIS_REMINDER_EMAIL_AFTER_MIN minutes after the due time (0 = off, the default), one e-mail to the
     owner's own address (EMAIL_USER) for any item still open then (also while the bridge is down). The e-mail is no
     ack: the item stays open for steps 2 and 3. It is in the ledger before it is sent, so it never goes twice: only
     a send that surely did not reach the server (it failed before DATA, or the server refused the message) is tried
     again, at most EMAIL_ATTEMPTS_MAX (3) times; one the server took (a failed QUIT after it is only logged) or whose
     connection broke during DATA (outcome unknown) is never sent again.
  An item counts as delivered only on an ack. An open item is retried on the next UI connect (step 3 again: the
  bridge emits reminder_due once more and an open dashboard acknowledges it; a bridge restart, told by the boot id
  of its svc_poll answer, counts as a connect of every UI connected after it) and on the next laptop connect (step
  2; an item stored at the bridge is asked there first, and one a dashboard acknowledged meanwhile is delivered on
  the UI and not spoken; one sent again to a connected UI within ui_ack_grace_s (5 s, on the monotonic clock; a
  ledger stamp from before a restart that lies ahead of the wall clock counts as elapsed) is held until that
  dashboard's ack could land, then asked again). An item held at either retry is tried again after each wait while
  it stays held, at most held_rounds times in a row (enough to outlast restart_hold_s; a new hold starts the count
  again, and after it the next connect tries it). A bridge answer 'unsure' (bridge.db could not tell an asked
  result or ack, or not record an 'svc' ack) counts as no answer: nothing is settled, acknowledged or spoken on it.
  The ledger is the truth, also across restarts: an item it marks delivered is never delivered again, only its source
  is marked (reminders.json 'triggered' or the task completed; again on each tick while it is still due there, and
  its entry is never dropped meanwhile); an item delivered on another channel after step 3 is acknowledged at the
  bridge by 'svc' at once, so no dashboard shows it again. The ack's attempt row and delivered_at are one ledger
  write. Each announce and pc.speak_local is in the ledger before it is sent (announce_call, laptop_call), and so is
  each first step 3 (notified with notify_unknown: the bridge may store it from then on). An announce stays open
  until the job's result is read or its window ends, also when its outcome is not known (a lost answer: the bridge
  may have emitted it) or a restart cut its wait; it is settled by its stored result (at the item's next ladder run
  or laptop connect; the bridge keeps results 1 h, also across its restarts), as a timeout only after a poll the
  bridge answered without one, never sent again, and the item is not spoken or stored at the bridge meanwhile. A
  step 3 whose answer was lost (or cut by a stop) counts as stored: no announce or speak follows without asking the
  bridge first, and it is sent again (idempotent at the bridge) until the bridge answers; only an answer proving it
  was not stored takes that back. A speak whose outcome is not known (unknown, or cut by a restart: its result then
  comes as a 'restart' late outcome, matched by call id) holds the item on every channel (no announce, no step 3,
  also not on a UI connect, and no speak) before its late outcome or until late_wait_s after the laptop's last
  connect; a restarted svc holds it until its first node hello plus late_wait_s, at most restart_hold_s (11 min)
  after the call. After that hold the next ladder run goes on to step 3, and a UI connect held meanwhile gets its
  step 3. A reminder entry is closed as gone only once its reminder is no longer pending in reminders.json
  (cancelled, or taken by the PC brain), never because it is not due right now. A stored time that lies ahead of the
  wall clock (it stepped back) never stretches a retry throttle or the UI grace.
- Scheduled desktop actions (the K-32 hook for C-80): SchedulerHost.run_desktop_action sends a laptop tool through
  this process's DesktopLink with origin 'scheduler'; while the laptop is offline it waits up to 15 min, then the
  failure is announced through the ladder. Until C-80 sets action_runner, a due task with an action is announced and
  completed after delivery without running the action (as the PC brain's task monitor does today).
- Node events (K-70, DesktopLink.subscribe('event')): location -> conversations/location.json through
  jarvis_location.save_device_location (file-locked) and notify location_sync {kind 'device', source 'device'} (K-08);
  log_batch -> items redacted (K-29) and posted as vortex_log_batch with source 'desktop'. Vitals, desktop_state,
  power and alerts are notices the bridge reads itself; nothing here forwards them.
- Daily snapshot at 03:30 PKT (and once, 10 min after a start that finds none from the last 26 h): SQLite online
  copies plus every other file of conversations, data and chroma_db, as one tar sealed to
  BACKUP_RECIPIENT_PUBLIC_KEY (services.utils.backup_crypto: X25519 + HKDF-SHA256 + ChaCha20-Poly1305), written to
  /app/snapshots/jarvis_snapshot_<PKT date>.tar.enc (JARVIS_SNAPSHOTS_DIR; the jarvis_agent_snapshots volume, whose
  newest file the bridge serves to the laptop, K-73). Env files, keys, locks and temp files are never inside; nothing
  is ever deleted. Without a usable recipient key there is no snapshot and one warning.
- Heartbeat to the bridge every 30 s: notify svc_heartbeat with the K-68/K-77 counts (GET /status 'svc').
Logs carry ids, kinds, steps and counts, never reminder, task, message or location text, or secrets (K-29).
"""
# pylint: disable=too-many-lines  # one service: ledger, ladder, ports, snapshot, health and the process entry
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import smtplib
import sqlite3
import ssl
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from services.utils import net_guard
from services.utils.jarvis_config import BRAIN_VPS, ROLE_SVC, RuntimeProfile, config, runtime_profile
from services.utils.jarvis_filelock import locked
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SVC")

HEALTH_HOST = "127.0.0.1"
HEALTH_PORT = 8083
GIT_SHA = (os.getenv("GIT_SHA") or "").strip() or "unknown"
LEDGER_FILE = "delivery_ledger.json"
LEDGER_VERSION = 1
LEDGER_KEEP_S = 30 * 86400.0  # finished entries whose source is marked are dropped after 30 days
ATTEMPTS_KEPT = 40
LAPTOP_ATTEMPTS_MAX = 10
EMAIL_ATTEMPTS_MAX = 3
EMAIL_SENT, EMAIL_FAILED, EMAIL_UNKNOWN = "sent", "failed", "unknown"  # OwnerEmail.send outcomes
BRIDGE_UNSURE = "unsure"  # a bridge answer status: bridge.db could not tell what was asked (C-125, ui_bridge)
TEXT_MAX = 300
IDS_PER_POLL = 100
ENV_RECIPIENT = "BACKUP_RECIPIENT_PUBLIC_KEY"
ENV_EMAIL_AFTER = "JARVIS_REMINDER_EMAIL_AFTER_MIN"
ENV_SNAPSHOTS_DIR = "JARVIS_SNAPSHOTS_DIR"  # the bridge reads the same volume through the same name (K-73)
DEFAULT_TZ = "Asia/Karachi"
SNAPSHOT_AT = (3, 30)  # 03:30 PKT
SNAPSHOT_PREFIX, SNAPSHOT_SUFFIX = "jarvis_snapshot_", ".tar.enc"
SNAPSHOT_ROOTS = ("conversations", "data", "chroma_db")
SNAPSHOT_CATCHUP_AGE_S = 26 * 3600.0
STATE_ACTIVE, STATE_STANDBY, STATE_CONFIG_ERROR = "active", "standby", "config_error"
CH_ANNOUNCE, CH_LAPTOP, CH_UI = "announce", "laptop", "ui"
KIND_REMINDER, KIND_TASK = "reminder", "task"
SQLITE_MAGIC = b"SQLite format 3\x00"
_ID_CLEAN = re.compile(r"[^A-Za-z0-9._-]+")
_SECRET_NAME = re.compile(r"secret|password|passwd|credential|private|token", re.IGNORECASE)
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_USER_PATH = re.compile(r"(?i)\b([a-z]):[\\/]+users[\\/]+[^\\/\s]+")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_REASONS = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}


# ---------------------------------------------------------------- small helpers


def clean_text(value: Any, limit: int = TEXT_MAX) -> str:
    """Printable text on one line (control and format characters dropped, whitespace collapsed), cut at limit."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:limit]


def _short_hash(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8", "replace")).hexdigest()[:8]


def _safe_id(value: Any) -> str:
    return _ID_CLEAN.sub("-", str(value or "")).strip("-")[:48] or "x"


def local_zone() -> ZoneInfo:
    """TZ (the compose file sets Asia/Karachi), else Asia/Karachi: the wall clock reminders and tasks are kept in."""
    for name in ((os.getenv("TZ") or "").strip(), DEFAULT_TZ):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except Exception:  # pylint: disable=broad-exception-caught  # unknown name or no tzdata entry
            continue
    return ZoneInfo("UTC")


def parse_local(value: Any) -> float | None:
    """Epoch seconds of a stored naive local ISO time (reminders.json 'time', tasks.db scheduled_time)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local_zone())
    return moment.timestamp()


def next_local_time(now: float, hour: int, minute: int) -> float:
    """The next wall-clock hour:minute in local_zone() strictly after now (epoch seconds)."""
    zone = local_zone()
    current = datetime.fromtimestamp(now, zone)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now:
        target = (current + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target.timestamp()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# ---------------------------------------------------------------- due items


@dataclass(frozen=True)
class DueItem:
    """One thing to deliver. id is the ledger key (and the bridge notification id); ref the source's own id (a
    reminder id, a task row id; None for an item the svc made itself, synthetic)."""

    id: str
    kind: str
    ref: Any
    text: str
    due_ts: float | None = None
    due_at: str = ""
    synthetic: bool = False
    action: str | None = None


def reminder_item(reminder: Mapping[str, Any]) -> DueItem | None:
    """A due reminder of list_due_reminders() as a DueItem; its key also covers created_at and time, because two
    reminders set for the same second share an id (rem_<epoch>)."""
    rid = reminder.get("id")
    text = clean_text(reminder.get("message")) or "Reminder"
    if not isinstance(rid, str) or not rid:
        return None
    key = f"r:{_safe_id(rid)}:{_short_hash(rid, reminder.get('created_at'), reminder.get('time'))}"
    due_at = reminder.get("time") if isinstance(reminder.get("time"), str) else ""
    return DueItem(id=key, kind=KIND_REMINDER, ref=rid, text=text, due_ts=parse_local(due_at), due_at=due_at)


def task_item(row: Mapping[str, Any]) -> DueItem | None:
    task_id = row.get("id")
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        return None
    text = clean_text(row.get("description")) or f"task {task_id}"
    key = f"t:{task_id}:{_short_hash(task_id, row.get('created_at'))}"
    due_at = row.get("scheduled_time") if isinstance(row.get("scheduled_time"), str) else ""
    action = row.get("action") if isinstance(row.get("action"), str) and row.get("action") else None
    return DueItem(id=key, kind=KIND_TASK, ref=task_id, text=text, due_ts=parse_local(due_at), due_at=due_at,
                   action=action)


def spoken_text(item: DueItem) -> str:
    return f"Sir, reminder: {item.text}" if item.kind == KIND_REMINDER else f"Sir, scheduled task: {item.text}"


# ---------------------------------------------------------------- the ledger


class DeliveryLedger:
    """conversations/delivery_ledger.json: {v, items: {id: {id, kind, ref, due_at, due_ts, first_seen, attempts[],
    notified?, notify_unknown?, ui_emit_ts?, ui_emit_clients?, delivered_at?, channel?, closed_at?, source_marked?,
    bridge_ack?, emailed_at?, synthetic?, text?, announce_seq?, announce_call? {id, ts}, laptop_call? {id, ts}}}}.
    announce_call and laptop_call name a call whose outcome is not known yet (written before it is sent). notified
    means the bridge may hold the UI notification: written with notify_unknown before the first step 3 is sent, and
    notify_unknown is dropped once the bridge answered it stored. ui_emit_ts and ui_emit_clients tell when the bridge
    last emitted reminder_due and to how many UI sockets.
    Every read and every read-modify-write holds jarvis_filelock.locked(path); a write goes through a temp file and
    os.replace and happens only when something changed. text is kept only for synthetic items (fixed svc lines); a
    reminder's or task's own text stays in its source. A file that is not JSON is renamed aside (never deleted) and the
    ledger starts empty; an OSError is raised to the caller (the tick tries again)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> dict[str, dict[str, Any]]:
        with locked(self.path):
            return self._read_unlocked()

    def update(self, change: Callable[[dict[str, dict[str, Any]]], Any]) -> Any:
        with locked(self.path):
            items = self._read_unlocked()
            before = json.dumps(items, sort_keys=True, default=str)
            result = change(items)
            if json.dumps(items, sort_keys=True, default=str) != before:
                self._write_unlocked(items)
            return result

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            doc = json.loads(raw)
        except ValueError:
            self._set_aside("not JSON")
            return {}
        items = doc.get("items") if isinstance(doc, dict) else None
        if not isinstance(items, dict):
            self._set_aside("no items")
            return {}
        return {key: value for key, value in items.items() if isinstance(key, str) and isinstance(value, dict)}

    def _set_aside(self, why: str) -> None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = self.path.with_name(f"{self.path.name}.unreadable-{stamp}")
        n = 2
        while target.exists():
            target = self.path.with_name(f"{self.path.name}.unreadable-{stamp}-{n}")
            n += 1
        try:
            os.replace(self.path, target)
        except OSError:
            return
        logger.warning("DELIVERY ledger unreadable (%s): kept as %s, starting empty", why, target.name)

    def _write_unlocked(self, items: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        try:
            tmp.write_text(json.dumps({"v": LEDGER_VERSION, "items": items}, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), default=str), encoding="utf-8")
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _is_open(entry: Mapping[str, Any]) -> bool:
    return not entry.get("delivered_at") and not entry.get("closed_at")


def _attempts(entry: Mapping[str, Any], step: str | None = None) -> list[dict[str, Any]]:
    attempts = entry.get("attempts")
    rows = [row for row in attempts if isinstance(row, dict)] if isinstance(attempts, list) else []
    return [row for row in rows if step is None or row.get("step") == step]


def _add_attempt(entry: dict[str, Any], step: str, outcome: str, now: float, ref: str | None = None) -> None:
    attempts = entry.get("attempts") if isinstance(entry.get("attempts"), list) else []
    row: dict[str, Any] = {"step": step, "outcome": clean_text(outcome, 40) or "-", "ts": round(now, 3)}
    if ref:
        row["ref"] = ref
    attempts.append(row)
    entry["attempts"] = attempts[-ATTEMPTS_KEPT:]


def _new_entry(item: DueItem, now: float) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": item.id, "kind": item.kind, "ref": item.ref, "due_at": item.due_at,
                             "due_ts": item.due_ts, "first_seen": round(now, 3), "attempts": []}
    if item.synthetic:
        entry.update(synthetic=True, text=item.text)
    return entry


def _pending_call(entry: Mapping[str, Any], name: str) -> tuple[str, float] | None:
    """(id, ts) of the call in entry[name] whose outcome is not known yet: 'announce_call' (an announce whose result
    was not read yet: its wait runs, or a restart cut it) or 'laptop_call' (a pc.speak_local that may have spoken)."""
    pending = entry.get(name)
    if not isinstance(pending, Mapping):
        return None
    call_id = pending.get("id")
    if not isinstance(call_id, str) or not call_id:
        return None
    return call_id, _number(pending.get("ts")) or 0.0


def _release(entry: dict[str, Any], name: str, call_id: str | None) -> None:
    """Drops entry[name] once the outcome of that very call is known (a newer call's record is kept)."""
    pending = _pending_call(entry, name)
    if pending is not None and call_id is not None and pending[0] == call_id:
        entry.pop(name, None)


def _new_call_id() -> str:
    """A K-70 call_id ([A-Za-z0-9_-]{8,64}) made here, so the ledger has it before the call is submitted."""
    return "c" + secrets.token_hex(16)


def _item_of_call(items: Mapping[str, Mapping[str, Any]], call_id: str | None) -> str | None:
    """The ledger key whose pc.speak_local had call_id (its laptop_call, or a laptop attempt row with that ref)."""
    if not call_id:
        return None
    for key, entry in items.items():
        pending = _pending_call(entry, "laptop_call")
        if (pending is not None and pending[0] == call_id) or \
                any(row.get("ref") == call_id for row in _attempts(entry, CH_LAPTOP)):
            return key
    return None


def _clear_bridge_acks(keys: list[str]) -> Callable[[dict[str, dict[str, Any]]], None]:
    """The ledger change after the bridge took the 'svc' acks of keys."""
    def clear(items: dict[str, dict[str, Any]]) -> None:
        for key in keys:
            if key in items:
                items[key].pop("bridge_ack", None)
    return clear


# ---------------------------------------------------------------- ports (production adapters below; tests use fakes)


@dataclass(frozen=True)
class PollAnswer:
    """The bridge's answer to svc_poll / svc_heartbeat (see src/core/ui_bridge.py, C-125). boot changes with every
    bridge start ('' from a bridge that does not send it). Only a status 'ok' answer is one: 'unsure' (bridge.db
    could not tell an asked result or ack, or not record an 'svc' ack) reads as None, like a poll that got no
    answer, so nothing is settled, acknowledged or spoken on it."""

    ui_clients: int = 0
    ui_connects: int = 0
    agents: int = 0
    acks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    boot: str = ""

    @classmethod
    def from_json(cls, data: Any) -> PollAnswer | None:
        if not isinstance(data, Mapping) or data.get("status") != "ok":
            return None

        def count(name: str) -> int:
            value = data.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
        acks = data.get("acks")
        clean = {key: dict(value) for key, value in acks.items()
                 if isinstance(key, str) and isinstance(value, Mapping)} if isinstance(acks, Mapping) else {}
        boot = data.get("boot")
        return cls(ui_clients=count("ui_clients"), ui_connects=count("ui_connects"), agents=count("agents"),
                   acks=clean, boot=clean_text(boot, 64) if isinstance(boot, str) else "")


NOTIFY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class NotifyResult:
    """A step 3 answer: stored 'new' or 'exists' (the bridge holds it; acked: a dashboard or 'svc' acknowledged it
    before, so it was not emitted again; ui_clients: the UI sockets it was emitted to), or NOTIFY_UNKNOWN when the
    answer was lost (the bridge may have stored and emitted it)."""

    stored: str
    acked: bool = False
    ui_clients: int = 0


class BridgePort(Protocol):
    async def poll(self, ids: list[str], ack_ids: list[str]) -> PollAnswer | None: ...
    async def heartbeat(self, payload: dict[str, Any]) -> PollAnswer | None: ...
    # announce: 'sent' | 'no_agent' | 'refused' (surely not emitted) | 'unknown' (the bridge may have emitted it)
    async def announce(self, attempt_id: str, text: str, kind: str) -> str: ...
    # notify: a NotifyResult ('new' | 'exists' | NOTIFY_UNKNOWN), or None when the bridge surely did not store it
    async def notify(self, payload: dict[str, Any]) -> NotifyResult | None: ...
    async def location_sync(self, payload: dict[str, Any]) -> None: ...
    async def log_batch(self, items: list[dict[str, Any]], dropped: int) -> None: ...


class LaptopPort(Protocol):
    def online(self) -> bool: ...
    async def speak(self, text: str, item_id: str, call_id: str | None = None) -> str: ...
    async def run_action(self, *, tool: str, args: Mapping[str, Any], param_schema_sha256: str, timeout_s: float,
                         approval: Mapping[str, Any] | None = None,
                         label: Mapping[str, Any] | None = None) -> dict[str, Any]: ...


class NotifyBridge:
    """BridgePort over POST /notify with the agent's own auth (jarvis_bridge.notify_with_answer)."""

    TIMEOUT_S = 5.0
    WARN_EVERY_S = 60.0

    def __init__(self) -> None:
        self._warned_at = float("-inf")

    async def _send(self, event_type: str, payload: dict[str, Any]) -> tuple[Any, bool]:
        """(answer, never_handled): the bridge's parsed answer, or None after a failure, with never_handled True
        when that failure proves no bridge handler ran (jarvis_bridge.notify_never_handled: never sent, or refused
        before dispatch, as a 429)."""
        from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
        try:
            return await jarvis_bridge.notify_with_answer(event_type, payload, self.TIMEOUT_S), False
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the bridge is down or refused
            now = time.monotonic()
            if now - self._warned_at >= self.WARN_EVERY_S:
                self._warned_at = now
                logger.warning("SVC bridge %s failed (%s); retrying on the next tick", event_type,
                               type(exc).__name__)
            return None, jarvis_bridge.notify_never_handled(exc)

    async def _ask(self, event_type: str, payload: dict[str, Any]) -> Any:
        return (await self._send(event_type, payload))[0]

    async def poll(self, ids: list[str], ack_ids: list[str]) -> PollAnswer | None:
        return PollAnswer.from_json(await self._ask("svc_poll", {"ids": ids, "ack_ids": ack_ids}))

    async def heartbeat(self, payload: dict[str, Any]) -> PollAnswer | None:
        return PollAnswer.from_json(await self._ask("svc_heartbeat", payload))

    async def announce(self, attempt_id: str, text: str, kind: str) -> str:
        """'sent'; 'no_agent' or 'refused' when it surely did not go out (no owner-room agent; any other answer of
        the bridge, which emits only with 'sent'; or a failure that proves no handler ran); 'unknown' when the
        bridge may have emitted it (a read timeout, a lost answer, a 5xx after dispatch): the ladder then keeps the
        attempt open until the job's result or the end of its window."""
        answer, never_handled = await self._send(
            "svc_deliver", {"step": "announce", "id": attempt_id, "text": text, "kind": kind})
        if isinstance(answer, Mapping):
            status = answer.get("status")
            return status if status in ("sent", "no_agent") else "refused"
        return "refused" if answer is None and never_handled else "unknown"

    async def notify(self, payload: dict[str, Any]) -> NotifyResult | None:
        """Step 3. The bridge's 'ok' answer as a NotifyResult; None when the bridge surely did not store it (any
        other answer: it stores and emits only with 'ok'; or a failure that proves no handler ran); NOTIFY_UNKNOWN
        when it may have stored and emitted it (a read timeout, a lost answer, a 5xx after dispatch), and for
        'unsure' (stored before, but bridge.db could not tell whether it was acknowledged, so it was not emitted
        again): the ladder then treats it as stored and sends it again until the bridge answers (_step_notify)."""
        answer, never_handled = await self._send("svc_deliver", {"step": "notify", **payload})
        if isinstance(answer, Mapping):
            if answer.get("status") == BRIDGE_UNSURE:
                return NotifyResult(stored=NOTIFY_UNKNOWN)
            if answer.get("status") != "ok":
                return None
            clients = answer.get("ui_clients")
            stored = str(answer.get("stored") or "new")
            return NotifyResult(stored=stored if stored != NOTIFY_UNKNOWN else "new",
                                acked=answer.get("acked") is True,
                                ui_clients=clients if isinstance(clients, int) and not isinstance(clients, bool)
                                else 0)
        return None if answer is None and never_handled else NotifyResult(stored=NOTIFY_UNKNOWN)

    async def location_sync(self, payload: dict[str, Any]) -> None:
        from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
        await jarvis_bridge.notify_location(payload)

    async def log_batch(self, items: list[dict[str, Any]], dropped: int) -> None:
        from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel
        await jarvis_bridge.notify_log_batch(items, dropped, source="desktop")


class NoLaptop:
    """No usable link (keys missing): the laptop is never online."""

    def online(self) -> bool:
        return False

    async def speak(self, text: str, item_id: str,  # pylint: disable=unused-argument
                    call_id: str | None = None) -> str:
        return "offline"

    async def run_action(self, *, tool: str, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "error", "tool": tool, "error_code": "DESKTOP_OFFLINE",
                "message": "Sir, laptop abhi online nahi hai, is liye yeh kaam nahi ho saka."}


def speak_outcome(outcome: Any) -> str:
    """'acked' | 'offline' | 'failed' | 'unknown' for a pc.speak_local CallOutcome. A call whose result is not known
    (unknown, timeout, stopped, or a start that may have spoken) is 'unknown': its late outcome may still ack it."""
    from services.ai_core import desktop_link as dl  # pylint: disable=import-outside-toplevel
    kind = getattr(outcome, "kind", None)
    if kind == dl.RESULT:
        result = outcome.result if isinstance(outcome.result, Mapping) else {}
        if result.get("status") in ("success", "ok"):
            return "acked"
        return "unknown" if dl.may_have_run(outcome.status, outcome.effect) else "failed"
    if kind == dl.OFFLINE:
        return "offline"
    if kind in (dl.REFUSED, dl.NOT_STARTED):
        return "failed"
    return "unknown"


def _pc_operation(name: str) -> Any:
    from services.ai_core import tool_placement  # pylint: disable=import-outside-toplevel
    return next(op for op in tool_placement.PC_OPERATIONS if op.name == name)


class LinkLaptop:
    """LaptopPort over this process's DesktopLink (peer 'svc', K-70 v42)."""

    def __init__(self, link: Any) -> None:
        self.link = link

    def online(self) -> bool:
        try:
            return bool(self.link.online())
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def speak(self, text: str, item_id: str, call_id: str | None = None) -> str:
        """pc.speak_local under call_id (the id the ledger already holds; a fresh one when None)."""
        from services.ai_core import desktop_link as dl  # pylint: disable=import-outside-toplevel
        if not self.online():
            return "offline"
        spec = _pc_operation("pc.speak_local")
        request = dl.CallRequest(tool=spec.name, args={"text": text},
                                 param_schema_sha256=spec.manifest_entry()["param_schema_sha256"],
                                 timeout_s=spec.timeout, origin=dl.origin_for(self.link.room, "internal"),
                                 label={"svc_item": item_id}, call_id=call_id or dl.new_call_id())
        outcome = await asyncio.wrap_future(self.link.submit_call(request))
        return speak_outcome(outcome)

    async def run_action(self, *, tool: str, args: Mapping[str, Any], param_schema_sha256: str, timeout_s: float,
                         approval: Mapping[str, Any] | None = None,
                         label: Mapping[str, Any] | None = None) -> dict[str, Any]:
        from services.ai_core import desktop_link as dl  # pylint: disable=import-outside-toplevel
        request = dl.CallRequest(tool=tool, args=dict(args), param_schema_sha256=param_schema_sha256,
                                 timeout_s=timeout_s, origin=dl.origin_for(self.link.room, "scheduler"),
                                 approval=approval, label=dict(label or {}))
        outcome = await asyncio.wrap_future(self.link.submit_call(request))
        return outcome.k12(tool)


class ReminderSource:
    """Cursor's X-66 reminder API (services/automation/jarvis_reminders.py), called only through its public
    functions: list_due_reminders() and load_reminders() read under the file lock; mark_reminder_delivered(id) is
    the only write."""

    def list_due(self) -> list[dict[str, Any]]:
        from services.automation import jarvis_reminders  # pylint: disable=import-outside-toplevel
        return list(jarvis_reminders.list_due_reminders())

    def pending_rows(self) -> list[dict[str, Any]]:
        """Every pending reminder in reminders.json order, the order in which mark_reminder_delivered(id) looks for
        its row (list_due_reminders sorts by the 'time' string instead). Read through load_reminders under the same
        file lock as list_due_reminders."""
        from services.automation import jarvis_reminders  # pylint: disable=import-outside-toplevel
        with locked(jarvis_reminders.REMINDERS_FILE):
            rows = jarvis_reminders.load_reminders()
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict) and row.get("status") == "pending"]

    def mark_delivered(self, reminder_id: str) -> bool:
        from services.automation import jarvis_reminders  # pylint: disable=import-outside-toplevel
        return bool(jarvis_reminders.mark_reminder_delivered(reminder_id))


class TaskSource:
    """Cursor's TaskManager (K-33; every connection with WAL and a 5 s busy_timeout, X-66)."""

    @staticmethod
    def _manager() -> Any:
        from services.automation.jarvis_task_manager import task_manager  # pylint: disable=import-outside-toplevel
        return task_manager

    def claim_due(self) -> list[dict[str, Any]]:
        return list(self._manager().get_due_tasks())

    def triggered(self) -> list[dict[str, Any]]:
        return list(self._manager().list_triggered_tasks())

    def mark_completed(self, task_id: int) -> None:
        self._manager().mark_completed(task_id)


class _OwnerSMTP(smtplib.SMTP):
    """smtplib.SMTP that tells whether the DATA command was begun: from then on a broken connection leaves it unknown
    whether the server took the message (its 250 reply may be what was lost)."""

    data_begun = False

    def data(self, msg: Any) -> tuple[int, bytes]:
        self.data_begun = True
        return super().data(msg)


def _quit_quietly(server: smtplib.SMTP) -> None:
    """QUIT and close once the message's outcome is known: an error here (a QUIT reply other than 221, a dropped
    connection) changes nothing about it and is only logged."""
    try:
        code = server.docmd("QUIT")[0]
        if code != 221:
            logger.debug("DELIVERY e-mail: QUIT answered %s", code)
    except (smtplib.SMTPException, OSError) as exc:
        logger.debug("DELIVERY e-mail: QUIT failed (%s)", type(exc).__name__)
    finally:
        with contextlib.suppress(OSError):
            server.close()


class OwnerEmail:
    """Ladder step 4: one plain e-mail to the owner's own address (EMAIL_USER, also the sender) over SMTP STARTTLS
    (SMTP_HOST / SMTP_PORT, default smtp.gmail.com:587). Never logs the address, the text or the password."""

    def configured(self) -> bool:
        return bool((os.getenv("EMAIL_USER") or "").strip() and (os.getenv("EMAIL_APP_PASSWORD") or "").strip())

    def send(self, subject: str, body: str) -> str:
        """EMAIL_SENT once send_message returned (the server took it; QUIT and close after it are only logged);
        EMAIL_FAILED when the server surely did not take it (a failure before DATA: connect, STARTTLS, login, MAIL or
        RCPT; or an error reply to DATA), so another try may follow; EMAIL_UNKNOWN when the connection broke during
        DATA (the server may have taken it), so it is never sent again."""
        user = (os.getenv("EMAIL_USER") or "").strip()
        password = (os.getenv("EMAIL_APP_PASSWORD") or "").strip()
        if not user or not password:
            return EMAIL_FAILED
        host = (os.getenv("SMTP_HOST") or "").strip() or "smtp.gmail.com"
        port_text = (os.getenv("SMTP_PORT") or "").strip()
        port = int(port_text) if port_text.isdigit() else 587
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = user, user, subject
        message.set_content(body)
        server: _OwnerSMTP | None = None
        try:
            server = _OwnerSMTP(host, port, timeout=30)
            server.starttls(context=ssl.create_default_context())
            server.login(user, password)
            server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            unknown = server is not None and server.data_begun and not isinstance(exc, smtplib.SMTPDataError)
            logger.warning("DELIVERY e-mail %s (%s)", "outcome unknown, not sent again" if unknown else "not sent",
                           type(exc).__name__)
            return EMAIL_UNKNOWN if unknown else EMAIL_FAILED
        finally:
            if server is not None:
                _quit_quietly(server)
        return EMAIL_SENT


def _email_outcome(result: Any) -> str:
    """EMAIL_SENT, EMAIL_FAILED or EMAIL_UNKNOWN for what an e-mail port's send returned (True and False, from a
    port that only tells sent or not, count as sent and failed)."""
    if result is True or result == EMAIL_SENT:
        return EMAIL_SENT
    return EMAIL_UNKNOWN if result == EMAIL_UNKNOWN else EMAIL_FAILED


# ---------------------------------------------------------------- the snapshot


class SnapshotAborted(Exception):
    """The service is stopping: the partial snapshot was removed."""


def _skip_snapshot_file(name: str) -> bool:
    lower = name.lower()
    if lower.startswith(".env") or lower.endswith(".env") or ".env." in lower:
        return True
    if lower.endswith((".lock", ".tmp", ".part", ".pem", ".key", ".dpapi", "-wal", "-shm", "-journal")):
        return True
    return bool(_SECRET_NAME.search(lower))


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def sqlite_online_copy(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite database through its online backup API (WAL content included)."""
    src = sqlite3.connect(str(source), timeout=30)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


class SnapshotWriter:
    """The daily sealed snapshot (see the module docstring). write() runs in a worker thread; stop aborts it between
    files and removes the partial '.<name>.<random>.part' file (a dotfile, which the bridge never serves)."""

    def __init__(self, *, root: Path, out_dir: Path, recipient_text: str | None, clock: Callable[[], float] = time.time,
                 chunk_size: int | None = None) -> None:
        from services.utils import backup_crypto  # pylint: disable=import-outside-toplevel
        self.root = Path(root)
        self.out_dir = Path(out_dir)
        self._clock = clock
        self._chunk = chunk_size or backup_crypto.DEFAULT_CHUNK_SIZE
        self.recipient: Any = None
        self.problem = ""
        text = (recipient_text or "").strip()
        if not text:
            self.problem = f"{ENV_RECIPIENT} is not set"
        else:
            try:
                self.recipient = backup_crypto.parse_public_key(text)
            except backup_crypto.SnapshotCryptoError:
                self.problem = f"{ENV_RECIPIENT} is not a usable X25519 public key"

    @property
    def enabled(self) -> bool:
        return self.recipient is not None

    def newest_ts(self) -> float | None:
        try:
            entries = list(os.scandir(self.out_dir))
        except OSError:
            return None
        times = []
        for entry in entries:
            if entry.name.startswith(SNAPSHOT_PREFIX) and entry.name.endswith(SNAPSHOT_SUFFIX):
                with contextlib.suppress(OSError):
                    if entry.is_file(follow_symlinks=False):
                        times.append(entry.stat(follow_symlinks=False).st_mtime)
        return max(times) if times else None

    def _free_name(self, now: float) -> Path:
        date = datetime.fromtimestamp(now, local_zone()).strftime("%Y-%m-%d")
        candidate = self.out_dir / f"{SNAPSHOT_PREFIX}{date}{SNAPSHOT_SUFFIX}"
        n = 2
        while candidate.exists():
            candidate = self.out_dir / f"{SNAPSHOT_PREFIX}{date}_{n}{SNAPSHOT_SUFFIX}"
            n += 1
        return candidate

    def write(self, stop: threading.Event | None = None) -> dict[str, Any] | None:
        """Writes one snapshot; returns {name, size, files, ts}, or None without a recipient key."""
        from services.utils.backup_crypto import SealingWriter  # pylint: disable=import-outside-toplevel
        if self.recipient is None:
            return None
        now = self._clock()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        final = self._free_name(now)
        part = self.out_dir / f".{final.name}.{secrets.token_hex(4)}.part"
        try:
            with tempfile.TemporaryDirectory(prefix="jarvis-snap-") as tmp, part.open("xb") as raw:
                writer = SealingWriter(raw, self.recipient, chunk_size=self._chunk)
                with tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                    files = self._add_roots(tar, Path(tmp), stop)
                writer.close()
                raw.flush()
                os.fsync(raw.fileno())
            if final.exists():  # never over an earlier snapshot
                final = self._free_name(now)
            os.replace(part, final)
        except BaseException:
            with contextlib.suppress(OSError):
                part.unlink()
            raise
        return {"name": final.name, "size": final.stat().st_size, "files": files, "ts": now}

    def _add_roots(self, tar: tarfile.TarFile, tmp: Path, stop: threading.Event | None) -> int:
        count = 0
        for name in SNAPSHOT_ROOTS:
            base = self.root / name
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith(".env"))
                for filename in sorted(filenames):
                    if stop is not None and stop.is_set():
                        raise SnapshotAborted()
                    path = Path(dirpath) / filename
                    if _skip_snapshot_file(filename) or path.is_symlink() or not path.is_file():
                        continue
                    arcname = f"{name}/{path.relative_to(base).as_posix()}"
                    if self._add_file(tar, path, arcname, tmp / f"f{count}"):
                        count += 1
        return count

    @staticmethod
    def _add_file(tar: tarfile.TarFile, path: Path, arcname: str, copy: Path) -> bool:
        """Adds a consistent copy of path (a SQLite online copy, else a file copy) so a file that changes meanwhile
        never breaks the tar stream. A file that vanished or cannot be read is skipped (named, never its content)."""
        try:
            if _is_sqlite(path):
                sqlite_online_copy(path, copy)
            else:
                shutil.copy2(path, copy)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("SNAPSHOT skipped %s (%s)", arcname, type(exc).__name__)
            with contextlib.suppress(OSError):
                copy.unlink()
            return False
        try:
            tar.add(str(copy), arcname=arcname, recursive=False)
        finally:
            with contextlib.suppress(OSError):
                copy.unlink()
        return True


# ---------------------------------------------------------------- the service


@dataclass(frozen=True)
class ServiceTimers:
    """K-77 intervals (tests shorten them)."""

    reminder_s: float = 30.0
    task_s: float = 45.0
    heartbeat_s: float = 30.0
    watch_s: float = 5.0  # acks and UI connects are polled this often while anything is open
    announce_wait_s: float = 45.0  # the live job's announce_result
    poll_s: float = 2.0
    retry_throttle_s: float = 25.0  # a new item whose ladder failed before step 3 is tried again after this
    late_wait_s: float = 30.0  # a speak of unknown outcome waits this long after a laptop connect for its late result
    ui_ack_grace_s: float = 5.0  # after reminder_due went to a connected UI, step 2 waits this long for its ack
    # a speak a restart cut waits at most this long after its call for this process's first node hello (whose
    # result_resend brings its outcome): the node keeps an unsent result 10 min (K-70 outbox), a speak runs <= 60 s
    restart_hold_s: float = 660.0
    scheduler_offline_wait_s: float = 900.0  # D16
    scheduler_poll_s: float = 15.0
    snapshot_catchup_s: float = 600.0


class SchedulerHost:
    """The K-32 hook for C-80: scheduled laptop actions through the svc DesktopLink with origin 'scheduler', carrying
    the approval recorded at scheduling time. While the laptop is offline it waits up to scheduler_offline_wait_s
    (15 min, D16), checking every scheduler_poll_s; then the failure is announced through the delivery ladder (a fixed
    line with the task id) and a DESKTOP_OFFLINE K-12 error is returned. The tool is never run twice."""

    def __init__(self, service: AgentServices) -> None:
        self.service = service

    async def run_desktop_action(self, *, task_id: Any, tool: str, args: Mapping[str, Any], param_schema_sha256: str,
                                 timeout_s: float, approval: Mapping[str, Any] | None = None) -> dict[str, Any]:
        svc = self.service
        timers = svc.timers
        deadline = time.monotonic() + timers.scheduler_offline_wait_s
        logged = False
        while svc.laptop is None or not svc.laptop.online():
            remaining = deadline - time.monotonic()
            if svc.stopping or remaining <= 0:
                logger.warning("SCHEDULER task %s: laptop offline, %s not run", task_id, tool)
                if not svc.stopping:
                    await svc.announce_system(
                        f"s:{_safe_id(task_id)}:offline",
                        f"Sir, scheduled task {task_id} laptop offline hone ki wajah se nahi ho saka.")
                return {"status": "error", "tool": tool, "error_code": "DESKTOP_OFFLINE",
                        "message": "Sir, laptop abhi online nahi hai, is liye yeh kaam nahi ho saka."}
            if not logged:
                logged = True
                logger.info("SCHEDULER task %s: laptop offline, waiting up to %.0f s", task_id, remaining)
            await svc.sleep(min(timers.scheduler_poll_s, remaining))
        logger.info("SCHEDULER task %s: %s sent to the laptop", task_id, tool)
        return await svc.laptop.run_action(tool=tool, args=args, param_schema_sha256=param_schema_sha256,
                                           timeout_s=timeout_s, approval=approval,
                                           label={"scheduler_task": str(task_id)})


class AgentServices:  # pylint: disable=too-many-instance-attributes,too-many-public-methods  # one service
    """jarvis-agent-svc (see the module docstring). Ports left None are built for production when it goes active."""

    def __init__(self, *, brain: str, root: Path, snapshots_dir: Path | None = None,  # pylint: disable=too-many-arguments
                 bridge: BridgePort | None = None, laptop: LaptopPort | None = None, reminders: Any = None,
                 tasks: Any = None, email: Any = None, location_saver: Callable[..., Any] | None = None,
                 recipient_key: str | None = None, email_after_min: int = 0, timers: ServiceTimers | None = None,
                 health_host: str = HEALTH_HOST, health_port: int = HEALTH_PORT, clock: Callable[[], float] = time.time,
                 link_factory: Callable[[], Any] | None = None, validate: Callable[[], Any] | None = None,
                 room: str = "jarvis-hybrid") -> None:
        self.brain = brain
        self.active = brain == BRAIN_VPS
        self.state = STATE_ACTIVE if self.active else STATE_STANDBY
        self.root = Path(root)
        self.ledger = DeliveryLedger(self.root / "conversations" / LEDGER_FILE)
        self.bridge = bridge
        self.laptop = laptop
        self.reminders = reminders
        self.tasks = tasks
        self.email = email
        self.location_saver = location_saver
        self.email_after_min = max(0, int(email_after_min or 0))
        self.timers = timers or ServiceTimers()
        self.health_host, self.health_port = health_host, health_port
        self.health_port_bound: int | None = None
        self.clock = clock
        self.room = room
        self.snapshots = SnapshotWriter(root=self.root, out_dir=Path(snapshots_dir or self.root / "snapshots"),
                                        recipient_text=recipient_key, clock=clock)
        self.scheduler = SchedulerHost(self)
        # C-80 sets it: async action_runner(row) runs a due task's action (K-32) and owns that row's status and
        # announcement; None: a task's action is not run (announced and completed after delivery, as on the PC today)
        self.action_runner: Callable[[dict[str, Any]], Awaitable[Any]] | None = None
        self._link_factory = link_factory
        self._validate = validate
        self._link: Any = None
        self._unsubscribe: list[Callable[[], None]] = []
        self._stop: asyncio.Event | None = None
        self._ladder: asyncio.Lock | None = None
        self._loops: dict[str, asyncio.Task[Any]] = {}
        self._intervals: dict[str, float | None] = {}
        self._ticks: dict[str, float] = {}
        self._spawned: set[asyncio.Task[Any]] = set()
        self._server: asyncio.AbstractServer | None = None
        self._ui_seen: tuple[str, int, int] | None = None  # (bridge boot id, ui_connects, ui_clients) last answered
        self._node_session_ts: float | None = None  # clock() of the laptop's last hello (DesktopLink 'session')
        self._ui_emitted: dict[str, float] = {}  # item id -> time.monotonic() when this process's step 3 reached a UI
        self._held_left: dict[tuple[str, str], int] = {}  # (trigger, item id) -> tries left of its waiting retry
        self._due_counts = {KIND_REMINDER: 0, KIND_TASK: 0}
        self._undelivered = 0
        self._warned: set[str] = set()
        self._snapshot_stop = threading.Event()
        self._snapshot_first = True
        self.next_snapshot_ts: float | None = None
        self.last_snapshot_ts: float | None = None
        self._started = clock()
        self._started_mono = time.monotonic()
        self.stopping = False

    @classmethod
    def from_environment(cls, profile: RuntimeProfile | None = None) -> AgentServices:
        profile = profile or runtime_profile()
        root = Path(config.project_root)
        raw_after = (os.getenv(ENV_EMAIL_AFTER) or "").strip()
        after = int(raw_after) if raw_after.isdigit() else 0
        snapshots = (os.getenv(ENV_SNAPSHOTS_DIR) or "").strip()
        return cls(brain=profile.brain, root=root, snapshots_dir=Path(snapshots) if snapshots else None,
                   recipient_key=os.getenv(ENV_RECIPIENT), email_after_min=after,
                   room=(os.getenv("JARVIS_ROOM_NAME") or "").strip() or "jarvis-hybrid",
                   validate=lambda: config.validate(ROLE_SVC))

    # --- lifecycle ---------------------------------------------------------------------------------------------

    async def run(self) -> int:
        """Serves health, runs the active duties (vps brain) until request_stop(), then shuts down. Returns 0."""
        self._stop = asyncio.Event()
        self._ladder = asyncio.Lock()
        try:
            await self._start_health()
            if self.active:
                await self._start_active()
            logger.info("AGENT-SVC %s (brain %s); health on %s:%s", self.state, self.brain, self.health_host,
                        self.health_port_bound)
            await self._stop.wait()
        finally:
            await self._shutdown()
        return 0

    def request_stop(self) -> None:
        """SIGTERM / SIGINT: ends run() (idempotent; call on the service's loop)."""
        if not self.stopping:
            logger.info("AGENT-SVC stopping")
        self.stopping = True
        self._snapshot_stop.set()
        if self._stop is not None:
            self._stop.set()

    async def sleep(self, seconds: float) -> None:
        """Waits seconds, or less when the service stops."""
        if self._stop is None:
            await asyncio.sleep(seconds)
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), max(0.0, seconds))

    async def _start_active(self) -> None:
        if self._validate is not None:
            try:
                self._validate()
            except RuntimeError:
                self.state = STATE_CONFIG_ERROR
                logger.critical("AGENT-SVC configuration unusable (names logged above); duties not started")
                return
        self._build_ports()
        self.last_snapshot_ts = await asyncio.to_thread(self.snapshots.newest_ts)
        self._spawn_loop("reminders", self.reminder_tick, self.timers.reminder_s)
        self._spawn_loop("tasks", self.task_tick, self.timers.task_s)
        self._spawn_loop("watch", self.watch_tick, self.timers.watch_s)
        self._spawn_loop("heartbeat", self.heartbeat_tick, self.timers.heartbeat_s)
        self._spawn_loop("snapshot", self._snapshot_cycle, None)

    def _build_ports(self) -> None:
        if self.bridge is None:
            self.bridge = NotifyBridge()
        if self.reminders is None:
            self.reminders = ReminderSource()
        if self.tasks is None:
            self.tasks = TaskSource()
        if self.email is None:
            self.email = OwnerEmail()
        if self.location_saver is None:
            from services.utils.jarvis_location import save_device_location  # pylint: disable=import-outside-toplevel
            self.location_saver = save_device_location
        if self.laptop is None:
            self._link = self._make_link()
            if self._link is None:
                self.laptop = NoLaptop()
            else:
                self.laptop = LinkLaptop(self._link)
                loop = asyncio.get_running_loop()
                self._unsubscribe = [self._link.subscribe("event", self.on_node_event, loop),
                                     self._link.subscribe("session", self.on_node_session, loop),
                                     self._link.subscribe("late", self.on_late_outcome, loop)]

    def _make_link(self) -> Any:
        if self._link_factory is not None:
            return self._link_factory()
        from services.ai_core import desktop_link  # pylint: disable=import-outside-toplevel
        return desktop_link.ensure_link(room=self.room, job_id="svc", svc=True)

    def _spawn_loop(self, name: str, tick: Callable[[], Awaitable[Any]], interval: float | None) -> None:
        self._intervals[name] = interval

        async def runner() -> None:
            while not self.stopping:
                failed = False
                try:
                    await tick()
                except Exception as exc:  # pylint: disable=broad-exception-caught  # the next tick tries again
                    failed = True
                    logger.error("SVC loop %s failed (%s); it goes on", name, type(exc).__name__)
                self._ticks[name] = time.monotonic()
                if interval is not None:
                    await self.sleep(interval)
                elif failed:
                    await self.sleep(60.0)  # a self-timed loop (the snapshot) never spins on a repeating error
        self._ticks[name] = time.monotonic()
        self._loops[name] = asyncio.create_task(runner(), name=f"svc-{name}")

    def _spawn(self, coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._spawned.add(task)
        task.add_done_callback(self._spawned.discard)

    async def _shutdown(self) -> None:
        self.stopping = True
        self._snapshot_stop.set()
        tasks = [*self._loops.values(), *self._spawned]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10.0)
        for unsubscribe in self._unsubscribe:
            with contextlib.suppress(Exception):
                unsubscribe()
        if self._link is not None:
            from services.ai_core import desktop_link  # pylint: disable=import-outside-toplevel
            with contextlib.suppress(Exception):
                desktop_link.retire(self._link)
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 5.0)
        logger.info("AGENT-SVC stopped cleanly")

    # --- health ------------------------------------------------------------------------------------------------

    async def _start_health(self) -> None:
        self._server = await asyncio.start_server(self._serve_health, self.health_host, self.health_port)
        sockets = self._server.sockets or ()
        self.health_port_bound = sockets[0].getsockname()[1] if sockets else self.health_port

    def health(self) -> dict[str, Any]:
        """The /health body (counts, states and times only)."""
        now_mono = time.monotonic()
        loops: dict[str, dict[str, Any]] = {}
        healthy = self.state != STATE_CONFIG_ERROR
        for name, task in self._loops.items():
            alive = not task.done()
            age = round(now_mono - self._ticks.get(name, now_mono), 1)
            interval = self._intervals.get(name)
            stalled = interval is not None and age > max(3 * interval, interval + self.timers.announce_wait_s + 60)
            loops[name] = {"alive": alive, "last_tick_age_s": age}
            healthy = healthy and alive and not stalled
        if self.stopping:
            healthy = False
        link_started = self._link is not None or isinstance(self.laptop, LinkLaptop)
        return {
            "status": "ok" if healthy else "degraded",
            "role": ROLE_SVC,
            "brain": self.brain,
            "state": "stopping" if self.stopping else self.state,
            "pid": os.getpid(),
            "uptime_s": round(now_mono - self._started_mono, 1),
            "git_sha": GIT_SHA,
            "due": {"reminders": self._due_counts[KIND_REMINDER], "tasks": self._due_counts[KIND_TASK]},
            "due_pending": sum(self._due_counts.values()),
            "undelivered": self._undelivered,
            "last_snapshot_ts": self.last_snapshot_ts,
            "snapshot": {"enabled": self.active and self.snapshots.enabled, "next_ts": self.next_snapshot_ts},
            "loops": loops,
            "link": {"started": link_started, "online": bool(self.laptop is not None and self.laptop.online())},
            "ts": round(self.clock(), 3),
        }

    async def _serve_health(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), 5.0)
            for _ in range(100):  # the headers, up to the blank line
                header = await asyncio.wait_for(reader.readline(), 5.0)
                if header in (b"\r\n", b"\n", b""):
                    break
            parts = request.decode("latin-1").split()
            method, path = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
            if method in ("GET", "HEAD") and path.split("?", 1)[0] == "/health":
                body = self.health()
                code = 200 if body["status"] == "ok" else 503
            else:
                body, code = {"error": "not_found"}, 404
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            head = (f"HTTP/1.1 {code} {_REASONS[code]}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(data)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
            writer.write(head.encode("ascii") + (b"" if method == "HEAD" else data))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, OSError, UnicodeError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    # --- ledger helpers ----------------------------------------------------------------------------------------

    async def _ledger(self, change: Callable[[dict[str, dict[str, Any]]], Any]) -> Any:
        return await asyncio.to_thread(self.ledger.update, change)

    async def _ledger_read(self) -> dict[str, dict[str, Any]]:
        return await asyncio.to_thread(self.ledger.read)

    async def _entry(self, item: DueItem) -> dict[str, Any]:
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
            entry = items.get(item.id)
            if entry is None:
                entry = items[item.id] = _new_entry(item, now)
            return dict(entry)
        return await self._ledger(change)

    async def _attempt(self, item_id: str, step: str, outcome: str, ref: str | None = None, *,
                       release: str | None = None, drop: tuple[str, ...] = (), **fields: Any) -> None:
        """One attempt row (and fields, in the same write); release names the pending-call field (announce_call,
        laptop_call) that this row settles when it holds the call ref; drop names fields it removes."""
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> None:
            entry = items.get(item_id)
            if entry is not None:
                _add_attempt(entry, step, outcome, now, ref)
                for name in drop:
                    entry.pop(name, None)
                for name, value in fields.items():
                    if name == "notified_at" and entry.get(name):
                        continue  # the first time it was stored counts
                    entry[name] = value
                if release is not None:
                    _release(entry, release, ref)
        await self._ledger(change)
        logger.info("DELIVERY %s %s: %s", item_id, step, clean_text(outcome, 40))

    async def _open_call(self, item: DueItem, name: str, call_id: str | None = None) -> str:
        """Records entry[name] = {id, ts} before the call is sent and returns its id: for 'announce_call' a new
        attempt id <item id>#a<n> (n never repeats within the entry, so no old result at the bridge can pass for a
        new attempt's), else call_id."""
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> str:
            entry = items.get(item.id)
            if entry is None:
                entry = items[item.id] = _new_entry(item, now)
            new_id = call_id or ""
            if name == "announce_call":
                seq = max(int(_number(entry.get("announce_seq")) or 0), len(_attempts(entry, CH_ANNOUNCE))) + 1
                entry["announce_seq"] = seq
                new_id = f"{item.id}#a{seq}"
            entry[name] = {"id": new_id, "ts": round(now, 3)}
            return new_id
        return await self._ledger(change)

    # --- the ladder --------------------------------------------------------------------------------------------

    @property
    def _lock(self) -> asyncio.Lock:
        if self._ladder is None:
            self._ladder = asyncio.Lock()
        return self._ladder

    async def process_due(self, item: DueItem) -> None:
        """A due item seen by a tick: its first ladder run, or nothing when the ledger says it is delivered (then only
        its source is marked), closed, or waiting for an ack after step 3, or while a speak of it may still be
        acknowledged by its late outcome (_speak_pending: the process before a restart sent it). A step 3 whose
        answer was lost (notify_unknown) is sent again instead, at most once per retry_throttle_s, until the bridge
        answers it: idempotent there ('exists', or acked when a dashboard acknowledged it meanwhile). The throttle
        counts from the last try; a try stamped ahead of the clock (it stepped back, or the stamp was rounded up)
        counts as elapsed, so a clock step never holds an item back."""
        async with self._lock:
            entry = await self._entry(item)
            if entry.get("delivered_at"):
                # still due in its source after the delivery: its source is marked, again when an earlier mark did not
                # take (source_marked set, yet the row is still pending: a save that failed, or a reminder sharing
                # its id), so the item never stays due until its ledger entry expires and is delivered once more
                await self._mark_source(entry)
                return
            if entry.get("closed_at") or self._speak_pending(entry):
                return
            tries = _attempts(entry)
            since = self.clock() - float(tries[-1].get("ts") or 0) if tries else None
            throttled = since is not None and 0.0 <= since < self.timers.retry_throttle_s
            if entry.get("notified"):
                if entry.get("notify_unknown") and not throttled:
                    await self._step_notify(item)
                return
            if throttled:
                return
            await self._run_ladder(item)

    async def _run_ladder(self, item: DueItem) -> None:
        held: set[str] = set()
        announced = await self._step_announce(item)
        if announced is None or announced or self.stopping:
            return  # delivered, or the job may still have announced it (its result unread): the next run settles it
        if await self._step_laptop(item, held) or self.stopping:
            return
        if self._speak_pending(await self._entry(item)):
            # a speak of unknown outcome (this run's, or an earlier one a node hello made pending again) holds every
            # channel: no step 3 before its late outcome or the end of its hold (process_due runs the ladder again)
            logger.debug("DELIVERY %s: step 3 waits for the outcome of a speak", item.id)
        else:
            await self._step_notify(item)
        if held and not self.stopping:
            self._retry_later("node", held)

    async def _step_announce(self, item: DueItem) -> bool | None:
        """Step 1: the live job (only when an owner-room agent is connected). The attempt id is in the ledger
        (announce_call) before the announce is sent, and it stays there until the attempt is settled: by the job's
        result, by the end of its window (a timeout), or at once when the announce surely did not go out ('no_agent',
        'refused'). One whose outcome is not known ('unknown': the bridge may have emitted it) is waited for like a
        sent one. An attempt a restart left open (its wait was cut) is settled first and is this run's step 1
        (_settle_announce). It is never announced again. True delivered, False not (the ladder goes on), None while
        the attempt is not settled (no poll the bridge answered told its result or its timeout, or the service
        stops): the ladder stops there, so the item is neither spoken nor stored at the bridge before its result."""
        entry = await self._entry(item)
        left = _pending_call(entry, "announce_call")
        answer = await self.bridge.poll([left[0]] if left is not None else [], [])
        if left is not None:
            return await self._settle_announce(item, left, answer)
        if answer is None or answer.agents <= 0:
            await self._attempt(item.id, CH_ANNOUNCE, "no_bridge" if answer is None else "no_agent")
            return False
        attempt_id = await self._open_call(item, "announce_call")
        status = await self.bridge.announce(attempt_id, item.text, item.kind)  # the job adds its own label
        if status in ("no_agent", "refused"):  # surely not emitted: nothing to wait for
            await self._attempt(item.id, CH_ANNOUNCE, status, ref=attempt_id, release="announce_call")
            return False
        # 'sent', or 'unknown': the job may announce it, so it is never also spoken before its result or window end
        await self._attempt(item.id, CH_ANNOUNCE, status, ref=attempt_id)
        return await self._await_announce(item, attempt_id, self.timers.announce_wait_s)

    async def _settle_announce(self, item: DueItem, left: tuple[str, float], answer: PollAnswer | None, *,
                               wait: bool = True) -> bool | None:
        """An announce attempt whose result was never read (left: its id and send time; answer: the poll that asked
        for it, None when the bridge did not answer): a result stored at the bridge is the outcome; once
        announce_wait_s from its send has passed, a poll the bridge answered without a result makes it a timeout
        (settled, as a wait that ran out), while an unanswered one settles nothing (None: its result may be stored
        at the bridge, which keeps results across its restarts); inside that window it is waited for until the
        window ends, or with wait False None is returned at once (the caller holds the item). None also when the
        service stops first: nothing is recorded, the next run settles it."""
        attempt_id, sent_ts = left
        ack = answer.acks.get(attempt_id) if answer is not None else None
        if ack is not None:
            return await self._announce_result(item, attempt_id, ack)
        remaining = self.timers.announce_wait_s - max(0.0, self.clock() - sent_ts)
        if remaining <= 0:
            if answer is None:
                logger.debug("DELIVERY %s announce %s: no answer from the bridge; settled on a later run", item.id,
                             attempt_id)
                return None
            await self._attempt(item.id, CH_ANNOUNCE, "timeout", ref=attempt_id, release="announce_call")
            return False
        if not wait:
            return None
        logger.info("DELIVERY %s announce %s still open: waiting %.0f s more", item.id, attempt_id, remaining)
        return await self._await_announce(item, attempt_id, remaining)

    async def _await_announce(self, item: DueItem, attempt_id: str, wait_s: float) -> bool | None:
        """Polls for the job's announce_result: True delivered (the ack), False not delivered, or no result within
        wait_s by a poll the bridge answered (both recorded: the attempt is settled); None when the service stops
        first, or when the bridge did not answer the poll at the end of wait_s. Nothing is recorded then, so a later
        run (also the restarted svc) settles the attempt by its result instead of announcing the item again."""
        deadline = time.monotonic() + wait_s
        while not self.stopping:
            await self.sleep(self.timers.poll_s)
            if self.stopping:
                break
            answer = await self.bridge.poll([attempt_id], [])
            ack = answer.acks.get(attempt_id) if answer is not None else None
            if ack is not None:
                return await self._announce_result(item, attempt_id, ack)
            if time.monotonic() >= deadline:
                if answer is None:
                    logger.debug("DELIVERY %s announce %s: no answer from the bridge; settled on a later run",
                                 item.id, attempt_id)
                    return None
                await self._attempt(item.id, CH_ANNOUNCE, "timeout", ref=attempt_id, release="announce_call")
                return False
        return None

    async def _announce_result(self, item: DueItem, attempt_id: str, ack: Mapping[str, Any]) -> bool:
        if ack.get("delivered") is True:
            await self._delivered(item.id, CH_ANNOUNCE, attempt=(CH_ANNOUNCE, "acked", attempt_id))
            return True
        await self._attempt(item.id, CH_ANNOUNCE, str(ack.get("reason") or "not_delivered"), ref=attempt_id,
                            release="announce_call")
        return False

    def _speak_pending(self, entry: Mapping[str, Any]) -> bool:
        """True while a pc.speak_local of this item may have spoken and has no outcome yet, until late_wait_s after
        that call or after the laptop's last hello, whichever is later (a late result follows a hello within
        seconds). Before this process saw any node hello (a restart cut the speak, and its outcome can only come as
        the result_resend of the new link's first hello) it holds until that hello plus late_wait_s, but at most
        restart_hold_s after the call (the node keeps an unsent result 10 min). Nothing delivers the item meanwhile,
        on any channel: no announce or step 3 (process_due runs no ladder, _run_ladder stops before step 3, a UI
        connect's retry holds it) and no speak (_laptop_gate). After that wait it may be delivered again."""
        pending = _pending_call(entry, "laptop_call")
        if pending is None:
            return False
        if self._node_session_ts is None:
            return self.clock() - pending[1] < self.timers.restart_hold_s
        return self.clock() - max(pending[1], self._node_session_ts) < self.timers.late_wait_s

    @staticmethod
    def _hold(item: DueItem, held: set[str] | None) -> bool:
        """The step does not deliver item now; held (when given) is told, so it is tried again after late_wait_s (at
        least ui_ack_grace_s), again while it stays held (_retry_later)."""
        if held is not None:
            held.add(item.id)
        return False

    async def _step_laptop(self, item: DueItem, held: set[str] | None = None) -> bool:
        """Step 2: pc.speak_local on the laptop (it speaks and shows a local notice). An item already stored at the
        bridge is asked there first: a dashboard that acknowledged it since the last watch poll is its delivery
        (channel 'ui') and nothing is spoken; without an answer it is held. The call id is in the ledger
        (laptop_call) before the call is submitted, so its outcome acks the item also when it comes late or after a
        restart. A held item (see _laptop_gate) is added to held and not spoken now."""
        laptop = self.laptop
        if laptop is None or not laptop.online():
            await self._attempt(item.id, CH_LAPTOP, "offline")
            return False
        gate = await self._laptop_gate(item, held)
        if gate is not None:
            return gate
        call_id = await self._open_call(item, "laptop_call", _new_call_id())
        try:
            outcome = await laptop.speak(clean_text(spoken_text(item), 1000), item.id, call_id=call_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the link answers; never raise here
            outcome = f"error_{type(exc).__name__}"
        if outcome == "acked":  # the ack row and delivered_at in one ledger write
            await self._delivered(item.id, CH_LAPTOP, attempt=(CH_LAPTOP, outcome, call_id))
            return True
        # 'unknown' may have spoken: laptop_call stays until its late outcome (on_late_outcome) or the hold ends
        await self._attempt(item.id, CH_LAPTOP, outcome, ref=call_id,
                            release=None if outcome == "unknown" else "laptop_call")
        return False

    async def _laptop_gate(self, item: DueItem,  # pylint: disable=too-many-return-statements  # one answer a case
                           held: set[str] | None) -> bool | None:
        """None when step 2 may speak item now. Else its result without a speak: False while the item is held (an
        announce of it still open inside its window or not settled yet, a speak still pending, the bridge did not
        say whether its stored notification was acknowledged, or the bridge emitted reminder_due to a connected UI
        less than ui_ack_grace_s ago, so that dashboard's ack may still be on its way; held is told) or at
        LAPTOP_ATTEMPTS_MAX; True when the job's stored result of an open announce says delivered, or a dashboard
        acknowledged the stored notification since the last watch poll (it is delivered there then, never also
        spoken). An open announce (announce_call: a restart cut its wait) is settled here too, so it never holds a
        notified item, which runs no ladder again: by its stored result, or as a timeout once its window has passed
        and the bridge answered without a result."""
        entry = await self._entry(item)
        left = _pending_call(entry, "announce_call")
        if left is not None:
            settled = await self._settle_announce(item, left, await self.bridge.poll([left[0]], []), wait=False)
            if settled is None:  # the job may still announce it
                return self._hold(item, held)
            if settled:
                return True
            entry = await self._entry(item)
        if self._speak_pending(entry):
            return self._hold(item, held)
        speaks = {row.get("ref") for row in _attempts(entry, CH_LAPTOP) if row.get("ref")}  # one per call id
        if len(speaks) >= LAPTOP_ATTEMPTS_MAX:
            return False
        if not entry.get("notified"):
            return None
        answer = await self.bridge.poll([item.id], [])
        if answer is None:  # the bridge did not say (or could not tell) whether a dashboard acknowledged it: held
            return self._hold(item, held)
        if item.id in answer.acks:
            return await self._delivered(item.id, CH_UI)
        if self._ui_grace(item.id, entry):
            return self._hold(item, held)  # reminder_due just went to a connected UI: its ack may be on its way
        return None

    def _ui_grace(self, item_id: str, entry: Mapping[str, Any]) -> bool:
        """True while reminder_due went to a connected UI less than ui_ack_grace_s ago, so that dashboard's ack may
        still be on its way. Measured on the monotonic clock when this process emitted it, so no wall-clock step
        moves it; else (emitted before a restart) on the ledger's ui_emit_ts, where a stamp ahead of the wall clock
        (it stepped back) counts as elapsed."""
        if (_number(entry.get("ui_emit_clients")) or 0) <= 0:
            return False
        grace = self.timers.ui_ack_grace_s
        emitted = self._ui_emitted.get(item_id)
        if emitted is not None:
            return time.monotonic() - emitted < grace
        emitted_ts = _number(entry.get("ui_emit_ts"))
        return emitted_ts is not None and 0.0 <= self.clock() - emitted_ts < grace

    def _note_ui_emit(self, item_id: str, clients: int) -> None:
        """Keeps the monotonic time of a step 3 that reached clients UI sockets (for _ui_grace); stamps whose grace
        ended are dropped (the ledger's stamp then answers, and only within the grace)."""
        now = time.monotonic()
        grace = self.timers.ui_ack_grace_s
        for key in [key for key, stamp in self._ui_emitted.items() if now - stamp >= grace]:
            del self._ui_emitted[key]
        if clients > 0:
            self._ui_emitted[item_id] = now
        else:
            self._ui_emitted.pop(item_id, None)

    async def _mark_notify(self, item: DueItem) -> bool:
        """Before the first step 3 is sent: notified and notify_unknown, since the bridge may hold the notification
        from then on (a stop or a crash before its answer leaves it so). True when this call set them."""
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> bool:
            entry = items.get(item.id)
            if entry is None:
                entry = items[item.id] = _new_entry(item, now)
            if entry.get("notified"):
                return False
            entry.update(notified=True, notify_unknown=True)
            return True
        return bool(await self._ledger(change))

    async def _step_notify(self, item: DueItem) -> bool:
        """Step 3: the bridge stores the UI notification and emits 'reminder_due' (again on a retry). The entry counts
        as stored from before the send (_mark_notify). The bridge's answer settles it: stored ('new' or 'exists';
        acked: delivered on the UI) drops notify_unknown and records when reminder_due went to how many UI sockets
        (_laptop_gate's grace); NOTIFY_UNKNOWN (a lost answer: the bridge may have stored and emitted it, and a
        dashboard may have acknowledged it) keeps notified with notify_unknown, so the watch tick asks the bridge for
        its ack, step 2 asks the bridge before it speaks, no ladder run announces it, and process_due sends it again
        until the bridge answers; None (surely not stored) records 'failed' and takes the mark back when this send
        set it. Returns True unless the bridge surely did not store it."""
        payload: dict[str, Any] = {"id": item.id, "description": item.text, "source": item.kind, "kind": item.kind}
        if item.due_ts is not None:
            payload["due_at"] = round(item.due_ts, 3)
        marked = await self._mark_notify(item)
        result = await self.bridge.notify(payload)
        if result is None:
            await self._attempt(item.id, "notify", "failed",
                                drop=("notified", "notify_unknown") if marked else ())
            return False
        if result.stored == NOTIFY_UNKNOWN:
            await self._attempt(item.id, "notify", NOTIFY_UNKNOWN)
            return True
        now = round(self.clock(), 3)
        fields: dict[str, Any] = {"notified": True, "notified_at": now}
        if not result.acked:  # emitted again
            fields.update(ui_emit_ts=now, ui_emit_clients=max(0, result.ui_clients))
            self._note_ui_emit(item.id, result.ui_clients)
        await self._attempt(item.id, "notify", result.stored, drop=("notify_unknown",), **fields)
        if result.acked:
            await self._delivered(item.id, CH_UI)
        return True

    async def _delivered(self, item_id: str, channel: str, attempt: tuple[str, str, str | None] | None = None) -> bool:
        """The ack: delivered_at and channel in the ledger first (the truth), in the same write as the attempt row
        that acknowledged it (attempt: step, outcome, call ref; that call is released), so no stop or crash can leave
        an open entry whose ack released its pending call and that a restarted svc would deliver again. A stored UI
        notification of an item delivered on another channel is then acknowledged at the bridge by 'svc' at once, so
        a dashboard that connects now does not show it (bridge_ack keeps it for the watch tick when the bridge does
        not answer); then the source is marked. False when the entry was not open (the attempt row is still kept)."""
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
            entry = items.get(item_id)
            if entry is None:
                return None
            if attempt is not None:
                step, outcome, ref = attempt
                _add_attempt(entry, step, outcome, now, ref)
                for name in ("announce_call", "laptop_call"):
                    _release(entry, name, ref)
            if not _is_open(entry):
                return None
            entry["delivered_at"], entry["channel"] = round(now, 3), channel
            entry.pop("announce_call", None)
            entry.pop("laptop_call", None)
            entry.pop("notify_unknown", None)
            if entry.get("notified") and channel != CH_UI:
                entry["bridge_ack"] = True
            return dict(entry)
        entry = await self._ledger(change)
        if attempt is not None:
            logger.info("DELIVERY %s %s: %s", item_id, attempt[0], clean_text(attempt[1], 40))
        if entry is None:
            return False
        self._ui_emitted.pop(item_id, None)
        logger.info("DELIVERY %s delivered via %s (%s)", item_id, channel, entry.get("kind"))
        if entry.get("bridge_ack"):
            await self._send_bridge_acks([item_id])
        await self._mark_source(entry)
        return True

    async def _send_bridge_acks(self, keys: list[str]) -> None:
        """'svc' acks of stored notifications whose items were delivered elsewhere; bridge_ack is cleared once the
        bridge answered (else the next watch tick sends them)."""
        if await self.bridge.poll([], keys) is not None:
            await self._ledger(_clear_bridge_acks(keys))

    async def _mark_source(self, entry: Mapping[str, Any]) -> None:
        """Marks the source of a delivered item: a reminder through mark_reminder_delivered (_mark_reminder), a task
        completed (C-56). source_marked tells how the last try went: set once the mark was made, cleared when it
        failed or has to wait for an earlier reminder with the same id. Either way a later tick tries again for as
        long as the item is still due in its source (process_due)."""
        item_id, kind, ref = entry.get("id"), entry.get("kind"), entry.get("ref")
        marked = True
        try:
            if entry.get("synthetic") or ref is None:
                pass
            elif kind == KIND_REMINDER:
                marked = await asyncio.to_thread(self._mark_reminder, str(item_id), ref)
                if not marked:
                    logger.debug("DELIVERY %s: source mark waits for an earlier reminder with its id", item_id)
            elif kind == KIND_TASK:
                await asyncio.to_thread(self.tasks.mark_completed, ref)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # marked on a later tick
            logger.warning("DELIVERY %s: source not marked yet (%s)", item_id, type(exc).__name__)
            marked = False

        def change(items: dict[str, dict[str, Any]]) -> None:
            current = items.get(str(item_id))
            if current is None:
                return
            if marked:
                current["source_marked"] = True
            else:
                current.pop("source_marked", None)
        await self._ledger(change)

    def _mark_reminder(self, item_id: str, ref: Any) -> bool:
        """mark_reminder_delivered(id) marks the first pending reminder with that id in reminders.json order, and two
        reminders set for the same second share one (rem_<epoch>) while their 'time' strings may still differ (a
        relative or 'kal' reminder keeps its microseconds), so list_due_reminders, sorted by that string, may list
        them in another order. So the mark is made only while this item is the first pending reminder with its id
        in file order (ReminderSource.pending_rows); else False: that earlier one is delivered and marked first, and
        this mark is tried again on a later tick. True without a call when nothing of this item is pending any more
        (cancelled, or taken by the PC brain's loop). Runs in a worker thread."""
        same: list[str] = []
        for row in self.reminders.pending_rows():
            if isinstance(row, Mapping) and row.get("id") == ref:
                item = reminder_item(row)
                if item is not None:
                    same.append(item.id)
        if item_id not in same:
            return True
        if same[0] != item_id:
            return False
        self.reminders.mark_delivered(ref)
        return True

    async def announce_system(self, key: str, text: str, kind: str = KIND_TASK) -> None:
        """Delivers a fixed line of the svc itself (a scheduler failure, C-80 results) through the ladder; its text is
        kept in the ledger for the retries."""
        item = DueItem(id=key, kind=kind if kind in (KIND_REMINDER, KIND_TASK) else KIND_TASK, ref=None,
                       text=clean_text(text), due_ts=self.clock(), synthetic=True)
        await self.process_due(item)

    # --- retries -----------------------------------------------------------------------------------------------

    async def current_items(self) -> dict[str, DueItem]:
        """Every item that may still be delivered, by key, from the sources (and the ledger's synthetic items)."""
        found: dict[str, DueItem] = {}
        try:
            for reminder in await asyncio.to_thread(self.reminders.list_due):
                item = reminder_item(reminder) if isinstance(reminder, Mapping) else None
                if item is not None:
                    found[item.id] = item
        except (TimeoutError, OSError, ValueError) as exc:
            logger.debug("reminders not read (%s)", type(exc).__name__)
        try:
            for row in await asyncio.to_thread(self.tasks.triggered):
                item = task_item(row) if isinstance(row, Mapping) else None
                if item is not None:
                    found[item.id] = item
        except (sqlite3.Error, OSError) as exc:
            logger.debug("tasks not read (%s)", type(exc).__name__)
        for key, entry in (await self._ledger_read()).items():
            if entry.get("synthetic") and isinstance(entry.get("text"), str):
                found[key] = DueItem(id=key, kind=str(entry.get("kind") or KIND_TASK), ref=None, text=entry["text"],
                                     due_ts=_number(entry.get("due_ts")), synthetic=True)
        return found

    async def retry(self, trigger: str, only: set[str] | None = None) -> int:
        """trigger 'node' (a laptop hello): step 2 for every open item (one stored at the bridge is asked there
        first, _step_laptop); 'ui' (a UI connect): step 3 again for every open item already stored at the bridge,
        except one a speak of unknown outcome holds (_speak_pending: the dashboard would show it while the laptop
        may have spoken it). only limits it to those keys. Items held at either step are tried again later, again
        while they stay held (_retry_later). Returns how many items it tried."""
        tried, held = await self._retry(trigger, only)
        if held and not self.stopping:
            self._retry_later(trigger, held)
        return tried

    async def _retry(self, trigger: str, only: set[str] | None, *, again: bool = False) -> tuple[int, set[str]]:
        """One retry pass (see retry): how many items it tried, and the keys it held."""
        held: set[str] = set()
        async with self._lock:
            entries = {key: entry for key, entry in (await self._ledger_read()).items()
                       if _is_open(entry) and (only is None or key in only)}
            if trigger == "ui":
                entries = {key: entry for key, entry in entries.items() if entry.get("notified")}
            if not entries:
                return 0, held
            current = await self.current_items()
            tried = 0
            for key, entry in entries.items():
                item = current.get(key)
                if item is None or self.stopping:
                    continue
                tried += 1
                if trigger == "node":
                    await self._step_laptop(item, held)
                elif self._speak_pending(entry):
                    self._hold(item, held)  # no reminder_due while a speak of it may still be acknowledged
                else:
                    await self._step_notify(item)
            if tried:
                where = f"of held {trigger} items" if again else f"on {trigger} connect"
                logger.info("DELIVERY retry %s: %d open item(s)%s", where, tried, f", {len(held)} held" if held else "")
        return tried, held

    def _held_wait_s(self) -> float:
        return max(self.timers.late_wait_s, self.timers.ui_ack_grace_s) + self.timers.poll_s

    def _held_rounds(self) -> int:
        """The tries of a waiting retry: enough rounds of _held_wait_s to outlast the longest hold, a speak a restart
        cut (restart_hold_s) plus late_wait_s; a bridge that stays unreadable longer leaves the item to the next
        connect."""
        wait = max(self._held_wait_s(), 0.001)
        return max(2, math.ceil((self.timers.restart_hold_s + self.timers.late_wait_s) / wait) + 1)

    def _retry_later(self, trigger: str, keys: set[str]) -> None:
        """Items held at a connect's retry or in a ladder run (trigger 'node': step 2; 'ui': step 3) are tried again
        after _held_wait_s, and again after each wait while they stay held, at most _held_rounds() times in a row;
        every new hold gives them all their tries again. One waiting retry per item and trigger."""
        start: set[str] = set()
        for key in keys:
            slot = (trigger, key)
            if slot not in self._held_left:
                start.add(key)
            self._held_left[slot] = self._held_rounds()
        if start and not self.stopping:
            self._spawn(self._retry_held(start, trigger))

    async def _retry_held(self, keys: set[str], trigger: str = "node") -> None:
        """The waiting retry of _retry_later: once the late outcome, or the ack of the dashboard that just got
        reminder_due, had its chance (an item acknowledged or delivered meanwhile is no longer open), the step runs
        again; an item held again waits once more while it has tries left."""
        waiting = set(keys)
        try:
            while waiting and not self.stopping:
                await self.sleep(self._held_wait_s())
                if self.stopping:
                    break
                for key in waiting:
                    slot = (trigger, key)
                    self._held_left[slot] = self._held_left.get(slot, 1) - 1
                held = (await self._retry(trigger, waiting, again=True))[1]
                still = {key for key in waiting if key in held and self._held_left.get((trigger, key), 0) > 0}
                for key in waiting - still:
                    self._held_left.pop((trigger, key), None)
                    if key in held:
                        logger.info("DELIVERY %s still held after its retries: the next %s connect tries it", key,
                                    trigger)
                waiting = still
        finally:
            for key in waiting:
                self._held_left.pop((trigger, key), None)

    def on_node_session(self, ident: Any = None) -> None:
        """DesktopLink 'session': a node hello (its ident) or a dropped link (None). The open items go to the laptop
        (step 2); a hello also starts the late_wait_s in which a speak of unknown outcome waits for its late result
        (the hello's result_resend is handled after this callback)."""
        if self.stopping:
            return
        if ident is not None:
            self._node_session_ts = self.clock()
        self._spawn(self.retry("node"))

    def on_late_outcome(self, late: Any) -> None:
        """DesktopLink 'late': the real outcome of a pc.speak_local the svc no longer waited for: one answered
        unknown (label svc_item), or one the process before a restart sent (reason 'restart', no label: matched by
        its call id in the ledger). A success is the item's ack (channel 'laptop'); any other end is recorded and
        lets step 2 speak the item again."""
        label = getattr(late, "label", None)
        item_id = label.get("svc_item") if isinstance(label, Mapping) else None
        item_id = item_id if isinstance(item_id, str) and item_id else None
        call_id = getattr(late, "call_id", None)
        call_id = call_id if isinstance(call_id, str) and call_id else None
        if item_id is None and (call_id is None or getattr(late, "reason", None) != "restart"):
            return
        state = getattr(late, "state", None)
        result = getattr(late, "result", None)
        acked = state == "result" and isinstance(result, Mapping) and result.get("status") in ("success", "ok")
        self._spawn(self._late_outcome(item_id, call_id, acked, clean_text(state, 20) or "unknown"))

    async def _late_outcome(self, item_id: str | None, call_id: str | None, acked: bool, state: str) -> None:
        async with self._lock:
            if item_id is None:
                item_id = _item_of_call(await self._ledger_read(), call_id)
                if item_id is None:
                    return  # no speak of this ledger (a scheduled action's result, or an entry already dropped)
            if acked:
                if await self._delivered(item_id, CH_LAPTOP):
                    await self._attempt(item_id, CH_LAPTOP, "acked_late", ref=call_id)
                return
            entry = (await self._ledger_read()).get(item_id)
            if entry is not None and _is_open(entry):
                await self._attempt(item_id, CH_LAPTOP, f"late_{state}", ref=call_id, release="laptop_call")

    # --- ticks -------------------------------------------------------------------------------------------------

    async def reminder_tick(self) -> None:
        """Every 30 s: due reminders (Cursor's list_due_reminders, under its file lock), then the svc's own open
        synthetic items, the reminder entries that are gone from the source, and the optional e-mails."""
        try:
            due = await asyncio.to_thread(self.reminders.list_due)
        except TimeoutError:
            logger.debug("reminders.json is busy; next tick")
            return
        items = [item for item in (reminder_item(r) for r in due if isinstance(r, Mapping)) if item is not None]
        self._due_counts[KIND_REMINDER] = len(items)
        for item in items:
            if self.stopping:
                return
            await self.process_due(item)
        for key, entry in (await self._ledger_read()).items():
            waiting = not entry.get("notified") or entry.get("notify_unknown")
            if entry.get("synthetic") and _is_open(entry) and waiting and not self.stopping:
                await self.process_due(DueItem(id=key, kind=str(entry.get("kind") or KIND_TASK), ref=None,
                                               text=str(entry.get("text") or ""), due_ts=_number(entry.get("due_ts")),
                                               synthetic=True))
        pending = await self._pending_reminder_keys()
        if pending is not None:
            await self._close_missing(KIND_REMINDER, pending | {item.id for item in items})
        await self.email_tick()

    async def _pending_reminder_keys(self) -> set[str] | None:
        """The ledger keys of every reminder still pending in reminders.json, due or not (ReminderSource.pending_rows),
        or None when the file could not be read: a reminder that is not due at this moment (its time ahead after the
        clock stepped back) has not left its source, so its entry is never closed as gone for that."""
        try:
            rows = await asyncio.to_thread(self.reminders.pending_rows)
        except (TimeoutError, OSError, ValueError) as exc:
            logger.debug("pending reminders not read (%s); no entry closed this tick", type(exc).__name__)
            return None
        return {item.id for item in (reminder_item(row) for row in rows if isinstance(row, Mapping))
                if item is not None}

    async def task_tick(self) -> None:
        """Every 45 s: tasks.db rows that fell due (get_due_tasks marks them triggered) and rows left triggered (a
        restart); each is delivered, then completed (the C-56 rule)."""
        claimed = await asyncio.to_thread(self.tasks.claim_due)
        triggered = await asyncio.to_thread(self.tasks.triggered)
        rows: dict[Any, Mapping[str, Any]] = {}
        for row in [*triggered, *claimed]:
            if isinstance(row, Mapping):
                rows.setdefault(row.get("id"), row)
        items = [(item, row) for item, row in ((task_item(row), row) for row in rows.values()) if item is not None]
        self._due_counts[KIND_TASK] = len(items)
        for item, row in items:
            if self.stopping:
                return
            if item.action and self.action_runner is not None:
                await self._run_action(item, row)
            else:
                await self.process_due(item)
        await self._close_missing(KIND_TASK, {item.id for item, _row in items})

    async def _run_action(self, item: DueItem, row: Mapping[str, Any]) -> None:
        """C-80: the runner owns the row (status and announcement); it starts once per row."""
        now = self.clock()

        def claim(items: dict[str, dict[str, Any]]) -> bool:
            entry = items.setdefault(item.id, {"id": item.id, "kind": item.kind, "ref": item.ref, "due_at": item.due_at,
                                               "due_ts": item.due_ts, "first_seen": round(now, 3), "attempts": []})
            if entry.get("action_started") or not _is_open(entry):
                return False
            entry.update(action_started=round(now, 3), closed_at=round(now, 3), close_reason="action")
            return True
        runner = self.action_runner
        if runner is None or not await self._ledger(claim):
            return
        logger.info("SCHEDULER task %s: action handed to the K-32 runner", item.ref)
        try:
            await runner(dict(row))  # pylint: disable=not-callable  # None was refused just above
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("SCHEDULER task %s: runner failed (%s)", item.ref, type(exc).__name__)

    async def _close_missing(self, kind: str, present: set[str]) -> None:
        """Open entries of kind whose item left the source (cancelled, or delivered by the PC brain after a switch)
        are closed; a stored notification of theirs is acknowledged by 'svc'. present: the items still in the source
        (every pending reminder, due or not; the triggered tasks). Finished entries whose source is marked are dropped
        after LEDGER_KEEP_S, but never one whose item is still in its source (present): a new entry would deliver it
        again (process_due marks such a source again instead)."""
        now = self.clock()

        def change(items: dict[str, dict[str, Any]]) -> list[str]:
            closed = []
            for key, entry in list(items.items()):
                if entry.get("kind") != kind or key in present:
                    continue
                if _is_open(entry) and not entry.get("synthetic"):
                    entry.update(closed_at=round(now, 3), close_reason="gone")
                    if entry.get("notified"):
                        entry["bridge_ack"] = True
                    closed.append(key)
                    continue
                done_at = _number(entry.get("delivered_at")) or _number(entry.get("closed_at"))
                finished = entry.get("source_marked") or entry.get("closed_at") or entry.get("synthetic")
                if done_at and finished and not entry.get("bridge_ack") and now - done_at > LEDGER_KEEP_S:
                    del items[key]
            return closed
        closed = await self._ledger(change)
        for key in closed:
            logger.info("DELIVERY %s closed: no longer due in its source", key)

    async def watch_tick(self) -> None:
        """Every 5 s while anything is open or waits for a bridge ack: one svc_poll. Acks of stored notifications
        deliver their items (channel 'ui'); a UI connect since the last poll retries step 3."""
        entries = await self._ledger_read()
        open_entries = {key: entry for key, entry in entries.items() if _is_open(entry)}
        self._undelivered = len(open_entries)
        pending = [key for key, entry in open_entries.items() if entry.get("notified")][:IDS_PER_POLL]
        acks_out = [key for key, entry in entries.items() if entry.get("bridge_ack")][:IDS_PER_POLL]
        if not open_entries and not acks_out:
            return
        answer = await self.bridge.poll(pending, acks_out)
        if answer is None:
            return
        if acks_out:
            await self._ledger(_clear_bridge_acks(acks_out))
        acked = [key for key in pending if key in answer.acks]
        if acked:
            async with self._lock:
                for key in acked:
                    await self._delivered(key, CH_UI)
        waiting = any(entry.get("notified") for key, entry in open_entries.items() if key not in acked)
        if self._ui_connected(answer) and waiting:
            await self.retry("ui")

    def _ui_connected(self, answer: PollAnswer) -> bool:
        """True when a UI connected since the last answer (ui_connects moved, or the first UI socket appeared); the
        first answer, and the first one from a restarted bridge (its boot id changed, and its ui_connects counter
        started again from 0, so the counts alone could read as before), count when a UI is connected right now.
        Every answer (watch or heartbeat) moves the baseline."""
        seen = self._ui_seen
        self._ui_seen = (answer.boot, answer.ui_connects, answer.ui_clients)
        if seen is None or answer.boot != seen[0]:
            return answer.ui_clients > 0
        return answer.ui_connects != seen[1] or (seen[2] == 0 and answer.ui_clients > 0)

    async def email_tick(self) -> None:
        """Step 4 (JARVIS_REMINDER_EMAIL_AFTER_MIN > 0): one owner e-mail for an item still open that long after its
        due time, whether or not step 3 reached the bridge (a bridge that is down is when it helps most); the item
        stays open (an e-mail is no ack). emailed_at is written before the send (_email_one), so no e-mail goes while
        the ledger cannot record it, and none goes twice."""
        if self.email_after_min <= 0 or self.email is None:
            return
        now = self.clock()
        due_for_mail = []
        for key, entry in (await self._ledger_read()).items():
            waiting = _is_open(entry) and not entry.get("emailed_at")
            due_ts = _number(entry.get("due_ts")) or _number(entry.get("first_seen"))
            late = due_ts is not None and now - due_ts >= self.email_after_min * 60
            if waiting and late and len(_attempts(entry, "email")) < EMAIL_ATTEMPTS_MAX:
                due_for_mail.append(key)
        if not due_for_mail:
            return
        if not self.email.configured():
            if "email" not in self._warned:
                self._warned.add("email")
                logger.warning("DELIVERY e-mail step is on but EMAIL_USER or EMAIL_APP_PASSWORD is not set")
            return
        current = await self.current_items()
        for key in due_for_mail:
            item = current.get(key)
            if item is None or self.stopping:
                continue
            if not await self._email_one(item):
                return  # the ledger cannot be written (a full disk): no e-mail until it can

    async def _email_one(self, item: DueItem) -> bool:
        """One e-mail for item, claimed in the ledger first (emailed_at): a claim that cannot be written sends
        nothing (returns False), and a result that cannot be written leaves the claim, so the next tick never sends
        it again (a crash mid-send too). Only a send that surely did not reach the server (EMAIL_FAILED: before DATA,
        or refused) clears the claim for another try; one it took (EMAIL_SENT) or may have taken (EMAIL_UNKNOWN: the
        connection broke during DATA) keeps it."""
        key = item.id
        claimed_at = round(self.clock(), 3)

        def claim(items: dict[str, dict[str, Any]]) -> bool:
            entry = items.get(key)
            if entry is None or not _is_open(entry) or entry.get("emailed_at"):
                return False
            entry["emailed_at"] = claimed_at
            return True
        try:
            if not await self._ledger(claim):
                return True
        except (OSError, TimeoutError) as exc:
            logger.warning("DELIVERY %s e-mail not sent: the ledger cannot record it (%s)", key, type(exc).__name__)
            return False
        label = "Reminder" if item.kind == KIND_REMINDER else "Scheduled task"
        body = f"{label}: {item.text}\n" + (f"Due: {item.due_at}\n" if item.due_at else "") + \
            "Abhi tak kisi screen ya laptop par deliver nahi hua. (JARVIS)"
        outcome = _email_outcome(await asyncio.to_thread(self.email.send, f"JARVIS {label.lower()}", body))
        now = self.clock()

        def record(items: dict[str, dict[str, Any]]) -> None:
            entry = items.get(key)
            if entry is not None:
                _add_attempt(entry, "email", outcome, now)
                if outcome == EMAIL_FAILED:
                    entry.pop("emailed_at", None)
        try:
            await self._ledger(record)
        except (OSError, TimeoutError) as exc:
            logger.warning("DELIVERY %s e-mail %s but not recorded (%s); it is not sent again", key, outcome,
                           type(exc).__name__)
            return False
        logger.info("DELIVERY %s email: %s", key, outcome)
        return True

    async def heartbeat_tick(self) -> None:
        """Every 30 s: svc_heartbeat with the K-68/K-77 counts (never text). Its answer also counts for the UI-connect
        retry, so the baseline is never stale when the watch tick starts polling again."""
        answer = await self.bridge.heartbeat(self.heartbeat_payload())
        if answer is not None and self._ui_connected(answer):
            entries = await self._ledger_read()
            if any(_is_open(entry) and entry.get("notified") for entry in entries.values()):
                await self.retry("ui")

    def heartbeat_payload(self) -> dict[str, Any]:
        return {"svc": STATE_ACTIVE if self.state == STATE_ACTIVE else self.state, "brain": self.brain,
                "due_pending": sum(self._due_counts.values()), "due_reminders": self._due_counts[KIND_REMINDER],
                "due_tasks": self._due_counts[KIND_TASK], "undelivered": self._undelivered,
                "last_snapshot_ts": self.last_snapshot_ts, "pid": os.getpid(), "since": round(self._started, 3),
                "git_sha": GIT_SHA, "link": {"online": bool(self.laptop is not None and self.laptop.online())},
                "ts": round(self.clock(), 3)}

    # --- snapshot ----------------------------------------------------------------------------------------------

    def snapshot_delay(self, now: float | None = None) -> float:
        """Seconds until the next snapshot: 03:30 PKT, or snapshot_catchup_s after a start that finds no snapshot of
        the last 26 h once today's 03:30 has passed."""
        now = self.clock() if now is None else now
        next_run = next_local_time(now, *SNAPSHOT_AT)
        if self._snapshot_first:
            self._snapshot_first = False
            last = self.last_snapshot_ts
            if (last is None or now - last >= SNAPSHOT_CATCHUP_AGE_S) and next_run - now > self.timers.snapshot_catchup_s:
                return self.timers.snapshot_catchup_s
        return max(0.0, next_run - now)

    async def _snapshot_cycle(self) -> None:
        if not self.snapshots.enabled:
            logger.warning("SNAPSHOT off: %s (no daily snapshot until it is set)", self.snapshots.problem)
            self.next_snapshot_ts = None
            await self.sleep(365 * 86400.0)
            return
        delay = self.snapshot_delay()
        self.next_snapshot_ts = round(self.clock() + delay, 3)
        await self.sleep(delay)
        if not self.stopping:
            await self.run_snapshot()

    async def run_snapshot(self) -> dict[str, Any] | None:
        self._snapshot_stop.clear()
        if self.stopping:
            return None
        try:
            info = await asyncio.to_thread(self.snapshots.write, self._snapshot_stop)
        except SnapshotAborted:
            logger.info("SNAPSHOT aborted: the service is stopping")
            return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("SNAPSHOT failed (%s)", type(exc).__name__)
            return None
        if info:
            self.last_snapshot_ts = info["ts"]
            logger.info("SNAPSHOT %s written (%d files, %d bytes)", info["name"], info["files"], info["size"])
        return info

    # --- node events -------------------------------------------------------------------------------------------

    def on_node_event(self, body: Any) -> None:
        """DesktopLink 'event' (decrypted, for the svc peer): location and log_batch; any other kind is ignored."""
        if not isinstance(body, Mapping) or self.stopping:
            return
        kind = body.get("kind")
        if kind == "location":
            self._spawn(self.cache_location(body))
        elif kind == "log_batch":
            self._spawn(self.relay_log_batch(body))
        else:
            logger.debug("node event %s ignored by the svc", clean_text(kind, 32) or "-")

    async def cache_location(self, body: Mapping[str, Any]) -> bool:
        """K-08/K-77: conversations/location.json (file-locked) and location_sync. Never logged."""
        city = body.get("city")
        lat, lng = _number(body.get("lat")), _number(body.get("lng"))
        now = self.clock()
        ts = _number(body.get("ts"))
        ts = ts if ts is not None and now - 3600 <= ts <= now + 120 else now
        ts = min(ts, now)
        if not isinstance(city, str) or lat is None or lng is None:
            logger.warning("LOCATION event refused: a field is missing or invalid")
            return False
        try:
            stored = await asyncio.to_thread(self.location_saver, city, lat, lng, ts=ts)
        except (ValueError, TypeError):
            logger.warning("LOCATION event refused: invalid values")
            return False
        except (OSError, TimeoutError) as exc:
            logger.warning("LOCATION cache not written (%s)", type(exc).__name__)
            return False
        logger.debug("LOCATION cached")
        await self.bridge.location_sync({"city": stored["city"], "lat": stored["lat"], "lng": stored["lng"],
                                         "kind": "device", "source": "device", "ts": stored["ts"]})
        return True

    async def relay_log_batch(self, body: Mapping[str, Any]) -> int:
        """K-77/K-29: the node's log lines, redacted, as vortex_log_batch with source 'desktop'. Returns the count."""
        from services.utils.jarvis_security import redact_secrets  # pylint: disable=import-outside-toplevel
        raw_items = body.get("items")
        items: list[dict[str, Any]] = []
        dropped = body.get("dropped")
        dropped = dropped if isinstance(dropped, int) and not isinstance(dropped, bool) and dropped > 0 else 0
        for raw in raw_items if isinstance(raw_items, list) else []:
            if len(items) >= 50 or not isinstance(raw, Mapping):
                dropped += 1
                continue
            text = redact_secrets(clean_text(raw.get("text"), 2000))
            text = _MAIL.sub("***@***", _USER_PATH.sub(r"\1:\\Users\\***", text))
            level = raw.get("level") if isinstance(raw.get("level"), str) else ""
            row: dict[str, Any] = {"text": text,
                                   "category": clean_text(redact_secrets(str(raw.get("category") or "DESKTOP")), 40)
                                   or "DESKTOP",
                                   "level": level.upper() if level.upper() in _LOG_LEVELS else "INFO"}
            ts = _number(raw.get("ts"))
            if ts is not None:
                row["ts"] = ts
            items.append(row)
        if items or dropped:
            await self.bridge.log_batch(items, dropped)
        return len(items)


# ---------------------------------------------------------------- process entry


def install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: Callable[[], None]) -> Callable[[], None]:
    """SIGTERM and SIGINT call stop on the loop (the loop's own handlers on POSIX, signal.signal elsewhere). Returns
    the function that restores the previous handlers."""
    signals = [sig for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)) if sig is not None]
    try:
        for sig in signals:
            loop.add_signal_handler(sig, stop)

        def restore_loop() -> None:
            for sig in signals:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)
        return restore_loop
    except (NotImplementedError, RuntimeError, ValueError):
        previous = {}
        for sig in signals:
            previous[sig] = signal.signal(sig, lambda _signum, _frame: loop.call_soon_threadsafe(stop))

        def restore_signal() -> None:
            for sig, handler in previous.items():
                with contextlib.suppress(Exception):
                    signal.signal(sig, handler)
        return restore_signal


async def serve(service: AgentServices) -> int:
    loop = asyncio.get_running_loop()
    restore = install_signal_handlers(loop, service.request_stop)
    try:
        return await service.run()
    finally:
        restore()


def main(argv: list[str] | None = None) -> int:  # pylint: disable=unused-argument
    net_guard.harden_process()  # first: non-dumpable PID 1, child env allow-list, egress guard (K-72, C-124)
    from services.utils.jarvis_logger import redact_root_handlers  # pylint: disable=import-outside-toplevel
    redact_root_handlers()
    profile = runtime_profile()
    if profile.role != ROLE_SVC:
        logger.error("agent_services runs only with JARVIS_ROLE=svc (this process has role %s)", profile.role)
        return 2
    service = AgentServices.from_environment(profile)
    try:
        return asyncio.run(serve(service))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
