"""
# jarvis_vector_memory.py
Long-Term Semantic Memory for JARVIS using ChromaDB.
"""

import os
import uuid
import logging
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
import chromadb
from dotenv import load_dotenv
from services.utils.jarvis_logger import setup_logger

# Late-bound Phoenix import to avoid startup lag
_PX_CACHED = None


def get_px():
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

    def _ensure_initialized(self):
        """Initializes components only when needed."""
        if self.client is None:
            try:
                logger.info("Initializing Vector Memory (Lazy Loading)...")
                self.client = chromadb.PersistentClient(path=DB_PATH)
                self.embedding_func = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2"
                )
                self.collection = self.client.get_or_create_collection(
                    name=COLLECTION_NAME,
                    embedding_function=self.embedding_func
                )
            except (ImportError, ValueError, RuntimeError, OSError, AttributeError) as e:
                logger.error("Failed to initialize Vector Memory: %s", e)
                # Keep client/collection as None so we can try again
                self.client = None
                self.collection = None

    def add_memory(self, text, metadata=None):
        """
        Add a piece of text to the semantic memory.
        """
        if not text or not text.strip():
            return False

        _px = get_px()
        # pylint: disable=no-member, c-extension-no-member
        if _px and hasattr(_px, "active_span"):
            with _px.active_span("VectorMemory.add_memory") as span:
                span.set_attribute("memory.text", text[:100] + "...")
                return self._add_memory_internal(text, metadata, span)

        return self._add_memory_internal(text, metadata)

    def _add_memory_internal(self, text, metadata=None, span=None):
        self._ensure_initialized()
        if self.collection is None:
            logger.warning(
                "Vector Memory not initialized. Skipping add_memory.")
            return False

        memory_id = str(uuid.uuid4())
        try:
            self.collection.add(
                documents=[text],
                metadatas=[metadata or {}],
                ids=[memory_id]
            )
            if span:
                _px = get_px()
                if _px:
                    # pylint: disable=no-member, c-extension-no-member
                    span.set_status(_px.SpanStatus.OK)
            return True
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error adding to Vector Memory: %s", e)
            if span:
                span.record_exception(e)
            return False

    def query_memory(self, query_text, n_results=5):
        """
        Search for relevant memories with semantic similarity scores.
        Returns: List[Dict] containing 'document' and normalized 'score' (0-1).
        """
        if not query_text:
            return []

        _px = get_px()
        # pylint: disable=no-member, c-extension-no-member
        if _px and hasattr(_px, "active_span"):
            with _px.active_span("VectorMemory.query_memory") as span:
                span.set_attribute("query.text", query_text)
                return self._query_memory_internal(query_text, n_results, span)

        return self._query_memory_internal(query_text, n_results)

    def _query_memory_internal(self, query_text, n_results=5, span=None):
        self._ensure_initialized()
        if self.collection is None:
            logger.warning(
                "Vector Memory not initialized. Skipping query_memory.")
            return []
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results
            )

            docs = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]

            scored_results = []
            for doc, dist in zip(docs, distances):
                # Normalize distance to 0-1 confidence score
                # ChromaDB L2 distance: 0 is perfect, >1 is weak
                confidence = max(0, 1.0 - (dist / 1.5))
                scored_results.append({
                    "document": doc,
                    "score": round(confidence, 4)
                })

            if span:
                _px = get_px()
                if _px:
                    span.set_attribute("results.count", len(docs))
                    # pylint: disable=no-member, c-extension-no-member
                    span.set_status(_px.SpanStatus.OK)

            return scored_results
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error querying Vector Memory: %s", e)
            if span:
                span.record_exception(e)
            return []

    def clear_memory(self):
        """
        Wipes the entire memory collection. (USE WITH CAUTION)
        """
        self._ensure_initialized()
        try:
            self.client.delete_collection(name=COLLECTION_NAME)
            self.collection = self.client.create_collection(
                name=COLLECTION_NAME,
                embedding_function=self.embedding_func
            )
            logger.info("🧹 Collection '%s' cleared.", COLLECTION_NAME)
            return True
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error clearing Vector Memory: %s", e)
            return False

    def get_count(self):
        """
        Returns number of items in the collection.
        """
        self._ensure_initialized()
        try:
            return self.collection.count()
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error counting DB: %s", e)
            return 0


# Global Instance
jarvis_vector_db = VectorMemory()

if __name__ == "__main__":
    # Quick test
    db_test = VectorMemory()
    db_test.add_memory("Sir Matloob ka favourite color black hai.",
                       {"user": "Matloob"})
    print("Test Query:", db_test.query_memory(
        "Matloob ko kaunsa color pasand hai?"))
