"""
# services/utils/jarvis_diagnostics.py
Self-Diagnostics and Health Reporting Module for JARVIS.
Checks dependencies, permissions, and service connectivity.
"""

import os
import subprocess
import shutil
import asyncio
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-DIAGNOSTICS")


class JarvisDiagnostics:
    """
    Performs comprehensive system checks to ensure JARVIS is fully functional.
    Reports missing dependencies, configuration issues, or service outages.
    """
    def __init__(self):
        """Initializes diagnostics with default check registry."""
        self.checks = [
            self.check_network,
            self.check_dependencies,
            self.check_environment,
            self.check_disk_space
        ]

    async def run_all(self):
        """Executes all diagnostics checks concurrently."""
        logger.info("Running system diagnostics...")
        results = []
        for check in self.checks:
            try:
                res = await check()
                results.append(res)
            except (subprocess.SubprocessError, OSError, ValueError) as e:
                results.append({"status": "error", "error": str(e)})

        logger.info("Diagnostics complete: %d checks performed.", len(results))
        return results

    async def check_network(self):
        """Checks Internet connectivity via ping."""
        import sys  # pylint: disable=import-outside-toplevel
        try:
            # -n is Windows flag, -c is Linux/macOS
            count_flag = "-n" if sys.platform == "win32" else "-c"
            process = await asyncio.create_subprocess_exec(
                "ping", count_flag, "1", "8.8.8.8",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            await process.communicate()
            if process.returncode == 0:
                return {"check": "network", "status": "online"}
            return {"check": "network", "status": "offline"}
        except (subprocess.SubprocessError, OSError):
            return {"check": "network", "status": "error"}

    async def check_dependencies(self):
        """Verifies presence of core command-line tools."""
        deps = ["ffmpeg", "pnpm", "node", "python"]
        missing = []
        for d in deps:
            if not shutil.which(d):
                missing.append(d)
        return {
            "check": "dependencies",
            "status": "pass" if not missing else "fail",
            "missing": missing
        }

    async def check_environment(self):
        """Checks required environment variables."""
        required = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
        missing = [v for v in required if v not in os.environ]
        return {
            "check": "environment",
            "status": "pass" if not missing else "fail",
            "missing": missing
        }

    async def check_disk_space(self):
        """Checks if there's enough free space for logs and downloads."""
        usage = shutil.disk_usage(".")
        free_gb = usage.free / (1024**3)
        return {
            "check": "disk_space",
            "status": "pass" if free_gb > 1.0 else "warning",
            "free_gb": f"{free_gb:.2f}"
        }

    async def generate_report(self):
        """Generates a human-friendly diagnostics report."""
        results = await self.run_all()
        report = "JARVIS Diagnostics Report:\n"
        for r in results:
            report += f"- {r.get('check', 'unknown')}: {r.get('status')}\n"
        return report


async def check_system_integrity():
    """Stand-alone integrity check for deployment verification."""
    diag = JarvisDiagnostics()
    return await diag.run_all()


if __name__ == "__main__":
    # Test diagnostics locally
    diagnostics = JarvisDiagnostics()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        report_data = loop.run_until_complete(diagnostics.generate_report())
        print(report_data)
    finally:
        loop.close()


# Global instance
diagnostics = JarvisDiagnostics()
