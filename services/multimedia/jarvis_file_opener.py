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

try:
    import pygetwindow as gw
except ImportError:
    gw = None

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

sys.stdout.reconfigure(encoding='utf-8')  # type: ignore

# Setup logging
logger = setup_logger("JARVIS-FILE-OPENER")


async def focus_window(title_keyword: str) -> bool:
    """
    Attempts to find a window by a keyword in its title and bring it to the foreground.
    """
    if not gw:
        logger.warning("⚠ pygetwindow")
        return False

    await asyncio.sleep(1.5)
    title_keyword = title_keyword.lower().strip()

    for window in gw.getAllWindows():
        try:
            if title_keyword in window.title.lower():
                if window.isMinimized:
                    window.restore()
                window.activate()
                logger.info("🪟 window focus mein hai: %s", window.title)
                return True
        except (AttributeError, RuntimeError) as e:
            logger.debug("Window focus error for %s: %s", window.title, e)
    logger.warning("⚠ Focus karne ke liye window nahi mili.")
    return False


# --- Global Index Cache ---
global_file_index: list[dict] = []
LAST_INDEX_TIME: float = 0.0
INDEX_CACHE_TIMEOUT = 300  # 5 minutes


async def index_files(search_dirs):
    """
    Recursively scans specified directories and indexes all files found.
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
                        "type": "file",
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
                os.startfile(item["path"])  # nosec B606
            else:
                subprocess.call(['open' if sys.platform ==
                                'darwin' else 'xdg-open', item["path"]])

        await asyncio.to_thread(start_file)
        await focus_window(item["name"])  # 👈 Focus window after opening
        return {
            "status": "success",
            "message": f"✅ File open ho gayi: {item['name']}",
            "file_path": item['path'],
            "file_name": item['name'],
        }
    except (OSError, ValueError, subprocess.SubprocessError) as open_e:
        logger.exception("❌ File open karne mein error aaya: %s", open_e)
        return {
            "status": "error",
            "message": f"❌ File open karne mein vifal raha. {open_e}",
            "error": str(open_e),
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
        "message": "❌ Maaf kijiye, mujhe wo file nahi mili.",
    }


@jarvis_tool
async def play_video(file_path: str) -> dict:
    """
    Opens and plays a video file.
    """
    return await play_file(file_path)


@jarvis_tool
async def play_music(file_path: str) -> dict:
    """
    Opens and plays a music file.
    """
    return await play_file(file_path)


@jarvis_tool
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


@jarvis_tool
async def close_file_window_tool(name_keyword: str) -> dict:
    """
    Closes an open file window (like Image Viewer, Notepad, etc) by its title keyword.
    Use this when the user says "image close kar do" or "Notepad band karo".
    """
    if not gw:
        return {"status": "error", "message": "Window control library (pygetwindow) not available."}

    keyword = name_keyword.lower().strip()
    closed_count = 0

    for window in gw.getAllWindows():
        try:
            if keyword in window.title.lower() and window.title.strip():
                window.close()
                logger.info("❌ Window closed: %s", window.title)
                closed_count += 1
        except (AttributeError, RuntimeError, Exception) as e:
            logger.debug("Window close error for %s: %s", window.title, e)

    if closed_count > 0:
        return {"status": "success", "message": f"✅ {closed_count} windows related to '{name_keyword}' closed."}

    return {"status": "not_found", "message": f"❌ '{name_keyword}' title wali koi window nahi mili."}


@jarvis_tool
async def list_generated_media_tool(media_type: str = "all") -> dict:
    """
    Lists all images or QR codes generated by JARVIS so far.
    media_type: "images", "qr", or "all".
    """
    from services.utils.jarvis_config import config
    results = []

    paths = {
        "images": os.path.join(config.shared_dir, "Generated_Images"),
        "qr": os.path.join(config.shared_dir, "QR_Codes")
    }

    try:
        target_keys = ["images", "qr"] if media_type == "all" else [media_type]

        for key in target_keys:
            path = paths.get(key)
            if path and os.path.exists(path):
                for f in os.listdir(path):
                    f_path = os.path.join(path, f)
                    if os.path.isfile(f_path):
                        mtime = os.path.getmtime(f_path)
                        results.append({
                            "name": f,
                            "type": key,
                            "path": f_path,
                            "created": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)),
                            "timestamp": mtime
                        })

        # Sort by newest first
        results.sort(key=lambda x: x["timestamp"], reverse=True)

        if not results:
            return {"status": "empty", "message": f"Sir, abhi tak koi {media_type} generate nahi hui hain."}

        # Format message
        msg = f"Sir, mujhe {len(results)} items milay hain:\n"
        for i, item in enumerate(results[:10], 1): # Show top 10
            msg += f"{i}. {item['name']} ({item['type'].upper()} - {item['created']})\n"

        return {
            "status": "success",
            "count": len(results),
            "media": results,
            "message": msg
        }

    except Exception as e:
        logger.error("Error listing generated media: %s", e)
        return {"status": "error", "message": f"Media list karne mein error aaya: {e}"}
