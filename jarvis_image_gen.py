"""
# jarvis_image_gen.py
Jarvis Image Generation Module
Primary: Hugging Face Inference API (FLUX.1-schnell)
Fallback: Pollinations.ai (Synchronous)
"""

import os
import requests
import re
from datetime import datetime
from dotenv import load_dotenv
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-IMAGE-GEN")

load_dotenv()


def generate_via_hf(prompt: str) -> str:
    """Primary high-quality path using Hugging Face InferenceClient."""
    logger.info("Attempting generation via Hugging Face (FLUX)...")
    token = os.getenv("HF_TOKEN")
    if not token:
        logger.warning("HF_TOKEN not found. Skipping HF path.")
        return None

    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=token)
        # Using FLUX.1-schnell for top-tier quality and speed
        model_id = "black-forest-labs/FLUX.1-schnell"

        image = client.text_to_image(prompt, model=model_id)

        image_dir = os.path.join(
            os.getcwd(), "Jarvis_Outputs", "Generated_Images")
        os.makedirs(image_dir, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"gen_hf_{timestamp}.png"
        filepath = os.path.join(image_dir, filename)

        image.save(filepath)
        os.startfile(filepath)
        logger.info("HF Image saved and opened: %s", filepath)
        return f"Success: Image generated via Hugging Face and saved to {filepath}"
    except Exception as e:
        logger.warning("Hugging Face attempt failed: %s", str(e))
    return None


def generate_via_pollinations(prompt: str) -> str:
    """Fallback fast-path using Pollinations.ai."""
    logger.info("Attempting generation via Pollinations fallback...")

    # Aggressive distillation for stability (Pollinations WAF bypass)
    words = [w for w in re.sub(
        r'[^a-zA-Z0-9\s]', '', prompt).split() if len(w) > 2]
    short_prompt = " ".join(words[:5])

    safe_prompt = requests.utils.quote(short_prompt)
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?nologo=true"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://pollinations.ai/"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200 and response.content.startswith((b'\xff\xd8\xff', b'\x89PNG', b'RIFF')):
            image_dir = os.path.join(
                os.getcwd(), "Jarvis_Outputs", "Generated_Images")
            os.makedirs(image_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"gen_poll_{timestamp}.jpg"
            filepath = os.path.join(image_dir, filename)

            with open(filepath, "wb") as f:
                f.write(response.content)
            os.startfile(filepath)
            logger.info(
                "Pollinations Image saved and opened: %s", filepath)
            return f"Success: Image generated via Pollinations for '{short_prompt}'. Saved to {filepath}"
    except Exception as e:
        logger.warning("Pollinations fallback failed: %s", str(e))
    return None


def generate_image(prompt: str) -> str:
    """Main entry point with multi-provider failover."""
    logger.info("Generating image with prompt: %s", prompt)

    # 1. Try Hugging Face (FLUX) - Best quality
    result = generate_via_hf(prompt)
    if result:
        return result

    # 2. Try Pollinations (Synchronous Fallback)
    result = generate_via_pollinations(prompt)
    if result:
        return result

    return "Error: Image generation failed on all available free providers. Please try again later."


# Tool integration for JARVIS agent
try:
    from livekit.agents import function_tool

    @function_tool
    def tool_generate_image(prompt: str) -> str:
        """
        Generates an image based on a description. Use this when the user asks to 'draw', 'generate an image', or 'show an image of'.
        """
        return generate_image(prompt)
except ImportError:
    pass
