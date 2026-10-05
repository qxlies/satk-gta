"""``satk crash info``: the parts of a dump or crash log as tables (modules, threads, streams, MTA sections...)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.paths import jpath
from .analyze import Symbols, hx, load
from .images import Images
from .minidump import STREAM_NAMES, Minidump
from .mta import USER_STREAMS, decode_section, exception_name, parse_log, trailing_sections
from .stack import walk

__all__ = ["info_file", "PARTS"]

PARTS = ("summary", "modules", "threads", "streams", "memory", "sections", "section", "stack", "text", "blocks")


def _page(rows: list[list], limit: int, offset: int) -> tuple[list[list], str | None]:
    nxt = f"o:{offset + limit}" if len(rows) > offset + limit else None
    return rows[offset:offset + limit], nxt


def _section_summary(dec: dict) -> str:
    k = dec.get("kind")
    if k == "pools":
        full = sum(1 for _n, c, u in dec["rows"] if c and u is not None and u >= 0.95 * c)
        return f"{len(dec['rows'])} pools, {full} full"
    if k in ("log", "crash_averted"):
        return f"{len(dec['rows'])} entries"
    if k == "text":
        return f"text, {len(dec['text'].splitlines())} lines"
    if k == "misc":
        return f"bytes@0x748add {dec['bytes_748add']}, crash zone {dec['crash_zone']}"
    if k == "memory":
        return f"process {dec.get('process_kb', '?')} KB, streaming {dec.get('streaming_used', '?')}"
    return f"{dec.get('size', 0)} bytes"


def _dump_info(d: Minidump, path: str, part: str, *, section: str | None, limit: int, offset: int,
               images: list[str] | None, thread: int | None) -> dict:
    img = Images(d, dirs=images)
    if part == "summary":
        exc = d.exception
        sections, start = trailing_sections(d.data)
        mem = d.memory_ranges
        ts = _dt.datetime.fromtimestamp(d.timestamp, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") \
            if d.timestamp else None
        return obj(None, file=jpath(path), kind="minidump", arch=d.arch,
                   os=d.sysinfo.os if d.sysinfo else None, cpus=d.sysinfo.cpus if d.sysinfo else None,
                   pid=d.pid, time=ts, size=d.size, streams=len(d.streams), modules=len(d.modules),
                   unloaded=len(d.unloaded), threads=len(d.threads),
                   memory={"ranges": len(mem), "bytes": sum(m[1] for m in mem)},
                   exception={"code": f"0x{exc.code:08X}", "name": exception_name(exc.code),
                              "address": hx(exc.address), "thread": exc.tid,
                              "params": [hx(p) for p in exc.params]} if exc else None,
                   mta={"streams": [USER_STREAMS[k] for k in USER_STREAMS if k in d.user_streams],
                        "sections": [f"{s.tag}:{s.size}" for s in sections],
                        "sections_offset": start if sections else None},
                   comment=d.comments[0][:300] if d.comments else None,
                   warn=d.warnings or None)
    if part == "modules":
        rows = []
        for m in d.modules:
            pe = img.pe(m)
            rows.append([hx(m.base), hx(m.end), m.name, m.version, f"{m.timestamp:08x}", m.pdb, m.pdb_id,
                         jpath(pe.path) if pe else None])
        page, nxt = _page(rows, limit, offset)
        return table(["base", "end", "name", "version", "timestamp", "pdb", "pdb_id", "image"], page,
                     total=len(rows), next=nxt)
    if part == "threads":
        sym = Symbols(img)
        rows = []
        exc_tid = d.exception.tid if d.exception else None
        for t in d.threads:
            c = t.context
            desc = sym.describe(c.ip) if c else {}
            rows.append([t.tid, t.name, hx(c.ip) if c else None, desc.get("at"), desc.get("fn"),
                         hx(c.sp) if c else None, f"{t.stack_size}", t.suspend or None,
                         True if t.tid == exc_tid else None])
        page, nxt = _page(rows, limit, offset)
        return table(["tid", "name", "ip", "at", "fn", "sp", "stack_bytes", "suspend", "crashed"], page,
                     total=len(rows), next=nxt)
    if part == "streams":
        rows = [[f"0x{st:x}", STREAM_NAMES.get(st) or USER_STREAMS.get(st) or ("comment" if st == 10 else "user"),
                 size, f"0x{rva:x}"] for st, size, rva in d.streams]
        page, nxt = _page(rows, limit, offset)
        env = table(["type", "name", "size", "rva"], page, total=len(rows), next=nxt)
        if d.warnings:
            env["warn"] = d.warnings[:20]
        return env
    if part == "memory":
        rows = []
        for start, size, off in d.memory_ranges:
            m = d.module_at(start)
            rows.append([hx(start), hx(start + size), size, f"{m.name}+0x{start - m.base:x}" if m else None])
        page, nxt = _page(rows, limit, offset)
        return table(["start", "end", "size", "at"], page, total=len(rows), next=nxt)
    if part in ("sections", "section"):
        sections, _start = trailing_sections(d.data)
        if part == "sections":
            rows = [[i + 1, s.tag, s.offset, s.size, decode_section(s).get("kind"), _section_summary(decode_section(s))]
                    for i, s in enumerate(sections)]
            env = table(["n", "tag", "offset", "size", "kind", "summary"], rows, total=len(rows))
            if not rows:
                env["warn"] = ["NO_SECTIONS: no MTA trailing sections (not an MTA client dump, or written before "
                               "the sections were appended)"]
            return env
        return _one_section(sections, section, limit, offset)
    if part == "stack":
        sym = Symbols(img)
        exc = d.exception
        t = d.thread(thread) if thread is not None else (d.thread(exc.tid) if exc else (d.threads[0] if d.threads else None))
        if t is None:
            raise SatkError("NOT_FOUND", f"thread {thread} not in the dump" if thread is not None else "no threads",
                            did_you_mean=[str(x.tid) for x in d.threads[:8]])
        ctx = exc.context if exc is not None and exc.tid == t.tid and exc.context else t.context
        if ctx is None:
            raise SatkError("NOT_FOUND", f"thread {t.tid} has no context")
        frames, st = walk(d, ctx, t, img, is_code=sym.is_code)
        rows = []
        for i, f in enumerate(frames):
            desc = sym.describe(f.va, ret=f.how != "ip")
            rows.append([i, hx(f.slot), desc.get("addr"), desc.get("at"), desc.get("fn"), f.call, f.how,
                         hx(f.target)])
        page, nxt = _page(rows, limit, offset)
        env = table(["#", "slot", "addr", "at", "fn", "call", "via", "target"], page, total=len(rows), next=nxt)
        env.update(thread=t.tid, stack=st)
        if sym.warn:
            env["warn"] = sym.warn
        return env
    if part == "text":
        texts = {name: d.user_streams[k].split(b"\0", 1)[0].decode("utf-8", "replace").splitlines()
                 for k, name in USER_STREAMS.items() if k in d.user_streams}
        if d.comments:
            texts["comment"] = d.comments[0].splitlines()
        if not texts:
            raise SatkError("NOT_FOUND", "the dump has no text streams (MTA user streams or comments)")
        return obj(None, **{k: v[:limit] for k, v in texts.items()})
    raise SatkError("BAD_PARAMS", f"part {part!r} is for crash logs, not dumps", did_you_mean=list(PARTS[:9]))


def _one_section(sections, section: str | None, limit: int, offset: int) -> dict:
    if not section:
        raise SatkError("BAD_PARAMS", "give --section TAG (POL, LOG, REP, ...) or TAG:N for the N-th of that tag",
                        hint="satk crash info FILE --part sections")
    tag, _, nth = section.upper().partition(":")
    same = [s for s in sections if s.tag == tag]
    try:
        k = int(nth) if nth else 1
        if k < 1:
            raise IndexError
        s = same[k - 1]
    except (ValueError, IndexError):
        raise SatkError("NOT_FOUND", f"no section {section!r}", did_you_mean=sorted({x.tag for x in sections})) from None
    dec = decode_section(s)
    if "cols" in dec:
        page, nxt = _page(dec["rows"], limit, offset)
        env = table(dec["cols"], page, total=len(dec["rows"]), next=nxt)
        env.update({k: v for k, v in dec.items() if k not in ("cols", "rows")})
        return env
    if dec.get("kind") == "text":
        lines = dec["text"].splitlines()
        tail = lines[-limit:] if offset == 0 else lines[max(0, len(lines) - limit - offset):len(lines) - offset]
        return obj(None, tag=s.tag, kind="text", total_lines=len(lines), lines=tail,
                   next=f"o:{offset + limit}" if len(lines) > offset + limit else None)
    return obj(None, tag=s.tag, **dec)


def _text_info(text: str, path: str, part: str, *, block: int, limit: int, offset: int) -> dict:
    blocks = parse_log(text)
    if part == "blocks":
        rows = [[i + 1, b.fields.get("Time"), b.module, f"0x{b.code:08X}" if b.code is not None else None,
                 hx(b.offset), len(b.frames)] for i, b in enumerate(blocks)]
        page, nxt = _page(rows, limit, offset)
        return table(["block", "time", "module", "code", "offset", "frames"], page, total=len(rows), next=nxt)
    if part != "summary":
        raise SatkError("BAD_PARAMS", f"part {part!r} needs a minidump; crash logs have: summary, blocks",
                        did_you_mean=["summary", "blocks"])
    try:
        b = blocks[block if block < 0 else block - 1]
    except IndexError:
        raise SatkError("BAD_PARAMS", f"block {block} out of range ({len(blocks)} blocks)") from None
    return obj(None, file=jpath(path), kind="log", blocks=len(blocks), fields=b.fields,
               regs={k: hx(v) for k, v in b.regs.items()}, reason=b.reason, frames=[f.raw for f in b.frames[:limit]])


def info_file(path: str, part: str = "summary", *, section: str | None = None, limit: int = 50, offset: int = 0,
              images: list[str] | None = None, thread: int | None = None, block: int = -1) -> dict:
    kind, o = load(path)
    if kind == "dump":
        d: Minidump = o  # type: ignore[assignment]
        try:
            return _dump_info(d, path, part, section=section, limit=limit, offset=offset, images=images, thread=thread)
        finally:
            d.close()
    return _text_info(o, str(Path(path)), part, block=block, limit=limit, offset=offset)  # type: ignore[arg-type]
