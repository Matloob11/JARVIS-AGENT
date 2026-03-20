"""
# services/info/tests/test_cnic_lookup.py
Standalone test script for CNIC-based multi-SIM lookup.
"""
import asyncio
import sys
import os

# Add root directory to path to allow importing services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.info.jarvis_sim_lookup import lookup_sim_data

async def test_cnic_lookup():
    print("--- 📱 JARVIS CNIC Multi-SIM Lookup Test ---")
    
    # Test with a sample CNIC (13 digits)
    sample_cnic = "3310012345678"
    
    print(f"\n🚀 Testing CNIC: {sample_cnic}...")
    result = await lookup_sim_data(sample_cnic)
    
    print(f"Status: {result.get('status')}")
    print(f"Count: {result.get('count', 0)}")
    print(f"Message:\n{result.get('message')}")
    
    if result.get("count", 0) > 1:
        print("\n✅ SUCCESS: Multi-record response received for CNIC.")
    else:
        print("\n⚠️ NOTE: Only one or zero records found (Check API state).")

if __name__ == "__main__":
    try:
        asyncio.run(test_cnic_lookup())
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
