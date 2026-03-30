"""
# agent_memory.py
Memory extraction and processing for the JARVIS agent.
"""

import asyncio
import os
from datetime import datetime
from typing import Any

from services.ai_core.memory_store import ConversationMemory
from services.utils.jarvis_bridge import notify_memory
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MEMORY-EXTRACTOR")

_background_tasks: set[asyncio.Task[Any]] = set()


class MemoryExtractor:
    """
    Handles extracting and saving conversation context to memory store.
    """
    user_id: str
    memory: ConversationMemory
    conversation_count: int
    _audited: bool

    def __init__(self, user_id: str | None = None):
        """
        Initialize the memory extractor with a user ID and perform integrity audit.
        """
        effective_id = user_id or os.getenv("USER_NAME") or "User"
        self.user_id: str = effective_id
        self.memory = ConversationMemory(self.user_id)
        self.conversation_count = 0
        self._audited = False

    async def _audit_memory(self) -> None:
        """Audits memory structure and logs stale context count."""
        try:
            is_valid: bool = await self.memory.validate_integrity()
            if not is_valid:
                logger.warning(
                    "⚠️ Memory Integrity Compromised for user: %s", self.user_id)

            stale_count: int = await self.memory.archive_stale_context()
            if stale_count > 0:
                logger.info(
                    "🧠 Memory Optimizer: %d stale contexts identified.", stale_count)
        except (OSError, ValueError, RuntimeError, AttributeError) as e:
            logger.debug("Memory audit failed: %s", e)

    async def run(self, chat_ctx: list[Any]) -> None:
        """
        Process chat context to extract and save new messages to memory.
        """
        if not self._audited:
            await self._audit_memory()
            self._audited = True

        try:
            # Save current conversation context
            if chat_ctx and len(chat_ctx) > self.conversation_count:
                new_messages = chat_ctx[self.conversation_count:]

                for message in new_messages:
                    # Convert ChatMessage to dict if needed
                    raw_content: Any = getattr(message, 'content', str(message))
                    role: str = getattr(message, 'role', 'unknown')

                    if isinstance(raw_content, list):
                        msg_content: str = " ".join(
                            [getattr(p, 'text', str(p)) for p in raw_content])
                    else:
                        msg_content = str(raw_content)

                    from services.utils.jarvis_security import vortex_guard

                    # SELF-DEFENSE: Sanitize content before it enters Long-Term Memory
                    safe_content = vortex_guard.sanitize_input(msg_content)

                    conversation_data: dict[str, Any] = {
                        "messages": [{"role": role, "content": safe_content}],
                        "timestamp": datetime.now().isoformat(),
                        "user_id": self.user_id,
                    }

                    # Save with shielding to prevent corruption during shutdown
                    success: bool = await asyncio.shield(
                        self.memory.save_conversation(conversation_data))
                    if success:
                        logger.info(
                            "✅ Memory Extracted and Saved for role: %s", role)
                        # Notify UI about new memory (pass the string content)
                        task = asyncio.create_task(notify_memory(msg_content))
                        _background_tasks.add(task)
                        task.add_done_callback(_background_tasks.discard)
                    else:
                        logger.error("❌ Memory save failed for role: %s", role)

                self.conversation_count = len(chat_ctx)

        except (asyncio.CancelledError, RuntimeError) as e:
            logger.error("Memory extractor error: %s", e)
        except (OSError, ValueError, AttributeError) as e:
            logger.exception("❌ Memory extraction error: %s", e)

    def clear_context(self) -> None:
        """Resets the conversation count."""
        self.conversation_count = 0
