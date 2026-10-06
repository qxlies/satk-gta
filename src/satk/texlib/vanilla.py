"""Read-only, pixel-free discovery and export planning for vanilla map textures.

The index's mean_rgba is an approximate DXT endpoint mean, not decoded pixels.
A DFF stores texture names; its IDE definition selects one TXD plus that TXD's
parent chain. Merely using the same name never loads another dictionary.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from .catalog import COLOURS, colour as parse_colour


ROLE_WORDS = {
    "wall": ("wall", "brick", "concrete", "conc", "plaster", "stucco", "facade", "bldg"),
    "road": ("road", "tarmac", "asphalt", "pave", "freeway", "highway"),
    "ground": ("ground", "grnd", "dirt", "sand", "mud", "grass", "gravel", "rock", "stone"),
    "roof": ("roof", "rooftop", "slate", "shingle"),
    "floor": ("floor", "tile", "pave", "carpet"),
    "wood": ("wood", "plank", "timber", "crate", "fence"),
    "metal": ("metal", "steel", "rust", "iron", "corrug", "tin", "mesh"),
    "glass": ("glass", "window", "wind", "glaze"),
    "vegetation": ("grass", "leaf", "leaves", "bush", "tree", "palm", "bark", "ivy"),
    "fabric": ("fabric", "cloth", "carpet", "curtain", "leather", "seat"),
    "prop": ("wood", "metal", "crate", "barrel", "bin", "sign", "plastic", "rubber"),
    "decal": ("sign", "decal", "graff", "poster", "logo", "notice", "mark"),
}
SYNONYMS = {"asphalt": ("asphalt", "tarmac", "road"), "concrete": ("concrete", "conc", "cement"),
            "plaster": ("plaster", "stucco"), "grass": ("grass", "grss"), "brick": ("brick", "brck")}
MAP_CLASSES = frozenset(("map", "object", "world", "prop", "building", "interior_shell", "interior_prop", "lod"))
CONSTRAINTS = {
    "use": "Map objects only; not vehicles, vehicle upgrades, peds or weapons.",
    "reference": "Write texture_name into the DFF material and ide_txd into the object's IDE TXD field, without .txd.",
    "pixels": "Reference only: do not extract, copy or pack the vanilla texture pixels into the mod.",
    "dictionary": "One IDE TXD per model; all materials must resolve in it or its registered txdp parent chain.",
    "streaming": "The vanilla TXD and its parents must exist in registered archives and load with the model. "
                 "A matching texture name or a nearby object does not keep a TXD loaded; request the model and let "
                 "the engine stream its IDE dictionary before displaying it.",
    "replacement": "Replacing a DFF does not change its existing IDE TXD field. Supply the matching IDE definition.",
}
KIT_NOTE = ("Use satk.texlib.vanilla.material_preset and export_plan for vanilla:<txd>/<tex>. "
            "The kit owner must connect the material resolver and IDE packaging hooks; see docs/en/texlib.md.")

_JOINS = """
    FROM texture t JOIN txd d ON d.id=t.txd_id JOIN blob b ON b.id=d.blob_id
    JOIN image i ON i.hash=t.hash
"""
_MAP_USE = """EXISTS (SELECT 1 FROM model_tex mt JOIN model m ON m.id=mt.model_id AND m.active=1
                      WHERE mt.texture_id=t.id AND m.sec IN ('objs','tobj'))"""


def _db(db=None):
    from ..index.api import open_index

    return db if db is not None else open_index("vanilla")


def _like(word):
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%").replace("?", "_") + "%"


def _words_clause(words, params):
    params.extend(_like(w) for w in words)
    return "(" + " OR ".join("lower(t.name) LIKE ? ESCAPE '\\'" for _ in words) + ")"


def _role(name):
    for role, words in ROLE_WORDS.items():
        if any(w in name for w in words):
            return role
    return "prop"


def _search_input(query, role, colour):
    if not isinstance(query, str) or not query.strip() or len(query) > 256:
        raise SatkError("BAD_PARAMS", "query must contain 1 to 256 characters", hint="use * to search by role or colour alone")
    role = role.strip().lower()
    if role != "auto" and role not in ROLE_WORDS:
        raise SatkError("BAD_PARAMS", f"unknown vanilla texture role {role!r}", hint="roles: auto, " + ", ".join(ROLE_WORDS))
    rgb = parse_colour(colour)
    terms = []
    for token in query.strip().lower().split():
        if token in COLOURS or re.fullmatch(r"#[0-9a-f]{6}", token):
            if rgb is None:
                rgb = parse_colour(token)
            else:
                terms.append(token)
        else:
            terms.append(token)
    return terms, role, rgb


def search(query: str, *, role="auto", colour=None, limit=20, cursor=None, db=None) -> dict:
    terms, role, rgb = _search_input(query, role, colour)
    limit = clamp_limit(limit)
    fingerprint = hashlib.sha256(json.dumps([terms, role, rgb], separators=(",", ":")).encode()).hexdigest()[:12]
    offset = 0
    if cursor:
        m = re.fullmatch(r"o(\d+)-([0-9a-f]{12})", cursor)
        if m is None or m[2] != fingerprint or len(m[1]) > 9:
            raise SatkError("BAD_PARAMS", "invalid cursor for this vanilla texture search", hint="omit --cursor to start again")
        offset = int(m[1])
    params = []
    where = ["b.active=1", _MAP_USE]
    for term in terms:
        where.append(_words_clause(SYNONYMS.get(term, (term,)), params))
    if role != "auto":
        where.append(_words_clause(ROLE_WORDS[role], params))
    distance = "0"
    distance_params = []
    if rgb is not None:
        where.append("i.mean_rgba IS NOT NULL")
        parts = []
        for shift, channel in zip((24, 16, 8), rgb):
            parts.append(f"((((i.mean_rgba >> {shift}) & 255)-?) * (((i.mean_rgba >> {shift}) & 255)-?))")
            distance_params.extend((channel, channel))
        distance = " + ".join(parts)
    db = _db(db)
    clause = " AND ".join(where)
    total = db.query("SELECT count(*) " + _JOINS + " WHERE " + clause, params, limit=1)["rows"][0][0]
    sql = f"""SELECT 'tex:'||lower(d.name)||'/'||lower(t.name), lower(t.name), lower(d.name),
                     t.w, t.h, t.d3dfmt, t.alpha, i.mean_rgba, ({distance}) AS distance,
                     (SELECT count(*) FROM model_tex mt JOIN model m ON m.id=mt.model_id AND m.active=1
                      WHERE mt.texture_id=t.id AND m.sec IN ('objs','tobj')) AS map_models,
                     (SELECT 'model:'||m.id FROM model_tex mt JOIN model m ON m.id=mt.model_id AND m.active=1
                      LEFT JOIN model_link ml ON ml.id=m.id
                      WHERE mt.texture_id=t.id AND m.sec IN ('objs','tobj')
                      ORDER BY coalesce(ml.n_inst,0) DESC, m.id LIMIT 1) AS example
              {_JOINS} WHERE {clause}
              ORDER BY CASE WHEN t.w<64 OR t.h<64 OR lower(t.name) LIKE '%lod%' THEN 1 ELSE 0 END,
                       distance, map_models DESC, lower(d.name), lower(t.name)
              LIMIT ? OFFSET ?"""
    result = db.query(sql, [*distance_params, *params, limit, offset], limit=limit)
    rows = []
    for sid, name, txd, w, h, fmt, alpha, mean, distance, models, example in result["rows"]:
        mean_rgb = "unknown" if mean is None else "#" + "".join(f"{(mean >> shift) & 255:02x}" for shift in (24, 16, 8))
        rows.append([sid, role if role != "auto" else _role(name), txd, name, f"vanilla:{txd}/{name}",
                     f"{w}x{h}", fmt, bool(alpha), mean_rgb, round(math.sqrt(distance), 2), models, example])
    next_cursor = f"o{offset + len(rows)}-{fingerprint}" if offset + len(rows) < total else None
    env = table(["id", "role", "ide_txd", "texture_name", "material_preset", "size", "format", "alpha",
                 "mean_rgb", "colour_distance", "map_models", "example"], rows, total=total, next=next_cursor,
                warn=db.stale_warnings())
    env.update(profile="vanilla", constraints=dict(CONSTRAINTS), kit=KIT_NOTE,
               colour_source="Approximate index image.mean_rgba (DXT endpoint means); no pixels decoded.")
    return env


def parse_reference(preset: str) -> tuple[str, str]:
    """Validate vanilla:<txd>/<texture>, without paths, extensions, or layer overrides."""
    if not isinstance(preset, str):
        raise SatkError("BAD_PARAMS", "a vanilla material preset must be a string")
    match = re.fullmatch(r"vanilla:([a-z0-9_-][a-z0-9_.-]{0,30})/([a-z0-9_-][a-z0-9_.-]{0,30})", preset.strip().lower())
    if match is None or match[1].endswith(".txd"):
        raise SatkError("BAD_PARAMS", f"invalid vanilla material preset {preset!r}",
                        hint="use vanilla:<txd>/<texture> from satk texlib vanilla (no extension or @layer)")
    return match[1], match[2]


def _map_only(asset_class):
    if asset_class not in MAP_CLASSES:
        raise SatkError("UNSUPPORTED", "vanilla TXD reuse is supported only for map objects",
                        hint="vehicles use their own TXD and shared vehicle atlases; do not repoint their IDE TXD")


def material_preset(preset: str, *, asset_class="map", db=None) -> dict:
    """A kit-compatible shared material description, validated without reading pixels.

    The caller must retain ``vanilla`` on the material/export manifest and call
    export_plan before packaging. ``shared`` prevents kit from saving a PNG.
    """
    _map_only(asset_class)
    txd, texture = parse_reference(preset)
    db = _db(db)
    result = db.query("SELECT t.w,t.h,t.alpha," + _MAP_USE + _JOINS
                      + " WHERE b.active=1 AND lower(d.name)=? AND lower(t.name)=?", [txd, texture], limit=2)
    if not result["rows"]:
        raise SatkError("NOT_FOUND", f"no vanilla texture tex:{txd}/{texture}", hint="satk texlib vanilla " + texture)
    if len(result["rows"]) != 1:
        raise SatkError("AMBIGUOUS", f"duplicate texture name tex:{txd}/{texture} in the vanilla index")
    w, h, alpha, used = result["rows"][0]
    if not used:
        raise SatkError("UNSUPPORTED", f"tex:{txd}/{texture} is not used by a vanilla map object",
                        hint="search with satk texlib vanilla for map-object textures")
    return {"role": _role(texture), "texture": texture, "shared": True, "rgba": [255, 255, 255, 255],
            "size": [w, h], "alpha": bool(alpha), "surface": [1.0, 0.0, 1.0],
            "vanilla": f"vanilla:{txd}/{texture}", "id": f"tex:{txd}/{texture}", "ide_txd": txd,
            "copy_pixels": False}


def _chain(db, txd):
    chain = []
    while txd:
        if txd in chain or len(chain) >= 32:
            raise SatkError("CHECK_FAILED", "invalid cyclic or overlong vanilla TXD parent chain")
        chain.append(txd)
        rows = db.query("SELECT lower(d.parent),d.parent_via FROM txd d JOIN blob b ON b.id=d.blob_id "
                        "WHERE b.active=1 AND lower(d.name)=?", [txd], limit=2)["rows"]
        if len(rows) != 1:
            raise SatkError("NOT_FOUND", f"vanilla TXD {txd!r} or its parent is unavailable")
        parent, via = rows[0]
        if parent and via != "txdp":
            raise SatkError("UNSUPPORTED", "implicit vehicle TXD inheritance cannot be used for map objects")
        txd = parent
    return chain


def export_plan(presets: list[str], *, asset_class="map", own_textures=(), db=None) -> dict:
    """Plan an IDE TXD reference instead of a packed TXD (kit integration boundary).

    Empty input leaves ordinary kit exports alone. Mixed own/vanilla pixels and
    unrelated dictionaries cannot be represented by one IDE field, so fail before
    writing any files. Parent-chain name shadowing is checked explicitly.
    """
    if not presets:
        return {"reference_only": False}
    _map_only(asset_class)
    if own_textures:
        raise SatkError("BAD_PARAMS", "one model cannot combine own packed textures with a reference-only vanilla TXD",
                        hint="split it into map objects with separate IDE TXD references")
    db = _db(db)
    materials = [material_preset(s, asset_class=asset_class, db=db) for s in sorted(set(presets))]
    dictionaries = {m["ide_txd"] for m in materials}
    chains = {name: _chain(db, name) for name in sorted(dictionaries)}
    candidates = [name for name, chain in chains.items() if dictionaries.issubset(chain)]
    if not candidates:
        raise SatkError("BAD_PARAMS", "vanilla materials need unrelated TXDs; an IDE row has only one TXD field",
                        hint="use textures from one TXD/parent chain or split the object")
    txd = sorted(candidates, key=lambda name: (len(chains[name]), name))[0]
    chain = chains[txd]
    for material in materials:
        for dictionary in chain:
            found = db.query("SELECT 1 FROM texture t JOIN txd d ON d.id=t.txd_id JOIN blob b ON b.id=d.blob_id "
                             "WHERE b.active=1 AND lower(d.name)=? AND lower(t.name)=?",
                             [dictionary, material["texture"]], limit=1)["rows"]
            if found:
                if dictionary != material["ide_txd"]:
                    raise SatkError("BAD_PARAMS", f"texture {material['texture']!r} is shadowed by TXD {dictionary!r}",
                                    hint="choose an unambiguous texture within the selected TXD chain")
                break
    return {"reference_only": True, "ide_txd": txd, "txd_chain": chain, "pack_txd": False,
            "texture_names": sorted({m["texture"] for m in materials}), "materials": materials,
            "constraints": dict(CONSTRAINTS)}
