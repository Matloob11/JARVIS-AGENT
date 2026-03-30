import inspect

from livekit.plugins import silero

print(f"silero.vad Signature: {inspect.signature(silero.vad)}")  # type: ignore
