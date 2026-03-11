"""
# services/utils/jarvis_config.py
Centralized configuration loader for JARVIS-AGENT.
Handles environment variables and application mappings.
"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger("JARVIS-CONFIG")


# pylint: disable=too-many-instance-attributes
class JarvisConfig:
    """
    Centralized configuration loader for JARVIS-AGENT.
    Supports .env with production/development toggles.
    """

    def __init__(self):
        """Loads environment variables and sets up configuration attributes."""
        # Load environment variables from .env
        load_dotenv()
        self.env = os.getenv("VORTEX_ENV", "development").lower()
        self.security_token = os.getenv("VORTEX_SECURITY_TOKEN")
        self.bridge_url = os.getenv("UI_BRIDGE_URL", "http://127.0.0.1:5001")

        # LLM & Search API Keys
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.google_search_key = os.getenv("GOOGLE_SEARCH_API_KEY")
        self.search_engine_id = os.getenv("SEARCH_ENGINE_ID")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.hf_token = os.getenv("HF_TOKEN")

        # LiveKit
        self.livekit_url = os.getenv("LIVEKIT_URL")
        self.livekit_key = os.getenv("LIVEKIT_API_KEY")
        self.livekit_secret = os.getenv("LIVEKIT_API_SECRET")

        # Email
        self.email_user = os.getenv("EMAIL_USER")
        self.email_password = os.getenv("EMAIL_APP_PASSWORD")

        # Weather
        self.weather_api_key = os.getenv("WEATHER_API_KEY")

        # User Info
        self.user_name = os.getenv("USER_NAME", "Sir Matloob")
        self.user_city = os.getenv("USER_CITY", "Lahore")

        # Logging
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.log_file = os.getenv("LOG_FILE", "logs/jarvis.log")

        # Network
        origins = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
        )
        self.allowed_origins = origins.split(",")

    def is_production(self):
        """Returns True if the environment is set to production."""
        return self.env == "production"

    def validate(self) -> bool:
        """Mandatory validation for critical keys."""
        critical_keys = {
            "GOOGLE_API_KEY": self.google_api_key,
            "VORTEX_SECURITY_TOKEN": self.security_token,
            "LIVEKIT_API_KEY": self.livekit_key,
            "LIVEKIT_API_SECRET": self.livekit_secret
        }

        missing = [k for k, v in critical_keys.items() if not v]

        if missing:
            logger.critical("❌ CRITICAL CONFIG MISSING: %s",
                            ', '.join(missing))
            if self.is_production():
                raise RuntimeError(
                    f"Production safety check failed: Missing {missing}")

        return not missing


# Global singleton instance
config = JarvisConfig()

# 📱 Application Mappings (App Name -> Path or URL)
APP_MAPPINGS = {
    "notepad": "notepad.exe",
    "chrome": "https://www.google.com",
    "edge": "msedge",
    "calculator": "calc",
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "vlc": "vlc",
    "excel": "excel",
    "word": "winword",
    "powerpoint": "powerpnt"
}

# 🎯 Focus Titles (App Name -> Window Title Fragment)
FOCUS_TITLES = {
    "notepad": " - Notepad",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "whatsapp": "WhatsApp",
    "youtube": "YouTube",
    "vlc": "VLC media player",
    "calculator": "Calculator"
}
