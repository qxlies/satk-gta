"""Step 5 of ``index build``: links (SPEC §4.3.2). Owner: WP-03.

* :func:`txd_chain` - own TXD -> ``txdp`` parents -> ``vehicle`` (for ``cars``);
* :func:`resolve_textures` - ``model_tex``: every material texture name of the model's DFF
  resolved along the chain (``own``/``txdp``/``vehicle``/``missing``);
* :func:`lod_links` - ``xxx_streamN`` LOD indexes into the inst list of the text IPL ``xxx``;
  ``is_lod`` = referenced by someone (NOT ``lod == -1``, a DragonFF bug);
* :func:`inst_aabb` - world AABB of a placement from the COL bbox (else the DFF bounding sphere,
  else the point), rotated by the CONJUGATE of the IPL quaternion (V9).

Stdlib only.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from .api import rotate, world_aabb, world_quat

__all__ = ["txd_chain", "resolve_textures", "lod_links", "inst_aabb"]


def txd_chain(own: str | None, sec: str, active_txd: Callable[[str], object | None],
              parent_of: Callable[[object], tuple[str | None, str | None]]) -> list[tuple[object, str]]:
    """``[(txd, via)]`` for a model: own TXD, its parents, then ``vehicle`` for ``cars``.

    ``active_txd(name)`` returns the active TXD record or ``None``; ``parent_of(txd)`` returns
    ``(parent name, via)``. Cycles stop the walk.
    """
    out: list[tuple[object, str]] = []
    seen: set[int] = set()
    t = active_txd(own) if own else None
    how = "own"
    while t is not None and id(t) not in seen:
        seen.add(id(t))
        out.append((t, how))
        pname, via = parent_of(t)
        how = via or "txdp"
        t = active_txd(pname) if pname else None
    if sec == "cars":
        v = active_txd("vehicle")
        if v is not None and id(v) not in seen:
            out.append((v, "vehicle"))
    return out


def resolve_textures(mat_textures: Iterable[str], chain: Sequence[tuple[object, str]],
                     tex_lookup: Callable[[object, str], int | None]) -> list[tuple[str, int | None, str, int]]:
    """``[(texture lower, texture_id|None, via, uses)]`` sorted by name.

    ``mat_textures`` = one entry per material (names compared case-insensitively);
    ``tex_lookup(txd, name)`` -> texture id inside that TXD or ``None``.
    """
    uses: dict[str, int] = {}
    for n in mat_textures:
        if n:
            k = n.lower()
            uses[k] = uses.get(k, 0) + 1
    out = []
    for name in sorted(uses):
        tid, via = None, "missing"
        for t, how in chain:
            tid = tex_lookup(t, name)
            if tid is not None:
                via = how
                break
        out.append((name, tid, via, uses[name]))
    return out


def lod_links(ipls: dict[int, tuple[str, int | None]], insts: Sequence[tuple[int, int, int, int]]
              ) -> tuple[dict[int, int], set[int], int, int]:
    """Resolve LOD references.

    Args:
        ipls: ``{ipl_id: (kind 'text'|'binary', parent text ipl_id or None)}``.
        insts: ``[(inst_id, ipl_id, idx, lod_idx)]``.

    Returns:
        ``({inst_id: lod_inst_id}, {inst ids that are LODs}, links, unresolved)``.
    """
    by_key = {(ipl, idx): iid for iid, ipl, idx, _l in insts}
    lod: dict[int, int] = {}
    unresolved = links = 0
    for iid, ipl, _idx, lod_idx in insts:
        if lod_idx < 0:
            continue
        links += 1
        kind, parent = ipls[ipl]
        target_ipl = ipl if kind == "text" else parent
        t = by_key.get((target_ipl, lod_idx)) if target_ipl is not None else None
        if t is None:
            unresolved += 1
        else:
            lod[iid] = t
    return lod, set(lod.values()), links, unresolved


def inst_aabb(pos: Sequence[float], q_ipl: Sequence[float], col_box: Sequence[float] | None,
              dff_sphere: Sequence[float] | None) -> tuple[tuple[float, float, float, float, float, float], str]:
    """``(aabb (minx,miny,minz,maxx,maxy,maxz), bbox_src)`` for one placement."""
    if col_box is not None:
        return world_aabb(col_box[:3], col_box[3:6], pos, q_ipl), "col"
    if dff_sphere is not None and dff_sphere[3] > 0:
        cx, cy, cz = rotate(world_quat(q_ipl), dff_sphere[:3])
        r = dff_sphere[3]
        x, y, z = pos[0] + cx, pos[1] + cy, pos[2] + cz
        return (x - r, y - r, z - r, x + r, y + r, z + r), "dff"
    x, y, z = pos
    return (x, y, z, x, y, z), "point"
