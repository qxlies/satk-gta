"""Asset projects (contract K7): ``<project>/asset.json`` - the resumable state of one authored asset.

A project folder (under the work directory, default ``work/assets/<name>``) holds ``asset.json``, the
session journal ``journal.jsonl``, ``checkpoints/``, ``snaps/`` and ``refs/``. ``asset.json``::

    {"asset": 1, "name", "kind", "intent": "replace|add", "like": "model:426", "target": "sp|mta|samp",
     "tier": "vanilla|sa_plus", "dims": {"target": [x, y, z], "like": [x, y, z], "source"},
     "gates": {"G0": "open|done|skipped", ... "G5"}, "decisions": [{"n", "text"}],
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
GATE_STATES = ("open", "done", "skipped")
#: Asset kinds when the kit catalog (data/kit/kinds.json) is not installed.
KINDS = ("automobile", "mtruck", "quad", "bike", "bmx", "boat", "plane", "heli", "trailer", "train",
         "prop", "building", "interior_shell", "interior_prop", "breakable", "animated_object", "weapon", "ped",
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
            out["dims"] = [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)]
    return out


#: Metrics a session's step stats measure (the rest of a profile stays out of the request): exact names
#: and prefixes.
_SESSION_METRICS = frozenset({"geo.tris", "dims.W", "dims.L", "dims.H"})
_SESSION_PREFIXES = ("shade.", "dff.verts_per_tri", "uv.zero_area", "part.tris[")


def _bands(cls_or_sid: str | None, tier: str) -> dict:
    """Tier bands of the peer set for the session stats, exactly as ``asset.check`` judges them:
    ``{metric: {"s": [p10, p50, p90, n], "lo", "hi", "status", "cap"?, "parent"?}}`` (``satk.style`` K2)."""
    if not cls_or_sid:
        return {}
    try:
        from ..style import cache as SK  # lane style (K2); optional
        from ..style import classes as SC
        from ..style.profile import resolve_target, tier_band

        c = SK.load("vanilla")
        tg = resolve_target(str(cls_or_sid), c)
        key = tg["peer_set"]
        peer = c.peers[key]["metrics"]
        chain = SC.fallback_chain(key)
        parent = c.peers.get(chain[1], {}).get("metrics", {}) if len(chain) > 1 else {}
    except Exception:  # noqa: BLE001 - no index, no style cache or no profile for this class: no bands
        return {}
    out: dict = {}
    for m in sorted(peer):
        if m not in _SESSION_METRICS and not m.startswith(_SESSION_PREFIXES):
            continue
        st = peer[m]
        if not isinstance(st, list) or len(st) < 3:
            continue
        tb = tier_band(m, st, key, tier)
        row: dict = {"s": [float(x) for x in st[:4]], "lo": float(tb["lo"]), "hi": float(tb["hi"]),
                     "status": tb["status"]}
        if "cap" in tb:
            row["cap"] = float(tb["cap"])
        if m in parent:
            row["parent"] = [float(x) for x in parent[m][:4]]
        out[m] = row
    return out


def session_config(p: Path, data: dict) -> dict:
    """What a session of this project gets in its request: class bands and target dimensions."""
    out: dict = {}
    dims = (data.get("dims") or {}).get("target")
    if isinstance(dims, list) and len(dims) == 3:
        out["target_dims"] = dims
    bands = _bands(data.get("like") or data.get("class") or _kind_class(data), data.get("tier") or "sa_plus")
    if bands:
        out["bands"] = bands
        out["tier"] = data.get("tier") or "sa_plus"
    return out


def _kind_class(data: dict) -> str | None:
    """The style class of an asset without a like model: its kind and the size bucket of its target size."""
    try:
        from ..style import classes as SC

        kind = str(data.get("kind") or "")
        if not kind:
            return None
        try:
            cls = SC.resolve(kind)
        except SatkError:
            cls = next((c for c, v in SC.taxonomy()["classes"].items() if v.get("kind") == kind), None)
        dims = (data.get("dims") or {}).get("target") or []
        size = max(float(x) for x in dims) if len(dims) == 3 else None
        return SC.peer_key(cls, SC.size_bucket(size)) if cls else None
    except Exception:  # noqa: BLE001 - no style package
        return None


def init(d: str, *, kind: str, intent: str = "add", like: str | None = None, target: str = "sp",
         tier: str = "sa_plus", dims: list[float] | None = None, force: bool = False) -> dict:
    """Create ``asset.json`` (G0 opened); ``like`` fills the target dimensions from the vanilla model."""
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
    save(p, data)
    out = {"project": paths.jpath(p), "name": p.name, "kind": kind, "tier": tier, "intent": intent}
    if data.get("like"):
        out["like"] = data["like"]
    if dd.get("target"):
        out["dims"] = dd["target"]
    out["next"] = (f"satk blender session start --project {p.name}; then G0: style profile and refs "
                   f"(satk ref import <photo> --project {p.name})")
    if warn:
        out["warn"] = warn
    return out


def record(d: str, rec: dict) -> dict:
    """Record decisions, gate states, issues and the last check/export in ``asset.json``.

    ``rec`` keys: ``decision`` (text), ``gate`` + ``state`` (open|done|skipped), ``issue`` (text),
    ``close`` (issue id), ``check`` / ``export`` (a short result object), ``checkpoint`` ({n, tag}),
    ``dims`` ([x, y, z] target), ``step`` (journal step of a decision).
    """
    if not isinstance(rec, dict) or not rec:
        raise SatkError("BAD_PARAMS", "record: give an object such as {\"decision\": \"...\"}")
    known = {"decision", "gate", "state", "issue", "close", "check", "export", "checkpoint", "dims", "step"}
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
    card["gates"] = " ".join(f"{g}{'+' if gates.get(g) == 'done' else ('~' if gates.get(g) == 'skipped' else '-')}"
                             for g in GATES)
    if nxt:
        card["next_gate"] = f"{nxt}: {GATES[nxt]}"
    dims = data.get("dims") or {}
    if dims.get("target"):
        card["dims"] = dims["target"]
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
    for key in ("issues_more", "last_export", "decisions", "snapshot", "last_check", "issues", "journal"):
        if len(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= MAX_CARD:
            break
        card.pop(key, None)
    return card
