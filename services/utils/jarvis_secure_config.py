"""
J.A.R.V.I.S Secure Configuration Manager
Handles loading and management of encrypted configuration
"""

import os

# import logging (Removed as unused)
from services.utils.jarvis_crypto import JarvisCrypto
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-SECURE-CONFIG")


class SecureConfigManager:
    """
    Secure configuration manager for JARVIS
    Handles encrypted API keys and sensitive configuration
    """

    def __init__(self, encrypted_env_file: str = ".env.encrypted"):
        """
        Initialize secure config manager

        Args:
            encrypted_env_file: Path to encrypted environment file
        """
        self.encrypted_env_file = encrypted_env_file
        self._uses_default_file = os.path.normcase(os.path.abspath(encrypted_env_file)) == os.path.normcase(
            os.path.abspath(".env.encrypted")
        )
        self._config_cache: dict[str, str] = {}
        self._crypto = JarvisCrypto()

        # Load configuration on initialization
        self._load_configuration()

    def _load_configuration(self) -> None:
        """Load and decrypt configuration from encrypted file"""
        try:
            # Try to load from encrypted file first
            if os.path.exists(self.encrypted_env_file):
                self._config_cache = self._crypto.load_encrypted_env(self.encrypted_env_file)
                logger.info("🔓 Loaded configuration from encrypted file")

            # Fallback to regular .env if encrypted doesn't exist
            elif self._uses_default_file and os.path.exists(".env"):
                logger.warning("⚠️ Using unencrypted .env file. Consider encrypting it.")
                self._load_plain_env()

            # Load from environment variables as final fallback
            if self._uses_default_file:
                self._load_from_environment()

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to load configuration: %s", e)
            self._config_cache = {}

    def _load_plain_env(self) -> None:
        """Load from plain .env file"""
        try:
            with open(".env", encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        self._config_cache[key.strip()] = value.strip()
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to load plain .env file: %s", e)

    def _load_from_environment(self) -> None:
        """Load configuration from environment variables"""
        # Important JARVIS configuration keys
        jarvis_keys = [
            'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'LIVEKIT_URL',
            'GOOGLE_API_KEY', 'WEATHER_API_KEY', 'OPENWEATHER_API_KEY',
            'GOOGLE_SEARCH_API_KEY', 'SEARCH_ENGINE_ID', 'USER_NAME',
            'CONTROLLER_TOKEN', 'TAVILY_API_KEY', 'JARVIS_ENCRYPTION_KEY',
        ]

        for key in jarvis_keys:
            if key in os.environ and key not in self._config_cache:
                self._config_cache[key] = os.environ[key]

    def get(self, key: str, default: str | None = None) -> str | None:
        """
        Get configuration value

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        return self._config_cache.get(key, default)

    def get_required(self, key: str) -> str:
        """
        Get required configuration value

        Args:
            key: Configuration key

        Returns:
            Configuration value

        Raises:
            ValueError: If required key is not found
        """
        value = self.get(key)
        if value is None:
            raise ValueError(f"Required configuration key '{key}' not found")
        return value

    def get_livekit_config(self) -> dict[str, str]:
        """Get LiveKit configuration"""
        return {
            'api_key': self.get_required('LIVEKIT_API_KEY'),
            'api_secret': self.get_required('LIVEKIT_API_SECRET'),
            'url': self.get_required('LIVEKIT_URL'),
        }

    def get_google_config(self) -> dict[str, str]:
        """Get Google API configuration"""
        return {
            'api_key': self.get_required('GOOGLE_API_KEY'),
            'search_api_key': self.get('GOOGLE_SEARCH_API_KEY') or '',
            'search_engine_id': self.get('SEARCH_ENGINE_ID') or '',
        }

    def get_weather_config(self) -> dict[str, str]:
        """Get weather API configuration"""
        api_key = self.get('WEATHER_API_KEY') or self.get('OPENWEATHER_API_KEY')
        if not api_key:
            self.get_required('WEATHER_API_KEY')
        return {
            'api_key': api_key or '',
        }

    def get_user_config(self) -> dict[str, str]:
        """Get user-specific configuration"""
        return {
            'name': self.get('USER_NAME', 'User') or 'User',
            'controller_token': self.get('CONTROLLER_TOKEN') or '',
            'tavily_api_key': self.get('TAVILY_API_KEY') or '',
        }

    def validate_configuration(self) -> dict[str, bool]:
        """
        Validate required configuration

        Returns:
            Dictionary with validation results
        """
        required_keys = [
            'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'LIVEKIT_URL',
            'GOOGLE_API_KEY', 'WEATHER_API_KEY',
        ]

        validation_results = {}
        for key in required_keys:
            validation_results[key] = bool(self.get(key))
        if not validation_results['WEATHER_API_KEY']:
            validation_results['WEATHER_API_KEY'] = bool(self.get('OPENWEATHER_API_KEY'))

        # Overall validation status
        all_valid = all(validation_results.values())
        validation_results['overall'] = all_valid

        if not all_valid:
            missing = [k for k, v in validation_results.items() if not v and k != 'overall']
            logger.warning("⚠️ Missing required configuration: %s", missing)
        else:
            logger.info("✅ All required configuration present")

        return validation_results

    def encrypt_current_env(self, env_file: str = ".env") -> bool:
        """
        Encrypt current .env file

        Args:
            env_file: Path to .env file to encrypt

        Returns:
            True if successful, False otherwise
        """
        try:
            encrypted_file = self._crypto.encrypt_env_file(env_file)
            if encrypted_file:
                # Update the encrypted file path
                self.encrypted_env_file = encrypted_file
                # Reload configuration
                self._load_configuration()
                logger.info("🔐 Environment file encrypted successfully")
                return True
            return False
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to encrypt environment file: %s", e)
            return False

    def reload(self) -> None:
        """Reload configuration from files"""
        logger.info("🔄 Reloading configuration...")
        self._config_cache.clear()
        self._load_configuration()


# Global secure configuration instance
_secure_config: SecureConfigManager | None = None

def get_secure_config() -> SecureConfigManager:
    """
    Get global secure configuration manager

    Returns:
        SecureConfigManager instance
    """
    global _secure_config # pylint: disable=global-statement
    if _secure_config is None:
        _secure_config = SecureConfigManager()
    return _secure_config

def initialize_secure_config(encrypted_env_file: str = ".env.encrypted") -> SecureConfigManager:
    """
    Initialize secure configuration manager

    Args:
        encrypted_env_file: Path to encrypted environment file

    Returns:
        SecureConfigManager instance
    """
    global _secure_config # pylint: disable=global-statement
    _secure_config = SecureConfigManager(encrypted_env_file)
    return _secure_config
