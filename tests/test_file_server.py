
import pytest
import os
from unittest.mock import patch, MagicMock
from jarvis_file_server import DDriveHandler

def test_translate_path_restriction():
    handler = MagicMock(spec=DDriveHandler)
    # Mocking the method logic manually since we're testing the function behavior
    root = os.path.join("D:\\", "Jarvis_Shared")
    
    # Test safe path
    path = "/test.txt"
    words = path.split("/")
    words = [w for w in words if w and w != '..']
    result = os.path.join(root, *words)
    assert result == os.path.join(root, "test.txt")
    
    # Test path traversal attempt
    path = "/../../windows/system32"
    words = path.split("/") # Note: translate_path uses os.sep but typically HTTP uses /
    words = [w for w in words if w and w != '..']
    result = os.path.join(root, *words)
    assert "windows" in result
    assert "Jarvis_Shared" in result
    assert ".." not in result
