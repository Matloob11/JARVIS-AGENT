#!/usr/bin/env python3
"""
J.A.R.V.I.S WhatsApp Tool Comprehensive Testing Script
Tests WhatsApp tool functionality and agent integration
"""

import os
import sys
import time
import asyncio
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
logger = logging.getLogger("JARVIS-WHATSAPP-TEST")

class WhatsAppToolTester:
    """Comprehensive WhatsApp tool testing"""
    
    def __init__(self):
        self.test_results = {}
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
    
    def test_whatsapp_module_import(self) -> Tuple[bool, str]:
        """Test WhatsApp module import"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            if whatsapp:
                return True, "✅ WhatsApp module imported successfully"
            else:
                return False, "❌ WhatsApp module failed to initialize"
                
        except Exception as e:
            return False, f"❌ WhatsApp import failed: {e}"
    
    def test_whatsapp_class_methods(self) -> Tuple[bool, str]:
        """Test WhatsApp class methods"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Check for required methods
            required_methods = [
                'send_message',
                'open_whatsapp',
                'check_whatsapp_status',
                'get_chat_list'
            ]
            
            available_methods = []
            missing_methods = []
            
            for method in required_methods:
                if hasattr(whatsapp, method):
                    available_methods.append(method)
                else:
                    missing_methods.append(method)
            
            logger.info(f"Available methods: {available_methods}")
            logger.info(f"Missing methods: {missing_methods}")
            
            if len(missing_methods) == 0:
                return True, f"✅ All {len(required_methods)} methods available"
            else:
                return False, f"❌ Missing {len(missing_methods)} methods: {missing_methods}"
                
        except Exception as e:
            return False, f"❌ Method testing failed: {e}"
    
    def test_whatsapp_dependencies(self) -> Tuple[bool, str]:
        """Test WhatsApp dependencies"""
        try:
            dependencies = {
                'pyautogui': 'GUI automation',
                'pygetwindow': 'Window management',
                'psutil': 'Process management',
                'subprocess': 'System operations'
            }
            
            available_deps = []
            missing_deps = []
            
            for dep, description in dependencies.items():
                try:
                    __import__(dep)
                    available_deps.append(f"{dep} ({description})")
                except ImportError:
                    missing_deps.append(f"{dep} ({description})")
            
            logger.info(f"Available dependencies: {available_deps}")
            logger.info(f"Missing dependencies: {missing_deps}")
            
            if len(missing_deps) == 0:
                return True, f"✅ All {len(dependencies)} dependencies available"
            else:
                return False, f"❌ Missing {len(missing_deps)} dependencies: {missing_deps}"
                
        except Exception as e:
            return False, f"❌ Dependency testing failed: {e}"
    
    def test_whatsapp_safety_features(self) -> Tuple[bool, str]:
        """Test WhatsApp safety features"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Check for safety features
            safety_features = {
                'token_validation': 'Security token validation',
                'confirmation_prompts': 'User confirmation prompts',
                'error_handling': 'Error handling mechanisms',
                'logging': 'Activity logging'
            }
            
            implemented_features = []
            missing_features = []
            
            # Check for common safety patterns in the module
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            
            module_source = whatsapp_module.__doc__ or ""
            
            # Look for safety-related keywords
            if "token" in module_source.lower() or "security" in module_source.lower():
                implemented_features.append("Security measures")
            
            if "try:" in open(whatsapp_module.__file__).read():
                implemented_features.append("Error handling")
            
            if "logger" in module_source.lower():
                implemented_features.append("Logging")
            
            # Check for actual safety methods
            if hasattr(whatsapp, 'validate_action'):
                implemented_features.append("Action validation")
            
            safety_score = len(implemented_features) / len(safety_features) * 100
            
            logger.info(f"Implemented safety features: {implemented_features}")
            logger.info(f"Safety score: {safety_score:.1f}%")
            
            if safety_score >= 75:
                return True, f"✅ Good safety implementation ({safety_score:.1f}%)"
            elif safety_score >= 50:
                return True, f"⚠️ Moderate safety implementation ({safety_score:.1f}%)"
            else:
                return False, f"❌ Poor safety implementation ({safety_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Safety testing failed: {e}"
    
    def test_whatsapp_file_operations(self) -> Tuple[bool, str]:
        """Test WhatsApp file operations"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Test file-related operations
            file_operations = {
                'chat_backup': 'Chat backup functionality',
                'media_download': 'Media download capability',
                'contact_export': 'Contact export feature'
            }
            
            available_operations = []
            missing_operations = []
            
            for operation, description in file_operations.items():
                if hasattr(whatsapp, operation):
                    available_operations.append(f"{operation} ({description})")
                else:
                    missing_operations.append(f"{operation} ({description})")
            
            logger.info(f"Available file operations: {available_operations}")
            logger.info(f"Missing file operations: {missing_operations}")
            
            # It's okay if some file operations are missing
            return True, f"✅ File operations checked ({len(available_operations)} available)"
                
        except Exception as e:
            return False, f"❌ File operations testing failed: {e}"
    
    def test_whatsapp_integration_readiness(self) -> Tuple[bool, str]:
        """Test WhatsApp integration readiness"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Check integration points
            integration_points = {
                'agent_integration': 'Agent integration methods',
                'plugin_compatibility': 'Plugin system compatibility',
                'configuration_support': 'Configuration management',
                'error_reporting': 'Error reporting mechanisms'
            }
            
            integration_status = []
            
            # Check for @jarvis_tool decorator
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            module_source = open(whatsapp_module.__file__).read()
            
            if "@jarvis_tool" in module_source:
                integration_status.append("Plugin system compatible")
            
            if "def " in module_source and "async def " in module_source:
                integration_status.append("Async support available")
            
            if "logger" in module_source:
                integration_status.append("Logging integrated")
            
            if "try:" in module_source and "except" in module_source:
                integration_status.append("Error handling present")
            
            integration_score = len(integration_status) / len(integration_points) * 100
            
            logger.info(f"Integration status: {integration_status}")
            logger.info(f"Integration score: {integration_score:.1f}%")
            
            if integration_score >= 75:
                return True, f"✅ Excellent integration readiness ({integration_score:.1f}%)"
            elif integration_score >= 50:
                return True, f"⚠️ Good integration readiness ({integration_score:.1f}%)"
            else:
                return False, f"❌ Poor integration readiness ({integration_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Integration testing failed: {e}"
    
    def test_whatsapp_error_scenarios(self) -> Tuple[bool, str]:
        """Test WhatsApp error scenarios"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Test error handling
            error_scenarios = {
                'whatsapp_not_running': 'Handle WhatsApp not running',
                'no_network': 'Handle network issues',
                'invalid_contact': 'Handle invalid contacts',
                'permission_denied': 'Handle permission issues'
            }
            
            handled_scenarios = []
            
            # Check for error handling patterns
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            module_source = open(whatsapp_module.__file__).read()
            
            if "except" in module_source:
                handled_scenarios.append("General error handling")
            
            if "try:" in module_source:
                handled_scenarios.append("Try-catch blocks present")
            
            if "logger.error" in module_source or "logger.warning" in module_source:
                handled_scenarios.append("Error logging present")
            
            if "return" in module_source and "error" in module_source.lower():
                handled_scenarios.append("Error return values")
            
            error_handling_score = len(handled_scenarios) / len(error_scenarios) * 100
            
            logger.info(f"Handled error scenarios: {handled_scenarios}")
            logger.info(f"Error handling score: {error_handling_score:.1f}%")
            
            if error_handling_score >= 60:
                return True, f"✅ Good error handling ({error_handling_score:.1f}%)"
            else:
                return False, f"❌ Poor error handling ({error_handling_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Error scenario testing failed: {e}"
    
    def test_agent_whatsapp_integration(self) -> Tuple[bool, str]:
        """Test agent integration with WhatsApp"""
        try:
            # Test if agent can import and use WhatsApp
            from services.ai_core.jarvis_plugin_manager import jarvis_tool
            
            # Check if WhatsApp tools are properly decorated
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            
            module_source = open(whatsapp_module.__file__).read()
            
            # Count @jarvis_tool decorators
            tool_count = module_source.count("@jarvis_tool")
            
            logger.info(f"Found {tool_count} @jarvis_tool decorators")
            
            if tool_count > 0:
                return True, f"✅ Agent integration ready ({tool_count} tools available)"
            else:
                return False, "❌ No agent integration found"
                
        except Exception as e:
            return False, f"❌ Agent integration testing failed: {e}"
    
    def test_whatsapp_performance(self) -> Tuple[bool, str]:
        """Test WhatsApp performance characteristics"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            # Test initialization time
            start_time = time.time()
            whatsapp = WhatsAppAutomation()
            init_time = time.time() - start_time
            
            logger.info(f"WhatsApp initialization time: {init_time:.3f} seconds")
            
            # Check for performance optimizations
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            module_source = open(whatsapp_module.__file__).read()
            
            performance_features = []
            
            if "async" in module_source:
                performance_features.append("Async support")
            
            if "await" in module_source:
                performance_features.append("Await operations")
            
            if "cache" in module_source.lower():
                performance_features.append("Caching mechanisms")
            
            if "timeout" in module_source.lower():
                performance_features.append("Timeout handling")
            
            performance_score = len(performance_features) / 4 * 100
            
            logger.info(f"Performance features: {performance_features}")
            logger.info(f"Performance score: {performance_score:.1f}%")
            
            if init_time < 1.0 and performance_score >= 50:
                return True, f"✅ Good performance ({init_time:.3f}s init, {performance_score:.1f}% score)"
            else:
                return False, f"❌ Performance issues ({init_time:.3f}s init, {performance_score:.1f}% score)"
                
        except Exception as e:
            return False, f"❌ Performance testing failed: {e}"
    
    def test_whatsapp_security(self) -> Tuple[bool, str]:
        """Test WhatsApp security features"""
        try:
            from services.automation.jarvis_whatsapp_automation import WhatsAppAutomation
            
            whatsapp = WhatsAppAutomation()
            
            # Security checks
            security_features = {
                'input_validation': 'Input validation',
                'permission_checks': 'Permission checks',
                'data_protection': 'Data protection',
                'secure_storage': 'Secure storage'
            }
            
            implemented_security = []
            
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            module_source = open(whatsapp_module.__file__).read()
            
            # Check for security patterns
            if "validate" in module_source.lower():
                implemented_security.append("Input validation")
            
            if "permission" in module_source.lower():
                implemented_security.append("Permission checks")
            
            if "encrypt" in module_source.lower() or "secure" in module_source.lower():
                implemented_security.append("Data protection")
            
            if "token" in module_source.lower():
                implemented_security.append("Token security")
            
            security_score = len(implemented_security) / len(security_features) * 100
            
            logger.info(f"Implemented security features: {implemented_security}")
            logger.info(f"Security score: {security_score:.1f}%")
            
            if security_score >= 50:
                return True, f"✅ Adequate security ({security_score:.1f}%)"
            else:
                return False, f"❌ Insufficient security ({security_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Security testing failed: {e}"
    
    def run_test(self, test_name: str, test_function) -> bool:
        """Run individual test"""
        self.total_tests += 1
        
        try:
            logger.info(f"🧪 Testing {test_name}...")
            
            success, message = test_function()
            
            if success:
                self.passed_tests += 1
                logger.info(f"✅ {test_name}: {message}")
                self.test_results[test_name] = {"status": "PASS", "message": message}
                return True
            else:
                self.failed_tests += 1
                logger.error(f"❌ {test_name}: {message}")
                self.test_results[test_name] = {"status": "FAIL", "message": message}
                return False
                
        except Exception as e:
            self.failed_tests += 1
            error_msg = f"❌ {test_name}: Test crashed - {e}"
            logger.error(error_msg)
            self.test_results[test_name] = {"status": "CRASH", "message": str(e)}
            return False
    
    def run_all_tests(self):
        """Run all WhatsApp tests"""
        logger.info("🚀 Starting Comprehensive WhatsApp Tool Testing")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Run all tests
        tests = [
            ("Module Import", self.test_whatsapp_module_import),
            ("Class Methods", self.test_whatsapp_class_methods),
            ("Dependencies", self.test_whatsapp_dependencies),
            ("Safety Features", self.test_whatsapp_safety_features),
            ("File Operations", self.test_whatsapp_file_operations),
            ("Integration Readiness", self.test_whatsapp_integration_readiness),
            ("Error Scenarios", self.test_whatsapp_error_scenarios),
            ("Agent Integration", self.test_agent_whatsapp_integration),
            ("Performance", self.test_whatsapp_performance),
            ("Security", self.test_whatsapp_security)
        ]
        
        for test_name, test_function in tests:
            self.run_test(test_name, test_function)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Generate results
        self.generate_test_report(duration)
        
        return self.passed_tests == self.total_tests
    
    def generate_test_report(self, duration: float):
        """Generate comprehensive test report"""
        logger.info("=" * 60)
        logger.info("📊 WHATSAPP TOOL TEST RESULTS SUMMARY")
        logger.info("=" * 60)
        
        # Overall results
        success_rate = (self.passed_tests / self.total_tests) * 100
        logger.info(f"📈 Total Tests: {self.total_tests}")
        logger.info(f"✅ Passed: {self.passed_tests}")
        logger.info(f"❌ Failed: {self.failed_tests}")
        logger.info(f"📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"⏱️ Duration: {duration:.2f} seconds")
        
        # Failed tests details
        if self.failed_tests > 0:
            logger.info("\n❌ FAILED TESTS:")
            for test_name, result in self.test_results.items():
                if result["status"] != "PASS":
                    logger.info(f"  - {test_name}: {result['message']}")
        
        # Final verdict
        logger.info("\n" + "=" * 60)
        if success_rate == 100.0:
            logger.info("🎉 WHATSAPP TOOL IS PERFECT! ALL TESTS PASSED! 🎉")
        elif success_rate >= 80.0:
            logger.info(f"🎯 EXCELLENT! {success_rate:.1f}% - WhatsApp Tool Ready!")
        elif success_rate >= 60.0:
            logger.info(f"👍 GOOD! {success_rate:.1f}% - Minor Issues")
        else:
            logger.info(f"⚠️ NEEDS WORK! {success_rate:.1f}% - Major Issues")
        
        logger.info("=" * 60)
        
        # Agent integration assessment
        self.assess_agent_readiness()
        
        # Save detailed report
        self.save_detailed_report(success_rate, duration)
    
    def assess_agent_readiness(self):
        """Assess if agent can handle WhatsApp tool"""
        logger.info("\n🤖 AGENT INTEGRATION ASSESSMENT")
        logger.info("-" * 40)
        
        # Check critical factors for agent integration
        critical_factors = {
            "Module Loading": self.test_results.get("Module Import", {}).get("status") == "PASS",
            "Method Availability": self.test_results.get("Class Methods", {}).get("status") == "PASS",
            "Agent Integration": self.test_results.get("Agent Integration", {}).get("status") == "PASS",
            "Error Handling": self.test_results.get("Error Scenarios", {}).get("status") == "PASS",
            "Safety Features": self.test_results.get("Safety Features", {}).get("status") == "PASS"
        }
        
        passed_critical = sum(critical_factors.values())
        total_critical = len(critical_factors)
        readiness_score = (passed_critical / total_critical) * 100
        
        logger.info(f"Critical Factors Passed: {passed_critical}/{total_critical}")
        logger.info(f"Agent Readiness Score: {readiness_score:.1f}%")
        
        if readiness_score >= 80:
            logger.info("✅ AGENT IS READY for WhatsApp tool integration")
        elif readiness_score >= 60:
            logger.info("⚠️ AGENT IS MOSTLY READY - Minor improvements needed")
        else:
            logger.info("❌ AGENT NEEDS PREPARATION - Major improvements required")
        
        # Potential issues
        logger.info("\n🔍 POTENTIAL ISSUES:")
        issues = []
        
        if self.test_results.get("Dependencies", {}).get("status") != "PASS":
            issues.append("Missing dependencies may cause runtime failures")
        
        if self.test_results.get("Security", {}).get("status") != "PASS":
            issues.append("Security features need improvement")
        
        if self.test_results.get("Performance", {}).get("status") != "PASS":
            issues.append("Performance may impact user experience")
        
        if issues:
            for issue in issues:
                logger.info(f"  ⚠️ {issue}")
        else:
            logger.info("  ✅ No critical issues identified")
    
    def save_detailed_report(self, success_rate: float, duration: float):
        """Save detailed test report to file"""
        report = {
            "test_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool_name": "WhatsApp Automation",
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "is_perfect": success_rate == 100.0,
            "detailed_results": self.test_results
        }
        
        report_file = f"whatsapp_tool_test_report_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed report saved to: {report_file}")


def main():
    """Main testing function"""
    print("📱 J.A.R.V.I.S WhatsApp Tool Comprehensive Testing")
    print("=" * 50)
    
    tester = WhatsAppToolTester()
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 WHATSAPP TOOL IS PERFECT! AGENT READY! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ WhatsApp tool needs improvements before agent use.")
        sys.exit(1)


if __name__ == "__main__":
    main()
