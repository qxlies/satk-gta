"""Operations of ``satk.pack``, all CLI only (``mcp=False``; MCP reaches them through ``satk_op``):

* ``pack.build``   -> ``satk pack build <folder|files...>``: a ``.saepak`` content container (IMG VER2 compatible,
  content addressed, per-chunk SHA-256, mount metadata) from DFF/TXD/COL/IFP/IPL/DAT files you own;
* ``pack.verify``  -> ``satk pack verify <pack|manifest|dac>``: structure, IMG directory, every hash; CHECK_FAILED on errors;
* ``pack.inspect`` -> ``satk pack inspect <pack|manifest|dac>``: header, mount metadata, names, entries, chunk plan;
* ``pack.dac``     -> ``satk pack dac <dff|folder|dff:name|model:id>``: derived-asset cache files (normals, night
  colours, tangents, lights) in ``work/cache/dac/``;
* ``pack.census``  -> ``satk pack census [--profile vanilla]``: name-preserving texture dedup statistics per TXD and
  globally, written to ``work/out/pack/census/<profile>/``.

Module-level imports stay stdlib/satk only (fast imports).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table, with_warn
from ..core.errors import SatkError
from ..core.paths import jpath
from ..core.registry import op, report_progress

Mount = Literal["overlay", "addon", "cache"]
Fast = Literal["none", "xxh3"]
NormalsMode = Literal["missing", "all", "none"]
Show = Literal["names", "entries", "chunks"]


def _page(rows: list[list], limit: int, cursor: str | None) -> tuple[list[list], str | None]:
    off = 0
    if cursor:
        m = re.fullmatch(r"o(\d+)", str(cursor))
        if not m:
            raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
        off = int(m.group(1))
    nxt = f"o{off + limit}" if off + limit < len(rows) else None
    return rows[off:off + limit], nxt


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_\-]+", "_", s.lower()).strip("_")
    return s or "pack"


def _find_file(target: str) -> Path:
    s = str(target).strip().strip('"')
    if not s:
        raise SatkError("BAD_PARAMS", "no target given", hint="satk pack inspect mymod.saepak")
    p = Path(s)
    cands = [p] if p.is_absolute() else [Path.cwd() / p]
    from ..core.paths import cfg

    out = Path(os.path.abspath(cfg().paths.work)) / "out" / "pack"
    cands += [out / s, out / (s + ".saepak")]
    for c in cands:
        if c.is_file():
            return Path(os.path.abspath(c))
    raise SatkError("NOT_FOUND", f"no such file: {s}", hint="a .saepak, a .saepak.manifest or a .dac file "
                                                           "(absolute, relative to the current folder, or a name in "
                                                           "work/out/pack)")


def _is_dac(p: Path) -> bool:
    from .layout import DAC_MAGIC

    with open(p, "rb") as f:
        return f.read(8) == DAC_MAGIC


# --------------------------------------------------------------------------- build


@op("pack.build", mcp=False, long_running=True, group="asset",
    summary="Build a .saepak content container from a folder or DFF/TXD/COL/IFP/IPL/DAT files you own: IMG VER2 "
            "compatible, content addressed (SHA-256, identical files stored once), per-chunk hashes, mount "
            "metadata, manifest sidecar. Writes work/out/pack/ by default.",
    summary_ru="Собрать контейнер .saepak из папки или файлов DFF/TXD/COL/IFP/IPL/DAT: совместим с IMG VER2, адресация "
               "по содержимому (SHA-256, одинаковые файлы хранятся один раз), хеши чанков, метаданные монтирования.",
    examples=("satk pack build mymod --out mymod.saepak", "satk pack build mymod --namespace mymod --priority 10",
              "satk pack build car.dff car.txd --name car --chunk-kib 16"))
def pack_build(inputs: list[str], out: str | None = None, name: str | None = None, namespace: str = "",
               title: str = "", mount_name: str = "", mount: Mount = "overlay", priority: int = 0,
               chunk_kib: int = 64, fast: Fast = "none", flat: bool = False, dedup_ok: bool = True,
               local_only: bool = False, manifest: bool = True, force: bool = False) -> dict:
    """Build a pack.

    Args:
        inputs: folders (recursive) and/or .dff .txd .col .ifp .ipl .dat files; logical names are the lower-case
            relative paths inside a folder, or the file name.
        out: the pack file (default work/out/pack/<name>.saepak). An existing explicit target needs --force.
        name: pack name for the default file name (default: the first input's name).
        namespace: namespace of the logical names (the registry derives asset ids from namespace + name).
        title: a human title stored in the manifest.
        mount_name: suggested IMG file name when the pack is mounted (default empty).
        mount: overlay (shadows same-named entries of lower layers) | addon (new names only) | cache.
        priority: mount priority, larger wins.
        chunk_kib: hash chunk size in KiB, a power of two from 2 to 65536 (default 64).
        fast: none (fast-hash fields stay zero) | xxh3 (fill XXH3-128; needs the xxhash package).
        flat: use only the file name as the logical name, ignoring sub-folders.
        dedup_ok: allow the residency layer to share identical textures of this pack's TXDs.
        local_only: mark the pack as derived or private content that must never be redistributed.
        manifest: also write <pack>.manifest, the manifest-only file for servers and fetchers.
        force: replace an existing explicit --out file.
    """
    from . import layout as L
    from .saepak import build_pack
    from .sources import collect

    srcs, warn = collect([str(i) for i in inputs], flat=flat)
    first = Path(str(inputs[0]).strip().strip('"'))
    pname = _slug(name or (first.stem if first.suffix.lower().lstrip(".") in L.STREAM_EXTS else first.name))
    explicit = out is not None
    if explicit:
        target = Path(out)
        if not target.is_absolute():
            target = Path.cwd() / target
        if target.is_dir() or str(out).endswith(("/", "\\")):
            target = target / f"{pname}.saepak"
        target = Path(os.path.abspath(target))
        if target.exists() and not force:
            raise SatkError("EXISTS", f"{jpath(target)} exists", hint="pass --force to replace it, or pick another --out")
    else:
        from ..core.paths import work

        target = work("out", "pack", f"{pname}.saepak")
    report = build_pack(srcs, target, namespace=namespace, title=title, mount_name=mount_name, mount_mode=mount,
                        priority=priority, chunk_size=int(chunk_kib) * 1024, fast=fast, dedup_ok=dedup_ok,
                        local_only=local_only, sidecar=manifest, progress=report_progress)
    env = obj(None, out=jpath(report["out"]), manifest=jpath(report["sidecar"]) if report["sidecar"] else None,
              pack_id=report["pack_id"], names=report["names"], entries=report["entries"],
              dir_slots=report["dir_slots"], chunks=report["chunks"], chunk_kib=report["chunk_size"] // 1024,
              bytes=report["bytes"], payload_bytes=report["payload_bytes"], manifest_bytes=report["manifest_bytes"],
              named_bytes=report["named_bytes"], content_bytes=report["content_bytes"],
              dedup_saved_bytes=report["dedup_saved"], derived_stream_names=report["derived_stream_names"],
              mount=mount, priority=priority, namespace=namespace)
    return with_warn(env, *warn, *report["warn"])


# --------------------------------------------------------------------------- verify


@op("pack.verify", mcp=False, long_running=True, group="asset",
    summary="Verify a .saepak (or its .manifest, or a .dac): footer and manifest hashes, table bounds, IMG VER2 "
            "directory against the manifest, alignment, overlaps, pack id, and with --deep SHA-256 of every entry "
            "and chunk. CHECK_FAILED lists the errors.",
    summary_ru="Проверить .saepak (или .manifest, .dac): хеши футера и манифеста, границы таблиц, каталог IMG против "
               "манифеста, выравнивание, пересечения, pack id, с --deep SHA-256 каждой записи и чанка.",
    examples=("satk pack verify mymod.saepak", "satk pack verify mymod.saepak --no-deep",
              "satk pack verify mymod.saepak.manifest"))
def pack_verify(target: str, deep: bool = True, sev: Literal["info", "warn", "error"] = "warn",
                limit: int = 20, cursor: str | None = None) -> dict:
    """Verify a pack.

    Args:
        target: a .saepak, a manifest-only .manifest or a .dac file (path or a name in work/out/pack).
        deep: hash every entry and chunk (otherwise only the structure, the footer and the manifest are checked).
        sev: lowest severity shown in the table (the counts always include all): info | warn | error.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .dac import verify_dac
    from .saepak import verify_pack

    path = _find_file(target)
    limit = clamp_limit(limit)
    if _is_dac(path):
        findings, stats = verify_dac(path.read_bytes()), {"bytes": path.stat().st_size}
        kind = "dac"
    else:
        findings, stats = verify_pack(path, deep=deep, progress=report_progress)
        kind = "pack"
    rank = {"info": 0, "warn": 1, "error": 2}
    counts = {s: sum(1 for f in findings if f.sev == s) for s in ("error", "warn", "info")}
    shown = [f.row() for f in sorted(findings, key=lambda f: (-rank[f.sev], f.code, f.where)) if rank[f.sev] >= rank[sev]]
    page, nxt = _page(shown, limit, cursor)
    if counts["error"]:
        bad = [f.row() for f in findings if f.sev == "error"][:limit]
        raise SatkError("CHECK_FAILED", f"{counts['error']} error(s) in {path.name}", hint="satk pack inspect "
                        + path.name, data={"cols": ["sev", "code", "where", "msg"], "rows": bad, "counts": counts})
    env = table(["sev", "code", "where", "msg"], page, total=len(shown), next=nxt)
    env["summary"] = {"file": jpath(path), "kind": kind, "verdict": "pass", "deep": bool(deep and kind == "pack"),
                      **{k: v for k, v in counts.items() if v}, **stats}
    return env


# --------------------------------------------------------------------------- inspect


@op("pack.inspect", mcp=False, group="asset",
    summary="Show a .saepak, its .manifest or a .dac: format version, pack id, namespace, mount mode and priority, "
            "counts, sizes and dedup saving; a table of names (logical name, IMG stream name, kind, size, hash), "
            "entries or the chunk plan of one name.",
    summary_ru="Показать .saepak, .manifest или .dac: версия формата, pack id, namespace, режим монтирования, "
               "размеры и экономия от дедупликации; таблица имён, записей или план чанков одного имени.",
    examples=("satk pack inspect mymod.saepak", "satk pack inspect mymod.saepak --show entries",
              "satk pack inspect mymod.saepak --show chunks --name models/car.dff"))
def pack_inspect(target: str, show: Show = "names", name: str | None = None, limit: int = 20,
                 cursor: str | None = None) -> dict:
    """Inspect a pack.

    Args:
        target: a .saepak, a .manifest or a .dac file (path or a name in work/out/pack).
        show: names (logical names) | entries (distinct contents) | chunks (the chunk plan of --name).
        name: logical name for --show chunks (a pack path such as models/car.dff).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from . import layout as L
    from .dac import DacError, describe_dac
    from .saepak import Pack, PackError

    path = _find_file(target)
    limit = clamp_limit(limit)
    if _is_dac(path):
        try:
            summary, rows = describe_dac(path.read_bytes())
        except DacError as e:
            raise SatkError("UNSUPPORTED", f"{path.name}: {e.msg}", hint=f"satk pack verify {path.name}") from None
        page, nxt = _page(rows, limit, cursor)
        env = table(["kind", "stream", "index", "count", "format", "bytes", "flags"], page, total=len(rows), next=nxt)
        env["summary"] = {"file": jpath(path), "type": "dac", **summary}
        return env
    try:
        pk = Pack.open(path, check_manifest_hash=False)
    except PackError as e:
        raise SatkError("UNSUPPORTED", f"{path.name}: {e.msg}", hint=f"satk pack verify {path.name}") from None
    try:
        with pk:
            slots = None
            if pk.has_payload:
                try:
                    slots = pk.img_directory()
                except PackError:
                    slots = None
            summary = {"file": jpath(path), "type": "saepak", **pk.describe()}
            if not pk.manifest_hash_ok:
                summary["manifest_hash"] = "mismatch"
            warn = ["MANIFEST_HASH: the manifest does not match its footer hash; run satk pack verify"] \
                if not pk.manifest_hash_ok else []
            share: dict[int, int] = {}
            first_name: dict[int, str] = {}
            for n in pk.names:
                share[n["entry_index"]] = share.get(n["entry_index"], 0) + 1
                first_name.setdefault(n["entry_index"], n["logical"])
            if show == "entries":
                rows = []
                for i, e in enumerate(pk.entries):
                    first = first_name.get(i, "")
                    rows.append([i, L.KINDS.get(e["kind"], str(e["kind"])), e["size"], e["sha256"].hex()[:16], e["offset"],
                                 e["chunk_count"], share.get(i, 0), first])
                cols = ["entry", "kind", "size", "sha256", "offset", "chunks", "names", "first_name"]
            elif show == "chunks":
                if not name:
                    raise SatkError("BAD_PARAMS", "--show chunks needs --name", hint="--name models/car.dff")
                idx = pk.name_index(name)
                if idx is None:
                    near = [n["logical"] for n in pk.names if name.lower().rsplit("/", 1)[-1] in n["logical"]][:5]
                    raise SatkError("NOT_FOUND", f"no logical name {name!r} in the pack", did_you_mean=near)
                if pk.names[idx]["entry_index"] >= len(pk.entries):
                    raise SatkError("UNSUPPORTED", f"{name!r} points at a missing entry", hint=f"satk pack verify {path.name}")
                plan = pk.chunk_plan(pk.names[idx]["entry_index"])
                rows = [[k, off, ln, sha.hex()] for k, (off, ln, sha) in enumerate(plan)]
                cols = ["chunk", "offset", "bytes", "sha256"]
                summary["name"] = name.lower()
            else:
                rows = []
                for n in pk.names:
                    stream = ""
                    if slots is not None and n["dir_index"] < len(slots):
                        stream = slots[n["dir_index"]]["name"]
                    if n["entry_index"] >= len(pk.entries):          # a damaged manifest: show the name, not a crash
                        rows.append([n["logical"], stream, "?", 0, "", 0, 0])
                        continue
                    e = pk.entries[n["entry_index"]]
                    rows.append([n["logical"], stream, L.KINDS.get(e["kind"], str(e["kind"])), e["size"],
                                 e["sha256"].hex()[:16], e["chunk_count"], share.get(n["entry_index"], 0)])
                cols = ["name", "stream", "kind", "size", "sha256", "chunks", "shared"]
            page, nxt = _page(rows, limit, cursor)
            env = table(cols, page, total=len(rows), next=nxt, warn=warn)
            env["summary"] = summary
            return env
    except PackError as e:         # a damaged manifest can fail wherever a string or an index is trusted
        raise SatkError("UNSUPPORTED", f"{path.name}: {e.msg}", hint=f"satk pack verify {path.name}") from None


# --------------------------------------------------------------------------- dac


def _dff_targets(targets: list[str], profile: str) -> tuple[list[tuple[str, bytes]], list[str]]:
    """``(label, DFF bytes)`` for files, folders, ``dff:<name>`` and ``model:<id>`` targets."""
    from .sources import collect

    out: list[tuple[str, bytes]] = []
    warn: list[str] = []
    files: list[str] = []
    for t in targets:
        low = str(t).lower().strip()
        if low.startswith(("dff:", "model:")):
            from ..formats.rw import rw_payload_size
            from ..index.api import open_index

            db = open_index(profile)
            if low.startswith("model:"):
                mid = low[6:]
                ref = db.model_files(int(mid) if mid.isdigit() else mid).dff
                if ref is None:
                    raise SatkError("NOT_FOUND", f"{t} has no DFF", hint="satk asset get " + t)
            else:
                ref = db.blob_ref(low)
            raw = db.read_blob(ref)
            n = rw_payload_size(raw)
            out.append((low, raw[:n] if n else raw))
        else:
            files.append(t)
    if files:
        srcs, w = collect(files)
        warn += w
        others = [s.logical for s in srcs if s.ext != "dff"]
        if others:
            warn.append(f"SKIPPED: {len(others)} file(s) that are not DFFs left out (first: {others[0]})")
        for s in srcs:
            if s.ext == "dff":
                out.append((s.logical, s.path.read_bytes()))
    if not out:
        raise SatkError("BAD_PARAMS", "no DFF to derive from", hint="a .dff file, a folder, dff:<name> or model:<id>")
    return out, warn


@op("pack.dac", mcp=False, long_running=True, group="asset",
    summary="Derive DAC (derived-asset cache) files from DFFs: seam-aware normals for geometry without them, night "
            "colour streams, tangents and the 2dEffect light list, content addressed by the source SHA-256 and the "
            "recipe. Local only. Default target: work/cache/dac/.",
    summary_ru="Получить DAC-файлы (кэш производных данных) из DFF: нормали для геометрии без них, потоки ночных "
               "цветов, тангенты и список источников света; адрес по SHA-256 источника. Только локально.",
    examples=("satk pack dac mymod/car.dff", "satk pack dac mymod --normals all", "satk pack dac model:411"))
def pack_dac(targets: list[str], normals: NormalsMode = "missing", tangents: bool = True, night: bool = True,
             lights: bool = True, angle: float = 45.0, out: str | None = None, profile: str = "vanilla",
             limit: int = 20, cursor: str | None = None) -> dict:
    """Derive DAC files.

    Args:
        targets: .dff files, folders, dff:<name> or model:<id> (game models are read from the profile index).
        normals: missing (a computed stream for each geometry without normals) | all | none.
        tangents: derive tangents from UV set 0.
        night: copy the night colours into a stream.
        lights: copy the 2dEffect lights (type 0) into a light list.
        angle: smoothing angle in degrees for computed normals.
        out: write <name>.dac files into this folder instead of the content-addressed cache work/cache/dac/.
        profile: profile whose index resolves dff:/model: targets.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from ..core.paths import atomic_write, work
    from . import layout as L
    from .dac import dac_cache_path, derive_dff, encode_dac

    if not 0 < angle <= 180:
        raise SatkError("BAD_PARAMS", f"angle must be in (0, 180] degrees, got {angle}")
    items, warn = _dff_targets([str(t) for t in targets], profile)
    root = None if out else work("cache", "dac")
    dest = None
    if out:
        dest = Path(out)
        if not dest.is_absolute():
            dest = Path.cwd() / dest
        dest = Path(os.path.abspath(dest))
    rows = []
    used_stems: set[str] = set()
    for i, (label, data) in enumerate(items):
        report_progress(i, len(items), f"deriving {label}")
        w: list[str] = []
        try:
            dac = derive_dff(data, normals=normals, tangents=tangents, night=night, lights=lights, angle=angle, warn=w)
        except ValueError as e:      # PatchError, CodecError, FormatError: a broken DFF is a warning, not a failed batch
            warn.append(f"BAD_DFF: {label}: {e}")
            continue
        warn += [f"{label}: {x}" for x in w]
        blob = encode_dac(dac)
        if dest is not None:
            stem = Path(label.replace(":", "_")).stem
            if stem in used_stems:           # models of different folders with one file name: keep both
                stem = f"{stem}.{dac.key.hex()[:8]}"
            used_stems.add(stem)
            path = dest / (stem + ".dac")
            status = "written"
        else:
            path = dac_cache_path(root, dac.key)
            status = "cached" if path.is_file() and path.read_bytes() == blob else "new"
        if status != "cached":
            atomic_write(path, blob)
        counts = {k: sum(1 for s in dac.sections if s.kind == L.DAC_GEOM_STREAM and s.stream == v)
                  for k, v in (("normal", L.STREAM_NORMAL), ("night", L.STREAM_NIGHT_COLOR), ("tangent", L.STREAM_TANGENT))}
        nl = sum(s.count for s in dac.sections if s.kind == L.DAC_LIGHT_LIST)
        rows.append([label, counts["normal"], counts["night"], counts["tangent"], nl, len(blob), dac.key.hex()[:16],
                     status, jpath(path)])
    page, nxt = _page(rows, clamp_limit(limit), cursor)
    return table(["source", "normals", "night", "tangents", "lights", "bytes", "key", "status", "file"], page,
                 total=len(rows), next=nxt, warn=warn)


# --------------------------------------------------------------------------- census


@op("pack.census", mcp=False, long_running=True, group="asset",
    summary="Texture dedup census over an index profile: per TXD and globally, distinct texel hashes and the "
            "name-preserving key (hash, name, format, size, mips), bytes total vs unique, copy histogram and the most "
            "duplicated groups. Files go to work/out/pack/census/<profile>/.",
    summary_ru="Перепись дедупликации текстур по профилю индекса: по каждому TXD и глобально, различные хеши текселей "
               "и ключ с сохранением имени, байты всего и уникальных, самые продублированные группы.",
    examples=("satk pack census", "satk pack census --by txd --limit 30", "satk pack census --profile installed --exact"))
def pack_census(profile: str = "vanilla", by: Literal["groups", "txd"] = "groups", exact: bool = False,
                limit: int = 20, cursor: str | None = None) -> dict:
    """Count shareable textures.

    Args:
        profile: index profile (vanilla, installed, samp, ...); needs a built index.
        by: table rows: groups (the most duplicated keys) | txd (per-TXD statistics, most shared bytes first).
        exact: re-read the TXD files and hash the whole mip chain instead of trusting the index's base-level hash.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from ..core.paths import work
    from .census import run_census, write_census

    res = run_census(profile, exact=exact)
    out_dir = work("out", "pack", "census", _slug(profile) + ("-exact" if exact else ""))
    files = write_census(res, out_dir)
    if by == "txd":
        data = sorted(res["txds"], key=lambda r: (-r["shared_bytes"], -r["chain_bytes"], r["txd"]))
        cols = ["txd", "textures", "chain_bytes", "distinct", "dup_in_txd", "shared_bytes", "exclusive_bytes", "shared_pct"]
    else:
        data = res["groups"]
        cols = ["rank", "name", "fmt", "w", "h", "levels", "copies", "txds", "chain_bytes", "saved_chain_bytes"]
    rows = [[r[c] for c in cols] + ([", ".join(r["in"][:3])] if by == "groups" else []) for r in data]
    if by == "groups":
        cols = cols + ["in"]
    page, nxt = _page(rows, clamp_limit(limit), cursor)
    env = table(cols, page, total=len(rows), next=nxt, warn=res["warn"])
    env["summary"] = res["summary"]
    env["files"] = files
    return env
