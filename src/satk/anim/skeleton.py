"""Skeletons animations bind to: skinned DFFs (HAnim bone ids), plain frame hierarchies, the SA ped skeleton.

How the engine binds a sequence (``CAnimBlendAssociation::Init``): a sequence with a bone tag (``!= -1``) goes to
the bone with that HAnim id (``RpAnimBlendClumpFindBone``); a sequence without one goes, on a skinned clump, to
the bone whose **engine bone name** (``ConvertBoneTag2BoneName``: ``Root``, ``Pelvis``, ``R Forearm``, ...;
not the DFF frame name) equals its name case-insensitively, and on a plain clump to the frame with that name.
Frames of a plain clump have no bone tags, so tagged sequences never bind there. A bone that two sequences bind
to keeps the later one.

``data/anim/ped_skeleton.json`` holds the SA ped skeleton: 32 bones (id, DFF frame name, parent, HAnim flags)
shared by all 265 skinned vanilla ped DFFs, and the engine's bone-name table (strings of ``gta_sa.exe``).
Stdlib only.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..core import resources
from ..formats.rw import FormatError, find_child, iter_children, read_chunk

__all__ = ["Bone", "Skeleton", "read_skeleton", "ped_skeleton", "engine_bone_names", "PUSH", "POP"]

_CLUMP, _FRAMELIST, _STRUCT, _EXT, _FRAMENAME, _HANIM = 0x10, 0x0E, 0x01, 0x03, 0x253F2FE, 0x11E
#: HAnim node flags (``rpHANIMPOPPARENTMATRIX``, ``rpHANIMPUSHPARENTMATRIX``).
POP, PUSH = 1, 2


@dataclass(frozen=True)
class Bone:
    """``idx`` = node index (HAnim order) or frame index; ``parent`` = index of the parent bone, -1 = none."""

    idx: int
    id: int
    name: str
    parent: int
    flags: int = 0


@dataclass
class Skeleton:
    """``skinned``: bones are HAnim nodes (bind by tag / engine name); else frames (bind by frame name)."""

    name: str
    bones: list[Bone]
    skinned: bool
    source: str = ""
    engine_names: dict[int, str] = field(default_factory=dict)

    def by_id(self) -> dict[int, Bone]:
        return {b.id: b for b in self.bones if b.id >= 0}

    def bind(self, name: str, tag: int) -> Bone | None:
        """The bone the engine binds a sequence ``(name, tag)`` to, or ``None``."""
        if tag != -1:
            return self.by_id().get(int(tag)) if self.skinned else None
        key = name.upper()
        hit = None
        for b in self.bones:
            label = self.engine_names.get(b.id) if self.skinned else b.name
            if label is not None and label.upper() == key:
                hit = b                                    # ForAllFrames keeps the last match
        return hit

    def root(self) -> Bone | None:
        return self.bones[0] if self.bones else None


def engine_bone_names() -> dict[int, str]:
    """``{bone id: engine bone name}`` (``ConvertBoneTag2BoneName``; strings verified in ``gta_sa.exe``)."""
    d = resources.read_json("anim", "ped_skeleton.json")
    return {int(k): str(v) for k, v in d["engine_names"].items()}


def ped_skeleton() -> Skeleton:
    """The SA ped skeleton shipped in ``data/anim/ped_skeleton.json``."""
    d = resources.read_json("anim", "ped_skeleton.json")
    bones = [Bone(i, int(b["id"]), str(b["name"]), int(b["parent"]), int(b.get("flags", 0)))
             for i, b in enumerate(d["bones"])]
    return Skeleton("sa-ped", bones, True, d.get("source", ""), {int(k): v for k, v in d["engine_names"].items()})


def _frames(buf, cl) -> list[tuple[str | None, int, tuple | None]]:
    """``(name, parent frame, hanim)`` per frame; ``hanim`` = ``(id, [(node id, index, flags)])``."""
    fl = find_child(buf, cl.data_off, cl.end, _FRAMELIST)
    if fl is None:
        raise FormatError("dff", cl.data_off, "clump without a FrameList")
    kids = list(iter_children(buf, fl.data_off, fl.end))
    if not kids or kids[0].type != _STRUCT or kids[0].size < 4:
        raise FormatError("dff", fl.data_off, "FrameList without Struct")
    st = kids[0]
    (n,) = struct.unpack_from("<I", buf, st.data_off)
    if 4 + 56 * n > st.size:
        raise FormatError("dff", st.data_off, f"FrameList struct too short for {n} frames")
    parents = [struct.unpack_from("<i", buf, st.data_off + 4 + 56 * i + 48)[0] for i in range(n)]
    out: list[tuple[str | None, int, tuple | None]] = []
    exts = [k for k in kids[1:] if k.type == _EXT]
    for i in range(n):
        name = None
        han = None
        if i < len(exts):
            e = exts[i]
            for k in iter_children(buf, e.data_off, e.end):
                if k.type == _FRAMENAME:
                    name = bytes(buf[k.data_off:k.end]).split(b"\0", 1)[0].decode("latin-1")
                elif k.type == _HANIM and k.size >= 12:
                    _ver, bid, nb = struct.unpack_from("<IiI", buf, k.data_off)
                    nodes = []
                    if nb and k.size >= 20 + 12 * nb:
                        nodes = [struct.unpack_from("<iii", buf, k.data_off + 20 + 12 * j) for j in range(nb)]
                    han = (bid, nodes)
        out.append((name, parents[i], han))
    return out


def read_skeleton(dff: bytes, name: str = "") -> Skeleton:
    """Skeleton of a DFF: the HAnim hierarchy of a skinned model (node order, ids, frame names, parents from the
    push/pop flags like ``RpAnimBlendClumpInitSkinned``), else every frame below the clump root."""
    cl = read_chunk(dff, 0)
    if cl.type != _CLUMP:
        raise FormatError("dff", 0, f"not a DFF (first chunk 0x{cl.type:X})")
    frames = _frames(dff, cl)
    hier = next(((i, h) for i, (_n, _p, h) in enumerate(frames) if h and h[1]), None)
    names_by_id = {h[0]: (nm or "") for nm, _p, h in frames if h is not None}
    if hier is not None:
        nodes = hier[1][1]
        bones = []
        stack: list[int] = []
        cur = -1
        for i, (bid, _index, flags) in enumerate(nodes):
            parent = cur if i else -1
            bones.append(Bone(i, int(bid), names_by_id.get(bid, ""), parent, int(flags)))
            if flags & PUSH:
                stack.append(cur if i else 0)
            cur = (stack.pop() if stack else 0) if flags & POP else i
        return Skeleton(name or "dff", bones, True, "hanim", engine_bone_names())
    # plain clump: frames below the root, as RpAnimBlendClumpInitNonSkinned orders them (depth first)
    kids: dict[int, list[int]] = {}
    for i, (_n, p, _h) in enumerate(frames):
        kids.setdefault(p, []).append(i)
    order: list[int] = []

    def walk(f: int) -> None:
        for c in kids.get(f, []):
            order.append(c)
            walk(c)

    for r in kids.get(-1, []):
        walk(r)
    pos = {f: k for k, f in enumerate(order)}
    bones = [Bone(k, -1, frames[f][0] or "", pos.get(frames[f][1], -1)) for k, f in enumerate(order)]
    return Skeleton(name or "dff", bones, False, "frames", {})
