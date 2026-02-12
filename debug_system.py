import os
import win32gui
from comtypes import COMError
from pycaw.pycaw import AudioUtilities
from dotenv import load_dotenv

load_dotenv()


def list_windows():
    """List all currently visible windows"""
    print("\n--- Visible Windows ---")
    windows = []

    def handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title.strip():
                print(f"HWND: {hwnd} | Title: {title}")
                windows.append((hwnd, title))
    win32gui.EnumWindows(handler, None)
    return windows


def test_pycaw():
    """Diagnostic for Pycaw volume control"""
    print("\n--- Testing Pycaw (Direct CoreAudio) ---")
    try:
        AudioUtilities.GetSpeakers()
        print("Pycaw interface access successful.")
    except (COMError, OSError) as e:
        print(f"Pycaw Diagnostic Error: {e}")


def check_edge():
    """Verify Edge browser executable path"""
    print("\n--- Checking Edge Path ---")
    paths = [
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "msedge.exe"
    ]
    for p in paths:
        exists = os.path.exists(p)
        print(f"Path: {p} | Exists: {exists}")


if __name__ == "__main__":
    list_windows()
    test_pycaw()
    check_edge()
