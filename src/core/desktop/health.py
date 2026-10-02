"""Desktop Node health, status file, stop token and audit log (plan task C-120 steps 1 and 8; K-53 v4, K-76).

- The node writes run/desktop_node.json every STATUS_EVERY_S and on every change: link, mode, locked, panic,
  outbox, offset, last exec, git_sha and the like. Never arguments, results, paths, titles or keys.
- The supervisor serves 127.0.0.1:8082 (HealthServer): GET /health (its own state plus that file) and
  POST /stop with the token of run/desktop_node.token (header X-Node-Token), which only the current Windows
  user can read (user-only ACL, inheritance off), for 'agent_ctl node stop' (C-123). Requests whose Host is
  not the loopback address are refused (DNS rebinding).
- logs/desktop_audit.jsonl (5 MB x 10): one JSON line per audited node event. Only the AUDIT_FIELDS names are
  written, so no raw argument can reach it.
Standard library only (pywin32 for the ACL on Windows, loaded on use).
"""
from __future__ import annotations

import hmac
import http.server
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger("JARVIS-DESKTOP.health")

STATUS_FILE = "desktop_node.json"
TOKEN_FILE = "desktop_node.token"
TOKEN_HEADER = "X-Node-Token"
AUDIT_FILE = Path("logs") / "desktop_audit.jsonl"
STATUS_EVERY_S = 10.0
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 10
# Every name an audit line may carry: hashes and codes, never raw arguments or results (K-76).
AUDIT_FIELDS = frozenset({
    "call_id", "tool", "cls", "origin", "room", "peer", "code", "status", "effect", "duration_ms", "risk",
    "args_sha256", "approval_channel", "confirm_id", "seq_n", "reason", "mode", "count", "state", "source",
    "kind", "node_id", "boot_id", "in_order"})
_MAX_REQUEST_BYTES = 4096


# --- JSON files ----------------------------------------------------------------------------------------------

def write_json_atomic(path: Path, doc: Mapping[str, Any]) -> None:
    """tmp + os.replace; on Windows a reader holding the file open can make the rename fail, so that case
    writes in place (readers treat a half-written file as missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(doc), indent=2, sort_keys=True, ensure_ascii=False)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except PermissionError:
        try:
            path.write_text(text, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


class StatusFile:
    """run/desktop_node.json: the node's latest status (written by the node, read by the supervisor)."""

    def __init__(self, path: Path, *, wall: Callable[[], float] = time.time) -> None:
        self.path, self._wall = Path(path), wall

    def write(self, doc: Mapping[str, Any]) -> None:
        try:
            write_json_atomic(self.path, {**dict(doc), "updated_at": round(self._wall(), 3)})
        except OSError as exc:
            logger.warning("Status file not written (%s)", type(exc).__name__)

    def read(self) -> dict[str, Any] | None:
        return read_json(self.path)

    def age_s(self, doc: Mapping[str, Any] | None = None) -> float | None:
        doc = self.read() if doc is None else doc
        updated = (doc or {}).get("updated_at")
        return None if not isinstance(updated, (int, float)) else max(0.0, self._wall() - float(updated))


# --- audit ---------------------------------------------------------------------------------------------------

class AuditLog:
    """logs/desktop_audit.jsonl under the data root, rotated at AUDIT_MAX_BYTES with AUDIT_BACKUPS kept. On
    Windows a reader holding the file or a backup open makes the rotation fail: the line is still appended and
    the rotation is tried again after ROTATE_RETRY_S."""

    ROTATE_RETRY_S = 60.0

    def __init__(self, path: Path, *, max_bytes: int = AUDIT_MAX_BYTES, backups: int = AUDIT_BACKUPS,
                 wall: Callable[[], float] = time.time) -> None:
        self.path, self._max, self._backups, self._wall = Path(path), max_bytes, backups, wall
        self._lock = threading.Lock()
        self._rotate_after = 0.0  # time.monotonic() before which a failed rotation is not tried again
        self._rotation_failing = False

    def record(self, event: str, **fields: Any) -> None:
        line = {"ts": round(self._wall(), 3), "event": str(event)[:64]}
        line.update({name: value for name, value in fields.items() if name in AUDIT_FIELDS and value is not None})
        text = json.dumps(line, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(text) > self._max:
                    self._try_rotate()
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError as exc:
                logger.warning("Audit line not written (%s)", type(exc).__name__)

    def _try_rotate(self) -> None:
        if time.monotonic() < self._rotate_after:
            return
        try:
            self._rotate()
        except OSError as exc:
            self._rotate_after = time.monotonic() + self.ROTATE_RETRY_S
            if not self._rotation_failing:
                logger.warning("Audit log not rotated (%s); lines are still appended", type(exc).__name__)
            self._rotation_failing = True
            return
        self._rotation_failing = False

    def _rotate(self) -> None:
        for index in range(self._backups - 1, 0, -1):
            older = self.path.with_name(f"{self.path.name}.{index}")
            if older.exists():
                os.replace(older, self.path.with_name(f"{self.path.name}.{index + 1}"))
        os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))


# --- the stop token ------------------------------------------------------------------------------------------

def restrict_to_current_user(path: Path) -> None:
    """Gives only the current user access to path: a protected DACL with one ACE (Windows), or mode 600."""
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        return
    # pylint: disable=import-outside-toplevel,import-error,c-extension-no-member
    import ntsecuritycon
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid)
    win32security.SetNamedSecurityInfo(
        os.fspath(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, dacl, None)


def new_token_file(path: Path, *, restrict: Callable[[Path], None] = restrict_to_current_user) -> str:
    """A fresh random token in path, readable by the current user only (ACL set before the token is written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    restrict(path)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def read_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


# --- the health server ---------------------------------------------------------------------------------------

class HealthServer:
    """127.0.0.1:<port>: GET /health -> status(); POST /stop with the token -> stop() (after the reply)."""

    def __init__(self, *, host: str, port: int, status: Callable[[], Mapping[str, Any]],
                 stop: Callable[[], None], token: Callable[[], str | None]) -> None:
        self._host, self._port = host, port
        self.status_fn, self.stop_fn, self.token_fn = status, stop, token
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server is not None else self._port

    def start(self) -> int:
        """Binds and serves on a daemon thread; returns the port. OSError when the port is taken."""
        server = http.server.ThreadingHTTPServer((self._host, self._port), self._handler_class())
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                                        name="desktop-health", daemon=True)
        self._thread.start()
        return self.port

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(5.0)

    def _handler_class(self) -> type[http.server.BaseHTTPRequestHandler]:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = "JarvisDesktopNode/1"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
                return  # requests are not logged (the stop token travels in a header)

            def _reply(self, code: int, doc: Mapping[str, Any]) -> None:
                body = json.dumps(dict(doc), sort_keys=True, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _local_host(self) -> bool:
                host = (self.headers.get("Host") or "").strip().lower()
                port = outer.port
                return host in {"127.0.0.1", "localhost", f"127.0.0.1:{port}", f"localhost:{port}"}

            def do_GET(self) -> None:  # pylint: disable=invalid-name
                if not self._local_host():
                    self._reply(403, {"error": "forbidden"})
                elif self.path.split("?", 1)[0] == "/health":
                    try:
                        self._reply(200, outer.status_fn())
                    except Exception as exc:  # pylint: disable=broad-exception-caught  # health never 500s silently
                        self._reply(500, {"status": "error", "error": type(exc).__name__})
                else:
                    self._reply(404, {"error": "not found"})

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if 0 < length <= _MAX_REQUEST_BYTES:
                    self.rfile.read(length)
                if not self._local_host():
                    self._reply(403, {"error": "forbidden"})
                    return
                if self.path.split("?", 1)[0] != "/stop":
                    self._reply(404, {"error": "not found"})
                    return
                expected = outer.token_fn() or ""
                given = (self.headers.get(TOKEN_HEADER) or "").strip()
                if not expected or not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
                    self._reply(403, {"error": "bad token"})
                    return
                self._reply(200, {"status": "stopping"})
                threading.Thread(target=outer.stop_fn, name="desktop-stop", daemon=True).start()

        return Handler
