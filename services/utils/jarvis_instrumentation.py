"""
# services/utils/jarvis_instrumentation.py
Initializes Arize Phoenix instrumentation via OpenTelemetry.
"""

import socket
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-INSTRUMENTATION")


def setup_instrumentation():
    """
    Initializes Arize Phoenix instrumentation via OpenTelemetry.
    Traces will be sent to the local Phoenix server.
    """
    # Pre-flight check: Is the Phoenix server/OTel collector actually listening?
    collector_addr = ("127.0.0.1", 4317)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect(collector_addr)
        except (ConnectionRefusedError, socket.timeout, OSError):
            print("⚠️ Phoenix server (port 4317) not reachable. Skipping.")
            return

    print("🧠 Initializing JARVIS Instrumentation (Arize Phoenix)...")

    if not _register_phoenix():
        return

    _instrument_llm_frameworks()
    _instrument_network_clients()

    print("✅ Instrumentation check complete.")


def _register_phoenix() -> bool:
    """Registers the application with the Phoenix server."""
    try:
        # pylint: disable=import-outside-toplevel
        from phoenix.otel import register
        register()
        logger.info("Phoenix OTel registration successful.")
        return True
    except ImportError:
        logger.warning("arize-phoenix not installed. Tracing disabled.")
        return False
    except (RuntimeError, ValueError, OSError) as e:
        logger.error("Failed to register Phoenix: %s", e)
        return False


def _instrument_llm_frameworks():
    """Instruments OpenAI and LangChain if available."""
    try:
        # pylint: disable=import-outside-toplevel
        from openinference.instrumentation.openai import OpenAIInstrumentor
        OpenAIInstrumentor().instrument()
        logger.info("OpenAI instrumentation enabled.")
    except ImportError:
        logger.debug("OpenAI instrumentation package not found.")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning("OpenAI instrumentation failed: %s", e)

    try:
        # pylint: disable=import-outside-toplevel
        from openinference.instrumentation.langchain import LangChainInstrumentor
        LangChainInstrumentor().instrument()
        logger.info("LangChain instrumentation enabled.")
    except ImportError:
        logger.debug("LangChain instrumentation package not found.")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning("LangChain instrumentation failed: %s", e)


def _instrument_network_clients():
    """Instruments Requests and HTTPX if available."""
    try:
        # pylint: disable=import-outside-toplevel
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
        logger.info("Requests instrumentation enabled.")
    except ImportError:
        logger.debug("Requests instrumentation package not found.")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning("Requests instrumentation failed: %s", e)

    try:
        # pylint: disable=import-outside-toplevel
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logger.info("HTTPX instrumentation enabled.")
    except ImportError:
        logger.debug("HTTPX instrumentation package not found.")
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning("HTTPX instrumentation failed: %s", e)


if __name__ == "__main__":
    setup_instrumentation()
