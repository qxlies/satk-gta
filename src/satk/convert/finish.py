"""Finish source bakes using texture.finish, then fit the measured role's palette, value and grain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError
from . import plan as P


def _roundtrip(rgba):
    from ..formats.dxt import decode_rgba
    from ..formats.txd import mip0_bytes, parse_txd
    from ..texmod.encode import encode_level
    from ..texmod.txdwrite import NativeSpec, native_chunk, txd_chunk

    h, w = rgba.shape[:2]
    alpha = bool((rgba[..., 3] < 255).any())
    fmt = "DXT3" if alpha else "DXT1"
    block = encode_level(rgba, fmt, alpha=alpha)
    buf = txd_chunk([native_chunk(NativeSpec("convert", fmt, w, h, (block,), alpha))])
    tex = parse_txd(buf).textures[0]
    return decode_rgba(tex, mip0_bytes(buf, tex))


def _grade(array, targets: dict):
    import numpy as np

    rgb = array[..., :3].astype(np.float64)
    visible = array[..., 3] > 0
    if not visible.any():
        raise SatkError("BAD_PARAMS", "a baked texture is fully transparent", hint="check the source material alpha")
    mx, mn = rgb.max(-1), rgb.min(-1)
    saturation = float(((mx - mn) / np.maximum(mx, 1))[visible].mean())
    wanted_sat = float(targets["tex.sat_mean"][1])
    if saturation < 1e-5:
        rgb *= np.array([1.0, 1 - wanted_sat * 0.4, 1 - wanted_sat])
    else:
        gray = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114)[..., None]
        rgb = gray + (rgb - gray) * min(4, wanted_sat / saturation)
    value = float(rgb.max(-1)[visible].mean())
    wanted_value = float(targets["tex.val_mean"][1]) * 255
    if value <= 1e-6:
        rgb[:] = wanted_value * np.array([1, 1 - wanted_sat * 0.4, 1 - wanted_sat])
    else:
        rgb *= wanted_value / value
    return np.clip(rgb, 0, 255)


def fit(source: Path, dest: Path, role: str, distribution: dict) -> dict:
    """Fit deterministic blur, grain and palette candidates against the decoded DXT, not just the PNG."""
    import numpy as np
    from PIL import Image, ImageFilter

    from ..media.png import encode
    from ..style.texture import judge_texture, texture_stats

    with Image.open(source) as image:
        raw = np.array(image.convert("RGBA"))
    h, w = raw.shape[:2]
    entry = distribution["roles"].get(role, distribution["roles"]["generic"])
    targets = entry["metrics"]
    rgb = _grade(raw, targets)
    base = Image.fromarray(np.uint8(np.rint(rgb)), "RGB")
    seed = int.from_bytes(hashlib.sha256(raw.tobytes() + role.encode("ascii")).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    grain = rng.normal(0, 1, (h, w, 1))
    target_hf = float(targets["tex.hf_energy"][1])
    gain, blur = 1.0, 0.6
    best = None
    target_colours = float(targets["tex.colours"][1])
    palette = max(16, min(256, round(target_colours / 4)))
    for _ in range(12):
        rgb = np.asarray(base.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float64)
        candidate = np.concatenate([np.clip(rgb + grain * gain, 0, 255), raw[..., 3:4]], axis=2)
        candidate = np.uint8(np.rint(candidate))
        pal = Image.fromarray(candidate[..., :3], "RGB").quantize(colors=palette, method=Image.Quantize.MEDIANCUT,
                                                                   dither=Image.Dither.NONE).convert("RGB")
        candidate[..., :3] = np.asarray(pal)
        encoded = _roundtrip(candidate)
        dxt_stats = texture_stats(encoded, w, h)
        png_stats = texture_stats(candidate.tobytes(), w, h)
        judged = judge_texture(dest.stem, dxt_stats, distribution, role)
        png_judged = judge_texture(dest.stem, png_stats, distribution, role)
        score = sum(r[-1] in ("low", "high") for r in judged["rows"]) * 100
        score += sum(r[-1] != "ok" for r in judged["rows"]) * 10
        score += 20 * (png_judged["verdict"] != "in")
        score += abs(dxt_stats["tex.hf_energy"] - target_hf) / max(1, target_hf)
        score += abs(dxt_stats["tex.colours"] - target_colours) / max(1, target_colours)
        if best is None or score < best[0]:
            best = (score, candidate, encoded, dxt_stats, png_stats, judged, png_judged)
        if all(r[-1] == "ok" for r in judged["rows"]) and png_judged["verdict"] == "in":
            break
        hf = dxt_stats["tex.hf_energy"]
        if hf > targets["tex.hf_energy"][2]:
            blur = min(3.0, blur * 1.4)
            gain *= 0.6
        elif hf < targets["tex.hf_energy"][0]:
            gain = min(24, max(gain * 1.5, (target_hf - hf) / 3))
        colours = dxt_stats["tex.colours"]
        if not targets["tex.colours"][0] <= colours <= targets["tex.colours"][2]:
            palette = max(16, min(256, round(palette * (target_colours / max(1, colours)) ** 0.7)))
    _, candidate, encoded, dxt_stats, png_stats, judged, png_judged = best
    preview = dest.with_name(dest.stem + "_dxt.png")
    paths.atomic_write(dest, encode(w, h, candidate.tobytes()))
    paths.atomic_write(preview, encode(w, h, encoded))
    return {"name": dest.stem, "role": role, "file": paths.jpath(dest), "preview": paths.jpath(preview), "size": w,
            "alpha": bool((candidate[..., 3] < 255).any()),
            "png": png_stats, "dxt": dxt_stats, "verdict": judged["verdict"], "png_verdict": png_judged["verdict"],
            "rows": judged["rows"]}


def run(plan_path: str) -> dict:
    from ..media.png import encode
    from ..texmod.finish import finish

    plan, path = P.load(plan_path)
    bakes = P.json_read(path.parent / "bakes.json")
    textures = list(P.texture_rows(bakes, plan, path.parent, "baked"))
    out = paths.ensure_writable(path.parent / "textures")
    out.mkdir(parents=True, exist_ok=True)
    if any(f.get("part") == "wheel" for f in plan["template"]["frames"]) \
            and not any(t["role"] == "wheel" for t in textures):
        size = plan["presets"]["roles"]["wheel"][plan["tier"]]
        raw = path.parent / "baked" / f"{plan['name']}_wheel.png"
        # Only the generated kit wheel needs a new own rim texture; tyre pixels stay shared.
        paths.atomic_write(raw, encode(size, size, bytes((74, 72, 69, 255)) * (size * size)))
        textures.append({"role": "wheel", "file": str(raw), "size": size})
    result = []
    for texture in textures:
        role = texture["role"]
        preset = plan["presets"]["roles"][role]["preset"]
        initial = finish(texture["file"], preset=preset, out=str(path.parent / "finish_work"))
        dest = out / f"{plan['name']}_{role}.png"
        result.append(fit(Path(initial["file"]), dest, role, plan["texture_distribution"]))
    doc = {"key": plan["key"], "textures": result}
    paths.atomic_write(path.parent / "finished.json", json.dumps(doc, ensure_ascii=False, indent=1))
    return doc


def check_txd(txd: str, textures: list[dict], distribution: dict) -> list[dict]:
    """Check the actual exported mip0 pixels, with the same role used for each source bake."""
    from ..style.texture import judge_texture, load_images, texture_stats

    by_name = {t["name"]: t["role"] for t in textures}
    result = []
    for name, w, h, rgba in load_images(txd):
        stem = name.rsplit("/", 1)[-1]
        if stem not in by_name:  # LOD atlases have their own 64 px budget, not the HD role distribution.
            continue
        stats = texture_stats(rgba, w, h)
        result.append(dict(judge_texture(stem, stats, distribution, by_name[stem]), metrics=stats))
    missing = sorted(set(by_name) - {r["texture"] for r in result})
    if missing:
        raise SatkError("CHECK_FAILED", "export omitted converted textures", data={"missing": missing})
    return result
