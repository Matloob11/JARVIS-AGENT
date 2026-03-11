import asyncio
import time
import httpx
import psutil
from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_config import config

logger = setup_logger("QA-STRESS")

class StressQA:
    """
    Autonomous Stress Testing module.
    Simulates message bursts and monitors system degradation.
    """
    def __init__(self, bridge_url="http://127.0.0.1:5001"):
        self.bridge_url = bridge_url
        self.token = config.security_token

    async def run(self) -> dict:
        """Executes a 30-second stress audit."""
        logger.info("⚡ Starting system stress test...")
        
        start_time = time.time()
        msg_count = 0
        latencies = []
        errors = 0
        
        headers = {
            "X-Vortex-Token": self.token,
            "X-Vortex-Signature": "INTERNAL"
        }
        
        async with httpx.AsyncClient() as client:
            while time.time() - start_time < 30:
                try:
                    payload = {
                        "type": "transcription",
                        "payload": {"role": "agent", "text": f"QA Stress Ping {msg_count}", "timestamp": time.time()}
                    }
                    t0 = time.perf_counter()
                    resp = await client.post(f"{self.bridge_url}/notify", json=payload, headers=headers)
                    latencies.append(time.perf_counter() - t0)
                    
                    if resp.status_code != 200:
                        errors += 1
                except Exception as e:
                    errors += 1
                    logger.debug("Stress ping failed: %s", e)
                
                msg_count += 1
                await asyncio.sleep(0.01) # Simulating 100 msg/sec burst

        avg_latency = (sum(latencies) / len(latencies)) * 1000 if latencies else 0
        vitals = psutil.cpu_percent()
        
        status = "PASS" if errors == 0 and avg_latency < 100 else "DEGRADED"
        if errors > (msg_count * 0.1): # Over 10% errors
            status = "FAIL"
            
        logger.info("📊 Stress Test Result: %s (Avg Latency: %.2fms, Load: %.1f%%)", status, avg_latency, vitals)
        
        return {
            "status": status,
            "messages_sent": msg_count,
            "avg_latency_ms": avg_latency,
            "error_count": errors,
            "cpu_load": vitals
        }
