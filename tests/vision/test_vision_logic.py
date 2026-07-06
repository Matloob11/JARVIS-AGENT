import asyncio
from types import SimpleNamespace

from livekit.agents import llm

from src.core.vision_handler import VisionHandler


def test_vision_injection():
    asyncio.run(_run_vision_injection())


async def _run_vision_injection():
    handler = VisionHandler(assistant_ref=SimpleNamespace())
    turn_ctx = SimpleNamespace(chat_ctx=SimpleNamespace(messages=[]))

    sample_b64 = "data:image/jpeg;base64,ZmFrZV9qcGVn"
    handler.last_vision_frame = sample_b64

    test_message = llm.ChatMessage(
        role="user",
        content=["Jarvis, what do you see in the camera?"],
    )

    await handler.handle_vision_query(
        "Jarvis, what do you see in the camera?",
        test_message,
        turn_ctx,
    )

    assert isinstance(test_message.content, list)
    image_part = next(
        (part for part in test_message.content if isinstance(part, llm.ImageContent)),
        None,
    )
    assert image_part is not None
    assert image_part.mime_type == "image/jpeg"

    assert len(turn_ctx.chat_ctx.messages) == 1
    system_message = turn_ctx.chat_ctx.messages[0]
    assert system_message.role == "system"
    assert "[VISION SYSTEM ACTIVE]" in system_message.content[0]


def test_vision_injection_uses_assistant_frame_and_items_context():
    asyncio.run(_run_vision_injection_items_context())


async def _run_vision_injection_items_context():
    assistant = SimpleNamespace(last_vision_frame="data:image/jpeg;base64,ZmFrZV9qcGVn")
    handler = VisionHandler(assistant_ref=assistant)
    turn_ctx = SimpleNamespace(chat_ctx=SimpleNamespace(items=[]))
    test_message = llm.ChatMessage(
        role="user",
        content=["Jarvis, camera dekh kar batao"],
    )

    await handler.handle_vision_query("Jarvis, camera dekh kar batao", test_message, turn_ctx)

    assert any(isinstance(part, llm.ImageContent) for part in test_message.content)
    assert len(turn_ctx.chat_ctx.items) == 1
    assert "[VISION SYSTEM ACTIVE]" in turn_ctx.chat_ctx.items[0].content[0]

if __name__ == "__main__":
    test_vision_injection()
