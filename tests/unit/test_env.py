import sys
print("Python Version:", sys.version)
try:
    import livekit
    print("LiveKit Version:", getattr(livekit, "__version__", "unknown"))
except ImportError:
    print("LiveKit NOT FOUND")
