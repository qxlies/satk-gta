"""The inventory sidecar of an exported model: ``<stem>.inventory.json`` next to the DFF. Stdlib only.

A DFF keeps no custom properties, so the item tags of the exported frames travel beside it. ``kit export`` calls
:func:`export_sidecar` after it wrote the package (two lines; they never fail the export)::

    from ..inventory.sidecar import export_sidecar
    side = export_sidecar([pkg, lint_dir], stem, session=session, blend=blend, model=kname or None, warn=warn)
    if side: files["inventory"] = side

The sidecar holds the inventory as it was at export time, the facts of the kit frames (measured in Blender by
``scene.items`` with ``scope: kit``: the frames the game gets, without ``_dam``/``_vlo``) and the triangle count of
every frame and the SHA-256 of the DFF, so :func:`satk.inventory.api.report` with ``dff=`` can tell a stale
sidecar from a fresh one. A kit export always writes one (``authored``: the model was built with the kit), with
``inventory: null`` when the model has none, so ``asset.check --strict`` knows an authored model that has no
inventory. A failure removes the old sidecars (they would describe another export) and is an ``ERROR`` warning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from . import schema as SC
from .api import SIDECAR, facts_from_session

__all__ = ["FORMAT", "export_sidecar", "write", "session_project"]

FORMAT = "satk.inventory-sidecar/1"


def session_project(session: str | None) -> Path | None:
    """The asset project a running session belongs to (``None`` when it has none)."""
    if not session:
        return None
    try:
        from ..saap import client as C
        from ..studio import launcher as L

        sess = C.read_session(L.role(session)) or {}
    except Exception:  # noqa: BLE001 - no session file: no project
        return None
    proj = sess.get("project")
    return Path(proj) if proj and Path(proj).is_dir() else None


def write(dirs: list[str | os.PathLike], stem: str, *, inventory: dict | None, facts: dict,
          project: Path | None = None, model: str | None = None, lod: str | None = None,
          dff_sha256: str | None = None, authored: bool = True) -> list[str]:
    """Write ``<stem>.inventory.json`` into every folder of ``dirs`` (deduplicated); returns the paths."""
    doc: dict[str, Any] = {"format": FORMAT, "stem": stem, "frames": facts.get("frames") or {},
                           "inventory": inventory, "facts": {k: v for k, v in facts.items() if k != "frames"},
                           "authored": bool(authored)}
    if model:
        doc["model"] = model
    if lod:
        doc["lod"] = lod
    if dff_sha256:
        doc["dff_sha256"] = dff_sha256
    if project is not None:
        doc["project"] = paths.jpath(project)
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        if d is None:
            continue
        p = Path(d).absolute()
        key = os.path.normcase(str(p))
        if key in seen:
            continue
        seen.add(key)
        f = p / f"{stem}{SIDECAR}"
        paths.atomic_write(paths.ensure_writable(f), text)
        out.append(paths.jpath(f))
    return out


def _remove_old(dirs, stem: str) -> None:
    for d in dirs:
        if d is None:
            continue
        f = Path(d).absolute() / f"{stem}{SIDECAR}"
        try:
            if f.is_file():
                paths.ensure_writable(f).unlink()
        except (OSError, SatkError):
            pass


def export_sidecar(dirs: list[str | os.PathLike] | str | os.PathLike, stem: str, *, session: str | None = None,
                   blend: str | None = None, model: str | None = None, project: str | os.PathLike | None = None,
                   warn: list[str] | None = None, timeout: float = 300.0, lod: str | None = None,
                   dff: str | os.PathLike | None = None, always: bool = True) -> str | None:
    """Measure the kit frames and write the sidecar; returns the first path or ``None``. ``always``: write it for a
    model without inventory and tags too (a kit export: the authored marker). A failure removes the old sidecars of
    ``stem`` in ``dirs`` and goes to ``warn`` as an ``ERROR`` (it never stops the export)."""
    if isinstance(dirs, (str, os.PathLike)):
        dirs = [dirs]
    warn = warn if warn is not None else []
    sha = None
    if dff is not None:
        import hashlib

        try:
            sha = hashlib.sha256(Path(dff).read_bytes()).hexdigest()
        except OSError:
            sha = None
    try:
        pdir = Path(project) if project is not None and Path(project).is_dir() else None
        if pdir is None and project is not None:
            from ..studio.project import project_dir

            pdir = project_dir(project)
        if pdir is None:
            pdir = session_project(session)
        inv = None
        if pdir is not None and (pdir / SC.FILE).is_file():
            inv = SC.load_file(pdir / SC.FILE)
        facts = facts_from_session(inv or {"items": []}, session=session, blend=blend, scope="kit", model=model,
                                   timeout=timeout)
        if inv is None and not facts.get("tagged") and not always:
            return None
        files = write(list(dirs), stem, inventory=inv, facts=facts, project=pdir, model=model, lod=lod,
                      dff_sha256=sha)
        return files[0] if files else None
    except SatkError as e:
        _remove_old(dirs, stem)
        warn.append(f"ERROR: inventory sidecar not written ({e.code}: {e.msg}); asset.check --strict cannot verify "
                    "the items of this export: fix it and export again"[:300])
    except (OSError, ValueError) as e:
        _remove_old(dirs, stem)
        warn.append(f"ERROR: inventory sidecar not written (INTERNAL: {type(e).__name__}: {e}); export again"[:300])
    return None
