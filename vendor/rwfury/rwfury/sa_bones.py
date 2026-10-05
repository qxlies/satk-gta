"""Canonical GTA San Andreas pedestrian HAnim bone tags and names."""

from __future__ import annotations

from enum import IntEnum


class SaBoneTag(IntEnum):
    UNKNOWN = -1

    ROOT = 0
    PELVIS = 1
    SPINE = 2
    SPINE1 = 3
    NECK = 4
    HEAD = 5
    LEFT_BROW = 6
    RIGHT_BROW = 7
    JAW = 8

    RIGHT_CLAVICLE = 21
    RIGHT_UPPER_ARM = 22
    RIGHT_FOREARM = 23
    RIGHT_HAND = 24
    RIGHT_FINGER = 25
    RIGHT_FINGER_01 = 26

    LEFT_CLAVICLE = 31
    LEFT_UPPER_ARM = 32
    LEFT_FOREARM = 33
    LEFT_HAND = 34
    LEFT_FINGER = 35
    LEFT_FINGER_01 = 36

    LEFT_THIGH = 41
    LEFT_CALF = 42
    LEFT_FOOT = 43
    LEFT_TOE = 44

    RIGHT_THIGH = 51
    RIGHT_CALF = 52
    RIGHT_FOOT = 53
    RIGHT_TOE = 54

    BELLY = 201
    RIGHT_BREAST = 301
    LEFT_BREAST = 302

    @property
    def canonical_name(self) -> str | None:
        return SA_BONE_NAMES.get(self)

    @classmethod
    def from_name(cls, name: str) -> SaBoneTag:
        tag = sa_bone_tag_from_name(name)
        if tag is None:
            raise ValueError(f"Unknown GTA San Andreas bone name: {name!r}")
        return tag


SA_BONE_NAMES: dict[SaBoneTag, str] = {
    SaBoneTag.ROOT: "Root",
    SaBoneTag.PELVIS: "Pelvis",
    SaBoneTag.SPINE: "Spine",
    SaBoneTag.SPINE1: "Spine1",
    SaBoneTag.NECK: "Neck",
    SaBoneTag.HEAD: "Head",
    SaBoneTag.LEFT_BROW: "L Brow",
    SaBoneTag.RIGHT_BROW: "R Brow",
    SaBoneTag.JAW: "Jaw",
    SaBoneTag.RIGHT_CLAVICLE: "Bip01 R Clavicle",
    SaBoneTag.RIGHT_UPPER_ARM: "R UpperArm",
    SaBoneTag.RIGHT_FOREARM: "R Forearm",
    SaBoneTag.RIGHT_HAND: "R Hand",
    SaBoneTag.RIGHT_FINGER: "R Fingers",
    SaBoneTag.RIGHT_FINGER_01: "R Finger01",
    SaBoneTag.LEFT_CLAVICLE: "Bip01 L Clavicle",
    SaBoneTag.LEFT_UPPER_ARM: "L UpperArm",
    SaBoneTag.LEFT_FOREARM: "L Forearm",
    SaBoneTag.LEFT_HAND: "L Hand",
    SaBoneTag.LEFT_FINGER: "L Fingers",
    SaBoneTag.LEFT_FINGER_01: "L Finger01",
    SaBoneTag.LEFT_THIGH: "L Thigh",
    SaBoneTag.LEFT_CALF: "L Calf",
    SaBoneTag.LEFT_FOOT: "L Foot",
    SaBoneTag.LEFT_TOE: "L Toe",
    SaBoneTag.RIGHT_THIGH: "R Thigh",
    SaBoneTag.RIGHT_CALF: "R Calf",
    SaBoneTag.RIGHT_FOOT: "R Foot",
    SaBoneTag.RIGHT_TOE: "R Toe",
    SaBoneTag.BELLY: "Belly",
    SaBoneTag.RIGHT_BREAST: "R Breast",
    SaBoneTag.LEFT_BREAST: "L Breast",
}


def _normalize_bone_name(name: str) -> str:
    return " ".join(name.split()).casefold()


_SA_BONE_TAGS_BY_NAME = {
    _normalize_bone_name(name): tag
    for tag, name in SA_BONE_NAMES.items()
}
_SA_BONE_TAGS_BY_NAME.update({
    "normal": SaBoneTag.ROOT,
    "l finger": SaBoneTag.LEFT_FINGER,
    "r finger": SaBoneTag.RIGHT_FINGER,
    "l toe0": SaBoneTag.LEFT_TOE,
    "r toe0": SaBoneTag.RIGHT_TOE,
})


def sa_bone_tag_from_name(name: str) -> SaBoneTag | None:
    """Resolve IFP canonical names and common DFF frame-name variants."""
    return _SA_BONE_TAGS_BY_NAME.get(_normalize_bone_name(name))


def sa_bone_name_from_tag(tag: int | SaBoneTag) -> str | None:
    """Return the canonical animation name for a known SA HAnim bone tag."""
    try:
        return SA_BONE_NAMES.get(SaBoneTag(tag))
    except ValueError:
        return None


__all__ = [
    "SA_BONE_NAMES",
    "SaBoneTag",
    "sa_bone_name_from_tag",
    "sa_bone_tag_from_name",
]
