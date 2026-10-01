"""
Local TTS/STT helpers for reminders and offline smoke (K-50 / C-AUDIO-1).

Production voice stays on LiveKit; these helpers are for PC-local playback and tests.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import threading
from typing import Any


def _test_mode() -> bool:
    return os.environ.get("JARVIS_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_ascii_english(text: str) -> bool:
    if not text:
        return True
    if any(ord(ch) > 127 for ch in text):
        return False
    # Prefer 'en' for ASCII-only Latin text
    return bool(re.search(r"[A-Za-z]", text))


def generate_speech(text: str, lang: str = "ur") -> bytes:
    """Return MP3 bytes (gTTS) or WAV fallback (pyttsx3). Empty text -> b''."""
    if not text or not str(text).strip():
        return b""

    if _test_mode():
        # Deterministic stub MP3-ish header + payload (no network)
        payload = f"JARVIS_TEST_SPEECH:{text}:{lang}".encode("utf-8")
        return b"ID3" + payload.ljust(1200, b"\0")

    tts_lang = "en" if _is_ascii_english(text) and lang.startswith("ur") else lang
    # When caller passes lang='en' keep it; when ur + ASCII English -> en
    if lang.lower().startswith("en"):
        tts_lang = "en"
    elif _is_ascii_english(text):
        tts_lang = "en"

    try:
        from gtts import gTTS

        buf = io.BytesIO()
        gTTS(text=text, lang=tts_lang).write_to_fp(buf)
        data = buf.getvalue()
        if data:
            return data
    except Exception:
        pass

    # Offline fallback: pyttsx3 -> WAV bytes
    try:
        import pyttsx3

        engine = pyttsx3.init()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
        try:
            engine.save_to_file(text, path)
            engine.runAndWait()
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        return b""


def process_audio_input(
    audio_data: bytes,
    sample_rate: int = 16000,
    lang: str = "ur-PK",
) -> dict[str, Any]:
    """STT via SpeechRecognition + Google; never raises."""
    if _test_mode():
        return {
            "transcript": "test transcript",
            "confidence": 1.0,
            "bytes": len(audio_data or b""),
            "engine": "test",
        }

    if not audio_data:
        return {
            "transcript": "",
            "confidence": 0.0,
            "bytes": 0,
            "engine": "none",
            "error": "empty_audio",
        }

    try:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        audio = sr.AudioData(audio_data, sample_rate, 2)
        transcript = recognizer.recognize_google(audio, language=lang)
        return {
            "transcript": transcript or "",
            "confidence": 0.0,
            "bytes": len(audio_data),
            "engine": "google",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "transcript": "",
            "confidence": 0.0,
            "bytes": len(audio_data),
            "engine": "google",
            "error": str(exc),
        }


def speak_locally(text: str, lang: str = "ur") -> bool:
    """Generate speech and play via pygame in a daemon thread. False on failure."""
    if _test_mode():
        return bool(text and str(text).strip())

    try:
        data = generate_speech(text, lang=lang)
        if not data:
            return False

        def _play() -> None:
            path = None
            try:
                import pygame

                suffix = ".mp3" if data[:3] == b"ID3" or data[:2] == b"\xff\xfb" else ".wav"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(data)
                    path = tmp.name
                pygame.mixer.init()
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(50)
            except Exception:
                pass
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        threading.Thread(target=_play, daemon=True).start()
        return True
    except Exception:
        return False
