"""
Standalone smoke test for CNIC-shaped input in privacy-safe SIM lookup mode.
"""

import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.info.jarvis_sim_lookup import lookup_sim_data


async def test_cnic_lookup() -> None:
    print("--- JARVIS Safe CNIC Input Smoke Test ---")

    sample_cnic = "3310012345678"
    result = await lookup_sim_data(sample_cnic)

    print(f"Status: {result.get('status')}")
    print(f"Records: {len(result.get('records', []))}")
    print(f"Message:\n{result.get('message')}")

    if result.get("status") == "blocked":
        print("\nSafe mode confirmed: authorized source is required before records are displayed.")


if __name__ == "__main__":
    try:
        asyncio.run(test_cnic_lookup())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"\nTest failed: {exc}")
