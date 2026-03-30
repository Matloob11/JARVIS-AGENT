"""
# realtime_voice_test.py
Real-time Speaker Identification test for JARVIS.
Shows match percentage as you speak.
"""

import sys
import time
from pathlib import Path
from queue import Queue

import numpy as np
import sounddevice as sd
import torchaudio

# Monkeypatch for torchaudio compatibility
if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: []

# Add current directory to path for imports
sys.path.append(str(Path.cwd()))

from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("REALTIME-VOICE-ID")

# Audio Config
SAMPLE_RATE = 16000
CHUNK_DURATION = 3  # Seconds of audio to analyze at once
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

# Verification thresholds
MATCH_THRESHOLD = 0.65
HIGH_CONFIDENCE_THRESHOLD = 0.9
LOW_CONFIDENCE_THRESHOLD = 0.3

audio_queue = Queue()

def audio_callback(indata, _frames, _time, status):
    """This is called (from a separate thread) for each audio block."""
    if status:
        print(status, file=sys.stderr)  # noqa: T201
    audio_queue.put(indata.copy())

def start_realtime_test() -> None:
    print("\n" + "="*60)  # noqa: T201
    print("🎙️  JARVIS REAL-TIME VOICE IDENTITY MONITOR")  # noqa: T201
    print("="*60)  # noqa: T201
    print("Instructions:")  # noqa: T201
    print("1. Speak naturally. The system will analyze in 3-second blocks.")  # noqa: T201
    print("2. It will show Success (Match %) or Denied (Match %).")  # noqa: T201
    print("3. Press Ctrl+C to stop.")  # noqa: T201
    print("="*60 + "\n")  # noqa: T201

    master_path = Path("d:/Personal-Assistant-main/data/identity/master_voice.wav")
    if not master_path.exists():
        print(f"❌ Error: Master voice not found at {master_path}")  # noqa: T201
        return

    print("⏳ Initializing SpeechBrain models... (Please wait)")  # noqa: T201
    # Ensure engine is loaded
    if voice_id_engine.verification is None:
        print("❌ Error: Could not initialize Voice ID Engine.")  # noqa: T201
        return
    print("✅ System Ready. Listening...\n")  # noqa: T201

    buffer = np.zeros((0, 1), dtype=np.float32)
    session_peak = 0.0

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, callback=audio_callback):
            while True:
                # Get audio data from queue
                while not audio_queue.empty():
                    data = audio_queue.get()
                    buffer = np.append(buffer, data, axis=0)

                # If we have enough for a chunk, analyze it
                if len(buffer) >= CHUNK_SIZE:
                    segment = buffer[:CHUNK_SIZE]
                    buffer = buffer[CHUNK_SIZE:] # Keep remainder

                    # Convert to bytes for the engine (simulating the agent flow)
                    # Engine expects 16-bit PCM bytes
                    audio_int16 = (segment * 32767).astype(np.int16)
                    audio_bytes = audio_int16.tobytes()

                    # Run Verification (Threshold 0.65 is optimal for recognition & security)
                    is_match, score = voice_id_engine.verify_bytes(audio_bytes, SAMPLE_RATE, threshold=0.65)

                    # Display result
                    percentage = score * 100
                    session_peak = max(session_peak, score)

                    status_icon = "🟢" if is_match else "🔴"
                    identity = "BOSS (Matloob)" if is_match else "STRANGER / FRIEND"

                    print(f"{status_icon} [{identity}] | Confidence: {percentage:.2f}% (Peak: {float(session_peak)*100:.2f}%)")  # noqa: T201

                    if is_match and score > HIGH_CONFIDENCE_THRESHOLD:
                        print("   ✨ AUTHENTICATED: High Confidence.")  # noqa: T201
                    elif not is_match and score < LOW_CONFIDENCE_THRESHOLD:
                        print("   🚫 STRANGER: High Confidence.")  # noqa: T201

                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\n🛑 Stopping Real-time Monitor...")  # noqa: T201
    except Exception as e:
        print(f"\n❌ Error: {e}")  # noqa: T201

if __name__ == "__main__":
    start_realtime_test()
