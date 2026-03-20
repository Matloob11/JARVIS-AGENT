"""
# services/automation/tests/test_whatsapp.py
Standalone test script for WhatsApp automation.
"""
import asyncio
import sys
import os

# Add root directory to path to allow importing services
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.automation.jarvis_whatsapp_automation import automate_whatsapp

async def test_send_whatsapp():
    print("--- 📱 JARVIS WhatsApp Automation Test ---")
    
    contact = "Matloob"
    message = "main subah ghar nahi aa raha"
    
    print(f"\n🚀 Sending message to '{contact}': {message}")
    result = await automate_whatsapp(contact, message, close_after=True)
    
    print("\nResult:")
    print(f"Status: {result.get('status')}")
    print(f"Message: {result.get('message')}")
    
    if result.get("status") == "success":
        print("\n✅ SUCCESS: WhatsApp message automation flow completed.")
    else:
        print("\n❌ FAILURE: Check error details above.")

if __name__ == "__main__":
    try:
        asyncio.run(test_send_whatsapp())
    except Exception as e:
        print(f"\n❌ Test execution failed: {e}")
