"""Which Mod Loader plugin takes a mod file, and what it does with it.

A port of the ``GetBehaviour`` rules of the Mod Loader 0.3 plugins for GTA SA (std.fx, std.asi,
std.data, std.text, std.scm, std.sprites, std.stream, audio). Plugins are asked in priority order
(FX 48, then the default 50, sprites 51, stream 52) and the first that accepts a file handles it.

:func:`classify` returns a :class:`Behaviour`; its ``key`` groups files that compete for the same
game file: two files with the same key in different mods are a conflict (the winner is chosen by
:mod:`satk.modinspect.modloader`), unless the plugin merges them (``merge=True``).

Source: thelink2012/modloader (MIT), ``src/plugins/gta3/*`` - see ``NOTICE-modloader.txt``.
Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Behaviour", "classify", "DATA_MERGE", "DATA_OVERRIDE", "FX_NAMES", "STREAM_EXTS", "README_EXTS"]

#: Data files std.data merges line by line (and the override when only one mod has the file).
DATA_MERGE = frozenset({
    "gta.dat", "default.dat", "carcols.dat", "carmods.dat", "handling.cfg", "plants.dat", "melee.dat", "water.dat",
    "cargrp.dat", "pedgrp.dat", "ped.dat", "pedstats.dat", "statdisp.dat", "shopping.dat", "object.dat",
    "surface.dat", "surfinfo.dat", "surfaud.dat", "weapon.dat", "procobj.dat", "stream.ini", "fistfite.cfg",
    "particle.cfg", "ar_stats.dat", "animgrp.dat",
})
#: Data files std.data only overrides (the highest-priority mod's file is used as a whole).
DATA_OVERRIDE = frozenset({
    "timecyc.dat", "popcycle.dat", "clothes.dat", "furnitur.dat", "fonts.dat", "roadblox.dat", "tracks.dat",
    "tracks2.dat", "tracks3.dat", "tracks4.dat", "cullzone.dat", "waterpro.dat", "flight.dat", "flight2.dat",
    "flight3.dat",
})
#: Files the FX loader (priority 48) replaces by name.
FX_NAMES = frozenset({
    "arrow.dff", "effects.fxp", "effectspc.txd", "fonts.txd", "fronten1.txd", "fronten2.txd", "fronten3.txd",
    "fronten_pc.txd", "frontend.txd", "grass0_1.dff", "grass0_2.dff", "grass0_3.dff", "grass0_4.dff",
    "grass1_1.dff", "grass1_2.dff", "grass1_3.dff", "grass1_4.dff", "hud.txd", "loadscs.txd", "menu.txd",
    "particle.txd", "pcbtns.txd", "plant1.txd", "vehicle.txd", "zonecylb.dff",
})
#: Extensions std.stream (priority 52) takes: streamed resources that normally live in IMG archives.
STREAM_EXTS = frozenset({"dff", "txd", "col", "ipl", "dat", "ifp", "rrr", "scm", "img"})
README_EXTS = frozenset({"txt"})
_FORBIDDEN_ASI = frozenset({"cleo.asi", "iii.cleo.asi", "vc.cleo.asi", "modloader.asi"})
_CLEO_SCRIPT = re.compile(r"^(?:cm|cs|cs[345])$")
_DECISION = re.compile(r"(?:^|/)decision/allowed/\w+\.(?:ped|grp)$", re.I)
_SCRIPT_SPRITE = re.compile(r"(?:^|/)models/txd/\w{1,8}\.txd$", re.I)
_CARREC = re.compile(r"^carrec\d+", re.I)
_NODES = re.compile(r"^nodes\d+\.dat$", re.I)
_AUDIO_EXTS = frozenset({"raw", "sdt", "wav", "mp3", "adf", "vb", "ogg"})


@dataclass(frozen=True, slots=True)
class Behaviour:
    """What Mod Loader does with one file.

    Attributes:
        plugin: handling plugin (``std.stream``, ``std.data`` ...) or ``""`` if none (the file is ignored).
        kind: ``dff txd col ifp ipl-bin rrr scm nodes img | ide ipl zon data readme | asi cleo | gxt fxt |
            sprite fx | audio movie | other``.
        key: competition key: files with equal keys replace (or merge with) each other.
        merge: files with this key are merged by std.data instead of replaced.
        note: short remark (``forced clothing``, ``img folder``, ``binary IPL`` ...).
    """

    plugin: str
    kind: str
    key: str
    merge: bool = False
    note: str = ""


def _in_img_folder(rel: str) -> str | None:
    """Name of the ``*.img`` (or ``player_img``) folder the file sits in directly, if any."""
    parts = rel.split("/")
    if len(parts) >= 2 and (parts[-2].lower().endswith(".img") or parts[-2].lower() == "player_img"):
        return parts[-2].lower()
    return None


def classify(rel: str, head: bytes = b"", *, in_img: bool = False) -> Behaviour:
    """Classify a mod file by its path inside the mod.

    Args:
        rel: path inside the mod (forward slashes).
        head: the first 4 bytes, used for ``.ipl`` (``bnry`` = binary IPL, streamed).
        in_img: the file is an entry of an ``.img`` archive shipped by the mod (always streamed).
    """
    lrel = rel.lower()
    name = lrel.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if in_img:
        return Behaviour("std.stream", _stream_kind(ext, head), f"stream:{name}", note="img archive entry")
    # FX loader (priority 48)
    if name in FX_NAMES:
        return Behaviour("std.fx", "fx", f"fx:{name}")
    # default-priority plugins
    if ext in ("asi", "cleo") or name == "d3d9.dll":
        if name in _FORBIDDEN_ASI:
            return Behaviour("", "asi", f"asi:{name}", note="forbidden: Mod Loader never loads this ASI")
        return Behaviour("std.asi", "asi", f"asi:{lrel}", note="loaded as a plugin")
    if _CLEO_SCRIPT.match(ext):
        return Behaviour("std.asi", "cleo", f"cleo:{lrel}", note="CLEO script")
    if ext == "txt":
        return Behaviour("std.data", "readme", f"readme:{lrel}")
    if ext == "ide":
        return Behaviour("std.data", "ide", "ide:" + gta_path(lrel), merge=True)
    if ext == "zon" or (ext == "ipl" and head[:4] != b"bnry"):
        return Behaviour("std.data", "zon" if ext == "zon" else "ipl", "ipl:" + gta_path(lrel))
    if ext in ("ped", "grp"):
        if _DECISION.search(lrel):
            return Behaviour("std.data", "data", f"data:{name}", merge=True, note="decision maker")
        return Behaviour("", "other", f"other:{lrel}", note="decision files must be in decision/allowed/")
    if name in DATA_MERGE:
        return Behaviour("std.data", "data", f"data:{name}", merge=True)
    if name in DATA_OVERRIDE:
        return Behaviour("std.data", "data", f"data:{name}")
    if ext == "gxt":
        return Behaviour("std.text", "gxt", f"text:{name}")
    if ext == "fxt":
        return Behaviour("std.text", "fxt", f"fxt:{lrel}", note="FXT text entries")
    if ext == "mpg":
        return Behaviour("std.movies", "movie", f"movie:{name}")
    if ext in _AUDIO_EXTS:
        return Behaviour("std.audio", "audio", f"audio:{name}", note="audio (not inspected)")
    if ext == "scm" and name == "main.scm":
        return Behaviour("std.scm", "script", "scm:main.scm", note="main script")
    # std.sprites (51): TXDs under a txd folder
    if ext == "txd" and "/txd/" in "/" + lrel:
        if _SCRIPT_SPRITE.search(lrel):
            return Behaviour("std.sprites", "sprite", f"sprite:{name}", note="script sprite")
        return Behaviour("std.sprites", "sprite", f"splash:{name}", note="loading screen")
    # std.stream (52)
    if ext in STREAM_EXTS:
        imgdir = _in_img_folder(rel)
        if ext == "img":
            return Behaviour("std.stream", "img", f"imgfile:{name}", note="IMG archive added to the game")
        forced = imgdir in ("player.img", "player_img")
        if imgdir is None:
            if ext == "dat":
                if _NODES.match(name):
                    return Behaviour("", "nodes", f"other:{lrel}", note="nodes*.dat only works inside a *.img folder")
                return Behaviour("", "other", f"other:{lrel}", note="no Mod Loader handler")
            if ext == "ipl" and head[:4] != b"bnry":  # pragma: no cover - text IPLs are handled above
                return Behaviour("", "other", f"other:{lrel}")
            if ext == "rrr" and not _CARREC.match(name):
                return Behaviour("", "other", f"other:{lrel}", note="vehicle recordings must be named carrec<N>.rrr")
        kind = _stream_kind(ext, head)
        note = "forced clothing" if forced else ("img folder" if imgdir else "")
        return Behaviour("std.stream", kind, f"stream:{name}" + ("#cloth" if forced else ""), note=note)
    return Behaviour("", "other", f"other:{lrel}", note="no Mod Loader handler")


def _stream_kind(ext: str, head: bytes) -> str:
    if ext == "ipl":
        return "ipl-bin"
    if ext == "dat":
        return "nodes"
    return ext if ext in ("dff", "txd", "col", "ifp", "rrr", "scm") else "other"


def gta_path(lrel: str) -> str:
    """``find_gta_path``: the path from the first ``data/`` folder (``mymod/data/maps/x.ide`` -> ``data/maps/x.ide``).

    Without a ``data/`` folder the path inside the mod is kept; Mod Loader then matches the file by
    name against the ``IDE``/``IPL`` lines of ``gta.dat`` (:func:`satk.modinspect.base.match_gta_path`).
    """
    p = lrel.lower()
    i = ("/" + p).find("/data/")
    return p[i:] if i > 0 else p
