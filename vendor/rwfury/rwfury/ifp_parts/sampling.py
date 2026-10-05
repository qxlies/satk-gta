"""Animation sampling and transform helpers."""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import IfpAnimation, IfpFrame, IfpObject, IfpOutOfRange


def interpolate_frame(
    frame: IfpFrame,
    other: IfpFrame,
    factor: float,
) -> IfpFrame:
    if not math.isfinite(factor) or not 0.0 <= factor <= 1.0:
        raise ValueError("Interpolation factor must be finite and between 0 and 1")
    scale = None
    if frame.scale is not None or other.scale is not None:
        scale = _lerp_vector(
            frame.scale or (1.0, 1.0, 1.0),
            other.scale or (1.0, 1.0, 1.0),
            factor,
        )
    return type(frame)(
        rotation=_slerp(frame.rotation, other.rotation, factor),
        translation=_lerp_vector(frame.translation, other.translation, factor),
        scale=scale,
        time=frame.time + (other.time - frame.time) * factor,
        raw_time=None,
    )


def frame_to_matrix(frame: IfpFrame) -> tuple[float, ...]:
    x, y, z, w = _normalize_quaternion(frame.rotation)
    sx, sy, sz = frame.scale or (1.0, 1.0, 1.0)
    tx, ty, tz = frame.translation
    return (
        (1.0 - 2.0 * (y * y + z * z)) * sx,
        (2.0 * (x * y - z * w)) * sy,
        (2.0 * (x * z + y * w)) * sz,
        tx,
        (2.0 * (x * y + z * w)) * sx,
        (1.0 - 2.0 * (x * x + z * z)) * sy,
        (2.0 * (y * z - x * w)) * sz,
        ty,
        (2.0 * (x * z - y * w)) * sx,
        (2.0 * (y * z + x * w)) * sy,
        (1.0 - 2.0 * (x * x + y * y)) * sz,
        tz,
        0.0,
        0.0,
        0.0,
        1.0,
    )


def object_duration(obj: IfpObject) -> float:
    if not obj.frames:
        return 0.0
    times = [frame.time for frame in obj.frames]
    if not all(map(math.isfinite, times)):
        raise ValueError(f"Object {obj.name!r} contains a non-finite frame time")
    return max(0.0, max(times))


def sample_object(
    obj: IfpObject,
    time: float,
    out_of_range: IfpOutOfRange,
) -> IfpFrame | None:
    if not obj.frames:
        return None
    sample_time = _resolve_sample_time(time, object_duration(obj), out_of_range)
    frames = _ordered_unique_frames(obj.frames)
    times = [frame.time for frame in frames]
    index = bisect_right(times, sample_time)
    if index == 0:
        return _held_frame(frames[0], sample_time)
    if index == len(frames):
        return _held_frame(frames[-1], sample_time)
    left = frames[index - 1]
    right = frames[index]
    if sample_time == left.time:
        return _held_frame(left, sample_time)
    factor = (sample_time - left.time) / (right.time - left.time)
    sampled = interpolate_frame(left, right, factor)
    sampled.time = sample_time
    return sampled


def animation_duration(animation: IfpAnimation) -> float:
    return max((object_duration(obj) for obj in animation.objects), default=0.0)


def sample_animation(
    animation: IfpAnimation,
    time: float,
    out_of_range: IfpOutOfRange,
) -> list[tuple[IfpObject, IfpFrame | None]]:
    sample_time = _resolve_sample_time(
        time,
        animation_duration(animation),
        out_of_range,
    )
    return [
        (obj, sample_object(obj, sample_time, "clamp"))
        for obj in animation.objects
    ]


def _resolve_sample_time(
    time: float,
    duration: float,
    out_of_range: IfpOutOfRange,
) -> float:
    if out_of_range not in ("clamp", "error"):
        raise ValueError("out_of_range must be 'clamp' or 'error'")
    if not math.isfinite(time):
        raise ValueError("Sample time must be finite")
    if out_of_range == "error" and not 0.0 <= time <= duration:
        raise ValueError(f"Sample time {time} is outside 0..{duration}")
    return min(duration, max(0.0, time))


def _ordered_unique_frames(frames: list[IfpFrame]) -> list[IfpFrame]:
    ordered = sorted(enumerate(frames), key=lambda item: (item[1].time, item[0]))
    unique: list[IfpFrame] = []
    for _index, frame in ordered:
        if unique and frame.time == unique[-1].time:
            unique[-1] = frame
        else:
            unique.append(frame)
    return unique


def _held_frame(frame: IfpFrame, time: float) -> IfpFrame:
    return type(frame)(
        rotation=frame.rotation,
        translation=frame.translation,
        scale=frame.scale,
        time=time,
        raw_time=None,
    )


def _lerp_vector(left, right, factor: float):
    return tuple(a + (b - a) * factor for a, b in zip(left, right))


def _normalize_quaternion(rotation) -> tuple[float, float, float, float]:
    if len(rotation) != 4 or not all(map(math.isfinite, rotation)):
        raise ValueError("Quaternion components must be finite")
    length_squared = sum(component * component for component in rotation)
    if length_squared == 0.0:
        raise ValueError("Cannot normalize a zero-length quaternion")
    inverse_length = 1.0 / math.sqrt(length_squared)
    return tuple(component * inverse_length for component in rotation)


def _slerp(left, right, factor: float) -> tuple[float, float, float, float]:
    first = _normalize_quaternion(left)
    second = _normalize_quaternion(right)
    dot = sum(a * b for a, b in zip(first, second))
    if dot < 0.0:
        second = tuple(-component for component in second)
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return _normalize_quaternion(_lerp_vector(first, second, factor))
    angle = math.acos(dot)
    sine = math.sin(angle)
    left_weight = math.sin((1.0 - factor) * angle) / sine
    right_weight = math.sin(factor * angle) / sine
    return tuple(
        a * left_weight + b * right_weight
        for a, b in zip(first, second)
    )
