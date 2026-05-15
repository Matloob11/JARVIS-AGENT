"""
Minimal audio helpers for local integrations and tests.

The production voice pipeline lives in the LiveKit agent runtime; these helpers
give local callers a stable module-level contract for patching and fallback use.
"""

from __future__ import annotations


def process_audio_input(audio_data: bytes) -> dict[str, object]:
    """Return a neutral transcription payload for raw audio bytes."""
    if not audio_data:
        return {"transcript": "", "confidence": 0.0, "bytes": 0}

    return {"transcript": "", "confidence": 0.0, "bytes": len(audio_data)}


def generate_speech(text: str) -> bytes:
    """Fallback text-to-bytes speech placeholder for offline/local tests."""
    return (text or "").encode("utf-8")
