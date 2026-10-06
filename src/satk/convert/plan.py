"""Conversion plans and content checks; no Blender, image or numerical imports at discovery time."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ..core import paths, resources
from ..core.errors import SatkError

FORMAT = "satk.convert-plan/1"
VERSION = 3
_REQUEST_KEYS = ("version", "kind", "group", "like", "name", "tier", "dims", "profile", "budget", "bands",
                 "presets", "texture_distribution", "template", "style_class")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", re.I)


def model_name(name: str, group: str) -> str:
    from ..kit.plan import check_name

    value = check_name(name, group)
    if _DEVICE.fullmatch(value):
        raise SatkError("BAD_PARAMS", "name is reserved by Windows", hint="choose another model name")
    return value


def output_dir(path: str | Path) -> Path:
    folder = paths.ensure_writable(path).resolve()
    if not folder.is_relative_to(paths.cfg().paths.work.resolve()):
        raise SatkError("PROTECTED_PATH", "conversion outputs must stay under the work directory",
                        hint="omit --out, or choose <workspace>/work/out/convert/<name>")
    return folder


@contextmanager
def lock(folder: Path):
    """Non-blocking OS lock, released even when a converter crashes; never delete someone else's lock."""
    file = paths.ensure_writable(folder / ".convert.lock")
    with file.open("a+b") as stream:
        if not file.stat().st_size:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SatkError("BUSY", "another conversion is using this output folder",
                            hint="wait for it to finish or choose a different --out") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with paths.open_ro(path) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_read(path: Path) -> dict:
    try:
        with paths.open_ro(path) as stream:
            data = json.load(stream)
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read JSON {paths.jpath(path)}: {e}",
                        hint="use the plan.json returned by satk convert prepare") from None
    if not isinstance(data, dict):
        raise SatkError("BAD_PARAMS", f"expected a JSON object in {paths.jpath(path)}")
    return data


def dependencies(source: Path) -> list[Path]:
    """Early glTF dependencies. Blender supplies the definitive image/library dependencies after import."""
    result = {source.resolve()}
    if source.suffix.lower() == ".gltf":
        data = json_read(source)
        entries = []
        for key in ("buffers", "images"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(not isinstance(entry, dict) for entry in value):
                raise SatkError("BAD_PARAMS", f"glTF {key} must be an array of objects")
            entries += value
        for entry in entries:
            uri = entry.get("uri", "")
            if not isinstance(uri, str) or "\0" in unquote(uri):
                raise SatkError("BAD_PARAMS", "invalid glTF resource URI")
            if not uri or uri.startswith("data:"):
                continue
            parsed = urlsplit(uri)
            if parsed.scheme or parsed.netloc:
                raise SatkError("UNSUPPORTED", "remote glTF resources are not supported",
                                hint="save the buffers and images next to the model; no downloads are performed")
            p = (source.parent / unquote(parsed.path)).resolve()
            if not p.is_file():
                raise SatkError("NOT_FOUND", f"missing glTF resource {paths.jpath(p)}",
                                hint="keep the glTF buffers and images with the source")
            result.add(p)
    elif source.suffix.lower() == ".obj":
        # MTL edits must invalidate even when the referenced image list did not change.
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s*mtllib\s+(.+)", line, re.I)
            if not match:
                continue
            value = match[1].strip()
            # Blender accepts an unquoted library name containing spaces as well as multiple libraries.
            whole = (source.parent / value.strip('\"\'')).resolve()
            if whole.is_file():
                result.add(whole)
                continue
            lexer = shlex.shlex(value, posix=False)
            lexer.whitespace_split = True
            try:
                names = list(lexer)
            except ValueError:
                raise SatkError("BAD_PARAMS", "invalid OBJ material library declaration") from None
            for name in names:
                p = (source.parent / name.strip('\"\'')).resolve()
                if not p.is_file():
                    raise SatkError("NOT_FOUND", f"missing OBJ material library {paths.jpath(p)}",
                                    hint="keep each MTL file with the source model")
                result.add(p)
    return sorted(result, key=str)


def fingerprints(files) -> dict[str, str]:
    return {paths.jpath(Path(p)): digest(Path(p)) for p in sorted(set(map(str, files)))}


def unchanged(files: dict) -> bool:
    try:
        return bool(files) and all(Path(p).is_file() and digest(Path(p)) == h for p, h in files.items())
    except OSError:
        return False


def validate(plan: dict, path: Path) -> None:
    """Validate persisted plans at both entry points, including every name used to construct a write path."""
    from ..kit import kinds as K

    try:
        if plan.get("format") != FORMAT or plan["version"] != VERSION:
            raise ValueError("unsupported plan version")
        if plan["group"] != K.get(plan["kind"])["group"] or plan["tier"] not in K.TIERS:
            raise ValueError("invalid kind or tier")
        if plan["name"] != model_name(plan["name"], plan["group"]):
            raise ValueError("non-canonical model name")
        if not isinstance(plan["key"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["key"]):
            raise ValueError("invalid plan key")
        dims = plan["dims"]
        if len(dims) != 3 or any(not math.isfinite(float(v)) or float(v) <= 0 for v in dims):
            raise ValueError("invalid dimensions")
        source = Path(plan["source"])
        if not source.is_absolute() or source.suffix.lower() not in plan["presets"]["formats"]:
            raise ValueError("invalid source path")
        if paths.jpath(source) not in plan["input_files"]:
            raise ValueError("source fingerprint is missing")
        for filename, digest_value in plan["input_files"].items():
            if not Path(filename).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", digest_value):
                raise ValueError("invalid input fingerprint")
        if plan["presets"] != resources.read_json("convert", "presets.json"):
            raise ValueError("conversion settings have changed")
        budget = plan["budget"]
        if not 4 <= budget["lo"] <= budget["target"] <= budget["hi"]:
            raise ValueError("invalid triangle budget")
        template = plan["template"]
        if template["name"] != plan["name"]:
            raise ValueError("template name differs")
        if Path(plan["template_path"]).resolve() != path.parent / "template.json":
            raise ValueError("template must stay next to the plan")
        if json_read(path.parent / "template.json") != template:
            raise ValueError("template contents have changed")
        for frame in template["frames"]:
            if not re.fullmatch(r"[a-zA-Z0-9_. -]{1,64}", frame["name"]) or frame["name"] in (".", ".."):
                raise ValueError("invalid template frame name")
        request = {k: plan[k] for k in _REQUEST_KEYS}
        request["source"] = plan["input_files"]
        actual = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
        if actual != plan["key"]:
            raise ValueError("plan contents have changed")
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise SatkError("BAD_PARAMS", f"invalid conversion plan: {e}",
                        hint="run satk convert prepare with a new output folder") from None


def load(path: str | Path) -> tuple[dict, Path]:
    p = Path(path).resolve()
    plan = json_read(p)
    output_dir(p.parent)
    validate(plan, p)
    return plan, p


def texture_rows(document: dict, plan: dict, folder: Path, stage: str) -> list[dict]:
    """Bind bake/finish sidecars to this plan's own paths before image processing or kit export."""
    if document.get("key") != plan["key"]:
        raise SatkError("REVISION", "the texture sidecar belongs to another conversion")
    rows = document.get("textures")
    if not isinstance(rows, list) or not rows:
        raise SatkError("NOT_READY", "the conversion has no texture results", hint="run convert.bake, then convert finish")
    seen = set()
    try:
        for row in rows:
            role = row["role"]
            if role in seen or role not in plan["presets"]["roles"]:
                raise ValueError("unknown or repeated texture role")
            seen.add(role)
            expected = folder / stage / f"{plan['name']}_{role}.png"
            if Path(row["file"]).resolve() != expected.resolve():
                raise ValueError("texture paths must stay in this conversion")
            if row["size"] != plan["presets"]["roles"][role][plan["tier"]]:
                raise ValueError("unexpected texture dimensions")
            if stage == "textures" and row["name"] != expected.stem:
                raise ValueError("unexpected texture name")
            if not expected.is_file():
                raise ValueError("the texture file is missing")
    except (KeyError, ValueError, TypeError) as e:
        raise SatkError("BAD_PARAMS", f"invalid texture sidecar: {e}",
                        hint="repeat convert.bake and convert finish from the plan") from None
    return rows


def prepare(model: str, *, kind: str = "prop", like: str | None = None, tier: str = "sa_plus",
            dims: list[float] | None = None, out: str | None = None, name: str | None = None,
            profile: str = "vanilla") -> tuple[dict, Path]:
    from ..kit import kinds as K
    from ..kit.plan import template_plan
    from ..style import api as S, classes as C
    from ..style.texture import vanilla

    source = Path(model).resolve()
    presets = resources.read_json("convert", "presets.json")
    if source.suffix.lower() not in presets["formats"]:
        raise SatkError("BAD_PARAMS", f"unsupported source extension {source.suffix!r}",
                        hint="use GLB, glTF, FBX, OBJ, DAE or blend")
    if not source.is_file():
        raise SatkError("NOT_FOUND", f"no model {paths.jpath(source)}", hint="give a local source file")
    kind = K.canonical("automobile" if kind == "vehicle" else kind)
    if kind in ("ped", "animated_object"):
        raise SatkError("UNSUPPORTED", "automatic skin or animation retargeting is not supported",
                        hint="keep the SA skeleton and weights in a studio session; use kit.export for skinned assets")
    if tier not in K.TIERS:
        raise SatkError("BAD_PARAMS", "tier must be vanilla or sa_plus")
    if dims is not None and (len(dims) != 3 or any(not math.isfinite(float(x)) or float(x) <= 0 for x in dims)):
        raise SatkError("BAD_PARAMS", "dims must contain three finite positive metres: L,W,H")
    group = K.get(kind)["group"]
    slug = re.sub(r"[^a-z0-9_]+", "_", source.stem.lower()).strip("_") or "converted"
    name = model_name(name if name is not None else slug[:17], group)
    cls = C.resolve(kind)
    target = like or cls
    anchor_bands = S.profile(target, tier, metrics=["dims.L", "dims.W", "dims.H"], profile_name=profile)
    if dims is None and not like:
        dims = [float(anchor_bands[f"dims.{axis}"]["p50"]) for axis in "LWH"]
    template = template_plan(kind=kind, like=like, name=name, dims=dims, tier=tier, profile=profile)
    dims = [float(template["dims"]["target"][axis]) for axis in "LWH"]
    if not like and C.family(cls) == "map":
        target = C.peer_key(cls, C.size_bucket(max(dims)))
    metric = "veh.hd_tris" if group == "vehicle" and kind != "vehicle_upgrade" else "geo.tris"
    bands = S.profile(target, tier, metrics=[metric, "geo.tris", "part.tris[wheel]", "part.tris[chassis]",
                                            "shade.normal_bend", "shade.flat_share"],
                      profile_name=profile)
    band = bands.get(metric)
    if not band:
        raise SatkError("NOT_READY", f"no {metric} style band for {target}", hint="satk style build")
    lo, hi = max(4, math.ceil(band["lo"])), math.floor(band["hi"])
    budget = {"metric": metric, "lo": lo, "hi": hi, "target": round(lo + 0.7 * (hi - lo)),
              "peer_set": band["peer_set"], "status": band["status"]}
    dist = vanilla(profile)
    # The values behind the percentile estimates are not needed in Blender or the conversion manifest.
    dist = {"roles": {r: {"n": d["n"], "metrics": d["metrics"]} for r, d in dist["roles"].items()}}
    request = {"version": VERSION, "source": fingerprints(dependencies(source)), "kind": kind, "group": group, "like": like,
               "name": name, "tier": tier, "dims": dims, "profile": profile, "budget": budget,
               "bands": bands, "presets": presets, "texture_distribution": dist, "template": template,
               "style_class": target}
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
    folder = output_dir(Path(out) if out else paths.work("out", "convert", f"{name}-{key[:10]}"))
    path = folder / "plan.json"
    if folder.exists():
        if not folder.is_dir():
            raise SatkError("BAD_PARAMS", "out must name a directory")
        if any(f.name != ".convert.lock" for f in folder.iterdir()) \
                and (not path.is_file() or json_read(path).get("key") != key):
            raise SatkError("EXISTS", f"output folder is already in use: {paths.jpath(folder)}",
                            hint="choose another --out folder")
    folder.mkdir(parents=True, exist_ok=True)
    with lock(folder):
        if any(f.name != ".convert.lock" for f in folder.iterdir()):
            if not path.is_file() or json_read(path).get("key") != key:
                raise SatkError("EXISTS", f"output folder is already in use: {paths.jpath(folder)}",
                                hint="choose another --out folder; existing files are never replaced by a different conversion")
            return load(path)
        plan = dict(request, format=FORMAT, key=key, source=paths.jpath(source), input_files=request["source"],
                    template_path=paths.jpath(folder / "template.json"))
        paths.atomic_write(folder / "template.json", json.dumps(template, ensure_ascii=False, indent=1))
        paths.atomic_write(path, json.dumps(plan, ensure_ascii=False, indent=1))
    return plan, path
