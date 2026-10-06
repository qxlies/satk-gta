"""Operations of ``satk.anim`` (CLI; ``mcp=False``: agents reach them through ``satk_ops``/``satk_op``):

* ``anim.list``      -> ``satk anim list <ifp|SID|img|folder> [--anim NAME] [--name TEXT]``;
* ``anim.extract``   -> ``satk anim extract <ifp|SID> [NAME ...] [--all] [--out dir|x.json|x.ifp]``;
* ``anim.write``     -> ``satk anim write <json|folder|ifp> [--out x.ifp] [--format ANP3] [--compress yes|no]``;
* ``anim.merge``     -> ``satk anim merge <base.ifp> <add.ifp ...> [--replace] [--anim NAME ...]``;
* ``anim.check``     -> ``satk anim check <ifp|SID> [--anim NAME ...] [--skin model:7|x.dff]``;
* ``anim.skeleton``  -> ``satk anim skeleton [model:7|x.dff]`` (bones the engine binds sequences to);
* ``anim.roundtrip`` -> ``satk anim roundtrip [target] [--via-json]`` (decode + encode every IFP, compare bytes);
* ``anim.mta``       -> ``satk anim mta <ifp> [--replace ped/WALK_civi=mywalk ...]`` (an MTA client resource);
* ``anim.to_blender`` / ``anim.from_blender`` -> Blender jobs (:mod:`satk.anim.blender`).

Outputs are new files under ``<work>/out/anim/`` (or an explicit writable path); game files are only read.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, ensure_writable, jpath, work
from ..core.registry import op, report_progress

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
Sev = Literal["info", "warn", "error"]


# ============================================================================ helpers


def _out_root() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "out" / "anim"


def _out_path(out: str | None, default: str) -> Path:
    """``out`` as a path: absolute (must be writable) or relative to ``<work>/out/anim``; default name there."""
    if not out:
        p = _out_root() / default
    else:
        q = Path(out)
        if q.is_absolute():
            p = q
        else:
            if ".." in q.parts or not q.parts:
                raise SatkError("BAD_PARAMS", f"--out must be a plain name, a relative path or an absolute path: {out}")
            p = _out_root() / q
    return ensure_writable(p)


def _safe(name: str) -> str:
    return _SAFE.sub("_", name.strip()).strip("_") or "anim"


def _page(rows: list, limit: int, cursor: str | None) -> tuple[list, str | None]:
    start = 0
    if cursor:
        if not str(cursor).isdigit():
            raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r} (use the 'next' value of the previous page)")
        start = int(cursor)
    lim = clamp_limit(limit)
    return rows[start:start + lim], (str(start + lim) if start + lim < len(rows) else None)


def _read(target: str, profile: str):
    from ..formats.rw import FormatError
    from .ifp import read_ifp
    from .source import load_ifp

    src = load_ifp(target, profile)
    try:
        return src, read_ifp(src.data)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot read {src.label} as an IFP: {e.msg}",
                        hint="satk formats dump " + src.label, data={"offset": e.offset}) from None


def _select(ifp, names: list[str] | None, label: str) -> list:
    """Animations matching ``names`` (case-insensitive, ``*``/``?`` globs), in file order; NOT_FOUND names one."""
    if not names:
        return list(ifp.anims)
    out, seen = [], set()
    for n in names:
        pat = n.strip().upper()
        hits = [a for a in ifp.anims if fnmatch.fnmatchcase(a.name.upper(), pat)] if any(c in pat for c in "*?[") \
            else [a for a in ifp.anims if a.name.upper() == pat][:1]
        if not hits:
            import difflib

            close = difflib.get_close_matches(n.upper(), [a.name.upper() for a in ifp.anims], n=5, cutoff=0.5)
            raise SatkError("NOT_FOUND", f"no animation {n!r} in {label}", did_you_mean=[c.lower() for c in close],
                            hint=f"satk anim list {label} --name {n[:4]}")
        for a in hits:
            if id(a) not in seen:
                seen.add(id(a))
                out.append(a)
    order = {id(a): i for i, a in enumerate(ifp.anims)}
    return sorted(out, key=lambda a: order[id(a)])


def _comp_label(anims) -> str:
    kinds = {a.compressed for a in anims if a.keys}
    return "yes" if kinds == {True} else ("no" if kinds == {False} else ("mixed" if kinds else "-"))


def _write_ifp_file(path: Path, ifp, *, fmt: str | None = None) -> dict:
    """Encode and verify before replacing any output; returns facts for the envelope."""
    from ..formats.rw import FormatError
    from ..formats.ifp import parse_ifp
    from .ifp import read_ifp, write_ifp

    try:
        data = write_ifp(ifp, fmt)
        fmt2, pack, summ = parse_ifp(data)                 # independent structural reader
        back = read_ifp(data)
    except (ValueError, FormatError) as e:
        raise SatkError("BAD_PARAMS", f"cannot encode the IFP: {e}",
                        hint="names hold 23 characters; satk anim check <file> lists the problems") from None
    verified = (fmt2 == (fmt or ifp.format) and pack == ifp.pack and len(summ) == len(ifp.anims)
                and [a.name for a in back.anims] == [a.name for a in ifp.anims]
                and [len(a.seqs) for a in back.anims] == [len(a.seqs) for a in ifp.anims]
                and [sum(len(s.keys) for s in a.seqs) for a in back.anims]
                == [sum(len(s.keys) for s in a.seqs) for a in ifp.anims])
    if not verified:
        raise SatkError("CHECK_FAILED", "encoded IFP does not match the source; output was not written")
    ensure_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, data)
    return {"path": jpath(path), "format": fmt2, "pack": pack, "anims": len(summ), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()[:16], "verified": verified}


# ============================================================================ list


def _list_many(p: Path, profile: str, q: str | None, limit: int, cursor: str | None) -> dict:
    from ..formats.ifp import parse_ifp
    from ..formats.rw import FormatError
    from .source import _rel_label, iter_ifps

    rows = []
    bad = 0
    for label, reader in iter_ifps(p, label=lambda x: _rel_label(x, profile)):
        if q and q.lower() not in label.lower():
            continue
        try:
            fmt, pack, anims = parse_ifp(reader())
        except FormatError:
            bad += 1
            rows.append([label, "bad", "-", 0])
            continue
        rows.append([label, fmt, pack, len(anims)])
    if not rows:
        raise SatkError("NOT_FOUND", f"no IFP files in {jpath(p)}", hint="satk anim list ifp:ped")
    page, nxt = _page(rows, limit, cursor)
    env = table(["file", "format", "pack", "anims"], page, total=len(rows), next=nxt)
    env["ifps"] = len(rows)
    if bad:
        env["unreadable"] = bad
    return env


@op("anim.list", summary="List the animations of an IFP (bones, keys, duration, root motion), the bones of one "
                         "animation (tag, keys, translation travel), or the IFPs inside an IMG or a folder. Target: "
                         ".ifp, <img>/<entry>.ifp, ifp:<pack>, anim:<pack>/<name>, .img or folder.",
    summary_ru="Анимации IFP (кости, ключи, длительность, движение корня), кости одной анимации или IFP внутри "
               "IMG/папки. Цель: .ifp, <img>/<entry>.ifp, ifp:<pack>, anim:<pack>/<name>, .img, папка.",
    mcp=False, group="formats",
    examples=("satk anim list ifp:ped --name walk", "satk anim list anim:ped/walk_civi",
              "satk anim list anim/anim.img", "satk anim list mymod/dance.ifp --anim dance1"))
def anim_list(target: str, anim: str | None = None, name: str | None = None, limit: int = 20, cursor: str | None = None,
              profile: str = "vanilla") -> dict:
    """List animations or bones.

    Args:
        target: .ifp file, <img>/<entry>.ifp, ifp:<pack>, anim:<pack>/<name>, an .img archive or a folder (lists
            its IFP files).
        anim: show the bones of this animation (case-insensitive).
        name: only animations (or files) whose name contains this text; * and ? are globs.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose index and game root resolve the target.
    """
    from .check import root_motion
    from .source import find_path

    s = str(target or "").strip()
    low = s.lower().replace("\\", "/")
    if not low.startswith(("ifp:", "anim:", "file:")) and ".img/" not in low:
        p = find_path(s, profile)
        if p is not None and (p.is_dir() or p.suffix.lower() == ".img"):
            return _list_many(p, profile, name, limit, cursor)
    src, ifp = _read(target, profile)
    one = anim or src.anim
    if one:
        a = _select(ifp, [one], src.label)[0]
        rows = []
        for sq in a.seqs:
            travel = "-"
            if sq.trans and sq.keys:
                fr = sq.frames()
                d = [fr[-1][2][i] - fr[0][2][i] for i in range(3)]
                travel = ",".join(f"{v:.2f}" for v in d)
            rows.append([sq.name, sq.tag, "rot+trans" if sq.trans else "rot", len(sq.keys), round(sq.end_time, 3),
                         travel])
        page, nxt = _page(rows, limit, cursor)
        env = table(["bone", "tag", "type", "keys", "end", "travel"], page, total=len(rows), next=nxt)
        env.update({"id": f"anim:{ifp.pack.lower()}/{a.name.lower()}", "file": src.label, "anim": a.name,
                    "index": next(i for i, x in enumerate(ifp.anims) if x is a), "duration": round(a.duration, 3),
                    "compressed": a.compressed, "root": root_motion(a)})
        return env
    rows = []
    for a in ifp.anims:
        if name:
            pat = name.upper()
            ok = fnmatch.fnmatchcase(a.name.upper(), pat) if any(c in pat for c in "*?[") else pat in a.name.upper()
            if not ok:
                continue
        rows.append([a.name, len(a.seqs), a.keys, round(a.duration, 3), root_motion(a)])
    page, nxt = _page(rows, limit, cursor)
    env = table(["anim", "bones", "keys", "dur", "root"], page, total=len(rows), next=nxt)
    env.update({"file": src.label, "format": ifp.format, "pack": ifp.pack, "anims": len(ifp.anims),
                "compressed": _comp_label(ifp.anims)})
    return env


# ============================================================================ extract


@op("anim.extract", summary="Extract animations of an IFP to editable JSON (seconds, quaternions, metres; one file "
                            "per animation, or one .json) or into a new IFP with only those animations. Names are "
                            "case-insensitive, * globs work; --all takes every animation. Output under "
                            "<work>/out/anim/.",
    summary_ru="Извлечь анимации IFP в редактируемый JSON (секунды, кватернионы, метры; файл на анимацию или один "
               ".json) или в новый IFP только с ними. Имена без учёта регистра, маски *; --all — все.",
    mcp=False, group="formats",
    examples=("satk anim extract ifp:ped walk_civi run_civi", "satk anim extract anim:ped/walk_civi --out walk.json",
              "satk anim extract ifp:ped \"walk_*\" --out walks.ifp", "satk anim extract ifp:bar --all"))
def anim_extract(target: str, names: list[str] | None, all: bool = False, out: str | None = None,  # noqa: A002
                 profile: str = "vanilla") -> dict:
    """Extract animations.

    Args:
        target: .ifp file, <img>/<entry>.ifp, ifp:<pack> or anim:<pack>/<name>.
        names: animation names or globs (default: the animation of an anim: SID).
        all: extract every animation of the file.
        out: a folder (one JSON per animation), a .json file (all in one) or an .ifp file; relative paths go under
            <work>/out/anim/ (default: <work>/out/anim/<pack>/).
        profile: profile whose index and game root resolve the target.
    """
    from .ifp import Ifp
    from .jsonio import anim_to_json, dumps, ifp_to_json

    src, ifp = _read(target, profile)
    want = list(names or [])
    if not want and src.anim:
        want = [src.anim]
    if not want and not all:
        some = ", ".join(a.name for a in ifp.anims[:6])
        raise SatkError("BAD_PARAMS", f"name the animations to extract (or --all); {src.label} has {len(ifp.anims)}: "
                                      f"{some}{', ...' if len(ifp.anims) > 6 else ''}",
                        hint=f"satk anim extract {target} {ifp.anims[0].name if ifp.anims else 'NAME'}")
    sel = list(ifp.anims) if all else _select(ifp, want, src.label)
    pos = {id(a): i for i, a in enumerate(ifp.anims)}
    rows = []
    env_extra: dict = {}
    low = (out or "").lower()
    if low.endswith(".ifp"):
        path = _out_path(out, "")
        sub = Ifp(ifp.format, ifp.pack, sel, ifp.raw_pack)
        env_extra = _write_ifp_file(path, sub)
        for a in sel:
            rows.append([a.name, len(a.seqs), a.keys, round(a.duration, 3), env_extra["path"]])
    elif low.endswith(".json"):
        path = _out_path(out, "")
        doc = ifp_to_json(ifp, sel, source=src.label)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, dumps(doc))
        env_extra = {"path": jpath(path)}
        for a in sel:
            rows.append([a.name, len(a.seqs), a.keys, round(a.duration, 3), jpath(path)])
    else:
        d = _out_path(out, _safe(ifp.pack.lower() or src.stem.lower()))
        d.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        for i, a in enumerate(sel):
            report_progress(i, len(sel), a.name)
            base = _safe(a.name.lower())
            stem, k = base, 2
            while stem in used:
                stem, k = f"{base}_{k}", k + 1
            used.add(stem)
            doc = {"satk": "anim/1", "format": ifp.format, "pack": ifp.pack, "source": src.label}
            if ifp.raw_pack is not None:
                doc["pack_raw"] = ifp.raw_pack.hex()
            doc["anims"] = [anim_to_json(a, pos[id(a)])]
            p = d / f"{stem}.json"
            atomic_write(p, dumps(doc))
            rows.append([a.name, len(a.seqs), a.keys, round(a.duration, 3), jpath(p)])
        env_extra = {"dir": jpath(d)}
    env = table(["anim", "bones", "keys", "dur", "file"], rows[:clamp_limit(20)], total=len(rows))
    env.update({"source": src.label, "format": ifp.format, "pack": ifp.pack, "extracted": len(rows)})
    env.update(env_extra)
    return env


# ============================================================================ write


def _load_json_docs(source: str, profile: str) -> tuple[list, str]:
    from .source import find_path

    p = find_path(source, profile)
    if p is None:
        raise SatkError("NOT_FOUND", f"no such file or folder: {source}",
                        hint="a .json from satk anim extract, a folder of them, or an .ifp")
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    if not files:
        raise SatkError("NOT_FOUND", f"no .json files in {jpath(p)}", hint="satk anim extract ifp:ped walk_civi")
    docs = []
    for f in files:
        try:
            docs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            raise SatkError("BAD_PARAMS", f"{jpath(f)} is not valid JSON: {e}") from None
    return docs, jpath(p)


@op("anim.write", summary="Write an IFP (ANP3 by default, also ANP2/ANPK) from JSON animations (a .json or a folder "
                          "of them from anim extract) or re-encode/convert an IFP. Key frames stay compressed or float "
                          "as the source had them (--compress yes|no forces). Re-read to verify; output under "
                          "<work>/out/anim/.",
    summary_ru="Собрать IFP (ANP3 по умолчанию, также ANP2/ANPK) из JSON-анимаций (.json или папка из anim extract) "
               "или перекодировать IFP. Ключи сжатые/float как в источнике (--compress yes|no меняет).",
    mcp=False, group="formats",
    examples=("satk anim write ped --out ped_new.ifp", "satk anim write walk.json --out mywalk.ifp --pack mywalk",
              "satk anim write cuts/intro.ifp --format ANP3 --compress yes"))
def anim_write(source: str, out: str | None = None, format: Literal["ANP3", "ANP2", "ANPK"] | None = None,  # noqa: A002
               pack: str | None = None, compress: Literal["keep", "yes", "no"] = "keep",
               profile: str = "vanilla") -> dict:
    """Write an IFP.

    Args:
        source: a .json file (anim/1), a folder of .json files (ordered by their index, then name), or an .ifp
            to re-encode or convert.
        out: output .ifp; relative paths go under <work>/out/anim/ (default: <work>/out/anim/<pack>.ifp).
        format: ANP3 (SA), ANP2 or ANPK (default: the source's format, or ANP3 for JSON without a format).
        pack: pack (block) name stored in the file, max 23 characters (default: from the source).
        compress: keep (as the source), yes (int16 key frames, the SA style) or no (floats).
        profile: profile whose game root resolves relative sources.
    """
    from .ifp import Ifp, convert_anim
    from .jsonio import JsonError, json_to_ifp

    low = str(source).lower()
    is_ifp = low.endswith(".ifp") or low.startswith(("ifp:", "file:")) or ".img/" in low
    if is_ifp:
        src, ifp = _read(source, profile)
        label = src.label
    else:
        docs, label = _load_json_docs(source, profile)
        try:
            ifp = json_to_ifp(docs if len(docs) > 1 else docs[0], pack=pack)
        except JsonError as e:
            raise SatkError("BAD_PARAMS", f"{label}: {e}", hint="keys are [t, qx, qy, qz, qw] or "
                                                              "[t, qx, qy, qz, qw, tx, ty, tz]; see docs/en/anim.md") from None
    fmt = (format or ifp.format).upper()
    comp = {"keep": None, "yes": True, "no": False}[compress]
    if comp is True and fmt == "ANPK":
        raise SatkError("BAD_PARAMS", "ANPK key frames are always floats", hint="--format ANP3 --compress yes")
    notes: list[str] = []
    anims = []
    for a in ifp.anims:
        try:
            na, n = convert_anim(a, ifp.format, fmt, comp)
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"{a.name}: {e}", hint="--compress no keeps float key frames") from None
        anims.append(na)
        notes += n
    new = Ifp(fmt, pack or ifp.pack, anims, ifp.raw_pack if (pack is None and fmt == ifp.format) else None)
    if len(new.pack.encode("latin-1", "replace")) > 23 and fmt != "ANPK":
        raise SatkError("BAD_PARAMS", f"pack name {new.pack!r} is longer than 23 characters", hint="--pack NAME")
    path = _out_path(out, f"{_safe(new.pack.lower())}.ifp")
    if path.suffix.lower() != ".ifp":
        path = path / f"{_safe(new.pack.lower())}.ifp"
    facts = _write_ifp_file(path, new)
    env = obj(facts.pop("path"), **facts, source=label, compressed=_comp_label(new.anims))
    if notes:
        env["warn"] = [f"NOTE: {n}" for n in notes[:20]]
    return env


# ============================================================================ merge


@op("anim.merge", summary="Merge animations of other IFPs into a base IFP (ped.ifp style): new names are appended, "
                          "same names (case-insensitive) are skipped or, with --replace, replaced in place. The base "
                          "keeps its format and pack name; output under <work>/out/anim/.",
    summary_ru="Добавить анимации других IFP в базовый (как в ped.ifp): новые имена дописываются, совпадающие "
               "пропускаются или заменяются на месте (--replace). Формат и имя пакета — от базы.",
    mcp=False, group="formats",
    examples=("satk anim merge ifp:ped mymod/dance.ifp", "satk anim merge ifp:ped mywalk.ifp --replace",
              "satk anim merge ifp:ped mymod/pack.ifp --anim \"dance*\" --out ped_dance.ifp"))
def anim_merge(base: str, add: list[str], replace: bool = False, anim: list[str] | None = None,
               out: str | None = None, compress: Literal["keep", "yes", "no"] = "keep",
               profile: str = "vanilla") -> dict:
    """Merge IFPs.

    Args:
        base: the IFP to extend (.ifp, <img>/<entry>.ifp, ifp:<pack>).
        add: IFPs (or JSON files from anim extract) whose animations are added.
        replace: replace animations of the base that have the same name (default: keep the base's and skip).
        anim: only these animations of the added files (names or globs).
        out: output .ifp; relative paths go under <work>/out/anim/ (default: <work>/out/anim/<base name>.ifp).
        compress: keep, yes or no for the added animations' key frames (keep = as they come).
        profile: profile whose index and game root resolve the targets.
    """
    from .ifp import Ifp, convert_anim, upper_key
    from .jsonio import JsonError, json_to_ifp

    bsrc, bifp = _read(base, profile)
    comp = {"keep": None, "yes": True, "no": False}[compress]
    anims = list(bifp.anims)
    index = {upper_key(a.name): i for i, a in reversed(list(enumerate(anims)))}
    rows: list[list] = []
    notes: list[str] = []
    counts = {"added": 0, "replaced": 0, "skipped": 0}
    for item in add:
        if str(item).lower().endswith(".json"):
            docs, label = _load_json_docs(item, profile)
            try:
                aifp = json_to_ifp(docs[0])
            except JsonError as e:
                raise SatkError("BAD_PARAMS", f"{label}: {e}") from None
        else:
            asrc, aifp = _read(item, profile)
            label = asrc.label
        for a in _select(aifp, anim, label) if anim else aifp.anims:
            try:
                na, n = convert_anim(a, aifp.format, bifp.format, comp)
            except ValueError as e:
                raise SatkError("BAD_PARAMS", f"{label}: {a.name}: {e}", hint="--compress no") from None
            notes += n
            k = upper_key(a.name)
            if k in index:
                if not replace:
                    counts["skipped"] += 1
                    rows.append([a.name, "skipped", label])
                    continue
                anims[index[k]] = na
                counts["replaced"] += 1
                rows.append([a.name, "replaced", label])
            else:
                index[k] = len(anims)
                anims.append(na)
                counts["added"] += 1
                rows.append([a.name, "added", label])
    if not rows:
        raise SatkError("NOT_FOUND", "nothing to merge: the added files have no animations")
    path = _out_path(out, f"{_safe(bsrc.stem.lower())}.ifp")
    new = Ifp(bifp.format, bifp.pack, anims, bifp.raw_pack)
    facts = _write_ifp_file(path, new)
    warn = [f"NOTE: {n}" for n in notes[:10]]
    if counts["skipped"]:
        warn.append(f"SKIPPED: {counts['skipped']} animation(s) already in the base (--replace replaces them)")
    lim = clamp_limit(20)
    env = table(["anim", "action", "from"], rows[:lim], total=len(rows), warn=warn)
    env.update(facts)
    env["base"] = bsrc.label
    env.update({k: v for k, v in counts.items() if v})
    return env


# ============================================================================ check / skeleton


def _skeleton(skin: str | None, profile: str):
    from ..formats.rw import FormatError
    from .skeleton import ped_skeleton, read_skeleton
    from .source import load_dff

    if not skin or skin.strip().lower() in ("ped", "sa-ped"):
        return ped_skeleton(), "sa-ped"
    label, name, data = load_dff(skin, profile)
    try:
        return read_skeleton(data, name), label
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot read the frames of {label}: {e.msg}") from None


@op("anim.check", summary="Check IFP animations against a skeleton (default: the SA ped skeleton; --skin a model or "
                          ".dff): bone tags and names the engine cannot bind, duplicate bones, root motion left on "
                          "the pelvis, key order, non-unit rotations, name lengths, mixed compression.",
    summary_ru="Проверка анимаций IFP по скелету (по умолчанию скелет педа SA; --skin модель или .dff): теги и имена "
               "костей, которые движок не свяжет, дубли, движение на тазе вместо корня, порядок ключей, длины имён.",
    mcp=False, group="formats",
    examples=("satk anim check mymod/dance.ifp", "satk anim check mymod/dance.ifp --loader mta", "satk anim check ifp:ped --anim \"walk_*\" --sev info",
              "satk anim check mydoor.ifp --skin mymod/door.dff", "satk anim check out.ifp --fail-on error"))
def anim_check(target: str, anim: list[str] | None = None, skin: str | None = None,
               loader: Literal["game", "mta"] = "game", sev: Sev = "warn",
               fail_on: Literal["never", "warn", "error"] = "never", limit: int = 20, cursor: str | None = None,
               profile: str = "vanilla") -> dict:
    """Check animations.

    Args:
        target: .ifp file, <img>/<entry>.ifp, ifp:<pack> or anim:<pack>/<name>.
        anim: only these animations (names or globs).
        skin: skeleton to check against: ped (default, the SA ped skeleton), model:<id|name>, dff:<name> or a .dff
            path (skinned: bone ids; plain models: frame names).
        loader: game (the game's own loader: ped.ifp, anim.img, also in MTA) or mta (MTA's engineLoadIFP, which maps
            untagged names to bone ids with its own table and keeps only its 64 bone ids).
        sev: lowest severity shown in the table (the summary counts all): info|warn|error.
        fail_on: return CHECK_FAILED when a finding of this severity or higher exists.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose index and game root resolve the targets.
    """
    from .check import SEVERITIES, check_ifp

    src, ifp = _read(target, profile)
    names = list(anim or [])
    if not names and src.anim:
        names = [src.anim]
    sel = _select(ifp, names, src.label) if names else None
    skel, slabel = _skeleton(skin, profile)
    fs = check_ifp(ifp, skel, sel, loader)
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    fs.sort(key=lambda f: (-rank[f.sev], f.check, f.anim.upper()))
    summary: dict[str, int] = {}
    by_check: dict[str, int] = {}
    for f in fs:
        summary[f.sev] = summary.get(f.sev, 0) + 1
        by_check[f.check] = by_check.get(f.check, 0) + 1
    shown = [[f.anim, f.sev, f.check, f.msg] for f in fs if rank[f.sev] >= rank[sev]]
    page, nxt = _page(shown, limit, cursor)
    env = table(["anim", "sev", "check", "msg"], page, total=len(shown), next=nxt)
    env.update({"file": src.label, "skeleton": slabel, "loader": loader,
                "anims": len(sel) if sel is not None else len(ifp.anims)})
    if summary:
        env["summary"] = {k: summary[k] for k in ("error", "warn", "info") if k in summary}
    if by_check:
        env["by_check"] = dict(sorted(by_check.items(), key=lambda kv: -kv[1])[:20])
    if fail_on != "never":
        bad = [[f.anim, f.sev, f.check, f.msg] for f in fs if rank[f.sev] >= rank[fail_on]]
        if bad:
            raise SatkError("CHECK_FAILED", f"{len(bad)} animation finding(s) at {fail_on} or above in {src.label}",
                            hint=f"satk anim check {target} --sev {fail_on}",
                            data={"cols": ["anim", "sev", "check", "msg"], "rows": bad[:clamp_limit(limit)]})
    return env


@op("anim.skeleton", summary="Bones animations bind to: the SA ped skeleton (default) or a model's skeleton (HAnim "
                             "bone ids of a skinned DFF, frame names of a plain one), with the engine bone name an "
                             "untagged IFP sequence must use.",
    summary_ru="Кости, к которым привязываются анимации: скелет педа SA (по умолчанию) или модели (id костей HAnim "
               "или имена фреймов), с именем кости движка для последовательностей без тега.",
    mcp=False, group="formats",
    examples=("satk anim skeleton", "satk anim skeleton model:7", "satk anim skeleton mymod/door.dff"))
def anim_skeleton(skin: str | None, limit: int = 50, profile: str = "vanilla") -> dict:
    """Show a skeleton.

    Args:
        skin: ped (default, the SA ped skeleton), model:<id|name>, dff:<name> or a .dff path.
        limit: rows to show (max 500).
        profile: profile whose index and game root resolve the model.
    """
    skel, label = _skeleton(skin, profile)
    rows = []
    for b in skel.bones:
        parent = skel.bones[b.parent].name.strip() if 0 <= b.parent < len(skel.bones) else "-"
        if skel.skinned:
            rows.append([b.idx, b.id, b.name, skel.engine_names.get(b.id, "-"), parent])
        else:
            rows.append([b.idx, "-", b.name, b.name, parent])
    lim = clamp_limit(limit)
    env = table(["idx", "id", "name", "engine_name", "parent"], rows[:lim], total=len(rows))
    env.update({"skeleton": label, "skinned": skel.skinned, "bones": len(skel.bones)})
    return env


# ============================================================================ roundtrip


@op("anim.roundtrip", summary="Decode every IFP of a target (default: the whole game folder of the profile, IMG "
                              "entries included) with all key frames and encode it again; reports files that do not "
                              "come back byte for byte (--via-json goes through the JSON form too).",
    summary_ru="Декодировать каждый IFP цели (по умолчанию вся папка игры профиля, вместе с IMG) и закодировать "
               "снова; показывает файлы, которые не совпали байт в байт (--via-json — через JSON).",
    mcp=False, group="formats", long_running=True,
    examples=("satk anim roundtrip", "satk anim roundtrip anim/anim.img --via-json", "satk anim roundtrip mymod"))
def anim_roundtrip(target: str | None, via_json: bool = False, limit: int = 20,
                   profile: str = "vanilla") -> dict:
    """Round-trip IFPs.

    Args:
        target: a folder, an .img or an .ifp (default: the profile's game folder).
        via_json: also go through the JSON form (slower: floats are printed and parsed).
        limit: rows of differing files to show (max 500).
        profile: profile whose game root is the default target.
    """
    from ..core.paths import profile_root
    from ..formats.rw import FormatError
    from .ifp import read_ifp, write_ifp
    from .jsonio import ifp_to_json, json_to_ifp
    from .source import _rel_label, find_path, iter_ifps

    t0 = time.monotonic()
    if target:
        p = find_path(target, profile)
        if p is None:
            raise SatkError("NOT_FOUND", f"no such file or folder: {target}")
    else:
        p = Path(profile_root(profile))
    rows: list[list] = []
    n = same = keys = 0
    fmts: dict[str, int] = {}
    for label, reader in iter_ifps(p, label=lambda x: _rel_label(x, profile)):
        data = reader()
        n += 1
        report_progress(n, None, label)
        try:
            ifp = read_ifp(data)
        except FormatError as e:
            rows.append([label, "-", "unreadable", e.msg])
            continue
        fmts[ifp.format] = fmts.get(ifp.format, 0) + 1
        keys += sum(len(s.keys) for a in ifp.anims for s in a.seqs)
        out = write_ifp(ifp)
        orig = data[:ifp.end]
        how = "binary"
        if out == orig and via_json:
            how = "json"
            out = write_ifp(json_to_ifp(json.loads(json.dumps(ifp_to_json(ifp)))))
        if out == orig:
            same += 1
            continue
        i = next((i for i, (x, y) in enumerate(zip(out, orig)) if x != y), min(len(out), len(orig)))
        rows.append([label, ifp.format, f"{how}: differs", f"first difference at byte {i} of {len(orig)}"])
    if not n:
        raise SatkError("NOT_FOUND", f"no IFP files in {jpath(p)}")
    lim = clamp_limit(limit)
    env = table(["file", "format", "result", "detail"], rows[:lim], total=len(rows))
    env.update({"root": jpath(p), "files": n, "identical": same, "keys": keys, "formats": fmts,
                "via_json": via_json, "seconds": round(time.monotonic() - t0, 1)})
    return env


# ============================================================================ Blender


def _skin_plan(skin: str, txd: str | None, profile: str) -> dict:
    """Plan for ``satk_blender.importer.import_model``: a model SID via the index, or a loose .dff (+ .txd)."""
    from .source import find_path

    low = skin.strip().lower()
    if low.endswith(".dff"):
        p = find_path(skin, profile)
        if p is None or not p.is_file():
            raise SatkError("NOT_FOUND", f"no such DFF: {skin}", hint="a skinned ped .dff, or model:<id|name>")
        txds = []
        tp = find_path(txd, profile) if txd else p.with_suffix(".txd")
        if tp is not None and tp.is_file():
            txds.append(jpath(tp))
        return {"model": {"sid": jpath(p), "id": -1, "name": p.stem, "sec": "peds", "dff": jpath(p), "txd": txds,
                          "txd_names": [Path(t).stem.lower() for t in txds]}, "warnings": []}
    from ..blender.resolve import plan_model

    plan = plan_model(skin, profile=profile)
    if plan["model"].get("sec") not in ("peds", None):
        plan.setdefault("warnings", []).append(f"NOT_A_PED: {plan['model']['sid']} is a {plan['model'].get('sec')} "
                                               "model; IFP animations need a skinned model with bones")
    return plan


@op("anim.to_blender", summary="Apply IFP animations to a skinned ped in headless Blender: the skin (default "
                               "model:7, or any ped / .dff) is imported with DragonFF and every animation becomes an "
                               "action on its armature; saves a .blend, optional 4-frame render, --verify exports "
                               "back and compares with the source.",
    summary_ru="Наложить анимации IFP на педа в Blender (фоново): скин (model:7 по умолчанию, любой пед или .dff) "
               "импортируется через DragonFF, каждая анимация — action на арматуре; .blend, рендер 4 кадров, "
               "--verify сверяет экспорт с исходником.",
    mcp=False, group="blender", long_running=True,
    examples=("satk anim to-blender ifp:ped walk_civi --verify --render", "satk anim to-blender anim:ped/run_civi",
              "satk anim to-blender mymod/dance.ifp dance1 --skin mymod/myped.dff"))
def anim_to_blender(target: str, names: list[str] | None, skin: str = "model:7", txd: str | None = None,
                    all: bool = False, verify: bool = False, render: bool = False, fps: int | None = None,  # noqa: A002
                    size: int = 320, timeout: float = 600.0, profile: str = "vanilla") -> dict:
    """Animations onto a ped in Blender.

    Args:
        target: .ifp file, <img>/<entry>.ifp, ifp:<pack> or anim:<pack>/<name>.
        names: animation names or globs (default: the animation of an anim: SID).
        skin: the skinned model: model:<id|name> (default model:7, male01) or a .dff file (its .txd next to it).
        txd: texture dictionary for a .dff skin (default: same name, .txd).
        all: every animation of the file (at most 500).
        verify: export the actions back in the same Blender session and compare with the source key frames.
        render: render 4 frames of the first animation into one contact sheet (PNG path).
        fps: scene frame rate (default: 30 when every key sits on a 1/30 s frame, else 60).
        size: render size of one frame in pixels (64..1024).
        timeout: seconds before Blender is stopped.
        profile: profile whose index and game root resolve the targets.
    """
    from .blender import compare, run_anim_job
    from .jsonio import JsonError, json_to_ifp

    if not 64 <= size <= 1024:
        raise SatkError("BAD_PARAMS", f"--size must be in 64..1024, got {size}")
    if fps is not None and not 1 <= fps <= 240:
        raise SatkError("BAD_PARAMS", f"--fps must be in 1..240, got {fps}")
    src, ifp = _read(target, profile)
    want = list(names or []) or ([src.anim] if src.anim else [])
    if not want and not all:
        raise SatkError("BAD_PARAMS", f"name the animations to apply (or --all); {src.label} has {len(ifp.anims)}",
                        hint=f"satk anim list {target}")
    sel = list(ifp.anims) if all else _select(ifp, want, src.label)
    if not sel:
        raise SatkError("BAD_PARAMS", f"{src.label} has no animations to apply")
    if len(sel) > 500:
        raise SatkError("BAD_PARAMS", f"{len(sel)} animations: at most 500 per job", hint="name fewer animations")
    pos = {id(a): i for i, a in enumerate(ifp.anims)}
    plan = _skin_plan(skin, txd, profile)
    lead = _select(ifp, want[:1], src.label)[0] if want else sel[0]      # the one shown first and rendered
    args = {"plan": plan, "indices": [pos[id(a)] for a in sel], "lead": sel.index(lead), "label": src.label,
            "roundtrip": verify, "render": render, "fps": fps, "size": size, "save": True}
    resp = run_anim_job("to_blender", args, profile=profile, files={"source.ifp": src.data[:ifp.end]},
                        timeout=timeout)
    st = resp.get("stats") or {}
    files = resp.get("files") or {}
    rows = [list(r) for r in st.get("actions") or []]
    cols = ["anim", "action", "seqs", "bound", "unbound", "frames"]
    env_extra: dict = {}
    if verify:
        try:
            back = json_to_ifp(json.loads(Path(files["roundtrip"]).read_text(encoding="utf-8")))
        except (KeyError, OSError, ValueError, JsonError) as e:
            raise SatkError("EXTERNAL_TOOL", f"the Blender round trip wrote no usable JSON: {e}",
                            data={"job": resp.get("job")}) from None
        cols += ["rot_deg", "trans_m", "exact"]
        for row in rows:
            row += ["-", "-", False]
        ok = len(rows) == len(sel) == len(back.anims)
        if not ok:
            env_extra["problems"] = [f"animation count: expected {len(sel)}, Blender reported {len(rows)} "
                                     f"actions and exported {len(back.anims)} animations"]
        worst = {"rot_deg": 0.0, "trans_m": 0.0, "time_s": 0.0}
        n_exact = 0
        for i, (a, b) in enumerate(zip(sel, back.anims)):
            c = compare(a, b)
            if i < len(rows):
                rows[i][-3:] = [c["rot_deg"], c["trans_m"], c["exact"]]
            n_exact += bool(c["exact"])
            for k in worst:
                worst[k] = max(worst[k], c[k])
            ok = ok and c["within_tolerance"]
            if c.get("problems"):
                env_extra.setdefault("problems", []).extend(f"{a.name}: {p}" for p in c["problems"])
        env_extra.update({"verified": ok, "exact": f"{n_exact}/{len(sel)}", "max_error": worst})
    env = table(cols, rows[:clamp_limit(20)], total=len(rows), warn=(resp.get("warnings") or [])[:20])
    env.update({"source": src.label, "skin": st.get("skin"), "armature": st.get("armature"), "fps": st.get("fps"),
                "blend": files.get("blend"), "png": files.get("png"), "job": resp.get("job"),
                "seconds": st.get("seconds")})
    env.update(env_extra)
    return {k: v for k, v in env.items() if v is not None}


def _blend_path(blend: str, profile: str = "vanilla") -> Path:
    from .source import find_path

    p = find_path(blend, profile)
    if p is None and re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", blend.strip()):
        q = work("blender", "jobs", blend.strip(), "scene.blend")
        p = q if q.is_file() else None
    if p is None or not p.is_file() or p.suffix.lower() != ".blend":
        raise SatkError("NOT_FOUND", f"no such .blend: {blend}",
                        hint="a .blend path, or the job id printed by satk anim to-blender")
    return p


@op("anim.from_blender", summary="Export Blender actions on a ped armature (a .blend from anim to-blender or your "
                                 "own DragonFF import) to an IFP: imported actions keep their sequence order, new "
                                 "ones get one sequence per animated bone (tag = bone id). Output under "
                                 "<work>/out/anim/.",
    summary_ru="Экспорт action арматуры педа из .blend (из anim to-blender или своего импорта DragonFF) в IFP: "
               "импортированные сохраняют порядок, новые — по последовательности на кость (тег = id кости).",
    mcp=False, group="blender", long_running=True,
    examples=("satk anim from-blender 20261005-120000-ab12 --out mywalk.ifp",
              "satk anim from-blender mymod/dance.blend --action dance1 --pack dance --compress yes"))
def anim_from_blender(blend: str, action: list[str] | None = None, armature: str | None = None,
                      out: str | None = None, pack: str | None = None,
                      format: Literal["ANP3", "ANP2", "ANPK"] = "ANP3",  # noqa: A002
                      compress: Literal["keep", "yes", "no"] = "keep", fps: int | None = None, bake: bool = False,
                      fresh: bool = False, timeout: float = 600.0, profile: str = "vanilla") -> dict:
    """Actions of a .blend to an IFP.

    Args:
        blend: a .blend file, or the job id printed by satk anim to-blender (its scene.blend).
        action: action names to export (default: the actions satk created, else the armature's active one).
        armature: armature object to read (default: the one with the most bones).
        out: output .ifp; relative paths go under <work>/out/anim/ (default: <work>/out/anim/<pack>.ifp).
        pack: pack (block) name stored in the file (default: the imported one, else custom).
        format: ANP3 (SA, default), ANP2 or ANPK.
        compress: keep (as imported; new actions: compressed), yes or no.
        fps: frame rate of the action's frames (default: the stored one, else the scene's).
        bake: sample every frame of the action's range instead of its key frames (constraints, IK, drivers).
        fresh: ignore what to-blender stored on the action: one sequence per animated bone in armature order,
            named after the bone, tagged with its bone id.
        timeout: seconds before Blender is stopped.
        profile: profile used for relative paths.
    """
    from .blender import run_anim_job
    from .ifp import Ifp, convert_anim
    from .jsonio import JsonError, json_to_ifp

    if fps is not None and not 1 <= fps <= 240:
        raise SatkError("BAD_PARAMS", f"--fps must be in 1..240, got {fps}")
    if format == "ANPK" and compress == "yes":
        raise SatkError("BAD_PARAMS", "ANPK key frames are always floats", hint="--format ANP3 --compress yes")
    p = _blend_path(blend, profile)
    comp = {"keep": None, "yes": True, "no": False}[compress]
    if format == "ANPK":
        comp = False
    args = {"actions": list(action or []), "armature": armature, "fps": fps, "bake": bake, "fresh": fresh,
            "compressed": comp}
    resp = run_anim_job("from_blender", args, blend=p, profile=profile, timeout=timeout)
    files = resp.get("files") or {}
    try:
        ifp = json_to_ifp(json.loads(Path(files["json"]).read_text(encoding="utf-8")), pack=pack)
    except (KeyError, OSError, ValueError) as e:
        bad = isinstance(e, JsonError)
        raise SatkError("BAD_PARAMS" if bad else "EXTERNAL_TOOL",
                        f"cannot build the IFP from the exported actions: {e}",
                        hint="--compress no writes float key frames" if bad else None,
                        data={"job": resp.get("job")}) from None
    if not ifp.anims:
        raise SatkError("NOT_FOUND", "the selected Blender actions exported no animations",
                        hint="select an action with bone key frames")
    anims, notes = [], []
    for a in ifp.anims:
        try:
            converted, changes = convert_anim(a, ifp.format, format, comp)
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"cannot encode {a.name}: {e}", hint="--compress no") from None
        anims.append(converted)
        notes.extend(f"NOTE: {n}" for n in changes)
    new = Ifp(format, ifp.pack, anims, ifp.raw_pack if format == ifp.format else None)
    path = _out_path(out, f"{_safe(new.pack.lower())}.ifp")
    facts = _write_ifp_file(path, new)
    rows = [list(r) for r in (resp.get("stats") or {}).get("actions") or []]
    env = table(["anim", "action", "bones", "keys"], rows[:clamp_limit(20)], total=len(rows),
                warn=((resp.get("warnings") or []) + notes)[:20])
    env.update(facts)
    env.update({"blend": jpath(p), "job": resp.get("job"), "json": files.get("json")})
    return env


# ============================================================================ MTA resource

_RES_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _game_anims(block: str, profile: str) -> set[str] | None:
    """Upper-case animation names of a game block (its IFP via the index), ``None`` when it cannot be read."""
    try:
        _src, gifp = _read(f"ifp:{block}", profile)
    except SatkError:
        return None
    return {a.name.upper() for a in gifp.anims}


@op("anim.mta", summary="Make an MTA:SA client resource from an IFP: meta.xml, client.lua and the IFP. The script "
                        "loads it with engineLoadIFP and replaces game animations of players and peds with "
                        "engineReplaceAnimation (--replace ped/WALK_civi=mywalk), checked by MTA loader rules. "
                        "Output under <work>/out/anim/mta/<name>/.",
    summary_ru="Клиентский ресурс MTA:SA из IFP: meta.xml, client.lua и сам IFP. Скрипт грузит его через "
               "engineLoadIFP и подменяет анимации игроков и педов через engineReplaceAnimation; проверка по "
               "правилам загрузчика MTA.",
    mcp=False, group="formats",
    examples=("satk anim mta mywalk.ifp --replace ped/WALK_civi=WALK_civi",
              "satk anim mta mymod/dance.ifp --block dance --name dance_anims",
              "satk anim mta ifp:ped --replace WALK_civi=WALK_old"))
def anim_mta(target: str, replace: list[str] | None = None, block: str | None = None, name: str | None = None,
             profile: str = "vanilla") -> dict:
    """An MTA resource for an IFP.

    Args:
        target: .ifp file, <img>/<entry>.ifp or ifp:<pack> (ANP3, ANP2 or ANPK: MTA reads all three).
        replace: game animations to replace: [block/]game_anim[=custom_anim] (block defaults to ped, the custom
            animation to the same name), e.g. ped/WALK_civi=mywalk.
        block: block name engineLoadIFP registers (default: the file name); must differ from other loaded IFPs.
        name: resource (folder) name, letters, digits, _ and - (default: satk_anim_<block>).
        profile: profile whose index and game root resolve the target and the game blocks.
    """
    from .check import check_ifp
    from .mta import client_lua, meta_xml, parse_replace
    from .skeleton import ped_skeleton

    src, ifp = _read(target, profile)
    blk = (block or _safe(src.stem.lower())).strip()
    if not _RES_NAME.match(blk):
        raise SatkError("BAD_PARAMS", f"bad block name {blk!r}: 1-64 letters, digits, _ or -", hint="--block mywalk")
    res = name or f"satk_anim_{blk}"
    if not _RES_NAME.match(res):
        raise SatkError("BAD_PARAMS", f"bad resource name {res!r}: 1-64 letters, digits, _ or -",
                        hint="--name my_anims")
    reps = []
    for item in replace or []:
        try:
            reps.append(parse_replace(item))
        except ValueError as e:
            raise SatkError("BAD_PARAMS", str(e), hint="--replace ped/WALK_civi=mywalk") from None
    customs = {a.name.upper(): a for a in ifp.anims}
    rows = []
    warn: list[str] = []
    game: dict[str, set[str] | None] = {}
    used = []
    for r in reps:
        a = customs.get(r.custom.upper())
        if a is None:
            import difflib

            close = difflib.get_close_matches(r.custom.upper(), list(customs), n=5, cutoff=0.5)
            raise SatkError("NOT_FOUND", f"no animation {r.custom!r} in {src.label}",
                            did_you_mean=[c.lower() for c in close], hint=f"satk anim list {target}")
        if all(x is not a for x in used):
            used.append(a)
        if r.block.lower() not in game:
            game[r.block.lower()] = _game_anims(r.block, profile)
        known = game[r.block.lower()]
        status = "unchecked" if known is None else ("ok" if r.anim.upper() in known else "not in game")
        if status == "not in game":
            warn.append(f"NOT_IN_GAME: block {r.block} has no animation {r.anim} (engineReplaceAnimation fails)")
        elif status == "unchecked":
            warn.append(f"UNCHECKED: block {r.block} not found in the {profile} index")
        rows.append([r.block, r.anim, a.name, status])
    findings = check_ifp(ifp, ped_skeleton(), used or None, "mta")
    summary: dict[str, int] = {}
    for f in findings:
        summary[f.sev] = summary.get(f.sev, 0) + 1
    if summary.get("error") or summary.get("warn"):
        warn.append(f"CHECK: {summary.get('error', 0)} error(s), {summary.get('warn', 0)} warning(s) with MTA's "
                    f"loader: satk anim check {target} --loader mta")
    d = _out_path(None, f"mta/{res}")
    d.mkdir(parents=True, exist_ok=True)
    ifp_file = f"{blk}.ifp"
    atomic_write(d / ifp_file, src.data[:ifp.end])
    atomic_write(d / "client.lua", client_lua(ifp_file, blk, reps, [a.name for a in ifp.anims]))
    atomic_write(d / "meta.xml", meta_xml(res, ifp_file, f"IFP {src.stem} as animation block {blk} (satk anim mta)"))
    env = table(["block", "anim", "custom", "game"], rows[:clamp_limit(50)], total=len(rows), warn=warn[:20])
    env.update({"dir": jpath(d), "resource": res, "block": blk, "ifp": ifp_file, "anims": len(ifp.anims),
                "files": ["meta.xml", "client.lua", ifp_file]})
    if summary:
        env["check"] = {k: summary[k] for k in ("error", "warn", "info") if k in summary}
    env["install"] = f"copy {jpath(d)} into <server>/mods/deathmatch/resources/ and run: start {res}"
    return env
