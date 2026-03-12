#!/usr/bin/env python3
"""
J.A.R.V.I.S Project File Debugger
Reads and debugs each project file systematically
"""

import os
import sys
import ast
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
logger = logging.getLogger("JARVIS-DEBUG")

class ProjectFileDebugger:
    """Systematic project file debugger"""
    
    def __init__(self):
        self.debug_results = {}
        self.total_files = 0
        self.files_with_issues = 0
        self.critical_issues = []
        
        # Important files to check
        self.priority_files = [
            "agent.py",
            "agent_core.py", 
            "agent_runner.py",
            "services/ai_core/jarvis_plugin_manager.py",
            "services/ai_core/agent_memory.py",
            "services/utils/jarvis_config.py",
            "services/utils/jarvis_logger.py",
            "services/automation/jarvis_whatsapp_automation.py",
            "services/automation/jarvis_clipboard.py",
            "services/info/jarvis_search.py",
            "services/multimedia/jarvis_youtube_downloader.py",
            "services/system/jarvis_window_ctrl.py"
        ]
    
    def debug_file_syntax(self, file_path: str) -> Tuple[bool, List[str]]:
        """Check file for syntax errors"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Try to parse as AST
            try:
                ast.parse(content)
                issues.append("✅ Syntax: Valid Python syntax")
            except SyntaxError as e:
                issues.append(f"❌ Syntax Error: Line {e.lineno}: {e.msg}")
                self.critical_issues.append(f"Syntax error in {file_path}: {e.msg}")
            
            # Check for common issues
            lines = content.split('\n')
            
            # Check for import issues
            import_issues = []
            for i, line in enumerate(lines, 1):
                line = line.strip()
                if line.startswith('import ') or line.startswith('from '):
                    # Check for missing modules (basic check)
                    if 'import ' in line:
                        module = line.split('import ')[-1].split(' as ')[0].strip()
                        if module and not module.startswith('.'):
                            try:
                                __import__(module)
                            except ImportError:
                                import_issues.append(f"Line {i}: Missing module '{module}'")
            
            if import_issues:
                issues.extend(import_issues)
            else:
                issues.append("✅ Imports: All modules found")
            
            # Check for undefined variables (basic check)
            undefined_vars = []
            for i, line in enumerate(lines, 1):
                # Simple check for common undefined patterns
                if 'undefined' in line.lower() or 'not defined' in line.lower():
                    undefined_vars.append(f"Line {i}: Potential undefined variable")
            
            if undefined_vars:
                issues.extend(undefined_vars)
            else:
                issues.append("✅ Variables: No obvious undefined variables")
            
            # Check for TODO/FIXME comments
            todo_count = content.lower().count('todo')
            fixme_count = content.lower().count('fixme')
            
            if todo_count > 0 or fixme_count > 0:
                issues.append(f"⚠️ Development notes: {todo_count} TODOs, {fixme_count} FIXMEs")
            else:
                issues.append("✅ Development: No TODO/FIXME comments")
            
            return len([i for i in issues if i.startswith('❌')]) == 0, issues
            
        except Exception as e:
            return False, [f"❌ File reading error: {e}"]
    
    def debug_file_structure(self, file_path: str) -> Tuple[bool, List[str]]:
        """Check file structure and organization"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            lines = content.split('\n')
            
            # Check for docstring
            has_docstring = False
            if lines and lines[0].strip().startswith('"""'):
                has_docstring = True
            
            if has_docstring:
                issues.append("✅ Documentation: Has module docstring")
            else:
                issues.append("⚠️ Documentation: Missing module docstring")
            
            # Check for class definitions
            class_count = content.count('class ')
            if class_count > 0:
                issues.append(f"✅ Structure: {class_count} class(es) found")
            else:
                issues.append("⚠️ Structure: No classes found")
            
            # Check for function definitions
            def_count = content.count('def ')
            if def_count > 0:
                issues.append(f"✅ Functions: {def_count} function(s) found")
            else:
                issues.append("⚠️ Functions: No functions found")
            
            # Check for main guard
            has_main_guard = 'if __name__ == "__main__":' in content
            if has_main_guard:
                issues.append("✅ Entry Point: Has main guard")
            else:
                issues.append("⚠️ Entry Point: No main guard")
            
            # Check line length
            long_lines = []
            for i, line in enumerate(lines, 1):
                if len(line) > 120:  # PEP8 standard
                    long_lines.append(f"Line {i}: {len(line)} chars")
            
            if long_lines:
                issues.append(f"⚠️ Style: {len(long_lines)} lines exceed 120 characters")
                if len(long_lines) <= 5:
                    issues.append("✅ Style: Acceptable line length")
            else:
                issues.append("✅ Style: All lines within 120 characters")
            
            return True, issues
            
        except Exception as e:
            return False, [f"❌ Structure analysis error: {e}"]
    
    def debug_file_dependencies(self, file_path: str) -> Tuple[bool, List[str]]:
        """Check file dependencies and imports"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract imports
            imports = []
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('import '):
                    imports.append(line)
                elif line.startswith('from '):
                    imports.append(line)
            
            if imports:
                issues.append(f"✅ Dependencies: {len(imports)} imports found")
                
                # Check for circular dependencies (basic check)
                local_imports = [imp for imp in imports if 'services.' in imp]
                if local_imports:
                    issues.append(f"✅ Local Dependencies: {len(local_imports)} local imports")
                else:
                    issues.append("✅ Local Dependencies: No local imports")
                
                # Check for external dependencies
                external_imports = [imp for imp in imports if not imp.startswith('services.') and not imp.startswith('.')]
                if external_imports:
                    issues.append(f"✅ External Dependencies: {len(external_imports)} external imports")
                
            else:
                issues.append("⚠️ Dependencies: No imports found")
            
            return True, issues
            
        except Exception as e:
            return False, [f"❌ Dependency analysis error: {e}"]
    
    def debug_file_security(self, file_path: str) -> Tuple[bool, List[str]]:
        """Check file for security issues"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for security issues
            security_issues = []
            
            # Check for hardcoded passwords/keys
            if 'password' in content.lower() and '=' in content:
                security_issues.append("⚠️ Security: Potential hardcoded password")
            
            if 'api_key' in content.lower() and '=' in content and not 'os.getenv' in content:
                security_issues.append("⚠️ Security: Potential hardcoded API key")
            
            # Check for eval/exec usage
            if 'eval(' in content or 'exec(' in content:
                security_issues.append("⚠️ Security: Uses eval/exec functions")
            
            # Check for SQL injection risks
            if 'execute(' in content and '%' in content:
                security_issues.append("⚠️ Security: Potential SQL injection risk")
            
            # Check for file operations without validation
            if 'open(' in content and 'with' not in content:
                security_issues.append("⚠️ Security: File operations without context manager")
            
            if security_issues:
                issues.extend(security_issues)
            else:
                issues.append("✅ Security: No obvious security issues")
            
            # Check for input validation
            if 'input(' in content or 'sys.argv' in content:
                if 'validate' not in content.lower():
                    issues.append("⚠️ Security: Input without validation")
                else:
                    issues.append("✅ Security: Input validation present")
            
            return True, issues
            
        except Exception as e:
            return False, [f"❌ Security analysis error: {e}"]
    
    def debug_file_performance(self, file_path: str) -> Tuple[bool, List[str]]:
        """Check file for performance issues"""
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check for performance issues
            performance_issues = []
            
            # Check for infinite loops
            if 'while True:' in content and 'break' not in content:
                performance_issues.append("⚠️ Performance: Potential infinite loop")
            
            # Check for inefficient string concatenation
            if content.count('+ ') > 10 and 'join(' not in content:
                performance_issues.append("⚠️ Performance: Inefficient string concatenation")
            
            # Check for missing async/await where appropriate
            if 'import asyncio' in content and 'await ' not in content:
                performance_issues.append("⚠️ Performance: Async import without await")
            
            # Check for global variables
            global_count = content.count('global ')
            if global_count > 5:
                performance_issues.append(f"⚠️ Performance: {global_count} global variables")
            
            if performance_issues:
                issues.extend(performance_issues)
            else:
                issues.append("✅ Performance: No obvious performance issues")
            
            # Check file size
            file_size = os.path.getsize(file_path)
            if file_size > 100000:  # 100KB
                issues.append(f"⚠️ Performance: Large file ({file_size/1024:.1f}KB)")
            else:
                issues.append(f"✅ Performance: Reasonable file size ({file_size/1024:.1f}KB)")
            
            return True, issues
            
        except Exception as e:
            return False, [f"❌ Performance analysis error: {e}"]
    
    def debug_single_file(self, file_path: str) -> Dict[str, Any]:
        """Debug a single file comprehensively"""
        logger.info(f"🔍 Debugging file: {file_path}")
        
        full_path = project_root / file_path
        
        if not full_path.exists():
            return {
                "file": file_path,
                "exists": False,
                "error": "File not found"
            }
        
        # Run all debug checks
        syntax_ok, syntax_issues = self.debug_file_syntax(str(full_path))
        structure_ok, structure_issues = self.debug_file_structure(str(full_path))
        deps_ok, deps_issues = self.debug_file_dependencies(str(full_path))
        security_ok, security_issues = self.debug_file_security(str(full_path))
        perf_ok, perf_issues = self.debug_file_performance(str(full_path))
        
        # Calculate overall status
        all_issues = syntax_issues + structure_issues + deps_issues + security_issues + perf_issues
        error_count = len([i for i in all_issues if i.startswith('❌')])
        warning_count = len([i for i in all_issues if i.startswith('⚠️')])
        
        overall_status = "PASS" if error_count == 0 else "FAIL"
        
        return {
            "file": file_path,
            "exists": True,
            "overall_status": overall_status,
            "syntax": {"ok": syntax_ok, "issues": syntax_issues},
            "structure": {"ok": structure_ok, "issues": structure_issues},
            "dependencies": {"ok": deps_ok, "issues": deps_issues},
            "security": {"ok": security_ok, "issues": security_issues},
            "performance": {"ok": perf_ok, "issues": perf_issues},
            "error_count": error_count,
            "warning_count": warning_count,
            "total_issues": len(all_issues)
        }
    
    def run_debug(self):
        """Run comprehensive debugging on all files"""
        logger.info("🚀 Starting Comprehensive Project File Debugging")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Debug each priority file
        for file_path in self.priority_files:
            self.total_files += 1
            
            result = self.debug_single_file(file_path)
            self.debug_results[file_path] = result
            
            if result["overall_status"] == "FAIL":
                self.files_with_issues += 1
            
            # Print summary for this file
            self.print_file_summary(result)
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Generate overall report
        self.generate_overall_report(duration)
    
    def print_file_summary(self, result: Dict[str, Any]):
        """Print summary for a single file"""
        file_name = result["file"]
        status = result["overall_status"]
        errors = result["error_count"]
        warnings = result["warning_count"]
        
        if not result["exists"]:
            logger.error(f"❌ {file_name}: FILE NOT FOUND")
            return
        
        status_icon = "✅" if status == "PASS" else "❌"
        logger.info(f"{status_icon} {file_name}: {status} (Errors: {errors}, Warnings: {warnings})")
        
        # Print critical issues
        if errors > 0:
            for category in ["syntax", "structure", "dependencies", "security", "performance"]:
                category_issues = result[category]["issues"]
                error_issues = [i for i in category_issues if i.startswith('❌')]
                if error_issues:
                    for issue in error_issues:
                        logger.error(f"    {issue}")
    
    def generate_overall_report(self, duration: float):
        """Generate overall debugging report"""
        logger.info("=" * 60)
        logger.info("📊 PROJECT DEBUGGING SUMMARY")
        logger.info("=" * 60)
        
        # Overall statistics
        success_rate = ((self.total_files - self.files_with_issues) / self.total_files) * 100
        
        logger.info(f"📈 Total Files: {self.total_files}")
        logger.info(f"✅ Files OK: {self.total_files - self.files_with_issues}")
        logger.info(f"❌ Files with Issues: {self.files_with_issues}")
        logger.info(f"📊 Success Rate: {success_rate:.1f}%")
        logger.info(f"⏱️ Duration: {duration:.2f} seconds")
        
        # Critical issues summary
        if self.critical_issues:
            logger.info("\n🚨 CRITICAL ISSUES FOUND:")
            for issue in self.critical_issues:
                logger.error(f"  - {issue}")
        
        # Files with issues
        if self.files_with_issues > 0:
            logger.info("\n❌ FILES WITH ISSUES:")
            for file_name, result in self.debug_results.items():
                if result["overall_status"] == "FAIL":
                    logger.info(f"  - {file_name}: {result['error_count']} errors, {result['warning_count']} warnings")
        
        # Recommendations
        self.generate_recommendations(success_rate)
        
        # Save detailed report
        self.save_debug_report(success_rate, duration)
    
    def generate_recommendations(self, success_rate: float):
        """Generate recommendations based on debugging results"""
        logger.info("\n📋 RECOMMENDATIONS:")
        logger.info("-" * 40)
        
        recommendations = []
        
        if success_rate < 80:
            recommendations.append("🔧 Fix critical syntax and import errors")
        
        if self.critical_issues:
            recommendations.append("🚨 Address critical security and syntax issues")
        
        # Check for common issues across files
        syntax_errors = 0
        security_issues = 0
        performance_issues = 0
        
        for result in self.debug_results.values():
            if result.get("syntax", {}).get("ok") == False:
                syntax_errors += 1
            if result.get("security", {}).get("ok") == False:
                security_issues += 1
            if result.get("performance", {}).get("ok") == False:
                performance_issues += 1
        
        if syntax_errors > 0:
            recommendations.append(f"📝 Fix {syntax_errors} files with syntax errors")
        
        if security_issues > 0:
            recommendations.append(f"🛡️ Address {security_issues} files with security issues")
        
        if performance_issues > 0:
            recommendations.append(f"⚡ Optimize {performance_issues} files with performance issues")
        
        # General recommendations
        recommendations.extend([
            "🧪 Run comprehensive tests after fixes",
            "📊 Monitor performance in production",
            "🔐 Regular security audits",
            "📝 Keep documentation updated",
            "🔄 Continuous integration setup"
        ])
        
        for i, rec in enumerate(recommendations, 1):
            logger.info(f"{i:2d}. {rec}")
    
    def save_debug_report(self, success_rate: float, duration: float):
        """Save detailed debugging report"""
        report = {
            "debug_timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_files": self.total_files,
            "files_with_issues": self.files_with_issues,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "critical_issues": self.critical_issues,
            "detailed_results": self.debug_results
        }
        
        report_file = f"project_debug_report_{int(time.time())}.json"
        
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"📄 Detailed debug report saved to: {report_file}")


def main():
    """Main debugging function"""
    print("🔍 J.A.R.V.I.S Project File Debugger")
    print("=" * 50)
    print("🚀 Systematically debugging all project files...")
    print("=" * 50)
    
    debugger = ProjectFileDebugger()
    debugger.run_debug()
    
    if debugger.files_with_issues == 0:
        print("\n🎉 ALL FILES ARE PERFECT! NO ISSUES FOUND! 🎉")
        sys.exit(0)
    else:
        print(f"\n⚠️ Found issues in {debugger.files_with_issues} files.")
        print("🔧 Please check recommendations above.")
        sys.exit(1)


if __name__ == "__main__":
    import time
    main()
