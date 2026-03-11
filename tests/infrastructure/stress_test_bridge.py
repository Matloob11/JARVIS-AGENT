import asyncio
try:
    import socketio
except ImportError:
    print("âŒ python-socketio not installed")
    import sys
    sys.exit(1)

import time
import psutil
import os
import sys

# Validate socketio version/content
if not hasattr(socketio, 'AsyncClient'):
    print(f"âŒ socketio module ({socketio.__file__}) has no AsyncClient. Check if you installed 'socketio' (wrong) instead of 'python-socketio' (right).")
    # Attempt fallback if it's just a path issue
    try:
        from socketio import AsyncClient
    except ImportError:
        print("âŒ Could not import AsyncClient from socketio.")
        sys.exit(1)
else:
    from socketio import AsyncClient

# Standardized settings
BRIDGE_URL = "http://127.0.0.1:5001"
STRESS_DURATION = 300  # 5 minute stress test
MESSAGE_FREQ = 0.05  # 20 messages per second

class StressTester:
    def __init__(self):
        self.sio = socketio.AsyncClient()
        self.msg_count = 0
        self.errors = []
        self.start_time = None
        self.latencies = []

    async def connect(self):
        try:
            await self.sio.connect(BRIDGE_URL)
            print("âœ… Connected to Bridge for Stress Test")
        except Exception as e:
            print(f"âŒ Connection failed: {e}")
            sys.exit(1)

    async def flood_transcription(self, client):
        """Flood the bridge with transcription updates."""
        print(f"ðŸŒŠ Flooding bridge with transcription events ({1/MESSAGE_FREQ} msg/s)...")
        while time.time() - self.start_time < STRESS_DURATION:
            try:
                msg = {
                    "type": "transcription",
                    "payload": {
                        "role": "agent",
                        "text": f"Stress test message {self.msg_count}",
                        "timestamp": time.ctime()
                    }
                }
                t0 = time.perf_counter()
                resp = await client.post(f"{BRIDGE_URL}/notify", json=msg)
                self.latencies.append(time.perf_counter() - t0)

                self.msg_count += 1
                await asyncio.sleep(MESSAGE_FREQ)
            except Exception as e:
                self.errors.append(str(e))

    async def toggle_persona(self, client):
        """Rapidly switch personas to detect state race conditions."""
        print("ðŸŽ­ Simulating rapid persona switching...")
        while time.time() - self.start_time < STRESS_DURATION:
            try:
                persona = "anna" if self.msg_count % 2 == 0 else "jarvis"
                await client.post(f"{BRIDGE_URL}/notify", json={
                    "type": "persona_change",
                    "payload": persona
                })
                await asyncio.sleep(1.0) # Switch every second
            except Exception as e:
                self.errors.append(str(e))

    async def monitor_resources(self):
        """Track memory and CPU during stress."""
        mem_samples = []
        while time.time() - self.start_time < STRESS_DURATION:
            mem = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            mem_samples.append(mem)
            await asyncio.sleep(2)
        return mem_samples

    async def run(self):
        self.start_time = time.time()
        await self.connect()

        # We use HTTpx client for all notify calls
        import httpx
        async with httpx.AsyncClient() as client:
            # Run concurrent tasks
            results = await asyncio.gather(
                self.flood_transcription(client),
                self.toggle_persona(client),
                self.monitor_resources()
            )

        mem_samples = results[2]
        duration = time.time() - self.start_time
        avg_latency = sum(self.latencies) / len(self.latencies) if self.latencies else 0

        print("\n--- STRESS TEST REPORT ---")
        print(f"Duration: {duration:.2f}s")
        print(f"Messages Sent: {self.msg_count}")
        print(f"Avg Latency: {avg_latency*1000:.2f}ms")
        print(f"Errors Detected: {len(self.errors)}")
        print(f"Memory Growth: {mem_samples[-1] - mem_samples[0]:.2f}MB (Final: {mem_samples[-1]:.2f}MB)")

        if self.errors:
            print("ðŸš¨ Errors list:")
            for err in set(self.errors):
                print(f" - {err}")

        await self.sio.disconnect()

if __name__ == "__main__":
    tester = StressTester()
    asyncio.run(tester.run())
