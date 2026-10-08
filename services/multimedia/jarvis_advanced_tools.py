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
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils import jarvis_audit
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_security import redact_secrets

# Setup logging
logger = setup_logger("JARVIS-ADVANCED")

_UNSAFE_NAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")
_MAX_EMAIL_ATTACHMENT_BYTES = 20 * 1024 * 1024
_GMAIL_AUTH_HINT = (
    "Gmail ne app password reject kiya - myaccount.google.com/apppasswords se naya banayein "
    "(2-Step Verification ON honi chahiye)"
)


def _safe_folder_basename(name: str, default: str) -> str:
    base = os.path.basename((name or default).strip() or default)
    cleaned = _UNSAFE_NAME_CHARS.sub("_", base).strip("._") or default
    return cleaned[:120]


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


@jarvis_tool
async def download_images(query: str, count: int = 5,
                          folder_name: str = "Downloaded_Images") -> dict:
    """
    Searches and downloads images from the internet into a local folder.
    Cascades through: DuckDuckGo -> Google Custom Search.
    """
    try:
        safe_folder = _safe_folder_basename(folder_name, "Downloaded_Images")
        base_path = os.path.join(os.getcwd(), "Jarvis_Outputs", "Downloads")
        target_dir = os.path.abspath(os.path.join(base_path, safe_folder))
        os.makedirs(target_dir, exist_ok=True)

        logger.info("Searching for %d images for query: '%s'...", count, query)

        results = []
        
        # 1. Try DuckDuckGo
        try:
            def search_images_ddg():
                ddg_results = []
                with DDGS() as ddgs:
                    for r in ddgs.images(query, max_results=count):
                        ddg_results.append(r['image'])
                return ddg_results
            
            results = await asyncio.to_thread(search_images_ddg)
            logger.info("DuckDuckGo found %d images.", len(results))
        except Exception as e:
            logger.warning("DuckDuckGo Image Search failed (likely rate limit): %s", e)
            
            # 2. Fallback to Google Custom Search
            google_key = os.getenv("GOOGLE_SEARCH_API_KEY")
            google_cx = os.getenv("SEARCH_ENGINE_ID")
            
            if google_key and google_cx:
                logger.info("Attempting Google Image Search fallback...")
                results = await _search_images_google(query, count, google_key, google_cx)
                logger.info("Google Image Search found %d images.", len(results))
            else:
                logger.error("No Google Search credentials found for fallback.")

        if not results:
            return {
                "status": "error",
                "message": f"❌ Maazrat, '{query}' ke liye koi images nahi mileen (Search engines limited).",
            }

        logger.info("Downloading images to %s", target_dir)

        downloaded_count = 0
        for i, url in enumerate(results, 1):
            if downloaded_count >= count:
                break
            downloaded_count += await _download_single_image(url, i, query, target_dir)

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


async def _search_images_google(query: str, count: int, api_key: str, cx: str) -> list[str]:
    """Helper to search images using Google Custom Search API."""
    try:
        from urllib.parse import quote
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}&cx={cx}&q={quote(query)}&searchType=image&num={count}"
        )
        
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        return [item['link'] for item in data.get('items', [])]
    except Exception as e:
        logger.error("Google Image Search failed: %s", e)
        return []


async def _download_single_image(url: str, index: int, query: str, target_dir: str) -> int:
    """Helper to download a single image."""
    try:
        ext = url.rsplit('.', maxsplit=1)[-1].split('?', maxsplit=1)[0]
        if ext.lower() not in ['jpg', 'jpeg', 'png', 'webp']:
            ext = 'jpg'

        file_name = f"{query.replace(' ', '_')}_{index}.{ext}"
        file_path = os.path.join(target_dir, file_name)

        response = await asyncio.to_thread(requests.get, url, timeout=10)
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                f.write(response.content)
            return 1
    except (requests.RequestException, OSError) as e:
        logger.warning("Failed to download image %d: %s", index, e)
    return 0


@jarvis_tool
async def zip_files(folder_path: str, zip_name: str = "Archive.zip") -> dict:
    """
    Compresses a folder or directory into a .zip archive.
    Use this when the user wants to zip or compress files.
    """
    try:
        # Resolve path - if folder_path is just a name, look in common locations
        if not os.path.isabs(folder_path):
            # Try Jarvis_Outputs first
            base_dir = os.path.join(os.getcwd(), "Jarvis_Outputs")
            actual_path = os.path.abspath(os.path.join(base_dir, folder_path))
            if not os.path.exists(actual_path):
                return {
                    "status": "error",
                    "message": f"❌ Folder '{folder_path}' nahi mila.",
                }
            folder_path = actual_path

        safe_zip = _safe_zip_basename(zip_name)
        archive_base = os.path.join(os.path.dirname(folder_path), os.path.splitext(safe_zip)[0])
        await asyncio.to_thread(shutil.make_archive, archive_base, "zip", folder_path)
        logger.info("Folder '%s' zipped successfully.", folder_path)
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
