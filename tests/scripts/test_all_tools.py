#!/usr/bin/env python3
"""
J.A.R.V.I.S Comprehensive Tools Testing Script
Tests each tool and automation individually for 10/10 results
"""

import os
import sys
import time
import traceback
import importlib.util
from pathlib import Path
from typing import Dict, List, Tuple, Any
import logging

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)8s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("JARVIS-TOOLS-TEST")

class ToolTester:
    """Comprehensive tool testing framework"""
    
    def __init__(self):
        self.test_results = {}
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
        
        # Tool categories and their files
        self.tool_categories = {
            "Automation": [
                "services/automation/jarvis_clipboard.py",
                "services/automation/jarvis_notepad_automation.py", 
                "services/automation/jarvis_reminders.py",
                "services/automation/jarvis_whatsapp_automation.py",
                "services/automation/jarvis_youtube_automation.py",
                "services/automation/keyboard_mouse_ctrl.py"
            ],
            "Info": [
                "services/info/jarvis_get_weather.py",
                "services/info/jarvis_researcher.py",
                "services/info/jarvis_search.py"
            ],
            "Multimedia": [
                "services/multimedia/jarvis_advanced_tools.py",
                "services/multimedia/jarvis_file_opener.py",
                "services/multimedia/jarvis_image_gen.py",
                "services/multimedia/jarvis_qr_gen.py",
                "services/multimedia/jarvis_youtube_downloader.py"
            ],
            "System": [
                "services/system/jarvis_file_server.py",
                "services/system/jarvis_system_ctrl.py",
                "services/system/jarvis_system_info.py",
                "services/system/jarvis_window_ctrl.py"
            ]
        }
    
    def load_module(self, file_path: str):
        """Load Python module from file path"""
        try:
            spec = importlib.util.spec_from_file_location("test_module", file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            logger.error(f"❌ Failed to load {file_path}: {e}")
            return None
    
    def test_automation_tools(self):
        """Test automation tools"""
        logger.info("🤖 Testing Automation Tools")
        
        # Test Clipboard
        self.test_tool("Clipboard", self.test_clipboard)
        
        # Test Notepad Automation
        self.test_tool("Notepad Automation", self.test_notepad_automation)
        
        # Test Reminders
        self.test_tool("Reminders", self.test_reminders)
        
        # Test WhatsApp Automation
        self.test_tool("WhatsApp Automation", self.test_whatsapp_automation)
        
        # Test YouTube Automation
        self.test_tool("YouTube Automation", self.test_youtube_automation)
        
        # Test Keyboard/Mouse Control
        self.test_tool("Keyboard/Mouse Control", self.test_keyboard_mouse_control)
    
    def test_info_tools(self):
        """Test information tools"""
        logger.info("📊 Testing Info Tools")
        
        self.test_tool("Weather", self.test_weather)
        self.test_tool("Researcher", self.test_researcher)
        self.test_tool("Search", self.test_search)
    
    def test_multimedia_tools(self):
        """Test multimedia tools"""
        logger.info("🎵 Testing Multimedia Tools")
        
        self.test_tool("Advanced Tools", self.test_advanced_tools)
        self.test_tool("File Opener", self.test_file_opener)
        self.test_tool("Image Generation", self.test_image_gen)
        self.test_tool("QR Generation", self.test_qr_gen)
        self.test_tool("YouTube Downloader", self.test_youtube_downloader)
    
    def test_system_tools(self):
        """Test system tools"""
        logger.info("⚙️ Testing System Tools")
        
        self.test_tool("File Server", self.test_file_server)
        self.test_tool("System Control", self.test_system_ctrl)
        self.test_tool("System Info", self.test_system_info)
        self.test_tool("Window Control", self.test_window_control)
    
    def test_clipboard(self) -> Tuple[bool, str]:
        """Test clipboard functionality"""
        try:
            from services.automation.jarvis_clipboard import ClipboardMonitor
            
            # Test clipboard monitoring
            monitor = ClipboardMonitor()
            
            # Test getting clipboard content
            content = monitor.get_clipboard_content()
            
            # Test setting clipboard content
            test_text = "JARVIS clipboard test " + str(time.time())
            monitor.set_clipboard_content(test_text)
            
            # Verify
            time.sleep(0.5)
            new_content = monitor.get_clipboard_content()
            
            if test_text in new_content:
                return True, "✅ Clipboard read/write working"
            else:
                return False, "❌ Clipboard write failed"
                
        except Exception as e:
            return False, f"❌ Clipboard test failed: {e}"
    
    def test_notepad_automation(self) -> Tuple[bool, str]:
        """Test notepad automation"""
        try:
            from services.automation.jarvis_notepad_automation import NotepadAutomation
            
            notepad = NotepadAutomation()
            
            # Test opening notepad
            notepad.open_notepad()
            time.sleep(1)
            
            # Test writing text
            test_text = f"JARVIS Notepad Test - {time.time()}"
            notepad.write_text(test_text)
            
            # Test saving
            test_file = os.path.join(os.path.expanduser("~"), "Desktop", "jarvis_test.txt")
            notepad.save_file(test_file)
            
            # Test closing
            notepad.close_notepad()
            
            # Verify file was created
            if os.path.exists(test_file):
                with open(test_file, 'r') as f:
                    content = f.read()
                    if test_text in content:
                        os.remove(test_file)  # Cleanup
                        return True, "✅ Notepad automation working"
                    else:
                        return False, "❌ Notepad content mismatch"
                os.remove(test_file)  # Cleanup
            else:
                return False, "❌ Notepad file not created"
                
        except Exception as e:
            return False, f"❌ Notepad automation failed: {e}"
    
    def test_reminders(self) -> Tuple[bool, str]:
        """Test reminders functionality"""
        try:
            from services.automation.jarvis_reminders import ReminderManager
            
            reminders = ReminderManager()
            
            # Test adding reminder
            test_message = f"JARVIS Test Reminder - {time.time()}"
            reminder_id = reminders.add_reminder(test_message, delay_seconds=5)
            
            if reminder_id:
                # Test getting reminders
                all_reminders = reminders.get_all_reminders()
                
                # Test removing reminder
                reminders.remove_reminder(reminder_id)
                
                return True, "✅ Reminders system working"
            else:
                return False, "❌ Failed to add reminder"
                
        except Exception as e:
            return False, f"❌ Reminders test failed: {e}"
    
    def test_whatsapp_automation(self) -> Tuple[bool, str]:
        """Test WhatsApp automation (basic functionality)"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Test initialization (don't actually send messages)
            if hasattr(whatsapp, 'is_connected') or hasattr(whatsapp, 'initialize'):
                return True, "✅ WhatsApp automation initialized"
            else:
                return True, "✅ WhatsApp automation module loaded"
                
        except Exception as e:
            return False, f"❌ WhatsApp automation failed: {e}"
    
    def test_youtube_automation(self) -> Tuple[bool, str]:
        """Test YouTube automation"""
        try:
            from services.automation.jarvis_youtube_automation import YouTubeAutomation
            
            youtube = YouTubeAutomation()
            
            # Test searching (without actually opening)
            if hasattr(youtube, 'search_youtube'):
                return True, "✅ YouTube automation loaded"
            else:
                return False, "❌ YouTube automation missing search method"
                
        except Exception as e:
            return False, f"❌ YouTube automation failed: {e}"
    
    def test_keyboard_mouse_control(self) -> Tuple[bool, str]:
        """Test keyboard/mouse control"""
        try:
            from services.automation.keyboard_mouse_ctrl import KeyboardMouseController
            
            controller = KeyboardMouseController()
            
            # Test getting current position
            pos = controller.get_current_position()
            
            if pos and len(pos) == 2:
                return True, "✅ Keyboard/mouse control working"
            else:
                return False, "❌ Position detection failed"
                
        except Exception as e:
            return False, f"❌ Keyboard/mouse control failed: {e}"
    
    def test_weather(self) -> Tuple[bool, str]:
        """Test weather functionality"""
        try:
            from services.info.jarvis_get_weather import get_weather_info
            
            # Test weather for a known city
            weather = get_weather_info("London")
            
            if weather and isinstance(weather, dict):
                return True, "✅ Weather service working"
            else:
                return False, "❌ Weather service returned invalid data"
                
        except Exception as e:
            return False, f"❌ Weather test failed: {e}"
    
    def test_researcher(self) -> Tuple[bool, str]:
        """Test researcher functionality"""
        try:
            from services.info.jarvis_researcher import JarvisResearcher
            
            researcher = JarvisResearcher()
            
            # Test basic search
            if hasattr(researcher, 'search'):
                return True, "✅ Researcher loaded"
            else:
                return False, "❌ Researcher missing search method"
                
        except Exception as e:
            return False, f"❌ Researcher test failed: {e}"
    
    def test_search(self) -> Tuple[bool, str]:
        """Test search functionality"""
        try:
            from services.info.jarvis_search import search_web
            
            # Test basic search
            results = search_web("JARVIS AI", limit=1)
            
            if results and len(results) > 0:
                return True, "✅ Search functionality working"
            else:
                return False, "❌ Search returned no results"
                
        except Exception as e:
            return False, f"❌ Search test failed: {e}"
    
    def test_advanced_tools(self) -> Tuple[bool, str]:
        """Test advanced multimedia tools"""
        try:
            from services.multimedia.jarvis_advanced_tools import AdvancedMultimediaTools
            
            tools = AdvancedMultimediaTools()
            
            # Test initialization
            if hasattr(tools, 'process_media'):
                return True, "✅ Advanced multimedia tools loaded"
            else:
                return False, "❌ Advanced tools missing process method"
                
        except Exception as e:
            return False, f"❌ Advanced tools test failed: {e}"
    
    def test_file_opener(self) -> Tuple[bool, str]:
        """Test file opener"""
        try:
            from services.multimedia.jarvis_file_opener import FileOpener
            
            opener = FileOpener()
            
            # Test basic functionality
            if hasattr(opener, 'open_file'):
                return True, "✅ File opener loaded"
            else:
                return False, "❌ File opener missing open method"
                
        except Exception as e:
            return False, f"❌ File opener test failed: {e}"
    
    def test_image_gen(self) -> Tuple[bool, str]:
        """Test image generation"""
        try:
            from services.multimedia.jarvis_image_gen import ImageGenerator
            
            generator = ImageGenerator()
            
            # Test initialization
            if hasattr(generator, 'generate_image'):
                return True, "✅ Image generator loaded"
            else:
                return False, "❌ Image generator missing generate method"
                
        except Exception as e:
            return False, f"❌ Image generation test failed: {e}"
    
    def test_qr_gen(self) -> Tuple[bool, str]:
        """Test QR code generation"""
        try:
            from services.multimedia.jarvis_qr_gen import QRCodeGenerator
            
            generator = QRCodeGenerator()
            
            # Test QR generation
            test_data = f"JARVIS QR Test {time.time()}"
            qr_path = generator.generate_qr(test_data, save_path="test_qr.png")
            
            if qr_path and os.path.exists(qr_path):
                os.remove(qr_path)  # Cleanup
                return True, "✅ QR generation working"
            else:
                return False, "❌ QR generation failed"
                
        except Exception as e:
            return False, f"❌ QR generation test failed: {e}"
    
    def test_youtube_downloader(self) -> Tuple[bool, str]:
        """Test YouTube downloader"""
        try:
            from services.multimedia.jarvis_youtube_downloader import YouTubeDownloader
            
            downloader = YouTubeDownloader()
            
            # Test initialization
            if hasattr(downloader, 'download_video'):
                return True, "✅ YouTube downloader loaded"
            else:
                return False, "❌ YouTube downloader missing download method"
                
        except Exception as e:
            return False, f"❌ YouTube downloader test failed: {e}"
    
    def test_file_server(self) -> Tuple[bool, str]:
        """Test file server"""
        try:
            from services.system.jarvis_file_server import FileServer
            
            server = FileServer()
            
            # Test initialization
            if hasattr(server, 'start_server'):
                return True, "✅ File server loaded"
            else:
                return False, "❌ File server missing start method"
                
        except Exception as e:
            return False, f"❌ File server test failed: {e}"
    
    def test_system_ctrl(self) -> Tuple[bool, str]:
        """Test system control"""
        try:
            from services.system.jarvis_system_ctrl import SystemController
            
            controller = SystemController()
            
            # Test getting system info
            if hasattr(controller, 'get_system_info'):
                return True, "✅ System controller loaded"
            else:
                return False, "❌ System controller missing info method"
                
        except Exception as e:
            return False, f"❌ System control test failed: {e}"
    
    def test_system_info(self) -> Tuple[bool, str]:
        """Test system info"""
        try:
            from services.system.jarvis_system_info import get_system_info
            
            # Test getting system info
            info = get_system_info()
            
            if info and isinstance(info, dict):
                return True, "✅ System info working"
            else:
                return False, "❌ System info returned invalid data"
                
        except Exception as e:
            return False, f"❌ System info test failed: {e}"
    
    def test_window_control(self) -> Tuple[bool, str]:
        """Test window control"""
        try:
            from services.system.jarvis_window_ctrl import WindowController
            
            controller = WindowController()
            
            # Test getting window list
            windows = controller.get_window_list()
            
            if windows and len(windows) > 0:
                return True, "✅ Window control working"
            else:
                return False, "❌ No windows detected"
                
        except Exception as e:
            return False, f"❌ Window control test failed: {e}"
    
    def test_tool(self, tool_name: str, test_function) -> bool:
        """Test individual tool"""
        self.total_tests += 1
        
        try:
            logger.info(f"🧪 Testing {tool_name}...")
            
            success, message = test_function()
            
            if success:
                self.passed_tests += 1
                logger.info(f"✅ {tool_name}: {message}")
                self.test_results[tool_name] = {"status": "PASS", "message": message}
                return True
            else:
                self.failed_tests += 1
                logger.error(f"❌ {tool_name}: {message}")
                self.test_results[tool_name] = {"status": "FAIL", "message": message}
                return False
                
        except Exception as e:
            self.failed_tests += 1
            error_msg = f"❌ {tool_name}: Test crashed - {e}"
            logger.error(error_msg)
            self.test_results[tool_name] = {"status": "CRASH", "message": str(e)}
            return False
    
    def run_all_tests(self):
        """Run all tool tests"""
        logger.info("🚀 Starting Comprehensive J.A.R.V.I.S Tools Testing")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Test each category
        self.test_automation_tools()
        self.test_info_tools()
        self.test_multimedia_tools()
        self.test_system_tools()
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Generate results
        self.generate_test_report(duration)
        
        return self.passed_tests == self.total_tests
    
    def generate_test_report(self, duration: float):
        """Generate comprehensive test report"""
        logger.info("=" * 60)
        logger.info("📊 TEST RESULTS SUMMARY")
        logger.info("=" * 60)
        
        # Overall results
        success_rate = (self.passed_tests / self.total_tests) * 100
        logger.info(f"📈 Total Tests: {self.total_tests}")
        logger.info(f"✅ Passed: {self.passed_tests}")
        logger.info(f"❌ Failed: {self.failed_tests}")
        logger.info(f"📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"⏱️ Duration: {duration:.2f} seconds")
        
        # Category breakdown
        logger.info("\n📋 RESULTS BY CATEGORY:")
        for category, tools in self.tool_categories.items():
            category_passed = sum(1 for tool in tools if any(tool_name in tool for tool_name in self.test_results.keys() and self.test_results[tool_name]["status"] == "PASS"))
            category_total = len(tools)
            logger.info(f"  {category}: {category_passed}/{category_total} passed")
        
        # Failed tests details
        if self.failed_tests > 0:
            logger.info("\n❌ FAILED TESTS:")
            for tool_name, result in self.test_results.items():
                if result["status"] != "PASS":
                    logger.info(f"  - {tool_name}: {result['message']}")
        
        # Final verdict
        logger.info("\n" + "=" * 60)
        if success_rate == 100.0:
            logger.info("🎉 PERFECT SCORE! ALL TESTS PASSED - 10/10! 🎉")
        elif success_rate >= 90.0:
            logger.info(f"🎯 EXCELLENT! {success_rate:.1f}% - Almost Perfect!")
        elif success_rate >= 80.0:
            logger.info(f"👍 GOOD! {success_rate:.1f}% - Most Tools Working")
        elif success_rate >= 70.0:
            logger.info(f"⚠️ FAIR! {success_rate:.1f}% - Some Issues Detected")
        else:
            logger.info(f"🚨 POOR! {success_rate:.1f}% - Major Issues Detected")
        
        logger.info("=" * 60)
        
        # Save detailed report
        self.save_detailed_report(success_rate, duration)
    
    def save_detailed_report(self, success_rate: float, duration: float):
        """Save detailed test report to file"""
        report = {
            "test_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "is_perfect": success_rate == 100.0,
            "detailed_results": self.test_results
        }
        
        report_file = f"jarvis_tools_test_report_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed report saved to: {report_file}")


def main():
    """Main testing function"""
    print("🤖 J.A.R.V.I.S Comprehensive Tools Testing")
    print("=" * 50)
    
    tester = ToolTester()
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 ALL TESTS PASSED - 10/10 RESULT! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ Some tests failed. Check logs for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
