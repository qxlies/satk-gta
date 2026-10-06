"""An MTA:SA resource folder: ``meta.xml`` entries, scripts per side, client downloads, file checks.

Mirrors what the MTA server does when it loads a resource (``CResource.cpp``): ``<script type>`` is
``server`` (default), ``client`` or ``shared`` (case-insensitive; anything else is loaded as ``server`` with a
warning); ``src`` paths may use ``/`` or ``\\`` and glob patterns; a path with characters outside 32..126, ``:``,
``..`` or ``\\\\`` is invalid; ``<file download="false">`` is not downloaded; ``<min_mta_version>`` strings must
sort like ``0.0.0-0.00000.0.000``. The PNG and RenderWare checks are the ones of ``CResourceChecker``.
"""

from __future__ import annotations

import glob as _glob
import os
import xml.parsers.expat
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Entry", "Resource", "Finding", "load_resource", "valid_path", "valid_version", "SCRIPT_SIDES",
           "KNOWN_TAGS", "MAX_META"]

#: Sides of a ``<script type=...>``.
SCRIPT_SIDES = {"server": ("server",), "client": ("client",), "shared": ("client", "server")}
#: Child tags of ``<meta>`` the MTA server reads.
KNOWN_TAGS = frozenset({"info", "script", "map", "file", "include", "config", "export", "html", "settings",
                        "min_mta_version", "aclrequest", "sync_map_element_data", "oop", "download_priority_group"})
MAX_META = 4 * 1024 * 1024
_VERSION_TEMPLATE = "0.0.0-0.00000.0.000"


@dataclass(slots=True)
class Finding:
    sev: str            # error | warn | info
    file: str           # path relative to the resource ("meta.xml", "client.lua")
    line: int
    col: int
    code: str
    msg: str

    @property
    def at(self) -> str:
        if self.line <= 0:
            return self.file
        return f"{self.file}:{self.line}" + (f":{self.col}" if self.col > 0 else "")


@dataclass(slots=True)
class Entry:
    tag: str
    attrs: dict
    line: int
    text: str = ""
    files: list[str] = field(default_factory=list)      # matched files (relative, forward slashes)


@dataclass
class Resource:
    root: Path
    name: str
    meta: Path | None
    entries: list[Entry] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    scripts: list[tuple[str, tuple[str, ...], Entry]] = field(default_factory=list)   # (rel, sides, entry)
    oop: bool | None = None                              # None = no meta.xml (unknown)
    min_client: str = ""
    min_server: str = ""

    def add(self, sev: str, file: str, line: int, code: str, msg: str, col: int = 0) -> None:
        self.findings.append(Finding(sev, file, line, col, code, msg))

    def entries_of(self, tag: str) -> list[Entry]:
        return [e for e in self.entries if e.tag == tag]

    def downloads(self) -> list[tuple[str, int, str]]:
        """``(rel, bytes, kind)`` of every file a client downloads (client scripts, files, html is not)."""
        out: dict[str, tuple[int, str]] = {}
        for e in self.entries:
            if e.tag == "script":
                sides = SCRIPT_SIDES.get(e.attrs.get("type", "server").lower(), ("server",))
                if "client" not in sides:
                    continue
                kind = "script"
            elif e.tag == "file":
                if e.attrs.get("download", "").lower() in ("no", "false"):
                    continue
                kind = "file"
            else:
                continue
            for rel in e.files:
                try:
                    out.setdefault(rel, ((self.root / rel).stat().st_size, kind))
                except OSError:
                    pass
        return sorted(((k, v[0], v[1]) for k, v in out.items()), key=lambda r: (-r[1], r[0]))


def valid_path(p: str) -> bool:
    """``IsValidFilePath`` of MTA: printable ASCII, no ``:``, no ``..``, no ``\\\\``."""
    for i, c in enumerate(p):
        o = ord(c)
        nxt = p[i + 1] if i + 1 < len(p) else ""
        if o < 32 or o > 126 or c == ":" or (c == "." and nxt == ".") or (c == "\\" and nxt == "\\"):
            return False
    return True


def valid_version(v: str) -> bool:
    """``IsValidVersionString`` of MTA (a prefix of ``0.0.0-0.00000.0.000`` shape)."""
    for c, d in zip(v, _VERSION_TEMPLATE):
        if not (c.isdigit() and d.isdigit()) and c != d:
            return False
    return True


def _magic(p: str) -> bool:
    return any(ch in p for ch in "*?[")


def _match(root: Path, src: str) -> list[str]:
    s = src.replace("\\", "/").lstrip("/")
    if _magic(s):
        hits = []
        for h in _glob.glob(s, root_dir=str(root), recursive=True):
            hp = root / h
            if hp.is_file():
                hits.append(Path(h).as_posix())
        return sorted(hits)
    p = root / s
    if p.is_file():
        return [s]
    # MTA on Windows is case-insensitive; Linux servers are not: find a case-only match to warn about
    return []


def _case_match(root: Path, rel: str) -> str | None:
    cur = root
    parts = rel.split("/")
    out = []
    for part in parts:
        try:
            names = {n.lower(): n for n in os.listdir(cur)}
        except OSError:
            return None
        real = names.get(part.lower())
        if real is None:
            return None
        out.append(real)
        cur = cur / real
    return "/".join(out) if cur.is_file() else None


def _parse_xml(data: bytes) -> tuple[list[tuple[int, str, dict, int, list[str]]], str | None, int, int]:
    """``(elements, error, line, col)``; an element is ``(depth, tag, attrs, line, text parts)``."""
    elems: list[tuple[int, str, dict, int, list[str]]] = []
    stack: list[list[str]] = []
    p = xml.parsers.expat.ParserCreate()
    p.buffer_text = True

    def start(tag, attrs):
        parts: list[str] = []
        elems.append((len(stack), tag, dict(attrs), p.CurrentLineNumber, parts))
        stack.append(parts)

    def end(_tag):
        stack.pop()

    def chars(data):
        if stack:
            stack[-1].append(data)

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.CharacterDataHandler = chars
    try:
        p.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        return elems, xml.parsers.expat.ErrorString(e.code), e.lineno, e.offset + 1
    return elems, None, 0, 0


def _rw_ok(path: Path) -> bool | None:
    """``CheckRwFileForIssues``: the chunks inside the first RenderWare chunk add up to its size."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return False
            size = int.from_bytes(head[4:8], "little")
            valid = size + 12
            if int.from_bytes(head[:4], "little") not in (0x10, 0x16) or valid > os.fstat(f.fileno()).st_size:
                return False
            pos = 12
            while pos < valid:
                if valid - pos < 12:
                    return False
                h = f.read(12)
                if len(h) != 12:
                    return False
                n = int.from_bytes(h[4:8], "little")
                if n > valid - pos - 12:
                    return False
                f.seek(n, os.SEEK_CUR)
                pos += n + 12
            return pos == valid
    except OSError:
        return None


def _png_ok(path: Path) -> bool | None:
    try:
        with open(path, "rb") as f:
            h = f.read(8)
    except OSError:
        return None
    return h == b"\x89PNG\r\n\x1a\n" or h[:3] == b"\xff\xd8\xff"


def _col_ok(path: Path) -> bool | None:
    try:
        with open(path, "rb") as f:
            h = f.read(4)
    except OSError:
        return None
    return h in (b"COLL", b"COL2", b"COL3", b"COL4")


def load_resource(root: Path) -> Resource:
    """Read ``root/meta.xml`` (if any) and resolve every entry; problems become :class:`Finding` rows."""
    root = Path(os.path.abspath(root))
    meta = root / "meta.xml"
    res = Resource(root=root, name=root.name, meta=meta if meta.is_file() else None)
    if res.meta is None:
        res.add("error", "meta.xml", 0, "META_MISSING",
                "no meta.xml: MTA does not load this folder as a resource (satk mta resource new makes one)")
        for p in sorted(root.rglob("*.lua")):
            rel = p.relative_to(root).as_posix()
            res.scripts.append((rel, ("any",), Entry("script", {"src": rel}, 0, files=[rel])))
        return res
    try:
        data = meta.read_bytes()
    except OSError as e:
        res.add("error", "meta.xml", 0, "META_XML", f"cannot read meta.xml: {e}")
        return res
    if len(data) > MAX_META:
        res.add("error", "meta.xml", 0, "META_XML", f"meta.xml is {len(data) // 1024} KB: not a resource meta file")
        return res
    elems, err, eline, ecol = _parse_xml(data)
    if err is not None:
        res.add("error", "meta.xml", eline, "META_XML", f"XML error: {err} (MTA refuses to load the resource)", ecol)
        return res
    if not elems or elems[0][1] != "meta":
        res.add("error", "meta.xml", elems[0][3] if elems else 0, "META_ROOT",
                f"the root element is <{elems[0][1] if elems else ''}>, MTA expects <meta>")
        return res
    res.oop = False
    seen: dict[tuple[str, str], int] = {}
    for depth, tag, attrs, line, parts in elems[1:]:
        if depth != 1:
            continue
        e = Entry(tag, attrs, line, "".join(parts).strip())
        res.entries.append(e)
        if tag not in KNOWN_TAGS:
            low = tag.lower()
            hint = f" (tags are case-sensitive: <{low}>)" if low in KNOWN_TAGS else ""
            res.add("info", "meta.xml", line, "UNKNOWN_TAG", f"<{tag}> is ignored by MTA{hint}")
            continue
        if tag == "oop":
            res.oop = e.text.strip().lower() in ("true", "1", "yes")
        elif tag == "min_mta_version":
            for k in ("client", "server", "both"):
                v = attrs.get(k)
                if v is None:
                    continue
                if not valid_version(v):
                    res.add("error", "meta.xml", line, "MIN_VERSION",
                            f"<min_mta_version {k}=\"{v}\">: invalid version string (form 1.6.0 or "
                            "1.6.0-9.22000); MTA refuses to start the resource")
                if k in ("client", "both"):
                    res.min_client = v
                if k in ("server", "both"):
                    res.min_server = v
        elif tag == "download_priority_group" and e.text and not e.text.lstrip("-").isdigit():
            res.add("warn", "meta.xml", line, "META_VALUE", f"<download_priority_group> must be an integer, "
                                                              f"got {e.text!r}")
        if tag in ("script", "file", "map", "config", "html"):
            src = attrs.get("src")
            if src is None or not src.strip():
                res.add("error", "meta.xml", line, "META_VALUE", f"<{tag}> without src")
                continue
            if not valid_path(src):
                res.add("error", "meta.xml", line, "BAD_PATH",
                        f"<{tag} src=\"{src}\">: MTA rejects paths with '..', ':', '\\\\' or characters outside "
                        "printable ASCII")
                continue
            e.files = _match(root, src)
            norm = src.replace("\\", "/").lstrip("/")
            if not e.files and not _magic(norm):
                alt = _case_match(root, norm)
                if alt is not None:
                    e.files = [alt]
                    res.add("warn", "meta.xml", line, "FILE_CASE",
                            f"<{tag} src=\"{src}\"> is '{alt}' on disk: Linux servers are case-sensitive")
                else:
                    res.add("error", "meta.xml", line, "FILE_MISSING",
                            f"<{tag} src=\"{src}\">: file not found (MTA: Couldn't find file(s) {src})")
            elif not e.files:
                res.add("info", "meta.xml", line, "FILE_MISSING", f"<{tag} src=\"{src}\">: the pattern matches "
                                                                   "no file")
            if tag == "script":
                typ = attrs.get("type", "server")
                sides = SCRIPT_SIDES.get(typ.lower())
                if sides is None:
                    res.add("warn", "meta.xml", line, "SCRIPT_TYPE",
                            f"<script type=\"{typ}\">: unknown type, MTA loads it as 'server' (client, server or "
                            "shared)")
                    sides = ("server",)
                if attrs.get("cache") is not None and "client" not in sides:
                    res.add("info", "meta.xml", line, "META_VALUE", "cache=\"...\" only applies to client scripts")
                for rel in e.files:
                    if not rel.lower().endswith((".lua", ".luac")):
                        res.add("warn", "meta.xml", line, "SCRIPT_EXT", f"<script src=\"{rel}\"> is not a .lua file")
                    for s in sides:
                        k = (s, rel.lower())
                        if k in seen:
                            res.add("warn", "meta.xml", line, "DUPLICATE_FILE",
                                    f"{rel} is already a {s} script (line {seen[k]}): MTA ignores the duplicate")
                        else:
                            seen[k] = line
                    res.scripts.append((rel, sides, e))
            elif tag == "file":
                for rel in e.files:
                    k = ("file", rel.lower())
                    if k in seen or ("client", rel.lower()) in seen:
                        res.add("warn", "meta.xml", line, "DUPLICATE_FILE",
                                f"{rel} is already a client file (line {seen.get(k) or seen.get(('client', rel.lower()))})"
                                ": MTA ignores the duplicate")
                    seen.setdefault(k, line)
                    if rel.lower().endswith(".lua"):
                        res.add("info", "meta.xml", line, "LUA_AS_FILE",
                                f"{rel} is a <file>: it is downloaded but not run (use <script type=\"client\">, "
                                "unless the code loads it with loadstring)")
        elif tag == "include":
            name = attrs.get("resource")
            if not name:
                res.add("error", "meta.xml", line, "META_VALUE", "<include> without resource")
            elif not (root.parent / name / "meta.xml").is_file() and not (root.parent / f"{name}.zip").is_file():
                res.add("info", "meta.xml", line, "INCLUDE",
                        f"<include resource=\"{name}\">: not found next to this resource (the server must have it)")
        elif tag == "export":
            if not attrs.get("function"):
                res.add("error", "meta.xml", line, "META_VALUE", "<export> without function")
            t = attrs.get("type", "server")
            if t.lower() not in SCRIPT_SIDES:
                res.add("warn", "meta.xml", line, "SCRIPT_TYPE", f"<export type=\"{t}\">: client, server or shared")
    listed = {rel.lower() for rel, _s, _e in res.scripts} | {r.lower() for e in res.entries for r in e.files}
    for p in sorted(root.rglob("*.lua")):
        rel = p.relative_to(root).as_posix()
        if rel.lower() not in listed and not any(part.startswith(".") for part in Path(rel).parts):
            res.add("info", rel, 0, "NOT_LISTED", "this .lua file is not in meta.xml: MTA does not run it")
    for e in res.entries:
        if e.tag != "file":
            continue
        for rel in e.files:
            low = rel.lower()
            p = root / rel
            if low.endswith(".png"):
                ok = _png_ok(p)
                if ok is False:
                    res.add("warn", rel, 0, "FILE_INVALID", "not a PNG (or JPEG) image: MTA warns 'File ... is "
                                                            "invalid' and the client cannot load it")
            elif low.endswith((".txd", ".dff")):
                ok = _rw_ok(p)
                if ok is False:
                    res.add("warn", rel, 0, "FILE_INVALID", "RenderWare chunk sizes do not add up: MTA warns "
                                                            "'contains errors' (truncated or not a RW file)")
            elif low.endswith(".col"):
                ok = _col_ok(p)
                if ok is False:
                    res.add("warn", rel, 0, "FILE_INVALID", "not a collision file (COLL/COL2/COL3/COL4 header)")
    return res
