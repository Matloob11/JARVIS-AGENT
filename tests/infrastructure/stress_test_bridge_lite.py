import asyncio
import httpx
import time
import os
import statistics
from dotenv import load_dotenv

load_dotenv()

VORTEX_SECURITY_TOKEN = os.getenv("VORTEX_SECURITY_TOKEN", "jarvis_secure_token_2025")
BRIDGE_URL = "http://localhost:5001"

async def stress_test_bridge():
    print("--- JARVIS PRODUCTION STRESS TEST ---")
    print(f"Target: {BRIDGE_URL}/notify")
    print("Test: 500 rapid notification pulses...")
    
    headers = {"X-Vortex-Token": VORTEX_SECURITY_TOKEN}
    latencies = []
    errors = 0
    
    async with httpx.AsyncClient() as client:
        start_test = time.time()
        
        # Concurrent bursts
        for burst in range(10):
            tasks = []
            for i in range(50):
                payload = {
                    "type": "STRESS_TEST",
                    "payload": {"id": f"burst_{burst}_{i}", "ts": time.time()}
                }
                tasks.append(client.post(f"{BRIDGE_URL}/notify", json=payload, headers=headers, timeout=2.0))
            
            burst_start = time.time()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            burst_end = time.time()
            
            for res in results:
                if isinstance(res, httpx.Response):
                    if res.status_code == 200:
                        latencies.append(res.elapsed.total_seconds())
                    else:
                        errors += 1
                else:
                    errors += 1
            
            print(f"Burst {burst+1}/10 completed. Partial Errors: {errors}")
            # Dynamic throttle simulation
            await asyncio.sleep(0.1)

        total_time = time.time() - start_test
        
    print("\n--- RESULTS ---")
    print(f"Total Requests: 500")
    print(f"Total Time: {total_time:.2f}s")
    print(f"Total Errors: {errors}")
    if latencies:
        print(f"Avg Latency: {statistics.mean(latencies)*1000:.2f}ms")
        print(f"P95 Latency: {statistics.quantiles(latencies, n=20)[18]*1000:.2f}ms")
    
    if errors > 0 or (latencies and statistics.mean(latencies) > 0.5):
         print("WARNING: Performance degradation detected under load.")
    else:
         print("SUCCESS: System remains stable under concurrent bursts.")

if __name__ == "__main__":
    asyncio.run(stress_test_bridge())
