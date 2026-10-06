"""Atlas v1: named regions of the shared vehicle textures with their measured vanilla support (stdlib only).

Regions are rectangles ``[u0, v0, u1, v1]`` in texture space with the DFF/D3D origin (top-left, v down).
Blender UVs are flipped (``v_blender = 1 - v``): :func:`to_blender` converts. ``support`` = share of the
role's vanilla UV area inside the rectangle; ``usage`` = share of all vanilla UV area on that texture inside it.

Example::

    from satk.kit import atlas
    r = atlas.region("generic.glass_core")      # {"texture": "vehiclegeneric256", "rect": [...], "support": 0.674}
    atlas.to_blender(r["rect"])                  # (u0, v0, u1, v1) with Blender's v axis
    atlas.fit((0.0, 0.0, 1.0, 1.0), r["rect"], 0.5)   # map a unit UV box into the region
"""

from __future__ import annotations

import difflib
import functools

from ..core import resources
from ..core.errors import SatkError

__all__ = ["regions", "region", "to_blender", "fit", "by_texture", "ATLASES"]

ATLASES = ("vehicle",)


@functools.lru_cache(maxsize=None)
def _doc(atlas: str = "vehicle") -> dict:
    return resources.read_json("kit", "atlas", f"{atlas}.json")


def regions(atlas: str = "vehicle") -> dict[str, dict]:
    return _doc(atlas)["regions"]


def region(name: str, atlas: str = "vehicle") -> dict:
    rs = regions(atlas)
    n = str(name).strip().lower()
    if n not in rs:
        raise SatkError("NOT_FOUND", f"no atlas region {name!r}", hint="satk kit kinds --atlas",
                        did_you_mean=difflib.get_close_matches(n, list(rs), n=3, cutoff=0.4))
    return dict(rs[n], name=n)


def by_texture(texture: str, atlas: str = "vehicle") -> list[str]:
    t = str(texture).lower()
    return [k for k, v in regions(atlas).items() if v["texture"] == t]


def to_blender(rect) -> tuple[float, float, float, float]:
    """``[u0, v0, u1, v1]`` (v down) -> Blender ``(u0, v0, u1, v1)`` (v up, v0 < v1)."""
    u0, v0, u1, v1 = (float(x) for x in rect)
    return u0, 1.0 - v1, u1, 1.0 - v0


def fit(src_box, rect, margin: float = 0.0) -> tuple[float, float, float, float]:
    """Scale/offset ``(su, sv, ou, ov)`` mapping UVs inside ``src_box`` (Blender space ``u0, v0, u1, v1``)
    into the Blender-space rectangle of ``rect`` (shrunk by ``margin`` of its size on each side), keeping the
    aspect ratio (the smaller scale wins, centred)."""
    bu0, bv0, bu1, bv1 = to_blender(rect)
    mw, mh = (bu1 - bu0) * margin, (bv1 - bv0) * margin
    bu0, bu1, bv0, bv1 = bu0 + mw, bu1 - mw, bv0 + mh, bv1 - mh
    su0, sv0, su1, sv1 = (float(x) for x in src_box)
    w, h = max(su1 - su0, 1e-9), max(sv1 - sv0, 1e-9)
    s = min((bu1 - bu0) / w, (bv1 - bv0) / h)
    ou = bu0 + ((bu1 - bu0) - w * s) / 2 - su0 * s
    ov = bv0 + ((bv1 - bv0) - h * s) / 2 - sv0 * s
    return s, s, ou, ov
