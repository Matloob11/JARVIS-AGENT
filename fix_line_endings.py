
import os

def fix_line_endings(directory):
    for root, dirs, files in os.walk(directory):
        if ".git" in root or ".venv" in root or "__pycache__" in root:
            continue
        for file in files:
            if file.endswith((".py", ".md", ".txt", ".json")):
                path = os.path.join(root, file)
                try:
                    with open(path, "rb") as f:
                        content = f.read()
                    # Normalize to \n then to \r\n
                    normalized = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                    if normalized != content:
                        with open(path, "wb") as f:
                            f.write(normalized)
                        print(f"Fixed: {path}")
                except Exception as e:
                    print(f"Error {path}: {e}")

if __name__ == "__main__":
    fix_line_endings(".")
