"""
# jarvis_identity.py
Manages persistent identity and background context for the User and their "Sir".

Cross-process (C-125 step 1, K-77): on the VPS a job and jarvis-agent-svc share conversations/identity.json, and on
the PC several job threads can change it. Every change is one read-modify-write under the file lock
(services.utils.jarvis_filelock.locked, '<file>.lock'): the file is read again when it changed since this process last
saw it, the change is applied, and the whole file is written through a temp file and os.replace. Readers
(get_context, get_anna_state) reload when the file's mtime, inode or size changed, so a change made by another
process shows up in the next prompt. An unreadable file keeps the data last read (and is never moved aside).
"""
import asyncio
import json
import os
from collections.abc import Callable
from typing import Any, TypedDict, cast

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_filelock import locked
from services.utils.jarvis_logger import setup_logger
from services.utils.probe_context import bridge_suppressed  # K-57 (C-39)

logger = setup_logger("JARVIS-IDENTITY")

FileStamp = tuple[int, int, int]  # (st_mtime_ns, st_ino, st_size): a change of any means another writer

class AnnaState(TypedDict):
    """Structured emotional state for the Anna persona."""
    mood: str
    is_upset: bool

class IdentityData(TypedDict):
    """Full schema for persistent identity storage."""
    user_background: str
    sir_background: str
    sageer_background: str
    facts: list[str]
    relationships: dict[str, str]
    anna_mood: str
    is_upset: bool

def _default_data() -> IdentityData:
    return {
        "user_background": "User background not set yet.",
        "sir_background": "Sir's background not set yet.",
        "sageer_background": "Sir's friend/mentor background not set yet.",
        "facts": [],
        "relationships": {
            "sageer": "User's close friend and mentor (BSCS student).",
        },
        "anna_mood": "loving",
        "is_upset": False,
    }


class IdentityManager:
    """Manages persistent facts about the user and their associates."""

    def __init__(self, storage_path: str = "conversations/identity.json") -> None:
        self.storage_path = storage_path
        self.data: IdentityData = _default_data()
        self.lock = asyncio.Lock()
        self._stamp: FileStamp | None = None
        self._load_sync()

    # -- disk (C-125 step 1) -----------------------------------------------------------------------------------

    def _file_stamp(self) -> FileStamp | None:
        try:
            st = os.stat(self.storage_path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_ino, st.st_size)

    def _read_file(self) -> bool:
        """Reads the file into data (defaults first, then the stored fields) and remembers its stamp. False when it
        is missing or unreadable; data then stays as it was."""
        stamp = self._file_stamp()
        if stamp is None:
            return False
        try:
            with open(self.storage_path, encoding='utf-8') as f:
                loaded = json.load(f)
        except (OSError, ValueError) as e:
            logger.error("Failed to load identity file: %s", type(e).__name__)
            return False
        if not isinstance(loaded, dict):
            logger.error("Failed to load identity file: not an object")
            return False
        data = _default_data()
        data.update(cast(IdentityData, loaded))
        self.data = data
        self._stamp = stamp
        return True

    def _load_sync(self) -> None:
        """Initial load from disk."""
        if self._read_file():
            logger.info("Identity context loaded from disk.")

    def _refresh(self) -> None:
        """Reloads the file when another writer changed it since this process last read or wrote it."""
        stamp = self._file_stamp()
        if stamp is None or stamp == getattr(self, "_stamp", None):
            return
        if self._read_file():
            logger.info("Identity context reloaded: the file changed on disk.")

    def _write_file(self) -> None:
        """Writes data whole (temp file + os.replace); the caller holds the file lock."""
        os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
        temp_path = self.storage_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)
        os.replace(temp_path, self.storage_path)  # atomic: a reader sees the old or the new file
        self._stamp = self._file_stamp()

    def _change_sync(self, change: Callable[[IdentityData], tuple[Any, bool]]) -> Any:
        """One read-modify-write under the file lock: reload if another process changed the file, apply change
        (which returns (result, changed)), write when it changed. A failed write is logged and the change stays in
        memory (as before C-125); a lock that stays busy past its timeout applies the change in memory only."""
        try:
            with locked(self.storage_path):
                self._refresh()
                result, changed = change(self.data)
                if changed:
                    try:
                        self._write_file()
                    except OSError as e:
                        logger.error("Failed to save identity file: %s", type(e).__name__)
                return result
        except TimeoutError:
            logger.error("Failed to save identity file: its lock stayed busy")
            return change(self.data)[0]

    async def _change(self, change: Callable[[IdentityData], tuple[Any, bool]]) -> Any:
        async with self.lock:
            return await asyncio.to_thread(self._change_sync, change)

    async def save(self) -> None:
        """Persist data to disk asynchronously (under the file lock)."""
        if bridge_suppressed.get():
            return  # K-57: a probe or canary job never writes the identity file
        async with self.lock:
            try:
                def _write() -> None:
                    with locked(self.storage_path):
                        self._write_file()
                await asyncio.to_thread(_write)
                logger.info("Identity context saved.")
            except (OSError, TimeoutError) as e:
                logger.error("Failed to save identity file: %s", type(e).__name__)

    def get_context(self) -> str:
        """Returns a formatted string for the system prompt."""
        self._refresh()
        facts_str = "\n".join([f"- {f}" for f in self.data.get("facts", [])])
        rels = cast(dict[str, str], self.data.get("relationships", {}))
        rels_str = "\n".join([f"- {k}: {v}" for k, v in rels.items()])

        return (
            "\n---------------------------------------\n"
            "🌟 USER & SIR BACKGROUND (PERSISTENT)\n"
            "---------------------------------------\n"
            f"- User Context: {self.data.get('user_background')}\n"
            f"- Sir Context: {self.data.get('sir_background')}\n"
            f"- Sageer Context: {self.data.get('sageer_background')}\n"
            f"\n📚 KEY FACTS:\n{facts_str if facts_str else '- No key facts stored yet.'}\n"
            f"\n🤝 RELATIONSHIPS:\n{rels_str}\n"
        )

    async def add_fact(self, fact: str) -> str:
        """Adds a new fact to the persistent memory with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."

        def change(data: IdentityData) -> tuple[str, bool]:
            facts = data["facts"]
            if fact in facts:
                return "Fact already exists.", False
            facts.append(fact)
            return f"Fact stored: {fact}", True
        return cast(str, await self._change(change))

    async def update_relationship(self, person: str, info: str) -> str:
        """Updates relationship info for a specific person with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."

        def change(data: IdentityData) -> tuple[str, bool]:
            data["relationships"][person.lower()] = info
            return f"Relationship info updated for {person}.", True
        return cast(str, await self._change(change))

    async def _set_fields(self, fields: dict[str, Any], message: str) -> str:
        def change(data: IdentityData) -> tuple[str, bool]:
            data.update(cast(IdentityData, fields))
            return message, True
        return cast(str, await self._change(change))

    async def update_user_background(self, info: str) -> str:
        """Updates user background info with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."
        return await self._set_fields({"user_background": info}, "Aapka background update ho gaya hai, Sir.")

    async def update_sir_background(self, info: str) -> str:
        """Updates 'Sir' background info with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."
        return await self._set_fields({"sir_background": info}, "Aapke Sir ka background update ho gaya hai.")

    async def update_sageer_background(self, info: str) -> str:
        """Updates Sageer's background info with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."
        return await self._set_fields({"sageer_background": info}, "Sageer bhai ka background update ho gaya hai.")

    async def set_anna_mood(self, mood: str, is_upset: bool = False) -> str:
        """Updates Anna's emotional state with lock protection."""
        if bridge_suppressed.get():
            return "Probe job: nothing stored (K-57)."
        return await self._set_fields({"anna_mood": mood, "is_upset": is_upset},
                                      f"Anna's mood updated to {mood}. Upset status: {is_upset}")

    async def _save_internal(self) -> None:
        """Internal save (kept for older callers): the whole file under the file lock. Call it while holding
        self.lock."""
        if bridge_suppressed.get():
            return  # K-57: a probe or canary job never writes the identity file

        def _write() -> None:
            with locked(self.storage_path):
                self._write_file()
        try:
            await asyncio.to_thread(_write)
        except (OSError, TimeoutError) as e:
            logger.error("Failed to save identity file: %s", type(e).__name__)

    # Consolidated into standard public save method

    def get_anna_state(self) -> AnnaState:
        """Returns Anna's current emotional state."""
        self._refresh()
        return {
            "mood": cast(str, self.data.get("anna_mood", "loving")),
            "is_upset": cast(bool, self.data.get("is_upset", False)),
        }

# Global instance
jarvis_id = IdentityManager()

@jarvis_tool
async def tool_update_user_background(background_info: str) -> dict[str, str]:
    """
    Updates the assistant's knowledge about the user's background, occupation, or preferences.
    Use this when the user says 'Mere background mein ye add karo' or 'Yaad rakho main...'.
    """
    msg = await jarvis_id.update_user_background(background_info)
    return {"status": "success", "message": msg}

@jarvis_tool
async def tool_update_sir_background(background_info: str) -> dict[str, str]:
    """
    Updates the assistant's knowledge about the user's superior/Sir.
    Use this when the user says 'Mere Sir ke bare mein ye yaaad rakho' or describes background.
    """
    msg = await jarvis_id.update_sir_background(background_info)
    return {"status": "success", "message": msg}

@jarvis_tool
async def tool_update_sageer_background(background_info: str) -> dict[str, str]:
    """
    Updates the assistant's knowledge about the user's friend and mentor, Sageer.
    Use this when the user describes Sageer's background or role in their life.
    """
    msg = await jarvis_id.update_sageer_background(background_info)
    return {"status": "success", "message": msg}

@jarvis_tool
async def tool_add_persistent_fact(fact: str) -> dict[str, str]:
    """
    Adds a new important fact to the assistant's long-term memory.
    Use this when the user mentions something important to remember permanently.
    """
    msg = await jarvis_id.add_fact(fact)
    return {"status": "success", "message": msg}

@jarvis_tool
async def tool_update_relationship_info(person_name: str, relationship_details: str) -> dict[str, str]:
    """
    Updates details about a specific person in the user's life (e.g., 'Sageer', 'Bhai').
    Use this to deepen the assistant's understanding of social circles.
    """
    msg = await jarvis_id.update_relationship(person_name, relationship_details)
    return {"status": "success", "message": msg}

@jarvis_tool
async def tool_set_anna_state(mood: str, is_upset: bool) -> dict[str, str]:
    """
    Explicitly sets Anna's emotional mood and upset status.
    Moods: 'loving', 'angry', 'sad', 'playful'.
    """
    msg = await jarvis_id.set_anna_mood(mood, is_upset)
    return {"status": "success", "message": msg}
