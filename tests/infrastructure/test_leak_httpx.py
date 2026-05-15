import tracemalloc
import asyncio
import gc
from unittest.mock import AsyncMock, patch

from services.utils.jarvis_bridge import notify_ui

async def test_notify_leak():
    print("Testing notify_ui memory growth...")
    tracemalloc.start()
    
    # Baseline
    gc.collect()
    s1 = tracemalloc.take_snapshot()
    
    print("Sending fast mocked notifications...")
    with patch("services.utils.jarvis_bridge.notify_event", new_callable=AsyncMock) as mock_notify:
        tasks = [notify_ui("TICK") for _ in range(100)]
        await asyncio.gather(*tasks, return_exceptions=True)
        assert mock_notify.await_count == 100
    
    gc.collect()
    s2 = tracemalloc.take_snapshot()
    
    top_stats = s2.compare_to(s1, 'lineno')
    print("\n[TOP ALLOCATIONS]")
    for stat in top_stats[:10]:
        print(stat)

if __name__ == "__main__":
    asyncio.run(test_notify_leak())
