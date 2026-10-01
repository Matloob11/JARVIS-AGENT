"""Tests for jarvis_audio (JARVIS_TEST_MODE — no network)."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ["JARVIS_TEST_MODE"] = "true"

from services.multimedia import jarvis_audio as audio  # noqa: E402


def test_generate_speech_test_mode_deterministic() -> None:
    a = audio.generate_speech("Salam Sir", lang="ur")
    b = audio.generate_speech("Salam Sir", lang="ur")
    assert len(a) > 1000
    assert a == b
    assert audio.generate_speech("") == b""
    assert audio.generate_speech("   ") == b""


def test_generate_speech_fallback_when_gtts_fails() -> None:
    fake_wav = b"RIFF" + b"\0" * 1200

    class _Engine:
        def save_to_file(self, _text: str, path: str) -> None:
            with open(path, "wb") as fh:
                fh.write(fake_wav)

        def runAndWait(self) -> None:
            return None

    with (
        patch.object(audio, "_test_mode", return_value=False),
        patch("gtts.gTTS", side_effect=RuntimeError("gtts down")),
        patch("pyttsx3.init", return_value=_Engine()),
    ):
        out = audio.generate_speech("hello world", lang="ur")
    assert len(out) > 1000
    assert out.startswith(b"RIFF")


def test_process_audio_input_error_handling() -> None:
    with patch.object(audio, "_test_mode", return_value=False):
        with patch("speech_recognition.Recognizer") as rec_cls:
            inst = rec_cls.return_value
            inst.recognize_google.side_effect = Exception("network")
            result = audio.process_audio_input(b"\x00\x01" * 100)
    assert result["transcript"] == ""
    assert "error" in result
    assert result["bytes"] == 200


def test_process_audio_input_test_mode() -> None:
    result = audio.process_audio_input(b"abc")
    assert result["transcript"] == "test transcript"
    assert result["engine"] == "test"


def test_speak_locally_test_mode() -> None:
    assert audio.speak_locally("hi") is True
    assert audio.speak_locally("") is False
