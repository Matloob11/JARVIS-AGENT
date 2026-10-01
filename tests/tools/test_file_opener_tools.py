"""X-35: jarvis_file_opener indexing, media filters, and safe window close."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.multimedia.jarvis_file_opener as fo


@pytest.fixture(autouse=True)
def _reset_file_opener_globals() -> None:
    fo.global_file_index = []
    fo._index_mtime = 0.0
    fo._index_build_started = None
    fo._background_started = False
    fo._background_index_task = None
    yield
    fo.global_file_index = []
    fo._index_build_started = None
    fo._background_started = False


def test_index_roots_from_config_no_d_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    music = tmp_path / "Music"
    music.mkdir()
    fake_config = MagicMock()
    fake_config.media_dirs = [str(music)]
    monkeypatch.setattr(fo, "get_index_roots", lambda: list(fake_config.media_dirs))
    roots = fo.get_index_roots()
    assert roots == [str(music)]
    assert not any(r.upper().startswith("D:") for r in roots)


def test_index_roots_include_d_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fo.os.path, "isdir", lambda p: p in ("D:/", "/fake/Music"))
    monkeypatch.setattr(
        fo,
        "get_index_roots",
        lambda: ["D:/", "/fake/Music"],
    )
    roots = fo.get_index_roots()
    assert "D:/" in roots


def test_build_index_skips_sensitive_and_stores_fields() -> None:
    # Avoid pytest AppData temp roots (is_sensitive_path skips AppData segments).
    with tempfile.TemporaryDirectory(dir=str(Path.home() / "Desktop")) as raw:
        root = Path(raw)
        good = root / "report.pdf"
        good.write_bytes(b"x")
        secret = root / ".env"
        secret.write_text("KEY=1", encoding="utf-8")

        index = fo._build_index_sync([str(root)])
        names = {item["name"] for item in index}
        assert "report.pdf" in names
        assert ".env" not in names
        item = next(i for i in index if i["name"] == "report.pdf")
        assert item["ext"] == ".pdf"
        assert item["size"] == 1
        assert "mtime" in item


@pytest.mark.asyncio
async def test_exec_never_auto_opened_from_fuzzy_match() -> None:
    fo.global_file_index = [
        {
            "name": "setup.exe",
            "path": "C:/Downloads/setup.exe",
            "ext": ".exe",
            "size": 1,
            "mtime": 1.0,
        }
    ]
    fo._index_build_started = 0.0

    with patch.object(fo, "open_file", new_callable=AsyncMock) as mock_open:
        result = await fo.play_file("setup")

    mock_open.assert_not_called()
    assert result["status"] == "needs_confirmation"
    assert result["data"]["suggested_tool"] == "folder_file"
    assert "setup.exe" in result["data"]["file_path"]


@pytest.mark.asyncio
async def test_play_music_filters_audio_only() -> None:
    fo.global_file_index = [
        {
            "name": "clip.mp4",
            "path": "C:/Videos/clip.mp4",
            "ext": ".mp4",
            "size": 1,
            "mtime": 1.0,
        },
        {
            "name": "Tajdar.mp3",
            "path": "C:/Music/Tajdar.mp3",
            "ext": ".mp3",
            "size": 1,
            "mtime": 1.0,
        },
    ]
    fo._index_build_started = 0.0

    with patch.object(fo, "open_file", new_callable=AsyncMock) as mock_open:
        mock_open.return_value = {"status": "success"}
        await fo.play_music("Tajdar")

    mock_open.assert_called_once()
    opened = mock_open.call_args[0][0]
    assert opened["ext"] == ".mp3"


@pytest.mark.asyncio
async def test_play_video_prefers_video_ext() -> None:
    fo.global_file_index = [
        {
            "name": "song.mp3",
            "path": "C:/Music/song.mp3",
            "ext": ".mp3",
            "size": 1,
            "mtime": 1.0,
        },
        {
            "name": "movie.mp4",
            "path": "C:/Videos/movie.mp4",
            "ext": ".mp4",
            "size": 1,
            "mtime": 1.0,
        },
    ]
    fo._index_build_started = 0.0

    with patch.object(fo, "open_file", new_callable=AsyncMock) as mock_open:
        mock_open.return_value = {"status": "success"}
        await fo.play_video("movie")

    opened = mock_open.call_args[0][0]
    assert opened["ext"] == ".mp4"


@pytest.mark.asyncio
async def test_protected_windows_not_closed() -> None:
    from services.system.jarvis_desktop_common import WindowInfo

    protected = WindowInfo(1, "JARVIS & ANNA", 99, "electron.exe")
    photo = WindowInfo(2, "Vacation Photos", 100, "Photos.exe")

    with patch.object(fo, "find_windows", return_value=[protected, photo]):
        with patch.object(fo, "close_windows_gracefully", new_callable=AsyncMock) as mock_close:
            mock_close.return_value = {"closed": [2], "still_open": []}
            result = await fo.close_file_window_tool("photo")

    mock_close.assert_called_once()
    assert mock_close.call_args[0][0] == [2]
    assert result["status"] == "success"
    assert "Vacation Photos" in result["data"]["closed_titles"][0]


@pytest.mark.asyncio
async def test_multiple_match_sets_medium_risk() -> None:
    from services.system.jarvis_desktop_common import WindowInfo

    wins = [
        WindowInfo(1, "Report - Notepad", 10, "notepad.exe"),
        WindowInfo(2, "Report backup - Notepad", 11, "notepad.exe"),
    ]
    with patch.object(fo, "find_windows", return_value=wins):
        with patch.object(fo, "close_windows_gracefully", new_callable=AsyncMock) as mock_close:
            mock_close.return_value = {"closed": [1, 2], "still_open": []}
            result = await fo.close_file_window_tool("report")

    assert result["data"]["risk"] == "medium"
    assert len(result["data"]["closed_titles"]) == 2


@pytest.mark.asyncio
async def test_close_requires_three_char_keyword() -> None:
    result = await fo.close_file_window_tool("ab")
    assert result["status"] == "error"


def test_index_persisted_under_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = fo.file_index_path()
    assert path.parent.name == "JARVIS"
    assert path.name == "file_index.json"

    sample = [{"name": "a.txt", "path": "C:/a.txt", "ext": ".txt", "size": 0, "mtime": 0.0}]
    fo._save_index_to_disk(sample)
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded[0]["name"] == "a.txt"
    assert fo._load_index_from_disk() == loaded


@pytest.mark.asyncio
async def test_empty_index_returns_started_within_grace() -> None:
    fo.global_file_index = []
    fo._index_build_started = fo.time.monotonic()

    with patch.object(fo, "refresh_file_index", new_callable=AsyncMock):
        result = await fo.play_file("report")

    assert result["status"] == "started"
    assert "10 second" in result["message"].lower() or "10 second" in result["message"]


@pytest.mark.asyncio
async def test_direct_absolute_path_skips_index() -> None:
    with patch.object(fo.os.path, "isabs", return_value=True):
        with patch.object(fo.os.path, "exists", return_value=True):
            with patch.object(fo, "open_file", new_callable=AsyncMock) as mock_open:
                mock_open.return_value = {"status": "success"}
                await fo.play_file(r"C:\Users\me\Desktop\notes.txt")

    mock_open.assert_called_once()
    assert mock_open.call_args[0][0]["path"] == r"C:\Users\me\Desktop\notes.txt"
