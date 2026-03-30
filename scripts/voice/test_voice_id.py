"""
# test_voice_id.py
Standalone tool to test JARVIS Voice Fingerprinting (Speaker ID).
This script compares a new recording/file against your stored master voice.
"""

import sys
import time
from pathlib import Path

from services.ai_core.voice_fingerprint import voice_id_engine
from services.utils.jarvis_logger import setup_logger

# Add current directory to path
sys.path.append(str(Path.cwd()))

logger = setup_logger("TEST-VOICE-ID")

def run_test() -> None:
    print("\n" + "="*50)  # noqa: T201
    print("🎤 JARVIS VOICE IDENTITY TESTER")  # noqa: T201
    print("="*50)  # noqa: T201

    master_path = Path("d:/Personal-Assistant-main/data/identity/master_voice.wav")

    if not master_path.exists():
        print(f"❌ Error: Master voice file not found at {master_path}")  # noqa: T201
        return

    print(f"✅ Master Voice found. (Size: {master_path.stat().st_size} bytes)")  # noqa: T201
    print("\nHow would you like to test?")  # noqa: T201
    print("1. Compare existing .wav file")  # noqa: T201
    print("2. Live Recording Test (Coming soon/Manual check recommended)")  # noqa: T201

    choice = input("\nEnter choice (1): ") or "1"

    if choice == "1":
        test_file = input("Enter path to test audio file (e.g., d:/test.wav): ").strip()
        test_path = Path(test_file)
        if not test_path.exists():
            print(f"❌ File not found: {test_file}")  # noqa: T201
            return

        print(f"\n🔍 Analyzing identity of '{test_path.name}'...")  # noqa: T201
        start_time = time.time()

        is_match, score = voice_id_engine.verify_segment(test_file)

        duration = time.time() - start_time

        print("\n" + "-"*30)  # noqa: T201
        print("📊 RESULT:")  # noqa: T201
        print(f"   - Match: {'✅ SUCCESS' if is_match else '❌ DENIED (Stranger Detected)'}")  # noqa: T201
        print(f"   - Confidence Score: {score:.4f}")  # noqa: T201
        print(f"   - Analysis Time: {duration:.2f}s")  # noqa: T201
        print("-"*30)  # noqa: T201

        if is_match:
            print("\n🌟 JARVIS: Welcome back, Sir Matloob.")  # noqa: T201
        else:
            print("\n⚠️ JARVIS: Access Denied. You are not my boss.")  # noqa: T201

if __name__ == "__main__":
    try:
        run_test()
    except KeyboardInterrupt:
        print("\nExiting test...")  # noqa: T201
