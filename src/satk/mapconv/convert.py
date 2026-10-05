"""Format detection, loading, writing and loss checks of ``satk map convert`` (report 23 §13 P0).

Formats: ``pawn`` (SA-MP/open.mp script text: ``.pwn .inc .p .txt``), ``mta`` (``.map``), ``ipl`` (text or
``bnry``; written as text) and ``json`` (the canonical :mod:`satk.mapconv.scene`, ``.json``). A source is
a file path or an ``ipl:<name>`` SID of the index (the text IPL file or the ``bnry`` blob in an IMG).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, jpath, open_ro
from .scene import Issue, Scene, num

__all__ = ["FORMATS", "EXT", "Source", "detect_format", "load_source", "read_scene", "write_scene",
           "model_names", "check_losses", "MAX_INPUT"]

FORMATS = ("pawn", "mta", "ipl", "json")
EXT = {"pawn": ".pwn", "mta": ".map", "ipl": ".ipl", "json": ".json"}
_BY_EXT = {".pwn": "pawn", ".inc": "pawn", ".p": "pawn", ".txt": "pawn", ".map": "mta", ".xml": "mta",
           ".ipl": "ipl", ".json": "json"}
MAX_INPUT = 64 * 1024 * 1024


@dataclass
class Source:
    """Raw input: ``label`` is the path (``jpath``) or the SID; ``stem`` names the outputs."""

    label: str
    data: bytes
    stem: str
    ext: str
    path: Path | None = None  # the file read (None for blobs inside an IMG)


def detect_format(src: Source) -> str:
    """Format by extension, else by content (``bnry``/``<``/``{``/an ``inst`` line), else ``pawn``."""
    f = _BY_EXT.get(src.ext.lower())
    if f:
        return f
    head = src.data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"bnry"):
        return "ipl"
    if head.startswith(b"<"):
        return "mta"
    if head.startswith(b"{"):
        return "json"
    if any(ln.strip().lower() == b"inst" for ln in head.splitlines()):
        return "ipl"
    return "pawn"


def _read_file(p: Path) -> bytes:
    try:
        size = p.stat().st_size
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read {jpath(p)}: {e.strerror or e}",
                        hint="give an existing file or an ipl:<name> SID") from None
    if not p.is_file():
        raise SatkError("BAD_PARAMS", f"not a file: {jpath(p)}")
    if size > MAX_INPUT:
        raise SatkError("BAD_PARAMS", f"{jpath(p)} is {size} bytes, the limit is {MAX_INPUT}")
    with open_ro(p) as f:
        return f.read()


def load_source(src: str, profile: str = "vanilla", db=None) -> Source:
    """Read a file path or an ``ipl:<name>`` SID (through ``db`` or the index of ``profile``)."""
    if not src or not str(src).strip():
        raise SatkError("BAD_PARAMS", "no source given", hint="satk map convert <file.pwn|.map|.ipl|ipl:name> --to mta")
    s = str(src).strip()
    if s.lower().startswith("ipl:"):
        if db is None:
            from ..index.api import open_index

            db = open_index(profile)
        info = db.get(s)
        fkey = str(info.get("file") or "")
        name = str(info.get("id", s)).split(":", 1)[1]
        if info.get("kind") == "binary":
            return Source(str(info.get("id", s)), db.read_blob(db.blob_ref(fkey)), name, ".ipl")
        p = Path(db.root) / fkey.split(":", 1)[1]
        return Source(str(info.get("id", s)), _read_file(p), name, ".ipl", p)
    p = Path(os.path.abspath(s))
    return Source(jpath(p), _read_file(p), p.stem, p.suffix, p)


def read_scene(source: Source, fmt: str = "auto", names: dict[int, str] | None = None) -> Scene:
    """Parse a :class:`Source` into a :class:`Scene` (``BAD_PARAMS`` with the line on malformed input)."""
    from .mta import MtaSyntaxError, read_mta
    from .pawn import read_pawn
    from .scene import scene_from_json

    f = detect_format(source) if fmt == "auto" else fmt
    if f not in FORMATS:
        raise SatkError("BAD_PARAMS", f"unknown map format {f!r}", data={"choices": list(FORMATS)})
    try:
        if f == "pawn":
            sc = read_pawn(source.data, source.label)
        elif f == "mta":
            sc = read_mta(source.data, source.label)
        elif f == "ipl":
            from .ipl import read_ipl

            sc = read_ipl(source.data, source.label, names)
        else:
            sc = scene_from_json(source.data.decode("utf-8-sig"))
            sc.source = source.label
    except MtaSyntaxError as e:
        raise SatkError("BAD_PARAMS", f"{source.label}: not a valid .map: {e}", hint="check the XML near that line",
                        data={"line": e.line}) from None
    except UnicodeDecodeError as e:
        raise SatkError("BAD_PARAMS", f"{source.label}: not UTF-8 text: {e}") from None
    except ValueError as e:  # FormatError (binary IPL), JSON errors, size limits
        raise SatkError("BAD_PARAMS", f"{source.label}: cannot read as {f}: {e}") from None
    sc.fmt = f
    return sc


def model_names(ids, profile: str = "vanilla") -> tuple[dict[int, str], str | None]:
    """Model id -> name from the index (``({}, reason)`` when there is no index)."""
    want = sorted({int(i) for i in ids if int(i) >= 0})
    if not want:
        return {}, None
    try:
        from ..index.api import open_index

        db = open_index(profile)
        out: dict[int, str] = {}
        for k in range(0, len(want), 400):
            part = want[k:k + 400]
            env = db.query(f"SELECT id, name FROM v_model WHERE id IN ({','.join('?' * len(part))})", part, limit=500)
            out.update({int(r[0]): str(r[1]) for r in env["rows"]})
        return out, None
    except SatkError as e:
        return {}, f"{e.code}: {e.msg}"


def write_scene(sc: Scene, to: str, *, names: dict[int, str] | None = None, pawn_func: str = "auto",
                materials: bool = True, tilt: str = "warn", header: str | None = None) -> str:
    """Text of ``sc`` in format ``to`` (adds writer issues such as ``TILT_LOST`` to ``sc``)."""
    from .scene import scene_to_json

    if to == "pawn":
        from .pawn import write_pawn

        return write_pawn(sc, func=pawn_func, header=header)
    if to == "mta":
        from .mta import write_mta

        return write_mta(sc, names, materials=materials)
    if to == "ipl":
        from .ipl import write_ipl

        return write_ipl(sc, names, tilt=tilt, header=header)
    if to == "json":
        return scene_to_json(sc)
    raise SatkError("BAD_PARAMS", f"unknown target format {to!r}", data={"choices": list(FORMATS)})


def _count(objs, pred) -> list[int]:
    return [i for i, o in enumerate(objs) if pred(o)]


def _lost(sc: Scene, code: str, idx: list[int], what: str) -> None:
    if idx:
        shown = ", ".join(str(i) for i in idx[:8]) + (" ..." if len(idx) > 8 else "")
        sc.issues.append(Issue("warn", code, "file", f"{what} on {len(idx)} objects is lost (obj {shown})"))


def check_losses(sc: Scene, to: str, *, pawn_func: str = "auto", materials: bool = True) -> None:
    """Add ``LOST_*`` warnings for what format ``to`` cannot express."""
    objs = sc.objects
    if to == "json":
        return
    if to in ("pawn", "ipl"):
        _lost(sc, "LOST_MTA", _count(objs, lambda o: o.scale != 1.0 or o.alpha != 255 or o.doublesided
                                     or not o.collisions or o.frozen or o.breakable is False), "MTA scale/alpha/flags")
    if to == "pawn":
        _lost(sc, "LOST_LOD", _count(objs, lambda o: o.lod >= 0), "IPL LOD link")
        _lost(sc, "LOST_IFLAGS", _count(objs, lambda o: o.iflags != 0), "IPL instance flags")
    if to == "pawn" and pawn_func == "CreateObject":
        _lost(sc, "LOST_STREAMER", _count(objs, lambda o: o.world != -1 or o.interior != -1 or o.stream is not None),
              "world/interior/stream distance (CreateObject has none)")
    if to == "mta" and sc.models:
        sc.issues.append(Issue("warn", "LOST_MODELS", "file",
                               f"{len(sc.models)} AddSimpleModel/AddCharModel: .map has no custom models (see report)"))
    if to == "mta" and not materials and (sc.n_materials or sc.n_texts):
        sc.issues.append(Issue("warn", "LOST_MATERIALS", "file",
                               f"{sc.n_materials} materials and {sc.n_texts} material texts dropped (--materials drop)"))
    if to == "ipl":
        _lost(sc, "LOST_MATERIALS", _count(objs, lambda o: o.materials or o.texts), "SetObjectMaterial(Text)")
        _lost(sc, "LOST_STREAMER", _count(objs, lambda o: o.world not in (-1, 0) or o.draw or o.stream is not None),
              "world/draw/stream distance")
        if sc.removals:
            sc.issues.append(Issue("warn", "LOST_REMOVALS", "file",
                                   f"{len(sc.removals)} building removals: IPL cannot remove buildings "
                                   "(edit the original IPL instead)"))
        if sc.models:
            sc.issues.append(Issue("warn", "LOST_MODELS", "file",
                                   f"{len(sc.models)} custom models: define them in an IDE instead"))
        neg = _count(objs, lambda o: o.model < 0)
        if neg:
            sc.issues.append(Issue("error", "BAD_MODEL", "file",
                                   f"{len(neg)} objects use custom (negative) model ids: invalid in an IPL"))


def report_rows(sc: Scene) -> dict:
    """Machine-readable report body: materials, texts, custom models, removals."""
    mats = [[i, m.slot, m.model, m.txd, m.tex, f"0x{m.color:08X}"] for i, o in enumerate(sc.objects) for m in o.materials]
    texts = [[i, t.slot, t.text, t.size, t.font, t.font_size, t.bold, f"0x{t.color:08X}", f"0x{t.back:08X}", t.align]
             for i, o in enumerate(sc.objects) for t in o.texts]
    models = [[m.kind, m.id, m.base, m.dff, m.txd, m.world, m.time_on, m.time_off] for m in sc.models]
    rem = [[i, r.model, r.lod_model, [num(c) for c in r.pos], num(r.radius), r.interior]
           for i, r in enumerate(sc.removals)]
    return {"materials": {"cols": ["obj", "slot", "model", "txd", "tex", "color"], "rows": mats},
            "texts": {"cols": ["obj", "slot", "text", "size", "font", "font_size", "bold", "color", "back", "align"],
                      "rows": texts},
            "models": {"cols": ["kind", "id", "base", "dff", "txd", "world", "time_on", "time_off"], "rows": models},
            "removals": {"cols": ["rm", "model", "lod_model", "pos", "radius", "interior"], "rows": rem}}


def out_path(out: str | None, stem: str, to: str) -> Path:
    """Where to write: ``None``/relative -> under ``work/out/mapconv/``; absolute -> as given."""
    from ..core.paths import ensure_writable, work

    if out is None:
        return work("out", "mapconv", f"{stem}{EXT[to]}")
    p = Path(out)
    if not p.is_absolute():
        base = work("out", "mapconv")
        p = Path(os.path.abspath(base / p))
        if os.path.commonpath([str(p), str(base)]) != str(base):
            raise SatkError("BAD_PARAMS", f"relative --out must stay under {jpath(base)}: {out!r}")
    return ensure_writable(p)


def under_work(p: Path) -> bool:
    base = os.path.normcase(os.path.abspath(cfg().paths.work))
    try:
        return os.path.commonpath([os.path.normcase(str(p)), base]) == base
    except ValueError:
        return False
