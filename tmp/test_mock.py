from unittest.mock import MagicMock
import subprocess

def test_mock_unpacking():
    m = MagicMock()
    try:
        a, b = m()
        print(f"Unpacked: {a}, {b}")
    except ValueError as e:
        print(f"Caught ValueError: {e}")

def test_mock_method_unpacking():
    m = MagicMock()
    try:
        a, b = m.communicate()
        print(f"Unpacked: {a}, {b}")
    except ValueError as e:
        print(f"Caught ValueError: {e}")

if __name__ == "__main__":
    test_mock_unpacking()
    test_mock_method_unpacking()
