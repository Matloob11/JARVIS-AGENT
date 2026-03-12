import asyncio
import tracemalloc
import agent_runner
from livekit import agents
from unittest.mock import MagicMock

async def debug_main():
    print("Starting Agent Runner with Memory Tracing...")
    tracemalloc.start()
    s1 = tracemalloc.take_snapshot()
    
    # Run the entrypoint logic in a mock context or just let it idle
    # Since we can't easily mock the LiveKit connection, we will monitor the REAL RUNNER
    # by identifying the source of the leak in the code.
    
    # Let's check the objects in the current process
    import gc
    import objgraph # If available
    
    print("Scanning for large objects...")
    # ...
    
if __name__ == "__main__":
     # Actually, let's just add the tracing to a new version of agent_runner
     pass
