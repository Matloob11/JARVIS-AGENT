"""
# jarvis_win32.py
Safe wrapper for Win32 imports to avoid duplication and handle ImportErrors across the project.
"""

try:
    import pywintypes
    import win32api
    import win32con
    import win32gui
except ImportError:
    win32gui = None
    win32con = None
    win32api = None
    pywintypes = None

from typing import Any

__all__ = ["WIN32_ERRORS", "pywintypes", "win32api", "win32con", "win32gui"]

# Unified error tuple for easier exception handling
# Type annotation helps mypy understand this is a tuple of catchable exceptions
WIN32_ERRORS: tuple[Any, ...] = (OSError, ValueError, AttributeError, RuntimeError)
if pywintypes is not None:
    WIN32_ERRORS += (pywintypes.error,)
