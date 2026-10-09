"""The style guides (``docs/agent/style``), the style help topics (``data/style/topics``) and the asset brief.

Every number in a style table carries a metric reference, every metric is defined once (the glossary of
``docs/agent/style/README.md``), and every metric band (a metric with its qualifier) lives in exactly one table.
Table kinds, by the first header cell (see README.md "How to read the numbers"):

* ``Metric`` - band table: the first cell is one metric reference (```name``` or ```name[qualifier]```), a
  ``Peer set`` column names what it was measured on;
* ``Fact`` - engine constants and counts, with a ``Source`` column;
* ``Name`` - the glossary (README.md only);
* a class table - the first column is the qualifier (class or size bucket), a column ``n``, metric references in
  the column headers: the band of a cell is ``metric[row label]``;
* any other table - a row with a number must reference a metric.

Fast, stdlib only, no game files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from satk.core.config import REPO_ROOT

STYLE = REPO_ROOT / "docs" / "agent" / "style"
TOPICS = REPO_ROOT / "data" / "style" / "topics"
SKILL = REPO_ROOT / "docs" / "agent" / "SKILL.md"
BRIEF = REPO_ROOT / "docs" / "agent" / "briefs" / "asset-brief.md"
GUIDES = ("README", "construction", "references", "shading", "vehicles", "world", "peds-weapons", "textures",
          "modelling", "limits", "done", "kinds")
TOPIC_NAMES = ("style", "style_construction", "style_references", "style_shading", "style_vehicle", "style_world",
               "style_ped_weapon", "style_texture", "authoring", "visual_qa", "creation", "done", "style_kinds")
#: Wording that prescribed the anti-patterns of agent-built assets (flat colours, vector art, faceted bodies,
#: real-world scale, light damage parts). ``style.brief_check`` owns the full vocabulary; this is the floor.
BRIEF_BANNED = ("crisp", "clean shapes", "clean readable shapes", "flat colour", "flat color", "flat material",
                "no photographs", "never photographs", "close to real scale", "deliberate hard edges",
                "never heavier", "not more than its", "rebuilds")

METRIC = re.compile(r"^([a-z]+\.[A-Za-z0-9_]+)(\[[^\]`]+\])?$")
CODE = re.compile(r"`([^`]+)`")
#: A number = a digit that is not part of an identifier (S25, G1, L10, x4, DXT1 are labels, not numbers).
NUMBER = re.compile(r"(?<![A-Za-z_])\d")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _cells(line: str) -> list[str]:
    """Split a Markdown table row on ``|`` outside code spans."""
    out, cur, code = [], [], False
    for ch in line.strip().strip("|"):
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def _tables(text: str) -> list[tuple[int, list[str], list[list[str]]]]:
    """(first line number, header cells, row cells) of every table outside fenced code."""
    out, block, start, fence = [], [], 0, False
    for i, line in enumerate(text.splitlines() + [""], 1):
        if FENCE.match(line):
            fence = not fence
        is_row = not fence and line.lstrip().startswith("|")
        if is_row:
            if not block:
                start = i
            block.append(line)
            continue
        if len(block) >= 2 and set(block[1].replace("|", "").strip()) <= set("-: "):
            out.append((start, _cells(block[0]), [_cells(b) for b in block[2:]]))
        block = []
    return out


def _metric(span: str) -> tuple[str, str | None] | None:
    m = METRIC.match(span.strip())
    return (m.group(1), m.group(2)) if m else None


def _plain(cell: str) -> str:
    return CODE.sub("", cell)


def _glossary() -> dict[str, list[str]]:
    """metric name -> files listed in "Bands in" (README.md glossary)."""
    text = (STYLE / "README.md").read_text(encoding="utf-8")
    gl = [t for t in _tables(text) if t[1][0] == "Name"]
    assert len(gl) == 1, "README.md needs exactly one glossary table (first header cell 'Name')"
    _, head, rows = gl[0]
    assert head[-1] == "Bands in", head
    out: dict[str, list[str]] = {}
    for r in rows:
        names = [_metric(s) for s in CODE.findall(r[0])]
        assert names and all(n and n[1] is None for n in names), f"glossary name cell: {r[0]!r}"
        files = [f.strip() for f in r[-1].split(",") if f.strip()]
        for n, _ in names:
            assert n not in out, f"metric defined twice in the glossary: {n}"
            out[n] = files
    return out


def _bands() -> tuple[dict[str, set[tuple[str, int]]], list[str]]:
    """band key -> {(file, table line)}, plus every violation of the table conventions."""
    gloss = _glossary()
    keys: dict[str, set[tuple[str, int]]] = {}
    bad: list[str] = []
    for name in GUIDES:
        path = STYLE / f"{name}.md"
        for line, head, rows in _tables(path.read_text(encoding="utf-8")):
            where = f"{path.name}:{line}"
            first = head[0]
            for h in head:
                if h.lower().startswith("sa_plus") and "proposal" not in h:
                    bad.append(f"{where}: the sa_plus column must say 'proposal': {h!r}")
            if first == "Name":
                continue
            if first == "Fact":
                if "Source" not in head:
                    bad.append(f"{where}: a Fact table needs a Source column")
                    continue
                si = head.index("Source")
                bad += [f"{where}: fact without a source: {r[0]!r}" for r in rows if len(r) <= si or not r[si]]
                continue
            if first == "Metric":
                peer = [i for i, h in enumerate(head) if h.startswith("Peer set")]
                if not peer:
                    bad.append(f"{where}: a band table needs a 'Peer set' column")
                    continue
                for r in rows:
                    spans = CODE.findall(r[0])
                    ms = [_metric(s) for s in spans]
                    if len(spans) != 1 or not ms[0] or ms[0][0] not in gloss:
                        bad.append(f"{where}: first cell must be one glossary metric: {r[0]!r}")
                        continue
                    if len(r) <= peer[0] or not r[peer[0]]:
                        bad.append(f"{where}: no peer set for {r[0]!r}")
                    keys.setdefault(spans[0].strip().lower(), set()).add((path.name, line))
                continue
            cols = {i: _metric(s) for i, h in enumerate(head) for s in CODE.findall(h)}
            cols = {i: m for i, m in cols.items() if m and m[0] in gloss}
            if cols:  # a class table: qualifier in column 0, metric per column header
                if "n" not in head:
                    bad.append(f"{where}: a class table needs an 'n' column (the peer-set size)")
                for r in rows:
                    label = _plain(r[0]).strip().lower()
                    for i, cell in enumerate(r[1:], 1):
                        if not NUMBER.search(_plain(cell)) or head[i] == "n":
                            continue
                        if i not in cols:
                            bad.append(f"{where}: number in a column without a metric: {head[i]!r} / {cell!r}")
                            continue
                        keys.setdefault(f"{cols[i][0]}[{label}]", set()).add((path.name, line))
                continue
            for r in rows:
                if not NUMBER.search(" ".join(_plain(c) for c in r)):
                    continue
                refs = [_metric(s) for c in r for s in CODE.findall(c)]
                if not any(m and m[0] in gloss for m in refs):
                    bad.append(f"{where}: a row with numbers names no metric: {r[0]!r}")
    return keys, bad


@pytest.mark.parametrize("name", GUIDES)
def test_guides_exist_english_and_public(name):
    text = (STYLE / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("# "), name
    assert not CYRILLIC.search(text), f"{name}.md: model-facing docs are English-only"
    assert "docs/design" not in text and "D:\\" not in text and "D:/" not in text


def test_every_number_in_a_style_table_names_its_metric():
    _, bad = _bands()
    assert bad == []


def test_each_metric_band_has_exactly_one_table():
    keys, _ = _bands()
    dup = {k: sorted(v) for k, v in keys.items() if len(v) > 1}
    assert dup == {}, dup
    assert len(keys) >= 150, len(keys)  # the vanilla reference stays complete (numbers are reference, not targets)


def test_glossary_points_to_the_files_that_hold_the_bands():
    gloss = _glossary()
    keys, _ = _bands()
    holders: dict[str, set[str]] = {}
    for key, where in keys.items():
        base = METRIC.match(key).group(1) if METRIC.match(key) else key.split("[", 1)[0]
        holders.setdefault(base, set()).update(f for f, _ in where)
    for name, files in gloss.items():
        got = holders.get(name.lower(), set())
        assert set(files) == got, (name, files, sorted(got))


def test_peer_set_and_tier_wording_in_readme():
    text = (STYLE / "README.md").read_text(encoding="utf-8")
    for must in ("p10", "p50", "p90", "peer set", "proposal", "`sa_plus`", "`vanilla`", "## Tiers",
                 "## The ten rules", "## Anti-patterns", "## Metric names", "## Reconciled rules"):
        assert must in text, must


# --------------------------------------------------------------------------- help topics


def test_style_topics_exist_and_fit_the_help_budget():
    from satk.runtime.help import MAX_TOPIC_CHARS

    on_disk = sorted(p.stem for p in TOPICS.glob("*.md"))
    assert on_disk == sorted(TOPIC_NAMES)
    total = 0
    for name in TOPIC_NAMES:
        assert name.isidentifier()
        text = (TOPICS / f"{name}.md").read_text(encoding="utf-8")
        total += len(text)
        assert len(text) <= MAX_TOPIC_CHARS, (name, len(text))
        assert not CYRILLIC.search(text), name
        last = [ln for ln in text.splitlines() if ln.strip()][-1]
        assert re.search(r"docs/agent/(style/[\w-]+|workflows|SKILL)\.md", last), (name, last)
        for ref in re.findall(r"docs/agent/[\w/-]+\.md", text):
            assert (REPO_ROOT / ref).is_file(), (name, ref)
    assert total <= len(TOPIC_NAMES) * MAX_TOPIC_CHARS


def test_style_topics_fit_the_help_registration_contract(monkeypatch):
    """K8: ``satk.style.ops`` registers each file with ``register_topic``; the files must render unchanged."""
    from satk.runtime import help as H

    monkeypatch.setattr(H, "TOPICS", dict(H.TOPICS))  # register_topic writes into the module dict
    for name in TOPIC_NAMES:
        text = (TOPICS / f"{name}.md").read_text(encoding="utf-8")
        if name not in H.TOPICS:
            H.register_topic(name, text.splitlines()[0].lstrip("# "), text)
        env = H.render(name)
        assert env["topic"] == name and env["text"].strip() == text.strip()
        assert name in H.topic_names()


def test_style_topics_render_through_help_when_registered():
    pytest.importorskip("satk.style.ops", reason="satk.style registers the topics (data/style/topics)")
    from satk.runtime import help as H

    for name in TOPIC_NAMES:
        env = H.render(name)
        assert env["topic"] == name
        assert env["text"].strip() == (TOPICS / f"{name}.md").read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- SKILL.md and the brief


def test_skill_creating_assets_section_is_short():
    text = SKILL.read_text(encoding="utf-8")
    m = re.search(r"^## \d+\. Creating assets.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, "SKILL.md needs a '## <n>. Creating assets' section"
    assert len(m.group(1).strip().splitlines()) <= 40
    assert "style/README.md" in m.group(1) and "S25" in m.group(1)


def test_asset_brief_template_avoids_the_anti_pattern_wording():
    text = BRIEF.read_text(encoding="utf-8")
    low = text.lower()
    assert [p for p in BRIEF_BANNED if p in low] == []
    assert not CYRILLIC.search(text)
    for topic in ("style", "authoring"):
        assert f'satk_help("{topic}")' in text or f"satk help {topic}" in text, topic


def test_asset_brief_template_passes_brief_check_when_available():
    from satk.core.registry import all_ops, invoke

    if "style.brief_check" not in {o.name for o in all_ops()}:
        pytest.skip("style.brief_check is not installed")
    spec = next(o for o in all_ops() if o.name == "style.brief_check")
    arg = spec.params[0].name
    env = invoke("style.brief_check", {arg: str(BRIEF)})
    assert env.get("ok") is True, env
    assert not env.get("rows"), env


#: Glossary names that ``satk.style.registry`` does not define yet (derived ratios, data-line fields, class
#: shares). The set only shrinks: once the registry defines a name, remove it here.
DOCS_ONLY = frozenset({
    "col.mesh_ratio", "col.shadow_ratio", "dims.H_rel", "dims.L_rel", "dims.W_rel", "dims.origin_z",
    "dims.real_ratio", "dims.track_rel", "dims.wheel_ratio", "dims.wheelbase_rel", "frame.pos",
    "geo.open_edge_share", "geo.round_sides", "handling.mass", "handling.max_vel", "ide.draw",
    "light.dark_vertex_share", "light.night_models_share", "light.normal_models_share", "light.prelit_lum_std",
    "light.white_vertex_share", "lod.ratio", "mat.per_geom", "mat.refl", "mat.spec", "tex.own_count",
    "tex.side_px", "tex.txd_kb", "txd.models", "txd.textures", "uv.area_share",
})


def test_glossary_names_follow_the_metric_registry():
    reg = pytest.importorskip("satk.style.registry", reason="satk.style is not installed")
    names = {k.split("[", 1)[0] for k in reg.REGISTRY}
    gloss = set(_glossary())
    assert gloss - names == DOCS_ONLY, sorted((gloss - names) ^ DOCS_ONLY)
    assert not DOCS_ONLY & names, sorted(DOCS_ONLY & names)
