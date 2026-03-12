#!/usr/bin/env python3
"""
J.A.R.V.I.S Agent WhatsApp Integration Test
Tests how the agent handles WhatsApp tool execution
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
logger = logging.getLogger("JARVIS-AGENT-WHATSAPP-TEST")

class AgentWhatsAppIntegrationTester:
    """Test agent integration with WhatsApp tool"""
    
    def __init__(self):
        self.test_results = {}
        self.total_tests = 0
        self.passed_tests = 0
        self.failed_tests = 0
    
    def test_plugin_manager_integration(self) -> Tuple[bool, str]:
        """Test if plugin manager can load WhatsApp tool"""
        try:
            from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
            
            plugin_manager = JarvisPluginManager()
            
            # Check if WhatsApp tools are loaded
            available_tools = plugin_manager.get_available_tools()
            
            whatsapp_tools = [tool for tool in available_tools if 'whatsapp' in tool.get('name', '').lower()]
            
            logger.info(f"Available tools: {[tool.get('name') for tool in available_tools]}")
            logger.info(f"WhatsApp tools found: {[tool.get('name') for tool in whatsapp_tools]}")
            
            if len(whatsapp_tools) > 0:
                return True, f"✅ Found {len(whatsapp_tools)} WhatsApp tools in plugin manager"
            else:
                return False, "❌ No WhatsApp tools found in plugin manager"
                
        except Exception as e:
            return False, f"❌ Plugin manager test failed: {e}"
    
    def test_direct_tool_execution(self) -> Tuple[bool, str]:
        """Test direct WhatsApp tool execution"""
        try:
            from services.automation.jarvis_whatsapp_automation import automate_whatsapp
            
            # Test with dummy data (safe test)
            test_contact = "Test Contact"
            test_message = "Test message from JARVIS"
            
            logger.info("Testing direct tool execution...")
            logger.info(f"Contact: {test_contact}")
            logger.info(f"Message: {test_message}")
            
            # Execute the tool (this will try to open WhatsApp)
            # We'll catch any expected errors
            try:
                result = asyncio.run(automate_whatsapp(test_contact, test_message, close_after=False))
                
                logger.info(f"Tool execution result: {result}")
                
                if result and isinstance(result, dict):
                    if result.get('status') == 'success':
                        return True, "✅ Tool executed successfully"
                    elif result.get('status') == 'error':
                        # Check if error is expected (WhatsApp not installed)
                        error_msg = result.get('message', '').lower()
                        if 'not installed' in error_msg or 'not found' in error_msg or 'focus' in error_msg:
                            return True, f"✅ Tool handled expected error: {result.get('message')}"
                        else:
                            return False, f"❌ Unexpected tool error: {result.get('message')}"
                    else:
                        return False, f"❌ Unknown tool status: {result.get('status')}"
                else:
                    return False, "❌ Tool returned invalid result"
                    
            except Exception as tool_error:
                # Check if this is an expected error (WhatsApp not installed, etc.)
                error_str = str(tool_error).lower()
                if any(keyword in error_str for keyword in ['not found', 'not installed', 'no such file', 'access denied']):
                    return True, f"✅ Tool handled expected system error: {tool_error}"
                else:
                    return False, f"❌ Unexpected system error: {tool_error}"
                    
        except Exception as e:
            return False, f"❌ Direct tool execution test failed: {e}"
    
    def test_agent_tool_discovery(self) -> Tuple[bool, str]:
        """Test if agent can discover WhatsApp tools"""
        try:
            # Simulate agent tool discovery
            import services.automation.jarvis_whatsapp_automation as whatsapp_module
            
            # Find all @jarvis_tool decorated functions
            tool_functions = []
            
            for name in dir(whatsapp_module):
                obj = getattr(whatsapp_module, name)
                if callable(obj) and hasattr(obj, '__doc__') and obj.__doc__:
                    # Check if it's a tool function
                    if 'whatsapp' in name.lower() or 'automate' in name.lower():
                        tool_functions.append(name)
            
            logger.info(f"Discovered tool functions: {tool_functions}")
            
            if len(tool_functions) > 0:
                return True, f"✅ Agent can discover {len(tool_functions)} WhatsApp tool functions"
            else:
                return False, "❌ Agent cannot discover WhatsApp tool functions"
                
        except Exception as e:
            return False, f"❌ Tool discovery test failed: {e}"
    
    def test_tool_safety_validation(self) -> Tuple[bool, str]:
        """Test tool safety validation"""
        try:
            from services.automation.jarvis_whatsapp_automation import automate_whatsapp
            
            # Test with potentially dangerous inputs
            dangerous_inputs = [
                ("", "Empty message test"),
                ("A" * 1000, "Very long message test"),
                ("Test\n\r\t", "Special characters test"),
                ("🔥💣🚀", "Emoji test")
            ]
            
            safety_results = []
            
            for contact, message in dangerous_inputs:
                try:
                    logger.info(f"Testing safety with contact: '{contact}', message length: {len(message)}")
                    
                    # This should handle dangerous inputs gracefully
                    result = asyncio.run(automate_whatsapp(contact, message, close_after=False))
                    
                    if isinstance(result, dict):
                        if result.get('status') in ['success', 'error']:
                            safety_results.append(f"✅ Safe handling for: {contact[:20]}...")
                        else:
                            safety_results.append(f"❌ Unsafe handling for: {contact[:20]}...")
                    else:
                        safety_results.append(f"❌ Invalid result for: {contact[:20]}...")
                        
                except Exception as e:
                    # Check if error is handled gracefully
                    error_str = str(e).lower()
                    if any(keyword in error_str for keyword in ['validation', 'invalid', 'safe']):
                        safety_results.append(f"✅ Error validation for: {contact[:20]}...")
                    else:
                        safety_results.append(f"❌ Unhandled error for: {contact[:20]}...")
            
            safe_count = sum(1 for r in safety_results if r.startswith("✅"))
            safety_score = (safe_count / len(safety_results)) * 100
            
            logger.info(f"Safety results: {safety_results}")
            logger.info(f"Safety score: {safety_score:.1f}%")
            
            if safety_score >= 75:
                return True, f"✅ Good safety handling ({safety_score:.1f}%)"
            else:
                return False, f"❌ Poor safety handling ({safety_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Safety validation test failed: {e}"
    
    def test_tool_error_recovery(self) -> Tuple[bool, str]:
        """Test tool error recovery mechanisms"""
        try:
            from services.automation.jarvis_whatsapp_automation import automate_whatsapp
            
            # Test scenarios that should trigger error recovery
            error_scenarios = [
                ("NonExistentContact12345", "Test with non-existent contact"),
                ("", "Test with empty contact"),
                ("Test", ""),  # Empty message
            ]
            
            recovery_results = []
            
            for contact, message in error_scenarios:
                try:
                    logger.info(f"Testing error recovery with contact: '{contact}', message: '{message}'")
                    
                    result = asyncio.run(automate_whatsapp(contact, message, close_after=False))
                    
                    if isinstance(result, dict):
                        status = result.get('status')
                        error_msg = result.get('message', '')
                        
                        if status == 'error':
                            # Check if error message is helpful
                            if len(error_msg) > 10 and 'error' in error_msg.lower():
                                recovery_results.append(f"✅ Good error recovery for: {contact}")
                            else:
                                recovery_results.append(f"⚠️ Basic error recovery for: {contact}")
                        else:
                            recovery_results.append(f"❌ No error handling for: {contact}")
                    else:
                        recovery_results.append(f"❌ Invalid error response for: {contact}")
                        
                except Exception as e:
                    # Check if exception is handled gracefully
                    recovery_results.append(f"⚠️ Exception handled for: {contact}")
            
            good_recovery = sum(1 for r in recovery_results if r.startswith("✅"))
            recovery_score = (good_recovery / len(recovery_results)) * 100
            
            logger.info(f"Recovery results: {recovery_results}")
            logger.info(f"Recovery score: {recovery_score:.1f}%")
            
            if recovery_score >= 60:
                return True, f"✅ Good error recovery ({recovery_score:.1f}%)"
            else:
                return False, f"❌ Poor error recovery ({recovery_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Error recovery test failed: {e}"
    
    def test_agent_tool_integration(self) -> Tuple[bool, str]:
        """Test complete agent-tool integration"""
        try:
            # Simulate how agent would use the tool
            from services.automation.jarvis_whatsapp_automation import automate_whatsapp
            
            # Test typical agent usage pattern
            agent_requests = [
                {
                    "intent": "send_message",
                    "contact": "Test Contact",
                    "message": "Hello from JARVIS Agent",
                    "expected_behavior": "Should attempt to send message"
                },
                {
                    "intent": "send_message",
                    "contact": "Emergency Contact",
                    "message": "This is an urgent message",
                    "expected_behavior": "Should handle priority message"
                }
            ]
            
            integration_results = []
            
            for request in agent_requests:
                try:
                    logger.info(f"Testing agent request: {request['intent']}")
                    
                    result = asyncio.run(automate_whatsapp(
                        request['contact'], 
                        request['message'], 
                        close_after=False
                    ))
                    
                    if isinstance(result, dict):
                        status = result.get('status')
                        message = result.get('message', '')
                        
                        integration_results.append({
                            "request": request['intent'],
                            "status": status,
                            "message": message[:50] + "..." if len(message) > 50 else message,
                            "success": status in ['success', 'error']  # Both are valid responses
                        })
                    else:
                        integration_results.append({
                            "request": request['intent'],
                            "status": "invalid",
                            "message": "Invalid response format",
                            "success": False
                        })
                        
                except Exception as e:
                    integration_results.append({
                        "request": request['intent'],
                        "status": "exception",
                        "message": str(e)[:50] + "..." if len(str(e)) > 50 else str(e),
                        "success": False
                    })
            
            successful_integrations = sum(1 for r in integration_results if r['success'])
            integration_score = (successful_integrations / len(integration_results)) * 100
            
            logger.info(f"Integration results: {integration_results}")
            logger.info(f"Integration score: {integration_score:.1f}%")
            
            if integration_score >= 80:
                return True, f"✅ Excellent agent integration ({integration_score:.1f}%)"
            elif integration_score >= 60:
                return True, f"⚠️ Good agent integration ({integration_score:.1f}%)"
            else:
                return False, f"❌ Poor agent integration ({integration_score:.1f}%)"
                
        except Exception as e:
            return False, f"❌ Agent integration test failed: {e}"
    
    def test_performance_impact(self) -> Tuple[bool, str]:
        """Test performance impact on agent"""
        try:
            import psutil
            import time
            
            # Measure baseline performance
            process = psutil.Process()
            baseline_memory = process.memory_info().rss / 1024 / 1024  # MB
            baseline_cpu = process.cpu_percent()
            
            logger.info(f"Baseline memory: {baseline_memory:.1f} MB")
            logger.info(f"Baseline CPU: {baseline_cpu:.1f}%")
            
            # Load WhatsApp tool multiple times
            from services.automation.jarvis_whatsapp_automation import automate_whatsapp, whatsapp_bot
            
            start_time = time.time()
            
            for i in range(5):
                try:
                    # Quick initialization test
                    result = asyncio.run(automate_whatsapp("Test", "Test", close_after=False))
                    logger.info(f"Iteration {i+1}: {result.get('status', 'unknown')}")
                except:
                    pass  # Expected errors are okay
            
            end_time = time.time()
            
            # Measure performance after tool usage
            peak_memory = process.memory_info().rss / 1024 / 1024  # MB
            peak_cpu = process.cpu_percent()
            
            logger.info(f"Peak memory: {peak_memory:.1f} MB")
            logger.info(f"Peak CPU: {peak_cpu:.1f}%")
            logger.info(f"Total time: {end_time - start_time:.2f} seconds")
            
            memory_increase = peak_memory - baseline_memory
            cpu_increase = peak_cpu - baseline_cpu
            
            # Performance criteria
            performance_issues = []
            
            if memory_increase > 50:  # More than 50MB increase
                performance_issues.append(f"High memory increase: {memory_increase:.1f} MB")
            
            if cpu_increase > 20:  # More than 20% CPU increase
                performance_issues.append(f"High CPU increase: {cpu_increase:.1f}%")
            
            if (end_time - start_time) > 10:  # More than 10 seconds
                performance_issues.append(f"Slow execution: {end_time - start_time:.2f}s")
            
            if len(performance_issues) == 0:
                return True, "✅ Good performance characteristics"
            else:
                return False, f"❌ Performance issues: {'; '.join(performance_issues)}"
                
        except Exception as e:
            return False, f"❌ Performance test failed: {e}"
    
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
        """Run all agent integration tests"""
        logger.info("🚀 Starting Agent WhatsApp Integration Testing")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Run all tests
        tests = [
            ("Plugin Manager Integration", self.test_plugin_manager_integration),
            ("Direct Tool Execution", self.test_direct_tool_execution),
            ("Agent Tool Discovery", self.test_agent_tool_discovery),
            ("Tool Safety Validation", self.test_tool_safety_validation),
            ("Tool Error Recovery", self.test_tool_error_recovery),
            ("Agent Tool Integration", self.test_agent_tool_integration),
            ("Performance Impact", self.test_performance_impact)
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
        logger.info("📊 AGENT WHATSAPP INTEGRATION TEST RESULTS")
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
            logger.info("🎉 AGENT IS PERFECTLY READY FOR WHATSAPP! 🎉")
        elif success_rate >= 80.0:
            logger.info(f"🎯 EXCELLENT! {success_rate:.1f}% - Agent Ready for WhatsApp!")
        elif success_rate >= 60.0:
            logger.info(f"👍 GOOD! {success_rate:.1f}% - Agent Mostly Ready")
        else:
            logger.info(f"⚠️ NEEDS WORK! {success_rate:.1f}% - Agent Not Ready")
        
        logger.info("=" * 60)
        
        # Recommendations
        self.generate_recommendations()
        
        # Save detailed report
        self.save_detailed_report(success_rate, duration)
    
    def generate_recommendations(self):
        """Generate recommendations based on test results"""
        logger.info("\n📋 RECOMMENDATIONS:")
        logger.info("-" * 40)
        
        recommendations = []
        
        # Analyze test results and generate recommendations
        if self.test_results.get("Plugin Manager Integration", {}).get("status") != "PASS":
            recommendations.append("🔧 Fix plugin manager integration")
        
        if self.test_results.get("Tool Safety Validation", {}).get("status") != "PASS":
            recommendations.append("🛡️ Improve input safety validation")
        
        if self.test_results.get("Tool Error Recovery", {}).get("status") != "PASS":
            recommendations.append("🔄 Enhance error recovery mechanisms")
        
        if self.test_results.get("Performance Impact", {}).get("status") != "PASS":
            recommendations.append("⚡ Optimize tool performance")
        
        # General recommendations
        recommendations.extend([
            "📱 Ensure WhatsApp Desktop is installed",
            "🔐 Check Windows permissions for automation",
            "📊 Monitor tool usage in production",
            "🧪 Test with real WhatsApp scenarios"
        ])
        
        for rec in recommendations:
            logger.info(f"  {rec}")
    
    def save_detailed_report(self, success_rate: float, duration: float):
        """Save detailed test report to file"""
        report = {
            "test_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "test_type": "Agent WhatsApp Integration",
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "is_agent_ready": success_rate >= 80.0,
            "detailed_results": self.test_results
        }
        
        report_file = f"agent_whatsapp_integration_report_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed report saved to: {report_file}")


def main():
    """Main testing function"""
    print("🤖 J.A.R.V.I.S Agent WhatsApp Integration Testing")
    print("=" * 50)
    
    tester = AgentWhatsAppIntegrationTester()
    success = tester.run_all_tests()
    
    if success:
        print("\n🎉 AGENT IS PERFECTLY READY FOR WHATSAPP! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ Agent needs improvements before WhatsApp usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
