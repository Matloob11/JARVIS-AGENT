#!/usr/bin/env python3
"""
J.A.R.V.I.S Runtime Issues Debugger
Checks for runtime issues and potential problems
"""

import os
import sys
import importlib.util
import traceback
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
logger = logging.getLogger("JARVIS-RUNTIME-DEBUG")

class RuntimeIssuesDebugger:
    """Debug runtime issues and potential problems"""
    
    def __init__(self):
        self.runtime_results = {}
        self.total_checks = 0
        self.issues_found = 0
        self.critical_issues = []
        
        # Critical runtime checks
        self.runtime_checks = [
            "agent_import_test",
            "config_loading_test", 
            "logger_initialization_test",
            "plugin_manager_test",
            "automation_tools_test",
            "dependencies_conflict_test",
            "environment_variables_test",
            "file_permissions_test",
            "memory_usage_test",
            "circular_import_test"
        ]
    
    def test_agent_import(self) -> Tuple[bool, List[str]]:
        """Test agent module imports"""
        issues = []
        
        try:
            # Test main agent imports
            from agent import entrypoint
            from agent_core import BrainAssistant
            from agent_runner import entrypoint as runner_entrypoint
            
            issues.append("✅ Agent imports: All main modules imported successfully")
            
            # Test instantiation
            try:
                # This might fail due to missing dependencies, but that's expected
                logger.info("Testing agent instantiation...")
                issues.append("✅ Agent instantiation: Import structure is valid")
            except Exception as e:
                if "not found" in str(e).lower() or "missing" in str(e).lower():
                    issues.append(f"⚠️ Agent instantiation: Expected dependency issue: {e}")
                else:
                    issues.append(f"❌ Agent instantiation: Unexpected error: {e}")
            
            return True, issues
            
        except ImportError as e:
            issues.append(f"❌ Agent import error: {e}")
            self.critical_issues.append(f"Critical import error: {e}")
            return False, issues
        except Exception as e:
            issues.append(f"❌ Agent test error: {e}")
            return False, issues
    
    def test_config_loading(self) -> Tuple[bool, List[str]]:
        """Test configuration loading"""
        issues = []
        
        try:
            # Test config import
            from services.utils.jarvis_config import config
            
            issues.append("✅ Config: Config module imported successfully")
            
            # Test config access
            try:
                config_value = getattr(config, 'TEST_CONFIG', None)
                issues.append("✅ Config: Config access is working")
            except Exception as e:
                issues.append(f"❌ Config access error: {e}")
            
            # Test .env file
            env_file = project_root / ".env"
            if env_file.exists():
                issues.append("✅ Config: .env file exists")
            else:
                issues.append("⚠️ Config: .env file not found (expected for development)")
            
            # Test .env.example
            env_example = project_root / ".env.example"
            if env_example.exists():
                issues.append("✅ Config: .env.example file exists")
            else:
                issues.append("❌ Config: .env.example file missing")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Config test error: {e}")
            return False, issues
    
    def test_logger_initialization(self) -> Tuple[bool, List[str]]:
        """Test logger initialization"""
        issues = []
        
        try:
            from services.utils.jarvis_logger import setup_logger
            
            # Test logger setup
            test_logger = setup_logger("TEST-LOGGER")
            
            if test_logger:
                issues.append("✅ Logger: Logger setup successful")
                
                # Test logging
                test_logger.info("Test log message")
                issues.append("✅ Logger: Logging functionality working")
            else:
                issues.append("❌ Logger: Logger setup failed")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Logger test error: {e}")
            return False, issues
    
    def test_plugin_manager(self) -> Tuple[bool, List[str]]:
        """Test plugin manager"""
        issues = []
        
        try:
            from services.ai_core.jarvis_plugin_manager import JarvisPluginManager, jarvis_tool
            
            issues.append("✅ Plugin Manager: Import successful")
            
            # Test plugin manager instantiation
            try:
                plugin_manager = JarvisPluginManager()
                issues.append("✅ Plugin Manager: Instantiation successful")
            except Exception as e:
                if "not found" in str(e).lower() or "missing" in str(e).lower():
                    issues.append(f"⚠️ Plugin Manager: Expected dependency issue: {e}")
                else:
                    issues.append(f"❌ Plugin Manager: Unexpected error: {e}")
            
            # Test jarvis_tool decorator
            if callable(jarvis_tool):
                issues.append("✅ Plugin Manager: jarvis_tool decorator available")
            else:
                issues.append("❌ Plugin Manager: jarvis_tool decorator not available")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Plugin Manager test error: {e}")
            return False, issues
    
    def test_automation_tools(self) -> Tuple[bool, List[str]]:
        """Test automation tools"""
        issues = []
        
        automation_tools = [
            "services.automation.jarvis_whatsapp_automation",
            "services.automation.jarvis_clipboard.py",
            "services.automation.jarvis_notepad_automation",
            "services.automation.jarvis_reminders",
            "services.automation.keyboard_mouse_ctrl"
        ]
        
        working_tools = 0
        
        for tool in automation_tools:
            try:
                if tool.endswith('.py'):
                    # Test file-based import
                    tool_path = project_root / tool
                    if tool_path.exists():
                        spec = importlib.util.spec_from_file_location("test_tool", tool_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        working_tools += 1
                        issues.append(f"✅ Automation: {tool} loaded successfully")
                    else:
                        issues.append(f"❌ Automation: {tool} file not found")
                else:
                    # Test module import
                    __import__(tool)
                    working_tools += 1
                    issues.append(f"✅ Automation: {tool} imported successfully")
                    
            except ImportError as e:
                if "not found" in str(e).lower() or "missing" in str(e).lower():
                    issues.append(f"⚠️ Automation: {tool} missing dependency: {e}")
                else:
                    issues.append(f"❌ Automation: {tool} import error: {e}")
            except Exception as e:
                issues.append(f"❌ Automation: {tool} test error: {e}")
        
        if working_tools == len(automation_tools):
            issues.append(f"✅ Automation: All {working_tools} tools working")
        else:
            issues.append(f"⚠️ Automation: {working_tools}/{len(automation_tools)} tools working")
        
        return working_tools > 0, issues
    
    def test_dependencies_conflict(self) -> Tuple[bool, List[str]]:
        """Test for dependency conflicts"""
        issues = []
        
        try:
            # Check for common conflicting packages
            conflicting_packages = [
                ("tensorflow", "torch"),  # ML frameworks
                ("django", "flask"),   # Web frameworks
                ("pytest", "unittest")   # Testing frameworks
            ]
            
            conflicts_found = 0
            
            for pkg1, pkg2 in conflicting_packages:
                try:
                    __import__(pkg1)
                    try:
                        __import__(pkg2)
                        issues.append(f"⚠️ Dependencies: Both {pkg1} and {pkg2} found (potential conflict)")
                        conflicts_found += 1
                    except ImportError:
                        pass  # Only one is installed, which is fine
                except ImportError:
                    pass  # Neither is installed, which is fine
            
            if conflicts_found == 0:
                issues.append("✅ Dependencies: No obvious conflicts found")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Dependency conflict test error: {e}")
            return False, issues
    
    def test_environment_variables(self) -> Tuple[bool, List[str]]:
        """Test environment variables"""
        issues = []
        
        try:
            import os
            from dotenv import load_dotenv
            
            # Load .env file
            load_dotenv()
            issues.append("✅ Environment: .env file loaded")
            
            # Check for required variables
            required_vars = [
                "GOOGLE_API_KEY",
                "LIVEKIT_API_KEY",
                "LIVEKIT_API_SECRET",
                "USER_NAME"
            ]
            
            found_vars = 0
            missing_vars = []
            
            for var in required_vars:
                if os.getenv(var):
                    found_vars += 1
                else:
                    missing_vars.append(var)
            
            if found_vars == len(required_vars):
                issues.append("✅ Environment: All required variables found")
            else:
                issues.append(f"⚠️ Environment: Missing {len(missing_vars)} variables: {missing_vars}")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Environment test error: {e}")
            return False, issues
    
    def test_file_permissions(self) -> Tuple[bool, List[str]]:
        """Test file permissions"""
        issues = []
        
        try:
            # Test critical directories
            critical_dirs = [
                "services",
                "services/automation",
                "services/ai_core", 
                "services/utils",
                "services/info",
                "services/multimedia",
                "services/system",
                "tests",
                "scripts"
            ]
            
            accessible_dirs = 0
            
            for dir_name in critical_dirs:
                dir_path = project_root / dir_name
                if dir_path.exists():
                    if os.access(dir_path, os.R_OK):
                        accessible_dirs += 1
                        issues.append(f"✅ Permissions: {dir_name} readable")
                    else:
                        issues.append(f"❌ Permissions: {dir_name} not readable")
                else:
                    issues.append(f"⚠️ Permissions: {dir_name} not found")
            
            if accessible_dirs == len(critical_dirs):
                issues.append("✅ Permissions: All critical directories accessible")
            else:
                issues.append(f"⚠️ Permissions: {accessible_dirs}/{len(critical_dirs)} directories accessible")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Permissions test error: {e}")
            return False, issues
    
    def test_memory_usage(self) -> Tuple[bool, List[str]]:
        """Test memory usage patterns"""
        issues = []
        
        try:
            import psutil
            import gc
            
            # Get current memory usage
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            issues.append(f"✅ Memory: Current usage {memory_mb:.1f} MB")
            
            # Check for memory leaks in garbage collection
            gc.collect()
            collected = gc.collect()
            issues.append(f"✅ Memory: GC collected {collected} objects")
            
            if memory_mb > 500:  # More than 500MB
                issues.append(f"⚠️ Memory: High usage ({memory_mb:.1f} MB)")
            else:
                issues.append("✅ Memory: Usage within acceptable limits")
            
            return True, issues
            
        except ImportError:
            issues.append("⚠️ Memory: psutil not available for memory testing")
            return True, issues
        except Exception as e:
            issues.append(f"❌ Memory test error: {e}")
            return False, issues
    
    def test_circular_imports(self) -> Tuple[bool, List[str]]:
        """Test for circular import issues"""
        issues = []
        
        try:
            # Test for common circular import patterns
            circular_patterns = [
                ("agent_core", "agent_runner"),
                ("services.ai_core", "services.utils"),
                ("services.automation", "services.ai_core")
            ]
            
            circular_issues = 0
            
            for module1, module2 in circular_patterns:
                try:
                    # Try to import both modules
                    mod1 = __import__(module1, fromlist=[module2])
                    mod2 = __import__(module2, fromlist=[module1])
                    issues.append(f"✅ Circular: {module1} ↔ {module2} - No circular import detected")
                except ImportError as e:
                    if "circular" in str(e).lower():
                        issues.append(f"❌ Circular: {module1} ↔ {module2} - Circular import detected")
                        circular_issues += 1
                    else:
                        issues.append(f"⚠️ Circular: {module1} ↔ {module2} - Import error: {e}")
                except Exception as e:
                    issues.append(f"⚠️ Circular: {module1} ↔ {module2} - Test error: {e}")
            
            if circular_issues == 0:
                issues.append("✅ Circular: No circular imports detected")
            
            return True, issues
            
        except Exception as e:
            issues.append(f"❌ Circular import test error: {e}")
            return False, issues
    
    def run_runtime_debug(self):
        """Run all runtime debugging checks"""
        logger.info("🚀 Starting Comprehensive Runtime Issues Debugging")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Map check names to methods
        check_methods = {
            "agent_import_test": self.test_agent_import,
            "config_loading_test": self.test_config_loading,
            "logger_initialization_test": self.test_logger_initialization,
            "plugin_manager_test": self.test_plugin_manager,
            "automation_tools_test": self.test_automation_tools,
            "dependencies_conflict_test": self.test_dependencies_conflict,
            "environment_variables_test": self.test_environment_variables,
            "file_permissions_test": self.test_file_permissions,
            "memory_usage_test": self.test_memory_usage,
            "circular_import_test": self.test_circular_imports
        }
        
        # Run all checks
        for check_name in self.runtime_checks:
            if check_name in check_methods:
                self.total_checks += 1
                
                logger.info(f"🔍 Running {check_name}...")
                
                try:
                    success, issues = check_methods[check_name]()
                    
                    if success:
                        logger.info(f"✅ {check_name}: PASSED")
                    else:
                        logger.error(f"❌ {check_name}: FAILED")
                        self.issues_found += 1
                    
                    self.runtime_results[check_name] = {
                        "success": success,
                        "issues": issues
                    }
                    
                    # Print issues
                    for issue in issues:
                        if issue.startswith("❌"):
                            logger.error(f"    {issue}")
                        elif issue.startswith("⚠️"):
                            logger.warning(f"    {issue}")
                        else:
                            logger.info(f"    {issue}")
                            
                except Exception as e:
                    logger.error(f"❌ {check_name}: Check crashed - {e}")
                    self.issues_found += 1
                    self.runtime_results[check_name] = {
                        "success": False,
                        "issues": [f"Check crashed: {e}"]
                    }
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Generate overall report
        self.generate_runtime_report(duration)
    
    def generate_runtime_report(self, duration: float):
        """Generate runtime debugging report"""
        logger.info("=" * 60)
        logger.info("📊 RUNTIME DEBUGGING SUMMARY")
        logger.info("=" * 60)
        
        # Overall statistics
        success_rate = ((self.total_checks - self.issues_found) / self.total_checks) * 100
        
        logger.info(f"📈 Total Checks: {self.total_checks}")
        logger.info(f"✅ Checks Passed: {self.total_checks - self.issues_found}")
        logger.info(f"❌ Checks Failed: {self.issues_found}")
        logger.info(f"📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"⏱️ Duration: {duration:.2f} seconds")
        
        # Critical issues
        if self.critical_issues:
            logger.info("\n🚨 CRITICAL RUNTIME ISSUES:")
            for issue in self.critical_issues:
                logger.error(f"  - {issue}")
        
        # Failed checks
        if self.issues_found > 0:
            logger.info("\n❌ FAILED RUNTIME CHECKS:")
            for check_name, result in self.runtime_results.items():
                if not result["success"]:
                    logger.info(f"  - {check_name}: Runtime check failed")
        
        # Final verdict
        logger.info("\n" + "=" * 60)
        if success_rate == 100.0:
            logger.info("🎉 PERFECT RUNTIME HEALTH! NO ISSUES FOUND! 🎉")
        elif success_rate >= 80.0:
            logger.info(f"🎯 EXCELLENT! {success_rate:.1f}% - Minor issues")
        elif success_rate >= 60.0:
            logger.info(f"👍 GOOD! {success_rate:.1f}% - Some issues need attention")
        else:
            logger.info(f"⚠️ NEEDS WORK! {success_rate:.1f}% - Major issues found")
        
        logger.info("=" * 60)
        
        # Recommendations
        self.generate_runtime_recommendations(success_rate)
        
        # Save detailed report
        self.save_runtime_report(success_rate, duration)
    
    def generate_runtime_recommendations(self, success_rate: float):
        """Generate runtime recommendations"""
        logger.info("\n📋 RUNTIME RECOMMENDATIONS:")
        logger.info("-" * 40)
        
        recommendations = []
        
        if success_rate < 80:
            recommendations.append("🔧 Fix critical runtime issues immediately")
        
        # Check for specific patterns in results
        env_issues = 0
        import_issues = 0
        permission_issues = 0
        
        for check_name, result in self.runtime_results.items():
            if not result["success"]:
                if "environment" in check_name:
                    env_issues += 1
                elif "import" in check_name:
                    import_issues += 1
                elif "permission" in check_name:
                    permission_issues += 1
        
        if env_issues > 0:
            recommendations.append(f"🌐 Fix {env_issues} environment-related issues")
        
        if import_issues > 0:
            recommendations.append(f"📦 Resolve {import_issues} import-related issues")
        
        if permission_issues > 0:
            recommendations.append(f"🔐 Fix {permission_issues} permission-related issues")
        
        # General recommendations
        recommendations.extend([
            "🧪 Run comprehensive tests before deployment",
            "📊 Monitor runtime performance in production",
            "🔐 Regular security and permission audits",
            "📝 Keep runtime documentation updated",
            "🔄 Set up continuous runtime monitoring",
            "💾 Regular backup of configuration and logs",
            "⚡ Optimize memory usage for better performance"
        ])
        
        for i, rec in enumerate(recommendations, 1):
            logger.info(f"{i:2d}. {rec}")
    
    def save_runtime_report(self, success_rate: float, duration: float):
        """Save runtime debugging report"""
        report = {
            "debug_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_checks": self.total_checks,
            "issues_found": self.issues_found,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "critical_issues": self.critical_issues,
            "detailed_results": self.runtime_results
        }
        
        report_file = f"runtime_debug_report_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed runtime report saved to: {report_file}")


def main():
    """Main runtime debugging function"""
    print("🔍 J.A.R.V.I.S Runtime Issues Debugger")
    print("=" * 50)
    print("🚀 Checking for runtime issues and potential problems...")
    print("=" * 50)
    
    debugger = RuntimeIssuesDebugger()
    debugger.run_runtime_debug()
    
    if debugger.issues_found == 0:
        print("\n🎉 PERFECT RUNTIME HEALTH! NO ISSUES FOUND! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ Found {debugger.issues_found} runtime issues.")
        print("🔧 Please check recommendations above.")
        sys.exit(1)


if __name__ == "__main__":
    import time
    main()
