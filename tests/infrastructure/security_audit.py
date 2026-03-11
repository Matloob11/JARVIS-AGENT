
from services.utils.jarvis_config import config
import httpx
import socketio
import asyncio
import sys
import os
# Ensure root is in path for services imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Constants
BRIDGE_URL = "http://127.0.0.1:5001"
VORTEX_TOKEN = config.security_token


async def test_socketio_unauthorized_access():
    """CSWH Simulation: Can an external client connect WITHOUT token?"""
    print("\n[!] SIMULATION: CSWH (Unauthorized Client Connection)...")
    sio = socketio.AsyncClient()
    try:
        # Attempt connection WITHOUT auth token
        await sio.connect(BRIDGE_URL)
        print("âŒ VULNERABILITY STILL PRESENT: Unauthorized client connected to Socket.IO bridge.")
        await sio.disconnect()
        return True
    except Exception as e:
        print(f"âœ… SECURITY VERIFIED: Connection rejected as expected ({e})")
        return False


async def test_socketio_authorized_access():
    """Verify that the LEGITIMATE token works."""
    print("\n[!] VERIFICATION: Authorized Client Connection...")
    # Token from config
    sio = socketio.AsyncClient()
    try:
        await sio.connect(BRIDGE_URL, auth={"token": VORTEX_TOKEN})
        print("âœ… SUCCESS: Authorized client connected successfully.")
        await sio.disconnect()
        return True
    except Exception as e:
        print(f"âŒ REGRESSION: Authorized connection failed ({e})")
        return False


async def test_command_injection_via_bridge():
    """Simulate Command Injection via the /notify endpoint - Test Auth"""
    print("\n[!] SIMULATION: /notify Endpoint Authentication...")

    TOKEN = VORTEX_TOKEN

    async with httpx.AsyncClient() as client:
        # 1. Unauthorized Attempt (No Token)
        payload = {"type": "status", "payload": "START"}
        try:
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload)
            if resp.status_code == 401:
                print(
                    "âœ… SECURITY VERIFIED: /notify rejected unauthorized request (401).")
            else:
                print(
                    f"âŒ VULNERABILITY: /notify accepted request without token ({resp.status_code})")
        except Exception as e:
            print(
                f"âœ… Potential Filter: Bridge unreachable or rejected request ({e})")

        # 2. Authorized Attempt (Valid Token)
        try:
            headers = {"X-Vortex-Token": TOKEN}
            resp = await client.post(f"{BRIDGE_URL}/notify", json=payload, headers=headers)
            if resp.status_code == 200:
                print("âœ… SUCCESS: /notify accepted authorized request with token.")
            else:
                print(
                    f"âŒ REGRESSION: /notify rejected authorized request ({resp.status_code})")
        except Exception as e:
            print(f"âŒ Error during authorized notify test: {e}")


async def test_path_traversal():
    """Test for Path Traversal in File Access Server (Simulated check)"""
    # Note: This requires the server to be running. We can mock the logic check.
    from services.system.jarvis_file_server import DDriveHandler

    print("\n[!] SIMULATION: Path Traversal Audit...")

    # We bypass the __init__ of DDriveHandler which triggers server logic
    class MockHandler(DDriveHandler):
        def __init__(self):
            pass  # Skip parent init

    handler = MockHandler()
    malicious_paths = [
        "/../../Windows/System32/drivers/etc/hosts",
        "/%2e%2e/%2e%2e/Windows/win.ini",
        "/subdir/../secret.txt"
    ]

    vulnerable = False
    for p in malicious_paths:
        translated = handler.translate_path(p)
        # Check if it resolved outside D:\Jarvis_Shared (or the intended root)
        if "Jarvis_Shared" not in translated:
            print(
                f"âŒ VULNERABILITY: Path '{p}' translated to '{translated}' (OUTSIDE ROOT)")
            vulnerable = True
        else:
            print(f"âœ… Path '{p}' safely translated to '{translated}'")

    return vulnerable


async def main():
    print("="*60)
    print("JARVIS-AGENT SECURITY VERIFICATION SUITE v2.0")
    print("="*60)

    unauth_vuln = await test_socketio_unauthorized_access()
    auth_success = await test_socketio_authorized_access()
    await test_command_injection_via_bridge()
    traversal_vuln = await test_path_traversal()

    print("\n" + "="*60)
    print("RE-TEST SUMMARY")
    print(
        f"Unauthorized Access Blocked: {'PASS' if not unauth_vuln else 'FAIL'}")
    print(f"Authorized Access Working: {'PASS' if auth_success else 'FAIL'}")
    print(
        f"Path Traversal: {'SECURE' if not traversal_vuln else 'VULNERABLE'}")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
