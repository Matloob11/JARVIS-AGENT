"""
# jarvis_file_server.py
Jarvis File Access Server (X-30)

Shares ONLY the configured root (config.file_server_root: JARVIS_FILE_SERVER_ROOT, else D:/ when
present, else the shared folder) with a device on the same LAN, behind a per-start random token,
a TTL auto-stop and an audit trail. Nothing is served without the token; traversal, dotted
segments and sensitive paths (K-25 is_sensitive_path) are always rejected.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.multimedia.jarvis_qr_gen import generate_qr_code
from services.system.jarvis_desktop_common import is_sensitive_path
from services.utils.jarvis_audit import JarvisAudit
from services.utils.jarvis_bridge import notify_event
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

# Setup logger
logger = setup_logger("JARVIS-FILE-SERVER")

ERROR_ROOT_MISSING = "FILE_SERVER_ROOT_MISSING"
ERROR_NO_LAN = "NO_LAN"
ERROR_BIND_FAILED = "BIND_FAILED"

# ---------------------------------------------------------------------------
# Module state (one server at a time). Guarded by _state_lock; _stop_server_sync clears it all.
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_server_timer: Optional[threading.Timer] = None
_server_token: Optional[str] = None
_server_url: Optional[str] = None
_server_masked_url: Optional[str] = None
_server_root: Optional[str] = None


def get_local_ip() -> str:
    """Retrieves the local LAN IP address of this PC (127.0.0.1 when there is no LAN route)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except (OSError, socket.herror, socket.gaierror) as e:
        logger.error("Could not determine local IP: %s", e)
        return "127.0.0.1"


def _resolve_root() -> tuple[Optional[Path], Optional[str]]:
    """
    X-30 step 1: config.file_server_root already applies JARVIS_FILE_SERVER_ROOT, else D:/ when it
    exists, else shared_dir. Returns (root, None) when it is a real, resolvable directory, else
    (None, ERROR_ROOT_MISSING).
    """
    raw = getattr(config, "file_server_root", None)
    if not raw:
        return None, ERROR_ROOT_MISSING
    candidate = Path(raw)
    if not candidate.is_dir():
        return None, ERROR_ROOT_MISSING
    try:
        return candidate.resolve(strict=True), None
    except (OSError, RuntimeError, ValueError):
        return None, ERROR_ROOT_MISSING


def _ttl_seconds() -> float:
    """config.file_server_ttl_min in seconds; tests patch this directly for a fast TTL."""
    try:
        minutes = float(getattr(config, "file_server_ttl_min", 30))
    except (TypeError, ValueError):
        minutes = 30.0
    return max(0.0, minutes) * 60.0


def _confirm_summary_text(_arguments: dict[str, Any]) -> str:
    """X-30 step 8: '<root> folder phone ke liye LAN par <ttl> minute share karna hai (token link)'."""
    root, error_code = _resolve_root()
    root_display = str(root) if root and not error_code else "configured"
    ttl_min = getattr(config, "file_server_ttl_min", 30)
    return f"{root_display} folder phone ke liye LAN par {ttl_min} minute share karna hai (token link)"


# ---------------------------------------------------------------------------
# Handler (X-30 step 3-4): GET/HEAD only, token-gated, root-contained, no dotfile/sensitive path.
# ---------------------------------------------------------------------------


class DDriveHandler(SimpleHTTPRequestHandler):
    """
    Token-gated, read-only (GET/HEAD) handler for the file access server.

    _make_handler_class returns a fresh subclass of this with _jarvis_root/_jarvis_token bound to
    one server start; the bare class (no root/token) is kept importable under this name for
    tests/infrastructure/test_file_server.py (K-contract: 'keep DDriveHandler as an alias').
    """

    _jarvis_root: Optional[Path] = None
    _jarvis_token: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if self._jarvis_root is not None:
            super().__init__(*args, directory=str(self._jarvis_root), **kwargs)
        else:
            super().__init__(*args, **kwargs)

    # --- token + traversal guard -------------------------------------------------

    def _token_prefix(self) -> str:
        return "/" + (self._jarvis_token or "")

    def _has_token(self) -> bool:
        if not self._jarvis_token:
            return False
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        prefix = self._token_prefix()
        return raw == prefix or raw.startswith(prefix + "/")

    def _strip_token(self, raw_path: Optional[str] = None) -> str:
        """The request path without the leading '/<token>' (unchanged when it has no token)."""
        raw = (raw_path if raw_path is not None else self.path).split("?", 1)[0].split("#", 1)[0]
        prefix = self._token_prefix()
        if raw == prefix:
            return "/"
        if raw.startswith(prefix + "/"):
            rest = raw[len(prefix):]
            return rest or "/"
        return raw

    def _safe_resolve(self, sub_path: str) -> Optional[Path]:
        """The real filesystem path for sub_path (already without the token), or None when it
        escapes the root, has a '..' or dotted segment, or is_sensitive_path matches."""
        if self._jarvis_root is None:
            return None
        decoded = urllib.parse.unquote(sub_path.split("?", 1)[0].split("#", 1)[0])
        segments = [s for s in decoded.replace("\\", "/").split("/") if s not in ("", ".")]
        if any(seg == ".." or seg.startswith(".") for seg in segments):
            return None
        candidate = self._jarvis_root
        for seg in segments:
            candidate = candidate / seg
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return None
        try:
            resolved.relative_to(self._jarvis_root)
        except ValueError:
            return None
        if is_sensitive_path(resolved):
            return None
        return resolved

    def translate_path(self, path: str) -> str:
        """X-30 step 3: strips the token prefix (already done by _serve for a real request),
        resolves under the root, and refuses to leave it, a dotted segment or a sensitive path."""
        resolved = self._safe_resolve(path)
        if resolved is None:
            logger.warning("PATH ESCAPE ATTEMPT BLOCKED: %s", path)
            return str(self._jarvis_root) if self._jarvis_root is not None else path
        return str(resolved)

    # --- verbs --------------------------------------------------------------------

    def _serve(self, *, head: bool) -> None:
        if not self._has_token():
            self.send_error(403)
            return
        sub = self._strip_token()
        if self._safe_resolve(sub) is None:
            self.send_error(403)
            return
        self.path = sub
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler method name)
        self._serve(head=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head=True)

    def do_POST(self) -> None:  # noqa: N802
        self.send_error(403)

    do_PUT = do_POST  # noqa: N815
    do_DELETE = do_POST  # noqa: N815

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
        """X-30 step 4: the logger, plus one FILE_SERVER_ACCESS audit line per request."""
        try:
            rendered = format % args
        except (TypeError, ValueError):
            rendered = format
        logger.info("Server Request: %s", rendered)
        try:
            client_ip = self.client_address[0] if getattr(self, "client_address", None) else "?"
        except (AttributeError, IndexError, TypeError):
            client_ip = "?"
        status = args[1] if len(args) >= 2 else (args[0] if args else "-")
        try:
            JarvisAudit.log_event("FILE_SERVER_ACCESS", f"{client_ip} {self._strip_token()} {status}")
        except Exception:  # pylint: disable=broad-exception-caught  # the access log is what counts
            pass


def _make_handler_class(root: Path, token: str) -> type[DDriveHandler]:
    """A fresh DDriveHandler subclass bound to one server's root + token."""
    return type("DDriveHandler", (DDriveHandler,), {
        "_jarvis_root": root.resolve(),
        "_jarvis_token": token,
    })


class _FileAccessHTTPServer(ThreadingHTTPServer):
    """allow_reuse_address off: on Windows, SO_REUSEADDR (HTTPServer's default) lets a second
    socket bind to a port that is already LISTENING, so a genuine 'address in use' would never
    surface. Off, a second start on the same port raises OSError as the plan's acceptance test
    expects ('netstat' shows one LISTENING entry, not two)."""

    allow_reuse_address = False


# ---------------------------------------------------------------------------
# Server lifecycle (X-30 steps 2 and 7)
# ---------------------------------------------------------------------------


def _start_server(root: Path, bind_ip: str, port: int, token: str) -> ThreadingHTTPServer:
    """
    Binds (bind_ip, port) and starts serving root behind token in a daemon thread. Call this via
    asyncio.to_thread so a bind failure ('address in use') comes back as a normal exception
    instead of blocking the event loop. port=0 lets the OS pick a free port (useful for tests);
    read it back from server.server_address[1].
    """
    handler_cls = _make_handler_class(root, token)
    server = _FileAccessHTTPServer((bind_ip, port), handler_cls)  # may raise OSError
    thread = threading.Thread(target=server.serve_forever, name="jarvis-file-server", daemon=True)
    thread.start()
    server._jarvis_serve_thread = thread  # type: ignore[attr-defined]  # retrieved by the caller
    return server


def _stop_server_sync() -> bool:
    """Stops the running server (if any) and cancels its TTL timer; clears the module state.
    Returns True when a server was actually stopped. Used by stop_file_access_server (via
    asyncio.to_thread) and by the TTL timer itself."""
    global _server, _server_thread, _server_timer, _server_token, _server_url  # pylint: disable=global-statement
    global _server_masked_url, _server_root  # pylint: disable=global-statement
    with _state_lock:
        server = _server
        thread = _server_thread
        timer = _server_timer
    if timer is not None:
        timer.cancel()
    if server is None:
        return False
    try:
        server.shutdown()
    finally:
        server.server_close()
    if thread is not None:
        thread.join(timeout=5.0)
    # Cleared only once the socket is actually closed, so a caller that polls _server for None
    # (the TTL auto-stop path included) never sees a free port before it truly is free.
    with _state_lock:
        _server = None
        _server_thread = None
        _server_timer = None
        _server_token = None
        _server_url = None
        _server_masked_url = None
        _server_root = None
    return True


def _ttl_expired() -> None:
    """threading.Timer callback (X-30 step 7): auto-stop once the TTL elapses."""
    if _stop_server_sync():
        logger.info("File server TTL expired; stopped automatically")
        JarvisAudit.log_event("FILE_SERVER_ACCESS", "ttl_expired auto_stop")


# ---------------------------------------------------------------------------
# Tools (X-30 step 8)
# ---------------------------------------------------------------------------


@jarvis_tool(timeout=20, risk="medium", confirm_summary=_confirm_summary_text)
async def start_file_access_server(port: int | None = None) -> dict[str, Any]:
    """
    Starts a token-protected, TTL-limited local file server that shares only the configured root
    (D:/ when present, else the shared folder) on the LAN, and shows a QR code on this PC to open
    it on a phone on the same Wi-Fi. Use this when the user wants to browse or grab files from
    this PC on their phone.
    """
    global _server, _server_thread, _server_timer, _server_token, _server_url  # pylint: disable=global-statement
    global _server_masked_url, _server_root  # pylint: disable=global-statement

    with _state_lock:
        if _server is not None and _server_url:
            return {
                "status": "info",
                "url": _server_url,
                "message": f"Sir, file server pehle se chal raha hai: {_server_masked_url}",
            }

    root, error_code = _resolve_root()
    if error_code:
        return {
            "status": "error",
            "error_code": error_code,
            "message": ("Sir, koi valid share-able folder configured nahi hai "
                        "(D:/ aur shared folder dono missing hain)."),
        }

    bind_ip = get_local_ip()
    if bind_ip.startswith("127."):
        return {
            "status": "error",
            "error_code": ERROR_NO_LAN,
            "message": "Sir, is PC ka LAN IP nahi mil raha; Wi-Fi se connected hona zaroori hai.",
        }

    chosen_port = int(port) if port is not None else int(getattr(config, "file_server_port", 8000))
    token = secrets.token_urlsafe(16)

    try:
        server = await asyncio.to_thread(_start_server, root, bind_ip, chosen_port, token)
    except OSError as e:
        logger.exception("File server bind failed on %s:%s: %s", bind_ip, chosen_port, e)
        return {
            "status": "error",
            "error_code": ERROR_BIND_FAILED,
            "message": f"Sir, port {chosen_port} par server start nahi ho saka: {e!s}",
        }

    actual_port = server.server_address[1]
    url = f"http://{bind_ip}:{actual_port}/{token}/"
    masked_url = f"http://{bind_ip}:{actual_port}/"
    ttl_min = getattr(config, "file_server_ttl_min", 30)

    timer = threading.Timer(_ttl_seconds(), _ttl_expired)
    timer.daemon = True
    timer.start()

    with _state_lock:
        _server = server
        _server_thread = getattr(server, "_jarvis_serve_thread", None)
        _server_timer = timer
        _server_token = token
        _server_url = url
        _server_masked_url = masked_url
        _server_root = str(root)

    # X-30 step 5: the QR (with the token baked in) is shown on this PC only.
    qr_result: dict[str, Any] = {}
    try:
        qr_result = await generate_qr_code(data=url, filename="jarvis_file_server_qr.png",
                                           open_file=False)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("File server QR generation failed: %s", exc)

    # X-30 step 6: until bridge cookie auth is live, only the masked URL (no token) goes out here.
    try:
        await notify_event("intelligence_update", {
            "type": "json",
            "label": "FILE SERVER",
            "data": {
                "url": masked_url,
                "root": str(root),
                "ttl_min": ttl_min,
                "status": "active",
                "mode": "file_server",
            },
        })
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug("File server notify_event skipped: %s", exc)

    # When a media relay helper exists (OPS-08/DASH-C01), relay the QR PNG through it too.
    try:
        import services.utils.jarvis_bridge as _bridge  # pylint: disable=import-outside-toplevel

        relay = getattr(_bridge, "upload_media", None) or getattr(_bridge, "publish_media", None)
        qr_path = qr_result.get("file_path") or qr_result.get("path")
        if callable(relay) and qr_result.get("status") == "success" and qr_path:
            await relay(qr_path, {"kind": "file_server_qr"})
    except (ImportError, TypeError, RuntimeError, OSError, ValueError) as exc:
        logger.debug("File server QR relay skipped: %s", exc)

    JarvisAudit.log_event("FILE_SERVER_ACCESS", f"start root={root} ip={bind_ip} port={actual_port}")

    message = (
        f"✅ Sir, file server shuru ho gaya hai ({masked_url}); screen par QR code scan karein "
        f"(token wahi diya hai), {ttl_min} minute baad khud band ho jayega."
    )
    return {
        "status": "success",
        "url": url,
        "masked_url": masked_url,
        "root": str(root),
        "qr_path": qr_result.get("file_path"),
        "ttl_min": ttl_min,
        "message": message,
    }


@jarvis_tool(timeout=10)
async def stop_file_access_server() -> dict[str, Any]:
    """Stops the running file access server (if any) and cancels its TTL timer."""
    try:
        stopped = await asyncio.to_thread(_stop_server_sync)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error stopping file server: %s", e)
        return {"status": "error", "message": f"Server band karne mein error aaya: {e!s}"}
    if stopped:
        JarvisAudit.log_event("FILE_SERVER_ACCESS", "stop manual")
        return {"status": "success", "message": "Sir, file server band kar diya gaya hai."}
    return {"status": "info", "message": "Sir, koi server active nahi hai."}
