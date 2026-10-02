"""
# services/utils/jarvis_delivery.py
Delivery helper of the VPS brain (contracts K-72 and K-73; plan task C-113): how a tool opens a finished
file or a URL for Sir, wherever the agent runs.

- open_artifact(path): local mode (the PC brain) opens it with os.startfile, as before. Remote mode (the
  VPS brain) opens nothing on the VPS: a file in the media relay areas (config.shared_dir as 'shared' and
  config.outputs_dir as 'outputs', /app/Jarvis_Shared and /app/Jarvis_Outputs in the container) goes to
  the laptop as one fire-and-forget pc.fetch {area, relpath, sha256, size, open: true}, which the
  Desktop Link signs (K-70). The node copies the file from the relay, checks its sha256 and opens it.
  The relay serves no file modified less than 5 s ago (K-73), so a fresh file's pc.fetch follows from a
  thread once the file is that old; open_artifact itself returns at once.
- open_url(url): http and https only; the local browser, or pc.open_url on the laptop.

Every failure is a DeliveryError: an OSError whose str() is its K-12 code, so a tool's existing OSError
handling reports the truth. DESKTOP_OFFLINE: no laptop link. PATH_NOT_ALLOWED: outside the relay areas
or their allowed subtrees. DELIVERY_FAILED: no regular file, unreadable, or the browser or the link
refused it. BAD_ARGS: not an http(s) URL, or os.startfile options the laptop cannot honour.
DESKTOP_ONLY: local mode on a host without os.startfile.

The laptop link is the DesktopLink that C-119 registers with set_desktop_sender(); until then remote mode
is always offline. Importing this module starts nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from services.utils.jarvis_config import DESKTOP_MODE_REMOTE, config, desktop_mode
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-DELIVERY")

DESKTOP_OFFLINE = "DESKTOP_OFFLINE"
PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
DELIVERY_FAILED = "DELIVERY_FAILED"
BAD_ARGS = "BAD_ARGS"
DESKTOP_ONLY = "DESKTOP_ONLY"

OP_FETCH, OP_OPEN_URL = "pc.fetch", "pc.open_url"
AREA_SHARED, AREA_OUTPUTS = "shared", "outputs"
# K-73: what the media relay serves from each area. Outputs also serves its top-level *.zip files.
RELAY_SUBTREES = {AREA_SHARED: ("Generated_Images", "QR_Codes", "Downloads"),
                  AREA_OUTPUTS: ("Downloads", "leads")}
_EXCLUDED_NAMES = frozenset({"sim_lookup_history.jsonl"})
_EXCLUDED_SUFFIXES = (".part", ".ytdl", ".tmp")
MAX_URL_CHARS = 4096
# A bigger file opened from the event loop is hashed in a thread: the call returns at once and the
# send follows.
INLINE_HASH_MAX = 8 * 1024 * 1024
_CHUNK = 1024 * 1024
# K-73: the relay skips files modified less than RELAY_MIN_AGE_S ago, so the thread waits until the file
# is that old, plus a margin, and again (a few rounds at most) while it keeps changing. 0 sends at once.
RELAY_MIN_AGE_S = 5.0
_SETTLE_MARGIN_S = 0.5
_SETTLE_ROUNDS = 3


class DeliveryError(OSError):
    """A file or URL that could not be handed to Sir; str() is the K-12 code alone."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DesktopSender(Protocol):
    """
    What C-119's DesktopLink registers. online(): the laptop link is up (K-74 desktop_status.online).
    submit(op, args): sends one internal pc.* operation (a K-70 exec the link signs) without waiting for
    its result, and raises OSError (ideally a DeliveryError) when it cannot be sent. Both are called from
    any thread, the event loop's included, and must not block.
    """

    def online(self) -> bool:
        ...

    def submit(self, op: str, args: dict[str, Any]) -> None:
        ...


_sender: DesktopSender | None = None
_sender_lock = threading.Lock()


def set_desktop_sender(sender: DesktopSender | None) -> DesktopSender | None:
    """Registers this process's laptop link (None removes it); returns the previous one."""
    global _sender  # pylint: disable=global-statement
    with _sender_lock:
        previous, _sender = _sender, sender
    return previous


def desktop_sender() -> DesktopSender | None:
    return _sender


def desktop_online() -> bool:
    """True while a registered laptop link reports itself online; a link that fails to answer is offline."""
    sender = _sender
    if sender is None:
        return False
    try:
        return bool(sender.online())
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _remote(remote: bool | None) -> bool:
    return desktop_mode() == DESKTOP_MODE_REMOTE if remote is None else remote


def _online_sender() -> DesktopSender:
    sender = _sender
    if sender is None or not desktop_online():
        raise DeliveryError(DESKTOP_OFFLINE)
    return sender


def _submit(sender: DesktopSender, op: str, args: dict[str, Any]) -> None:
    """Hands one operation to the link; the link's own failures become DELIVERY_FAILED."""
    try:
        sender.submit(op, args)
    except OSError:
        raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        raise DeliveryError(DELIVERY_FAILED) from e


# ---------------------------------------------------------------- files


def _real_path(path: Any) -> str:
    try:
        return os.path.realpath(os.fsdecode(os.fspath(path)))
    except (TypeError, ValueError) as e:
        raise DeliveryError(BAD_ARGS) from e


def _relative_to(real: str, root: str) -> str | None:
    """real below root as a '/' path; None when it is root itself or outside it."""
    try:
        rel = os.path.relpath(real, root)
    except ValueError:  # another drive (Windows)
        return None
    if rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def _relay_allows(area: str, parts: list[str]) -> bool:
    if any(part in ("", os.curdir, os.pardir) or part.startswith(".") for part in parts):
        return False  # dotfiles too (K-73 exclusions)
    name = parts[-1]
    if name in _EXCLUDED_NAMES or name.lower().endswith(_EXCLUDED_SUFFIXES):
        return False
    if len(parts) == 1:
        return area == AREA_OUTPUTS and name.lower().endswith(".zip")
    return parts[0] in RELAY_SUBTREES[area]


def _relay_location(real: str) -> tuple[str, str]:
    for area, root in ((AREA_SHARED, config.shared_dir), (AREA_OUTPUTS, config.outputs_dir)):
        if not root:
            continue
        rel = _relative_to(real, os.path.realpath(root))
        if rel is not None and _relay_allows(area, rel.split("/")):
            return area, rel
    raise DeliveryError(PATH_NOT_ALLOWED)


def relay_path(path: Any) -> tuple[str, str]:
    """
    (area, relpath) of a file the laptop can fetch from the K-73 media relay (symlinks resolved).
    DeliveryError PATH_NOT_ALLOWED for anything else: outside both areas or their allowed subtrees, a
    dotfile, a partial download or the SIM lookup history.
    """
    return _relay_location(_real_path(path))


def _regular_file(path: str) -> os.stat_result:
    try:
        info = os.stat(path)
    except OSError as e:
        raise DeliveryError(DELIVERY_FAILED) from e
    if not stat.S_ISREG(info.st_mode):
        raise DeliveryError(DELIVERY_FAILED)
    return info


def _settle_wait(info: os.stat_result) -> float:
    """Seconds until the relay serves a file with this stat (K-73), none left when 0 or less; at most
    one full wait, for an mtime in the future."""
    if RELAY_MIN_AGE_S <= 0:
        return 0.0
    full = RELAY_MIN_AGE_S + _SETTLE_MARGIN_S
    return min(full, info.st_mtime + full - time.time())


def file_digest(path: str) -> tuple[str, int]:
    """(sha256 hex, size) of one regular file, read once; DeliveryError DELIVERY_FAILED otherwise."""
    _regular_file(path)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
    except OSError as e:
        raise DeliveryError(DELIVERY_FAILED) from e
    return digest.hexdigest(), size


def _send_fetch(sender: DesktopSender, real: str, area: str, relpath: str) -> None:
    sha256, size = file_digest(real)
    _submit(sender, OP_FETCH, {"area": area, "relpath": relpath, "sha256": sha256, "size": size,
                               "open": True})


def _send_fetch_later(real: str, area: str, relpath: str, wait: float) -> None:
    """
    The pc.fetch sent from a thread, once the relay serves the file, to the laptop link of that moment.
    open_artifact has returned by then, so a failure is only logged.
    """
    try:
        for _ in range(_SETTLE_ROUNDS):
            if wait <= 0:
                break
            time.sleep(wait)
            wait = _settle_wait(_regular_file(real))
        _send_fetch(_online_sender(), real, area, relpath)
    except OSError as e:
        logger.warning("pc.fetch of a %s file not sent: %s", area, e)


def in_event_loop() -> bool:
    """True on a thread whose asyncio event loop is running, where nothing may block."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _local_startfile(path: Any, operation: str | None) -> None:
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise DeliveryError(DESKTOP_ONLY)
    if operation is None:
        startfile(path)
    else:
        startfile(path, operation)


def open_artifact(path: Any, operation: str | None = None, *, remote: bool | None = None) -> None:
    """
    Opens a finished file for Sir, with os.startfile semantics: None once it is handed over, OSError
    otherwise. Local mode: os.startfile. Remote mode (remote defaults to JARVIS_DESKTOP_MODE=remote): a
    fire-and-forget pc.fetch with open, after the relay path and the link checks; DESKTOP_OFFLINE while
    the laptop is offline, so the tool reports the truth. A file younger than the relay serves (K-73),
    or a big one opened from the event loop, is sent from a thread and the call returns at once.
    """
    if not _remote(remote):
        _local_startfile(path, operation)
        return
    if operation not in (None, "open"):
        raise DeliveryError(BAD_ARGS)
    real = _real_path(path)
    area, relpath = _relay_location(real)
    info = _regular_file(real)
    sender = _online_sender()
    wait = _settle_wait(info)
    if wait <= 0 and (info.st_size <= INLINE_HASH_MAX or not in_event_loop()):
        _send_fetch(sender, real, area, relpath)
        return
    threading.Thread(target=_send_fetch_later, args=(real, area, relpath, wait),
                     name="jarvis-delivery", daemon=True).start()


# ---------------------------------------------------------------- URLs


def check_url(url: Any) -> str:
    """url when it is an absolute http(s) URL with a host, at most 4096 characters and without
    whitespace or control characters; DeliveryError BAD_ARGS otherwise."""
    if not isinstance(url, str) or not 0 < len(url) <= MAX_URL_CHARS:
        raise DeliveryError(BAD_ARGS)
    if any(char.isspace() or not char.isprintable() for char in url):
        raise DeliveryError(BAD_ARGS)
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError as e:
        raise DeliveryError(BAD_ARGS) from e
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise DeliveryError(BAD_ARGS)
    return url


def _local_browser_open(url: str) -> bool:
    import webbrowser  # pylint: disable=import-outside-toplevel
    return bool(webbrowser.open(url))


def open_url(url: Any, *, remote: bool | None = None) -> bool:
    """
    Opens an http(s) URL for Sir: the local browser on the PC brain, else pc.open_url on the laptop
    (fire-and-forget). True once it is handed over; DeliveryError BAD_ARGS for any other URL,
    DESKTOP_OFFLINE without the laptop link, DELIVERY_FAILED when the browser or the link refuses it.
    """
    url = check_url(url)
    if not _remote(remote):
        if not _local_browser_open(url):
            raise DeliveryError(DELIVERY_FAILED)
        return True
    _submit(_online_sender(), OP_OPEN_URL, {"url": url})
    return True
