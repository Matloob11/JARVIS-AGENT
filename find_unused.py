import ast
import os


def check_unused_imports(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())

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
            # This is tricky, but we can check the base name
            curr = node.value
            while isinstance(curr, ast.Attribute):
                curr = curr.value
            if isinstance(curr, ast.Name):
                used_names.add(curr.id)

    unused = [name for name, line in imports if name not in used_names]
    return unused


def main():
    for root, _, files in os.walk('.'):
        for file in files:
            if file.endswith('.py') and file != 'check_unused.py':
                path = os.path.join(root, file)
                try:
                    unused = check_unused_imports(path)
                    if unused:
                        print(f"{path}: {', '.join(unused)}")
                except (SyntaxError, OSError) as e:
                    print(f"Error checking {path}: {e}")


if __name__ == "__main__":
    main()
