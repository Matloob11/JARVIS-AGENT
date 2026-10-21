"""C-32 (design .claude/wip/jarvisruntime/design.md §1.1 / §3 step 1): the worker prewarm hook.

prewarm(proc) fills JobProcess.userdata['tools'|'vad'|'city'] once per executor; plugin discovery
stays a singleton re-entry across prewarms; the city lookup is capped and falls back to
config.user_city; and the entrypoint reads the userdata, booting inline on any missing key
(PREWARM_MISS) so a job before/without prewarm still works (dev console mode, tests).
"""
# pylint: disable=redefined-outer-name, protected-access
import asyncio
import types

import pytest

from services.ai_core import jarvis_plugin_manager
from src.core import agent_runner as ar


class FakePlugins:
    """The real manager's singleton semantics (one instance, a 'discovered' re-entry guard), with
    counters so the tests can see how often the scan and the conversion really ran."""

    instance = None

    def __new__(cls):
        if cls.instance is None:
            inst = super().__new__(cls)
            inst.discovered = False
            inst.scans = 0
            inst.conversions = 0
            cls.instance = inst
        return cls.instance

    def discover_plugins(self, _path):
        if self.discovered:
            return
        self.discovered = True
        self.scans += 1

    def get_livekit_tools(self):
        self.conversions += 1
        return [f"tool-{index}" for index in range(79)]

    running_tools = staticmethod(set)


@pytest.fixture
def prewarm_env(monkeypatch):
    """prewarm() with fakes for the heavy parts: the plugin singleton, silero and the city."""
    FakePlugins.instance = None
    monkeypatch.setattr(jarvis_plugin_manager, "JarvisPluginManager", FakePlugins)
    vad = object()
    monkeypatch.setattr(ar, "silero",
                        types.SimpleNamespace(VAD=types.SimpleNamespace(load=lambda **_k: vad)))

    async def city():
        return "Lahore"

    monkeypatch.setattr(ar, "get_current_city", city)
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    return types.SimpleNamespace(vad=vad)


def _proc():
    """A fake JobProcess: prewarm only touches .userdata."""
    return types.SimpleNamespace(userdata={})


def test_prewarm_fills_tools_vad_and_city(prewarm_env):
    proc = _proc()
    ar.prewarm(proc)
    assert len(proc.userdata["tools"]) == 79
    assert proc.userdata["vad"] is prewarm_env.vad
    assert proc.userdata["city"] == "Lahore"


def test_discovery_runs_once_across_two_prewarms(prewarm_env):
    first, second = _proc(), _proc()
    ar.prewarm(first)
    ar.prewarm(second)  # num_idle_processes > 1 or a replaced executor: a cheap re-entry
    assert FakePlugins.instance.scans == 1
    assert len(first.userdata["tools"]) == len(second.userdata["tools"]) == 79


def test_a_slow_city_falls_back_to_config(prewarm_env, monkeypatch):
    async def slow_city():
        await asyncio.sleep(5)
        return "Never"

    monkeypatch.setattr(ar, "get_current_city", slow_city)
    monkeypatch.setattr(ar, "PREWARM_CITY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(ar.config, "user_city", "FallbackCity")
    proc = _proc()
    ar.prewarm(proc)
    assert proc.userdata["city"] == "FallbackCity"


def test_a_failing_part_leaves_its_key_unset_and_never_raises(prewarm_env, monkeypatch):
    def broken_scan(self, _path):  # pylint: disable=unused-argument
        raise RuntimeError("discovery broke")

    def no_model(**_kwargs):
        raise OSError("no model files")

    monkeypatch.setattr(FakePlugins, "discover_plugins", broken_scan)
    monkeypatch.setattr(ar, "silero", types.SimpleNamespace(VAD=types.SimpleNamespace(load=no_model)))
    proc = _proc()
    ar.prewarm(proc)  # must not raise: that job's PREWARM_MISS path boots inline instead
    assert "tools" not in proc.userdata and "vad" not in proc.userdata
    assert proc.userdata["city"] == "Lahore"


def test_prewarm_starts_the_vector_model_load_and_never_opens_chroma(prewarm_env, monkeypatch):
    """C-53 step 2 (K-69): the hook starts the embedding model's load (VectorMemory.warm_model, its own thread) and
    never the vector start-up (VectorMemory.prewarm, which opens Chroma)."""
    from services.ai_core import jarvis_vector_memory as jvm

    calls: list[str] = []
    monkeypatch.setattr(ar, "_vector_model_prewarm_allowed", lambda: True)
    monkeypatch.setattr(jvm.jarvis_vector_db, "warm_model", lambda: calls.append("warm_model"))
    monkeypatch.setattr(jvm.jarvis_vector_db, "prewarm", lambda: calls.append("prewarm"))
    proc = _proc()
    ar.prewarm(proc)
    assert calls == ["warm_model"] and len(proc.userdata["tools"]) == 79


# ------------------------------------------------------------------ the entrypoint's userdata reads


class _FakeLocal:
    async def set_attributes(self, _attributes):
        return None


class _FakeRoom:
    name = "jarvis-hybrid"

    def __init__(self):
        self.local_participant = _FakeLocal()
        self.remote_participants = {}

    def on(self, _event, callback=None):  # decorator or (event, callback), like rtc.Room
        return (lambda fn: fn) if callback is None else callback

    def off(self, *_args):
        return None


class _FakeJobContext:
    """The same shape as tests/core/test_agent_runner.py's context; .proc only when a test sets it."""

    def __init__(self):
        self.room = _FakeRoom()
        self.shutdowns = []

    async def connect(self):
        return None

    def shutdown(self, reason=""):
        self.shutdowns.append(reason)

    def is_fake_job(self):
        return False

    def add_shutdown_callback(self, _callback):
        return None


@pytest.fixture
def entry(prewarm_env, monkeypatch):
    """agent_runner.entrypoint with the same light stand-ins as the 'job' fixture in
    tests/core/test_agent_runner.py, plus captures for the C-32 reads (assistant kwargs, session
    vad). The session ends the job right after it starts, so each test runs one clean pass."""
    state = types.SimpleNamespace(assistants=[], sessions=[], vad=prewarm_env.vad)

    class FakeAssistant:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._stop_event = asyncio.Event()
            self.end_reason = None
            state.assistants.append(self)

        def attach_router(self, _router):
            return None

        def attach_session(self, _session):
            return None

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            state.sessions.append(self)

        def on(self, _event, callback=None):  # decorator or (event, callback), like AgentSession
            return (lambda fn: fn) if callback is None else callback

        async def aclose(self):
            return None

        async def start(self, room, agent, room_options, record=None):  # pylint: disable=unused-argument
            agent._stop_event.set()  # end the job right after the session starts

    class FakeRouter:
        text_input_cb = None

        def __init__(self, *_args, **_kwargs):
            pass

        def attach(self, _session):
            return None

        def detach(self):
            return None

    async def nothing(*_args, **_kwargs):
        return None

    async def no_tasks(*_args, **_kwargs):
        return []

    async def formatted_datetime():
        return {"formatted": "c32-day"}

    for name, value in (("_prewarm_vector_memory", lambda *_a: None), ("_prewarm_voice_id", lambda *_a: None),
                        ("get_formatted_datetime", formatted_datetime),
                        ("google", types.SimpleNamespace(realtime=types.SimpleNamespace(
                            RealtimeModel=lambda **_k: types.SimpleNamespace(voice="Charon")))),
                        ("_resolve_user_id", lambda _room: "c32-user"), ("BrainAssistant", FakeAssistant),
                        ("ConversationRouter", FakeRouter), ("AgentSession", FakeSession),
                        ("harden_room_input", lambda *_a, **_k: []), ("follow_owner_reconnects", lambda *_a: None),
                        ("_location_loop", nothing), ("_pc_vitals_loop", nothing),
                        ("_print_startup_banner", lambda: None), ("_start_loop_watchdog", lambda *_a: None),
                        ("_start_background_tasks", no_tasks), ("_rag_warm_delay", lambda: None),
                        ("_cleanup_session_resources", nothing), ("_quiet_livekit_agents_logger", lambda: None)):
        monkeypatch.setattr(ar, name, value)

    async def run(ctx):
        await asyncio.wait_for(ar.entrypoint(ctx), 10)
        for _ in range(5):  # let the best-effort attribute tasks finish
            await asyncio.sleep(0)

    state.run = run
    return state


@pytest.mark.asyncio
async def test_a_prewarmed_job_reuses_the_executor_singletons(entry):
    """The second (and every) job of a prewarmed executor: no in-job discovery, the prewarmed VAD
    and city are what the session and the assistant get."""
    ctx = _FakeJobContext()
    tools = [object(), object()]
    ctx.proc = types.SimpleNamespace(userdata={"tools": tools, "vad": entry.vad, "city": "PrewarmCity"})
    await entry.run(ctx)
    assert FakePlugins.instance.scans == 0  # discovery never ran inside the job
    assistant = entry.assistants[0]
    assert assistant.kwargs["tools"] is tools
    assert assistant.kwargs["current_city"] == "PrewarmCity"
    assert entry.sessions[0].kwargs["vad"] is entry.vad
    assert ctx.shutdowns == ["ended_by_command"]


@pytest.mark.asyncio
async def test_a_job_without_prewarm_boots_inline(entry):
    """PREWARM_MISS: a context without prewarmed userdata (dev console mode, tests) still boots —
    discovery, VAD and the city all run inline as before C-32."""
    ctx = _FakeJobContext()  # no .proc at all
    await entry.run(ctx)
    assert FakePlugins.instance.scans == 1  # the fallback discovered inline, once
    assistant = entry.assistants[0]
    assert len(assistant.kwargs["tools"]) == 79
    assert assistant.kwargs["current_city"] == "Lahore"  # the inline get_current_city call
    assert entry.sessions[0].kwargs["vad"] is entry.vad  # the inline silero load
    assert ctx.shutdowns == ["ended_by_command"]
