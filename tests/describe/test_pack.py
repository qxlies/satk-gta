"""satk.describe.pack: locating, verifying and reading gta-scout packs; text extraction (M2-13)."""

from __future__ import annotations

import json

import pytest

from satk.core.errors import SatkError
from satk.describe.pack import (AUTHOR, FORMAT, created_at, default_pack_dir, digest, english, find_pack,
                                load_pack, note_text)


def test_load_counts_and_skips(pack_file, scout):
    p = load_pack(pack_file)
    assert (p.version, p.license, p.entries, p.digest_ok) == ("sa-2026-10-03", "MIT", 5, True)
    assert [m.name for m in p.models] == ["infernus", "roads_published_name", "lodlae2_roads89"]
    assert dict(p.skipped) == {"texture": 1, "no_dff_source": 1}
    m = p.models[0]
    assert (m.dff_archive, m.dff_entry, m.dff_bytes, m.dff_sha256) == ("gta3.img", "infernus.dff", len(scout.INFERNUS),
                                                                       scout.sha(scout.INFERNUS))
    assert m.dff_ref == "gta3.img/infernus.dff" and m.tags == ("car", "red") and m.confidence == 0.9
    s = p.summary()
    assert s["models_with_dff"] == 3 and s["digest"] == "ok" and s["skipped"] == {"no_dff_source": 1, "texture": 1}
    assert p.source == "gta-scout:sa-2026-10-03" and AUTHOR == "import:gta-scout"


def test_digest_mismatch_needs_no_verify(tmp_path, scout):
    f = scout.write_pack(tmp_path / "sa-x.json", [scout.entry("a", "A thing.", data=scout.dff_bytes(1))], digest_ok=False)
    with pytest.raises(SatkError) as e:
        load_pack(f)
    assert e.value.code == "UNSUPPORTED" and "--no-verify" in e.value.hint
    p = load_pack(f, verify=False)
    assert p.digest_ok is None and len(p.models) == 1


def test_pack_without_digest_warns(tmp_path):
    f = tmp_path / "sa-2026-01-01.json"
    f.write_text(json.dumps({"format": FORMAT, "version": "v", "license": "MIT", "entries": []}), encoding="utf-8")
    p = load_pack(f)
    assert p.digest_ok is None and p.warn and p.warn[0].startswith("PACK_DIGEST")


@pytest.mark.parametrize("change", [{"license": "CC-BY-NC"}, {"fmt": "other-v9"}])
def test_wrong_license_or_format(tmp_path, change, scout):
    f = scout.write_pack(tmp_path / "sa-1.json", [], **change)
    with pytest.raises(SatkError) as e:
        load_pack(f)
    assert e.value.code == "UNSUPPORTED"


def test_not_json(tmp_path):
    f = tmp_path / "sa-bad.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        load_pack(f)
    assert e.value.code == "UNSUPPORTED"
    with pytest.raises(SatkError) as e:
        load_pack(tmp_path / "missing.json")
    assert e.value.code == "NOT_FOUND"


def test_malformed_entries_are_skipped(tmp_path, scout):
    good = scout.entry("good", "Good one.", data=scout.dff_bytes(5))
    bad_sha = scout.entry("badsha", "x", sources=[{"archive": "gta3.img", "entry": "badsha.dff", "bytes": 2048,
                                                    "sha256": "XYZ"}])
    bad_bytes = scout.entry("badbytes", "x", sources=[{"archive": "gta3.img", "entry": "b.dff", "bytes": True,
                                                        "sha256": "a" * 64}])
    path_entry = scout.entry("p", "x", sources=[{"archive": "gta3.img", "entry": "../p.dff", "bytes": 2048,
                                                  "sha256": "a" * 64}])
    loose = scout.entry("loose", "Loose file.", sources=[{"archive": None, "entry": "loose.dff", "bytes": 100,
                                                          "sha256": "b" * 64}])
    no_text = scout.entry("empty", " ", data=scout.dff_bytes(6))
    vc = scout.entry("vcthing", "Vice City model.", data=scout.dff_bytes(7), game="vc")
    entries = [good, bad_sha, bad_bytes, path_entry, loose, no_text, vc, "not-a-dict"]
    p = load_pack(scout.write_pack(tmp_path / "sa-m.json", entries))
    assert sorted(m.name for m in p.models) == ["good", "loose"]
    assert [m.dff_ref for m in p.models if m.name == "loose"] == ["loose.dff"]
    assert dict(p.skipped) == {"no_dff_source": 3, "invalid": 2, "other_game": 1}


def test_find_pack_newest_and_explicit(satk_home, tmp_path, scout):
    with pytest.raises(SatkError) as e:
        find_pack(None)
    assert e.value.code == "NOT_FOUND" and "gta-scout" in e.value.hint
    d = default_pack_dir()
    assert d == satk_home / "src" / "gta-scout" / "data" / "annotations"
    scout.write_pack(d / "sa-2026-09-11.json", [])
    scout.write_pack(d / "sa-2026-10-03.json", [])
    (d / "notes.txt").write_text("x", encoding="utf-8")
    assert find_pack(None).name == "sa-2026-10-03.json"
    assert find_pack(str(d)).name == "sa-2026-10-03.json"
    assert find_pack(d / "sa-2026-09-11.json").name == "sa-2026-09-11.json"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SatkError) as e:
        find_pack(empty)
    assert e.value.code == "NOT_FOUND"


@pytest.mark.parametrize("text, want", [
    ("FR: Banc rouge. EN: Red bench.", ("Red bench.", "en")),
    ("FR: a\n  b EN:   two   words ", ("two words", "en")),
    ("Pair of dark radial fan blades.", ("Pair of dark radial fan blades.", "en")),
    ("Observations: Structure grise en arc avec une pelouse / Gray curved structure.",
     ("Observations: Structure grise en arc avec une pelouse / Gray curved structure.", "mul")),
    ("OPEN: sign above the door.", ("OPEN: sign above the door.", "en")),
    ("FR: seulement le français et la suite. EN:", ("FR: seulement le français et la suite. EN:", "mul")),
])
def test_english(text, want):
    assert english(text) == want


def test_note_text_modes():
    t = "FR: Banc rouge et la table. EN: Red bench."
    assert note_text(t) == ("Red bench.", "en")
    assert note_text(t, "full") == (t, "mul")
    with pytest.raises(SatkError) as e:
        note_text(t, "fr")
    assert e.value.code == "BAD_PARAMS"


def test_created_at_and_digest(scout):
    assert created_at("sa-2026-10-03") == "2026-10-03T00:00:00Z"
    assert created_at("custom") is None
    # canonical form: sorted keys, no spaces, UTF-8 kept
    assert digest({"b": 1, "a": "é"}) == scout.sha('{"a":"é","b":1}'.encode("utf-8"))
