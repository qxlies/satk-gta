"""satk.fx2d -- 2dEffect (2DFX) entries of DFF models as editable JSON.

Stdlib only. The 2dEffect geometry plugin (``0x253F2F8``) holds lights (coronas, light shadows), particle
emitters, ped attractors, entry/exit markers, road signs, slot-machine trigger points, cover points and
escalators. Modules:

* :mod:`~satk.fx2d.schema` - one entry <-> one JSON object, exact both ways (``keep`` holds ignored bytes);
* :mod:`~satk.fx2d.doc` - a DFF <-> the ``satk.fx2d/1`` document (all geometries), writing on the lossless
  chunk tree of :mod:`satk.rw` (untouched chunks stay byte-identical);
* :mod:`~satk.fx2d.check` - sanity checks: types the game reads, particle names against ``effects.fxp``,
  corona/shadow textures against ``particle.txd``, ranges;
* :mod:`~satk.fx2d.ops` - ``satk fx2d dump|apply|copy|check|roundtrip`` (output under ``<work>/out/fx2d/``).

Example::

    from satk.fx2d.doc import read_doc, normalize, apply_doc
    doc = read_doc(dff_bytes)
    doc["geometries"][0]["effects"][0]["color"] = [255, 0, 0, 200]
    new_bytes, report = apply_doc(dff_bytes, normalize(doc))
"""
