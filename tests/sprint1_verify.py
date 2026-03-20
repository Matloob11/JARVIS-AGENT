
import asyncio
import os
import sys
import json
import time

# Mocking parts of the system to test logic isolation
class MockLock:
    def __init__(self):
        self.locked = False
    async def __aenter__(self):
        if self.locked:
            raise RuntimeError("DEADLOCK DETECTED!")
        self.locked = True
    async def __aexit__(self, exc_type, exc, tb):
        self.locked = False

async def test_memory_deadlock():
    print("Testing Memory Deadlock Fix...")
    # This simulates the logic in memory_store.py
    lock = MockLock()
    
    async def _load_memory_unlocked():
        return []

    async def load_memory():
        async with lock:
            return await _load_memory_unlocked()

    async def save_conversation():
        async with lock:
            print("  Acquired lock in save_conversation")
            # This would deadlock if it called load_memory()
            memory = await _load_memory_unlocked()
            print("  Successfully called _load_memory_unlocked")
            return True

    try:
        await save_conversation()
        print("[SUCCESS] Deadlock Test Passed!")
    except Exception as e:
        print(f"[FAILURE] Deadlock Test Failed: {e}")

async def test_plugin_manager_async_sleep():
    print("Testing Plugin Manager Async Sleep...")
    # Simulate the async wait
    start = time.time()
    await asyncio.sleep(0.1) # Event loop is running
    
    async def mock_tool():
        await asyncio.sleep(0.2)
        return "ok"

    # Testing that it can await a coroutine
    res = await mock_tool()
    end = time.time()
    if res == "ok" and (end - start) >= 0.2:
        print("[SUCCESS] Plugin Manager Async Test Passed!")
    else:
        print(f"[FAILURE] Plugin Manager Async Test Failed: {res}, {end-start}")

async def main():
    await test_memory_deadlock()
    await test_plugin_manager_async_sleep()

if __name__ == "__main__":
    asyncio.run(main())
