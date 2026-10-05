"""IDE checks (``ide.*`` rules): per line (name lengths, ID range, draw distance) and across the IDE set
(duplicate IDs and names). The definitions feed the link checks as :class:`~satk.lint.dff.ModelDef`."""

from __future__ import annotations

from ..formats.ide import parse_ide
from .dff import ModelDef
from .rules import Collector

__all__ = ["check_ide", "check_ide_set"]


def check_ide(c: Collector, label: str, text: str) -> tuple[list[ModelDef], list[tuple[str, str]]]:
    """Run the per-line ``ide.*`` rules; returns ``(definitions, txdp pairs)``."""
    errs: list = []
    defs, txdp, _fx = parse_ide(text, errors=errs)
    for line, msg in errs:
        c.add("ide.parse", label, line=line, err=msg)
    nmax = int(c.rules.param("ide.name_len", "max", 23))
    tmax = int(c.rules.param("ide.txd_len", "max", 23))
    imax = int(c.rules.param("ide.id_range", "max", 19999))
    dmin = float(c.rules.param("ide.draw_min", "min", 4.0))
    out: list[ModelDef] = []
    for d in defs:
        if len(d.name) > nmax:
            c.add("ide.name_len", label, line=d.line, id=d.id, name=d.name, n=len(d.name))
        if d.txd and len(d.txd) > tmax:
            c.add("ide.txd_len", label, line=d.line, id=d.id, txd=d.txd, n=len(d.txd))
        if not 0 <= d.id <= imax:
            c.add("ide.id_range", label, line=d.line, id=d.id)
        meshes = "meshes" in d.extra
        if d.sec in ("objs", "tobj") and not meshes and d.draw is not None and d.draw < dmin:
            c.add("ide.draw_min", label, line=d.line, id=d.id, name=d.name, draw=d.draw)
        out.append(ModelDef(d.id, d.name, d.txd, d.sec, d.draw, f"{label}:{d.line}", meshes))
    return out, list(txdp)


def check_ide_set(c: Collector, defs: list[ModelDef]) -> None:
    """Duplicate IDs (error) and names (warn) across all definitions, in load order."""
    by_id: dict[int, ModelDef] = {}
    by_name: dict[str, ModelDef] = {}
    for d in defs:
        if d.origin == "index":
            continue
        label, _, line = d.origin.rpartition(":")
        prev = by_id.get(d.id)
        if prev is not None:
            c.add("ide.dup_id", label, line=line, id=d.id, name=d.name, other=prev.origin)
        else:
            by_id[d.id] = d
        k = d.name.lower()
        other = by_name.get(k)
        if other is not None and other.id != d.id:
            c.add("ide.dup_name", label, line=line, id=d.id, name=d.name, other_id=other.id, other=other.origin)
        elif other is None:
            by_name[k] = d
