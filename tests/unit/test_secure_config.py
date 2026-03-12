"""
Unit Tests for J.A.R.V.I.S Secure Configuration Manager
"""

import pytest
import os
import json
import tempfile
import shutil
from unittest.mock import patch, mock_open

from services.utils.jarvis_secure_config import SecureConfigManager
from services.utils.jarvis_crypto import JarvisCrypto


class TestSecureConfigManager:
    """Test cases for SecureConfigManager"""
    
    def test_init_with_encrypted_file(self, encrypted_env_file):
        """Test initialization with encrypted file"""
        config = SecureConfigManager(encrypted_env_file)
        
        assert config.get('LIVEKIT_API_KEY') == 'test_livekit_key'
        assert config.get('USER_NAME') == 'Test User'
        assert config.get('NON_EXISTENT_KEY') is None
    
    def test_init_without_encrypted_file(self, temp_dir):
        """Test initialization without encrypted file"""
        config = SecureConfigManager("non_existent.encrypted")
        
        # Should not raise exception, just empty config
        assert config.get('ANY_KEY') is None
    
    def test_get_with_default(self, secure_config_manager):
        """Test getting configuration with default value"""
        result = secure_config_manager.get('NON_EXISTENT_KEY', 'default_value')
        assert result == 'default_value'
    
    def test_get_required_success(self, secure_config_manager):
        """Test getting required configuration successfully"""
        result = secure_config_manager.get_required('LIVEKIT_API_KEY')
        assert result == 'test_livekit_key'
    
    def test_get_required_failure(self, secure_config_manager):
        """Test getting required configuration that doesn't exist"""
        with pytest.raises(ValueError, match="Required configuration key 'NON_EXISTENT' not found"):
            secure_config_manager.get_required('NON_EXISTENT')
    
    def test_get_livekit_config(self, secure_config_manager):
        """Test getting LiveKit configuration"""
        config = secure_config_manager.get_livekit_config()
        
        assert 'api_key' in config
        assert 'api_secret' in config
        assert 'url' in config
        assert config['api_key'] == 'test_livekit_key'
        assert config['url'] == 'wss://test.livekit.cloud'
    
    def test_get_google_config(self, secure_config_manager):
        """Test getting Google configuration"""
        config = secure_config_manager.get_google_config()
        
        assert 'api_key' in config
        assert config['api_key'] == 'test_google_key'
    
    def test_get_weather_config(self, secure_config_manager):
        """Test getting weather configuration"""
        config = secure_config_manager.get_weather_config()
        
        assert 'api_key' in config
        assert config['api_key'] == 'test_weather_key'
    
    def test_get_user_config(self, secure_config_manager):
        """Test getting user configuration"""
        config = secure_config_manager.get_user_config()
        
        assert 'name' in config
        assert config['name'] == 'Test User'
        assert 'controller_token' in config
    
    def test_validate_configuration_success(self, secure_config_manager):
        """Test successful configuration validation"""
        validation = secure_config_manager.validate_configuration()
        
        assert validation['overall'] is True
        assert validation['LIVEKIT_API_KEY'] is True
        assert validation['GOOGLE_API_KEY'] is True
    
    def test_validate_configuration_missing_keys(self, temp_dir):
        """Test configuration validation with missing keys"""
        # Create incomplete config
        incomplete_env = os.path.join(temp_dir, ".env")
        with open(incomplete_env, 'w') as f:
            f.write("LIVEKIT_API_KEY=test_key\n")
        
        crypto = JarvisCrypto()
        encrypted_file = crypto.encrypt_env_file(incomplete_env)
        config = SecureConfigManager(encrypted_file)
        
        validation = config.validate_configuration()
        
        assert validation['overall'] is False
        assert validation['LIVEKIT_API_KEY'] is True
        assert validation['GOOGLE_API_KEY'] is False
    
    @patch('os.path.exists')
    def test_load_plain_env_fallback(self, mock_exists, temp_dir):
        """Test fallback to plain .env file"""
        mock_exists.side_effect = lambda path: path == '.env'
        
        # Create plain .env file
        plain_env = os.path.join(temp_dir, ".env")
        with open(plain_env, 'w') as f:
            f.write("TEST_KEY=test_value\n")
        
        with patch('builtins.open', mock_open(read_data="TEST_KEY=test_value\n")):
            config = SecureConfigManager("non_existent.encrypted")
            assert config.get('TEST_KEY') == 'test_value'
    
    def test_reload_configuration(self, secure_config_manager):
        """Test configuration reloading"""
        original_value = secure_config_manager.get('LIVEKIT_API_KEY')
        
        # Reload should not change anything if file unchanged
        secure_config_manager.reload()
        assert secure_config_manager.get('LIVEKIT_API_KEY') == original_value


class TestJarvisCrypto:
    """Test cases for JarvisCrypto"""
    
    def test_encrypt_decrypt_string(self):
        """Test basic string encryption and decryption"""
        crypto = JarvisCrypto()
        original = "test_secret_string"
        
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        
        assert decrypted == original
        assert encrypted != original
    
    def test_encrypt_decrypt_bytes(self):
        """Test bytes encryption and decryption"""
        crypto = JarvisCrypto()
        original = b"test_secret_bytes"
        
        encrypted = crypto.encrypt(original)
        decrypted = crypto.decrypt(encrypted)
        
        assert decrypted == original.decode()
    
    def test_encrypt_env_file(self, mock_env_file):
        """Test environment file encryption"""
        crypto = JarvisCrypto()
        encrypted_file = crypto.encrypt_env_file(mock_env_file)
        
        assert os.path.exists(encrypted_file)
        assert encrypted_file.endswith('.encrypted')
        
        # Verify encrypted content is not plain text
        with open(encrypted_file, 'r') as f:
            content = f.read()
            assert 'test_livekit_key' not in content
    
    def test_load_encrypted_env(self, encrypted_env_file):
        """Test loading encrypted environment file"""
        crypto = JarvisCrypto()
        env_data = crypto.load_encrypted_env(encrypted_env_file)
        
        assert 'LIVEKIT_API_KEY' in env_data
        assert env_data['LIVEKIT_API_KEY'] == 'test_livekit_key'
        assert env_data['USER_NAME'] == 'Test User'
    
    def test_get_secure_env_var(self, encrypted_env_file):
        """Test getting specific encrypted environment variable"""
        crypto = JarvisCrypto()
        
        api_key = crypto.get_secure_env_var('LIVEKIT_API_KEY', encrypted_env_file)
        assert api_key == 'test_livekit_key'
        
        non_existent = crypto.get_secure_env_var('NON_EXISTENT', encrypted_env_file)
        assert non_existent is None
    
    def test_encrypt_nonexistent_file(self):
        """Test encrypting non-existent file"""
        crypto = JarvisCrypto()
        
        with pytest.raises(Exception):
            crypto.encrypt_env_file('non_existent.env')
    
    def test_load_nonexistent_encrypted_file(self):
        """Test loading non-existent encrypted file"""
        crypto = JarvisCrypto()
        
        result = crypto.load_encrypted_env('non_existent.encrypted')
        assert result == {}


@pytest.mark.integration
class TestSecureConfigIntegration:
    """Integration tests for secure configuration"""
    
    def test_full_encryption_workflow(self, mock_env_file, temp_dir):
        """Test complete encryption workflow"""
        # Step 1: Encrypt the file
        crypto = JarvisCrypto()
        encrypted_file = crypto.encrypt_env_file(mock_env_file)
        
        # Step 2: Load with SecureConfigManager
        config = SecureConfigManager(encrypted_file)
        
        # Step 3: Validate configuration
        validation = config.validate_configuration()
        assert validation['overall'] is True
        
        # Step 4: Access specific configurations
        livekit_config = config.get_livekit_config()
        assert livekit_config['api_key'] == 'test_livekit_key'
    
    def test_config_manager_with_missing_file(self, temp_dir):
        """Test config manager behavior with missing files"""
        config = SecureConfigManager('missing.encrypted')
        
        # Should not crash, just return empty config
        assert config.get('ANY_KEY') is None
        
        # Validation should fail
        validation = config.validate_configuration()
        assert validation['overall'] is False


@pytest.mark.security
class TestSecurityFeatures:
    """Security-specific tests"""
    
    def test_encryption_key_consistency(self):
        """Test that encryption is consistent within session"""
        crypto1 = JarvisCrypto()
        crypto2 = JarvisCrypto()
        
        original = "security_test_string"
        
        encrypted1 = crypto1.encrypt(original)
        encrypted2 = crypto2.encrypt(original)
        
        # Both should be able to decrypt each other's data
        decrypted1 = crypto1.decrypt(encrypted2)
        decrypted2 = crypto2.decrypt(encrypted1)
        
        assert decrypted1 == original
        assert decrypted2 == original
    
    def test_sensitive_key_identification(self, mock_env_file):
        """Test that sensitive keys are properly identified and encrypted"""
        crypto = JarvisCrypto()
        encrypted_file = crypto.encrypt_env_file(mock_env_file)
        
        # Load raw encrypted file and check that sensitive values are encrypted
        with open(encrypted_file, 'r') as f:
            encrypted_data = json.load(f)
        
        # API keys should be encrypted (not plain text)
        assert 'gAAAA' in encrypted_data['LIVEKIT_API_KEY']
        assert 'gAAAA' in encrypted_data['GOOGLE_API_KEY']
        
        # Non-sensitive values should remain plain text
        assert encrypted_data['USER_NAME'] == 'Test User'
