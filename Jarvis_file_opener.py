"""
Jarvis File Opener Module
Handles searching and opening local files by name.
"""
import asyncio
import os
import subprocess
import sys
import logging
from fuzzywuzzy import process
try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    from livekit.agents import function_tool
except ImportError:
    def function_tool(func):
        return func

# langchain import removed for stability

sys.stdout.reconfigure(encoding='utf-8')


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def focus_window(title_keyword: str) -> bool:
    if not gw:
        logger.warning("⚠ pygetwindow")
        return False

    await asyncio.sleep(1.5)
    title_keyword = title_keyword.lower().strip()

    for window in gw.getAllWindows():
        if title_keyword in window.title.lower():
            if window.isMinimized:
                window.restore()
            window.activate()
            logger.info("🪟 window focus mein hai: %s", window.title)
            return True
    logger.warning("⚠ Focus karne ke liye window nahi mili.")
    return False


async def index_files(search_dirs):
    file_index = []
    for base_dir in search_dirs:
        for root, _, files in os.walk(base_dir):
            for f in files:
                file_index.append({
                    "name": f,
                    "path": os.path.join(root, f),
                    "type": "file"
                })
    logger.info(
        "✅ %s se kul %d files ko index kiya gaya.", search_dirs, len(file_index))
    return file_index


async def search_file(query, index):
    choices = [item["name"] for item in index]
    if not choices:
        logger.warning("⚠ Match karne ke liye koi files nahi hain.")
        return None

    best_match, score = process.extractOne(query, choices)
    logger.info("🔍 Matched '%s' to '%s' (Score: %d)", query, best_match, score)
    if score > 70:
        for item in index:
            if item["name"] == best_match:
                return item
    return None


async def open_file(item):
    try:
        logger.info("📂 File khol rahe hain: %s", item['path'])
        if os.name == 'nt':
            os.startfile(item["path"])
        else:
            subprocess.call(['open' if sys.platform ==
                            'darwin' else 'xdg-open', item["path"]])
        await focus_window(item["name"])  # 👈 Focus window after opening
        return f"✅ File open ho gayi: {item['name']}"
    except Exception as open_e:  # pylint: disable=broad-exception-caught
        logger.error("❌ File open karne mein error aaya: %s", open_e)
        return f"❌ File open karne mein vifal raha. {open_e}"


async def handle_command(command, index):
    item = await search_file(command, index)
    if item:
        return await open_file(item)
    logger.warning("❌ File nahi mili.")
    return "❌ File nahi mili."


@function_tool
async def play_file(name: str) -> str:
    """
    Searches for and opens a file by name from the D:/ drive.

    Use this tool when the user wants to open a file like a video, PDF, document, image, etc.
    Example prompts:
    - "D drive se my resume kholo"
    - "Open D:/project report"
    - "MP4 file play karo"
    """

    folders_to_index = ["D:/"]
    index = await index_files(folders_to_index)
    command = name.strip()
    return await handle_command(command, index)
