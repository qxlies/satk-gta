"""Semantic validation for parsed or constructed IFP packages."""

from __future__ import annotations

import math
from typing import Any

from .models import (
    IfpKeyframeType,
    IfpValidationIssue,
    IfpVersion,
)


def validate(package: Any) -> list[IfpValidationIssue]:
    issues: list[IfpValidationIssue] = []
    animation_names: set[str] = set()

    for animation in package.animations:
        animation_key = animation.name.casefold()
        if animation_key in animation_names:
            issues.append(IfpValidationIssue(
                "duplicate_animation_name",
                f"Duplicate animation name {animation.name!r}",
                animation_name=animation.name,
            ))
        animation_names.add(animation_key)

        if (
            animation.declared_frame_data_size is not None
            and animation.declared_frame_data_size != animation.frame_data_size
        ):
            issues.append(IfpValidationIssue(
                "frame_data_size_mismatch",
                f"Animation {animation.name!r} declares "
                f"{animation.declared_frame_data_size} frame bytes but contains "
                f"{animation.frame_data_size}",
                animation_name=animation.name,
            ))

        object_names: set[str] = set()
        for obj in animation.objects:
            object_key = obj.name.casefold()
            if object_key in object_names:
                issues.append(IfpValidationIssue(
                    "duplicate_object_name",
                    f"Duplicate object name {obj.name!r} in {animation.name!r}",
                    animation_name=animation.name,
                    object_name=obj.name,
                ))
            object_names.add(object_key)

            if obj.keyframe_type == IfpKeyframeType.NONE and obj.frames:
                issues.append(IfpValidationIssue(
                    "frames_without_keyframe_type",
                    f"Object {animation.name!r}/{obj.name!r} has frames but uses K000",
                    animation_name=animation.name,
                    object_name=obj.name,
                ))

            previous_time = -math.inf
            for index, frame in enumerate(obj.frames):
                context = f"{animation.name!r}/{obj.name!r} frame {index}"
                values = (*frame.rotation, *frame.translation)
                if frame.scale is not None:
                    values += frame.scale
                if not math.isfinite(frame.time) or not all(map(math.isfinite, values)):
                    issues.append(IfpValidationIssue(
                        "non_finite_frame",
                        f"Non-finite value in {context}",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))
                if frame.time < previous_time:
                    issues.append(IfpValidationIssue(
                        "non_monotonic_time",
                        f"Frame times are not monotonic in {animation.name!r}/{obj.name!r}",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))
                    break
                previous_time = frame.time
                if sum(component * component for component in frame.rotation) == 0.0:
                    issues.append(IfpValidationIssue(
                        "zero_quaternion",
                        f"Zero-length quaternion in {context}",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))
                if int(package.version) == int(IfpVersion.ANP3) and frame.scale is not None:
                    issues.append(IfpValidationIssue(
                        "unsupported_anp3_scale",
                        f"ANP3 cannot store scale in {context}",
                        animation_name=animation.name,
                        object_name=obj.name,
                    ))

    return issues


__all__ = ["validate"]
