"""Class taxonomy and peer sets of satk.style (data: ``data/style/peers.json``).

A *class* names the vanilla models an asset is compared with:

* vehicles: the IDE type (``car``, ``mtruck``, ``quad``, ``bike``, ``bmx``, ``boat``, ``plane``, ``heli``,
  ``trailer``, ``train``) and, for ``car``, a curated body class ``car.<body>`` (``car.sedan``, ...);
* map models: ``prop``, ``building``, ``terrain``, ``vegetation``, ``overlay``, ``time_object``,
  ``interior_prop``, ``interior_shell``, ``lod``, ``seabed``, ``procedural``, ``animated``, ``cutscene``,
  each split by a size bucket (``prop@1-2m``, max bbox side);
* ``ped``, ``weapon``, ``pickup``, ``upgrade``.

A *peer set* is a class or ``<class>@<bucket>``; a peer set smaller than ``min_peers`` falls back to its
parent (:func:`fallback_chain`). Stdlib only.

Example::

    from satk.style import classes as C
    C.resolve("sedan")                 # 'car.sedan'
    C.classify({"sec": "objs", "ide": "data/maps/generic/dynamic.ide", "dims": (0.4, 0.4, 1.1)})
    # ('prop', '1-2m')
"""

from __future__ import annotations

import difflib
import functools
import re

from ..core import resources
from ..core.errors import SatkError

__all__ = ["taxonomy", "resolve", "class_names", "family", "kind", "size_bucket", "peer_key", "split_peer",
           "fallback_chain", "classify", "car_body", "VEHICLE_TYPES", "MAP_FAMILY"]

VEHICLE_TYPES = ("car", "mtruck", "quad", "bike", "bmx", "boat", "plane", "heli", "trailer", "train")
MAP_FAMILY = "map"

_VEG_RE = re.compile(r"(?<!s)tree|palm|bush|shrub|hedge|cactus|pine|redwood|weed|flower|cypress|birch|joshua|"
                     r"yucca|copse|foliage|_fir|fir_|fern|^veg_|^sm_veg|_veg_|potplant|plantpot|leaves|ivy", re.I)
_TERRAIN_RE = re.compile(r"road|land|grnd|ground|hill|cliff|terrain|beach|field|riverbank|mount|desert|lawn|"
                         r"grasspatch|dirt|slope|canyon|rock_?face|valley|sand", re.I)
#: Collision surfaces that count as ground (roads, grass, dirt, sand, rock, ...; surfinfo.dat ids).
_GROUND = (set(range(1, 23)) | set(range(24, 41)) | {160, 178} | set(range(74, 90)) | set(range(96, 102))
           | set(range(109, 158)))
#: IDE flag bits used by the classifier.
_F_ROAD, _F_DRAWLAST, _F_TREE, _F_PALM = 1, 4, 8192, 16384


@functools.lru_cache(maxsize=1)
def taxonomy() -> dict:
    """``data/style/peers.json`` plus derived maps (``body_of``: model name -> body)."""
    d = resources.read_json("style", "peers.json")
    d = dict(d)
    d["body_of"] = {n: b for b, names in d["car_bodies"].items() for n in names.split()}
    d["pickup_ids"] = frozenset(int(x) for x in d["pickups"])
    return d


def class_names() -> list[str]:
    return list(taxonomy()["classes"])


def resolve(name: str) -> str:
    """A class name (aliases like ``sedan``, ``automobile``, ``vehicle_upgrade`` accepted); ``BAD_PARAMS``."""
    t = taxonomy()
    key = (name or "").strip().lower()
    base, _, bucket = key.partition("@")
    base = t["aliases"].get(base, base)
    if base in t["classes"]:
        if bucket and bucket not in {b[2] for b in t["size_buckets"]}:
            raise SatkError("BAD_PARAMS", f"unknown size bucket {bucket!r}",
                            hint="buckets: " + ", ".join(b[2] for b in t["size_buckets"]))
        if bucket and family(base) != MAP_FAMILY:
            raise SatkError("BAD_PARAMS", f"{base}: only map classes have size buckets")
        return f"{base}@{bucket}" if bucket else base
    choices = list(t["classes"]) + list(t["aliases"])
    raise SatkError("BAD_PARAMS", f"unknown style class {name!r}",
                    hint="satk style profile --list (classes and peer sets)",
                    did_you_mean=difflib.get_close_matches(key, choices, n=4, cutoff=0.5))


def family(cls: str) -> str:
    """``vehicle`` | ``map`` | ``ped`` | ``weapon`` | ``pickup`` | ``upgrade``."""
    return taxonomy()["classes"][split_peer(cls)[0]]["family"]


def kind(cls: str) -> str:
    """The kit kind of a class (``automobile``, ``prop``, ``building``, ...)."""
    return taxonomy()["classes"][split_peer(cls)[0]]["kind"]


def size_bucket(max_side: float | None) -> str | None:
    """Size bucket label of a max bbox side in metres (``None`` without a size)."""
    if max_side is None:
        return None
    for lo, hi, label in taxonomy()["size_buckets"]:
        if lo <= max_side < hi:
            return label
    return taxonomy()["size_buckets"][-1][2]


def peer_key(cls: str, bucket: str | None = None) -> str:
    return f"{cls}@{bucket}" if bucket and family(cls) == MAP_FAMILY else cls


def split_peer(key: str) -> tuple[str, str | None]:
    c, _, b = key.partition("@")
    return c, (b or None)


def fallback_chain(key: str) -> list[str]:
    """``car.sedan`` -> [car.sedan, car]; ``prop@1-2m`` -> [prop@1-2m, prop]."""
    out = [key]
    c, b = split_peer(key)
    if b:
        out.append(c)
    parent = taxonomy()["classes"].get(c, {}).get("parent")
    while parent and parent not in out:
        out.append(parent)
        parent = taxonomy()["classes"].get(parent, {}).get("parent")
    return out


def car_body(name: str) -> str | None:
    return taxonomy()["body_of"].get((name or "").lower())


def _dims(f: dict) -> tuple[float, float, float] | None:
    d = f.get("dims")
    if d:
        return tuple(float(x) for x in d)
    b = f.get("bbox")
    if b and len(b) == 6:
        return (b[3] - b[0], b[4] - b[1], b[5] - b[2])
    return None


def _ground_share(surfaces: dict | None) -> float:
    if not surfaces:
        return 0.0
    tot = sum(surfaces.values())
    return sum(v for k, v in surfaces.items() if int(k) in _GROUND) / tot if tot else 0.0


def classify(f: dict) -> tuple[str, str | None]:
    """Class and size bucket of a model from its facts.

    ``f`` keys (all optional except ``sec``): ``id``, ``name``, ``sec`` (IDE section), ``type`` (vehicle
    type), ``ide`` (IDE path, forward slashes), ``flags``, ``draw``, ``dims`` (x, y, z) or ``bbox``,
    ``n_ext``/``n_int`` (exterior/interior placements), ``as_lod`` (placements that use it as a LOD),
    ``objdat`` (has an object.dat entry), ``col`` (``{"spheres", "boxes", "faces", "surfaces"}``).
    """
    t = taxonomy()
    sec = (f.get("sec") or "objs").lower()
    name = (f.get("name") or "").lower()
    ide = (f.get("ide") or "").lower().replace("\\", "/")
    if sec == "cars":
        vt = (f.get("type") or "car").lower()
        if vt == "car":
            body = car_body(name)
            return (f"car.{body}" if body else "car"), None
        return (vt if vt in VEHICLE_TYPES else "car"), None
    if sec == "peds":
        return "ped", None
    if sec == "weap":
        return "weapon", None
    d = _dims(f)
    mx = max(d) if d else None
    bucket = size_bucket(mx)
    if sec == "hier":
        return "cutscene", bucket
    if f.get("id") is not None and int(f["id"]) in t["pickup_ids"]:
        return "pickup", None
    if "veh_mods" in ide:
        return "upgrade", None
    if (f.get("as_lod") or 0) > 0 or (name.startswith("lod") and (f.get("draw") or 0) >= 300):
        return "lod", bucket
    if sec == "anim":
        return "animated", bucket
    if "/interior/" in ide or ((f.get("n_int") or 0) > 0 and not (f.get("n_ext") or 0)):
        return ("interior_shell" if (mx or 0) >= 8 else "interior_prop"), bucket
    fl = int(f.get("flags") or 0)
    if "vegepart" in ide or fl & (_F_TREE | _F_PALM) or (_VEG_RE.search(name) and (mx or 0) < 130):
        return "vegetation", bucket
    if "procobj" in ide:
        return "procedural", bucket
    if "seabed" in ide:
        return "seabed", bucket
    if sec == "tobj":
        return "time_object", bucket
    col = f.get("col") or {}
    col_empty = not (col.get("faces") or col.get("spheres") or col.get("boxes"))
    if fl & _F_DRAWLAST and col_empty and not f.get("objdat") and "/generic/" not in ide:
        return "overlay", bucket
    if "/generic/" in ide or f.get("objdat") or (mx is not None and mx < 6):
        return "prop", bucket
    if d and max(d[0], d[1]) >= 20 and (fl & _F_ROAD or _ground_share(col.get("surfaces")) >= 0.6
                                         or (_TERRAIN_RE.search(name) and d[2] < 0.35 * max(d[0], d[1]))):
        return "terrain", bucket
    return "building", bucket
