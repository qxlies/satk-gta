"""Asset projects (contract K7): ``<project>/asset.json`` - the resumable state of one authored asset.

A project folder (under the work directory, default ``work/assets/<name>``) holds ``asset.json``, the
session journal ``journal.jsonl``, ``checkpoints/``, ``snaps/`` and ``refs/``. ``asset.json``::

    {"asset": 1, "name", "kind", "intent": "replace|add", "like": "model:426", "target": "sp|mta|samp",
     "tier": "vanilla|sa_plus", "dims": {"target": [x, y, z], "like": [x, y, z], "source"},
     "gates": {"G0": "open|review|done|skipped", ... "G5"}, "gate_notes": {"G3": "why it was skipped"},
     "decisions": [{"n", "text"}],
     "checkpoints": [{"n", "tag"}], "last_export": {...}, "last_check": {...},
     "issues": [{"id", "text", "open"}], "session": "<name>"}

Only the studio writes it: :func:`init`, :func:`record` (``asset.status --record``) and the session
(gate checkpoints). :func:`status` is the resume card (at most 1 KB) an agent reads after a restart.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError

__all__ = ["VERSION", "GATES", "KINDS", "INTENTS", "TARGETS", "TIERS", "MAX_CARD", "project_dir", "load", "save",
           "init", "record", "status", "kinds", "like_info"]

VERSION = 1
#: Gates of the authoring workflow (docs/agent workflows: create an asset).
GATES = {
    "G0": "asset.init, class profile, references",
    "G1": "blockout at scale + lineup sheet for a human look",
    "G2": "shape and shading metrics in band",
    "G3": "parts, damage, LOD, collision",
    "G4": "UV and textures (style.texture)",
    "G5": "export, asset.check, lint, package",
}
#: ``review``: the builder handed the gate in (a reviewer or the harness closes it); ``done`` needs the gate's
#: inventory items built (:func:`gate_open_items`); ``skipped`` needs a reason (``why``), listed on the card.
GATE_STATES = ("open", "review", "done", "skipped")
#: Asset kinds when the kit catalog (data/kit/kinds.json) is not installed.
KINDS = ("automobile", "mtruck", "quad", "bike", "bmx", "boat", "plane", "heli", "trailer", "train",
         "prop", "building", "interior_shell", "interior_prop", "breakable", "animated_object", "weapon",
         "weapon_melee", "ped",
         "pickup", "vehicle_upgrade")
INTENTS = ("replace", "add")
TARGETS = ("sp", "mta", "samp")
TIERS = ("vanilla", "sa_plus")
MAX_CARD = 1024
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FILE = "asset.json"
_TEXT_MAX = 200


def kinds() -> tuple[str, ...]:
    """Kinds of the kit catalog (lane kit) when present, else :data:`KINDS`."""
    try:
        from ..core.resources import read_json

        data = read_json("kit/kinds.json")
        names = data.get("kinds") if isinstance(data, dict) else None
        if isinstance(names, dict):
            return tuple(sorted(names))
        if isinstance(names, list):
            return tuple(sorted(str(k.get("kind") if isinstance(k, dict) else k) for k in names))
    except Exception:  # noqa: BLE001 - the catalog is optional
        pass
    return KINDS


def _inside(p: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(str(p)), os.path.normcase(str(root))]) == os.path.normcase(str(root))
    except ValueError:
        return False


def project_dir(d: str | os.PathLike) -> Path:
    """A project folder: a bare name means ``work/assets/<name>``; a path must lie under the work directory."""
    s = os.fspath(d).strip()
    if not s:
        raise SatkError("BAD_PARAMS", "the project folder is empty")
    work = Path(os.path.abspath(paths.cfg().paths.work))
    if not any(ch in s for ch in "/\\:") and s not in (".", ".."):
        if not NAME_RE.match(s.lower()):
            raise SatkError("BAD_PARAMS", f"bad project name {s!r} (a-z, 0-9, '-', '_'; at most 32 chars)")
        return work / "assets" / s.lower()
    p = Path(os.path.abspath(s))
    if not _inside(p, work):
        raise SatkError("PROTECTED_PATH", f"{paths.jpath(p)} is outside the work directory",
                        hint=f"use a name (-> {paths.jpath(work / 'assets')}/<name>) or a folder under the work "
                             "directory")
    return p


def load(d: str | os.PathLike) -> tuple[Path, dict]:
    p = project_dir(d)
    f = p / FILE
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no asset project in {paths.jpath(p)}",
                        hint=f"satk asset init {p.name} --kind <kind>") from None
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{paths.jpath(f)}: {e}") from None
    if not isinstance(data, dict) or data.get("asset") != VERSION:
        raise SatkError("BAD_PARAMS", f"{paths.jpath(f)} is not an asset.json of version {VERSION}")
    return p, data


def save(p: Path, data: dict) -> Path:
    return paths.atomic_write(p / FILE, json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n")


def _text(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise SatkError("BAD_PARAMS", f"{what} must be a non-empty string")
    return v.strip()[:_TEXT_MAX]


def like_info(sid: str, profile: str = "vanilla") -> dict:
    """``{"sid", "name", "sec", "dims": [x, y, z]}`` of a vanilla model from the index (numbers only)."""
    from ..index.api import open_index

    s = sid.strip()
    key: Any = s.split(":", 1)[1] if s.lower().startswith("model:") else s
    if isinstance(key, str) and key.lstrip("-").isdigit():
        key = int(key)
    db = open_index(profile)
    mf = db.model_files(key)
    out: dict = {"sid": f"model:{mf.model_id}", "name": mf.name}
    if mf.sec:
        out["sec"] = mf.sec
    if mf.dff is not None:
        stem = mf.dff.name.rsplit(".", 1)[0]
        env = db.query("SELECT bmin_x, bmin_y, bmin_z, bmax_x, bmax_y, bmax_z FROM dff WHERE name = ? "
                       "AND bmin_x IS NOT NULL", [stem], limit=1)
        if env.get("rows"):
            b = env["rows"][0]
            d = [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)]
            if str(mf.sec or "").lower() == "peds":
                d = [d[0], d[2], d[1]]      # the ped bind pose lies along Y (head +Y): a standing ped is Z-up
            out["dims"] = d
    return out


def session_config(p: Path, data: dict) -> dict:
    """What a session of this project gets in its request: the target dimensions (step stats judge no number
    against a band, so no class bands are sent)."""
    out: dict = {}
    dims = (data.get("dims") or {}).get("target")
    if isinstance(dims, list) and len(dims) == 3:
        out["target_dims"] = dims
    return out


def init(d: str, *, kind: str, intent: str = "add", like: str | None = None, target: str = "sp",
         tier: str = "sa_plus", dims: list[float] | None = None, force: bool = False,
         detail: str | None = "hero") -> dict:
    """Create ``asset.json`` (G0 opened); ``like`` fills the target dimensions from the vanilla model; ``detail``
    (simple|standard|hero, ``None``/``"none"``: no inventory) seeds ``design/inventory.json`` from the kind's starter
    list unless the project already has one."""
    ks = kinds()
    if kind not in ks:
        import difflib

        raise SatkError("BAD_PARAMS", f"unknown kind {kind!r}", data={"kinds": list(ks)},
                        did_you_mean=difflib.get_close_matches(kind, ks, n=3, cutoff=0.5))
    for v, allowed, what in ((intent, INTENTS, "intent"), (target, TARGETS, "target"), (tier, TIERS, "tier")):
        if v not in allowed:
            raise SatkError("BAD_PARAMS", f"{what} must be one of {', '.join(allowed)}, got {v!r}")
    if intent == "replace" and not like:
        raise SatkError("BAD_PARAMS", "a replacement needs --like <SID> (the model it replaces)")
    p = project_dir(d)
    if (p / FILE).exists() and not force:
        raise SatkError("EXISTS", f"{paths.jpath(p / FILE)} exists", hint="satk asset status " + p.name +
                        " (resume) or --force to start over")
    warn: list[str] = []
    data: dict[str, Any] = {"asset": VERSION, "name": p.name, "kind": kind, "intent": intent, "target": target,
                            "tier": tier}
    dd: dict[str, Any] = {}
    if like:
        try:
            info = like_info(like)
            data["like"] = info["sid"]
            data["like_name"] = info["name"]
            if info.get("dims"):
                dd["like"] = info["dims"]
                dd["target"] = info["dims"]
                dd["source"] = "like"
        except SatkError as e:
            if e.code in ("NOT_FOUND", "BAD_ID"):
                raise
            data["like"] = like
            warn.append(f"{e.code}: like {like}: {e.msg}")
    if like:
        _check_like_kind(kind, data.get("like"), warn)
    if dims is not None:
        if len(dims) != 3 or not all(isinstance(x, (int, float)) and x > 0 for x in dims):
            raise SatkError("BAD_PARAMS", "dims must be three positive numbers: width (x), length (y), height (z)")
        dd["target"] = [float(x) for x in dims]
        dd["source"] = "given"
    if dd:
        data["dims"] = dd
    data["gates"] = {g: "open" for g in GATES}
    data["decisions"] = []
    data["checkpoints"] = []
    data["issues"] = []
    for sub in ("refs", "snaps", "checkpoints", "out"):
        paths.ensure_writable(p / sub).mkdir(parents=True, exist_ok=True)
    inv_out = _seed_inventory(p, kind, detail, warn)
    if inv_out:
        data["detail"] = inv_out["detail"]
    elif detail in (None, "", "none"):
        data["detail"] = "none"         # no inventory: asset.check --strict will not call this asset done
        warn.append("NO_INVENTORY: --detail none: the asset has no itemised task list, so asset.check --strict "
                    "never reports it done (re-skins and texture jobs use mod check)")

    save(p, data)
    out = {"project": paths.jpath(p), "name": p.name, "kind": kind, "tier": tier, "intent": intent}
    if data.get("like"):
        out["like"] = data["like"]
    if dd.get("target"):
        out["dims"] = dd["target"]
    if inv_out:
        out["inventory"] = inv_out
    out["next"] = (f"satk blender session start --project {p.name}; then G0: style profile and refs "
                   f"(satk ref import <photo> --project {p.name})"
                   + (f", and make design/inventory.json the real design (satk asset inventory {p.name} --plan)"
                      if inv_out else ""))
    if warn:
        out["warn"] = warn
    return out


def _seed_inventory(p: Path, kind: str, detail: str | None, warn: list[str]) -> dict | None:
    """``design/inventory.json`` from the starter of ``kind`` (an existing inventory is kept)."""
    if detail in (None, "", "none"):
        return None
    try:
        from ..inventory import schema as SC
        from ..inventory import starter as ST
    except ImportError:
        return None
    f = p / SC.FILE
    if f.is_file():
        try:
            inv = SC.load_file(f)
            return {"file": paths.jpath(f), "detail": inv.get("detail"), "items": len(inv.get("items") or []),
                    "kept": True}
        except SatkError as e:
            warn.append(f"{e.code}: {e.msg}")
            return None
    try:
        inv = ST.instantiate(kind, detail)
    except SatkError as e:
        warn.append(f"{e.code}: no starter inventory: {e.msg}")
        return None
    SC.save(p, inv)
    return {"file": paths.jpath(f), "detail": detail, "items": len(inv["items"])}


def record(d: str, rec: dict) -> dict:
    """Record decisions, gate states, issues and the last check/export in ``asset.json``.

    ``rec`` keys: ``decision`` (text), ``gate`` + ``state`` (open|review|done|skipped) + ``why`` (the reason of a
    skip), ``issue`` (text), ``close`` (issue id), ``check`` / ``export`` (a short result object), ``checkpoint``
    ({n, tag}), ``dims`` ([x, y, z] target), ``step`` (journal step of a decision). ``done`` of G1-G5 is refused
    while the gate's required inventory items are not built (``asset.inventory --stage``).
    """
    if not isinstance(rec, dict) or not rec:
        raise SatkError("BAD_PARAMS", "record: give an object such as {\"decision\": \"...\"}")
    known = {"decision", "gate", "state", "why", "issue", "close", "check", "export", "checkpoint", "dims", "step"}
    extra = set(rec) - known
    if extra:
        raise SatkError("BAD_PARAMS", f"record: unknown key(s) {sorted(extra)}", data={"keys": sorted(known)})
    p, data = load(d)
    done: list[str] = []
    if "decision" in rec:
        row: dict[str, Any] = {"text": _text(rec["decision"], "decision")}
        if isinstance(rec.get("step"), int):
            row["n"] = rec["step"]
        data.setdefault("decisions", []).append(row)
        done.append("decision")
    if "gate" in rec:
        g = str(rec["gate"]).upper()
        if g not in GATES:
            raise SatkError("BAD_PARAMS", f"gate must be one of {', '.join(GATES)}")
        st = rec.get("state", "done")
        if st not in GATE_STATES:
            raise SatkError("BAD_PARAMS", f"state must be one of {', '.join(GATE_STATES)}")
        notes = data.setdefault("gate_notes", {})
        if st == "skipped":
            why = rec.get("why")
            if not isinstance(why, str) or len(why.strip()) < 8:
                raise SatkError("BAD_PARAMS", f"skipping {g} needs a reason: {{\"gate\": \"{g}\", \"state\": "
                                              "\"skipped\", \"why\": \"<what makes this gate not apply>\"}")
            notes[g] = why.strip()[:_TEXT_MAX]
        elif st == "done" and g != "G0":
            left = gate_open_items(p, g)
            if left:
                raise SatkError("BAD_PARAMS", f"{g} is not done: {len(left)} required inventory item(s) are not "
                                              f"built: {'; '.join(left[:6])}",
                                hint=f"build and tag them (satk asset inventory {p.name} --stage {g}); a reviewer "
                                     "closes a gate handed in with state review")
            notes.pop(g, None)
        else:
            notes.pop(g, None)
        if not notes:
            data.pop("gate_notes", None)
        data.setdefault("gates", {})[g] = st
        done.append(f"{g}={st}")
    if "issue" in rec:
        issues = data.setdefault("issues", [])
        nid = max([i.get("id", 0) for i in issues] + [0]) + 1
        issues.append({"id": nid, "text": _text(rec["issue"], "issue"), "open": True})
        done.append(f"issue {nid}")
    if "close" in rec:
        hit = [i for i in data.get("issues", []) if i.get("id") == rec["close"]]
        if not hit:
            raise SatkError("NOT_FOUND", f"no issue {rec['close']!r}")
        hit[0]["open"] = False
        done.append(f"closed {rec['close']}")
    for key in ("check", "export"):
        if key in rec:
            if not isinstance(rec[key], dict):
                raise SatkError("BAD_PARAMS", f"{key} must be an object")
            blob = json.dumps(rec[key], ensure_ascii=False)
            if len(blob) > 2000:
                raise SatkError("BAD_PARAMS", f"{key}: keep it short (at most 2,000 characters; paths and counts)")
            data[f"last_{key}"] = rec[key]
            done.append(key)
    if "checkpoint" in rec:
        c = rec["checkpoint"]
        if not isinstance(c, dict) or not isinstance(c.get("n"), int):
            raise SatkError("BAD_PARAMS", "checkpoint must be {\"n\": step, \"tag\": \"G1\"}")
        cps = [x for x in data.setdefault("checkpoints", []) if x.get("tag") != c.get("tag") or not c.get("tag")]
        cps.append({k: c[k] for k in ("n", "tag") if k in c})
        data["checkpoints"] = cps[-50:]
        done.append("checkpoint")
    if "dims" in rec:
        v = rec["dims"]
        if not isinstance(v, list) or len(v) != 3 or not all(isinstance(x, (int, float)) and x > 0 for x in v):
            raise SatkError("BAD_PARAMS", "dims must be [x, y, z] in metres")
        data.setdefault("dims", {})["target"] = [float(x) for x in v]
        data["dims"]["source"] = "given"
        done.append("dims")
    save(p, data)
    return {"project": paths.jpath(p), "recorded": done}


def gate_open_items(p: Path, gate: str) -> list[str]:
    """Required inventory items of the gates up to ``gate`` (G5: all) that are not built, as ``"I05 Rim: missing"``
    (empty: none, or the project has no inventory). The report measures the running session or the newest
    checkpoint; when it cannot run, that is the one row."""
    if not (p / "design" / "inventory.json").is_file():
        return []
    try:
        from ..inventory.api import report
    except ImportError:
        return []
    order = ("G1", "G2", "G3", "G4")
    upto = order if gate == "G5" else order[: order.index(gate) + 1] if gate in order else order
    try:
        rep = report(p, strict=True)
    except SatkError as e:
        return [f"the inventory report could not run ({e.code}: {e.msg})"[:200]]
    out = [f"{r['id']} {r.get('name', '')}: {r['status']}" for r in rep.get("items") or []
           if r.get("required") and r.get("stage") in upto and r.get("status") != "built"]
    if gate == "G5" and rep.get("problems"):
        out += [f"inventory: {x}"[:160] for x in rep["problems"][:3]]
    return out


def _check_like_kind(kind: str, like: str | None, warn: list[str]) -> None:
    """The ``--like`` model must be of the kind's family (a pickup truck is not the collectible kind ``pickup``)."""
    if not like:
        return
    try:
        from ..kit.kinds import get
        from ..look import lineup as LU
        from ..look import regions as RG

        row = LU.model_row(str(like), "vanilla")
        lk = RG.kind_of_row(row)
    except Exception:  # noqa: BLE001 - no index or no catalog: nothing to compare
        return
    if not lk or lk == kind:
        return
    try:
        ga, gb = get(kind).get("group"), get(lk).get("group")
    except SatkError:
        return
    if ga != gb or (kind != lk and {kind, lk} & {"pickup", "vehicle_upgrade"}):
        raise SatkError("BAD_PARAMS", f"--like {like} is a {lk} ({gb}), not a {kind} ({ga}): the kind decides the "
                                      "inventory, regions and checks",
                        hint=f"satk asset init <name> --kind {lk} --like {like}, or a --like model of kind {kind}")
    warn.append(f"KIND: --like {like} is a {lk}; the asset is a {kind} (same family, kept)")


def _short(v: Any, n: int = 80) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return s if len(s) <= n else s[: n - 3] + "..."


def journal_summary(p: Path) -> dict:
    """Steps, the last method, failures and the newest snapshot of a project's journal."""
    from .core import Journal

    entries = Journal(p / "journal.jsonl").entries()
    if not entries:
        return {}
    out: dict = {"steps": max((e.get("n") or 0) for e in entries)}
    calls = [e for e in entries if e.get("method") not in ("session.start", "session.end")]
    if calls:
        out["last"] = calls[-1].get("method")
    bad = sum(1 for e in entries if e.get("ok") is False)
    if bad:
        out["failed"] = bad
    return out


def _inventory_line(p: Path) -> str | None:
    """``"hero, 58 items (52 required)"`` of the project's inventory (no measuring)."""
    f = p / "design" / "inventory.json"
    if not f.is_file():
        return None
    try:
        inv = json.loads(f.read_text(encoding="utf-8-sig"))
        items = [i for i in inv.get("items") or [] if isinstance(i, dict)]
    except (OSError, ValueError, AttributeError):
        return "unreadable: satk inventory validate " + p.name
    req = sum(1 for i in items if i.get("required", True) is not False)
    return f"{inv.get('detail')}, {len(items)} items ({req} required): satk asset inventory {p.name}"


def _newest(folder: Path, pattern: str) -> Path | None:
    try:
        files = sorted(folder.glob(pattern), key=lambda f: f.name)
    except OSError:
        return None
    return files[-1] if files else None


def status(d: str, *, session: dict | None = None) -> dict:
    """The resume card of a project (at most :data:`MAX_CARD` bytes)."""
    from .core import Checkpoints

    p, data = load(d)
    gates = data.get("gates") or {}
    nxt = next((g for g in GATES if gates.get(g, "open") == "open"), None)
    card: dict[str, Any] = {"name": data.get("name"), "kind": data.get("kind"), "tier": data.get("tier"),
                            "intent": data.get("intent")}
    if data.get("like"):
        card["like"] = data["like"]
    card["target"] = data.get("target")
    marks = {"done": "+", "skipped": "~", "review": "?"}
    card["gates"] = " ".join(f"{g}{marks.get(gates.get(g), '-')}" for g in GATES)
    if data.get("gate_notes"):
        card["skipped"] = {g: _short(t, 60) for g, t in data["gate_notes"].items()}
    if nxt:
        card["next_gate"] = f"{nxt}: {GATES[nxt]}"
    dims = data.get("dims") or {}
    if dims.get("target"):
        card["dims"] = dims["target"]
    inv = _inventory_line(p)
    if inv:
        card["inventory"] = inv
    js = journal_summary(p)
    if js:
        card["journal"] = js
    cps = Checkpoints(p / "checkpoints", "blend", lambda _p: None, lambda _p: None).list()
    if cps:
        tags = [c["tag"] for c in cps if c.get("tag")]
        card["checkpoints"] = {"count": len(cps), "last": cps[-1]["n"], **({"tags": tags[-5:]} if tags else {})}
    if session:
        card["session"] = session
    dec = data.get("decisions") or []
    if dec:
        card["decisions"] = [_short(x.get("text", ""), 70) for x in dec[-3:]]
    issues = [i for i in data.get("issues") or [] if i.get("open")]
    if issues:
        card["issues"] = [f"#{i['id']} {_short(i.get('text', ''), 60)}" for i in issues[:3]]
        if len(issues) > 3:
            card["issues_more"] = len(issues) - 3
    for key in ("last_check", "last_export"):
        if data.get(key):
            card[key] = _short(data[key], 120)
    snap = _newest(p / "snaps", "*.jpg")
    if snap is not None:
        card["snapshot"] = paths.jpath(snap)
    card["dir"] = paths.jpath(p)
    if session is None or not session.get("up"):
        card["resume"] = f"satk blender session start --project {p.name}"
    # stay inside the budget: drop the least important fields first
    for key in ("issues_more", "last_export", "decisions", "snapshot", "last_check", "issues", "journal", "inventory"):
        if len(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= MAX_CARD:
            break
        card.pop(key, None)
    return card
