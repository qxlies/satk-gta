"""Stable IDs (SID) and SID providers (SPEC §3.2). FROZEN contract.

Grammar: ``sid := kind ":" key [ "@" layer ]``.

* canonical form is lower case; addresses are ``0x...`` lower-case hex without leading zeros;
* keys never contain absolute paths or rowids;
* ``@layer`` names a version that did *not* win in the profile (only for asset kinds).

========  ====================  ===================================
kind      key                   example
========  ====================  ===================================
model     id or name            ``model:411``, ``model:infernus``
dff, txd  file stem             ``dff:infernus``, ``txd:vehicle``
tex       txd/texture           ``tex:bistro/vent_64``
pix       24 hex (blake2b-96)   ``pix:3fa2...``
file      relpath[/entry]       ``file:models/gta3.img/infernus.dff``
col       colname               ``col:sm_bush``
ide       relpath               ``ide:data/maps/la/lae2.ide``
ipl       name                  ``ipl:lae2_stream0``
inst      ipl#idx               ``inst:lae2_stream0#4``
item      ipl#sec#idx           ``item:lae2#enex#3``
zone      name                  ``zone:gan1``
ifp       name                  ``ifp:ped``
anim      ifp/anim              ``anim:ped/walk_civi``
fn, g, vt address or name       ``fn:0x53bf09``, ``fn:cped::update``, ``g:0xc8d4c0``
patch     origin/name           ``patch:upstream/hookpos_cstreaming_update_caller``
el        type/id               ``el:object/412``
bm, cap   name / id             ``bm:grove_center``, ``cap:20261004-153201-ab12``
note      integer id            ``note:17``
========  ====================  ===================================

Providers: packages that own a kind register a :class:`SidProvider` (index WP-03, re WP-09,
notes WP-06, viewer WP-07) from their ``ops`` module; :func:`provider_for` finds it.
"""

from __future__ import annotations

import difflib
import re
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .errors import SatkError

__all__ = [
    "KINDS",
    "LAYERED_KINDS",
    "KIND_OWNER",
    "Sid",
    "SidProvider",
    "register_provider",
    "unregister_provider",
    "provider_for",
    "providers",
    "is_sid",
]

#: kind -> owning package (used for NOT_READY hints).
KIND_OWNER: dict[str, str] = {
    "model": "index", "dff": "index", "txd": "index", "tex": "index", "pix": "index",
    "file": "index", "col": "index", "ide": "index", "ipl": "index", "inst": "index",
    "item": "index", "zone": "index", "ifp": "index", "anim": "index",
    "fn": "re", "g": "re", "vt": "re", "patch": "re",
    "el": "viewer", "bm": "viewer", "cap": "viewer",
    "note": "notes",
}

#: All valid kinds, in the order of SPEC §3.2.
KINDS: tuple[str, ...] = tuple(KIND_OWNER)

#: Kinds that accept ``@layer`` (asset kinds from the index).
LAYERED_KINDS: frozenset[str] = frozenset(k for k, o in KIND_OWNER.items() if o == "index")

_ADDR_KINDS = frozenset({"fn", "g", "vt"})
_HEX_ADDR = re.compile(r"^0x[0-9a-f]+$")
_LAYER = re.compile(r"^[a-z0-9][a-z0-9_.\-]*(?::[a-z0-9_.\-]+)*$")
_PIX = re.compile(r"^[0-9a-f]{24}$")
_INT = re.compile(r"^[0-9]+$")
_DRIVE = re.compile(r"^[a-z]:")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _bad(msg: str, s: str | None = None, **data) -> SatkError:
    d = {"valid_kinds": list(KINDS)}
    d.update(data)
    if s is not None:
        d["input"] = s
    return SatkError("BAD_ID", msg, hint="satk help ids", data=d)


def _norm_relpath(key: str) -> str:
    k = key.replace("\\", "/")
    while k.startswith("./"):
        k = k[2:]
    if k.startswith("/") or _DRIVE.match(k):
        raise _bad(f"absolute paths are not allowed in a SID key: {key!r}")
    parts = [p for p in k.split("/") if p not in ("", ".")]
    if not parts:
        raise _bad("empty path in SID key")
    if any(p == ".." for p in parts):
        raise _bad(f"'..' is not allowed in a SID key: {key!r}")
    if any(not p.strip() for p in parts):
        raise _bad(f"whitespace-only path components are not allowed in a SID key: {key!r}")
    normalized = "/".join(parts)
    # Removing separators/dot components must not expose whitespace that the next
    # parse would strip. Interior names with spaces retain their existing spelling.
    if normalized != normalized.strip():
        raise _bad(f"leading/trailing whitespace in normalized SID path: {key!r}")
    return normalized


def _norm_int(s: str, what: str) -> str:
    if not _INT.match(s):
        raise _bad(f"{what} must be a non-negative integer, got {s!r}")
    return str(int(s))


def _norm_addr_or_name(key: str) -> str:
    if key.startswith("0x"):
        if not _HEX_ADDR.match(key):
            raise _bad(f"bad hex address {key!r}")
        return "0x" + format(int(key, 16), "x")
    return key


def _two_part(key: str, sep: str, what: str) -> str:
    a, s, b = key.partition(sep)
    if not s or not a or not b or sep in b:
        raise _bad(f"key must look like {what}, got {key!r}")
    return f"{a}{sep}{b}"


def _normalize_key(kind: str, key: str) -> str:
    if kind == "model":
        return str(int(key)) if _INT.match(key) else key
    if kind in ("file", "ide"):
        return _norm_relpath(key)
    if kind == "tex":
        return _two_part(key, "/", "txd/texture")
    if kind == "anim":
        return _two_part(key, "/", "ifp/anim")
    if kind == "el":
        return _two_part(key, "/", "type/id")
    if kind == "patch":
        return _two_part(key, "/", "origin/name")
    if kind == "pix":
        if not _PIX.match(key):
            raise _bad(f"pix key must be 24 hex digits, got {key!r}")
        return key
    if kind == "inst":
        ipl, s, idx = key.partition("#")
        if not s or not ipl or "#" in idx:
            raise _bad(f"key must look like ipl#idx, got {key!r}")
        return f"{ipl}#{_norm_int(idx, 'inst index')}"
    if kind == "item":
        parts = key.split("#")
        if len(parts) != 3 or not parts[0] or not parts[1]:
            raise _bad(f"key must look like ipl#sec#idx, got {key!r}")
        return f"{parts[0]}#{parts[1]}#{_norm_int(parts[2], 'item index')}"
    if kind == "note":
        return _norm_int(key, "note id")
    if kind in _ADDR_KINDS:
        return _norm_addr_or_name(key)
    return key


@dataclass(frozen=True, slots=True)
class Sid:
    """A stable ID. Construction normalizes to the canonical form (or raises ``BAD_ID``).

    ``Sid.parse(str(x)) == x`` holds for every valid ``x``.
    """

    kind: str
    key: str
    layer: str | None = None

    def __post_init__(self) -> None:
        kind = str(self.kind).strip().lower()
        if kind not in KIND_OWNER:
            close = difflib.get_close_matches(kind, KINDS, n=3, cutoff=0.5)
            raise SatkError(
                "BAD_ID",
                f"unknown SID kind {kind!r}",
                hint="satk help ids",
                did_you_mean=[f"{c}:{self.key}" for c in close],
                data={"valid_kinds": list(KINDS)},
            )
        key = str(self.key).strip().lower()
        if not key:
            raise _bad(f"empty key in SID of kind {kind!r}")
        if _CTRL.search(key):
            raise _bad("control characters are not allowed in a SID key")
        if kind in LAYERED_KINDS and "@" in key:
            raise _bad(f"'@' is not allowed in a {kind} key (use the layer field): {key!r}")
        key = _normalize_key(kind, key)
        layer = self.layer
        if layer is not None:
            layer = str(layer).strip().lower()
            if not layer:
                layer = None
            elif kind not in LAYERED_KINDS:
                raise _bad(f"kind {kind!r} does not take an @layer")
            elif not _LAYER.match(layer):
                raise _bad(f"bad layer name {layer!r}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "layer", layer)

    @staticmethod
    def parse(s: "str | Sid") -> "Sid":
        """Parse ``kind:key[@layer]``; raises ``BAD_ID`` listing the valid kinds."""
        if isinstance(s, Sid):
            return s
        if not isinstance(s, str):
            raise _bad(f"SID must be a string, got {type(s).__name__}")
        text = s.strip()
        kind, sep, rest = text.partition(":")
        if not sep:
            raise _bad(f"not a SID (expected kind:key): {s!r}", s)
        kind_l = kind.strip().lower()
        layer = None
        if kind_l in LAYERED_KINDS and "@" in rest:
            rest, _, layer = rest.rpartition("@")
        return Sid(kind_l, rest, layer)

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}" + (f"@{self.layer}" if self.layer else "")

    @property
    def owner(self) -> str:
        """Package that owns this kind (``index``, ``re``, ``viewer``, ``notes``)."""
        return KIND_OWNER[self.kind]

    @property
    def addr(self) -> int | None:
        """Integer address for ``fn``/``g``/``vt`` keys written as ``0x...``, else ``None``."""
        if self.kind in _ADDR_KINDS and self.key.startswith("0x"):
            return int(self.key, 16)
        return None

    @property
    def num(self) -> int | None:
        """Integer key for ``model``/``note`` when numeric, else ``None``."""
        if self.kind in ("model", "note") and _INT.match(self.key):
            return int(self.key)
        return None

    @property
    def parts(self) -> tuple[str, ...]:
        """Key split by its structural separator (``/`` or ``#``)."""
        if self.kind in ("inst", "item"):
            return tuple(self.key.split("#"))
        if self.kind in ("tex", "anim", "el", "patch", "file", "ide"):
            return tuple(self.key.split("/"))
        return (self.key,)

    def without_layer(self) -> "Sid":
        return Sid(self.kind, self.key) if self.layer else self


def is_sid(s: str) -> bool:
    """True if ``s`` parses as a SID."""
    try:
        Sid.parse(s)
        return True
    except SatkError:
        return False


@runtime_checkable
class SidProvider(Protocol):
    """Resolves SIDs of some kinds. ``find``/``refs`` return table envelopes (§3.3)."""

    kinds: tuple[str, ...]

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict: ...

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict: ...

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict: ...


_lock = threading.RLock()
_providers: dict[str, SidProvider] = {}


def register_provider(p: SidProvider, *, replace: bool = False) -> None:
    """Register ``p`` for each of ``p.kinds``.

    A kind already served by a *different* provider raises ``ValueError`` unless
    ``replace=True`` (re-registering the same object is a no-op).
    """
    kinds = tuple(getattr(p, "kinds", ()) or ())
    if not kinds:
        raise ValueError("provider has no kinds")
    for k in kinds:
        if k not in KIND_OWNER:
            raise ValueError(f"unknown SID kind {k!r}")
    with _lock:
        for k in kinds:
            cur = _providers.get(k)
            if cur is not None and cur is not p and not replace:
                raise ValueError(f"SID kind {k!r} already has a provider: {type(cur).__name__}")
        for k in kinds:
            _providers[k] = p


def unregister_provider(p: SidProvider) -> None:
    """Remove ``p`` from all kinds it serves (used by tests and shutdown)."""
    with _lock:
        for k in [k for k, v in _providers.items() if v is p]:
            del _providers[k]


def providers() -> dict[str, SidProvider]:
    """Snapshot ``kind -> provider``."""
    with _lock:
        return dict(_providers)


def provider_for(kind: str) -> SidProvider:
    """Provider for ``kind``; runs op discovery once, then ``NOT_READY`` if still missing."""
    kind = str(kind).lower()
    if kind not in KIND_OWNER:
        raise _bad(f"unknown SID kind {kind!r}")
    with _lock:
        p = _providers.get(kind)
    if p is not None:
        return p
    from .registry import discover  # local import: registry imports this module

    discover()
    with _lock:
        p = _providers.get(kind)
    if p is not None:
        return p
    owner = KIND_OWNER[kind]
    raise SatkError(
        "NOT_READY",
        f"no provider for SID kind {kind!r} (package satk.{owner} not loaded or not built)",
        hint="satk doctor",
        data={"kind": kind, "owner": f"satk.{owner}"},
    )
