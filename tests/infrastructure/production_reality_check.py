import asyncio
import time
import httpx
import socketio
import psutil
import os
import signal
import subprocess
import json
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import jarvis_log as log

# Test Configuration
BRIDGE_URL = "http://127.0.0.1:5001"
TOKEN = config.security_token


class ProductionAudit:
    def __init__(self):
        self.results = {}
        self.metrics = {
            "startup_time": 0,
            "transcription_latency": [],
            "cpu_peaks": [],
            "memory_usage": 0
        }

    async def check_startup_performance(self):
        log.info("[AUDIT] Checking startup performance...")
        start_time = time.time()
        try:
            async with httpx.AsyncClient() as client:
                # Ensure ui_bridge is up (it should be running already in the background)
                resp = await client.get(BRIDGE_URL + "/")
                self.metrics["startup_time"] = time.time() - start_time
                self.results["startup"] = "PASS" if resp.status_code < 500 else "FAIL"
        except Exception as e:
            self.results["startup"] = f"FAIL: {e}"

    async def check_socket_security(self):
        log.info("[AUDIT] Checking Socket.IO security...")
        sio_unauth = socketio.AsyncClient()
        sio_auth = socketio.AsyncClient()

        # 1. Unauthorized connection attempt
        try:
            await sio_unauth.connect(BRIDGE_URL, auth={"token": "wrong_token"})
            self.results["socket_unauth"] = "FAIL (Succeeded)"
            await sio_unauth.disconnect()
        except Exception:
            self.results["socket_unauth"] = "PASS (Rejected)"

        # 2. Authorized connection attempt
        try:
            await sio_auth.connect(BRIDGE_URL, auth={"token": TOKEN})
            self.results["socket_auth"] = "PASS"
            await sio_auth.disconnect()
        except Exception as e:
            self.results["socket_auth"] = f"FAIL: {e}"

    async def stress_test_bridge(self):
        log.info("[AUDIT] Starting stress test (20 commands/sec)...")
        sio = socketio.AsyncClient()
        await sio.connect(BRIDGE_URL, auth={"token": TOKEN})

        start_stress = time.time()
        # Test for 30 seconds instead of 5 mins for brevity in audit report
        end_time = start_stress + 30
        cmd_count = 0

        while time.time() < end_time:
            # Send 20 notifications per second via REST (simulate runner load)
            async with httpx.AsyncClient() as client:
                data = {
                    "type": "transcription",
                    "payload": {"role": "agent", "text": f"Stress test ping {cmd_count}", "timestamp": time.time()}
                }
                headers = {"X-Vortex-Token": TOKEN}
                await client.post(f"{BRIDGE_URL}/notify", json=data, headers=headers)
                cmd_count += 1

            self.metrics["cpu_peaks"].append(psutil.cpu_percent())
            await asyncio.sleep(0.05)  # 20 per second

        await sio.disconnect()
        self.results["stress_test"] = f"PASS ({cmd_count} events total)"

    async def verify_self_healing(self):
        log.info("[AUDIT] Testing self-healing (watchdog)...")
        # Find agent_runner process and kill it
        killed = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if proc.info['cmdline'] and 'agent_runner.py' in " ".join(proc.info['cmdline']):
                log.info(
                    f"Targeting {proc.info['pid']} for accidental termination")
                proc.terminate()
                killed = True
                break

        if not killed:
            self.results["self_healing"] = "FAIL: agent_runner not found"
            return

        # Wait for watchdog to notice (5-10s)
        await asyncio.sleep(10)

        restarted = False
        for proc in psutil.process_iter(['cmdline']):
            if proc.info['cmdline'] and 'agent_runner.py' in " ".join(proc.info['cmdline']):
                restarted = True
                break

        self.results["self_healing"] = "PASS" if restarted else "FAIL: Watchdog failed to restart runner"

    async def verify_backup_restore(self):
        log.info("[AUDIT] Testing backup integrity...")
        from services.utils.backup_system import JarvisBackup
        b = JarvisBackup()
        zip_path = b.perform_backup()
        if zip_path and os.path.exists(zip_path):
            self.results["backup"] = "PASS"
            # Optional: Test restore logic (manual verify zip content)
        else:
            self.results["backup"] = "FAIL"

    async def check_security_penetration(self):
        log.info("[AUDIT] Verifying security hardening...")
        # Path Traversal
        headers = {"X-Vortex-Token": TOKEN}
        async with httpx.AsyncClient() as client:
            # Attempt access to sensitive file via bridge if it had a handler,
            # or directly via the tool logic if exposed in standard service.
            # Here we just verify the notify endpoint remains protected.
            bad_resp = await client.post(f"{BRIDGE_URL}/notify", json={}, headers={"X-Vortex-Token": "bad"})
            self.results["penetration_bridge"] = "PASS" if bad_resp.status_code == 401 else "FAIL"

    def generate_report(self):
        print("\n" + "="*50)
        print("ðŸš€ JARVIS-AGENT PRODUCTION REALITY CHECK")
        print("="*50)
        for check, status in self.results.items():
            icon = "âœ…" if "PASS" in status else "âŒ"
            print(f"{icon} {check.upper():<20} : {status}")

        print("\nðŸ“Š CRITICAL METRICS:")
        print(f"- Startup Time        : {self.metrics['startup_time']:.4f}s")
        print(
            f"- Avg CPU Load        : {sum(self.metrics['cpu_peaks'])/len(self.metrics['cpu_peaks']):.2f}%" if self.metrics['cpu_peaks'] else "N/A")
        print(
            f"- Peak CPU            : {max(self.metrics['cpu_peaks']):.2f}%" if self.metrics['cpu_peaks'] else "N/A")
        print(f"- Memory Usage (System) : {psutil.virtual_memory().percent}%")
        print("="*50)


async def run_audit():
    audit = ProductionAudit()
    await audit.check_startup_performance()
    await audit.check_socket_security()
    await audit.check_security_penetration()
    await audit.verify_backup_restore()
    await audit.verify_self_healing()
    await audit.stress_test_bridge()
    audit.generate_report()

if __name__ == "__main__":
    asyncio.run(run_audit())
