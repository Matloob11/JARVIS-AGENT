"""X-55: download_images + zip_files — paths, concurrency, zip_name, size cap."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.multimedia import jarvis_advanced_tools as adv


@pytest.fixture
def outputs_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "proj"
    outputs = project / "Jarvis_Outputs"
    (outputs / "Downloads").mkdir(parents=True)
    (outputs / "Archives").mkdir(parents=True)
    monkeypatch.setattr(adv.config, "project_root", str(project))
    monkeypatch.setattr(adv.config, "outputs_dir", str(outputs))
    monkeypatch.setattr(adv.config, "shared_dir", str(tmp_path / "Jarvis_Shared"))
    return outputs


@pytest.mark.asyncio
async def test_download_images_rejects_folder_traversal(outputs_tree: Path) -> None:
    with patch.object(adv, "search_images", new_callable=AsyncMock) as mock_search:
        result = await adv.download_images("cats", count=1, folder_name="..")

    assert result["status"] == "error"
    assert result["message"] == "folder name invalid"
    mock_search.assert_not_called()


@pytest.mark.asyncio
async def test_download_images_sanitized_filenames(outputs_tree: Path) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "image/jpeg"}
    mock_resp.content = b"\xff\xd8\xff"

    with patch.object(adv, "search_images", new_callable=AsyncMock, return_value=["http://x/a.jpg"]):
        with patch.object(adv, "_emit_download_media_update", new_callable=AsyncMock):
            with patch("services.multimedia.jarvis_advanced_tools.requests.get", return_value=mock_resp):
                result = await adv.download_images('cat:dog|?', count=1, folder_name="Downloaded_Images")

    assert result["status"] == "success"
    assert result["downloaded_count"] == 1
    saved = list((outputs_tree / "Downloads" / "Downloaded_Images").glob("*.jpg"))
    assert len(saved) == 1
    assert saved[0].name == "cat_dog_1.jpg"


@pytest.mark.asyncio
async def test_zip_files_honours_zip_name_under_archives(outputs_tree: Path) -> None:
    src = outputs_tree / "Downloads" / "Downloaded_Images"
    src.mkdir(exist_ok=True)
    (src / "a.txt").write_text("hello", encoding="utf-8")

    with patch("services.multimedia.jarvis_advanced_tools.shutil.make_archive") as mock_archive:
        result = await adv.zip_files(str(src), zip_name="cats.zip")

    assert result["status"] == "success"
    expected_base = outputs_tree / "Archives" / "cats"
    assert result["zip_path"] == f"{expected_base}.zip"
    mock_archive.assert_called_once()
    archive_base, fmt, folder = mock_archive.call_args[0]
    assert archive_base == str(expected_base)
    assert fmt == "zip"
    assert folder == str(src.resolve())


@pytest.mark.asyncio
async def test_zip_files_refuses_folder_over_2gb(outputs_tree: Path) -> None:
    src = outputs_tree / "Downloads" / "big"
    src.mkdir(parents=True)

    with patch.object(adv, "_directory_size_bytes", return_value=adv._MAX_ZIP_FOLDER_BYTES + 1):
        with patch("services.multimedia.jarvis_advanced_tools.shutil.make_archive") as mock_archive:
            result = await adv.zip_files(str(src), zip_name="big.zip")

    assert result["status"] == "error"
    assert "2 GB" in result["message"]
    mock_archive.assert_not_called()
