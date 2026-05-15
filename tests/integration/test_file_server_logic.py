from pathlib import Path
import urllib.parse


def translate_path(path):
    path_str = path.split('?', 1)[0].split('#', 1)[0]
    root = Path("D:/").resolve()
    requested_path = Path(urllib.parse.unquote(path_str))
    sanitized_parts = [p for p in requested_path.parts if p not in ('/', '\\', '..', 'D:', 'd:')]
    final_path = root.joinpath(*sanitized_parts).resolve()

    if not str(final_path).lower().startswith("d:"):
        return str(root)

    return str(final_path)


def test_translate_path_root():
    assert translate_path("/").lower().startswith("d:")


def test_translate_path_nested():
    assert translate_path("/Folder/file.png").lower().endswith("folder\\file.png")
