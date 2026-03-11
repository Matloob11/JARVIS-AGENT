"""
# jarvis_win32.py
Safe wrapper for Win32 imports to avoid duplication and handle ImportErrors across the project.
"""

try:
    import win32gui
    import win32con
    import win32api
    import pywintypes
except ImportError:
    win32gui = None
    win32con = None
    win32api = None
    pywintypes = None

__all__ = ["win32gui", "win32con", "win32api", "pywintypes"]
