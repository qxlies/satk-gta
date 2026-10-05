"""satk.idmgr -- model ID manager (B4; report 29 №2, report 30 G4). Stdlib only.

* :mod:`~satk.idmgr.ranges` - ID space facts: the engine limit, reserved ids, SA-MP and SA-MP DL ranges,
  model-info store sizes; range parsing and formatting;
* :mod:`~satk.idmgr.scan` - who defines which id: the index of a profile (loaded IDE lines, IDE files the
  game does not load such as modloader folders, modloader readme lines) and mod folders on disk;
* :mod:`~satk.idmgr.free` - free ids for a kind of model across profiles;
* :mod:`~satk.idmgr.conflicts` - ids and model names defined twice between mods and the game;
* :mod:`~satk.idmgr.remap` - move a mod's ids to another range in a copy (IDE, text/binary IPL, readme lines);
* :mod:`~satk.idmgr.ops` - ``satk id free``, ``satk id conflicts``, ``satk id remap``.

The group is ``id`` (``satk help ids`` is the SID topic).
"""
