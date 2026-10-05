"""Shared blob selection and texture SID policy for index search and typed lookups.

``tex:txd/name[@layer]`` can only identify the first texture of that name in the
TXD selected by a blob lookup. Other namespaces, further same-layer variants and
duplicate texture names use the existing content SID (``pix:``), as do names
that the SID grammar cannot preserve.
"""

from __future__ import annotations

import sqlite3

from ..core.errors import SatkError
from ..core.ids import Sid


def blob_id(conn: sqlite3.Connection, stem: str, ext: str, layer: str | None = None) -> int | None:
    """The blob that ``<ext>:<stem>[@layer]`` resolves to, with main/loose preferred."""
    sql = "SELECT b.id FROM blob b "
    if layer is not None:
        sql += "JOIN source s ON s.id=b.source_id JOIN layer l ON l.id=s.layer_id "
    sql += "WHERE b.ns IN ('main','loose','player','anim','cuts') AND b.stem=? AND b.ext=? "
    args = [stem, ext]
    if layer is None:
        sql += "AND b.active=1 ORDER BY (b.ns NOT IN ('main','loose')), b.id "
    else:
        sql += "AND l.name=? ORDER BY b.active, (b.ns NOT IN ('main','loose')), b.id "
        args.append(layer)
    row = conn.execute(sql + "LIMIT 1", args).fetchone()
    return row[0] if row else None


class TextureSids:
    """Format SIDs using the lookup rules, caching repeated TXD queries within a batch."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._choices: dict[tuple[str, str | None], int | None] = {}
        self._first: dict[int, dict[str, int]] = {}

    def _choice(self, txd: str, layer: str | None) -> int | None:
        key = (txd, layer)
        if key not in self._choices:
            self._choices[key] = blob_id(self.conn, txd, "txd", layer)
        return self._choices[key]

    def sid(self, tid: int, name: str, digest: bytes, bid: int, txd: str, layer: str) -> str:
        name, txd = name.lower(), txd.lower()
        pix = "pix:" + bytes(digest).hex()
        if self._choice(txd, None) == bid:
            suffix = ""
        elif self._choice(txd, layer) == bid:
            suffix = f"@{layer}"
        else:
            return pix
        if bid not in self._first:
            first: dict[str, int] = {}
            for xid, xname in self.conn.execute(
                "SELECT x.id,x.name FROM texture x JOIN txd t ON t.id=x.txd_id "
                "WHERE t.blob_id=? ORDER BY x.idx", (bid,),
            ):
                first.setdefault(xname.lower(), xid)
            self._first[bid] = first
        if self._first[bid].get(name) != tid:
            return pix
        key = f"{txd}/{name}"
        try:
            sid = Sid("tex", key, layer if suffix else None)
        except SatkError:  # e.g. vanilla's tw@t_wall1 cannot be expressed as a tex key
            return pix
        if sid.key != key:  # normalization must not strip significant texture-name whitespace
            return pix
        return str(sid)
