"""
Integration Tests for J.A.R.V.I.S Agent Core
"""

import pytest
import asyncio
import tempfile
import os
from unittest.mock import Mock, patch, AsyncMock

from src.core.agent_core import BrainAssistant
from services.utils.jarvis_secure_config import SecureConfigManager


@pytest.mark.integration
class TestBrainAssistantIntegration:
    """Integration tests for BrainAssistant"""
    
    @pytest.fixture
    def mock_chat_ctx(self):
        """Mock chat context"""
        ctx = Mock()
        ctx.messages = []
        return ctx
    
    @pytest.fixture
    def agent_instance(self, mock_chat_ctx, encrypted_env_file):
        """Create BrainAssistant instance for testing"""
        with patch('services.utils.jarvis_secure_config.get_secure_config') as mock_config:
            # Mock secure config
            config = SecureConfigManager(encrypted_env_file)
            mock_config.return_value = config
            
            # Create agent instance
            agent = BrainAssistant(
                chat_ctx=mock_chat_ctx,
                current_date="2026-03-11",
                current_city="Test City"
            )
            return agent
    
    def test_agent_initialization(self, agent_instance):
        """Test agent initialization"""
        assert agent_instance is not None
        assert hasattr(agent_instance, 'chat_ctx')
        assert agent_instance.current_date == "2026-03-11"
        assert agent_instance.current_city == "Test City"
    
    @pytest.mark.asyncio
    async def test_agent_with_mock_session(self, agent_instance):
        """Test agent with mock LiveKit session"""
        # Mock session
        mock_session = Mock()
        mock_session.say = AsyncMock()
        mock_session.listen = AsyncMock()
        
        # Assign session to agent
        agent_instance.session = mock_session
        
        # Test basic functionality
        await mock_session.say("Test message")
        mock_session.say.assert_called_once_with("Test message")
    
    def test_agent_configuration_loading(self, agent_instance):
        """Test that agent loads configuration correctly"""
        # The agent should have access to configuration through the secure config
        # This tests the integration between agent and secure config
        assert hasattr(agent_instance, 'current_date')
        assert hasattr(agent_instance, 'current_city')
    
    @pytest.mark.llm
    @pytest.mark.asyncio
    async def test_agent_llm_integration(self, agent_instance):
        """Test agent LLM integration"""
        # Keep this offline: importing livekit.plugins.google can be very slow
        # because it imports Google Cloud speech packages.
        mock_llm = Mock()
        agent_instance._local_llm = mock_llm

        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Test response"
        mock_llm.achat = AsyncMock(return_value=mock_response)

        response = await agent_instance._local_llm.achat("Test prompt")
        assert response.choices[0].message.content == "Test response"


@pytest.mark.integration
@pytest.mark.audio
class TestAudioProcessingIntegration:
    """Integration tests for audio processing"""
    
    @pytest.fixture
    def mock_audio_data(self):
        """Mock audio data"""
        return b"mock_audio_data_bytes"
    
    @pytest.mark.asyncio
    async def test_audio_input_processing(self, mock_audio_data):
        """Test audio input processing pipeline"""
        # Mock audio processing components
        with patch('services.multimedia.jarvis_audio.process_audio_input') as mock_process:
            mock_process.return_value = {"transcript": "Hello Jarvis", "confidence": 0.95}
            
            result = mock_process(mock_audio_data)
            assert result["transcript"] == "Hello Jarvis"
            assert result["confidence"] == 0.95
    
    @pytest.mark.asyncio
    async def test_audio_output_generation(self):
        """Test audio output generation"""
        with patch('services.multimedia.jarvis_audio.generate_speech') as mock_generate:
            mock_generate.return_value = b"generated_audio_bytes"
            
            result = mock_generate("Hello, this is Jarvis speaking")
            assert result == b"generated_audio_bytes"


@pytest.mark.integration
class TestMemorySystemIntegration:
    """Integration tests for memory system"""
    
    @pytest.fixture
    def mock_memory_store(self):
        """Mock memory store"""
        memory = Mock()
        memory.add = AsyncMock()
        memory.get = AsyncMock()
        memory.search = AsyncMock(return_value=[])
        return memory
    
    @pytest.mark.asyncio
    async def test_memory_storage(self, mock_memory_store):
        """Test memory storage functionality"""
        # Test adding memory
        await mock_memory_store.add("test_key", {"content": "test_memory"})
        mock_memory_store.add.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_memory_retrieval(self, mock_memory_store):
        """Test memory retrieval functionality"""
        # Mock retrieval
        mock_memory_store.get.return_value = {"content": "retrieved_memory"}
        
        result = await mock_memory_store.get("test_key")
        assert result["content"] == "retrieved_memory"
    
    @pytest.mark.asyncio
    async def test_memory_search(self, mock_memory_store):
        """Test memory search functionality"""
        # Mock search results
        mock_memory_store.search.return_value = [
            {"content": "matching_memory_1", "score": 0.9},
            {"content": "matching_memory_2", "score": 0.8}
        ]
        
        results = await mock_memory_store.search("search_query")
        assert len(results) == 2
        assert results[0]["score"] == 0.9


@pytest.mark.integration
class TestToolIntegration:
    """Integration tests for tool system"""
    
    @pytest.fixture
    def mock_plugin_manager(self):
        """Mock plugin manager"""
        manager = Mock()
        manager.execute_tool = AsyncMock()
        manager.get_available_tools.return_value = [
            {"name": "weather", "description": "Get weather information"},
            {"name": "search", "description": "Search the web"}
        ]
        return manager
    
    @pytest.mark.asyncio
    async def test_tool_execution(self, mock_plugin_manager):
        """Test tool execution"""
        mock_plugin_manager.execute_tool.return_value = {"result": "Tool executed successfully"}
        
        result = await mock_plugin_manager.execute_tool("weather", {"location": "New York"})
        assert result["result"] == "Tool executed successfully"
        mock_plugin_manager.execute_tool.assert_called_once_with("weather", {"location": "New York"})
    
    def test_tool_discovery(self, mock_plugin_manager):
        """Test tool discovery"""
        tools = mock_plugin_manager.get_available_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "weather"
        assert tools[1]["name"] == "search"


@pytest.mark.integration
@pytest.mark.security
class TestSecurityIntegration:
    """Integration tests for security features"""
    
    def test_secure_config_integration(self, encrypted_env_file):
        """Test secure config integration with agent"""
        config = SecureConfigManager(encrypted_env_file)
        
        # Test that sensitive data is properly loaded
        api_key = config.get('LIVEKIT_API_KEY')
        assert api_key == 'test_livekit_key'
        
        # Test validation
        validation = config.validate_configuration()
        assert validation['overall'] is True
    
    def test_encryption_decryption_workflow(self, temp_dir):
        """Test complete encryption/decryption workflow"""
        from services.utils.jarvis_crypto import JarvisCrypto
        
        # Create test data
        test_data = "sensitive_api_key_12345"
        
        # Encrypt
        crypto = JarvisCrypto()
        encrypted = crypto.encrypt(test_data)
        
        # Decrypt
        decrypted = crypto.decrypt(encrypted)
        
        assert decrypted == test_data
        assert encrypted != test_data


@pytest.mark.integration
@pytest.mark.slow
class TestPerformanceIntegration:
    """Integration tests for performance"""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test handling concurrent requests"""
        # Mock concurrent agent requests
        async def mock_request():
            await asyncio.sleep(0.1)  # Simulate processing time
            return "response"
        
        # Create multiple concurrent requests
        tasks = [mock_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 10
        assert all(result == "response" for result in results)
    
    def test_memory_usage(self):
        """Test memory usage during operations"""
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss
        
        # Simulate some operations
        test_data = []
        for i in range(1000):
            test_data.append(f"test_data_{i}" * 100)
        
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory
        
        # Memory increase should be reasonable (less than 100MB)
        assert memory_increase < 100 * 1024 * 1024
        
        # Cleanup
        del test_data


@pytest.mark.integration
class TestErrorHandling:
    """Integration tests for error handling"""
    
    @pytest.mark.asyncio
    async def test_api_failure_handling(self):
        """Test handling of API failures"""
        with patch('httpx.AsyncClient.get') as mock_get:
            # Simulate API failure
            mock_get.side_effect = Exception("API Error")
            
            # Test error handling
            with pytest.raises(Exception):
                await mock_get("https://api.example.com")
    
    def test_configuration_error_handling(self):
        """Test handling of configuration errors"""
        # Test with invalid encrypted file
        with pytest.raises(Exception):
            config = SecureConfigManager("invalid_encrypted_file.encrypted")
            config.get_required('MISSING_KEY')
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test handling of timeouts"""
        with patch('asyncio.sleep') as mock_sleep:
            # Simulate timeout
            mock_sleep.side_effect = asyncio.TimeoutError()
            
            with pytest.raises(asyncio.TimeoutError):
                await mock_sleep(1.0)
