"""python -m src.core.desktop supervisor|node [--canary]

'supervisor' is what the logon task and 'agent_ctl node start' run (C-123; also 'python -m
src.core.desktop.supervisor'); 'node' is the child the supervisor starts. Neither starts on import.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else ""
    if command == "node":
        from src.core.desktop import node  # pylint: disable=import-outside-toplevel

        return node.main(args)
    if command == "supervisor":
        from src.core.desktop import supervisor  # pylint: disable=import-outside-toplevel

        return supervisor.main(args)
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
