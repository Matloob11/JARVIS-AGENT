"""
# jarvis_diagnostics.py
Jarvis Diagnostics and Environment Verification Module.
Ensures all APIs, dependencies, and environment variables are ready for operation.
"""

import asyncio
import shutil
import os
import sys
import json
from typing import Dict, Any
import requests
from dotenv import load_dotenv
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-DIAGNOSTICS")

load_dotenv()


class SystemCheck:
    """Handles verification of system dependencies and API connectivity."""

    def __init__(self):
        self.results = {}

    async def check_env_vars(self) -> Dict[str, str]:
        """Verify presence of essential environment variables."""
        required_vars = [
            "GOOGLE_API_KEY", "WEATHER_API_KEY", "GOOGLE_SEARCH_API_KEY",
            "SEARCH_ENGINE_ID", "HF_TOKEN", "EMAIL_USER", "EMAIL_APP_PASSWORD",
            "OPENROUTER_API_KEY"
        ]
        var_status = {}
        for var in required_vars:
            val = os.getenv(var)
            if val and len(val) > 5:
                var_status[var] = "✅ OK"
            else:
                var_status[var] = "❌ MISSING"
        return var_status

    async def check_external_dependencies(self) -> Dict[str, str]:
        """Check for external CLI tools."""
        deps = {
            "ffmpeg": "FFmpeg (Audio/Video conversion)",
            "yt-dlp": "YouTube Downloader Engine",
            "python": "Python Interpreter",
            "cmd": "Window Command Prompt"
        }
        dep_status = {}
        for cmd, desc in deps.items():
            path = shutil.which(cmd)
            if path:
                dep_status[desc] = f"✅ Installed ({path})"
            else:
                dep_status[desc] = "❌ NOT FOUND"
        return dep_status

    async def check_api_connectivity(self) -> Dict[str, str]:
        """Mock test connectivity to core API endpoints."""
        endpoints = {
            "Google Gemini": "https://generativelanguage.googleapis.com",
            "Hugging Face": "https://api-inference.huggingface.co",
            "OpenRouter": "https://openrouter.ai/api/v1",
            "OpenWeatherMap": "https://api.openweathermap.org"
        }
        conn_status = {}
        for name, url in endpoints.items():
            try:
                # Simple HEAD request to verify endpoint is reachable
                response = await asyncio.to_thread(requests.head, url, timeout=5)
                if response.status_code < 500:
                    conn_status[name] = "✅ Reachable"
                else:
                    conn_status[name] = f"⚠️ Server Error ({response.status_code})"
            except (requests.RequestException, asyncio.TimeoutError):
                conn_status[name] = "❌ Unreachable (Check Internet)"
        return conn_status

    async def run_full_diagnostics(self) -> Dict[str, Any]:
        """Perform all checks and compile a health report."""
        logger.info("Starting comprehensive system diagnostics...")

        env_results = await self.check_env_vars()
        dep_results = await self.check_external_dependencies()
        api_results = await self.check_api_connectivity()

        # Calculate health percentage
        total_checks = len(env_results) + len(dep_results) + len(api_results)
        passed_checks = sum(1 for v in env_results.values() if "✅" in v) + \
            sum(1 for v in dep_results.values() if "✅" in v) + \
            sum(1 for v in api_results.values() if "✅" in v)

        health_score = (passed_checks / total_checks) * 100

        self.results = {
            "health_score": f"{health_score:.1f}%",
            "environment_variables": env_results,
            "dependencies": dep_results,
            "api_connectivity": api_results,
            "summary": "Elite Status" if health_score > 90 else "Action Required"
        }

        logger.info("Diagnostics completed. Health Score: %s",
                    self.results["health_score"])
        return self.results


# Global Instance
diagnostics = SystemCheck()


def format_health_report(report: Dict[str, Any]) -> str:
    """Formats the nested diagnostic results into a readable Urdu/English report."""
    msg = f"🔱 **JARVIS HEALTH REPORT** 🔱\nHealth Score: {report['health_score']}\n"
    msg += f"Status: {report['summary']}\n\n"

    msg += "📋 **Environment Variables:**\n"
    for k, v in report['environment_variables'].items():
        msg += f"- {k}: {v}\n"

    msg += "\n🛠️ **Dependencies:**\n"
    for k, v in report['dependencies'].items():
        msg += f"- {k}: {v}\n"

    msg += "\n🌐 **API Connectivity:**\n"
    for name, status in report['api_connectivity'].items():
        msg += f"- {name}: {status}\n"

    return msg  # This specific line has a small bug to test self-healing later


try:
    from livekit.agents import function_tool

    @function_tool
    async def tool_perform_diagnostics() -> dict:
        """
        Runs a comprehensive system health check to verify all tools and APIs are working.
        Use this if 'Sir' wants to check if everything is ready or if tools are failing.
        """
        report = await diagnostics.run_full_diagnostics()
        # Formatted message for the voice assistant
        urdu_summary = f"Sir Matloob, maine diagnostic run kar liye hain. Health score {report['health_score']} hai. "
        if "Action Required" in report['summary']:
            urdu_summary += "Kuch masle mile hain jinhe theek karne ki zaroorat hai."
        else:
            urdu_summary += "Saare systems bilkul theek kaam kar rahe hain."

        return {
            "status": "success",
            "health_report": report,
            "message": urdu_summary
        }
except ImportError:
    pass

if __name__ == "__main__":
    # Local CLI testing
    async def test():
        """Local test runner."""
        try:
            # Force UTF-8 encoding for console output if possible
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')

            res = await diagnostics.run_full_diagnostics()
            print(json.dumps(res, indent=2, ensure_ascii=False))
        except (IOError, ValueError, UnicodeEncodeError) as e:
            # Fallback for old terminals or encoding issues
            print(f"Diagnostics failed to print: {e}")
            # Try printing without emojis if possible, but the above reconfigure is usually enough
    asyncio.run(test())
