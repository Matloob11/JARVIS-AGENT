import asyncio
import base64
import contextlib
from io import BytesIO
from typing import Any

from livekit.agents import llm
from PIL import Image

from services.ai_core.jarvis_vision import vision_system
from services.system.jarvis_window_ctrl import get_active_window_context
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("VISION-HANDLER")

class VisionHandler:
    """
    Manages background vision tasks and environment context injection.
    """

    def __init__(self, assistant_ref: Any) -> None:
        self._assistant: Any = assistant_ref
        self.last_vision_frame: str | None = None
        self.active_window_context: dict[str, Any] = {}
        self._proactive_vision_task: asyncio.Task[None] | None = None

    def start_loop(self) -> None:
        """Starts the proactive awareness loop."""
        if not self._proactive_vision_task:
            self._proactive_vision_task = asyncio.create_task(self._proactive_vision_loop())

    async def stop_loop(self) -> None:
        """Cancels the proactive awareness loop."""
        if self._proactive_vision_task:
            self._proactive_vision_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._proactive_vision_task
            self._proactive_vision_task = None
            logger.info("VisionHandler: Loop stopped.")

    async def _proactive_vision_loop(self) -> None:
        """Background loop for environment awareness."""
        logger.info("🔭 AuraView 2.0: Proactive awareness loop started.")
        while True:
            try:
                ctx: dict[str, Any] = await get_active_window_context()
                if ctx.get("status") == "success":
                    self.active_window_context = ctx
            except (asyncio.CancelledError, RuntimeError) as e:
                logger.error("Vision loop error: %s", e)
            await asyncio.sleep(60)

    async def handle_vision_query(self, text: str, new_message: Any, turn_ctx: Any) -> None:
        """Detects vision-related keywords and injects image frame if available."""
        kw: list[str] = ["vision", "dekh", "see", "view", "camera", "nazar", "peeche", "pic", "click", "tasveer", "environment", "surroundings"]
        is_vision: bool = any(w in text.lower() for w in kw)

        if is_vision and self.last_vision_frame:
            logger.info("Vision query detected. Injecting frame.")
            b64_data: str | None = self._get_b64_frame()
            if not b64_data:
                return

            try:
                img_content: llm.ImageContent = llm.ImageContent(image=b64_data, mime_type="image/jpeg")
                new_message.content = [text, img_content]
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="system",
                    content=["[VISION SYSTEM ACTIVE] User is asking about the camera. Describe the frame provided."],
                ))
            except (ValueError, TypeError, RuntimeError) as e:
                logger.error("Failed to inject vision frame: %s", e)

    def _get_b64_frame(self) -> str | None:
        """Extracts base64 data from last_vision_frame."""
        raw_data: str | None = self.last_vision_frame
        if not raw_data:
            return None

        if isinstance(raw_data, str):
            parts: list[str] = raw_data.split(",")
            return parts[1] if len(parts) > 1 else raw_data
        return str(raw_data)

    async def analyze_current_frame(self, prompt: str) -> str:
        """Analyze the current frame using the jarvis_vision system."""
        b64_data: str | None = self._get_b64_frame()
        if not b64_data:
            return "Sir, right now I don't have a clear view from the camera. Please make sure it's turned on."

        try:
            image_bytes: bytes = base64.b64decode(b64_data)
            image: Image.Image = Image.open(BytesIO(image_bytes))
            result: str = await vision_system.analyze_via_google(prompt, image)
            return result
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("direct analysis failed: %s", e)
            return f"Error analyzing surroundings: {e!s}"

    async def inject_window_context(self, turn_ctx: Any) -> None:
        """Injects current window context into the chat messages."""
        if self.active_window_context and self.active_window_context.get("status") == "success":
            title: str = str(self.active_window_context.get("title", "Unknown"))
            system_msg: str = f"[ENVIRONMENT]: User is focused on: '{title}'."
            turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                role="system", content=[system_msg],
            ))
