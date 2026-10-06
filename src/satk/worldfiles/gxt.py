"""GTA SA GXT (version 4) documents: read with the string order kept, write bit-exact. Stdlib only.

Layout written (the reader of the game and :mod:`satk.formats.gxt` agree; checked on the five vanilla
``text/*.gxt``, which this writer reproduces byte for byte from their own documents)::

    u16 version = 4, u16 bits per char (8 or 16)
    "TABL" u32 size  { char name[8] (zero padded), u32 offset } x tables      MAIN first
    per table, each starting on a 4-byte boundary (zero padding before it):
        [char name[8]]           every table except MAIN repeats its name
        "TKEY" u32 size { u32 offset in TDAT, u32 JAMCRC32(upper-case key) }   sorted by hash (binary search)
        "TDAT" u32 size  zero-terminated strings in document order

A document (:class:`GxtDoc`) is a list of tables, each an ordered list of ``(key, text)``; ``key`` is the key
name (``CRED001``) or, when the name is unknown, its hash written ``0x1A2B3C4D``. The string order of TDAT is
the entry order, so reading and writing a file gives the same bytes. JSON form::

    {"format": "satk-gxt/1", "bits": 8, "charset": "gta",
     "tables": [{"name": "MAIN", "entries": [["CRED001", "Text"], ["0x0001A2B3", "..."]]}, ...]}
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field

from . import charset as CS

__all__ = ["FORMAT", "GxtDoc", "GxtTable", "key_hash", "parse_key", "read_gxt", "write_gxt", "doc_from_json",
           "doc_to_json", "parse_text_table", "format_text_table", "TABLE_NAME_MAX", "KEY_LABEL_MAX"]

FORMAT = "satk-gxt/1"
#: Table names live in ``char[8]`` fields.
TABLE_NAME_MAX = 7
#: SCM text-label parameters and ``CZone``/``CVehicleModelInfo`` labels hold 7 characters + NUL.
KEY_LABEL_MAX = 7
_HASH_KEY = re.compile(r"^0[xX]([0-9A-Fa-f]{1,8})$")
_KEY = re.compile(r"^[!-~]+$")
MAX_TABLES = 4096
MAX_SIZE = 64 << 20


def key_hash(key: str) -> int:
    """JAMCRC32 of the upper-case key (CRC-32 without the final inversion), as SA stores keys."""
    return zlib.crc32(key.upper().encode("latin-1")) ^ 0xFFFFFFFF


def parse_key(key: str) -> tuple[int, str | None]:
    """``(hash, name)`` of a document key: ``0x1A2B3C4D`` is a bare hash (name ``None``)."""
    k = key.strip()
    m = _HASH_KEY.match(k)
    if m:
        return int(m.group(1), 16), None
    if not k or not _KEY.match(k):
        raise ValueError(f"bad GXT key {key!r}: printable ASCII without spaces expected")
    try:
        k.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(f"bad GXT key {key!r}: ASCII expected") from None
    return key_hash(k), k.upper()


def hash_key(h: int) -> str:
    return f"0x{h:08X}"


@dataclass
class GxtTable:
    name: str
    entries: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class GxtDoc:
    bits: int = 8
    charset: str = "gta"
    tables: list[GxtTable] = field(default_factory=list)

    def table(self, name: str) -> GxtTable | None:
        up = name.upper()
        return next((t for t in self.tables if t.name.upper() == up), None)

    def find(self, key: str) -> list[tuple[str, int, str]]:
        """``[(table, entry index, text)]`` of a key name or ``0x`` hash in every table."""
        h, _n = parse_key(key)
        out = []
        for t in self.tables:
            for i, (k, txt) in enumerate(t.entries):
                if parse_key(k)[0] == h:
                    out.append((t.name, i, txt))
        return out

    def count(self) -> int:
        return sum(len(t.entries) for t in self.tables)


class GxtError(ValueError):
    """A file or document problem; ``offset`` is a byte offset (files) or ``None``."""

    def __init__(self, msg: str, offset: int | None = None):
        super().__init__(msg)
        self.offset = offset


# --------------------------------------------------------------------------- reading


def _block(data: bytes, off: int, magic: bytes) -> tuple[int, int]:
    if data[off:off + 4] != magic:
        raise GxtError(f"expected {magic.decode()} block", off)
    if off + 8 > len(data):
        raise GxtError("truncated block header", off)
    size = struct.unpack_from("<I", data, off + 4)[0]
    if off + 8 + size > len(data):
        raise GxtError(f"{magic.decode()} block of {size} bytes exceeds the file", off)
    return off + 8, size


def _cstr(data: bytes, start: int, end: int, bits: int) -> bytes:
    if bits == 8:
        stop = data.find(b"\0", start, end)
        if stop < 0:
            raise GxtError("unterminated string", start)
        return data[start:stop]
    i = start
    while i + 1 < end and data[i:i + 2] != b"\0\0":
        i += 2
    if i + 1 >= end:
        raise GxtError("unterminated string", start)
    return data[start:i]


def read_gxt(data: bytes, names: dict[int, str] | None = None, charset: str = "gta") -> GxtDoc:
    """Parse an SA GXT into a document; keys are named from ``names`` (hash -> name) when known.

    Entries come in TDAT order (the order the strings are stored), which is what makes a write of the
    document reproduce the file. Two keys pointing at one string are kept as two entries (the writer then
    stores the string twice: :func:`write_gxt` notes it).
    """
    if len(data) > MAX_SIZE:
        raise GxtError(f"file too large ({len(data)} bytes)", 0)
    if len(data) < 8:
        raise GxtError("truncated header", 0)
    version, bits = struct.unpack_from("<HH", data, 0)
    if version != 4 or bits not in (8, 16):
        raise GxtError(f"unsupported GXT (version {version}, {bits} bits per char); only GTA SA (version 4) "
                       f"is supported", 0)
    names = names or {}
    tabl, tsize = _block(data, 4, b"TABL")
    if tsize % 12 or tsize // 12 > MAX_TABLES:
        raise GxtError(f"bad TABL size {tsize}", 4)
    doc = GxtDoc(bits=bits, charset=charset if bits == 8 else "utf-16")
    for i in range(tsize // 12):
        raw, off = struct.unpack_from("<8sI", data, tabl + i * 12)
        name = raw.split(b"\0", 1)[0].decode("latin-1")
        if name != "MAIN":
            if data[off:off + 8].split(b"\0", 1)[0] != raw.split(b"\0", 1)[0]:
                raise GxtError(f"table {name!r} does not start with its name", off)
            off += 8
        kstart, ksize = _block(data, off, b"TKEY")
        if ksize % 8:
            raise GxtError(f"bad TKEY size {ksize} in table {name!r}", off)
        dstart, dsize = _block(data, kstart + ksize, b"TDAT")
        keys = [struct.unpack_from("<II", data, kstart + k * 8) for k in range(ksize // 8)]
        order = sorted(range(len(keys)), key=lambda k: (keys[k][0], k))
        entries = []
        for k in order:
            toff, h = keys[k]
            if toff >= dsize:
                raise GxtError(f"text offset {toff} outside TDAT of table {name!r}", kstart + k * 8)
            raw_s = _cstr(data, dstart + toff, dstart + dsize, bits)
            text = CS.decode(raw_s, charset) if bits == 8 else raw_s.decode("utf-16-le", errors="surrogatepass")
            entries.append((names.get(h) or hash_key(h), text))
        doc.tables.append(GxtTable(name, entries))
    return doc


# --------------------------------------------------------------------------- writing


def _encode(text: str, doc: GxtDoc, where: str, folded: list[str]) -> bytes:
    if doc.bits == 16:
        if "\0" in text:
            raise GxtError(f"{where}: text contains a NUL character")
        return text.encode("utf-16-le", errors="surrogatepass") + b"\0\0"
    try:
        return CS.encode(text, doc.charset, folded) + b"\0"
    except ValueError as e:
        raise GxtError(f"{where}: {e}") from None


def write_gxt(doc: GxtDoc, warn: list[str] | None = None) -> bytes:
    """Bytes of an SA GXT for ``doc`` (see the module docstring for the layout)."""
    if doc.bits not in (8, 16):
        raise GxtError(f"bits must be 8 or 16, not {doc.bits}")
    if not doc.tables:
        raise GxtError("the document has no tables (a GXT needs at least MAIN)")
    if doc.tables[0].name.upper() != "MAIN":
        raise GxtError(f"the first table must be MAIN, not {doc.tables[0].name!r} (the game reads MAIN from the "
                       f"first TABL entry without a name)")
    seen_t: set[str] = set()
    folded: list[str] = []
    blobs: list[bytes] = []
    for t in doc.tables:
        nm = t.name
        if not nm or len(nm.encode("latin-1", errors="replace")) > TABLE_NAME_MAX or not _KEY.match(nm):
            raise GxtError(f"bad table name {nm!r}: 1..{TABLE_NAME_MAX} printable ASCII characters")
        if nm.upper() in seen_t:
            raise GxtError(f"table {nm!r} appears twice")
        if nm.upper() == "MAIN" and t is not doc.tables[0]:
            raise GxtError("MAIN must be the first table only")
        seen_t.add(nm.upper())
        tdat = bytearray()
        keys: list[tuple[int, int]] = []
        by_hash: dict[int, str] = {}
        for k, text in t.entries:
            try:
                h, name = parse_key(k)
            except ValueError as e:
                raise GxtError(f"table {nm}: {e}") from None
            if h in by_hash:
                other = by_hash[h]
                same = other.upper() == (name or k).upper()
                raise GxtError(f"table {nm}: key {k!r} " + ("appears twice" if same else
                               f"has the same hash 0x{h:08X} as {other!r}; rename one of them"))
            by_hash[h] = name or k
            keys.append((h, len(tdat)))
            tdat += _encode(text, doc, f"table {nm} key {k}", folded)
        keys.sort()
        tkey = b"".join(struct.pack("<II", off, h) for h, off in keys)
        body = b"TKEY" + struct.pack("<I", len(tkey)) + tkey + b"TDAT" + struct.pack("<I", len(tdat)) + bytes(tdat)
        if nm.upper() != "MAIN":
            body = nm.encode("latin-1").ljust(8, b"\0") + body
        blobs.append(body)
    head_len = 4 + 8 + 12 * len(doc.tables)
    tabl = bytearray()
    parts = []
    pos = head_len
    for t, body in zip(doc.tables, blobs):
        pad = (-pos) % 4
        parts.append(b"\0" * pad)
        pos += pad
        tabl += t.name.encode("latin-1").ljust(8, b"\0") + struct.pack("<I", pos)
        parts.append(body)
        pos += len(body)
    if warn is not None and folded:
        warn.append("CHARSET: replaced " + " ".join(repr(c) for c in folded) + " by ASCII (no glyph in the fonts)")
    return struct.pack("<HH", 4, doc.bits) + b"TABL" + struct.pack("<I", len(tabl)) + bytes(tabl) + b"".join(parts)


# --------------------------------------------------------------------------- JSON and text forms


def doc_to_json(doc: GxtDoc) -> dict:
    return {"format": FORMAT, "bits": doc.bits, "charset": doc.charset,
            "tables": [{"name": t.name, "entries": [[k, v] for k, v in t.entries]} for t in doc.tables]}


def _entries(raw, where: str) -> list[tuple[str, str]]:
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = []
        for i, e in enumerate(raw):
            if isinstance(e, (list, tuple)) and len(e) == 2:
                items.append((e[0], e[1]))
            elif isinstance(e, dict) and "key" in e and "text" in e:
                items.append((e["key"], e["text"]))
            else:
                raise GxtError(f"{where}: entry {i} must be [key, text] or {{key, text}}")
    else:
        raise GxtError(f"{where}: entries must be a list of [key, text] or an object key -> text")
    out = []
    for k, v in items:
        if not isinstance(k, str) or not isinstance(v, str):
            raise GxtError(f"{where}: keys and texts must be strings ({k!r})")
        out.append((k, v))
    return out


def doc_from_json(obj) -> GxtDoc:
    """A document from the JSON form; also accepts ``{"KEY": "text"}`` (one MAIN table)."""
    if not isinstance(obj, dict):
        raise GxtError("a GXT JSON document is an object")
    if "tables" not in obj:
        return GxtDoc(tables=[GxtTable("MAIN", _entries(obj, "MAIN"))])
    fmt = obj.get("format", FORMAT)
    if fmt != FORMAT:
        raise GxtError(f"unknown format {fmt!r}; expected {FORMAT}")
    bits = int(obj.get("bits", 8))
    cs = obj.get("charset", "gta" if bits == 8 else "utf-16")
    if bits == 8 and cs not in CS.CHARSETS:
        raise GxtError(f"unknown charset {cs!r}; expected one of {CS.CHARSETS}")
    doc = GxtDoc(bits=bits, charset=cs)
    tables = obj["tables"]
    if isinstance(tables, dict):
        tables = [{"name": k, "entries": v} for k, v in tables.items()]
    for i, t in enumerate(tables):
        if not isinstance(t, dict) or "name" not in t:
            raise GxtError(f"table {i} needs a name")
        doc.tables.append(GxtTable(str(t["name"]), _entries(t.get("entries", []), f"table {t['name']}")))
    return doc


def parse_text_table(text: str, where: str) -> list[tuple[str, str]]:
    """FXT-style lines ``KEY text`` (the first space separates; ``#`` lines and blank lines skipped)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        s = line.lstrip()
        k, sep, rest = s.partition(" ")
        if "\t" in k:
            k, _sep, rest2 = s.partition("\t")
            rest = rest2
        out.append((k, rest))
    return out


def format_text_table(entries: list[tuple[str, str]]) -> str:
    return "".join(f"{k} {v}\n" for k, v in entries)


def load_json(text: str) -> GxtDoc:
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise GxtError(f"not JSON: {e}") from None
    return doc_from_json(obj)
