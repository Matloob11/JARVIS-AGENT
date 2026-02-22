"""
Jarvis Memory Store Module
Persistent storage for conversation history and user context.
"""

import json
import os
import asyncio
from datetime import datetime
from typing import List, Dict, Union
from jarvis_vector_memory import jarvis_vector_db
from jarvis_logger import setup_logger

# Configure logging
logger = setup_logger("JARVIS-MEMORY-STORE")


class ConversationMemory:
    """Handles persistent conversation memory for users"""

    def __init__(self, user_id: str, storage_path: str = "conversations"):
        self.user_id = user_id
        self.storage_path = storage_path
        self.memory_file = os.path.join(storage_path, f"{user_id}_memory.json")
        self.lock = asyncio.Lock()

        # Create storage directory if it doesn't exist
        os.makedirs(storage_path, exist_ok=True)
        logger.info("ConversationMemory initialized for user: %s", user_id)
        logger.info("Memory file path: %s", os.path.abspath(self.memory_file))

    async def load_memory(self) -> List[Dict]:
        """Load all past conversations with corruption detection."""
        if await asyncio.to_thread(os.path.exists, self.memory_file):
            try:
                def _read_json():
                    with open(self.memory_file, 'r', encoding="utf-8") as f:
                        return json.load(f)

                return await asyncio.to_thread(_read_json)
            except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
                logger.exception(
                    "Omega Corruption in User Memory %s: %s", self.user_id, e)
                # Backup corrupted file
                if await asyncio.to_thread(os.path.exists, self.memory_file):
                    timestamp = int(datetime.now().timestamp())
                    await asyncio.to_thread(
                        os.rename, self.memory_file, f"{self.memory_file}.corrupted_{timestamp}"
                    )
                return []
        return []

    def _conversation_exists(
            self, new_conversation: Dict, existing_conversations: List[Dict]) -> bool:
        """Check if a conversation already exists in memory"""
        # Normalize new conversation data
        if hasattr(new_conversation, 'model_dump'):
            new_conv_data = new_conversation.model_dump()
        elif hasattr(new_conversation, 'to_dict'):
            new_conv_data = new_conversation.to_dict()
        else:
            new_conv_data = new_conversation

        new_timestamp = new_conv_data.get('timestamp')
        new_messages = new_conv_data.get('messages', [])

        for existing_conv in existing_conversations:
            existing_timestamp = existing_conv.get('timestamp')
            existing_messages = existing_conv.get('messages', [])

            # Compare by timestamp and message count
            if (existing_timestamp == new_timestamp and
                    len(existing_messages) == len(new_messages)):
                return True

        return False

    async def save_conversation(self, conversation: Union[Dict, object]) -> bool:
        """Atomic save - returns True if successful"""
        async with self.lock:
            try:
                memory = await self.load_memory()
                if hasattr(conversation, 'model_dump'):
                    conversation_dict = conversation.model_dump()
                else:
                    conversation_dict = conversation

                if 'timestamp' not in conversation_dict:
                    conversation_dict['timestamp'] = datetime.now().isoformat()

                if self._conversation_exists(conversation_dict, memory):
                    return True

                if memory and self._is_conversation_update(conversation_dict, memory[-1]):
                    memory[-1] = conversation_dict
                else:
                    memory.append(conversation_dict)

                def json_default(obj):
                    if hasattr(obj, 'model_dump'):
                        return obj.model_dump()
                    if hasattr(obj, 'to_dict'):
                        return obj.to_dict()
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    return str(obj)

                # Save to disk (Atomic & Async)
                def _write_json():
                    temp_file = f"{self.memory_file}.tmp"
                    with open(temp_file, 'w', encoding='utf-8') as f:
                        json.dump(memory, f, indent=2,
                                  ensure_ascii=False, default=json_default)
                    os.replace(temp_file, self.memory_file)

                await asyncio.to_thread(_write_json)

                # Vector DB Sync (Background task to avoid blocking)
                asyncio.create_task(self._sync_to_vector_db(conversation_dict))
                return True
            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.error("Error saving memory: %s", e)
                return False

    async def _sync_to_vector_db(self, conversation_dict: Dict):
        """Sync messages to Vector DB in background thread."""
        try:
            if 'messages' in conversation_dict and conversation_dict['messages']:
                for msg in conversation_dict['messages']:
                    content = msg.get('content', '')
                    role = msg.get('role', 'user')
                    if content and len(content) > 5:  # Skip very short filler words
                        # Run blocking vector DB call in thread
                        await asyncio.to_thread(
                            jarvis_vector_db.add_memory,
                            text=content,
                            metadata={
                                "user_id": self.user_id,
                                "role": role,
                                "timestamp": conversation_dict.get('timestamp')
                            }
                        )
        except (ValueError, KeyError, AttributeError, OSError) as e:
            logger.error("Vector DB sync failed: %s", e)

    def _is_conversation_update(self, new_conv: Dict, last_conv: Dict) -> bool:
        """Check if new conversation is an update to the last one"""
        # Simple heuristic: if timestamps are close and new conversation has more messages
        try:
            new_timestamp = datetime.fromisoformat(
                new_conv.get('timestamp', ''))
            last_timestamp = datetime.fromisoformat(
                last_conv.get('timestamp', ''))

            time_diff = abs((new_timestamp - last_timestamp).total_seconds())
            new_msg_count = len(new_conv.get('messages', []))
            last_msg_count = len(last_conv.get('messages', []))

            # If within 5 minutes and has more messages, consider it an update
            return time_diff < 300 and new_msg_count > last_msg_count

        except (ValueError, TypeError, KeyError):
            return False

    async def get_recent_context(self, max_messages: int = 30) -> List[Dict]:
        """Get recent conversation context for the agent"""
        memory = await self.load_memory()
        all_messages = []

        # Flatten all conversations into a single message list
        for conversation in memory:
            if "messages" in conversation:
                all_messages.extend(conversation["messages"])

        # Return the most recent messages
        recent_messages = all_messages[-max_messages:] if all_messages else []
        logger.info(
            "Retrieved %d recent messages for user %s", len(recent_messages), self.user_id)
        return recent_messages

    async def get_conversation_count(self) -> int:
        """Get total number of saved conversations"""
        memory = await self.load_memory()
        return len(memory)

    async def clear_duplicates(self) -> int:
        """Remove duplicate conversations and return count of removed duplicates"""
        memory = await self.load_memory()
        unique_conversations = []
        removed_count = 0

        for conv in memory:
            if not self._conversation_exists(conv, unique_conversations):
                unique_conversations.append(conv)
            else:
                removed_count += 1

        if removed_count > 0:
            def _write_unique():
                with open(self.memory_file, 'w', encoding='utf-8') as f:
                    json.dump(unique_conversations, f,
                              indent=2, ensure_ascii=False)

            await asyncio.to_thread(_write_unique)
            logger.info("Removed %d duplicate conversations", removed_count)

        return removed_count

    async def get_semantic_context(self, query: str, n_results: int = 3) -> List[str]:
        """Search Long-Term Memory for semantically relevant information"""
        logger.info("Semantic search initiated for query: %s", query)
        # ChromaDB query is blocking, run in thread
        return await asyncio.to_thread(jarvis_vector_db.query_memory, query, n_results)
