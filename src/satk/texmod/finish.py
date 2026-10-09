"""Photo-free "SA look" finishing of a texture (``satk texture finish``; owner M2-03).

Vanilla San Andreas textures are small, soft, desaturated photographs: an interior texture of a vanilla car
has 364-590-921 colours (p10/p50/p90, exact RGB, n=147) at a mean value of 0.085-0.119-0.261, while a
hand-drawn agent texture had 19-77 colours (flat fills, vector look). :func:`finish` turns a flat or drawn
image into that look without photographs, deterministically (the noise seed is the image content + preset):

1. work at 4x the size (supersampling: grain and edges come back anti-aliased), or take an image painted at
   ``supersample`` times the output size as it is (the 4x paint-and-downscale method: paint big, finish shrinks);
2. desaturate towards the preset's saturation;
3. two-scale noise: soft low-frequency blotches plus a faint grain (``grain`` = its share of the noise; tileable);
4. ambient occlusion from an optional mask (white = open, black = occluded), blurred;
5. grime (``grime``): darker low-frequency patches, heavier towards the bottom. Off (0) for vehicle roles (interior,
   wheel, decal, body): the engine's dirt level does the dirt of a car;
6. edge wear (``wear``) from an optional edge mask (``kit.bake`` writes ``<object>_edge.png``; white = edge): worn
   edges turn lighter and greyer, like chipped paint. Off (0) for vehicle roles unless asked for;
7. value: scale the mean value to the preset's target (when it has one);
8. a soft filter: a blur of ``soft`` output pixels, then the box filter back to the output size (SA textures look
   slightly out of focus at native size); a DXT1 round trip gives the preview of what the game will show;
9. band landing: the preview is measured with the ``style.texture`` metrics and the distribution of the texture's
   role (``prop``, ``interior``, ...); a metric near or beyond the edge of the vanilla band is pulled into the
   inner part of the band (value, saturation by their targets, detail and colour count by the noise gain), up to
   four rounds. A mask of another size is resized to the image (warning ``RESIZED_MASK``).

``photo`` (0..1) switches the noise of steps 3 and 9 to the photo-like variation of vanilla textures, which is
quiet, not dirt: a soft low-frequency tonal drift, mottling and a soft grain at a few luma levels, a slight hue
drift and a soft light gradient from above (not on tiling roles: wall, ground). It keeps the painted structure,
adds no grime by default (every role) and lands the fine tonal variation of the DXT1 preview
(``tex.tone_mid`` of ``style.texture``) at p10 + ``photo`` x (p50 - p10) of the role's vanilla textures, up to
four rounds; value and saturation land as in step 9, detail and colour count are never chased with noise. A flat,
clean paint (the "CG-clean" look ``style.texture`` advises on) gets the variation a photo has; an image that
already has it is left nearly alone.

The answer compares the result (and its DXT1 preview) with the vanilla band of the preset's role
(:data:`VANILLA_BANDS`, measured on the vanilla game) or, when the ``style.texture`` operation of
``satk.style`` is installed, with its answer.

numpy and Pillow are imported inside the functions only (SPEC §2.3).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable, jpath, work

__all__ = ["PRESETS", "VANILLA_BANDS", "finish", "texture_stats"]

#: Vanilla texture statistics per role, p10/p50/p90 (measured 2026-10-05 on the vanilla game: car-TXD
#: textures named *interior* (n=147) and *wheel* (n=133), map textures named *wall* (n=300)).
#: ``value`` = mean of max(r, g, b) / 255, ``sat`` = mean HSV saturation, ``colours`` = exact RGB colours.
VANILLA_BANDS: dict[str, dict[str, tuple[float, float, float]]] = {
    "interior": {"value": (0.085, 0.119, 0.261), "sat": (0.10, 0.226, 0.35), "colours": (364, 590, 921)},
    "wheel": {"value": (0.21, 0.268, 0.477), "sat": (0.029, 0.069, 0.188), "colours": (208, 296, 412)},
    "wall": {"value": (0.462, 0.639, 0.808), "sat": (0.025, 0.152, 0.354), "colours": (77, 564, 2708)},
}

#: ``sat``/``value``: targets (``None`` = keep the input); ``contrast`` = target coefficient of variation of the
#: luminance (std / mean; vanilla interior 0.089 / ~0.10, wheel 0.217 / ~0.25, wall 0.078 / ~0.6); ``grain`` =
#: share of the noise variance in fine grain (the rest is soft blotches); ``grime`` and ``ao`` = darkening.
PRESETS: dict[str, dict] = {
    "photo_like": {"role": None, "sat": 0.22, "value": None, "contrast": 0.20, "grain": 0.4, "grime": 0.18,
                   "ao": 0.4, "grime_bottom": True},
    "interior": {"role": "interior", "sat": 0.20, "value": 0.13, "contrast": 0.80, "grain": 0.45, "grime": 0.22,
                 "ao": 0.5, "grime_bottom": True},
    "wheel": {"role": "wheel", "sat": 0.07, "value": 0.27, "contrast": 0.75, "grain": 0.4, "grime": 0.20,
              "ao": 0.5, "grime_bottom": False},
    "wall": {"role": "wall", "sat": 0.15, "value": None, "contrast": 0.14, "grain": 0.35, "grime": 0.20,
             "ao": 0.35, "grime_bottom": False},
}
_SS = 4
#: Share of the band (from each end) that counts as the edge: a metric inside the inner part is left alone.
BAND_MARGIN = 0.25
#: Rounds of the band landing (each one finishes the image again and measures the DXT1 preview).
BAND_ROUNDS = 4
#: ``style.texture`` metric of each ``VANILLA_BANDS`` key (the fallback when the style cache is not built).
_BAND_METRIC = {"value": "tex.val_mean", "sat": "tex.sat_mean", "colours": "tex.colours"}
_ROLES = ("interior", "wheel", "decal", "body", "ped", "weapon", "wall", "ground", "prop", "generic")
#: Roles of vehicle textures: grime and wear default to 0 (clean; the engine adds the dirt).
VEHICLE_ROLES = ("interior", "wheel", "decal", "body")
#: Default blur of the soft filter in output pixels.
SOFT_DEFAULT = 0.5
#: Photo-like variation (``photo``): luminance std of the low-frequency drift, the light gradient (top to bottom)
#: and the hue drift per channel at ``photo`` 1 (fixed), and of the mid band (mottling + soft grain) at landing
#: gain 1 (the landing scales only the mid band, so the drift never turns into blotches).
PHOTO_AMP = {"drift": 0.06, "mid": 0.08, "light": 0.05, "hue": 0.015}
#: Roles that tile (no light gradient: it would show a seam at every repeat).
TILING_ROLES = ("wall", "ground")
#: Rounds of the photo landing, and the share of the target within which it stops.
PHOTO_ROUNDS, PHOTO_TOL = 4, 0.15
#: Fallback target of ``tex.tone_mid`` (luma levels) at photo 0 and 1 without the vanilla distribution.
PHOTO_FALLBACK = (1.6, 7.0)
SUPERSAMPLES = (1, 2, 4, 8)


def _np():
    from ..core.errors import require_module

    return require_module("numpy", purpose="texture finish")


def texture_stats(img) -> dict:
    """``value``, ``sat``, ``lum_std``, ``colours`` (exact RGB) and ``colours15`` (5 bits per channel)."""
    np = _np()
    rgb = img[..., :3].astype(np.float64) / 255.0
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    q = img[..., :3].reshape(-1, 3).astype(np.int64)
    exact = int(len(np.unique(q[:, 0] * 65536 + q[:, 1] * 256 + q[:, 2])))
    q5 = q >> 3
    c15 = int(len(np.unique(q5[:, 0] * 1024 + q5[:, 1] * 32 + q5[:, 2])))
    return {"value": round(float(mx.mean()), 3), "sat": round(float(sat.mean()), 3),
            "lum_std": round(float(lum.std()), 3), "colours": exact, "colours15": c15}


def _periodic_noise(rng, h: int, w: int, cells: int, stretch: float = 1.0):
    """Smooth tileable noise in -1..1: a random ``cells`` grid, bilinear with wrap-around (``stretch`` > 1 makes the
    features that many times taller than wide: vertical streaks)."""
    np = _np()
    gh, gw = max(2, round(cells / stretch)), max(2, round(cells * w / max(h, 1)))
    g = rng.uniform(-1.0, 1.0, (gh, gw))
    y = (np.arange(h) + 0.5) * gh / h - 0.5
    x = (np.arange(w) + 0.5) * gw / w - 0.5
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = (y - y0)[:, None], (x - x0)[None, :]
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)              # smoothstep
    y0m, y1m, x0m, x1m = y0 % gh, (y0 + 1) % gh, x0 % gw, (x0 + 1) % gw
    a = g[y0m][:, x0m]
    b = g[y0m][:, x1m]
    c = g[y1m][:, x0m]
    d = g[y1m][:, x1m]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _blur(a, r: int):
    """Box blur (wrap-around) of a 2-D array with radius ``r`` (two passes)."""
    np = _np()
    if r <= 0:
        return a
    out = a
    for _ in range(2):
        acc = np.zeros_like(out)
        for k in range(-r, r + 1):
            acc += np.roll(out, k, axis=0)
        out = acc / (2 * r + 1)
        acc = np.zeros_like(out)
        for k in range(-r, r + 1):
            acc += np.roll(out, k, axis=1)
        out = acc / (2 * r + 1)
    return out


def _lum(rgb):
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def _blur_rgb(rgb, r: int):
    """Box blur (two passes, wrap-around) of every channel of an ``(H, W, 3)`` array."""
    np = _np()
    if r <= 0:
        return rgb
    return np.stack([_blur(rgb[..., k], r) for k in range(rgb.shape[-1])], axis=-1)


def _photo_pass(np, rng, rgb, H: int, W: int, h: int, w: int, S: int, k: float, km: float, grain: float,
                light: bool):
    """Photo-like variation (see the module docstring): ``k`` = strength (drift, light, hue), ``km`` = strength x
    landing gain (the mid band)."""
    m = min(h, w)

    def field(cells):
        f = _periodic_noise(rng, H, W, cells)
        return f / (f.std() + 1e-9)

    drift = 0.6 * field(max(2, round(m / 48))) + 0.4 * field(max(3, m // 16))
    drift /= drift.std() + 1e-9
    mott = 0.5 * field(max(4, m // 6))
    streak = _periodic_noise(rng, H, W, max(6, m // 4), stretch=4.0)    # soft vertical runs, like light on paint
    mott = mott + 0.5 * streak / (streak.std() + 1e-9)
    mott /= mott.std() + 1e-9
    fine = rng.normal(0.0, 1.0, (h, w))
    fine = 0.5 * fine + 0.5 * _blur(fine, 1)                      # a soft grain, not salt and pepper
    fine /= fine.std() + 1e-9
    fine = np.repeat(np.repeat(fine, S, axis=0), S, axis=1)
    g = min(max(float(grain), 0.0), 1.0)
    mid = np.sqrt(1.0 - g) * mott + np.sqrt(g) * fine
    lum = k * PHOTO_AMP["drift"] * drift + km * PHOTO_AMP["mid"] * mid
    ang = rng.uniform(-0.35, 0.35)
    if light:                                                       # soft light from above, a little sideways
        yy = (np.arange(H) + 0.5) / H - 0.5
        xx = (np.arange(W) + 0.5) / W - 0.5
        lum = lum - k * PHOTO_AMP["light"] * 2.0 * (np.cos(ang) * yy[:, None] + np.sin(ang) * xx[None, :])
    rgb = rgb * np.exp(lum - 0.5 * (k * PHOTO_AMP["drift"]) ** 2)[..., None]
    hue = np.stack([field(max(2, round(m / 32))) for _ in range(3)], axis=-1)
    hue -= hue.mean(axis=-1, keepdims=True)                         # hue and saturation, not brightness
    return rgb * (1.0 + PHOTO_AMP["hue"] * k * hue)


def _apply(img, mask, preset: dict, seed: int, gain: float = 1.0, edge=None):
    """One finishing pass; ``gain`` scales the noise (the caller calibrates it to the preset contrast). The preset
    may carry ``ss_in`` (the image is painted at that many times the output size), ``soft`` (blur in output pixels),
    ``grime`` and ``wear`` (edge wear strength, default 0.35)."""
    np = _np()
    ss_in = int(preset.get("ss_in") or 1)
    if ss_in > 1:
        S = ss_in
        H, W = img.shape[:2]
        h, w = H // S, W // S
        big = img.astype(np.float64) / 255.0
    else:
        S = _SS
        h, w = img.shape[:2]
        H, W = h * S, w * S
        big = np.repeat(np.repeat(img.astype(np.float64) / 255.0, S, axis=0), S, axis=1)
    rng = np.random.default_rng(seed)
    rgb, alpha = big[..., :3], big[..., 3:4]
    lum = _lum(rgb)[..., None]
    mx, mn = rgb.max(-1), rgb.min(-1)
    cur_sat = float(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0).mean())
    if preset["sat"] is not None and cur_sat > 1e-6 and (cur_sat > preset["sat"] or preset.get("sat_match")):
        k = preset["sat"] / cur_sat
        rgb = lum + (rgb - lum) * (min(k, 4.0) if preset.get("sat_match") else k)
    # noise: soft tileable blotches at two scales + grain at the output resolution (survives the box filter)
    blot = 0.7 * _periodic_noise(rng, H, W, max(4, min(h, w) // 8)) + \
        0.3 * _periodic_noise(rng, H, W, max(8, min(h, w) // 3))
    grain = rng.normal(0.0, 1.0, (h, w))
    grain = np.repeat(np.repeat(grain, S, axis=0), S, axis=1)
    blot /= blot.std() + 1e-9
    g = preset["grain"]
    field = np.sqrt(1 - g) * blot + np.sqrt(g) * grain
    k = gain * float(np.sqrt(np.log1p(preset["contrast"] ** 2)))
    rgb = rgb * np.exp(k * field - k * k / 2)[..., None]               # lognormal: positive, mean kept
    tint = _periodic_noise(rng, H, W, max(3, min(h, w) // 16))
    rgb = rgb * (1.0 + 0.05 * tint[..., None] * rng.uniform(-1.0, 1.0, 3))   # a faint colour cast
    ph = float(preset.get("photo") or 0.0)
    if ph > 0:
        rgb = _photo_pass(np, rng, rgb, H, W, h, w, S, ph, ph * float(preset.get("photo_gain", 1.0)),
                          preset["grain"], bool(preset.get("light", True)))
    if mask is not None:
        m = mask.astype(np.float64) / 255.0
        if ss_in == 1:
            m = np.repeat(np.repeat(m, S, axis=0), S, axis=1)
        m = _blur(m, max(1, S * min(h, w) // 64))
        rgb = rgb * (1.0 - preset["ao"] * (1.0 - m))[..., None]
    s_e = float(preset.get("wear", preset.get("edge", 0.35)))
    if edge is not None and s_e > 0:
        e = edge.astype(np.float64) / 255.0
        if ss_in == 1:
            e = np.repeat(np.repeat(e, S, axis=0), S, axis=1)
        e = np.clip(_blur(e, max(1, S // 2)), 0.0, 1.0)
        light = np.clip(0.55 * rgb + 0.45 * (_lum(rgb)[..., None] + 0.30), 0.0, 1.0)   # lighter and greyer
        rgb = rgb * (1.0 - s_e * e)[..., None] + light * (s_e * e)[..., None]
    gr = np.clip(0.5 + 0.5 * _periodic_noise(rng, H, W, max(3, min(h, w) // 16)), 0.0, 1.0) ** 2
    if preset["grime_bottom"]:
        gr = gr * (0.35 + 0.65 * np.linspace(0.0, 1.0, H)[:, None])
    rgb = rgb * (1.0 - preset["grime"] * gr)[..., None]
    if preset["value"] is not None:
        cur = float(np.clip(rgb, 0, 1).max(-1).mean())
        if cur > 1e-6:
            rgb = rgb * (preset["value"] / cur)
    rgb = np.clip(rgb, 0.0, 1.0)
    rgb = _blur_rgb(rgb, int(round(float(preset.get("soft") or 0.0) * S / 2.0)))   # the soft filter
    out = np.concatenate([rgb, alpha], axis=-1)[:h * S, :w * S].reshape(h, S, w, S, 4).mean(axis=(1, 3))
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def _contrast(img) -> float:
    np = _np()
    lum = _lum(img[..., :3].astype(np.float64) / 255.0)
    m = float(lum.mean())
    return float(lum.std()) / m if m > 1e-6 else 0.0


def _calibrated(img, mask, preset: dict, seed: int, edge=None):
    """Finish with the noise gain fitted (two secant steps) so the luminance contrast meets the preset.

    Returns ``(finished image, gain)``."""
    target = preset["contrast"]
    base = _contrast(img)
    if base >= target:                       # the image already has the detail: keep it, add a light grain
        return _apply(img, mask, preset, seed, gain=0.25, edge=edge), 0.25
    lo_g, lo_c = 0.0, base
    gain = 1.0
    res = _apply(img, mask, preset, seed, gain, edge)
    for _ in range(3):
        c = _contrast(res)
        if abs(c - target) <= 0.05 * target or c <= lo_c + 1e-6:
            break
        gain = max(0.05, min(4.0, lo_g + (gain - lo_g) * (target - lo_c) / (c - lo_c)))
        res = _apply(img, mask, preset, seed, gain, edge)
    return res, gain


def _band(stats: dict, role: str | None) -> list[list]:
    """Rows ``[metric, value, p10, p50, p90, verdict]`` against the vanilla band of ``role``."""
    if role is None:
        return []
    rows = []
    for k, (p10, p50, p90) in VANILLA_BANDS[role].items():
        v = stats[k]
        rows.append([k, v, p10, p50, p90, "in" if p10 <= v <= p90 else ("low" if v < p10 else "high")])
    return rows


def _pick_role(role: str, preset: str, stem: str) -> str:
    """The style role the result is judged by: ``role`` when given, else the preset's, else guessed from the name
    (a name that says nothing means a street prop: the kit's usual own texture)."""
    if role and role != "auto":
        if role not in _ROLES:
            raise SatkError("BAD_PARAMS", f"unknown texture role {role!r}", hint="roles: auto, " + ", ".join(_ROLES))
        return role
    if PRESETS[preset]["role"]:
        return PRESETS[preset]["role"]
    try:
        from ..style import texture as T

        guess = T.role_of(stem)
    except ImportError:
        guess = "generic"
    return "prop" if guess == "generic" else guess


def _role_band(role: str, profile: str) -> tuple[dict[str, tuple[float, float, float]], str]:
    """``({style metric: (p10, p50, p90)}, source)`` of ``role``: the style distribution of the vanilla textures
    when the vanilla index is there, else the measured ``VANILLA_BANDS`` (three roles), else nothing."""
    try:
        from ..style import texture as T

        dist = T.vanilla(profile)
        rr = dist["roles"]
        used = role if rr.get(role, {}).get("n", 0) >= int(T.roles()["min_samples"]) else "generic"
        peer = rr.get(used, {}).get("metrics", {})
        got = {m: tuple(float(x) for x in peer[m][:3]) for m in T.roles()["check"] if m in peer}
        if got:
            return got, f"style.texture vanilla distribution of {used}"
    except (ImportError, SatkError, OSError, ValueError, KeyError):
        pass
    if role in VANILLA_BANDS:
        return {_BAND_METRIC[k]: v for k, v in VANILLA_BANDS[role].items()}, "satk.texmod.finish.VANILLA_BANDS"
    return {}, ""


def _inner(metric: str, p10: float, p90: float) -> tuple[float, float]:
    """The inner part of a band (``BAND_MARGIN`` cut from each end; counts in log space)."""
    import math

    log = metric == "tex.colours"
    a, b = (math.log1p(p10), math.log1p(p90)) if log else (p10, p90)
    lo, hi = a + BAND_MARGIN * (b - a), b - BAND_MARGIN * (b - a)
    return (math.expm1(lo), math.expm1(hi)) if log else (lo, hi)


def _aim(metric: str, v: float, lo: float, hi: float) -> float:
    """Where to move a metric that left the inner band: 20 % of the inner width inside its near end."""
    import math

    log = metric == "tex.colours"
    a, b, x = (math.log1p(lo), math.log1p(hi), math.log1p(v)) if log else (lo, hi, v)
    t = b - 0.2 * (b - a) if x > b else a + 0.2 * (b - a)
    return math.expm1(t) if log else t


def _preview(res):
    """The DXT1 round trip of a finished image: what the game shows (``(h, w, 4)`` uint8)."""
    from ..formats.dxt import decode_rgba
    from .encode import encode_level, to_array

    h, w = res.shape[:2]
    alpha = bool((res[..., 3] < 255).any())
    return to_array(w, h, _dxt1_decode(decode_rgba, encode_level(res, "DXT1", alpha=alpha), w, h, alpha))


def _land_in_band(img, mask, edge, preset: dict, seed: int, band: dict, res, gain: float):
    """Pull the metrics of the DXT1 preview out of the edges of the role band (see the module docstring); with
    ``photo`` only value and saturation (the photo landing owns the detail).

    Returns ``(image, preset used, rows [round, metric, value, inner low, inner high])``."""
    if preset.get("photo"):
        band = {k: v for k, v in band.items() if k in ("tex.val_mean", "tex.sat_mean")}
    from ..style import texture as T

    p = dict(preset)
    rows: list[list] = []
    for rnd in range(1, BAND_ROUNDS + 1):
        prev = _preview(res)
        h, w = prev.shape[:2]
        st = T.texture_stats(prev.tobytes(), w, h)
        moved = False
        for metric, (p10, _p50, p90) in band.items():
            if metric not in st:
                continue
            lo, hi = _inner(metric, p10, p90)
            v = float(st[metric])
            if lo <= v <= hi:
                continue
            rows.append([rnd, metric, round(v, 3), round(lo, 3), round(hi, 3)])
            tgt = _aim(metric, v, lo, hi)
            if metric == "tex.val_mean" and v > 1e-6:
                p["value"] = max(0.02, min(1.0, (p.get("value") or v) * tgt / v))
                moved = True
            elif metric == "tex.sat_mean" and v > 1e-6:
                # the first correction switches the saturation step from "only desaturate" to "match the target"
                p["sat"] = max(0.005, min(1.0, p["sat"] * tgt / v if p.get("sat_match") else tgt))
                p["sat_match"] = True
                moved = True
            elif metric == "tex.hf_energy" and v > 1e-6:
                gain = max(0.05, min(6.0, gain * (tgt / v) ** 0.9))
                moved = True
            elif metric == "tex.colours" and v < tgt:        # too few colours: more grain
                gain = min(6.0, gain * 1.25)
                moved = True
        if not moved:
            break
        res = _apply(img, mask, p, seed, gain, edge)
    return res, p, rows


def _photo_target(role: str, profile: str, photo: float) -> tuple[float, str]:
    """``(target tex.tone_mid in luma levels, source)``: the percentile 10 + 40 x photo (p10 at 0, p34 at 0.6, p50
    at 1) of the fine tonal variation of the role's vanilla textures."""
    try:
        import numpy as np

        from ..style import texture as T

        dist = T.vanilla(profile)
        rr = dist["roles"]
        used = role if rr.get(role, {}).get("n", 0) >= int(T.roles()["min_samples"]) else "generic"
        vals = (rr.get(used, {}).get("values") or {}).get("tex.tone_mid")
        if vals:
            q = 10.0 + 40.0 * photo
            return float(np.percentile(np.asarray(vals, dtype=np.float64), q)),                 f"style.texture vanilla tex.tone_mid p{q:g} of {used}"
    except (ImportError, SatkError, OSError, ValueError, KeyError):
        pass
    lo, hi = PHOTO_FALLBACK
    return lo + photo * (hi - lo), "satk.texmod.finish.PHOTO_FALLBACK"


def _tone_mid(res) -> float:
    from ..style.texture import texture_stats as style_stats

    prev = _preview(res)
    h, w = prev.shape[:2]
    return float(style_stats(prev.tobytes(), w, h).get("tex.tone_mid", 0.0))


def _land_photo(img, mask, edge, preset: dict, seed: int, target: float):
    """Scale the photo variation until the DXT1 preview's ``tex.tone_mid`` is within ``PHOTO_TOL`` of
    ``target``. Returns ``(image, preset used, rows [round, tone_mid, target, gain])``."""
    p = dict(preset)
    p["photo_gain"] = 1.0
    res = _apply(img, mask, p, seed, 0.0, edge)
    rows = []
    for rnd in range(1, PHOTO_ROUNDS + 1):
        tm = _tone_mid(res)
        rows.append([rnd, round(tm, 3), round(target, 3), round(p["photo_gain"], 3)])
        if abs(tm - target) <= PHOTO_TOL * target or (tm > target and p["photo_gain"] <= 0.1 + 1e-9) \
                or (tm < target and p["photo_gain"] >= 4.0 - 1e-9):
            break
        p["photo_gain"] = min(4.0, max(0.1, p["photo_gain"] * min(2.5, max(0.4, target / max(tm, 0.05)))))
        res = _apply(img, mask, p, seed, 0.0, edge)
    return res, p, rows


def _load_gray(name: str, label: str, h: int, w: int, warn: list[str]):
    """A grey mask (``(h, w)`` float, 0-255) of the image's size; another size is resized with a warning."""
    from .api import find_input, load_image

    np = _np()
    mp = find_input(name)
    m = load_image(mp)
    if m.shape[:2] != (h, w):
        from ..core.errors import require_module

        Image = require_module("PIL.Image", pip="Pillow", purpose=f"resizing the {label} to the image")
        mh, mw = m.shape[:2]
        im = Image.fromarray(np.ascontiguousarray(m), "RGBA").resize((w, h), Image.Resampling.LANCZOS)
        m = np.asarray(im, dtype=np.uint8)
        warn.append(f"RESIZED_MASK: {label} {mp.name} {mw}x{mh} -> {w}x{h}")
    return m[..., :3].astype(np.float64).mean(-1)


def finish(src: str, preset: str = "photo_like", mask: str | None = None, out: str | None = None,
           edge: str | None = None, role: str = "auto", profile: str = "vanilla", grime: float | None = None,
           wear: float | None = None, grain: float | None = None, soft: float = SOFT_DEFAULT,
           supersample: int = 1, photo: float = 0.0) -> dict:
    """Finish one image (see the module docstring); writes ``<stem>-<preset>.png`` and ``...-dxt.png``.

    ``grime``/``wear``/``grain`` override the preset (``None``: the preset's, grime and wear 0 for vehicle roles;
    grime 0 for every role with ``photo``); ``soft`` = blur of the soft filter in output pixels; ``supersample`` =
    the input is painted at that many times the output size (1, 2, 4 or 8); ``photo`` = strength of the photo-like
    variation (0 = off, the noise finish)."""
    from ..core.envelope import table
    from ..media import png as _png
    from .api import find_input, load_image

    if preset not in PRESETS:
        raise SatkError("BAD_PARAMS", f"unknown preset {preset!r}", did_you_mean=sorted(PRESETS))
    p = find_input(src)
    img = load_image(p)
    _np()
    if int(supersample) not in SUPERSAMPLES:
        raise SatkError("BAD_PARAMS", f"supersample must be one of {SUPERSAMPLES}, got {supersample}")
    ss = int(supersample)
    H, W = img.shape[:2]
    if H % ss or W % ss:
        raise SatkError("BAD_PARAMS", f"{p.name}: {W}x{H} is not a multiple of supersample {ss}",
                        hint="paint at exactly 2, 4 or 8 times the texture size (512x512 for a 128 px texture at 4x)")
    h, w = H // ss, W // ss
    if w % 4 or h % 4:
        raise SatkError("BAD_PARAMS", f"{p.name}: the output {w}x{h} is not a multiple of 4 (the DXT preview needs "
                        "whole blocks)", hint="resize to a power of two first (texture pack does that with --pot)")
    for nm, v, hi in (("grime", grime, 1.0), ("wear", wear, 1.0), ("grain", grain, 1.0), ("soft", soft, 8.0),
                      ("photo", photo, 1.0)):
        if v is not None and not 0.0 <= float(v) <= hi:
            raise SatkError("BAD_PARAMS", f"{nm} must be 0..{hi:g}, got {v}")
    warn: list[str] = []
    mimg = _load_gray(mask, "mask", H, W, warn) if mask else None
    eimg = _load_gray(edge, "edge mask", H, W, warn) if edge else None
    role_used = _pick_role(role, preset, p.stem)
    pre = dict(PRESETS[preset])
    vehicle = role_used in VEHICLE_ROLES
    ph = float(photo or 0.0)
    pre["grime"] = float(grime) if grime is not None else (0.0 if vehicle or ph > 0 else pre["grime"])
    pre["wear"] = float(wear) if wear is not None else (0.0 if vehicle else 0.35)
    if grain is not None:
        pre["grain"] = float(grain)
    elif ph > 0:
        pre["grain"] = 0.5
    pre["soft"] = float(soft)
    pre["ss_in"] = ss
    if ph > 0:
        pre["photo"] = ph
        pre["light"] = role_used not in TILING_ROLES
        if not PRESETS[preset]["role"]:
            pre["sat"] = None          # keep the painted colour; the band landing corrects only an outlier
    salt = (mimg.tobytes() if mimg is not None else b"") + (b"E" + eimg.tobytes() if eimg is not None else b"")
    seed = int.from_bytes(hashlib.blake2b(img.tobytes() + preset.encode() + salt, digest_size=8).digest(), "little")
    photo_rows: list[list] = []
    if ph > 0:
        target, photo_src = _photo_target(role_used, profile, ph)
        res, pre, photo_rows = _land_photo(img, mimg, eimg, pre, seed, target)
        gain = 0.0
    else:
        res, gain = _calibrated(img, mimg, pre, seed, eimg)
    band_all, band_src = _role_band(role_used, profile)
    landing: list[list] = []
    if band_all:
        res, _used, landing = _land_in_band(img, mimg, eimg, pre, seed, band_all, res, gain)
    alpha = bool((res[..., 3] < 255).any())
    prev = _preview(res)
    if out:
        d = ensure_writable(Path(out) if Path(out).is_absolute() else Path.cwd() / out)
        d.mkdir(parents=True, exist_ok=True)
    else:
        d = work("out", "texmod", "finish")
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in p.stem) or "tex"
    f_out = d / f"{stem}-{preset}.png"
    f_dxt = d / f"{stem}-{preset}-dxt.png"
    _png.write_file(f_out, _png.encode(w, h, res.tobytes(), opaque=not alpha))
    _png.write_file(f_dxt, _png.encode(w, h, prev.tobytes(), opaque=not alpha))
    before, after, dxt_s = texture_stats(img), texture_stats(res), texture_stats(prev)
    rows = [[k, before[k], after[k], dxt_s[k]] for k in ("value", "sat", "lum_std", "colours", "colours15")]
    env = table(["metric", "input", "finished", "dxt1"], rows)
    band = _band(dxt_s, role_used if role_used in VANILLA_BANDS else None)
    style = _style_texture(f_dxt, role_used)
    if style is not None:
        env["style"] = style
    elif band:
        env["band"] = {"role": role_used, "cols": ["metric", "dxt1", "p10", "p50", "p90", "verdict"], "rows": band,
                       "source": "vanilla game, see satk.texmod.finish.VANILLA_BANDS"}
        env["in_band"] = all(r[-1] == "in" for r in band)
    if landing:
        env["landing"] = {"cols": ["round", "metric", "value", "inner_low", "inner_high"], "rows": landing[:12],
                          "source": band_src}
    if photo_rows:
        env["photo"] = {"strength": ph, "cols": ["round", "tone_mid", "target", "gain"], "rows": photo_rows,
                        "source": photo_src}
    env.update(file=jpath(f_out), dxt_preview=jpath(f_dxt), preset=preset, role=role_used, size=f"{w}x{h}",
               grime=pre["grime"], wear=pre["wear"], soft=pre["soft"])
    if ss > 1:
        env["supersample"] = ss
    if warn:
        env["warn"] = warn
    meta = {"source": jpath(p), "preset": preset, "role": role_used, "seed": seed,
            "knobs": {k: pre[k] for k in ("grime", "wear", "grain", "soft", "ss_in", "photo", "photo_gain")
                      if k in pre},
            "stats": {"input": before, "finished": after, "dxt1": dxt_s}}
    atomic_write(d / f"{stem}-{preset}.json", json.dumps(meta, indent=1, sort_keys=True) + "\n")
    return env


def _dxt1_decode(decode_rgba, blocks: bytes, w: int, h: int, alpha: bool) -> bytes:
    """Decode DXT1 blocks the way the game reader does: wrap them in a TXD and read it back."""
    from ..formats.txd import mip0_bytes, parse_txd
    from .txdwrite import FILTER_TRILINEAR, NativeSpec, native_chunk, txd_chunk

    data = txd_chunk([native_chunk(NativeSpec("preview", "DXT1", w, h, (blocks,), alpha, filter=FILTER_TRILINEAR))])
    t = parse_txd(data).textures[0]
    return decode_rgba(t, mip0_bytes(data, t))


def _style_texture(path: Path, role: str | None) -> dict | None:
    """The ``style.texture`` answer for the DXT preview when satk.style provides it (else ``None``)."""
    from ..core.registry import get_op, invoke

    try:
        spec = get_op("style.texture")
    except SatkError:
        return None
    if not spec.params:
        return None
    args: dict = {spec.params[0].name: jpath(path)}
    if role and any(p.name == "role" for p in spec.params):
        args["role"] = role
    env = invoke(spec, args)
    return env if env.get("ok") else None
