"""
# services/utils/jarvis_crypto.py
Handles AES-256 encryption for JARVIS memory and sensitive logs.
Uses Fernet (Symmetric encryption).
"""

import os
import logging
from typing import Union
from cryptography.fernet import Fernet

logger = logging.getLogger("JARVIS-CRYPTO")


class JarvisCrypto:
    """
    Handles AES-256 encryption for JARVIS memory and sensitive logs.
    Uses Fernet (Symmetric encryption).
    """
    _key: bytes = None

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


# Global utility
jarvis_crypto = JarvisCrypto()
