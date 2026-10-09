"""C-126 (K-75): the VPS agent image's Python list stays the PC brain's list, minus the desktop packages.

deploy/hostinger/requirements-agent.txt must carry every requirement of deploy/windows-local/requirements-hybrid.txt
(the PC brain) with the same specifier, extras and marker, except the Windows and GUI packages the containers cannot
use, and add only the headless OpenCV build and the docstring-parser pin (C-118). deploy/hostinger/constraints-agent.txt
pins the +cpu builds of the PC's torch versions, chromadb and the whole livekit family at the PC's versions, plus the
documented transitive pins. A change to the hybrid list that is not mirrored here fails these tests.
"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
HYBRID = ROOT / "deploy" / "windows-local" / "requirements-hybrid.txt"
AGENT = ROOT / "deploy" / "hostinger" / "requirements-agent.txt"
CONSTRAINTS = ROOT / "deploy" / "hostinger" / "constraints-agent.txt"
DOCKERFILE = ROOT / "deploy" / "hostinger" / "Dockerfile.agent"

# K-75's list, plus the Desktop Node's tray (pystray, C-121): Windows, audio-device and GUI packages
EXCLUDED = frozenset(canonicalize_name(name) for name in (
    "PyAudio", "sounddevice", "opencv-python", "opencv-contrib-python", "mediapipe", "PyAutoGUI", "pynput", "pystray",
    "pygetwindow", "comtypes", "pycaw", "pywin32", "pywhatkit", "pygame", "playsound"))
ADDED = frozenset({"opencv-contrib-python-headless", "docstring-parser"})
TORCH = ("torch", "torchaudio", "torchvision")
# Pinned for reproducibility although nothing names them: livekit-agents pulls sounddevice, and the hybrid list gives
# huggingface_hub a range
TRANSITIVE = frozenset({"sounddevice", "huggingface-hub"})
_COMMENT = re.compile(r"\s+#.*$")


def _parse(path: Path) -> dict[str, Requirement]:
    """{canonical name: requirement} of a pip requirements or constraints file (comments and blank lines skipped)."""
    requirements: dict[str, Requirement] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name not in requirements, f"{path.name} names {requirement.name} twice"
        requirements[name] = requirement
    return requirements


def _pin(requirement: Requirement) -> Version:
    """The version of an exact '==X' requirement."""
    specs = list(requirement.specifier)
    assert len(specs) == 1 and specs[0].operator == "==", f"{requirement} is not an exact pin"
    return Version(specs[0].version)


HYBRID_REQS = _parse(HYBRID)
AGENT_REQS = _parse(AGENT)
CONSTRAINT_REQS = _parse(CONSTRAINTS)


def test_the_hybrid_list_is_what_the_exclusions_expect():
    # If the PC list drops or renames one of these, this test and EXCLUDED are updated together
    assert {"opencv-python", "opencv-contrib-python", "mediapipe", "pyautogui", "pynput", "sounddevice"} <= set(
        HYBRID_REQS)
    assert {"torch", "torchaudio", "torchvision", "chromadb", "livekit-agents"} <= set(HYBRID_REQS)


def test_every_hybrid_requirement_is_kept_unchanged_except_the_desktop_packages():
    for name, hybrid in HYBRID_REQS.items():
        if name in EXCLUDED:
            assert name not in AGENT_REQS, f"{hybrid.name} is a desktop package and must stay out of the image"
            continue
        assert name in AGENT_REQS, f"{hybrid.name} is in requirements-hybrid.txt but not in requirements-agent.txt"
        agent = AGENT_REQS[name]
        assert agent.specifier == hybrid.specifier, f"{hybrid.name}: {agent.specifier} != {hybrid.specifier}"
        assert agent.extras == hybrid.extras, f"{hybrid.name}: extras differ"
        assert str(agent.marker) == str(hybrid.marker), f"{hybrid.name}: marker differs"


def test_the_agent_list_adds_only_the_headless_opencv_and_docstring_parser():
    assert set(AGENT_REQS) - set(HYBRID_REQS) == ADDED
    assert not EXCLUDED & set(AGENT_REQS)


def test_the_headless_opencv_is_the_pcs_opencv_build():
    assert _pin(AGENT_REQS["opencv-contrib-python-headless"]) == _pin(HYBRID_REQS["opencv-contrib-python"])


def test_docstring_parser_is_pinned_exactly():
    # C-118: the tool schemas and the desktop manifest depend on its version, so it never floats
    assert _pin(AGENT_REQS["docstring-parser"]) >= Version("0.16")


def test_every_constraint_is_an_exact_pin():
    for requirement in CONSTRAINT_REQS.values():
        _pin(requirement)
        assert requirement.marker is None and not requirement.extras, str(requirement)


def test_the_constraints_pin_the_cpu_builds_of_the_pcs_torch_versions():
    for name in TORCH:
        cpu = _pin(CONSTRAINT_REQS[name])
        assert cpu.local == "cpu", f"{name} must be the +cpu build, got {cpu}"
        assert cpu.public == str(_pin(HYBRID_REQS[name])), f"{name}: {cpu} is not the PC's version"
        # the agent list keeps the public pin, which the +cpu build satisfies
        assert AGENT_REQS[name].specifier.contains(cpu)


def test_the_constraints_pin_chromadb_and_the_livekit_family_at_the_pcs_versions():
    assert _pin(CONSTRAINT_REQS["chromadb"]) == _pin(HYBRID_REQS["chromadb"])
    livekit = {name for name in HYBRID_REQS if name.startswith("livekit")}
    assert {"livekit", "livekit-agents", "livekit-api", "livekit-protocol"} <= livekit
    assert livekit == {name for name in CONSTRAINT_REQS if name.startswith("livekit")}
    for name in livekit:
        assert _pin(CONSTRAINT_REQS[name]) == _pin(HYBRID_REQS[name]), name


def test_the_other_constraints_are_the_documented_transitive_pins():
    expected = set(TORCH) | {"chromadb"} | {name for name in CONSTRAINT_REQS if name.startswith("livekit")}
    assert set(CONSTRAINT_REQS) - expected == TRANSITIVE
    assert _pin(CONSTRAINT_REQS["sounddevice"]) == _pin(HYBRID_REQS["sounddevice"])
    hub = _pin(CONSTRAINT_REQS["huggingface-hub"])
    assert HYBRID_REQS["huggingface-hub"].specifier.contains(hub)
    assert AGENT_REQS["huggingface-hub"].specifier.contains(hub)


def test_the_dockerfile_installs_torch_from_the_cpu_index_then_the_list_with_the_constraints():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^COPY deploy/hostinger/requirements-agent\.txt deploy/hostinger/constraints-agent\.txt "
                     r"/tmp/agent-req/$", text, re.M)
    torch_step = re.search(r"pip install --no-deps --index-url https://download\.pytorch\.org/whl/cpu \\\n\s+"
                           r"-c /tmp/agent-req/constraints-agent\.txt torch torchaudio torchvision", text)
    assert torch_step, "torch, torchaudio and torchvision come alone from the CPU index, pinned by the constraints"
    rest = text.index("pip install -r /tmp/agent-req/requirements-agent.txt -c /tmp/agent-req/constraints-agent.txt")
    assert torch_step.start() < rest < text.index("&& pip check")
    # a GUI OpenCV or mediapipe would need libGL on the VPS: the build refuses it (grep without -q, see pipefail)
    assert "! pip list --format=freeze | grep -iE '^(opencv-python|opencv-contrib-python|mediapipe)=='" in text
