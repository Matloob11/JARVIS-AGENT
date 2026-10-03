"""Desktop Node sensors (plan task C-122 step 2; contracts K-19, K-21, K-26, K-70, K-74, K-77).

Active run mode only (install(node) adds them as active-only node tasks, so --canary runs none):
- vitals every VITALS_S (10 s): a K-74 vitals_pc notice to the bridge with exactly cpu, ram, disk, disk_label,
  host, cores and ram_total_gb (battery, plugged and secs_left stay on the laptop). host is the node id and
  disk_label 'Disk <letter>', so neither can carry a path, a title, clipboard text or a location.
- active window every WINDOW_POLL_S (5 s): pushed only on a change, as an encrypted window_context event
  {title?, ts} to the owner-room job peer (link.set_current: sent again after each owner-room agent_hello). The
  title is cut to 200 characters, user paths, mail addresses and long numbers are masked, and a password manager or
  a sensitive-path title is sent as 'private window'. A title is never logged, at any level.
- clipboard: the K-19 ClipboardMonitor (services.automation.jarvis_clipboard, config.clipboard_helper_enabled); only
  its redacted excerpt (at most 200 characters), the query and the solution (plus id, source_url and ts) leave the
  laptop, as an encrypted clipboard_suggestion event to the owner-room job peer.
- location at start, every LOCATION_S (600 s), and again after each svc agent_hello (link.set_current): an
  encrypted location event {city, lat, lng, ts, source 'device'} to the svc peer; never a notice, never logged.
- power and lock: the link sends the K-74 power notice (PowerListener: suspend, resume, shutdown, logoff) and the
  policy's lock flag reaches the bridge as a desktop_state notice on every change; the sensors add nothing there,
  so nothing is sent twice.
Window and clipboard sensing pause while the desktop mode is off, the panic latch holds or Windows is locked:
the window sensor reads no title, and on entering the pause replaces the link's current window_context with a
title-less one (which clears the agent's cache, so no old title is re-sent after an agent_hello); the clipboard
monitor is stopped, so the clipboard is not read and nothing goes to the K-19 search, and on resume it starts
again with the clipboard as it is then counted as already seen.

Every event body and every notice is built through an exact field allow-list (build_event, vitals_notice): an
unknown, missing, null or mistyped field is refused with DesktopLinkError code BAD_ARGS and nothing is sent.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.utils import desktop_protocol as dp

logger = logging.getLogger("JARVIS-DESKTOP.sensors")

WINDOW_POLL_S = 5.0
VITALS_S = 10.0
LOCATION_S = 600.0
LOCATION_RETRY_S = 60.0
TITLE_MAX = 200
EXCERPT_MAX = 200
QUERY_MAX = 300
SOLUTION_MAX = 500
URL_MAX = 2048
CITY_MAX = 100
PRIVATE_TITLE = "private window"
IPINFO_URL = "https://ipinfo.io/json"
VITALS_FIELDS = tuple(dp.NOTICE_FIELDS["vitals_pc"])  # exactly the K-74 list
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}")   # disk_label: a plain label, never a path
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")      # host: the node id format
_SUGGESTION_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LONG_DIGITS_RE = re.compile(r"\d{7,}")
# a user path up to the next whitespace or quote, as K-19's redact: the folder names after the home are private too
_USER_PATH_RE = re.compile(r"(?i)(?:[A-Za-z]:[\\/]+(?:users|documents and settings)[\\/]+[^\s\"']+|"
                           r"%USERPROFILE%[^\s\"']*|/home/[^\s\"']+|/Users/[^\s\"']+)")


def _bad(reason: str = "format") -> dp.DesktopLinkError:
    return dp.DesktopLinkError(reason, code="BAD_ARGS")


# --- field allow-lists ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class EventField:
    required: bool
    check: Callable[[Any], bool]


def _text(limit: int, *, empty: bool = False, lines: bool = False) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        if not isinstance(value, str) or len(value) > limit or (not value and not empty):
            return False
        text = value.replace("\n", "").replace("\t", "") if lines else value
        return text.isprintable()
    return check


def _number(low: float, high: float) -> Callable[[Any], bool]:
    def check(value: Any) -> bool:
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                and low <= value <= high)
    return check


def _url(value: Any) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= URL_MAX and value.isprintable()
            and value.lower().startswith(("https://", "http://")) and not any(ch.isspace() for ch in value))


_TS = EventField(True, _number(1.0, dp.MAX_EPOCH_S))
EVENT_FIELDS: Mapping[str, Mapping[str, EventField]] = {
    # K-08/K-77: what agent-svc caches in conversations/location.json
    "location": {"city": EventField(True, _text(CITY_MAX)), "lat": EventField(True, _number(-90.0, 90.0)),
                 "lng": EventField(True, _number(-180.0, 180.0)), "ts": _TS,
                 "source": EventField(False, lambda value: value == "device")},
    # K-21 v4/v7: {title, ts}; an event without a title clears the agent's cache
    "window_context": {"title": EventField(False, _text(TITLE_MAX)), "ts": _TS},
    # K-19 v4: only these leave the laptop
    "clipboard_suggestion": {
        "id": EventField(True, lambda value: isinstance(value, str) and bool(_SUGGESTION_ID_RE.fullmatch(value))),
        "error_excerpt": EventField(True, _text(EXCERPT_MAX, empty=True, lines=True)),
        "query": EventField(True, _text(QUERY_MAX)),
        "solution": EventField(True, _text(SOLUTION_MAX, empty=True, lines=True)),
        "source_url": EventField(False, _url), "ts": _TS},
}


def build_event(kind: str, **fields: Any) -> dict[str, Any]:
    """An event body (without 'kind', which the link adds) with exactly the kind's fields; anything else raises
    DesktopLinkError code BAD_ARGS. Optional fields may be absent, never None."""
    spec = EVENT_FIELDS.get(kind)
    if spec is None:
        raise _bad("direction")
    unknown = set(fields) - set(spec)
    if unknown or "kind" in fields:
        raise _bad()
    for name, rule in spec.items():
        if name not in fields:
            if rule.required:
                raise _bad()
            continue
        if fields[name] is None or not rule.check(fields[name]):
            raise _bad()
    return dict(fields)


def vitals_notice(**fields: Any) -> dict[str, Any]:
    """The vitals_pc notice fields, exactly K-74's list and rules (the library's NOTICE_FIELDS), with host and
    disk_label held to plain labels (letters, digits, space, '.', '_', '-'): no path, title or other free text can
    ride on them. Anything else raises DesktopLinkError code BAD_ARGS."""
    spec = dp.NOTICE_FIELDS["vitals_pc"]
    if set(fields) - set(spec):
        raise _bad()
    for name, rule in spec.items():
        if name not in fields:
            if rule.required:
                raise _bad()
            continue
        value = fields[name]
        if value is None or not rule.check(value):
            raise _bad()
        if name == "host" and not _HOST_RE.fullmatch(value):
            raise _bad()
        if name == "disk_label" and not _LABEL_RE.fullmatch(value):
            raise _bad()
    return dict(fields)


# --- text hygiene ------------------------------------------------------------------------------------------------

def _printable(text: str, *, keep_lines: bool = False) -> str:
    out = []
    for ch in text:
        if keep_lines and ch in "\n\t":
            out.append(ch)
        elif ch.isprintable():
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out)


def mask_private(text: str) -> str:
    """User paths, mail addresses and long digit runs masked (the K-19 redaction, plus the user's home)."""
    if not text:
        return text
    out = _EMAIL_RE.sub("[email]", text)
    out = _LONG_DIGITS_RE.sub("[digits]", out)
    out = _USER_PATH_RE.sub("[user_path]", out)
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        home = ""
    if len(home) > 3:
        for variant in dict.fromkeys((home, home.replace("\\", "/"), home.replace("\\", "\\\\"))):
            out = re.sub(re.escape(variant) + r"[^\s\"']*", "[user_path]", out, flags=re.IGNORECASE)
    return out


def window_title_for_event(title: str, process: str = "") -> str:
    """The title as it may leave the laptop: '' for none, 'private window' for a password manager or a sensitive
    path, else masked, printable, whitespace collapsed and cut to TITLE_MAX."""
    from src.core.desktop.capabilities import sensitive_foreground  # pylint: disable=import-outside-toplevel

    if not isinstance(title, str) or not title.strip():
        return ""
    if sensitive_foreground(title, process or ""):
        return PRIVATE_TITLE
    clean = " ".join(_printable(mask_private(title)).split())
    return clean[:TITLE_MAX].rstrip()


# --- default readers -------------------------------------------------------------------------------------------

def _foreground() -> tuple[str, str]:
    from src.core.desktop.capabilities import _foreground as read  # pylint: disable=import-outside-toplevel

    return read()


def _system_disk_letter() -> str:
    drive = (os.environ.get("SystemDrive") or "C:").strip()
    letter = drive[:1].upper()
    return letter if letter.isalpha() else "C"


def collect_vitals() -> dict[str, Any]:
    """cpu, ram and disk (K-26 collect_pc_vitals when it loads, else psutil), cores and ram_total_gb."""
    import psutil  # pylint: disable=import-outside-toplevel

    try:
        from services.system.jarvis_system_info import (  # pylint: disable=import-outside-toplevel
            collect_pc_vitals,
        )
        base = collect_pc_vitals()
    except Exception:  # pylint: disable=broad-exception-caught  # the K-26 helper is optional here
        disk_path = f"{_system_disk_letter()}:\\" if sys.platform == "win32" else "/"
        base = {"cpu": float(psutil.cpu_percent(interval=0.3)), "ram": float(psutil.virtual_memory().percent),
                "disk": float(psutil.disk_usage(disk_path).percent)}
    return {"cpu": base.get("cpu"), "ram": base.get("ram"), "disk": base.get("disk"),
            "cores": psutil.cpu_count(logical=True), "ram_total_gb": psutil.virtual_memory().total / (1024 ** 3)}


def ipinfo_location() -> dict[str, Any] | None:
    """{city, lat, lng} of this laptop from ipinfo (the PC brain's source), else None. Never logs the answer."""
    import requests  # pylint: disable=import-outside-toplevel

    try:
        response = requests.get(IPINFO_URL, timeout=5)
        data = response.json()
        lat_text, _, lng_text = str(data.get("loc", "")).partition(",")
        city = data.get("city")
        if not isinstance(city, str) or not city.strip() or not lat_text or not lng_text:
            return None
        return {"city": city.strip(), "lat": float(lat_text), "lng": float(lng_text)}
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Location lookup failed (%s)", type(exc).__name__)
        return None


def _clipboard_monitor() -> Any:
    from services.automation.jarvis_clipboard import ClipboardMonitor  # pylint: disable=import-outside-toplevel

    return ClipboardMonitor()


# --- the sensors ---------------------------------------------------------------------------------------------

class Sensors:
    """The node's sensors (see the module docstring). Readers are injectable; tests use fakes only."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments

    def __init__(self, node: Any, *, foreground: Callable[[], tuple[str, str]] = _foreground,
                 vitals: Callable[[], Mapping[str, Any]] = collect_vitals,
                 locate: Callable[[], Mapping[str, Any] | None] = ipinfo_location,
                 clipboard_monitor: Callable[[], Any] = _clipboard_monitor,
                 clipboard_text: Callable[[], str] | None = None, wall: Callable[[], float] = time.time,
                 window_poll_s: float = WINDOW_POLL_S, vitals_s: float = VITALS_S, location_s: float = LOCATION_S,
                 location_retry_s: float = LOCATION_RETRY_S) -> None:
        self._node = node
        self._foreground, self._vitals, self._locate = foreground, vitals, locate
        self._clipboard_monitor = clipboard_monitor
        self._clipboard_text = clipboard_text or _read_clipboard
        self._wall = wall
        self.window_poll_s, self.vitals_s = window_poll_s, vitals_s
        self.location_s, self.location_retry_s = location_s, location_retry_s
        self._last_title: str | None = None
        self._window_cleared = True  # no window_context with a title is current on the link
        self.monitor: Any = None
        self.sent: dict[str, int] = {"window_context": 0, "clipboard_suggestion": 0, "location": 0, "vitals_pc": 0}

    @property
    def _link(self) -> Any:
        return self._node.link

    def private_paused(self) -> bool:
        """Window and clipboard sensing pause while the mode is off, the panic latch holds or Windows is locked."""
        try:
            state = self._node.state.current()
        except Exception:  # pylint: disable=broad-exception-caught  # unknown state: stay quiet
            return True
        return bool(state.panic or state.locked or state.mode == "off")

    def tasks(self) -> list[tuple[Callable[[], Awaitable[Any]], str]]:
        return [(self.window_loop, "sensor-window"), (self.vitals_loop, "sensor-vitals"),
                (self.location_loop, "sensor-location"), (self.clipboard_loop, "sensor-clipboard")]

    # --- active window ---------------------------------------------------------------------------------------

    async def window_once(self) -> bool:
        """One look at the foreground window; True when a changed window_context was handed to the link."""
        if self.private_paused():
            self._last_title = None  # after the pause the current window is sent again
            if not self._window_cleared:  # the last title must not stay current (re-sent after an agent_hello)
                self._window_cleared = True
                await self._link.set_current("window_context", build_event("window_context",
                                                                           ts=round(self._wall(), 3)))
                logger.debug("window_context cleared for a pause")
            return False
        try:
            title, process = await asyncio.to_thread(self._foreground)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Foreground window unreadable (%s)", type(exc).__name__)
            return False
        clean = window_title_for_event(title or "", process or "")
        if clean == self._last_title:
            return False
        body = build_event("window_context", **({"title": clean} if clean else {}), ts=round(self._wall(), 3))
        self._last_title = clean
        self._window_cleared = not clean
        await self._link.set_current("window_context", body)
        self.sent["window_context"] += 1
        logger.debug("window_context changed")
        return True

    async def window_loop(self) -> None:
        while True:
            try:
                await self.window_once()
            except dp.DesktopLinkError as exc:
                logger.warning("window_context not sent (%s)", exc.reason)
            await asyncio.sleep(self.window_poll_s)

    # --- vitals ----------------------------------------------------------------------------------------------

    def vitals_fields(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        """The K-74 vitals_pc fields from a reading: only cpu, ram, disk (0..100, one decimal), cores,
        ram_total_gb, host (the node id) and disk_label ('Disk <letter>'); everything else stays here."""
        fields: dict[str, Any] = {}
        for name in ("cpu", "ram", "disk"):
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _bad()
            fields[name] = round(min(100.0, max(0.0, float(value))), 1)
        cores = raw.get("cores")
        if isinstance(cores, int) and not isinstance(cores, bool) and 1 <= cores <= 4096:
            fields["cores"] = cores
        total = raw.get("ram_total_gb")
        if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) \
                and 0 <= total <= 65536:
            fields["ram_total_gb"] = round(float(total), 1)
        node_id = str(getattr(self._node, "node_id", "") or "")
        if _HOST_RE.fullmatch(node_id[:64] or "-"):
            fields["host"] = node_id[:64]
        fields["disk_label"] = f"Disk {_system_disk_letter()}"
        return vitals_notice(**fields)

    async def vitals_once(self) -> bool:
        if not getattr(self._link, "connected", False):
            return False
        raw = await asyncio.to_thread(self._vitals)
        fields = self.vitals_fields(raw)
        sent = bool(await self._link.send_notice("vitals_pc", **fields))
        if sent:
            self.sent["vitals_pc"] += 1
        return sent

    async def vitals_loop(self) -> None:
        while True:
            try:
                await self.vitals_once()
            except dp.DesktopLinkError as exc:
                logger.warning("vitals_pc not sent (%s)", exc.reason)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # psutil hiccup: next tick
                logger.warning("Vitals not read (%s)", type(exc).__name__)
            await asyncio.sleep(self.vitals_s)

    # --- location --------------------------------------------------------------------------------------------

    async def location_once(self) -> bool:
        """Looks the location up and hands it to the link (sent now to the svc peer, and again after each svc
        agent_hello). False when the lookup failed."""
        found = await asyncio.to_thread(self._locate)
        if not isinstance(found, Mapping):
            return False
        city = " ".join(_printable(str(found.get("city") or "")).split())[:CITY_MAX]
        try:
            body = build_event("location", city=city, lat=float(found.get("lat")), lng=float(found.get("lng")),
                               ts=round(self._wall(), 3), source="device")
        except (TypeError, ValueError):
            raise _bad() from None
        await self._link.set_current("location", body)
        self.sent["location"] += 1
        logger.debug("location updated")
        return True

    async def location_loop(self) -> None:
        while True:
            ok = False
            try:
                ok = await self.location_once()
            except dp.DesktopLinkError as exc:
                logger.warning("location not sent (%s)", exc.reason)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Location not read (%s)", type(exc).__name__)
            await asyncio.sleep(self.location_s if ok else self.location_retry_s)

    # --- clipboard -------------------------------------------------------------------------------------------

    def clipboard_event(self, payload: Any) -> dict[str, Any]:
        """The clipboard_suggestion body of a K-19 payload: only id, the redacted excerpt (<= 200 characters),
        the query, the solution, source_url and ts."""
        if not isinstance(payload, Mapping):
            raise _bad()
        excerpt = _printable(mask_private(str(payload.get("error_excerpt") or "")), keep_lines=True)[:EXCERPT_MAX]
        query = " ".join(_printable(mask_private(str(payload.get("query") or ""))).split())[:QUERY_MAX]
        solution = _printable(str(payload.get("solution") or ""), keep_lines=True)[:SOLUTION_MAX]
        fields: dict[str, Any] = {"id": str(payload.get("id") or "")[:64], "error_excerpt": excerpt,
                                  "query": query, "solution": solution, "ts": round(self._wall(), 3)}
        url = payload.get("source_url")
        if isinstance(url, str) and _url(url):
            fields["source_url"] = url
        return build_event("clipboard_suggestion", **fields)

    async def on_clipboard(self, payload: Any) -> None:
        """ClipboardMonitor callback: the suggestion goes to the owner-room job peer, or is dropped."""
        if self.private_paused():
            return
        try:
            body = self.clipboard_event(payload)
        except dp.DesktopLinkError as exc:
            logger.warning("clipboard_suggestion not sent (%s)", exc.reason)
            return
        if await self._link.send_event("clipboard_suggestion", body):
            self.sent["clipboard_suggestion"] += 1
        logger.debug("clipboard_suggestion handled")

    async def clipboard_loop(self) -> None:
        """Runs the K-19 monitor while sensing is not paused; a pause stops it (no clipboard read, no search),
        and the end of the pause starts a new one primed with the clipboard as it is then. Returns when the
        monitor cannot be made or ends by itself."""
        resumed = False
        while True:
            while self.private_paused():
                await asyncio.sleep(self.window_poll_s)
            try:
                monitor = self._clipboard_monitor()
            except Exception as exc:  # pylint: disable=broad-exception-caught  # pyperclip/ddgs missing: no sensor
                logger.warning("Clipboard sensor off (%s)", type(exc).__name__)
                return
            self.monitor = monitor
            if resumed:
                await self._prime(monitor)
            task = asyncio.ensure_future(monitor.start(self.on_clipboard))
            paused = False
            try:
                while not task.done():
                    if self.private_paused():
                        paused = True
                        break
                    await asyncio.wait({task}, timeout=self.window_poll_s)
            finally:
                _halt(monitor)
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if not paused:
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("Clipboard sensor stopped (%s)", type(task.exception()).__name__)
                return
            logger.debug("Clipboard sensor paused")
            resumed = True

    async def _prime(self, monitor: Any) -> None:
        """What is on the clipboard when a pause ends counts as seen, so text copied during the pause is never
        searched or sent."""
        try:
            monitor.last_content = await asyncio.to_thread(self._clipboard_text)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # unreadable: the monitor reads it
            logger.debug("Clipboard not primed (%s)", type(exc).__name__)


def _halt(monitor: Any) -> None:
    """Ends a ClipboardMonitor's loop (is_running False) and calls its stop() when it has one."""
    try:
        monitor.is_running = False
    except AttributeError:
        pass
    stop = getattr(monitor, "stop", None)
    if callable(stop):
        try:
            stop()  # pylint: disable=not-callable
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug("Clipboard monitor stop failed (%s)", type(exc).__name__)


def _read_clipboard() -> str:
    """The clipboard text, read as the K-19 monitor reads it."""
    from services.automation import jarvis_clipboard  # pylint: disable=import-outside-toplevel

    reader = getattr(jarvis_clipboard, "_read_clipboard_sync", None)
    text = reader() if callable(reader) else ""
    return text if isinstance(text, str) else ""


def install(node: Any) -> None:
    """NodeRuntime plugin hook: the sensors as active-only node tasks (never in --canary)."""
    sensors = Sensors(node)
    for factory, name in sensors.tasks():
        node.add_task(factory, active_only=True, name=name)
    node.sensors = sensors
    logger.info("Desktop sensors installed (window %.0f s, vitals %.0f s, location %.0f s, clipboard)",
                sensors.window_poll_s, sensors.vitals_s, sensors.location_s)
