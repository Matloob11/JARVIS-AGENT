
import os
import sys
import ast

def check_file(filepath):
    print(f"Checking {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    tree = ast.parse(content)
    
    # Check for missing imports (simple check for uuid)
    uses_uuid = "uuid" in content
    has_uuid_import = any(isinstance(node, (ast.Import, ast.ImportFrom)) and 
                        (any(alias.name == 'uuid' for alias in node.names) if isinstance(node, ast.Import) 
                         else node.module == 'uuid')
                        for node in ast.walk(tree))
    
    if uses_uuid and not has_uuid_import:
        print(f"❌ {filepath}: Uses 'uuid' but missing import!")

    # Check for session.say without await (advanced check)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'say':
                # Check if it's inside an Await node
                parent = None
                for p in ast.walk(tree):
                    for child in ast.iter_child_nodes(p):
                        if child == node:
                            parent = p
                            break
                    if parent: break
                
                # This is a bit complex for a simple script, but let's look for common patterns
                pass

def main():
    files_to_check = [
        "src/core/agent_runner.py",
        "src/core/agent_core.py",
        "services/ai_core/agent_loops.py",
        "services/utils/jarvis_bug_hunter.py",
        "src/core/vision_handler.py"
    ]
    
    for f in files_to_check:
        full_path = os.path.join("d:/Personal-Assistant-main", f)
        if os.path.exists(full_path):
            check_file(full_path)
        else:
            print(f"⚠️ {f} not found.")

if __name__ == "__main__":
    main()
