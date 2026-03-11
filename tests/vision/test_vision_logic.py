import asyncio
import base64
from unittest.mock import MagicMock, AsyncMock
from agent_core import BrainAssistant
from livekit.agents import llm


async def test_vision_injection():
    print("[TEST] Running Vision Injection Logic Test...")

    # Mocking necessary components
    chat_ctx = AsyncMock()
    assistant = BrainAssistant(chat_ctx=chat_ctx)

    # 1. Set a dummy vision frame
    sample_b64 = "data:image/jpeg;base64,dummy_data"
    assistant.last_vision_frame = sample_b64
    print("   - Last vision frame set.")

    # 2. Simulate a vision query message
    test_message = llm.ChatMessage(
        role="user",
        content=["Jarvis, what do you see in the camera?"]
    )

    # 3. Call the turn completed handler
    print("   - Processing 'what do you see in the camera?'...")
    await assistant.on_user_turn_completed(chat_ctx, test_message)

    # 4. Verify injection
    if isinstance(test_message.content, list):
        print("[SUCCESS] Message content converted to list for multimodal data.")
        image_part = next(
            (p for p in test_message.content if isinstance(p, llm.ImageContent)), None)
        if image_part:
            print(
                f"[SUCCESS] ImageContent found in message. Mime Type: {image_part.mime_type}")
        else:
            print("[FAIL] ImageContent not found in message parts.")
    else:
        print("[FAIL] Message content remains a string.")

    # 5. Verify system hint
    if chat_ctx.add_message.called:
        args, kwargs = chat_ctx.add_message.call_args
        if "Analysing visual frame" in kwargs.get("content", ""):
            print("[SUCCESS] System instruction injected to guide vision analysis.")
    else:
        print("[FAIL] System instruction not injected.")

if __name__ == "__main__":
    asyncio.run(test_vision_injection())
