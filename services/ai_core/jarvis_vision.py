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
import cv2  # Added for local webcam support
import requests
from google import genai
from openai import OpenAI  # Used for Groq's OpenAI-compatible API
from dotenv import load_dotenv
from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-VISION")

load_dotenv()

# Global clients (lazy initialized where needed)
_google_client = None

def get_google_client():
    """Lazily initializes and returns the Google GenAI client."""
    global _google_client # pylint: disable=global-statement
    if _google_client is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("GOOGLE_API_KEY missing in environment.")
            return None
        _google_client = genai.Client(api_key=api_key)
    return _google_client

def get_groq_client():
    """Lazily initializes and returns the Groq OpenAI client."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1"
    )


class ScreenPerceiver:
    """
    Handles capturing and analyzing screen content.
    """

    def __init__(self, model_name="gemini-2.0-flash"):
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
        except (pyautogui.ImageNotFoundException, OSError, IOError) as e:
            logger.error("Error capturing screen: %s", e)
            raise

    async def capture_webcam(self) -> bytes:
        """
        Captures a frame from the local webcam using OpenCV.
        Returns JPEG bytes.
        """
        try:
            # Run in thread to avoid blocking loop
            def do_capture():
                # pylint: disable=no-member
                cap = cv2.VideoCapture(0)
                if not cap.isOpened():
                    return None
                ret, frame = cap.read()
                cap.release()
                if not ret:
                    return None
                # Convert BGR to RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # pylint: enable=no-member
                img = Image.fromarray(rgb_frame)
                buffered = BytesIO()
                img.save(buffered, format="JPEG")
                return buffered.getvalue()

            data = await asyncio.to_thread(do_capture)
            if data is None:
                raise IOError("Could not capture from webcam. Check if your camera is connected and available.")
            return data
        except Exception as e:
            logger.error("Error capturing webcam: %s", e)
            raise

    async def analyze_via_google(self, prompt: str, image: Image.Image) -> str:
        """Attempts analysis via native Google SDK."""
        google_client = get_google_client()
        if not google_client:
            raise ValueError("Google GenAI client not initialized. Check GOOGLE_API_KEY.")

        logger.info("Sending image to Gemini (%s) with prompt: %s",
                    self.model_name, prompt)
        try:
            response = await asyncio.to_thread(
                google_client.models.generate_content,
                model=self.model_name,
                contents=[prompt, image]
            )
            return str(response.text) if response.text else "No analysis found."
        except Exception as e:
            logger.error("Google Vision API error: %s", e)
            raise

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

    async def analyze_via_groq(self, prompt: str, image: Image.Image) -> str:
        """Analysis via Groq Llama 3.2 Vision."""
        groq_client = get_groq_client()
        if not groq_client:
            return "Error: Groq client not initialized."

        logger.info("Attempting analysis via Groq (Llama 3.2 Vision)...")
        try:
            # Convert PIL to base64
            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

            response = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}"}}
                    ]
                }],
                max_tokens=1024
            )
            return response.choices[0].message.content
        except (RuntimeError, ValueError, IOError) as e:
            logger.error("Groq Vision API error: %s", e)
            return f"Error: Groq failed: {str(e)}"

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
                # Catch API errors (google-genai APIError) and quota issues
                msg = str(e).upper()
                if any(err in msg for err in ["429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "500", "APIERROR", "QUOTA", "EXCEPTION"]):
                    logger.warning(
                        "Gemini error (%s). Falling back to Groq...", msg)

                    # Try Secondary: Groq
                    groq_result = await self.analyze_via_groq(prompt, image)
                    if not groq_result.startswith("Error"):
                        return groq_result

                    # Try Tertiary: OpenRouter
                    logger.warning("Groq failed. Falling back to OpenRouter...")
                    return await self.analyze_via_openrouter(prompt, image)
                raise e

        except (OSError, IOError, ValueError) as e:
            logger.error("Error in vision system: %s", e)
            return f"Error: Vision analysis failed: {str(e)}"


# Global Instance
vision_system = ScreenPerceiver()


@jarvis_tool
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
    except (OSError, IOError, RuntimeError) as e:
        logger.error("Vision tool error: %s", e)
        return {
            "status": "error",
            "message": f"👁️ Vision analysis failed: {str(e)}",
            "error": str(e)
        }


@jarvis_tool
async def analyze_camera(query: str = "What do you see in the camera?") -> dict:
    """
    Captures a frame from your local webcam and uses AI to describe it.
    Use this to ask questions about your surroundings or objects held in front of the camera.
    """
    try:
        logger.info("Capturing webcam for analysis...")
        image_bytes = await vision_system.capture_webcam()
        image = Image.open(BytesIO(image_bytes))

        # Try Primary: Gemini
        try:
            result = await vision_system.analyze_via_google(query, image)
        except Exception as e: # pylint: disable=broad-exception-caught
            msg = str(e).upper()
            if any(err in msg for err in ["429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND", "500", "APIERROR", "QUOTA", "EXCEPTION"]):
                logger.warning("Gemini error in camera. Falling back to Groq...")

                # Try Groq
                result = await vision_system.analyze_via_groq(query, image)
                if result.startswith("Error"):
                    logger.warning("Groq camera failed. Falling back to OpenRouter...")
                    result = await vision_system.analyze_via_openrouter(query, image)
            else:
                raise e

        if result.startswith("Error"):
            return {"status": "error", "message": f"📷 Camera analysis failed: {result}"}

        return {
            "status": "success",
            "query": query,
            "message": f"📷 Camera view analysis, Sir:\n{result}"
        }
    except (OSError, IOError, RuntimeError, ValueError) as e:
        logger.error("Camera tool error: %s", e)
        return {
            "status": "error",
            "message": f"📷 Camera analysis failed: {str(e)}",
            "error": str(e)
        }
