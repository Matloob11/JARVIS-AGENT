import win32gui
import win32con
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL
from ctypes import cast, POINTER
import os
from dotenv import load_dotenv

load_dotenv()

def list_windows():
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
    print("\n--- Testing Pycaw (Direct CoreAudio) ---")
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL, COMError
        from ctypes import cast, POINTER
        
        # Try finding the default speaker device
        device_enumerator = AudioUtilities.GetDeviceEnumerator()
        default_device = device_enumerator.GetDefaultAudioEndpoint(0, 1) # eRender, eMultimedia
        print(f"Default Device: {default_device.GetFriendlyName()}")
        
        interface = default_device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        
        print(f"Current Volume: {volume.GetMasterVolumeLevelScalar()}")
        print(f"Mute State: {volume.GetMute()}")
    except Exception as e:
        print(f"Pycaw Diagnostic Error: {e}")

def check_edge():
    print("\n--- Checking Edge Path ---")
    paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        "msedge.exe"
    ]
    for p in paths:
        exists = os.path.exists(p)
        print(f"Path: {p} | Exists: {exists}")

if __name__ == "__main__":
    list_windows()
    test_pycaw()
    check_edge()
