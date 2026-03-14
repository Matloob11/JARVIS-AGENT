"""
J.A.R.V.I.S Test Configuration
pytest configuration and shared fixtures
"""

import os
import sys
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

# Add project root and service directories to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Add subdirectories to path to allow direct imports in tests
service_dirs = [
    "services/automation",
    "services/multimedia",
    "services/info",
    "services/system",
    "services/ai_core",
    "services/utils",
    "services/connectivity",
    "services/vision",
    "ui"  # In case there's a ui dir
]

for d in service_dirs:
    full_path = str(project_root / d)
    if full_path not in sys.path and os.path.exists(full_path):
        sys.path.append(full_path)

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_secure_config import SecureConfigManager

# Test logger
logger = setup_logger("JARVIS-TESTS")


@pytest.fixture(scope="session")
def test_config():
    """Test configuration fixture"""
    return {
        "test_user_name": "Test User",
        "test_api_key": "test_api_key_12345",
        "test_livekit_url": "wss://test.livekit.cloud",
        "temp_dir": tempfile.mkdtemp(prefix="jarvis_test_")
    }


@pytest.fixture
def temp_dir():
    """Temporary directory fixture"""
    temp_path = tempfile.mkdtemp(prefix="jarvis_test_")
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def mock_env_file(temp_dir):
    """Create a mock .env file for testing"""
    env_content = """
LIVEKIT_API_KEY=test_livekit_key
LIVEKIT_API_SECRET=test_livekit_secret
LIVEKIT_URL=wss://test.livekit.cloud
GOOGLE_API_KEY=test_google_key
WEATHER_API_KEY=test_weather_key
USER_NAME=Test User
CONTROLLER_TOKEN=test_token
TAVILY_API_KEY=test_tavily_key
"""
    env_file = os.path.join(temp_dir, ".env")
    with open(env_file, "w") as f:
        f.write(env_content.strip())
    return env_file


@pytest.fixture
def encrypted_env_file(mock_env_file):
    """Create encrypted .env file for testing"""
    from services.utils.jarvis_crypto import JarvisCrypto
    
    crypto = JarvisCrypto()
    encrypted_file = crypto.encrypt_env_file(mock_env_file)
    return encrypted_file


@pytest.fixture
def secure_config_manager(encrypted_env_file):
    """Secure config manager fixture with encrypted env"""
    return SecureConfigManager(encrypted_env_file)


@pytest.fixture
def mock_livekit_agent():
    """Mock LiveKit agent fixture"""
    agent = Mock()
    agent.session = Mock()
    agent.say = Mock()
    agent.listen = Mock()
    return agent


@pytest.fixture
def mock_google_llm():
    """Mock Google LLM fixture"""
    llm = Mock()
    llm.chat = Mock()
    llm.ainfer = Mock()
    return llm


@pytest.fixture
def sample_audio_file(temp_dir):
    """Create a sample audio file for testing"""
    import wave
    import struct
    
    audio_file = os.path.join(temp_dir, "test_audio.wav")
    
    # Create a simple WAV file
    with wave.open(audio_file, 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(16000)  # 16kHz
        
        # Generate simple sine wave
        duration = 1.0  # 1 second
        frequency = 440  # A4 note
        samples = int(duration * 16000)
        
        for i in range(samples):
            value = int(32767 * 0.5 * (i % 100 < 50))  # Simple square wave
            wav_file.writeframes(struct.pack('<h', value))
    
    return audio_file


@pytest.fixture
def mock_memory_store():
    """Mock memory store fixture"""
    memory_store = Mock()
    memory_store.add = Mock()
    memory_store.get = Mock()
    memory_store.search = Mock()
    memory_store.delete = Mock()
    return memory_store


@pytest.fixture
def mock_plugin_manager():
    """Mock plugin manager fixture"""
    plugin_manager = Mock()
    plugin_manager.load_plugins = Mock()
    plugin_manager.execute_tool = Mock()
    plugin_manager.get_available_tools = Mock(return_value=[])
    return plugin_manager


@pytest.fixture(autouse=True)
def mock_environment_variables(monkeypatch):
    """Mock environment variables for all tests"""
    test_env = {
        "JARVIS_ENCRYPTION_KEY": "MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=",
        "PYTHONPATH": str(project_root),
        "JARVIS_TEST_MODE": "true"
    }
    
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def mock_http_client():
    """Mock HTTP client fixture"""
    import httpx
    
    # Create mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success"}
    mock_response.text = "Success"
    
    # Mock client
    mock_client = Mock(spec=httpx.Client)
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    
    return mock_client


# Test markers
pytest_plugins = []

def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "security: mark test as security-related"
    )
    config.addinivalue_line(
        "markers", "audio: mark test as audio-related"
    )
    config.addinivalue_line(
        "markers", "llm: mark test as LLM-related"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically"""
    for item in items:
        # Add markers based on test location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        
        # Add markers based on test name
        if "security" in item.name.lower():
            item.add_marker(pytest.mark.security)
        if "audio" in item.name.lower():
            item.add_marker(pytest.mark.audio)
        if "llm" in item.name.lower() or "google" in item.name.lower():
            item.add_marker(pytest.mark.llm)
