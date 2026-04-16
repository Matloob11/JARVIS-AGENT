"""
# swarm_manager.py
Implements Task Swarming / Coordinator Mode (Ultraplan) for JARVIS.
Spawns parallel LLM sub-agents to achieve tasks autonomously and concurrently.
Uses Gemini REST API via httpx (no extra packages needed — matches existing project pattern).
"""

import asyncio
import json
import os

import httpx

from services.utils.jarvis_bridge import notify_transcription, notify_ui
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("SWARM-MANAGER")

# Gemini REST endpoint — same model used by the live voice agent
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def _get_api_key() -> str | None:
    """Reads GOOGLE_API_KEY lazily so dotenv is already loaded by the time this runs."""
    return os.getenv("GOOGLE_API_KEY")


async def _call_gemini(prompt: str) -> str:
    """
    Calls Gemini REST API with a single user prompt.
    Returns the text response or raises on failure.
    """
    api_key = _get_api_key()
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not set in environment.")

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{_GEMINI_URL}?key={api_key}",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]


class SwarmCoordinator:
    """
    Coordinates multi-agent swarming.
    Spawns background clones (sub-agents) for parallel task processing.
    Architecture:
      1. Decompose  → LLM splits task into 2-3 isolated sub-tasks
      2. Execute    → asyncio.gather runs them in parallel (max 3)
      3. Aggregate  → LLM synthesizes results into a final Markdown report
    """

    MAX_AGENTS = 3

    @property
    def is_active(self) -> bool:
        """Returns True if the API key is present."""
        return bool(_get_api_key())

    async def _send_ui_update(self, msg: str) -> None:
        """Pushes a real-time status update to the UI."""
        logger.info("Swarm UI Update: %s", msg)
        asyncio.create_task(
            notify_ui("agent_message", {"text": f"\U0001f916 **[SWARM]** {msg}"})
        )
        asyncio.create_task(
            notify_transcription("agent", f"[SWARM] {msg}", is_final=True)
        )

    async def _decompose_task(self, user_intent: str) -> list[dict[str, str]]:
        """Uses the LLM to split the user request into 2-3 parallel sub-tasks."""
        await self._send_ui_update("Task breakdown shuru kar raha hun...")

        prompt = (
            "You are an ultra-planner. Decompose the following user task into exactly "
            "2 or 3 parallel sub-tasks that can be executed independently by specialist agents.\n"
            f"Task: {user_intent}\n\n"
            "Output ONLY a valid JSON array, no markdown fences:\n"
            '[{"id":"agent_1","role":"Search Specialist","instruction":"..."},'
            '{"id":"agent_2","role":"Code Writer","instruction":"..."}]'
        )
        try:
            text = await _call_gemini(prompt)
            # Strip possible markdown fences
            text = text.strip()
            for fence in ("```json", "```"):
                if text.startswith(fence):
                    text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            tasks: list[dict[str, str]] = json.loads(text.strip())
            logger.info("Decomposed into %d sub-tasks.", len(tasks))
            return tasks
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Task decomposition failed: %s", e)
            await self._send_ui_update("Task decomposition mein error aagaya. Single-agent fallback activated.")
            return [{"id": "agent_1", "role": "Generalist", "instruction": user_intent}]

    async def _run_sub_agent(self, task: dict[str, str]) -> dict[str, str]:
        """Runs one isolated sub-agent and returns its result dict."""
        agent_id = task.get("id", "agent_X")
        agent_role = task.get("role", "Worker")
        instruction = task.get("instruction", "")

        await self._send_ui_update(
            f"Sub-agent '{agent_role}' ({agent_id}) start ho gaya..."
        )

        prompt = (
            f"You are a specialized JARVIS sub-agent. Role: {agent_role}.\n"
            "Execute the instruction below and return a detailed, accurate report.\n"
            f"Instruction: {instruction}"
        )
        try:
            result_text = await _call_gemini(prompt)
            await self._send_ui_update(
                f"Sub-agent '{agent_role}' ({agent_id}) complete ho gaya."
            )
            return {"id": agent_id, "role": agent_role, "result": result_text}
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Sub-agent %s failed: %s", agent_id, e)
            await self._send_ui_update(
                f"Sub-agent '{agent_role}' ({agent_id}) failed — API error."
            )
            return {"id": agent_id, "role": agent_role, "result": f"Execution failed: {e}"}

    async def _aggregate_results(
        self, original_task: str, results: list[dict[str, str]]
    ) -> str:
        """Synthesizes all sub-agent reports into one final Markdown response."""
        await self._send_ui_update("Tamam reports compile aur merge kar raha hun...")

        combined = "\n\n".join(
            f"--- {r.get('role')} ({r.get('id')}) ---\n{r.get('result')}"
            for r in results
        )

        prompt = (
            f"Original Task: {original_task}\n\n"
            f"Sub-agent Reports:\n{combined}\n\n"
            "Synthesize these into a single, cohesive Markdown response. "
            'Begin with: "Sir Matloob, Swarm execution complete ho gayi hai. Yeh rahi final report:"\n'
            "Format code blocks properly. Highlight key information. "
            "Do NOT repeat sub-agent names unnecessarily."
        )
        try:
            return await _call_gemini(prompt)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Aggregation failed: %s", e)
            return f"Sir, final merge nahi ho saki. Raw output:\n\n{combined}"

    async def execute_swarm(self, user_intent: str) -> str:
        """
        Main entry point for Coordinator Mode.
        Called by UIBridgeListener when cmd_type == 'swarm'.
        """
        if not self.is_active:
            return "Sir, Swarm mode offline hai — GOOGLE_API_KEY .env mein set karein."

        try:
            # Stage 1: Decompose
            sub_tasks = await self._decompose_task(user_intent)
            if not sub_tasks:
                return "Sir, task decomposition fail ho gayi."

            # Enforce hard cap on parallel agents
            if len(sub_tasks) > self.MAX_AGENTS:
                await self._send_ui_update(
                    f"Resource cap: {len(sub_tasks)} tasks ko {self.MAX_AGENTS} tak limit kar diya."
                )
                sub_tasks = sub_tasks[: self.MAX_AGENTS]

            # Stage 2: Run all sub-agents in parallel
            agent_coros = [self._run_sub_agent(st) for st in sub_tasks]
            results: list[dict[str, str]] = list(
                await asyncio.gather(*agent_coros, return_exceptions=False)
            )

            # Stage 3: Aggregate
            final_report = await self._aggregate_results(user_intent, results)

            # Broadcast final report to UI
            asyncio.create_task(
                notify_ui("agent_message", {"text": final_report})
            )
            return final_report

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("SwarmCoordinator.execute_swarm critical failure: %s", e, exc_info=True)
            return "Sir, swarm coordination mein critical error aa gayi hai."


# Singleton — imported by agent_loops.py
swarm_coordinator = SwarmCoordinator()
