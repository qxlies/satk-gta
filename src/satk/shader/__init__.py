"""satk.shader -- MTA:SA shader helper: world texture names, starter shader resources, effect checks.

CLI (``mcp=False``; MCP reaches them through the generic operation tool)::

    satk shader textures --kind road                   # names + a short exact apply/remove pattern list
    satk shader new wet_roads --name wetroads          # -> work/out/shader/wetroads/ (meta.xml, client.lua, .fx)
    satk shader check work/out/shader/wetroads         # fxc fx_2_0 + MTA rules + client Lua cross-check

Modules: :mod:`.wild` (MTA texture-name matching, pattern cover), :mod:`.names` (texture names, kinds and
selections from the profile index), :mod:`.fx` (effect reader with MTA include rules), :mod:`.check` (fxc discovery
and compile, MTA and Lua checks), :mod:`.templates` (resource templates in ``data/shader/templates``),
:mod:`.ops`. Stdlib only; the templates are MIT and written for satk.
"""

from __future__ import annotations

__all__: list[str] = []
