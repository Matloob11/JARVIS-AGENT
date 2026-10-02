"""
# jarvis_qr_gen.py
Jarvis QR Code Generation Tool
Supports stylish QR codes with "Modern Dots" style as requested.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask
from qrcode.image.styles.moduledrawers import CircleModuleDrawer

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils import jarvis_delivery
from services.utils.jarvis_bridge import notify_event
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-QR")

_DEFAULT_FILENAME = "my_stylish_qr.png"
_QR_MEDIUM_CHARS = 1200
_QR_MAX_CHARS = 2900
_UNSAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize_qr_filename(filename: str) -> str:
    """Basename only; allow letters, digits, '.', '_', '-'; always end with .png."""
    base = os.path.basename((filename or "").strip() or _DEFAULT_FILENAME)
    if base in (".", ".."):
        base = _DEFAULT_FILENAME
    stem, ext = os.path.splitext(base)
    if ext.lower() != ".png":
        stem = stem or base.replace(ext, "") or "my_stylish_qr"
    else:
        stem = stem or "my_stylish_qr"
    stem = _UNSAFE_FILENAME_CHARS.sub("_", stem).strip("._") or "my_stylish_qr"
    return f"{stem}.png"


def _resolve_qr_output_dir() -> str:
    shared = (config.shared_dir or "").strip() or os.path.join(
        os.path.expanduser("~"), "Jarvis_Shared"
    )
    base_dir = os.path.join(shared, "QR_Codes")
    try:
        os.makedirs(base_dir, exist_ok=True)
        return base_dir
    except OSError as exc:
        fallback = os.path.join(os.path.expanduser("~"), "Jarvis_Shared", "QR_Codes")
        logger.warning(
            "QR output dir %s unavailable (%s); falling back to %s",
            base_dir,
            exc,
            fallback,
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _error_correction_level(data: str) -> int | None:
    length = len(data)
    if length > _QR_MAX_CHARS:
        return None
    if length > _QR_MEDIUM_CHARS:
        return qrcode.constants.ERROR_CORRECT_M
    return qrcode.constants.ERROR_CORRECT_H


def _lan_qr_url(filepath: str) -> str:
    """Build a LAN URL relative to the file-server root (when the server is running)."""
    from services.system.jarvis_file_server import get_local_ip  # pylint: disable=import-outside-toplevel

    root = getattr(config, "file_server_root", None) or config.shared_dir
    try:
        rel = os.path.relpath(filepath, root).replace("\\", "/")
    except ValueError:
        rel = f"{os.path.basename(config.shared_dir)}/QR_Codes/{os.path.basename(filepath)}"
    local_ip = get_local_ip()
    return f"http://{local_ip}:8000/{rel}"


async def _emit_qr_update(*, label: str, url: str, filename: str, data: str) -> None:
    meta = {"kind": "qr", "filename": filename}
    await notify_event(
        "intelligence_update",
        {
            "type": "image",
            "label": label,
            "url": url,
            "data": {"filename": filename, "payload_chars": len(data), **meta},
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


@jarvis_tool(arg_policies={"data": "raw", "filename": "plain"})
async def generate_qr_code(
    data: str,
    filename: str = _DEFAULT_FILENAME,
    open_file: bool = True,
) -> dict[str, Any]:
    """
    Generates a stylish QR code with dots style (Modern Dots).
    Use this when the user asks to create or generate a QR code for a URL, text, or number.
    """
    try:
        ec_level = _error_correction_level(data)
        if ec_level is None:
            return {
                "status": "error",
                "message": (
                    f"❌ Maazrat Sir, QR data {len(data)} characters ka hai; "
                    f"maximum {_QR_MAX_CHARS} allowed hain."
                ),
                "error": "data_too_long",
            }

        safe_name = _sanitize_qr_filename(filename)
        base_dir = _resolve_qr_output_dir()
        file_path = os.path.join(base_dir, safe_name)

        logger.info("Generating Stylish QR (Dots) for: %s", data)

        def make_qr() -> str:
            qr = qrcode.QRCode(
                version=1,
                error_correction=ec_level,
                box_size=10,
                border=4,
            )
            qr.add_data(data)
            qr.make(fit=True)

            drawer = CircleModuleDrawer()
            color_mask = SolidFillColorMask(
                back_color=(255, 255, 255),
                front_color=(233, 69, 96),
            )

            img = qr.make_image(
                image_factory=StyledPilImage,
                module_drawer=drawer,
                color_mask=color_mask,
            )
            img.save(file_path)
            if open_file:
                jarvis_delivery.open_artifact(file_path)
            return file_path

        saved_path = await asyncio.to_thread(make_qr)

        logger.info("QR Code saved successfully to: %s", saved_path)

        url = _lan_qr_url(saved_path)
        await _emit_qr_update(
            label=f"QR · {safe_name}",
            url=url,
            filename=safe_name,
            data=data,
        )

        return {
            "status": "success",
            "data": data,
            "file_path": saved_path,
            "path": saved_path,
            "url": url,
            "filename": safe_name,
            "opened_file": open_file,
            "message": (
                f"✅ Sir Matloob, aapka stylish dots wala QR code '{safe_name}' "
                f"ke naam se save kar diya gaya hai. Path: {saved_path}"
            ),
        }

    except (OSError, ValueError, RuntimeError) as e:
        logger.exception("Error in generate_qr_code: %s", e)
        return {
            "status": "error",
            "message": f"❌ Maazrat Sir, QR code generate karne mein error aaya: {e!s}",
            "error": str(e),
        }
