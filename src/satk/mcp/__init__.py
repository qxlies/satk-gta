"""satk.mcp — the MCP stdio server generated from the operation registry (SPEC §4.7, owner WP-06).

* :mod:`satk.mcp.adapter` — registry -> tools, groups, instructions, ``.mcp.json`` (stdlib only);
* :mod:`satk.mcp.server` — the server on ``mcp==2.3.0`` (all SDK code is here);
* :mod:`satk.mcp.worker` — subprocess runner for ``long_running`` operations;
* :mod:`satk.mcp.selftest` — ``satk mcp selftest`` (real stdio session);
* :mod:`satk.mcp.inline` — inline images (Pillow, lazy).

Run: ``python -m satk.mcp`` (``.mcp.json``) or ``satk mcp``.
"""
