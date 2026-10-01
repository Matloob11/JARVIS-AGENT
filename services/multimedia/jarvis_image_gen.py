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
from services.utils.jarvis_security import redact_secrets

logger = setup_logger("JARVIS-IMAGE-GEN")

_DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"
_DEFAULT_PROVIDER = "auto"
_POLLINATIONS_MODEL = "pollinations"


class JarvisImageGenerator:
    """
    Handles image generation requests utilizing Hugging Face API
    and Pollinations as a fallback.
    """

    def __init__(self):
        """Initializes the HF client if token is present."""
        self.hf_token = (os.getenv("HF_TOKEN") or "").strip() or None
        self.image_model = (
            (os.getenv("JARVIS_IMAGE_MODEL") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
        )
        self.image_provider = (
            (os.getenv("JARVIS_IMAGE_PROVIDER") or _DEFAULT_PROVIDER).strip()
            or _DEFAULT_PROVIDER
        )
        self.pollinations_token = (os.getenv("POLLINATIONS_TOKEN") or "").strip() or None
        self.client: InferenceClient | None = None
        if self.hf_token:
            self.client = InferenceClient(
                provider=self.image_provider,
                api_key=self.hf_token,
            )
        shared = (config.shared_dir or "").strip() or os.path.join(
            os.path.expanduser("~"), "Jarvis_Shared"
        )
        self.output_dir = os.path.join(shared, "Generated_Images")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as exc:
            fallback = os.path.join(
                os.path.expanduser("~"), "Jarvis_Shared", "Generated_Images"
            )
            logger.warning(
                "Image output dir %s unavailable (%s); falling back to %s",
                self.output_dir,
                exc,
                fallback,
            )
            self.output_dir = fallback
            os.makedirs(self.output_dir, exist_ok=True)

    def _lan_image_url(self, filepath: str) -> str:
        """Build a LAN URL relative to the file-server root (when the server is running)."""
        root = getattr(config, "file_server_root", None) or config.shared_dir
        try:
            rel = os.path.relpath(filepath, root).replace("\\", "/")
        except ValueError:
            rel = f"{os.path.basename(config.shared_dir)}/Generated_Images/{os.path.basename(filepath)}"
        local_ip = get_local_ip()
        return f"http://{local_ip}:8000/{rel}"

    async def _emit_image_update(
        self,
        *,
        label: str,
        url: str,
        prompt: str,
        filename: str,
        provider: str,
        model: str,
    ) -> None:
        meta = {"prompt": prompt, "provider": provider, "model": model}
        await notify_event(
            "intelligence_update",
            {
                "type": "image",
                "label": label,
                "url": url,
                "data": {"filename": filename, **meta},
                "meta": meta,
            },
        )
        try:
            import services.utils.jarvis_bridge as bridge  # pylint: disable=import-outside-toplevel

            upload_media = getattr(bridge, "upload_media", None)
            if callable(upload_media):
                await upload_media(url, meta)
        except (ImportError, TypeError, RuntimeError, OSError, ValueError) as exc:
            logger.debug("upload_media relay skipped: %s", exc)

    async def generate_image(self, prompt: str, aspect_ratio: str = "1:1") -> dict[str, Any]:
        """
        Generates an image from a text prompt.
        """
        logger.info(
            "Generating image (model=%s, provider=%s) for prompt: %s",
            self.image_model,
            self.image_provider,
            prompt,
        )

        if not self.hf_token or not self.client:
            logger.info(
                "HF_TOKEN missing or client not initialized. Falling back to Pollinations."
            )
            return await self._generate_pollinations(prompt, aspect_ratio)

        try:

            def sync_gen():
                return self.client.text_to_image(prompt, model=self.image_model)

            image = await asyncio.to_thread(sync_gen)
            timestamp = int(time.time())
            filename = f"gen_{timestamp}.png"
            filepath = os.path.join(self.output_dir, filename)
            image.save(filepath)
            image_url = self._lan_image_url(filepath)

            await self._emit_image_update(
                label="GENERATED IMAGE",
                url=image_url,
                prompt=prompt,
                filename=filename,
                provider=self.image_provider,
                model=self.image_model,
            )

            return {
                "status": "success",
                "path": filepath,
                "url": image_url,
                "provider": self.image_provider,
                "model": self.image_model,
                "message": f"Sir, aapke liye image generate kar di hai: {filename}",
            }
        except Exception as exc:  # noqa: BLE001 — HF + provider errors vary by version
            logger.error(
                "HF generation failed: %s. Trying Pollinations fallback.",
                redact_secrets(str(exc)),
            )
            return await self._generate_pollinations(prompt, aspect_ratio)

    async def _generate_pollinations(self, prompt: str, aspect_ratio: str) -> dict[str, Any]:
        """Fallback: Pollinations.ai"""
        import requests
        from urllib.parse import quote

        width, height = self._get_dimensions(aspect_ratio)
        safe_prompt = quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{safe_prompt}"
            f"?width={width}&height={height}&seed={int(time.time())}&nologo=true"
        )
        headers: dict[str, str] = {}
        if self.pollinations_token:
            headers["Authorization"] = f"Bearer {self.pollinations_token}"

        try:
            logger.info("Polling Pollinations.ai: %s", url)
            response = await asyncio.to_thread(
                requests.get, url, timeout=60, headers=headers or None
            )
            status = response.status_code
            content_type = (response.headers.get("Content-Type") or "").lower()
            if status != 200:
                return {
                    "status": "error",
                    "provider": "pollinations",
                    "model": self.image_model,
                    "message": (
                        f"Maazrat Sir, Pollinations ne image nahi di (HTTP {status})."
                    ),
                }
            if not content_type.startswith("image/"):
                return {
                    "status": "error",
                    "provider": "pollinations",
                    "model": self.image_model,
                    "message": (
                        "Maazrat Sir, Pollinations ne image nahi di "
                        f"(HTTP {status}, Content-Type: {content_type or 'missing'})."
                    ),
                }

            timestamp = int(time.time())
            ext = "jpg" if "jpeg" in content_type or "jpg" in content_type else "png"
            filename = f"poll_{timestamp}.{ext}"
            filepath = os.path.join(self.output_dir, filename)

            with open(filepath, "wb") as f:
                f.write(response.content)

            local_url = self._lan_image_url(filepath)

            await self._emit_image_update(
                label="GENERATED IMAGE (Fallback)",
                url=local_url,
                prompt=prompt,
                filename=filename,
                provider="pollinations",
                model=_POLLINATIONS_MODEL,
            )

            return {
                "status": "success",
                "path": filepath,
                "url": local_url,
                "provider": "pollinations",
                "model": _POLLINATIONS_MODEL,
                "message": (
                    f"Sir, image fallback se generate ho gayi hai (Pollinations): {filename}"
                ),
            }
        except (requests.RequestException, OSError) as exc:
            logger.error("Pollinations fallback failed: %s", redact_secrets(str(exc)))

        return {
            "status": "error",
            "provider": "pollinations",
            "model": self.image_model,
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
            "image_model": self.image_model,
            "image_provider": self.image_provider,
            "supported_ratios": ["1:1", "16:9", "4:3", "9:16"],
        }


# Global Singleton
image_gen = JarvisImageGenerator()


@jarvis_tool(execution_timeout=150.0)
async def generate_image(prompt: str, aspect_ratio: str = "1:1") -> dict[str, Any]:
    """
    Generates an image from a text prompt.
    User can specify aspect ratios like '1:1', '16:9', '4:3', '9:16'.
    """
    return await image_gen.generate_image(prompt, aspect_ratio)
