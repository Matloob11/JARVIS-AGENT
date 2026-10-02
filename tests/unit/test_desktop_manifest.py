"""C-118 (contract K-71): the committed desktop manifest, services/ai_core/desktop_manifest.json.

The comparison with the live registry runs the exporter (scripts/desktop/export_manifest.py --check)
in a fresh interpreter. It is advisory: a stale committed file skips with the reason, unless
JARVIS_MANIFEST_STRICT=1 (the deploy pre-flight and CI), where it fails. Every other test here checks
the committed file and the codec offline; nothing touches the network, the desktop or the live agent.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

import pytest

from services.ai_core import tool_placement as tp
from services.ai_core.jarvis_plugin_manager import plugin_manager

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPORTER = PROJECT_ROOT / "scripts" / "desktop" / "export_manifest.py"
STRICT = os.getenv("JARVIS_MANIFEST_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}
TOOL_FIELDS = {"name", "module", "doc", "params", "meta", "cls", "placement", "idempotent",
               "param_schema_sha256", "lk_schema_sha256"}
META_FIELDS = {"timeout", "risk", "arg_policies", "exclusive", "voice_confirm", "max_output_chars",
               "long_running"}
PARAM_FIELDS = {"name", "kind", "annotation", "default", "required"}
PC_OPERATIONS = {"pc.ping", "pc.capture_screen", "pc.capture_webcam", "pc.read_file", "pc.fetch",
                 "pc.open_url", "pc.speak_local", "pc.play_sound"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return tp.load_manifest()


@pytest.fixture(scope="module")
def tools(manifest) -> dict:
    return tp.manifest_tools(manifest)


@pytest.fixture(scope="module")
def exporter():
    spec = importlib.util.spec_from_file_location("jarvis_export_manifest", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def registry():
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        before = list(plugin_manager.tools)
    yield plugin_manager
    with plugin_manager._tools_lock:  # pylint: disable=protected-access
        plugin_manager.tools[:] = before


# ---------------------------------------------------------------- live registry (advisory)


def test_committed_manifest_matches_the_live_registry():
    env = {name: value for name, value in os.environ.items() if name != "PYTEST_CURRENT_TEST"}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, str(EXPORTER), "--check"], cwd=PROJECT_ROOT, env=env,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=600,
                            check=False)
    if result.returncode == 0:
        return
    report = [line for line in result.stdout.splitlines()
              if line.startswith(("desktop manifest", "  - ", "export_manifest"))]
    reason = " | ".join(report[:12]) or result.stderr[-1500:]
    if STRICT or result.returncode not in (1, 2):
        pytest.fail(f"export_manifest.py --check exited {result.returncode}: {reason}")
    pytest.skip(f"advisory (JARVIS_MANIFEST_STRICT unset): {reason}")


# ---------------------------------------------------------------- the committed file


def test_committed_manifest_is_intact(manifest):
    assert set(manifest) == {"version", "git_sha", "manifest_sha256", "tools", "pc_operations",
                             "lk_schema_libs"}
    assert set(manifest["lk_schema_libs"]) == set(tp.LK_SCHEMA_DISTRIBUTIONS)
    assert manifest["version"] == tp.MANIFEST_VERSION
    assert manifest["manifest_sha256"] == tp.manifest_digest(manifest)
    assert len(manifest["git_sha"]) in (7, 40) or manifest["git_sha"] == "unknown"
    names = [tool["name"] for tool in manifest["tools"]]
    assert names == sorted(names) and len(names) == len(set(names))
    for tool in manifest["tools"]:
        assert set(tool) == TOOL_FIELDS, tool["name"]
        assert set(tool["meta"]) == META_FIELDS, tool["name"]
        assert tool["meta"]["risk"] in ("low", "medium", "high", "dynamic"), tool["name"]
        assert all(set(param) == PARAM_FIELDS for param in tool["params"]), tool["name"]
        assert tool["placement"] in tp.PLACEMENT_KINDS, tool["name"]


def test_manifest_agrees_with_the_placement_table(tools):
    assert set(tools) == set(tp.PLACEMENTS) - set(tp.INTERNAL_TOOLS)
    assert len(tools) == 79
    for name, tool in tools.items():
        entry = tp.PLACEMENTS[name]
        assert (tool["placement"], tool["cls"], tool["idempotent"]) == (
            entry.kind, entry.cls, entry.idempotent), name


def test_parameter_hashes_match_the_params(manifest):
    for item in manifest["tools"] + manifest["pc_operations"]:
        assert item["param_schema_sha256"] == tp.param_schema_sha256(item["params"]), item["name"]


def test_laptop_capable_tools_rebuild_from_the_whitelist(tools):
    capable = [name for name in tools if tp.laptop_capable(tp.PLACEMENTS[name])]
    assert len(capable) == 51  # laptop 48, routed 2, download_youtube_media (laptop fallback)
    for name in capable:
        for param in tools[name]["params"]:
            hint = tp.annotation_from_str(param["annotation"])
            assert tp.annotation_to_str(hint) == param["annotation"], (name, param["name"])
            assert param["kind"] in ("POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY"), (name, param["name"])
            assert param["default"] is None or isinstance(param["default"], (str, int, float, bool))


def test_arg_hooks_name_real_parameters(tools, manifest):
    operations = {op["name"]: op for op in manifest["pc_operations"]}
    hooked = [(name, entry.arg_hooks) for name, entry in tp.PLACEMENTS.items() if entry.arg_hooks]
    hooked += [(op.name, op.arg_hooks) for op in tp.PC_OPERATIONS if op.arg_hooks]
    assert hooked
    for name, hooks in hooked:
        params = {param["name"] for param in (tools.get(name) or operations[name])["params"]}
        for hook in hooks:
            assert hook.partition(":")[2] in params, (name, hook)


def test_pc_operations_are_listed(manifest):
    operations = manifest["pc_operations"]
    assert [op["name"] for op in operations] == sorted(PC_OPERATIONS)
    for op in operations:
        assert op["placement"] == tp.LAPTOP and op["cls"] in tp.CLASSES, op["name"]
        for param in op["params"]:
            tp.annotation_from_str(param["annotation"])
    fetch = next(op for op in operations if op["name"] == "pc.fetch")
    assert [param["name"] for param in fetch["params"]] == ["area", "relpath", "sha256", "size",
                                                            "open"]
    assert next(op for op in operations if op["name"] == "pc.read_file")["cls"] == tp.EXPOSURE


def _proxy_from_manifest(tool: dict) -> Any:
    """What a C-119 proxy rebuilds from the manifest alone: name, doc and a signature of real types."""
    async def proxy(**kwargs: Any) -> Any:
        return kwargs

    params = [inspect.Parameter(
        param["name"], getattr(inspect.Parameter, param["kind"]),
        default=inspect.Parameter.empty if param["required"] else param["default"],
        annotation=tp.annotation_from_str(param["annotation"])) for param in tool["params"]]
    proxy.__name__ = proxy.__qualname__ = tool["name"]
    proxy.__module__ = tool["module"]
    proxy.__doc__ = tool["doc"]
    proxy.__signature__ = inspect.Signature(params)
    proxy.__annotations__ = {param.name: param.annotation for param in params}
    return proxy


def _laptop_capable(tools: dict) -> list[dict]:
    return [tool for name, tool in tools.items() if tp.laptop_capable(tp.PLACEMENTS[name])]


def test_a_proxy_rebuilt_from_the_manifest_has_the_same_parameters(tools):
    for tool in _laptop_capable(tools):
        proxy = _proxy_from_manifest(tool)
        assert tp.live_param_schema_sha256(proxy) == tool["param_schema_sha256"], tool["name"]


def test_a_proxy_rebuilt_from_the_manifest_declares_the_same_tool(tools, manifest):
    drift = tp.lk_schema_drift(manifest)
    if drift:  # the strict live-registry check reports this drift
        pytest.skip("lk_schema_sha256 needs the recorded library versions: " + ", ".join(drift))
    for tool in _laptop_capable(tools):
        assert tp.lk_schema_sha256(_proxy_from_manifest(tool)) == tool["lk_schema_sha256"], \
            tool["name"]


def test_lk_hashes_are_compared_only_between_equal_library_versions(manifest):
    installed = tp.lk_schema_libs()
    assert list(installed) == list(tp.LK_SCHEMA_DISTRIBUTIONS)
    assert "missing" not in installed.values()
    assert not tp.lk_schema_drift({"lk_schema_libs": installed})
    older = {**installed, "docstring-parser": "0.15"}
    assert tp.lk_schema_drift({"lk_schema_libs": older}) == [
        f"docstring-parser 0.15 -> {installed['docstring-parser']}"]
    assert tp.lk_schema_drift({}) == [f"{dist} unrecorded -> {version}"
                                      for dist, version in installed.items()]

    committed = {**json.loads(json.dumps(manifest)), "lk_schema_libs": older}
    live = json.loads(json.dumps(committed))
    live["lk_schema_libs"] = installed
    live["tools"][0]["lk_schema_sha256"] = "0" * 64
    assert tp.manifest_differences(committed, live) == [
        f"lk_schema_libs changed: docstring-parser 0.15 -> {installed['docstring-parser']} "
        "(lk_schema_sha256 not compared)"]
    live["lk_schema_libs"] = older  # the same versions: the hashes are compared again
    assert tp.manifest_differences(committed, live) == [
        f"{live['tools'][0]['name']}: lk_schema_sha256 changed"]


# ---------------------------------------------------------------- the codec


async def c118_lap(text: str, count: int = 3, mode: Literal["a", "b"] = "a",
                   tags: list[str] | None = None) -> dict:
    """Laptop sample.

    Args:
        text: the words
    """
    return {"text": text, "count": count, "mode": mode, "tags": tags}


async def c118_vps(path: str | Path) -> str:
    """VPS sample."""
    return str(path)


TABLE = {"c118_lap": tp.ToolPlacement("c118_lap", tp.LAPTOP, cls=tp.INPUT, gui=True),
         "c118_vps": tp.ToolPlacement("c118_vps", tp.VPS)}


def _wrappers(registry) -> list:
    registry.register_tool(c118_lap, timeout=20, raw_args=("text",),
                           risk=lambda args: "medium" if args.get("count", 0) > 5 else "low")
    registry.register_tool(c118_vps)
    return [tool for tool in registry.tools if tool.__name__ in TABLE]


def test_build_manifest_is_deterministic(registry):
    wrappers = _wrappers(registry)
    first = tp.build_manifest(wrappers, git_sha="a" * 40, table=TABLE)
    again = tp.build_manifest(list(reversed(wrappers)), git_sha="b" * 40, table=TABLE)
    assert first["manifest_sha256"] == again["manifest_sha256"] == tp.manifest_digest(first)
    assert not tp.manifest_differences(first, again)
    assert json.dumps(first, sort_keys=True) == json.dumps({**again, "git_sha": "a" * 40},
                                                           sort_keys=True)
    lap, vps = first["tools"]
    assert (lap["name"], vps["name"]) == ("c118_lap", "c118_vps")
    assert [(p["name"], p["annotation"], p["default"], p["required"]) for p in lap["params"]] == [
        ("text", "str", None, True), ("count", "int", 3, False),
        ("mode", 'Literal["a", "b"]', "a", False), ("tags", "list[str] | None", None, False)]
    assert lap["meta"] == {"timeout": 20.0, "risk": "dynamic", "arg_policies": {"text": "raw"},
                           "exclusive": None, "voice_confirm": None, "max_output_chars": 12000,
                           "long_running": False}
    assert (lap["cls"], lap["placement"], lap["doc"]) == (tp.INPUT, tp.LAPTOP, c118_lap.__doc__)
    assert lap["module"] == __name__
    # A VPS-only tool may keep an annotation outside the whitelist; it is recorded as text.
    assert vps["params"][0]["annotation"] == "str | pathlib.Path"
    assert [op["name"] for op in first["pc_operations"]] == sorted(PC_OPERATIONS)


def test_build_manifest_refuses_what_a_proxy_cannot_rebuild(registry):
    wrappers = _wrappers(registry)
    with pytest.raises(tp.ManifestError, match="c118_vps: no placement entry"):
        tp.build_manifest(wrappers, table={"c118_lap": TABLE["c118_lap"]})
    as_laptop = {**TABLE, "c118_vps": tp.ToolPlacement("c118_vps", tp.LAPTOP, cls=tp.INPUT)}
    with pytest.raises(tp.ManifestError, match="c118_vps: path: annotation .* whitelist"):
        tp.build_manifest(wrappers, table=as_laptop)

    def kwargs_tool(**kwargs: str) -> None:
        """Takes anything."""

    def any_tool(value: Any) -> None:
        """Takes any value."""

    def list_default(value: list[str] = ["x"]) -> None:  # pylint: disable=dangerous-default-value
        """Has a list default."""

    for func, message in ((kwargs_tool, "cannot be rebuilt by a proxy"),
                          (any_tool, "outside the K-71 whitelist"),
                          (list_default, "is not a JSON scalar")):
        with pytest.raises(tp.ManifestError, match=message):
            tp.describe_params(func, strict=True)
        assert tp.describe_params(func, strict=False)  # recorded as text for a VPS-only tool


@pytest.mark.parametrize("text", ["str", "int", "float", "bool", "None", "str | None",
                                  "list[str]", "list[int] | None", "dict[str, str]",
                                  'Literal["a", "b"]', 'Literal["x"] | None'])
def test_whitelisted_annotations_round_trip(text):
    assert tp.annotation_to_str(tp.annotation_from_str(text)) == text


@pytest.mark.parametrize("text", ["Any", "list[float]", "str | int", "Literal['a']", "Literal[1]",
                                  "None | None", "dict[str, int]", "Optional[str]", "__import__('os')",
                                  "Literal[]", "str|None"])
def test_other_annotations_are_refused_without_evaluation(text):
    with pytest.raises(tp.ManifestError):
        tp.annotation_from_str(text)


def test_load_manifest_refuses_a_bad_file(tmp_path, manifest):
    path = tmp_path / "desktop_manifest.json"
    with pytest.raises(tp.ManifestError, match="cannot read"):
        tp.load_manifest(str(path))
    edited = json.loads(json.dumps(manifest))
    edited["tools"][0]["doc"] = "edited by hand"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(tp.ManifestError, match="manifest_sha256 does not match"):
        tp.load_manifest(str(path))
    twice = {**manifest, "tools": manifest["tools"] + manifest["tools"][:1]}
    twice["manifest_sha256"] = tp.manifest_digest(twice)
    path.write_text(json.dumps(twice), encoding="utf-8")
    with pytest.raises(tp.ManifestError, match="listed twice"):
        tp.load_manifest(str(path))
    path.write_text(json.dumps({**manifest, "version": 99}), encoding="utf-8")
    with pytest.raises(tp.ManifestError, match="not a version 1 manifest"):
        tp.load_manifest(str(path))


def test_check_reports_drift_and_write_keeps_an_unchanged_file(tmp_path, manifest, exporter):
    path = tmp_path / "desktop_manifest.json"
    assert exporter.write_manifest(str(path), manifest) is True
    assert exporter.differences(str(path), manifest) == []
    newer = {**manifest, "git_sha": "f" * 40}
    assert exporter.write_manifest(str(path), newer) is False  # only git_sha differs
    assert json.loads(path.read_text(encoding="utf-8"))["git_sha"] == manifest["git_sha"]

    drifted = json.loads(json.dumps(newer))
    drifted["tools"][0]["doc"] = "a new docstring"
    drifted["tools"] = drifted["tools"][:-1]
    drifted["manifest_sha256"] = tp.manifest_digest(drifted)
    first, last = manifest["tools"][0]["name"], manifest["tools"][-1]["name"]
    assert exporter.differences(str(path), drifted) == [
        f"{first}: doc changed", f"{last}: in the committed manifest but not registered"]
    assert exporter.write_manifest(str(path), drifted) is True
    assert tp.load_manifest(str(path))["git_sha"] == "f" * 40
    assert path.read_bytes().endswith(b"}\n") and b"\r\n" not in path.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="the exporter runs on Windows only")
def test_writing_refuses_uncommitted_registry_files(tmp_path, manifest, exporter, monkeypatch,
                                                    capsys):
    path = str(tmp_path / "desktop_manifest.json")
    asked: list[list[str]] = []
    status = [" M services/multimedia/jarvis_file_opener.py"]
    monkeypatch.setattr(exporter, "live_manifest", lambda git_sha: manifest)  # no discovery
    monkeypatch.setattr(exporter, "uncommitted", lambda paths: asked.append(paths) or status)

    assert exporter.main(["--path", path]) == 1
    assert not os.path.exists(path)
    assert "NOT written" in capsys.readouterr().out
    assert {"services/multimedia/jarvis_file_opener.py", "services/ai_core/tool_placement.py",
            "services/ai_core/jarvis_plugin_manager.py"} <= set(asked[-1])

    assert exporter.main(["--path", path, "--allow-dirty"]) == 0
    assert tp.load_manifest(path)["manifest_sha256"] == manifest["manifest_sha256"]
    assert "commit them together" in capsys.readouterr().out
    assert exporter.main(["--path", path]) == 0  # unchanged: nothing to refuse
    assert exporter.main(["--path", path, "--check"]) == 0  # --check only warns
    assert "checks the working tree rather than the commit" in capsys.readouterr().out

    status = None  # git cannot tell
    os.remove(path)
    assert exporter.main(["--path", path]) == 1 and not os.path.exists(path)
    assert "git cannot tell" in capsys.readouterr().out
    status = []  # clean
    assert exporter.main(["--path", path]) == 0 and os.path.exists(path)
