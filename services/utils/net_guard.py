"""
# services/utils/net_guard.py
C-124 process hardening of the VPS brain's agent and agent-svc processes (docs/ARCHITECTURE_VPS_BRAIN.md
section 11, items 9 and 10):

1. Egress guard (JARVIS_NET_GUARD=1, agent/svc roles on the VPS only). It wraps socket.create_connection
   and socket.socket.connect / connect_ex for AF_INET and AF_INET6 and refuses loopback, RFC1918, CGNAT,
   link-local (169.254.169.254 included), ULA, multicast, 0.0.0.0/8 and every other non-global address,
   in IPv4-mapped, IPv4-compatible, NAT64 and 6to4 spellings too, plus the Docker gateway and the VPS
   public IP. The jarvis-network subnet is allowed except its gateway, its network and its broadcast
   address. A host name is resolved by the guard (at most 30 s cached) and the vetted address is what the
   socket connects to, so DNS cannot switch it between check and connect.

   It is best effort and protects against SSRF only: subprocesses, C extensions that open their own
   sockets (LiveKit's Rust core, gRPC) and raw sendto() are not covered. It never starts on the PC worker,
   which reaches LiveKit at the VPS public IP and its LAN/loopback services (bridge, PinchTab).

   self_test() is the deploy gate's check (python -m services.utils.net_guard --self-test): the bridge,
   LiveKit and Gemini must be allowed; the Docker gateway, the public IP and the forbidden ranges refused.

2. Child processes (the VPS, Linux only): every subprocess.Popen, and so every
   asyncio.create_subprocess_exec/shell, gets an environment allow-list (PATH, HOME, LANG, LC_*, TZ,
   TMPDIR, HF_* without tokens, TRANSFORMERS_OFFLINE). prctl(PR_SET_DUMPABLE, 0) makes the process
   non-dumpable, so no other process of the same uid reads its memory or /proc/<pid>/environ. A Windows PC
   keeps today's child environment: its desktop tools need SYSTEMROOT, APPDATA, USERPROFILE, PATHEXT,
   COMSPEC and more.

harden_process() applies whatever this process qualifies for and is idempotent; agent_runner calls it at
import (worker and every job process) and again in the job entrypoint. Settings (names only, values never
logged): JARVIS_NET_GUARD, JARVIS_NET_GUARD_SUBNETS, JARVIS_NET_GUARD_GATEWAYS, JARVIS_NET_GUARD_PUBLIC_IPS,
JARVIS_NET_GUARD_ALLOW_HOSTS.
"""

from __future__ import annotations

import argparse
import ctypes
import inspect
import ipaddress
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from services.utils.jarvis_config import (
    BRAIN_VPS,
    DESKTOP_MODE_REMOTE,
    ROLE_AGENT,
    ROLE_SVC,
    RuntimeProfile,
    runtime_profile,
)

logger = logging.getLogger("JARVIS-NET-GUARD")

ENV_FLAG = "JARVIS_NET_GUARD"
ENV_SUBNETS = "JARVIS_NET_GUARD_SUBNETS"
ENV_GATEWAYS = "JARVIS_NET_GUARD_GATEWAYS"
ENV_PUBLIC_IPS = "JARVIS_NET_GUARD_PUBLIC_IPS"
ENV_ALLOW_HOSTS = "JARVIS_NET_GUARD_ALLOW_HOSTS"
# The VPS public IP (CONTRACTS K-38/OP-13); JARVIS_NET_GUARD_PUBLIC_IPS replaces it.
DEFAULT_PUBLIC_IPS = ("187.127.123.235",)
DNS_CACHE_S = 30.0
GEMINI_HOST = "generativelanguage.googleapis.com"
SELF_TEST_REFUSED = ("127.0.0.1", "10.0.0.1", "172.16.4.1", "192.168.1.1", "100.64.0.1",
                     "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1",
                     "::ffff:127.0.0.1", "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:7f00:1::1")

_TRUE = frozenset({"1", "true", "yes", "on"})
_INET_FAMILIES = (socket.AF_INET, socket.AF_INET6)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_THIS_NETWORK = ipaddress.ip_network("0.0.0.0/8")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPAT = ipaddress.ip_network("::/96")
_PROC_ROUTE = Path("/proc/net/route")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class EgressBlocked(ConnectionRefusedError):
    """The egress guard refused a connect (an OSError, so callers treat it as a failed connection)."""


# ---------------------------------------------------------------- the address policy


def _parse_ip(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value.split("%", 1)[0])  # an IPv6 scope id is not part of the address
    except ValueError:
        return None


def embedded_ipv4(ip: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 spelling carries (mapped, compatible, NAT64, 6to4); None otherwise."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    packed = int(ip) & 0xFFFFFFFF
    if ip in _NAT64 or (ip in _IPV4_COMPAT and packed > 1):  # ::, ::1 are not IPv4 spellings
        return ipaddress.IPv4Address(packed)
    return None


def _category(ip: IPAddress) -> str | None:
    """Why ip is never a destination, or None for a global unicast address."""
    checks = (
        (ip.is_loopback, "loopback"),
        (ip.is_multicast, "multicast"),
        (ip.is_unspecified, "unspecified"),
        (ip.is_link_local, "link_local"),
        (isinstance(ip, ipaddress.IPv4Address) and ip in _THIS_NETWORK, "this_network"),
        (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT, "cgnat"),
        (isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("fc00::/7"), "ula"),
        (ip.is_reserved, "reserved"),
        (ip.is_private, "private"),
        (not ip.is_global, "not_global"),
    )
    return next((reason for failed, reason in checks if failed), None)


@dataclass(frozen=True)
class Policy:
    """What the guard allows: subnets (minus their gateways, network and broadcast addresses), the
    resolved allow-listed host names, and always-refused addresses (Docker gateways, the public IP)."""

    subnets: tuple[IPNetwork, ...] = ()
    gateways: frozenset[IPAddress] = frozenset()
    public_ips: frozenset[IPAddress] = frozenset()
    allow_hosts: tuple[str, ...] = ()

    def refusal(self, ip: IPAddress, allowed_names: Iterable[IPAddress] = ()) -> str | None:
        """None when a connect to ip is allowed, else the reason it is refused."""
        inner = embedded_ipv4(ip)
        candidates = [ip] if inner is None else [ip, inner]
        for addr in candidates:
            if addr in self.gateways:
                return "docker_gateway"
            if addr in self.public_ips:
                return "public_ip"
        target = inner if inner is not None else ip
        for net in self.subnets:
            if target.version == net.version and target in net:
                if net.num_addresses > 2 and target in (net.network_address, net.broadcast_address):
                    return "subnet_edge"
                return None
        if target in set(allowed_names):
            return None
        if inner is not None:
            reason = _category(inner)
            return None if reason is None else f"ipv4_embedded_{reason}"
        return _category(ip)


def _proc_routes(path: Path = _PROC_ROUTE) -> tuple[list[IPNetwork], list[IPAddress]]:
    """Directly connected IPv4 subnets and default gateways from /proc/net/route (Linux); empty elsewhere."""
    subnets: list[IPNetwork] = []
    gateways: list[IPAddress] = []
    try:
        lines = path.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return subnets, gateways
    for line in lines:
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            dest, gateway, mask = (ipaddress.IPv4Address(int(value, 16).to_bytes(4, "little"))
                                   for value in (parts[1], parts[2], parts[7]))
        except ValueError:
            continue
        if int(dest) == 0 and int(mask) == 0:
            if int(gateway):
                gateways.append(gateway)
        elif int(gateway) == 0:
            try:
                subnets.append(ipaddress.ip_network(f"{dest}/{mask}", strict=False))
            except ValueError:
                continue
    return subnets, gateways


def build_policy(env: Mapping[str, str] | None = None, route_file: Path = _PROC_ROUTE) -> Policy:
    """
    The policy from the environment: JARVIS_NET_GUARD_SUBNETS (the jarvis-network subnet; default: the
    directly connected subnets of /proc/net/route), JARVIS_NET_GUARD_GATEWAYS (default: the default
    route's gateway plus the first host of each subnet, Docker's gateway), JARVIS_NET_GUARD_PUBLIC_IPS
    (default: the VPS public IP) and JARVIS_NET_GUARD_ALLOW_HOSTS. A malformed entry is skipped with a
    WARNING naming the variable only.
    """
    source = os.environ if env is None else env

    def items(name: str) -> list[str]:
        return [part.strip() for part in (source.get(name) or "").replace(";", ",").split(",") if part.strip()]

    def parse(name: str, values: list[str], kind: Any) -> list[Any]:
        parsed = []
        for value in values:
            try:
                parsed.append(kind(value))
            except ValueError:
                logger.warning("NET_GUARD %s has an entry that is not valid; it is skipped", name)
        return parsed

    route_subnets, route_gateways = _proc_routes(route_file)
    subnets = parse(ENV_SUBNETS, items(ENV_SUBNETS), lambda v: ipaddress.ip_network(v, strict=False))
    if not subnets:
        subnets = route_subnets
    gateways = parse(ENV_GATEWAYS, items(ENV_GATEWAYS), ipaddress.ip_address)
    if not gateways:
        gateways = list(route_gateways) + [next(net.hosts()) for net in subnets if net.num_addresses > 2]
    public = parse(ENV_PUBLIC_IPS, items(ENV_PUBLIC_IPS) or list(DEFAULT_PUBLIC_IPS), ipaddress.ip_address)
    hosts = tuple(host.lower() for host in items(ENV_ALLOW_HOSTS))
    return Policy(tuple(subnets), frozenset(gateways), frozenset(public), hosts)


# ---------------------------------------------------------------- resolution with a short cache


class _Resolver:
    """getaddrinfo with a cache of at most DNS_CACHE_S seconds per (host, family)."""

    def __init__(self, ttl: float = DNS_CACHE_S) -> None:
        self.ttl = min(ttl, DNS_CACHE_S)
        self._cache: dict[tuple[str, int], tuple[float, list[IPAddress]]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def resolve(self, host: str, family: int = 0) -> list[IPAddress]:
        literal = _parse_ip(host)
        if literal is not None:
            return [literal]
        key = (host.lower().rstrip("."), family)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and hit[0] > now:
                return list(hit[1])
        infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
        found: list[IPAddress] = []
        for info in infos:
            ip = _parse_ip(str(info[4][0]))
            if ip is not None and ip not in found:
                found.append(ip)
        with self._lock:
            self._cache[key] = (now + self.ttl, found)
        return found


@dataclass
class _State:
    installed: bool = False
    policy: Policy = field(default_factory=Policy)
    resolver: _Resolver = field(default_factory=_Resolver)
    originals: dict[str, Any] = field(default_factory=dict)


_state = _State()
_state_lock = threading.RLock()


def _allowed_name_ips(policy: Policy, resolver: _Resolver) -> list[IPAddress]:
    ips: list[IPAddress] = []
    for host in policy.allow_hosts:
        try:
            ips.extend(resolver.resolve(host))
        except OSError:
            continue
    return ips


def check_ip(ip: IPAddress | str, policy: Policy | None = None,
             resolver: _Resolver | None = None) -> str | None:
    """None when the guard allows a connect to ip, else the refusal reason (the guard table)."""
    addr = _parse_ip(ip) if isinstance(ip, str) else ip
    if addr is None:
        return "not_an_address"
    policy = _state.policy if policy is None else policy
    resolver = _state.resolver if resolver is None else resolver
    return policy.refusal(addr, _allowed_name_ips(policy, resolver))


def vet_address(family: int, address: Any, policy: Policy | None = None,
                resolver: _Resolver | None = None) -> Any:
    """
    The address a guarded connect may use: the same tuple with the host replaced by a vetted IP literal
    (the first allowed address the name resolves to). Raises EgressBlocked when nothing is allowed.
    Families other than AF_INET/AF_INET6 pass unchanged.
    """
    if family not in _INET_FAMILIES or not isinstance(address, tuple) or not address:
        return address
    policy = _state.policy if policy is None else policy
    resolver = _state.resolver if resolver is None else resolver
    host = str(address[0])
    try:
        candidates = resolver.resolve(host, family)
    except OSError as e:
        raise EgressBlocked(f"NET_GUARD: name not resolved ({type(e).__name__})") from e
    allowed_names = _allowed_name_ips(policy, resolver)
    reasons: list[str] = []
    for ip in candidates:
        if (family == socket.AF_INET) != (ip.version == 4):
            continue
        reason = policy.refusal(ip, allowed_names)
        if reason is None:
            return (str(ip), *address[1:])
        reasons.append(reason)
    reason = reasons[0] if reasons else "no_address"
    port = address[1] if len(address) > 1 else "?"
    logger.warning("NET_GUARD refused a connect: %s (port %s)", reason, port)
    logger.debug("NET_GUARD refused address %s", host)
    raise EgressBlocked(f"NET_GUARD: egress refused ({reason})")


# ---------------------------------------------------------------- install / uninstall


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    return _state.originals["connect"](self, vet_address(self.family, address))


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    return _state.originals["connect_ex"](self, vet_address(self.family, address))


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
    host = address[0] if isinstance(address, tuple) and address else None
    if isinstance(host, str) and _parse_ip(host) is None:
        # Refuses early, with one clear reason, a name whose every address is forbidden. The addresses
        # create_connection then tries are each vetted again by the guarded connect.
        _vet_any(host, address[1] if len(address) > 1 else 0, _state.policy, _state.resolver)
    return _state.originals["create_connection"](address, *args, **kwargs)


def install(policy: Policy | None = None) -> bool:
    """Wraps the socket connect functions (idempotent); returns True once installed."""
    with _state_lock:
        _state.policy = build_policy() if policy is None else policy
        _state.resolver.clear()
        if _state.installed:
            return True
        _state.originals = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex,
                            "create_connection": socket.create_connection}
        socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
        socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
        _state.installed = True
    logger.info("NET_GUARD on: %d allowed subnet(s), %d gateway(s) and %d public IP(s) refused",
                len(_state.policy.subnets), len(_state.policy.gateways), len(_state.policy.public_ips))
    return True


def uninstall() -> None:
    """Puts the original socket functions back (tests)."""
    with _state_lock:
        if not _state.installed:
            return
        socket.socket.connect = _state.originals["connect"]  # type: ignore[method-assign]
        socket.socket.connect_ex = _state.originals["connect_ex"]  # type: ignore[method-assign]
        socket.create_connection = _state.originals["create_connection"]  # type: ignore[assignment]
        _state.originals = {}
        _state.installed = False
        _state.resolver.clear()


def is_installed() -> bool:
    return _state.installed


# ---------------------------------------------------------------- where it applies


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.getenv("container"))


def on_vps(profile: RuntimeProfile | None = None) -> bool:
    """True for a Linux agent or svc process of the VPS brain (remote mode, brain vps or a container).
    Never on Windows, so the PC worker is never touched."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return False
    profile = runtime_profile() if profile is None else profile
    if profile.role not in (ROLE_AGENT, ROLE_SVC):
        return False
    return profile.mode == DESKTOP_MODE_REMOTE or profile.brain == BRAIN_VPS or _in_container()


def guard_enabled(profile: RuntimeProfile | None = None) -> bool:
    """JARVIS_NET_GUARD=1 and an agent/svc process on the VPS; default off."""
    return (os.getenv(ENV_FLAG) or "").strip().lower() in _TRUE and on_vps(profile)


# ---------------------------------------------------------------- child processes


CHILD_ENV_NAMES = frozenset({"PATH", "HOME", "LANG", "TZ", "TMPDIR", "TRANSFORMERS_OFFLINE"})
CHILD_ENV_PREFIXES = ("LC_", "HF_")
_SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")


def _looks_secret(name: str) -> bool:
    upper = name.upper()
    return any(word in upper for word in _SECRET_WORDS)


def child_env_allowed(name: str) -> bool:
    """True for a name a child process may inherit: the allow-list, never a token-like HF_* name."""
    upper = name.upper()
    if _looks_secret(upper):
        return False
    return upper in CHILD_ENV_NAMES or upper.startswith(CHILD_ENV_PREFIXES)


def scrub_child_env(env: Mapping[str, str] | None, parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """
    The environment a child gets. env=None (inherit): the allow-listed names of the parent. An explicit
    env: its allow-listed names plus the names the caller added that the parent does not have (and that
    do not look like a secret); a parent name outside the allow-list is dropped whatever its value.
    """
    parent = os.environ if parent is None else parent
    if env is None:
        return {name: value for name, value in parent.items() if child_env_allowed(name)}
    parent_names = {name.upper() for name in parent}
    return {name: value for name, value in env.items()
            if child_env_allowed(name) or (name.upper() not in parent_names and not _looks_secret(name))}


_popen_state: dict[str, Any] = {}
_ENV_POSITION = 10  # Popen(args, bufsize, executable, stdin, stdout, stderr, preexec_fn, close_fds, shell, cwd, env)


def _guarded_popen_init(self: subprocess.Popen, *args: Any, **kwargs: Any) -> None:
    if len(args) > _ENV_POSITION:
        args = (*args[:_ENV_POSITION], scrub_child_env(args[_ENV_POSITION]), *args[_ENV_POSITION + 1:])
    else:
        kwargs["env"] = scrub_child_env(kwargs.get("env"))
    _popen_state["init"](self, *args, **kwargs)


def install_child_env_guard() -> bool:
    """subprocess.Popen (and so asyncio's subprocess functions) gets the allow-listed environment."""
    with _state_lock:
        if "init" not in _popen_state:
            original = subprocess.Popen.__init__
            names = list(inspect.signature(original).parameters)
            if names[1:2] != ["args"] or names.index("env") != _ENV_POSITION + 1:
                logger.error("CHILD_ENV guard not installed: subprocess.Popen has an unexpected signature")
                return False
            _popen_state["init"] = original
            subprocess.Popen.__init__ = _guarded_popen_init  # type: ignore[method-assign]
    return True


def uninstall_child_env_guard() -> None:
    with _state_lock:
        original = _popen_state.pop("init", None)
        if original is not None:
            subprocess.Popen.__init__ = original  # type: ignore[method-assign]


def child_env_guard_installed() -> bool:
    return "init" in _popen_state


PR_SET_DUMPABLE = 4


def set_non_dumpable() -> bool:
    """prctl(PR_SET_DUMPABLE, 0) on Linux; False elsewhere or when the call fails."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            logger.warning("prctl(PR_SET_DUMPABLE) failed: errno %d", ctypes.get_errno())
            return False
    except (OSError, AttributeError) as e:
        logger.warning("prctl(PR_SET_DUMPABLE) unavailable: %s", type(e).__name__)
        return False
    return True


@dataclass(frozen=True)
class HardeningReport:
    net_guard: bool = False
    child_env: bool = False
    non_dumpable: bool = False


def harden_process(profile: RuntimeProfile | None = None) -> HardeningReport:
    """
    Applies what this process qualifies for: on the VPS (Linux agent/svc) the child env allow-list and
    PR_SET_DUMPABLE=0, plus the egress guard when JARVIS_NET_GUARD=1. Elsewhere (the PC) nothing.
    Idempotent; prctl is set again on every call, because an exec (a spawned job process) resets it.
    """
    profile = runtime_profile() if profile is None else profile
    if not on_vps(profile):
        return HardeningReport()
    was = (_state.installed, child_env_guard_installed())
    report = HardeningReport(net_guard=install() if guard_enabled(profile) else False,
                             child_env=install_child_env_guard(), non_dumpable=set_non_dumpable())
    if was != (report.net_guard, report.child_env):
        logger.info("PROCESS_HARDENING role=%s net_guard=%s child_env=%s non_dumpable=%s", profile.role,
                    report.net_guard, report.child_env, report.non_dumpable)
    return report


# ---------------------------------------------------------------- self-test


def _url_host(name: str, default: str | None = None) -> str | None:
    raw = (os.getenv(name) or default or "").strip()
    if not raw:
        return None
    host = urlsplit(raw if "//" in raw else f"//{raw}").hostname
    return host or None


@dataclass(frozen=True)
class SelfTestReport:
    ok: bool
    allowed: dict[str, str]
    refused: dict[str, str]
    failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "allowed": self.allowed, "refused": self.refused,
                "failures": list(self.failures)}


def self_test(policy: Policy | None = None, must_allow: Mapping[str, str] | None = None,
              must_refuse: Iterable[str] | None = None) -> SelfTestReport:
    """
    The deploy gate: every must_allow name (default: the bridge from UI_BRIDGE_URL, LiveKit from
    LIVEKIT_URL and Gemini) resolves to an allowed address, and every must_refuse address (default:
    SELF_TEST_REFUSED, the policy's gateways and public IPs) is refused. Nothing connects.
    """
    policy = build_policy() if policy is None else policy
    resolver = _Resolver()
    if must_allow is None:
        must_allow = {label: host for label, host in (
            ("bridge", _url_host("UI_BRIDGE_URL")), ("livekit", _url_host("LIVEKIT_URL")),
            ("gemini", GEMINI_HOST)) if host}
    refuse = list(SELF_TEST_REFUSED if must_refuse is None else must_refuse)
    if must_refuse is None:
        refuse += [str(ip) for ip in sorted(policy.gateways | policy.public_ips, key=str)]
    allowed: dict[str, str] = {}
    refused: dict[str, str] = {}
    failures: list[str] = []
    for label, host in must_allow.items():
        try:
            _vet_any(host, 443, policy, resolver)
            allowed[label] = "allowed"
        except EgressBlocked as e:
            allowed[label] = str(e)
            failures.append(f"{label} refused")
    names = _allowed_name_ips(policy, resolver)
    for value in dict.fromkeys(refuse):
        reason = policy.refusal(ipaddress.ip_address(value), names)
        refused[value] = reason or "ALLOWED"
        if reason is None:
            failures.append(f"{value} allowed")
    return SelfTestReport(not failures, allowed, refused, tuple(failures))


def _vet_any(host: str, port: Any, policy: Policy, resolver: _Resolver) -> Any:
    """vet_address for a host over IPv4, then IPv6: the first vetted address, else EgressBlocked."""
    last: EgressBlocked | None = None
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            return vet_address(family, (host, port), policy, resolver)
        except EgressBlocked as e:
            last = e
    raise last or EgressBlocked("NET_GUARD: egress refused")


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS egress guard (C-124)")
    parser.add_argument("--self-test", action="store_true", help="run the deploy-gate self-test")
    options = parser.parse_args(argv)
    if not options.self_test:
        parser.print_help()
        return 2
    report = self_test()
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(run_cli())
