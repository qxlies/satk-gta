"""satk.addon: add-on models for Mod Loader and the records of the game's data files.

* :mod:`.add` - ``satk mod add``: a Mod Loader folder that adds a new vehicle, ped, weapon or object next to a
  donor model (files, data lines with a free id, names checked);
* :mod:`.data` - ``satk data get|patch|explain`` for ``handling.cfg``, ``weapon.dat``, ``carcols.dat`` and
  ``peds.ide``/``pedstats.dat``;
* :mod:`.handling`, :mod:`.weapon` - token-preserving parsers/writers (an unchanged line is written back byte for
  byte); :mod:`.fields` - the field tables of ``data/addon/*.json``.

Everything reads the game read-only and writes only under ``<work>/out/addon/``. Stdlib only.
"""
