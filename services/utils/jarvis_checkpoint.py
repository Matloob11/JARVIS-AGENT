"""
# services/utils/jarvis_checkpoint.py
Checkpoint and State Management Module for JARVIS.
Handles periodic state serialization and recovery.
"""

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-CHECKPOINT")


class JarvisCheckpointManager:
    """
    Manages system state checkpoints.
    Allows for state recovery after failures or restarts.
    """
    def __init__(self, checkpoint_dir: str | None = None) -> None:
        """Initializes checkpoint manager with a target directory."""
        if checkpoint_dir is None:
            checkpoint_path = Path.cwd() / "backups" / "checkpoints"
        else:
            checkpoint_path = Path(checkpoint_dir)
        self.checkpoint_dir = str(checkpoint_path)
        checkpoint_path.mkdir(parents=True, exist_ok=True)

    async def create_checkpoint(self, state: dict[str, Any], label: str = "auto") -> str | None:
        """Serializes current state to a JSON file."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"checkpoint_{label}_{timestamp}.json"
            filepath = str(Path(self.checkpoint_dir) / filename)

            def write_file():
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=4)

            await asyncio.to_thread(write_file)
            logger.info("Checkpoint created: %s", filename)
            return filepath
        except (OSError, ValueError) as e:
            logger.error("Failed to create checkpoint: %s", e)
            return None

    async def start_checkpoint_loop(self, get_state_func: Callable[[], Coroutine[Any, Any, dict[str, Any]]], interval: int = 300) -> None:
        """Continuous background loop for periodic state saving."""
        logger.info("🔱 Checkpoint loop active. Interval: %ds", interval)
        while True:
            try:
                state = await get_state_func()
                await self.create_checkpoint(state)
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.info("Checkpoint loop stopping...")
                raise
            except (ValueError, OSError, RuntimeError) as e:  # pylint: disable=broad-exception-caught
                logger.error("Error in checkpoint loop: %s", e)

            await asyncio.sleep(interval)


# Global instance
checkpoint_manager = JarvisCheckpointManager()
