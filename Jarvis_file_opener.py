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
        """
        Placeholder decorator for when livekit is not installed.
        """
        return func

# langchain import removed for stability

sys.stdout.reconfigure(encoding='utf-8')


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def focus_window(title_keyword: str) -> bool:
    """
    Attempts to find a window by a keyword in its title and bring it to the foreground.

    Args:
        title_keyword (str): A keyword to search for in window titles.

    Returns:
        bool: True if a matching window was found and focused, False otherwise.
    """
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
    """
    Recursively scans specified directories and indexes all files found.

    Args:
        search_dirs (list): A list of directory paths to scan.

    Returns:
        list: A list of dictionaries containing file metadata (name, path, type).
    """
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
    """
    Performs a fuzzy search to find the best matching file in the index.

    Args:
        query (str): The search term.
        index (list): The list of indexed files.

    Returns:
        dict: The metadata of the best matching file, or None if no good match is found.
    """
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
    """
    Opens a file using the host operating system's default application.

    Args:
        item (dict): The metadata dictionary for the file to open.

    Returns:
        str: A status message indicating success or failure.
    """
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
    """
    Handles a file opening command by searching for the file and then opening it.

    Args:
        command (str): The name of the file to search for.
        index (list): The list of indexed files.

    Returns:
        str: A message indicating the outcome of the command (file opened or not found).
    """
    item = await search_file(command, index)
    if item:
        return await open_file(item)
    logger.warning("❌ File nahi mili.")
    return "❌ File nahi mili."


@function_tool
async def play_video(file_path: str) -> str:
    """
    Opens and plays a video file.
    """
    return await play_file(file_path)


@function_tool
async def play_music(file_path: str) -> str:
    """
    Opens and plays a music file.
    """
    return await play_file(file_path)


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
