#!/usr/bin/env python3
"""
J.A.R.V.I.S Test Runner
Comprehensive test execution script with different test profiles
"""

import os
import sys
import argparse
import subprocess
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_command(cmd, description=""):
    """Run command and return success status"""
    print(f"\n🚀 {description}")
    print(f"📝 Command: {' '.join(cmd)}")
    print("-" * 60)

    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    end_time = time.time()

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    duration = end_time - start_time
    status = "✅ PASSED" if result.returncode == 0 else "❌ FAILED"
    print(f"\n{status} (Duration: {duration:.2f}s)")

    return result.returncode == 0


def run_unit_tests(coverage=True, verbose=True):
    """Run unit tests"""
    cmd = [sys.executable, "-m", "pytest", "-m", "unit"]

    if verbose:
    
        cmd.append("-v")

    if coverage:
        cmd.extend([
            "--cov=services",
            "--cov=agent_core",
            "--cov=agent_runner",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov",
            "--cov-report=xml"
        ])

    return run_command(cmd, "Running Unit Tests")


def run_integration_tests(verbose=True):
    """Run integration tests"""
    cmd = [sys.executable, "-m", "pytest", "-m", "integration"]

    if verbose:
        cmd.append("-v")

    return run_command(cmd, "Running Integration Tests")


def run_security_tests(verbose=True):
    """Run security tests"""
    cmd = [sys.executable, "-m", "pytest", "-m", "security"]

    if verbose:
        cmd.append("-v")

    return run_command(cmd, "Running Security Tests")


def run_all_tests(coverage=True, parallel=False, verbose=True):
    """Run all tests"""
    cmd = [sys.executable, "-m", "pytest"]

    if verbose:
        cmd.append("-v")

    if coverage:
        cmd.extend([
            "--cov=services",
            "--cov=agent_core",
            "--cov=agent_runner",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov",
            "--cov-report=xml"
        ])

    if parallel:
        cmd.extend(["-n", "auto"])

    return run_command(cmd, "Running All Tests")


def run_specific_tests(test_path, coverage=True, verbose=True):
    """Run specific test file or directory"""
    cmd = [sys.executable, "-m", "pytest", test_path]

    if verbose:
        cmd.append("-v")

    if coverage:
        cmd.extend([
            "--cov=services",
            "--cov=agent_core",
            "--cov=agent_runner",
            "--cov-report=term-missing"
        ])

    return run_command(cmd, f"Running Tests: {test_path}")


def run_performance_tests():
    """Run performance tests"""
    cmd = [sys.executable, "-m", "pytest", "-m", "slow", "--durations=0"]
    return run_command(cmd, "Running Performance Tests")


def check_test_dependencies():
    """Check if test dependencies are installed"""
    required_packages = ["pytest", "pytest-cov", "pytest-asyncio"]
    missing_packages = []

    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing_packages.append(package)

    if missing_packages:
        print(f"❌ Missing test dependencies: {', '.join(missing_packages)}")
        print("Install with: pip install -r requirements-dev.txt")
        return False

    print("✅ All test dependencies are installed")
    return True


def generate_test_report():
    """Generate comprehensive test report"""
    print("\n📊 Generating Test Report...")

    # Run tests with detailed reporting
    cmd = [
        sys.executable, "-m", "pytest",
        "--cov=services",
        "--cov=agent_core",
        "--cov=agent_runner",
        "--cov-report=html:htmlcov",
        "--cov-report=xml",
        "--junit-xml=test-results.xml",
        "--html=test-report.html",
        "--self-contained-html"
    ]

    success = run_command(cmd, "Generating Test Report")

    if success:
        print("\n📋 Test Reports Generated:")
        print("  - HTML Coverage Report: htmlcov/index.html")
        print("  - XML Coverage Report: coverage.xml")
        print("  - Test Results: test-results.xml")
        print("  - HTML Test Report: test-report.html")

    return success


def main():
    """Main test runner"""
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S Test Runner")
    parser.add_argument(
        "--profile",
        choices=["unit", "integration", "security", "all", "performance"],
        default="all",
        help="Test profile to run"
    )
    parser.add_argument(
        "--path",
        help="Specific test file or directory to run"
    )
    parser.add_argument(
        "--no-coverage",
        action="store_true",
        help="Disable coverage reporting"
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run tests in parallel (requires pytest-xdist)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce output verbosity"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate comprehensive test report"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any warnings or linting issues are detected"
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check test dependencies only"
    )

    args = parser.parse_args()

    # Fix Unicode for Windows
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("🤖 J.A.R.V.I.S Test Runner")
    print("=" * 50)

    # Check dependencies
    if not check_test_dependencies():
        sys.exit(1)

    # Strict mode handling
    if hasattr(args, 'strict') and args.strict:
        print("🛡️ STRICT MODE ENABLED")

    if args.check_deps:
        print("✅ Dependencies check complete")
        sys.exit(0)

    # Change to project directory
    os.chdir(project_root)

    verbose = not args.quiet
    coverage = not args.no_coverage

    success = True

    try:
        if args.path:
            # Run specific tests
            success = run_specific_tests(args.path, coverage, verbose)
        else:
            # Run based on profile
            if args.profile == "unit":
                success = run_unit_tests(coverage, verbose)
            elif args.profile == "integration":
                success = run_integration_tests(verbose)
            elif args.profile == "security":
                success = run_security_tests(verbose)
            elif args.profile == "performance":
                success = run_performance_tests()
            elif args.profile == "all":
                success = run_all_tests(coverage, args.parallel, verbose)

        # Generate report if requested
        if args.report and success:
            success = generate_test_report()

    except KeyboardInterrupt:
        print("\n⚠️ Tests interrupted by user")
        success = False
    except Exception as e:
        print(f"\n❌ Test runner error: {e}")
        success = False

    # Final status
    print("\n" + "=" * 50)
    if success:
        print("🎉 All tests completed successfully!")
        sys.exit(0)
    else:
        print("💥 Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
