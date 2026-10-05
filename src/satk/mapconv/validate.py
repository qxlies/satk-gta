"""``satk map validate``: structural checks of a scene and checks against the asset index.

Structural (no index): coordinates finite and inside the map (``|x|, |y| <= 3000``, the 6 km grid of
streaming sectors; ``z`` in ``[-200, 2500]``; the vanilla world spans x/y +-2994, z -120..1961),
duplicate objects and removals, material slots ``0..15``, material text sizes, SA-MP limits
(1000 ``CreateObject``, about 1000 removals per player), custom (negative) model ids.

Index (profile, default ``vanilla``): every object/material/removal model id is an active model of
the profile; material textures exist (``tex:<txd>/<name>``); each removal hits at least one placement
(3D distance <= radius), and when it removes an HD placement whose LOD stays, the LOD model id is
suggested (report 23 §2.3: SA-MP removes LODs by a separate call, MTA via ``lodModel``). SA-MP ids
(``11682..11753``, ``18631..19999``) are absent from ``vanilla``: the hint says ``--profile samp``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..core.errors import SatkError
from .pawn import MATERIAL_SIZES
from .scene import Issue, Scene, num

__all__ = ["validate_scene", "WORLD_XY", "WORLD_Z", "SAMP_RANGES", "MAX_REMOVALS", "MAX_SAMP_OBJECTS"]

WORLD_XY = 3000.0
WORLD_Z = (-200.0, 2500.0)
SAMP_RANGES = ((11682, 11753), (18631, 19999))
MAX_REMOVALS = 1000
MAX_SAMP_OBJECTS = 1000
_CHUNK = 400


def _is_samp(model: int) -> bool:
    return any(a <= model <= b for a, b in SAMP_RANGES)


def _where(i: int, line: int, prefix: str = "obj") -> str:
    return f"{prefix} {i}" + (f" (line {line})" if line else "")


def _first(sc: Scene, idx: list[int]) -> str:
    """``obj 3 (line 12)`` for the first of several objects, ``+N`` more."""
    return _where(idx[0], sc.objects[idx[0]].line) + (f" +{len(idx) - 1}" if len(idx) > 1 else "")


def _structural(sc: Scene, out: list[Issue]) -> None:
    custom = {m.id for m in sc.models}
    seen: dict[tuple, int] = {}
    for i, o in enumerate(sc.objects):
        w = _where(i, o.line)
        vals = (*o.pos, *o.rot)
        if not all(math.isfinite(v) for v in vals):
            out.append(Issue("error", "BAD_COORD", w, f"non-finite position/rotation {list(vals)}"))
            continue
        x, y, z = o.pos
        if abs(x) > WORLD_XY or abs(y) > WORLD_XY:
            out.append(Issue("warn", "OUT_OF_MAP", w, f"({num(x, 2)}, {num(y, 2)}) is outside the map "
                                                      f"(|x|,|y| <= {num(WORLD_XY)}): edge streaming sectors"))
        if not WORLD_Z[0] <= z <= WORLD_Z[1]:
            out.append(Issue("warn", "Z_RANGE", w, f"z = {num(z, 2)} outside {num(WORLD_Z[0])}..{num(WORLD_Z[1])}"))
        if o.model < 0 and o.model not in custom:
            out.append(Issue("error", "MODEL_UNKNOWN", w, f"custom model {o.model} is not defined by AddSimpleModel"))
        key = (o.model, *(round(v, 3) for v in o.pos), *(round(v, 2) for v in o.rot), o.interior, o.world)
        if key in seen:
            out.append(Issue("warn", "DUP_OBJECT", w, f"same model, position and rotation as obj {seen[key]}"))
        else:
            seen[key] = i
        for m in o.materials:
            if not 0 <= m.slot <= 15:
                out.append(Issue("error", "MAT_SLOT", w, f"material slot {m.slot} (valid 0..15)"))
        for t in o.texts:
            if not 0 <= t.slot <= 15:
                out.append(Issue("error", "MAT_SLOT", w, f"material text slot {t.slot} (valid 0..15)"))
            if t.size not in MATERIAL_SIZES:
                out.append(Issue("warn", "TEXT_SIZE", w, f"materialsize {t.size} is not one of "
                                                         f"{', '.join(map(str, MATERIAL_SIZES))}"))
            if t.align not in (0, 1, 2):
                out.append(Issue("warn", "TEXT_ALIGN", w, f"text alignment {t.align} (0 left, 1 center, 2 right)"))
    n_native = sum(1 for o in sc.objects if o.func in ("CreateObject", "CreatePlayerObject"))
    if n_native > MAX_SAMP_OBJECTS:
        out.append(Issue("warn", "OBJECT_LIMIT", "file", f"{n_native} CreateObject calls: SA-MP allows "
                                                         f"{MAX_SAMP_OBJECTS} (use CreateDynamicObject)"))
    rseen: dict[tuple, int] = {}
    for i, r in enumerate(sc.removals):
        w = _where(i, r.line, "rm")
        if not all(math.isfinite(v) for v in (*r.pos, r.radius)):
            out.append(Issue("error", "BAD_COORD", w, "non-finite removal position/radius"))
            continue
        if r.radius <= 0:
            out.append(Issue("warn", "REMOVE_RADIUS", w, f"radius {num(r.radius)} removes nothing"))
        key = (r.model, *(round(v, 3) for v in r.pos), round(r.radius, 3))
        if key in rseen:
            out.append(Issue("warn", "REMOVE_DUP", w, f"same removal as rm {rseen[key]} "
                                                      "(removing a building twice crashes SA-MP clients)"))
        else:
            rseen[key] = i
    if len(sc.removals) > MAX_REMOVALS:
        out.append(Issue("warn", "REMOVE_LIMIT", "file", f"{len(sc.removals)} removals: SA-MP keeps about "
                                                         f"{MAX_REMOVALS} per player"))


def _known_models(db, ids: set[int]) -> set[int]:
    want = sorted(i for i in ids if i >= 0)
    have: set[int] = set()
    for k in range(0, len(want), _CHUNK):
        part = want[k:k + _CHUNK]
        env = db.query(f"SELECT id FROM v_model WHERE id IN ({','.join('?' * len(part))})", part, limit=500)
        have.update(int(r[0]) for r in env["rows"])
    return have


def _known_textures(db, pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    txds = sorted({t for t, _ in pairs})
    have: set[tuple[str, str]] = set()
    for k in range(0, len(txds), 50):
        part = txds[k:k + 50]
        sql = ("SELECT lower(t.name), lower(x.name) FROM texture x JOIN txd t ON t.id = x.txd_id "
               f"WHERE t.name IN ({','.join('?' * len(part))})")
        off = 0
        while True:  # a TXD can hold many textures: page through (500 rows per query)
            env = db.query(f"{sql} LIMIT 500 OFFSET {off}", part, limit=500)
            have.update((r[0], r[1]) for r in env["rows"])
            if len(env["rows"]) < 500:
                break
            off += 500
    return have


def _model_hint(model: int, profile: str) -> str:
    if _is_samp(model) and profile != "samp":
        return " (an SA-MP object id: validate with --profile samp)"
    return ""


def _removal_hits(db, r) -> list[list]:
    x, y, z = r.pos
    rad = max(r.radius, 0.0)
    sql = ("SELECT i.model_id, i.x, i.y, i.z, l.model_id, l.x, l.y, l.z FROM inst i "
           "LEFT JOIN inst l ON l.id = i.lod_id WHERE i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ? "
           "AND i.z BETWEEN ? AND ?")
    params: list = [x - rad, x + rad, y - rad, y + rad, z - rad, z + rad]
    if r.model >= 0:
        sql += " AND i.model_id = ?"
        params.append(r.model)
    rows = db.query(sql, params, limit=500)["rows"]
    return [row for row in rows if math.dist((row[1], row[2], row[3]), r.pos) <= rad + 1e-6]


def _removed(sc: Scene, model: int, pos) -> bool:
    for r in sc.removals:
        if (r.model in (model, -1) or r.lod_model == model) and math.dist(pos, r.pos) <= r.radius + 1e-6:
            return True
    return False


def _against_index(sc: Scene, db, profile: str, out: list[Issue]) -> None:
    obj_models: dict[int, list[int]] = defaultdict(list)
    for i, o in enumerate(sc.objects):
        obj_models[o.model].append(i)
    mat_models: dict[int, list[int]] = defaultdict(list)
    tex_use: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, o in enumerate(sc.objects):
        for m in o.materials:
            if m.model >= 0:
                mat_models[m.model].append(i)
            if m.txd.lower() != "none" and m.tex.lower() != "none" and m.txd and m.tex:
                tex_use[(m.txd.lower(), m.tex.lower())].append(i)
    rm_models = {r.model for r in sc.removals if r.model >= 0} | {r.lod_model for r in sc.removals if r.lod_model}
    known = _known_models(db, set(obj_models) | set(mat_models) | rm_models)
    for model, idx in sorted(obj_models.items()):
        if model >= 0 and model not in known:
            out.append(Issue("error", "MODEL_UNKNOWN", _first(sc, idx),
                             f"model {model} is not in profile {profile!r}{_model_hint(model, profile)}"))
    for model, idx in sorted(mat_models.items()):
        if model not in known:
            out.append(Issue("warn", "MAT_MODEL_UNKNOWN", _first(sc, idx),
                             f"material source model {model} is not in profile {profile!r}"))
    if tex_use:
        have = _known_textures(db, set(tex_use))
        for (txd, tex), idx in sorted(tex_use.items()):
            if (txd, tex) not in have:
                out.append(Issue("warn", "MAT_TEX_UNKNOWN", _first(sc, idx),
                                 f"texture tex:{txd}/{tex} is not in profile {profile!r}"))
    for i, r in enumerate(sc.removals):
        w = _where(i, r.line, "rm")
        if r.model >= 0 and r.model not in known:
            out.append(Issue("warn", "REMOVE_MODEL_UNKNOWN", w,
                             f"model {r.model} is not in profile {profile!r}{_model_hint(r.model, profile)}"))
            continue
        if not all(math.isfinite(v) for v in (*r.pos, r.radius)):
            continue
        hits = _removal_hits(db, r)
        if not hits:
            at = ", ".join(num(c, 2) for c in r.pos)
            out.append(Issue("warn", "REMOVE_NOTHING", w,
                             f"no placement of model {r.model} within {num(r.radius)} m of ({at})"))
            continue
        missing = Counter()
        for row in hits:
            lod_model = row[4]
            if lod_model is not None and not _removed(sc, int(lod_model), (row[5], row[6], row[7])):
                missing[int(lod_model)] += 1
        for lod_model, n in sorted(missing.items()):
            out.append(Issue("warn", "REMOVE_NO_LOD", w,
                             f"removes {n} placement(s) of model {r.model} whose LOD model {lod_model} stays: add "
                             f"RemoveBuildingForPlayer(playerid, {lod_model}, ...) or lodModel=\"{lod_model}\""))


def validate_scene(sc: Scene, profile: str = "vanilla", use_index: bool = True) -> tuple[list[Issue], str | None]:
    """All issues (reader diagnostics + structural + index) and, if the index was unusable, why."""
    out: list[Issue] = list(sc.issues)
    _structural(sc, out)
    reason = None
    if use_index:
        try:
            from ..index.api import open_index

            db = open_index(profile)
            _against_index(sc, db, profile, out)
        except SatkError as e:
            if e.code not in ("INDEX_MISSING", "NOT_READY"):
                raise
            reason = f"{e.code}: {e.msg}"
    order = {"error": 0, "warn": 1, "info": 2}
    out.sort(key=lambda i: order.get(i.sev, 3))  # stable: source order inside a severity
    return out, reason
