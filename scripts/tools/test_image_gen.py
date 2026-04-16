
import asyncio
import os
import sys

# Bootstrap
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from dotenv import load_dotenv
load_dotenv()

from services.multimedia.jarvis_image_gen import generate_image

async def test():
    print("Testing Image Generation...")
    prompt = "a majestic lion sitting on a throne, digital art, high quality"
    result = await generate_image(prompt)
    print(f"Result: {result}")
    if result.get("status") == "success":
        print(f"✅ SUCCESS: Image saved at {result.get('path')}")
        print(f"🔗 URL: {result.get('url')}")
    else:
        print(f"❌ FAILED: {result.get('message')}")

if __name__ == "__main__":
    asyncio.run(test())
