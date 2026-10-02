"""JARVIS Desktop Node (plan tasks C-120..C-123; contracts K-70, K-72, K-74, K-76; design
docs/ARCHITECTURE_VPS_BRAIN.md section 13).

The laptop half of the VPS brain: a small, single-instance, crash-safe process that runs the desktop tools
for the VPS agents over the signed and encrypted Desktop Link.

- supervisor.py: the pythonw entry ('pythonw -m src.core.desktop.supervisor'): single instance, refuses an
  elevated token, holds run/desktop_node.lock and the shared run/desktop_actor.lock, serves
  127.0.0.1:8082 (/health, token-protected POST /stop) and restarts the node child with backoff.
- node.py: the node child ('python -m src.core.desktop node'): settings and data root (settings.py), keys,
  write-ahead log (wal.py), tool executor (executor.py), Desktop Link client (link.py), status and audit
  (health.py), and the optional modules of the later tasks (policy, capabilities, sensors, mirror,
  backup_pull), each installed through its install(node) hook when it exists.

Nothing here runs at import time and this package imports no services module: the node must make dotenv a
no-op before any of them loads (settings.neutralize_dotenv).
"""

# Exit codes of the supervisor and of the node child.
EXIT_OK = 0                # stopped on request
EXIT_FAILED = 1            # crashed
EXIT_CONFIG = 2            # unusable configuration (node_config.json, keys, data root, port)
EXIT_ALREADY_RUNNING = 3   # run/desktop_node.lock or run/desktop_actor.lock is held (as agent_lock)
EXIT_ELEVATED = 4          # refused: an elevated (administrator) token
EXIT_STALLED = 70          # the node's event loop stalled for STALL_RESTART_S (C-31 watchdog): restart now
EXIT_RESTART = 75          # a restart-only setting changed (JARVIS_LINK_URL, K-15 path or app keys)

HEALTH_HOST = "127.0.0.1"
HEALTH_PORT = 8082
HEALTH_PORT_ENV = "JARVIS_NODE_HEALTH_PORT"  # tests and dev only
STALL_RESTART_S = 60.0

RUN_MODES = ("active", "canary")
