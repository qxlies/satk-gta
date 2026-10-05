"""A game root plus a set of mods, resolved the way Mod Loader would load them.

:class:`World` opens every mod (folder or zip), classifies its files (:mod:`.classify`), skips the
profile's ``IgnoreFiles``, reads readme lines, and answers:

* :meth:`World.claims` - files that replace the same game file (``stream:infernus.dff`` ...),
  each with its mods in install order (the last one wins);
* :meth:`World.data_files` / :meth:`World.plan` - data files and how Mod Loader builds each of them
  (:mod:`.merge`), with readme lines attached to the file they target;
* :meth:`World.ide_target` - which level-file IDE/IPL a mod's IDE/IPL loads as (or none).

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import SatkError
from .base import BaseGame
from .classify import Behaviour, classify
from .merge import Plan, plan_stores
from .modloader import ModEntry, install_order_key
from .source import ModFile, ModSource, open_mod
from .traits import (IDE_README_FILES, CarcolsTrait, GtaDatTrait, HandlingTrait, IdeTrait, Rec, Store, Trait,
                     trait_for, trim_config_line)

__all__ = ["World", "Loaded", "ReadmeLine", "README_MAX", "decode_readme"]

#: Mod Loader reads readmes up to this size (``max_readme_size``).
README_MAX = 60000


def decode_readme(data: bytes) -> str:
    """Readme bytes to text: UTF-8/UTF-16 by BOM, else UTF-8 when valid, else latin-1."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


@dataclass
class Loaded:
    """One mod file Mod Loader handles (or ignores)."""

    mod: int                 # index into World.mods
    file: ModFile
    beh: Behaviour
    ignored: str = ""        # why the file is not used ("" = used)


@dataclass
class ReadmeLine:
    """A readme line one of std.data's readme readers takes."""

    mod: int
    file: ModFile
    line: int
    text: str
    fs: str                  # data file it goes to: 'handling.cfg', 'carcols.dat', 'gta.dat', 'ide:<path>'
    rec: Rec


@dataclass
class World:
    """Mods over a base game.

    Args:
        base: the game under the mods.
        mods: loaded mods (any order; sorted into install order here).
        ignore_files: the profile's IgnoreFiles wildcards (see :class:`satk.modinspect.modloader.Folder`).
        file_ignored: predicate for IgnoreFiles (defaults to none).
    """

    base: BaseGame
    mods: list[ModEntry]
    file_ignored: object = None
    sources: list[ModSource] = field(default_factory=list)
    files: list[Loaded] = field(default_factory=list)
    readme: list[ReadmeLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.mods = sorted(self.mods, key=lambda m: install_order_key(m.priority, m.name))
        for i, m in enumerate(self.mods):
            try:
                src = open_mod(m.path)
            except SatkError as e:
                self.warnings.append(f"MOD: {m.name}: {e.msg}")
                src = ModSource(m.path, "dir", m.name, [])
            self.sources.append(src)
            self.warnings += [f"{w.split(':', 1)[0]}: {m.name}: {w.split(':', 1)[1].strip()}" if ":" in w else w
                              for w in src.warnings]
            for f in src.files:
                if f.container is not None and src.kind != "img":
                    continue  # entries of an IMG shipped inside a mod: see img_entries()
                head = b""
                if f.ext == "ipl" and f.reader is not None:
                    try:
                        head = f.read()[:4]
                    except SatkError:
                        head = b""
                beh = classify(f.rel, head, in_img=src.kind == "img")
                ign = ""
                if callable(self.file_ignored) and self.file_ignored(f.rel):
                    ign = "IgnoreFiles"
                elif not beh.plugin:
                    ign = beh.note or "no Mod Loader handler"
                self.files.append(Loaded(i, f, beh, ign))
        self._cache: dict = {}
        self._read_readmes()

    def close(self) -> None:
        for s in self.sources:
            s.close()

    def __enter__(self) -> "World":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- helpers

    def used(self, kind: str | None = None) -> list[Loaded]:
        return [x for x in self.files if not x.ignored and (kind is None or x.beh.kind == kind)]

    def label(self, mod: int) -> str:
        return self.mods[mod].name

    def origin(self, x: Loaded | ReadmeLine) -> str:
        if isinstance(x, ReadmeLine):
            return f"{self.mods[x.mod].name}/{x.file.rel}:{x.line}"
        return f"{self.mods[x.mod].name}/{x.file.rel}"

    def img_entries(self, mod: int, img_rel: str) -> list[ModFile]:
        return [f for f in self.sources[mod].files if f.container == img_rel]

    @property
    def model_names(self) -> frozenset[str]:
        """Model names known to the game: base IDEs plus the IDE lines of every mod (readme matching)."""
        names = set(self.base.by_name)
        tr = IdeTrait()
        for x in self.used("ide"):
            try:
                st = tr.parse(x.file.text())
            except SatkError:
                continue
            names |= {r.cells[1] for r in st.recs.values() if r.key[0] == "id"}
        return frozenset(names)

    # ---------------------------------------------------------------- level files

    def mod_level_entries(self, kind: str) -> list[str]:
        """IDE/IPL/IMG (``kind``) paths that mods add through their own ``gta.dat``/``default.dat`` and readmes."""
        ck = ("level", kind)
        if ck in self._cache:
            return self._cache[ck]
        sec = {"ide": "IDE", "ipl": "IPL", "img": "IMG"}[kind]
        out: list[str] = []
        tr = GtaDatTrait()
        for x in self.used("data"):
            if x.file.lname not in ("gta.dat", "default.dat"):
                continue
            try:
                st = tr.parse(x.file.text())
            except SatkError:
                continue
            out += [k[1] for k in st.recs if k[0] == sec]
        out += [r.rec.key[1] for r in self.readme if r.fs == "gta.dat" and r.rec.key[0] == sec]
        self._cache[ck] = out
        return out

    def ide_target(self, x: Loaded) -> str | None:
        """Level-file path a mod IDE/IPL/ZON loads as (``None`` = not loaded)."""
        kind = "ide" if x.beh.kind == "ide" else "ipl"
        vpath = x.beh.key.split(":", 1)[1]
        return self.base.match_gta_path(vpath, kind, self.mod_level_entries(kind))

    # ---------------------------------------------------------------- readmes

    def _read_readmes(self) -> None:
        readers: list[tuple[Trait, str]] = [(GtaDatTrait(), "gta.dat"), (HandlingTrait(), "handling.cfg"),
                                            (CarcolsTrait(), "carcols.dat"), (IdeTrait(), "ide")]
        models: frozenset[str] | None = None
        for x in self.used("readme"):
            if x.file.size > README_MAX:
                self.warnings.append(f"README: {self.origin(x)}: {x.file.size} bytes > {README_MAX}, "
                                     "Mod Loader does not read it")
                continue
            try:
                text = decode_readme(x.file.read())
            except SatkError as e:
                self.warnings.append(f"README: {self.origin(x)}: {e.msg}")
                continue
            for n, raw in enumerate(text.split("\n"), 1):
                line = trim_config_line(raw)
                if not line:
                    continue
                for tr, fs in readers:
                    if tr.name == "carcols.dat" and models is None:
                        models = self.model_names
                    rec = tr.readme_rec(line, n, models or frozenset())
                    if rec is None:
                        continue
                    dest = fs
                    if fs == "ide":
                        fname = next((f for f, s in IDE_README_FILES.items() if s == rec.section), None)
                        target = next((p for p in self.base.ide_paths if p.rsplit("/", 1)[-1] == fname),
                                      f"data/{fname}")
                        dest = f"ide:{target}"
                    self.readme.append(ReadmeLine(x.mod, x.file, n, line, dest, rec))
                    break

    # ---------------------------------------------------------------- data files

    def data_files(self) -> dict[str, list[Loaded]]:
        """Mergeable/overridable data files by Mod Loader key (``handling.cfg``, ``ide:data/vehicles.ide`` ...)."""
        if "data" in self._cache:
            return self._cache["data"]
        out: dict[str, list[Loaded]] = {}
        for x in self.used():
            if x.beh.kind == "data":
                out.setdefault(x.beh.key.split(":", 1)[1], []).append(x)
            elif x.beh.kind == "ide":
                t = self.ide_target(x)
                if t is not None:
                    out.setdefault(f"ide:{t}", []).append(x)
        for r in self.readme:
            out.setdefault(r.fs, [])
        self._cache["data"] = out
        return out

    def default_file(self, fs: str) -> tuple[str, str] | None:
        """``(relpath, text)`` of the game's own copy of a data file, or ``None``."""
        if fs.startswith("ide:"):
            rel = fs[4:]
            p = self.base.file(rel)
            return (rel, self.base.text(p)) if p is not None and p.is_file() else None
        hit = self.base.data_file(fs)
        if hit is None:
            return None
        return hit[0], self.base.text(hit[1])

    def plan(self, fs: str, *, upto: int | None = None) -> tuple[Trait, Plan, str | None]:
        """How Mod Loader builds ``fs``: ``(trait, plan, game relpath)``.

        Args:
            fs: key from :meth:`data_files`.
            upto: use only mods with an index below this (``0`` = the game alone).
        """
        trait = trait_for(fs[4:] if fs.startswith("ide:") else fs)
        files = [x for x in self.data_files().get(fs, []) if upto is None or x.mod < upto]
        lines = [r for r in self.readme if r.fs == fs and (upto is None or r.mod < upto)]
        d = self.default_file(fs)
        default = trait.parse(d[1], label="default", origin=d[0], is_default=True) if d else None
        mods: list[Store] = []
        engine: list[Store] = []
        for x in files:
            try:
                text = x.file.text()
            except SatkError as e:
                self.warnings.append(f"DATA: {self.origin(x)}: {e.msg}")
                continue
            mods.append(trait.parse(text, label=self.label(x.mod), origin=self.origin(x)))
            engine.append(trait.parse(text, label=self.label(x.mod), origin=self.origin(x), engine=True))
        readme = [(self.origin(r), r.rec) for r in lines]
        return trait, plan_stores(trait, default, mods, readme, engine), (d[0] if d else None)

    # ---------------------------------------------------------------- replaced files

    def claims(self) -> dict[str, list[Loaded]]:
        """Non-merged files by competition key, mods in install order (the last entry wins)."""
        out: dict[str, list[Loaded]] = {}
        for x in self.used():
            if x.beh.merge or x.beh.kind in ("readme", "asi", "cleo", "fxt"):
                continue
            key = x.beh.key
            if x.beh.kind in ("ipl", "zon"):
                t = self.ide_target(x)
                if t is None:
                    continue
                key = f"ipl:{t}"
            out.setdefault(key, []).append(x)
        return out
