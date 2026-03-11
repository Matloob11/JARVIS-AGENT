import socketio
import base64
import time
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

VORTEX_URL = os.getenv("VORTEX_URL", "http://localhost:5001")
SECURITY_TOKEN = os.getenv("VORTEX_SECURITY_TOKEN", "your_token_here")

# Create a sample base64 image (small red square)


def generate_sample_frame():
    # This is a tiny 1x1 red dot in JPEG/Base64
    return "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc6Olpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECBA1ADRBRAURBcSEwUxAiGwRRYHInIQWhmgisFzJC0SGyJ5LiInJOf90NDR3SJhYmPkNWVzZGVlcW21hZWZnaGlqc6Onpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/aAAgGAtBAEACEEDEB/9oADAMBAAIRAxEAPwD+f+iiigD/2Q=="


def test_vision():
    sio = socketio.Client()

    @sio.event
    def connect():
        print(f"[SUCCESS] Connected to Bridge at {VORTEX_URL}")

        # 1. Send dummy vision frame
        print("[TEST] Sending dummy vision frame...")
        sample_frame = generate_sample_frame()
        sio.emit('vision_frame', {'frame': sample_frame})
        print(
            "[DONE] Frame emitted. Check backend logs for 'Vision frame relayed to agent'.")

        time.sleep(1)

        # 2. Simulate a command if needed
        # print("🧠 Sending intelligence check command...")
        # sio.emit('agent_command', {'type': 'check_intelligence'})

        print("\nTest complete. Disconnecting in 2 seconds...")
        time.sleep(2)
        sio.disconnect()

    @sio.event
    def disconnect():
        print("[INFO] Disconnected from Bridge")

    @sio.event
    def connect_error(data):
        print(f"[ERROR] Connection failed: {data}")

    try:
        # Connect with security token
        sio.connect(VORTEX_URL, auth={'token': SECURITY_TOKEN})
        sio.wait()
    except Exception as e:
        print(f"[ERROR] Error: {e}")


if __name__ == "__main__":
    test_vision()
