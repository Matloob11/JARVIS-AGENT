"""
# jarvis_rag.py
Jarvis Local Documents RAG Module
Handles searching, reading, and analyzing local PDF and Word documents.
"""

import os
import logging
import asyncio
from typing import Optional
from fuzzywuzzy import process
from pypdf import PdfReader
from docx import Document
from livekit.agents import function_tool

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-RAG")


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
        """
        try:
            file_list = []
            for base_dir in self.search_dirs:
                if not os.path.exists(base_dir):
                    continue
                for root, _, files in os.walk(base_dir):
                    for f in files:
                        if f.lower().endswith(('.pdf', '.docx')):
                            file_list.append(os.path.join(root, f))

            if not file_list:
                return None

            choices = {os.path.basename(f): f for f in file_list}
            best_match, score = process.extractOne(query, list(choices.keys()))

            logger.info(
                "Fuzzy search: '%s' matched to '%s' (Score: %d)", query, best_match, score)

            if score > 60:
                return choices[best_match]
            return None
        except Exception as e:
            logger.error("Error searching for document: %s", e)
            return None

    def read_pdf(self, file_path: str) -> str:
        """Reads text from a PDF file."""
        text = ""
        try:
            reader = PdfReader(file_path)
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            logger.error("Error reading PDF %s: %s", file_path, e)
            return f"Error reading PDF: {e}"

    def read_docx(self, file_path: str) -> str:
        """Reads text from a Word document."""
        try:
            doc = Document(file_path)
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
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
async def ask_about_document(doc_name: str, question: str = "Summarize this document.") -> str:
    """
    Searches for a local document (PDF or Word) on your D: drive and answers questions about it.
    Use this when the user asks about invoices, reports, resumes, or any local document content.

    Example queries:
    - 'Jarvis, meri last month ki invoice summarize karo.'
    - 'What is the total amount in the electricity bill PDF?'
    - 'Resume.docx mein kya experience likha hai?'
    """
    logger.info("RAG Tool: Searching for '%s' to answer: '%s'",
                doc_name, question)

    file_path = await rag_system.find_document(doc_name)
    if not file_path:
        return f"❌ Maaf kijiye, mujhe '{doc_name}' naam ka koi PDF ya Word document nahi mila."

    logger.info("Found document at: %s. Extracting text...", file_path)
    content = await rag_system.get_document_content(file_path)

    if len(content) > 15000:
        # Simple truncation for context window limits if document is massive
        content = content[:15000] + "... [Content truncated]"

    return (f"📄 Document Found: {os.path.basename(file_path)}\n\n"
            f"Context Content:\n{content}\n\n"
            f"User Question: {question}")
