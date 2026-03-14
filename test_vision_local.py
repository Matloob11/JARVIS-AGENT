import asyncio
from services.ai_core.jarvis_vision import analyze_screen, analyze_camera

async def main():
    print("Testing Screen Analysis...")
    try:
        res = await analyze_screen("What do you see on my screen?")
        print("Screen Result:", res)
    except Exception as e:
        print("Screen Exception:", e)

    print("\nTesting Camera Analysis...")
    try:
        res = await analyze_camera("What do you see in the camera?")
        print("Camera Result:", res)
    except Exception as e:
        print("Camera Exception:", e)

if __name__ == "__main__":
    asyncio.run(main())
