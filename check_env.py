import json
import os

env_vars = {
    "GOOGLE_API_KEY": bool(os.environ.get("GOOGLE_API_KEY")),
    "GOOGLE_APPLICATION_CREDENTIALS": os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
    "LIVEKIT_URL": bool(os.environ.get("LIVEKIT_URL")),
    "LIVEKIT_API_KEY": bool(os.environ.get("LIVEKIT_API_KEY")),
    "LIVEKIT_API_SECRET": bool(os.environ.get("LIVEKIT_API_SECRET")),
}

print(json.dumps(env_vars, indent=2))  # noqa: T201
