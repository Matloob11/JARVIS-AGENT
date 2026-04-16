"""
# services/multimedia/jarvis_image_gen.py
Image generation service for JARVIS.
Used to create visuals using Hugging Face or Pollinations.
"""

import asyncio
import os
import time
from typing import Any

from huggingface_hub import InferenceClient

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.system.jarvis_file_server import get_local_ip
from services.utils.jarvis_bridge import notify_event
from services.utils.jarvis_config import config
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-IMAGE-GEN")


class JarvisImageGenerator:
    """
    Handles image generation requests utilizing Hugging Face API
    and Pollinations as a fallback.
    """

    def __init__(self):
        """Initializes the HF client if token is present."""
        self.hf_token = os.getenv("HF_TOKEN")
        self.client = InferenceClient(
            token=self.hf_token) if self.hf_token else None
        self.output_dir = os.path.join(config.shared_dir, "Generated_Images")
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_image(self, prompt: str, aspect_ratio: str = "1:1") -> dict[str, Any]:
        """
        Generates an image from a text prompt.
        """
        logger.info("Generating image for prompt: %s", prompt)

        if not self.hf_token or not self.client:
            logger.info("HF_TOKEN missing or client not initialized. Falling back to Pollinations.")
            return await self._generate_pollinations(prompt, aspect_ratio)

        try:
            # Use FLUX.1-schnell or common Stable Diffusion
            model = "stabilityai/stable-diffusion-xl-base-1.0" # More stable for free tier

            def sync_gen():
                return self.client.text_to_image(prompt, model=model)

            image = await asyncio.to_thread(sync_gen)

            timestamp = int(time.time())
            filename = f"gen_{timestamp}.png"
            filepath = os.path.join(self.output_dir, filename)

            image.save(filepath)

            local_ip = get_local_ip()
            # Since server root is now D:/, we need to include Jarvis_Shared in the URL path
            folder_name = os.path.basename(config.shared_dir)
            image_url = f"http://{local_ip}:8000/{folder_name}/Generated_Images/{filename}"

            # Notify UI about the new image
            await notify_event("intelligence_update", {
                "type": "image",
                "label": "GENERATED IMAGE",
                "url": image_url,
                "data": {
                    "prompt": prompt,
                    "filename": filename,
                },
            })

            return {
                "status": "success",
                "path": filepath,
                "url": image_url,
                "message": f"Sir, aapke liye image generate kar di hai: {filename}",
            }
        except (ValueError, RuntimeError, OSError) as e:
            logger.error("HF Generation failed: %s. Trying fallback.", e)
            return await self._generate_pollinations(prompt, aspect_ratio)

    async def _generate_pollinations(self, prompt: str, aspect_ratio: str) -> dict[str, Any]:
        """Fallback: Pollinations.ai (No Token Required)"""
        import requests
        from urllib.parse import quote

        width, height = self._get_dimensions(aspect_ratio)
        safe_prompt = quote(prompt)
        # Use newer image.pollinations.ai endpoint
        url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width={width}&height={height}&seed={int(time.time())}&nologo=true"

        try:
            logger.info("Polling Pollinations.ai: %s", url)
            # Increase timeout to 60s for Pollinations
            response = await asyncio.to_thread(requests.get, url, timeout=60)
            if response.status_code == 200:
                timestamp = int(time.time())
                filename = f"poll_{timestamp}.jpg"
                filepath = os.path.join(self.output_dir, filename)

                with open(filepath, "wb") as f:
                    f.write(response.content)

                local_ip = get_local_ip()
                folder_name = os.path.basename(config.shared_dir)
                local_url = f"http://{local_ip}:8000/{folder_name}/Generated_Images/{filename}"

                # Notify UI about fallback image
                await notify_event("intelligence_update", {
                    "type": "image",
                    "label": "GENERATED IMAGE (Fallback)",
                    "url": local_url,
                    "data": {"prompt": prompt, "filename": filename},
                })

                return {
                    "status": "success",
                    "path": filepath,
                    "url": local_url,
                    "message": f"Sir, image generate ho gayi hai (Pollinations): {filename}",
                }
        except (requests.RequestException, OSError) as e:
            logger.error("Pollinations fallback failed: %s", e)

        return {
            "status": "error",
            "message": "Maazrat Sir, dono systems image generate nahi kar paaye.",
        }

    def _get_dimensions(self, ratio: str):
        """Maps aspect ratio string to pixels."""
        mapping = {
            "1:1": (1024, 1024),
            "16:9": (1280, 720),
            "4:3": (1024, 768),
            "9:16": (720, 1280),
        }
        return mapping.get(ratio, (1024, 1024))

    def get_service_info(self) -> dict[str, Any]:
        """Returns metadata about the image generation service."""
        return {
            "output_directory": self.output_dir,
            "hf_token_configured": self.hf_token is not None,
            "supported_ratios": ["1:1", "16:9", "4:3", "9:16"],
        }


# Global Singleton
image_gen = JarvisImageGenerator()


@jarvis_tool(execution_timeout=60.0)
async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> dict[str, Any]:
    """
    Generates an image from a text prompt.
    User can specify aspect ratios like '1:1', '16:9', '4:3', '9:16'.
    """
    return await image_gen.generate_image(prompt, aspect_ratio)
