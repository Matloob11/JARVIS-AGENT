"""
Standalone YouTube Automation Test Script
This script helps to verify the YouTube automation logic without running the full agent.
"""
import asyncio
import sys
import os

# Add root directory to path to allow importing services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.automation.jarvis_youtube_automation import automate_youtube

async def test_youtube():
    print("--- 📺 JARVIS YouTube Automation Test ---")
    
    # Test cases
    tests = [
        {"action": "open", "query": "", "desc": "Opening YouTube Homepage"},
        {"action": "play", "query": "Badnam song", "desc": "Playing 'Badnam song' (Direct)"},
        {"action": "search", "query": "Python tutorial", "desc": "Searching for 'Python tutorial'"}
    ]
    
    for test in tests:
        print(f"\n🚀 Running: {test['desc']}...")
        result = await automate_youtube(test['action'], test['query'])
        print(f"Result: {result}")
        await asyncio.sleep(2) # Buffer time

if __name__ == "__main__":
    if os.name != 'nt':
        print("❌ This test is designed for Windows (requires Edge/Chrome).")
        sys.exit(1)
        
    try:
        asyncio.run(test_youtube())
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
