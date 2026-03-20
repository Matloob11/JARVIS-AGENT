print("1. Testing asyncio/traceback...")
import asyncio
import traceback
print("2. Testing typing...")
from typing import Optional, Any
print("3. Testing livekit.rtc...")
from livekit import rtc
print("4. Testing livekit.agents...")
from livekit import agents
print("5. Testing livekit.agents components...")
from livekit.agents import AgentSession, llm, WorkerOptions, cli
print("6. Testing jarvis_logger...")
from services.utils.jarvis_logger import setup_logger
print("7. Testing jarvis_health...")
from services.utils.jarvis_health import health_monitor
print("8. Testing jarvis_healing...")
from services.utils.jarvis_healing import healing_engine
print("9. Testing jarvis_diagnostics...")
from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance
print("10. Testing agent_memory...")
from services.ai_core.agent_memory import MemoryExtractor
print("11. Testing jarvis_search...")
from services.info.jarvis_search import (
    get_formatted_datetime, get_current_city, get_current_city_data
)
print("12. Testing jarvis_clipboard...")
from services.automation.jarvis_clipboard import ClipboardMonitor
print("13. ALL IMPORTS FINISHED")
