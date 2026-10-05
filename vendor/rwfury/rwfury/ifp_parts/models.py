"""Public IFP data models and their object-level API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Literal

from ..sa_bones import SaBoneTag, sa_bone_tag_from_name
from . import sampling


IfpOutOfRange = Literal["clamp", "error"]


class IfpVersion(IntEnum):
    ANPK = 1
    ANP3 = 3


class IfpFrameType(IntEnum):
    CHILD_FLOAT = 1
    ROOT_FLOAT = 2
    CHILD = 3
    ROOT = 4


class IfpKeyframeType(str, Enum):
    NONE = "K000"
    ROTATION = "KR00"
    ROTATION_TRANSLATION = "KRT0"
    ROTATION_TRANSLATION_SCALE = "KRTS"


@dataclass
class IfpFrame:
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    time: float = 0.0
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] | None = None
    raw_time: int | None = None

    def interpolate(self, other: IfpFrame, factor: float) -> IfpFrame:
        return sampling.interpolate_frame(self, other, factor)

    def to_matrix(self) -> tuple[float, ...]:
        return sampling.frame_to_matrix(self)


@dataclass(frozen=True)
class IfpValidationIssue:
    code: str
    message: str
    animation_name: str | None = None
    object_name: str | None = None


class IfpValidationError(ValueError):
    def __init__(self, issues: list[IfpValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


@dataclass
class IfpObject:
    name: str = ""
    frame_type: int | IfpFrameType = IfpFrameType.CHILD
    bone_id: int = 0
    frames: list[IfpFrame] = field(default_factory=list)
    keyframe_type: IfpKeyframeType | None = None
    anpk_name_unknown: int = 0
    anpk_unknown: tuple[int, int] = (0, 0)
    anpk_extra: int | None = None
    _raw_name_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_key_data: bytes | None = field(default=None, repr=False, compare=False)
    _anpk_anim_size: int | None = field(default=None, repr=False, compare=False)
    _raw_cpan_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_anim_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_key_padding: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def has_translation(self) -> bool:
        if self.keyframe_type is not None:
            return self.keyframe_type in (
                IfpKeyframeType.ROTATION_TRANSLATION,
                IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
            )
        return int(self.frame_type) in (
            int(IfpFrameType.ROOT_FLOAT),
            int(IfpFrameType.ROOT),
        )

    @property
    def has_scale(self) -> bool:
        return self.keyframe_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE

    @property
    def resolved_bone_id(self) -> int:
        """Return the source ID or GTA SA's canonical name-derived HAnim ID."""
        if self.bone_id != int(SaBoneTag.UNKNOWN):
            return self.bone_id
        tag = sa_bone_tag_from_name(self.name)
        return int(tag) if tag is not None else int(SaBoneTag.UNKNOWN)

    @property
    def bone_tag(self) -> SaBoneTag | None:
        """Return the resolved ID as a known SA bone tag when possible."""
        try:
            return SaBoneTag(self.resolved_bone_id)
        except ValueError:
            return None

    @property
    def duration(self) -> float:
        return sampling.object_duration(self)

    def sample(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> IfpFrame | None:
        return sampling.sample_object(self, time, out_of_range)

    def sample_matrix(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> tuple[float, ...] | None:
        frame = self.sample(time, out_of_range=out_of_range)
        return frame.to_matrix() if frame is not None else None


@dataclass
class IfpAnimation:
    name: str = ""
    objects: list[IfpObject] = field(default_factory=list)
    unknown: int = 1
    declared_frame_data_size: int | None = None
    _raw_name_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_name_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_dgan_padding: bytes | None = field(default=None, repr=False, compare=False)
    _raw_info_data: bytes | None = field(default=None, repr=False, compare=False)
    _raw_info_padding: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def frame_data_size(self) -> int:
        total = 0
        for obj in self.objects:
            if obj.keyframe_type not in (None, IfpKeyframeType.NONE):
                frame_size = _anpk_frame_size(obj.keyframe_type)
            else:
                frame_size = _anp3_frame_size(int(obj.frame_type))
            total += len(obj.frames) * frame_size
        return total

    def get_object(self, name: str) -> IfpObject | None:
        name_lower = name.lower()
        for obj in self.objects:
            if obj.name.lower() == name_lower:
                return obj
        return None

    @property
    def duration(self) -> float:
        return sampling.animation_duration(self)

    def sample(
        self,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> list[tuple[IfpObject, IfpFrame | None]]:
        return sampling.sample_animation(self, time, out_of_range)


def _anpk_frame_size(key_type: IfpKeyframeType) -> int:
    sizes = {
        IfpKeyframeType.ROTATION: 20,
        IfpKeyframeType.ROTATION_TRANSLATION: 32,
        IfpKeyframeType.ROTATION_TRANSLATION_SCALE: 44,
    }
    try:
        return sizes[key_type]
    except KeyError as exc:
        raise ValueError(
            f"ANPK keyframe type {key_type.value!r} cannot contain frames"
        ) from exc


def _anp3_frame_size(frame_type: int) -> int:
    sizes = {
        int(IfpFrameType.CHILD_FLOAT): 20,
        int(IfpFrameType.ROOT_FLOAT): 32,
        int(IfpFrameType.ROOT): 16,
        int(IfpFrameType.CHILD): 10,
    }
    try:
        return sizes[frame_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported ANP3 frame type: {frame_type}") from exc
