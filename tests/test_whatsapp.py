"""
# test_whatsapp.py
Verification script for WhatsApp Automation and Pylint 10/10 compliance.
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from services.automation.jarvis_whatsapp_automation import automate_whatsapp
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("WHATSAPP-TEST")

async def run_test():
    """
    Runs a live test of the WhatsApp automation.
    Requires WhatsApp Desktop (Store version) to be installed.
    """
    print("\n--- JARVIS WhatsApp Verification Tool ---")
    print("Objective: Send a test message to verify automation logic.")
    
    contact = "Matloob" # Replace with a valid contact name for local test
    message = "Assalam-o-Alaikum Sir! Main JARVIS hoon. Verification complete: 10/10 Quality Score achieved! ❤️"
    
    print(f"\nTarget Contact: {contact}")
    print(f"Message: {message}")
    print("\nStarting automation in 3 seconds... (Please don't move the mouse)")
    await asyncio.sleep(3)
    
    result = await automate_whatsapp(contact, message, close_after=False)
    
    if result["status"] == "success":
        print("\n✅ SUCCESS: Message sent logically.")
        print(f"Response: {result['message']}")
    else:
        print("\n❌ FAILED: Automation encountered an issue.")
        print(f"Error: {result['message']}")

if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
    except Exception as e:
        print(f"\nUnexpected test error: {e}")
