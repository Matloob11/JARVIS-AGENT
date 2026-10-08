"""
# jarvis_advanced_tools.py
Jarvis Advanced Automation Tools
Handles image downloading, file compression, and mock email simulation.
"""

import asyncio
import os
import re
import shutil
import smtplib
import ssl
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.info.jarvis_search import search_images
from services.utils import jarvis_audit
from services.utils.jarvis_bridge import notify_event
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

# Setup logging
logger = setup_logger("JARVIS-ADVANCED")

_UNSAFE_NAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")
_WIN_IMAGE_NAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_ZIP_FOLDER_BYTES = 2 * 1024 * 1024 * 1024
_DOWNLOAD_CONCURRENCY = 4
_GMAIL_AUTH_HINT = (
    "Gmail ne app password reject kiya - myaccount.google.com/apppasswords se naya banayein "
    "(2-Step Verification ON honi chahiye)"
)


def _smtp_host() -> str:
    raw = os.getenv("SMTP_HOST")
    if raw is None:
        return "smtp.gmail.com"
    host = raw.strip()
    if not host or ("." not in host and host.lower() not in ("localhost",)):
        return "smtp.gmail.com"
    return host


def _smtp_port() -> int:
    raw = os.getenv("SMTP_PORT")
    if raw is None:
        return 587
    port_text = raw.strip()
    if port_text.isdigit():
        return int(port_text)
    return 587


def _email_recipient_count(recipient: str) -> int:
    raw = (recipient or "").strip()
    if not raw:
        return 0
    parts = [p.strip() for p in re.split(r"[,;]", raw) if p.strip()]
    return len(parts) if parts else 1


def _email_attachment_allow_roots() -> list[str]:
    home = os.path.expanduser("~")
    project = config.project_root
    outputs = getattr(config, "outputs_dir", None) or os.path.join(project, "Jarvis_Outputs")
    shared = (getattr(config, "shared_dir", None) or "").strip() or os.path.join(home, "Jarvis_Shared")
    roots = [
        os.path.abspath(outputs),
        os.path.abspath(shared),
        os.path.abspath(os.path.join(home, "Desktop")),
        os.path.abspath(os.path.join(home, "Documents")),
        os.path.abspath(os.path.join(home, "Downloads")),
    ]
    return list(dict.fromkeys(roots))


def _path_under_allowed_root(path: str, root: str) -> bool:
    path_abs = os.path.abspath(path)
    root_abs = os.path.abspath(root)
    try:
        return os.path.commonpath([path_abs, root_abs]) == root_abs
    except ValueError:
        return False


def _validate_email_attachment(attachment_path: str) -> tuple[str | None, dict | None]:
    raw = (attachment_path or "").strip()
    if not raw:
        return "", None
    expanded = os.path.expanduser(raw)
    project = config.project_root
    outputs = getattr(config, "outputs_dir", None) or os.path.join(project, "Jarvis_Outputs")
    candidates = [expanded] if os.path.isabs(expanded) else [
        os.path.join(project, expanded),
        os.path.join(outputs, expanded),
    ]
    resolved = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            resolved = os.path.realpath(candidate)
            break
    if not resolved:
        return None, {
            "status": "error",
            "message": f"❌ Attachment nahi mili ya readable nahi: {os.path.basename(raw)}",
            "error_code": "ATTACHMENT_MISSING",
        }
    if not any(_path_under_allowed_root(resolved, root) for root in _email_attachment_allow_roots()):
        return None, {
            "status": "error",
            "message": (
                "❌ Attachment sirf Jarvis_Outputs, Jarvis_Shared ya "
                "Desktop/Documents/Downloads se attach ho sakti hai."
            ),
            "error_code": "ATTACHMENT_NOT_ALLOWED",
        }
    try:
        size = os.path.getsize(resolved)
    except OSError as exc:
        return None, {
            "status": "error",
            "message": f"❌ Attachment read nahi ho saki: {redact_secrets(str(exc))}",
            "error_code": "ATTACHMENT_MISSING",
        }
    if size > _MAX_EMAIL_ATTACHMENT_BYTES:
        return None, {
            "status": "error",
            "message": "❌ Attachment 20 MB se zyada hai.",
            "error_code": "ATTACHMENT_TOO_LARGE",
        }
    return resolved, None


def _safe_zip_basename(name: str) -> str:
    base = os.path.basename((name or "Archive.zip").strip() or "Archive.zip")
    stem, ext = os.path.splitext(base)
    if ext.lower() != ".zip":
        stem = stem or base
    stem = _UNSAFE_NAME_CHARS.sub("_", stem).strip("._") or "Archive"
    return f"{stem[:120]}.zip"


def _outputs_root() -> str:
    project = config.project_root
    return os.path.abspath(
        getattr(config, "outputs_dir", None) or os.path.join(project, "Jarvis_Outputs"),
    )


def _downloads_base() -> str:
    return os.path.abspath(os.path.join(_outputs_root(), "Downloads"))


def _archives_base() -> str:
    return os.path.abspath(os.path.join(_outputs_root(), "Archives"))


def _folder_name_invalid() -> dict:
    return {
        "status": "error",
        "message": "folder name invalid",
    }


def _resolve_download_dir(folder_name: str) -> tuple[str | None, dict | None]:
    base = _downloads_base()
    name = (folder_name or "Downloaded_Images").strip() or "Downloaded_Images"
    target = os.path.abspath(os.path.join(base, name))
    try:
        if os.path.commonpath([base, target]) != base:
            return None, _folder_name_invalid()
    except ValueError:
        return None, _folder_name_invalid()
    return target, None


def _zip_allow_roots() -> list[str]:
    home = os.path.expanduser("~")
    project = config.project_root
    outputs = _outputs_root()
    shared = (getattr(config, "shared_dir", None) or "").strip() or os.path.join(home, "Jarvis_Shared")
    roots = [
        outputs,
        os.path.abspath(shared),
        os.path.abspath(os.path.join(home, "Desktop")),
        os.path.abspath(os.path.join(home, "Documents")),
        os.path.abspath(os.path.join(home, "Downloads")),
    ]
    return list(dict.fromkeys(roots))


def _resolve_zip_source(folder_path: str) -> tuple[str | None, dict | None]:
    raw = (folder_path or "").strip()
    if not raw:
        return None, {
            "status": "error",
            "message": f"❌ Folder '{folder_path}' nahi mila.",
        }
    expanded = os.path.expanduser(raw)
    project = config.project_root
    outputs = _outputs_root()
    candidates: list[str] = []
    if os.path.isabs(expanded):
        candidates.append(expanded)
    else:
        candidates.extend([
            os.path.join(project, expanded),
            os.path.join(outputs, expanded),
            expanded,
        ])
    resolved = None
    for candidate in candidates:
        path = os.path.abspath(candidate)
        if os.path.isdir(path):
            resolved = path
            break
    if not resolved:
        return None, {
            "status": "error",
            "message": f"❌ Folder '{folder_path}' nahi mila.",
        }
    if not any(_path_under_allowed_root(resolved, root) for root in _zip_allow_roots()):
        return None, {
            "status": "error",
            "message": (
                "❌ Sirf Jarvis_Outputs, Jarvis_Shared ya Desktop/Documents/Downloads "
                "folders zip ho sakte hain."
            ),
        }
    return resolved, None


def _directory_size_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _sanitize_image_stem(query: str) -> str:
    stem = _WIN_IMAGE_NAME_BAD.sub("_", (query or "image").strip() or "image")
    stem = stem.strip("._") or "image"
    return stem[:60]


def _image_ext(url: str, content_type: str) -> str:
    ctype = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    if ctype in mapping:
        return mapping[ctype]
    ext = url.rsplit(".", maxsplit=1)[-1].split("?", maxsplit=1)[0].lower()
    if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "jpg" if ext == "jpeg" else ext
    return "jpg"


def _lan_download_url(filepath: str) -> str:
    from services.system.jarvis_file_server import get_local_ip  # pylint: disable=import-outside-toplevel

    root = getattr(config, "file_server_root", None) or config.shared_dir
    try:
        rel = os.path.relpath(filepath, root).replace("\\", "/")
    except ValueError:
        rel = os.path.basename(filepath)
    local_ip = get_local_ip()
    return f"http://{local_ip}:8000/{rel}"


async def _emit_download_media_update(
    *,
    label: str,
    url: str,
    filename: str,
    query: str,
) -> None:
    meta = {"kind": "download", "filename": filename, "query": query}
    await notify_event(
        "intelligence_update",
        {
            "type": "image",
            "label": label,
            "url": url,
            "data": {"filename": filename, **meta},
            "meta": meta,
        },
    )
    try:
        import services.utils.jarvis_bridge as bridge  # pylint: disable=import-outside-toplevel

        upload_media = getattr(bridge, "upload_media", None)
        if callable(upload_media):
            await upload_media(url, meta)
    except (ImportError, TypeError, RuntimeError, OSError, ValueError) as exc:
        logger.debug("upload_media relay skipped: %s", exc)


@jarvis_tool(
    execution_timeout=90,
    arg_policies={"folder_name": "path"},
)
async def download_images(query: str, count: int = 5,
                          folder_name: str = "Downloaded_Images") -> dict:
    """
    Searches and downloads images from the internet into a local folder.
    Uses jarvis_search.search_images (DuckDuckGo, Tavily, Google CSE).
    """
    try:
        target_dir, folder_err = _resolve_download_dir(folder_name)
        if folder_err:
            return folder_err
        assert target_dir is not None
        os.makedirs(target_dir, exist_ok=True)

        want = max(1, min(int(count), 30))
        logger.info("Searching for %d images for query: '%s'...", want, query)

        results = await search_images(query, want)
        if not results:
            return {
                "status": "error",
                "message": f"❌ Maazrat, '{query}' ke liye koi images nahi mileen (Search engines limited).",
            }

        logger.info("Downloading images to %s", target_dir)
        stem = _sanitize_image_stem(query)
        sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

        async def fetch_one(url: str, index: int) -> str | None:
            async with sem:
                return await _download_single_image(url, index, stem, target_dir)

        outcomes = await asyncio.gather(*(fetch_one(url, i) for i, url in enumerate(results[:want], 1)))
        saved_paths = [p for p in outcomes if p]

        for file_path in saved_paths:
            filename = os.path.basename(file_path)
            media_url = _lan_download_url(file_path)
            await _emit_download_media_update(
                label=f"Downloaded: {filename}",
                url=media_url,
                filename=filename,
                query=query,
            )

        downloaded_count = len(saved_paths)
        return {
            "status": "success",
            "query": query,
            "downloaded_count": downloaded_count,
            "target_directory": target_dir,
            "message": (
                f"✅ Done! {downloaded_count} images for '{query}' download "
                f"karke '{target_dir}' mein save kar di hain, Sir Matloob."
            ),
        }
    except (requests.RequestException, OSError, ValueError, RuntimeError) as e:
        logger.exception("Error in download_images: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error downloading images: {e!s}",
            "error": str(e),
        }


async def _download_single_image(url: str, index: int, stem: str, target_dir: str) -> str | None:
    """Download one image; return saved path or None."""
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        if response.status_code != 200:
            return None
        content_type = response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            return None
        body = response.content
        if not body or len(body) > _MAX_IMAGE_BYTES:
            return None

        ext = _image_ext(url, content_type)
        file_name = f"{stem}_{index}.{ext}"
        file_path = os.path.join(target_dir, file_name)
        with open(file_path, "wb") as handle:
            handle.write(body)
        return file_path
    except (requests.RequestException, OSError) as e:
        logger.warning("Failed to download image %d: %s", index, e)
    return None


@jarvis_tool(
    execution_timeout=180,
    arg_policies={"folder_path": "path", "zip_name": "plain"},
)
async def zip_files(folder_path: str, zip_name: str = "Archive.zip") -> dict:
    """
    Compresses a folder or directory into a .zip archive.
    Use this when the user wants to zip or compress files.
    """
    try:
        source_dir, src_err = _resolve_zip_source(folder_path)
        if src_err:
            return src_err
        assert source_dir is not None

        folder_bytes = await asyncio.to_thread(_directory_size_bytes, source_dir)
        if folder_bytes > _MAX_ZIP_FOLDER_BYTES:
            return {
                "status": "error",
                "message": "❌ Folder 2 GB se zyada hai; zip refuse kar diya gaya.",
            }

        safe_zip = _safe_zip_basename(zip_name)
        archives_dir = _archives_base()
        os.makedirs(archives_dir, exist_ok=True)
        archive_base = os.path.join(archives_dir, os.path.splitext(safe_zip)[0])
        await asyncio.to_thread(shutil.make_archive, archive_base, "zip", source_dir)
        logger.info("Folder '%s' zipped successfully.", source_dir)
        zip_path = f"{archive_base}.zip"
        return {
            "status": "success",
            "zip_path": zip_path,
            "message": f"✅ Folder ko successfully zip kar diya gaya hai: {zip_path}",
        }
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.error("Zip error: %s", e)
        return {
            "status": "error",
            "message": f"❌ Error zipping files: {e!s}",
        }


@jarvis_tool(
    execution_timeout=60,
    arg_policies={
        "recipient": "email_list",
        "subject": "plain",
        "body": "multiline",
        "attachment_path": "path",
    },
    risk="high",
    confirm_summary="Email to {recipient}: {subject}",
)
async def send_email(recipient: str, subject: str, body: str,
                     attachment_path: str = "") -> dict:
    """
    Sends a real email using Gmail SMTP and an App Password.
    Use this when the user asks to email something.
    """
    user_email = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_APP_PASSWORD")

    if not user_email or not password:
        msg = "❌ Error: Email credentials (.env mein EMAIL_USER ya EMAIL_APP_PASSWORD) nahi mile."
        return {
            "status": "error",
            "message": msg,
        }

    resolved_attachment, attach_err = _validate_email_attachment(attachment_path)
    if attach_err:
        attach_err.setdefault("recipient", recipient)
        return attach_err

    has_attachment = bool(resolved_attachment)

    try:
        email_msg = MIMEMultipart()
        email_msg["From"] = user_email
        email_msg["To"] = recipient
        email_msg["Subject"] = Header(subject, "utf-8")
        email_msg.attach(MIMEText(body, "plain", "utf-8"))

        if has_attachment:
            filename = os.path.basename(resolved_attachment)
            try:
                with open(resolved_attachment, "rb") as attachment:
                    payload = attachment.read()
            except OSError as read_err:
                return {
                    "status": "error",
                    "recipient": recipient,
                    "message": f"❌ Attachment read nahi ho saki: {redact_secrets(str(read_err))}",
                    "error_code": "ATTACHMENT_MISSING",
                }
            part = MIMEBase("application", "octet-stream")
            part.set_payload(payload)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename= {filename}")
            email_msg.attach(part)

        smtp_host = _smtp_host()
        smtp_port = _smtp_port()

        def do_send():
            logger.info("Connecting to SMTP server %s:%s...", smtp_host, smtp_port)
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.set_debuglevel(0)
            server.starttls(context=ssl.create_default_context())
            server.login(user_email, password)
            text = email_msg.as_string()
            server.sendmail(user_email, recipient, text)
            server.quit()

        await asyncio.to_thread(do_send)

        jarvis_audit.JarvisAudit.log_event(
            "EMAIL_SENT",
            f"to_count={_email_recipient_count(recipient)} "
            f"subject_len={len(subject or '')} attachment={has_attachment}",
        )
        logger.info("Real Email sent to %s successfully.", recipient)
        return {
            "status": "success",
            "recipient": recipient,
            "subject": subject,
            "message": f"📧 Email successfully bhej diya gaya hai '{recipient}' ko, Sir Matloob.",
        }
    except smtplib.SMTPAuthenticationError:
        logger.error("Email SMTP authentication failed.")
        return {
            "status": "error",
            "recipient": recipient,
            "message": f"❌ {_GMAIL_AUTH_HINT}",
        }
    except (smtplib.SMTPException, ConnectionError, OSError) as e:
        logger.error("Email send error: %s", redact_secrets(str(e)))
        return {
            "status": "error",
            "recipient": recipient,
            "message": f"❌ Error sending email: {redact_secrets(str(e))}",
        }
