"""C-124 steps 1 and 2: services/utils/net_guard.py.

The guard table (every refused range, IPv4-mapped and other IPv6 spellings, the Docker gateway, the VPS
public IP, the jarvis-network subnet), name resolution per connect with a cache of at most 30 s, the real
socket wrappers on a loopback listener, the deploy-gate self_test(), and the child-process side: the env
allow-list on a real child process (no secret reaches it) and PR_SET_DUMPABLE. The gating keeps the PC
worker (Windows, local mode) untouched. Nothing here leaves the machine: names resolve through a fake
getaddrinfo, and the only real connects go to a listener on 127.0.0.1.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from services.utils import net_guard as ng
from services.utils.jarvis_config import RuntimeProfile

SUBNET = ipaddress.ip_network("172.16.4.0/24")
GATEWAY = ipaddress.ip_address("172.16.4.1")
PUBLIC_IP = "187.127.123.235"
FAKE_SECRET = "c124-fake-secret-value-0000"


@pytest.fixture(autouse=True)
def _clean_state():
    """Every test starts and ends without the guard, the child env guard or cached names."""
    ng.uninstall()
    ng.uninstall_child_env_guard()
    yield
    ng.uninstall()
    ng.uninstall_child_env_guard()


@pytest.fixture
def policy() -> ng.Policy:
    return ng.Policy(subnets=(SUBNET,), gateways=frozenset({GATEWAY}),
                     public_ips=frozenset({ipaddress.ip_address(PUBLIC_IP)}))


@pytest.fixture
def fake_dns(monkeypatch) -> dict[str, list[str]]:
    """socket.getaddrinfo answers from this table only; a name outside it does not resolve."""
    table: dict[str, list[str]] = {}
    calls: list[str] = []

    def getaddrinfo(host, port, family=0, type_=0, *args, **kwargs):  # noqa: ANN001
        calls.append(host)
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "not in the test table")
        infos = []
        for ip in table[host]:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            if family in (0, fam):
                infos.append((fam, socket.SOCK_STREAM, 6, "", (ip, port or 0)))
        return infos

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    table["__calls__"] = calls  # type: ignore[assignment]
    return table


# ---------------------------------------------------------------- the guard table


@pytest.mark.parametrize(("address", "reason"), [
    ("8.8.8.8", None),
    ("142.250.181.10", None),
    ("2606:4700:4700::1111", None),
    ("172.16.4.7", None),                       # jarvis-network: the bridge, LiveKit
    ("172.16.4.254", None),
    ("172.16.4.1", "docker_gateway"),
    ("::ffff:172.16.4.1", "docker_gateway"),
    ("172.16.4.0", "subnet_edge"),
    ("172.16.4.255", "subnet_edge"),
    (PUBLIC_IP, "public_ip"),
    (f"::ffff:{PUBLIC_IP}", "public_ip"),
    ("127.0.0.1", "loopback"),
    ("127.8.9.10", "loopback"),
    ("::1", "loopback"),
    ("10.0.0.1", "private"),
    ("172.16.5.1", "private"),                  # another Docker network on the VPS
    ("172.31.255.254", "private"),
    ("192.168.1.10", "private"),
    ("100.64.0.1", "cgnat"),
    ("100.127.255.254", "cgnat"),
    ("169.254.169.254", "link_local"),
    ("fe80::1", "link_local"),
    ("fd00::1", "ula"),
    ("fc00::5", "ula"),
    ("224.0.0.1", "multicast"),
    ("ff02::1", "multicast"),
    ("0.0.0.0", "unspecified"),
    ("::", "unspecified"),
    ("0.1.2.3", "this_network"),
    ("240.0.0.1", "reserved"),
    ("::ffff:127.0.0.1", "ipv4_embedded_loopback"),
    ("::ffff:10.1.2.3", "ipv4_embedded_private"),
    ("::ffff:169.254.169.254", "ipv4_embedded_link_local"),
    ("::127.0.0.1", "ipv4_embedded_loopback"),  # IPv4-compatible
    ("64:ff9b::a9fe:a9fe", "ipv4_embedded_link_local"),  # NAT64 of 169.254.169.254
    ("2002:7f00:1::1", "ipv4_embedded_loopback"),  # 6to4 of 127.0.0.1
    ("::ffff:8.8.8.8", None),
])
def test_the_guard_table(policy, address, reason):
    assert ng.check_ip(address, policy, ng._Resolver()) == reason


def test_without_a_subnet_rule_the_private_ranges_are_all_refused():
    bare = ng.Policy()
    assert bare.refusal(ipaddress.ip_address("172.16.4.7")) == "private"
    assert bare.refusal(ipaddress.ip_address("8.8.8.8")) is None


def test_an_allow_listed_name_is_allowed_but_never_the_gateway_or_public_ip(fake_dns):
    fake_dns["jarvis-extra"] = ["10.9.8.7"]
    fake_dns["bad-extra"] = [str(GATEWAY)]
    policy = ng.Policy(gateways=frozenset({GATEWAY}), allow_hosts=("jarvis-extra", "bad-extra"))
    resolver = ng._Resolver()
    assert ng.check_ip("10.9.8.7", policy, resolver) is None
    assert ng.check_ip(str(GATEWAY), policy, resolver) == "docker_gateway"
    assert ng.check_ip("10.9.8.8", policy, resolver) == "private"


def test_policy_from_proc_net_route_and_from_the_environment(tmp_path):
    route = tmp_path / "route"
    # eth0: 172.16.4.0/24 directly connected, default via 172.16.4.1 (little-endian hex as in /proc).
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010410AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000410AC\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n", encoding="ascii")
    auto = ng.build_policy({}, route_file=route)
    assert auto.subnets == (SUBNET,)
    assert GATEWAY in auto.gateways and ipaddress.ip_address(PUBLIC_IP) in auto.public_ips
    env = {ng.ENV_SUBNETS: "10.20.0.0/16", ng.ENV_PUBLIC_IPS: "203.0.113.9, nonsense",
           ng.ENV_ALLOW_HOSTS: "Jarvis-Bridge"}
    configured = ng.build_policy(env, route_file=tmp_path / "missing")
    assert configured.subnets == (ipaddress.ip_network("10.20.0.0/16"),)
    assert configured.gateways == frozenset({ipaddress.ip_address("10.20.0.1")})  # Docker's first host
    assert configured.public_ips == frozenset({ipaddress.ip_address("203.0.113.9")})
    assert configured.allow_hosts == ("jarvis-bridge",)


# ---------------------------------------------------------------- names, re-resolved with a short cache


def test_names_are_re_resolved_after_at_most_30_seconds(fake_dns, monkeypatch, policy):
    clock = [1000.0]
    monkeypatch.setattr(ng.time, "monotonic", lambda: clock[0])
    resolver = ng._Resolver(ttl=300)  # asked for more: capped at 30 s
    assert resolver.ttl == ng.DNS_CACHE_S == 30.0
    fake_dns["api.example.test"] = ["93.184.216.34"]
    assert ng.vet_address(socket.AF_INET, ("api.example.test", 443), policy, resolver) == ("93.184.216.34", 443)
    clock[0] += 29
    fake_dns["api.example.test"] = ["10.0.0.5"]  # DNS rebinding to a private address
    assert ng.vet_address(socket.AF_INET, ("api.example.test", 443), policy, resolver)[0] == "93.184.216.34"
    clock[0] += 2  # past 30 s: resolved again, and the private answer is refused
    with pytest.raises(ng.EgressBlocked, match="private"):
        ng.vet_address(socket.AF_INET, ("api.example.test", 443), policy, resolver)
    assert fake_dns["__calls__"].count("api.example.test") == 2  # type: ignore[union-attr]


def test_a_name_with_a_forbidden_and_an_allowed_address_connects_to_the_allowed_one(fake_dns, policy):
    fake_dns["mixed.example.test"] = ["127.0.0.1", "8.8.4.4"]
    assert ng.vet_address(socket.AF_INET, ("mixed.example.test", 80), policy, ng._Resolver()) == ("8.8.4.4", 80)


def test_an_unresolvable_name_and_other_families_pass_through_unharmed(fake_dns, policy):
    with pytest.raises(ng.EgressBlocked, match="not resolved"):
        ng.vet_address(socket.AF_INET, ("nowhere.example.test", 80), policy, ng._Resolver())
    unix_address = "/tmp/jarvis.sock"
    assert ng.vet_address(getattr(socket, "AF_UNIX", 1), unix_address, policy, ng._Resolver()) == unix_address


# ---------------------------------------------------------------- the real socket wrappers


@pytest.fixture
def listener():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    stop = threading.Event()

    def accept_all() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                continue

    thread = threading.Thread(target=accept_all, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join(2)
    server.close()


def test_installed_guard_refuses_loopback_and_lets_the_allowed_subnet_through(listener):
    ng.install(ng.Policy())
    assert ng.is_installed()
    for connect in (lambda: socket.create_connection(("127.0.0.1", listener), timeout=2),
                    lambda: socket.socket().connect(("127.0.0.1", listener))):
        with pytest.raises(ng.EgressBlocked):
            connect()
    # Re-installing swaps the policy: 127.0.0.0/8 as the allowed subnet now reaches the listener.
    ng.install(ng.Policy(subnets=(ipaddress.ip_network("127.0.0.0/8"),)))
    socket.create_connection(("127.0.0.1", listener), timeout=2).close()
    ng.uninstall()
    assert socket.socket.connect is not ng._guarded_connect
    socket.create_connection(("127.0.0.1", listener), timeout=2).close()


def test_connect_ex_is_guarded_too(listener):
    ng.install(ng.Policy())
    with socket.socket() as sock, pytest.raises(ng.EgressBlocked):
        sock.connect_ex(("127.0.0.1", listener))


def test_asyncio_connections_are_guarded(listener):
    # A selector loop connects with sock.connect, as on the VPS (Linux). It is made before the guard:
    # on Windows its self-pipe is a loopback socket pair.
    loop = asyncio.SelectorEventLoop()
    try:
        ng.install(ng.Policy())
        with pytest.raises(ng.EgressBlocked):
            loop.run_until_complete(asyncio.open_connection("127.0.0.1", listener))
    finally:
        ng.uninstall()
        loop.close()


def test_a_refused_connect_logs_the_reason_and_port_but_not_the_host(fake_dns, policy, caplog):
    fake_dns["internal.example.test"] = ["10.1.1.1"]
    with caplog.at_level("DEBUG", logger="JARVIS-NET-GUARD"), pytest.raises(ng.EgressBlocked):
        ng.vet_address(socket.AF_INET, ("internal.example.test", 8443), policy, ng._Resolver())
    infos = [r.getMessage() for r in caplog.records if r.levelname != "DEBUG"]
    assert infos == ["NET_GUARD refused a connect: private (port 8443)"]


# ---------------------------------------------------------------- where it applies


def _profile(role: str = "agent", brain: str = "pc", mode: str = "local") -> RuntimeProfile:
    return RuntimeProfile(role, brain, mode)


def _as_linux(monkeypatch) -> None:
    monkeypatch.setattr(ng.os, "name", "posix")
    monkeypatch.setattr(ng.sys, "platform", "linux")


def test_the_pc_worker_is_never_hardened(monkeypatch):
    monkeypatch.setenv(ng.ENV_FLAG, "1")
    monkeypatch.setattr(ng.os, "name", "nt")
    monkeypatch.setattr(ng.sys, "platform", "win32")
    for profile in (_profile(), _profile(mode="remote"), _profile(brain="vps", mode="remote")):
        assert not ng.on_vps(profile) and not ng.guard_enabled(profile)
        assert ng.harden_process(profile) == ng.HardeningReport()
    assert not ng.is_installed() and not ng.child_env_guard_installed()


def test_the_guard_needs_the_flag_and_a_vps_agent_or_svc(monkeypatch):
    _as_linux(monkeypatch)
    monkeypatch.setattr(ng, "_in_container", lambda: False)
    monkeypatch.delenv(ng.ENV_FLAG, raising=False)
    remote = _profile(mode="remote")
    assert ng.on_vps(remote) and not ng.guard_enabled(remote)  # default off
    monkeypatch.setenv(ng.ENV_FLAG, "1")
    assert ng.guard_enabled(remote) and ng.guard_enabled(_profile("svc", "vps", "remote"))
    assert not ng.guard_enabled(_profile("desktop", "vps", "remote"))
    assert not ng.guard_enabled(_profile("bridge", "vps", "remote"))
    assert not ng.guard_enabled(_profile())  # a Linux local-mode process outside a container
    monkeypatch.setattr(ng, "_in_container", lambda: True)
    assert ng.guard_enabled(_profile())


def test_harden_process_on_the_vps_installs_everything(monkeypatch):
    _as_linux(monkeypatch)
    monkeypatch.setenv(ng.ENV_FLAG, "1")
    monkeypatch.setattr(ng, "build_policy", lambda env=None: ng.Policy())
    monkeypatch.setattr(ng, "set_non_dumpable", lambda: True)
    report = ng.harden_process(_profile(mode="remote"))
    assert report == ng.HardeningReport(net_guard=True, child_env=True, non_dumpable=True)
    assert ng.is_installed() and ng.child_env_guard_installed()
    assert ng.harden_process(_profile(mode="remote")) == report  # idempotent


# ---------------------------------------------------------------- the deploy-gate self-test


def test_self_test_allows_bridge_livekit_and_gemini_and_refuses_gateway_and_public_ip(fake_dns, monkeypatch,
                                                                                      policy):
    fake_dns.update({"jarvis-ui-bridge": ["172.16.4.3"], "jarvis-livekit": ["172.16.4.4"],
                     ng.GEMINI_HOST: ["142.250.181.10", "2a00:1450:4019:80b::200a"]})
    monkeypatch.setenv("UI_BRIDGE_URL", "http://jarvis-ui-bridge:5002")
    monkeypatch.setenv("LIVEKIT_URL", "ws://jarvis-livekit:7880")
    report = ng.self_test(policy)
    assert report.ok, report.failures
    assert report.allowed == {"bridge": "allowed", "livekit": "allowed", "gemini": "allowed"}
    assert report.refused["172.16.4.1"] == "docker_gateway" and report.refused[PUBLIC_IP] == "public_ip"
    assert report.refused["169.254.169.254"] == "link_local"


def test_self_test_fails_when_a_needed_service_is_refused(fake_dns, monkeypatch, policy, capsys):
    fake_dns.update({"jarvis-ui-bridge": [str(GATEWAY)], "jarvis-livekit": ["172.16.4.4"],
                     ng.GEMINI_HOST: ["142.250.181.10"]})
    monkeypatch.setenv("UI_BRIDGE_URL", "http://jarvis-ui-bridge:5002")
    monkeypatch.setenv("LIVEKIT_URL", "ws://jarvis-livekit:7880")
    report = ng.self_test(policy)
    assert not report.ok and report.failures == ("bridge refused",)
    monkeypatch.setattr(ng, "build_policy", lambda env=None: policy)
    assert ng.run_cli(["--self-test"]) == 1
    assert '"ok": false' in capsys.readouterr().out
    fake_dns["jarvis-ui-bridge"] = ["172.16.4.3"]
    assert ng.run_cli(["--self-test"]) == 0


# ---------------------------------------------------------------- child processes


def test_the_child_env_allow_list():
    parent = {"PATH": "/usr/bin", "HOME": "/app", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "Asia/Karachi",
              "TMPDIR": "/tmp", "HF_HOME": "/models", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
              "HF_TOKEN": FAKE_SECRET, "HUGGING_FACE_HUB_TOKEN": FAKE_SECRET, "GOOGLE_API_KEY": FAKE_SECRET,
              "JARVIS_ENCRYPTION_KEY": FAKE_SECRET, "LIVEKIT_API_SECRET": FAKE_SECRET,
              "DESKTOP_AGENT_SIGNING_KEY": FAKE_SECRET, "EMAIL_APP_PASSWORD": FAKE_SECRET,
              "JARVIS_USER_ID": "matloob", "SYSTEMROOT": "C:\\Windows"}
    inherited = ng.scrub_child_env(None, parent)
    assert sorted(inherited) == ["HF_HOME", "HF_HUB_OFFLINE", "HOME", "LANG", "LC_ALL", "PATH", "TMPDIR",
                                 "TRANSFORMERS_OFFLINE", "TZ"]
    explicit = ng.scrub_child_env({**parent, "YTDLP_EXTRA": "1", "NEW_API_KEY": FAKE_SECRET}, parent)
    assert "YTDLP_EXTRA" in explicit and "PATH" in explicit  # what the caller added on purpose stays
    assert FAKE_SECRET not in explicit.values() and "JARVIS_USER_ID" not in explicit


def test_the_media_priority_reaches_child_processes():
    # C-126 (K-75): the image's ffmpeg/ffprobe/yt-dlp wrappers read JARVIS_MEDIA_PRIORITY in the child itself
    assert ng.child_env_allowed("JARVIS_MEDIA_PRIORITY")
    parent = {"PATH": "/usr/local/jarvis-bin:/usr/bin", "JARVIS_MEDIA_PRIORITY": "low", "JARVIS_NET_GUARD": "1",
              "GOOGLE_API_KEY": FAKE_SECRET}
    assert ng.scrub_child_env(None, parent) == {"PATH": "/usr/local/jarvis-bin:/usr/bin",
                                                "JARVIS_MEDIA_PRIORITY": "low"}


def _env_dump_command() -> list[str]:
    if os.name == "nt":
        return [os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "cmd.exe"), "/c", "set"]
    return ["/usr/bin/env"]


@pytest.fixture
def secret_env(monkeypatch):
    for name in ("GOOGLE_API_KEY", "LIVEKIT_API_SECRET", "JARVIS_ENCRYPTION_KEY", "HF_TOKEN", "EMAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, FAKE_SECRET)
    monkeypatch.setenv("TZ", "Asia/Karachi")


def test_no_secret_reaches_a_real_child_process(secret_env):
    assert ng.install_child_env_guard()
    for run in (lambda: subprocess.run(_env_dump_command(), capture_output=True, text=True, check=True).stdout,
                lambda: subprocess.check_output(_env_dump_command(), text=True,
                                                env={**os.environ, "C124_CALLER_ADDED": "yes"})):
        output = run()
        assert FAKE_SECRET not in output
        names = {line.split("=", 1)[0].upper() for line in output.splitlines() if "=" in line}
        assert {"GOOGLE_API_KEY", "LIVEKIT_API_SECRET", "JARVIS_ENCRYPTION_KEY", "HF_TOKEN"}.isdisjoint(names)
        assert "PATH" in names and "TZ" in names
    assert "C124_CALLER_ADDED=yes" in output


def test_no_secret_reaches_an_asyncio_child_process(secret_env):
    assert ng.install_child_env_guard()

    async def dump() -> str:
        proc = await asyncio.create_subprocess_exec(*_env_dump_command(), stdout=subprocess.PIPE)
        out, _ = await proc.communicate()
        return out.decode(errors="replace")

    output = asyncio.run(dump())
    assert FAKE_SECRET not in output and "PATH=" in output.upper()


def test_without_the_guard_a_child_inherits_everything_as_today(secret_env):
    output = subprocess.run(_env_dump_command(), capture_output=True, text=True, check=True).stdout
    assert FAKE_SECRET in output  # the PC worker's children keep today's environment


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux only")
def test_the_process_becomes_non_dumpable_on_linux():
    probe = ("import ctypes, sys; sys.path.insert(0, %r); from services.utils import net_guard as ng; "
             "ok = ng.set_non_dumpable(); print(ok, ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))") % str(
        Path(__file__).resolve().parents[2])
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.split() == ["True", "0"]  # PR_GET_DUMPABLE answers 0


def test_prctl_is_a_no_op_off_linux(monkeypatch):
    monkeypatch.setattr(ng.sys, "platform", "win32")
    assert ng.set_non_dumpable() is False
