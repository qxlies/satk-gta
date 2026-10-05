"""Package-level inspection, sampling, and export API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import generic_export
from .models import IfpAnimation, IfpFrame, IfpObject, IfpOutOfRange

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..generic_animation import GenericAnimation, GenericAnimationSet


class IfpPackageApi:
    animations: list[IfpAnimation]

    def get_animation(self, name: str) -> IfpAnimation | None:
        name_lower = name.lower()
        for animation in self.animations:
            if animation.name.lower() == name_lower:
                return animation
        return None

    def get_animation_names(self) -> list[str]:
        return [animation.name for animation in self.animations]

    def iter_objects(self) -> Iterator[tuple[IfpAnimation, IfpObject]]:
        for animation in self.animations:
            for obj in animation.objects:
                yield animation, obj

    def get_objects(self, animation_name: str | None = None) -> list[dict]:
        if animation_name is None:
            return [
                self._object_data(animation, obj)
                for animation, obj in self.iter_objects()
            ]

        animation = self.get_animation(animation_name)
        if animation is None:
            return []
        return [self._object_data(animation, obj) for obj in animation.objects]

    def get_object(
        self,
        animation_name: str,
        object_name: str,
    ) -> IfpObject | None:
        animation = self.get_animation(animation_name)
        if animation is None:
            return None
        return animation.get_object(object_name)

    def get_animation_duration(self, name: str) -> float | None:
        animation = self.get_animation(name)
        return animation.duration if animation is not None else None

    def sample_animation(
        self,
        name: str,
        time: float,
        *,
        out_of_range: IfpOutOfRange = "clamp",
    ) -> list[tuple[IfpObject, IfpFrame | None]]:
        animation = self.get_animation(name)
        if animation is None:
            raise KeyError(f"Animation not found: {name}")
        return animation.sample(time, out_of_range=out_of_range)

    def iter_generic_animations(self) -> Iterator[GenericAnimation]:
        """Yield format-neutral clips without converting the whole package."""
        return generic_export.iter_generic_animations(self)

    def to_generic_animations(self) -> list[GenericAnimation]:
        """Return every clip as an independent format-neutral object."""
        return list(self.iter_generic_animations())

    def get_generic_animation(self, name: str) -> GenericAnimation | None:
        """Return the first case-insensitive generic clip match."""
        name_lower = name.lower()
        for animation in self.iter_generic_animations():
            if animation.name.lower() == name_lower:
                return animation
        return None

    def to_generic_animation_set(self) -> GenericAnimationSet:
        """Convert the package to a self-contained in-memory animation set."""
        return generic_export.to_generic_animation_set(self)

    def to_animation_data(self) -> dict:
        animations = []
        object_count = 0
        frame_count = 0

        for animation in self.animations:
            objects = []
            for obj in animation.objects:
                object_count += 1
                frame_count += len(obj.frames)
                objects.append({
                    "name": obj.name,
                    "frame_type": int(obj.frame_type),
                    "bone_id": obj.bone_id,
                    "frame_count": len(obj.frames),
                    "has_translation": obj.has_translation,
                    "has_scale": obj.has_scale,
                    "keyframe_type": (
                        obj.keyframe_type.value if obj.keyframe_type else None
                    ),
                    "anpk_name_unknown": obj.anpk_name_unknown,
                    "anpk_unknown": list(obj.anpk_unknown),
                    "anpk_extra": obj.anpk_extra,
                    "frames": [
                        {
                            "time": frame.time,
                            "raw_time": frame.raw_time,
                            "rotation": list(frame.rotation),
                            "translation": list(frame.translation),
                            "scale": (
                                list(frame.scale)
                                if frame.scale is not None
                                else None
                            ),
                        }
                        for frame in obj.frames
                    ],
                })

            animations.append({
                "name": animation.name,
                "object_count": len(animation.objects),
                "frame_data_size": animation.frame_data_size,
                "unknown": animation.unknown,
                "objects": objects,
            })

        return {
            "version": int(self.version),
            "internal_name": self.internal_name,
            "animation_count": len(self.animations),
            "object_count": object_count,
            "frame_count": frame_count,
            "animations": animations,
        }

    def to_animation_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_animation_data(), indent=indent)

    def export_animations(self, path: str) -> dict:
        data = self.to_animation_data()
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        return data

    @staticmethod
    def _object_data(animation: IfpAnimation, obj: IfpObject) -> dict:
        return {
            "animation": animation,
            "animation_name": animation.name,
            "object": obj,
            "object_name": obj.name,
            "frame_type": int(obj.frame_type),
            "frame_count": len(obj.frames),
            "bone_id": obj.bone_id,
            "has_translation": obj.has_translation,
            "has_scale": obj.has_scale,
            "keyframe_type": obj.keyframe_type.value if obj.keyframe_type else None,
        }


__all__ = ["IfpPackageApi"]
