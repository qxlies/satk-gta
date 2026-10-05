"""rwfury - Python API for GTA RenderWare DFF and TXD files."""

from .dff import Dff
from .dff_parts.models import (
    BinMeshPLG, BinMeshSplit, CollisionData,
    DffAtomic, DffFrame, DffGeometry, DffLight,
    DffLightFlags, DffLightType, DffMaterial,
    DffUvAnimation, DffUvAnimationFrame, DffUvAnimationRef,
    Mesh, MorphTarget,
    SkinPLG, HAnimPLG, HAnimBone,
    Effect2dfxEntry, Effect2dfxLight, Effect2dfxParticle,
)
from .txd import Txd, TxdTexture
from .img import Img, ImgEntry
from .ifp import (
    Ifp, IfpAnimation, IfpObject, IfpFrame,
    IfpVersion, IfpFrameType, IfpKeyframeType,
    IfpValidationIssue, IfpValidationError,
    IfpOutOfRange,
)
from .col import Col, ColModel, ColSphere, ColBox, ColFace, ColBounds, ColSurface, ColFaceGroup
from .col_materials import ColMaterial
from .paths import (
    SaPathFile, SaPaths, PathNode, NaviNode, PathLink, NaviLink, PathLinkRecord,
    PathFileFormat, PathNodeKind, PathTrafficLevel, PathNodeFlag, PathIntersectionFlag,
)
from .generic_mesh import GenericMesh
from .generic_animation import (
    GenericAnimation, GenericAnimationBuffers, GenericAnimationKeyframe,
    GenericAnimationSet, GenericAnimationTrack,
)
from .sa_bones import (
    SA_BONE_NAMES, SaBoneTag, sa_bone_name_from_tag, sa_bone_tag_from_name,
)
from .rwbinary import ChunkHeader, RwBinaryReader, RwBinaryWriter

__all__ = [
    "Dff", "Mesh",
    "DffGeometry", "DffMaterial", "DffFrame", "DffAtomic",
    "DffLight", "DffLightFlags", "DffLightType",
    "DffUvAnimation", "DffUvAnimationFrame", "DffUvAnimationRef",
    "MorphTarget", "CollisionData",
    "BinMeshPLG", "BinMeshSplit",
    "SkinPLG", "HAnimPLG", "HAnimBone",
    "Effect2dfxEntry", "Effect2dfxLight", "Effect2dfxParticle",
    "Txd", "TxdTexture",
    "Img", "ImgEntry",
    "Ifp", "IfpAnimation", "IfpObject", "IfpFrame",
    "IfpVersion", "IfpFrameType", "IfpKeyframeType",
    "IfpValidationIssue", "IfpValidationError",
    "IfpOutOfRange",
    "Col", "ColModel", "ColSphere", "ColBox", "ColFace",
    "ColBounds", "ColSurface", "ColFaceGroup", "ColMaterial",
    "SaPathFile", "SaPaths", "PathNode", "NaviNode", "PathLink", "NaviLink",
    "PathLinkRecord", "PathFileFormat", "PathNodeKind", "PathTrafficLevel", "PathNodeFlag",
    "PathIntersectionFlag",
    "GenericMesh",
    "GenericAnimation", "GenericAnimationBuffers", "GenericAnimationKeyframe",
    "GenericAnimationSet", "GenericAnimationTrack",
    "SA_BONE_NAMES", "SaBoneTag", "sa_bone_name_from_tag",
    "sa_bone_tag_from_name",
    "ChunkHeader", "RwBinaryReader", "RwBinaryWriter",
]
