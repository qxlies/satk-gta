"""The texture dedup census: how much texel data the name-preserving dedup key would share, per TXD and globally.

The residency layer shares one D3D texture between textures that agree on the **name-preserving key**
``(texel hash, name, format, width, height, mip count)``: the name stays part of the key, so MTA's name-based world
shader matching keeps working. The census computes, over an index profile (``vanilla``, ``installed``, ...), for
every TXD the winning (active) blobs carry:

* the texture count, bytes, distinct keys, copies inside the TXD and how much of it other TXDs also carry;
* globally: distinct keys and unique bytes under three keys (texel hash; hash + name; the full key), the bytes
  saved, a histogram of copy counts and the groups with the most saved bytes.

Bytes are counted twice: ``mip0`` (the index's ``data_size``, the base level only) and ``chain`` (all levels,
computed from format, size and level count: exact for DXT and 32-bit formats). The index hashes only the base
level (plus a palette); ``exact=True`` re-reads the TXD files and hashes the whole mip chain, so textures with equal
base levels but different lower levels no longer count as equal.

The census reads the index and, with ``exact``, the game files (read-only). Its files are game-derived numbers
and names: they go to ``work/out/pack/census/<profile>/`` and never into git. Stdlib only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro

__all__ = ["Tex", "level_bytes", "chain_bytes", "load_textures", "run_census", "KEYS", "MIB"]

MIB = 1024 * 1024
KEYS = ("texel", "texel_name", "full")
_BLOCK = {"DXT1": 8, "DXT3": 16, "DXT5": 16}
_BPP = {"A8R8G8B8": 4.0, "X8R8G8B8": 4.0, "R5G6B5": 2.0, "A1R5G5B5": 2.0, "A4R4G4B4": 2.0, "A8L8": 2.0, "L8": 1.0,
        "PAL8": 1.0, "PAL4": 0.5}


@dataclass(frozen=True, slots=True)
class Tex:
    txd: str
    ns: str
    blob: int
    name: str
    fmt: str
    w: int
    h: int
    levels: int
    mip0: int
    chain: int
    hash: bytes
    platform: int
    data_off: int
    pal_off: int | None
    relpath: str

    @property
    def key_texel(self) -> bytes:
        return self.hash

    @property
    def key_name(self) -> tuple:
        return (self.hash, self.name)

    @property
    def key_full(self) -> tuple:
        return (self.hash, self.name, self.fmt, self.w, self.h, self.levels)


def level_bytes(fmt: str, w: int, h: int, level: int) -> int | None:
    """Bytes of one mip level of a D3D9 texture, or ``None`` for a format the census does not know."""
    lw, lh = max(1, w >> level), max(1, h >> level)
    blk = _BLOCK.get(fmt)
    if blk is not None:
        return max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4) * blk
    bpp = _BPP.get(fmt)
    if bpp is None:
        return None
    return max(1, math.ceil(lw * lh * bpp))


def chain_bytes(fmt: str, w: int, h: int, levels: int, mip0: int) -> int:
    """Bytes of all levels: the stored base level plus the formula for the lower ones (unknown format: base only)."""
    total = mip0
    for lv in range(1, max(1, levels)):
        b = level_bytes(fmt, w, h, lv)
        if b is None:
            return mip0
        total += b
    return total


_SQL = ("SELECT x.name, b.ns, b.id, lower(tx.name), tx.d3dfmt, tx.w, tx.h, tx.levels, tx.data_size, tx.hash, "
        "tx.platform, tx.data_off, tx.pal_off, s.relpath, tx.idx "
        "FROM texture tx JOIN txd x ON x.id = tx.txd_id JOIN blob b ON b.id = x.blob_id "
        "JOIN source s ON s.id = b.source_id WHERE b.active = 1 "
        "ORDER BY lower(x.name), b.id, tx.idx")


def load_textures(profile: str) -> tuple[list[Tex], dict, object]:
    """The textures of the active TXDs of a profile index, in a stable order, with the index meta and the handle."""
    import sqlite3

    from ..index.api import open_index

    db = open_index(profile)
    path = getattr(db, "path", None)
    if path is None or not Path(path).is_file():
        raise SatkError("INDEX_MISSING", f"profile {profile!r} has no index file", hint=f"satk index build --profile {profile}")
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    try:
        con.execute("PRAGMA query_only = 1")
        meta = dict(con.execute("SELECT key, value FROM meta"))
        rows = []
        for (txd, ns, blob, name, fmt, w, h, levels, size, hh, plat, doff, poff, rel, _idx) in con.execute(_SQL):
            rows.append(Tex(txd, ns, int(blob), name, fmt, int(w), int(h), int(levels), int(size),
                            chain_bytes(fmt, int(w), int(h), int(levels), int(size)), bytes(hh), int(plat), int(doff),
                            None if poff is None else int(poff), rel))
    finally:
        con.close()
    return rows, meta, db


def _exact_hashes(rows: list[Tex], db, warn: list[str]) -> list[bytes]:
    """Hash of the whole stored texel data (palette, every level) of each D3D9 texture; others keep the index hash."""
    q = db._q()
    by_file: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(rows):
        by_file[t.relpath].append(i)
    out = [t.hash for t in rows]
    skipped = failed = 0
    for rel, idxs in by_file.items():
        try:
            p = q.path_of(rel)
            fh = open_ro(p)
        except (OSError, SatkError):
            failed += len(idxs)
            continue
        with fh:
            for i in idxs:
                t = rows[i]
                if t.platform != 9:
                    skipped += 1
                    continue
                try:
                    h = hashlib.blake2b(digest_size=12)
                    if t.pal_off is not None:
                        fh.seek(t.pal_off)
                        h.update(fh.read(1024 if t.fmt == "PAL8" else 64))
                    fh.seek(t.data_off)
                    h.update(fh.read(t.mip0))
                    pos = t.data_off + t.mip0
                    for _lv in range(1, max(1, t.levels)):
                        fh.seek(pos)
                        raw = fh.read(4)
                        if len(raw) < 4:
                            raise ValueError("short")
                        (n,) = struct.unpack("<I", raw)
                        if n > 1 << 28:
                            raise ValueError("implausible level size")
                        h.update(raw)
                        h.update(fh.read(n))
                        pos += 4 + n
                    out[i] = h.digest()
                except (OSError, ValueError, struct.error):
                    failed += 1
    if skipped:
        warn.append(f"EXACT_SKIPPED: {skipped} texture(s) of a platform other than D3D9 keep the index hash")
    if failed:
        warn.append(f"EXACT_FAILED: {failed} texture(s) could not be re-read and keep the index hash")
    return out


def _exact_effect(rows: list[Tex], digests: list[bytes]) -> dict:
    """What hashing the whole mip chain changes: index full-key groups that split into several chain hashes."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, t in enumerate(rows):
        groups[t.key_full].append(i)
    split = lost0 = lostc = 0
    for idx in groups.values():
        if len(idx) < 2:
            continue
        by: dict[bytes, int] = Counter(digests[i] for i in idx)
        if len(by) < 2:
            continue
        t0 = rows[idx[0]]
        split += 1
        lost0 += (len(idx) - 1) * t0.mip0 - sum((m - 1) * t0.mip0 for m in by.values())
        lostc += (len(idx) - 1) * t0.chain - sum((m - 1) * t0.chain for m in by.values())
    return {"split_groups": split, "lost_mip0_bytes": lost0, "lost_chain_bytes": lostc}


def _hist(counts: Counter) -> dict:
    bins = {"1": 0, "2": 0, "3-5": 0, "6-10": 0, "11+": 0}
    for n in counts.values():
        k = "1" if n == 1 else "2" if n == 2 else "3-5" if n <= 5 else "6-10" if n <= 10 else "11+"
        bins[k] += 1
    return bins


def run_census(profile: str = "vanilla", *, exact: bool = False) -> dict:
    """Compute the census. Returns ``{"summary", "txds", "groups", "warn"}`` (plain data, deterministic order)."""
    rows, meta, db = load_textures(profile)
    warn: list[str] = []
    if not rows:
        raise SatkError("NOT_FOUND", f"profile {profile!r} has no textures in its active TXDs",
                        hint=f"satk index build --profile {profile}")
    exact_vs_index = None
    if exact:
        digests = _exact_hashes(rows, db, warn)
        exact_vs_index = _exact_effect(rows, digests)
        rows = [Tex(r.txd, r.ns, r.blob, r.name, r.fmt, r.w, r.h, r.levels, r.mip0, r.chain, d, r.platform,
                    r.data_off, r.pal_off, r.relpath) for r, d in zip(rows, digests)]
    n = len(rows)
    total0 = sum(t.mip0 for t in rows)
    totalc = sum(t.chain for t in rows)
    mismatch = sum(1 for t in rows if (level_bytes(t.fmt, t.w, t.h, 0) or t.mip0) != t.mip0)
    # -- keys
    by_key: dict[str, dict] = {}
    for kname, keyf in (("texel", lambda t: t.key_texel), ("texel_name", lambda t: t.key_name),
                        ("full", lambda t: t.key_full)):
        first: dict = {}
        for t in rows:
            first.setdefault(keyf(t), t)
        u0 = sum(t.mip0 for t in first.values())
        uc = sum(t.chain for t in first.values())
        by_key[kname] = {"distinct": len(first), "unique_mip0_bytes": u0, "unique_chain_bytes": uc,
                         "saved_mip0_bytes": total0 - u0, "saved_chain_bytes": totalc - uc,
                         "saved_pct_mip0": round(100.0 * (total0 - u0) / total0, 1) if total0 else 0.0,
                         "saved_pct_chain": round(100.0 * (totalc - uc) / totalc, 1) if totalc else 0.0}
    # -- groups under the full key
    groups: dict[tuple, list[Tex]] = defaultdict(list)
    for t in rows:
        groups[t.key_full].append(t)
    key_txds = {k: len({t.blob for t in v}) for k, v in groups.items()}
    # -- what the extra fields of the full key cost against (hash, name): groups that split, by the field that differs
    by_name: dict[tuple, dict[tuple, list[Tex]]] = defaultdict(lambda: defaultdict(list))
    for t in rows:
        by_name[t.key_name][(t.fmt, t.w, t.h, t.levels)].append(t)
    splits: dict[str, dict] = {}
    for variants in by_name.values():
        if len(variants) < 2:
            continue
        why = [label for label, pick in (("format", lambda v: v[0]), ("size", lambda v: (v[1], v[2])),
                                         ("levels", lambda v: v[3])) if len({pick(v) for v in variants}) > 1]
        allt = [t for ts in variants.values() for t in ts]
        sp = splits.setdefault("+".join(why), {"groups": 0, "lost_mip0_bytes": 0, "lost_chain_bytes": 0})
        sp["groups"] += 1
        sp["lost_mip0_bytes"] += (len(allt) - 1) * allt[0].mip0 - sum((len(ts) - 1) * ts[0].mip0 for ts in variants.values())
        sp["lost_chain_bytes"] += (len(allt) - 1) * allt[0].chain - sum((len(ts) - 1) * ts[0].chain for ts in variants.values())
    # -- per TXD
    txd_order: list[tuple[int, str, str]] = []
    per: dict[int, list[Tex]] = defaultdict(list)
    for t in rows:
        if t.blob not in per:
            txd_order.append((t.blob, t.txd, t.ns))
        per[t.blob].append(t)
    names = Counter(name for _b, name, _ns in txd_order)
    txds = []
    within_unique_chain = 0
    for blob, name, ns in txd_order:
        ts = per[blob]
        cnt = Counter(t.key_full for t in ts)
        first = {}
        for t in ts:
            first.setdefault(t.key_full, t)
        within_unique_chain += sum(t.chain for t in first.values())
        shared = [k for k in first if key_txds[k] > 1]
        shared_b = sum(first[k].chain for k in shared)
        excl_b = sum(first[k].chain for k in first if key_txds[k] == 1)
        bytes_c = sum(t.chain for t in ts)
        dup_n = len(ts) - len(first)
        dup_b = bytes_c - sum(t.chain for t in first.values())
        txds.append({"txd": name if names[name] == 1 else f"{name}#{blob}", "ns": ns, "textures": len(ts),
                     "mip0_bytes": sum(t.mip0 for t in ts), "chain_bytes": bytes_c, "distinct": len(first),
                     "dup_in_txd": dup_n, "dup_in_txd_bytes": dup_b, "shared_keys": len(shared),
                     "shared_bytes": shared_b, "exclusive_bytes": excl_b,
                     "shared_pct": round(100.0 * shared_b / (bytes_c - dup_b), 1) if bytes_c - dup_b else 0.0})
    glist = []
    for k, ts in groups.items():
        if len(ts) < 2:
            continue
        t0 = ts[0]
        owners = sorted({(t.txd, t.blob) for t in ts})
        glist.append({"name": t0.name, "fmt": t0.fmt, "w": t0.w, "h": t0.h, "levels": t0.levels,
                      "hash": k[0].hex(), "copies": len(ts), "txds": len(owners), "mip0_bytes": t0.mip0,
                      "chain_bytes": t0.chain, "saved_chain_bytes": (len(ts) - 1) * t0.chain,
                      "saved_mip0_bytes": (len(ts) - 1) * t0.mip0, "in": [o[0] for o in owners]})
    glist.sort(key=lambda g: (-g["saved_chain_bytes"], -g["copies"], g["name"], g["hash"]))
    for i, g in enumerate(glist, 1):
        g["rank"] = i
    fmts: dict[str, dict] = {}
    seen_full: set = set()
    for t in rows:
        f = fmts.setdefault(t.fmt, {"textures": 0, "chain_bytes": 0, "unique_chain_bytes": 0})
        f["textures"] += 1
        f["chain_bytes"] += t.chain
        if t.key_full not in seen_full:
            seen_full.add(t.key_full)
            f["unique_chain_bytes"] += t.chain
    summary = {
        "profile": profile, "exact_mips": bool(exact), "index_content_hash": meta.get("content_hash", ""),
        "txds": len(txd_order), "textures": n, "mip0_bytes": total0, "chain_bytes": totalc,
        "mip0_mib": round(total0 / MIB, 1), "chain_mib": round(totalc / MIB, 1),
        "keys": by_key, "name_key_splits": dict(sorted(splits.items())),
        "full_key_within_txd_only": {"unique_chain_bytes": within_unique_chain,
                                     "saved_chain_bytes": totalc - within_unique_chain,
                                     "saved_pct_chain": round(100.0 * (totalc - within_unique_chain) / totalc, 1)},
        "copies_histogram": _hist(Counter({k: len(v) for k, v in groups.items()})),
        "duplicated_groups": len(glist), "by_format": dict(sorted(fmts.items())),
        "chain_formula_mismatch": mismatch,
    }
    if exact_vs_index is not None:
        summary["exact_vs_index"] = exact_vs_index
    return {"summary": summary, "txds": txds, "groups": glist, "warn": warn}


# --------------------------------------------------------------------------- files


def census_files(result: dict) -> dict[str, bytes]:
    """The census files as ``{name: bytes}``: census.json (summary), per_txd.csv, groups.csv. Deterministic."""
    out: dict[str, bytes] = {}
    out["census.json"] = (json.dumps({"summary": result["summary"]}, indent=1, sort_keys=True) + "\n").encode("utf-8")
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    cols = ["txd", "ns", "textures", "mip0_bytes", "chain_bytes", "distinct", "dup_in_txd", "dup_in_txd_bytes",
            "shared_keys", "shared_bytes", "exclusive_bytes", "shared_pct"]
    w.writerow(cols)
    for r in result["txds"]:
        w.writerow([r[c] for c in cols])
    out["per_txd.csv"] = buf.getvalue().encode("utf-8")
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    gcols = ["rank", "name", "fmt", "w", "h", "levels", "copies", "txds", "mip0_bytes", "chain_bytes",
             "saved_mip0_bytes", "saved_chain_bytes", "hash"]
    w.writerow(gcols + ["in"])
    for g in result["groups"]:
        w.writerow([g[c] for c in gcols] + [";".join(g["in"])])
    out["groups.csv"] = buf.getvalue().encode("utf-8")
    return out


def write_census(result: dict, out_dir: Path) -> dict[str, str]:
    """Write the census files atomically (unchanged files are left alone); returns ``{name: path}``."""
    from ..core.paths import atomic_write

    paths: dict[str, str] = {}
    for name, data in census_files(result).items():
        p = out_dir / name
        try:
            same = p.is_file() and p.read_bytes() == data
        except OSError:
            same = False
        if not same:
            atomic_write(p, data)
        paths[name] = jpath(p)
    return paths
