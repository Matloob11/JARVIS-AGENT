"""
# jarvis_vector_memory.py
Long-Term Semantic Memory for JARVIS using ChromaDB.
"""

import asyncio
import logging
import os
import uuid
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

from services.utils.jarvis_logger import setup_logger

# Late-bound Phoenix import to avoid startup lag
_PX_CACHED = None


def get_px() -> Any:
    """Lazily load Arize Phoenix to improve startup performance."""
    global _PX_CACHED # pylint: disable=global-statement
    if _PX_CACHED is not None:
        return _PX_CACHED
    try:
        # pylint: disable=import-outside-toplevel
        import phoenix as px_mod
        _PX_CACHED = px_mod
        return _PX_CACHED
    except ImportError:
        _PX_CACHED = False  # Use False to avoid repeated import attempts
        return None
    except (AttributeError, RuntimeError) as e:
        logger.debug("Phoenix load skipped: %s", e)
        _PX_CACHED = False
        return None


load_dotenv()


# --- Aggressive Log Suppression ---
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Suppress noisy library logs
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("chromadb").setLevel(logging.ERROR)

# Setup logging
logger = setup_logger("JARVIS-VECTOR-MEMORY")


# --- Configuration ---
DB_PATH = os.path.join(os.getcwd(), "chroma_db")
COLLECTION_NAME = "jarvis_memory"


class VectorMemory:
    """
    Handles vector-based semantic memory using ChromaDB with lazy initialization.
    """

    def __init__(self):
        self.client = None
        self.embedding_func = None
        self.collection = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Initializes components only when needed with thread-safe locking."""
        if self.client is not None:
            return

        async with self._init_lock:
            # Double-check after acquiring lock
            if self.client is not None:
                return

            try:
                logger.info("Initializing Vector Memory (Lazy Loading)...")
                # PersistentClient is a blocking operation, so we run in thread to avoid blocking loop
                self.client = await asyncio.to_thread(chromadb.PersistentClient, path=DB_PATH)
                self.embedding_func = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2",
                )
                self.collection = await asyncio.to_thread(
                    self.client.get_or_create_collection,
                    name=COLLECTION_NAME,
                    embedding_function=self.embedding_func,
                )
            except Exception as e:
                logger.error("Failed to initialize Vector Memory: %s", e)
                # Keep client/collection as None so we can try again
                self.client = None
                self.collection = None

    async def add_memory(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        """Add a piece of text to the semantic memory asynchronously."""
        if not text or not text.strip():
            return False

        await self._ensure_initialized()
        if self.collection is None:
            logger.warning("Vector Memory not initialized. Skipping add_memory.")
            return False

        memory_id = str(uuid.uuid4())
        try:
            # collection.add is blocking, run in thread
            await asyncio.to_thread(
                self.collection.add,
                documents=[text],
                metadatas=[metadata or {}],
                ids=[memory_id],
            )
            return True
        except Exception as e:
            logger.error("Error adding to Vector Memory: %s", e)
            return False

    async def query_memory(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        Search for relevant memories with semantic similarity scores.
        Returns: List[Dict] containing 'document' and normalized 'score' (0-1).
        """
        if not query_text:
            return []

        await self._ensure_initialized()
        if self.collection is None:
            return []

        try:
            # collection.query is blocking, run in thread
            results = await asyncio.to_thread(
                self.collection.query,
                query_texts=[query_text],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

            extracted = []
            if results and results.get("documents") and results["documents"][0]:
                for i in range(len(results["documents"][0])):
                    doc = results["documents"][0][i]
                    # Convert distance to a similarity score (0.0 to 1.0)
                    dist = results["distances"][0][i] if "distances" in results else 1.0
                    # Normalizing distance (L2 distance in ChromaDB)
                    score = round(max(0, 1.0 - (dist / 1.5)), 4)

                    extracted.append({
                        "document": doc,
                        "metadata": results["metadatas"][0][i] if "metadatas" in results else {},
                        "score": score,
                    })
            return extracted
        except Exception as e:
            logger.error("Error querying Vector Memory: %s", e)
            return []

    async def clear_memory(self) -> bool:
        """Wipes the entire memory collection. (USE WITH CAUTION)"""
        await self._ensure_initialized()
        try:
            if self.client:
                await asyncio.to_thread(self.client.delete_collection, name=COLLECTION_NAME)
                self.collection = await asyncio.to_thread(
                    self.client.create_collection,
                    name=COLLECTION_NAME,
                    embedding_function=self.embedding_func,
                )
                logger.info("🧹 Collection '%s' cleared.", COLLECTION_NAME)
                return True
            return False
        except Exception as e:
            logger.error("Error clearing Vector Memory: %s", e)
            return False

    async def get_count(self) -> int:
        """Returns number of items in the collection."""
        await self._ensure_initialized()
        try:
            if self.collection:
                return int(await asyncio.to_thread(self.collection.count))
            return 0
        except Exception as e:
            logger.error("Error counting DB: %s", e)
            return 0


# Global Instance
jarvis_vector_db = VectorMemory()

if __name__ == "__main__":
    # Small test runner
    async def _test() -> None:
        db_test = VectorMemory()
        await db_test.add_memory("Sir Matloob ka favourite color black hai.", {"user": "Matloob"})
        results = await db_test.query_memory("Matloob ko kaunsa color pasand hai?")
        print("Test Query:", results)

    asyncio.run(_test())
