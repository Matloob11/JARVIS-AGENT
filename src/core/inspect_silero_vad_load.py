import inspect

from livekit.plugins import silero

try:
    print(f"silero.VAD.load Signature: {inspect.signature(silero.VAD.load)}")
except Exception as e:  # pylint: disable=broad-exception-caught
    print(f"Error: {e}")
