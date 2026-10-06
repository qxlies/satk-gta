"""A pure-Python studio world: the same ``author`` methods as Blender on a fake scene (tests, conformance).

The fake scene keeps, per object, its type, vertex and triangle counts and an axis-aligned box. The
counts of :func:`primitive_counts` are exactly what ``mesh.primitive`` makes in Blender (the live test
compares them), so unit tests and the SAAP conformance cases can run without Blender. ``python`` is
*not executed* here: only a top-level ``raise`` (-> ``BAD_PARAMS``) and ``result = <literal>`` are
interpreted, everything else is journaled and ignored with a warning.

Example::

    from satk.saap.server import SaapServer
    srv = SaapServer(MockStudioWorld(), token=token, role="blender").start()
"""

from __future__ import annotations

import ast
import fnmatch
import os
from typing import Any

from ..core.errors import SatkError
from .core import AuthorCore, AuthorWorld, Journal, readonly

__all__ = ["KINDS", "ROUND_KINDS", "primitive_counts", "primitive_dims", "MockScene", "MockHost", "METHODS",
           "MockStudioWorld"]

#: ``mesh.primitive`` kinds.
KINDS: tuple[str, ...] = ("plane", "grid", "cube", "cylinder", "cone", "uv_sphere", "ico_sphere", "circle")
#: Kinds whose density must be given explicitly (``segments``/``rings``/``subdivisions``).
ROUND_KINDS: frozenset[str] = frozenset({"cylinder", "cone", "uv_sphere", "circle"})


def _int(p: dict, key: str, lo: int, hi: int, default: int | None = None) -> int:
    v = p.get(key, default)
    if v is None:
        raise SatkError("BAD_PARAMS", f"mesh.primitive {p.get('kind')}: '{key}' is required ({lo}-{hi}); "
                        "choose the density on purpose", hint=f"add \"{key}\": {max(lo, 8)}")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= int(v) <= hi:
        raise SatkError("BAD_PARAMS", f"mesh.primitive: '{key}' must be an integer {lo}-{hi}, got {v!r}")
    return int(v)


def _num(p: dict, key: str, default: float) -> float:
    v = p.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
        raise SatkError("BAD_PARAMS", f"mesh.primitive: '{key}' must be a positive number, got {v!r}")
    return float(v)


def _vec(p: dict, key: str, n: int, default: float) -> list[float]:
    v = p.get(key, default)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = [float(v)] * n
    if not isinstance(v, (list, tuple)) or len(v) != n or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in v):
        raise SatkError("BAD_PARAMS", f"mesh.primitive: '{key}' must be a positive number or {n} of them, got {v!r}")
    return [float(x) for x in v]


def primitive_counts(kind: str, p: dict) -> tuple[int, int]:
    """``(verts, tris)`` of ``mesh.primitive`` (validates the density parameters)."""
    if kind not in KINDS:
        raise SatkError("BAD_PARAMS", f"mesh.primitive: unknown kind {kind!r}", data={"kinds": list(KINDS)})
    if kind == "plane":
        return 4, 2
    if kind == "grid":
        x, y = _int(p, "x_segments", 1, 512), _int(p, "y_segments", 1, 512)
        return (x + 1) * (y + 1), 2 * x * y
    if kind == "cube":
        return 8, 12
    if kind == "ico_sphere":
        s = _int(p, "subdivisions", 1, 6)
        return 10 * 4 ** (s - 1) + 2, 20 * 4 ** (s - 1)
    n = _int(p, "segments", 3, 512)
    cap = p.get("cap", "ngon")
    if cap not in ("ngon", "none"):
        raise SatkError("BAD_PARAMS", f"mesh.primitive: cap must be 'ngon' or 'none', got {cap!r}")
    caps = (n - 2) if cap == "ngon" else 0
    if kind == "cylinder":
        return 2 * n, 2 * n + 2 * caps
    if kind == "cone":
        if float(p.get("radius_top", 0) or 0) > 0:
            return 2 * n, 2 * n + 2 * caps
        return n + 1, n + caps
    if kind == "circle":
        return n, caps
    rings = _int(p, "rings", 3, 512)  # uv_sphere
    return n * (rings - 1) + 2, 2 * n * (rings - 1)


def primitive_dims(kind: str, p: dict) -> list[float]:
    """Nominal ``[dx, dy, dz]`` of ``mesh.primitive`` before rotation and object scale."""
    if kind in ("plane", "grid"):
        s = _vec(p, "size", 2, 1.0)
        return [s[0], s[1], 0.0]
    if kind == "cube":
        return _vec(p, "size", 3, 1.0)
    r = _num(p, "radius", 0.5)
    if kind in ("uv_sphere", "ico_sphere"):
        return [2 * r, 2 * r, 2 * r]
    if kind == "circle":
        return [2 * r, 2 * r, 0.0]
    if kind == "cone":
        r = max(r, float(p.get("radius_top", 0) or 0))
    return [2 * r, 2 * r, _num(p, "depth", 1.0)]


class MockScene:
    """``{name: {"type", "verts", "tris", "bbox": [[min], [max]]}}`` in creation order."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def unique(self, name: str) -> str:
        if name not in self.objects:
            return name
        i = 1
        while f"{name}.{i:03d}" in self.objects:
            i += 1
        return f"{name}.{i:03d}"

    def match(self, pattern: str | None) -> list[str]:
        pat = pattern or "*"
        return [n for n in self.objects if fnmatch.fnmatchcase(n, pat)]


def _r2(v: float) -> float:
    return round(float(v) + 0.0, 2)


class MockCtx:
    def __init__(self, host: "MockHost", n: int, warns: list[str]):
        self.host = host
        self.scene = host.scene
        self.out_dir = host.out_dir
        self.n = n
        self._warn = warns

    def warn(self, text: str) -> None:
        self._warn.append(text)

    def stats(self, names: list[str] | None = None) -> dict:
        return self.host.stats(names)


class MockHost:
    """Host of :class:`~satk.studio.core.AuthorCore` over a :class:`MockScene` (no files)."""

    def __init__(self, out_dir: str | None = None):
        self.scene = MockScene()
        self.out_dir = out_dir

    def make_ctx(self, n: int, warn: list[str]) -> MockCtx:
        return MockCtx(self, n, warn)

    def stats(self, names: list[str] | None) -> dict:
        objs = self.scene.objects
        sel = [n for n in (names if names is not None else list(objs)) if n in objs]
        per = {n: {"tris": objs[n]["tris"], "verts": objs[n]["verts"],
                   "dims": [_r2(b - a) for a, b in zip(*objs[n]["bbox"])]} for n in sel}
        meshes = [o for o in objs.values() if o["type"] == "MESH"]
        scene: dict[str, Any] = {"objects": len(meshes), "tris": sum(o["tris"] for o in meshes),
                                 "verts": sum(o["verts"] for o in meshes)}
        if meshes:
            lo = [min(o["bbox"][0][i] for o in meshes) for i in range(3)]
            hi = [max(o["bbox"][1][i] for o in meshes) for i in range(3)]
            scene["bbox"] = [[_r2(x) for x in lo], [_r2(x) for x in hi]]
        out: dict[str, Any] = {"scene": scene}
        if per:
            out["objects"] = per
        return out

    def snapshot(self, spec: dict, n: int) -> str:
        raise SatkError("UNSUPPORTED", "the mock world has no renderer")

    def checkpoint(self, n: int) -> str:
        raise SatkError("UNSUPPORTED", "the mock world keeps no checkpoints")

    def check_open(self, path: str) -> None:
        raise SatkError("UNSUPPORTED", "the mock world cannot open .blend files")

    def check_save(self, path: str) -> None:
        raise SatkError("UNSUPPORTED", "the mock world cannot save .blend files")

    open = check_open

    def save(self, path: str) -> str:  # pragma: no cover - check_save refuses first
        raise SatkError("UNSUPPORTED", "the mock world cannot save .blend files")


# --------------------------------------------------------------------------- methods


@readonly
def scene_info(ctx: MockCtx, p: dict) -> dict:
    """List the objects of the scene: name, type, triangles, dimensions (match = glob, limit)."""
    names = ctx.scene.match(p.get("match"))
    limit = int(p.get("limit", 50))
    rows = []
    for n in names[:limit]:
        o = ctx.scene.objects[n]
        rows.append({"name": n, "type": o["type"], "tris": o["tris"],
                     "dims": [_r2(b - a) for a, b in zip(*o["bbox"])]})
    return {"objects": rows, "total": len(names)}


def scene_clear(ctx: MockCtx, p: dict) -> dict:
    """Delete objects (all, or those whose name matches the glob 'match') and their unused data."""
    names = ctx.scene.match(p.get("match"))
    for n in names:
        del ctx.scene.objects[n]
    return {"removed": len(names)}


def mesh_primitive(ctx: MockCtx, p: dict) -> dict:
    """Add a mesh primitive with explicit density: plane grid cube cylinder cone uv_sphere ico_sphere circle."""
    kind = p.get("kind")
    verts, tris = primitive_counts(kind, p)
    dims = primitive_dims(kind, p)
    loc = p.get("location", [0, 0, 0])
    if not isinstance(loc, (list, tuple)) or len(loc) != 3:
        raise SatkError("BAD_PARAMS", "location must be [x, y, z]")
    name = ctx.scene.unique(str(p.get("name") or kind))
    lo = [float(loc[i]) - dims[i] / 2 for i in range(3)]
    hi = [float(loc[i]) + dims[i] / 2 for i in range(3)]
    ctx.scene.objects[name] = {"type": "MESH", "verts": verts, "tris": tris, "bbox": [lo, hi]}
    return {"object": name, "changed": [name]}


def python(ctx: MockCtx, p: dict) -> dict:
    """Run Python in the scene (journaled with its sha256; a checkpoint follows). Mock: not executed."""
    code = p.get("code")
    if not isinstance(code, str) or not code.strip():
        raise SatkError("BAD_PARAMS", "python: 'code' (a string) is required")
    try:
        tree = ast.parse(code, "<studio-python>")
    except SyntaxError as e:
        raise SatkError("BAD_PARAMS", f"python: SyntaxError: {e.msg} (line {e.lineno})") from None
    out: dict[str, Any] = {}
    ignored = 0
    for node in tree.body:
        if isinstance(node, ast.Raise):
            exc = node.exc
            fn = exc.func if isinstance(exc, ast.Call) else exc
            cls = fn.id if isinstance(fn, ast.Name) else "Exception"
            msg = ""
            if isinstance(exc, ast.Call) and exc.args and isinstance(exc.args[0], ast.Constant):
                msg = str(exc.args[0].value)
            raise SatkError("BAD_PARAMS", f"python: {cls}: {msg}", data={"line": node.lineno})
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "result"):
            try:
                out["value"] = ast.literal_eval(node.value)
            except ValueError:
                ignored += 1
        else:
            ignored += 1
    if ignored:
        ctx.warn(f"UNSUPPORTED: the mock world does not execute Python ({ignored} statement(s) ignored)")
    return out


METHODS = {"scene.info": scene_info, "scene.clear": scene_clear, "mesh.primitive": mesh_primitive,
           "python": python}
python.checkpoint = True  # type: ignore[attr-defined]


class MockStudioWorld(AuthorWorld):
    """SAAP world (``core`` + ``author``) over a :class:`MockHost`."""

    impl = "satk-studio-mock"

    def __init__(self, journal: str | os.PathLike | None = None):
        from .core import methods_from_module
        import sys

        methods: dict = {}
        errors: list[str] = []
        methods_from_module(sys.modules[__name__], methods, errors)
        self.host = MockHost()
        super().__init__(AuthorCore(self.host, dict(sorted(methods.items())), errors, journal=Journal(journal)))

    def status_info(self) -> dict:
        return {"objects": len(self.host.scene.objects)}
