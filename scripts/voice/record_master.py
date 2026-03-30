import time
import wave
from pathlib import Path

import sounddevice as sd

# Config
SAMPLE_RATE = 16000
DURATION = 10 # Seconds to record
OUTPUT_PATH = "d:/Personal-Assistant-main/data/identity/master_voice.wav"

def record_master() -> None:
    print("\n" + "="*50)  # noqa: T201
    print("🎙️ JARVIS MASTER VOICE ENROLLMENT")  # noqa: T201
    print("="*50)  # noqa: T201
    print(f"I will record {DURATION} seconds from your microphone.")  # noqa: T201
    print("Please speak clearly and continuously (e.g., read a sentence).")  # noqa: T201
    print("="*50 + "\n")  # noqa: T201

    input("Press ENTER to start recording...")

    print(f"\n🔴 RECORDING for {DURATION} seconds... Speak now!")  # noqa: T201

    try:
        # Record mono
        recording = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')

        # simple progress bar
        for i in range(DURATION):
            time.sleep(1)
            print(f"Progress: [{('■'*(i+1)) + (' '*(DURATION-i-1))}] {i+1}s", end='\r')  # noqa: T201

        sd.wait() # Wait for recording to finish
        print("\n\n✅ Recording finished.")  # noqa: T201

        # Ensure output directory exists
        Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)

        # Save to WAV
        with wave.open(OUTPUT_PATH, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2) # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(recording.tobytes())

        print(f"✨ Master voice saved to: {OUTPUT_PATH}")  # noqa: T201
        print("\nNow you can run 'realtime_voice_test.py' to verify.")  # noqa: T201

    except Exception as e:
        print(f"❌ Error during recording: {e}")  # noqa: T201

if __name__ == "__main__":
    record_master()
