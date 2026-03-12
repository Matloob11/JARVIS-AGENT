import tracemalloc
import asyncio
import psutil
import os
import signal
import sys
from agent_runner import entrypoint
from livekit import agents

async def run_with_profiler():
    print("--- DEEP MEMORY PROFILER (tracemalloc) ---")
    tracemalloc.start()
    
    # Mocking JobContext if necessary, else we just monitor a live process
    # But for a script, let's just snapshot the running processes
    
    snapshot1 = tracemalloc.take_snapshot()
    
    print("Monitoring for 30 seconds...")
    await asyncio.sleep(30)
    
    snapshot2 = tracemalloc.take_snapshot()
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')

    print("\n[TOP LEAKS]")
    for stat in top_stats[:10]:
        print(stat)

if __name__ == "__main__":
    # This script will monitor its own memory if we import and run logic,
    # but we want to monitor the existing production runner.
    # To do that, we need to run a special version of agent_runner.
    print("Please use the modified 'agent_runner_debug.py' to get local traces.")
