import asyncio
import sys
import os

# Add root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.automation.jarvis_whatsapp_automation import automate_whatsapp

async def run_test():
    print("Starting WhatsApp Automation Verification...")
    print("Contact: Matloob")
    print("Message: Kia hall ha?")
    
    # Run tool
    result = await automate_whatsapp(
        contact_name="Matloob", 
        message="Kia hall ha?", 
        close_after=False # Keep open for verification
    )
    
    print("\n--- TEST RESULT ---")
    print(f"Status: {result.get('status')}")
    print(f"Message: {result.get('message')}")
    print("-------------------\n")
    
    if result.get('status') == "success":
        print("Test PASSED successfully. Please check WhatsApp for visual confirmation.")
    else:
        print("❌ Test FAILED. Check logs for details.")

if __name__ == "__main__":
    asyncio.run(run_test())
