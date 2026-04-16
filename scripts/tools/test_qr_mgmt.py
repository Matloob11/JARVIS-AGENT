
import asyncio
import os
import sys

# Bootstrap
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.multimedia.jarvis_qr_gen import generate_qr_code
from services.multimedia.jarvis_file_opener import list_generated_media_tool, play_file, close_file_window_tool

async def test():
    filename = "jarvis_qr_test_history.png"
    print(f"=== Testing QR Code Management for {filename} ===")

    # 1. Create QR code
    print("\n1. Generating QR code...")
    res = await generate_qr_code("https://www.google.com", filename)
    print(f"Result: {res.get('message')}")
    await asyncio.sleep(2)
    # Most QR generators open the file automatically. Let's close it first to test manual opening.
    await close_file_window_tool(filename)
    
    # 2. List QR codes
    print("\n2. Listing QR code history...")
    list_res = await list_generated_media_tool(media_type="qr")
    print(f"Result: {list_res.get('message')}")
    
    # 3. Open specific QR from history
    print(f"\n3. Re-opening: {filename} from history...")
    open_res = await play_file(filename) # Fuzzy search in D:/ drive
    print(f"Result: {open_res.get('message')}")
    await asyncio.sleep(3)
    
    # 4. Close window
    print(f"\n4. Closing QR window: {filename}...")
    close_res = await close_file_window_tool(filename)
    print(f"Result: {close_res.get('message')}")

if __name__ == "__main__":
    asyncio.run(test())
