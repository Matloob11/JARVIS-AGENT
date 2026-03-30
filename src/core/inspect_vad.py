import inspect

from livekit.plugins import silero

try:
    print(f"VAD Init Signature: {inspect.signature(silero.VAD.__init__)}")
except Exception as e:  # pylint: disable=broad-exception-caught
    print(f"Error: {e}")
