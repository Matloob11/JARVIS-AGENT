import os
import phoenix as px
import time

# Phoenix playground features may require an OpenAI API key.
# We set a placeholder if not present to prevent the server from crashing on startup.
if "OPENAI_API_KEY" not in os.environ:
    os.environ["OPENAI_API_KEY"] = "sk-placeholder"


def start_phoenix():
    print("🚀 Starting Arize Phoenix locally...")
    # Launch phoenix
    session = px.launch_app()
    print(f"✅ Phoenix is available at: {session.url}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping Phoenix server...")


if __name__ == "__main__":
    start_phoenix()
