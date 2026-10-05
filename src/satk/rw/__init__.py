"""RenderWare writers without Blender (M3-B1): DFF patches, COL from JSON, new IMG archives.

Stdlib only. The writers are satk's own and follow librw's stream semantics (MIT); the vendored
rwfury 0.6.1 (``vendor/rwfury``, MIT) supplies collision surface names and is the reference reader in
the tests. Modules:

* :mod:`satk.rw.chunk` - lossless RW chunk tree (bit-exact write-back, recomputed sizes);
* :mod:`satk.rw.codecs` - typed struct codecs (geometry, material, clump, skin, MatFX, ...);
* :mod:`satk.rw.dff` - :class:`~satk.rw.dff.DffDoc`: rename textures, RW version, night colours,
  normals, material colour;
* :mod:`satk.rw.col` - exact COL1-4 codec, JSON form, bounds/flags/face groups for new models;
* :mod:`satk.rw.img` - new VER2/VER1 archives (INU_Core BUGS.md regressions covered), archive diff;
* :mod:`satk.rw.roundtrip` - bit-for-bit measurements on a game installation.

Operations (``satk rw patch``, ``satk rw roundtrip``, ``satk col write|export``, ``satk img build|diff``)
live in :mod:`satk.rw.ops` and write only under ``<work>/out/rw/``.
"""
