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
from jarvis_logger import setup_logger

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
    Handles vector-based semantic memory using ChromaDB.
    """

    def __init__(self):
        # Initialize the ChromaDB client with persistent storage
        self.client = chromadb.PersistentClient(path=DB_PATH)

        # Use a local embedding function (Free and local)
        self.embedding_func = SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )

        # Get or create the collection
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self.embedding_func
        )

    def add_memory(self, text, metadata=None):
        """
        Add a piece of text to the semantic memory.
        """
        if not text or not text.strip():
            return False

        memory_id = str(uuid.uuid4())

        try:
            self.collection.add(
                documents=[text],
                metadatas=[metadata or {}],
                ids=[memory_id]
            )
            return True
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error adding to Vector Memory: %s", e)
            return False

    def query_memory(self, query_text, n_results=5):
        """
        Search for relevant memories based on semantic similarity.
        """
        if not query_text:
            return []

        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results
            )

            # Extract documents from the result dictionary
            return results.get("documents", [[]])[0]
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error querying Vector Memory: %s", e)
            return []

    def clear_memory(self):
        """
        Wipes the entire memory collection. (USE WITH CAUTION)
        """
        try:
            self.client.delete_collection(name=COLLECTION_NAME)
            self.collection = self.client.create_collection(
                name=COLLECTION_NAME,
                embedding_function=self.embedding_func
            )
            print(f"🧹 Collection '{COLLECTION_NAME}' cleared.")
            return True
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error clearing Vector Memory: %s", e)
            return False

    def get_count(self):
        """
        Returns number of items in the collection.
        """
        try:
            return self.collection.count()
        except (ValueError, KeyError, RuntimeError, OSError) as e:
            logger.error("Error adding to DB: %s", e)
            return 0


# Global Instance
jarvis_vector_db = VectorMemory()

if __name__ == "__main__":
    # Quick test
    db = VectorMemory()
    db.add_memory("Sir Matloob ka favourite color black hai.",
                  {"user": "Matloob"})
    print("Test Query:", db.query_memory(
        "Matloob ko kaunsa color pasand hai?"))
