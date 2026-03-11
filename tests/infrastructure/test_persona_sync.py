import asyncio
import httpx
import time
import os
from dotenv import load_dotenv

load_dotenv()

VORTEX_SECURITY_TOKEN = os.getenv(
    "VORTEX_SECURITY_TOKEN", "jarvis_secure_token_2025")
BRIDGE_URL = "http://localhost:5001"


async def verify_persona_sync():
    print("Starting Persona Sync Verification...")

    headers = {"X-Vortex-Token": VORTEX_SECURITY_TOKEN}

    async with httpx.AsyncClient() as client:
        # Test Persona Change to Anna
        print("[PERSONA] Switching to 'anna'...")
        payload = {
            "type": "persona_change",
            "payload": "anna"
        }
        try:
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload, headers=headers)
            print(f"[PERSONA] Code: {resp.status_code}, Body: {resp.json()}")

            # Now verify the change in the bridge state (implied by 200)
            if resp.status_code == 200:
                print("✅ [PERSONA] Switch to 'anna' successful.")
            else:
                print("❌ [PERSONA] Switch FAILED.")

            # Switch back to Jarvis
            print("\n[PERSONA] Switching back to 'jarvis'...")
            payload["payload"] = "jarvis"
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload, headers=headers)
            print(f"[PERSONA] Code: {resp.status_code}, Body: {resp.json()}")
            if resp.status_code == 200:
                print("✅ [PERSONA] Switch to 'jarvis' successful.")
            else:
                print("❌ [PERSONA] Switch back FAILED.")

        except Exception as e:
            print(f"❌ [PERSONA] Error: {e}")

if __name__ == "__main__":
    asyncio.run(verify_persona_sync())
