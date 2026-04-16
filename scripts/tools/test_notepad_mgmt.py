
import asyncio
import os
import sys

# Bootstrap
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.automation.jarvis_notepad_automation import (
    write_custom_code, 
    list_notepad_files_tool, 
    edit_notepad_file_tool, 
    append_notepad_file_tool, 
    close_notepad_tool
)

async def test():
    filename = "jarvis_mgmt_test.txt"
    print(f"=== Testing Notepad Management for {filename} ===")

    # 1. Create initial file
    print("\n1. Creating initial file...")
    await write_custom_code("Initial content for JARVIS test.", filename, auto_run=False)
    await asyncio.sleep(2)
    # Close it first to test re-opening
    await close_notepad_tool()
    
    # 2. List files
    print("\n2. Listing files...")
    files_res = await list_notepad_files_tool()
    print(f"Result: {files_res.get('message')}")
    
    # 3. Edit (Replace)
    print("\n3. Editing (Replacing) content...")
    edit_res = await edit_notepad_file_tool(filename, "This is the NEW replaced content. All old text should be gone.")
    print(f"Result: {edit_res.get('message')}")
    await asyncio.sleep(2)
    
    # 4. Append
    print("\n4. Appending content...")
    append_res = await append_notepad_file_tool(filename, "And here is an appended line at the bottom.")
    print(f"Result: {append_res.get('message')}")
    await asyncio.sleep(2)
    
    # 5. Cleanup
    print("\n5. Closing Notepad...")
    close_res = await close_notepad_tool()
    print(f"Result: {close_res.get('message')}")

if __name__ == "__main__":
    asyncio.run(test())
