"""
# jarvis_file_server.py
Jarvis File Server Tool (D: Drive Edition)
Starts a local HTTP server to share ONLY the D: drive with mobile devices on the same network.
"""

import os
import socket
import threading
import urllib.parse
from http.server import SimpleHTTPRequestHandler, HTTPServer
from livekit.agents import function_tool
from jarvis_logger import setup_logger
from jarvis_qr_gen import generate_qr_code

# Setup logger
logger = setup_logger("JARVIS-FILE-SERVER")


class DDriveHandler(SimpleHTTPRequestHandler):
    """
    Custom HTTP request handler that serves the D: drive directly.
    """

    def translate_path(self, path):
        """Translate a /-separated PATH to a local path on the D: drive."""
        # Abandon query parameters
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]

        # Don't allow absolute paths or parent directory references
        path = os.path.normpath(urllib.parse.unquote(path))
        words = path.split(os.sep)
        words = [w for w in words if w and w != '..']

        # Base drive restricted to a specific folder for security
        # Ensure this folder exists or use a default like 'Jarvis_Shared'
        root = os.path.join("D:\\", "Jarvis_Shared")
        if not os.path.exists(root):
            os.makedirs(root, exist_ok=True)

        # Construct physical path: D:\Jarvis_Shared\path
        return os.path.join(root, *words)

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        logger.info("Server Request: %s", format % args)


def get_local_ip():
    """Retrieves the local IP address of the laptop."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except (socket.error, socket.herror, socket.gaierror) as e:
        logger.error("Could not determine local IP: %s", e)
        return "127.0.0.1"


server_instance = None  # pylint: disable=invalid-name
server_thread = None  # pylint: disable=invalid-name


@function_tool
async def start_file_access_server(port: int = 8000) -> dict:
    """
    Starts a local file server and generates a QR code to access the D: drive from mobile.
    Use this when the user wants to see their D: storage on their phone.
    """
    # global server_instance is not needed here if only reading
    try:
        if server_instance:
            local_ip = get_local_ip()
            message = (f"Sir, server pehle se hi chal raha hai. Aap ise "
                       f"http://{local_ip}:{port} par access kar sakte hain.")
            return {
                "status": "info",
                "message": message
            }

        local_ip = get_local_ip()
        server_url = f"http://{local_ip}:{port}"

        def run_server():
            nonlocal port
            global server_instance  # pylint: disable=global-statement
            server_address = ('', port)
            server_instance = HTTPServer(server_address, DDriveHandler)
            logger.info("D: Drive Server started at %s", server_url)
            server_instance.serve_forever()

        global server_thread  # pylint: disable=global-statement
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Generate QR Code for the server URL
        qr_filename = "d_drive_qr.png"
        qr_result = await generate_qr_code(data=server_url, filename=qr_filename)

        if qr_result["status"] == "success":
            message = (
                f"✅ Sir Matloob, ab aap apni D: drive mobile par access kar sakte hain!\n\n"
                f"🔗 URL: {server_url}\n"
                f"📱 Is QR code ko scan kar ke D: storage browsing shuru karein."
            )
            return {
                "status": "success",
                "url": server_url,
                "qr_path": qr_result["file_path"],
                "message": message
            }

        return {
            "status": "partial_success",
            "url": server_url,
            "message": f"✅ Server toh start ho gaya hai ({server_url}), lekin QR code banane mein masla aaya."
        }

    except (OSError, RuntimeError, ValueError) as e:
        logger.exception("Error starting storage server: %s", e)
        return {
            "status": "error",
            "message": f"❌ Maazrat Sir, storage server start karne mein error aaya: {str(e)}"
        }


@function_tool
async def stop_file_access_server() -> dict:
    """Stops the running file server."""
    global server_instance  # pylint: disable=global-statement
    try:
        if server_instance:
            server_instance.shutdown()
            server_instance = None
            logger.info("File server stopped.")
            return {"status": "success", "message": "Sir, file server band kar diya gaya hai."}
        return {"status": "info", "message": "Sir, koi server active nahi hai."}
    except (RuntimeError, OSError) as e:
        logger.error("Error stopping file server: %s", e)
        return {"status": "error", "message": f"Server band karne mein error aaya: {str(e)}"}
