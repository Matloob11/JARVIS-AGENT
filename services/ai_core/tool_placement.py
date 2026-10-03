"""
# tool_placement.py
Contract K-71 (VPS brain): where every LLM-visible tool runs, the desktop manifest codec and the
placement-aware discovery plan for JARVIS_DESKTOP_MODE=remote.

- PLACEMENTS has one entry per tool name: 83 on 2026-10-01, the 79 plugin tools plus the 4
  internal wrap_* tools of BrainAssistant. place is 'vps', 'laptop', 'split' (the VPS runs the tool
  and one step of it runs on the laptop) or a Route decided per call. cls is the K-76 class floor
  of a run on the laptop; the Desktop Node may raise it per call (dynamic risk).
- JARVIS_TOOL_PLACEMENT_OVERRIDES='name=laptop,...' changes a place from the next session, after
  the checks in parse_overrides.
- desktop_manifest.json is written on Windows by scripts/desktop/export_manifest.py from the live
  registry and baked into the agent image. build_manifest and the hashes live here, so the
  exporter, the agent and the Desktop Node compute them the same way.
- RemoteDiscovery gives the plugin manager the modules it must not import in remote mode, the
  import guard for the static deny-list and, after the scan, the RemotePlan: which tools run on
  the VPS and which become laptop proxies. The proxies themselves are C-119.

Local mode (the PC brain) ignores the table: its discovery imports this module like any other, which
registers no tool and has no side effect. Only the standard library is loaded at import; livekit is
loaded when lk_schema_sha256 needs it.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.metadata
import inspect
import json
import logging
import math
import os
import re
import sys
import tempfile
import types
import typing
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

VPS, LAPTOP, SPLIT, ROUTED = "vps", "laptop", "split", "routed"
PLACEMENT_KINDS = (VPS, SPLIT, ROUTED, LAPTOP)

# K-76 classes of a laptop run.
READ, CAPTURE, INPUT, MESSAGING = "read", "capture", "input", "messaging"
CODE_EXEC, AVAILABILITY, EXPOSURE = "code_exec", "availability", "exposure"
DE_ESCALATION = "de_escalation"
CLASSES =(READ, CAPTURE, INPUT, MESSAGING, CODE_EXEC, AVAILABILITY, EXPOSURE, DE_ESCALATION)
# A call in one of these classes needs the agent's ConfirmFlow approval before it is sent (K-76).
CONFIRM_CLASSES = frozenset({MESSAGING, CODE_EXEC, AVAILABILITY, EXPOSURE})
# Shared-IP block codes (K-76) that give a VPS run one re-run on the laptop.
FALLBACK_CODES = frozenset({"YT_BOT_CHECK", "MAPS_BLOCKED"})

# Mandatory argument hooks, written 'hook:argument', and the side that applies them: the node
# (K-76 path policy, C-121) or the agent (VPS argument hooks, C-124).
ARG_HOOKS = {
    "open_roots": LAPTOP,        # resolve_in_roots(open_roots) plus the extended is_sensitive_path
    "outputs_basename": LAPTOP,  # a basename only, inside Jarvis_Outputs
    "safe_name": VPS,            # a safe basename for a file or folder the tool creates
    "vps_attachment": VPS,       # realpath inside the VPS media dirs or the blob dir; deny list
    "vps_media_path": VPS,       # realpath strictly inside the VPS media dirs; the same deny list
    "owner_recipient": VPS,      # a recipient other than the owner makes the call high risk
}

OVERRIDES_ENV = "JARVIS_TOOL_PLACEMENT_OVERRIDES"
MANIFEST_VERSION = 1
MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_manifest.json")

# BrainAssistant's internal tools (src/core/agent_core.py): always on the VPS, not in the manifest.
INTERNAL_TOOLS = ("wrap_set_wake_word_mode", "wrap_change_voice", "wrap_toggle_gf_mode",
                  "wrap_analyze_surroundings")

# Never imported in remote mode, whatever the table says (K-71 static deny-list).
REMOTE_DENIED_MODULES = frozenset({
    "services.automation.keyboard_mouse_ctrl",
    "services.system.jarvis_window_ctrl",
    "services.automation.jarvis_whatsapp_automation",
    "services.automation.jarvis_notepad_automation",
    "services.system.jarvis_system_ctrl",
    "services.system.jarvis_system_info",
    "services.automation.jarvis_browser_pinchtab",
})


class ManifestError(ValueError):
    """The desktop manifest cannot be built, read or trusted."""


@dataclass(frozen=True)
class Route:
    """A placement decided per call: decide(args, online) gives 'vps' or 'laptop'. online is the
    Desktop Node's link presence (K-74 desktop_status.online)."""

    name: str
    decide: Callable[[Mapping[str, Any], bool], str]

    def __call__(self, args: Mapping[str, Any], online: bool) -> str:
        return LAPTOP if self.decide(args, online) == LAPTOP else VPS


@dataclass(frozen=True)
class ToolPlacement:
    """One K-71 table entry. gui: the tool needs the laptop's desktop or hardware, so no override
    may move it to the VPS. opens_urls: while the tool runs in the VPS agent, the K-72 compat shim
    forwards its webbrowser.open (http(s) only) to pc.open_url; a GUI tool never runs there, so it
    never has the flag. timeout_override replaces the decorator timeout in remote mode (the VPS
    wrapper, or the exec timeout_s and so the link cap of a laptop call)."""

    name: str
    place: str | Route
    cls: str | None = None
    idempotent: bool = False
    fallback: str | None = None
    fallback_on: tuple[str, ...] = ()
    arg_hooks: tuple[str, ...] = ()
    opens_urls: bool = False
    timeout_override: float | None = None
    gui: bool = False

    @property
    def kind(self) -> str:
        return placement_kind(self.place) or ""


def placement_kind(place: str | Route | None) -> str | None:
    """'vps', 'laptop', 'split' or 'routed' for a place; None for a missing one."""
    return ROUTED if isinstance(place, Route) else place


def laptop_capable(entry: ToolPlacement) -> bool:
    """True when the tool itself can run on the laptop: laptop-placed, routed, or a VPS tool with a
    laptop fallback. Its parameters must stay inside the annotation whitelist (proxies, node
    checks)."""
    return entry.kind in (LAPTOP, ROUTED) or entry.fallback == LAPTOP


# An absolute Windows path: a drive letter, a UNC share, a %VAR% prefix or the user's home.
_WINDOWS_PATH = re.compile(r"\s*(?:[A-Za-z]:|[\\/]{2}[^\\/]|%[^%\\/]+%|~(?:[\\/]|$))")


def _route_zip_files(args: Mapping[str, Any], online: bool) -> str:
    """zip_files: a Windows path is on the laptop; a relative name (Jarvis_Outputs) or a Linux path
    is on the VPS."""
    path = args.get("folder_path")
    return LAPTOP if isinstance(path, str) and _WINDOWS_PATH.match(path) else VPS


def _route_media_listing(args: Mapping[str, Any], online: bool) -> str:
    """list_generated_media_tool: the laptop's mirror listing while it is online, else the VPS
    listing."""
    return LAPTOP if online else VPS


ZIP_ROUTE = Route("windows_path_on_laptop", _route_zip_files)
MEDIA_LIST_ROUTE = Route("laptop_when_online", _route_media_listing)


def _vps(name: str, idempotent: bool = False, **extra: Any) -> ToolPlacement:
    return ToolPlacement(name, VPS, idempotent=idempotent, **extra)


def _laptop(name: str, cls: str, idempotent: bool = False, **extra: Any) -> ToolPlacement:
    extra.setdefault("gui", True)
    return ToolPlacement(name, LAPTOP, cls=cls, idempotent=idempotent, **extra)


# Grouped as in docs/ARCHITECTURE_VPS_BRAIN.md section 4.2. Planned K-17 tools get their entry when
# they are registered: close_window and cancel_shutdown on the laptop; list_background_jobs,
# cancel_background_job, cancel_scheduled_task and confirm_selftest on the VPS.
_TABLE: tuple[ToolPlacement, ...] = (
    # ---------------------------------------------------------------- VPS (32)
    _vps("tool_report_plan_progress", True),
    _vps("tool_update_user_background"),
    _vps("tool_update_sir_background"),
    _vps("tool_update_sageer_background"),
    _vps("tool_add_persistent_fact"),
    _vps("tool_update_relationship_info"),
    _vps("tool_set_anna_state"),
    # A browser frame first; without a fresh one, pc.capture_webcam on the laptop (presence only).
    _vps("analyze_camera", True, timeout_override=30.0),
    _vps("tool_investigate_recent_bugs", True),
    _vps("autonomous_self_repair"),
    _vps("wrap_set_wake_word_mode", True),
    _vps("wrap_change_voice", True),
    _vps("wrap_toggle_gf_mode", True),
    _vps("wrap_analyze_surroundings", True),
    _vps("set_reminder"),
    _vps("list_reminders", True),
    _vps("schedule_task"),
    _vps("list_tasks", True),
    _vps("get_weather", True),
    _vps("show_location_on_map", True),
    _vps("perform_web_research", True),
    _vps("autonomous_research_and_email", arg_hooks=("owner_recipient:recipient",)),
    _vps("search_internet", True),
    _vps("get_formatted_datetime", True),
    _vps("open_sim_lookup_panel", True),
    _vps("lookup_sim_data", True),
    _vps("download_images", arg_hooks=("safe_name:folder_name",)),
    _vps("generate_image"),
    _vps("generate_qr_code", arg_hooks=("safe_name:filename",)),
    # YouTube's bot check on the shared VPS IP: one re-run on the laptop, then 6 h there. The
    # browser fallback (yt_bot.open_url_in_app: webbrowser.open off Windows) opens on the laptop.
    _vps("download_youtube_media", cls=INPUT, fallback=LAPTOP, fallback_on=("YT_BOT_CHECK",),
         opens_urls=True),
    # A Windows attachment path is pulled from the laptop as an encrypted blob (pc.read_file).
    _vps("send_email", arg_hooks=("vps_attachment:attachment_path", "owner_recipient:recipient"),
         timeout_override=60.0),
    _vps("confirm_action"),
    # ---------------------------------------------------------------- split (1)
    # The laptop captures (pc.capture_screen), the VPS analyses.
    ToolPlacement("analyze_screen", SPLIT, cls=CAPTURE, idempotent=True, timeout_override=45.0),
    # ---------------------------------------------------------------- routed (2)
    # On the VPS the tool zips folder_path (a relative name resolves in Jarvis_Outputs) into
    # <folder_path>.zip beside it, so vps_media_path guards folder_path there. zip_name is ignored
    # until X-55/X-65 honour it; safe_name covers it from then on.
    ToolPlacement("zip_files", ZIP_ROUTE, cls=INPUT,
                  arg_hooks=("open_roots:folder_path", "vps_media_path:folder_path",
                             "safe_name:zip_name")),
    ToolPlacement("list_generated_media_tool", MEDIA_LIST_ROUTE, cls=READ, idempotent=True),
    # ---------------------------------------------------------------- laptop (48)
    _laptop("ask_about_document", READ, True, gui=False),
    _laptop("automate_chrome_browser", INPUT),
    _laptop("chrome_browser_read_page", READ, True),
    _laptop("chrome_browser_perform_action", INPUT),
    _laptop("chrome_browser_close", DE_ESCALATION, True),
    _laptop("create_template_code", INPUT),
    # With auto_run the node finds code_exec and answers APPROVAL_REQUIRED (dynamic risk, K-76).
    _laptop("write_custom_code", INPUT),
    _laptop("run_cmd_command", CODE_EXEC),
    _laptop("open_notepad_simple", INPUT),
    _laptop("list_notepad_files_tool", READ, True),
    _laptop("edit_notepad_file_tool", INPUT, arg_hooks=("outputs_basename:filename",)),
    _laptop("append_notepad_file_tool", INPUT, arg_hooks=("outputs_basename:filename",)),
    _laptop("close_notepad_tool", INPUT),  # force=True: availability
    _laptop("automate_whatsapp", MESSAGING),
    _laptop("move_cursor_tool", INPUT),
    _laptop("mouse_click_tool", INPUT),
    _laptop("scroll_cursor_tool", INPUT),
    _laptop("type_text_tool", INPUT),
    _laptop("type_code_animation_tool", INPUT),
    _laptop("press_key_tool", INPUT),
    _laptop("press_hotkey_tool", INPUT),  # launch surfaces (Win+*, Ctrl+Shift+Esc, ...): code_exec
    _laptop("control_volume_tool", INPUT),
    _laptop("set_volume_tool", INPUT, True),
    _laptop("swipe_gesture_tool", INPUT),
    _laptop("play_video", INPUT, arg_hooks=("open_roots:file_path",)),
    _laptop("play_music", INPUT, arg_hooks=("open_roots:file_path",)),
    _laptop("play_file", INPUT, arg_hooks=("open_roots:name",)),
    _laptop("close_file_window_tool", INPUT),  # more than one match: availability
    _laptop("start_file_access_server", EXPOSURE),
    _laptop("stop_file_access_server", DE_ESCALATION, True),
    _laptop("shutdown_system", AVAILABILITY),
    _laptop("restart_system", AVAILABILITY),
    _laptop("sleep_system", AVAILABILITY),
    _laptop("empty_recycle_bin", AVAILABILITY),
    _laptop("get_laptop_info", READ, True),
    _laptop("open_app", INPUT),  # an unmapped or executable target: code_exec
    _laptop("save_notepad", INPUT),
    _laptop("open_notepad_file", INPUT, arg_hooks=("open_roots:file_path",)),
    _laptop("close", INPUT),  # force or more than one match: availability
    _laptop("minimize_window", INPUT),
    _laptop("maximize_window", INPUT),
    # Executables, scripts, shortcuts and archives: code_exec.
    _laptop("folder_file", INPUT, arg_hooks=("open_roots:path",)),
    _laptop("create_folder", INPUT),
    _laptop("open_outputs_folder", INPUT),
    _laptop("get_active_window_context", READ, True),
    _laptop("automate_youtube", INPUT),
    # On the laptop while the shared VPS IP would be blocked; a headless VPS job is planned (V8).
    _laptop("scrape_google_maps_leads", INPUT, gui=False),
    _laptop("open_leads_file", INPUT),
)


def _entry_problems(entry: ToolPlacement) -> list[str]:
    kind = entry.kind
    hooks = [hook.partition(":") for hook in entry.arg_hooks]
    checks = (
        (kind not in PLACEMENT_KINDS, f"unknown place {entry.place!r}"),
        (entry.cls is not None and entry.cls not in CLASSES, f"unknown class {entry.cls!r}"),
        (entry.cls is None and (kind != VPS or entry.fallback == LAPTOP),
         "a laptop run needs a class (cls)"),
        (entry.fallback not in (None, LAPTOP), "fallback must be None or 'laptop'"),
        (bool(entry.fallback) != bool(entry.fallback_on), "fallback and fallback_on go together"),
        (not FALLBACK_CODES.issuperset(entry.fallback_on), "unknown fallback_on code"),
        (any(name not in ARG_HOOKS or not arg for name, _, arg in hooks), "unknown argument hook"),
        (entry.gui and kind != LAPTOP, "a GUI tool must be laptop-placed"),
        (entry.gui and entry.opens_urls, "opens_urls is for tools that run in the VPS agent"),
        (entry.timeout_override is not None and not 0 < entry.timeout_override <= 3600,
         "timeout_override must be in (0, 3600] s"),
    )
    return [f"{entry.name}: {problem}" for failed, problem in checks if failed]


def _index(entries: Iterable[ToolPlacement]) -> tuple[dict[str, ToolPlacement], list[str]]:
    """The table by name and its problems; a repeated name keeps its first entry."""
    table: dict[str, ToolPlacement] = {}
    problems: list[str] = []
    for entry in entries:
        problems.extend(_entry_problems(entry))
        if entry.name in table:
            problems.append(f"{entry.name}: placed twice")
            continue
        table[entry.name] = entry
    return table, problems


_PLACEMENTS, _TABLE_PROBLEMS = _index(_TABLE)
PLACEMENTS: Mapping[str, ToolPlacement] = types.MappingProxyType(_PLACEMENTS)
# Empty unless the table itself is wrong; remote discovery reports them and the tests fail on them.
TABLE_ERRORS: tuple[str, ...] = tuple(_TABLE_PROBLEMS)


def placement_counts(places: Mapping[str, str | Route] | None = None) -> dict[str, int]:
    """How many names each placement kind has (K-71 counts)."""
    places = effective_placements() if places is None else places
    counts = dict.fromkeys(PLACEMENT_KINDS, 0)
    for place in places.values():
        kind = placement_kind(place)
        if kind in counts:
            counts[kind] += 1
    return counts


# ---------------------------------------------------------------- overrides


def parse_overrides(raw: str | None, manifest: Mapping[str, Mapping[str, Any]] | None
                    ) -> tuple[dict[str, str], list[str]]:
    """
    JARVIS_TOOL_PLACEMENT_OVERRIDES ('name=laptop,name=vps') -> ({name: place}, refusals). manifest
    is the manifest's tools by name (None when it could not be loaded). Refused: a malformed item,
    an unknown name or place, a split tool, a GUI tool on the VPS, and on the laptop an internal
    wrap_* tool, a tool without a laptop class, one missing from the manifest or one whose manifest
    params fall outside the annotation whitelist. A later item for the same name wins.
    """
    accepted: dict[str, str] = {}
    refused: list[str] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, sep, value = (part.strip() for part in item.partition("="))
        place = value.lower()
        reason = _override_refusal(name, place, manifest) if sep else "expected name=vps|laptop"
        if reason:
            refused.append(f"{item}: {reason}")
        else:
            accepted[name] = place
    return accepted, refused


def _override_refusal(name: str, place: str,
                      manifest: Mapping[str, Mapping[str, Any]] | None) -> str | None:
    entry = PLACEMENTS.get(name)
    if entry is None:
        return "unknown tool"
    tool = (manifest or {}).get(name)
    laptop = place == LAPTOP
    rules = (
        (place not in (VPS, LAPTOP), "the place must be vps or laptop"),
        (entry.kind == SPLIT, "a split tool keeps its split"),
        (place == VPS and entry.gui, "a GUI tool never runs on the VPS"),
        (laptop and name in INTERNAL_TOOLS, "an internal wrap_* tool never runs on the laptop"),
        (laptop and entry.cls is None, "the tool has no laptop class"),
        (laptop and tool is None, "laptop only for tools in the desktop manifest"),
        (laptop and tool is not None and not _params_whitelisted(tool),
         "its parameters fall outside the annotation whitelist"),
    )
    return next((reason for failed, reason in rules if failed), None)


def effective_placements(overrides: Mapping[str, str] | None = None) -> dict[str, str | Route]:
    """Every table name with its place after the (already validated) overrides."""
    places: dict[str, str | Route] = {name: entry.place for name, entry in PLACEMENTS.items()}
    places.update(overrides or {})
    return places


def resolve_place(name: str, args: Mapping[str, Any], online: bool,
                  places: Mapping[str, str | Route] | None = None) -> str | None:
    """Where one call of name runs: 'vps' or 'laptop' (a split tool runs on the VPS); None for a
    name without a placement."""
    place = (effective_placements() if places is None else places).get(name)
    if place is None:
        return None
    if isinstance(place, Route):
        return place(args, online)
    return LAPTOP if place == LAPTOP else VPS


# ---------------------------------------------------------------- annotations and parameters

_NONE_TYPE = type(None)
_OPTIONAL_SUFFIX = " | None"
_SCALAR_NAMES = {str: "str", int: "int", float: "float", bool: "bool"}
_PLAIN_TYPES: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool,
                                "list[str]": list[str], "list[int]": list[int],
                                "dict[str, str]": dict[str, str]}


def _whitelist_error(hint: Any) -> ManifestError:
    return ManifestError(f"annotation {hint!r} is outside the K-71 whitelist")


def _plain_annotation(hint: Any) -> str | None:
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if isinstance(hint, type) and hint in _SCALAR_NAMES:
        return _SCALAR_NAMES[hint]
    if origin is list and len(args) == 1 and args[0] in (str, int):
        return f"list[{_SCALAR_NAMES[args[0]]}]"
    if origin is dict and args == (str, str):
        return "dict[str, str]"
    if origin is typing.Literal and args and all(isinstance(arg, str) for arg in args):
        return "Literal[" + ", ".join(json.dumps(arg, ensure_ascii=False) for arg in args) + "]"
    return None


def annotation_to_str(hint: Any) -> str:
    """
    Canonical text of a whitelisted annotation: str, int, float, bool, None, X | None,
    list[str], list[int], dict[str, str] or Literal of str values. Anything else (an unresolved
    string, Annotated, Any, a class) raises ManifestError.
    """
    if hint is None or hint is _NONE_TYPE:
        return "None"
    if isinstance(hint, str):
        raise _whitelist_error(hint)
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        args = typing.get_args(hint)
        inner = [arg for arg in args if arg is not _NONE_TYPE]
        if len(args) == 2 and len(inner) == 1:
            text = _plain_annotation(inner[0])
            if text is not None:
                return text + _OPTIONAL_SUFFIX
        raise _whitelist_error(hint)
    text = _plain_annotation(hint)
    if text is None:
        raise _whitelist_error(hint)
    return text


def annotation_from_str(text: str) -> Any:
    """The type object for a canonical annotation (the inverse of annotation_to_str), built from the
    whitelist only; nothing is evaluated. Any other text raises ManifestError."""
    if text == "None":
        return None
    core = text[:-len(_OPTIONAL_SUFFIX)] if text.endswith(_OPTIONAL_SUFFIX) else text
    hint = _PLAIN_TYPES.get(core)
    if hint is None:
        hint = _literal_from_str(core)
    return hint | None if core != text else hint


def _literal_from_str(text: str) -> Any:
    if not (text.startswith("Literal[") and text.endswith("]")):
        raise _whitelist_error(text)
    try:
        values = json.loads("[" + text[len("Literal["):-1] + "]")
    except ValueError as e:
        raise _whitelist_error(text) from e
    if not values or not all(isinstance(value, str) for value in values):
        raise _whitelist_error(text)
    hint = typing.Literal[tuple(values)]
    if _plain_annotation(hint) != text:  # only the canonical spelling
        raise _whitelist_error(text)
    return hint


def _annotation_text(hint: Any) -> str:
    """Informational text of an annotation outside the whitelist (VPS-only tools)."""
    if hint is inspect.Parameter.empty:
        return ""
    if isinstance(hint, str):
        return hint
    if isinstance(hint, type):
        builtin = hint.__module__ == "builtins"
        return hint.__name__ if builtin else f"{hint.__module__}.{hint.__qualname__}"
    return repr(hint)


def _json_default(value: Any, strict: bool) -> Any:
    if value is None or isinstance(value, (bool, int, str)) or (
            isinstance(value, float) and math.isfinite(value)):
        return value
    if strict:
        raise ManifestError(f"default {value!r} is not a JSON scalar")
    return repr(value)


def describe_params(func: Callable[..., Any], strict: bool = True) -> list[dict[str, Any]]:
    """
    K-71 params of a tool in signature order: {name, kind, annotation, default, required}. strict
    (every laptop-capable tool) raises ManifestError for *args, **kwargs, an annotation outside the
    whitelist or a default that is no JSON scalar; otherwise such values are kept as text.
    """
    try:
        hints = typing.get_type_hints(func, include_extras=True)
    except Exception:  # pylint: disable=broad-exception-caught
        hints = {}  # an annotation that does not resolve: strict tools fail on the raw string
    params: list[dict[str, Any]] = []
    for param in inspect.signature(func).parameters.values():
        if strict and param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise ManifestError(f"{param.name}: *args and **kwargs cannot be rebuilt by a proxy")
        hint = hints.get(param.name, param.annotation)
        try:
            annotation = annotation_to_str(hint)
        except ManifestError as e:
            if strict:
                raise ManifestError(f"{param.name}: {e}") from e
            annotation = _annotation_text(hint)
        required = param.default is inspect.Parameter.empty
        params.append({"name": param.name, "kind": param.kind.name, "annotation": annotation,
                       "default": None if required else _json_default(param.default, strict),
                       "required": required})
    return params


def _params_whitelisted(tool: Mapping[str, Any]) -> bool:
    try:
        for param in tool.get("params") or ():
            annotation_from_str(param["annotation"])
    except (ManifestError, KeyError, TypeError):
        return False
    return True


def _canonical_json(value: Any) -> bytes:
    """The K-70 canonical form: sorted keys, no spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def param_schema_sha256(params: Iterable[Mapping[str, Any]]) -> str:
    """K-71 parameter schema hash: names, annotations and required flags only, in order. A mismatch
    at exec time gives MANIFEST_MISMATCH; docstrings do not count."""
    return _sha256([{"name": param["name"], "annotation": param["annotation"],
                     "required": bool(param["required"])} for param in params])


def live_param_schema_sha256(func: Callable[..., Any]) -> str:
    """param_schema_sha256 of a registered tool, as the Desktop Node checks it at exec time."""
    return param_schema_sha256(describe_params(func, strict=False))


def lk_schema_sha256(func: Callable[..., Any]) -> str:
    """sha256 of the full LLM declaration LiveKit builds for func (name, description and
    parameters)."""
    # pylint: disable=import-outside-toplevel
    from livekit.agents import llm
    from livekit.agents.llm import utils as llm_utils
    return _sha256(llm_utils.build_legacy_openai_schema(llm.function_tool(func),
                                                        internally_tagged=True))


# What shapes lk_schema_sha256 besides the tool: LiveKit's schema builder, pydantic's JSON schema
# and docstring_parser, which reads the tool and parameter descriptions (not pinned by
# requirements.txt). Hashes made with other versions of these are not comparable.
LK_SCHEMA_DISTRIBUTIONS = ("docstring-parser", "livekit-agents", "pydantic", "pydantic-core")


def lk_schema_libs() -> dict[str, str]:
    """The installed versions of LK_SCHEMA_DISTRIBUTIONS ('missing' for one that is not installed);
    the manifest records them as lk_schema_libs."""
    versions: dict[str, str] = {}
    for dist in LK_SCHEMA_DISTRIBUTIONS:
        try:
            versions[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            versions[dist] = "missing"
    return versions


def _version_changes(old: Any, new: Any) -> list[str]:
    old = old if isinstance(old, Mapping) else {}
    new = new if isinstance(new, Mapping) else {}
    return [f"{dist} {old.get(dist, 'unrecorded')} -> {new.get(dist, 'unrecorded')}"
            for dist in sorted(set(old) | set(new)) if old.get(dist) != new.get(dist)]


def lk_schema_drift(manifest: Mapping[str, Any]) -> list[str]:
    """Why lk_schema_sha256 computed here cannot be compared with the manifest's: one
    'distribution recorded -> installed' per differing version; empty when it can."""
    return _version_changes(manifest.get("lk_schema_libs"), lk_schema_libs())


# ---------------------------------------------------------------- manifest

_META_KEYS = ("timeout", "risk", "arg_policies", "exclusive", "voice_confirm", "max_output_chars",
              "long_running")
_UNHASHED = ("manifest_sha256", "git_sha")
_REQUIRED = object()


@dataclass(frozen=True)
class PcOperation:
    """An internal laptop operation (K-70 pc.*): never LLM-visible, on the node's allow-list.
    params are (name, canonical annotation, default); _REQUIRED marks a required one."""

    name: str
    doc: str
    params: tuple[tuple[str, str, Any], ...] = ()
    cls: str = READ
    idempotent: bool = False
    timeout: float = 15.0
    arg_hooks: tuple[str, ...] = ()

    def manifest_entry(self) -> dict[str, Any]:
        params = [{"name": name, "kind": "KEYWORD_ONLY", "annotation": annotation,
                   "default": None if default is _REQUIRED else default,
                   "required": default is _REQUIRED} for name, annotation, default in self.params]
        return {"name": self.name, "doc": self.doc, "params": params, "cls": self.cls,
                "placement": LAPTOP, "idempotent": self.idempotent, "timeout": self.timeout,
                "arg_hooks": list(self.arg_hooks),
                "param_schema_sha256": param_schema_sha256(params)}


# pc.speak_local and pc.play_sound are 'read': they need mode read or full and are not actuation
# (K-76 Origins), like pc.fetch whose open only starts an auto-open, non-executable type (K-73).
PC_OPERATIONS: tuple[PcOperation, ...] = (
    PcOperation("pc.ping", "Link and clock probe; answers at once.",
                idempotent=True, timeout=5.0),
    PcOperation("pc.capture_screen", "Captures the laptop screen and uploads it as an encrypted "
                "blob (K-73).", cls=CAPTURE, idempotent=True, timeout=20.0),
    PcOperation("pc.capture_webcam", "Captures one webcam frame (presence only) and uploads it as "
                "an encrypted blob.", cls=CAPTURE, idempotent=True, timeout=20.0),
    PcOperation("pc.read_file", "Uploads one file of at most 25 MB from inside the open roots as "
                "an encrypted blob.", (("path", "str", _REQUIRED),), cls=EXPOSURE, idempotent=True,
                timeout=60.0, arg_hooks=("open_roots:path",)),
    PcOperation("pc.fetch", "Copies a relay file (K-73) into the laptop mirror after its sha256 "
                "check; open starts an auto-open type.",
                (("area", 'Literal["outputs", "shared"]', _REQUIRED), ("relpath", "str", _REQUIRED),
                 ("sha256", "str", _REQUIRED), ("size", "int", _REQUIRED), ("open", "bool", False)),
                timeout=120.0),
    PcOperation("pc.open_url", "Opens an http(s) URL in the laptop's default browser.",
                (("url", "str", _REQUIRED),), cls=INPUT),
    PcOperation("pc.speak_local", "Speaks a short text on the laptop with a toast (K-77 delivery).",
                (("text", "str", _REQUIRED),), timeout=60.0),
    PcOperation("pc.play_sound", "Plays a named JARVIS sound on the laptop.",
                (("name", "str", _REQUIRED),)),
)


def _manifest_meta(func: Callable[..., Any]) -> dict[str, Any]:
    meta = dict(getattr(func, "__jarvis_meta__", None) or {})
    risk = meta.get("risk", "low")
    meta["risk"] = risk if isinstance(risk, str) else "dynamic"
    meta["arg_policies"] = dict(meta.get("arg_policies") or {})
    return {key: meta.get(key) for key in _META_KEYS}


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """sha256 of the manifest content without git_sha and the digest itself, so the same tools give
    the same digest on every commit and machine."""
    return _sha256({key: value for key, value in manifest.items() if key not in _UNHASHED})


def build_manifest(tools: Iterable[Callable[..., Any]], git_sha: str = "unknown",
                   table: Mapping[str, ToolPlacement] | None = None) -> dict[str, Any]:
    """
    The K-71 manifest of registered tools (plugin_manager.get_tools()): tools sorted by name with
    their params, meta, cls, placement, idempotent and both hashes, then the pc.* operations, the
    lk_schema_libs versions and manifest_sha256. Raises ManifestError listing every tool without a
    placement entry and every laptop-capable tool outside the annotation whitelist.
    """
    table = PLACEMENTS if table is None else table
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    for func in tools:
        name = getattr(func, "__name__", "")
        entry = table.get(name)
        if entry is None:
            problems.append(f"{name}: no placement entry in tool_placement.py")
            continue
        try:
            params = describe_params(func, strict=laptop_capable(entry))
        except ManifestError as e:
            problems.append(f"{name}: {e}")
            continue
        entries.append({
            "name": name, "module": func.__module__, "doc": func.__doc__, "params": params,
            "meta": _manifest_meta(func), "cls": entry.cls, "placement": entry.kind,
            "idempotent": entry.idempotent, "param_schema_sha256": param_schema_sha256(params),
            "lk_schema_sha256": lk_schema_sha256(func),
        })
    if problems:
        raise ManifestError("; ".join(problems))
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION, "git_sha": git_sha,
        "tools": sorted(entries, key=lambda tool: tool["name"]),
        "pc_operations": [op.manifest_entry()
                          for op in sorted(PC_OPERATIONS, key=lambda op: op.name)],
        "lk_schema_libs": lk_schema_libs(),
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return json.loads(json.dumps(manifest))  # plain JSON values, as a loaded file has them


def load_manifest(path: str | None = None) -> dict[str, Any]:
    """The manifest file after its integrity checks: ManifestError when it is missing, unreadable,
    another version, edited by hand (manifest_sha256 differs) or lists a tool twice."""
    path = path or MANIFEST_PATH
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as e:
        raise ManifestError(f"cannot read {os.path.basename(path)}: {e}") from e
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise ManifestError(
            f"{os.path.basename(path)} is not a version {MANIFEST_VERSION} manifest")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise ManifestError("manifest_sha256 does not match the content")
    tools = manifest.get("tools")
    if not isinstance(tools, list) or not all(isinstance(tool, dict) and isinstance(
            tool.get("name"), str) for tool in tools):
        raise ManifestError("tools must be a list of named entries")
    if len({tool["name"] for tool in tools}) != len(tools):
        raise ManifestError("a tool is listed twice")
    return manifest


def manifest_tools(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The manifest's tools by name."""
    return {tool["name"]: tool for tool in manifest.get("tools") or ()}


def manifest_differences(committed: Mapping[str, Any], live: Mapping[str, Any]) -> list[str]:
    """What differs between two manifests, ignoring git_sha: changed top-level keys first, then
    added or removed tools and the changed fields of a tool. The tools' lk_schema_sha256 values
    are compared only when both manifests record the same lk_schema_libs."""
    diffs: list[str] = []
    libs = _version_changes(committed.get("lk_schema_libs"), live.get("lk_schema_libs"))
    if libs:
        diffs.append(f"lk_schema_libs changed: {', '.join(libs)} (lk_schema_sha256 not compared)")
    for key in sorted((set(committed) | set(live)) - {"tools", "lk_schema_libs", *_UNHASHED}):
        if committed.get(key) != live.get(key):
            diffs.append(f"{key} changed")
    ignored = {"lk_schema_sha256"} if libs else set()
    old, new = manifest_tools(committed), manifest_tools(live)
    for name in sorted(set(old) | set(new)):
        if name not in old:
            diffs.append(f"{name}: registered but not in the committed manifest")
        elif name not in new:
            diffs.append(f"{name}: in the committed manifest but not registered")
        else:
            changed = sorted(key for key in (set(old[name]) | set(new[name])) - ignored
                             if old[name].get(key) != new[name].get(key))
            if changed:
                diffs.append(f"{name}: {', '.join(changed)} changed")
    return diffs


# ---------------------------------------------------------------- remote discovery


@dataclass(frozen=True)
class RemotePlan:
    """
    How the VPS brain serves every expected tool name (K-71); C-119 builds the proxies.
    vps_local: discovered here and run here (vps, split and routed tools). swap_for_proxy:
    laptop-placed tools an imported module registered; add_proxy: laptop-placed manifest tools whose
    module is not imported. expected = discovery union manifest. missing: expected names nothing
    can serve; unplaced: expected names without a placement. not_in_manifest: run here but absent
    from the manifest (stale file). stale_entries: table names that match no tool.
    """

    vps_local: tuple[str, ...] = ()
    swap_for_proxy: tuple[str, ...] = ()
    add_proxy: tuple[str, ...] = ()
    expected: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    not_in_manifest: tuple[str, ...] = ()
    stale_entries: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """False means a mismatch: module_health marks the tools degraded."""
        return not (self.missing or self.unplaced or self.errors)

    @property
    def laptop_proxies(self) -> tuple[str, ...]:
        return tuple(sorted(self.swap_for_proxy + self.add_proxy))

    def detail(self) -> str:
        parts = [f"{label}: {', '.join(names)}" for label, names in
                 (("missing", self.missing), ("unplaced", self.unplaced)) if names]
        return "; ".join(parts + list(self.errors))


def remote_skip_modules(manifest: Mapping[str, Mapping[str, Any]] | None,
                        places: Mapping[str, str | Route]) -> frozenset[str]:
    """Modules remote discovery never imports: the static deny-list plus every module whose manifest
    tools are all laptop-placed."""
    by_module: dict[str, list[str]] = {}
    for name, tool in (manifest or {}).items():
        by_module.setdefault(str(tool.get("module") or ""), []).append(name)
    laptop_only = {module for module, names in by_module.items() if module and all(
        placement_kind(places.get(name)) == LAPTOP for name in names)}
    return frozenset(REMOTE_DENIED_MODULES | laptop_only)


def plan_remote(discovered: Mapping[str, str], manifest: Mapping[str, Mapping[str, Any]] | None,
                places: Mapping[str, str | Route], errors: Iterable[str] = ()) -> RemotePlan:
    """The RemotePlan for the tools discovered in remote mode ({name: module}), the manifest's tools
    by name and the effective places. A laptop tool needs its manifest entry to become a proxy."""
    manifest = manifest or {}
    expected = sorted(set(discovered) | set(manifest))
    lists: dict[str, list[str]] = {key: [] for key in ("vps_local", "swap_for_proxy", "add_proxy",
                                                      "missing", "unplaced", "not_in_manifest")}
    for name in expected:
        kind = placement_kind(places.get(name))
        if name in discovered and name not in manifest:
            lists["not_in_manifest"].append(name)
        if kind is None:
            lists["unplaced"].append(name)
            if name not in discovered:
                lists["missing"].append(name)  # manifest-only and placed nowhere
        elif kind == LAPTOP:
            target = ("missing" if name not in manifest
                      else "swap_for_proxy" if name in discovered else "add_proxy")
            lists[target].append(name)
        else:
            lists["vps_local" if name in discovered else "missing"].append(name)
    stale = sorted(set(places) - set(expected) - set(INTERNAL_TOOLS))
    return RemotePlan(expected=tuple(expected), stale_entries=tuple(stale), errors=tuple(errors),
                      **{key: tuple(names) for key, names in lists.items()})


class LaptopOnlyImportGuard(importlib.abc.MetaPathFinder):
    """Remote mode: importing a module of the static deny-list raises ImportError, so no VPS module
    can pull the system-control or a GUI module in, not even indirectly (K-71)."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> None:
        """Refuses a deny-listed module; every other import goes on to the next finder."""
        if fullname in REMOTE_DENIED_MODULES:
            raise ImportError(f"{fullname} is laptop-only and never imported in remote mode (K-71)",
                              name=fullname)


def install_import_guard() -> None:
    """Puts the LaptopOnlyImportGuard first on sys.meta_path, once per process."""
    if not any(isinstance(finder, LaptopOnlyImportGuard) for finder in sys.meta_path):
        sys.meta_path.insert(0, LaptopOnlyImportGuard())


def remove_import_guard() -> None:
    sys.meta_path[:] = [finder for finder in sys.meta_path
                        if not isinstance(finder, LaptopOnlyImportGuard)]


# ---------------------------------------------------------------- VPS argument hooks (C-124)
#
# The VPS side of ARG_HOOKS, applied by the agent to a tool call that runs on the VPS (remote mode;
# services/ai_core/remote_compat.install_arg_hooks wraps the registered tools). The PC brain never
# applies them. Paths are checked after realpath, so a symlink, '..' or /proc/self cannot leave a root.

VPS_APP_ROOT = "/app"
# Where the agent writes a blob pulled from the laptop (K-73, a Windows-path attachment, C-119).
VPS_BLOB_DIR = os.path.join(tempfile.gettempdir(), "jarvis_blobs")
VPS_DATA_DIRS = ("conversations", "chroma_db", "data", "logs", "backups")
VPS_SYSTEM_DENY = ("/proc", "/sys", "/etc")
HOOK_DENIED = "ARG_PATH_DENIED"
HOOK_OUTSIDE = "ARG_PATH_OUTSIDE_ROOTS"
HOOK_BAD_VALUE = "ARG_INVALID"


class ArgHookError(ValueError):
    """A VPS argument hook refused a call. code is a K-12 error_code; the argument value is never in
    the message."""

    def __init__(self, code: str, hook: str, arg: str, message: str) -> None:
        super().__init__(message)
        self.code, self.hook, self.arg = code, hook, arg


@dataclass(frozen=True)
class HookRoots:
    """Folders the VPS hooks use: attachment roots, media roots (zip_files), denied trees and the
    folders a relative path is tried in."""

    attachments: tuple[str, ...]
    media: tuple[str, ...]
    denied: tuple[str, ...]
    relative_bases: tuple[str, ...]


def vps_hook_roots(project_root: str, outputs_dir: str, shared_dir: str,
                   blob_dir: str = VPS_BLOB_DIR) -> HookRoots:
    """The roots for this deployment: /app/Jarvis_Outputs, /app/Jarvis_Shared and the blob dir (plus
    the configured outputs and shared folders) for attachments; /proc, /sys, /etc and the agent data
    folders under /app and under project_root are denied."""
    app_outputs = os.path.join(VPS_APP_ROOT, "Jarvis_Outputs")
    app_shared = os.path.join(VPS_APP_ROOT, "Jarvis_Shared")
    media = tuple(dict.fromkeys((outputs_dir, shared_dir, app_outputs, app_shared)))
    denied = list(VPS_SYSTEM_DENY)
    for root in dict.fromkeys((VPS_APP_ROOT, project_root)):
        denied += [os.path.join(root, name) for name in VPS_DATA_DIRS]
        denied += [os.path.join(root, name) for name in (".env", ".env.agent", ".env.svc")]
    return HookRoots(attachments=media + (blob_dir,), media=media, denied=tuple(dict.fromkeys(denied)),
                     relative_bases=tuple(dict.fromkeys((project_root, outputs_dir))))


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(path))


def _inside(path: str, root: str, strict: bool = False) -> bool:
    root = _real(root)
    try:
        common = os.path.commonpath([path, root])
    except ValueError:  # another drive
        return False
    return common == root and not (strict and path == root)


def contained_path(value: Any, roots: tuple[str, ...], denied: tuple[str, ...],
                   relative_bases: tuple[str, ...], *, hook: str, arg: str,
                   strict: bool = False) -> str:
    """
    The real path of value when it lies inside one of roots (strictly inside with strict) and in no
    denied tree; a relative value is tried against each relative base in turn. Raises ArgHookError
    otherwise. The real path is what the tool then gets, so it cannot change between check and use.
    """
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ArgHookError(HOOK_BAD_VALUE, hook, arg, f"{arg}: not a usable path")
    raw = os.path.expanduser(value.strip())
    candidates = [raw] if os.path.isabs(raw) else [os.path.join(base, raw) for base in relative_bases]
    reals = [_real(candidate) for candidate in candidates]
    if any(_inside(real, root) for real in reals for root in denied):
        raise ArgHookError(HOOK_DENIED, hook, arg, f"{arg}: this location is never used on the VPS")
    inside = [candidate for candidate, real in zip(candidates, reals)
              if any(_inside(real, root, strict) for root in roots)]
    if not inside:
        raise ArgHookError(HOOK_OUTSIDE, hook, arg,
                           f"{arg}: only files inside Jarvis_Outputs or Jarvis_Shared can be used here")
    existing = [candidate for candidate in inside if os.path.exists(candidate)]
    return os.path.realpath((existing or inside)[0])


def safe_basename(value: Any) -> str | None:
    """The last path part of a model-supplied name ('\\' counts as a separator); None when nothing
    usable is left ('', '.', '..'), so the tool's own default applies."""
    if not isinstance(value, str):
        return None
    base = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip().replace("\x00", "")
    if ":" in base[:2]:  # a bare drive spelling such as 'C:name'
        base = base.split(":", 1)[1]
    return None if base in ("", ".", "..") else base


def non_owner_recipients(value: Any, owners: Iterable[str]) -> list[str]:
    """The addresses in a recipient field (comma lists and 'Name <addr>' allowed) that are not the
    owner's; an empty or unparsable field counts as one non-owner recipient."""
    from email.utils import getaddresses  # pylint: disable=import-outside-toplevel
    known = {owner.strip().lower() for owner in owners if owner and owner.strip()}
    addresses = [addr.strip().lower() for _, addr in getaddresses([str(value or "")]) if addr.strip()]
    if not addresses:
        return ["<none>"]
    return [addr for addr in addresses if addr not in known]


@dataclass(frozen=True)
class HookOutcome:
    """The arguments after the VPS hooks and the risk they raise the call to (None: unchanged)."""

    arguments: dict[str, Any]
    risk: str | None = None
    applied: tuple[str, ...] = ()


def vps_hooks(name: str, entry: ToolPlacement | None = None) -> tuple[tuple[str, str], ...]:
    """The (hook, argument) pairs the agent applies to name on the VPS."""
    entry = PLACEMENTS.get(name) if entry is None else entry
    if entry is None:
        return ()
    pairs = (hook.partition(":") for hook in entry.arg_hooks)
    return tuple((hook, arg) for hook, _, arg in pairs if ARG_HOOKS.get(hook) == VPS)


def apply_vps_arg_hooks(name: str, arguments: Mapping[str, Any], *, roots: HookRoots,
                        owners: Iterable[str], online: bool = False) -> HookOutcome:
    """
    Applies the VPS hooks of tool name to its bound arguments (C-124): vps_attachment and
    vps_media_path replace the path by its checked real path (ArgHookError when it is outside the
    roots or in a denied tree), safe_name reduces a name to its basename (or drops it for the tool's
    default), owner_recipient raises the call to K-45 'high' for a recipient other than the owner.
    A routed tool whose call goes to the laptop gets no VPS hook.
    """
    entry = PLACEMENTS.get(name)
    hooks = vps_hooks(name, entry)
    args = dict(arguments)
    if not hooks or (entry is not None and isinstance(entry.place, Route)
                     and entry.place(args, online) == LAPTOP):
        return HookOutcome(args)
    risk: str | None = None
    applied: list[str] = []
    owner_list = list(owners)
    for hook, arg in hooks:
        if arg not in args:
            continue
        value = args[arg]
        if hook == "vps_attachment":
            if isinstance(value, str) and value.strip():
                args[arg] = contained_path(value, roots.attachments, roots.denied, roots.relative_bases,
                                           hook=hook, arg=arg)
        elif hook == "vps_media_path":
            args[arg] = contained_path(value, roots.media, roots.denied, roots.relative_bases[1:]
                                       or roots.relative_bases, hook=hook, arg=arg, strict=True)
        elif hook == "safe_name":
            base = safe_basename(value)
            if base is None:
                del args[arg]
            else:
                args[arg] = base
        elif hook == "owner_recipient":
            if non_owner_recipients(value, owner_list):
                risk = "high"
        applied.append(f"{hook}:{arg}")
    return HookOutcome(args, risk, tuple(applied))


class RemoteDiscovery:
    """One remote-mode discovery for the plugin manager: the modules to skip before the scan, the
    plan after it."""

    def __init__(self, overrides_raw: str | None, manifest_path: str | None = None) -> None:
        self.errors: list[str] = list(TABLE_ERRORS)
        self.manifest: dict[str, dict[str, Any]] | None = None
        try:
            self.manifest = manifest_tools(load_manifest(manifest_path))
        except ManifestError as e:
            self.errors.append(f"desktop manifest unusable: {e}")
        self.overrides, self.refused = parse_overrides(overrides_raw, self.manifest)
        self.places = effective_placements(self.overrides)
        self.skip_modules = remote_skip_modules(self.manifest, self.places)

    @classmethod
    def start(cls, log: logging.Logger) -> RemoteDiscovery:
        """Reads JARVIS_TOOL_PLACEMENT_OVERRIDES and the manifest, logs refused overrides and
        installs the import guard."""
        remote = cls(os.environ.get(OVERRIDES_ENV))
        for refusal in remote.refused:
            log.error("%s entry ignored: %s", OVERRIDES_ENV, refusal)
        install_import_guard()
        return remote

    def finish(self, discovered: Mapping[str, str], loaded: Collection[str],
               log: logging.Logger) -> RemotePlan:
        """The plan for the discovered tools ({name: module}); loaded is sys.modules. Logs a
        summary, warnings for a stale manifest or table, and ERROR for a mismatch."""
        errors = list(self.errors)
        imported = [name for name in sorted(REMOTE_DENIED_MODULES) if name in loaded]
        if imported:
            errors.append("laptop-only modules were imported: " + ", ".join(imported))
        plan = plan_remote(discovered, self.manifest, self.places, errors)
        log.info("Remote discovery: %d tools run here, %d laptop proxies (%d to swap), %d modules "
                 "skipped, %d names expected", len(plan.vps_local), len(plan.laptop_proxies),
                 len(plan.swap_for_proxy), len(self.skip_modules), len(plan.expected))
        if plan.not_in_manifest:
            log.warning("Not in the desktop manifest (stale file): %s",
                        ", ".join(plan.not_in_manifest))
        if plan.stale_entries:
            log.warning("Placement entries without a tool: %s", ", ".join(plan.stale_entries))
        if not plan.ok:
            log.error("Remote discovery mismatch, tools degraded: %s", plan.detail())
        return plan
