"""The port on the real clean copy (read-only): vanilla data files parse completely and a mod made of
full vanilla copies with one edit each reports exactly those edits (no spurious differences)."""

from __future__ import annotations

import re
import time

import pytest

from satk.core.paths import open_ro
from satk.modinspect.traits import trait_for

pytestmark = pytest.mark.game


def _read(root, rel: str) -> str:
    with open_ro(root.joinpath(*rel.split("/"))) as f:
        return f.read().decode("latin-1")


@pytest.mark.parametrize("rel,min_recs", [("data/handling.cfg", 289), ("data/carcols.dat", 326),
                                          ("data/object.dat", 990), ("data/vehicles.ide", 200),
                                          ("data/gta.dat", 100)])
def test_vanilla_files_parse_completely(clean_root, rel, min_recs):
    st = trait_for(rel.rsplit("/", 1)[-1]).parse(_read(clean_root, rel))
    assert st.failed == [] and len(st.recs) >= min_recs


def test_vanilla_copies_with_one_edit_each(clean_root, tmp_path, run_cli):
    mod = tmp_path / "edits"
    (mod / "data").mkdir(parents=True)
    h = re.sub(r"(?m)^(INFERNUS\s+)1400\.0", r"\g<1>1450.0", _read(clean_root, "data/handling.cfg"))
    c = re.sub(r"(?m)^infernus,.*$", "infernus, 1,1, 0,0", _read(clean_root, "data/carcols.dat"))
    v = re.sub(r"(?m)^(411,\s+infernus,.*?)0\.7,\s+0\.7,", r"\g<1>0.8, 0.8,", _read(clean_root, "data/vehicles.ide"))
    for name, text in (("handling.cfg", h), ("carcols.dat", c), ("vehicles.ide", v)):
        (mod / "data" / name).write_bytes(text.encode("latin-1"))
    t0 = time.perf_counter()
    r = run_cli(["mod", "inspect", str(mod), "--json"])
    took = time.perf_counter() - t0
    assert r.code == 0, r.out + r.err
    rows = [x[:3] for x in r.json["rows"]]
    assert rows == [["data", "carcols:infernus", "modify"], ["data", "handling:INFERNUS", "modify"],
                    ["ide", "model:411", "modify"]], r.json["rows"]
    assert r.json["rows"][1][4] == "mass 1400->1450"
    assert took < 20
