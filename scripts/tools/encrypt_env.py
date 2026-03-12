#!/usr/bin/env python3
"""
J.A.R.V.I.S Environment Encryption Script
Encrypts .env file for enhanced security
"""

import os
import sys
import argparse
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.utils.jarvis_crypto import JarvisCrypto
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("ENCRYPT-ENV")


def main():
    """Main encryption script"""
    parser = argparse.ArgumentParser(description="Encrypt JARVIS environment file")
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file (default: .env)"
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create backup of original .env file"
    )
    parser.add_argument(
        "--remove-original",
        action="store_true",
        help="Remove original .env file after encryption"
    )

    args = parser.parse_args()

    # Check if .env file exists
    if not os.path.exists(args.env_file):
        logger.error(f"❌ Environment file not found: {args.env_file}")
        sys.exit(1)

    try:
        logger.info("🔐 Starting environment file encryption...")

        # Create backup if requested
        if args.backup:
            backup_file = f"{args.env_file}.backup"
            import shutil
            shutil.copy2(args.env_file, backup_file)
            logger.info(f"📋 Backup created: {backup_file}")

        # Initialize crypto manager
        crypto = JarvisCrypto()

        # Encrypt the environment file
        encrypted_file = crypto.encrypt_env_file(args.env_file)

        if encrypted_file:
            logger.info("✅ Environment file encrypted successfully!")
            logger.info(f"📁 Encrypted file: {encrypted_file}")

            # Set encryption key environment variable for future use
            if not os.getenv("JARVIS_ENCRYPTION_KEY"):
                logger.info("🔑 Consider setting JARVIS_ENCRYPTION_KEY environment variable")
                logger.info("   This ensures consistent encryption across sessions")

            # Remove original if requested
            if args.remove_original:
                os.remove(args.env_file)
                logger.info(f"🗑️ Original file removed: {args.env_file}")

            # Show next steps
            logger.info("\n📋 Next Steps:")
            logger.info("1. Update your application to use SecureConfigManager")
            logger.info("2. Set JARVIS_ENCRYPTION_KEY environment variable")
            logger.info("3. Test the encrypted configuration loading")

        else:
            logger.error("❌ Encryption failed")
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Encryption failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
