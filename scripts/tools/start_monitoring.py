#!/usr/bin/env python3
"""
J.A.R.V.I.S Monitoring Startup Script
Starts performance monitoring and metrics server
"""

import sys
import argparse
import asyncio
import signal
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.utils.jarvis_monitoring import start_monitoring, stop_monitoring, get_performance_monitor
from services.utils.jarvis_metrics_server import start_metrics_server
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-MONITORING-STARTUP")

# Global flag for graceful shutdown
shutdown_flag = False

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    global shutdown_flag
    logger.info("🛑 Shutdown signal received")
    shutdown_flag = True

async def run_monitoring_services(host: str = "127.0.0.1", port: int = 8000):
    """Run monitoring services"""
    logger.info("🚀 Starting J.A.R.V.I.S Monitoring Services")

    try:
        # Start performance monitoring
        logger.info("📊 Starting performance monitoring...")
        start_monitoring()

        # Start metrics server
        logger.info(f"🌐 Starting metrics server on http://{host}:{port}")
        server_task = asyncio.create_task(start_metrics_server(host, port))

        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("✅ All monitoring services started successfully!")
        logger.info(f"📊 Dashboard available at: http://{host}:{port}")
        logger.info("📈 Metrics API available at: http://{host}:{port}/api/metrics/current")
        logger.info("💚 Health check at: http://{host}:{port}/api/health")

        # Keep running until shutdown
        while not shutdown_flag:
            await asyncio.sleep(1)

        logger.info("🛑 Shutting down monitoring services...")

        # Cleanup
        stop_monitoring()
        server_task.cancel()

        try:
            await server_task
        except asyncio.CancelledError:
            pass

        logger.info("✅ Monitoring services stopped gracefully")

    except Exception as e:
        logger.error(f"❌ Error running monitoring services: {e}")
        sys.exit(1)

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Start J.A.R.V.I.S Monitoring Services")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Metrics server host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Metrics server port (default: 8000)"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check monitoring setup, don't start services"
    )

    args = parser.parse_args()

    if args.check_only:
        # Check monitoring setup
        logger.info("🔍 Checking monitoring setup...")

        try:
            get_performance_monitor()
            logger.info("✅ Performance monitor initialized successfully")

            # Check dependencies
            import psutil
            import fastapi
            import uvicorn
            logger.info("✅ All dependencies available")

            logger.info("✅ Monitoring setup is ready")

        except ImportError as e:
            logger.error(f"❌ Missing dependency: {e}")
            logger.error("Install with: pip install -r requirements.txt")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Setup check failed: {e}")
            sys.exit(1)

        return

    # Run monitoring services
    try:
        asyncio.run(run_monitoring_services(args.host, args.port))
    except KeyboardInterrupt:
        logger.info("🛑 Monitoring stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
