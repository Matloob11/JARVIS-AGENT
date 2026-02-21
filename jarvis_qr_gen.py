"""
# jarvis_qr_gen.py
Jarvis QR Code Generation Tool
Supports stylish QR codes with "Modern Dots" style as requested.
"""

import os
import asyncio
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import CircleModuleDrawer
from qrcode.image.styles.colormasks import SolidFillColorMask
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logger
logger = setup_logger("JARVIS-QR")


@function_tool
async def generate_qr_code(data: str, filename: str = "my_stylish_qr.png") -> dict:
    """
    Generates a stylish QR code with dots style (Modern Dots).
    Use this when the user asks to create or generate a QR code for a URL, text, or number.
    """
    try:
        # Base directory for QR codes
        base_dir = os.path.join(os.getcwd(), "Jarvis_Outputs", "QR_Codes")
        os.makedirs(base_dir, exist_ok=True)

        if not filename.lower().endswith('.png'):
            filename += ".png"

        file_path = os.path.join(base_dir, filename)

        logger.info("Generating Stylish QR (Dots) for: %s", data)

        def make_qr():
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4
            )
            qr.add_data(data)
            qr.make(fit=True)

            # Modern Dots style
            drawer = CircleModuleDrawer()
            # Crimson/Red color mask
            color_mask = SolidFillColorMask(
                back_color=(255, 255, 255),
                front_color=(233, 69, 96)
            )

            img = qr.make_image(
                image_factory=StyledPilImage,
                module_drawer=drawer,
                color_mask=color_mask
            )
            img.save(file_path)
            os.startfile(file_path)
            return file_path

        await asyncio.to_thread(make_qr)

        logger.info("QR Code saved successfully to: %s", file_path)

        return {
            "status": "success",
            "data": data,
            "file_path": file_path,
            "message": f"✅ Sir Matloob, aapka stylish dots wala QR code '{filename}' ke naam se save kar diya gaya hai. Path: {file_path}"
        }

    except Exception as e:
        logger.exception("Error in generate_qr_code: %s", e)
        return {
            "status": "error",
            "message": f"❌ Maazrat Sir, QR code generate karne mein error aaya: {str(e)}",
            "error": str(e)
        }
