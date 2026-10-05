"""``python -m satk.mcp`` -- the MCP stdio server (what ``.mcp.json`` runs).

With arguments it is the ``satk mcp ...`` CLI instead: ``python -m satk.mcp selftest``.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        from satk.core.cli import main as cli

        return cli(["mcp", *args])
    from satk.core.envelope import dumps
    from satk.core.errors import SatkError, bootstrap_hint

    try:
        from .server import serve
    except ImportError as e:  # the mcp package is missing: say so on stderr, never on stdout
        err = SatkError("DEPENDENCY", f"python package 'mcp' is not installed ({e})",
                        hint=bootstrap_hint())
        sys.stderr.write(dumps(err.to_dict()) + "\n")
        return 3
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
