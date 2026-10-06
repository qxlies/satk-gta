"""Read-only access to the data files of a load profile, with the original text of every line.

:class:`GameData` wraps :class:`satk.modinspect.base.BaseGame` (the profile root before Mod Loader) and adds
what add-ons and patches need: the raw text of data files, the IDE line of a model, parsed
``handling.cfg`` / ``weapon.dat`` (token-preserving), ``carcols.dat``, ``carmods.dat`` (mods section),
``cargrp.dat`` (groups), ``pedstats.dat`` and the vehicles' GXT keys. Everything is read through
``open_ro``; nothing is written.

Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import jpath
from ..formats.dat import read_text, resolve_ci
from ..formats.ide import IdeRec, parse_ide
from ..modinspect.base import BaseGame, ModelDef
from . import handling as H
from . import weapon as W
from .tokline import TokLine

__all__ = ["GameData", "ModelLine", "CargrpLine", "PedStat", "parse_cargrp", "parse_carmods", "parse_pedstats"]

#: ``model kind`` -> IDE sections a donor of that kind may come from.
KIND_SECTIONS = {"vehicle": ("cars",), "ped": ("peds",), "weapon": ("weap",), "object": ("objs", "tobj")}


@dataclass
class ModelLine:
    """A model's IDE line: definition, file, 1-based line number and the raw text (no end of line)."""

    rec: IdeRec
    ide: str
    line: int
    text: str


@dataclass
class CargrpLine:
    """One ``cargrp.dat`` group: index (0-based, in file order), model names, label from the comment."""

    idx: int
    line: int
    models: list[str]
    label: str


@dataclass
class PedStat:
    idx: int
    line: int
    name: str
    values: list[str]


def parse_cargrp(text: str) -> list[CargrpLine]:
    """Groups of ``cargrp.dat``: every line with at least one model name is the next group."""
    out: list[CargrpLine] = []
    for n, raw in enumerate(text.splitlines(), 1):
        body, _, comment = raw.partition("#")
        names = [t for t in body.replace(",", " ").split() if t]
        if not names:
            continue
        out.append(CargrpLine(len(out), n, names, comment.strip()))
    return out


def parse_carmods(text: str) -> dict[str, tuple[int, list[str]]]:
    """``mods`` section of ``carmods.dat``: lower model name -> (line, upgrade names)."""
    out: dict[str, tuple[int, list[str]]] = {}
    sec = None
    for n, raw in enumerate(text.splitlines(), 1):
        toks = raw.split("#", 1)[0].replace(",", " ").split()
        if not toks:
            continue
        if sec is None:
            sec = toks[0].lower()
            continue
        if toks[0].lower() == "end":
            sec = None
            continue
        if sec == "mods":
            out[toks[0].lower()] = (n, toks[1:])
    return out


def parse_pedstats(text: str) -> list[PedStat]:
    """``pedstats.dat`` lines in order (the engine indexes them by position, the name is a label)."""
    out: list[PedStat] = []
    for n, raw in enumerate(text.splitlines(), 1):
        toks = raw.split("#", 1)[0].split()
        if len(toks) < 2:
            continue
        out.append(PedStat(len(out), n, toks[0], toks[1:]))
    return out


class GameData:
    """Data files of one profile (read-only). ``profile`` as in ``satk config show``."""

    def __init__(self, profile: str = "vanilla", *, base: BaseGame | None = None):
        self.base = base or BaseGame(profile)
        self.profile = profile

    # ---- files

    def data(self, fs_name: str, *, required: bool = True) -> tuple[str, Path, str] | None:
        """``(relpath, path, text)`` of a data file handled by name (``handling.cfg``)."""
        hit = self.base.data_file(fs_name)
        if hit is None:
            if not required:
                return None
            raise SatkError("NOT_FOUND", f"profile {self.profile!r} has no data/{fs_name}",
                            hint=f"check the game folder of the profile: {jpath(self.base.root)}")
        rel, p = hit
        return rel, p, read_text(p)

    @cached_property
    def handling(self) -> tuple[str, H.HFile]:
        rel, _p, text = self.data("handling.cfg")
        return rel, H.parse(text)

    @cached_property
    def weapons(self) -> tuple[str, W.WFile]:
        rel, _p, text = self.data("weapon.dat")
        return rel, W.parse(text)

    @cached_property
    def carcols(self):
        from ..formats.carcols import parse_carcols

        got = self.data("carcols.dat", required=False)
        if got is None:
            return None, None, None
        rel, _p, text = got
        return rel, parse_carcols(text), text

    @cached_property
    def pedstats(self) -> tuple[str, list[PedStat]] | None:
        got = self.data("pedstats.dat", required=False)
        return None if got is None else (got[0], parse_pedstats(got[2]))

    # ---- models

    def model(self, key: str, kind: str | None = None) -> ModelDef:
        """A model by ``model:411``, ``411`` or name; ``kind`` checks its IDE section."""
        k = key.strip()
        if k.lower().startswith("model:"):
            k = k[6:]
        if re.fullmatch(r"-?\d+", k):
            m = self.base.models.get(int(k))
        else:
            m = self.base.by_name.get(k.lower())
        if m is None:
            import difflib

            close = difflib.get_close_matches(k.lower(), list(self.base.by_name), n=3, cutoff=0.75)
            raise SatkError("NOT_FOUND", f"no model {key!r} in profile {self.profile!r}",
                            hint=f"satk asset find {k} --kind model",
                            did_you_mean=[f"model:{self.base.by_name[c].id} {c}" for c in close])
        if kind and m.sec not in KIND_SECTIONS[kind]:
            raise SatkError("BAD_PARAMS", f"model:{m.id} {m.name} is a {m.sec} model, not a {kind}",
                            hint=f"pick a donor from the IDE section {'/'.join(KIND_SECTIONS[kind])} "
                                 f"(satk asset find <name> --kind model)")
        return m

    def model_line(self, m: ModelDef) -> ModelLine:
        """The IDE line that defines ``m`` (the last definition of its id in its IDE)."""
        p = resolve_ci(self.base.root, m.ide)
        if p is None:
            raise SatkError("NOT_FOUND", f"{m.ide} of model:{m.id} is missing")
        text = read_text(p)
        defs, _t, _f = parse_ide(text)
        hit = [d for d in defs if d.id == m.id]
        if not hit:
            raise SatkError("NOT_FOUND", f"model:{m.id} is not in {m.ide}")
        d = hit[-1]
        raw = text.splitlines()[d.line - 1]
        return ModelLine(d, m.ide, d.line, raw.rstrip("\r\n"))

    def models_of_handling(self, hid: str) -> list[ModelDef]:
        return sorted((m for m in self.base.models.values() if m.sec == "cars" and m.handling
                       and m.handling.lower() == hid.lower()), key=lambda m: m.id)

    @cached_property
    def car_gxt_keys(self) -> dict[str, int]:
        """GXT keys of the vehicles (vehicles.ide 6th field, upper case) -> model id."""
        out: dict[str, int] = {}
        for ide in sorted({m.ide for m in self.base.models.values() if m.sec == "cars"}):
            p = resolve_ci(self.base.root, ide)
            if p is None:
                continue
            defs, _t, _f = parse_ide(read_text(p))
            for d in defs:
                if d.sec == "cars" and d.extra.get("gxt") is not None:
                    out.setdefault(str(d.extra["gxt"]).upper(), d.id)
        return out

    @cached_property
    def gxt_main(self) -> set[int] | None:
        """Key hashes of the MAIN table of ``text/american.gxt`` (``None`` when absent or unreadable)."""
        from ..formats.gxt import load_gxt
        from ..formats.rw import FormatError

        p = resolve_ci(self.base.root, "text/american.gxt")
        if p is None:
            return None
        try:
            return set(load_gxt(p).tables.get("MAIN", {}))
        except (FormatError, OSError):
            return None


def tok(raw: str) -> TokLine:
    """An IDE / carcols / carmods line as a token line (commas are separators, ``#`` starts a comment)."""
    return TokLine.parse(raw, commas=True, comment="#")
