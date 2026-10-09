"""Operations of satk.obs: ``satk obs validate|read|summarize|convert|merge|pack|unpack|schema|sample``.

All are ``mcp=False`` (agents reach them through ``satk_ops``/``satk_op``). They read observability files (JSONL
streams ``sae-obs/1``, bench JSON ``sae-bench/1`` and the legacy ``satk-bench/1``, ``.saenet`` / ``.saerec`` containers)
and write only under ``work/out/obs``. Module-level imports are stdlib and satk core only; the work lives in the sibling
modules.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.registry import op

Kind = Literal["auto", "jsonl", "bench", "container"]


def _j(p: Any) -> str:
    from ..core import paths

    return paths.jpath(p)


def _problems_table(rep, limit: int, cursor: str | None, extra: dict[str, Any]) -> dict[str, Any]:
    from . import reader as R

    rows = [p.row() for p in rep.problems]
    sl, nxt = R.page(rows, limit, cursor)
    warn = []
    if rep.truncated:
        warn.append(f"TRUNCATED: only the first {len(rep.problems)} problems are kept; counts below are complete")
    env = table(["where", "severity", "code", "message"], sl, total=len(rows), next=nxt, warn=warn)
    env["verdict"] = "pass" if rep.ok else "FAIL"
    env["errors"] = rep.errors
    env["warnings"] = rep.warnings
    if rep.by_code:
        env["by_code"] = {k: v for k, v in sorted(rep.by_code.items())}
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None and v != {}}


@op("obs.validate", mcp=False, group="engine",
    summary="Validate an observability file: a sae-obs/1 JSONL stream, a sae-bench/1 (or legacy satk-bench/1) bench JSON, or a "
            ".saenet/.saerec container. Schema plus key order, tick grid, mode and footer rules; table of problems and a verdict.",
    summary_ru="Проверить файл наблюдаемости: поток JSONL sae-obs/1, JSON бенча sae-bench/1 (или satk-bench/1), контейнер "
               ".saenet/.saerec; таблица проблем и вердикт.",
    examples=("satk obs validate sample/client-fixed.jsonl", "satk obs validate sample/session.saerec --strict",
              "satk obs validate sample/client-fixed.bench.json"))
def obs_validate(path: str, kind: Kind = "auto", strict: bool = False, deep: bool = True, limit: int = 20,
                 cursor: str | None = None) -> dict:
    """Check one file against the specification (docs/en/obs-spec.md); a failing file is a result with verdict FAIL, not an error.

    Args:
        path: a file, or a name under work/out/obs.
        kind: auto (detected), jsonl, bench or container.
        strict: also reject members the schema does not declare (default: ignored, so newer minor versions still pass).
        deep: containers only: read and check every chunk payload (CRC, size, records); false checks the header and chunk table.
        limit: problems per page (max 500).
        cursor: next cursor from an earlier page.
    """
    from . import bench as B
    from . import container as C
    from . import reader as R
    from . import stream as S

    p = R.resolve(path)
    k = R.kind_of(p, kind)
    lim = clamp_limit(limit)
    base: dict[str, Any] = {"path": _j(p), "kind": k}
    if k == "jsonl":
        rep = S.check_file(p, strict=strict)
        base.update(schema="sae-obs/1", records=rep.records, kinds=dict(sorted(rep.kinds.items())), nodes=rep.nodes)
    elif k == "container":
        cont, rep = C.validate(p, deep=deep, strict=strict)
        base.update(records=rep.records, kinds=dict(sorted(rep.kinds.items())), nodes=rep.nodes)
        if cont is not None:
            base["container"] = cont.header.as_dict() | {"chunks": len(cont.chunks), "bytes": cont.size,
                                                         "table": "used" if cont.used_table else "scanned"}
    else:
        doc = B.load_json(p)
        sch = B.detect_schema(doc)
        warn_extra = None
        if sch == "satk-bench/1":
            new = B.convert_satk_bench(doc)
            if isinstance(doc.get("summary"), dict):
                new["summary"] = doc["summary"]
            rep = B.validate(new, strict=False)
            warn_extra = "LEGACY: a satk-bench/1 file, checked after an in-memory conversion (satk obs convert writes sae-bench/1)"
        else:
            rep = B.validate(doc, strict=strict)
        base.update(schema=sch, stages=len(doc.get("stages") or []) if isinstance(doc, dict) else None)
        env = _problems_table(rep, lim, cursor, base)
        if warn_extra:
            env["warn"] = [*env.get("warn", []), warn_extra]
        return env
    return _problems_table(rep, lim, cursor, base)


@op("obs.read", mcp=False, group="engine",
    summary="List records of a sae-obs/1 stream or container (filter by kind, time, tick, node; pick dotted fields), the stages of a "
            "bench JSON, or the chunk table of a .saenet/.saerec. Paged table; out= writes the matches as JSONL.",
    summary_ru="Записи потока sae-obs/1 или контейнера (фильтры по виду, времени, тику, узлу), стадии бенча, таблица чанков "
               ".saenet/.saerec; постранично, out= пишет выборку в JSONL.",
    examples=("satk obs read sample/client-fixed.jsonl --rec frame --limit 5", "satk obs read sample/session.saerec --chunks",
              "satk obs read sample/client-fixed.jsonl --rec frame --fields frame_ms ticks fence_ms.F5 --limit 5"))
def obs_read(path: str, kind: Kind = "auto", rec: list[str] | None = None, t_from: int | None = None, t_to: int | None = None,
             tick_from: int | None = None, tick_to: int | None = None, node: str | None = None, fields: list[str] | None = None,
             chunks: bool = False, chunk: int | None = None, out: str | None = None, limit: int = 20,
             cursor: str | None = None) -> dict:
    """Read a file as a table.

    Args:
        path: a file, or a name under work/out/obs.
        kind: auto (detected), jsonl, bench or container.
        rec: record kinds to keep (hdr frame tick stats event net input seed snap end).
        t_from: keep records with t_us >= this (microseconds of session time).
        t_to: keep records with t_us <= this.
        tick_from: keep records with server tick >= this.
        tick_to: keep records with server tick <= this.
        node: keep the records of this node (merged streams).
        fields: dotted members to show as columns (frame_ms, fence_ms.F5, frames.p99_ms); default: key columns and a summary.
        chunks: container only: list the chunks instead of the records.
        chunk: container only: read the records of this chunk only.
        out: write the matching records as JSONL to this file (relative names go under work/out/obs); the table still pages.
        limit: rows per page (max 500).
        cursor: next cursor from an earlier page.
    """
    from . import bench as B
    from . import container as C
    from . import reader as R
    from . import stream as S

    p = R.resolve(path)
    k = R.kind_of(p, kind)
    lim = clamp_limit(limit)
    extra: dict[str, Any] = {"path": _j(p), "kind": k}
    if k == "bench":
        doc = B.load_json(p)
        if not isinstance(doc, dict):
            raise SatkError("BAD_PARAMS", f"{p.name} is not a bench result")
        cols, rows = R.bench_rows(doc, fields)
        sl, nxt = R.page(rows, lim, cursor)
        extra.update(schema=doc.get("schema"), scene=doc.get("scene"), label=doc.get("label"), run=doc.get("run"))
        env = table(cols, sl, total=len(rows), next=nxt)
        env.update(extra)
        return {k2: v for k2, v in env.items() if v is not None}
    if k == "container":
        try:
            cont = C.open_container(p)
        except C.ContainerError as e:
            raise SatkError("BAD_PARAMS", f"{p.name}: {e.msg}", hint=f"satk obs validate {p.name}") from None
        extra["container"] = cont.header.as_dict() | {"chunks": len(cont.chunks), "bytes": cont.size}
        if chunks:
            rows = R.container_rows(cont)
            sl, nxt = R.page(rows, lim, cursor)
            env = table(R.CHUNK_COLS, sl, total=len(rows), next=nxt, warn=[f"{x.code}: {x.msg}" for x in cont.problems[:3]])
            env.update(extra)
            return {k2: v for k2, v in env.items() if v is not None}
    cols, rows = R.record_rows(p, k, recs=rec, t_from=t_from, t_to=t_to, tick_from=tick_from, tick_to=tick_to, node=node,
                               fields=fields, chunk=chunk)
    if out:
        dest = R.out_path(out, p.stem + ".out.jsonl")
        wanted = {r[0] for r in rows}
        S.write_jsonl(dest, (r for seq, r in R.iter_records(p, k, chunk=chunk) if seq in wanted))
        extra["out"] = _j(dest)
        extra["written"] = len(wanted)
    sl, nxt = R.page(rows, lim, cursor)
    env = table(cols, sl, total=len(rows), next=nxt)
    env.update(extra)
    return {k2: v for k2, v in env.items() if v is not None}


@op("obs.summarize", mcp=False, group="engine",
    summary="Summarize an observability file: for a stream or container the frame-time statistics, ticks, dropped time, per-fence "
            "durations, counters, events and the last engine snapshot per node; for a bench JSON a table per stage.",
    summary_ru="Сводка файла наблюдаемости: статистика кадров, тики, потерянное время, фенсы, счётчики, события по узлам; "
               "для JSON бенча таблица по стадиям.",
    examples=("satk obs summarize sample/client-fixed.jsonl", "satk obs summarize sample/session.saerec",
              "satk obs summarize sample/client-fixed.bench.json"))
def obs_summarize(path: str, kind: Kind = "auto", node: str | None = None) -> dict:
    """Numbers of a file in a few lines (no record dump).

    Args:
        path: a file, or a name under work/out/obs.
        kind: auto (detected), jsonl, bench or container.
        node: summarize only this node of a merged stream.
    """
    from . import bench as B
    from . import container as C
    from . import reader as R
    from . import stream as S

    p = R.resolve(path)
    k = R.kind_of(p, kind)
    if k == "bench":
        doc = B.load_json(p)
        if not isinstance(doc, dict):
            raise SatkError("BAD_PARAMS", f"{p.name} is not a bench result")
        legacy = doc.get("schema") == "satk-bench/1"
        view = B.convert_satk_bench(doc) if legacy else doc
        env = table(B.TABLE_COLS, B.table_rows(view))
        env.update(path=_j(p), kind=k, schema=doc.get("schema"), run=doc.get("run"), scene=doc.get("scene"),
                   label=doc.get("label"), env=view.get("env"), summary=view.get("summary"), source=view.get("source"),
                   incomplete=doc.get("incomplete"))
        return {k2: v for k2, v in env.items() if v is not None and v != {}}
    extra: dict[str, Any] = {"path": _j(p), "kind": k}
    if k == "container":
        try:
            cont = C.open_container(p)
        except C.ContainerError as e:
            raise SatkError("BAD_PARAMS", f"{p.name}: {e.msg}", hint=f"satk obs validate {p.name}") from None
        census: dict[str, dict[str, int]] = {}
        for c in cont.chunks:
            row = census.setdefault(c.name, {"chunks": 0, "records": 0, "stored": 0, "raw": 0})
            row["chunks"] += 1
            row["records"] += c.rec_count
            row["stored"] += c.stored_size
            row["raw"] += c.raw_size
        extra["container"] = cont.header.as_dict() | {"bytes": cont.size, "chunks": len(cont.chunks),
                                                      "table": "used" if cont.used_table else "scanned"}
        extra["chunk_types"] = census
        stored = sum(c.stored_size for c in cont.chunks)
        raw = sum(c.raw_size for c in cont.chunks)
        if stored:
            extra["compression_ratio"] = round(raw / stored, 2)
        if cont.problems:
            extra["warn"] = [f"{x.code}: {x.msg}" for x in cont.problems[:3]]
    recs = (r for _, r in R.iter_records(p, k))
    if node is not None:
        recs = (r for r in recs if r.get("node") == node or (r.get("rec") == "hdr" and r.get("node") == node))
    return obj(None, **extra, **S.summarize_records(recs))


@op("obs.convert", mcp=False, group="engine",
    summary="Convert to the sae-bench/1 bench JSON: from a satk-bench/1 result of 'satk ingame bench' (lossless) or from the frame, "
            "tick and stats records of a sae-obs/1 stream or container (one stage per node). Writes under work/out/obs.",
    summary_ru="Преобразовать в JSON бенча sae-bench/1: из результата satk-bench/1 или из записей потока/контейнера sae-obs/1.",
    examples=("satk obs convert sample/client-fixed.jsonl --scene S0 --label fixed", "satk obs convert sample/session.saerec --skip-s 1"))
def obs_convert(src: str, out: str | None = None, run: str | None = None, scene: str | None = None, label: str | None = None,
                skip_s: float = 0.0, until_s: float | None = None) -> dict:
    """Write a sae-bench/1 document and report its validation.

    Args:
        src: a satk-bench/1 result, a sae-obs/1 stream or a container (a file, or a name under work/out/obs).
        out: output file (default <name>.sae-bench.json under work/out/obs).
        run: run id for a stream conversion (default: the file name).
        scene: scene name for a stream conversion (default: trace).
        label: label for a stream conversion (default: the node id).
        skip_s: stream conversion: drop the first seconds (warm-up) of the frame records.
        until_s: stream conversion: stop this many seconds after the first frame record.
    """
    from . import bench as B
    from . import reader as R

    p = R.resolve(src)
    k = R.kind_of(p, "auto")
    warn: list[str] = []
    if k == "bench":
        doc = B.load_json(p)
        sch = B.detect_schema(doc)
        if sch == "sae-bench/1":
            raise SatkError("BAD_PARAMS", f"{p.name} is already sae-bench/1", hint="satk obs validate " + p.name)
        new = B.convert_satk_bench(doc)
        if isinstance(doc.get("summary"), dict) and doc["summary"] != new["summary"]:
            warn.append("SUMMARY: the recomputed summary differs from the one in the source; the recomputed one is written")
        origin = "satk-bench/1"
    else:
        recs = [r for _, r in R.iter_records(p, k)]
        new = B.doc_from_records(recs, run=run or p.stem, scene=scene or "trace", label=label, skip_s=float(skip_s),
                                 until_s=until_s)
        origin = "sae-obs/1"
    dest = R.out_path(out, p.stem + ".sae-bench.json")
    dest.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    rep = B.validate(new)
    return obj(None, path=_j(dest), schema="sae-bench/1", converted_from=origin, stages=len(new["stages"]),
               summary=new["summary"], verdict="pass" if rep.ok else "FAIL", errors=rep.errors or None,
               problems=[x.row() for x in rep.problems[:5]] or None, warn=warn or None)


@op("obs.merge", mcp=False, group="engine",
    summary="Merge sae-obs/1 streams or containers of several nodes (client, server, bots) into one JSONL stream ordered by the "
            "timeline key (t_us, tick, sub, frame); every record gets its node id. Warns when a node's clock is not server-aligned.",
    summary_ru="Слить потоки sae-obs/1 нескольких узлов (клиент, сервер, боты) в один JSONL по ключу времени (t_us, tick, sub, frame).",
    examples=("satk obs merge sample/client-fixed.jsonl sample/server.jsonl --out merged-demo.jsonl",))
def obs_merge(paths: list[str], out: str | None = None) -> dict:
    """Merge several streams; node ids must differ.

    Args:
        paths: two or more streams or containers (files, or names under work/out/obs).
        out: output JSONL (default merged.jsonl under work/out/obs).
    """
    from . import reader as R
    from . import stream as S

    if len(paths) < 2:
        raise SatkError("BAD_PARAMS", "give at least two streams to merge")
    ins = []
    for spec in paths:
        p = R.resolve(spec)
        k = R.kind_of(p, "auto")
        if k == "bench":
            raise SatkError("BAD_PARAMS", f"{p.name} is a bench result, not a stream")
        ins.append((p.name, p, k))
    local: list[str] = []

    def feed(p: Path, k: str):
        for _, rec in R.iter_records(p, k):
            if rec.get("tsrc") == "local" and p.name not in local:
                local.append(p.name)
            yield rec

    dest = R.out_path(out, "merged.jsonl")
    n = S.write_jsonl(dest, S.merge_streams([(name, feed(p, k)) for name, p, k in ins]))
    warn = [f"CLOCK: {', '.join(local)} carry tsrc=local records: their t_us is not server-aligned, the merge order is approximate"] if local else []
    return obj(None, path=_j(dest), records=n, inputs=[name for name, _, _ in ins], warn=warn or None)


@op("obs.pack", mcp=False, group="engine",
    summary="Pack a sae-obs/1 JSONL stream into a .saenet (transport trace) or .saerec (session record) container: META chunk, "
            "OBSJ chunks, optional zlib compression, chunk table. Kind follows the output extension.",
    summary_ru="Упаковать поток JSONL sae-obs/1 в контейнер .saenet или .saerec (чанки, zlib, таблица чанков).",
    examples=("satk obs pack sample/client-fixed.jsonl demo.saerec",))
def obs_pack(src: str, out: str, compress: bool = True, chunk_records: int = 2000) -> dict:
    """Write a container from a stream.

    Args:
        src: a JSONL stream (file, or name under work/out/obs).
        out: output file ending in .saenet or .saerec (relative names go under work/out/obs).
        compress: store chunks with zlib when that is smaller (the COMPRESSED header flag).
        chunk_records: records per OBSJ chunk.
    """
    from . import container as C
    from . import reader as R
    from . import stream as S

    p = R.resolve(src)
    if R.kind_of(p, "auto") != "jsonl":
        raise SatkError("BAD_PARAMS", f"{p.name} is not a JSONL stream")
    dest = R.out_path(out, p.stem + ".saerec")
    res = C.pack_records((r for _, r in R.iter_records(p, "jsonl")), dest, compress=bool(compress), chunk_records=max(1, int(chunk_records)))
    cont, rep = C.validate(dest, deep=True)
    return obj(None, path=_j(dest), kind=cont.header.kind if cont else None, chunks=res["chunks"], records=res["records"],
               bytes=res["bytes"], flags=C.flag_names(cont.header.flags) if cont else None, verdict="pass" if rep.ok else "FAIL",
               errors=rep.errors or None, problems=[x.row() for x in rep.problems[:5]] or None)


@op("obs.unpack", mcp=False, group="engine",
    summary="Unpack a .saenet/.saerec container into a sae-obs/1 JSONL stream (META header first, then every OBSJ record in order).",
    summary_ru="Распаковать контейнер .saenet/.saerec в поток JSONL sae-obs/1.",
    examples=("satk obs unpack sample/session.saerec --out unpacked-demo.jsonl",))
def obs_unpack(src: str, out: str | None = None) -> dict:
    """Write the records of a container as JSONL.

    Args:
        src: a container (file, or name under work/out/obs).
        out: output JSONL (default <name>.jsonl under work/out/obs).
    """
    from . import reader as R
    from . import stream as S

    p = R.resolve(src)
    if R.kind_of(p, "auto") != "container":
        raise SatkError("BAD_PARAMS", f"{p.name} is not a .saenet/.saerec container")
    dest = R.out_path(out, p.stem + ".jsonl")
    n = S.write_jsonl(dest, (r for _, r in R.iter_records(p, "container")))
    return obj(None, path=_j(dest), records=n)


@op("obs.schema", mcp=False, group="engine",
    summary="The machine-readable schemas of the observability formats: list them, or print one (common, record, bench, layout), "
            "optionally one definition (frame, tick, stats, key, snapshot, ...). Files live in data/obs.",
    summary_ru="Машиночитаемые схемы форматов наблюдаемости: список или содержимое одной схемы/определения.",
    examples=("satk obs schema", "satk obs schema record --definition frame"))
def obs_schema(name: str | None = None, definition: str | None = None) -> dict:
    """List the schema documents, or return one (or one definition of it).

    Args:
        name: common, record, bench or layout; empty lists them all.
        definition: a name under $defs of the schema (frame, tick, stats, event, net, key, snapshot, stage, ...).
    """
    from ..core import resources
    from . import schemas as SC

    if name is None:
        rows = SC.describe()
        return table(["name", "id", "file", "title", "defs"], [[r["name"], r["id"], r["file"], r["title"], len(r["defs"])] for r in rows])
    doc = SC.layout() if name == "layout" else SC.load(name)
    fname = "sae-container-layout.json" if name == "layout" else SC.FILES[name][0]
    path = resources.data_path("obs", fname)
    if definition:
        defs = doc.get("$defs") or {}
        if definition not in defs:
            raise SatkError("NOT_FOUND", f"schema {name!r} has no definition {definition!r}", hint="defs: " + ", ".join(sorted(defs)),
                            did_you_mean=difflib.get_close_matches(definition, list(defs), n=5, cutoff=0.5))
        return obj(None, name=name, definition=definition, path=_j(path), schema=defs[definition])
    if name == "layout":
        return obj(None, name=name, path=_j(path), schema=doc)
    return obj(None, name=name, path=_j(path), schema_id=doc.get("$id"), title=doc.get("title"), defs=sorted(doc.get("$defs") or {}),
               schema=doc if len(json.dumps(doc)) < 6000 else None)


@op("obs.sample", mcp=False, group="engine",
    summary="Write deterministic example files (client and server JSONL streams, a merged stream, a .saenet and a .saerec container, "
            "a sae-bench/1 document) under work/out/obs: the reference data for writers and for the docs.",
    summary_ru="Записать детерминированные примеры (потоки JSONL, слитый поток, контейнеры, бенч sae-bench/1) в work/out/obs.",
    examples=("satk obs sample", "satk obs sample --dir sample"))
def obs_sample(dir: str = "sample") -> dict:  # noqa: A002 - a CLI option name
    """Generate the example files; the same call writes the same bytes (the numbers are invented, not measured).

    Args:
        dir: output folder (relative names go under work/out/obs).
    """
    from . import reader as R
    from . import sample as SM

    d = R.out_dir(dir)
    files = SM.write_samples(d)
    rows = [[name, p.stat().st_size, _j(p)] for name, p in sorted(files.items())]
    return table(["file", "bytes", "path"], rows)
