"""Field layout of one ``nodes<N>.dat`` region (SA PC "compact" profile). Stdlib only.

A region file is five ``u32`` counts (nodes, vehicle nodes, pedestrian nodes, navis, links) followed
by the node array, the navi array and the link arrays; :mod:`satk.paths.vendor` (gta-flow
``codec``) splits it losslessly. This module only names the fields of the records:

* path node, 28 bytes: ``+8 <3h>`` position / 8, ``+14 <h>`` distance from origin (runtime),
  ``+16 <h>`` first link index, ``+18 <HH>`` area and node id, ``+22`` width (/16), ``+23`` flood
  label (connected component), ``+24 <I>`` flags: bits 0-3 number of links, 4 dead end,
  5 switched off, 6 roadblocks, 7 water (boat path), 8 switched off originally, 10 don't wander,
  12 not highway, 13 highway, 16-19 spawn probability, 20-23 behaviour (1 roadblock, 2 parking);
* navi (car path link), 14 bytes: ``<hh>`` position / 8, ``<HH>`` attached node (area, id),
  ``<bb>`` direction / 100, width (/16), lanes byte (bits 0-2 lanes toward the attached node,
  3-5 lanes away from it, 6 traffic light direction), ``<H>`` bits 0-1 traffic light state,
  2 bridge lights;
* link entry: target ``<HH>`` (area, id), navi address ``<H>`` = ``area << 10 | navi``, a
  distance byte and an intersection byte (bit 0 road cross, bit 1 pedestrian traffic light).

Vehicle nodes come first in the node array; a vehicle node with the water flag is a boat node.
Regions are an 8x8 grid of 750-unit squares starting at (-3000, -3000): :func:`area_of`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = ["AREAS", "AREA_SIZE", "NODE_SIZE", "NAVI_SIZE", "NODE_FLAGS", "KINDS", "Node", "Navi",
           "parse_node", "parse_navi", "flag_names", "area_of", "addr", "parse_addr", "navi_addr"]

AREAS = 64
AREA_SIZE = 750.0
NODE_SIZE = 28
NAVI_SIZE = 14
#: Named single-bit node flags (bit -> name).
NODE_FLAGS: dict[int, str] = {4: "dead_end", 5: "switched_off", 6: "roadblocks", 7: "water",
                              8: "switched_off_orig", 10: "dont_wander", 12: "not_highway", 13: "highway"}
#: Node kinds as satk names them.
KINDS = ("car", "boat", "ped")

_NODE = struct.Struct("<3hhhHHBBI")   # from +8: x y z, dist, base, area, id, width, flood, flags
_NAVI = struct.Struct("<hhHHbbBBH")


@dataclass(frozen=True, slots=True)
class Node:
    """One path node (positions in world units, width in units)."""

    area: int
    idx: int
    kind: str
    x: float
    y: float
    z: float
    base: int
    degree: int
    width: float
    flood: int
    flags: int
    spawn: int
    behaviour: int
    rec_area: int
    rec_idx: int


@dataclass(frozen=True, slots=True)
class Navi:
    """One navi (car path link): the lane description of a road segment."""

    area: int
    idx: int
    x: float
    y: float
    at_area: int
    at_idx: int
    dx: float
    dy: float
    width: float
    lanes_to: int
    lanes_away: int
    light_dir: int
    light: int
    bridge: int


def parse_node(raw: bytes, area: int, idx: int, vehicles: int) -> Node:
    """Decode a 28-byte node record; ``vehicles`` = the region's vehicle node count."""
    x, y, z, _dist, base, rec_area, rec_idx, width, flood, flags = _NODE.unpack_from(raw, 8)
    if idx < vehicles:
        kind = "boat" if flags & 0x80 else "car"
    else:
        kind = "ped"
    return Node(area, idx, kind, x / 8, y / 8, z / 8, base, flags & 15, width / 16, flood, flags,
                (flags >> 16) & 15, (flags >> 20) & 15, rec_area, rec_idx)


def parse_navi(raw: bytes, area: int, idx: int) -> Navi:
    """Decode a 14-byte navi record."""
    x, y, at_area, at_idx, dx, dy, width, lanes, extra = _NAVI.unpack(raw)
    return Navi(area, idx, x / 8, y / 8, at_area, at_idx, dx / 100, dy / 100, width / 16,
                lanes & 7, (lanes >> 3) & 7, (lanes >> 6) & 1, extra & 3, (extra >> 2) & 1)


def flag_names(flags: int) -> list[str]:
    """Names of the single-bit flags set in a node's flag word."""
    return [name for bit, name in NODE_FLAGS.items() if flags >> bit & 1]


def area_of(x: float, y: float) -> int:
    """Region (0..63) of a world position, outer squares clamped (as the engine and gta-flow do)."""
    cx = min(7, max(0, int((x + 3000) / AREA_SIZE)))
    cy = min(7, max(0, int((y + 3000) / AREA_SIZE)))
    return cx + 8 * cy


def addr(area: int, idx: int) -> str:
    """``"15:6"`` - the address of a node or navi (region, index)."""
    return f"{area}:{idx}"


def parse_addr(s: str) -> tuple[int, int]:
    """``"15:6"`` -> ``(15, 6)``; ``ValueError`` for anything else."""
    a, sep, i = str(s).strip().partition(":")
    if not sep or not a.isdigit() or not i.isdigit():
        raise ValueError(f"bad node address {s!r} (expected <area>:<index>, e.g. 15:6)")
    return int(a), int(i)


def navi_addr(packed: int) -> tuple[int, int]:
    """Packed navi address of a link entry -> ``(area, navi index)``."""
    return packed >> 10, packed & 1023
