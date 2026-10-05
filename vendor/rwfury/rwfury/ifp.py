"""GTA IFP animation package facade."""

from __future__ import annotations

import io

from .ifp_parts import anp3, anpk
from .ifp_parts.api import IfpPackageApi
from .ifp_parts.binary import (
    IFP_ANP3_MAGIC,
    IFP_ANPK_MAGIC,
    IFP_V2_NAME_SIZE,
    IFP_V2_QUAT_SCALE,
    IFP_V2_TIME_SCALE,
    IFP_V2_TRANS_SCALE,
    slice_ifp_data,
)
from .ifp_parts.models import (
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpOutOfRange,
    IfpValidationError,
    IfpValidationIssue,
    IfpVersion,
)
from .ifp_parts.validation import validate


class Ifp(IfpPackageApi):
    """GTA San Andreas ANP3 or chunked ANPK animation package."""

    def __init__(self):
        self.version: int | IfpVersion = IfpVersion.ANP3
        self.internal_name: str = ""
        self.animations: list[IfpAnimation] = []
        self._raw_internal_name_data: bytes | None = None
        self._raw_info_padding: bytes | None = None

    @classmethod
    def from_file(cls, path: str) -> Ifp:
        with open(path, "rb") as file:
            return cls.from_bytes(file.read())

    @classmethod
    def from_bytes(cls, data: bytes) -> Ifp:
        package = cls()
        if data.startswith(IFP_ANP3_MAGIC):
            stream = io.BytesIO(slice_ifp_data(data))
            anp3.parse_into(package, stream)
            return package
        if data.startswith(IFP_ANPK_MAGIC):
            stream = io.BytesIO(slice_ifp_data(data))
            anpk.parse_into(package, stream)
            return package
        raise ValueError("Not an IFP: expected ANP3 or ANPK header")

    def to_file(self, path: str):
        with open(path, "wb") as file:
            file.write(self.to_bytes())

    def to_bytes(self) -> bytes:
        if int(self.version) == int(IfpVersion.ANPK):
            return anpk.to_bytes(self)
        if int(self.version) == int(IfpVersion.ANP3):
            return anp3.to_bytes(self)
        raise NotImplementedError(
            f"Writing IFP version {self.version!r} is not supported"
        )

    def validate(self) -> list[IfpValidationIssue]:
        return validate(self)

    def validate_or_raise(self):
        issues = self.validate()
        if issues:
            raise IfpValidationError(issues)


# Keep public type identities and old pickle import paths stable after the split.
for _public_type in (
    IfpAnimation,
    IfpFrame,
    IfpFrameType,
    IfpKeyframeType,
    IfpObject,
    IfpValidationError,
    IfpValidationIssue,
    IfpVersion,
):
    _public_type.__module__ = __name__
del _public_type


__all__ = [
    "IFP_ANP3_MAGIC",
    "IFP_ANPK_MAGIC",
    "IFP_V2_NAME_SIZE",
    "IFP_V2_QUAT_SCALE",
    "IFP_V2_TIME_SCALE",
    "IFP_V2_TRANS_SCALE",
    "Ifp",
    "IfpAnimation",
    "IfpFrame",
    "IfpFrameType",
    "IfpKeyframeType",
    "IfpObject",
    "IfpOutOfRange",
    "IfpValidationError",
    "IfpValidationIssue",
    "IfpVersion",
]
