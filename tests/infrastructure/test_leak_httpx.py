import tracemalloc
import asyncio
import os
import gc
from services.utils.jarvis_bridge import notify_ui, get_http_client
from services.utils.jarvis_config import config

async def test_notify_leak():
    print("Testing notify_ui memory growth...")
    tracemalloc.start()
    
    # Baseline
    gc.collect()
    s1 = tracemalloc.take_snapshot()
    
    print("Sending 1000 fast notifications...")
    tasks = []
    for i in range(1000):
        tasks.append(notify_ui("TICK"))
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    gc.collect()
    s2 = tracemalloc.take_snapshot()
    
    top_stats = s2.compare_to(s1, 'lineno')
    print("\n[TOP ALLOCATIONS]")
    for stat in top_stats[:10]:
        print(stat)

if __name__ == "__main__":
    asyncio.run(test_notify_leak())
