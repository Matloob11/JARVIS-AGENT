import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.ai_core.jarvis_vision import analyze_screen, analyze_camera

def safe_print(text):
    """Helper to print text safely on Windows console with non-ASCII characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))

async def run_integration_test():
    safe_print("========================================")
    safe_print("   JARVIS INTEGRATED VISION TEST       ")
    safe_print("========================================\n")

    # 1. Test Screen Analysis
    safe_print("[TEST 1] Testing Screen Analysis (analyze_screen)...")
    try:
        screen_result = await analyze_screen("What is visible on the screen right now?")
        if screen_result.get("status") == "success":
            msg = screen_result.get('message', '')
            safe_print(f"Result: {msg[:300]}...")
        else:
            safe_print(f"Error: {screen_result.get('message')}")
    except Exception as e:
        safe_print(f"Exception in analyze_screen: {e}")

    safe_print("\n" + "-"*40 + "\n")

    # 2. Test Camera Analysis
    safe_print("[TEST 2] Testing Camera Analysis (analyze_camera)...")
    try:
        camera_result = await analyze_camera("What do you see in the camera?")
        if camera_result.get("status") == "success":
            msg = camera_result.get('message', '')
            safe_print(f"Result: {msg[:300]}...")
        else:
            safe_print(f"Error: {camera_result.get('message')}")
    except Exception as e:
        safe_print(f"Exception in analyze_camera: {e}")

    safe_print("\n========================================")
    safe_print("          TEST COMPLETE                 ")
    safe_print("========================================")

if __name__ == "__main__":
    asyncio.run(run_integration_test())
