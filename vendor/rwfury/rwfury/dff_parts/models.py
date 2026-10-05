"""DFF data models and small helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from math import acos, cos, degrees, radians


def rw_version_unpack(version: int) -> tuple[int, int, int, int]:
    """Decode a RW chunk version field into (major, minor, revision, build).

    For library-stamped versions (post-3.1):
      version = 0x1803FFFF -> RW 3.6.0.3, build 0xFFFF
    For old pre-3.1 versions the raw value is the version directly.
    """
    if version & 0xFFFF0000:
        packed = (version >> 16) & 0xFFFF
        major = ((packed >> 14) & 0x03) + 3
        minor = (packed >> 10) & 0x0F
        revision = (packed >> 2) & 0xFF
        sub = packed & 0x03
        build = version & 0xFFFF
        return (major, minor, revision, sub)

    return (3, version >> 8, version & 0xFF, 0)


def rw_version_raw(version: int) -> int:
    """Convert a chunk version to a comparable raw version number."""
    major, minor, rev, sub = rw_version_unpack(version)
    return (major << 16) | (minor << 12) | (rev << 4) | sub


@dataclass
class Mesh:
    """High-level mesh data for a single material split."""
    positions: list[tuple[float, float, float]]
    indices: list[int]
    texcoords: list[list[tuple[float, float]]]
    normals: list[tuple[float, float, float]] | None = None
    vertex_colors: list[tuple[int, int, int, int]] | None = None


@dataclass
class DffMaterial:
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    texture_name: str = ""
    mask_name: str = ""
    ambient: float = 1.0
    diffuse: float = 1.0
    specular: float = 0.0
    mat_fx_type: int = 0
    mat_fx_data: dict | None = None
    specular_level: float = 0.0
    specular_texture: str = ""
    reflection: dict | None = None
    uv_animations: list[DffUvAnimationRef] = field(default_factory=list)

    @property
    def uv_animation_names(self) -> list[str]:
        return [ref.name for ref in self.uv_animations]


@dataclass
class DffUvAnimationRef:
    channel: int = 0
    name: str = ""


@dataclass
class DffUvAnimationFrame:
    time: float = 0.0
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    previous_frame: int = -1


@dataclass
class DffUvAnimation:
    version: int = 0x100
    animation_type: int = 0x1C1
    declared_frame_count: int = 0
    flags: int = 0
    duration: float = 0.0
    unknown: int = 0
    name: str = ""
    node_to_uv: tuple[float, float, float, float, float, float, float, float] = (
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    )
    frames: list[DffUvAnimationFrame] = field(default_factory=list)
    raw_data: bytes = b""

    @property
    def frame_count(self) -> int:
        return self.declared_frame_count or len(self.frames)


@dataclass
class BinMeshSplit:
    material_index: int = 0
    indices: list[int] = field(default_factory=list)


@dataclass
class BinMeshPLG:
    flags: int = 0  # 0 = tri list, 1 = tri strip
    splits: list[BinMeshSplit] = field(default_factory=list)


@dataclass
class SkinPLG:
    num_bones: int = 0
    used_bone_indices: list[int] = field(default_factory=list)
    bone_indices: list[tuple[int, int, int, int]] = field(default_factory=list)
    weights: list[tuple[float, float, float, float]] = field(default_factory=list)
    inverse_matrices: list[tuple] = field(default_factory=list)


@dataclass
class HAnimBone:
    node_id: int = 0
    node_index: int = 0
    flags: int = 0


@dataclass
class HAnimPLG:
    version: int = 0x100
    node_id: int = 0
    hierarchy_flags: int = 0
    key_frame_size: int = 36
    bones: list[HAnimBone] = field(default_factory=list)


@dataclass
class Effect2dfxLight:
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    corona_far_clip: float = 0.0
    pointlight_range: float = 0.0
    corona_size: float = 0.0
    shadow_size: float = 0.0
    corona_show_mode: int = 0
    corona_enable_reflection: int = 0
    corona_flare_type: int = 0
    shadow_color_multiplier: int = 0
    flags1: int = 0
    corona_tex_name: str = ""
    shadow_tex_name: str = ""
    shadow_z_distance: int = 0
    flags2: int = 0
    look_direction: tuple[int, int, int] | None = None


@dataclass
class Effect2dfxParticle:
    effect_name: str = ""


@dataclass
class Effect2dfxEntry:
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    entry_type: int = 0
    data: object = None


@dataclass
class MorphTarget:
    bounding_sphere: tuple[float, float, float, float] = (0, 0, 0, 0)
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)


@dataclass
class CollisionData:
    raw: bytes = b""

    def parse(self):
        """Parse the raw collision bytes into a ColModel."""
        if not self.raw:
            return None
        from ..col import Col
        col = Col.from_bytes(self.raw)
        return col.models[0] if col.models else None


@dataclass
class DffGeometry:
    flags: int = 0
    num_uv_sets: int = 0
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int, int]] = field(default_factory=list)
    texcoord_sets: list[list[tuple[float, float]]] = field(default_factory=list)
    vertex_colors: list[tuple[int, int, int, int]] = field(default_factory=list)
    materials: list[DffMaterial] = field(default_factory=list)
    bounding_sphere: tuple[float, float, float, float] = (0, 0, 0, 0)
    morph_targets: list[MorphTarget] = field(default_factory=list)
    bin_mesh: BinMeshPLG | None = None
    skin: SkinPLG | None = None
    night_colors: list[tuple[int, int, int, int]] | None = None
    effects_2dfx: list[Effect2dfxEntry] | None = None


@dataclass
class DffFrame:
    name: str = ""
    rotation_matrix: tuple = (1, 0, 0, 0, 1, 0, 0, 0, 1)
    position: tuple[float, float, float] = (0, 0, 0)
    parent: int = -1
    hanim: HAnimPLG | None = None


@dataclass
class DffAtomic:
    frame_index: int = 0
    geometry_index: int = 0
    flags: int = 0


class DffLightFlags(IntFlag):
    """RenderWare light scope flags."""
    SCENE = 0x01
    WORLD = 0x02


class DffLightType(IntEnum):
    """RenderWare light source types."""
    DIRECTIONAL = 0x01
    AMBIENT = 0x02
    POINT = 0x80
    SPOT = 0x81
    SPOT_SOFT = 0x82


@dataclass
class DffLight:
    """RenderWare real-time light attached to a frame."""
    frame_index: int = 0
    radius: float = 0.0
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    cone_angle: float = 0.0
    flags: int | DffLightFlags = 0
    light_type: int | DffLightType = 0
    extension_data: bytes = b""
    extra_chunks: list[tuple[int, bytes]] = field(default_factory=list)

    @property
    def flags_enum(self) -> DffLightFlags:
        return DffLightFlags(self.flags)

    @property
    def type_enum(self) -> DffLightType | None:
        try:
            return DffLightType(self.light_type)
        except ValueError:
            return None

    @property
    def type_name(self) -> str:
        light_type = self.type_enum
        return light_type.name if light_type else f"UNKNOWN_{int(self.light_type):#x}"

    @property
    def affects_scene(self) -> bool:
        return bool(self.flags_enum & DffLightFlags.SCENE)

    @property
    def affects_world(self) -> bool:
        return bool(self.flags_enum & DffLightFlags.WORLD)

    @property
    def spot_angle_radians(self) -> float:
        return acos(max(-1.0, min(1.0, 1.0 - self.cone_angle)))

    @property
    def spot_angle_degrees(self) -> float:
        return degrees(self.spot_angle_radians)

    def set_spot_angle_radians(self, value: float):
        self.cone_angle = 1.0 - cos(value)

    def set_spot_angle_degrees(self, value: float):
        self.set_spot_angle_radians(radians(value))

    @classmethod
    def ambient(
        cls,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return cls(
            frame_index=frame_index,
            color=color,
            flags=flags,
            light_type=DffLightType.AMBIENT,
        )

    @classmethod
    def directional(
        cls,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return cls(
            frame_index=frame_index,
            color=color,
            flags=flags,
            light_type=DffLightType.DIRECTIONAL,
        )

    @classmethod
    def point(
        cls,
        radius: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        return cls(
            frame_index=frame_index,
            radius=radius,
            color=color,
            flags=flags,
            light_type=DffLightType.POINT,
        )

    @classmethod
    def spot(
        cls,
        radius: float,
        angle_degrees: float,
        color: tuple[float, float, float] = (1.0, 1.0, 1.0),
        frame_index: int = 0,
        soft: bool = False,
        flags: int | DffLightFlags = DffLightFlags.SCENE,
    ) -> DffLight:
        light = cls(
            frame_index=frame_index,
            radius=radius,
            color=color,
            flags=flags,
            light_type=DffLightType.SPOT_SOFT if soft else DffLightType.SPOT,
        )
        light.set_spot_angle_degrees(angle_degrees)
        return light
