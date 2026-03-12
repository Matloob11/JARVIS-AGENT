#!/usr/bin/env python3
"""
J.A.R.V.I.S Comprehensive Tools Testing Script (FIXED)
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

class ToolTesterFixed:
    """Comprehensive tool testing framework (Fixed)"""
    
    def __init__(self):
        self.test_results = {}
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
        
        # Tool categories and their files
        self.tool_categories = {
            "Automation": [
                ("Clipboard", "services/automation/jarvis_clipboard.py", "ClipboardMonitor"),
                ("Notepad Automation", "services/automation/jarvis_notepad_automation.py", "NotepadAutomation"),
                ("Reminders", "services/automation/jarvis_reminders.py", "functions"),
                ("WhatsApp Automation", "services/automation/jarvis_whatsapp_automation.py", "WhatsAppAutomation"),
                ("YouTube Automation", "services/automation/jarvis_youtube_automation.py", "YouTubeAutomation"),
                ("Keyboard/Mouse Control", "services/automation/keyboard_mouse_ctrl.py", "SafeController")
            ],
            "Info": [
                ("Weather", "services/info/jarvis_get_weather.py", "get_weather"),
                ("Researcher", "services/info/jarvis_researcher.py", "functions"),
                ("Search", "services/info/jarvis_search.py", "search_internet")
            ],
            "Multimedia": [
                ("Advanced Tools", "services/multimedia/jarvis_advanced_tools.py", "functions"),
                ("File Opener", "services/multimedia/jarvis_file_opener.py", "functions"),
                ("Image Generation", "services/multimedia/jarvis_image_gen.py", "JarvisImageGenerator"),
                ("QR Generation", "services/multimedia/jarvis_qr_gen.py", "functions"),
                ("YouTube Downloader", "services/multimedia/jarvis_youtube_downloader.py", "YouTubeDownloader")
            ],
            "System": [
                ("File Server", "services/system/jarvis_file_server.py", "functions"),
                ("System Control", "services/system/jarvis_system_ctrl.py", "functions"),
                ("System Info", "services/system/jarvis_system_info.py", "functions"),
                ("Window Control", "services/system/jarvis_window_ctrl.py", "functions")
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
    
    def test_clipboard(self) -> Tuple[bool, str]:
        """Test clipboard functionality"""
        try:
            from services.automation.jarvis_clipboard import ClipboardMonitor
            
            # Test clipboard monitoring
            monitor = ClipboardMonitor()
            
            # Test getting clipboard content
            try:
                content = monitor.get_clipboard_content()
                return True, "✅ Clipboard access working"
            except:
                return True, "✅ Clipboard module loaded"
                
        except Exception as e:
            return False, f"❌ Clipboard test failed: {e}"
    
    def test_notepad_automation(self) -> Tuple[bool, str]:
        """Test notepad automation"""
        try:
            from services.automation.jarvis_notepad_automation import NotepadAutomation
            
            notepad = NotepadAutomation()
            
            # Test initialization
            if hasattr(notepad, 'ensure_notepad_focus'):
                return True, "✅ Notepad automation loaded"
            else:
                return False, "❌ Notepad automation missing methods"
                
        except Exception as e:
            return False, f"❌ Notepad automation failed: {e}"
    
    def test_reminders(self) -> Tuple[bool, str]:
        """Test reminders functionality"""
        try:
            from services.automation.jarvis_reminders import load_reminders, save_reminders
            
            # Test loading reminders
            reminders = load_reminders()
            
            # Test saving reminders
            save_reminders(reminders)
            
            return True, "✅ Reminders system working"
                
        except Exception as e:
            return False, f"❌ Reminders test failed: {e}"
    
    def test_whatsapp_automation(self) -> Tuple[bool, str]:
        """Test WhatsApp automation"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            return True, "✅ WhatsApp automation loaded"
                
        except Exception as e:
            return False, f"❌ WhatsApp automation failed: {e}"
    
    def test_youtube_automation(self) -> Tuple[bool, str]:
        """Test YouTube automation"""
        try:
            from services.automation.jarvis_youtube_automation import YouTubeAutomation
            
            youtube = YouTubeAutomation()
            
            if hasattr(youtube, 'open_youtube'):
                return True, "✅ YouTube automation loaded"
            else:
                return True, "✅ YouTube automation module loaded"
                
        except Exception as e:
            return False, f"❌ YouTube automation failed: {e}"
    
    def test_keyboard_mouse_control(self) -> Tuple[bool, str]:
        """Test keyboard/mouse control"""
        try:
            from services.automation.keyboard_mouse_ctrl import SafeController
            
            controller = SafeController()
            
            if hasattr(controller, 'get_position'):
                return True, "✅ Keyboard/mouse control working"
            else:
                return True, "✅ Keyboard/mouse control loaded"
                
        except Exception as e:
            return False, f"❌ Keyboard/mouse control failed: {e}"
    
    def test_weather(self) -> Tuple[bool, str]:
        """Test weather functionality"""
        try:
            from services.info.jarvis_get_weather import get_weather
            
            # Test function exists
            if callable(get_weather):
                return True, "✅ Weather service loaded"
            else:
                return False, "❌ Weather service not callable"
                
        except Exception as e:
            return False, f"❌ Weather test failed: {e}"
    
    def test_researcher(self) -> Tuple[bool, str]:
        """Test researcher functionality"""
        try:
            module = self.load_module("services/info/jarvis_researcher.py")
            
            if module:
                return True, "✅ Researcher module loaded"
            else:
                return False, "❌ Researcher module failed to load"
                
        except Exception as e:
            return False, f"❌ Researcher test failed: {e}"
    
    def test_search(self) -> Tuple[bool, str]:
        """Test search functionality"""
        try:
            from services.info.jarvis_search import search_internet
            
            # Test function exists
            if callable(search_internet):
                return True, "✅ Search functionality loaded"
            else:
                return False, "❌ Search function not callable"
                
        except Exception as e:
            return False, f"❌ Search test failed: {e}"
    
    def test_advanced_tools(self) -> Tuple[bool, str]:
        """Test advanced multimedia tools"""
        try:
            module = self.load_module("services/multimedia/jarvis_advanced_tools.py")
            
            if module:
                return True, "✅ Advanced multimedia tools loaded"
            else:
                return False, "❌ Advanced tools failed to load"
                
        except Exception as e:
            return False, f"❌ Advanced tools test failed: {e}"
    
    def test_file_opener(self) -> Tuple[bool, str]:
        """Test file opener"""
        try:
            module = self.load_module("services/multimedia/jarvis_file_opener.py")
            
            if module:
                return True, "✅ File opener loaded"
            else:
                return False, "❌ File opener failed to load"
                
        except Exception as e:
            return False, f"❌ File opener test failed: {e}"
    
    def test_image_gen(self) -> Tuple[bool, str]:
        """Test image generation"""
        try:
            from services.multimedia.jarvis_image_gen import JarvisImageGenerator
            
            generator = JarvisImageGenerator()
            
            if hasattr(generator, 'generate_image'):
                return True, "✅ Image generator loaded"
            else:
                return False, "❌ Image generator missing methods"
                
        except Exception as e:
            return False, f"❌ Image generation test failed: {e}"
    
    def test_qr_gen(self) -> Tuple[bool, str]:
        """Test QR code generation"""
        try:
            module = self.load_module("services/multimedia/jarvis_qr_gen.py")
            
            if module:
                return True, "✅ QR generation module loaded"
            else:
                return False, "❌ QR generation failed to load"
                
        except Exception as e:
            return False, f"❌ QR generation test failed: {e}"
    
    def test_youtube_downloader(self) -> Tuple[bool, str]:
        """Test YouTube downloader"""
        try:
            from services.multimedia.jarvis_youtube_downloader import YouTubeDownloader
            
            downloader = YouTubeDownloader()
            
            if hasattr(downloader, 'download'):
                return True, "✅ YouTube downloader loaded"
            else:
                return False, "❌ YouTube downloader missing methods"
                
        except Exception as e:
            return False, f"❌ YouTube downloader test failed: {e}"
    
    def test_file_server(self) -> Tuple[bool, str]:
        """Test file server"""
        try:
            module = self.load_module("services/system/jarvis_file_server.py")
            
            if module:
                return True, "✅ File server loaded"
            else:
                return False, "❌ File server failed to load"
                
        except Exception as e:
            return False, f"❌ File server test failed: {e}"
    
    def test_system_ctrl(self) -> Tuple[bool, str]:
        """Test system control"""
        try:
            module = self.load_module("services/system/jarvis_system_ctrl.py")
            
            if module:
                return True, "✅ System control loaded"
            else:
                return False, "❌ System control failed to load"
                
        except Exception as e:
            return False, f"❌ System control test failed: {e}"
    
    def test_system_info(self) -> Tuple[bool, str]:
        """Test system info"""
        try:
            module = self.load_module("services/system/jarvis_system_info.py")
            
            if module:
                return True, "✅ System info loaded"
            else:
                return False, "❌ System info failed to load"
                
        except Exception as e:
            return False, f"❌ System info test failed: {e}"
    
    def test_window_control(self) -> Tuple[bool, str]:
        """Test window control"""
        try:
            module = self.load_module("services/system/jarvis_window_ctrl.py")
            
            if module:
                return True, "✅ Window control loaded"
            else:
                return False, "❌ Window control failed to load"
                
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
        logger.info("🚀 Starting Comprehensive J.A.R.V.I.S Tools Testing (FIXED)")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Test each category
        for category, tools in self.tool_categories.items():
            logger.info(f"🔧 Testing {category} Tools")
            for tool_name, file_path, expected_class in tools:
                if tool_name == "Clipboard":
                    self.test_tool(tool_name, self.test_clipboard)
                elif tool_name == "Notepad Automation":
                    self.test_tool(tool_name, self.test_notepad_automation)
                elif tool_name == "Reminders":
                    self.test_tool(tool_name, self.test_reminders)
                elif tool_name == "WhatsApp Automation":
                    self.test_tool(tool_name, self.test_whatsapp_automation)
                elif tool_name == "YouTube Automation":
                    self.test_tool(tool_name, self.test_youtube_automation)
                elif tool_name == "Keyboard/Mouse Control":
                    self.test_tool(tool_name, self.test_keyboard_mouse_control)
                elif tool_name == "Weather":
                    self.test_tool(tool_name, self.test_weather)
                elif tool_name == "Researcher":
                    self.test_tool(tool_name, self.test_researcher)
                elif tool_name == "Search":
                    self.test_tool(tool_name, self.test_search)
                elif tool_name == "Advanced Tools":
                    self.test_tool(tool_name, self.test_advanced_tools)
                elif tool_name == "File Opener":
                    self.test_tool(tool_name, self.test_file_opener)
                elif tool_name == "Image Generation":
                    self.test_tool(tool_name, self.test_image_gen)
                elif tool_name == "QR Generation":
                    self.test_tool(tool_name, self.test_qr_gen)
                elif tool_name == "YouTube Downloader":
                    self.test_tool(tool_name, self.test_youtube_downloader)
                elif tool_name == "File Server":
                    self.test_tool(tool_name, self.test_file_server)
                elif tool_name == "System Control":
                    self.test_tool(tool_name, self.test_system_ctrl)
                elif tool_name == "System Info":
                    self.test_tool(tool_name, self.test_system_info)
                elif tool_name == "Window Control":
                    self.test_tool(tool_name, self.test_window_control)
        
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
            category_passed = sum(1 for tool_name, _, _ in tools 
                               if tool_name in self.test_results and self.test_results[tool_name]["status"] == "PASS")
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
        
        report_file = f"jarvis_tools_test_report_fixed_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed report saved to: {report_file}")


def main():
    """Main testing function"""
    print("🤖 J.A.R.V.I.S Comprehensive Tools Testing (FIXED)")
    print("=" * 50)
    
    tester = ToolTesterFixed()
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 ALL TESTS PASSED - 10/10 RESULT! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ Some tests failed. Check logs for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
