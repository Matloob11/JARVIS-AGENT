"""
# jarvis_qa_reporter.py
QA Reporting module for JARVIS, providing system diagnostics and performance reports.
"""
import os
from datetime import datetime
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("JARVIS-QA-REPORTER")


class JarvisQAReporter:
    """
    Automated QA Reporting system.
    Generates markdown summaries of quality audits.
    """
    def __init__(self, report_dir="logs/qa_reports"):
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)

    def _write_report_header(self, file, date_str):
        file.write(f"# 🛡️ JARVIS Autonomous QA Report - {date_str}\n\n")

    def _write_audit_results(self, file, results):
        file.write("### 🧪 Audit Results\n")
        for module, res in results.items():
            mod_status = res.get("status", "UNKNOWN")
            icon = "✅" if mod_status == "PASS" else "⚠️" if mod_status == "DEGRADED" else "❌"
            file.write(f"- {icon} **{module.capitalize()}**: {mod_status}\n")
            if "avg_latency_ms" in res:
                file.write(f"  - Avg Latency: {res['avg_latency_ms']:.2f}ms\n")
                file.write(f"  - Messages: {res['messages_sent']}\n")
            if "probes" in res:
                for probe, p_status in res["probes"].items():
                    file.write(f"  - {probe.replace('_',' ')}: {p_status}\n")

    def generate_daily_report(self, history: list) -> str:
        """Generates a markdown report from audit history."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        report_path = os.path.join(self.report_dir, f"qa_report_{date_str}.md")

        try:
            with open(report_path, "w", encoding="utf-8") as f:
                self._write_report_header(f, date_str)
                if not history:
                    f.write("> [!WARNING]\n> No audit data available.\n")
                    return report_path

                latest = history[-1]
                score = latest["score"]
                status = "PASS" if score >= 80 else "DEGRADED" if score >= 60 else "FAIL"
                f.write(f"## 📊 Current Score: **{score}/100** ({status})\n\n")
                self._write_audit_results(f, latest["results"])

                f.write("\n### 📉 Performance Trends\n")
                f.write("| Timestamp | Quality Score | Status |\n| :--- | :--- | :--- |\n")
                for entry in history[-10:]:
                    e_status = "PASS" if entry['score'] >= 80 else "FAIL"
                    ts = datetime.fromtimestamp(entry['timestamp']).strftime('%H:%M:%S')
                    f.write(f"| {ts} | {entry['score']} | {e_status} |\n")

                f.write("\n\n---\n*Report generated autonomously by JARVIS QA Engine.*")
            return report_path
        except (IOError, OSError, ValueError) as error:
            logger.error("Failed to generate QA report: %s", error)
            return ""

    def get_status(self):
        """Returns the reporter status and report directory."""
        return {"report_dir": self.report_dir, "status": "active"}


# Global Singleton
qa_reporter = JarvisQAReporter()
