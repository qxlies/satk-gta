"""Inventory report (contract K6): which items of ``design/inventory.json`` are built, missing, unattached or
rejected, from a live Blender session or from an exported DFF with its sidecar. Stdlib only.

Example::

    from satk.inventory import api
    r = api.report("mycar")                                    # the project's running session (or newest checkpoint)
    r = api.report("mycar", dff="<work>/out/kit/premier/modloader/premier/premier.dff")
    r = api.report(None, dff=dff)                              # the sidecar carries the inventory
    r["complete"], r["counts"], [i for i in r["items"] if i["status"] != "built"]
    r["gates"], r["done_through"]                             # {"G1": "3/3", "G2": "9/12"}, "G1"

**Facts and verdicts.** Blender measures (``scene.items``: per item its objects, frames, triangles, size, connected
pieces, and the gap to each item it attaches to) and :func:`verdicts` decides, so a session report and a DFF
report (the facts were written next to the DFF by :func:`satk.inventory.sidecar.export_sidecar`) read the same:

* ``rejected``: a reviewer rejected it (``mark``), it is tagged only on hidden, ghost or reference objects, it
  shares an object with other items without face tags, or its geometry is a placeholder: under 5 mm, or faces of
  another item's surface with no geometry of its own (every vertex shared with another item: a recoloured face
  standing in for the part);
* ``missing``: no geometry carries its id, fewer connected pieces with geometry of their own than its ``count``,
  or (a surface item, category ``decal``) its faces carry no image texture;
* ``unattached``: built, but one of its pieces is farther from every built item it attaches to than its tolerance
  (``max_gap_mm`` or the category's: panel 30, glass 25, mechanism 60, default 10 mm);
* ``built``: otherwise. ``complete`` = every required item built and the inventory valid (``strict``: no starter
  item dropped without a waiver; always: the commission's kind and detail of the project's ``asset.json``).

A DFF report checks the sidecar against the DFF: a frame whose triangle count changed (``STALE``) or tagged
triangles the frame does not have (an edited sidecar) are ``problems`` (``complete`` false).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from . import schema as SC

__all__ = ["SIDECAR", "FACTS_FORMAT", "report", "verdicts", "gates", "locate", "mark", "facts_from_session",
           "pairs_of", "has_inventory"]

#: The sidecar next to an exported DFF: ``<stem>.inventory.json``.
SIDECAR = ".inventory.json"
FACTS_FORMAT = "satk.inventory-facts/1"
_PLACEHOLDER_M = 0.005
#: Categories whose item may be a face patch of the shell: a glass pane welded into its opening.
_PANE_CATEGORIES = ("glass",)
_OBJ_SHOWN = 6


def has_inventory(project: str | os.PathLike | None = None, dff: str | os.PathLike | None = None) -> bool:
    """Whether a project has ``design/inventory.json`` or a DFF has a sidecar (cheap; for ``asset.check --strict``)."""
    try:
        if project is not None and SC.path_of(project).is_file():
            return True
    except SatkError:
        pass
    return dff is not None and locate(dff) is not None


def locate(dff: str | os.PathLike) -> Path | None:
    """The sidecar of a DFF: ``<stem>.inventory.json`` next to it or up to two folders above (the kit export folder)."""
    p = Path(dff)
    stem = p.stem
    for d in (p.parent, *list(p.parents)[1:3]):
        f = d / f"{stem}{SIDECAR}"
        if f.is_file():
            return f
    return None


def pairs_of(inv: dict) -> dict[str, list[str]]:
    """``{child id: [parent ids]}`` of an inventory (the gaps Blender measures)."""
    out: dict[str, list[str]] = {}
    for it in inv.get("items") or []:
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            continue
        a = it.get("attaches_to")
        ps = [a] if isinstance(a, str) else [x for x in a if isinstance(x, str)] if isinstance(a, list) else []
        if ps:
            out[it["id"]] = ps
    return out


# --------------------------------------------------------------------------- verdicts


def _r(v: float, nd: int = 1) -> float:
    return float(round(float(v), nd)) + 0.0


def _surface_reason(f: dict, how: str) -> str | None:
    """Why a texture- or paint-carried item is not built from its facts (``None``: it is, or old facts)."""
    if how == "paint":
        share = f.get("paint_share")
        if share is not None and float(share) < 0.5:
            return (f"a paint surface is on the paint key: {float(share):.0%} of its faces are on 60,255,0 or "
                    "255,0,175 (a paint1/paint2 material on these faces, then scene.tag them)")
        return None
    share = f.get("tex_share")
    if share is not None and float(share) < 0.5:
        return (f"a surface item is its texture: {float(share):.0%} of its faces carry an image texture (a material "
                "with the texture image on these faces, then scene.tag them)")
    return None


def _reviewer(it: dict) -> str | None:
    rj = it.get("rejected")
    if isinstance(rj, dict):
        by = f" ({rj['by']})" if rj.get("by") else ""
        return f"{rj.get('reason')}{by}"
    if isinstance(rj, str) and rj.strip():
        return rj.strip()
    return None


def verdicts(inv: dict, facts: dict) -> list[dict]:
    """One row per inventory item: ``{id, name, status, stage, region, required, objects?, reason?, gap_mm?, ...}``."""
    items = [it for it in inv.get("items") or [] if isinstance(it, dict) and isinstance(it.get("id"), str)]
    fi: dict = facts.get("items") or {}
    gaps: dict = facts.get("gaps") or {}
    amb: dict = facts.get("ambiguous") or {}
    hidden: dict = facts.get("hidden") or {}
    amb_ids: dict[str, list[str]] = {}
    for obj, ids in amb.items():
        for i in ids:
            amb_ids.setdefault(i, []).append(obj)
    rows: list[dict] = []
    for it in items:
        iid = it["id"]
        row: dict[str, Any] = {"id": iid, "name": it.get("name", ""), "stage": it.get("stage"),
                               "region": it.get("region"), "required": it.get("required", True) is not False}
        if isinstance(it.get("count"), int):
            row["count"] = it["count"]
        how = SC.verify_of(it)
        surface = how != "geometry"
        f = fi.get(iid)
        if f:
            row["objects"] = list(f.get("objects") or [])[:_OBJ_SHOWN]
            if f.get("frames"):
                row["frames"] = list(f["frames"])[:_OBJ_SHOWN]
            if f.get("pieces") is not None:
                row["pieces"] = f["pieces"]
        why = _reviewer(it)
        if why:
            row.update(status="rejected", reason=f"reviewer: {why}")
        elif not f:
            if iid in amb_ids:
                row.update(status="rejected", reason=f"shares {', '.join(amb_ids[iid][:3])} with other items without "
                                                     "face tags: tag its faces (scene.tag with select)")
            elif iid in hidden:
                row.update(status="rejected", reason=f"tagged only on hidden, ghost or reference objects: "
                                                     f"{', '.join(hidden[iid][:3])}")
            else:
                row.update(status="missing", reason="no geometry carries this id (build it, then scene.tag)")
        else:
            size = f.get("size") or [0, 0, 0]
            count = it.get("count")
            own = f.get("own_verts")
            tex_why = _surface_reason(f, how) if surface else None
            if max(size) < _PLACEHOLDER_M or (f.get("tris") or 0) < 1:
                row.update(status="rejected", reason=f"placeholder: {max(size) * 1000:.1f} mm, {f.get('tris', 0)} "
                                                     "triangles")
            elif not surface and own == 0 and it.get("category") not in _PANE_CATEGORIES:
                shared = ", ".join((f.get("shares_with") or [])[:3]) or "another item"
                row.update(status="rejected",
                           reason=f"placeholder: no geometry of its own; its {f.get('tris', 0)} triangles are a patch "
                                  f"of {shared}'s surface (every vertex shared): build the part as its own piece "
                                  "(extrude, inset and extrude, a separate mesh) and tag that")
            elif tex_why:
                row.update(status="missing", reason=tex_why)
            elif isinstance(count, int) and f.get("pieces") is not None and f["pieces"] < count:
                patch = f.get("patch_pieces") or 0
                row.update(status="missing", reason=f"{f['pieces']} of {count} pieces built"
                           + (f" ({patch} more are face patches of another part)" if patch else ""))
            else:
                row["status"] = "built"
                parents = [q for q in SC._parents(it)]   # noqa: SLF001 - same package
                if parents:
                    built = [q for q in parents if fi.get(q)]
                    if not built:
                        row["note"] = f"attaches to {', '.join(parents)}: not built yet"
                    else:
                        g = gaps.get(iid) or {}
                        vals = [g.get(q) for q in built]
                        known = [v for v in vals if isinstance(v, (int, float))]
                        tol = SC.gap_mm(it)
                        best = min(known) if known else None
                        if best is None or best > tol:
                            far = (f"{_r(best)} mm" if best is not None
                                   else f"more than {facts.get('search_mm', 300)} mm")
                            row.update(status="unattached",
                                       reason=f"{far} from {', '.join(built)} (tolerance {_r(tol, 0):g} mm): move it "
                                              "onto its parent (mesh.attach snap) or weld it")
                        if best is not None:
                            row["gap_mm"] = _r(best)
        frames = it.get("frame")
        if f and frames and facts.get("scope") in ("kit", "dff"):
            want = [frames] if isinstance(frames, str) else list(frames)
            got = set(f.get("frames") or [])
            lost = [x for x in want if x not in got]
            if lost and not (set(want) & got):
                row["note"] = f"planned in {', '.join(want)}; found in {', '.join(sorted(got)) or 'no frame'}"
        if not row["required"]:
            row["optional"] = True
        rows.append(row)
    return rows


def gates(rows: list[dict]) -> tuple[dict, str | None]:
    """``({"G1": "3/3", ...}, done_through)``: built of required items per gate, and the last gate up to which every
    required item is built (``None``: not even G1)."""
    per: dict[str, list[int]] = {}
    for r in rows:
        if not r.get("required"):
            continue
        g = per.setdefault(str(r.get("stage")), [0, 0])
        g[1] += 1
        g[0] += r["status"] == "built"
    done = None
    for st in SC.STAGES:
        b, t = per.get(st, [0, 0])
        if b < t:
            break
        done = st
    return {st: f"{per[st][0]}/{per[st][1]}" for st in SC.STAGES if st in per}, done


def _counts(rows: list[dict]) -> dict:
    c: dict[str, int] = {s: 0 for s in SC.STATUSES}
    for r in rows:
        c[r["status"]] = c.get(r["status"], 0) + 1
    c["total"] = len(rows)
    c["required_open"] = sum(1 for r in rows if r["required"] and r["status"] != "built")
    c["optional_open"] = sum(1 for r in rows if not r["required"] and r["status"] != "built")
    return c


# --------------------------------------------------------------------------- sources


def facts_from_session(inv: dict, *, session: str | None = None, blend: str | None = None, scope: str = "scene",
                       model: str | None = None, timeout: float = 300.0) -> dict:
    """The facts of the current scene (``scene.items`` in Blender, full result read from its file)."""
    from ..studio import api

    params: dict[str, Any] = {"pairs": pairs_of(inv), "scope": scope}
    if model:
        params["model"] = model
    if blend is not None and session is None:
        # a file is read in a fresh Blender: api.call would load it into the running "default" session instead
        bp = Path(blend).absolute()
        if bp.suffix.lower() != ".blend" or not bp.is_file():
            raise SatkError("NOT_FOUND", f"no .blend file {paths.jpath(bp)}")
        res = api.cold("author.call", {"method": "scene.items", "params": params, "stats": "none",
                                       "checkpoint": False}, blend=str(bp), timeout=timeout)
    else:
        res = api.call("scene.items", params, session=session, blend=blend, stats="none", checkpoint=False,
                       timeout=timeout)
    out = res.get("result") or {}
    f = out.get("file")
    if not f or not Path(f).is_file():
        raise SatkError("EXTERNAL_TOOL", "scene.items wrote no facts file", data={"reply": out})
    data = json.loads(Path(f).read_text(encoding="utf-8"))
    if data.get("format") != FACTS_FORMAT:
        raise SatkError("INTERNAL", f"{Path(f).name} is not a {FACTS_FORMAT} file")
    return data


def _dff_frames(dff: Path) -> dict[str, int]:
    from ..model3d.mesh import build_scene

    scene = build_scene(dff.read_bytes(), name=dff.stem)
    out: dict[str, int] = {}
    for p in scene.parts:
        m = scene.meshes[p.geom]
        out[p.name] = out.get(p.name, 0) + len(m.tris or []) // 3          # a flat index list
    return out


def _from_dff(dff: Path, warn: list[str]) -> tuple[dict, dict | None]:
    side = locate(dff)
    if side is None:
        raise SatkError("NOT_FOUND", f"no inventory sidecar next to {paths.jpath(dff)}",
                        hint="kit export writes <stem>.inventory.json when the model carries item tags; or report "
                             "from the session: satk asset inventory <project> --session <name>")
    try:
        data = json.loads(side.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"the inventory sidecar {side.name} does not read ({type(e).__name__}): "
                                      "re-export the model (kit export writes it)") from None
    if not isinstance(data, dict) or not isinstance(data.get("facts") or {}, dict) \
            or not isinstance(data.get("frames") or {}, dict):
        raise SatkError("BAD_PARAMS", f"the inventory sidecar {side.name} is not a {paths.jpath(side)} object of "
                                      "satk.inventory-sidecar/1: re-export the model (kit export writes it)")
    facts = dict(data.get("facts") or {})
    if not isinstance(facts.get("items") or {}, dict):
        raise SatkError("BAD_PARAMS", f"the inventory sidecar {side.name} has no item facts: re-export the model")
    facts["items"] = {k: dict(v) for k, v in (facts.get("items") or {}).items() if isinstance(v, dict)}
    facts["scope"] = "dff"
    try:
        frames = _dff_frames(dff)
    except Exception as e:  # noqa: BLE001 - a damaged DFF still gets the sidecar verdicts
        warn.append(f"BAD_PARAMS: {dff.name} could not be read ({type(e).__name__}); verdicts from the sidecar only")
        frames = None
    sha = data.get("dff_sha256")
    if sha:
        import hashlib

        try:
            if hashlib.sha256(dff.read_bytes()).hexdigest() != sha:
                warn.append(f"STALE: {dff.name} is not the export the sidecar was written for (another hash): "
                            "re-export to refresh it")
        except OSError:
            pass
    if frames is not None:
        exp = data.get("frames") or {}
        stale = [n for n, t in exp.items() if n in frames and frames[n] != t]
        if stale:
            warn.append(f"STALE: {', '.join(stale[:4])} differ from the export the sidecar was written for "
                        "(re-export to refresh it)")
        # tagged triangles per frame never exceed the frame's own (copied or invented item facts do)
        claimed: dict[str, int] = {}
        for f in facts["items"].values():
            for fr, n in (f.get("frame_tris") or {}).items():
                if isinstance(n, int):
                    claimed[fr] = claimed.get(fr, 0) + n
        over = [fr for fr, n in claimed.items() if fr in frames and n > frames[fr]]
        if over:
            raise SatkError("BAD_PARAMS", f"the inventory sidecar of {dff.name} claims more tagged triangles in "
                                          f"{', '.join(sorted(over)[:4])} than the DFF has (an edited sidecar): "
                                          "re-export the model")
        gone = {n for n in exp if n not in frames}
        if gone:
            for iid, f in list((facts.get("items") or {}).items()):
                fr = set(f.get("frames") or [])
                if fr and fr <= gone:
                    del facts["items"][iid]
            warn.append(f"MISSING: frame(s) {', '.join(sorted(gone)[:4])} of the sidecar are not in {dff.name}")
    return facts, data.get("inventory")


# --------------------------------------------------------------------------- report


def _sidecar_project_dir(dff: Path) -> Path | None:
    """The project folder a DFF's sidecar names (``None``: no sidecar, no project, or not a project)."""
    side = locate(dff)
    if side is None:
        return None
    try:
        data = json.loads(side.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    proj = data.get("project") if isinstance(data, dict) else None
    if not proj:
        return None
    p = Path(str(proj))
    return p if (p / "asset.json").is_file() else None


def _project_session(pdir: Path) -> tuple[str | None, str | None]:
    """``(running session, newest checkpoint)`` of a project."""
    from ..studio import launcher as L
    from ..studio import project as P
    from ..studio.core import Checkpoints

    data = {}
    try:
        data = P.load(pdir)[1]
    except SatkError:
        pass
    name = str(data.get("session") or pdir.name)
    try:
        if L.status(name, probe=False).get("up"):
            return name, None
    except SatkError:
        pass
    cps = Checkpoints(pdir / "checkpoints", "blend", lambda _p: None, lambda _p: None).list()
    return None, (cps[-1]["path"] if cps else None)


def report(project_dir: str | os.PathLike | None, dff: str | os.PathLike | None = None,
           session: str | None = None, *, blend: str | None = None, strict: bool = False,
           facts: dict | None = None) -> dict:
    """The inventory report (module doc): ``{items, counts, complete, source, inventory?, warn?, problems?}``.

    Args:
        project_dir: asset project name or folder (its ``design/inventory.json``); ``None`` with ``dff`` uses the
            inventory the sidecar carries.
        dff: an exported DFF: verdicts from its sidecar, checked against the DFF's frames.
        session: a running Blender session (default: the project's session, else its newest checkpoint in a one-shot
            run).
        blend: a .blend to read in a one-shot run instead of a session.
        strict: starter items dropped without a waiver make the inventory invalid (``complete`` false).
        facts: precomputed facts (tests, callers that measured already).
    """
    warn: list[str] = []
    inv: dict | None = None
    inv_path: Path | None = None
    pdir: Path | None = None
    if project_dir is not None:
        from ..studio.project import project_dir as resolve_dir

        pdir = resolve_dir(project_dir)
        inv_path = pdir / SC.FILE
        if inv_path.is_file():
            inv = SC.load_file(inv_path)
        elif dff is None:
            raise SatkError("NOT_FOUND", f"no inventory in {paths.jpath(pdir)}",
                            hint=f"satk inventory starter <kind> --project {pdir.name} writes the starter to edit")
    if project_dir is None and dff is None:
        raise SatkError("BAD_PARAMS", "give a project (its design/inventory.json) or an exported DFF",
                        hint="satk asset inventory <project> [--dff <file.dff>]")
    source: str
    if facts is not None:
        if inv is None:
            raise SatkError("NOT_FOUND", "no inventory to judge the facts against")
        source = "given"
    elif dff is not None:
        dp = Path(dff)
        if not dp.is_file():
            raise SatkError("NOT_FOUND", f"no DFF {paths.jpath(dp)}")
        facts, side_inv = _from_dff(dp, warn)
        if inv is None:
            inv = side_inv
            if inv is None:
                raise SatkError("NOT_FOUND", f"the sidecar of {dp.name} carries no inventory and no project was given",
                                hint="satk asset inventory <project> --dff <file.dff>")
        source = f"dff:{paths.jpath(dp)}"
    else:
        assert inv is not None and pdir is not None
        cp = None
        if session is None and blend is None:
            session, cp = _project_session(pdir)
            if session is None and cp is None:
                raise SatkError("NOT_READY", f"project {pdir.name} has no running session and no checkpoint",
                                hint=f"satk blender session start --project {pdir.name}")
            if cp is not None:
                blend = cp
                warn.append(f"INFO: no running session: read the newest checkpoint {Path(cp).name}")
        facts = facts_from_session(inv, session=session, blend=blend)
        source = f"session:{session}" if session else f"blend:{paths.jpath(Path(blend))}" if blend else "session"
    if pdir is None and source.startswith("dff:"):
        side_proj = _sidecar_project_dir(Path(dff))
        if side_proj is not None:
            pdir = side_proj
    commission = SC.commission_of(pdir)
    val = SC.validate(inv, strict=strict, commission=commission)
    rows = verdicts(inv, facts)
    counts = _counts(rows)
    known = {r["id"] for r in rows}
    unknown = sorted(i for i in (facts.get("tagged") or []) if i not in known)
    if unknown:
        warn.append(f"UNKNOWN: tags name no inventory item: {', '.join(unknown[:8])}")
    unt = facts.get("untagged") or []
    per_gate, done = gates(rows)
    out: dict[str, Any] = {"kind": inv.get("kind"), "detail": inv.get("detail"), "source": source,
                           "complete": counts["required_open"] == 0 and not val["errors"], "counts": counts,
                           "gates": per_gate, "done_through": done, "items": rows}
    if inv_path is not None and inv_path.is_file():
        out["inventory"] = paths.jpath(inv_path)
    from .starter import views

    vs = views(str(inv.get("kind") or ""))
    used = sorted({str(r.get("region")) for r in rows if r.get("region") in vs})
    if used:
        out["views"] = {r: vs[r] for r in used}
    if unt:
        out["untagged"] = {"objects": len(unt), "tris": sum(int(u.get("tris") or 0) for u in unt),
                           "names": [u.get("object") for u in unt[:8]]}
    if commission:
        out["commission"] = commission
    stale = [w for w in warn if w.startswith(("STALE:", "MISSING:"))]
    problems = list(val["errors"]) + [f"sidecar: {w}" for w in stale]
    if stale:
        out["complete"] = False
    if problems:
        out["problems"] = problems[:20]
    if val["warnings"]:
        warn += [f"INVENTORY: {w}" for w in val["warnings"][:10]]
    if warn:
        out["warn"] = warn
    return out


# --------------------------------------------------------------------------- reviewer marks


def mark(project: str | os.PathLike, item: str, *, reject: str | None = None, accept: bool = False,
         by: str = "reviewer") -> dict:
    """Reject an item with a reason (it stays ``rejected`` whatever its geometry) or accept it again."""
    p, inv = SC.load(project)
    hit = next((it for it in inv.get("items") or [] if isinstance(it, dict) and it.get("id") == item), None)
    if hit is None:
        import difflib

        ids = [it.get("id") for it in inv.get("items") or [] if isinstance(it, dict)]
        raise SatkError("NOT_FOUND", f"no item {item!r} in {paths.jpath(p)}",
                        did_you_mean=difflib.get_close_matches(item, [str(i) for i in ids], n=3, cutoff=0.5))
    if bool(reject) == bool(accept):
        raise SatkError("BAD_PARAMS", "give --reject \"<reason>\" or --accept")
    if reject:
        if len(reject.strip()) < 8:
            raise SatkError("BAD_PARAMS", "the reason says what to fix (at least 8 characters)")
        hit["rejected"] = {"reason": reject.strip()[:300], "by": by[:40]}
    else:
        hit.pop("rejected", None)
    paths.atomic_write(p, json.dumps(inv, ensure_ascii=False, indent=1) + "\n")
    return {"inventory": paths.jpath(p), "item": item, "rejected": hit.get("rejected")}
