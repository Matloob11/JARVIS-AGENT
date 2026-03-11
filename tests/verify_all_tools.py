import os
from livekit.agents import llm
import sys
import importlib
import inspect
from typing import List

# Setup path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)


def verify_tools():
    print("--- JARVIS Tool Verification ---")

    try:
        from services.ai_core.jarvis_plugin_manager import plugin_manager
    except ImportError as e:
        print(f"CRITICAL ERROR: Could not import plugin_manager: {e}")
        return

    # 1. Discover Plugins
    services_path = os.path.join(project_root, "services")
    print(f"Discovering tools in: {services_path}")
    plugin_manager.discover_plugins(services_path)

    tools = plugin_manager.get_livekit_tools()

    from services.ai_core.jarvis_plugin_manager import jarvis_tool

    # Internal tools from BrainAssistant (simulated)
    @jarvis_tool
    async def tool_set_wake_word_mode(active: bool) -> dict:
        """Toggle wake word mode."""
        return {}

    @jarvis_tool
    async def tool_change_voice(voice_name: str) -> dict:
        """Change voice."""
        return {}

    @jarvis_tool
    async def tool_toggle_gf_mode(active: bool) -> dict:
        """Toggle GF mode."""
        return {}

    tools.extend([
        llm.function_tool(tool_set_wake_word_mode),
        llm.function_tool(tool_change_voice),
        llm.function_tool(tool_toggle_gf_mode),
    ])

    print(f"Checking {len(tools)} total tools...")

    failures = []

    for tool in tools:
        name = tool.__name__
        print(f"Checking Tool: {name}...", end=" ", flush=True)

        try:
            # Check for generic wrapper issues
            if not hasattr(tool, "__is_jarvis_tool__"):
                raise ValueError("Missing __is_jarvis_tool__ attribute.")

            # Check for docstrings (essential for LLM)
            doc = inspect.getdoc(tool)
            if not doc:
                print("WARNING (No Docstring)", end=" ")

            # Check for parameters
            sig = inspect.signature(tool)
            if not sig.parameters:
                # Some tools might not have params, but we should note it
                pass

            print("OK")

        except Exception as e:
            print(f"FAILED: {e}")
            failures.append((name, str(e)))

    print("\n--- Summary ---")
    if not failures:
        print("PASS: All tools verified successfully.")
    else:
        print(f"FAIL: {len(failures)} tools had issues.")
        for name, err in failures:
            print(f" - {name}: {err}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(verify_tools())
