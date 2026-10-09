"""The ``.saepak`` content container: writer, reader and verifier (format 1.0; the byte layout is :mod:`.layout`).

A pack is, from the first byte to the last:

1. an **IMG VER2 directory** (``VER2``, a count, 32-byte entries): the file is a valid IMG archive for the engine's
   streamer and for any IMG tool; every directory slot points at one payload;
2. the **payload**: each distinct content once, at a 2048-byte sector boundary, zero padded to whole sectors. The
   content address is its SHA-256; names that carry identical bytes share one payload and, when their stream names
   are equal, one directory slot;
3. the **manifest block** (sector aligned): header, entry table (hashes, offsets, sizes, chunk ranges), name table
   (logical name -> entry + directory slot), chunk table (a SHA-256 per chunk of ``chunk_size`` bytes, so a content
   channel can fetch and verify ranges piecemeal), and the string area (namespace, title, mount name, logical
   names). Fast-hash fields (XXH3-128) are reserved in every record and filled when the pack is built with
   ``fast="xxh3"``;
4. the **footer** (128 bytes, last): the manifest location, its SHA-256 and a CRC. Reading from the end (an HTTP
   suffix range) finds the manifest without touching the payload. ``<pack>.manifest`` is a manifest-only copy
   (manifest block + footer) for servers and fetchers.

Logical names are lower-case relative paths (``vehicles/tesla/body.dff``); the registry and the VFS derive their
asset ids from the namespace and the logical name. IMG stream names (the engine's view) are the base name when it
fits the 23-character IMG field, else ``~<18 hex of the content SHA-256>.<ext>``.

Determinism: the same inputs and options give the same bytes (no time, no machine data in the file).
Stdlib only (``xxhash`` is imported only for ``fast="xxh3"``).
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from ..core.errors import SatkError, require_module
from ..core.paths import ensure_writable, jpath, open_ro
from . import layout as L
from .sources import Source, logical_problem

__all__ = ["PackError", "Finding", "Pack", "build_pack", "verify_pack", "compute_pack_id", "stream_name_for",
           "fast_digest", "rw_info", "StringTable", "sidecar_path"]

NUL = bytes(1)
#: A manifest larger than this is refused (about 10 million chunk records).
MAX_MANIFEST = 1 << 30
PACK_ID_PREFIX = b"SAEPACK1\n"
_STREAM_BASE = re.compile(r"[a-z0-9_][a-z0-9_.\-]*\.[a-z]{3}")


class PackError(ValueError):
    """The file is not a readable pack. ``code`` is a verifier finding code (``BAD_MAGIC``, ``FOOTER_CRC``, ...)."""

    def __init__(self, code: str, msg: str, where: str = ""):
        self.code = code
        self.msg = msg
        self.where = where
        super().__init__(f"{code}: {msg}" + (f" ({where})" if where else ""))


@dataclass(frozen=True)
class Finding:
    sev: str          # error | warn | info
    code: str
    where: str
    msg: str

    def row(self) -> list:
        return [self.sev, self.code, self.where, self.msg]


# --------------------------------------------------------------------------- hashes


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def fast_digest(algo: int, data: bytes) -> bytes:
    """The 16-byte fast hash field of ``data``: zeros for ``FAST_NONE``, the canonical XXH3-128 digest otherwise."""
    if algo == L.FAST_NONE:
        return bytes(16)
    if algo != L.FAST_XXH3_128:
        raise ValueError(f"unknown fast hash algorithm {algo}")
    xx = require_module("xxhash", purpose="XXH3-128 fast hashes of a pack")
    return bytes(xx.xxh3_128_digest(data))


def compute_pack_id(namespace: str, items: Iterable[tuple[str, bytes]]) -> bytes:
    """Pack identity: SHA-256 over ``SAEPACK1\\n``, the namespace and the sorted ``logical NUL sha256 \\n`` lines.

    Independent of chunk size, mount metadata, offsets and the fast hash: it names *what the pack maps*.
    """
    h = hashlib.sha256()
    h.update(PACK_ID_PREFIX)
    h.update(namespace.encode("utf-8") + b"\n")
    for logical, digest in sorted(((n.encode("utf-8"), d) for n, d in items)):
        h.update(logical + NUL + digest + b"\n")
    return h.digest()


def chunk_ranges(size: int, chunk_size: int) -> list[tuple[int, int]]:
    """``(offset inside the content, length)`` of every chunk of a ``size``-byte content."""
    return [(o, min(chunk_size, size - o)) for o in range(0, size, chunk_size)]


# --------------------------------------------------------------------------- content facts


def rw_version_of(libid: int) -> int:
    """RenderWare version from a chunk's library stamp (``0x1803FFFF`` -> ``0x36003``); 0 when unrecognisable."""
    if libid >> 16:
        return (((libid >> 14) & 0x3FF00) + 0x30000) | ((libid >> 16) & 0x3F)
    return libid << 8 if libid else 0


def rw_info(ext: str, data: bytes) -> tuple[int, list[str]]:
    """``(rw_version, notes)`` of a streamable file: a cheap header check, never a parse of the body.

    ``notes`` are warnings such as a DFF that does not start with a Clump chunk or a chunk longer than the file.
    """
    notes: list[str] = []
    if ext in ("dff", "txd"):
        want, label = (0x10, "Clump") if ext == "dff" else (0x16, "TextureDictionary")
        if len(data) < 12:
            return 0, [f"shorter than a RenderWare chunk header ({len(data)} bytes)"]
        t, size, lib = struct.unpack_from("<III", data, 0)
        if t != want:
            notes.append(f"first chunk is 0x{t:X}, a .{ext} starts with {label} 0x{want:X}")
        if size + 12 > len(data):
            notes.append(f"first chunk declares {size} bytes, the file has {len(data) - 12} after its header")
        return rw_version_of(lib), notes
    if ext == "col" and data[:4] not in (b"COLL", b"COL2", b"COL3", b"COL4"):
        notes.append("does not start with COLL/COL2/COL3/COL4")
    if ext == "ifp" and data[:4] not in (b"ANP3", b"ANPK"):
        notes.append("does not start with ANP3/ANPK")
    return 0, notes


# --------------------------------------------------------------------------- strings


class StringTable:
    """Deduplicating UTF-8 string area; ``add`` returns ``(offset, length)`` and the empty string is ``(0, 0)``."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self._seen: dict[bytes, tuple[int, int]] = {}

    def add(self, s: str) -> tuple[int, int]:
        b = s.encode("utf-8")
        if not b:
            return 0, 0
        hit = self._seen.get(b)
        if hit is None:
            hit = (len(self.buf), len(b))
            self.buf += b
            self._seen[b] = hit
        return hit


def derived_stream_name(ext: str, sha_hex: str) -> str:
    """The content-addressed IMG name ``~<18 hex of the SHA-256>.<ext>`` (23 characters for a 3-letter extension)."""
    return f"~{sha_hex[:18]}.{ext}"


def stream_name_for(logical: str, ext: str, sha_hex: str) -> tuple[str, bool]:
    """``(IMG stream name, derived)``: the base name when it fits the IMG field, else the derived name."""
    base = logical.rsplit("/", 1)[-1]
    if len(base) <= L.STREAM_NAME_MAX and _STREAM_BASE.fullmatch(base):
        return base, False
    return derived_stream_name(ext, sha_hex), True


# --------------------------------------------------------------------------- the manifest block


def _pad(n: int, a: int) -> int:
    return -n % a


def encode_manifest(*, header: dict, entries: list[dict], names: list[dict], chunks: list[tuple[bytes, bytes]],
                    strings: bytes) -> bytes:
    """The manifest block (padded to 8 bytes) for already laid out tables; fills the table offsets."""
    n_e, n_n, n_c = len(entries), len(names), len(chunks)
    entries_off = L.HEADER.size
    names_off = entries_off + n_e * L.ENTRY.size
    chunks_off = names_off + n_n * L.NAME.size
    strings_off = chunks_off + n_c * L.CHUNK.size
    h = dict(header)
    h.update(magic=L.MANIFEST_MAGIC, major=L.FORMAT_MAJOR, minor=L.FORMAT_MINOR, header_size=L.HEADER.size,
             sector_size=L.SECTOR, entry_count=n_e, name_count=n_n, chunk_count=n_c, entries_off=entries_off,
             names_off=names_off, chunks_off=chunks_off, strings_off=strings_off, strings_size=len(strings))
    parts = [L.HEADER.pack(**h)]
    parts += [L.ENTRY.pack(**e) for e in entries]
    parts += [L.NAME.pack(**n) for n in names]
    parts += [L.CHUNK.pack(sha256=s, fast=f) for s, f in chunks]
    parts.append(strings)
    out = b"".join(parts)
    return out + bytes(_pad(len(out), 8))


def encode_footer(*, manifest: bytes, manifest_offset: int, pack_size: int, fast_algo: int) -> bytes:
    values = dict(magic=L.FOOTER_MAGIC, major=L.FORMAT_MAJOR, minor=L.FORMAT_MINOR, manifest_offset=manifest_offset,
                  manifest_size=len(manifest), pack_size=pack_size, manifest_sha256=sha256(manifest),
                  manifest_fast=fast_digest(fast_algo, manifest))
    raw = L.FOOTER.pack(**values)
    return L.FOOTER.pack(**values, crc32=zlib.crc32(raw[:L.FOOTER_CRC_SPAN]))


def sidecar_path(pack: str | os.PathLike) -> Path:
    return Path(str(pack) + ".manifest")


# --------------------------------------------------------------------------- the writer


@dataclass
class _Content:
    index: int
    sha: bytes
    fast: bytes
    size: int
    kind: int
    ext: str
    rw_version: int
    chunks: list[tuple[bytes, bytes]]
    path: Path
    names: list[str] = field(default_factory=list)
    offset: int = 0
    first_chunk: int = 0


def _read_source(src: Source) -> bytes:
    try:
        with open_ro(src.path) as f:
            return f.read()
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read {jpath(src.path)}: {e}") from None


def _check_chunk_size(chunk_size: int) -> int:
    c = int(chunk_size)
    if c < L.MIN_CHUNK or c > L.MAX_CHUNK or c & (c - 1) or c % L.SECTOR:
        raise SatkError("BAD_PARAMS", f"chunk size must be a power of two from {L.MIN_CHUNK} to {L.MAX_CHUNK} bytes, "
                                      f"got {chunk_size}", hint="--chunk-kib 64")
    return c


def build_pack(sources: list[Source], out: str | os.PathLike, *, namespace: str = "", title: str = "",
               mount_name: str = "", mount_mode: str = "overlay", priority: int = 0,
               chunk_size: int = L.DEFAULT_CHUNK, fast: str = "none", dedup_ok: bool = True,
               local_only: bool = False, sidecar: bool = True,
               progress: Callable[[float, float | None, str | None], None] | None = None) -> dict:
    """Write a pack from ``sources`` (sorted by logical name) to ``out``; returns the build report.

    The pack file and the manifest-only sidecar are written atomically (a temp file beside the target, then
    ``os.replace``); sources are only read.
    """
    if mount_mode not in L.MOUNT_MODES:
        raise SatkError("BAD_PARAMS", f"mount mode must be one of {', '.join(L.MOUNT_MODES)}, got {mount_mode!r}")
    if fast not in ("none", "xxh3"):
        raise SatkError("BAD_PARAMS", f"fast must be none or xxh3, got {fast!r}")
    if not -(2 ** 31) <= int(priority) < 2 ** 31:
        raise SatkError("BAD_PARAMS", f"priority {priority} does not fit a 32-bit integer")
    csize = _check_chunk_size(chunk_size)
    algo = L.FAST_XXH3_128 if fast == "xxh3" else L.FAST_NONE
    if algo:
        fast_digest(algo, b"")        # DEPENDENCY error now, not after the hashing pass
    ns = namespace.strip()
    if "\n" in ns or NUL.decode() in ns:
        raise SatkError("BAD_PARAMS", "the namespace must be a single line of text")
    if not sources:
        raise SatkError("BAD_PARAMS", "nothing to pack")
    srcs = sorted(sources, key=lambda s: s.logical.encode("utf-8"))
    for s in srcs:
        why = logical_problem(s.logical)
        if why:
            raise SatkError("BAD_PARAMS", f"logical name {s.logical!r}: {why}")
    out_path = ensure_writable(out)
    warn: list[str] = []

    # -- pass 1: hash every source once (one file in memory at a time)
    contents: dict[bytes, _Content] = {}
    order: list[_Content] = []
    name_content: dict[str, _Content] = {}
    for i, s in enumerate(srcs):
        if progress:
            progress(i, len(srcs), f"hashing {s.logical}")
        data = _read_source(s)
        if not data:
            raise SatkError("BAD_PARAMS", f"{jpath(s.path)} is empty", hint="an empty file cannot be streamed")
        if len(data) > L.MAX_ENTRY_BYTES:
            raise SatkError("BAD_PARAMS", f"{jpath(s.path)}: {len(data)} bytes exceed the IMG limit of "
                                          f"{L.MAX_ENTRY_SECTORS} sectors ({L.MAX_ENTRY_BYTES} bytes)",
                            hint="split the asset: one IMG entry holds at most 128 MiB")
        digest = sha256(data)
        c = contents.get(digest)
        if c is None:
            ext = s.logical.rsplit(".", 1)[-1]
            ver, notes = rw_info(ext, data)
            for n in notes:
                warn.append(f"BAD_HEADER: {s.logical}: {n}")
            chunks = [(sha256(data[o:o + n]), fast_digest(algo, data[o:o + n])) for o, n in chunk_ranges(len(data), csize)]
            c = _Content(len(order), digest, fast_digest(algo, data), len(data), L.KIND_BY_EXT[ext], ext, ver, chunks,
                         s.path)
            contents[digest] = c
            order.append(c)
        c.names.append(s.logical)
        name_content[s.logical] = c
    # entries sorted by their lowest logical name (names are already sorted)
    order.sort(key=lambda c: c.names[0].encode("utf-8"))
    for i, c in enumerate(order):
        c.index = i

    # -- stream names and IMG directory slots
    used: dict[str, int] = {}                 # stream name -> content index
    slot_of_name: dict[str, tuple[str, bool]] = {}
    for s in srcs:
        c = name_content[s.logical]
        sname, derived = stream_name_for(s.logical, c.ext, c.sha.hex())
        if not derived and used.get(sname, c.index) != c.index:
            sname, derived = derived_stream_name(c.ext, c.sha.hex()), True   # another content owns the plain name
        other = used.get(sname)
        if other is not None and other != c.index:
            raise SatkError("INTERNAL", f"stream name collision on {sname}")
        used[sname] = c.index
        slot_of_name[s.logical] = (sname, derived)
    slot_names = sorted(used)
    slot_index = {n: i for i, n in enumerate(slot_names)}
    dir_count = len(slot_names)
    dir_bytes = 8 + L.IMG_ENTRY.size * dir_count
    data_offset = -(-dir_bytes // L.SECTOR) * L.SECTOR

    # -- payload layout and chunk table
    off = data_offset
    chunk_table: list[tuple[bytes, bytes]] = []
    for c in order:
        c.offset = off
        c.first_chunk = len(chunk_table)
        chunk_table += c.chunks
        off += -(-c.size // L.SECTOR) * L.SECTOR
    data_size = off - data_offset
    manifest_offset = off

    # -- manifest tables
    st = StringTable()
    ns_off, ns_len = st.add(ns)
    ti_off, ti_len = st.add(title)
    mn_off, mn_len = st.add(mount_name)
    entry_rows = [dict(sha256=c.sha, fast=c.fast, offset=c.offset, size=c.size, first_chunk=c.first_chunk,
                       chunk_count=len(c.chunks), kind=c.kind, stream_sectors=-(-c.size // L.SECTOR),
                       rw_version=c.rw_version) for c in order]
    name_rows = []
    for s in srcs:
        c = name_content[s.logical]
        sname, derived = slot_of_name[s.logical]
        lo, ll = st.add(s.logical)
        name_rows.append(dict(entry_index=c.index, dir_index=slot_index[sname], logical_off=lo, logical_len=ll,
                              flags=L.NAME_DERIVED if derived else 0))
    pack_id = compute_pack_id(ns, ((s.logical, name_content[s.logical].sha) for s in srcs))
    res_flags = (L.RES_DEDUP_OK if dedup_ok else 0) | (L.RES_LOCAL_ONLY if local_only else 0)
    header = dict(flags=1, chunk_size=csize, fast_algo=algo, dir_count=dir_count, data_offset=data_offset,
                  data_size=data_size, pack_id=pack_id, priority=int(priority), mount_mode=L.MOUNT_MODES[mount_mode],
                  residency_flags=res_flags, namespace_off=ns_off, namespace_len=ns_len, title_off=ti_off,
                  title_len=ti_len, mount_name_off=mn_off, mount_name_len=mn_len)
    manifest = encode_manifest(header=header, entries=entry_rows, names=name_rows, chunks=chunk_table,
                               strings=bytes(st.buf))
    pack_size = manifest_offset + len(manifest) + L.FOOTER.size
    if pack_size > L.MAX_PACK_BYTES:
        raise SatkError("BAD_PARAMS", f"the pack would be {pack_size} bytes; a mounted IMG stays under "
                                      f"{L.MAX_PACK_BYTES} bytes (4 GiB)", hint="split the inputs into several packs")
    footer = encode_footer(manifest=manifest, manifest_offset=manifest_offset, pack_size=pack_size, fast_algo=algo)

    # -- the IMG directory
    by_slot: dict[str, _Content] = {}
    for s in srcs:
        by_slot[slot_of_name[s.logical][0]] = name_content[s.logical]
    img = [L.IMG_MAGIC + struct.pack("<I", dir_count)]
    for n in slot_names:
        c = by_slot[n]
        img.append(L.IMG_ENTRY.pack(offset_sectors=c.offset // L.SECTOR, stream_sectors=-(-c.size // L.SECTOR),
                                    name=n.encode("ascii")))
    head = b"".join(img)
    head += bytes(data_offset - len(head))

    # -- write: temp file beside the target, then replace
    src_of = {s.logical: s for s in srcs}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(head)
            for i, c in enumerate(order):
                if progress:
                    progress(i, len(order), f"writing {c.names[0]}")
                src = src_of[c.names[0]]
                data = _read_source(src)
                if sha256(data) != c.sha:
                    raise SatkError("REVISION", f"{jpath(src.path)} changed while the pack was being written",
                                    hint="run the build again when the files are stable")
                f.write(data)
                f.write(bytes(-len(data) % L.SECTOR))
            f.write(manifest)
            f.write(footer)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, out_path)
        side = None
        if sidecar:
            side = sidecar_path(out_path)
            ensure_writable(side)
            tmp2 = side.with_name(f".{side.name}.{os.getpid()}.tmp")
            try:
                tmp2.write_bytes(manifest + footer)
                _replace(tmp2, side)
            finally:
                tmp2.unlink(missing_ok=True)
    finally:
        tmp.unlink(missing_ok=True)
    content_bytes = sum(c.size for c in order)
    named_bytes = sum(name_content[s.logical].size for s in srcs)
    return {
        "out": out_path, "sidecar": side, "pack_id": pack_id.hex(), "names": len(srcs), "entries": len(order),
        "dir_slots": dir_count, "chunks": len(chunk_table), "chunk_size": csize, "bytes": pack_size,
        "payload_bytes": data_size, "content_bytes": content_bytes, "named_bytes": named_bytes,
        "dedup_saved": named_bytes - content_bytes, "manifest_bytes": len(manifest), "derived_stream_names":
        sum(1 for s in srcs if slot_of_name[s.logical][1]), "warn": warn,
    }


def _replace(tmp: Path, dst: Path) -> None:
    import time

    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.02)


# --------------------------------------------------------------------------- the reader


class Pack:
    """An opened pack (or manifest-only file). Use :meth:`open`; it is a context manager.

    Attributes: ``header`` / ``footer`` (decoded records), ``entries`` / ``names`` (lists of dicts), ``chunks``
    (``(sha256, fast)`` tuples), ``has_payload`` (False for a manifest-only file), ``file_size``,
    ``manifest_hash_ok``.
    """

    def __init__(self, path: Path, fh: BinaryIO, file_size: int, footer: dict, manifest: bytes, has_payload: bool):
        self.path = path
        self._fh: BinaryIO | None = fh
        self.file_size = file_size
        self.footer = footer
        self.manifest = manifest
        self.has_payload = has_payload
        self.manifest_hash_ok = sha256(manifest) == footer["manifest_sha256"]
        self._parse()

    # -- opening
    @classmethod
    def open(cls, path: str | os.PathLike, *, check_manifest_hash: bool = True) -> "Pack":
        p = Path(path)
        fh = open_ro(p)
        try:
            fh.seek(0, 2)
            size = fh.tell()
            if size < L.FOOTER.size + L.HEADER.size:
                raise PackError("BAD_MAGIC", f"{size} bytes is too small for a pack footer and manifest")
            fh.seek(size - L.FOOTER.size)
            raw = fh.read(L.FOOTER.size)
            if raw[:8] != L.FOOTER_MAGIC:
                raise PackError("BAD_MAGIC", "no SAEPAKFT footer at the end of the file", "footer")
            ft = L.FOOTER.unpack(raw)
            if zlib.crc32(raw[:L.FOOTER_CRC_SPAN]) != ft["crc32"]:
                raise PackError("FOOTER_CRC", "the footer CRC does not match", "footer")
            if ft["major"] != L.FORMAT_MAJOR:
                raise PackError("BAD_VERSION", f"format {ft['major']}.{ft['minor']}; this reader knows "
                                               f"{L.FORMAT_MAJOR}.x", "footer")
            msize = ft["manifest_size"]
            if size == ft["pack_size"]:
                has_payload = True
                if ft["manifest_offset"] + msize + L.FOOTER.size != size or ft["manifest_offset"] % L.SECTOR:
                    raise PackError("MANIFEST_RANGE", "the manifest does not end at the footer or is not sector "
                                                      "aligned", "footer")
                mstart = ft["manifest_offset"]
            elif size == msize + L.FOOTER.size:
                has_payload = False
                mstart = 0
            else:
                raise PackError("SIZE_MISMATCH", f"file is {size} bytes; the footer says a pack of "
                                                 f"{ft['pack_size']} or a manifest-only file of "
                                                 f"{msize + L.FOOTER.size}", "footer")
            if msize < L.HEADER.size or msize % 8 or msize > MAX_MANIFEST:
                raise PackError("MANIFEST_RANGE", f"manifest size {msize} is invalid", "footer")
            fh.seek(mstart)
            manifest = fh.read(msize)
            if len(manifest) != msize:
                raise PackError("MANIFEST_RANGE", "short read of the manifest", "manifest")
            if check_manifest_hash and sha256(manifest) != ft["manifest_sha256"]:
                raise PackError("MANIFEST_HASH", "the manifest SHA-256 does not match the footer", "manifest")
            return cls(p, fh, size, ft, manifest, has_payload)
        except BaseException:
            fh.close()
            raise

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "Pack":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- manifest parsing
    def _parse(self) -> None:
        buf = self.manifest
        if buf[:8] != L.MANIFEST_MAGIC:
            raise PackError("MANIFEST_FORMAT", "the manifest does not start with SAEMANIF", "manifest")
        h = L.HEADER.unpack(buf[:L.HEADER.size])
        self.header = h
        if h["major"] != L.FORMAT_MAJOR:
            raise PackError("BAD_VERSION", f"manifest format {h['major']}.{h['minor']}", "manifest")
        if (h["major"], h["minor"]) != (self.footer["major"], self.footer["minor"]):
            raise PackError("MANIFEST_FORMAT", "footer and manifest versions differ", "manifest")
        if h["header_size"] < L.HEADER.size or h["header_size"] > len(buf):
            raise PackError("MANIFEST_FORMAT", f"header_size {h['header_size']} is invalid", "header")
        if h["sector_size"] != L.SECTOR:
            raise PackError("MANIFEST_FORMAT", f"sector_size {h['sector_size']} is not {L.SECTOR}", "header")
        c = h["chunk_size"]
        if c < L.MIN_CHUNK or c > L.MAX_CHUNK or c & (c - 1):
            raise PackError("MANIFEST_FORMAT", f"chunk_size {c} is invalid", "header")
        if h["fast_algo"] not in (L.FAST_NONE, L.FAST_XXH3_128):
            raise PackError("MANIFEST_FORMAT", f"unknown fast_algo {h['fast_algo']}", "header")
        for count, rec, key in ((h["entry_count"], L.ENTRY, "entries_off"), (h["name_count"], L.NAME, "names_off"),
                                (h["chunk_count"], L.CHUNK, "chunks_off")):
            off = h[key]
            if count > (1 << 26) or off < h["header_size"] or off % 8 or off + count * rec.size > len(buf):
                raise PackError("MANIFEST_FORMAT", f"the {rec.name} table does not fit the manifest", key)
        so, ss = h["strings_off"], h["strings_size"]
        if so < h["header_size"] or so + ss > len(buf):
            raise PackError("MANIFEST_FORMAT", "the string area does not fit the manifest", "strings_off")
        self.strings = bytes(buf[so:so + ss])
        self.entries = [self._rec(L.ENTRY, h["entries_off"], i) for i in range(h["entry_count"])]
        self.names = [self._rec(L.NAME, h["names_off"], i) for i in range(h["name_count"])]
        co = h["chunks_off"]
        self.chunks = [(bytes(buf[co + i * 48:co + i * 48 + 32]), bytes(buf[co + i * 48 + 32:co + i * 48 + 48]))
                       for i in range(h["chunk_count"])]
        for n in self.names:
            n["logical"] = self.string(n["logical_off"], n["logical_len"])

    def _rec(self, rec: L.Record, base: int, i: int) -> dict:
        return rec.unpack(self.manifest, base + i * rec.size)

    def string(self, off: int, length: int) -> str:
        if off + length > len(self.strings):
            raise PackError("MANIFEST_FORMAT", f"string ({off}, {length}) is outside the string area", "strings")
        try:
            return self.strings[off:off + length].decode("utf-8")
        except UnicodeDecodeError:
            raise PackError("MANIFEST_FORMAT", f"string at {off} is not UTF-8", "strings") from None

    @property
    def namespace(self) -> str:
        return self.string(self.header["namespace_off"], self.header["namespace_len"])

    @property
    def title(self) -> str:
        return self.string(self.header["title_off"], self.header["title_len"])

    @property
    def mount_name(self) -> str:
        return self.string(self.header["mount_name_off"], self.header["mount_name_len"])

    @property
    def mount_mode(self) -> str:
        inv = {v: k for k, v in L.MOUNT_MODES.items()}
        return inv.get(self.header["mount_mode"], f"unknown({self.header['mount_mode']})")

    # -- lookups and reads
    def name_index(self, logical: str) -> int | None:
        key = logical.lower().replace("\\", "/")
        if not hasattr(self, "_by_name"):
            self._by_name = {n["logical"]: i for i, n in enumerate(self.names)}
        return self._by_name.get(key)

    def read_at(self, offset: int, size: int) -> bytes:
        if not self.has_payload or self._fh is None:
            raise PackError("NO_PAYLOAD", "this is a manifest-only file or a closed pack")
        if offset < 0 or size < 0 or offset + size > self.file_size:
            raise PackError("SIZE_MISMATCH", f"read of {size} bytes at {offset} is outside the file")
        self._fh.seek(offset)
        data = self._fh.read(size)
        if len(data) != size:
            raise PackError("SIZE_MISMATCH", f"short read at {offset}")
        return data

    def read_entry(self, index: int) -> bytes:
        e = self.entries[index]
        return self.read_at(e["offset"], e["size"])

    def read_name(self, logical: str) -> bytes:
        i = self.name_index(logical)
        if i is None:
            raise KeyError(logical)
        return self.read_entry(self.names[i]["entry_index"])

    def chunk_plan(self, index: int) -> list[tuple[int, int, bytes]]:
        """``(absolute offset, length, sha256)`` of every chunk of an entry: what a ranged fetcher requests and checks."""
        e = self.entries[index]
        cs = self.header["chunk_size"]
        plan = []
        for k, (o, n) in enumerate(chunk_ranges(e["size"], cs)):
            plan.append((e["offset"] + o, n, self.chunks[e["first_chunk"] + k][0]))
        return plan

    def img_directory(self) -> list[dict]:
        """The IMG VER2 directory at the file start as ``{offset_sectors, stream_sectors, archive_sectors, name}``."""
        head = self.read_at(0, 8)
        if head[:4] != L.IMG_MAGIC:
            raise PackError("DIR_MISMATCH", "the file does not start with VER2", "img")
        (count,) = struct.unpack_from("<I", head, 4)
        if count != self.header["dir_count"]:
            raise PackError("DIR_MISMATCH", f"the IMG directory has {count} slots, the manifest says "
                                            f"{self.header['dir_count']}", "img")
        if 8 + count * 32 > self.header["data_offset"]:
            raise PackError("DIR_MISMATCH", "the IMG directory is longer than the space before the payload", "img")
        raw = self.read_at(8, count * 32)
        out = []
        for i in range(count):
            d = L.IMG_ENTRY.unpack(raw, i * 32)
            d["name"] = d["name"].split(NUL, 1)[0].decode("latin-1")
            out.append(d)
        return out

    def describe(self) -> dict:
        h = self.header
        kinds: dict[str, int] = {}
        for n in self.names:
            e = self.entries[n["entry_index"]] if n["entry_index"] < len(self.entries) else None
            if e is not None:
                k = L.KINDS.get(e["kind"], f"kind{e['kind']}")
                kinds[k] = kinds.get(k, 0) + 1
        content = sum(e["size"] for e in self.entries)
        named = sum(self.entries[n["entry_index"]]["size"] for n in self.names if n["entry_index"] < len(self.entries))
        return {
            "format": f"{h['major']}.{h['minor']}", "pack_id": h["pack_id"].hex(), "namespace": self.namespace,
            "title": self.title, "mount_name": self.mount_name, "mount_mode": self.mount_mode,
            "priority": h["priority"], "dedup_ok": bool(h["residency_flags"] & L.RES_DEDUP_OK),
            "local_only": bool(h["residency_flags"] & L.RES_LOCAL_ONLY), "names": len(self.names),
            "entries": len(self.entries), "dir_slots": h["dir_count"], "chunks": len(self.chunks),
            "chunk_size": h["chunk_size"], "fast_algo": "xxh3-128" if h["fast_algo"] else "none",
            "kinds": dict(sorted(kinds.items())), "content_bytes": content, "named_bytes": named,
            "dedup_saved": named - content, "payload_bytes": h["data_size"], "manifest_bytes": len(self.manifest),
            "file_bytes": self.file_size, "manifest_only": not self.has_payload,
        }


# --------------------------------------------------------------------------- the verifier


def verify_pack(path: str | os.PathLike, *, deep: bool = True,
                progress: Callable[[float, float | None, str | None], None] | None = None) -> tuple[list[Finding], dict]:
    """Check a pack; returns ``(findings, stats)``. ``deep`` hashes every entry and chunk (otherwise structure only).

    A manifest-only file is checked structurally. Findings of severity ``error`` mean the pack must not be mounted.
    """
    fs: list[Finding] = []

    def add(sev: str, code: str, where: str, msg: str) -> None:
        fs.append(Finding(sev, code, where, msg))

    try:
        pk = Pack.open(path, check_manifest_hash=False)
    except PackError as e:
        add("error", e.code, e.where or "file", e.msg)
        return fs, {}
    except OSError as e:
        add("error", "UNREADABLE", "file", str(e))
        return fs, {}
    stats: dict = {}
    try:
        with pk:
            h = pk.header
            if not pk.manifest_hash_ok:
                add("error", "MANIFEST_HASH", "manifest", "the manifest SHA-256 does not match the footer")
            for nm in L.FOOTER.reserved_nonzero(pk.footer):
                add("warn", "RESERVED", "footer", f"reserved field {nm} is not zero")
            for nm in L.HEADER.reserved_nonzero(h):
                add("warn", "RESERVED", "header", f"reserved field {nm} is not zero")
            if h["flags"] & 1 == 0:
                add("error", "MANIFEST_FORMAT", "header", "flags bit 0 (IMG directory present) is not set")
            # xxhash available for a fast-hash pack?
            fast_ok = True
            if h["fast_algo"]:
                try:
                    fast_digest(h["fast_algo"], b"")
                except SatkError:
                    fast_ok = False
                    add("info", "FAST_SKIPPED", "header", "the pack carries XXH3-128 hashes; install xxhash to check them")
            if fast_ok and h["fast_algo"] and fast_digest(h["fast_algo"], pk.manifest) != pk.footer["manifest_fast"]:
                add("error", "FAST_HASH", "manifest", "the manifest XXH3-128 does not match the footer")

            nentries, ncs = len(pk.entries), len(pk.chunks)
            cs = h["chunk_size"]
            # -- names
            seen: dict[str, int] = {}
            prev = None
            for i, n in enumerate(pk.names):
                where = f"name {i}"
                try:
                    name = n["logical"]
                except KeyError:
                    continue
                for nm in L.NAME.reserved_nonzero(n):
                    add("warn", "RESERVED", where, f"reserved field {nm} is not zero")
                why = logical_problem(name)
                if why:
                    add("error", "NAME_INVALID", where, f"{name!r}: {why}")
                if name in seen:
                    add("error", "NAME_DUP", where, f"{name!r} also at name {seen[name]}")
                seen[name] = i
                key = name.encode("utf-8")
                if prev is not None and key < prev:
                    add("warn", "NAME_ORDER", where, "names are not sorted by logical name")
                prev = key
                if n["entry_index"] >= nentries:
                    add("error", "NAME_ENTRY", where, f"{name!r}: entry index {n['entry_index']} is out of range")
                elif "." in name:
                    e = pk.entries[n["entry_index"]]
                    if L.KIND_BY_EXT.get(name.rsplit(".", 1)[-1], 0) != e["kind"]:
                        add("warn", "KIND_MISMATCH", where, f"{name!r}: extension and entry kind {e['kind']} disagree")
                if n["dir_index"] >= h["dir_count"]:
                    add("error", "NAME_DIR", where, f"{name!r}: directory slot {n['dir_index']} is out of range")
            # -- entries and the chunk table
            expect_chunk = 0
            spans = []
            sha_seen: dict[bytes, int] = {}
            for i, e in enumerate(pk.entries):
                where = f"entry {i}"
                for nm in L.ENTRY.reserved_nonzero(e):
                    add("warn", "RESERVED", where, f"reserved field {nm} is not zero")
                if e["flags"]:
                    add("warn", "RESERVED", where, "flags are not zero")
                if e["sha256"] in sha_seen:
                    add("error", "ENTRY_DUP", where, f"same SHA-256 as entry {sha_seen[e['sha256']]}")
                sha_seen[e["sha256"]] = i
                size = e["size"]
                if size < 1 or size > L.MAX_ENTRY_BYTES:
                    add("error", "ENTRY_SIZE", where, f"size {size} is outside 1 .. {L.MAX_ENTRY_BYTES}")
                    continue
                sectors = -(-size // L.SECTOR)
                if e["stream_sectors"] != sectors:
                    add("error", "ENTRY_SIZE", where, f"stream_sectors {e['stream_sectors']} should be {sectors}")
                if e["offset"] % L.SECTOR:
                    add("error", "ALIGN", where, f"offset {e['offset']} is not sector aligned")
                if e["offset"] < h["data_offset"] or e["offset"] + sectors * L.SECTOR > h["data_offset"] + h["data_size"]:
                    add("error", "ENTRY_RANGE", where, "the payload lies outside the payload region")
                spans.append((e["offset"], e["offset"] + sectors * L.SECTOR, i))
                want = -(-size // cs)
                if e["chunk_count"] != want:
                    add("error", "CHUNK_RANGE", where, f"chunk_count {e['chunk_count']} should be {want}")
                if e["first_chunk"] != expect_chunk:
                    add("error", "CHUNK_RANGE", where, f"first_chunk {e['first_chunk']} should be {expect_chunk} "
                                                       "(entries must tile the chunk table in order)")
                expect_chunk = e["first_chunk"] + e["chunk_count"]
                if e["first_chunk"] + e["chunk_count"] > ncs:
                    add("error", "CHUNK_RANGE", where, "the chunk range runs past the chunk table")
            if expect_chunk != ncs:
                add("error", "CHUNK_RANGE", "chunks", f"{expect_chunk} chunk records are used, the table has {ncs}")
            spans.sort()
            for (a0, a1, ai), (b0, _b1, bi) in zip(spans, spans[1:]):
                if b0 < a1:
                    add("error", "OVERLAP", f"entry {bi}", f"overlaps entry {ai}")
                elif b0 > a1:
                    add("warn", "GAP", f"entry {bi}", f"{b0 - a1} unused bytes before this payload")
            if spans and spans[0][0] != h["data_offset"]:
                add("warn", "GAP", "payload", "the first payload does not start at data_offset")
            # -- pack id
            try:
                pid = compute_pack_id(pk.namespace, ((n["logical"], pk.entries[n["entry_index"]]["sha256"])
                                                    for n in pk.names if n["entry_index"] < nentries))
                if pid != h["pack_id"]:
                    add("error", "PACK_ID", "header", "pack_id does not match the namespace and the name -> content map")
            except PackError as e:
                add("error", e.code, e.where, e.msg)
            stats = {"names": len(pk.names), "entries": nentries, "chunks": ncs, "bytes": pk.file_size}
            if not pk.has_payload:
                add("info", "MANIFEST_ONLY", "file", "a manifest-only file: payload, IMG directory and hashes not checked")
                return fs, stats
            # -- the IMG directory
            try:
                slots = pk.img_directory()
            except PackError as e:
                add("error", e.code, e.where, e.msg)
                slots = []
            if slots and not any(f.code == "DIR_MISMATCH" for f in fs):
                lowered: dict[str, int] = {}
                referenced = set()
                for i, s in enumerate(slots):
                    nm = s["name"]
                    if nm.lower() in lowered:
                        add("error", "STREAM_NAME_DUP", f"slot {i}", f"{nm!r} also at slot {lowered[nm.lower()]}")
                    lowered[nm.lower()] = i
                    if len(nm) > L.STREAM_NAME_MAX or not nm:
                        add("error", "STREAM_NAME", f"slot {i}", f"stream name {nm!r} is empty or longer than "
                                                                 f"{L.STREAM_NAME_MAX}")
                    if s["archive_sectors"]:
                        add("warn", "DIR_MISMATCH", f"slot {i}", "archive_sectors is not 0")
                for i, n in enumerate(pk.names):
                    if n["entry_index"] >= nentries or n["dir_index"] >= len(slots):
                        continue
                    referenced.add(n["dir_index"])
                    s, e = slots[n["dir_index"]], pk.entries[n["entry_index"]]
                    if s["offset_sectors"] * L.SECTOR != e["offset"] or s["stream_sectors"] != e["stream_sectors"]:
                        add("error", "DIR_MISMATCH", f"name {i}", f"{n['logical']!r}: slot {n['dir_index']} "
                                                                  f"({s['name']}) points elsewhere than its entry")
                    derived = bool(n["flags"] & L.NAME_DERIVED)
                    base = n["logical"].rsplit("/", 1)[-1]
                    if derived and not s["name"].startswith("~"):
                        add("error", "STREAM_NAME", f"name {i}", f"{n['logical']!r}: derived name expected, slot is "
                                                                 f"{s['name']!r}")
                    if not derived and s["name"] != base:
                        add("error", "STREAM_NAME", f"name {i}", f"{n['logical']!r}: slot is {s['name']!r}, expected "
                                                                 f"{base!r}")
                for i in range(len(slots)):
                    if i not in referenced:
                        add("warn", "DIR_ORPHAN", f"slot {i}", f"{slots[i]['name']!r} is not used by any logical name")
                if 8 + len(slots) * 32 > h["data_offset"] or h["data_offset"] % L.SECTOR:
                    add("error", "ALIGN", "header", "data_offset is not the sector after the IMG directory")
                pad_dir = pk.read_at(8 + len(slots) * 32, h["data_offset"] - 8 - len(slots) * 32) if deep else b""
                if any(pad_dir):
                    add("warn", "PADDING", "img", "the IMG directory padding is not zero")
            if pk.file_size > L.MAX_PACK_BYTES:
                add("warn", "TOO_LARGE", "file", f"{pk.file_size} bytes: an IMG of 4 GiB or more cannot be mounted by "
                                                 "the engine")
            if h["data_offset"] + h["data_size"] != pk.footer["manifest_offset"]:
                add("error", "MANIFEST_RANGE", "header", "the payload region does not end at the manifest")
            if not deep:
                return fs, stats
            # -- payload: every entry and chunk
            first_name: dict[int, str] = {}
            for n in pk.names:
                first_name.setdefault(n["entry_index"], n["logical"])
            for k, (_o0, _o1, i) in enumerate(spans):
                e = pk.entries[i]
                if progress:
                    progress(k, len(spans), f"hashing entry {i}")
                try:
                    data = pk.read_entry(i)
                except PackError as ex:
                    add("error", ex.code, f"entry {i}", ex.msg)
                    continue
                label = first_name.get(i, f"entry {i}")
                if sha256(data) != e["sha256"]:
                    add("error", "ENTRY_HASH", f"entry {i}", f"{label}: SHA-256 differs from the manifest")
                elif fast_ok and h["fast_algo"] and fast_digest(h["fast_algo"], data) != e["fast"]:
                    add("error", "FAST_HASH", f"entry {i}", f"{label}: XXH3-128 differs from the manifest")
                if e["first_chunk"] + e["chunk_count"] <= ncs and e["chunk_count"] == -(-e["size"] // cs):
                    for kidx, (o, n) in enumerate(chunk_ranges(e["size"], cs)):
                        sha_c, fast_c = pk.chunks[e["first_chunk"] + kidx]
                        if sha256(data[o:o + n]) != sha_c:
                            add("error", "CHUNK_HASH", f"entry {i}", f"{label}: chunk {kidx} (bytes {o}..{o + n}) differs")
                            break
                        if fast_ok and h["fast_algo"] and fast_digest(h["fast_algo"], data[o:o + n]) != fast_c:
                            add("error", "FAST_HASH", f"entry {i}", f"{label}: chunk {kidx} XXH3-128 differs")
                            break
                padn = -e["size"] % L.SECTOR
                if padn and any(pk.read_at(e["offset"] + e["size"], padn)):
                    add("warn", "PADDING", f"entry {i}", f"{label}: the sector padding is not zero")
    except PackError as e:        # a damaged pack can fail anywhere a table or an offset is trusted
        add("error", e.code, e.where or "payload", e.msg)
    return fs, stats
