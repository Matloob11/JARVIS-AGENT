"""C-118 (contract K-71): the placement table, overrides, routes and placement-aware discovery.

The live registry comes from a local-mode discovery (the PC brain) in a fresh interpreter. Remote mode
(the VPS brain) is discovered in another fresh interpreter, because it puts an import guard for the
laptop-only modules on sys.meta_path. Nothing here touches the network, the desktop or the live agent.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from services.ai_core import tool_placement as tp

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger("test-tool-placement")

PROBE = textwrap.dedent(
    """
    import importlib, json, os, sys
    denied = json.loads(os.environ["C118_DENIED"])
    from services.utils.jarvis_config import config
    from services.ai_core.jarvis_plugin_manager import plugin_manager
    plugin_manager.discover_plugins(os.path.join(config.project_root, "services"))
    plan = plugin_manager.remote_plan
    result = {
        "tools": {tool.__name__: tool.__module__ for tool in plugin_manager.get_tools()},
        "guard_installed": any(type(finder).__name__ == "LaptopOnlyImportGuard"
                               for finder in sys.meta_path),
        "denied_loaded": sorted(name for name in denied if name in sys.modules),
        "health": plugin_manager.discovery_health(),
        "plan": None,
    }
    if plan is not None:
        result["plan"] = {key: list(getattr(plan, key)) for key in (
            "vps_local", "swap_for_proxy", "add_proxy", "expected", "missing", "unplaced",
            "not_in_manifest", "stale_entries", "errors")}
        try:
            importlib.import_module("services.system.jarvis_system_ctrl")
            result["guard"] = "imported"
        except ImportError as e:
            result["guard"] = str(e)
    print("RESULT " + json.dumps(result))
    """
)


def _discover(mode: str) -> dict:
    env = {name: value for name, value in os.environ.items() if name != "PYTEST_CURRENT_TEST"}
    env.update({
        "JARVIS_DESKTOP_MODE": mode, "JARVIS_ROLE": "agent", tp.OVERRIDES_ENV: "",
        "JARVIS_TEST_MODE": "true", "UI_BRIDGE_URL": "http://127.0.0.1:9",
        "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(PROJECT_ROOT),
        "C118_DENIED": json.dumps(sorted(tp.REMOTE_DENIED_MODULES)),
    })
    result = subprocess.run([sys.executable, "-c", PROBE], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=600,
                            check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("RESULT ")]
    assert result.returncode == 0 and lines, (result.stdout[-2000:], result.stderr[-4000:])
    return json.loads(lines[-1][len("RESULT "):])


@pytest.fixture(scope="module")
def local() -> dict:
    return _discover("local")


@pytest.fixture(scope="module")
def remote() -> dict:
    return _discover("remote")


# ---------------------------------------------------------------- the table


def test_the_table_has_no_problems():
    assert not tp.TABLE_ERRORS, tp.TABLE_ERRORS


def test_every_name_is_placed_exactly_once(local):
    names = [entry.name for entry in tp._TABLE]  # pylint: disable=protected-access
    assert len(names) == len(set(names))
    assert set(tp.PLACEMENTS) == set(local["tools"]) | set(tp.INTERNAL_TOOLS)


def test_the_internal_tools_are_the_wrap_tools_of_brain_assistant():
    source = (PROJECT_ROOT / "src" / "core" / "agent_core.py").read_text(encoding="utf-8")
    assert set(re.findall(r"async def (wrap_\w+)\(", source)) == set(tp.INTERNAL_TOOLS)
    assert all(tp.PLACEMENTS[name].kind == tp.VPS for name in tp.INTERNAL_TOOLS)


def test_the_counts_match_k71(local):
    # C-57: +2 job tools; X-29: cancel_shutdown; X-32: cancel_scheduled_task; X-43: close_window;
    # C-81: start_swarm_mode
    assert tp.placement_counts() == {"vps": 36, "split": 1, "routed": 2, "laptop": 50}
    assert len(tp.PLACEMENTS) == len(local["tools"]) + len(tp.INTERNAL_TOOLS) == 89


def test_deny_listed_modules_hold_only_gui_laptop_tools(local):
    denied = {name for name, module in local["tools"].items() if module in tp.REMOTE_DENIED_MODULES}
    # keyboard/mouse 10, windows 11, Notepad 8, PinchTab 4, system 5, WhatsApp 1, vitals 1
    assert len(denied) == 40
    for name in denied:
        assert tp.PLACEMENTS[name].kind == tp.LAPTOP and tp.PLACEMENTS[name].gui, name


def test_classes_follow_k76():
    by_class: dict[str | None, set[str]] = {}
    for name, entry in tp.PLACEMENTS.items():
        by_class.setdefault(entry.cls, set()).add(name)
    assert by_class[tp.DE_ESCALATION] == {"chrome_browser_close", "stop_file_access_server", "cancel_shutdown"}
    assert by_class[tp.AVAILABILITY] == {"shutdown_system", "restart_system", "sleep_system",
                                         "empty_recycle_bin"}
    assert by_class[tp.EXPOSURE] == {"start_file_access_server"}
    assert by_class[tp.MESSAGING] == {"automate_whatsapp"}
    assert by_class[tp.CODE_EXEC] == {"run_cmd_command"}
    assert by_class[tp.CAPTURE] == {"analyze_screen"}
    assert {"get_laptop_info", "get_active_window_context", "list_notepad_files_tool",
            "chrome_browser_read_page", "ask_about_document"} <= by_class[tp.READ]
    for name, entry in tp.PLACEMENTS.items():
        laptop_run = entry.kind != tp.VPS or entry.fallback == tp.LAPTOP
        assert (entry.cls is not None) == laptop_run, name
    assert tp.CONFIRM_CLASSES == {tp.MESSAGING, tp.CODE_EXEC, tp.AVAILABILITY, tp.EXPOSURE}


def test_fallbacks_and_hooks_are_known():
    entry = tp.PLACEMENTS["download_youtube_media"]
    assert (entry.kind, entry.fallback, entry.fallback_on) == (tp.VPS, tp.LAPTOP, ("YT_BOT_CHECK",))
    assert [name for name, entry in tp.PLACEMENTS.items() if entry.fallback] == [
        "download_youtube_media"]
    for name, entry in tp.PLACEMENTS.items():
        for hook in entry.arg_hooks:
            assert hook.partition(":")[0] in tp.ARG_HOOKS, (name, hook)
    assert tp.PLACEMENTS["edit_notepad_file_tool"].arg_hooks == ("outputs_basename:filename",)
    assert "open_roots:path" in tp.PLACEMENTS["folder_file"].arg_hooks
    # On the VPS, zip_files reads folder_path and writes <folder_path>.zip beside it.
    assert tp.PLACEMENTS["zip_files"].arg_hooks == (
        "open_roots:folder_path", "vps_media_path:folder_path", "safe_name:zip_name")


def test_routed_tools_guard_on_the_vps_what_they_guard_on_the_laptop():
    for name, entry in tp.PLACEMENTS.items():
        if entry.kind != tp.ROUTED:
            continue
        sides: dict[str, set[str]] = {}
        for hook in entry.arg_hooks:
            hook_name, _, arg = hook.partition(":")
            sides.setdefault(arg, set()).add(tp.ARG_HOOKS[hook_name])
        for arg, guarded in sides.items():
            assert tp.LAPTOP not in guarded or tp.VPS in guarded, (name, arg)


def test_a_gui_tool_cannot_have_opens_urls():
    entry = tp.ToolPlacement("c118_gui", tp.LAPTOP, cls=tp.INPUT, gui=True, opens_urls=True)
    assert tp._entry_problems(entry) == [  # pylint: disable=protected-access
        "c118_gui: opens_urls is for tools that run in the VPS agent"]


def _references_webbrowser_open(node: ast.AST) -> bool:
    """node uses webbrowser.open*: a call, or the function handed to asyncio.to_thread."""
    return any(isinstance(sub, ast.Attribute) and sub.attr in {"open", "open_new", "open_new_tab"}
               and isinstance(sub.value, ast.Name) and sub.value.id == "webbrowser"
               for sub in ast.walk(node))


def _named_defs(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Module-level functions and the methods of module-level classes: the names other code can reach. A nested
    function's name is local (X-55's inner 'factory'), and the same word elsewhere (jarvis_jobs' factory parameter)
    is another object; following it made every job-running tool look like a browser opener."""
    defs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append(node)
        elif isinstance(node, ast.ClassDef):
            defs.extend(sub for sub in node.body if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return defs


def _calls_one_of(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(sub, ast.Call) and (getattr(sub.func, "attr", None) in names
                                              or getattr(sub.func, "id", None) in names)
               for sub in ast.walk(node))


def test_vps_tools_that_open_a_browser_are_flagged_opens_urls(local):
    """K-72: in the VPS agent, webbrowser.open reaches the laptop (pc.open_url) only inside a tool
    flagged opens_urls. A tool reaches a browser when its module uses webbrowser.open* or calls a
    services function that does (one hop, such as yt_bot.open_url_in_app)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # invalid escapes in other modules
        trees = {path.relative_to(PROJECT_ROOT).with_suffix("").as_posix().replace("/", "."):
                 ast.parse(path.read_text(encoding="utf-8"))
                 for path in (PROJECT_ROOT / "services").rglob("*.py") if "tests" not in path.parts}
    funcs = [func for tree in trees.values() for func in _named_defs(tree)]
    openers = {func.name for func in funcs if _references_webbrowser_open(func)}
    # Follow the chain: a function that reaches an opener (a direct call, or handing it to
    # asyncio.to_thread) is itself an opener. X-65 moved the real webbrowser.open into
    # _local_browser_open, so open_url_in_app reaches a browser one hop away (via to_thread).
    def _mentions(func: ast.AST, names: set[str]) -> bool:
        return any((isinstance(sub, ast.Attribute) and sub.attr in names)
                   or (isinstance(sub, ast.Name) and sub.id in names) for sub in ast.walk(func))
    while True:
        more = {func.name for func in funcs if func.name not in openers and _mentions(func, openers)}
        if not more:
            break
        openers |= more
    assert "open_url_in_app" in openers
    reaching = {name for name, module in local["tools"].items()
                if _references_webbrowser_open(trees[module])
                or _calls_one_of(trees[module], openers)}
    assert {"automate_youtube", "download_youtube_media", "write_custom_code"} <= reaching
    in_vps_agent = {name for name, entry in tp.PLACEMENTS.items() if not entry.gui}
    flagged = {name for name, entry in tp.PLACEMENTS.items() if entry.opens_urls}
    assert reaching & in_vps_agent <= flagged
    assert flagged <= in_vps_agent
    assert "download_youtube_media" in flagged


# ---------------------------------------------------------------- routes

B = "\\"


@pytest.mark.parametrize(("folder_path", "place"), [
    ("C:" + B + "Users" + B + "Matloob" + B + "Desktop" + B + "photos", tp.LAPTOP),
    ("d:/music", tp.LAPTOP),
    (B + B + "nas" + B + "share", tp.LAPTOP),
    ("%USERPROFILE%" + B + "Desktop", tp.LAPTOP),
    ("~/Documents", tp.LAPTOP),
    ("~", tp.LAPTOP),
    ("Downloaded_Images", tp.VPS),  # relative: Jarvis_Outputs
    ("/app/Jarvis_Outputs/leads", tp.VPS),
    ("notes:2024", tp.VPS),
    ("", tp.VPS),
    (None, tp.VPS),
    (7, tp.VPS),
])
def test_zip_files_routes_windows_paths_to_the_laptop(folder_path, place):
    assert tp.resolve_place("zip_files", {"folder_path": folder_path}, True) == place
    assert tp.resolve_place("zip_files", {"folder_path": folder_path}, False) == place


def test_the_media_listing_follows_the_laptop_link():
    assert tp.resolve_place("list_generated_media_tool", {}, online=True) == tp.LAPTOP
    assert tp.resolve_place("list_generated_media_tool", {}, online=False) == tp.VPS


def test_fixed_places_resolve_without_arguments():
    assert tp.resolve_place("analyze_screen", {}, True) == tp.VPS  # split: the VPS runs the tool
    assert tp.resolve_place("open_app", {}, False) == tp.LAPTOP  # offline: the proxy says so
    assert tp.resolve_place("get_weather", {}, True) == tp.VPS
    assert tp.resolve_place("no_such_tool", {}, True) is None
    places = tp.effective_placements({"zip_files": tp.LAPTOP})
    assert tp.resolve_place("zip_files", {"folder_path": "x"}, True, places) == tp.LAPTOP


# ---------------------------------------------------------------- overrides


def _manifest_entry(*annotations: str) -> dict:
    return {"module": "m", "params": [{"name": f"a{i}", "annotation": text, "required": True}
                                      for i, text in enumerate(annotations)]}


MANIFEST = {"download_youtube_media": _manifest_entry("str", "str"),
            "zip_files": _manifest_entry("str", "str"),
            "list_generated_media_tool": _manifest_entry("str"),
            "scrape_google_maps_leads": _manifest_entry("str", "str", "int", "str"),
            "autonomous_self_repair": _manifest_entry("str", "str | pathlib.Path")}


@pytest.mark.parametrize(("raw", "reason"), [
    ("open_app=vps", "a GUI tool never runs on the VPS"),
    ("shutdown_system=vps", "a GUI tool never runs on the VPS"),
    ("wrap_change_voice=laptop", "an internal wrap_* tool never runs on the laptop"),
    ("analyze_screen=vps", "a split tool keeps its split"),
    ("analyze_screen=laptop", "a split tool keeps its split"),
    ("get_weather=laptop", "the tool has no laptop class"),
    ("autonomous_self_repair=laptop", "the tool has no laptop class"),
    ("no_such_tool=vps", "unknown tool"),
    ("open_app=desktop", "the place must be vps or laptop"),
    ("open_app", "expected name=vps|laptop"),
])
def test_invalid_overrides_are_refused(raw, reason):
    accepted, refused = tp.parse_overrides(raw, MANIFEST)
    assert not accepted, accepted
    assert refused == [f"{raw}: {reason}"]


def test_valid_overrides_are_accepted_and_the_last_one_wins():
    raw = " scrape_google_maps_leads=VPS , zip_files=laptop,list_generated_media_tool=vps,," \
          "zip_files=vps , download_youtube_media=laptop, ask_about_document=vps"
    accepted, refused = tp.parse_overrides(raw, MANIFEST)
    assert not refused, refused
    assert accepted == {"scrape_google_maps_leads": tp.VPS, "zip_files": tp.VPS,
                        "list_generated_media_tool": tp.VPS, "download_youtube_media": tp.LAPTOP,
                        "ask_about_document": tp.VPS}


def test_laptop_overrides_need_a_whitelisted_manifest_entry():
    assert tp.parse_overrides("zip_files=laptop", None)[1] == [
        "zip_files=laptop: laptop only for tools in the desktop manifest"]
    assert tp.parse_overrides("zip_files=laptop", {"zip_files": _manifest_entry("str | int")})[1] == [
        "zip_files=laptop: its parameters fall outside the annotation whitelist"]
    assert tp.parse_overrides("zip_files=laptop", MANIFEST) == ({"zip_files": tp.LAPTOP}, [])
    assert tp.parse_overrides("", MANIFEST) == ({}, [])
    assert tp.parse_overrides(None, MANIFEST) == ({}, [])


def test_overrides_change_places_and_skipped_modules():
    manifest = tp.manifest_tools(tp.load_manifest())
    default_skip = tp.remote_skip_modules(manifest, tp.effective_placements())
    assert "services.automation.jarvis_maps_scraper" in default_skip
    assert tp.REMOTE_DENIED_MODULES <= default_skip
    places = tp.effective_placements({"scrape_google_maps_leads": tp.VPS})
    assert tp.placement_counts(places) == {"vps": 37, "split": 1, "routed": 2, "laptop": 49}
    assert "services.automation.jarvis_maps_scraper" not in tp.remote_skip_modules(manifest, places)
    # The deny-list holds even without a manifest.
    assert tp.remote_skip_modules(None, places) == tp.REMOTE_DENIED_MODULES


# ---------------------------------------------------------------- the remote decision table


def test_plan_remote_decides_every_name():
    places = {"vps_tool": tp.VPS, "vps_gone": tp.VPS, "split_tool": tp.SPLIT,
              "routed_tool": tp.ZIP_ROUTE, "lap_imported": tp.LAPTOP, "lap_skipped": tp.LAPTOP,
              "lap_no_manifest": tp.LAPTOP, "orphan_entry": tp.VPS,
              tp.INTERNAL_TOOLS[0]: tp.VPS}
    discovered = {"vps_tool": "m.a", "split_tool": "m.a", "routed_tool": "m.b", "lap_imported": "m.b",
                  "lap_no_manifest": "m.b", "new_vps_tool": "m.c"}
    manifest = {name: {"module": "m"} for name in (
        "vps_tool", "vps_gone", "split_tool", "routed_tool", "lap_imported", "lap_skipped",
        "manifest_only_unplaced")}
    plan = tp.plan_remote(discovered, manifest, places)
    assert plan.vps_local == ("routed_tool", "split_tool", "vps_tool")
    assert plan.swap_for_proxy == ("lap_imported",)
    assert plan.add_proxy == ("lap_skipped",)
    assert plan.laptop_proxies == ("lap_imported", "lap_skipped")
    assert plan.missing == ("lap_no_manifest", "manifest_only_unplaced", "vps_gone")
    assert plan.unplaced == ("manifest_only_unplaced", "new_vps_tool")
    assert plan.not_in_manifest == ("lap_no_manifest", "new_vps_tool")
    assert plan.stale_entries == ("orphan_entry",)
    assert set(plan.expected) == set(discovered) | set(manifest)
    assert not plan.ok
    assert plan.detail() == ("missing: lap_no_manifest, manifest_only_unplaced, vps_gone; "
                             "unplaced: manifest_only_unplaced, new_vps_tool")


def test_plan_remote_is_ok_when_everything_is_served():
    places = {"a": tp.VPS, "b": tp.LAPTOP, "c": tp.LAPTOP}
    plan = tp.plan_remote({"a": "m1", "b": "m1"}, {"a": {}, "b": {}, "c": {}}, places)
    assert plan.ok and plan.detail() == ""
    assert (plan.vps_local, plan.swap_for_proxy, plan.add_proxy) == (("a",), ("b",), ("c",))
    failing = tp.plan_remote({"a": "m1"}, {"a": {}}, places, errors=["manifest unusable"])
    assert not failing.ok and failing.detail() == "manifest unusable"


def test_remote_discovery_without_a_manifest_is_degraded(tmp_path):
    remote = tp.RemoteDiscovery("", manifest_path=str(tmp_path / "missing.json"))
    assert remote.skip_modules == tp.REMOTE_DENIED_MODULES
    plan = remote.finish({"get_weather": "services.info.jarvis_get_weather"}, set(), LOG)
    assert not plan.ok
    assert plan.errors[0].startswith("desktop manifest unusable: cannot read missing.json")
    loaded = {"services.system.jarvis_system_ctrl"}
    plan = tp.RemoteDiscovery("").finish({}, loaded, LOG)
    assert "laptop-only modules were imported: services.system.jarvis_system_ctrl" in plan.errors


def test_the_import_guard_refuses_only_deny_listed_modules():
    guard = tp.LaptopOnlyImportGuard()
    with pytest.raises(ImportError, match="never imported in remote mode"):
        guard.find_spec("services.system.jarvis_system_ctrl", None)
    assert guard.find_spec("services.info.jarvis_get_weather", None) is None
    try:
        tp.install_import_guard()
        tp.install_import_guard()
        assert sum(isinstance(finder, tp.LaptopOnlyImportGuard) for finder in sys.meta_path) == 1
    finally:
        tp.remove_import_guard()
    assert not any(isinstance(finder, tp.LaptopOnlyImportGuard) for finder in sys.meta_path)


# ---------------------------------------------------------------- discovery in both modes


def test_local_discovery_ignores_the_table(local):
    assert len(local["tools"]) == 85  # C-81: start_swarm_mode
    assert local["plan"] is None
    assert local["guard_installed"] is False
    assert local["health"] == {"ok": True, "count": 85, "detail": ""}
    assert set(local["denied_loaded"]) == tp.REMOTE_DENIED_MODULES  # the PC brain imports them all


def test_remote_discovery_imports_no_denied_module(remote):
    assert remote["guard_installed"] is True
    assert remote["denied_loaded"] == []
    assert "services.system.jarvis_system_ctrl" not in remote["tools"].values()
    assert remote["guard"] == ("services.system.jarvis_system_ctrl is laptop-only and never "
                               "imported in remote mode (K-71)")


def test_remote_discovery_serves_every_expected_name(local, remote):
    plan = remote["plan"]
    assert (plan["missing"], plan["unplaced"], plan["errors"]) == ([], [], [])
    assert (plan["not_in_manifest"], plan["stale_entries"]) == ([], [])
    assert set(plan["expected"]) == set(local["tools"])
    served = plan["vps_local"] + plan["swap_for_proxy"] + plan["add_proxy"]
    assert sorted(served) == sorted(plan["expected"])
    assert set(remote["tools"]) == set(plan["vps_local"]) | set(plan["swap_for_proxy"])
    assert all(tp.PLACEMENTS[name].kind != tp.LAPTOP for name in plan["vps_local"])
    assert all(tp.PLACEMENTS[name].kind == tp.LAPTOP
               for name in plan["swap_for_proxy"] + plan["add_proxy"])
    assert {"analyze_screen", "zip_files", "list_generated_media_tool", "send_email",
            "confirm_action"} <= set(plan["vps_local"])
    assert {"shutdown_system", "open_app", "automate_whatsapp"} <= set(plan["add_proxy"])
    # C-57: +2 VPS job tools; X-29: cancel_shutdown (laptop); X-32: cancel_scheduled_task (VPS);
    # C-81: start_swarm_mode (VPS)
    assert len(plan["vps_local"]) == 35 and len(served) == 85
    assert remote["health"] == {"ok": True, "count": len(remote["tools"]), "detail": ""}
