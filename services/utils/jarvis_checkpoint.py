"""
# services/utils/jarvis_checkpoint.py
Checkpoint and State Management Module for JARVIS.
Handles periodic state serialization and recovery.
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Callable, Optional
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-CHECKPOINT")


class JarvisCheckpointManager:
    """
    Manages system state checkpoints.
    Allows for state recovery after failures or restarts.
    """
    def __init__(self, checkpoint_dir: Optional[str] = None):
        """Initializes checkpoint manager with a target directory."""
        if checkpoint_dir is None:
            checkpoint_dir = os.path.join(os.getcwd(), "backups", "checkpoints")
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    async def create_checkpoint(self, state: dict, label: str = "auto"):
        """Serializes current state to a JSON file."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"checkpoint_{label}_{timestamp}.json"
            filepath = os.path.join(self.checkpoint_dir, filename)

            def write_file():
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=4)

            await asyncio.to_thread(write_file)
            logger.info("Checkpoint created: %s", filename)
            return filepath
        except (IOError, OSError, ValueError) as e:
            logger.error("Failed to create checkpoint: %s", e)
            return None

    async def start_checkpoint_loop(self, get_state_func: Callable, interval: int = 300):
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
