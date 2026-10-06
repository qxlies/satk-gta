"""satk.kit: asset kit for any GTA:SA asset kind (lane L3 of wave A1).

* :mod:`satk.kit.kinds` - the kinds catalog (``data/kit/kinds.json``), material presets, segment tables,
  game-ready classes and the Blender scene contract;
* :mod:`satk.kit.plan` - template plans from a vanilla ``--like`` model or a ``--kind`` (names, matrices,
  roles and numbers only, never vanilla vertices);
* :mod:`satk.kit.atlas` - named regions of the shared vehicle atlases (measured support);
* :mod:`satk.kit.export` - packaging of a kit export (TXD of own textures, Mod Loader folder or ``mod.add``,
  re-import diff, lint and ``asset.check`` summary);
* :mod:`satk.kit.ops` - ``kit.kinds``, ``kit.template``, ``kit.export``, ``kit.scene_spec``.

The Blender side is ``satk_blender.kit`` (GPL): studio methods ``kit.*`` (contract K6) reached through
:func:`satk.studio.api.call` (K4), in a live session or as a one-shot job.
"""
