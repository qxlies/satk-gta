"""Chunked ANPK codec."""

from __future__ import annotations

import io
import math
import struct
from typing import Any

from .binary import (
    IFP_ANPK_MAGIC,
    decode_string,
    pack_preserved_c_string,
    pack_preserved_fixed_string,
    pack_section,
    read_any_section_full,
    read_exact,
    read_section_full,
)
from .models import (
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpVersion,
    _anpk_frame_size,
)


def parse_into(package: Any, stream: io.BytesIO) -> None:
    package.version = IfpVersion.ANPK
    magic = read_exact(stream, 4)
    assert magic == IFP_ANPK_MAGIC
    read_exact(stream, 4)

    info, package._raw_info_padding = read_section_full(stream, b"INFO")
    if len(info) < 5:
        raise ValueError("ANPK package INFO section is too small")
    animation_count = struct.unpack_from("<I", info)[0]
    package._raw_internal_name_data = info[4:]
    package.internal_name = decode_string(package._raw_internal_name_data)

    for _ in range(animation_count):
        name_data, name_padding = read_section_full(stream, b"NAME")
        dgan_data, dgan_padding = read_section_full(stream, b"DGAN")
        animation = IfpAnimation(
            name=decode_string(name_data),
            _raw_name_data=name_data,
            _raw_name_padding=name_padding,
            _raw_dgan_padding=dgan_padding,
        )
        dgan = io.BytesIO(dgan_data)
        animation_info, animation._raw_info_padding = read_section_full(
            dgan, b"INFO"
        )
        if len(animation_info) < 4:
            raise ValueError(
                f"ANPK animation INFO is too small for {animation.name!r}"
            )
        animation._raw_info_data = animation_info
        object_count = struct.unpack_from("<I", animation_info)[0]

        for _ in range(object_count):
            cpan_data, cpan_padding = read_section_full(dgan, b"CPAN")
            cpan = io.BytesIO(cpan_data)
            object_info, anim_padding = read_section_full(cpan, b"ANIM")
            if len(object_info) not in (40, 44, 48):
                raise ValueError(
                    f"Unsupported ANPK ANIM section size {len(object_info)} "
                    f"in {animation.name!r}"
                )

            name_unknown = struct.unpack_from("<i", object_info, 24)[0]
            frame_count = struct.unpack_from("<I", object_info, 28)[0]
            unknown = struct.unpack_from("<ii", object_info, 32)
            obj = IfpObject(
                name=decode_string(object_info[:24]),
                frame_type=IfpFrameType.CHILD_FLOAT,
                bone_id=-1,
                anpk_name_unknown=name_unknown,
                anpk_unknown=unknown,
                _raw_name_data=object_info[:24],
                _anpk_anim_size=len(object_info),
                _raw_cpan_padding=cpan_padding,
                _raw_anim_padding=anim_padding,
            )
            if len(object_info) >= 44:
                obj.bone_id = struct.unpack_from("<i", object_info, 40)[0]
            if len(object_info) == 48:
                obj.anpk_extra = struct.unpack_from("<i", object_info, 44)[0]

            if frame_count:
                key_magic, key_data, obj._raw_key_padding = read_any_section_full(cpan)
                obj._raw_key_data = key_data
                try:
                    obj.keyframe_type = IfpKeyframeType(key_magic.decode("ascii"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ValueError(
                        f"Unsupported ANPK keyframe section {key_magic!r} "
                        f"in {animation.name!r}/{obj.name!r}"
                    ) from exc
                obj.frame_type = (
                    IfpFrameType.ROOT_FLOAT
                    if obj.has_translation
                    else IfpFrameType.CHILD_FLOAT
                )
                obj.frames = _read_frames(
                    key_data,
                    frame_count,
                    obj.keyframe_type,
                    f"{animation.name}/{obj.name}",
                )
            else:
                obj.keyframe_type = IfpKeyframeType.NONE

            if cpan.tell() != len(cpan.getbuffer()):
                raise ValueError(
                    "Unexpected trailing data in ANPK CPAN for "
                    f"{animation.name!r}/{obj.name!r}"
                )
            animation.objects.append(obj)

        if dgan.tell() != len(dgan.getbuffer()):
            raise ValueError(
                f"Unexpected trailing data in ANPK DGAN for {animation.name!r}"
            )
        package.animations.append(animation)


def to_bytes(package: Any) -> bytes:
    content = bytearray()
    package_info = struct.pack("<I", len(package.animations))
    package_info += pack_preserved_c_string(
        package.internal_name,
        package._raw_internal_name_data,
    )
    content += pack_section(b"INFO", package_info, package._raw_info_padding)

    for animation in package.animations:
        content += pack_section(
            b"NAME",
            pack_preserved_c_string(animation.name, animation._raw_name_data),
            animation._raw_name_padding,
        )
        dgan = bytearray()
        info_tail = (
            animation._raw_info_data[4:]
            if animation._raw_info_data is not None
            else struct.pack("<I", 0)
        )
        dgan += pack_section(
            b"INFO",
            struct.pack("<I", len(animation.objects)) + info_tail,
            animation._raw_info_padding,
        )

        for obj in animation.objects:
            key_type = obj.keyframe_type or _type_for_object(obj)
            object_info = bytearray(pack_preserved_fixed_string(
                obj.name,
                24,
                obj._raw_name_data,
            ))
            object_info += struct.pack(
                "<iIii",
                obj.anpk_name_unknown,
                len(obj.frames),
                *obj.anpk_unknown,
            )
            anim_size = obj._anpk_anim_size or 44
            if anim_size >= 44:
                object_info += struct.pack("<i", obj.bone_id)
            if anim_size == 48:
                object_info += struct.pack("<i", obj.anpk_extra or 0)

            cpan = bytearray(pack_section(
                b"ANIM",
                bytes(object_info),
                obj._raw_anim_padding,
            ))
            if obj.frames:
                cpan += pack_section(
                    key_type.value.encode("ascii"),
                    _pack_preserved_frames(
                        obj.frames,
                        key_type,
                        obj._raw_key_data,
                    ),
                    obj._raw_key_padding,
                )
            dgan += pack_section(b"CPAN", bytes(cpan), obj._raw_cpan_padding)

        content += pack_section(
            b"DGAN",
            bytes(dgan),
            animation._raw_dgan_padding,
        )

    return IFP_ANPK_MAGIC + struct.pack("<I", len(content)) + bytes(content)


def _type_for_object(obj: IfpObject) -> IfpKeyframeType:
    if obj.has_scale or any(frame.scale is not None for frame in obj.frames):
        return IfpKeyframeType.ROTATION_TRANSLATION_SCALE
    if obj.has_translation:
        return IfpKeyframeType.ROTATION_TRANSLATION
    return IfpKeyframeType.ROTATION


def _read_frames(
    data: bytes,
    count: int,
    key_type: IfpKeyframeType,
    context: str,
) -> list[IfpFrame]:
    frame_size = _anpk_frame_size(key_type)
    expected = count * frame_size
    if len(data) != expected:
        raise ValueError(
            f"ANPK keyframe data size mismatch in {context!r}: "
            f"expected {expected}, got {len(data)}"
        )

    frames = []
    offset = 0
    for _ in range(count):
        qx, qy, qz, qw = struct.unpack_from("<4f", data, offset)
        offset += 16
        translation = (0.0, 0.0, 0.0)
        scale = None
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            translation = struct.unpack_from("<3f", data, offset)
            offset += 12
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            scale = struct.unpack_from("<3f", data, offset)
            offset += 12
        time = struct.unpack_from("<f", data, offset)[0]
        offset += 4
        frames.append(IfpFrame(
            rotation=(-qx, -qy, -qz, qw),
            translation=translation,
            scale=scale,
            time=time,
        ))
    return frames


def _pack_frames(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
) -> bytes:
    if key_type == IfpKeyframeType.NONE:
        raise ValueError("ANPK K000 tracks cannot contain frames")
    data = bytearray()
    for frame in frames:
        qx, qy, qz, qw = frame.rotation
        data += struct.pack("<4f", -qx, -qy, -qz, qw)
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            data += struct.pack("<3f", *frame.translation)
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            data += struct.pack("<3f", *(frame.scale or (1.0, 1.0, 1.0)))
        data += struct.pack("<f", frame.time)
    return bytes(data)


def _pack_preserved_frames(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
    raw: bytes | None,
) -> bytes:
    if raw is not None and _frames_match_raw(frames, key_type, raw):
        return raw
    return _pack_frames(frames, key_type)


def _frames_match_raw(
    frames: list[IfpFrame],
    key_type: IfpKeyframeType,
    raw: bytes,
) -> bool:
    if len(raw) != len(frames) * _anpk_frame_size(key_type):
        return False
    offset = 0
    for frame in frames:
        qx, qy, qz, qw = struct.unpack_from("<4f", raw, offset)
        offset += 16
        if not _same_vector(frame.rotation, (-qx, -qy, -qz, qw)):
            return False
        if key_type in (
            IfpKeyframeType.ROTATION_TRANSLATION,
            IfpKeyframeType.ROTATION_TRANSLATION_SCALE,
        ):
            translation = struct.unpack_from("<3f", raw, offset)
            offset += 12
            if not _same_vector(frame.translation, translation):
                return False
        if key_type == IfpKeyframeType.ROTATION_TRANSLATION_SCALE:
            scale = struct.unpack_from("<3f", raw, offset)
            offset += 12
            if frame.scale is None or not _same_vector(frame.scale, scale):
                return False
        time = struct.unpack_from("<f", raw, offset)[0]
        offset += 4
        if not _same_float(frame.time, time):
            return False
    return True


def _same_float(left: float, right: float) -> bool:
    return left == right or (math.isnan(left) and math.isnan(right))


def _same_vector(left, right) -> bool:
    return all(_same_float(a, b) for a, b in zip(left, right))


__all__ = ["parse_into", "to_bytes"]
