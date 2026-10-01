"""
Jarvis File Opener Module
Handles searching and opening local files by name (X-35).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from fuzzywuzzy import fuzz, process

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_desktop_common import (
    EXEC_EXTS,
    MEDIA_AUDIO_EXTS,
    MEDIA_VIDEO_EXTS,
    close_windows_gracefully,
    find_windows,
    force_foreground,
    is_network_path,
    is_protected_window,
    is_sensitive_path,
    tool_result,
)
from services.utils.jarvis_logger import setup_logger

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore

logger = setup_logger("JARVIS-FILE-OPENER")

INDEX_REFRESH_INTERVAL_S = 600  # 10 minutes
INDEX_START_DELAY_S = 60
INDEX_EMPTY_GRACE_S = 10
FUZZ_SCORE_CUTOFF = 70

_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    "node_modules",
    ".git",
    "appdata",
    "__pycache__",
    "$recycle.bin",
})

global_file_index: list[dict[str, Any]] = []
_index_mtime: float = 0.0
_index_build_started: float | None = None
_background_index_task: asyncio.Task[None] | None = None
_background_started: bool = False

# ---------------------------------------------------------------------------
# Index paths and roots
# ---------------------------------------------------------------------------


def file_index_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "JARVIS" / "file_index.json"


def _default_media_roots() -> list[str]:
    home = os.path.expanduser("~")
    from services.utils.jarvis_config import config

    shared = getattr(config, "shared_dir", os.path.join(home, "Jarvis_Shared"))
    roots = [
        os.path.join(home, name)
        for name in ("Music", "Videos", "Downloads", "Desktop", "Documents")
    ]
    roots.append(shared)
    if os.path.exists("D:/"):
        roots.append("D:/")
    return [r for r in roots if os.path.isdir(r)]


def get_index_roots() -> list[str]:
    from services.utils.jarvis_config import config

    roots = getattr(config, "media_dirs", None)
    if not roots:
        roots = _default_media_roots()
    return [r for r in roots if os.path.isdir(r)]


def _should_skip_dir(dirname: str, dir_path: str) -> bool:
    low = dirname.lower()
    if low in _SKIP_DIR_NAMES or low.startswith(".venv"):
        return True
    if is_sensitive_path(dir_path) or is_network_path(dir_path):
        return True
    return False


def _should_skip_file(file_path: str) -> bool:
    if is_sensitive_path(file_path) or is_network_path(file_path):
        return True
    return False


def _build_index_sync(roots: Sequence[str]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for base_dir in roots:
        if not os.path.isdir(base_dir):
            continue
        logger.info("Indexing directory: %s", base_dir)
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [
                d
                for d in dirs
                if not _should_skip_dir(d, os.path.join(root, d))
            ]
            for fname in files:
                full = os.path.join(root, fname)
                if _should_skip_file(full):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                index.append(
                    {
                        "name": fname,
                        "path": full,
                        "ext": ext,
                        "size": int(st.st_size),
                        "mtime": float(st.st_mtime),
                    }
                )
    return index


def _load_index_from_disk() -> list[dict[str, Any]] | None:
    path = file_index_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Could not load file index from %s: %s", path, exc)
    return None


def _save_index_to_disk(index: list[dict[str, Any]]) -> None:
    path = file_index_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist file index to %s: %s", path, exc)


async def refresh_file_index(*, force: bool = False) -> list[dict[str, Any]]:
    """Rebuild the in-memory index from configured roots and persist to LOCALAPPDATA."""
    global global_file_index, _index_mtime, _index_build_started  # pylint: disable=global-statement

    _index_build_started = time.monotonic()
    roots = get_index_roots()
    if not roots:
        logger.warning("No media index roots configured or present.")
        global_file_index = []
        _index_mtime = time.time()
        return global_file_index

    file_index = await asyncio.to_thread(_build_index_sync, roots)
    global_file_index = file_index
    _index_mtime = time.time()
    await asyncio.to_thread(_save_index_to_disk, file_index)
    logger.info("Indexed %d files from %d roots.", len(file_index), len(roots))
    return file_index


async def _ensure_index_loaded() -> list[dict[str, Any]]:
    global global_file_index, _index_mtime  # pylint: disable=global-statement

    if global_file_index:
        return global_file_index

    disk = await asyncio.to_thread(_load_index_from_disk)
    if disk:
        global_file_index = disk
        _index_mtime = time.time()
        return global_file_index

    return await refresh_file_index(force=True)


async def _background_index_loop() -> None:
    try:
        await asyncio.sleep(INDEX_START_DELAY_S)
        while True:
            try:
                await refresh_file_index(force=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Background file index refresh failed: %s", exc)
            await asyncio.sleep(INDEX_REFRESH_INTERVAL_S)
    except asyncio.CancelledError:
        logger.debug("File index background loop cancelled.")


def start_file_index_background() -> None:
    """Schedule periodic index refresh (60 s after first call, then every 10 min)."""
    global _background_index_task, _background_started  # pylint: disable=global-statement

    if _background_started:
        return
    _background_started = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _background_index_task = loop.create_task(_background_index_loop())


# ---------------------------------------------------------------------------
# Search / open
# ---------------------------------------------------------------------------


def _indexing_started_response() -> dict[str, Any]:
    return tool_result(
        "started",
        "Indexing chal rahi hai, 10 second baad dobara kahen.",
        tool="play_file",
        data={"retry_after_s": INDEX_EMPTY_GRACE_S},
    )


def _index_still_empty() -> bool:
    if global_file_index:
        return False
    if _index_build_started is None:
        return True
    return (time.monotonic() - _index_build_started) < INDEX_EMPTY_GRACE_S


async def search_file(
    query: str,
    index: Sequence[dict[str, Any]],
    *,
    allowed_exts: Optional[frozenset[str]] = None,
) -> dict[str, Any] | None:
    """Fuzzy match with WRatio; prefer media extensions when filtered."""
    if not index:
        return None

    def _pick(pool: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
        choices = [item["name"] for item in pool]
        if not choices:
            return None

        def get_match() -> tuple[str, int] | None:
            return process.extractOne(
                query,
                choices,
                scorer=fuzz.WRatio,
                score_cutoff=FUZZ_SCORE_CUTOFF,
            )

        match_result = get_match()
        if not match_result:
            return None
        best_match, score = match_result
        logger.info("Matched '%s' to '%s' (score: %d)", query, best_match, score)
        for item in pool:
            if item["name"] == best_match:
                return item
        return None

    if allowed_exts:
        media_pool = [i for i in index if i.get("ext") in allowed_exts]
        hit = _pick(media_pool)
        if hit:
            return hit
    return _pick(index)


async def focus_window(title_keyword: str) -> bool:
    """Find a window by title keyword and bring it to the foreground."""
    needle = title_keyword.lower().strip()
    if not needle:
        return False
    await asyncio.sleep(1.5)
    matches = find_windows(needle, visible_only=True)
    if not matches:
        logger.warning("Focus ke liye window nahi mili: %s", title_keyword)
        return False
    ok = await force_foreground(matches[0].hwnd)
    if ok:
        logger.info("Window focus mein hai: %s", matches[0].title)
    return ok


async def open_file(item: dict[str, Any], *, tool: str = "play_file") -> dict[str, Any]:
    path = item["path"]
    try:
        logger.info("File khol rahe hain: %s", path)

        def start_file() -> None:
            if os.name == "nt":
                os.startfile(path)  # nosec B606
            else:
                subprocess.call(
                    ["open" if sys.platform == "darwin" else "xdg-open", path]
                )

        await asyncio.to_thread(start_file)
        await focus_window(item["name"])
        return tool_result(
            "success",
            f"File open ho gayi: {item['name']}",
            tool=tool,
            data={"file_path": path, "file_name": item["name"]},
            file_path=path,
            file_name=item["name"],
        )
    except (OSError, ValueError, subprocess.SubprocessError) as open_e:
        logger.exception("File open karne mein error: %s", open_e)
        return tool_result(
            "error",
            f"File open karne mein vifal raha. {open_e}",
            tool=tool,
            error=str(open_e),
        )


async def _play_by_name(
    name: str,
    *,
    tool: str,
    allowed_exts: Optional[frozenset[str]] = None,
) -> dict[str, Any]:
    start_file_index_background()
    command = name.strip()
    if not command:
        return tool_result("error", "File ka naam khali hai.", tool=tool)

    if os.path.isabs(command) and os.path.exists(command):
        logger.info("Direct path detected, skipping index: %s", command)
        base = os.path.basename(command)
        ext = os.path.splitext(base)[1].lower()
        if ext in EXEC_EXTS:
            return tool_result(
                "needs_confirmation",
                "Yeh executable file hai; auto-open nahi hoti. folder_file se path confirm karke kholein.",
                tool=tool,
                data={
                    "suggested_tool": "folder_file",
                    "file_path": command,
                },
                hint=f'folder_file path="{command}"',
            )
        if allowed_exts and ext not in allowed_exts:
            return tool_result(
                "not_found",
                f"Is tool ke liye sahi media type nahi mili: {base}",
                tool=tool,
            )
        return await open_file({"name": base, "path": command}, tool=tool)

    await _ensure_index_loaded()
    if _index_still_empty():
        asyncio.create_task(refresh_file_index(force=True))
        return _indexing_started_response()

    item = await search_file(command, global_file_index, allowed_exts=allowed_exts)
    if not item:
        return tool_result(
            "not_found",
            "Maaf kijiye, mujhe wo file nahi mili.",
            tool=tool,
        )

    ext = item.get("ext") or os.path.splitext(item["path"])[1].lower()
    if ext in EXEC_EXTS:
        return tool_result(
            "needs_confirmation",
            "Best match ek executable hai; auto-open nahi hoti. folder_file se path confirm karke kholein.",
            tool=tool,
            data={
                "suggested_tool": "folder_file",
                "file_path": item["path"],
                "file_name": item["name"],
            },
            hint=f'folder_file path="{item["path"]}"',
        )

    return await open_file(item, tool=tool)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@jarvis_tool(execution_timeout=30, risk="low")
async def play_video(file_path: str) -> dict:
    """Opens and plays a video file from the local media index."""
    return await _play_by_name(file_path, tool="play_video", allowed_exts=MEDIA_VIDEO_EXTS)


@jarvis_tool(execution_timeout=30, risk="low")
async def play_music(file_path: str) -> dict:
    """Opens and plays a music/audio file from the local media index."""
    return await _play_by_name(file_path, tool="play_music", allowed_exts=MEDIA_AUDIO_EXTS)


@jarvis_tool(execution_timeout=30, risk="low")
async def play_file(name: str) -> dict:
    """Searches configured media folders and opens the best-matching file by name."""
    return await _play_by_name(name, tool="play_file", allowed_exts=None)


def _close_file_window_risk(*_a: Any, **_k: Any) -> str:
    """DesktopToolPolicy: medium when more than one window would match (evaluated at call)."""
    return "low"


@jarvis_tool(execution_timeout=15, risk=_close_file_window_risk)
async def close_file_window_tool(name_keyword: str) -> dict:
    """
    Closes open file viewer windows (Photos, Notepad, etc.) by title keyword.
    Protected windows (JARVIS UI, terminals, IDE) are never closed.
    """
    keyword = (name_keyword or "").strip()
    if len(keyword) < 3:
        return tool_result(
            "error",
            "Window band karne ke liye kam az kam 3 characters ka keyword chahiye.",
            tool="close_file_window_tool",
        )

    needle = keyword.lower()
    candidates = [
        w
        for w in find_windows(needle, visible_only=True)
        if w.title.strip() and not is_protected_window(w)
    ]

    if not candidates:
        return tool_result(
            "not_found",
            f"'{name_keyword}' title wali koi safe window nahi mili.",
            tool="close_file_window_tool",
        )

    risk = "medium" if len(candidates) > 1 else "low"
    close_result = await close_windows_gracefully([w.hwnd for w in candidates])
    closed_hwnds = set(close_result.get("closed") or [])
    closed_titles = [w.title for w in candidates if w.hwnd in closed_hwnds]

    if not closed_titles:
        return tool_result(
            "error",
            "Matching windows mili lekin band nahi ho sakin.",
            tool="close_file_window_tool",
            data={"risk": risk, "matched": len(candidates)},
            risk=risk,
        )

    msg = f"{len(closed_titles)} window(s) band kar di: {', '.join(closed_titles[:5])}"
    return tool_result(
        "success",
        msg,
        tool="close_file_window_tool",
        data={"closed_titles": closed_titles, "risk": risk},
        risk=risk,
    )


def _generated_media_paths() -> dict[str, str]:
    from services.utils.jarvis_config import config

    outputs = getattr(config, "outputs_dir", os.path.join(config.project_root, "Jarvis_Outputs"))
    shared = config.shared_dir
    return {
        "images": os.path.join(shared, "Generated_Images"),
        "qr": os.path.join(shared, "QR_Codes"),
        "downloads": os.path.join(shared, "Downloads"),
        "leads": os.path.join(outputs, "leads"),
    }


@jarvis_tool(execution_timeout=10, risk="low")
async def list_generated_media_tool(media_type: str = "all") -> dict:
    """
    Lists JARVIS-generated images, QR codes, downloads, and leads files.
    media_type: images, qr, downloads, leads, or all.
    """
    paths = _generated_media_paths()
    key = (media_type or "all").lower().strip()
    if key == "all":
        target_keys = list(paths.keys())
    elif key in paths:
        target_keys = [key]
    else:
        return tool_result(
            "error",
            "media_type images, qr, downloads, leads, ya all hona chahiye.",
            tool="list_generated_media_tool",
        )

    results: list[dict[str, Any]] = []

    def _scan() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tkey in target_keys:
            path = paths.get(tkey)
            if not path or not os.path.isdir(path):
                continue
            for fname in os.listdir(path):
                f_path = os.path.join(path, fname)
                if not os.path.isfile(f_path):
                    continue
                mtime = os.path.getmtime(f_path)
                out.append(
                    {
                        "name": fname,
                        "type": tkey,
                        "path": f_path,
                        "created": time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(mtime)
                        ),
                        "timestamp": mtime,
                    }
                )
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out

    try:
        results = await asyncio.to_thread(_scan)
        if not results:
            return tool_result(
                "empty",
                f"Abhi tak koi {media_type} generate nahi hui.",
                tool="list_generated_media_tool",
            )

        msg = f"Sir, mujhe {len(results)} items milay hain:\n"
        for i, item in enumerate(results[:10], 1):
            msg += f"{i}. {item['name']} ({item['type'].upper()} - {item['created']})\n"

        return tool_result(
            "success",
            msg,
            tool="list_generated_media_tool",
            count=len(results),
            media=results,
        )
    except OSError as exc:
        logger.error("Error listing generated media: %s", exc)
        return tool_result(
            "error",
            f"Media list karne mein error aaya: {exc}",
            tool="list_generated_media_tool",
        )
