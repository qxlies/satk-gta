"""``satk crash bisect``: find the modloader mod behind a crash by halving, one game start per step.

Stateless: the plan is recomputed from the outcomes so far (``--results crash,ok,...``), so a
person or an agent only appends the result of each test run. Steps:

1. **all mods off** - every loaded mod goes into ``IgnoreMods``. ``crash`` here means the cause is
   not a modloader mod (ASI plugins, CLEO scripts outside modloader, the save, the game files):
   the bisection stops. ``ok`` starts the halving.
2. **halving** - of the remaining candidates the first half (by name) stays loaded and the second
   half is disabled; mods already cleared stay loaded, so the setup changes as little as possible.
   ``crash`` -> the culprit is in the loaded half; ``ok`` -> in the disabled half.
3. one candidate left -> the culprit; the fragment restores the original section.

The fragment replaces the whole ``[Profiles.<P>.IgnoreMods]`` section of ``<modloader>/modloader.ini``
and keeps the entries that were already there. satk never writes it: the user pastes it and
restores the section (``undo``) at the end. Mods that crash only together are not found by halving;
the plan then ends on a mod that is not the culprit (the ``note`` says so). Stdlib only.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..core.envelope import obj
from ..core.errors import SatkError
from ..core.paths import jpath
from .modfolder import Folder, find_folder, read_folder

__all__ = ["plan", "parse_results", "RESULT_WORDS"]

RESULT_WORDS = {"crash": "crash", "c": "crash", "bad": "crash", "fail": "crash", "y": "crash",
                "ok": "ok", "o": "ok", "good": "ok", "pass": "ok", "n": "ok"}


def parse_results(results: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in results or []:
        for word in str(item).replace(";", ",").split(","):
            w = word.strip().lower()
            if not w:
                continue
            if w not in RESULT_WORDS:
                raise SatkError("BAD_PARAMS", f"bad result {word!r}: use crash or ok",
                                hint="--results crash,ok,crash (one word per test run, in order)")
            out.append(RESULT_WORDS[w])
    return out


def _section(folder: Folder, extra: list[str], step_note: str) -> str:
    name = folder.section_name("IgnoreMods")
    lines = [f"[{name}]", f"; satk crash bisect: {step_note}"]
    keep = [ln for ln in folder.sections.get(f"profiles.{folder.profile}.ignoremods".lower(), [])
            if ln.strip()]
    lines += keep
    lines += extra
    return "\n".join(lines) + "\n"


def _undo(folder: Folder) -> str:
    name = folder.section_name("IgnoreMods")
    keep = [ln for ln in folder.sections.get(f"profiles.{folder.profile}.ignoremods".lower(), []) if ln.strip()]
    return "\n".join([f"[{name}]"] + keep) + "\n"


def plan(path: str | None, results: list[str] | None = None) -> dict:
    """The next bisection step for a modloader folder (or a game folder that has one)."""
    from ..core.paths import cfg

    if path:
        mdir = find_folder(path)
        if mdir is None:
            raise SatkError("NOT_FOUND", f"no modloader folder at {jpath(Path(path))}",
                            hint="give <game>/modloader or the game folder")
    else:
        root = cfg().paths.get("game_root") or cfg().paths.get("installed")
        mdir = find_folder(root) if root is not None and Path(root).is_dir() else None
        if mdir is None:
            raise SatkError("NOT_FOUND", "no modloader folder in the configured game folder",
                            hint="satk crash bisect <game>/modloader")
    folder = read_folder(mdir)
    res = parse_results(results)
    cands = [m.name for m in folder.loaded]
    off = [m for m in folder.mods if not m.loaded]
    base = {"folder": jpath(folder.path), "ini": jpath(folder.path / "modloader.ini"), "profile": folder.profile}
    if not cands:
        raise SatkError("NOT_FOUND", f"no loaded mods in {jpath(folder.path)}"
                        + (f" ({len(off)} disabled by modloader.ini)" if off else ""),
                        hint="nothing to bisect: the crash is not caused by a modloader mod",
                        data=base)
    total = 1 + math.ceil(math.log2(len(cands)))
    note = ("halving assumes one culprit; if the last mod is not it, two mods crash together: "
            "rerun without the last result")
    nested = [m.name for m in folder.loaded if m.nested]
    warn = [f"NESTED: {', '.join(nested[:5])} has its own modloader.ini (a folder of mods): bisected as one mod"] \
        if nested else None
    # step 1: all mods off
    if not res:
        return obj(None, step=1, steps=total, candidates=len(cands), test="start the game with ALL mods disabled",
                   disable=len(cands), section=_section(folder, cands, f"step 1/{total}: all {len(cands)} mods off"),
                   undo=_undo(folder), next="satk crash bisect ... --results crash  (or --results ok)", **base,
                   warn=warn)
    if res[0] == "crash":
        if len(res) > 1:
            raise SatkError("BAD_PARAMS", "step 1 crashed with all mods disabled: the bisection already ended",
                            hint="drop the results after the first one")
        return obj(None, step=1, steps=1, done=True, culprit=None, candidates=0,
                   result="the game crashes with every modloader mod disabled: the cause is outside modloader "
                          "(ASI plugins, CLEO scripts in the cleo folder, the save game or the game files)",
                   section=_undo(folder), undo=_undo(folder), **base, warn=warn)
    pool = list(cands)
    for i, r in enumerate(res[1:], 2):
        if len(pool) <= 1:
            raise SatkError("BAD_PARAMS", f"too many results: the culprit was found after step {i - 1}",
                            hint="drop the extra results")
        half = pool[:(len(pool) + 1) // 2]
        pool = half if r == "crash" else pool[len(half):]
    step = len(res) + 1
    if len(pool) == 1:
        culprit = pool[0]
        return obj(None, step=step - 1, steps=step - 1, done=True, culprit=culprit,
                   result=f"the crash follows mod {culprit!r}: keep it disabled, update or reinstall it, "
                          "or report it to its author",
                   section=_section(folder, [culprit], f"done: {culprit} stays disabled"), undo=_undo(folder),
                   note=note, **base, warn=warn)
    keep = pool[:(len(pool) + 1) // 2]
    drop = pool[len(keep):]
    return obj(None, step=step, steps=total, candidates=len(pool), test=f"start the game with {len(keep)} of the "
               f"{len(pool)} candidates loaded", keep=keep if len(keep) <= 12 else keep[:12] + ["..."],
               disable=len(drop),
               section=_section(folder, drop, f"step {step}/{total}: {len(keep)} candidates on, {len(drop)} mods off"),
               undo=_undo(folder),
               next=f"append the result: --results {','.join(res)},crash  (or ...,ok)", note=note, **base, warn=warn)
