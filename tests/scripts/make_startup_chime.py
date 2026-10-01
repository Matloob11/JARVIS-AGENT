"""Generate an original JARVIS startup chime WAV (no copyrighted audio).

Writes assets/audio/jarvis_startup_chime.wav (~2.5 s, 44.1 kHz, 16-bit mono).
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "audio" / "jarvis_startup_chime.wav"
SAMPLE_RATE = 44100
DURATION_S = 2.5


def _synthesize_numpy() -> bytes:
    assert np is not None
    n = int(SAMPLE_RATE * DURATION_S)
    t = np.linspace(0.0, DURATION_S, n, endpoint=False, dtype=np.float64)
    # Soft rising sweep + two harmonic tones (original, not Iron Man)
    sweep = np.sin(2 * np.pi * (420 + 280 * (t / DURATION_S)) * t)
    bell = np.sin(2 * np.pi * 880 * t) * np.exp(-2.8 * t)
    low = np.sin(2 * np.pi * 220 * t) * np.exp(-1.2 * t)
    mix = 0.45 * sweep + 0.35 * bell + 0.25 * low
    # Fade in/out
    fade = min(int(0.08 * SAMPLE_RATE), n // 10)
    env = np.ones(n, dtype=np.float64)
    if fade > 0:
        env[:fade] = np.linspace(0.0, 1.0, fade)
        env[-fade:] = np.linspace(1.0, 0.0, fade)
    mix = np.clip(mix * env * 0.55, -1.0, 1.0)
    pcm = (mix * 32767.0).astype(np.int16)
    return pcm.tobytes()


def _synthesize_stdlib() -> bytes:
    n = int(SAMPLE_RATE * DURATION_S)
    fade = min(int(0.08 * SAMPLE_RATE), n // 10)
    frames = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE
        sweep = math.sin(2 * math.pi * (420 + 280 * (t / DURATION_S)) * t)
        bell = math.sin(2 * math.pi * 880 * t) * math.exp(-2.8 * t)
        low = math.sin(2 * math.pi * 220 * t) * math.exp(-1.2 * t)
        sample = 0.45 * sweep + 0.35 * bell + 0.25 * low
        if i < fade:
            sample *= i / fade
        elif i > n - fade:
            sample *= (n - i) / fade
        sample = max(-1.0, min(1.0, sample * 0.55))
        frames += struct.pack("<h", int(sample * 32767))
    return bytes(frames)


def main() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pcm = _synthesize_numpy() if np is not None else _synthesize_stdlib()
    with wave.open(str(OUT), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    return OUT


if __name__ == "__main__":
    main()
