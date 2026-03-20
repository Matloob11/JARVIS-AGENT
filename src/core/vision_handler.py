import asyncio
from typing import Optional, Any
from livekit.agents import llm
from services.utils.jarvis_logger import setup_logger
from services.system.jarvis_window_ctrl import get_active_window_context

logger = setup_logger("VISION-HANDLER")

class VisionHandler:
    """
    Manages background vision tasks and environment context injection.
    """

    def __init__(self, assistant_ref):
        self._assistant = assistant_ref
        self.last_vision_frame = None
        self.active_window_context = {}
        self._proactive_vision_task: Optional[asyncio.Task] = None

    def start_loop(self):
        """Starts the proactive awareness loop."""
        if not self._proactive_vision_task:
            self._proactive_vision_task = asyncio.create_task(self._proactive_vision_loop())

    async def stop_loop(self):
        """Cancels the proactive awareness loop."""
        if self._proactive_vision_task:
            self._proactive_vision_task.cancel()
            try:
                await self._proactive_vision_task
            except asyncio.CancelledError:
                pass
            self._proactive_vision_task = None
            logger.info("VisionHandler: Loop stopped.")

    async def _proactive_vision_loop(self):
        """Background loop for environment awareness."""
        logger.info("🔭 AuraView 2.0: Proactive awareness loop started.")
        while True:
            try:
                ctx = await get_active_window_context()
                if ctx.get("status") == "success":
                    self.active_window_context = ctx
            except (asyncio.CancelledError, RuntimeError) as e:
                logger.error("Vision loop error: %s", e)
            await asyncio.sleep(60)

    async def handle_vision_query(self, text: str, new_message: Any, turn_ctx: Any):
        """Detects vision-related keywords and injects image frame if available."""
        kw = ["vision", "dekh", "see", "view", "camera", "nazar", "peeche", "pic", "click", "tasveer", "environment", "surroundings"]
        is_vision = any(w in text.lower() for w in kw)

        if is_vision and self.last_vision_frame:
            logger.info("Vision query detected. Injecting frame.")
            b64_data = self._get_b64_frame()
            if not b64_data:
                return

            try:
                img_content = llm.ImageContent(image=b64_data, mime_type="image/jpeg")
                new_message.content = [text, img_content]
                turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                    role="system",
                    content=[f"[VISION SYSTEM ACTIVE] User is asking about the camera. Describe the frame provided."]
                ))
            except (ValueError, TypeError, RuntimeError) as e:
                logger.error("Failed to inject vision frame: %s", e)

    def _get_b64_frame(self) -> Optional[str]:
        """Extracts base64 data from last_vision_frame."""
        raw_data = self.last_vision_frame
        if not raw_data:
            return None
            
        if isinstance(raw_data, str):
            parts = raw_data.split(",")
            return parts[1] if len(parts) > 1 else raw_data
        return str(raw_data)

    async def analyze_current_frame(self, prompt: str) -> str:
        """Analyze the current frame using the jarvis_vision system."""
        from services.ai_core.jarvis_vision import vision_system
        from PIL import Image
        import base64
        from io import BytesIO

        b64_data = self._get_b64_frame()
        if not b64_data:
            return "Sir, right now I don't have a clear view from the camera. Please make sure it's turned on."

        try:
            image_bytes = base64.b64decode(b64_data)
            image = Image.open(BytesIO(image_bytes))
            result = await vision_system.analyze_via_google(prompt, image)
            return result
        except Exception as e:
            logger.error("direct analysis failed: %s", e)
            return f"Error analyzing surroundings: {str(e)}"
        
    async def inject_window_context(self, turn_ctx: Any):
        """Injects current window context into the chat messages."""
        if self.active_window_context and self.active_window_context.get("status") == "success":
            title = self.active_window_context.get("title")
            system_msg = f"[ENVIRONMENT]: User is focused on: '{title}'."
            turn_ctx.chat_ctx.messages.append(llm.ChatMessage(
                role="system", content=[system_msg]
            ))
