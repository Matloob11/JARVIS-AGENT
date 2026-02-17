"""
# jarvis_advanced_tools.py
Jarvis Advanced Automation Tools
Handles image downloading, file compression, and mock email simulation.
"""

import os
import shutil
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import requests
from duckduckgo_search import DDGS
from livekit.agents import function_tool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-ADVANCED")


@function_tool
async def download_images(query: str, count: int = 5, folder_name: str = "Downloaded_Images") -> str:
    """
    Simulates searching and downloading images from the internet into a local folder.
    Use this when the user asks to download pictures or photos.
    """
    try:
        base_path = "D:/Jarvis_Downloads"
        target_dir = os.path.join(base_path, folder_name)
        os.makedirs(target_dir, exist_ok=True)

        logger.info("Searching for %d images for query: '%s'...", count, query)

        results = []
        with DDGS() as ddgs:
            ddgs_results = ddgs.images(query, max_results=count)
            for r in ddgs_results:
                results.append(r['image'])

        if not results:
            return f"❌ Maazrat, '{query}' ke liye koi images nahi mileen."

        logger.info("Downloading %d images to %s", len(results), target_dir)

        downloaded_count = 0
        for i, url in enumerate(results, 1):
            try:
                ext = url.split('.')[-1].split('?')[0]
                if ext.lower() not in ['jpg', 'jpeg', 'png', 'webp']:
                    ext = 'jpg'

                file_name = f"{query.replace(' ', '_')}_{i}.{ext}"
                file_path = os.path.join(target_dir, file_name)

                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    with open(file_path, 'wb') as f:
                        f.write(response.content)
                    downloaded_count += 1
            except Exception as e:
                logger.warning("Failed to download image %d: %s", i, e)

        return (f"✅ Done! {downloaded_count} real images for '{query}' download karke "
                f"'{target_dir}' mein save kar di hain, Sir Matloob.")
    except Exception as e:
        logger.error("Error in download_images: %s", e)
        return f"❌ Error downloading images: {str(e)}"


@function_tool
async def zip_files(folder_path: str, zip_name: str = "Archive.zip") -> str:
    """
    Compresses a folder or directory into a .zip archive.
    Use this when the user wants to zip or compress files.
    """
    _ = zip_name  # Suppress unused argument warning
    try:
        # Use zip_name to avoid unused argument warning
        _ = zip_name
        # Resolve path - if folder_path is just a name, look in common locations
        if not os.path.isabs(folder_path):
            # Try D:/Jarvis_Downloads first
            base_dir = "D:/Jarvis_Downloads"
            actual_path = os.path.join(base_dir, folder_path)
            if not os.path.exists(actual_path):
                return f"❌ Folder '{folder_path}' nahi mila."
            folder_path = actual_path

        shutil.make_archive(folder_path, 'zip', folder_path)
        logger.info("Folder '%s' zipped successfully.", folder_path)
        return f"✅ Folder ko successfully zip kar diya gaya hai: {folder_path}.zip"
    except Exception as e:
        logger.error("Error in zip_files: %s", e)
        return f"❌ Error zipping files: {str(e)}"


@function_tool
async def send_email(recipient: str, subject: str, body: str, attachment_path: str = "") -> str:
    """
    Sends a real email using Gmail SMTP and an App Password.
    Use this when the user asks to email something.
    """
    user_email = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_APP_PASSWORD")

    if not user_email or not password:
        return "❌ Error: Email credentials (.env mein EMAIL_USER ya EMAIL_APP_PASSWORD) nahi mile."

    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = user_email
        msg['To'] = recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Handle attachment
        if attachment_path and os.path.exists(attachment_path):
            filename = os.path.basename(attachment_path)
            with open(attachment_path, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                f"attachment; filename= {filename}")
                msg.attach(part)

        # Connect and send
        logger.info("Connecting to Gmail SMTP server...")
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(user_email, password)
        text = msg.as_string()
        server.sendmail(user_email, recipient, text)
        server.quit()

        logger.info("Real Email sent to %s successfully.", recipient)
        return f"📧 Email successfully bhej diya gaya hai '{recipient}' ko, Sir Matloob."
    except Exception as e:
        logger.error("Error in send_email: %s", e)
        return f"❌ Error sending email: {str(e)}"
