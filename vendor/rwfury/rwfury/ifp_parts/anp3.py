"""ANP3 codec."""

from __future__ import annotations

import io
import math
import struct
from typing import Any

from .binary import (
    IFP_ANP3_MAGIC,
    IFP_V2_NAME_SIZE,
    IFP_V2_QUAT_SCALE,
    IFP_V2_TIME_SCALE,
    IFP_V2_TRANS_SCALE,
    pack_preserved_fixed_string,
    read_exact,
    read_fixed_string_raw,
    to_i16,
    to_i32,
    to_u32,
)
from .models import (
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpObject,
    IfpVersion,
    _anp3_frame_size,
)


def parse_into(package: Any, stream: io.BytesIO) -> None:
    package.version = IfpVersion.ANP3
    magic = read_exact(stream, 4)
    assert magic == IFP_ANP3_MAGIC
    struct.unpack("<I", read_exact(stream, 4))[0]
    package.internal_name, package._raw_internal_name_data = read_fixed_string_raw(
        stream, IFP_V2_NAME_SIZE
    )
    animation_count = struct.unpack("<I", read_exact(stream, 4))[0]

    for _ in range(animation_count):
        animation_name, animation_name_raw = read_fixed_string_raw(
            stream, IFP_V2_NAME_SIZE
        )
        animation = IfpAnimation(
            name=animation_name,
            _raw_name_data=animation_name_raw,
        )
        object_count, frame_data_size, animation.unknown = struct.unpack(
            "<III", read_exact(stream, 12)
        )
        animation.declared_frame_data_size = frame_data_size

        for _ in range(object_count):
            object_name, object_name_raw = read_fixed_string_raw(
                stream, IFP_V2_NAME_SIZE
            )
            obj = IfpObject(name=object_name, _raw_name_data=object_name_raw)
            frame_type, frame_count, bone_id = struct.unpack(
                "<III", read_exact(stream, 12)
            )
            obj.frame_type = frame_type
            obj.bone_id = to_i32(bone_id)
            obj.frames = [
                _read_frame(stream, frame_type)
                for _ in range(frame_count)
            ]
            animation.objects.append(obj)

        if animation.frame_data_size != animation.declared_frame_data_size:
            raise ValueError(
                f"ANP3 frame data size mismatch in {animation.name!r}: "
                f"declared {animation.declared_frame_data_size}, "
                f"computed {animation.frame_data_size}"
            )
        package.animations.append(animation)


def to_bytes(package: Any) -> bytes:
    body = bytearray()
    body += pack_preserved_fixed_string(
        package.internal_name,
        IFP_V2_NAME_SIZE,
        package._raw_internal_name_data,
    )
    body += struct.pack("<I", len(package.animations))

    for animation in package.animations:
        body += pack_preserved_fixed_string(
            animation.name,
            IFP_V2_NAME_SIZE,
            animation._raw_name_data,
        )
        body += struct.pack(
            "<III",
            len(animation.objects),
            animation.frame_data_size,
            animation.unknown,
        )
        for obj in animation.objects:
            body += pack_preserved_fixed_string(
                obj.name,
                IFP_V2_NAME_SIZE,
                obj._raw_name_data,
            )
            body += struct.pack(
                "<III",
                int(obj.frame_type),
                len(obj.frames),
                to_u32(obj.bone_id),
            )
            for frame in obj.frames:
                body += _pack_frame(frame, int(obj.frame_type))

    file_size = 8 + len(body)
    return IFP_ANP3_MAGIC + struct.pack("<I", file_size - 8) + bytes(body)


def _read_frame(stream: io.BytesIO, frame_type: int) -> IfpFrame:
    if frame_type == int(IfpFrameType.CHILD_FLOAT):
        qx, qy, qz, qw, time = struct.unpack("<5f", read_exact(stream, 20))
        return IfpFrame(rotation=(qx, qy, qz, qw), time=time)

    if frame_type == int(IfpFrameType.ROOT_FLOAT):
        qx, qy, qz, qw, time, tx, ty, tz = struct.unpack(
            "<8f", read_exact(stream, 32)
        )
        return IfpFrame(
            rotation=(qx, qy, qz, qw),
            time=time,
            translation=(tx, ty, tz),
        )

    if frame_type == int(IfpFrameType.ROOT):
        qx, qy, qz, qw, raw_time, tx, ty, tz = struct.unpack(
            "<8h", read_exact(stream, 16)
        )
        return IfpFrame(
            rotation=tuple(
                value / IFP_V2_QUAT_SCALE
                for value in (qx, qy, qz, qw)
            ),
            time=raw_time / IFP_V2_TIME_SCALE,
            translation=tuple(
                value / IFP_V2_TRANS_SCALE
                for value in (tx, ty, tz)
            ),
            raw_time=raw_time,
        )

    if frame_type == int(IfpFrameType.CHILD):
        qx, qy, qz, qw, raw_time = struct.unpack(
            "<5h", read_exact(stream, 10)
        )
        return IfpFrame(
            rotation=tuple(
                value / IFP_V2_QUAT_SCALE
                for value in (qx, qy, qz, qw)
            ),
            time=raw_time / IFP_V2_TIME_SCALE,
            raw_time=raw_time,
        )

    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


def _pack_frame(frame: IfpFrame, frame_type: int) -> bytes:
    if frame.scale is not None:
        raise ValueError("ANP3 frames cannot store scale")

    if frame_type == int(IfpFrameType.CHILD_FLOAT):
        return struct.pack("<5f", *frame.rotation, frame.time)

    if frame_type == int(IfpFrameType.ROOT_FLOAT):
        return struct.pack(
            "<8f",
            *frame.rotation,
            frame.time,
            *frame.translation,
        )

    qx, qy, qz, qw = (
        to_i16(component * IFP_V2_QUAT_SCALE)
        for component in frame.rotation
    )
    if (
        frame.raw_time is not None
        and math.isclose(
            frame.time,
            frame.raw_time / IFP_V2_TIME_SCALE,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raw_time = to_i16(frame.raw_time)
    else:
        raw_time = to_i16(round(frame.time * IFP_V2_TIME_SCALE))

    if frame_type == int(IfpFrameType.ROOT):
        tx, ty, tz = (
            to_i16(component * IFP_V2_TRANS_SCALE)
            for component in frame.translation
        )
        return struct.pack("<8h", qx, qy, qz, qw, raw_time, tx, ty, tz)

    if frame_type == int(IfpFrameType.CHILD):
        return struct.pack("<5h", qx, qy, qz, qw, raw_time)

    raise ValueError(f"Unsupported ANP3 frame type: {frame_type}")


__all__ = ["parse_into", "to_bytes"]
