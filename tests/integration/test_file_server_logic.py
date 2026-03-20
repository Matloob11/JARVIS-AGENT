from pathlib import Path
import urllib.parse

def test_translate_path(path):
    # Abandon query parameters
    path_str = path.split('?', 1)[0].split('#', 1)[0]

    # Secure root for D: drive sharing
    root = Path("D:/").resolve()
    print(f"DEBUG: Root resolved to: {root}")

    # Normalize and sanitize the requested path
    requested_path = Path(urllib.parse.unquote(path_str))
    print(f"DEBUG: Requested path: {requested_path}")
    print(f"DEBUG: Requested path parts: {requested_path.parts}")

    # Build the final path relative to D:/ root
    sanitized_parts = [p for p in requested_path.parts if p not in ('/', '\\', '..', 'D:', 'd:')]
    print(f"DEBUG: Sanitized parts: {sanitized_parts}")
    final_path = root.joinpath(*sanitized_parts).resolve()
    print(f"DEBUG: Final path: {final_path}")

    # Final safety check
    if not str(final_path).lower().startswith("d:"):
        print(f"DEBUG: FAILED startswith check: {str(final_path).lower()}")
        return str(root)

    return str(final_path)

print(f"Result for '/': {test_translate_path('/')}")
print(f"Result for '/test.txt': {test_translate_path('/test.txt')}")
print(f"Result for '/Folder/file.png': {test_translate_path('/Folder/file.png')}")
