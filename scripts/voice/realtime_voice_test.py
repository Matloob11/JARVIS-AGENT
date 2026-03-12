"""
# realtime_voice_test.py
Real-time Speaker Identification test for JARVIS.
Shows match percentage as you speak.
"""

import os
import sys
import time
import torchaudio
import numpy as np
import sounddevice as sd
from queue import Queue

# Monkeypatch for torchaudio compatibility
if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: []

# Add current directory to path for imports
sys.path.append(os.getcwd())

from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("REALTIME-VOICE-ID")

# Audio Config
SAMPLE_RATE = 16000
CHUNK_DURATION = 3  # Seconds of audio to analyze at once
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

audio_queue = Queue()

def audio_callback(indata, frames, time, status):
    """This is called (from a separate thread) for each audio block."""
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def start_realtime_test():
    print("\n" + "="*60)
    print("🎙️  JARVIS REAL-TIME VOICE IDENTITY MONITOR")
    print("="*60)
    print("Instructions:")
    print("1. Speak naturally. The system will analyze in 3-second blocks.")
    print("2. It will show Success (Match %) or Denied (Match %).")
    print("3. Press Ctrl+C to stop.")
    print("="*60 + "\n")

    master_path = "d:/Personal-Assistant-main/data/identity/master_voice.wav"
    if not os.path.exists(master_path):
        print(f"❌ Error: Master voice not found at {master_path}")
        return

    print("⏳ Initializing SpeechBrain models... (Please wait)")
    # Ensure engine is loaded
    if voice_id_engine.verification is None:
        print("❌ Error: Could not initialize Voice ID Engine.")
        return
    print("✅ System Ready. Listening...\n")

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
                    if score > session_peak:
                        session_peak = score

                    status_icon = "🟢" if is_match else "🔴"
                    identity = "BOSS (Matloob)" if is_match else "STRANGER / FRIEND"

                    print(f"{status_icon} [{identity}] | Confidence: {percentage:.2f}% (Peak: {float(session_peak)*100:.2f}%)")

                    if is_match and score > 0.9:
                        print("   ✨ AUTHENTICATED: High Confidence.")
                    elif not is_match and score < 0.3:
                        print("   🚫 STRANGER: High Confidence.")

                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\n🛑 Stopping Real-time Monitor...")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    run_opts = {"device": "cpu"}
    start_realtime_test()
