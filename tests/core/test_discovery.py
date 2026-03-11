from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_plugin_manager import plugin_manager
import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.getcwd())


logger = setup_logger("VERIFY-DISCOVERY")


async def verify_plugins():
    logger.info("Starting Plugin Discovery Verification...")

    # Absolute path to services directory
    services_path = os.path.join(os.getcwd(), "services")

    try:
        plugin_manager.discover_plugins(services_path)
        tools = plugin_manager.get_tools()

        print(f"\n--- DISCOVERY RESULTS ---")
        print(f"Total Tools Registered: {len(tools)}")

        if len(tools) == 0:
            print("❌ FAILURE: No tools discovered!")
            sys.exit(1)

        # List some discovered tools for confirmation
        for i, tool in enumerate(tools[:10]):
            # Confirmed attributes from inspection
            try:
                name = tool.info.name
                desc = tool.info.description[:50]
                print(f"[{i+1}] {name}: {desc}...")
            except AttributeError:
                # Fallback for different SDK versions
                name = getattr(tool, "name", "Unknown")
                print(f"[{i+1}] {name}: [No description available]...")

        print("--------------------------\n")
        print("✅ Plugin Discovery test PASSED.")

    except Exception as e:
        print(f"❌ DISCOVERY CRASHED: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(verify_plugins())
