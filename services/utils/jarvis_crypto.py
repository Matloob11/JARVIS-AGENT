"""
J.A.R.V.I.S Cryptographic Security Module
Enhanced with API key encryption and secure environment management
"""

import os
import json
import logging
from typing import Dict, Optional, Union
from cryptography.fernet import Fernet

logger = logging.getLogger("JARVIS-CRYPTO")


class JarvisCrypto:
    """
    Enhanced cryptographic security for JARVIS sensitive data
    Handles API keys, environment variables, and sensitive data
    """
    _key: bytes = None
    _fernet: Fernet = None

    @classmethod
    def get_key(cls) -> bytes:
        """Retrieve the encryption key from environment or generate one."""
        if cls._key:
            return cls._key

        key = os.getenv("JARVIS_ENCRYPTION_KEY")
        if not key:
            logger.warning("No encryption key found. Generating session key.")
            key = Fernet.generate_key().decode()

        cls._key = key.encode() if isinstance(key, str) else key
        return cls._key

    @classmethod
    def encrypt(cls, data: Union[str, bytes]) -> bytes:
        """Encrypt data using AES-256."""
        f = Fernet(cls.get_key())
        if isinstance(data, str):
            data = data.encode()
        return f.encrypt(data)

    @classmethod
    def decrypt(cls, token: Union[str, bytes]) -> str:
        """Decrypt data back to string."""
        f = Fernet(cls.get_key())
        return f.decrypt(token).decode()

    @classmethod
    def encrypt_env_file(cls, env_file_path: str = ".env") -> str:
        """
        Encrypt environment file with API keys

        Args:
            env_file_path: Path to .env file

        Returns:
            Path to encrypted file
        """
        try:
            if not os.path.exists(env_file_path):
                logger.warning("⚠️ Environment file %s not found", env_file_path)
                return ""

            # Read and parse env file
            env_data = {}
            with open(env_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env_data[key.strip()] = value.strip()

            # Identify and encrypt sensitive keys
            sensitive_patterns = [
                'API_KEY', 'API_SECRET', 'PASSWORD', 'TOKEN',
                'SECRET', 'KEY', 'PRIVATE', 'CREDENTIALS'
            ]

            encrypted_data = {}
            for key, value in env_data.items():
                if any(pattern in key.upper() for pattern in sensitive_patterns):
                    encrypted_data[key] = cls.encrypt(value).decode()
                else:
                    encrypted_data[key] = value

            # Save encrypted file
            encrypted_file_path = env_file_path + ".encrypted"
            with open(encrypted_file_path, 'w', encoding='utf-8') as f:
                json.dump(encrypted_data, f, indent=2)

            logger.info("🔐 Environment file encrypted: %s", encrypted_file_path)
            return encrypted_file_path

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to encrypt environment file: %s", e)
            raise

    @classmethod
    def load_encrypted_env(cls, encrypted_file_path: str = ".env.encrypted") -> Dict[str, str]:
        """
        Load and decrypt environment variables from encrypted file

        Args:
            encrypted_file_path: Path to encrypted env file

        Returns:
            Dictionary of decrypted environment variables
        """
        try:
            if not os.path.exists(encrypted_file_path):
                logger.warning("⚠️ Encrypted environment file not found: %s", encrypted_file_path)
                return {}

            # Load encrypted data
            with open(encrypted_file_path, 'r', encoding='utf-8') as f:
                encrypted_data = json.load(f)

            # Decrypt data
            decrypted_data = {}
            for key, value in encrypted_data.items():
                try:
                    # Try to decrypt, if fails assume it's not encrypted
                    decrypted_data[key] = cls.decrypt(value)
                except Exception: # pylint: disable=broad-exception-caught
                    decrypted_data[key] = value

            logger.info("🔓 Environment file decrypted successfully")
            return decrypted_data

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to decrypt environment file: %s", e)
            return {}

    @classmethod
    def get_secure_env_var(cls, key: str, encrypted_file_path: str = ".env.encrypted") -> Optional[str]:
        """
        Get specific encrypted environment variable

        Args:
            key: Environment variable key
            encrypted_file_path: Path to encrypted env file

        Returns:
            Decrypted value or None
        """
        try:
            env_data = cls.load_encrypted_env(encrypted_file_path)
            return env_data.get(key)
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.error("❌ Failed to get encrypted env var %s: %s", key, e)
            return None


# Global utility
jarvis_crypto = JarvisCrypto()
