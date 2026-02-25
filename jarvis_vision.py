"""
# jarvis_vision.py
Jarvis Vision Module

Handles screen perception by capturing screenshots and analyzing them using 
Google's Gemini multimodal AI model. 
"""

import os
import asyncio
import base64
from io import BytesIO
import pyautogui
from PIL import Image
import requests
from google import genai
from livekit.agents import function_tool
from dotenv import load_dotenv
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-VISION")

load_dotenv()

# Configure Google Generative AI Client
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


class ScreenPerceiver:
    """
    Handles capturing and analyzing screen content.
    """

    def __init__(self, model_name="models/gemini-2.5-flash-native-audio-latest"):
        self.model_name = model_name

    async def capture_screen(self) -> bytes:
        """
        Captures the current primary screen and returns it as PNG bytes.
        """
        try:
            # Capture using pyautogui in a thread to avoid blocking
            screenshot = await asyncio.to_thread(pyautogui.screenshot)

            # Save to BytesIO to avoid disk I/O if possible,
            # but we need it in a format Gemini accepts (PIL or bytes)
            buffered = BytesIO()
            screenshot.save(buffered, format="PNG")
            return buffered.getvalue()
        except Exception as e:
            logger.error("Error capturing screen: %s", e)
            raise

    async def analyze_via_google(self, prompt: str, image: Image.Image) -> str:
        """Attempts analysis via native Google SDK."""
        logger.info("Sending image to Gemini (%s) with prompt: %s",
                    self.model_name, prompt)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.model_name,
            contents=[prompt, image]
        )
        return str(response.text) if response.text else "No analysis found."

    async def analyze_via_openrouter(self, prompt: str, image: Image.Image) -> str:
        """Fallback analysis via OpenRouter (NVIDIA Nemotron)."""
        logger.info("Attempting fallback via OpenRouter (Nemotron)...")
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            return "Error: OpenRouter API key missing for fallback."

        try:
            # Convert PIL to base64
            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Matloob11/JARVIS-AGENT",
                "X-Title": "JARVIS Agent"
            }

            payload = {
                "model": "nvidia/nemotron-nano-12b-v2-vl:free",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"}}
                    ]
                }]
            }

            response = await asyncio.to_thread(
                requests.post, url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            return f"OpenRouter Error: {response.text}"
        except (requests.RequestException, ValueError, KeyError) as e:
            return f"Fallback failed: {str(e)}"

    async def analyze_content(self, prompt: str = "What is on my screen?") -> str:
        """
        Captures the screen and uses tiered providers to analyze it.
        """
        try:
            logger.info("Capturing screen for analysis...")
            image_bytes = await self.capture_screen()
            image = Image.open(BytesIO(image_bytes))

            # Try Primary: Gemini
            try:
                return await self.analyze_via_google(prompt, image)
            except Exception as e:
                # Catch Quota or Rate Limit errors specifically if possible
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    logger.warning(
                        "Gemini quota exhausted. Falling back to OpenRouter...")
                    return await self.analyze_via_openrouter(prompt, image)
                raise e

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error in vision system: %s", e)
            return f"Error: Vision analysis failed: {str(e)}"


# Global Instance
vision_system = ScreenPerceiver()


@function_tool
async def analyze_screen(query: str = "Describe what you see on my screen in detail.") -> dict:
    """
    Captures a screenshot of your primary monitor and uses AI to describe or analyze it.
    Use this to ask questions about currently open windows, visible text, or graphical content.
    """
    try:
        result = await vision_system.analyze_content(query)
        if result.startswith("Error"):
            return {
                "status": "error",
                "message": f"👁️ Vision analysis failed: {result}"
            }
        return {
            "status": "success",
            "query": query,
            "message": f"👁️ Screen Analysis report taiyyar hai, Sir:\n{result}"
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Vision tool error: %s", e)
        return {
            "status": "error",
            "message": f"👁️ Vision analysis failed: {str(e)}",
            "error": str(e)
        }
