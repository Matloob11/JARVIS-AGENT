"""
Jarvis Memory Store Module
Persistent storage for conversation history and user context.
"""

import asyncio
import json
import os
from collections import OrderedDict
from datetime import datetime
from typing import Any

try:
    from cryptography.fernet import InvalidToken
except ImportError:
    class _FallbackInvalidToken(Exception):
        """Fallback exception if cryptography is not installed."""
    InvalidToken = _FallbackInvalidToken  # type: ignore

from services.ai_core.jarvis_vector_memory import jarvis_vector_db
from services.utils.jarvis_config import config
from services.utils.jarvis_crypto import jarvis_crypto
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MEMORY-STORE")

_background_tasks: set[asyncio.Task[Any]] = set()


class ConversationMemory:
    """Handles persistent conversation memory for users with LRU caching."""

    def __init__(self, user_id: str, storage_path: str | None = None):
        self.user_id = user_id
        self.storage_path = storage_path or config.conversations_dir
        self.memory_file = os.path.join(self.storage_path, f"{user_id}_memory.json")
        self.lock = asyncio.Lock()

        # Phase 5: Fast Retrieval LRU Cache (Threshold: 50 messages)
        self.cache: OrderedDict[str, Any] = OrderedDict()
        self._mem_cache_size = 50

        # Note: Directory is now ensured by jarvis_config singleton
        logger.info(
            "ConversationMemory initialized for user: %s (Cache: Enabled)", user_id)
        logger.info("Memory file path: %s", os.path.abspath(self.memory_file))

    async def load_memory(self) -> list[dict[str, Any]]:
        """Load conversation history from encrypted disk storage."""
        async with self.lock:
            return await self._load_memory_unlocked()

    async def _load_memory_unlocked(self) -> list[dict[str, Any]]:
        """Internal load without lock acquisition to safely call from other locked methods."""
        if not await asyncio.to_thread(os.path.exists, self.memory_file):
            return []

        try:
            def _read_file():
                with open(self.memory_file, 'rb') as f:
                    data = f.read()

                # Phase 6: Check if data is encrypted or legacy JSON
                try:
                    # Try decrypting
                    if isinstance(data, (bytes, str)) and data:
                        decrypted = jarvis_crypto.decrypt(data)
                        return json.loads(decrypted)
                    return []
                except (ValueError, RuntimeError, InvalidToken, TypeError):
                    # Fallback for legacy migration or key mismatch
                    logger.warning(
                        "Decryption failed or invalid token. Attempting legacy JSON load or resetting.")

                    try:
                        # Re-open in text mode for legacy JSON
                        with open(self.memory_file, encoding='utf-8') as f:
                            return json.load(f)
                    except Exception:
                        # If it's truly undecryptable/corrupt, the outer exception handler will back it up
                        raise InvalidToken("Undecryptable memory") from None

            memory = await asyncio.to_thread(_read_file)
            if not isinstance(memory, list):
                logger.error(
                    "Loaded memory is not a list for user %s. Resetting.", self.user_id)
                return []
            return memory
        except (json.JSONDecodeError, OSError, Exception) as e:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Omega Corruption in User Memory %s: %s", self.user_id, e)
            # Backup corrupted file
            if await asyncio.to_thread(os.path.exists, self.memory_file):
                timestamp = int(datetime.now().timestamp())
                await asyncio.to_thread(
                    os.rename, self.memory_file, f"{self.memory_file}.corrupted_{timestamp}",
                )
            return []

    async def clear(self) -> None:
        """Deletes all conversation history for this user."""
        async with self.lock:
            self.cache.clear()
            if await asyncio.to_thread(os.path.exists, self.memory_file):
                await asyncio.to_thread(os.unlink, self.memory_file)
            logger.info("🧠 Memory CLEARED for user: %s", self.user_id)

    def _conversation_exists(
            self, new_conversation: dict[str, Any], existing_conversations: list[dict[str, Any]]) -> bool:
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
        new_fingerprint = self._conversation_fingerprint(new_conv_data)

        for existing_conv in existing_conversations:
            existing_timestamp = existing_conv.get('timestamp')
            existing_messages = existing_conv.get('messages', [])

            if self._conversation_fingerprint(existing_conv) == new_fingerprint:
                return True

            # Compare timestamped message bundles without treating unrelated flat
            # records as duplicates just because they have no messages field.
            if (existing_timestamp == new_timestamp and new_messages and existing_messages and
                    len(existing_messages) == len(new_messages)):
                return True

        return False

    def _conversation_fingerprint(self, conversation: dict[str, Any]) -> str:
        """Create a stable duplicate key for both session bundles and flat records."""
        return json.dumps(conversation, sort_keys=True, default=str, ensure_ascii=False)

    async def save_conversation(self, conversation: dict | object) -> bool:
        """Atomic save - returns True if successful. Updates LRU cache."""
        async with self.lock:
            try:
                memory = await self._load_memory_unlocked()
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

                # Phase 5: Update Cache Proactively
                if "messages" in conversation_dict:
                    for msg in conversation_dict["messages"]:
                        msg_id = msg.get("id") or str(len(self.cache))
                        self.cache[msg_id] = msg
                        self.cache.move_to_end(msg_id)
                        if len(self.cache) > self._mem_cache_size:
                            self.cache.popitem(last=False)

                def json_default(obj):
                    if hasattr(obj, 'model_dump'):
                        return obj.model_dump()
                    if hasattr(obj, 'to_dict'):
                        return obj.to_dict()
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    return str(obj)

                # Phase 6: Encrypt and Sign
                def _write_encrypted():
                    temp_file = f"{self.memory_file}.tmp"

                    # Convert to JSON string first
                    json_data = json.dumps(memory, indent=2,
                                           ensure_ascii=False, default=json_default)

                    # Encrypt
                    encrypted_data = jarvis_crypto.encrypt(json_data)

                    with open(temp_file, 'wb') as f:
                        f.write(encrypted_data)

                    os.replace(temp_file, self.memory_file)

                await asyncio.to_thread(_write_encrypted)

                # Vector DB Sync (Background task to avoid blocking)
                t = asyncio.create_task(self._sync_to_vector_db(conversation_dict))
                _background_tasks.add(t)
                t.add_done_callback(_background_tasks.discard)
                return True
            except (AttributeError, TypeError, ValueError, KeyError, OSError) as e:
                logger.error("Error saving memory: %s", e)
                return False

    async def _sync_to_vector_db(self, conversation_dict: dict[str, Any]) -> None:
        """Sync messages to Vector DB in background coroutine."""
        try:
            if conversation_dict.get('messages'):
                for msg in conversation_dict['messages']:
                    content = msg.get('content', '')
                    role = msg.get('role', 'user')
                    if content and len(content) > 5:  # Skip short filler
                        # Call async vector DB directly
                        await jarvis_vector_db.add_memory(
                            text=content,
                            metadata={
                                "user_id": self.user_id,
                                "role": role,
                                "timestamp": conversation_dict.get('timestamp'),
                            },
                        )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Vector DB sync failed: %s", e)

    async def save_to_disk(self) -> None:
        """No-op flush (ConversationMemory saves everything immediately)."""
        logger.debug("Memory flush requested / already synced.")
        await asyncio.sleep(0.01)

    def _is_conversation_update(self, new_conv: dict, last_conv: dict) -> bool:
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

    async def get_recent_context(self, max_messages: int = 30) -> list[dict]:
        """Get recent conversation context using LRU cache for high-speed retrieval."""
        async with self.lock:
            # Phase 5: Optimization - Return from Cache if populated
            if len(self.cache) >= max_messages:
                logger.info("⚡ LRU Cache Hit: Returning %d messages", max_messages)
                items = list(self.cache.values())
                return items[-max_messages:]

            logger.info("💾 Cache Miss: Loading from disk...")
            # Use unlocked version to avoid deadlock
            memory = await self._load_memory_unlocked()
            all_messages = []

            # Flatten all conversations into a single message list
            for conversation in memory:
                if "messages" in conversation:
                    all_messages.extend(conversation["messages"])

            # Re-populate cache from disk
            for msg in all_messages[-self._mem_cache_size:]:
                msg_id = msg.get("id") or str(len(self.cache))
                self.cache[msg_id] = msg
                self.cache.move_to_end(msg_id)

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
        """Remove duplicate conversations atomically under the lock."""
        memory = await self.load_memory()
        unique_conversations = []
        removed_count = 0

        for conv in memory:
            if not self._conversation_exists(conv, unique_conversations):
                unique_conversations.append(conv)
            else:
                removed_count += 1

        if removed_count > 0:
            async with self.lock:
                def _write_unique():
                    temp_file = f"{self.memory_file}.tmp"
                    data = json.dumps(unique_conversations, indent=2, ensure_ascii=False)
                    encrypted = jarvis_crypto.encrypt(data)
                    with open(temp_file, 'wb') as f:
                        f.write(encrypted)
                    os.replace(temp_file, self.memory_file)

                await asyncio.to_thread(_write_unique)
                logger.info("Removed %d duplicate conversations", removed_count)

        return removed_count

    async def validate_integrity(self) -> bool:
        """Deep structural validation of the memory file."""
        try:
            memory = await self.load_memory()
            if not isinstance(memory, list):
                return False
            for conv in memory:
                if not isinstance(conv, dict) or "messages" not in conv:
                    return False
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    async def archive_stale_context(self, threshold_hours: int = 24) -> int:
        """
        Identify and 'archive' stale contexts by returning the count of old sessions.
        This helps the agent decide whether to start a fresh cognitive window.
        """
        memory = await self.load_memory()
        if not memory:
            return 0

        stale_count = 0
        now = datetime.now()
        for conv in memory:
            try:
                conv_time = datetime.fromisoformat(conv.get("timestamp", ""))
                if (now - conv_time).total_seconds() > (threshold_hours * 3600):
                    stale_count += 1
            except (ValueError, TypeError):
                continue

        logger.info("Memory Audit: Found %d stale conversation blocks (>%dh)",
                    stale_count, threshold_hours)
        return stale_count

    async def get_semantic_context(self, query: str, n_results: int = 3) -> list[dict[str, Any]]:
        """Search Long-Term Memory for semantically relevant information with scores."""
        logger.info("Semantic search initiated for query: %s", query)
        # Vector DB query is now async
        return await jarvis_vector_db.query_memory(query, n_results)
