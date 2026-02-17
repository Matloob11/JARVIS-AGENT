"""
# jarvis_vision.py
Jarvis Vision Module

Handles screen perception by capturing screenshots and analyzing them using 
Google's Gemini multimodal AI model. 
"""

import os
import logging
import asyncio
from io import BytesIO
import pyautogui
from PIL import Image
import google.generativeai as genai
from livekit.agents import function_tool
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-VISION")

load_dotenv()

# Configure Google Generative AI
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


class ScreenPerceiver:
    """
    Handles capturing and analyzing screen content.
    """

    def __init__(self, model_name="gemini-flash-latest"):
        self.model_name = model_name
        self.model = genai.GenerativeModel(model_name)

    async def capture_screen(self) -> bytes:
        """
        Captures the current primary screen and returns it as PNG bytes.
        """
        try:
            # Capture using pyautogui
            screenshot = pyautogui.screenshot()

            # Save to BytesIO to avoid disk I/O if possible,
            # but we need it in a format Gemini accepts (PIL or bytes)
            buffered = BytesIO()
            screenshot.save(buffered, format="PNG")
            return buffered.getvalue()
        except Exception as e:
            logger.error("Error capturing screen: %s", e)
            raise

    async def analyze_content(self, prompt: str = "What is on my screen?") -> str:
        """
        Captures the screen and asks Gemini to analyze it based on the prompt.
        """
        try:
            logger.info("Capturing screen for analysis...")
            image_bytes = await self.capture_screen()

            # Convert bytes to PIL Image for the SDK
            image = Image.open(BytesIO(image_bytes))

            logger.info("Sending image to Gemini (%s) with prompt: %s",
                        self.model_name, prompt)

            # Generate response
            response = await asyncio.to_thread(
                self.model.generate_content,
                [prompt, image]
            )

            return response.text
        except Exception as e:
            logger.error("Error analyzing screen content: %s", e)
            return f"❌ Vision analysis error: {str(e)}"


# Global Instance
vision_system = ScreenPerceiver()


@function_tool
async def analyze_screen(query: str = "Describe what you see on my screen in detail.") -> str:
    """
    Captures a screenshot of your primary monitor and uses AI to describe or analyze it.
    Use this to ask questions about currently open windows, visible text, or graphical content.

    Example queries:
    - 'Meri screen par kya hai?'
    - 'What is the error message on my screen?'
    - 'Is there a WhatsApp window visible?'
    - 'Summarize the code visible in my editor.'
    """
    result = await vision_system.analyze_content(query)
    return f"👁️ Screen Analysis:\n{result}"
