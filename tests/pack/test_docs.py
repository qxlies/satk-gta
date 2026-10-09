"""The format pages print the layout tables of the code, in both languages, and name every finding code."""

from __future__ import annotations

import re

import pytest

from satk.core.config import REPO_ROOT
from satk.pack import layout as L

DOCS = REPO_ROOT / "docs"
PACK_TABLES = (L.IMG_ENTRY, L.FOOTER, L.HEADER, L.ENTRY, L.NAME, L.CHUNK)
DAC_TABLES = (L.DAC_HEADER, L.DAC_SECTION)


def page(lang: str, name: str) -> str:
    return (DOCS / lang / f"{name}.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["en", "ru"])
@pytest.mark.parametrize("name,tables", [("saepak", PACK_TABLES), ("dac", DAC_TABLES)])
def test_every_field_is_in_the_page_with_its_offset_and_type(lang, name, tables):
    text = page(lang, name)
    for rec in tables:
        for off, typ, field, _desc in rec.rows():
            row = re.compile(rf"^\| {off} \| {re.escape(typ)} \| `{field}` \|", re.M)
            assert row.search(text), f"{lang}/{name}.md: no row for {rec.name}.{field} at offset {off} ({typ})"
    # the tables are complete: a printed table with as many rows as the record has fields
    for rec in tables:
        first = rec.rows()[0]
        head = f"| {first[0]} | {first[1]} | `{first[2]}` |"
        starts = [m.start() for m in re.finditer(re.escape(head), text)]
        sizes = [len(text[st:].split("\n\n", 1)[0].splitlines()) for st in starts]
        assert len(rec.fields) in sizes, (lang, name, rec.name, sizes)


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_light_record_fields_are_documented(lang):
    text = page(lang, "dac")
    for names, typ, field, _desc in [(n, t, n, d) for n, t, d in L.LIGHT_V1_FIELDS]:
        assert f"`{field}`" in text and typ in text, field


def codes_in_source() -> tuple[set[str], set[str]]:
    src = REPO_ROOT / "src" / "satk" / "pack"
    pack_src = (src / "saepak.py").read_text(encoding="utf-8")
    dac_src = (src / "dac.py").read_text(encoding="utf-8")
    pack = set(re.findall(r'add\("(?:error|warn|info)", "([A-Z_]+)"', pack_src))
    pack |= set(re.findall(r'PackError\("([A-Z_]+)"', pack_src))
    pack |= set(re.findall(r'Finding\("(?:error|warn|info)", "([A-Z_]+)"', pack_src))
    dac = set(re.findall(r'DacError\("([A-Z_]+)"', dac_src)) | set(re.findall(r'Finding\("(?:error|warn)", "([A-Z_]+)"', dac_src))
    dac |= {"DAC_RANGE"}
    # the shared codes of the container verifier also show up for DAC files
    return pack - {"NO_PAYLOAD"}, dac


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_every_finding_code_is_documented(lang):
    pack, dac = codes_in_source()
    assert {"ENTRY_HASH", "CHUNK_HASH", "MANIFEST_HASH", "PACK_ID", "DIR_MISMATCH"} <= pack
    assert {"DAC_CRC", "CACHE_KEY", "SECTION_DUP"} <= dac
    saepak, dacpage = page(lang, "saepak"), page(lang, "dac")
    for c in sorted(pack):
        assert f"`{c}`" in saepak, f"{lang}/saepak.md does not mention {c}"
    for c in sorted(dac):
        assert f"`{c}`" in dacpage or f"`{c}`" in saepak, f"{lang}/dac.md does not mention {c}"


def test_the_documented_constants_match_the_code():
    en = page("en", "saepak")
    assert "134,215,680" in en and L.MAX_ENTRY_BYTES == 134_215_680
    assert "65536" in en and L.DEFAULT_CHUNK == 65536
    assert "`~<first 18 hex digits" in en
    assert L.HEADER.size == 192 and L.FOOTER.size == 128 and "(128 bytes)" in en
