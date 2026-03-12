"""
# test_voice_id.py
Standalone tool to test JARVIS Voice Fingerprinting (Speaker ID).
This script compares a new recording/file against your stored master voice.
"""

import os
import sys
import time
from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_logger import setup_logger

# Add current directory to path
sys.path.append(os.getcwd())

logger = setup_logger("TEST-VOICE-ID")

def run_test():
    print("\n" + "="*50)
    print("🎤 JARVIS VOICE IDENTITY TESTER")
    print("="*50)

    master_path = "d:/Personal-Assistant-main/data/identity/master_voice.wav"

    if not os.path.exists(master_path):
        print(f"❌ Error: Master voice file not found at {master_path}")
        return

    print(f"✅ Master Voice found. (Size: {os.path.getsize(master_path)} bytes)")
    print("\nHow would you like to test?")
    print("1. Compare existing .wav file")
    print("2. Live Recording Test (Coming soon/Manual check recommended)")

    choice = input("\nEnter choice (1): ") or "1"

    if choice == "1":
        test_file = input("Enter path to test audio file (e.g., d:/test.wav): ").strip()
        if not os.path.exists(test_file):
            print(f"❌ File not found: {test_file}")
            return

        print(f"\n🔍 Analyzing identity of '{os.path.basename(test_file)}'...")
        start_time = time.time()

        is_match, score = voice_id_engine.verify_segment(test_file)

        duration = time.time() - start_time

        print("\n" + "-"*30)
        print("📊 RESULT:")
        print(f"   - Match: {'✅ SUCCESS' if is_match else '❌ DENIED (Stranger Detected)'}")
        print(f"   - Confidence Score: {score:.4f}")
        print(f"   - Analysis Time: {duration:.2f}s")
        print("-"*30)

        if is_match:
            print("\n🌟 JARVIS: Welcome back, Sir Matloob.")
        else:
            print("\n⚠️ JARVIS: Access Denied. You are not my boss.")

if __name__ == "__main__":
    try:
        run_test()
    except KeyboardInterrupt:
        print("\nExiting test...")
