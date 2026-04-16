
import asyncio
import os
import sys
import time

# Bootstrap
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.multimedia.jarvis_file_opener import list_generated_media_tool, play_file, close_file_window_tool

async def test():
    print("=== Testing Media Management ===")
    
    # 1. Listing media
    print("\n1. Listing Generated Media...")
    history = await list_generated_media_tool(media_type="all")
    print(f"Message: {history.get('message')}")
    
    media_count = history.get("count", 0)
    if media_count > 0:
        first_item = history.get("media")[0]
        filename = first_item["name"]
        print(f"✅ Found {media_count} items. Most recent: {filename}")
        
        # 2. Opening the most recent one
        print(f"\n2. Opening: {filename}...")
        open_res = await play_file(first_item["path"])
        print(f"Result: {open_res.get('message')}")
        
        # 3. Closing it
        print(f"\n3. Waiting 3 seconds, then closing: {filename}...")
        await asyncio.sleep(3)
        close_res = await close_file_window_tool(filename)
        print(f"Result: {close_res.get('message')}")
        
    else:
        print("❌ No media found to test opening/closing.")

if __name__ == "__main__":
    asyncio.run(test())
