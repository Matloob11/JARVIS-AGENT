"""
# jarvis_sim_lookup.py
SIM Data Lookup module for JARVIS.
Fetches name, address, and CNIC information for a given phone number.
"""

import asyncio
import requests
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-SIM-LOOKUP")

@jarvis_tool
async def lookup_sim_data(phone_number: str) -> dict:
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
        
    # 3. Validation: Pakistani mobile numbers (after cleaning) must be 10 digits
    if len(clean_number) < 10:
        return {
            "status": "validation_error",
            "message": f"Sir, ye number incomplete lag raha hai ({phone_number}). Please mukammal 11 digits wala number batayein (e.g. 03331234567)."
        }
    if len(clean_number) > 10:
        return {
            "status": "validation_error",
            "message": f"Sir, ye number bohot lamba hai ({phone_number}). Please sahi number check kar ke batayein."
        }
    
    logger.info("Looking up SIM data for cleaned number: %s", clean_number)

    try:
        # Using asyncio.to_thread for blocking requests call
        params = {"number": clean_number}
        response = await asyncio.to_thread(requests.get, base_url, params=params, timeout=12)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get("status") and data.get("result"):
            results = data["result"]
            
            # Format results for the agent to read
            formatted_results = []
            for idx, res in enumerate(results, 1):
                formatted_results.append(
                    f"Result #{idx}:\n"
                    f"Name: {res.get('full_name', 'N/A')}\n"
                    f"CNIC: {res.get('cnic', 'N/A')}\n"
                    f"Address: {res.get('address', 'N/A')}\n"
                )
            
            summary = "\n".join(formatted_results)
            return {
                "status": "success",
                "count": len(results),
                "message": f"Found {len(results)} records for {phone_number}:\n{summary}"
            }
        else:
            logger.info("No records found for %s", clean_number)
            return {
                "status": "not_found",
                "message": f"Sorry Sir, record for {phone_number} hamare database mein nahi mila."
            }
            
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.error("Error fetching SIM data: %s", e)
        return {
            "status": "error",
            "message": f"An error occurred while fetching data: {str(e)}"
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
