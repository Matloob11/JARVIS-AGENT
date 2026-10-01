"""X-39: QR data fidelity (raw policy), safe filename, open_file."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import cv2
import pytest

from services.multimedia import jarvis_qr_gen as qr_mod
from services.multimedia.jarvis_qr_gen import (
    _sanitize_qr_filename,
    generate_qr_code,
)


def _decode_png(path: str) -> str:
    img = cv2.imread(path)
    assert img is not None, f"cv2 could not read {path}"
    detector = cv2.QRCodeDetector()
    decoded, _points, _ = detector.detectAndDecode(img)
    assert decoded, "QR decode returned empty string"
    return decoded


@pytest.fixture
def qr_shared_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(qr_mod.config, "shared_dir", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_url_with_ampersand_decodes_exactly(qr_shared_dir):
    payload = "https://example.com/?a=1&b=2"
    with patch.object(qr_mod, "_emit_qr_update", new_callable=AsyncMock):
        res = await generate_qr_code(payload, "test_url_qr.png", open_file=False)

    assert res["status"] == "success"
    assert res["filename"] == "test_url_qr.png"
    assert _decode_png(res["file_path"]) == payload


@pytest.mark.asyncio
async def test_wifi_string_with_semicolons_decodes_exactly(qr_shared_dir):
    payload = "WIFI:T:WPA;S:TestNet;P:secret123;;"
    with patch.object(qr_mod, "_emit_qr_update", new_callable=AsyncMock):
        res = await generate_qr_code(payload, "wifi_qr.png", open_file=False)

    assert res["status"] == "success"
    assert _decode_png(res["file_path"]) == payload


def test_sanitize_filename_strips_path_and_unsafe_chars():
    assert _sanitize_qr_filename("../../etc/evil?name.png") == "evil_name.png"
    assert _sanitize_qr_filename("").endswith(".png")
    assert _sanitize_qr_filename("no_ext") == "no_ext.png"


@pytest.mark.asyncio
async def test_data_over_max_chars_returns_error(qr_shared_dir):
    payload = "x" * 2901
    res = await generate_qr_code(payload, open_file=False)
    assert res["status"] == "error"
