"""Effective files and static demand, using the existing Mod Loader and format readers.

No index is built and no game file is written. Whole-world inventories, mandatory resident
content, and a streaming scenario are deliberately different measurements.
"""

from __future__ import annotations

import math
import re
import struct
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import open_ro
from ..formats.dat import parse_dat
from ..formats.ide import parse_ide
from ..formats.img import ImgArchive
from ..formats.ipl import parse_ipl_binary, parse_ipl_text
from ..formats.layout import ArchiveSpec, archives, loose_assets
from ..formats.rw import FormatError
from ..modinspect.merge import merge
from ..modinspect.world import World

MAX_BINARY = 128 * 1024 * 1024
MAX_TEXT = 16 * 1024 * 1024


@dataclass
class Measure:
    need: int | None
    basis: str = "exact"
    note: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class Resource:
    name: str
    ns: str
    size: int
    origin: str
    reader: Callable[[], bytes]

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def stem(self) -> str:
        return self.name.rsplit(".", 1)[0]

    def read(self) -> bytes:
        if self.size > MAX_BINARY:
            raise SatkError("UNSUPPORTED", f"{self.origin}: metadata scan is limited to {MAX_BINARY} bytes per asset",
                            hint="split or shrink the asset and run the plan again")
        raw = self.reader()
        if len(raw) != self.size:
            raise SatkError("BAD_PARAMS", f"{self.origin}: truncated or changed resource",
                            hint="finish copying the mod and run the plan again")
        return raw


def _bad(label: str, exc) -> SatkError:
    return SatkError("BAD_PARAMS", f"cannot count {label}: {exc}",
                     hint="check the file with satk formats dump or satk mod check, then retry")


def _file_bytes(path: Path, bound: int = MAX_TEXT) -> bytes:
    if path.stat().st_size > bound:
        raise SatkError("UNSUPPORTED", f"{path.name}: file exceeds the {bound}-byte scan bound",
                        hint="split or shrink the input file")
    with open_ro(path) as stream:
        return stream.read(bound + 1)


def _render(trait, recs, *, sections: bool) -> str:
    lines = []
    previous = ""
    for rec in recs:
        if sections and rec.section != previous:
            if previous:
                lines.append("end")
            lines.append(rec.section)
            previous = rec.section
        lines.append(trait.render(rec))
    if sections and previous:
        lines.append("end")
    return "\n".join(lines) + "\n"


class Scan:
    def __init__(self, world: World, stack: ExitStack):
        self.world = world
        self.stack = stack
        self.warn = world.warnings
        self.measures: dict[str, Measure] = {}
        self.resources: dict[tuple[str, str], Resource] = {}
        self.models: dict[int, object] = {}
        self.parents: dict[str, str] = {}
        self.blocks: list[tuple[str, bool, list]] = []
        self.car_blocks: list[list[dict]] = []
        self.texts: dict[str, str | None] = {}
        self.archive_count = 0
        self.archive_inventory = 0
        self.archive_entries = 0
        self.largest_sectors = 0
        self.fx_types: Counter = Counter()
        self.embedded_models: set[str] = set()
        self.collisions: set[str] = set()
        self.collision_records = 0
        self.notes: list[str] = []

    def put(self, key: str, n: int | None, basis: str = "exact", note: str = "", **detail) -> None:
        self.measures[key] = Measure(n, basis if n is not None or basis == "runtime" else "unknown", note, detail)

    def unported_readme(self, fs: str) -> bool:
        """Recognize unsupported data instructions without pretending to know their merge result."""
        if fs not in ("weapon.dat", "carmods.dat"):
            return False
        from ..addon.weapon import parse as parse_weapons
        from ..modinspect.world import README_MAX, decode_readme

        handled = {(r.mod, r.file.rel, r.line) for r in self.world.readme}
        models = {d.name.lower(): d for d in self.models.values()}
        for x in self.world.used("readme"):
            if x.file.size > README_MAX:
                continue
            for n, line in enumerate(decode_readme(x.file.read()).splitlines(), 1):
                if (x.mod, x.file.rel, n) in handled:
                    continue
                if fs == "weapon.dat" and parse_weapons(line).recs:
                    return True
                if fs == "carmods.dat":
                    tokens = line.split("#", 1)[0].replace(",", " ").lower().split()
                    if len(tokens) > 1 and tokens[0] in models and models[tokens[0]].sec == "cars" and all(
                            t in models and models[t].sec == "objs" for t in tokens[1:]):
                        return True
        return False

    def data_text(self, fs: str) -> str | None:
        """Keep the default/override bytes; render only an actual, supported merge."""
        from ..modinspect.classify import DATA_OVERRIDE

        if fs in self.texts:
            return self.texts[fs]
        files = self.world.data_files().get(fs, [])
        has_readme = any(r.fs == fs for r in self.world.readme)
        if self.unported_readme(fs):
            self.warn.append(f"UNSUPPORTED: {fs}: unresolved readme data instructions; its demand is unknown")
            text = None
        elif not files and not has_readme:
            hit = self.world.base.data_file(fs) if not fs.startswith("ide:") else None
            path = self.world.base.file(fs[4:]) if fs.startswith("ide:") else hit[1] if hit else None
            text = _file_bytes(path).decode("latin-1") if path is not None else None
        elif files and (fs in DATA_OVERRIDE or len(files) == 1 and not has_readme):
            text = files[-1].file.text()
        else:
            trait, plan, _ = self.world.plan(fs)
            if not trait.ported:
                self.warn.append(f"UNSUPPORTED: {fs}: multiple Mod Loader stores use unported merge rules; "
                                 "its demand is unknown")
                text = None
            else:
                failed = sum(len(s.failed) for s in plan.stores)
                if failed:
                    raise _bad(fs, f"{failed} records rejected by the Mod Loader reader")
                text = _render(trait, [o.rec for o in merge(plan.stores, trait) if o.rec is not None],
                               sections=fs.startswith("ide:"))
        self.texts[fs] = text
        return text

    def level_paths(self, kind: str) -> list[str]:
        out: list[str] = []
        for dat in self.world.base.dat:
            name = Path(dat).name.lower()
            if name in ("default.dat", "gta.dat"):
                text = self.data_text(name)
            else:
                p = self.world.base.file(dat)
                text = _file_bytes(p).decode("latin-1") if p else None
            if text is None:
                raise _bad(dat, "required level manifest is missing")
            for line in parse_dat(text):
                if line.key == kind:
                    rel = line.path.replace("\\", "/").lower()
                    if kind == "IDE" or rel not in out:
                        out.append(rel)
        return out

    def read_models(self) -> None:
        from ..modinspect.check import SEC_STORE

        counts = Counter()
        definitions = []
        fx = 0
        duplicate_ids = set()
        for rel in self.level_paths("IDE"):
            text = self.data_text("ide:" + rel)
            if text is None:
                raise _bad(rel, "registered IDE file is missing")
            defs, parents, effects = parse_ide(text, strict=True)
            definitions.extend(defs)
            fx += len(effects)
            for child, parent in parents:
                self.parents[child.lower()] = parent.lower()
            for d in defs:
                if d.id < 0:
                    raise _bad(rel, f"negative model ID {d.id}")
                if d.id in self.models:
                    duplicate_ids.add(d.id)
                self.models[d.id] = d
                counts[SEC_STORE[d.sec]] += 1
        if duplicate_ids:
            self.warn.append(f"DUPLICATE_ID: {len(duplicate_ids)} model IDs have multiple loaded definitions; "
                             "store demand counts every allocation, while links use the last definition")
        for kind in ("vehicle", "ped", "weapon", "object", "timed", "clump"):
            self.put("model." + kind, counts[kind], note="Loaded IDE definitions; replacements within a file count once.")
        atomic = sum(d.sec == "objs" and not ((d.flags or 0) & 0x1000) for d in definitions)
        self.put("model.atomic", atomic)
        self.put("model.damageable", counts["object"] - atomic)
        self.put("model.ids", len(self.models), duplicate_ids=sorted(duplicate_ids))
        self.put("model.id_span", max(self.models, default=-1) + 1)
        self.put("fx.ide", fx)

    def _add_archive(self, label: str, ns: str, files) -> None:
        self.archive_inventory += 1
        self.archive_count += ns in ("main", "player")
        for name, size, reader in files:
            self.archive_entries += 1
            self.largest_sectors = max(self.largest_sectors, (size + 2047) // 2048)
            key = (ns, name.lower())
            self.resources.setdefault(key, Resource(name.lower(), ns, size, f"{label}/{name}", reader))

    def read_resources(self) -> None:
        claims = self.world.claims()
        used_mod_imgs: set[tuple[int, str]] = set()
        # Reuse the layout's executable/special registrations, then insert the effective
        # level-manifest archives. Base manifests may have been replaced or merged by mods.
        builtins = archives(self.world.base.root, [], self.world.base.img_order)
        specs = [a for a in builtins if a.ns == "main"]
        for rel in self.level_paths("IMG"):
            if all(a.relpath != rel for a in specs):
                specs.append(ArchiveSpec(rel, "main", "effective level manifest", len(specs)))
        specs += [a for a in builtins if a.ns != "main" and all(s.relpath != a.relpath for s in specs)]
        for spec in specs:
            name = spec.relpath.rsplit("/", 1)[-1]
            replacements = claims.get("imgfile:" + name, [])
            if replacements:
                x = replacements[-1]
                items = self.world.img_entries(x.mod, x.file.rel)
                used_mod_imgs.add((x.mod, x.file.rel))
                self._add_archive(self.world.origin(x), spec.ns,
                                  [(f.lname, f.size, lambda f=f: f.read(MAX_BINARY)) for f in items])
                continue
            path = self.world.base.file(spec.relpath)
            if path is None:
                raise _bad(spec.relpath, "registered IMG archive is missing")
            archive = self.stack.enter_context(ImgArchive.open(path))
            self._add_archive(spec.relpath, spec.ns,
                              [(e.name, e.size, lambda e=e, a=archive: a.read(e)) for e in archive.entries])
        for key, choices in sorted(claims.items()):
            if not key.startswith("imgfile:"):
                continue
            x = choices[-1]
            if (x.mod, x.file.rel) in used_mod_imgs:
                continue
            items = self.world.img_entries(x.mod, x.file.rel)
            self._add_archive(self.world.origin(x), "main",
                              [(f.lname, f.size, lambda f=f: f.read(MAX_BINARY)) for f in items])
        # A standalone IMG is a mod source whose files are its directory entries.
        for src in self.world.sources:
            if src.kind == "img":
                self._add_archive(src.name, "main", [(f.lname, f.size, lambda f=f: f.read(MAX_BINARY))
                                                      for f in src.files])
        for rel in loose_assets(self.world.base.root):
            path = self.world.base.file(rel)
            if path is None:
                continue
            name = path.name.lower()
            self.resources[("loose", name)] = Resource(name, "loose", path.stat().st_size, rel,
                                                       lambda p=path: _file_bytes(p, MAX_BINARY))
        # COLFILE may name a file outside models/. It is loaded directly into the generic
        # collision slot, unlike COLs discovered in the main streaming archives.
        for rel in self.level_paths("COLFILE"):
            name = rel.rsplit("/", 1)[-1]
            path = self.world.base.file(rel)
            if path is not None:
                self.resources[("loose", name)] = Resource(name, "loose", path.stat().st_size, rel,
                                                           lambda p=path: _file_bytes(p, MAX_BINARY))
            elif "stream:" + name not in claims:
                raise _bad(rel, "registered COLFILE is missing")
        # Streamed loose mods override archive entries by name; forced clothing has its own namespace.
        for key, choices in sorted(claims.items()):
            if not (key.startswith("stream:") or key.startswith("fx:")):
                continue
            x = choices[-1]
            f = x.file
            if f.container is not None and self.world.sources[x.mod].kind == "img":
                continue  # Archive precedence was resolved above, not as a second loose override.
            ns = "player" if key.endswith("#cloth") else "loose" if key.startswith("fx:") else "main"
            if ns == "main" and ("main", f.lname) not in self.resources:
                existing = [r.ns for r in self.resources.values() if r.name == f.lname]
                if existing == ["loose"]:
                    ns = "loose"  # A replacement of an explicitly loaded stock asset is not streamed.
            self.resources[(ns, f.lname)] = Resource(f.lname, ns, f.size, self.world.origin(x),
                                                   lambda f=f: f.read(MAX_BINARY))
        self.put("img.archives", self.archive_count)
        self.put("img.files", self.archive_inventory, "inventory")
        self.put("img.entries", self.archive_entries, "inventory")
        self.put("img.entry_sectors", self.largest_sectors)
        self.put("store.col", 1 + sum(r.ext == "col" and r.ns == "main" for r in self.resources.values()))
        txds = {r.stem for r in self.resources.values() if r.ext == "txd" and r.ns in ("main", "loose")}
        txds.update(d.txd.lower() for d in self.models.values() if d.txd)
        txds.update(self.parents)
        txds.update(self.parents.values())
        self.put("store.txd", len(txds), note="Main/loose and IDE TXD names, including missing referenced files. "
                 "Clothing texture files do not each allocate an ordinary TXD slot.")
        self.put("txd.files", sum(r.ext == "txd" for r in self.resources.values()), "inventory")
        for ext in ("ifp", "rrr", "scm"):
            self.put("store." + ext, sum(r.ext == ext and r.ns == "main" for r in self.resources.values()))

    def read_ipls(self) -> None:
        claims = self.world.claims()
        items = Counter()
        text_count = text_arrays = max_text = 0
        seen_names = set()
        for rel in self.level_paths("IPL"):
            replacement = claims.get("ipl:" + rel)
            if replacement:
                raw = replacement[-1].file.read()
            elif "stream:" + rel.rsplit("/", 1)[-1] in claims:
                raw = claims["stream:" + rel.rsplit("/", 1)[-1]][-1].file.read(MAX_BINARY)
            else:
                path = self.world.base.file(rel)
                if path is None:
                    raise _bad(rel, "registered IPL is missing")
                raw = _file_bytes(path)
            insts, parts = self._ipl(raw)
            binary = raw[:4] == b"bnry"
            self.blocks.append((rel, False, insts))
            self.car_blocks.append(parts.get("cars", []))
            # The generic IPL parser retains section lines. LoadGarage requires eleven fields;
            # stock IPLs contain two shorter, ignored lines in grge sections.
            if "grge" in parts:
                parts["grge"] = [r for r in parts["grge"] if len(r["fields"]) >= 11]
            items.update({k: len(v) for k, v in parts.items()})
            seen_names.add(rel.rsplit("/", 1)[-1])
            text_count += 1
            if not binary:
                text_arrays += bool(insts)
                max_text = max(max_text, len(insts))
        binary_count = 0
        for r in self.resources.values():
            if r.ext != "ipl" or r.ns != "main" or r.name in seen_names:
                continue
            insts, parts = self._ipl(r.read())
            self.blocks.append((r.origin, True, insts))
            self.car_blocks.append(parts.get("cars", []))
            items.update({k: len(v) for k, v in parts.items()})
            binary_count += 1
        self.put("store.ipl", 1 + text_count + binary_count)
        self.put("ipl.instances", sum(len(b[2]) for b in self.blocks), "inventory",
                 permanent=sum(len(b[2]) for b in self.blocks if not b[1]),
                 streamed=sum(len(b[2]) for b in self.blocks if b[1]))
        self.put("ipl.resort", max_text)
        self.put("ipl.entity_arrays", text_arrays)
        for sec, key in (("enex", "enex"), ("jump", "jumps"), ("grge", "garages"),
                         ("pick", "pickups"), ("tcyc", "timecycle_boxes")):
            self.put("ipl." + key, items[sec], "lower_bound" if sec in ("cars", "grge", "pick", "enex") else "exact",
                     "IPL records; script-created and DFF-created entries are additional.")
        self.put("ipl.car_records", items["cars"], "inventory")

    @staticmethod
    def _ipl(raw: bytes) -> tuple[list, dict]:
        if raw[:4] == b"bnry":
            insts, cars = parse_ipl_binary(raw)
            return insts, {"cars": cars}
        return parse_ipl_text(raw.decode("latin-1"), strict=True)

    def read_metadata(self) -> None:
        from ..formats.col import iter_col

        loose_cols = {rel.rsplit("/", 1)[-1] for rel in self.level_paths("COLFILE")}
        defined_names = {d.name.lower() for d in self.models.values()}
        for r in self.resources.values():
            try:
                if r.ext == "col":
                    if r.ns == "loose" and r.name not in loose_cols:
                        continue  # The stock folder also contains unused VC-era collision files.
                    cols = list(iter_col(r.read(), strict=True))
                    self.collision_records += len(cols)
                    self.collisions.update(c.name.lower() for c in cols)
                elif r.ext == "dff" and r.ns in ("main", "loose"):
                    if r.stem not in defined_names:
                        continue  # Unregistered leftovers and special clothing/cutscene namespaces are not IDE models.
                    fx, cols = dff_metadata(r.read())
                    self.fx_types.update(fx)
                    self.collision_records += len(cols)
                    if cols:
                        self.embedded_models.add(r.stem)
            except (ValueError, SatkError) as exc:
                raise _bad(r.origin, exc) from None
        self.put("fx.dff", sum(self.fx_types.values()), "inventory", types=dict(sorted(self.fx_types.items())))
        self.put("col.models", self.collision_records, "inventory")
        bound = sum(d.name.lower() in self.collisions or d.name.lower() in self.embedded_models
                    for d in self.models.values())
        self.put("pool.colmodels", None, "runtime",
                 "Collision binding is not simultaneous pool occupancy; model loading and temporary collision objects affect demand.",
                 bound_models=bound)

    def read_paths(self) -> None:
        from ..paths.records import NAVI_SIZE, NODE_SIZE

        counts = Counter()
        largest_nodes = largest_navis = largest_region = -1
        for r in self.resources.values():
            match = re.fullmatch(r"nodes(\d+)\.dat", r.name)
            if not match or r.ns != "main":
                continue
            raw = r.read()
            if len(raw) < 20:
                raise _bad(r.origin, "truncated path header")
            n, vehicles, peds, navis, links = struct.unpack_from("<5I", raw)
            required = 20 + NODE_SIZE * n + NAVI_SIZE * navis + (8 * links + 1152 if links else 0)
            if n != vehicles + peds or required > len(raw):
                raise _bad(r.origin, "inconsistent path header or truncated arrays")
            # Count overflows too: the existing strict path codec intentionally rejects them.
            counts.update(nodes=n, vehicle_nodes=vehicles, ped_nodes=peds, links=links)
            largest_region = max(largest_region, int(match[1]))
            largest_nodes = max(largest_nodes, n)
            largest_navis = max(largest_navis, navis)
        self.put("paths.files", largest_region + 1)
        for key in ("nodes", "vehicle_nodes", "ped_nodes"):
            self.put("paths." + key, counts[key], "inventory")
        self.put("paths.nodes_per_region", max(0, largest_nodes))
        self.put("paths.navis_per_region", max(0, largest_navis))

    def read_data(self) -> None:
        from ..addon.game import parse_cargrp, parse_carmods
        from ..addon.weapon import parse as parse_weapons, type_id
        from ..formats.handling import parse_handling
        from ..formats.timecyc import parse_timecyc

        for group in ("cargrp", "pedgrp"):
            text = self.data_text(group + ".dat")
            groups = parse_cargrp(text) if text is not None else None
            self.put(group + ".groups", len(groups) if groups is not None else None)
            largest = max((len(g.models) for g in groups), default=0) if groups is not None else None
            if group == "cargrp" and largest is not None:
                # LoadCarGroups reads at most the fixed number of names from each line.
                # The stock cargrp.dat itself has longer lines; preserve requested demand as inventory.
                from .catalog import load

                capacity = next(l.stock for l in load() if l.key == "cargrp.members")
                self.put("cargrp.requested", largest, "inventory")
                self.put("cargrp.members", min(largest, capacity), requested=largest)
                if largest > capacity:
                    self.warn.append(f"TRUNCATED: cargrp.dat requests up to {largest} names per group; "
                                     f"the stock reader uses at most {capacity}")
            else:
                self.put(group + ".members", largest)
        text = self.data_text("carmods.dat")
        mods = parse_carmods(text) if text is not None else None
        self.put("carmods.models", len(mods) if mods is not None else None, "inventory")
        self.put("carmods.members", max((len(v[1]) for v in mods.values()), default=0) if mods is not None else None)
        text = self.data_text("handling.cfg")
        handling = parse_handling(text, strict=True) if text is not None else None
        for kind in ("car", "bike", "boat", "flying"):
            self.put("handling." + kind, sum(r.kind == kind for r in handling.records.values())
                     if handling is not None else None)
        text = self.data_text("weapon.dat")
        weapons = parse_weapons(text) if text is not None else None
        if weapons and weapons.errors:
            raise _bad("weapon.dat", weapons.errors[0])
        self.put("weapons.infos", sum(r.kind != "aim" for r in weapons.recs.values()) if weapons is not None else None)
        self.put("weapons.types", len(weapons.types()) if weapons is not None else None)
        if weapons and any(type_id(t) is None for t in weapons.types()):
            self.warn.append("WEAPON_TYPE: new weapon names require fastman92's weapon-type loader regardless of count")
        text = self.data_text("timecyc.dat")
        self.put("timecyc.entries", len(parse_timecyc(text)) if text is not None else None)

    def resident(self, area: list[float] | None) -> None:
        from ..formats.objectdat import parse_object_dat
        from ..txdopt.budget import Model, World as StreamWorld, tally
        from ..txdopt.inputs import stream_size

        text = self.data_text("object.dat")
        objects = {r.name.lower() for r in parse_object_dat(text or "") if r.loaded}
        mandatory = Counter()
        streamed: list[Counter] = []
        selected: Counter = Counter()
        missing: set[int] = set()
        permanent_cars = selected_cars = largest_cars = 0
        for (_name, binary, insts), cars in zip(self.blocks, self.car_blocks):
            kinds = Counter()
            intersects = area is not None and any((i.interior & 255) == 0 and
                        math.hypot(i.pos[0] - area[0], i.pos[1] - area[1]) <= area[2] for i in insts)
            if area is not None and not intersects:
                intersects = any(math.hypot(pos[0] - area[0], pos[1] - area[1]) <= area[2]
                                 for c in cars if (pos := c.get("pos", (c.get("x", math.inf), c.get("y", math.inf)))))
            for i in insts:
                d = self.models.get(i.model_id)
                if d is None:
                    missing.add(i.model_id)
                    continue
                kinds["dummies" if d.name.lower() in objects else "buildings"] += 1
                if area is not None and intersects:
                    selected[i.model_id] += 1
            if not binary:
                mandatory.update(kinds)
                permanent_cars += len(cars)
            else:
                streamed.append(kinds if area is None or intersects else Counter())
                largest_cars = max(largest_cars, len(cars))
                if intersects:
                    selected_cars += len(cars)
        self.put("ipl.cars", permanent_cars + (largest_cars if area is None else selected_cars),
                 "lower_bound" if area is None else "scenario",
                 "Permanent generators plus the largest streamed block; scripts and concurrent blocks add more."
                 if area is None else "Permanent generators plus complete blocks intersecting the area; scripts add more.",
                 permanent=permanent_cars, largest_block=largest_cars)
        for kind in ("buildings", "dummies"):
            extra = max((c[kind] for c in streamed), default=0) if area is None else sum(c[kind] for c in streamed)
            self.put("pool." + kind, mandatory[kind] + extra if text is not None else None,
                     "lower_bound" if area is None else "scenario",
                     "All text IPL instances plus the largest streamed block" if area is None else
                     "All text IPL instances plus complete streamed blocks intersecting the area",
                     permanent=mandatory[kind], streamed=extra)
        if missing:
            self.warn.append(f"UNDEFINED_MODEL: {len(missing)} placed model IDs have no effective IDE definition")
            self.notes.append("Unresolved placements make entity and streaming estimates incomplete.")
            for key in ("pool.buildings", "pool.dummies"):
                self.measures[key].basis = "lower_bound" if text is not None else "unknown"
                self.measures[key].detail["unresolved_model_ids"] = sorted(missing)
        w = StreamWorld(self.world.base.profile)
        # Match texture.budget namespace precedence and sector rounding; keep an effective file once.
        ordered = sorted(self.resources.values(), key=lambda r: (r.ns not in ("main", "loose"),
                                                                 r.ns != "main", r.name))
        for r in ordered:
            if r.ext not in ("dff", "txd") or r.ns not in ("main", "loose"):
                continue
            k = (r.ext, r.stem)
            if k in w.size:
                continue
            w.size[k] = stream_size(r.size)
            w.streamed[k] = r.ns != "loose"
        w.parent.update(self.parents)
        for d in self.models.values():
            w.models[d.id] = Model(d.id, d.name, d.sec, d.txd.lower() if d.txd else None, d.name.lower())
        all_sizes = tally(w, Counter({mid: 1 for mid in w.models}))
        self.put("streaming.all_bytes", sum(v[0] for v in all_sizes.values()), "inventory",
                 "Same DFF/TXD sector accounting and chain traversal as texture.budget.")
        if area is None:
            worst = max(((sum(w.size[k] for k in w.assets(m)), m.id) for m in w.models.values()), default=(0, -1))
            self.put("streaming.memory", worst[0], "lower_bound", "Largest single model and its TXD chain; not peak memory.",
                     model=f"model:{worst[1]}" if worst[1] >= 0 else "none")
        else:
            total = sum(v[0] for v in tally(w, selected).values())
            self.put("streaming.memory", total, "scenario", "DFF/TXD chains for complete IPL blocks intersecting the area.",
                     area=area, models=len(selected))
        absent_dff = sorted(m.id for m in w.models.values() if ("dff", m.dff) not in w.size)
        absent_txd = set()
        cyclic_txd = set()
        for m in w.models.values():
            chain = set()
            name = m.txd
            while name:
                if name in chain:
                    cyclic_txd.add(name)
                    break
                chain.add(name)
                if ("txd", name) not in w.size:
                    absent_txd.add(name)
                name = w.parent.get(name)
        if absent_dff or absent_txd or cyclic_txd or missing:
            demand = self.measures["streaming.memory"]
            demand.basis = "lower_bound"
            demand.note += " Unresolved resources are not assigned zero cost; the known byte total is only a minimum."
            demand.detail.update(missing_dff_models=absent_dff, missing_txds=sorted(absent_txd),
                                 cyclic_txds=sorted(cyclic_txd), unresolved_placements=sorted(missing))
        if absent_dff:
            self.warn.append(f"NO_DFF: {len(absent_dff)} defined models have no DFF (including runtime placeholders); "
                             "streaming demand is a known lower bound")
        if absent_txd:
            self.warn.append(f"TEX_MISSING: {len(absent_txd)} referenced TXDs have no file; streaming demand is incomplete")
        if cyclic_txd:
            self.warn.append(f"TXDP_CYCLE: {len(cyclic_txd)} cyclic TXD chains need repair")


def dff_metadata(raw: bytes) -> tuple[Counter, list[str]]:
    """Walk chunk headers only; reuse the COL and 2DFX codecs without decoding vertices or textures."""
    from ..formats.col import iter_col
    from ..formats.rw import iter_children, read_chunk
    from ..rw.codecs import decode_2dfx

    read_chunk(raw, 0)
    fx = Counter()
    cols = []
    clumps = 0
    containers = {0x3, 0x6, 0x7, 0x8, 0xE, 0xF, 0x10, 0x14, 0x1A}

    def walk(start: int, end: int, parents: tuple[int, ...]) -> None:
        nonlocal clumps

        if len(parents) > 24:
            raise FormatError("dff", start, "chunk nesting exceeds 24")
        for ch in iter_children(raw, start, end):
            if ch.type == 0:
                break  # IMG sector padding
            if not parents and ch.type == 0x10:
                clumps += 1
            if ch.type == 0x253F2F8 and parents[-2:] == (0xF, 0x3):
                fx.update(t for _pos, t, _data in decode_2dfx(raw[ch.data_off:ch.end]).entries)
            elif ch.type == 0x253F2FA and parents[-2:] == (0x10, 0x3):
                payload = raw[ch.data_off:ch.end]
                if payload[:4] in (b"COLL", b"COL2", b"COL3", b"COL4"):
                    cols.extend(c.name for c in iter_col(payload, strict=True))
                elif payload:
                    # Legacy clump collision extensions (including the stock rccam) have no COL archive header.
                    # They still describe one model; capacity planning does not interpret their geometry.
                    cols.append("legacy embedded collision")
            elif ch.type in containers:
                walk(ch.data_off, ch.end, parents + (ch.type,))

    walk(0, len(raw), ())
    if not clumps:
        raise FormatError("dff", 0, "no RenderWare clump (an optional UV animation dictionary may precede it)")
    return fx, cols


def collect(world: World, area: list[float] | None) -> tuple[dict[str, Measure], dict]:
    with ExitStack() as stack:
        scan = Scan(world, stack)
        try:
            scan.read_models()
            scan.read_resources()
            scan.read_ipls()
            scan.read_metadata()
            scan.read_paths()
            scan.read_data()
            scan.resident(area)
        except (FormatError, OSError) as exc:
            raise _bad("effective game files", exc) from None
    return scan.measures, {"model_sections": dict(sorted(Counter(d.sec for d in scan.models.values()).items())),
                           "notes": scan.notes, "dff_effect_types": dict(sorted(scan.fx_types.items()))}
