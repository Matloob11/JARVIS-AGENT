import sys
import os

print(f"Python Version: {sys.version}")
print(f"Current Working Directory: {os.getcwd()}")
print(f"Python Path: {sys.path}")

try:
    import httpx
    print("SUCCESS: httpx imported")
except ImportError as e:
    print(f"FAILURE: httpx import failed: {e}")

try:
    from livekit import rtc
    print("SUCCESS: livekit-rtc imported")
except ImportError as e:
    print(f"FAILURE: livekit-rtc import failed: {e}")

try:
    import services.utils.jarvis_config
    print("SUCCESS: services.utils.jarvis_config imported")
except ImportError as e:
    print(f"FAILURE: services.utils.jarvis_config import failed: {e}")

try:
    import services.utils.jarvis_logger
    print("SUCCESS: services.utils.jarvis_logger imported")
except ImportError as e:
    print(f"FAILURE: services.utils.jarvis_logger import failed: {e}")

try:
    import services.ai_core.autonomous_planner
    print("SUCCESS: services.ai_core.autonomous_planner imported")
except ImportError as e:
    print(f"FAILURE: services.ai_core.autonomous_planner import failed: {e}")

print("\nVerification Complete.")
