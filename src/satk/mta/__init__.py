"""satk.mta: MTA:SA developer tools.

* :mod:`.luaparse` - a Lua 5.1 lexer and parser (MTA:SA runs Lua 5.1.5) with scope resolution, stdlib only;
* :mod:`.api` - the MTA scripting reference the checks use (knowledge base tables, else the MTA source tree);
* :mod:`.resource` - ``meta.xml`` reading; :mod:`.lint` - ``satk mta lint``;
* :mod:`.scaffold` - ``satk mta resource new``; :mod:`.pack` - ``satk mta pack``; :mod:`.logs` - ``satk mta logs``;
* :mod:`.servercheck` - ``satk mta server-check`` (the built MTA server loads a resource on loopback, no client).

Operations live in :mod:`.ops` (all ``mcp=False``: agents reach them through ``satk_ops``/``satk_op``).
"""
