"""
# jarvis_identity.py
Manages persistent identity and background context for the User and their "Sir".
"""
import json
import os
import asyncio
from typing import Dict, Any
from services.utils.jarvis_logger import setup_logger
from services.ai_core.jarvis_plugin_manager import jarvis_tool

logger = setup_logger("JARVIS-IDENTITY")


class IdentityManager:
    """Manages persistent facts about the user and their associates."""

    def __init__(self, storage_path: str = "conversations/identity.json"):
        self.storage_path = storage_path
        self.data: Dict[str, Any] = {
            "user_background": "User background not set yet.",
            "sir_background": "Sir's background not set yet.",
            "sageer_background": "Sageer's background not set yet.",
            "facts": [],
            "relationships": {
                "sageer": "User's close friend and mentor (BSCS student)."
            },
            "anna_mood": "loving",
            "is_upset": False
        }
        self.lock = asyncio.Lock()
        self._load_sync()

    def _load_sync(self):
        """Initial load from disk."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
                logger.info("Identity context loaded from disk.")
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to load identity file: %s", e)

    async def save(self):
        """Persist data to disk asynchronously."""
        async with self.lock:
            try:
                os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

                def _write():
                    temp_path = self.storage_path + ".tmp"
                    with open(temp_path, 'w', encoding='utf-8') as f:
                        json.dump(self.data, f, indent=4, ensure_ascii=False)
                    # Use os_replace for atomic operation
                    os.replace(temp_path, self.storage_path)
                await asyncio.to_thread(_write)
                logger.info("Identity context saved.")
            except IOError as e:
                logger.error("Failed to save identity file: %s", e)

    def get_context(self) -> str:
        """Returns a formatted string for the system prompt."""
        facts_str = "\n".join([f"- {f}" for f in self.data.get("facts", [])])
        rels = self.data.get("relationships", {})
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
        """Adds a new fact to the persistent memory."""
        if "facts" not in self.data:
            self.data["facts"] = []
        if fact not in self.data["facts"]:
            self.data["facts"].append(fact)
            await self.save()
            return f"Fact stored: {fact}"
        return "Fact already exists."

    async def update_relationship(self, person: str, info: str) -> str:
        """Updates relationship info for a specific person."""
        if "relationships" not in self.data:
            self.data["relationships"] = {}
        self.data["relationships"][person.lower()] = info
        await self.save()
        return f"Relationship info updated for {person}."

    async def update_user_background(self, info: str) -> str:
        """Updates user background info."""
        self.data["user_background"] = info
        await self.save()
        return "Aapka background update ho gaya hai, Sir."

    async def update_sir_background(self, info: str) -> str:
        """Updates 'Sir' background info."""
        self.data["sir_background"] = info
        await self.save()
        return "Aapke Sir ka background update ho gaya hai."

    async def update_sageer_background(self, info: str) -> str:
        """Updates Sageer's background info."""
        self.data["sageer_background"] = info
        await self.save()
        return "Sageer bhai ka background update ho gaya hai."

    async def set_anna_mood(self, mood: str, is_upset: bool = False) -> str:
        """Updates Anna's emotional state."""
        self.data["anna_mood"] = mood
        self.data["is_upset"] = is_upset
        await self.save()
        return f"Anna's mood updated to {mood}. Upset status: {is_upset}"

    def get_anna_state(self) -> dict:
        """Returns Anna's current emotional state."""
        return {
            "mood": self.data.get("anna_mood", "loving"),
            "is_upset": self.data.get("is_upset", False)
        }


# Global instance
jarvis_id = IdentityManager()


@jarvis_tool
async def tool_update_user_background(background_info: str) -> dict:
    """
    Updates the assistant's knowledge about the user's background, occupation, or preferences.
    Use this when the user says 'Mere background mein ye add karo' or 'Yaad rakho main...'.
    """
    msg = await jarvis_id.update_user_background(background_info)
    return {"status": "success", "message": msg}


@jarvis_tool
async def tool_update_sir_background(background_info: str) -> dict:
    """
    Updates the assistant's knowledge about the user's superior/Sir.
    Use this when the user says 'Mere Sir ke bare mein ye yaaad rakho' or describes background.
    """
    msg = await jarvis_id.update_sir_background(background_info)
    return {"status": "success", "message": msg}


@jarvis_tool
async def tool_update_sageer_background(background_info: str) -> dict:
    """
    Updates the assistant's knowledge about the user's friend and mentor, Sageer.
    Use this when the user describes Sageer's background or role in their life.
    """
    msg = await jarvis_id.update_sageer_background(background_info)
    return {"status": "success", "message": msg}


@jarvis_tool
async def tool_add_persistent_fact(fact: str) -> dict:
    """
    Adds a new important fact to the assistant's long-term memory.
    Use this when the user mentions something important to remember permanently.
    """
    msg = await jarvis_id.add_fact(fact)
    return {"status": "success", "message": msg}


@jarvis_tool
async def tool_update_relationship_info(person_name: str, relationship_details: str) -> dict:
    """
    Updates details about a specific person in the user's life (e.g., 'Sageer', 'Bhai').
    Use this to deepen the assistant's understanding of social circles.
    """
    msg = await jarvis_id.update_relationship(person_name, relationship_details)
    return {"status": "success", "message": msg}


@jarvis_tool
async def tool_set_anna_state(mood: str, is_upset: bool) -> dict:
    """
    Explicitly sets Anna's emotional mood and upset status.
    Moods: 'loving', 'angry', 'sad', 'playful'.
    """
    msg = await jarvis_id.set_anna_mood(mood, is_upset)
    return {"status": "success", "message": msg}
