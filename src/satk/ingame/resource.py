"""The two MTA resources of the in-game loop, written into the private server's ``resources`` folder.

* ``satk-testdrive`` -- the logic (``mta-resources/satk-testdrive`` of this checkout), copied as is;
* ``satk-testdrive-mod`` -- generated content: the mod's files under ``m/<key>.dff|txd|col`` (listed as
  ``<file>`` so clients download them, unchanged files stay cached), ``manifest.lua`` (a server script
  exporting ``manifest()``: models, test spots, check geometry) and ``meta.xml``.

Both are written only when their bytes change; :func:`build_content` reports what changed, so
``satk ingame reload`` restarts only what it must. The manifest ``rev`` is a hash of the manifest and
of every file, so a texture edit alone gives a new rev. Deterministic: sorted keys, no timestamps.
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from . import spots as SP

__all__ = ["LOGIC", "CONTENT", "lua", "logic_source", "install_logic", "content_files", "manifest",
           "build_content", "sha256"]

LOGIC = "satk-testdrive"
CONTENT = "satk-testdrive-mod"
_LUA_RESERVED = frozenset("and break do else elseif end false for function if in local nil not or repeat return "
                          "then true until while".split())


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- Lua literals


def _lua_str(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        c = chr(b)
        if c == "\\":
            out.append("\\\\")
        elif c == '"':
            out.append('\\"')
        elif 32 <= b < 127:
            out.append(c)
        else:
            out.append(f"\\{b:03d}")
    return '"' + "".join(out) + '"'


def _lua_key(k: str) -> str:
    if k.isidentifier() and k.isascii() and k not in _LUA_RESERVED:
        return k
    return "[" + _lua_str(k) + "]"


def lua(v: Any, indent: int | None = None, _level: int = 0) -> str:
    """A Lua 5.1 literal for a JSON-like value (dict keys sorted; ``None`` -> ``nil``)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise SatkError("BAD_PARAMS", "non-finite number in a Lua literal")
        return repr(v)
    if isinstance(v, str):
        return _lua_str(v)
    if isinstance(v, Path):
        return _lua_str(v.as_posix())
    if isinstance(v, (list, tuple)):
        items = [lua(x, indent, _level + 1) for x in v]
        return "{" + ", ".join(items) + "}"
    if isinstance(v, dict):
        keys = sorted(v, key=str)
        items = [f"{_lua_key(str(k))} = {lua(v[k], indent, _level + 1)}" for k in keys if v[k] is not None]
        if indent and _level < 2 and items:
            pad = " " * (indent * (_level + 1))
            return "{\n" + "".join(f"{pad}{it},\n" for it in items) + " " * (indent * _level) + "}"
        return "{" + ", ".join(items) + "}"
    raise SatkError("BAD_PARAMS", f"cannot write {type(v).__name__} as Lua")


# --------------------------------------------------------------------------- writing


def _write_if_changed(path: Path, data: bytes) -> bool:
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
    except OSError:
        pass
    paths.atomic_write(path, data)
    return True


def logic_source() -> Path:
    """``mta-resources/satk-testdrive`` of this checkout."""
    from ..core.config import REPO_ROOT

    return REPO_ROOT / "mta-resources" / LOGIC


def install_logic(resources: Path) -> dict[str, Any]:
    """Copy the logic resource into ``resources/satk-testdrive``; ``changed`` lists rewritten files."""
    src = logic_source()
    if not (src / "meta.xml").is_file():
        raise SatkError("NOT_READY", f"resource source missing: {paths.jpath(src)}")
    dst = resources / LOGIC
    changed = []
    digest = hashlib.sha256()
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src).as_posix()
        data = f.read_bytes()
        digest.update(rel.encode() + b"\0" + data)
        if _write_if_changed(dst / rel, data):
            changed.append(rel)
    return {"dir": paths.jpath(dst), "changed": changed, "sha": digest.hexdigest()[:12]}


def content_files(specs) -> tuple[dict[str, bytes], dict[str, dict[str, str]]]:
    """``({path in the resource: bytes}, {model key: {dff|txd|col: path}})`` of the model specs."""
    from .modset import col_bytes

    blobs: dict[str, bytes] = {}
    per: dict[str, dict[str, str]] = {}
    for s in specs:
        files: dict[str, str] = {}
        for kind in ("col", "txd", "dff"):
            src = s.files.get(kind)
            if src is None:
                continue
            rel = f"m/{s.key}.{kind}"
            try:
                data = col_bytes(Path(src), s.col_entry) if kind == "col" else Path(src).read_bytes()
            except OSError as e:
                raise SatkError("NOT_FOUND", f"cannot read {src}: {e}") from None
            blobs[rel] = data
            files[kind] = rel
        per[s.key] = files
    return blobs, per


def manifest(specs, per: dict[str, dict[str, str]], blobs: dict[str, bytes], *, label: str,
             defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """The manifest table (``rev`` = hash of the manifest body and every file)."""
    body: dict[str, Any] = {
        "version": 1,
        "label": label,
        "defaults": defaults or {"spot": "grove", "time": "12:00", "weather": 0},
        "models": [s.manifest(per.get(s.key, {})) for s in specs],
        "spots": SP.SPOTS,
        "geom": SP.GEOM,
    }
    h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for rel in sorted(blobs):
        h.update(rel.encode() + b"\0" + hashlib.sha256(blobs[rel]).digest())
    return {"rev": h.hexdigest()[:12], **body}


def _manifest_lua(m: dict[str, Any]) -> bytes:
    text = ("-- generated by satk ingame (do not edit): the model set of satk-testdrive\n"
            f"SATK_TD_MANIFEST = {lua(m, indent=4)}\n\n"
            "function manifest()\n    return SATK_TD_MANIFEST\nend\n")
    return text.encode("utf-8")


def _meta_xml(rels: list[str], label: str) -> bytes:
    from xml.sax.saxutils import quoteattr

    lines = ["<meta>",
             "    <!-- generated by satk ingame: the models and the manifest of satk-testdrive -->",
             f"    <info author=\"satk\" name=\"{CONTENT}\" version=\"1.0.0\" type=\"misc\" "
             f"description={quoteattr('satk in-game test set: ' + label)}/>",
             "    <script src=\"manifest.lua\" type=\"server\"/>",
             "    <export function=\"manifest\" type=\"server\"/>"]
    lines += [f"    <file src=\"{r}\"/>" for r in rels]
    lines += ["</meta>", ""]
    return "\n".join(lines).encode("utf-8")


def build_content(resources: Path, specs, *, label: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write ``resources/satk-testdrive-mod``; returns ``{rev, manifest, changed, removed, files}``."""
    dst = resources / CONTENT
    blobs, per = content_files(specs)
    m = manifest(specs, per, blobs, label=label, defaults=defaults)
    changed = []
    for rel in sorted(blobs):
        if _write_if_changed(dst / rel, blobs[rel]):
            changed.append(rel)
    removed = []
    mdir = dst / "m"
    if mdir.is_dir():
        for f in sorted(mdir.iterdir()):
            rel = f"m/{f.name}"
            if f.is_file() and rel not in blobs:
                paths.ensure_removable(f)
                f.unlink()
                removed.append(rel)
    if _write_if_changed(dst / "manifest.lua", _manifest_lua(m)):
        changed.append("manifest.lua")
    if _write_if_changed(dst / "meta.xml", _meta_xml(sorted(blobs), label)):
        changed.append("meta.xml")
    files = {rel: {"sha256": sha256(data)[:16], "bytes": len(data)} for rel, data in sorted(blobs.items())}
    return {"dir": paths.jpath(dst), "rev": m["rev"], "manifest": m, "changed": changed, "removed": removed,
            "files": files}
