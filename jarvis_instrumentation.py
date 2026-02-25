import os
import logging
from jarvis_logger import setup_logger

logger = setup_logger("JARVIS-INSTRUMENTATION")


def setup_instrumentation():
    """
    Initializes Arize Phoenix instrumentation via OpenTelemetry.
    Traces will be sent to the local Phoenix server (usually on http://localhost:6006).
    """
    import socket

    # Pre-flight check: Is the Phoenix server/OTel collector actually listening?
    # Default OTLP gRPC port is 4317.
    collector_addr = ("127.0.0.1", 4317)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(collector_addr)
        except (ConnectionRefusedError, socket.timeout, OSError):
            print("⚠️ Phoenix server (port 4317) not reachable. Skipping instrumentation to prevent terminal noise.")
            print(
                "💡 Tip: Run 'python phoenix_server.py' in a separate terminal to enable tracing.")
            return

    print("🧠 Initializing JARVIS Instrumentation (Arize Phoenix)...")

    try:
        from phoenix.otel import register
        # 1. Register with Phoenix
        register()
        logger.info("Phoenix OTel registration successful.")
    except ImportError:
        logger.warning(
            "arize-phoenix not installed. Tracing will be disabled.")
        return
    except Exception as e:
        logger.error("Failed to register Phoenix: %s", e)
        return

    # 2. Instrument LLM Frameworks
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor
        OpenAIInstrumentor().instrument()
        logger.info("OpenAI instrumentation enabled.")
    except ImportError:
        logger.debug("OpenAI instrumentation package not found.")
    except Exception as e:
        logger.warning("OpenAI instrumentation failed: %s", e)

    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        LangChainInstrumentor().instrument()
        logger.info("LangChain instrumentation enabled.")
    except ImportError:
        logger.debug("LangChain instrumentation package not found.")
    except Exception as e:
        logger.warning("LangChain instrumentation failed: %s", e)

    # 3. Instrument Network Calls (Requests & HTTPX)
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
        logger.info("Requests instrumentation enabled.")
    except ImportError:
        logger.debug("Requests instrumentation package not found.")
    except Exception as e:
        logger.warning("Requests instrumentation failed: %s", e)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.info("HTTPX instrumentation enabled.")
    except ImportError:
        logger.debug("HTTPX instrumentation package not found.")
    except Exception as e:
        logger.warning("HTTPX instrumentation failed: %s", e)

    print("✅ Instrumentation check complete.")


if __name__ == "__main__":
    # Test call
    setup_instrumentation()
