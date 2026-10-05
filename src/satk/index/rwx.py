"""A RenderWare helper the index needs beyond ``satk.formats.dff`` (owner WP-03).

:func:`empty_texture_names` - textured materials whose texture name is empty (``scan_dff`` reports
them as ``texture=None``); report 14's SQL metric counted them as unresolved references
(``texref.unresolved_all`` = 467 in vanilla).

Stdlib only; malformed data never raises (best effort, ``iter_children(strict=False)``).
"""

from __future__ import annotations

import struct

from ..formats.rw import iter_children

__all__ = ["empty_texture_names"]

_STRING, _EXT, _TEXTURE, _MATERIAL, _MATLIST = 0x02, 0x03, 0x06, 0x07, 0x08
_GEOMETRY, _CLUMP, _GEOMLIST, _UVANIMDICT = 0x0F, 0x10, 0x1A, 0x2B


def _clumps(buf) -> list[tuple[int, int]]:
    """``(data_start, end)`` of the top-level clumps (``UVAnimDict`` chunks before them are skipped)."""
    out = []
    n = len(buf)
    off = 0
    while off + 12 <= n:
        t, s, _v = struct.unpack_from("<III", buf, off)
        if t not in (_CLUMP, _UVANIMDICT) or off + 12 + s > n:
            break
        if t == _CLUMP:
            out.append((off + 12, off + 12 + s))
        off += 12 + s
    return out


def empty_texture_names(buf) -> int:
    """Number of ``Texture`` chunks whose name string is empty (6 in vanilla, 5 of them in model DFFs)."""
    n = 0
    stack = _clumps(buf)
    while stack:
        a, b = stack.pop()
        for k in iter_children(buf, a, b, strict=False):
            if k.type == _TEXTURE:
                for x in iter_children(buf, k.data_off, k.end, strict=False):
                    if x.type == _STRING:
                        if not bytes(buf[x.data_off:x.end]).split(b"\0", 1)[0]:
                            n += 1
                        break
            elif k.type in (_GEOMLIST, _GEOMETRY, _MATLIST, _MATERIAL):
                stack.append((k.data_off, k.end))
    return n
