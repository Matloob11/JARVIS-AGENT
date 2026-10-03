"""
# jarvis_rag.py
Jarvis Local Documents RAG Module
Handles searching, reading, and analyzing local PDF, Word, text and Markdown documents.

C-78: the document index covers the user's own folders only (JARVIS_DOC_DIRS, else Desktop, Documents,
Downloads, their OneDrive copies, the shared folder and D:/ when it exists), never system, tool or
secret folders, and never the JARVIS project/data root itself (chat history, logs, run state and keys
live there), except its Jarvis_Outputs folder. It is built in a worker thread, persisted to
conversations/doc_index.json and refreshed
when it is older than 10 minutes. Until the first index is ready, ask_about_document says so instead of
walking the disk inside the tool call. Chunking and scoring run off the event loop and always end.
"""

# pylint: disable=broad-exception-caught

import asyncio
import json
import os
import stat
import threading
import time
from typing import Any

from docx import Document
from fuzzywuzzy import process
from pypdf import PdfReader

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-RAG")

# --- Index limits (C-78) ---
VALID_EXTS = (".pdf", ".docx", ".txt", ".md")
MAX_WALK_DEPTH = 8  # directory levels below a root
MAX_INDEX_FILES = 50000
MAX_FILE_BYTES = 50 * 1024 * 1024
INDEX_REFRESH_S = 600  # 10 minutes
MATCH_THRESHOLD = 60  # fuzzy score a file name must beat
MAX_CHUNKS = 5000
MAX_CONTEXT_CHARS = 6000  # top 3 chunks; fits the 12000-char tool output cap (TI-01)
READY_WAIT_S = 5.0  # how long a question waits for a build that is almost done
INDEX_PATH = os.path.join(config.conversations_dir, "doc_index.json")

# Directory names never walked (compared in lower case); names that start with these prefixes neither.
EXCLUDED_DIR_NAMES = frozenset({
    "appdata", "node_modules", ".git", "venv", "__pycache__", "site-packages", "$recycle.bin",
    "windows", "programdata", "chroma_db", ".ssh",
})
EXCLUDED_DIR_PREFIXES = (".venv", "program files")
_FILE_ATTRIBUTE_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2)

# --- Global Index Cache ---
# global_doc_index and LAST_INDEX_TIME mirror the shared rag_system index for older callers.
global_doc_index: list[str] = []
LAST_INDEX_TIME: float = 0.0
INDEX_CACHE_TIMEOUT = INDEX_REFRESH_S

# State of the shared rag_system index: state idle/building/ready, files_seen while walking.
INDEX_STATUS: dict[str, Any] = {
    "state": "idle", "files_seen": 0, "built_at": None, "refreshing": False, "error": None,
}


def default_search_dirs() -> list[str]:
    """Document roots: JARVIS_DOC_DIRS (';'-separated) when set, else the user's own document folders,
    the shared folder and D:/ only when that drive exists. Never the whole of C:/Users."""
    env_dirs = [d.strip() for d in (os.getenv("JARVIS_DOC_DIRS") or "").split(";") if d.strip()]
    if env_dirs:
        roots = [os.path.expanduser(d) for d in env_dirs]
    else:
        home = os.path.expanduser("~")
        roots = [os.path.join(home, *parts) for parts in (
            ("Desktop",), ("Documents",), ("Downloads",),
            ("OneDrive", "Desktop"), ("OneDrive", "Documents"),
        )]
        shared = getattr(config, "shared_dir", None)
        if shared:
            roots.append(shared)
        if os.path.exists("D:/"):
            roots.append("D:/")
    unique: list[str] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def is_excluded_dir(name: str) -> bool:
    """True for a directory the walk never enters: system, tool, cache, secret and hidden folders."""
    lower = name.lower()
    return (lower in EXCLUDED_DIR_NAMES or lower.startswith(EXCLUDED_DIR_PREFIXES)
            or lower.startswith("."))


def is_secret_file(name: str) -> bool:
    """True for files that may hold credentials: .env*, *.pem, *.key, id_rsa*, id_ed25519*,
    credentials*.json."""
    lower = name.lower()
    return (lower.startswith((".env", "id_rsa", "id_ed25519"))
            or lower.endswith((".pem", ".key"))
            or (lower.startswith("credentials") and lower.endswith(".json")))


def is_excluded_path(path: str) -> bool:
    """True when any folder on the path is excluded or the file itself is a secret file. Applied to a
    persisted index too, so an index written under older rules never serves such a path."""
    parts = [p for p in os.path.normpath(path).replace("\\", "/").split("/") if p]
    if not parts:
        return True
    folders = parts[:-1]
    # The drive part ("C:") is never a folder name; hidden-name checks apply to the folders only.
    return any(is_excluded_dir(p) for p in folders if not p.endswith(":")) or is_secret_file(parts[-1])


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _within(key: str, root_key: str) -> bool:
    return key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep)


def _private_roots() -> list[str]:
    """The JARVIS data root (JARVIS_DATA_ROOT, set on the Desktop Node) and project root: the repo sits on
    the Desktop, a default document root, and holds conversations/, logs/, run/ and key files."""
    roots: list[str] = []
    for candidate in (os.getenv("JARVIS_DATA_ROOT"), getattr(config, "project_root", None)):
        if candidate and _key(candidate) not in roots:
            roots.append(_key(candidate))
    return roots


def _outputs_key() -> str | None:
    outputs = getattr(config, "outputs_dir", None)
    return _key(outputs) if outputs else None


def is_private_path(path: str) -> bool:
    """True for a path inside the JARVIS project/data root but outside its Jarvis_Outputs folder. Such a
    path is never indexed or served, so chat history and logs never reach the model as a 'document'."""
    key = _key(path)
    if not any(_within(key, root) for root in _private_roots()):
        return False
    outputs = _outputs_key()
    return not (outputs and _within(key, outputs))


def _walk_target(path: str) -> str | None:
    """The folder to walk for path: path itself when it is not private; for a private folder that holds
    Jarvis_Outputs, that folder only (generated documents stay findable); else None."""
    if not is_private_path(path):
        return path
    outputs = getattr(config, "outputs_dir", None)
    if outputs and os.path.isdir(outputs) and _within(_key(outputs), _key(path)):
        return outputs
    return None


def _is_hidden(entry: os.DirEntry) -> bool:
    """Windows hidden attribute (os.DirEntry caches it, so no extra system call)."""
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _FILE_ATTRIBUTE_HIDDEN)


def _is_link(entry: os.DirEntry) -> bool:
    """Symbolic links and Windows junctions are never followed (no loops, no walks outside a root)."""
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


class DocumentRAG:
    """
    Handles RAG operations over local files with recursive chunking and metadata search.
    """

    def __init__(self, search_dirs=None, index_path: str | None = None,
                 status: dict[str, Any] | None = None):
        if search_dirs is None:
            search_dirs = default_search_dirs()
        self.search_dirs = search_dirs
        self.index_path = index_path or INDEX_PATH
        self.status: dict[str, Any] = status if status is not None else {
            "state": "idle", "files_seen": 0, "built_at": None, "refreshing": False, "error": None,
        }
        self._files: list[dict[str, Any]] = []
        self._paths: list[str] = []
        self._indexed_roots: list[str] | None = None
        self._built_at = 0.0
        self._build_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._bg_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ index

    def _roots_key(self) -> list[str]:
        return [os.path.normcase(os.path.abspath(r)) for r in self.search_dirs]

    def is_ready(self) -> bool:
        """An index built for the current search_dirs is available."""
        return self._indexed_roots is not None and self._indexed_roots == self._roots_key()

    def _excluded_below_root(self, path: str) -> bool:
        """is_excluded_path() for the part of path below its root (a root the user chose may itself
        sit in a dotted folder). A path under no current root is excluded."""
        norm = os.path.normcase(os.path.abspath(path))
        for root in self._roots_key():
            prefix = root if root.endswith(os.sep) else root + os.sep
            if norm.startswith(prefix):
                return is_excluded_path(norm[len(prefix):])
        return True

    def is_stale(self) -> bool:
        return not self.is_ready() or (time.time() - self._built_at) >= INDEX_REFRESH_S

    def _walk(self) -> list[dict[str, Any]]:
        """Walks the roots with the C-78 exclusions and limits. Runs in a worker thread."""
        files: list[dict[str, Any]] = []
        seen_dirs: set[str] = set()
        for root in self.search_dirs:
            if not os.path.isdir(root):
                continue
            start = _walk_target(root)
            if start is None:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                path, depth = stack.pop()
                key = os.path.normcase(os.path.abspath(path))
                if key in seen_dirs:  # overlapping roots
                    continue
                seen_dirs.add(key)
                try:
                    with os.scandir(path) as it:
                        entries = list(it)
                except OSError:
                    continue
                for entry in entries:
                    name = entry.name
                    try:
                        if _is_link(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if (depth < MAX_WALK_DEPTH and not is_excluded_dir(name)
                                    and not _is_hidden(entry)):
                                target = _walk_target(entry.path)
                                if target is not None:
                                    stack.append((target, depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if not name.lower().endswith(VALID_EXTS) or is_secret_file(name):
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if info.st_size > MAX_FILE_BYTES:
                        continue
                    files.append({"path": entry.path, "mtime": info.st_mtime, "size": info.st_size})
                    self.status["files_seen"] = len(files)
                    if len(files) >= MAX_INDEX_FILES:
                        logger.warning("DOC_INDEX stopped at %d files", MAX_INDEX_FILES)
                        return files
        return files

    def _apply(self, files: list[dict[str, Any]], roots: list[str], built_at: float) -> None:
        global global_doc_index, LAST_INDEX_TIME  # pylint: disable=global-statement
        with self._state_lock:
            self._files = files
            self._paths = [f["path"] for f in files]
            self._indexed_roots = roots
            self._built_at = built_at
            self.status.update(state="ready", files_seen=len(files), built_at=built_at, error=None)
        if self.status is INDEX_STATUS:
            global_doc_index = list(self._paths)
            LAST_INDEX_TIME = built_at

    def _save(self, files: list[dict[str, Any]], built_at: float) -> None:
        payload = {"built_at": built_at, "roots": list(self.search_dirs), "files": files}
        try:
            folder = os.path.dirname(self.index_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = f"{self.index_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.index_path)
        except OSError as e:
            logger.warning("DOC_INDEX not saved: %s", e)

    def load_index(self) -> bool:
        """Loads the persisted index when it was built for the current roots. Returns True when loaded."""
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            return False
        roots = [os.path.normcase(os.path.abspath(r)) for r in data.get("roots") or []]
        if roots != self._roots_key():
            return False
        files = [f for f in data["files"]
                 if isinstance(f, dict) and isinstance(f.get("path"), str)
                 and not self._excluded_below_root(f["path"]) and not is_private_path(f["path"])]
        try:
            built_at = float(data.get("built_at") or 0.0)
        except (TypeError, ValueError):
            built_at = 0.0
        self._apply(files, roots, built_at)
        logger.info("DOC_INDEX loaded %d files from %s", len(files), self.index_path)
        return True

    def build_index_sync(self) -> list[str]:
        """Walks the roots and saves the index. One build at a time: a second caller waits for the
        running build and returns its result. Blocking: call it from a worker thread."""
        if not self._build_lock.acquire(blocking=False):
            with self._build_lock:
                return list(self._paths)
        try:
            roots = self._roots_key()
            if self.is_ready():
                self.status["refreshing"] = True
            else:
                self.status.update(state="building", files_seen=0)
            started = time.time()
            try:
                files = self._walk()
            except Exception as e:  # the walk itself never raises OSError; anything else is reported
                logger.error("DOC_INDEX build failed: %s", e)
                self.status["error"] = str(e)
                if not self.is_ready():
                    self.status["state"] = "idle"
                return list(self._paths)
            built_at = time.time()
            self._apply(files, roots, built_at)
            self._save(files, built_at)
            logger.info("DOC_INDEX built: %d files in %.1f s", len(files), built_at - started)
            return list(self._paths)
        finally:
            self.status["refreshing"] = False
            self._build_lock.release()

    async def build_index(self) -> list[str]:
        """Builds the index in a worker thread (never on the event loop)."""
        return await asyncio.to_thread(self.build_index_sync)

    def start_background_build(self) -> bool:
        """Starts a build in a daemon thread unless one is running. Returns True when one was started."""
        with self._state_lock:
            if self._bg_thread is not None and self._bg_thread.is_alive():
                return False
            if self._build_lock.locked():
                return False
            if not self.is_ready():
                self.status.update(state="building", files_seen=0)
            self._bg_thread = threading.Thread(target=self._background_build, name="doc-index",
                                               daemon=True)
            self._bg_thread.start()
            return True

    def _background_build(self) -> None:
        try:
            self.build_index_sync()
        except Exception as e:  # a daemon thread must never die with a traceback only
            logger.error("DOC_INDEX background build failed: %s", e)

    async def warm_index(self) -> None:
        """Loads the persisted index, then rebuilds it (in a thread) when it is missing or older than
        10 minutes. Safe to call more than once."""
        if not self.is_ready():
            await asyncio.to_thread(self.load_index)
        if self.is_stale():
            await self.build_index()

    def ensure_index(self) -> None:
        """Called per question: starts a background build when there is no index or it is stale."""
        if not self.is_ready() and self.load_index() and not self.is_stale():
            return
        if self.is_stale():
            self.start_background_build()

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """Starts the index when needed and waits up to timeout seconds (default READY_WAIT_S) for it,
        without blocking the loop."""
        if timeout is None:
            timeout = READY_WAIT_S
        if not self.is_ready():
            await asyncio.to_thread(self.ensure_index)
        elif self.is_stale():
            self.start_background_build()  # answers from the current index meanwhile
        deadline = time.monotonic() + max(0.0, timeout)
        while not self.is_ready() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return self.is_ready()

    async def find_document(self, query: str, metadata_filter: dict | None = None) -> str | None:
        """
        Fuzzy searches for a document, optionally filtered by metadata (extension, date, etc.).
        """
        if not self.is_ready():
            await asyncio.to_thread(self.load_index)
            if not self.is_ready():
                await self.build_index()
        elif self.is_stale():
            self.start_background_build()

        file_list = list(self._paths)
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
                best_match, score = match_result[0], match_result[1]
                return best_match, score, choices[best_match]
            return None, 0, None

        _, score, match_path = await asyncio.to_thread(fuzzy_match)
        if (score > MATCH_THRESHOLD and match_path and not is_private_path(match_path)
                and os.path.isfile(match_path)):
            return match_path
        return None

    # ------------------------------------------------------------------ text

    def chunk_text(self, text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
        """
        Implements Recursive Character Text Splitting.
        Splits by: Double Newline -> Single Newline -> Space -> Character.
        A separator counts only after the overlap, so every step moves forward; the loop always ends
        and returns at most MAX_CHUNKS chunks.
        """
        chunk_size = max(1, int(chunk_size))
        overlap = max(0, int(overlap))
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        split_points = ['\n\n', '\n', ' ', '.']
        while start < len(text) and len(chunks) < MAX_CHUNKS:
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break

            # Find the best split point after the overlap
            chunk = text[start:end]
            split_idx = -1
            for sep in split_points:
                last_sep = chunk.rfind(sep, overlap + 1)
                if last_sep != -1:
                    split_idx = last_sep
                    break

            if split_idx == -1:
                split_idx = chunk_size  # Force split

            chunks.append(text[start:start + split_idx])
            next_start = start + split_idx - overlap  # Backtrack for overlap
            if next_start <= start:
                next_start = start + split_idx
            start = next_start

        return chunks

    def select_context(self, text: str, question: str, top_k: int = 3,
                       max_chars: int = MAX_CONTEXT_CHARS) -> tuple[str, int]:
        """Chunks the text and keeps the top_k chunks by keyword overlap with the question, joined and
        capped at max_chars. Returns (context, total_chunks). Blocking: run it in a worker thread."""
        chunks = self.chunk_text(text)
        # Simple semantic scoring (Keyword overlap between question and chunks)
        question_words = set(question.lower().split())
        chunk_scores = []
        for chunk in chunks:
            score = len(set(chunk.lower().split()) & question_words)
            chunk_scores.append((score, chunk))
        # Pick top chunks (or fewer if small) to fit in context window effectively
        chunk_scores.sort(key=lambda x: x[0], reverse=True)
        best_chunks = [c[1] for c in chunk_scores[:top_k]]
        context = "\n---\n".join(best_chunks)
        return context[:max_chars], len(chunks)

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

    def read_text_file(self, file_path: str) -> str:
        """Reads a .txt or .md file as UTF-8; undecodable bytes become replacement characters."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError as e:
            logger.error("Error reading text file %s: %s", file_path, e)
            return ""

    async def get_document_content(self, file_path: str) -> str:
        """Dispatches based on extension."""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return await asyncio.to_thread(self.read_pdf, file_path)
        if ext == ".docx":
            return await asyncio.to_thread(self.read_docx, file_path)
        if ext in (".txt", ".md"):
            return await asyncio.to_thread(self.read_text_file, file_path)
        return ""


# Global Instance
rag_system = DocumentRAG(status=INDEX_STATUS)


def _indexing_reply(rag: DocumentRAG) -> dict:
    count = int(rag.status.get("files_seen") or 0)
    return {
        "status": "indexing",
        "message": f"Documents index ban raha hai ({count} files)... 1 minute mein dobara poochein.",
    }


@jarvis_tool(execution_timeout=60)
async def ask_about_document(doc_name: str, question: str = "Summarize this document.",
                             extension: str | None = None) -> dict:
    """
    Searches for a local document and answers questions using Optimized RAG.
    Supports extension filtering (e.g., '.pdf').
    """
    logger.info("RAG-OPT: Searching for '%s' (Filter: %s)", doc_name, extension)

    if not await rag_system.wait_ready():
        return _indexing_reply(rag_system)

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

    # Intelligent Chunking and keyword scoring, off the event loop
    relevant_context, total_chunks = await asyncio.to_thread(
        rag_system.select_context, full_text, question)

    return {
        "status": "success",
        "document": os.path.basename(file_path),
        "path": file_path,
        "context": relevant_context,
        "total_chunks": total_chunks,
        "message": f"📄 Document '{os.path.basename(file_path)}' ke relevant parts extract kar liye gaye hain. Main aapka jawab generate kar raha hoon."
    }
