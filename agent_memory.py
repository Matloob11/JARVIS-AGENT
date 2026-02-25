"""
# agent_memory.py
Memory extraction and processing for the JARVIS agent.
"""

import asyncio
from datetime import datetime
from typing import Optional
import os
from jarvis_logger import setup_logger
from memory_store import ConversationMemory

logger = setup_logger("JARVIS-MEMORY-EXTRACTOR")


class MemoryExtractor:
    """
    Handles extracting and saving conversation context to memory store.
    """

    def __init__(self, user_id: Optional[str] = None):
        """
        Initialize the memory extractor with a user ID.
        """
        effective_id = user_id or os.getenv("USER_NAME") or "User"
        self.user_id: str = effective_id
        self.memory = ConversationMemory(self.user_id)
        self.conversation_count = 0

    async def run(self, chat_ctx: list) -> None:
        """
        Process chat context to extract and save new messages to memory.
        """
        try:
            # Save current conversation context
            if chat_ctx and len(chat_ctx) > self.conversation_count:
                new_messages = chat_ctx[self.conversation_count:]

                for message in new_messages:
                    # Convert ChatMessage to dict if needed
                    raw_content = getattr(message, 'content', str(message))
                    if isinstance(raw_content, list):
                        msg_content = " ".join(
                            [getattr(p, 'text', str(p)) for p in raw_content])
                    else:
                        msg_content = str(raw_content)
                    role = message.role if hasattr(
                        message, 'role') else "unknown"

                    conversation_data = {
                        "messages": [{"role": role, "content": msg_content}],
                        "timestamp": datetime.now().isoformat(),
                        "user_id": self.user_id
                    }

                    # Save to memory with shielding to prevent corruption during shutdown
                    await asyncio.shield(self.memory.save_conversation(conversation_data))
                    logger.info("💾 Memory saved: %s message", role)

                self.conversation_count = len(chat_ctx)

        except (asyncio.CancelledError, RuntimeError) as e:
            logger.error("Memory extractor error: %s", e)
        except (IOError, ValueError, AttributeError) as e:
            logger.exception("❌ Memory extraction error: %s", e)

    def clear_context(self) -> None:
        """Resets the conversation count."""
        self.conversation_count = 0
