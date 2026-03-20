print("Testing imports for agent_runner.py (SKIP GOOGLE PLUGIN)...")
try:
    import asyncio
    import traceback
    from typing import Optional, Any
    print("Core typing/asyncio OK")
    from livekit import agents, rtc
    from livekit.agents import AgentSession, llm, WorkerOptions, cli
    print("LiveKit OK")
    # from livekit.plugins import google
    # print("LiveKit Google Plugin OK")
    from services.utils.jarvis_logger import setup_logger
    from services.utils.jarvis_health import health_monitor
    from services.utils.jarvis_healing import healing_engine
    from services.utils.jarvis_diagnostics import diagnostics as diagnostics_instance
    print("Services Utils OK")
    from services.ai_core.agent_memory import MemoryExtractor
    print("MemoryExtractor OK")
    from services.info.jarvis_search import (
        get_formatted_datetime, get_current_city, get_current_city_data
    )
    print("Jarvis Search OK")
    # from services.ai_core.agent_loops import (
    #    start_ui_command_listener
    # )
    # print("Agent Loops OK")
    from services.automation.jarvis_clipboard import ClipboardMonitor
    print("Clipboard OK")
    print("ALL TESTED IMPORTS SUCCESSFUL")
except Exception as e:
    print("\nIMPORT FAILED:")
    import traceback
    traceback.print_exc()
