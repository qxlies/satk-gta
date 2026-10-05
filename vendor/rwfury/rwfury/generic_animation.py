"""Format-agnostic skeletal animation data and binary channel helpers."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Iterator, Literal


FloatFormat = Literal["f32", "f64"]
IntegerFormat = Literal["i16", "i32"]
ByteOrder = Literal["little", "big"]


def _prefix(byte_order: ByteOrder) -> str:
    if byte_order == "little":
        return "<"
    if byte_order == "big":
        return ">"
    raise ValueError("byte_order must be 'little' or 'big'")


def _pack_floats(
    values: list[float],
    fmt: FloatFormat,
    byte_order: ByteOrder,
) -> bytes:
    codes = {"f32": "f", "f64": "d"}
    try:
        code = codes[fmt]
    except KeyError as exc:
        raise ValueError("fmt must be 'f32' or 'f64'") from exc
    return struct.pack(f"{_prefix(byte_order)}{len(values)}{code}", *values)


@dataclass(frozen=True)
class GenericAnimationKeyframe:
    """One local-space keyframe, preserving its position in the source track."""

    index: int
    time: float
    rotation: tuple[float, float, float, float]
    translation: tuple[float, float, float] | None = None
    scale: tuple[float, float, float] | None = None
    raw_time: int | None = None


@dataclass(frozen=True)
class GenericAnimationBuffers:
    """Packed, deinterleaved animation channels ready for another writer."""

    keyframe_count: int
    float_format: FloatFormat
    byte_order: ByteOrder
    times: bytes
    rotations: bytes
    translations: bytes | None
    scales: bytes | None
    raw_times: bytes | None
    raw_time_format: IntegerFormat | None


@dataclass
class GenericAnimationTrack:
    """A format-neutral transform track backed by flat channel arrays.

    Rotations use quaternion ``x, y, z, w`` order. ``bone_id`` is the effective
    target ID while ``source_bone_id`` retains the file value when conversion
    resolves an otherwise unnamed target. Translations and scales use ``x, y,
    z`` order. A missing channel is represented by ``None`` rather than a
    fabricated identity value. Array and keyframe order always matches source
    order, including duplicate or regressing timestamps.
    """

    name: str = ""
    bone_id: int = -1
    source_bone_id: int | None = None
    bone_binding: str = "unresolved"
    times: list[float] = field(default_factory=list)
    rotations: list[float] = field(default_factory=list)
    translations: list[float] | None = None
    scales: list[float] | None = None
    raw_times: list[int | None] | None = None
    source_index: int = 0
    rotation_interpolation: str = "slerp"
    translation_interpolation: str = "linear"
    scale_interpolation: str = "linear"
    source_metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate_layout()

    @property
    def keyframe_count(self) -> int:
        return len(self.times)

    @property
    def has_translation(self) -> bool:
        return self.translations is not None

    @property
    def has_scale(self) -> bool:
        return self.scales is not None

    @property
    def duration(self) -> float:
        finite_times = [time for time in self.times if math.isfinite(time)]
        return max(0.0, max(finite_times, default=0.0))

    def validate_layout(self) -> None:
        count = self.keyframe_count
        self._require_components("rotations", self.rotations, count * 4)
        if self.translations is not None:
            self._require_components("translations", self.translations, count * 3)
        if self.scales is not None:
            self._require_components("scales", self.scales, count * 3)
        if self.raw_times is not None and len(self.raw_times) != count:
            raise ValueError(
                f"raw_times has {len(self.raw_times)} values for {count} keyframes"
            )

    def get_keyframe(self, index: int) -> GenericAnimationKeyframe:
        self.validate_layout()
        normalized_index = index if index >= 0 else self.keyframe_count + index
        if normalized_index < 0 or normalized_index >= self.keyframe_count:
            raise IndexError("keyframe index out of range")
        offset3 = normalized_index * 3
        offset4 = normalized_index * 4
        return GenericAnimationKeyframe(
            index=normalized_index,
            time=self.times[normalized_index],
            rotation=tuple(self.rotations[offset4:offset4 + 4]),
            translation=(
                tuple(self.translations[offset3:offset3 + 3])
                if self.translations is not None
                else None
            ),
            scale=(
                tuple(self.scales[offset3:offset3 + 3])
                if self.scales is not None
                else None
            ),
            raw_time=(
                self.raw_times[normalized_index]
                if self.raw_times is not None
                else None
            ),
        )

    def iter_keyframes(self) -> Iterator[GenericAnimationKeyframe]:
        self.validate_layout()
        for index in range(self.keyframe_count):
            yield self.get_keyframe(index)

    def times_as_bytes(
        self,
        fmt: FloatFormat = "f32",
        byte_order: ByteOrder = "little",
    ) -> bytes:
        return _pack_floats(self.times, fmt, byte_order)

    def rotations_as_bytes(
        self,
        fmt: FloatFormat = "f32",
        byte_order: ByteOrder = "little",
    ) -> bytes:
        self.validate_layout()
        return _pack_floats(self.rotations, fmt, byte_order)

    def translations_as_bytes(
        self,
        fmt: FloatFormat = "f32",
        byte_order: ByteOrder = "little",
    ) -> bytes:
        self.validate_layout()
        if self.translations is None:
            return b""
        return _pack_floats(self.translations, fmt, byte_order)

    def scales_as_bytes(
        self,
        fmt: FloatFormat = "f32",
        byte_order: ByteOrder = "little",
    ) -> bytes:
        self.validate_layout()
        if self.scales is None:
            return b""
        return _pack_floats(self.scales, fmt, byte_order)

    def raw_times_as_bytes(
        self,
        fmt: IntegerFormat = "i16",
        byte_order: ByteOrder = "little",
    ) -> bytes:
        if self.raw_times is None:
            return b""
        if any(value is None for value in self.raw_times):
            raise ValueError("raw_times contains missing source values")
        codes = {"i16": "h", "i32": "i"}
        try:
            code = codes[fmt]
        except KeyError as exc:
            raise ValueError("fmt must be 'i16' or 'i32'") from exc
        return struct.pack(
            f"{_prefix(byte_order)}{len(self.raw_times)}{code}",
            *self.raw_times,
        )

    def to_buffers(
        self,
        fmt: FloatFormat = "f32",
        byte_order: ByteOrder = "little",
        raw_time_format: IntegerFormat = "i16",
    ) -> GenericAnimationBuffers:
        """Pack each channel separately without inventing absent channels."""
        self.validate_layout()
        return GenericAnimationBuffers(
            keyframe_count=self.keyframe_count,
            float_format=fmt,
            byte_order=byte_order,
            times=self.times_as_bytes(fmt, byte_order),
            rotations=self.rotations_as_bytes(fmt, byte_order),
            translations=(
                self.translations_as_bytes(fmt, byte_order)
                if self.translations is not None
                else None
            ),
            scales=(
                self.scales_as_bytes(fmt, byte_order)
                if self.scales is not None
                else None
            ),
            raw_times=(
                self.raw_times_as_bytes(raw_time_format, byte_order)
                if self.raw_times is not None
                else None
            ),
            raw_time_format=(raw_time_format if self.raw_times is not None else None),
        )

    @staticmethod
    def _require_components(name: str, values: list[float], expected: int) -> None:
        if len(values) != expected:
            raise ValueError(
                f"{name} has {len(values)} components; expected {expected}"
            )


@dataclass
class GenericAnimation:
    """One named animation clip in source order."""

    name: str = ""
    tracks: list[GenericAnimationTrack] = field(default_factory=list)
    source_index: int = 0
    time_unit: str = "seconds"
    time_mode: str = "absolute"
    rotation_order: str = "xyzw"
    transform_space: str = "local"
    rotation_semantics: str = "absolute_local"
    missing_channel_semantics: str = "preserve_bind_pose"
    source_metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max((track.duration for track in self.tracks), default=0.0)

    def get_tracks(self, name: str) -> list[GenericAnimationTrack]:
        name_lower = name.lower()
        return [track for track in self.tracks if track.name.lower() == name_lower]

    def get_track(self, name: str) -> GenericAnimationTrack | None:
        tracks = self.get_tracks(name)
        return tracks[0] if tracks else None

    def get_tracks_by_bone_id(self, bone_id: int) -> list[GenericAnimationTrack]:
        return [track for track in self.tracks if track.bone_id == bone_id]

    def get_tracks_by_source_bone_id(
        self,
        bone_id: int,
    ) -> list[GenericAnimationTrack]:
        return [
            track for track in self.tracks
            if track.source_bone_id == bone_id
        ]


@dataclass
class GenericAnimationSet:
    """A complete format-neutral animation package held in memory."""

    name: str = ""
    animations: list[GenericAnimation] = field(default_factory=list)
    source_format: str = ""
    time_unit: str = "seconds"
    time_mode: str = "absolute"
    rotation_order: str = "xyzw"
    transform_space: str = "local"
    rotation_semantics: str = "absolute_local"
    missing_channel_semantics: str = "preserve_bind_pose"
    source_metadata: dict[str, object] = field(default_factory=dict)

    def get_animations(self, name: str) -> list[GenericAnimation]:
        name_lower = name.lower()
        return [
            animation
            for animation in self.animations
            if animation.name.lower() == name_lower
        ]

    def get_animation(self, name: str) -> GenericAnimation | None:
        animations = self.get_animations(name)
        return animations[0] if animations else None


__all__ = [
    "ByteOrder",
    "FloatFormat",
    "GenericAnimation",
    "GenericAnimationBuffers",
    "GenericAnimationKeyframe",
    "GenericAnimationSet",
    "GenericAnimationTrack",
    "IntegerFormat",
]
