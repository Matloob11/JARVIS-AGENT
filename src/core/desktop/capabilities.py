"""Desktop Node capabilities: the pc.* operations (plan task C-122 steps 1 and 5; contracts K-70, K-71, K-73, K-76).

install(node) registers the K-70 internal operations on the node's OperationRegistry (pc.ping is the node's own):
- pc.capture_screen: the primary monitor, longest side 1600 px, JPEG quality 78, uploaded as an encrypted blob;
- pc.capture_webcam: one webcam frame, the same encoding and upload;
- pc.read_file: one regular file of at most 25 MB inside the open roots (the K-76 path policy again at run time),
  only with the ConfirmFlow approval reference the call was accepted with (exposure), uploaded as a blob;
- pc.fetch: one relay file copied by the mirror (agent-signed sha256 required, name validation, Mark-of-the-Web),
  then opened only when it is on the K-73 auto-open list; executables, scripts, shortcuts, archives and every
  other type are never opened, Explorer selects them instead;
- pc.open_url: an http(s) URL in the default browser;
- pc.speak_local: jarvis_audio.speak_locally with a local notice; pc.play_sound: a named JARVIS sound.
Plus the startup sound when the node activates (step 5), once per supervisor start.

The node's policy (C-121) has judged every call before it runs (mode, lock, presence, capture rules, class and
approval, origin, paths, caps); the handlers re-check what can change in between (the foreground for a capture,
the path and the approval reference for read_file) and never loosen a C-121 rule. Every argument is checked
against the operation's K-71 schema (BAD_ARGS). Uploads and deliveries go through the policy's byte caps
(note_bytes). Nothing here logs an argument, a path, a URL, a title or a spoken text.

Blobs (K-70/K-73): the plaintext is sealed with desktop_protocol.encrypt_blob to the session key of the agent peer
that sent the call, under a fresh 32-hex seal id (the chunk AAD), and POSTed PC-signed to /desktop/blob; the
bridge answers with its own blob_id, the id the agent fetches on /internal/blob/<id>. The result's blobs entry is
{blob_id (the bridge's fetch id), seal_id (the AAD id), sha256 and size of the plaintext, mime}.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import stat as stat_mod
import subprocess
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from src.core.desktop import mirror as mirror_mod
from src.core.desktop.executor import k12_error

logger = logging.getLogger("JARVIS-DESKTOP.capabilities")

SCREEN_LONGEST_SIDE = 1600
JPEG_QUALITY = 78               # K-73 / plan C-122: q75-80
READ_FILE_MAX_BYTES = 25 * 1024 * 1024  # K-76: pc.read_file is limited to 25 MB
URL_MAX_CHARS = 2048
SPEAK_MAX_CHARS = 1000
WEBCAM_WARMUP_FRAMES = 5
SOUND_NAMES = ("startup", "chime", "notify", "alert")
STARTUP_SOUND_FILE = "desktop_startup_sound.json"
STARTUP_SOUNDS = (Path("assets") / "audio" / "Installing... Ironman Jarvis Ai.mp3",
                  Path("assets") / "audio" / "Installing... Ironman Jarvis Ai.wav",
                  Path("assets") / "audio" / "jarvis_startup_chime.wav")
CHIME_SOUND = Path("assets") / "audio" / "jarvis_startup_chime.wav"
UPLOAD_PATH = "/desktop/blob"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_MB_ICONASTERISK, _MB_ICONHAND = 0x40, 0x10

# Codes (K-12/K-76) the handlers return in their K-12 error results.
BAD_ARGS = "BAD_ARGS"
UPLOAD_FAILED = "UPLOAD_FAILED"
DELIVERY_FAILED = "DELIVERY_FAILED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
SENSITIVE_SCREEN = "SENSITIVE_SCREEN"
DESKTOP_RATE_LIMITED = "DESKTOP_RATE_LIMITED"
DESKTOP_REJECTED = "DESKTOP_REJECTED"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
CAPTURE_FAILED = "CAPTURE_FAILED"
CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"
SOUND_UNAVAILABLE = "SOUND_UNAVAILABLE"
SPEAK_FAILED = "SPEAK_FAILED"
OPEN_FAILED = "OPEN_FAILED"

_MESSAGES = {
    BAD_ARGS: "Sir, laptop ko is kaam ki maloomat theek nahi mili.",
    UPLOAD_FAILED: "Sir, laptop se file bheji nahi ja saki; dobara koshish karein.",
    DELIVERY_FAILED: "Sir, file laptop par nahi aa saki.",
    APPROVAL_REQUIRED: "Sir, is file ko bhejne ke liye pehle ijazat chahiye.",
    PATH_NOT_ALLOWED: "Sir, yeh file laptop ki ijazat wali jagah mein nahi hai.",
    SENSITIVE_SCREEN: "Sir, saamne mehfooz window khuli hai, is liye screen nahi li gayi.",
    DESKTOP_RATE_LIMITED: "Sir, laptop se data bhejne ki had poori ho gayi; thori der rukein.",
    DESKTOP_REJECTED: "Sir, laptop ne yeh kaam nahi kiya.",
    FILE_TOO_LARGE: "Sir, yeh file 25 MB se bari hai, is liye nahi bheji.",
    CAPTURE_FAILED: "Sir, laptop ki screen nahi li ja saki.",
    CAMERA_UNAVAILABLE: "Sir, laptop ka camera is waqt dastyab nahi.",
    SOUND_UNAVAILABLE: "Sir, laptop par yeh awaaz nahi chal saki.",
    SPEAK_FAILED: "Sir, laptop par yeh baat boli nahi ja saki.",
    OPEN_FAILED: "Sir, file laptop par aa gayi magar khul nahi saki.",
}


class CapabilityError(Exception):
    """A pc.* operation that cannot go on: code is the K-12/K-76 code of its error result; reason a short rule
    name for the node log (never an argument, a path or content)."""

    def __init__(self, code: str, reason: str = "", message: str | None = None) -> None:
        super().__init__(f"{code}:{reason}")
        self.code = code
        self.reason = reason or code.lower()
        self.message = message


def error_result(tool: str, code: str, message: str | None = None) -> dict[str, Any]:
    return k12_error(tool, code, message or _MESSAGES.get(code))


# --- arguments (K-71 pc.* schemas) ---------------------------------------------------------------------------

_LITERAL_RE = re.compile(r'^Literal\[(.*)\]$')


def _annotation_ok(annotation: str, value: Any) -> bool:
    if annotation == "str":
        return isinstance(value, str)
    if annotation == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation == "bool":
        return isinstance(value, bool)
    if annotation == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    literal = _LITERAL_RE.match(annotation)
    if literal:
        allowed = [part.strip().strip('"').strip("'") for part in literal.group(1).split(",")]
        return isinstance(value, str) and value in allowed
    return False


OPERATION_SCHEMAS: Mapping[str, tuple[tuple[str, str, bool], ...]] = {
    op.name: tuple((name, annotation, default is tool_placement._REQUIRED)  # pylint: disable=protected-access
                   for name, annotation, default in op.params)
    for op in tool_placement.PC_OPERATIONS}


def check_args(tool: str, args: Any) -> str | None:
    """None when args fit the operation's K-71 schema exactly (no unknown name, every required one, the right
    types), else the rule broken (the result is BAD_ARGS)."""
    schema = OPERATION_SCHEMAS.get(tool)
    if schema is None:
        return "unknown_operation"
    if not isinstance(args, Mapping):
        return "not_a_mapping"
    names = {name for name, _annotation, _required in schema}
    if any(not isinstance(key, str) or key not in names for key in args):
        return "unknown_field"
    for name, annotation, required in schema:
        if name not in args:
            if required:
                return "missing_field"
            continue
        if not _annotation_ok(annotation, args[name]):
            return "bad_type"
    return None


# --- pure helpers --------------------------------------------------------------------------------------------

def encode_jpeg(image: Any, *, longest_side: int = SCREEN_LONGEST_SIDE,
                quality: int = JPEG_QUALITY) -> tuple[bytes, int, int]:
    """A PIL image as JPEG bytes: RGB, scaled down so its longest side is at most longest_side (never up)."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    picture = image.convert("RGB") if image.mode != "RGB" else image.copy()
    width, height = picture.size
    longest = max(width, height)
    if longest > longest_side:
        scale = longest_side / float(longest)
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        picture = picture.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=int(quality), optimize=True)
    return buffer.getvalue(), picture.size[0], picture.size[1]


def check_url(url: Any) -> str | None:
    """The normalised http(s) URL pc.open_url may open, else None: http or https only, a host, no user info, no
    whitespace, control or other non-printable character, at most URL_MAX_CHARS."""
    if not isinstance(url, str):
        return None
    text = url.strip()
    if not text or len(text) > URL_MAX_CHARS or not text.isprintable() or any(ch.isspace() for ch in text):
        return None
    try:
        parts = urllib.parse.urlsplit(text)
        host = parts.hostname
        port_ok = parts.port is None or 0 < parts.port < 65536
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc or not host or not port_ok:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    return text


def auto_opens(name: str) -> bool:
    """True when a fetched file may be opened (K-73 auto-open list: audio, video, image, pdf, txt, csv, json,
    xlsx, docx, pptx, html); executables, scripts, shortcuts, archives and every other type are never opened."""
    from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel

    return not policy_mod.opens_as_code(name, allow_directory=False, safe_exts=policy_mod.K73_AUTO_OPEN_EXTS)


def sensitive_foreground(title: str, process: str = "") -> bool:
    """The K-76 capture rule: a password manager or a title that shows a sensitive path."""
    from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel

    # pylint: disable=protected-access  # the policy's own lists, so both checks agree exactly
    if process.strip().lower() in policy_mod._PASSWORD_MANAGER_PROCESSES:
        return True
    low = (title or "").lower()
    return bool(low) and any(marker in low for marker in policy_mod._SENSITIVE_TITLE_MARKERS)


def _foreground() -> tuple[str, str]:
    """(title, process) of the foreground window on Windows; ('', '') elsewhere or when unreadable."""
    if sys.platform != "win32":
        return "", ""
    from src.core.desktop import presence  # pylint: disable=import-outside-toplevel

    # pylint: disable=protected-access  # the presence module's own never-raising readers
    return presence._foreground_title_windows(), presence._foreground_process_windows()


def _grab_screen() -> Any:
    """The primary monitor as a PIL image (Pillow ImageGrab; all_screens=False is the primary monitor)."""
    from PIL import ImageGrab  # pylint: disable=import-outside-toplevel

    return ImageGrab.grab(all_screens=False)


def _grab_webcam() -> Any:
    """One webcam frame as a PIL image (OpenCV, DirectShow on Windows); CapabilityError when no camera answers."""
    import cv2  # pylint: disable=import-outside-toplevel,no-member
    from PIL import Image  # pylint: disable=import-outside-toplevel

    backend = getattr(cv2, "CAP_DSHOW", 0) if sys.platform == "win32" else 0
    camera = cv2.VideoCapture(0, backend)  # pylint: disable=no-member  # a C extension
    try:
        if not camera.isOpened():
            raise CapabilityError(CAMERA_UNAVAILABLE, "not_opened")
        frame = None
        for _ in range(WEBCAM_WARMUP_FRAMES):  # the first frames of many cameras are dark
            ok, current = camera.read()
            if ok and current is not None:
                frame = current
        if frame is None:
            raise CapabilityError(CAMERA_UNAVAILABLE, "no_frame")
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))  # pylint: disable=no-member
    finally:
        camera.release()


def _open_file(path: Path) -> None:
    """The shell's default handler (only ever called for a K-73 auto-open type)."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # pylint: disable=no-member
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # pylint: disable=consider-using-with


def _reveal_file(path: Path) -> None:
    """Explorer with the file selected: never runs it."""
    if sys.platform == "win32":
        subprocess.Popen(f'explorer.exe /select,"{path}"')  # pylint: disable=consider-using-with
        return
    folder = str(path.parent)
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # pylint: disable=consider-using-with


def _open_browser(url: str) -> bool:
    import webbrowser  # pylint: disable=import-outside-toplevel

    return bool(webbrowser.open(url, new=2))


def _speak(text: str) -> bool:
    from services.multimedia.jarvis_audio import speak_locally  # pylint: disable=import-outside-toplevel

    return bool(speak_locally(text))


def _notice_beep(kind: int = _MB_ICONASTERISK) -> None:
    """The local notice: a system sound (no toast library is installed on the node)."""
    if sys.platform != "win32":
        return
    try:
        import winsound  # pylint: disable=import-outside-toplevel

        winsound.MessageBeep(kind)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _play_file(path: Path) -> None:
    """Plays an audio file to its end in a daemon thread (pygame, as the PC brain's startup voice)."""
    def play() -> None:
        try:
            import pygame  # pylint: disable=import-outside-toplevel

            pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(0.85)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.25)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # a sound never fails the node
            logger.warning("Sound not played (%s)", type(exc).__name__)

    threading.Thread(target=play, name="desktop-sound", daemon=True).start()


# --- the operations ------------------------------------------------------------------------------------------

class Capabilities:
    """The pc.* handlers of one node. Every device or OS action is injectable (tests use fakes only)."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments

    def __init__(self, node: Any, *, http: Callable[[], mirror_mod.NodeHttp | None] | None = None,
                 mirror: Callable[[], mirror_mod.MediaMirror] | None = None,
                 grab_screen: Callable[[], Any] = _grab_screen, grab_webcam: Callable[[], Any] = _grab_webcam,
                 foreground: Callable[[], tuple[str, str]] = _foreground, open_file: Callable[[Path], Any] = _open_file,
                 reveal_file: Callable[[Path], Any] = _reveal_file, open_browser: Callable[[str], bool] = _open_browser,
                 speak: Callable[[str], bool] = _speak, notice: Callable[[], None] = _notice_beep,
                 play_file: Callable[[Path], None] = _play_file, beep: Callable[[int], None] = _notice_beep) -> None:
        self._node = node
        self._http = http or self._default_http
        self._mirror = mirror or (lambda: mirror_mod.node_mirror(node))
        self._grab_screen, self._grab_webcam, self._foreground = grab_screen, grab_webcam, foreground
        self._open_file, self._reveal_file, self._open_browser = open_file, reveal_file, open_browser
        self._speak, self._notice, self._play_file, self._beep = speak, notice, play_file, beep

    def _default_http(self) -> mirror_mod.NodeHttp | None:
        try:
            return mirror_mod.node_http(self._node)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("No usable bridge URL for uploads")
            return None

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        return {"pc.capture_screen": self.capture_screen, "pc.capture_webcam": self.capture_webcam,
                "pc.read_file": self.read_file, "pc.fetch": self.fetch, "pc.open_url": self.open_url,
                "pc.speak_local": self.speak_local, "pc.play_sound": self.play_sound}

    # --- shared pieces ---------------------------------------------------------------------------------------

    def _context(self) -> Any:
        getter = getattr(self._node, "call_context", None)
        return getter() if callable(getter) else None

    def _note_bytes(self, bucket: str, size: int, *, call_id: str | None, tool: str) -> str | None:
        note = getattr(getattr(self._node, "policy", None), "note_bytes", None)
        if note is None:
            return None
        return note(bucket, size, call_id=call_id, tool=tool)

    def _audit(self, event: str, **fields: Any) -> None:
        record = getattr(getattr(self._node, "audit", None), "record", None)
        if record is not None:
            try:
                record(event, **fields)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Audit line not written")

    def upload_blob(self, ctx: Any, data: bytes, mime: str, *, tool: str) -> dict[str, Any]:
        """Seals data to the call's agent session and POSTs it (K-73); returns the result's blobs entry.
        Synchronous (a worker thread)."""
        link = getattr(self._node, "link", None)
        peer = link.peer(ctx.peer) if link is not None and ctx is not None else None
        if peer is None:
            raise CapabilityError(UPLOAD_FAILED, "no_session")
        seal_id = secrets.token_hex(16)
        try:
            sealed = dp.encrypt_blob(peer.key, seal_id, bytes(data))
        except (ValueError, dp.DesktopLinkError):
            raise CapabilityError(UPLOAD_FAILED, "too_large") from None
        if self._note_bytes("uploads", len(sealed), call_id=ctx.call_id, tool=tool):
            raise CapabilityError(DESKTOP_RATE_LIMITED, "uploads_cap")
        http = self._http()
        if http is None:
            raise CapabilityError(UPLOAD_FAILED, "no_link")
        try:
            answer = http.post(UPLOAD_PATH, sealed)
        except mirror_mod.NodeHttpError as exc:
            raise CapabilityError(UPLOAD_FAILED, f"http_{exc.reason}") from None
        blob_id = answer.get("blob_id") if isinstance(answer, dict) else None
        stored = answer.get("size") if isinstance(answer, dict) else None
        if not isinstance(blob_id, str) or not _HEX32.fullmatch(blob_id) or stored != len(sealed):
            raise CapabilityError(UPLOAD_FAILED, "bad_answer")
        return {"blob_id": blob_id, "seal_id": seal_id, "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data), "mime": mime}

    async def _run(self, tool: str, args: Mapping[str, Any], work: Callable[[Any], Any]) -> dict[str, Any]:
        """Arguments first (BAD_ARGS), then work(ctx) in a worker thread; a CapabilityError is the K-12 error."""
        problem = check_args(tool, args)
        if problem is not None:
            logger.info("%s refused: BAD_ARGS (%s)", tool, problem)
            return error_result(tool, BAD_ARGS)
        ctx = self._context()
        try:
            return await asyncio.to_thread(work, ctx)
        except CapabilityError as exc:
            logger.info("%s ended: %s (%s)", tool, exc.code, exc.reason)
            return error_result(tool, exc.code, exc.message)

    # --- captures --------------------------------------------------------------------------------------------

    def _check_foreground(self) -> None:
        try:
            title, process = self._foreground()
        except Exception:  # pylint: disable=broad-exception-caught  # unreadable: the policy judged it already
            return
        if sensitive_foreground(title or "", process or ""):
            raise CapabilityError(SENSITIVE_SCREEN, "sensitive_foreground")

    def _capture(self, tool: str, grab: Callable[[], Any], ctx: Any) -> dict[str, Any]:
        self._check_foreground()  # the K-76 capture rule again, right before the pixels are taken
        try:
            image = grab()
        except CapabilityError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            code = CAMERA_UNAVAILABLE if tool == "pc.capture_webcam" else CAPTURE_FAILED
            raise CapabilityError(code, type(exc).__name__) from None
        data, width, height = encode_jpeg(image)
        blob = self.upload_blob(ctx, data, "image/jpeg", tool=tool)
        what = "camera ki tasveer" if tool == "pc.capture_webcam" else "screen"
        return {"status": "success", "tool": tool, "message": f"Sir, laptop ki {what} le li gayi.",
                "data": {"width": width, "height": height, "mime": "image/jpeg", "size": len(data)},
                "blobs": [blob]}

    async def capture_screen(self, args: Mapping[str, Any]) -> dict[str, Any]:
        tool = "pc.capture_screen"
        return await self._run(tool, args, lambda ctx: self._capture(tool, self._grab_screen, ctx))

    async def capture_webcam(self, args: Mapping[str, Any]) -> dict[str, Any]:
        tool = "pc.capture_webcam"
        return await self._run(tool, args, lambda ctx: self._capture(tool, self._grab_webcam, ctx))

    # --- read_file -------------------------------------------------------------------------------------------

    def _resolve_read_path(self, value: str) -> Path:
        from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel
        from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

        node = self._node
        try:
            appdata: Path | None = node_settings.node_home().parent
        except Exception:  # pylint: disable=broad-exception-caught
            appdata = None
        outputs = policy_mod._outputs_dir(node)  # pylint: disable=protected-access  # the policy's own roots
        roots = policy_mod._open_roots(node) or [str(outputs)]  # pylint: disable=protected-access
        resolved = policy_mod.resolve_in_roots(value, roots, data_root=Path(node.data_root), outputs_dir=outputs,
                                               jarvis_appdata=appdata)
        if resolved is None:
            raise CapabilityError(PATH_NOT_ALLOWED, "path_policy")
        return resolved

    @staticmethod
    def _read_regular(path: Path, limit: int) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            raise CapabilityError(PATH_NOT_ALLOWED, "unreadable") from None
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat_mod.S_ISREG(st.st_mode):
                raise CapabilityError(PATH_NOT_ALLOWED, "not_a_file")
            if st.st_size > limit:
                raise CapabilityError(FILE_TOO_LARGE, "too_large")
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise CapabilityError(FILE_TOO_LARGE, "grew")
        return data

    def _read_file(self, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        tool = "pc.read_file"
        approval = getattr(ctx, "approval", None) or {}
        confirm_id = approval.get("confirm_id") if isinstance(approval, Mapping) else None
        if ctx is None or not isinstance(confirm_id, str) or not confirm_id.strip():
            raise CapabilityError(APPROVAL_REQUIRED, "no_approval_reference")
        path = self._resolve_read_path(args["path"])
        data = self._read_regular(path, READ_FILE_MAX_BYTES)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        blob = self.upload_blob(ctx, data, mime, tool=tool)
        self._audit("read_file_exposed", call_id=ctx.call_id, tool=tool, confirm_id=confirm_id,
                    approval_channel=approval.get("channel"), count=len(data))
        return {"status": "success", "tool": tool, "message": "Sir, file laptop se bhej di gayi.",
                "data": {"name": path.name, "size": len(data), "mime": mime, "confirm_id": confirm_id},
                "blobs": [blob]}

    async def read_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return await self._run("pc.read_file", args, lambda ctx: self._read_file(args, ctx))

    # --- fetch -----------------------------------------------------------------------------------------------

    def _fetch(self, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        tool = "pc.fetch"
        sha256, size, relpath, area = args["sha256"], args["size"], args["relpath"], args["area"]
        if not _HEX64.fullmatch(sha256) or size < 0:
            raise CapabilityError(BAD_ARGS, "sha256_or_size")
        want_open = bool(args.get("open", False))
        try:
            path = self._mirror().fetch_one(area, relpath, size=size, sha256=sha256, explicit=want_open)
        except mirror_mod.MirrorError as exc:
            if exc.code == mirror_mod.BAD_NAME:
                raise CapabilityError(BAD_ARGS, f"name_{exc.reason}") from None
            if exc.code == mirror_mod.DESKTOP_RATE_LIMITED:
                raise CapabilityError(DESKTOP_RATE_LIMITED, exc.reason) from None
            message = None
            if exc.reason == "sha256_mismatch":
                message = "Sir, laptop par aayi file ka sha256 mel nahi khata, is liye rakhi nahi gayi."
            elif exc.reason == "deferred_voice_active":
                message = "Sir, bari file voice session ke baad laptop par aa jayegi."
            elif exc.reason == "deferred_voice_unknown":
                message = ("Sir, laptop ko voice session ka haal maloom nahi, is liye bari file abhi nahi aayi; "
                           "khol kar mangwaein to foran aa jayegi.")
            elif exc.reason in ("reparse_point", "not_a_folder", "outside_root"):
                message = "Sir, laptop par target folder mehfooz nahi (link ya file), is liye file nahi rakhi gayi."
            elif exc.reason in ("over_cap", "size"):
                message = "Sir, yeh file laptop ki rozana data had se bari hai, is liye laptop par nahi aa sakti."
            raise CapabilityError(DELIVERY_FAILED, exc.reason, message) from None
        opened = revealed = False
        if want_open:
            try:
                if auto_opens(path.name):
                    self._open_file(path)
                    opened = True
                else:  # K-73: never opened; Explorer selects it instead
                    self._reveal_file(path)
                    revealed = True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise CapabilityError(OPEN_FAILED, type(exc).__name__) from None
        self._audit("fetch_delivered", call_id=getattr(ctx, "call_id", None), tool=tool, kind=area, count=size,
                    state="opened" if opened else ("revealed" if revealed else "saved"))
        message = ("Sir, file laptop par aa gayi aur khol di gayi." if opened else
                   "Sir, file laptop par aa gayi; folder mein select kar di gayi." if revealed else
                   "Sir, file laptop par aa gayi.")
        return {"status": "success", "tool": tool, "message": message,  # never the absolute local path
                "data": {"area": area, "name": path.name, "relpath": self._local_relpath(area, path), "size": size,
                         "opened": opened, "revealed": revealed}}

    def _local_relpath(self, area: str, path: Path) -> str:
        """The copy's path below its area root ('/'-joined; it may differ from the relay's by ' (n)')."""
        try:
            return "/".join(path.relative_to(self._mirror().root(area)).parts)
        except (ValueError, mirror_mod.MirrorError):
            return path.name

    async def fetch(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return await self._run("pc.fetch", args, lambda ctx: self._fetch(args, ctx))

    # --- open_url, speak_local, play_sound -------------------------------------------------------------------

    def _open_url(self, args: Mapping[str, Any]) -> dict[str, Any]:
        url = check_url(args["url"])
        if url is None:
            raise CapabilityError(BAD_ARGS, "url")
        try:
            opened = self._open_browser(url)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise CapabilityError(OPEN_FAILED, type(exc).__name__) from None
        if not opened:
            raise CapabilityError(OPEN_FAILED, "no_browser")
        return {"status": "success", "tool": "pc.open_url", "message": "Sir, link laptop ke browser mein khol diya."}

    async def open_url(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return await self._run("pc.open_url", args, lambda _ctx: self._open_url(args))

    def _speak_local(self, args: Mapping[str, Any]) -> dict[str, Any]:
        text = " ".join(str(args["text"]).split())
        if not text or len(text) > SPEAK_MAX_CHARS or not text.isprintable():
            raise CapabilityError(BAD_ARGS, "text")
        self._notice()
        try:
            spoken = self._speak(text)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise CapabilityError(SPEAK_FAILED, type(exc).__name__) from None
        if not spoken:
            raise CapabilityError(SPEAK_FAILED, "not_spoken")
        return {"status": "success", "tool": "pc.speak_local", "message": "Sir, laptop par bata diya gaya.",
                "data": {"chars": len(text)}}

    async def speak_local(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return await self._run("pc.speak_local", args, lambda _ctx: self._speak_local(args))

    def sound_candidates(self, name: str) -> list[Path]:
        """The files of a named sound, in priority order, from the data root and then the code root."""
        from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

        bases = [Path(self._node.data_root), node_settings.code_root()]
        files = STARTUP_SOUNDS if name == "startup" else (CHIME_SOUND,) if name == "chime" else ()
        seen: list[Path] = []
        for relative in files:
            for base in bases:
                path = base / relative
                if path not in seen:
                    seen.append(path)
        return seen

    def play_named(self, name: str) -> str:
        """Plays a SOUND_NAMES sound; returns what played (a file name or 'system'), else CapabilityError."""
        if name in ("notify", "alert"):
            if sys.platform != "win32":
                raise CapabilityError(SOUND_UNAVAILABLE, "no_system_sound")
            self._beep(_MB_ICONHAND if name == "alert" else _MB_ICONASTERISK)
            return "system"
        for path in self.sound_candidates(name):
            if path.is_file():
                self._play_file(path)
                return path.name
        raise CapabilityError(SOUND_UNAVAILABLE, "no_file")

    def _play_sound(self, args: Mapping[str, Any]) -> dict[str, Any]:
        name = args["name"]
        if name not in SOUND_NAMES:
            raise CapabilityError(BAD_ARGS, "sound_name")
        played = self.play_named(name)
        return {"status": "success", "tool": "pc.play_sound", "message": "Sir, laptop par awaaz chala di.",
                "data": {"name": name, "source": "system" if played == "system" else "file"}}

    async def play_sound(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return await self._run("pc.play_sound", args, lambda _ctx: self._play_sound(args))

    # --- step 5: the startup sound ---------------------------------------------------------------------------

    def startup_sound_due(self, *, parent_pid: int | None = None, boot_time: float | None = None) -> bool:
        """True once per supervisor start (and Windows boot): a node child that its supervisor restarts (a
        settings change, a stall) does not play it again. The marker lives in run/."""
        marker = Path(self._node.run_dir) / STARTUP_SOUND_FILE
        key = {"supervisor": int(os.getppid() if parent_pid is None else parent_pid),
               "boot": round(float(_boot_time() if boot_time is None else boot_time))}
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        if previous == key:
            return False
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(key), encoding="utf-8")
        except OSError:
            pass
        return True

    async def startup_sound(self) -> None:
        """The node task (active mode only): the startup sound (the Ironman MP3, else the X-02 chime)."""
        if os.environ.get("JARVIS_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
            return
        if not self.startup_sound_due():
            return
        try:
            played = await asyncio.to_thread(self.play_named, "startup")
            logger.info("Startup sound played (%s)", "chime" if "chime" in played else "voice")
        except CapabilityError as exc:
            logger.info("Startup sound not played (%s)", exc.reason)


def _boot_time() -> float:
    try:
        import psutil  # pylint: disable=import-outside-toplevel

        return float(psutil.boot_time())
    except Exception:  # pylint: disable=broad-exception-caught
        return 0.0


def install(node: Any) -> None:
    """NodeRuntime plugin hook: registers the pc.* operations (every run mode; the policy refuses actuation from
    canary rooms) and, in active mode, the startup sound."""
    caps = Capabilities(node)
    for name, handler in caps.handlers().items():
        node.register_operation(name, handler)
    node.capabilities = caps
    node.add_task(caps.startup_sound, active_only=True, name="startup-sound")
    logger.info("Desktop capabilities installed: %s", ", ".join(sorted(caps.handlers())))
