import os
import sys
import wave
import time
import numpy as np
import sounddevice as sd

# Config
SAMPLE_RATE = 16000
DURATION = 10 # Seconds to record
OUTPUT_PATH = "d:/Personal-Assistant-main/data/identity/master_voice.wav"

def record_master():
    print("\n" + "="*50)
    print("🎙️ JARVIS MASTER VOICE ENROLLMENT")
    print("="*50)
    print(f"I will record {DURATION} seconds from your microphone.")
    print("Please speak clearly and continuously (e.g., read a sentence).")
    print("="*50 + "\n")
    
    input("Press ENTER to start recording...")
    
    print(f"\n🔴 RECORDING for {DURATION} seconds... Speak now!")
    
    try:
        # Record mono
        recording = sd.rec(int(DURATION * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
        
        # simple progress bar
        for i in range(DURATION):
            time.sleep(1)
            print(f"Progress: [{('■'*(i+1)) + (' '*(DURATION-i-1))}] {i+1}s", end='\r')
        
        sd.wait() # Wait for recording to finish
        print("\n\n✅ Recording finished.")
        
        # Ensure output directory exists
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        
        # Save to WAV
        with wave.open(OUTPUT_PATH, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2) # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(recording.tobytes())
            
        print(f"✨ Master voice saved to: {OUTPUT_PATH}")
        print("\nNow you can run 'realtime_voice_test.py' to verify.")
        
    except Exception as e:
        print(f"❌ Error during recording: {e}")

if __name__ == "__main__":
    record_master()
