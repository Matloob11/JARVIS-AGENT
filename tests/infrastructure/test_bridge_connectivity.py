import asyncio
import httpx
import time
import os
from dotenv import load_dotenv

load_dotenv()

VORTEX_SECURITY_TOKEN = os.getenv(
    "VORTEX_SECURITY_TOKEN", "jarvis_secure_token_2025")
BRIDGE_URL = "http://localhost:5001"


async def verify_ui_bridge_endpoints():
    print("Starting UI Bridge Connectivity Verification (Header-Based Auth)...")

    headers = {"X-Vortex-Token": VORTEX_SECURITY_TOKEN}

    async with httpx.AsyncClient() as client:
        # 1. Test Health Endpoint
        try:
            resp = await client.get(f"{BRIDGE_URL}/health")
            print(f"[HEALTH] Code: {resp.status_code}, Body: {resp.json()}")
        except Exception as e:
            print(f"[HEALTH] Failed: {e}")

        # 2. Test Secure Notify Endpoint
        print("\n[NOTIFY] Testing auth and message routing...")
        payload = {
            "type": "TEST_EVENT",
            "data": {"message": "Verification Pulse", "timestamp": time.time()}
        }
        try:
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload, headers=headers)
            print(f"[NOTIFY] Code: {resp.status_code}, Body: {resp.json()}")
            if resp.status_code == 200:
                print("[NOTIFY] Authentication and routing PASSED.")
            else:
                print("[NOTIFY] Verification FAILED.")
        except Exception as e:
            print(f"[NOTIFY] Request Error: {e}")

        # 3. Test Unauthorized Access
        print("\n[AUTH] Testing security gate (invalid token)...")
        bad_headers = {"X-Vortex-Token": "WRONG_TOKEN"}
        try:
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload, headers=bad_headers)
            if resp.status_code == 401:
                print("[AUTH] Blocked invalid access as expected (401).")
            else:
                print(
                    f"[AUTH] SECURITY GAP: Allowed request with code {resp.status_code}")
        except Exception as e:
            print(f"[AUTH] Error: {e}")

if __name__ == "__main__":
    asyncio.run(verify_ui_bridge_endpoints())
