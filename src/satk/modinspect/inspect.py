"""``mod inspect``: what one mod changes in the game, as Mod Loader would load it.

Rows ``kind, target, change, by, detail``:

* streamed files (DFF, TXD, COL, IFP, binary IPL, RRR, SCM, nodes): ``replace`` when the game's IMG
  archives have that name, else ``add``; an ``.img`` in the mod replaces the game archive of the
  same name or is added by an ``IMG`` line;
* data files: per record - ``modify`` (with ``field old->new``), ``add``, ``remove`` (Mod Loader drops
  game lines the mod's file lacks); IDE records: ``new-id``, ``override-id`` (the ID belongs to
  another model in the game), ``modify``; files whose merge rules are not ported get one line-diff row;
* readme lines Mod Loader would merge (handling, carcols, vehicles/peds/veh_mods IDE, gta.dat);
* text IPL/ZON (``replace``), ASI/CLEO (``load``), texts, FX, sprites, movies;
* files without a handler: one ``ignored`` row per extension.

Stdlib only.
"""

from __future__ import annotations

from collections import Counter

from ..core.errors import SatkError
from ..core.paths import open_ro

from .base import VANILLA_MAX_ID
from .fields import diff_detail, short
from .merge import merge
from .traits import IdeTrait, LineTrait, Rec, recs_equal
from .world import Loaded, World

__all__ = ["inspect_world", "COLS", "CHANGES"]

#: Bytes the identical-content check may read per inspect (mod + game copies).
_COMPARE_BUDGET = 256 << 20

COLS = ["kind", "target", "change", "by", "detail"]
CHANGES = ("replace", "add", "modify", "remove", "new-id", "override-id", "load", "ignored")
_STREAM_KINDS = frozenset({"dff", "txd", "col", "ifp", "ipl-bin", "rrr", "scm", "nodes"})


def _sid(kind: str, name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    if kind in ("dff", "txd", "col"):
        return f"{kind}:{stem}"
    return f"file:{name}"


class _Same:
    """Counts mod files whose bytes equal the game's copy (they change nothing and get no row)."""

    def __init__(self) -> None:
        self.budget = _COMPARE_BUDGET
        self.count = 0
        self.skipped = 0   # not compared: the byte budget ran out

    def stream(self, world: World, f) -> bool:
        hit = world.base.img_names.get(f.lname)
        if hit is None or f.reader is None:
            return False
        size = hit[3]
        if not size - 2048 < f.size <= size:
            return False                       # different sector count: certainly different bytes
        if self.budget < size + f.size:
            self.skipped += 1
            return False
        self.budget -= size + f.size
        try:
            a, b = f.read(limit=size), world.base.read_entry(f.lname)
        except (SatkError, OSError):
            return False
        if b is not None and a == b[:len(a)] and not b[len(a):].strip(bytes(1)):
            self.count += 1
            return True
        return False

    def blob(self, world: World, f, rel: str) -> bool:
        """The mod file has the same bytes as the game file ``rel``."""
        p = world.base.file(rel)
        if p is None or f.reader is None or p.stat().st_size != f.size:
            return False
        if self.budget < 2 * f.size:
            self.skipped += 1
            return False
        self.budget -= 2 * f.size
        try:
            with open_ro(p) as fh:
                same = fh.read() == f.read(limit=f.size)
        except (SatkError, OSError):
            return False
        self.count += same
        return same

    def text(self, world: World, f, rel: str) -> bool:
        p = world.base.file(rel)
        if p is None or f.reader is None:
            return False
        if self.budget < 2 * f.size:
            self.skipped += 1
            return False
        self.budget -= 2 * f.size
        try:
            a, b = f.text(), world.base.text(p)
        except (SatkError, OSError):
            return False
        if [ln.rstrip() for ln in a.splitlines()] == [ln.rstrip() for ln in b.splitlines()]:
            self.count += 1
            return True
        return False


def _uses(world: World) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Model names and TXD names used by the mod's own IDE lines -> IDs."""
    dff: dict[str, list[int]] = {}
    txd: dict[str, list[int]] = {}
    tr = IdeTrait()
    for x in world.used("ide"):
        try:
            st = tr.parse(x.file.text())
        except Exception:  # noqa: BLE001 - unreadable file is reported elsewhere
            continue
        for r in st.recs.values():
            if r.key[0] == "id":
                dff.setdefault(r.cells[1], []).append(r.key[1])
                txd.setdefault(r.cells[2], []).append(r.key[1])
    return dff, txd


def _ids(ids: list[int], n: int = 4) -> str:
    s = ",".join(str(i) for i in sorted(set(ids))[:n])
    return s + (f" (+{len(set(ids)) - n})" if len(set(ids)) > n else "")


def _stream_row(world: World, x: Loaded, uses) -> list:
    name = x.file.lname
    stem = name.rsplit(".", 1)[0]
    kind = x.beh.kind
    hit = world.base.img_names.get(name)
    note = f"; {x.beh.note}" if x.beh.note else ""
    if hit is not None:
        if kind == "dff":
            m = world.base.by_name.get(stem)
            what = f"model {m.id} {m.name}" if m else "no model of this name"
        elif kind == "txd":
            users = world.base.txd_users.get(stem, [])
            what = f"TXD of {len(users)} model(s): {_ids(users)}" if users else "TXD"
        else:
            what = kind.upper()
        return [kind, _sid(kind, name), "replace", x.file.rel, f"{what} in {hit[0]}{note}"]
    ids = (uses[0] if kind == "dff" else uses[1] if kind == "txd" else {}).get(stem, [])
    if kind in ("dff", "txd"):
        what = f"new file, used by this mod's IDE ID {_ids(ids)}" if ids else \
            "new file; no IDE line of this mod uses it"
    else:
        what = "new file"
    return [kind, _sid(kind, name), "add", x.file.rel, what + note]


def _img_rows(world: World, x: Loaded, same: _Same) -> list[list]:
    """An ``.img`` shipped in a mod: replaces a game archive of that name, or is added by an IMG line."""
    rel, name = x.file.rel, x.file.lname
    base_archives = {}
    for ename, (arch, *_rest) in world.base.img_names.items():
        base_archives.setdefault(arch, set()).add(ename)
    match = next((a for a in sorted(base_archives) if a.rsplit("/", 1)[-1] == name), None)
    entries = world.img_entries(x.mod, rel)
    rows: list[list] = []
    if match is not None:
        old = base_archives[match]
        new = {e.lname for e in entries}
        n_same = 0
        for e in entries:
            if e.lname in old and same.stream(world, e):
                n_same += 1
                continue
            k = e.ext
            rows.append([k, _sid(k, e.lname), "replace" if e.lname in old else "add", e.rel,
                         f"entry of {name}"])
        if not rows and new == old:
            same.count += 1
            return rows                        # the same archive as the game's
        rows.insert(0, ["img", f"file:{match}", "replace", rel,
                        f"replaces the whole archive: {len(new & old) - n_same} changed, {n_same} identical, "
                        f"{len(new - old)} new, {len(old - new)} game entries gone"])
        return rows
    lines = [p for k, p, _ in world.base.level_entries if k == "IMG"] + world.mod_level_entries("img")
    line = next((p for p in lines if p.rsplit("/", 1)[-1] == name), None)
    if line is not None:
        rows.append(["img", f"file:{line}", "add", rel,
                     f"{len(entries)} entries; registered by the IMG line {line} (after the game's archives: "
                     "names already in an earlier archive stay shadowed)"])
        for e in entries:
            k = e.ext
            shadow = e.lname in world.base.img_names
            rows.append([k, _sid(k, e.lname), "ignored" if shadow else "add", e.rel,
                         f"shadowed by {world.base.img_names[e.lname][0]}" if shadow else f"entry of {name}"])
        return rows
    rows.append(["img", f"file:{rel.lower()}", "ignored", rel,
                 f"{len(entries)} entries; Mod Loader only swaps game archives of the same name "
                 "(models/gta3.img ...); other IMGs need an IMG line in gta.dat"])
    return rows


def _data_rows(world: World, fs: str, uses, same: _Same) -> list[list]:
    trait, plan, game_rel = world.plan(fs)
    if plan.mode == "default":
        return []
    files = world.data_files().get(fs, [])
    by_file = files[0].file.rel if files else ""
    target_file = fs[4:] if fs.startswith("ide:") else (game_rel or f"data/{fs}")
    kind = "ide" if fs.startswith("ide:") else "data"
    _t, dplan, _g = world.plan(fs, upto=0)
    default = dplan.stores[0] if dplan.stores else None
    if isinstance(trait, LineTrait):
        new = merge(plan.stores, trait)
        nk = {o.key for o in new if o.rec is not None}
        dk = set(default.recs) if default else set()
        if nk == dk:
            same.count += 1
            return []
        mode = "replace" if plan.mode == "override" else "modify"
        how = "the mod's file replaces the game's" if plan.mode == "override" else \
            "merged line by line (Mod Loader's per-record rules for this file are not ported)"
        return [[kind, f"file:{target_file}", mode, by_file,
                 f"+{len(nk - dk)}/-{len(dk - nk)} lines vs the game; {how}"]]
    readme_origin = {trait.final_key(rec.key): org for org, rec in plan.readme_lines}
    readme_store = len(plan.stores) - 1 if plan.mode == "merge" else None
    rows: list[list] = []
    out = merge(plan.stores, trait)
    seen = set()
    for o in out:
        seen.add(o.key)
        d = default.recs.get(o.key) if default else None
        r = o.rec
        if r is None and d is None:
            continue
        if r is not None and d is not None and recs_equal(r, d):
            continue
        by = by_file
        if r is not None:
            org = None
            if o.winner is not None:
                org = readme_origin.get(o.key) if o.winner == readme_store else plan.stores[o.winner].origin
            if org and "/" in org:
                org = org.split("/", 1)[1]           # drop the mod name: inspect shows paths inside the mod
            if org and not org.rsplit(":", 1)[-1].isdigit():
                org = f"{org}:{r.line}"
            by = org or by_file
        target = trait.target(o.key)
        if r is None:
            why = "the mod's file lacks it and replaces the game's" if plan.mode == "override" else \
                "the mod's file lacks it: Mod Loader drops game lines missing from a merged file"
            rows.append([kind, target, "remove", by_file, short(f"{why}; was: {d.text}")])
            continue
        if d is None:
            if kind == "ide" and o.key[0] == "id":
                rows.append(_new_id_row(world, o.key[1], r, by, target_file))
            else:
                rows.append([kind, target, "add", by, short(world_render(trait, r))])
            continue
        if kind == "ide" and o.key[0] == "id" and d.cells[1] != r.cells[1]:
            rows.append(["ide", target, "override-id", by, f"model {o.key[1]} {d.cells[1]} -> {r.cells[1]}"])
            continue
        rows.append([kind, target, "modify", by, diff_detail(trait, d, r)])
    if plan.mode == "override" and default is not None:
        for k, d in default.recs.items():
            if k not in seen:
                rows.append([kind, trait.target(k), "remove", by_file,
                             short(f"the mod's file lacks it and replaces the game's; was: {d.text}")])
    return rows


def world_render(trait, rec: Rec) -> str:
    return " | ".join(trait.render(rec).splitlines())


def _new_id_row(world: World, mid: int, r: Rec, by: str, target_file: str) -> list:
    base = world.base.models.get(mid)
    if base is not None and base.ide != target_file:
        return ["ide", f"model:{mid}", "override-id", by,
                f"ID {mid} is {base.name} in {base.ide}; {r.cells[1]} replaces it if its IDE loads later"]
    extra = "; above 19999: needs a model-limit adjuster" if mid > VANILLA_MAX_ID else ""
    return ["ide", f"model:{mid}", "new-id", by, f"{r.section} {r.cells[1]} txd {r.cells[2]}{extra}"]


def inspect_world(world: World) -> tuple[list[list], dict]:
    """Rows and summary for a world holding exactly one mod (index 0)."""
    rows: list[list] = []
    uses = _uses(world)
    same = _Same()
    ignored: Counter = Counter()
    ignored_example: dict[str, str] = {}
    dup: Counter = Counter(x.beh.key for x in world.used() if x.beh.kind in _STREAM_KINDS)
    for x in world.files:
        f, b = x.file, x.beh
        if x.ignored:
            if b.kind in ("asi", "ide", "ipl", "zon", "data", "nodes") or x.ignored == "IgnoreFiles":
                rows.append([b.kind, f"file:{f.lname}", "ignored", f.rel, x.ignored])
            else:
                ext = f.ext or "(none)"
                ignored[ext] += 1
                ignored_example.setdefault(ext, f.rel)
            continue
        if b.kind in _STREAM_KINDS:
            if same.stream(world, f):
                continue
            row = _stream_row(world, x, uses)
            if dup[b.key] > 1:
                row[4] += f"; {dup[b.key]} files of this name in the mod: the last in path order wins"
            rows.append(row)
        elif b.kind == "img":
            rows += _img_rows(world, x, same)
        elif b.kind in ("ide", "ipl", "zon"):
            t = world.ide_target(x)
            if t is None:
                rows.append([b.kind, f"file:{f.lname}", "ignored", f.rel,
                             "no IDE/IPL line of gta.dat names this file, Mod Loader does not load it"])
            elif b.kind != "ide" and not same.text(world, f, t):
                rows.append([b.kind, f"file:{t}", "replace", f.rel, "text IPL/ZON files are replaced, not merged"])
        elif b.kind in ("asi", "cleo"):
            rows.append([b.kind, f"file:{f.lname}", "load", f.rel, b.note])
        elif b.kind == "fxt":
            rows.append([b.kind, f"file:{f.lname}", "load", f.rel, b.note])
        elif b.kind == "script":
            if not same.blob(world, f, "data/script/main.scm"):
                rows.append([b.kind, "file:data/script/main.scm", "replace", f.rel, b.note])
        elif b.kind in ("fx", "sprite", "gxt", "movie", "audio"):
            rows.append([b.kind, f"file:{f.lname}", "replace", f.rel, b.note or b.plugin])
    for fs in world.data_files():
        rows += _data_rows(world, fs, uses, same)
    for ext, n in sorted(ignored.items()):
        rows.append(["other", f"*.{ext}", "ignored", ignored_example[ext] if n == 1 else f"{n} files",
                     "no Mod Loader handler"])
    rows.sort(key=_row_order)
    counts = Counter(r[2] for r in rows)
    new_ids = sorted({int(r[1].split(":", 1)[1]) for r in rows if r[2] == "new-id"})
    summary: dict = {"changes": dict(sorted(counts.items(), key=lambda kv: CHANGES.index(kv[0])
                                      if kv[0] in CHANGES else 99))} if counts else {}
    if same.count:
        summary["unchanged"] = same.count
    if same.skipped:
        summary["not_compared"] = same.skipped
    if new_ids:
        summary["new_ids"] = new_ids if len(new_ids) <= 20 else new_ids[:20] + [f"+{len(new_ids) - 20}"]
    return rows, summary


def _row_order(r: list) -> tuple:
    by = r[3]
    path, _, line = by.rpartition(":")
    if line.isdigit() and path:
        return (path.lower(), int(line), r[1])
    return (by.lower(), 1 << 30, r[1])
