"""
# services/info/tests/test_sim_lookup.py
Standalone test script for the enhanced SIM lookup service.
"""
import asyncio
import sys
import os

# Add root directory to path to allow importing services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.info.jarvis_sim_lookup import lookup_sim_data

async def test_sim_lookup():
    print("--- 📱 JARVIS Enhanced SIM Lookup Test ---")
    
    # Test cases with known and unknown formats
    test_cases = [
        "03336678955",  # Standard
        "+923336678955", # International
        "923336678955", # Without plus
        "3336678955",  # Without prefix
        "03123456789"  # Likely not in sample DB
    ]
    
    for tc in test_cases:
        print(f"\n🚀 Testing: {tc}...")
        result = await lookup_sim_data(tc)
        print(f"Status: {result.get('status')}")
        print(f"Message:\n{result.get('message')}")
        print("-" * 30)

if __name__ == "__main__":
    try:
        asyncio.run(test_sim_lookup())
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
