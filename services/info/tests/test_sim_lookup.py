"""
Standalone smoke test for the privacy-safe SIM lookup workflow.
"""

import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.info.jarvis_sim_lookup import lookup_sim_data


async def test_sim_lookup() -> None:
    print("--- JARVIS Safe SIM Lookup Smoke Test ---")

    test_cases = [
        "03336678955",
        "+923336678955",
        "923336678955",
        "3336678955",
        "123",
    ]

    for test_case in test_cases:
        print(f"\nTesting: {test_case}")
        result = await lookup_sim_data(test_case)
        print(f"Status: {result.get('status')}")
        print(f"Records: {len(result.get('records', []))}")
        print(f"Message:\n{result.get('message')}")
        print("-" * 30)


if __name__ == "__main__":
    try:
        asyncio.run(test_sim_lookup())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"\nTest failed: {exc}")
