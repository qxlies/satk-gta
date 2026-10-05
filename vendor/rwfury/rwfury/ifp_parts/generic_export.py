"""Faithful IFP conversion to the format-agnostic animation model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from ..generic_animation import (
    GenericAnimation,
    GenericAnimationSet,
    GenericAnimationTrack,
)
from .models import IfpAnimation, IfpObject, IfpVersion

if TYPE_CHECKING:
    from .api import IfpPackageApi


def iter_generic_animations(package: IfpPackageApi) -> Iterator[GenericAnimation]:
    """Convert clips lazily while retaining all semantic source values."""
    for animation_index, animation in enumerate(package.animations):
        yield _convert_animation(animation, animation_index)


def to_generic_animation_set(package: IfpPackageApi) -> GenericAnimationSet:
    try:
        source_format = IfpVersion(int(package.version)).name
    except ValueError:
        source_format = str(package.version)
    return GenericAnimationSet(
        name=package.internal_name,
        animations=list(iter_generic_animations(package)),
        source_format=source_format,
        source_metadata={"source_version": int(package.version)},
    )


def _convert_animation(
    animation: IfpAnimation,
    animation_index: int,
) -> GenericAnimation:
    return GenericAnimation(
        name=animation.name,
        tracks=[
            _convert_track(obj, track_index)
            for track_index, obj in enumerate(animation.objects)
        ],
        source_index=animation_index,
        source_metadata={
            "source_unknown": animation.unknown,
            "declared_frame_data_size": animation.declared_frame_data_size,
            "computed_frame_data_size": animation.frame_data_size,
        },
    )


def _convert_track(obj: IfpObject, track_index: int) -> GenericAnimationTrack:
    translations = [] if obj.has_translation else None
    scales = [] if obj.has_scale else None
    raw_times = (
        [frame.raw_time for frame in obj.frames]
        if any(frame.raw_time is not None for frame in obj.frames)
        else None
    )
    times = []
    rotations = []
    for frame in obj.frames:
        times.append(frame.time)
        rotations.extend(frame.rotation)
        if translations is not None:
            translations.extend(frame.translation)
        if scales is not None:
            if frame.scale is None:
                raise ValueError(
                    f"Track {obj.name!r} declares scale but a keyframe has no scale"
                )
            scales.extend(frame.scale)

    resolved_bone_id = obj.resolved_bone_id
    if obj.bone_id != -1:
        bone_binding = "source_id"
    elif resolved_bone_id != -1:
        bone_binding = "sa_hanim_name"
    else:
        bone_binding = "unresolved"

    return GenericAnimationTrack(
        name=obj.name,
        bone_id=resolved_bone_id,
        source_bone_id=obj.bone_id,
        bone_binding=bone_binding,
        times=times,
        rotations=rotations,
        translations=translations,
        scales=scales,
        raw_times=raw_times,
        source_index=track_index,
        source_metadata={
            "source_frame_type": int(obj.frame_type),
            "source_keyframe_type": (
                obj.keyframe_type.value if obj.keyframe_type is not None else None
            ),
            "anpk_name_unknown": obj.anpk_name_unknown,
            "anpk_unknown": obj.anpk_unknown,
            "anpk_extra": obj.anpk_extra,
        },
    )


__all__ = ["iter_generic_animations", "to_generic_animation_set"]
