"""satk.ingame: the in-game test loop on the real engine (MTA fork + resource ``satk-testdrive``).

Fast visual checks belong to the viewer (Ariane); behaviour (physics, collisions, damage, streaming
and LOD, lights, scripts) is checked here in the real game. ``satk ingame start`` builds a loopback
MTA server with the ``satk-agent`` bridge, the logic resource ``satk-testdrive`` and a generated
content resource ``satk-testdrive-mod`` (the mod's DFF/TXD/COL plus a manifest); the user's MTA
client joins it. ``reload`` hot-reloads changed files, ``spawn``/``drive`` place test elements at
test spots, ``check`` runs scripted behaviour checks with a screenshot per check and a verdict
against the vanilla model, ``shot`` and ``logs`` look at the game. satk never starts the client by
itself: ``satk ingame play`` (CLI only) or the generated ``play.cmd`` is for the user.

Modules: :mod:`.spots` (test spots, check geometry), :mod:`.modset` (mod folder -> models),
:mod:`.resource` (server resources, manifest), :mod:`.session` (server, preflight, bridge),
:mod:`.checks` (suites, verdicts, runner), :mod:`.logs` (log rows).
"""
