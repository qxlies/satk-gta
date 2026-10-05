"""satk.modinspect - mod inspector with Mod Loader's merge semantics (M3 lane C2).

What a mod changes and how Mod Loader (thelink2012/modloader 0.3, GTA SA) combines mods:

* :mod:`.source` - a mod as files: folder, ``.zip`` read in place, ``.img`` entries, one file;
* :mod:`.classify` - which Mod Loader plugin takes each file (``GetBehaviour`` rules);
* :mod:`.modloader` - ``modloader/`` folder, ``modloader.ini`` profiles, priorities, install order;
* :mod:`.traits` - data-file records and comparisons (port of std.data ``data_traits``);
* :mod:`.merge` - the merge itself (port of datalib's ``find_dominant_data`` and ``GetMergedData``);
* :mod:`.base` - the game under the mods (IMG names, IDE definitions, data files) read from a profile root;
* :mod:`.world` - base + mods resolved together; :mod:`.inspect`, :mod:`.conflicts`, :mod:`.effective`,
  :mod:`.check` build the reports; :mod:`.ops` - ``satk mod inspect|conflicts|effective|check``.

Ported code: Mod Loader (MIT) and its datalib (BSL-1.0); notices in ``NOTICE-modloader.txt``.
Stdlib only; the game is read only.
"""
