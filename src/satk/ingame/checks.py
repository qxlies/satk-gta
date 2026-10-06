"""Behaviour check suites: run the client jobs of ``satk-testdrive``, take the frames, give verdicts.

A check runs in the game as a job (``mta-resources/satk-testdrive/checks.lua``): the mod lane and the
vanilla reference lane (side by side for ``new`` models, in two passes for ``replace`` models) under the
same conditions. The job pauses at each frame it wants; :func:`run_job` takes it through the satk-agent
``capture`` and resumes the job. The numbers come back to :data:`VERDICTS`, which compare the mod with
the reference (and with the mod's own handling where it has one): ``pass``, ``warn``, ``fail``, ``info``
(look at the frame), ``error`` (the check could not run). Output: ``<work>/out/ingame/<model>/<suite>/``
with one PNG per frame, ``<check>-<frame>-pair.png`` side by side for two-pass models, and
``report.json``. Stdlib only (Pillow is used for the side-by-side frames when it is installed).
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any, Callable

from ..core import paths
from ..core.errors import SatkError

__all__ = ["SUITES", "ABOUT", "TIMEOUT_S", "VERDICTS", "run_job", "verdict", "run_suite", "side_by_side"]

#: Default order of each suite (same as TDC.SUITES in checks.lua).
SUITES: dict[str, list[str]] = {
    "vehicle": ["components", "rest", "speed", "crash", "damage", "lights", "dirt", "lod"],
    "object": ["collision_ped", "collision_car", "lod", "night"],
    "ped": ["anims", "walk"],
    "weapon": ["held", "fire"],
}
#: What each check shows (kind, check) -> text.
ABOUT: dict[tuple[str, str], str] = {
    ("vehicle", "components"): "frames of the model vs vanilla (wheels, doors, bumpers) and the exhaust/light dummies",
    ("vehicle", "rest"): "dropped on flat ground: wheels touching, suspension settling, ride height",
    ("vehicle", "speed"): "runway run with lane keeping: top speed and 0-100 km/h vs vanilla and the handling",
    ("vehicle", "crash"): "50 km/h into a wall: no driving through, collision depth, damage reaction",
    ("vehicle", "damage"): "all panels and doors broken: which damage parts exist",
    ("vehicle", "lights"): "headlights and tail lights at midnight",
    ("vehicle", "dirt"): "dirt level 15",
    ("vehicle", "lod"): "frames at 20/60/140 m (LOD switch) and the model LOD distance",
    ("object", "collision_ped"): "a ped walks into the object: blocked or through",
    ("object", "collision_car"): "a car at 30 km/h into the object: blocked or through",
    ("object", "lod"): "frames at 50/90/115 % of the LOD distance",
    ("object", "night"): "the object at midnight (night prelight)",
    ("ped", "anims"): "six animations: all play, no bone changes length (stretching), proportions vs vanilla",
    ("ped", "walk"): "walks forward for 3 s",
    ("weapon", "held"): "held and aimed by a ped: muzzle position vs the hand, vs vanilla",
    ("weapon", "fire"): "a ped fires: shots and the gun flash",
}
#: Seconds a check may take in the game (frames included).
TIMEOUT_S: dict[str, float] = {"speed": 180.0, "anims": 120.0, "crash": 90.0, "lod": 120.0}
_DEFAULT_TIMEOUT = 90.0
_DAMAGE_PARTS = ("bonnet_dummy", "boot_dummy", "door_lf_dummy", "door_rf_dummy", "door_lr_dummy", "door_rr_dummy",
                 "bump_front_dummy", "bump_rear_dummy", "windscreen_dummy", "wing_lf_dummy", "wing_rf_dummy")


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _fmt(x: Any, nd: int = 2) -> str:
    v = _num(x)
    if v is None:
        return "-"
    return f"{v:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{v:.0f}"


def _origin(v: Any) -> bool:
    return not isinstance(v, list) or len(v) != 3 or all(abs(_num(c) or 0) < 0.01 for c in v)


def _short(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" (+{len(items) - n})" if len(items) > n else "")


# --------------------------------------------------------------------------- verdicts
# each: (mod lane, ref lane or None, manifest model) -> (verdict, value, ref, expect, note)

Verdict = tuple[str, str, str, str, str]


def _v_components(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    mc = set(_l(mod.get("components")))
    notes: list[str] = []
    verdict = "pass"
    if ref is not None:
        rc = set(_l(ref.get("components")))
        missing, extra = sorted(rc - mc), sorted(mc - rc)
        crit = [n for n in missing if n == "chassis" or n.startswith("wheel_")]
        if crit:
            verdict = "fail"
            notes.append("missing frames the game needs: " + _short(crit))
        elif missing:
            verdict = "warn"
            notes.append("missing vs vanilla: " + _short(missing))
        if extra:
            notes.append("extra: " + _short(extra))
        ref_val = f"{len(rc)} frames"
    else:
        ref_val = ""
        wheels = [n for n in mc if n.startswith("wheel_")]
        if "chassis" not in mc or len(wheels) < 2:
            verdict = "fail"
            notes.append("no chassis or fewer than two wheel frames")
    dm, dr = _d(mod.get("dummies")), _d((ref or {}).get("dummies"))
    ex, bb = dm.get("exhaust"), _l(mod.get("bbox"))
    if _origin(ex):
        if ref is None or not _origin(dr.get("exhaust")):
            verdict = "warn" if verdict == "pass" else verdict
            notes.append("no exhaust dummy: the smoke comes out of the model centre")
    elif len(bb) == 6 and any((ex[i] < bb[i] - 0.5) or (ex[i] > bb[i + 3] + 0.5) for i in range(3)):
        verdict = "warn" if verdict == "pass" else verdict
        notes.append(f"exhaust dummy {ex} is outside the body")
    elif ref is not None and not _origin(dr.get("exhaust")) and (ex[1] > 0) != (dr["exhaust"][1] > 0):
        verdict = "warn" if verdict == "pass" else verdict
        notes.append("exhaust at the other end than in vanilla")
    fl = dm.get("light_front_main")
    if not _origin(fl) and fl[1] < 0:
        verdict = "warn" if verdict == "pass" else verdict
        notes.append("front light dummy behind the centre")
    return verdict, f"{len(mc)} frames, exhaust {ex if not _origin(ex) else 'none'}", ref_val, "", "; ".join(notes)


def _v_rest(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    mw = int(_num(mod.get("wheels")) or 0)
    notes: list[str] = []
    verdict = "pass"
    if ref is not None:
        rw = int(_num(ref.get("wheels")) or 0)
        if mw < rw:
            verdict = "fail"
            notes.append(f"only {mw} wheels touch the ground (vanilla {rw}): wheel size or suspension wrong")
        rh, mh = _num(ref.get("ride_m")), _num(mod.get("ride_m"))
        if rh is not None and mh is not None and abs(mh - rh) > 0.15:
            verdict = "warn" if verdict == "pass" else verdict
            notes.append(f"rides {abs(mh - rh):.2f} m {'higher' if mh > rh else 'lower'} than vanilla")
        ref_val = f"{rw} wheels, ride {_fmt(rh)} m"
    else:
        ref_val = ""
        if mw == 0:
            verdict = "fail"
            notes.append("no wheel touches the ground")
    settle = _num(mod.get("settle_s"))
    if settle is not None and settle > 3.0:
        verdict = "warn" if verdict == "pass" else verdict
        notes.append(f"still bouncing after {settle:.1f} s")
    gap = _num(mod.get("gap_m"))
    if gap is not None and gap < -0.3:
        verdict = "warn" if verdict == "pass" else verdict
        notes.append(f"the collision box sinks {abs(gap):.2f} m into the ground")
    value = f"{mw} wheels on the ground, settled {_fmt(settle)} s, ride {_fmt(mod.get('ride_m'))} m"
    return verdict, value, ref_val, "", "; ".join(notes)


def _v_speed(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    top, t100 = _num(mod.get("top_kmh")), _num(mod.get("t100_s"))
    exp = _d(spec.get("expect"))
    has_handling = bool(spec.get("handling"))
    expect = ""
    if _num(exp.get("max_vel")) is not None:
        expect = f"maxVelocity {_fmt(exp['max_vel'], 0)} km/h ({'mod handling' if has_handling else 'vanilla'})"
    value = f"top {_fmt(top, 1)} km/h, 0-100 {_fmt(t100)} s"
    if mod.get("plateau_s") is None:
        value += f" (still accelerating after {_fmt(mod.get('dist_m'), 0)} m)"
    if mod.get("lost_s") is not None:
        return "fail", value, "", expect, f"left its lane after {mod['lost_s']} s: pulls to one side or spins"
    if top is None or top < 20:
        return "fail", value, "", expect, "the vehicle did not move"
    if ref is None:
        return "info", value, "", expect, "no vanilla reference"
    rtop, rt100 = _num(ref.get("top_kmh")), _num(ref.get("t100_s"))
    ref_val = f"top {_fmt(rtop, 1)} km/h, 0-100 {_fmt(rt100)} s"
    if not rtop:
        return "info", value, ref_val, expect, "the reference did not move"
    ratio = top / rtop
    notes = [f"{(ratio - 1) * 100:+.0f}% top speed vs vanilla"]
    verdict = "pass"
    if has_handling:
        mv, rv = _num(exp.get("max_vel")), _num(exp.get("vanilla_max_vel"))
        want = mv / rv if mv and rv else 1.0
        verdict = "info"
        if ratio < 0.8 * want:
            verdict = "warn"
            notes.append(f"well below what the handling asks for (x{want:.2f} of vanilla)")
    else:
        if ratio < 0.92 or ratio > 1.08:
            verdict = "warn"
            notes.append("vanilla handling but a different top speed: wheel size, collision touching the ground?")
        if t100 and rt100 and t100 / rt100 > 1.15:
            verdict = "warn"
            notes.append(f"0-100 {t100 / rt100:.2f}x slower than vanilla")
    return verdict, value, ref_val, expect, "; ".join(notes)


def _v_crash(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    np_, nd = int(_num(mod.get("panels_damaged")) or 0), int(_num(mod.get("doors_damaged")) or 0)
    pen = _num(mod.get("penetration_m")) or 0.0
    value = (f"{_fmt(mod.get('speed_kmh'), 0)} km/h: front {pen:.2f} m into the wall, {np_} panels / {nd} doors "
             f"damaged, health -{_fmt(mod.get('health_loss'), 0)}")
    ref_val = ""
    if ref is not None:
        ref_val = (f"{_num(ref.get('penetration_m')) or 0:.2f} m, {int(_num(ref.get('panels_damaged')) or 0)} "
                   f"panels / {int(_num(ref.get('doors_damaged')) or 0)} doors")
    if mod.get("passed"):
        return "fail", value, ref_val, "stops at the wall", "drove through the wall: collision missing or too small"
    if pen > 0.35:
        return "warn", value, ref_val, "stops at the wall", "the front sinks into the wall: collision shorter " \
            "than the body or too soft"
    if ref is not None and np_ + nd == 0 and (_num(ref.get("panels_damaged")) or 0) > 0:
        return "warn", value, ref_val, "panels damage like vanilla", "no damage reaction (damage parts or " \
            "collision missing?)"
    return "pass", value, ref_val, "stops at the wall", ""


def _v_damage(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    pm = {k for k, v in _d(mod.get("parts")).items() if v}
    value = f"{len(pm)}/{len(_DAMAGE_PARTS)} damage parts"
    if ref is not None:
        pr = {k for k, v in _d(ref.get("parts")).items() if v}
        missing = sorted(pr - pm)
        ref_val = f"{len(pr)}/{len(_DAMAGE_PARTS)}"
        if missing:
            return "warn", value, ref_val, "", "no damage model for: " + _short(missing, 6)
        return "pass", value, ref_val, "", "compare the frames"
    core = [p for p in ("bonnet_dummy", "door_lf_dummy", "door_rf_dummy", "bump_front_dummy", "bump_rear_dummy")
            if p not in pm]
    if core:
        return "warn", value, "", "", "no damage model for: " + _short(core, 6)
    return "pass", value, "", "", "compare the frames"


def _v_lights(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    d, r = _d(mod.get("dummies")), _d((ref or {}).get("dummies"))
    f, b = d.get("light_front_main"), d.get("light_rear_main")
    notes = []
    if _origin(f) and (ref is None or not _origin(r.get("light_front_main"))):
        notes.append("no front light dummy")
    elif not _origin(f) and f[1] < 0:
        notes.append("front light dummy behind the centre")
    if _origin(b) and (ref is None or not _origin(r.get("light_rear_main"))):
        notes.append("no rear light dummy")
    elif not _origin(b) and b[1] > 0:
        notes.append("rear light dummy in front of the centre")
    value = f"front {f if not _origin(f) else '-'}, rear {b if not _origin(b) else '-'}"
    ref_val = ""
    if ref is not None:
        ref_val = f"front {r.get('light_front_main', '-')}, rear {r.get('light_rear_main', '-')}"
    if notes:
        return "warn", value, ref_val, "", "; ".join(notes)
    return "pass", value, ref_val, "", "compare the night frames"


def _v_info(field: str | None = None, label: str = "") -> Callable[[dict, dict | None, dict], Verdict]:
    def fn(mod: dict, ref: dict | None, spec: dict) -> Verdict:
        if field is None:
            return "info", label, "", "", "look at the frames"
        m, r = _num(mod.get(field)), _num((ref or {}).get(field))
        value, ref_val = f"{label} {_fmt(m, 1)}", (f"{_fmt(r, 1)}" if ref is not None else "")
        if m is not None and r and m < 0.5 * r:
            return "warn", value, ref_val, "", f"{label} under half of vanilla: the model vanishes early"
        return "info", value, ref_val, "", "look at the frames"
    return fn


def _v_collision(what: str) -> Callable[[dict, dict | None, dict], Verdict]:
    def fn(mod: dict, ref: dict | None, spec: dict) -> Verdict:
        if what == "ped":
            value = f"ped stopped at {_fmt(mod.get('ped_along'))} m (object {_fmt(mod.get('near'))}.." \
                    f"{_fmt(mod.get('far'))} m)"
        else:
            value = f"car front {_fmt(mod.get('gap_m'))} m from the object"
        ref_val = ("blocked" if (ref or {}).get("blocked") else "through") if ref is not None else ""
        if mod.get("passed"):
            note = "went through the object: no collision" + (" (vanilla blocks)" if (ref or {}).get("blocked") else "")
            return "fail", value, ref_val, "blocked", note
        h = _num(mod.get("height"))
        if what == "ped" and h is not None and h < 0.5:
            return "info", value, ref_val, "blocked", f"only {h:.2f} m high: peds step over it"
        return "pass", value, ref_val, "blocked", ""
    return fn


def _v_anims(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    played, n = _l(mod.get("played")), int(_num(mod.get("anims")) or 0)
    segs, rsegs = _d(mod.get("segments")), _d((ref or {}).get("segments"))
    worst, wname = 0.0, ""
    for name, s in segs.items():
        dev = _num(_d(s).get("dev")) or 0.0
        if dev > worst:
            worst, wname = dev, name
    value = f"{len(played)}/{n} animations, worst bone stretch {worst * 100:.1f}%"
    ref_val = f"{len(_l((ref or {}).get('played')))}/{n}" if ref is not None else ""
    if n and len(played) < n:
        return "fail", value, ref_val, f"{n} play", "did not play: " + _short(
            sorted(set(a for a in ("WALK_civi", "run_civi", "IDLE_chat", "XPRESSscratch", "handsup", "FightA_1"))
                   - set(played)))
    if worst > 0.06:
        return "fail", value, ref_val, "bones keep their length", f"{wname} changes length by {worst * 100:.0f}% " \
            "while animating: broken skeleton or bone ids"
    odd = []
    for name, s in segs.items():
        a, b = _num(_d(s).get("len")), _num(_d(rsegs.get(name)).get("len"))
        if a and b and not 0.75 <= a / b <= 1.33:
            odd.append(f"{name} x{a / b:.2f}")
    if odd:
        return "warn", value, ref_val, "proportions like vanilla", "bone lengths differ from vanilla: " + _short(odd)
    return "pass", value, ref_val, "", ""


def _v_walk(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    m, r = _num(mod.get("moved_m")) or 0.0, _num((ref or {}).get("moved_m"))
    value, ref_val = f"moved {m:.2f} m in 3 s", (f"{_fmt(r)} m" if ref is not None else "")
    if m < 1.0:
        return "fail", value, ref_val, "", "the ped does not walk (animation or collision problem)"
    if r and m < 0.6 * r:
        return "warn", value, ref_val, "", "walks much shorter than vanilla"
    return "pass", value, ref_val, "", ""


def _v_held(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    want = spec.get("weapon")
    m, r = _num(mod.get("muzzle_hand_aim_m")), _num((ref or {}).get("muzzle_hand_aim_m"))
    value = f"muzzle {_fmt(m)} m from the hand (aiming), {_fmt(mod.get('muzzle_hand_m'))} m idle"
    ref_val = f"{_fmt(r)} m" if ref is not None else ""
    if want is not None and mod.get("weapon") != want:
        return "fail", value, ref_val, f"weapon {want}", f"the ped holds weapon {mod.get('weapon')}"
    if m is not None and r is not None and abs(m - r) > 0.15:
        return "warn", value, ref_val, "", f"muzzle {abs(m - r):.2f} m off vanilla: bullets and flash start " \
            "elsewhere than the model's barrel"
    return "pass", value, ref_val, "", "look at the hand in the frames"


def _v_fire(mod: dict, ref: dict | None, spec: dict) -> Verdict:
    n = int(_num(mod.get("fired")) or 0)
    ref_val = f"{int(_num((ref or {}).get('fired')) or 0)} shots" if ref is not None else ""
    if n == 0:
        return "fail", "no shot fired", ref_val, "shots", "the ped could not fire the weapon"
    return "pass", f"{n} shots fired", ref_val, "", "look for the gun flash in the frame"


VERDICTS: dict[tuple[str, str], Callable[[dict, dict | None, dict], Verdict]] = {
    ("vehicle", "components"): _v_components,
    ("vehicle", "rest"): _v_rest,
    ("vehicle", "speed"): _v_speed,
    ("vehicle", "crash"): _v_crash,
    ("vehicle", "damage"): _v_damage,
    ("vehicle", "lights"): _v_lights,
    ("vehicle", "dirt"): _v_info(None, "dirt level 15"),
    ("vehicle", "lod"): _v_info("lod_distance", "LOD distance"),
    ("object", "collision_ped"): _v_collision("ped"),
    ("object", "collision_car"): _v_collision("car"),
    ("object", "lod"): _v_info("lod_distance", "LOD distance"),
    ("object", "night"): _v_info(None, "night"),
    ("ped", "anims"): _v_anims,
    ("ped", "walk"): _v_walk,
    ("weapon", "held"): _v_held,
    ("weapon", "fire"): _v_fire,
}


def verdict(kind: str, check: str, job: dict, spec: dict) -> Verdict:
    """Verdict of one finished job (``{state, result|error}``)."""
    if job.get("state") != "done":
        return "error", "", "", "", str(job.get("error") or job.get("state") or "no result")
    res = _d(job.get("result"))
    lanes = _d(res.get("lanes"))
    mod = _d(lanes.get("mod"))
    ref = lanes.get("ref") if isinstance(lanes.get("ref"), dict) else None
    fn = VERDICTS.get((kind, check))
    if fn is None:
        return "error", "", "", "", f"no verdict for {kind}/{check}"
    v = fn(mod, ref, spec)
    notes = [n for n in _l(res.get("notes")) if isinstance(n, str)]
    if notes:
        v = (v[0], v[1], v[2], v[3], "; ".join([x for x in (v[4], *notes) if x]))
    return v


# --------------------------------------------------------------------------- frames


def _decode(path: Path) -> tuple[int, int, bytes]:
    from ..saap import png as P

    w, h, ch, px = P.decode(path.read_bytes())
    if ch == 3:
        return w, h, px
    out = bytearray(w * h * 3)
    for i in range(w * h):
        if ch == 4:
            out[i * 3:i * 3 + 3] = px[i * 4:i * 4 + 3]
        else:
            out[i * 3:i * 3 + 3] = bytes((px[i],)) * 3
    return w, h, bytes(out)


def side_by_side(a: Path, b: Path, out: Path, gap: int = 6) -> Path:
    """``a`` | ``b`` in one PNG (left = mod, right = vanilla)."""
    try:
        from PIL import Image
    except ImportError:
        Image = None  # noqa: N806
    if Image is not None:
        ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
        canvas = Image.new("RGB", (ia.width + gap + ib.width, max(ia.height, ib.height)), (24, 24, 24))
        canvas.paste(ia, (0, 0))
        canvas.paste(ib, (ia.width + gap, 0))
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        return paths.atomic_write(out, buf.getvalue())
    from ..saap import png as P

    wa, ha, pa = _decode(a)
    wb, hb, pb = _decode(b)
    w, h = wa + gap + wb, max(ha, hb)
    rows = bytearray()
    for y in range(h):
        ra = pa[y * wa * 3:(y + 1) * wa * 3] if y < ha else bytes(wa * 3)
        rb = pb[y * wb * 3:(y + 1) * wb * 3] if y < hb else bytes(wb * 3)
        rows += ra + bytes((24,)) * (gap * 3) + rb
    return paths.atomic_write(out, P.encode(w, h, bytes(rows)))


# --------------------------------------------------------------------------- running


def run_job(bridge, *, check: str, key: str, kind: str, reference: bool, out_dir: Path, w: int, h: int,
            timeout: float, opts: dict | None = None) -> dict[str, Any]:
    """Run one check job in the game: ``{state, result?, error?, shots: [{name, file}]}``."""
    args: dict[str, Any] = {"check": check, "key": key, "kind": kind, "reference": reference}
    if opts:
        args["opts"] = opts
    jid = bridge.client("check.start", args).get("id")
    if not jid:
        raise SatkError("EXTERNAL_TOOL", f"satk-testdrive did not start the check {check}")
    shots: list[dict] = []
    warn: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        info = _d(bridge.client("job", {"id": jid}))
        state = info.get("state")
        if state == "shot":
            shot = _d(info.get("shot"))
            name = str(shot.get("name") or f"frame{len(shots) + 1}")
            prefix = out_dir / f"{check}-{name}"
            file = None
            try:
                cap = bridge.capture(shot.get("pose") or None, prefix, w=w, h=h, settle=shot.get("settle"))
                file = _d(cap.get("files")).get("color")
            except SatkError as e:
                warn.append(f"{e.code}: frame {name}: {e.msg}")
            bridge.client("job.resume", {"id": jid, "file": file})
            shots.append({"name": name, "file": file})
            continue
        if state == "done":
            return {"state": "done", "result": info.get("result"), "shots": shots, "warn": warn}
        if state in ("error", "cancelled"):
            return {"state": state, "error": info.get("failure"), "shots": shots, "warn": warn}
        if time.monotonic() > deadline:
            try:
                bridge.client("job.cancel", {"id": jid})
            except SatkError:
                pass
            return {"state": "timeout", "error": f"the check took longer than {timeout:g} s", "shots": shots,
                    "warn": warn}
        time.sleep(0.25)


def _pairs(check: str, shots: list[dict], out_dir: Path, warn: list[str]) -> list[str]:
    """Side-by-side frames of two-pass checks; the frames to show, best first."""
    files = {s["name"]: s["file"] for s in shots if s.get("file")}
    shown: list[str] = []
    for name, f in files.items():
        if name.endswith("-mod"):
            base = name[:-4]
            r = files.get(base + "-ref")
            if r:
                try:
                    shown.append(paths.jpath(side_by_side(Path(f), Path(r), out_dir / f"{check}-{base}-pair.png")))
                    continue
                except (OSError, ValueError) as e:
                    warn.append(f"INTERNAL: side-by-side {check}-{base}: {e}")
            shown.append(f)
        elif name.endswith("-both"):
            shown.append(f)
    return shown


def run_suite(bridge, spec: dict, *, kind: str, checks: list[str], reference: bool, out_dir: Path, w: int, h: int,
              timeout: float, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Run ``checks`` for the manifest model ``spec``; rows, raw results and the report path."""
    rows: list[list] = []
    results: dict[str, Any] = {}
    warn: list[str] = []
    t_end = time.monotonic() + timeout
    for i, check in enumerate(checks, 1):
        left = t_end - time.monotonic()
        if left <= 5:
            rows.append([check, "error", "", "", "", "the suite ran out of time", ""])
            continue
        if progress:
            progress(f"{i}/{len(checks)} {check}")
        job = run_job(bridge, check=check, key=spec["key"], kind=kind, reference=reference, out_dir=out_dir, w=w,
                      h=h, timeout=min(left, TIMEOUT_S.get(check, _DEFAULT_TIMEOUT)))
        warn += job.pop("warn", [])
        shown = _pairs(check, job.get("shots") or [], out_dir, warn)
        v = verdict(kind, check, job, spec)
        rows.append([check, v[0], v[1], v[2], v[3], v[4], shown[0] if shown else ""])
        results[check] = {**job, "frames": shown, "about": ABOUT.get((kind, check), "")}
    counts: dict[str, int] = {}
    for r in rows:
        counts[r[1]] = counts.get(r[1], 0) + 1
    report = {"model": spec, "kind": kind, "reference": reference, "rows": rows, "counts": counts,
              "results": results}
    rp = paths.atomic_write(out_dir / "report.json", json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return {"rows": rows, "counts": counts, "report": paths.jpath(rp), "warn": warn}
