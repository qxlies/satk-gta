"""Bind pack model entries to the models of one load profile by DFF content. Stdlib only.

The only binding rule (report 09, section 3.6): the SHA-256 of the DFF a model uses in this
profile equals ``sources[].sha256`` of the pack entry. Names are not trusted: a renamed or
modded DFF with other bytes is never bound, an identical DFF under another name is.

What is hashed: every DFF blob linked to an active model (``model_link.dff_id``) whose directory
size equals the ``bytes`` of some pack DFF source; the bytes are the whole IMG directory entry
(``blob.size`` at ``blob.abs_off``, read with :func:`satk.core.paths.open_ro`), exactly what
gta-scout hashed. A vanilla profile hashes ~15 000 DFFs in about a second.

The SID of a bound model is ``model:<id>``. When the winning definition comes from a non-vanilla
layer and overrides another one (SA-MP skins over IDs 300+), it is ``model:<name>`` instead, so a
note never lands on the vanilla model with the same ID (notes are not per profile).

Example::

    from satk.index.api import open_index
    from satk.describe.pack import load_pack, find_pack
    from satk.describe.bind import bind
    b = bind(load_pack(find_pack(None)), open_index("vanilla"))
    b.stats["bound"], b.descs[0].sid                       # (2131, 'model:...')
"""

from __future__ import annotations

import collections
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from satk.core.paths import jpath, open_ro
from satk.core.registry import report_progress

from .pack import ModelEntry, Pack

__all__ = ["DffUse", "Desc", "Binding", "profile_dff_uses", "hash_blobs", "bind"]

_PAGE = 500
_USES_SQL = (
    "SELECT ml.id AS model_id, m.name AS model, l.kind AS layer_kind, l.name AS layer, d.blob_id AS blob_id, "
    "b.name AS dff, b.size AS size, b.abs_off AS off, s.relpath AS relpath, "
    "(SELECT count(*) FROM model m2 WHERE m2.id = ml.id) AS defs "
    "FROM model_link ml "
    "JOIN model m ON m.id = ml.id AND m.active = 1 "
    "JOIN layer l ON l.id = m.layer_id "
    "JOIN dff d ON d.id = ml.dff_id "
    "JOIN blob b ON b.id = d.blob_id "
    "JOIN source s ON s.id = b.source_id "
    "WHERE ml.id > ? ORDER BY ml.id LIMIT " + str(_PAGE)
)


@dataclass(frozen=True)
class DffUse:
    """An active model of the profile and the DFF blob it loads."""

    model_id: int
    model: str
    layer_kind: str
    layer: str
    blob_id: int
    dff: str  #: blob name, ``infernus.dff``
    size: int
    off: int
    relpath: str  #: source file, ``models/gta3.img``
    defs: int  #: definitions of this ID in the profile (> 1 = overridden)

    @property
    def sid(self) -> str:
        """``model:<id>``, or ``model:<name>`` for a non-vanilla definition that overrides another."""
        if self.defs > 1 and self.layer_kind != "vanilla":
            return f"model:{self.model.lower()}"
        return f"model:{self.model_id}"


@dataclass(frozen=True)
class Desc:
    """One description bound to one model."""

    sid: str
    use: DffUse
    entry: ModelEntry

    @property
    def by_name(self) -> bool:
        """The publisher's selector names the same model (else bound by the DFF hash alone)."""
        return self.entry.name.lower() == self.use.model.lower()


@dataclass
class Binding:
    descs: list[Desc]
    stats: dict
    warn: list[str] = field(default_factory=list)


def profile_dff_uses(db) -> list[DffUse]:
    """All active models of an index (:class:`satk.index.api.IndexDB`) that have a DFF, by ID."""
    out: list[DffUse] = []
    last = -1
    while True:
        env = db.query(_USES_SQL, [last], limit=_PAGE)
        cols = env.get("cols") or []
        rows = env.get("rows") or []
        for r in rows:
            d = dict(zip(cols, r))
            out.append(DffUse(model_id=int(d["model_id"]), model=str(d["model"]), layer_kind=str(d["layer_kind"]),
                              layer=str(d["layer"]), blob_id=int(d["blob_id"]), dff=str(d["dff"]),
                              size=int(d["size"]), off=int(d["off"]), relpath=str(d["relpath"]),
                              defs=int(d["defs"])))
        if len(rows) < _PAGE:
            return out
        last = out[-1].model_id


def hash_blobs(root: Path, uses: list[DffUse], sizes: set[int] | None = None) -> tuple[dict[int, str], list[str]]:
    """SHA-256 of each distinct blob of ``uses`` (only sizes in ``sizes`` when given).

    Returns ``({blob_id: sha256_hex}, warnings)``; unreadable or short blobs are skipped with a
    ``BLOB_UNREADABLE`` warning (a stale index), never fatal. Files are read in offset order.
    """
    blobs: dict[int, DffUse] = {}
    for u in uses:
        if sizes is None or u.size in sizes:
            blobs.setdefault(u.blob_id, u)
    by_file: dict[str, list[DffUse]] = collections.defaultdict(list)
    for u in blobs.values():
        by_file[u.relpath].append(u)
    out: dict[int, str] = {}
    bad: list[str] = []
    done, total = 0, len(blobs)
    for rel in sorted(by_file):
        path = Path(root).joinpath(*rel.split("/"))
        items = sorted(by_file[rel], key=lambda u: u.off)
        try:
            f = open_ro(path)
        except OSError as e:
            bad.append(f"{rel} ({len(items)} DFFs; {type(e).__name__}: {jpath(path)})")
            done += len(items)
            continue
        with f:
            for u in items:
                f.seek(u.off)
                data = f.read(u.size)
                if len(data) != u.size:
                    bad.append(f"{rel}/{u.dff}")
                else:
                    out[u.blob_id] = hashlib.sha256(data).hexdigest()
                done += 1
                if done % 2000 == 0:
                    report_progress(done, total, "hashing DFFs")
    warn = []
    if bad:
        warn.append("BLOB_UNREADABLE: DFFs could not be read (stale index? satk index build): "
                    + ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else ""))
    return out, warn


def bind(pack: Pack, db) -> Binding:
    """Bind ``pack`` to the profile of index ``db`` (see the module docstring)."""
    root = Path(db.root)
    uses = profile_dff_uses(db)
    by_sha: dict[str, list[ModelEntry]] = collections.defaultdict(list)
    for m in pack.models:
        by_sha[m.dff_sha256].append(m)
    sizes = {m.dff_bytes for m in pack.models}
    shas, warn = hash_blobs(root, uses, sizes)

    descs: list[Desc] = []
    matched: set[str] = set()
    for u in uses:
        entries = by_sha.get(shas.get(u.blob_id, ""), [])
        if not entries:
            continue
        # several entries for one DFF: prefer the ones that name this model; keep each entry once
        named = [e for e in entries if e.name.lower() == u.model.lower()]
        seen: set[str] = set()
        for e in sorted(named or entries, key=lambda e: e.id):
            if e.id in seen:
                continue
            seen.add(e.id)
            matched.add(e.id)
            descs.append(Desc(u.sid, u, e))
    descs.sort(key=lambda d: (d.use.model_id, d.sid, d.entry.id))
    pack_ids = {m.id for m in pack.models}
    stats = {
        "models_in_profile": len(uses),
        "dff_hashed": len(shas),
        "entries_bound": len(matched),
        "entries_unmatched": len(pack_ids - matched),
        "bound": len(descs),
        "models": len({d.sid for d in descs}),
        "hash_only": sum(1 for d in descs if not d.by_name),
    }
    return Binding(descs=descs, stats=stats, warn=warn)
