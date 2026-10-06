# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in the parent directory.
"""IFP key frames <-> Blender actions on a DragonFF armature.

The engine poses a skinned ped bone by bone: the world (clump/armature space) matrix of a bone is
``W = W_parent @ L`` with ``L = T(t) @ R(q)``: ``q`` = the key's rotation, ``t`` = the key's translation, or the
bone's rest offset from its parent when the sequence has no translation. DragonFF's bone rest matrices
(``bone.matrix_local``) are the skin's bind matrices in armature space, so with ``R = parent_rest^-1 @ rest`` a
pose bone's basis is ``R^-1 @ L``: rotation ``q_R^-1 * q``, location ``q_R^-1 * (t - r)`` (zero for rotation-only
sequences). Export inverts it: ``q = q_R * q_basis``, ``t = r + q_R * loc``. Quaternion signs are kept (no
flip removal on import), so a compressed animation comes back to the same int16 values.

Sequences that bind to no bone (or lose to a later one on the same bone) and the original sequence order, names
and tags are stored on the action (``action["satk_anim"]``, JSON) so an exported action reproduces the file.
"""

from __future__ import annotations

import json
import math
import struct

import bpy
from mathutils import Euler, Quaternion, Vector

PROP = "satk_anim"
_ROT = "rotation_quaternion"
_LOC = "location"
_EULER = "rotation_euler"
_AXIS = "rotation_axis_angle"
_F32 = struct.Struct("<f")


# --------------------------------------------------------------------------- armature


def armatures() -> list:
    return [o for o in bpy.data.objects if o.type == "ARMATURE"]


def pick_armature(name: str | None = None):
    """The armature named ``name``, else the one with the most bones."""
    arms = armatures()
    if name:
        for o in arms:
            if o.name == name or o.data.name == name:
                return o
        raise LookupError(f"no armature {name!r} (have: {', '.join(o.name for o in arms) or 'none'})")
    if not arms:
        raise LookupError("the scene has no armature: import a skinned DFF with DragonFF first")
    return max(arms, key=lambda o: (len(o.data.bones), o.name))


def skeleton_of(arm):
    """``satk.anim.skeleton.Skeleton`` of an armature: DragonFF's ``bone_id`` custom properties are the HAnim ids."""
    from satk.anim.skeleton import Bone, Skeleton, engine_bone_names

    bones = list(arm.data.bones)
    idx = {b.name: i for i, b in enumerate(bones)}
    out = []
    for i, b in enumerate(bones):
        try:
            bid = int(b.get("bone_id", -1))
        except (TypeError, ValueError):
            bid = -1
        out.append(Bone(i, bid, b.name, idx[b.parent.name] if b.parent else -1))
    skinned = any(x.id >= 0 for x in out)
    return Skeleton(arm.name, out, skinned, "blender", engine_bone_names() if skinned else {})


def _rest(bone) -> tuple[Quaternion, Vector]:
    """Rotation and translation of the bone's rest matrix relative to its parent bone (armature space for roots)."""
    m = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local.copy()
    return m.to_quaternion().normalized(), m.to_translation()


# --------------------------------------------------------------------------- fcurves


def _channelbag(action, arm, create: bool):
    from bpy_extras import anim_utils

    ad = arm.animation_data
    slot = ad.action_slot if ad is not None and ad.action == action else None
    if slot is None:
        slot = next((s for s in action.slots if s.target_id_type == "OBJECT"), None)
    if slot is None:
        return None
    if create:
        return anim_utils.action_ensure_channelbag_for_slot(action, slot)
    return anim_utils.action_get_channelbag_for_slot(action, slot)


def _fcurves(action, arm) -> dict:
    """``{(bone name, channel, index): fcurve}`` of the pose bones animated by ``action``."""
    cb = _channelbag(action, arm, False)
    out = {}
    if cb is None:
        return out
    for fc in cb.fcurves:
        dp = fc.data_path
        if not dp.startswith('pose.bones["'):
            continue
        end = dp.rfind('"].')
        if end < 12:
            continue
        name = dp[12:end].replace('\\"', '"').replace("\\\\", "\\")
        prop = dp[end + 3:]
        if prop in (_ROT, _LOC, _EULER, _AXIS, "scale"):
            out[(name, prop, fc.array_index)] = fc
    return out


def _set_keys(action, arm, bone: str, prop: str, index: int, frames: list[float], values: list[float]) -> None:
    path = f'pose.bones["{bpy.utils.escape_identifier(bone)}"].{prop}'
    fc = action.fcurve_ensure_for_datablock(arm, path, index=index, group_name=bone)
    kp = fc.keyframe_points
    kp.add(len(frames))
    co = [0.0] * (2 * len(frames))
    co[0::2] = frames
    co[1::2] = values
    kp.foreach_set("co", co)
    for k in kp:
        k.interpolation = "LINEAR"
    fc.update()


# --------------------------------------------------------------------------- IFP -> action


def _nudge(frame: float) -> float:
    """Separate keys Blender would merge: 1/50 frame, or 4 float32 steps at long times.

    The original times are saved with their adjusted f-curve positions. Export restores them only while those
    positions are unchanged; this also works for float keys, many duplicates and a user-selected frame rate.
    """
    step = 2.0 ** (math.floor(math.log2(max(abs(frame), 1.0))) - 23)
    return max(0.02, 4.0 * step)


def pick_fps(anims) -> int:
    """30 when every key time sits on a 1/30 s frame (the SA norm), else 60."""
    for a in anims:
        for s in a.seqs:
            for t in s.times():
                if not math.isfinite(t):
                    continue
                if abs(t * 30.0 - round(t * 30.0)) > 1e-3:
                    return 60
    return 30


def apply_anim(arm, anim, *, fps: int, action_name: str, source: dict | None = None) -> dict:
    """Create an action with ``anim``'s key frames on ``arm``. Returns stats and the action."""
    from satk.anim.jsonio import anim_to_json

    skel = skeleton_of(arm)
    action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = action
    winners: dict[str, int] = {}
    for i, s in enumerate(anim.seqs):
        if not s.keys:
            continue
        b = skel.bind(s.name, s.tag)
        if b is not None:
            winners[b.name] = i
    mapped = set(winners.values())
    nudged = 0
    doc = anim_to_json(anim)
    meta = {"name": anim.name, "action_name": action.name, "fps": fps,
            "compressed": anim.compressed, "flags": anim.flags,
            "seqs": [{"name": s.name, "tag": s.tag, "trans": s.trans, "compressed": s.compressed,
                      "bone": next((bn for bn, j in winners.items() if j == i), None)} for i, s in enumerate(anim.seqs)],
            "unmapped": {str(i): doc["bones"][i] for i, s in enumerate(anim.seqs) if i not in mapped}}
    if anim.raw_name is not None:
        meta["name_raw"] = anim.raw_name.hex()
    meta.update(source or {})
    for bname, i in winners.items():
        s = anim.seqs[i]
        pb = arm.pose.bones[bname]
        pb.rotation_mode = "QUATERNION"
        qr, rr = _rest(pb.bone)
        qri = qr.inverted()
        frames, rot, loc = [], ([], [], [], []), ([], [], [])
        time_map = []
        for ki, (t, q, tr) in enumerate(s.frames()):
            if not all(math.isfinite(v) for v in (t, *q, *(tr or ()))):
                from satk.core.errors import SatkError

                raise SatkError("UNSUPPORTED", f"{anim.name}/{s.name}: key {ki} is not finite; cannot apply it in Blender")
            f = _F32.unpack(_F32.pack(t * fps))[0]
            if frames and f - frames[-1] < _nudge(frames[-1]):
                f = _F32.unpack(_F32.pack(frames[-1] + _nudge(frames[-1])))[0]
                time_map.append([f, t])
                nudged += 1
            frames.append(f)
            qb = qri @ Quaternion((q[3], q[0], q[1], q[2]))
            for c in range(4):
                rot[c].append(qb[c])
            if tr is not None:
                lb = qri @ (Vector(tr) - rr)
                for c in range(3):
                    loc[c].append(lb[c])
        for c in range(4):
            _set_keys(action, arm, bname, _ROT, c, frames, rot[c])
        if s.trans:
            for c in range(3):
                _set_keys(action, arm, bname, _LOC, c, frames, loc[c])
        if time_map:
            meta["seqs"][i]["time_map"] = time_map
    action[PROP] = json.dumps(meta, separators=(",", ":"))
    action.use_frame_range = True
    end = max(1.0, max((t for s in anim.seqs for t in s.times() if math.isfinite(t)), default=0.0) * fps)
    action.frame_start, action.frame_end = 0.0, end
    return {"action": action, "mapped": len(winners), "unmapped": len(anim.seqs) - len(winners), "frames": end,
            "nudged": nudged}


# --------------------------------------------------------------------------- action -> IFP


def _quat_key(q: Quaternion, prev: tuple | None) -> tuple:
    k = (q.x, q.y, q.z, q.w)
    if prev is not None and sum(a * b for a, b in zip(k, prev)) < 0.0:
        k = (-k[0], -k[1], -k[2], -k[3])
    return k


def _basis_rotation(pb, chans: dict, frame: float) -> Quaternion:
    """Evaluate the rotation mode the pose bone actually uses, including constant unkeyed components."""
    def values(prop, current):
        return tuple(chans[(prop, c)].evaluate(frame) if (prop, c) in chans else v
                     for c, v in enumerate(current))

    if pb.rotation_mode == "QUATERNION":
        return Quaternion(values(_ROT, pb.rotation_quaternion))
    if pb.rotation_mode == "AXIS_ANGLE":
        angle, *axis = values(_AXIS, pb.rotation_axis_angle)
        return Quaternion(Vector(axis), angle) if any(axis) else Quaternion()
    return Euler(values(_EULER, pb.rotation_euler), pb.rotation_mode).to_quaternion()


def _bake_pose(arm, action, frames: list[float], names: set[str]) -> dict:
    """Sample evaluated parent-relative matrices once per frame (constraints, IK and drivers included)."""
    scene = bpy.context.scene
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    ad = arm.animation_data or arm.animation_data_create()
    old_action, old_slot = ad.action, ad.action_slot
    samples = {n: [] for n in names}
    try:
        ad.action = action
        for f in frames:
            base = math.floor(f)
            scene.frame_set(base, subframe=f - base)
            evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
            for name in names:
                pb = evaluated.pose.bones[name]
                local = pb.parent.matrix.inverted() @ pb.matrix if pb.parent else pb.matrix.copy()
                samples[name].append((local.to_quaternion().normalized(), local.to_translation()))
    finally:
        ad.action = old_action
        if old_action is not None and old_slot is not None:
            ad.action_slot = old_slot
        scene.frame_set(old_frame, subframe=old_subframe)
    return samples


def export_action(arm, action, *, fps: int | None = None, compressed: bool | None = None,
                  bake: bool = False, use_meta: bool = True) -> tuple[dict, list[str]]:
    """``action`` as one ``anim/1`` animation object (float values; quantized later by satk.anim).

    Sequences follow the stored order of an imported action (unbound ones come back from the stored JSON); a new
    action (or ``use_meta=False``) gets one sequence per animated bone in armature order (name = bone name, tag =
    ``bone_id``; the root bone and bones with location keys get translation). Added channels are included on
    imported actions too. ``bake`` samples evaluated parent-relative poses at every integer frame of the action's
    range, including constraints, IK, drivers and unkeyed skin bones.
    """
    meta = json.loads(action.get(PROP, "{}") or "{}") if use_meta else {}
    keep_signs = bool(meta.get("seqs")) and not bake
    fps = int(fps or meta.get("fps") or bpy.context.scene.render.fps or 30)
    comp = bool(meta.get("compressed", True) if compressed is None else compressed)
    fcs = _fcurves(action, arm)
    skel = skeleton_of(arm)
    warn: list[str] = []
    by_bone: dict[str, dict] = {}
    for (bname, prop, idx), fc in fcs.items():
        by_bone.setdefault(bname, {})[(prop, idx)] = fc
    plan = [(s.get("bone"), s["name"], int(s["tag"]), bool(s["trans"]), i)
            for i, s in enumerate(meta.get("seqs") or [])]
    known = {name for name, *_rest in plan if name is not None}
    for b in skel.bones:
        if b.name in known or (b.name not in by_bone and not (bake and (b.id >= 0 or not skel.skinned))):
            continue
        has_loc = any(p == _LOC for p, _i in by_bone.get(b.name, {}))
        plan.append((b.name, b.name, b.id if skel.skinned else -1, b.parent < 0 or has_loc, None))
    f0, f1 = action.frame_range
    bake_frames = [float(f) for f in range(int(math.floor(f0)), int(math.ceil(f1)) + 1)] if bake else []
    baked = _bake_pose(arm, action, bake_frames,
                       {n for n, *_rest in plan if n is not None and n in arm.pose.bones}) if bake else {}
    bones_out = []
    for bname, sname, tag, trans, i in plan:
        seq_meta = meta["seqs"][i] if i is not None else {}
        seq_comp = bool(seq_meta.get("compressed", comp)) if compressed is None else bool(compressed)
        if bname is None or (bname not in by_bone and not bake) or bname not in arm.pose.bones:
            stored = (meta.get("unmapped") or {}).get(str(i)) if i is not None else None
            if stored is not None:
                bones_out.append({**stored, "compressed": seq_comp})
            elif bname is not None:
                warn.append(f"BONE_MISSING: {sname!r} ({bname}) has no keys in {action.name}: skipped")
            continue
        chans = by_bone.get(bname, {})
        trans = trans or bake or any(p == _LOC for p, _c in chans)
        if any(p == "scale" for p, _c in chans):
            warn.append(f"SCALE_IGNORED: {sname!r}: IFP playback does not support animated bone scale")
        if bake:
            frames = bake_frames
        else:
            frames = sorted({float(k.co[0]) for fc in chans.values() for k in fc.keyframe_points})
        pb = arm.pose.bones[bname]
        qr, rr = _rest(pb.bone)
        time_map = {f: t * meta.get("fps", fps) / fps for f, t in seq_meta.get("time_map", [])} if not bake else {}
        keys = []
        prev = None
        for fi, f in enumerate(frames):
            if bake:
                rotation, translation = baked[bname][fi]
            else:
                qb = _basis_rotation(pb, chans, f)
                mag = qb.magnitude
                if mag < 1e-8:
                    qb = Quaternion((1.0, 0.0, 0.0, 0.0))
                elif abs(mag - 1.0) > 1e-3:
                    # Preserve the int16 grid of imported rotations; normalize hand-authored rotations.
                    qb.normalize()
                rotation = qr @ qb
                lb = Vector(tuple(chans[(_LOC, c)].evaluate(f) if (_LOC, c) in chans else pb.location[c]
                                  for c in range(3)))
                translation = rr + qr @ lb
            # an imported action carries the source's quaternion signs (some vanilla anims flip between keys):
            # keep them; a new action gets sign continuity (no long-way interpolation in the engine)
            q = _quat_key(rotation, prev if not keep_signs else None)
            prev = q
            row = [time_map.get(f, f / fps), *q]
            if trans:
                row += list(translation)
            keys.append(row)
        d = {"name": sname, "tag": tag, "trans": trans, "keys": keys}
        if seq_comp != comp:
            d["compressed"] = seq_comp
        bones_out.append(d)
    name = meta.get("name") or action.name
    if meta.get("action_name") and action.name != meta["action_name"]:
        name = action.name
    a = {"name": name, "compressed": comp, "bones": bones_out}
    if meta.get("name_raw"):
        a["name_raw"] = meta["name_raw"]
    if meta.get("flags") is not None and meta.get("flags") != (1 if comp else 0):
        a["flags"] = meta["flags"]
    return a, warn
