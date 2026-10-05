"""satk.re — sa-re, the light symbol DB for gta_sa.exe (SPEC §4.9; owner WP-09).

``satk re build`` merges gta-reversed (``hooks.json``, ``StaticRef``, vtables, ``VALIDATE_SIZE``),
plugin-sdk, the HOODLUM exe (sections, thunks, call targets) and MTA patch sites (upstream, our
trunk, Neon) into ``work/re/symdb.sqlite``; ``re addr/find/src/patches/limits/export`` query it and
the ``fn``/``g``/``vt``/``patch`` SID provider serves ``asset_get``/``asset_find``/``asset_refs``.

Modules: :mod:`.build` (pipeline), :mod:`.sources` (scanners), :mod:`.resolve` (address ->
function), :mod:`.api` (queries), :mod:`.db`, :mod:`.crash`, :mod:`.export`, :mod:`.provider`,
:mod:`.ops`, :mod:`.pe`, :mod:`.cpp`, :mod:`.gitsrc`. Standard library only.
"""
