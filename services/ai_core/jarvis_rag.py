"""
# jarvis_rag.py
Jarvis Local Documents RAG Module
Handles searching, reading, and analyzing local PDF and Word documents.
"""

import asyncio
import os

from docx import Document
from fuzzywuzzy import process
from pypdf import PdfReader

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-RAG")


# --- Global Index Cache ---
global_doc_index: list[str] = []
LAST_INDEX_TIME: float = 0.0
INDEX_CACHE_TIMEOUT = 300  # 5 minutes


class DocumentRAG:
    """
    Handles RAG operations over local files with recursive chunking and metadata search.
    """

    def __init__(self, search_dirs=None):
        if search_dirs is None:
            search_dirs = ["D:/", "C:/Users"]
        self.search_dirs = search_dirs

    async def find_document(self, query: str, metadata_filter: dict | None = None) -> str | None:
        """
        Fuzzy searches for a document, optionally filtered by metadata (extension, date, etc.).
        """
        global global_doc_index, LAST_INDEX_TIME
        current_time = asyncio.get_event_loop().time()

        if global_doc_index and (current_time - LAST_INDEX_TIME < INDEX_CACHE_TIMEOUT):
            file_list = global_doc_index
        else:
            def walk_docs():
                results = []
                # Common document extensions
                valid_exts = ('.pdf', '.docx', '.txt', '.md')
                for base_dir in self.search_dirs:
                    if not os.path.exists(base_dir):
                        continue
                    try:
                        for root, _, files in os.walk(base_dir):
                            for f in files:
                                if f.lower().endswith(valid_exts):
                                    results.append(os.path.join(root, f))
                    except (PermissionError, OSError):
                        continue
                return results

            logger.info("📂 Refreshing document index...")
            file_list = await asyncio.to_thread(walk_docs)
            global_doc_index = file_list
            LAST_INDEX_TIME = current_time

        if not file_list:
            return None

        # Apply metadata filter if provided (e.g., {"extension": ".pdf"})
        if metadata_filter:
            ext = metadata_filter.get("extension")
            if ext:
                file_list = [f for f in file_list if f.lower().endswith(ext.lower())]

        def fuzzy_match():
            choices = {os.path.basename(f): f for f in file_list}
            match_result = process.extractOne(query, list(choices.keys()))
            if match_result:
                best_match, score = match_result
                return best_match, score, choices[best_match]
            return None, 0, None

        best_match, score, match_path = await asyncio.to_thread(fuzzy_match)
        if score > 60:
            return match_path
        return None

    def chunk_text(self, text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
        """
        Implements Recursive Character Text Splitting.
        Splits by: Double Newline -> Single Newline -> Space -> Character.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break
            
            # Find the best split point within the overlap
            chunk = text[start:end]
            split_points = ['\n\n', '\n', ' ', '.']
            split_idx = -1
            
            for sep in split_points:
                last_sep = chunk.rfind(sep)
                if last_sep != -1:
                    split_idx = last_sep
                    break
            
            if split_idx == -1:
                split_idx = chunk_size # Force split

            chunks.append(text[start:start + split_idx])
            start += split_idx - overlap # Backtrack for overlap
            if start < 0: start = 0
            
        return chunks

    def read_pdf(self, file_path: str) -> str:
        """Reads text from a PDF file."""
        text = ""
        try:
            reader = PdfReader(file_path)
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
            return text
        except Exception as e:
            logger.error("Error reading PDF %s: %s", file_path, e)
            return ""

    def read_docx(self, file_path: str) -> str:
        """Reads text from a Word document."""
        try:
            doc = Document(file_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            logger.error("Error reading DOCX %s: %s", file_path, e)
            return ""

    async def get_document_content(self, file_path: str) -> str:
        """Dispatches based on extension."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await asyncio.to_thread(self.read_pdf, file_path)
        if ext == ".docx":
            return await asyncio.to_thread(self.read_docx, file_path)
        return ""


# Global Instance
rag_system = DocumentRAG()


@jarvis_tool
async def ask_about_document(doc_name: str, question: str = "Summarize this document.", 
                             extension: str | None = None) -> dict:
    """
    Searches for a local document and answers questions using Optimized RAG.
    Supports extension filtering (e.g., '.pdf').
    """
    logger.info("RAG-OPT: Searching for '%s' (Filter: %s)", doc_name, extension)

    metadata_filter = {"extension": extension} if extension else None
    file_path = await rag_system.find_document(doc_name, metadata_filter)
    
    if not file_path:
        return {
            "status": "not_found",
            "message": f"❌ Maazrat, mujhe '{doc_name}' naam ka koi document nahi mila."
        }

    logger.info("Document found. Chunking and analyzing...")
    full_text = await rag_system.get_document_content(file_path)
    
    if not full_text.strip():
        return {"status": "error", "message": "❌ Document khali hai ya readable nahi hai."}

    # Intelligent Chunking
    chunks = rag_system.chunk_text(full_text)
    
    # Simple semantic scoring (Keyword overlap between question and chunks)
    question_words = set(question.lower().split())
    chunk_scores = []
    for chunk in chunks:
        overlap = len(set(chunk.lower().split()) & question_words)
        chunk_scores.append((overlap, chunk))
    
    # Pick top 3 chunks (or fewer if small) to fit in context window effectively
    chunk_scores.sort(key=lambda x: x[0], reverse=True)
    best_chunks = [c[1] for c in chunk_scores[:3]]
    relevant_context = "\n---\n".join(best_chunks)

    return {
        "status": "success",
        "document": os.path.basename(file_path),
        "path": file_path,
        "context": relevant_context,
        "total_chunks": len(chunks),
        "message": f"📄 Document '{os.path.basename(file_path)}' ke relevant parts extract kar liye gaye hain. Main aapka jawab generate kar raha hoon."
    }
