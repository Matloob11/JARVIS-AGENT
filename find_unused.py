"""
# find_unused.py
Utility to find unused files or code segments in the project.
"""
import ast
import os


def check_unused_imports(file_path):
    """
    Analyzes a Python file for unused imports using AST.

    Args:
        file_path (str): The path to the Python file to analyze.

    Returns:
        list: A list of names of unused imports.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            return []

    imports = []
    used_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.append((name.asname or name.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                imports.append((name.asname or name.name, node.lineno))
        elif isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            curr = node.value
            while isinstance(curr, ast.Attribute):
                curr = curr.value
            if isinstance(curr, ast.Name):
                used_names.add(curr.id)

    unused = [name for name, _ in imports if name not in used_names]
    return unused


def main():
    """
    Scans the current directory for unused imports in Python files.
    """
    for root, _, files in os.walk('.'):
        for file in files:
            if file.endswith('.py') and file != 'find_unused.py':
                path = os.path.join(root, file)
                try:
                    unused = check_unused_imports(path)
                    if unused:
                        print(f"{path}: {', '.join(unused)}")
                except (SyntaxError, OSError) as e:
                    print(f"Error checking {path}: {e}")


if __name__ == "__main__":
    main()
