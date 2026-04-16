import subprocess
import os

def run_pylint():
    print("--- Pylint Project Audit ---")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pylint_config = os.path.join(project_root, ".pylintrc")

    # Target directories
    targets = ["services", "agent_core.py", "agent_runner.py", "ui_bridge.py"]

    command = [
        "pylint",
        f"--rcfile={pylint_config}",
        "--output-format=colorized",
        "--score=yes"
    ] + targets

    try:
        print(f"Running: {' '.join(command)}")
        # Using subprocess to capture output and return code
        result = subprocess.run(command, cwd=project_root, capture_output=True, text=True)

        print("\n--- Results ---\n")
        print(result.stdout)

        if result.stderr:
            print("\n--- Errors/Warnings ---\n")
            print(result.stderr)

        if result.returncode == 0:
            print("\nSUCCESS: No critical lint issues detected.")
        else:
            print(f"\nAUDIT: Pylint found issues (Return Code: {result.returncode}).")

    except FileNotFoundError:
        print("ERROR: pylint not found. Please install it using 'pip install pylint'.")
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")

if __name__ == "__main__":
    run_pylint()
