"""
# jarvis_clipboard.py
Proactive Clipboard Monitor Module
Detects errors and technical terms in the clipboard and suggests solutions.
"""

import asyncio
import logging
import re
import pyperclip
from duckduckgo_search import DDGS

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("JARVIS-CLIPBOARD")


class ClipboardMonitor:
    """
    Monitors the system clipboard for technical errors or terms.
    """

    def __init__(self, check_interval: float = 2.0):
        self.check_interval = check_interval
        self.last_content = ""
        self.is_running = False
        self.error_patterns = [
            r"Traceback \(most recent call last\):",
            r"\w+Error: .+",
            r"Exception in thread .+",
            r"failed with exit code \d+",
            r"0x[0-9a-fA-F]{8}",
            r"syntax error",
            r"segmentation fault",
            r"access violation"
        ]

    def is_technical_error(self, text: str) -> bool:
        """
        Check if the text matches common technical error patterns.
        """
        if not text or len(text.strip()) < 10:
            return False

        for pattern in self.error_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    async def get_solution(self, error_text: str) -> str:
        """
        Searches the internet for a solution to the detected error.
        """
        try:
            # Truncate to first few lines of error for better search results
            lines = error_text.strip().split('\n')
            query = f"{lines[-1]} solution" if len(
                lines) > 0 else "technical error solution"

            logger.info("Searching for solution to: %s", query)
            with DDGS() as ddgs:
                # Use a specific region or shorter timeout if possible
                # DDGS internally uses primp/httpx, sometimes Bing/Google backends fail
                # Let's add a list conversion with a custom timeout or just catch and provide a fallback.
                results = list(ddgs.text(query, max_results=3))

            if not results:
                return "Maazrat Sir, is error ka koi fori solution nahi mila."

            best_match = results[0]['body']
            return f"Sir, maine clipboard par ye error dekha hai. Iska aik mumkina solution ye hai: {best_match}"
        except Exception as e:
            logger.error("Error searching for clipboard solution: %s", e)
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                return ("Sir, maine clipboard par error toh dekha hai lekin internet "
                        "connection slow honay ki wajah se solution nahi mil paaya.")
            return f"Error finding solution: {str(e)}"

    async def start(self, on_detection_callback):
        """
        Starts the monitoring loop. 
        Calls on_detection_callback(solution_text) when an error is found.
        """
        self.is_running = True
        logger.info("Clipboard Monitoring started (Interval: %s s)",
                    self.check_interval)

        while self.is_running:
            try:
                current_paste = pyperclip.paste()

                if current_paste != self.last_content:
                    self.last_content = current_paste

                    if self.is_technical_error(current_paste):
                        logger.info("Technical content detected in clipboard!")
                        solution = await self.get_solution(current_paste)
                        await on_detection_callback(solution)

                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error("Clipboard loop error: %s", e)
                await asyncio.sleep(5)

    def stop(self):
        """Stops the monitoring loop."""
        self.is_running = False
