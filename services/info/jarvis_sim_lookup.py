"""
# jarvis_sim_lookup.py
SIM Data Lookup module for JARVIS.
Fetches name, address, and CNIC information for a given phone number.
"""

import asyncio
from typing import Any

import requests

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_bridge import notify_sim_data, notify_sim_loading
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SIM-LOOKUP")

@jarvis_tool
async def lookup_sim_data(phone_number: str) -> dict[str, Any]:
    """
    Fetch SIM registration details (Name, CNIC, Address) for a Pakistani phone number.
    Handles formats like +92333..., 92333..., 0333..., or 333...
    """
    base_url = "https://arslan-apis.vercel.app/more/database"

    # 1. Clean the input: keep only digits
    clean_number = "".join(filter(str.isdigit, phone_number))

    # 2. Handle prefixes
    if clean_number.startswith("92"):
        if len(clean_number) > 10: # Avoid trimming if it's already just 10 digits starting with 92
            clean_number = clean_number[2:]
    elif clean_number.startswith("0"):
        clean_number = clean_number[1:]

    # 3. Validation: Pakistani mobile numbers (10 digits) or CNIC (13 digits)
    if not (len(clean_number) == 10 or len(clean_number) == 13):
        return {
            "status": "validation_error",
            "message": f"Sir, ye input sahi nahi lag raha ({phone_number}). Please 11 digits wala phone number ya 13 digits wala CNIC batayein.",
        }

    is_cnic_search = len(clean_number) == 13
    logger.info("Looking up SIM data for cleaned %s: %s", "CNIC" if is_cnic_search else "number", clean_number)

    # Notify UI that a lookup is in progress
    try:
        await notify_sim_loading()
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    try:
        # Using asyncio.to_thread for blocking requests call
        params = {"number": clean_number}
        response = await asyncio.to_thread(requests.get, base_url, params=params, timeout=12)
        response.raise_for_status()

        data = response.json()

        if data.get("status") and data.get("result"):
            results = data["result"]

            # Extract first found CNIC for proactive suggestion
            found_cnic = results[0].get('cnic') if not is_cnic_search else None

            # Format results for the agent to read
            formatted_results = []
            for idx, res in enumerate(results, 1):
                formatted_results.append(
                    f"Result #{idx}:\n"
                    f"Name: {res.get('full_name', 'N/A')}\n"
                    f"CNIC: {res.get('cnic', 'N/A')}\n"
                    f"Address: {res.get('address', 'N/A')}\n"
                    f"Phone: {res.get('phone', 'N/A')}\n",
                )

            # Notify the UI with the results
            try:
                await notify_sim_data(results, phone_number)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

            summary = "\n".join(formatted_results)
            proactive_msg = ""
            if found_cnic and not is_cnic_search:
                proactive_msg = f"\n\n**Sir, kia main is ka CNIC ({found_cnic}) number use kar ka is ki baki active sims ka data bi nikal doin?**"

            return {
                "status": "success",
                "count": len(results),
                "cnic_found": found_cnic,
                "message": f"Found {len(results)} records for {phone_number}:\n{summary}{proactive_msg}",
            }

        logger.info("No records found for %s", clean_number)
        return {
            "status": "not_found",
            "message": f"Sorry Sir, record for {phone_number} hamare database mein nahi mila.",
        }

    except (requests.RequestException, ValueError, KeyError) as e:
        logger.error("Error fetching SIM data: %s", e)
        return {
            "status": "error",
            "message": f"An error occurred while fetching data: {e!s}",
        }

if __name__ == "__main__":
    # Test script with various formats
    async def test():
        test_cases = ["03336678955", "+923336678955", "923336678955", "3336678955", "123", "0333123456789"]
        print("Testing Enhanced SIM Lookup...\n")
        for tc in test_cases:
            print(f"--- Testing: {tc} ---")
            res = await lookup_sim_data(tc)
            print(res["message"])
            print("-" * 20)

    asyncio.run(test())
