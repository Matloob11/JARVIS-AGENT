"""Plan task C-78: document Q&A with a scoped, persisted index, a terminating chunker and no work on the
event loop.

Every test runs on temporary folders with a temporary index file, so none walks the real disk or writes
conversations/doc_index.json. The document tool uses no embedding model or LLM (keyword scoring only),
so there is nothing heavy to load; fuzzy matching is the real fuzzywuzzy call on a few names.
"""
# pylint: disable=protected-access,redefined-outer-name
import asyncio
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.ai_core import jarvis_rag
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.ai_core.jarvis_rag import DocumentRAG
from src.core import agent_runner

ASK = jarvis_rag.ask_about_document  # jarvis_tool registers a wrapper and returns the function itself


def _write(path: Path, text: str | bytes = "hello") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _chunk_in_thread(rag: DocumentRAG, text: str, **kwargs) -> list[str]:
    """Runs chunk_text with a deadline, so a regression fails the test instead of hanging it."""
    result: dict[str, list[str]] = {}
    worker = threading.Thread(target=lambda: result.update(chunks=rag.chunk_text(text, **kwargs)),
                              daemon=True)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "chunk_text did not terminate"
    return result["chunks"]


@pytest.fixture
def rag(tmp_path: Path) -> DocumentRAG:
    root = tmp_path / "docs"
    root.mkdir()
    return DocumentRAG(search_dirs=[str(root)], index_path=str(tmp_path / "index" / "doc_index.json"))


@pytest.fixture
def shared_rag(monkeypatch: pytest.MonkeyPatch, rag: DocumentRAG) -> DocumentRAG:
    """The module's rag_system swapped for a temporary one, as ask_about_document uses it."""
    monkeypatch.setattr(jarvis_rag, "rag_system", rag)
    return rag


# ---------------------------------------------------------------------------------------------- chunker

def test_chunk_text_title_blank_line_and_long_token_terminates_fast():
    text = "Title" + "\n\n" + "x" * 5000
    started = time.monotonic()
    chunks = _chunk_in_thread(DocumentRAG(search_dirs=[]), text)
    assert time.monotonic() - started < 1.0
    assert len(chunks) >= 3
    assert chunks[0].startswith("Title")
    assert chunks[-1].endswith("x")
    assert all(len(c) <= 2000 for c in chunks)


@pytest.mark.parametrize("text,kwargs", [
    ("", {}),
    ("x" * 100_000, {}),  # a single huge token
    ("a b " * 30_000, {}),
    ("word " * 2000, {"chunk_size": 10, "overlap": 10}),  # overlap == chunk size
    ("word " * 2000, {"chunk_size": 10, "overlap": 50}),  # overlap > chunk size
    ("\n\n" * 5000, {"chunk_size": 7, "overlap": 3}),
    ("abc." * 3000, {"chunk_size": 1, "overlap": 0}),
    ("abc " * 3000, {"chunk_size": 0, "overlap": -5}),  # nonsense sizes are clamped
], ids=["empty", "huge-token", "short-words", "overlap-eq-size", "overlap-gt-size", "blank-lines",
        "size-1", "size-0"])
def test_chunk_text_always_terminates(text, kwargs):
    chunks = _chunk_in_thread(DocumentRAG(search_dirs=[]), text, **kwargs)
    assert chunks
    assert len(chunks) <= jarvis_rag.MAX_CHUNKS
    if text:
        size = max(1, kwargs.get("chunk_size", 2000))
        assert all(len(c) <= size for c in chunks)
        assert text.startswith(chunks[0])
        assert text.endswith(chunks[-1]) or len(chunks) == jarvis_rag.MAX_CHUNKS


def test_chunk_text_empty_and_short_text_unchanged():
    rag = DocumentRAG(search_dirs=[])
    assert rag.chunk_text("") == [""]
    assert rag.chunk_text("short text") == ["short text"]


def test_chunk_text_prefers_separators_after_the_overlap_and_overlaps():
    rag = DocumentRAG(search_dirs=[])
    text = ("para one " * 30).strip() + "\n\n" + ("para two " * 30).strip() + "\n\n" + "tail " * 40
    chunks = rag.chunk_text(text, chunk_size=300, overlap=50)
    assert chunks[0] == ("para one " * 30).strip()  # split on the blank line, not mid-word
    # The next chunk starts overlap characters before the split.
    assert text.index(chunks[1]) == len(chunks[0]) - 50


def test_chunk_text_caps_the_chunk_count(monkeypatch):
    monkeypatch.setattr(jarvis_rag, "MAX_CHUNKS", 4)
    chunks = _chunk_in_thread(DocumentRAG(search_dirs=[]), "x" * 10_000, chunk_size=10, overlap=0)
    assert len(chunks) == 4


def test_select_context_caps_at_6000_chars_and_ranks_by_keywords():
    rag = DocumentRAG(search_dirs=[])
    text = ("filler words here " * 120 + "\n\n") * 5 + "skills python liquid laravel " * 20
    context, total = rag.select_context(text, "skills kya hain python")
    assert total >= 3
    assert len(context) <= jarvis_rag.MAX_CONTEXT_CHARS
    assert "skills python" in context.split("\n---\n")[0]


# ------------------------------------------------------------------------------------------------ roots

def test_default_roots_never_cover_all_users(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_DOC_DIRS", raising=False)
    monkeypatch.setattr(jarvis_rag.os.path, "exists", lambda p: False if p == "D:/" else os.path.lexists(p))
    roots = jarvis_rag.default_search_dirs()
    home = os.path.expanduser("~")
    norm = [os.path.normcase(os.path.abspath(r)) for r in roots]
    assert os.path.normcase(os.path.abspath("C:/Users")) not in norm
    assert os.path.normcase(os.path.abspath(home)) not in norm
    assert "D:/" not in roots
    for name in ("Desktop", "Documents", "Downloads"):
        assert os.path.join(home, name) in roots
    assert os.path.join(home, "OneDrive", "Desktop") in roots
    assert os.path.join(home, "OneDrive", "Documents") in roots
    assert jarvis_rag.config.shared_dir in roots


def test_d_drive_only_when_it_exists(monkeypatch):
    monkeypatch.delenv("JARVIS_DOC_DIRS", raising=False)
    monkeypatch.setattr(jarvis_rag.os.path, "exists", lambda p: True if p == "D:/" else os.path.lexists(p))
    assert "D:/" in jarvis_rag.default_search_dirs()


def test_jarvis_doc_dirs_overrides_roots(monkeypatch, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("JARVIS_DOC_DIRS", f"{first};  ;{second};{first}")
    assert jarvis_rag.default_search_dirs() == [str(first), str(second)]
    assert DocumentRAG().search_dirs == [str(first), str(second)]


# ------------------------------------------------------------------------------------------------ index

def test_index_excludes_system_tool_and_secret_paths(rag, tmp_path, monkeypatch):
    root = Path(rag.search_dirs[0])
    keep = [
        _write(root / "my_cv.txt", "Skills: Python"),
        _write(root / "notes" / "plan.md"),
        _write(root / "work" / "report.pdf", b"%PDF-1.4"),
        _write(root / "work" / "letter.docx", b"PK"),
    ]
    skip = [
        _write(root / "AppData" / "Local" / "cache.txt"),
        _write(root / "proj" / "node_modules" / "pkg" / "README.md"),
        _write(root / ".ssh" / "notes.txt"),
        _write(root / ".git" / "description.md"),
        _write(root / ".venv_312" / "Lib" / "site-packages" / "x.txt"),
        _write(root / "venv" / "a.txt"),
        _write(root / "__pycache__" / "a.txt"),
        _write(root / "Program Files (x86)" / "app" / "readme.txt"),
        _write(root / "ProgramData" / "a.txt"),
        _write(root / "Windows" / "a.txt"),
        _write(root / "$Recycle.Bin" / "a.txt"),
        _write(root / "chroma_db" / "a.txt"),
        _write(root / ".hidden" / "a.txt"),
        _write(root / ".env.txt"),
        _write(root / ".env.local.md"),
        _write(root / "id_rsa.txt"),
        _write(root / "id_ed25519.md"),
        _write(root / "image.png", b"\x89PNG"),
        _write(root / "big.txt", "x" * 64),
        _write(root / "d1" / "d2" / "d3" / "deep.txt"),
    ]
    monkeypatch.setattr(jarvis_rag, "MAX_FILE_BYTES", 32)
    monkeypatch.setattr(jarvis_rag, "MAX_WALK_DEPTH", 2)

    paths = rag.build_index_sync()

    assert sorted(paths) == sorted(str(p) for p in keep)
    assert rag.status["state"] == "ready" and rag.status["files_seen"] == len(keep)
    saved = json.loads(Path(rag.index_path).read_text(encoding="utf-8"))
    assert set(saved) >= {"built_at", "roots", "files"}
    assert saved["roots"] == rag.search_dirs
    assert {f["path"] for f in saved["files"]} == {str(p) for p in keep}
    for entry in saved["files"]:
        assert set(entry) == {"path", "mtime", "size"}
        below_root = os.path.relpath(entry["path"], root)  # pytest's tmp_path itself sits in AppData
        assert not any(bad in below_root for bad in ("AppData", "node_modules", ".ssh", ".env"))
    assert not any(str(p) in paths for p in skip)


def test_index_stops_at_the_file_cap(rag, monkeypatch):
    root = Path(rag.search_dirs[0])
    for i in range(10):
        _write(root / f"doc{i}.txt")
    monkeypatch.setattr(jarvis_rag, "MAX_INDEX_FILES", 4)
    assert len(rag.build_index_sync()) == 4


def test_persisted_index_loads_for_the_same_roots_only(rag, tmp_path):
    root = Path(rag.search_dirs[0])
    _write(root / "cv.txt")
    rag.build_index_sync()

    same = DocumentRAG(search_dirs=list(rag.search_dirs), index_path=rag.index_path)
    assert same.load_index() and same.is_ready()
    assert same._paths == [str(root / "cv.txt")]

    other = DocumentRAG(search_dirs=[str(tmp_path)], index_path=rag.index_path)
    assert not other.load_index() and not other.is_ready()


def test_persisted_index_drops_excluded_paths(rag):
    root = Path(rag.search_dirs[0])
    good = str(root / "cv.txt")
    data = {"built_at": time.time(), "roots": rag.search_dirs, "files": [
        {"path": good, "mtime": 0, "size": 1},
        {"path": str(root / "AppData" / "x.txt"), "mtime": 0, "size": 1},
        {"path": str(root / "a" / ".ssh" / "k.txt"), "mtime": 0, "size": 1},
        {"path": str(root / ".env.md"), "mtime": 0, "size": 1},
        {"path": "C:/elsewhere/outside.txt", "mtime": 0, "size": 1},
    ]}
    _write(Path(rag.index_path), json.dumps(data))
    assert rag.load_index()
    assert rag._paths == [good]


# ------------------------------------------------------------------------------- the JARVIS repo itself

@pytest.fixture
def fake_repo(rag: DocumentRAG, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A JARVIS checkout inside the document root (the real repo sits on the Desktop, a default root),
    set as the project root with its Jarvis_Outputs folder."""
    repo = Path(rag.search_dirs[0]) / "JARVIS-AGENT-main"
    (repo / "Jarvis_Outputs").mkdir(parents=True)
    monkeypatch.delenv("JARVIS_DATA_ROOT", raising=False)
    monkeypatch.setattr(jarvis_rag.config, "project_root", str(repo))
    monkeypatch.setattr(jarvis_rag.config, "outputs_dir", str(repo / "Jarvis_Outputs"))
    return repo


def test_index_never_walks_the_jarvis_repo_except_its_outputs(rag, fake_repo):
    root = Path(rag.search_dirs[0])
    keep = [
        _write(root / "cv.txt"),
        _write(fake_repo / "Jarvis_Outputs" / "report.md"),
        _write(fake_repo / "Jarvis_Outputs" / "research" / "summary.txt"),
    ]
    private = [
        _write(fake_repo / "conversations" / "chat_history.txt", "owner chat"),
        _write(fake_repo / "logs" / "jarvis_hybrid_local.md"),
        _write(fake_repo / "run" / "state.txt"),
        _write(fake_repo / "docs" / "guide.md"),
        _write(fake_repo / "README.md"),
    ]

    paths = rag.build_index_sync()

    assert sorted(paths) == sorted(str(p) for p in keep)
    assert all(jarvis_rag.is_private_path(str(p)) for p in private)
    assert not any(jarvis_rag.is_private_path(str(p)) for p in keep)


def test_the_data_root_is_private_on_the_desktop_node(rag, monkeypatch, tmp_path):
    """The node runs from a snapshot (project_root there), and JARVIS_DATA_ROOT names the repo."""
    data_root = Path(rag.search_dirs[0]) / "repo"
    monkeypatch.setenv("JARVIS_DATA_ROOT", str(data_root))
    monkeypatch.setattr(jarvis_rag.config, "project_root", str(tmp_path / "snapshot"))
    monkeypatch.setattr(jarvis_rag.config, "outputs_dir", str(data_root / "Jarvis_Outputs"))
    kept = _write(data_root / "Jarvis_Outputs" / "qr_notes.md")
    _write(data_root / "conversations" / "chat.txt")
    _write(data_root / "logs" / "desktop_audit.md")

    assert rag.build_index_sync() == [str(kept)]


def test_a_root_that_is_the_repo_walks_only_its_outputs(tmp_path, fake_repo):
    kept = _write(fake_repo / "Jarvis_Outputs" / "letter.txt")
    _write(fake_repo / "conversations" / "chat.txt")
    repo_only = DocumentRAG(search_dirs=[str(fake_repo)],
                            index_path=str(tmp_path / "index2" / "doc_index.json"))

    assert repo_only.build_index_sync() == [str(kept)]


def test_persisted_index_drops_private_paths(rag, fake_repo):
    good = str(Path(rag.search_dirs[0]) / "cv.txt")
    output = str(fake_repo / "Jarvis_Outputs" / "report.md")
    data = {"built_at": time.time(), "roots": rag.search_dirs, "files": [
        {"path": good, "mtime": 0, "size": 1},
        {"path": output, "mtime": 0, "size": 1},
        {"path": str(fake_repo / "conversations" / "chat.txt"), "mtime": 0, "size": 1},
        {"path": str(fake_repo / "logs" / "jarvis.md"), "mtime": 0, "size": 1},
    ]}
    _write(Path(rag.index_path), json.dumps(data))

    assert rag.load_index()
    assert rag._paths == [good, output]


async def test_find_document_never_returns_a_private_path(rag, fake_repo):
    _write(Path(rag.search_dirs[0]) / "cv.txt")
    rag.build_index_sync()
    chat = _write(fake_repo / "conversations" / "chat_history.txt", "owner chat")
    rag._paths.append(str(chat))  # as if an index from older code still listed it

    assert await rag.find_document("chat history") is None


def test_changed_search_dirs_need_a_new_index(rag, tmp_path):
    _write(Path(rag.search_dirs[0]) / "a.txt")
    rag.build_index_sync()
    assert rag.is_ready()
    other = tmp_path / "other"
    other.mkdir()
    rag.search_dirs = [str(other)]
    assert not rag.is_ready()


def test_stale_index_refreshes_in_background_and_keeps_answering(rag, monkeypatch):
    root = Path(rag.search_dirs[0])
    _write(root / "first.txt")
    rag.build_index_sync()
    rag._built_at -= jarvis_rag.INDEX_REFRESH_S + 1
    assert rag.is_ready() and rag.is_stale()
    _write(root / "second.txt")

    assert rag.start_background_build()
    rag._bg_thread.join(timeout=5)
    assert not rag.is_stale()
    assert str(root / "second.txt") in rag._paths
    assert rag.status["state"] == "ready" and rag.status["refreshing"] is False


async def test_warm_index_builds_off_the_event_loop(rag, monkeypatch):
    _write(Path(rag.search_dirs[0]) / "cv.txt")
    walk_threads: list[str] = []
    real_walk = rag._walk

    def slow_walk():
        walk_threads.append(threading.current_thread().name)
        time.sleep(0.4)
        return real_walk()

    monkeypatch.setattr(rag, "_walk", slow_walk)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    tick_task = asyncio.create_task(ticker())
    try:
        await rag.warm_index()
    finally:
        tick_task.cancel()
    assert rag.is_ready()
    assert walk_threads and walk_threads[0] != threading.main_thread().name
    assert ticks >= 5  # the loop kept running while the walk slept
    assert Path(rag.index_path).is_file()

    # A second warm-up with a fresh index does not walk again.
    await rag.warm_index()
    assert len(walk_threads) == 1


async def test_find_document_uses_fuzzy_threshold(rag):
    root = Path(rag.search_dirs[0])
    _write(root / "Matloob_CV.txt")
    _write(root / "invoice_march.pdf", b"%PDF")
    rag.build_index_sync()
    assert await rag.find_document("matloob cv") == str(root / "Matloob_CV.txt")
    assert await rag.find_document("matloob cv", {"extension": ".pdf"}) is None
    assert await rag.find_document("zzzzqqqq") is None


# ------------------------------------------------------------------------------------------------- text

async def test_txt_and_md_files_are_read(rag):
    root = Path(rag.search_dirs[0])
    txt = _write(root / "a.txt", "plain text")
    md = _write(root / "b.md", b"# Heading\nbad byte \xff here")
    assert await rag.get_document_content(str(txt)) == "plain text"
    content = await rag.get_document_content(str(md))
    assert content.startswith("# Heading") and "\ufffd" in content
    assert await rag.get_document_content(str(root / "c.xyz")) == ""


# ------------------------------------------------------------------------------------------------- tool

def test_tool_timeout_is_60_seconds():
    meta = plugin_manager.get_tool_meta("ask_about_document")
    assert meta is not None
    assert meta["timeout"] == 60.0
    assert meta["max_output_chars"] >= 12000


async def test_ask_returns_indexing_while_the_index_builds(shared_rag, monkeypatch):
    release = threading.Event()

    def blocked_walk():
        shared_rag.status["files_seen"] = 7
        release.wait(5)
        return []

    monkeypatch.setattr(shared_rag, "_walk", blocked_walk)
    monkeypatch.setattr(jarvis_rag, "READY_WAIT_S", 0.2)
    try:
        started = time.monotonic()
        reply = await ASK(doc_name="cv", question="skills?")
        assert time.monotonic() - started < 3.0
        assert reply["status"] == "indexing"
        assert "7 files" in reply["message"] and "dobara" in reply["message"]
        assert shared_rag.status["state"] == "building"
    finally:
        release.set()
        if shared_rag._bg_thread is not None:
            shared_rag._bg_thread.join(timeout=5)


async def test_ask_answers_from_a_txt_file(shared_rag, monkeypatch):
    root = Path(shared_rag.search_dirs[0])
    _write(root / "Matloob_CV.txt",
           ("Experience at many companies over many years. " * 60 + "\n\n") * 4
           + "Skills: Python, Liquid, Laravel, Docker. " * 30)
    shared_rag.build_index_sync()
    chunk_threads: list[str] = []
    real_select = shared_rag.select_context

    def recording_select(*args, **kwargs):
        chunk_threads.append(threading.current_thread().name)
        return real_select(*args, **kwargs)

    monkeypatch.setattr(shared_rag, "select_context", recording_select)
    reply = await ASK(doc_name="matloob cv", question="Skills: kya likhi hain")
    assert reply["status"] == "success", reply
    assert reply["document"] == "Matloob_CV.txt"
    assert "Skills: Python" in reply["context"]
    assert len(reply["context"]) <= jarvis_rag.MAX_CONTEXT_CHARS
    assert reply["total_chunks"] >= 3
    assert chunk_threads and chunk_threads[0] != threading.main_thread().name


async def test_ask_not_found_and_empty(shared_rag):
    root = Path(shared_rag.search_dirs[0])
    _write(root / "empty_notes.txt", "   ")
    shared_rag.build_index_sync()
    assert (await ASK(doc_name="qqqqzzzz"))["status"] == "not_found"
    assert (await ASK(doc_name="empty notes"))["status"] == "error"


# ----------------------------------------------------------------------------------------- agent runner

@pytest.mark.parametrize("raw,expected", [
    (None, 120.0), ("", 120.0), ("30", 30.0), ("0", 0.0), ("abc", 120.0), ("-1", None),
])
def test_rag_warm_delay(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("JARVIS_RAG_WARM_DELAY_S", raising=False)
    else:
        monkeypatch.setenv("JARVIS_RAG_WARM_DELAY_S", raw)
    assert agent_runner._rag_warm_delay() == expected


async def test_warm_document_index_waits_then_warms(monkeypatch):
    warm = AsyncMock()
    monkeypatch.setattr(jarvis_rag.rag_system, "warm_index", warm)
    started = time.monotonic()
    await agent_runner._warm_document_index(0.2)
    assert time.monotonic() - started >= 0.15  # Windows timers may fire one tick (~16 ms) early
    warm.assert_awaited_once()


async def test_warm_document_index_logs_failures(monkeypatch):
    monkeypatch.setattr(jarvis_rag.rag_system, "warm_index", AsyncMock(side_effect=RuntimeError("boom")))
    await agent_runner._warm_document_index(0)  # no exception reaches the session


async def test_warm_document_index_is_cancellable(monkeypatch):
    warm = AsyncMock()
    monkeypatch.setattr(jarvis_rag.rag_system, "warm_index", warm)
    task = asyncio.create_task(agent_runner._warm_document_index(60))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    warm.assert_not_awaited()
