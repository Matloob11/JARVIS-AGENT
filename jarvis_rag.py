"""
# jarvis_rag.py
Jarvis Local Documents RAG Module
Handles searching, reading, and analyzing local PDF and Word documents.
"""

import os
import asyncio
from typing import Optional
from fuzzywuzzy import process
from pypdf import PdfReader
from docx import Document
from livekit.agents import function_tool
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-RAG")


# --- Global Index Cache ---
global_doc_index: list[str] = []
LAST_INDEX_TIME: float = 0.0
INDEX_CACHE_TIMEOUT = 300  # 5 minutes


class DocumentRAG:
    """
    Handles RAG operations over local files.
    """

    def __init__(self, search_dirs=None):
        if search_dirs is None:
            search_dirs = ["D:/"]
        self.search_dirs = search_dirs

    async def find_document(self, query: str) -> Optional[str]:
        """
        Fuzzy searches for a document in the search directories.
        Uses a global cache to avoid repetitive slow indexing.
        """
        global global_doc_index, LAST_INDEX_TIME  # pylint: disable=global-statement

        current_time = asyncio.get_event_loop().time()

        # Check cache
        if global_doc_index and (current_time - LAST_INDEX_TIME < INDEX_CACHE_TIMEOUT):
            logger.info("⚡ Using cached document index.")
            file_list = global_doc_index
        else:
            def walk_docs():
                results = []
                for base_dir in self.search_dirs:
                    if not os.path.exists(base_dir):
                        continue
                    for root, _, files in os.walk(base_dir):
                        for f in files:
                            if f.lower().endswith(('.pdf', '.docx')):
                                results.append(os.path.join(root, f))
                return results

            logger.info("📂 Indexing documents in %s...", self.search_dirs)
            file_list = await asyncio.to_thread(walk_docs)
            global_doc_index = file_list
            # Using loop time for consistency in async
            LAST_INDEX_TIME = current_time

        if not file_list:
            return None

        def fuzzy_match():
            choices = {os.path.basename(f): f for f in file_list}
            match_result = process.extractOne(query, list(choices.keys()))
            if match_result:
                best_match, score = match_result
                return best_match, score, choices[best_match]
            return None, 0, None

        logger.info("🔍 Fuzzy searching for: '%s'...", query)
        best_match, score, match_path = await asyncio.to_thread(fuzzy_match)

        if score > 60:
            logger.info("✅ Matched to '%s' (Score: %d)", best_match, score)
            return match_path
        return None

    def read_pdf(self, file_path: str) -> str:
        """Reads text from a PDF file."""
        text = ""
        try:
            reader = PdfReader(file_path)
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error reading PDF %s: %s", file_path, e)
            return f"Error reading PDF: {e}"

    def read_docx(self, file_path: str) -> str:
        """Reads text from a Word document."""
        try:
            doc = Document(file_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error reading DOCX %s: %s", file_path, e)
            return f"Error reading DOCX: {e}"

    async def get_document_content(self, file_path: str) -> str:
        """
        Dispatches the read task based on file extension.
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await asyncio.to_thread(self.read_pdf, file_path)
        if ext == ".docx":
            return await asyncio.to_thread(self.read_docx, file_path)
        return "Unsupported file format."


# Global Instance
rag_system = DocumentRAG()


@function_tool
async def ask_about_document(doc_name: str, question: str = "Summarize this document.") -> dict:
    """
    Searches for a local document (PDF or Word) on your D: drive and answers questions about it.
    """
    logger.info("RAG Tool: Searching for '%s' to answer: '%s'",
                doc_name, question)

    file_path = await rag_system.find_document(doc_name)
    if not file_path:
        return {
            "status": "not_found",
            "message": f"❌ Maaf kijiye, mujhe '{doc_name}' naam ka koi PDF ya Word document nahi mila."
        }

    logger.info("Found document at: %s. Extracting text...", file_path)
    content = await rag_system.get_document_content(file_path)

    if len(content) > 15000:
        # Simple truncation for context window limits if document is massive
        content = content[:15000] + "... [Content truncated]"

    return {
        "status": "success",
        "document_name": os.path.basename(file_path),
        "document_path": file_path,
        "content": content,
        "question": question,
        "message": f"📄 Document Found: {os.path.basename(file_path)}. Preparing to answer your question."
    }
