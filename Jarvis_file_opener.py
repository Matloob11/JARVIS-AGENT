"""
Jarvis File Opener Module
Handles searching and opening local files by name.
"""
import asyncio
import os
import subprocess
import sys
import time
from fuzzywuzzy import process
from jarvis_logger import setup_logger
try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    from livekit.agents import function_tool
except ImportError:
    from typing import Any

    def _function_tool_placeholder(func: Any) -> Any:
        """
        Placeholder decorator for when livekit is not installed.
        """
        return func
    function_tool = _function_tool_placeholder  # type: ignore

# langchain import removed for stability

sys.stdout.reconfigure(encoding='utf-8')  # type: ignore


# Setup logging
logger = setup_logger("JARVIS-FILE-OPENER")


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


# --- Global Index Cache ---
global_file_index: list[dict] = []
LAST_INDEX_TIME: float = 0.0
INDEX_CACHE_TIMEOUT = 300  # 5 minutes


async def index_files(search_dirs):
    """
    Recursively scans specified directories and indexes all files found.
    Uses a global cache to avoid repetitive slow indexing.
    """
    global global_file_index, LAST_INDEX_TIME  # pylint: disable=global-statement

    current_time = time.time()
    if global_file_index and (current_time - LAST_INDEX_TIME < INDEX_CACHE_TIMEOUT):
        logger.info("⚡ Using cached file index (%d files).",
                    len(global_file_index))
        return global_file_index

    def walk_dirs():
        index = []
        for base_dir in search_dirs:
            if not os.path.exists(base_dir):
                continue
            logger.info("🔍 Indexing directory: %s", base_dir)
            for root, _, files in os.walk(base_dir):
                for f in files:
                    index.append({
                        "name": f,
                        "path": os.path.join(root, f),
                        "type": "file"
                    })
        return index

    logger.info("📂 Indexing %s (Ho sakta hai thoda time lage)...", search_dirs)
    file_index = await asyncio.to_thread(walk_dirs)

    global_file_index = file_index
    LAST_INDEX_TIME = current_time

    logger.info("✅ %s se kul %d files ko index kiya gaya.",
                search_dirs, len(file_index))
    return file_index


async def search_file(query, index):
    """
    Performs a fuzzy search to find the best matching file in the index.
    """
    choices = [item["name"] for item in index]
    if not choices:
        logger.warning("⚠ Match karne ke liye koi files nahi hain.")
        return None

    # Run fuzzy match in a thread if index is huge
    def get_match():
        return process.extractOne(query, choices)

    match_result = await asyncio.to_thread(get_match)
    if not match_result:
        return None

    best_match, score = match_result
    logger.info("🔍 Matched '%s' to '%s' (Score: %d)", query, best_match, score)
    if score > 70:
        for item in index:
            if item["name"] == best_match:
                return item
    return None


async def open_file(item):
    """
    Opens a file using the host operating system's default application.
    """
    try:
        logger.info("📂 File khol rahe hain: %s", item['path'])

        def start_file():
            if os.name == 'nt':
                os.startfile(item["path"])
            else:
                subprocess.call(['open' if sys.platform ==
                                'darwin' else 'xdg-open', item["path"]])

        await asyncio.to_thread(start_file)
        await focus_window(item["name"])  # 👈 Focus window after opening
        return {
            "status": "success",
            "message": f"✅ File open ho gayi: {item['name']}",
            "file_path": item['path'],
            "file_name": item['name']
        }
    except Exception as open_e:  # pylint: disable=broad-exception-caught
        logger.exception("❌ File open karne mein error aaya: %s", open_e)
        return {
            "status": "error",
            "message": f"❌ File open karne mein vifal raha. {open_e}",
            "error": str(open_e)
        }


async def handle_command(command, index):
    """
    Handles a file opening command by searching for the file and then opening it.
    """
    item = await search_file(command, index)
    if item:
        return await open_file(item)
    logger.warning("❌ File nahi mili.")
    return {
        "status": "not_found",
        "message": "❌ Maaf kijiye, mujhe wo file nahi mili."
    }


@function_tool
async def play_video(file_path: str) -> dict:
    """
    Opens and plays a video file.
    """
    return await play_file(file_path)


@function_tool
async def play_music(file_path: str) -> dict:
    """
    Opens and plays a music file.
    """
    return await play_file(file_path)


@function_tool
async def play_file(name: str) -> dict:
    """
    Searches for and opens a file by name from the D:/ drive.
    """
    # If 'name' is already a direct path, skip indexing
    if os.path.isabs(name) and os.path.exists(name):
        logger.info("⚡ Direct path detected, skipping indexing: %s", name)
        return await open_file({"name": os.path.basename(name), "path": name})

    # Specific folders to index for better performance (can add more)
    folders_to_index = ["D:/"]
    index = await index_files(folders_to_index)
    command = name.strip()
    return await handle_command(command, index)
