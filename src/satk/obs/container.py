"""The ``.saenet`` / ``.saerec`` container: reader, writer, validator. Stdlib only.

The byte layout is in ``data/obs/sae-container-layout.json`` (machine-readable) and ``docs/en/obs-spec.md``; this module
is a reference implementation that a test checks against the layout file. In short: a 64-byte header, append-only chunks
(40-byte chunk header, payload, padding to 8 bytes) and an optional chunk table at the end, so a writer can stream and a
reader can seek or, when the writer died before closing, scan the chunks from the start.
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.errors import SatkError
from . import schemas
from .stream import Problem, Report, StreamChecker, dump_line

__all__ = ["MAGIC", "HEADER_SIZE", "CHUNK_HEADER_SIZE", "ENTRY_SIZE", "VERSION", "KINDS", "FLAG_FINALIZED", "FLAG_COMPRESSED",
           "FLAG_SORTED", "FLAG_MERGED", "CODEC_NONE", "CODEC_ZLIB", "CODEC_ZSTD", "CF_CRITICAL", "MAX_RAW_DEFAULT",
           "Header", "Chunk", "ContainerError", "Container", "Writer", "open_container", "validate", "pack_records", "is_container",
           "flag_names"]

MAGIC = b"\x89SAEC\r\n\x1a"
HEADER_SIZE = 64
CHUNK_HEADER_SIZE = 40
ENTRY_SIZE = 48
TABLE_MAGIC = b"SAEX"
TABLE_END = b"XEAS"
VERSION = (1, 0)
KINDS = {"SNET": ".saenet", "SREC": ".saerec"}
FLAG_FINALIZED, FLAG_COMPRESSED, FLAG_SORTED, FLAG_MERGED = 1, 2, 4, 8
CODEC_NONE, CODEC_ZLIB, CODEC_ZSTD = 0, 1, 2
CF_CRITICAL = 1
MAX_RAW_DEFAULT = 256 * 1024 * 1024
KNOWN_TYPES = {b"META": True, b"OBSJ": True, b"BLOB": False}   # type -> critical
_HEADER = struct.Struct("<8sHH4sIIQIIqqII")
_CHUNK = struct.Struct("<4sBBHIIqqII")
_NONE_T = -1

assert _HEADER.size == HEADER_SIZE and _CHUNK.size == CHUNK_HEADER_SIZE


def flag_names(flags: int) -> list[str]:
    names = [n for n, b in (("FINALIZED", FLAG_FINALIZED), ("COMPRESSED", FLAG_COMPRESSED), ("SORTED", FLAG_SORTED), ("MERGED", FLAG_MERGED)) if flags & b]
    if flags & ~0xF:
        names.append(f"0x{flags & ~0xF:X}")
    return names


def _align8(n: int) -> int:
    return (n + 7) & ~7


class ContainerError(Exception):
    """A fatal problem: the file cannot be read as a container at all."""

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


@dataclass
class Header:
    ver_major: int
    ver_minor: int
    kind: str
    header_size: int
    flags: int
    index_offset: int
    chunk_count: int
    dt_s_us: int
    t_first_us: int
    t_last_us: int
    session_id: int
    crc_ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"version": f"{self.ver_major}.{self.ver_minor}", "kind": self.kind, "flags": flag_names(self.flags),
                "index_offset": self.index_offset, "chunk_count": self.chunk_count, "dt_s_us": self.dt_s_us,
                "t_first_us": self.t_first_us, "t_last_us": self.t_last_us, "session_id": self.session_id}


@dataclass
class Chunk:
    index: int
    offset: int
    type: bytes
    codec: int
    cflags: int
    stored_size: int
    raw_size: int
    t_first_us: int
    t_last_us: int
    rec_count: int
    crc32: int

    @property
    def name(self) -> str:
        return self.type.decode("ascii", "replace")

    @property
    def critical(self) -> bool:
        return bool(self.cflags & CF_CRITICAL)

    @property
    def end(self) -> int:
        return self.offset + CHUNK_HEADER_SIZE + _align8(self.stored_size)

    def pack(self) -> bytes:
        return _CHUNK.pack(self.type, self.codec, self.cflags, 0, self.stored_size, self.raw_size, self.t_first_us,
                           self.t_last_us, self.rec_count, self.crc32)


def _parse_chunk(index: int, offset: int, raw: bytes) -> tuple[Chunk, int]:
    t, codec, cflags, reserved, stored, rawsz, t0, t1, recs, crc = _CHUNK.unpack(raw)
    return Chunk(index, offset, t, codec, cflags, stored, rawsz, t0, t1, recs, crc), reserved


def is_container(path: Path | str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


# --------------------------------------------------------------------------- reader


class Container:
    """An opened container: :attr:`header`, :attr:`chunks`, :attr:`problems` found while opening."""

    def __init__(self, path: Path, header: Header, chunks: list[Chunk], problems: list[Problem], *, size: int, used_table: bool,
                 max_raw: int):
        self.path = path
        self.header = header
        self.chunks = chunks
        self.problems = problems
        self.size = size
        self.used_table = used_table
        self.max_raw = max_raw

    # ------------------------------------------------------------------ payloads

    def raw_payload(self, c: Chunk) -> bytes:
        """The stored bytes of a chunk (CRC-checked)."""
        with open(self.path, "rb") as f:
            f.seek(c.offset + CHUNK_HEADER_SIZE)
            data = f.read(c.stored_size)
        if len(data) != c.stored_size:
            raise ContainerError("TRUNC", f"chunk {c.index} is cut: {len(data)} of {c.stored_size} payload bytes")
        if zlib.crc32(data) & 0xFFFFFFFF != c.crc32:
            raise ContainerError("CHUNK_CRC", f"chunk {c.index} ({c.name}) fails its CRC")
        return data

    def payload(self, c: Chunk) -> bytes:
        """The decompressed payload of a chunk (CRC and size checked, bomb-safe)."""
        data = self.raw_payload(c)
        if c.codec == CODEC_NONE:
            if c.raw_size != c.stored_size:
                raise ContainerError("SIZE", f"chunk {c.index}: codec 0 but raw_size {c.raw_size} != stored_size {c.stored_size}")
            return data
        if c.codec == CODEC_ZLIB:
            if c.raw_size > self.max_raw:
                raise ContainerError("SIZE", f"chunk {c.index}: raw_size {c.raw_size} above the reader limit {self.max_raw}")
            d = zlib.decompressobj()
            try:
                raw = d.decompress(data, c.raw_size + 1)
                if len(raw) <= c.raw_size and not d.eof and d.unconsumed_tail:
                    if d.decompress(d.unconsumed_tail, 1):
                        raw += b"\0"
            except zlib.error as e:
                raise ContainerError("ZLIB", f"chunk {c.index}: {e}") from None
            if len(raw) != c.raw_size:
                raise ContainerError("SIZE", f"chunk {c.index}: decompressed to {len(raw)} bytes, raw_size says {c.raw_size}")
            if not d.eof:
                raise ContainerError("ZLIB", f"chunk {c.index}: the zlib stream is truncated")
            return raw
        raise ContainerError("CODEC", f"chunk {c.index}: codec {c.codec} is not supported by this reader")

    def meta(self) -> list[dict[str, Any]]:
        """The header records of the META chunks."""
        out = []
        for c in self.chunks:
            if c.type == b"META":
                rec = json.loads(self.payload(c).decode("utf-8"))
                out.append(rec)
        return out

    def iter_chunk_records(self, c: Chunk) -> Iterator[dict[str, Any]]:
        raw = self.payload(c)
        if c.type == b"META":
            yield json.loads(raw.decode("utf-8"))
        elif c.type == b"OBSJ":
            for line in raw.decode("utf-8").split("\n"):
                if line.strip():
                    yield json.loads(line)

    def iter_records(self, chunk: int | None = None) -> Iterator[dict[str, Any]]:
        """All records in file order (META headers first, as written), or those of one chunk."""
        for c in self.chunks:
            if chunk is not None and c.index != chunk:
                continue
            if c.type in (b"META", b"OBSJ"):
                yield from self.iter_chunk_records(c)

    def blobs(self) -> Iterator[tuple[str, bytes]]:
        for c in self.chunks:
            if c.type == b"BLOB":
                raw = self.payload(c)
                n = struct.unpack_from("<H", raw)[0]
                yield raw[2:2 + n].decode("utf-8"), raw[2 + n:]


def _read_table(f, size: int, header: Header, problems: list[Problem]) -> list[tuple[int, Chunk, int]] | None:
    """Parse the chunk table; returns ``[(offset, chunk copy, reserved)]`` or None (problems appended)."""
    def bad(code: str, msg: str) -> None:
        problems.append(Problem("table", "error", code, msg))

    off = header.index_offset
    if off < header.header_size or off + 16 > size:
        bad("TABLE", f"index_offset {off} is outside the file")
        return None
    f.seek(off)
    head = f.read(16)
    if len(head) < 16 or head[:4] != TABLE_MAGIC:
        bad("TABLE", "no chunk table at index_offset")
        return None
    count, esize, _res = struct.unpack_from("<III", head, 4)
    if esize != ENTRY_SIZE:
        bad("TABLE", f"entry_size {esize} (version 1 uses {ENTRY_SIZE})")
        return None
    total = 16 + count * esize + 8
    if off + total > size or count > 10_000_000:
        bad("TABLE", "the chunk table is cut or its count is absurd")
        return None
    body = f.read(count * esize + 8)
    entries = body[:count * esize]
    crc, end_magic = struct.unpack_from("<I4s", body, count * esize)
    if end_magic != TABLE_END:
        bad("TABLE", "the end marker of the chunk table is missing")
        return None
    if zlib.crc32(head + entries) & 0xFFFFFFFF != crc:
        bad("TABLE_CRC", "the chunk table fails its CRC")
        return None
    out = []
    for i in range(count):
        o = struct.unpack_from("<Q", entries, i * esize)[0]
        c, res = _parse_chunk(i, o, entries[i * esize + 8:(i + 1) * esize])
        out.append((o, c, res))
    return out


def open_container(path: Path | str, *, max_raw: int = MAX_RAW_DEFAULT) -> Container:
    """Open a container: header, chunk list (from the table when valid, else by scanning), problems found.

    Raises :class:`ContainerError` only when the file cannot be a version 1 container (bad magic, cut header, other
    major version).
    """
    path = Path(path)
    problems: list[Problem] = []
    size = path.stat().st_size

    def err(where: str, code: str, msg: str, sev: str = "error") -> None:
        problems.append(Problem(where, sev, code, msg))

    with open(path, "rb") as f:
        raw = f.read(HEADER_SIZE)
        if len(raw) < HEADER_SIZE:
            raise ContainerError("TRUNC", f"{len(raw)} bytes: shorter than the {HEADER_SIZE}-byte header")
        magic, vmaj, vmin, kind, hsize, flags, index_off, count, dt, t0, t1, sid, crc = _HEADER.unpack(raw)
        if magic != MAGIC:
            raise ContainerError("MAGIC", "not a sa-engine container (bad magic)")
        if vmaj != VERSION[0]:
            raise ContainerError("VERSION", f"major version {vmaj} is not supported (this reader knows {VERSION[0]})")
        try:
            kind_s = kind.decode("ascii")
        except UnicodeDecodeError:
            kind_s = "????"
        hdr = Header(vmaj, vmin, kind_s, hsize, flags, index_off, count, dt, t0, t1, sid)
        if zlib.crc32(raw[:60]) & 0xFFFFFFFF != crc:
            hdr.crc_ok = False
            err("header", "HEADER_CRC", "the header fails its CRC")
        if vmin > VERSION[1]:
            err("header", "VERSION", f"minor version {vmin} is newer than {VERSION[1]}: unknown ancillary chunks are skipped", "warn")
        if kind_s not in KINDS:
            err("header", "KIND", f"kind {kind_s!r} is not one of {', '.join(KINDS)}")
        if hsize < HEADER_SIZE or hsize % 8 or hsize > size:
            raise ContainerError("HEADER", f"header_size {hsize} is invalid")
        if flags & ~0xF:
            err("header", "FLAGS", f"reserved flag bits 0x{flags & ~0xF:X} are set (ignored by this reader)", "warn")

        table = None
        if flags & FLAG_FINALIZED:
            table = _read_table(f, size, hdr, problems)
        else:
            err("header", "NOT_FINALIZED", "the file was not closed by its writer (no chunk table): scanning the chunks", "warn")

        # sequential scan (always: it is cheap, and it cross-checks the table)
        chunks: list[Chunk] = []
        pos = hsize
        table_pos = None
        while pos < size:
            f.seek(pos)
            head = f.read(CHUNK_HEADER_SIZE)
            if head[:4] == TABLE_MAGIC:
                table_pos = pos
                break
            if len(head) < CHUNK_HEADER_SIZE:
                err(f"offset {pos}", "TRUNC", f"{len(head)} bytes left: shorter than a chunk header")
                break
            c, reserved = _parse_chunk(len(chunks), pos, head)
            tname = c.type
            if not all(32 < b < 127 for b in tname):
                err(f"offset {pos}", "CHUNK", f"chunk type {tname!r} is not four printable ASCII characters")
                break
            if reserved:
                err(f"chunk {c.index}", "CHUNK", "reserved field is not 0", "warn")
            if pos + CHUNK_HEADER_SIZE + c.stored_size > size:
                err(f"chunk {c.index}", "TRUNC", f"the payload ({c.stored_size} bytes) runs past the end of the file")
                chunks.append(c)
                break
            chunks.append(c)
            pos = c.end
            if pos > size:
                pos = size
        if table is not None:
            if [(c.offset, c.pack()) for c in chunks] != [(o, c.pack()) for o, c, _ in table]:
                err("table", "TABLE_MISMATCH", f"the chunk table lists {len(table)} chunks, the scan found {len(chunks)} and/or their headers differ")
            if hdr.chunk_count != len(table):
                err("header", "COUNT", f"chunk_count {hdr.chunk_count} but the table has {len(table)} entries")
        elif flags & FLAG_FINALIZED:
            if table_pos is None:
                err("table", "TABLE", "the header says FINALIZED but no chunk table was found")
    return Container(path, hdr, chunks, problems, size=size, used_table=table is not None, max_raw=max_raw)


# --------------------------------------------------------------------------- validator


def validate(path: Path | str, *, deep: bool = True, strict: bool = False, max_problems: int = 100,
             max_raw: int = MAX_RAW_DEFAULT) -> tuple[Container | None, Report]:
    """Validate a container: header, chunk list and table; with ``deep`` also every payload (CRC, size, decompression,
    the records against the stream rules). Returns ``(container or None, report)``."""
    checker = StreamChecker(strict=strict, max_problems=max_problems)
    try:
        cont = open_container(path, max_raw=max_raw)
    except ContainerError as e:
        checker.error("header", e.code, e.msg)
        return None, checker.report
    for p in cont.problems:
        checker._add(p.where, p.severity, p.code, p.msg)  # noqa: SLF001 - one report for both layers
    h = cont.header
    chunks = cont.chunks
    # structure
    metas = [c for c in chunks if c.type == b"META"]
    first_other = next((c.index for c in chunks if c.type != b"META"), len(chunks))
    if not metas:
        checker.error("chunks", "META", "no META chunk")
    elif metas[0].index != 0:
        checker.error("chunks", "META", "the first chunk must be META")
    if any(m.index > first_other for m in metas):
        checker.error("chunks", "META", "META chunks must come before every other chunk")
    if len(metas) > 1 and not h.flags & FLAG_MERGED:
        checker.error("chunks", "META", f"{len(metas)} META chunks but the MERGED flag is not set")
    compressed = any(c.codec != CODEC_NONE for c in chunks)
    if bool(h.flags & FLAG_COMPRESSED) != compressed and h.flags & FLAG_FINALIZED:
        checker.error("header", "FLAGS", "the COMPRESSED flag " + ("is missing" if compressed else "is set but no chunk is compressed"))
    prev_end = None
    ordered = True
    lo = hi = None
    for c in chunks:
        if c.type not in KNOWN_TYPES and c.critical:
            checker.error(f"chunk {c.index}", "CRITICAL", f"critical chunk type {c.name!r} is unknown to this reader")
        if c.codec not in (CODEC_NONE, CODEC_ZLIB, CODEC_ZSTD):
            checker.error(f"chunk {c.index}", "CODEC", f"unknown codec {c.codec}")
        elif c.codec == CODEC_ZSTD:
            checker.warn(f"chunk {c.index}", "CODEC", "codec 2 (zstd) is reserved: its payload is not checked")
        if c.type in KNOWN_TYPES and bool(c.cflags & CF_CRITICAL) != KNOWN_TYPES[c.type]:
            checker.warn(f"chunk {c.index}", "CHUNK", f"{c.name} should have the critical flag {'set' if KNOWN_TYPES[c.type] else 'clear'}")
        if c.raw_size > cont.max_raw:
            checker.error(f"chunk {c.index}", "SIZE", f"raw_size {c.raw_size} above the reader limit {cont.max_raw}")
        if c.type == b"OBSJ" and c.t_first_us != _NONE_T:
            if c.t_last_us < c.t_first_us:
                checker.error(f"chunk {c.index}", "RANGE", "t_last_us is before t_first_us")
            if prev_end is not None and c.t_first_us < prev_end:
                ordered = False
            prev_end = c.t_last_us if prev_end is None else max(prev_end, c.t_last_us)
            lo = c.t_first_us if lo is None else min(lo, c.t_first_us)
            hi = c.t_last_us if hi is None else max(hi, c.t_last_us)
    if h.flags & FLAG_SORTED and not ordered:
        checker.error("chunks", "ORDER", "the SORTED flag is set but chunk time ranges overlap or go back")
    if h.flags & FLAG_FINALIZED and lo is not None and (h.t_first_us != lo or h.t_last_us != hi):
        checker.error("header", "RANGE", f"time range [{h.t_first_us}, {h.t_last_us}] differs from the chunks' [{lo}, {hi}]")
    # payloads
    if deep:
        for c in chunks:
            where = f"chunk {c.index}"
            if c.codec not in (CODEC_NONE, CODEC_ZLIB):
                continue
            try:
                raw = cont.payload(c)
            except ContainerError as e:
                checker.error(where, e.code, e.msg)
                continue
            if c.type == b"BLOB":
                if len(raw) < 2 or 2 + struct.unpack_from("<H", raw)[0] > len(raw):
                    checker.error(where, "BLOB", "the name length runs past the payload")
                continue
            if c.type not in (b"META", b"OBSJ"):
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                checker.error(where, "UTF8", f"payload is not UTF-8: {e.reason}")
                continue
            if c.type == b"META":
                try:
                    rec = json.loads(text)
                except ValueError as e:
                    checker.error(where, "JSON", f"META is not JSON: {e}")
                    continue
                if not isinstance(rec, dict) or rec.get("rec") != "hdr":
                    checker.error(where, "META", "META must hold one 'hdr' record")
                    continue
                if h.dt_s_us and (rec.get("clock") or {}).get("dt_s_us") not in (h.dt_s_us, None):
                    checker.error(where, "META", f"META clock.dt_s_us differs from the container header's {h.dt_s_us}")
                checker.feed(where, rec)
                continue
            n = 0
            tmin = tmax = None
            for j, line in enumerate(text.split("\n"), 1):
                if not line.strip():
                    continue
                n += 1
                try:
                    rec = json.loads(line)
                except ValueError as e:
                    checker.feed_bad_line(f"{where} rec {j}", f"not JSON: {e}")
                    continue
                if isinstance(rec, dict) and rec.get("rec") == "hdr":
                    checker.error(f"{where} rec {j}", "META", "a hdr record inside an OBSJ chunk (headers belong to META)")
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("t_us"), int):
                    t = rec["t_us"]
                    tmin = t if tmin is None else min(tmin, t)
                    tmax = t if tmax is None else max(tmax, t)
                checker.feed(f"{where} rec {j}", rec)
            if n != c.rec_count:
                checker.error(where, "REC_COUNT", f"rec_count says {c.rec_count}, the payload has {n} records")
            if n and (tmin != c.t_first_us or tmax != c.t_last_us):
                checker.error(where, "RANGE", f"chunk range [{c.t_first_us}, {c.t_last_us}] differs from its records' [{tmin}, {tmax}]")
    rep = checker.finish(require_records=deep)
    return cont, rep


# --------------------------------------------------------------------------- writer


class Writer:
    """Write a container. ``meta()`` first, then ``add()`` records in time order, then ``close()``.

    Chunks are flushed every ``chunk_records`` records or ``chunk_bytes`` raw bytes. ``compress`` stores OBSJ chunks with
    zlib when that makes them smaller. A writer that is never closed leaves a valid, scannable file without a table.
    """

    def __init__(self, path: Path | str, kind: str, *, dt_s_us: int = 0, session_id: int = 0, compress: bool = True,
                 chunk_records: int = 2000, chunk_bytes: int = 256 * 1024, merged: bool = False, level: int = 6):
        if kind not in KINDS:
            raise SatkError("BAD_PARAMS", f"container kind {kind!r} is not one of {', '.join(KINDS)}")
        self.path = Path(path)
        self.kind = kind
        self.dt_s_us = int(dt_s_us)
        self.session_id = int(session_id) & 0xFFFFFFFF
        self.compress = compress
        self.chunk_records = max(1, int(chunk_records))
        self.chunk_bytes = max(1024, int(chunk_bytes))
        self.merged = merged
        self.level = level
        self._f = open(self.path, "wb")
        self._f.write(self._header_bytes(0, 0, 0, _NONE_T, _NONE_T))
        self._chunks: list[Chunk] = []
        self._buf: list[str] = []
        self._times: list[int | None] = []
        self._buf_bytes = 0
        self._t0: int | None = None
        self._t1: int | None = None
        self._closed = False
        self._seen_data = False

    # ------------------------------------------------------------------ pieces

    def _header_bytes(self, flags: int, index_offset: int, count: int, t0: int, t1: int) -> bytes:
        body = _HEADER.pack(MAGIC, VERSION[0], VERSION[1], self.kind.encode("ascii"), HEADER_SIZE, flags, index_offset, count,
                            self.dt_s_us, t0, t1, self.session_id, 0)[:60]
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    def _write_chunk(self, ctype: bytes, raw: bytes, *, critical: bool, t0: int, t1: int, recs: int, compress: bool) -> Chunk:
        codec, stored = CODEC_NONE, raw
        if compress and len(raw) >= 64:
            z = zlib.compress(raw, self.level)
            if len(z) < len(raw):
                codec, stored = CODEC_ZLIB, z
        pos = self._f.tell()
        c = Chunk(len(self._chunks), pos, ctype, codec, CF_CRITICAL if critical else 0, len(stored), len(raw), t0, t1, recs,
                  zlib.crc32(stored) & 0xFFFFFFFF)
        self._f.write(c.pack())
        self._f.write(stored)
        self._f.write(b"\0" * (_align8(len(stored)) - len(stored)))
        self._chunks.append(c)
        return c

    # ------------------------------------------------------------------ api

    def meta(self, hdr: dict[str, Any], *, validate: bool = True) -> None:
        """Write a META chunk holding the stream header record."""
        if self._seen_data:
            raise SatkError("BAD_PARAMS", "META chunks must be written before any record")
        if self._chunks and not self.merged:
            raise SatkError("BAD_PARAMS", "a second META needs merged=True")
        if validate:
            issues = schemas.check_record(hdr)
            if issues or hdr.get("rec") != "hdr":
                raise SatkError("BAD_PARAMS", "the META record is not a valid header: " + "; ".join(i.text() for i in issues[:3]))
        self._write_chunk(b"META", json.dumps(hdr, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), critical=True,
                          t0=_NONE_T, t1=_NONE_T, recs=1, compress=False)

    def add(self, rec: dict[str, Any]) -> None:
        """Buffer one record (a dict with the key members); flushes a chunk when it is full."""
        if not self._chunks:
            raise SatkError("BAD_PARAMS", "write the META chunk first")
        self._seen_data = True
        line = dump_line(rec)
        self._buf.append(line)
        t = rec.get("t_us")
        self._times.append(t if isinstance(t, int) and not isinstance(t, bool) else None)
        self._buf_bytes += len(line) + 1
        if len(self._buf) >= self.chunk_records or self._buf_bytes >= self.chunk_bytes:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        times = [t for t in self._times if t is not None]
        t0 = min(times) if times else _NONE_T
        t1 = max(times) if times else _NONE_T
        raw = ("\n".join(self._buf) + "\n").encode("utf-8")
        self._write_chunk(b"OBSJ", raw, critical=True, t0=t0, t1=t1, recs=len(self._buf), compress=self.compress)
        if times:
            self._t0 = t0 if self._t0 is None else min(self._t0, t0)
            self._t1 = t1 if self._t1 is None else max(self._t1, t1)
        self._buf = []
        self._times = []
        self._buf_bytes = 0

    def blob(self, name: str, data: bytes, *, compress: bool = False) -> None:
        """Write an ancillary BLOB chunk (a named opaque byte string)."""
        self.flush()
        nb = name.encode("utf-8")
        if not nb or len(nb) > 65535:
            raise SatkError("BAD_PARAMS", "blob name must be 1..65535 bytes")
        self._write_chunk(b"BLOB", struct.pack("<H", len(nb)) + nb + bytes(data), critical=False, t0=_NONE_T, t1=_NONE_T,
                          recs=0, compress=compress)

    def close(self) -> Path:
        """Flush, write the chunk table and finalize the header."""
        if self._closed:
            return self.path
        self.flush()
        f = self._f
        index_off = f.tell()
        entries = b"".join(struct.pack("<Q", c.offset) + c.pack() for c in self._chunks)
        head = TABLE_MAGIC + struct.pack("<III", len(self._chunks), ENTRY_SIZE, 0)
        f.write(head + entries + struct.pack("<I", zlib.crc32(head + entries) & 0xFFFFFFFF) + TABLE_END)
        flags = FLAG_FINALIZED
        if any(c.codec != CODEC_NONE for c in self._chunks):
            flags |= FLAG_COMPRESSED
        if self.merged:
            flags |= FLAG_MERGED
        obs = [c for c in self._chunks if c.type == b"OBSJ" and c.t_first_us != _NONE_T]
        if all(b.t_first_us >= a.t_last_us for a, b in zip(obs, obs[1:])):
            flags |= FLAG_SORTED
        f.seek(0)
        f.write(self._header_bytes(flags, index_off, len(self._chunks), self._t0 if self._t0 is not None else _NONE_T,
                                   self._t1 if self._t1 is not None else _NONE_T))
        f.flush()
        f.close()
        self._closed = True
        return self.path

    def abandon(self) -> None:
        """Close the file without a table (what a crash leaves behind): used by tests of the scan path."""
        self.flush()
        if not self._closed:
            self._f.flush()
            self._f.close()
            self._closed = True

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, et, ev, tb) -> None:
        if et is None:
            self.close()
        else:
            self.abandon()


def pack_records(records: Iterable[dict[str, Any]], out: Path | str, *, kind: str | None = None, compress: bool = True,
                 chunk_records: int = 2000, session_id: int | None = None, validate: bool = True) -> dict[str, Any]:
    """Pack stream records (headers first, as in a JSONL stream) into a container at ``out``.

    ``kind`` defaults from the extension (``.saenet`` / ``.saerec``). Several headers make a MERGED container. Returns
    ``{"chunks", "records", "bytes"}``.
    """
    out = Path(out)
    if kind is None:
        kind = {v: k for k, v in KINDS.items()}.get(out.suffix.lower())
        if kind is None:
            raise SatkError("BAD_PARAMS", f"cannot tell the container kind from {out.name!r}",
                            hint="name the file *.saenet (transport trace) or *.saerec (session record), or pass kind")
    it = iter(records)
    heads: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for rec in it:
        if isinstance(rec, dict) and rec.get("rec") == "hdr":
            heads.append(rec)
        else:
            pending = rec
            break
    if not heads:
        raise SatkError("BAD_PARAMS", "the stream has no header record", hint="satk obs validate <file>")
    sid = session_id
    if sid is None:
        s = heads[0].get("session")
        sid = int(s[:8], 16) if isinstance(s, str) and s else 0
    dt = int((heads[0].get("clock") or {}).get("dt_s_us") or 0)
    n = 0
    with Writer(out, kind, dt_s_us=dt, session_id=sid, compress=compress, chunk_records=chunk_records, merged=len(heads) > 1) as w:
        for h in heads:
            w.meta(h, validate=validate)
        if pending is not None:
            w.add(pending)
            n += 1
        for rec in it:
            w.add(rec)
            n += 1
    return {"chunks": len(w._chunks), "records": n, "bytes": out.stat().st_size}  # noqa: SLF001
