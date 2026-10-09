"""The inventory file (contract K6) and its validation, with errors an agent can act on. Stdlib only.

``<project>/design/inventory.json``::

    {"kind": "automobile", "detail": "simple|standard|hero",
     "items": [{"id": "I01", "name": "Body shell (chassis)", "region": "overall", "category": "shell",
                "construction": "one welded half shell from mesh.loft shape sections ...",
                "attaches_to": null, "stage": "G1", "required": true,
                "key"?: "body_shell", "frame"?: "chassis" | [...], "count"?: 2, "max_gap_mm"?: 30,
                "detail"?: "simple", "notes"?: "...", "rejected"?: {"reason", "by"}}],
     "waived"?: [{"key": "doors_rear", "why": "two-door coupe"}]}

* ``id`` ``I`` + 2-4 digits, unique; ``attaches_to`` is ``null`` only for a root item (category shell, wheel,
  structure or body_part: the shell, hull, frame, a wheel), else one id or a list of ids (any one of them); no item
  attaches to itself and there are no cycles.
* ``construction`` says how the item is built (a method and its idea, at least a few words): the inventory is the
  builder's task list, so every item carries its plan.
* ``stage`` is the gate that builds it: G1 form, G2 compose, G3 detail, G4 surface (texture-carried items).
* ``key`` names the starter item it came from (``data/kit/inventory/<kind>.json``). A starter item of the
  inventory's detail level that no item carries, or that an item marks ``required: false`` although the starter
  requires it, is a *dropped* item: allowed only with a ``waived`` entry and a reason (a warning, an error under
  ``strict``). A waiver's reason is a design fact ("two-door coupe"), never effort: reasons about time, budget,
  difficulty, game distance or visibility are errors (:data:`BANNED_WAIVER`).
* ``count``: separate pieces the item has (a left/right pair = 2); the report counts the connected pieces that
  have geometry of their own. Under ``strict`` a count below the starter's needs a waiver with ``count``
  (``{"key": "doors_front", "count": 1, "why": "..."}``).
* ``max_gap_mm`` (0-150): a looser attachment tolerance than the category's; above 3x the category's is a warning.
* The commission (``asset.json`` of the project: ``kind``, ``detail``) binds the inventory: another kind or a lower
  detail level is an error.
* ``rejected``: set by a reviewer (``inventory.mark``); the item stays ``rejected`` until a reviewer accepts it.

:func:`validate` returns ``{"errors": [...], "warnings": [...]}`` (strings ``"I07 attaches_to: ..."``);
:func:`load` reads and checks a project's file; :func:`check` raises ``BAD_PARAMS`` with the first errors.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..core.errors import SatkError

__all__ = ["FILE", "STAGES", "DETAILS", "STATUSES", "CATEGORIES", "ROOT_CATEGORIES", "SURFACE_CATEGORIES",
           "BANNED_WAIVER", "ID_RE", "KEY_RE", "PROP", "ATTR", "IDS", "SCENE_IDS", "GAP_MM", "MAX_GAP_MM", "FIELDS",
           "detail_rank", "validate", "check", "load", "load_file", "path_of", "save", "gap_mm", "parse_ids",
           "banned_reason", "commission_of", "verify_of", "VERIFY"]

#: The inventory inside an asset project.
FILE = "design/inventory.json"
STAGES = ("G1", "G2", "G3", "G4")
DETAILS = ("simple", "standard", "hero")
STATUSES = ("built", "missing", "unattached", "rejected")
#: Item categories (what kind of part it is; the attachment tolerance follows it).
CATEGORIES = ("shell", "panel", "glass", "lamp", "trim", "wheel", "running_gear", "mechanism", "engine", "interior",
              "control", "underbody", "structure", "fixture", "opening", "clutter", "sign", "decal", "body_part",
              "clothing", "accessory", "weapon_part", "effect", "detail", "other")
#: Categories a root item (``attaches_to: null``) may have: what carries the rest.
ROOT_CATEGORIES = ("shell", "wheel", "structure", "body_part")
#: Categories built by their texture, not by geometry of their own (verified from the materials of their faces).
SURFACE_CATEGORIES = ("decal",)
#: Waiver reasons that are effort, not design (``done.md``: never a reason to drop an item).
BANNED_WAIVER = re.compile(
    r"\b(time|budget|deadline|quick|faster|effort|hard to \w+|difficult|complex|complicated|low priority|"
    r"too (many|much|small|detailed)|"
    r"game distance|at (a |the )?distance|far away|not visible|invisible|hard to see|barely (seen|visible)|"
    r"nobody|no one|not needed|unnecessary|not necessary|not important|skip|later|lazy|tool gap|"
    r"count looks|looks full|enough)\b", re.IGNORECASE)
ID_RE = re.compile(r"^I\d{2,4}$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
#: Object custom property: one item id or a comma list.
PROP = "satk_item"
#: Integer face attribute: 0 = no item, n = the n-th id of the id list.
ATTR = "satk_item_idx"
#: Object custom property: the id list the face attribute indexes (1-based values).
IDS = "satk_item_ids"
#: Scene custom property: the append-only id registry of the .blend (every object's list is a prefix of it).
SCENE_IDS = "satk_item_ids"
#: Attachment tolerance (mm) by category: how far an item may stand off what it attaches to.
GAP_MM = {"default": 10.0, "panel": 30.0, "glass": 25.0, "mechanism": 60.0, "running_gear": 20.0, "sign": 25.0,
          "decal": 25.0, "effect": 60.0, "wheel": 30.0, "lamp": 10.0}
#: The loosest attachment tolerance an item may set (mm).
MAX_GAP_MM = 150.0
_REQUIRED = ("id", "name", "region", "category", "construction", "attaches_to", "stage")
FIELDS = frozenset(_REQUIRED + ("required", "key", "frame", "count", "max_gap_mm", "detail", "notes", "rejected",
                                "mirror", "verify"))
#: How an item is proven built: ``geometry`` (pieces of its own, the default), ``texture`` (its faces carry an
#: image texture: the default of the surface categories) or ``paint`` (its faces are on a vehicle paint key).
VERIFY = ("geometry", "texture", "paint")
_TOP = frozenset({"kind", "detail", "items", "waived", "format", "name", "notes", "source", "regions"})
_TEXT_MAX = 400
_CONSTRUCTION_MIN = 12


def detail_rank(d: str | None) -> int:
    """simple 0, standard 1, hero 2 (unknown: 2)."""
    return DETAILS.index(d) if d in DETAILS else len(DETAILS) - 1


def verify_of(item: dict) -> str:
    """``geometry``, ``texture`` or ``paint``: how the report proves an item built."""
    v = item.get("verify")
    if v in VERIFY:
        return str(v)
    return "texture" if item.get("category") in SURFACE_CATEGORIES else "geometry"


def gap_mm(item: dict) -> float:
    """The attachment tolerance of an item in millimetres."""
    v = item.get("max_gap_mm")
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
        return float(v)
    return GAP_MM.get(str(item.get("category") or ""), GAP_MM["default"])


def banned_reason(why: Any) -> str | None:
    """The banned word of a waiver reason (effort, distance, visibility), or ``None``."""
    m = BANNED_WAIVER.search(str(why or ""))
    return m.group(0) if m else None


def commission_of(project_dir: str | os.PathLike | Path | None) -> dict | None:
    """``{"kind", "detail"}`` of a project's ``asset.json`` (what was commissioned), or ``None``."""
    if project_dir is None:
        return None
    f = Path(project_dir) / "asset.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out = {k: data[k] for k in ("kind", "detail") if isinstance(data.get(k), str) and data.get(k)}
    return out or None


def parse_ids(v: Any) -> list[str]:
    """Ids of a ``satk_item`` value: ``"I05"``, ``"I05,I06"`` or a list."""
    if isinstance(v, str):
        parts = v.replace(";", ",").split(",")
    elif isinstance(v, (list, tuple)):
        parts = [str(x) for x in v]
    else:
        return []
    out: list[str] = []
    for x in parts:
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def _parents(item: dict) -> list[str]:
    a = item.get("attaches_to")
    if a is None or a == "":
        return []
    if isinstance(a, str):
        return [a]
    if isinstance(a, list):
        return [x for x in a if isinstance(x, str)]
    return []


def _near(word: str, options) -> str:
    m = difflib.get_close_matches(str(word), list(options), n=2, cutoff=0.6)
    return f" (did you mean {', '.join(m)}?)" if m else ""


def _text_ok(v: Any, min_len: int = 1) -> bool:
    return isinstance(v, str) and len(v.strip()) >= min_len and len(v) <= _TEXT_MAX


def validate(data: Any, *, strict: bool = False, starter: dict | None = None,
             regions: list[str] | None = None, commission: dict | None = None) -> dict:
    """``{"errors", "warnings", "items", "required"}`` of an inventory.

    Args:
        data: the parsed inventory.
        strict: starter items dropped without a waiver (and counts below the starter's) are errors (else warnings).
        starter: the starter of the kind (``satk.inventory.starter.load``); default: looked up by ``kind``.
        regions: known region names (default: the starter's).
        commission: ``{"kind", "detail"}`` the asset was commissioned with (the project's ``asset.json``): another
            kind or a lower detail level is an error.
    """
    errors: list[str] = []
    warns: list[str] = []
    if not isinstance(data, dict):
        return {"errors": ["the inventory must be a JSON object {kind, detail, items}"], "warnings": [],
                "items": 0, "required": 0}
    extra = sorted(set(data) - _TOP)
    if extra:
        warns.append(f"unknown top-level key(s) {extra} are ignored")
    kind = data.get("kind")
    if not _text_ok(kind):
        errors.append("kind: required (the asset kind, such as automobile, bike, prop, building, weapon, ped)")
    detail = data.get("detail")
    if detail not in DETAILS:
        errors.append(f"detail: must be one of {', '.join(DETAILS)}, got {detail!r}")
    if commission:
        ck, cd = commission.get("kind"), commission.get("detail")
        if isinstance(kind, str) and ck and ck != kind:
            errors.append(f"kind: the asset was commissioned as {ck} (asset.json), the inventory says {kind}: "
                          f"start from the {ck} starter (satk inventory starter {ck} --project <project> --force)")
        if cd in DETAILS and detail in DETAILS and detail_rank(detail) < detail_rank(cd):
            errors.append(f"detail: the asset was commissioned at {cd} (asset.json); the inventory is {detail}: "
                          f"a lower detail level drops the {cd} items")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        errors.append("items: a non-empty list of {id, name, region, category, construction, attaches_to, stage}")
        items = []
    if starter is None and isinstance(kind, str):
        try:
            from .starter import load as load_starter

            starter = load_starter(kind)
        except SatkError:
            starter = None
            warns.append(f"kind {kind!r} has no starter list: regions and dropped items are not checked")
    if regions is None and starter:
        regions = list(starter.get("regions") or {})
    ids: dict[str, int] = {}
    keys: dict[str, str] = {}
    for n, it in enumerate(items):
        where = f"items[{n}]"
        if not isinstance(it, dict):
            errors.append(f"{where}: must be an object")
            continue
        iid = it.get("id")
        if not isinstance(iid, str) or not ID_RE.match(iid):
            errors.append(f"{where}.id: must be like I01 (I + 2-4 digits), got {iid!r}")
        elif iid in ids:
            errors.append(f"{iid}: duplicate id (items[{ids[iid]}] and {where})")
        else:
            ids[iid] = n
        tag = iid if isinstance(iid, str) and ID_RE.match(iid or "") else where
        for f in _REQUIRED:
            if f not in it:
                errors.append(f"{tag}: '{f}' is missing" + (" (null for a root item)" if f == "attaches_to" else ""))
        unknown = sorted(set(it) - FIELDS)
        if unknown:
            warns.append(f"{tag}: unknown field(s) {unknown}{_near(unknown[0], FIELDS)}")
        if "name" in it and not _text_ok(it.get("name"), 2):
            errors.append(f"{tag}.name: a short name such as 'Front bumper'")
        if "region" in it:
            r = it.get("region")
            if not _text_ok(r):
                errors.append(f"{tag}.region: a region name such as 'front'")
            elif regions and r not in regions:
                warns.append(f"{tag}.region {r!r} is not a review region of {kind}{_near(r, regions)} "
                             f"(regions: {', '.join(regions)})")
        if "category" in it and it.get("category") not in CATEGORIES:
            errors.append(f"{tag}.category: {it.get('category')!r} is not one of {', '.join(CATEGORIES)}"
                          f"{_near(it.get('category'), CATEGORIES)}")
        if "construction" in it and not _text_ok(it.get("construction"), _CONSTRUCTION_MIN):
            errors.append(f"{tag}.construction: say how it is built (method and idea, at least "
                          f"{_CONSTRUCTION_MIN} characters), e.g. 'mesh.sweep of a soft profile from arch to arch'")
        if "stage" in it and it.get("stage") not in STAGES:
            errors.append(f"{tag}.stage: must be one of {', '.join(STAGES)} (G1 form, G2 compose, G3 detail, "
                          f"G4 surface), got {it.get('stage')!r}")
        a = it.get("attaches_to")
        if a is not None and not (isinstance(a, str) or (isinstance(a, list) and a and all(
                isinstance(x, str) for x in a))):
            errors.append(f"{tag}.attaches_to: null, an item id or a list of item ids, got {a!r}")
        elif a in (None, "") and "attaches_to" in it and it.get("category") in CATEGORIES \
                and it.get("category") not in ROOT_CATEGORIES:
            errors.append(f"{tag}.attaches_to: a {it.get('category')} item names the item it sits on (null is only "
                          f"for a root: {', '.join(ROOT_CATEGORIES)}), so the report can measure that it touches it")
        if "required" in it and not isinstance(it.get("required"), bool):
            errors.append(f"{tag}.required: true or false")
        k = it.get("key")
        if k is not None:
            if not isinstance(k, str) or not KEY_RE.match(k):
                errors.append(f"{tag}.key: snake_case such as 'door_mirrors', got {k!r}")
            elif k in keys:
                warns.append(f"{tag}.key {k!r} is also on {keys[k]}")
            else:
                keys[k] = tag
        c = it.get("count")
        if c is not None and (not isinstance(c, int) or isinstance(c, bool) or not 1 <= c <= 64):
            errors.append(f"{tag}.count: an integer 1-64 (separate pieces), got {c!r}")
        g = it.get("max_gap_mm")
        if g is not None and (isinstance(g, bool) or not isinstance(g, (int, float)) or not 0 <= g <= MAX_GAP_MM):
            errors.append(f"{tag}.max_gap_mm: a number 0-{MAX_GAP_MM:g} (mm an item may stand off its parent), "
                          f"got {g!r}")
        elif g is not None and g > 3 * GAP_MM.get(str(it.get("category") or ""), GAP_MM["default"]):
            warns.append(f"{tag}.max_gap_mm {g:g} is over 3x the {it.get('category')} tolerance "
                         f"({GAP_MM.get(str(it.get('category') or ''), GAP_MM['default']):g} mm): attach the part "
                         "instead of loosening the check")
        fr = it.get("frame")
        if fr is not None and not (isinstance(fr, str) or (isinstance(fr, list) and all(isinstance(x, str)
                                                                                         for x in fr))):
            errors.append(f"{tag}.frame: a frame name or a list of them")
        vf = it.get("verify")
        if vf is not None and vf not in VERIFY:
            errors.append(f"{tag}.verify: one of {', '.join(VERIFY)}, got {vf!r}")
        d = it.get("detail")
        if d is not None and d not in DETAILS:
            errors.append(f"{tag}.detail: one of {', '.join(DETAILS)}")
        rj = it.get("rejected")
        if rj is not None and not (_text_ok(rj, 3) or (isinstance(rj, dict) and _text_ok(rj.get("reason"), 3))):
            errors.append(f"{tag}.rejected: {{\"reason\": \"...\", \"by\": \"critic\"}}")
    # references and cycles
    graph: dict[str, list[str]] = {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            continue
        iid = it["id"]
        ps = _parents(it)
        for q in ps:
            if q == iid:
                errors.append(f"{iid}.attaches_to: an item cannot attach to itself")
            elif q not in ids:
                errors.append(f"{iid}.attaches_to: no item {q!r}{_near(q, ids)}")
        graph[iid] = [q for q in ps if q in ids and q != iid]
    cyc = _cycle(graph)
    if cyc:
        errors.append(f"attaches_to forms a cycle: {' -> '.join(cyc)}")
    if items and ids and not any(not _parents(it) for it in items if isinstance(it, dict)):
        errors.append("no root item: at least one item (the main shell, hull or body) has attaches_to null")
    # starter items dropped without a waiver
    waived: dict[str, str] = {}
    waived_count: dict[str, int] = {}
    wv = data.get("waived")
    if wv is not None:
        if not isinstance(wv, list):
            errors.append("waived: a list of {key, why}")
        else:
            for n, w in enumerate(wv):
                if not isinstance(w, dict) or not isinstance(w.get("key"), str) or not _text_ok(w.get("why"), 8):
                    errors.append(f"waived[{n}]: {{\"key\": \"<starter key>\", \"why\": \"<a reason, 8+ chars>\"}}")
                    continue
                bad = banned_reason(w.get("why"))
                if bad:
                    errors.append(f"waived[{n}] {w['key']}: {bad!r} is not a reason to drop an item (effort, distance "
                                  "and visibility never are): build it, or name the design fact that removes it "
                                  "(\"two-door coupe\", \"open-top boat\")")
                    continue
                c = w.get("count")
                if c is not None:
                    if isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 64:
                        waived_count[w["key"]] = c
                    else:
                        errors.append(f"waived[{n}].count: the lower piece count the design has (0-64), got {c!r}")
                    continue
                waived[w["key"]] = w["why"]
    dropped: list[str] = []
    if starter and detail in DETAILS:
        want = [s for s in starter.get("items") or [] if detail_rank(s.get("detail")) <= detail_rank(detail)]
        by_key = {it.get("key"): it for it in items if isinstance(it, dict) and isinstance(it.get("key"), str)}
        for s in want:
            k = s["key"]
            if k in waived:
                continue
            it = by_key.get(k)
            if it is None:
                dropped.append(k)
                continue
            if s.get("required", True) is not False and it.get("required", True) is False:
                dropped.append(k)          # made optional: the same as dropping it
            sc = s.get("count")
            if isinstance(sc, int) and sc > 1:
                ic = it.get("count")
                floor = waived_count.get(k, sc)
                if not isinstance(ic, int) or ic < floor:
                    msg = (f"{by_key[k].get('id')} ({k}): count {ic!r} is below the starter's {sc} (a pair or a set): "
                           f"keep count {sc}, or waive it with the design's count "
                           f"({{\"key\": \"{k}\", \"count\": n, \"why\": \"...\"}})")
                    (errors if strict else warns).append(msg)
        for k in list(waived) + list(waived_count):
            if k not in {s["key"] for s in starter.get("items") or []}:
                warns.append(f"waived {k!r} is not a starter key of {kind}")
    if dropped:
        msg = (f"{len(dropped)} starter item(s) of {kind} {detail} are not in the inventory or not required: "
               f"{', '.join(dropped[:12])}{' ...' if len(dropped) > 12 else ''} (add them with their key and keep them "
               "required, or waive each with a design reason)")
        (errors if strict else warns).append(msg)
    req = sum(1 for it in items if isinstance(it, dict) and it.get("required", True) is not False)
    return {"errors": errors, "warnings": warns, "items": len(items), "required": req, "dropped": dropped}


def _cycle(graph: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m) is None:
                r = visit(m)
                if r:
                    return r
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(graph):
        if state.get(n) is None:
            r = visit(n)
            if r:
                return r
    return None


def check(data: Any, *, strict: bool = False, what: str = "inventory") -> dict:
    """:func:`validate` or ``BAD_PARAMS`` naming the first errors."""
    res = validate(data, strict=strict)
    if res["errors"]:
        raise SatkError("BAD_PARAMS", f"{what}: {len(res['errors'])} error(s): " + "; ".join(res["errors"][:4]),
                        hint="fix the items named; satk inventory validate <project> lists every error",
                        data={"errors": res["errors"][:30]})
    return res


def path_of(project: str | os.PathLike) -> Path:
    """``<project>/design/inventory.json`` of a project name or folder."""
    from ..studio.project import project_dir

    return project_dir(project) / FILE


def load_file(path: str | os.PathLike) -> dict:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no inventory {p.as_posix()}",
                        hint="satk inventory starter <kind> --project <project> writes a starter to edit") from None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{p.name}: not JSON ({type(e).__name__}: {e})"[:300]) from None
    if not isinstance(data, dict):
        raise SatkError("BAD_PARAMS", f"{p.name} must hold an object {{kind, detail, items}}")
    return data


def load(project: str | os.PathLike) -> tuple[Path, dict]:
    """``(path, data)`` of a project's inventory (not validated)."""
    p = path_of(project)
    return p, load_file(p)


def save(project_path: Path, data: dict) -> Path:
    from ..core import paths

    f = project_path / FILE
    paths.ensure_writable(f.parent).mkdir(parents=True, exist_ok=True)
    return paths.atomic_write(f, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
