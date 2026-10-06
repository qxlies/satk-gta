"""Surface materials for generated collisions (``satk.colgen.surface``). Stdlib only.

Three package data files (``data/colgen``):

* ``surfaces.json`` - the 179 surface ids of ``data/surfinfo.dat`` with the names ``satk col write`` uses;
* ``tex_surface.json`` - texture name -> ``[surface id, share %, votes]``, derived from the vanilla game by
  ``satk col derive`` (collision faces matched to the nearest render triangle); ``txd`` holds the
  ``(TXD, texture)`` pairs whose majority differs from the texture's own (the same grass texture is lush
  in one area and dry in another); plus the lighting fit;
* ``keywords.json`` - ordered keyword fallbacks for textures the table does not know
  (``grass`` -> ``GRASS_SHORT_LUSH``, ``sand`` -> ``SAND_MEDIUM``, ...), first match wins.

:meth:`SurfaceTable.lookup` answers ``(surface id, how)`` with ``how`` = ``txd`` | ``table`` | ``keyword:<kw>`` |
``default``. :meth:`SurfaceTable.light` turns prelit day/night brightness into the COL lighting byte.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..core import resources
from ..core.errors import SatkError

__all__ = ["SurfaceTable", "parse_surface", "MAX_SURFACE"]

#: Highest valid surface id (``RAIL_TRACK``).
MAX_SURFACE = 178
_TOKEN = re.compile(r"[a-z]+")


class SurfaceTable:
    """Names, the derived texture table, keyword fallbacks and the lighting fit."""

    def __init__(self, names: list[str], textures: dict[str, list], keywords: list[tuple[str, int]],
                 lighting: dict, default: int = 0, txd: dict[str, dict[str, list]] | None = None):
        self.names = names
        self.by_name = {n: i for i, n in enumerate(names)}
        self.textures = textures
        self.txd = txd or {}
        self.keywords = keywords
        self.lighting = lighting
        self.default = default

    # ------------------------------------------------------------------ construction
    @classmethod
    def load(cls) -> "SurfaceTable":
        return _load()

    def with_textures(self, textures: dict[str, list], txd: dict[str, dict[str, list]] | None = None) -> "SurfaceTable":
        return SurfaceTable(self.names, textures, self.keywords, self.lighting, self.default, txd)

    # ------------------------------------------------------------------ names
    def name(self, sid: int) -> str:
        return self.names[sid] if 0 <= sid < len(self.names) else f"#{sid}"

    def id_of(self, text: str) -> int:
        """Surface id of a name (any case, ``-``/space = ``_``) or a number; ``BAD_PARAMS`` otherwise."""
        sid = parse_surface(text, self.by_name)
        if sid is None:
            import difflib

            key = _norm(text)
            raise SatkError("BAD_PARAMS", f"unknown surface {text!r}",
                            did_you_mean=difflib.get_close_matches(key, self.names, n=3, cutoff=0.6),
                            hint="satk col surface --list")
        return sid

    # ------------------------------------------------------------------ lookup
    def lookup(self, texture: str | None, txd: str | None = None) -> tuple[int, str]:
        """``(surface id, how)`` for a texture name, optionally in the context of its TXD (module doc)."""
        if texture:
            t = texture.lower()
            if txd:
                row = (self.txd.get(txd.lower()) or {}).get(t)
                if row is not None:
                    return int(row[0]), "txd"
            row = self.textures.get(t)
            if row is not None:
                return int(row[0]), "table"
            for kw, sid in self.keywords:
                if kw in t:
                    return sid, f"keyword:{kw}"
        return self.default, "default"

    def light(self, day: float, night: float) -> int:
        """COL face lighting byte from prelit brightness (``-1`` = unknown: full bright)."""
        if day < 0:
            return 0xFF
        a, b = self.lighting.get("day", [0.0, 15.0])
        d = int(min(15, max(0, round(a * day + b))))
        if night >= 0:
            a, b = self.lighting.get("night", [0.0, 3.0])
            n = int(min(15, max(0, round(a * night + b))))
        else:
            n = int(min(15, max(0, round(self.lighting.get("night_default", 3)))))
        return d | (n << 4)


def _norm(text: str) -> str:
    return text.strip().upper().replace("-", "_").replace(" ", "_")


def parse_surface(text: str | int, by_name: dict[str, int]) -> int | None:
    if isinstance(text, int) and not isinstance(text, bool):
        return text if 0 <= text <= MAX_SURFACE else None
    s = str(text).strip()
    if s.isdigit():
        v = int(s)
        return v if 0 <= v <= MAX_SURFACE else None
    return by_name.get(_norm(s))


@lru_cache(maxsize=1)
def _load() -> SurfaceTable:
    names = resources.read_json("colgen", "surfaces.json")["names"]
    by_name = {n: i for i, n in enumerate(names)}
    tex: dict = {}
    txd: dict = {}
    lighting: dict = {}
    if resources.exists("colgen", "tex_surface.json"):
        doc = resources.read_json("colgen", "tex_surface.json")
        tex = doc.get("textures") or {}
        txd = doc.get("txd") or {}
        lighting = doc.get("lighting") or {}
    kws: list[tuple[str, int]] = []
    for kw, sname in resources.read_json("colgen", "keywords.json")["keywords"]:
        sid = parse_surface(sname, by_name)
        if sid is None:
            raise SatkError("INTERNAL", f"data/colgen/keywords.json: unknown surface {sname!r} for {kw!r}")
        kws.append((kw.lower(), sid))
    return SurfaceTable(names, tex, kws, lighting, 0, txd)
