import asyncio
import os
from jarvis_notepad_automation import create_template_code, write_custom_code

async def test_notepad_writing():
    print("🚀 Testing HTML Login Template Writing...")
    # This will open Notepad, type the code, save, wait 2 seconds, close, and then open browser
    result = await create_template_code("html_login", filename="test_visual_logic.html", auto_run=True)
    print(f"Result: {result}")
    
    await asyncio.sleep(5) # Wait for the user to see the browser before next test
    
    print("\n🚀 Testing Custom Python Code Writing...")
    custom_python = """# Visual Typing Test
import time

def say_hello():
    print("Hello from visually typed script!")
    time.sleep(1)

if __name__ == "__main__":
    say_hello()
"""
    # This will do the same for a custom python file
    result = await write_custom_code(custom_python, filename="test_visual_python.py", auto_run=True)
    print(f"Result: {result}")

if __name__ == "__main__":
    try:
        asyncio.run(test_notepad_writing())
    except KeyboardInterrupt:
        pass
    finally:
        print("\nTest completed. Verify that you saw the typing in Notepad.")
        input("Press Enter to exit...")
