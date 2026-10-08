"""X-63: Laptop (area) tool registry and DesktopToolPolicy (K-16) metadata."""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from typing import Any, Callable, Mapping

import pytest

from services.ai_core import tool_placement as tp
from services.ai_core.jarvis_plugin_manager import plugin_manager
from services.utils.jarvis_config import config

# Contract K-16 expectations for the 50 laptop-placed tools (timeout, risk, arg_policies, exclusive).
# risk="callable" means get_tool_meta must expose a callable risk resolver.
DESKTOP_TOOL_POLICY: dict[str, dict[str, Any]] = {
    "append_notepad_file_tool": {
        "timeout": 90.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {"append_text": "raw"},
    },
    "ask_about_document": {"timeout": 60.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "automate_chrome_browser": {
        "timeout": 60.0,
        "risk": "low",
        "exclusive": None,
        "arg_policies": {"url": "raw"},
    },
    "automate_whatsapp": {
        "timeout": 60.0,
        "risk": "medium",
        "exclusive": "desktop_input",
        "arg_policies": {"message": "raw"},
    },
    "automate_youtube": {"timeout": 30.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "cancel_shutdown": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "chrome_browser_close": {"timeout": 20.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "chrome_browser_perform_action": {
        "timeout": 30.0,
        "risk": "low",
        "exclusive": None,
        "arg_policies": {"text_input": "raw"},
    },
    "chrome_browser_read_page": {"timeout": 30.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "close": {"timeout": 15.0, "risk": "callable", "exclusive": None, "arg_policies": {}},
    "close_file_window_tool": {
        "timeout": 15.0,
        "risk": "callable",
        "exclusive": None,
        "arg_policies": {},
    },
    "close_notepad_tool": {"timeout": 15.0, "risk": "callable", "exclusive": None, "arg_policies": {}},
    "close_window": {"timeout": 15.0, "risk": "callable", "exclusive": None, "arg_policies": {}},
    "control_volume_tool": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "create_folder": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "create_template_code": {
        "timeout": 90.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "edit_notepad_file_tool": {
        "timeout": 90.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {"new_content": "raw"},
    },
    "empty_recycle_bin": {"timeout": 20.0, "risk": "high", "exclusive": None, "arg_policies": {}},
    "folder_file": {
        "timeout": 15.0,
        "risk": "callable",
        "exclusive": None,
        "arg_policies": {"path": "raw"},
    },
    "get_active_window_context": {"timeout": 5.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "get_laptop_info": {"timeout": 15.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "list_notepad_files_tool": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "maximize_window": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "minimize_window": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "mouse_click_tool": {
        "timeout": 10.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "move_cursor_tool": {
        "timeout": 10.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "open_app": {
        "timeout": 30.0,
        "risk": "callable",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "open_leads_file": {"timeout": 15.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "open_notepad_file": {
        "timeout": 10.0,
        "risk": "callable",
        "exclusive": None,
        "arg_policies": {"file_path": "raw"},
    },
    "open_notepad_simple": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "open_outputs_folder": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "play_file": {"timeout": 30.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "play_music": {"timeout": 30.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "play_video": {"timeout": 30.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "press_hotkey_tool": {
        "timeout": 10.0,
        "risk": "callable",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "press_key_tool": {
        "timeout": 10.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "restart_system": {"timeout": 20.0, "risk": "high", "exclusive": None, "arg_policies": {}},
    "run_cmd_command": {
        "timeout": 30.0,
        "risk": "callable",
        "exclusive": None,
        "arg_policies": {"command": "raw"},
    },
    "save_notepad": {
        "timeout": 25.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "scrape_google_maps_leads": {
        "timeout": 600.0,
        "risk": "low",
        "exclusive": None,
        "arg_policies": {},
    },
    "scroll_cursor_tool": {
        "timeout": 10.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "set_volume_tool": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "shutdown_system": {"timeout": 20.0, "risk": "high", "exclusive": None, "arg_policies": {}},
    "sleep_system": {"timeout": 20.0, "risk": "medium", "exclusive": None, "arg_policies": {}},
    "start_file_access_server": {
        "timeout": 20.0,
        "risk": "medium",
        "exclusive": None,
        "arg_policies": {},
    },
    "stop_file_access_server": {"timeout": 10.0, "risk": "low", "exclusive": None, "arg_policies": {}},
    "swipe_gesture_tool": {
        "timeout": 10.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {},
    },
    "type_code_animation_tool": {
        "timeout": 180.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {"code": "raw"},
    },
    "type_text_tool": {
        "timeout": 60.0,
        "risk": "low",
        "exclusive": "desktop_input",
        "arg_policies": {"text": "raw"},
    },
    "write_custom_code": {
        "timeout": 120.0,
        "risk": "callable",
        "exclusive": "desktop_input",
        "arg_policies": {"content": "raw"},
    },
}

AREA_PACKAGES = ("automation", "info", "multimedia", "system")

# Laptop tool hosted outside the four area packages (still services.*).
EXTRA_AREA_MODULES = ("services.ai_core.jarvis_rag",)

MIN_DOC_LEN = 40


def _area_module_names() -> list[str]:
    root = os.path.join(config.project_root, "services")
    names: list[str] = []
    for area in AREA_PACKAGES:
        path = os.path.join(root, area)
        if not os.path.isdir(path):
            continue
        for _finder, name, _ispkg in pkgutil.walk_packages([path], prefix=f"services.{area}."):
            if name.endswith(".__init__"):
                continue
            names.append(name)
    names.extend(EXTRA_AREA_MODULES)
    return sorted(set(names))


def _import_area_modules() -> None:
    for name in _area_module_names():
        importlib.import_module(name)


def _laptop_tool_names() -> tuple[str, ...]:
    return tuple(
        sorted(name for name, entry in tp.PLACEMENTS.items() if entry.kind == tp.LAPTOP)
    )


def _tool_by_name() -> dict[str, Callable[..., Any]]:
    return {getattr(tool, "__name__", ""): tool for tool in plugin_manager.get_tools()}


def _assert_policy(name: str, meta: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    assert meta["timeout"] == expected["timeout"], name
    risk = meta["risk"]
    exp_risk = expected["risk"]
    if exp_risk == "callable":
        assert callable(risk), name
    else:
        assert risk == exp_risk, name
    assert meta.get("exclusive") == expected.get("exclusive"), name
    assert dict(meta.get("arg_policies") or {}) == dict(expected.get("arg_policies") or {}), name


@pytest.fixture(scope="module")
def registered_area_tools() -> dict[str, Callable[..., Any]]:
    _import_area_modules()
    services_root = os.path.join(config.project_root, "services")
    plugin_manager.discover_plugins(services_root)
    return _tool_by_name()


def test_area_module_imports_cover_laptop_hosts() -> None:
    names = _area_module_names()
    assert names
    for name in names:
        importlib.import_module(name)


def test_all_laptop_tools_registered(registered_area_tools: dict[str, Callable[..., Any]]) -> None:
    expected = _laptop_tool_names()
    assert len(expected) == 50
    assert set(DESKTOP_TOOL_POLICY) == set(expected)
    missing = [name for name in expected if name not in registered_area_tools]
    assert not missing, f"missing from plugin_manager.get_tools(): {missing}"


def _source_doc(module_name: str, func_name: str) -> str:
    """Docstring on the module-level tool function (jarvis_tool wrappers may clear __doc__)."""
    import ast
    from pathlib import Path

    mod = importlib.import_module(module_name)
    path = Path(inspect.getfile(mod))
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == func_name:
            return ast.get_docstring(node) or ""
    return ""


def test_laptop_tool_docstrings(registered_area_tools: dict[str, Callable[..., Any]]) -> None:
    for name in _laptop_tool_names():
        tool = registered_area_tools[name]
        doc = inspect.getdoc(tool) or tool.__doc__ or ""
        if len(doc.strip()) < MIN_DOC_LEN:
            doc = _source_doc(tool.__module__, name)
        assert len(doc.strip()) >= MIN_DOC_LEN, name


def test_laptop_tool_policy_meta(registered_area_tools: dict[str, Callable[..., Any]]) -> None:
    for name in _laptop_tool_names():
        meta = plugin_manager.get_tool_meta(name)
        assert meta is not None, name
        _assert_policy(name, meta, DESKTOP_TOOL_POLICY[name])
