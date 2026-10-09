"""Contracts K6-K8 together (no Blender): ``asset.check --strict`` reads the real inventory report through the kit
export sidecar (the project it names, no ``--project`` needed) and requires a fresh, full and recorded
``checks/<stem>.leak.json``."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from satk.inventory import schema as SC
from satk.inventory import sidecar as SD
from satk.style import check as CK
from satk.style.subject import Subject

from .conftest import build_dff
from .test_report import FACTS, INV


def RV_regions(kind: str) -> dict:
    from satk.look import regions as RG

    return RG.load(kind)


def _subject(dff: Path) -> Subject:
    return Subject(str(dff), dff.stem, b"", "file", path=dff)


def test_strict_uses_the_sidecar_project_and_needs_a_fresh_leak_file(satk_home):
    from satk.core import paths
    from satk.studio import project as P

    P.init("bin9", kind="prop", detail="simple")
    pdir = P.project_dir("bin9")
    inv = copy.deepcopy(INV)
    SC.save(pdir, inv)
    work = Path(paths.cfg().paths.work)
    pkg = work / "out" / "kit" / "bin9" / "modloader" / "bin9"
    pkg.mkdir(parents=True)
    dff = pkg / "bin9.dff"
    dff.write_bytes(build_dff("bin", "lid", "hinge", "badge", "handle", "rim"))
    facts = copy.deepcopy(FACTS)
    facts["scope"] = "kit"
    SD.write([pkg], "bin9", inventory=inv, facts=facts, project=pdir, model="bin9")

    st = CK.strict_status(_subject(dff), {"rows": []})          # no --project: the sidecar names it
    assert st["done"] is False
    inv_rows = [b for b in st["blocking"] if b.startswith("inventory/")]
    assert any(b.startswith("inventory/I10 Rim: rejected") and "reviewer: the rim is a flat box band" in b
               for b in inv_rows)
    assert any(b.startswith("inventory/I06 Pedal: rejected") for b in inv_rows)
    assert not any("I05" in b for b in inv_rows)                # optional items never block
    assert st["blocking"][-1].startswith("leak: no checks/bin9.leak.json next to bin9.dff")
    assert st["inventory"]["required_open"] == len(inv_rows)

    # every item built and attached, and a leak file for this very export: done
    waived = inv["waived"] + [{"key": "base", "why": "the bin stands on its own bottom ring (I01)"}]
    done_inv = {"kind": "prop", "detail": "simple", "waived": waived,
                "items": [dict(inv["items"][0]), dict(inv["items"][2], attaches_to="I01")]}
    SC.save(pdir, done_inv)
    good = copy.deepcopy(facts)
    good["gaps"] = {"I03": {"I01": 2.0}}
    good["items"] = {k: v for k, v in good["items"].items() if k in ("I01", "I03")}
    SD.write([pkg], "bin9", inventory=done_inv, facts=good, project=pdir, model="bin9")
    from satk.look import review as RV

    defn = RV_regions("prop")
    doc = RV.leak_report({"gaps": [], "views": []}, subject=str(dff), kind="prop", dff=str(dff),
                         cover=RV.coverage(defn, [r["name"] for r in defn["regions"] if r.get("leak", True)]))
    RV.write_record(doc)
    RV.write_leak(pkg, doc)
    st = CK.strict_status(_subject(dff), {"rows": []})
    assert st == {"inventory": st["inventory"], "leak": st["leak"], "done": True, "blocking": []}
    assert st["inventory"]["built"] == 2 and st["leak"]["gaps"] == 0

    dff.write_bytes(build_dff("bin", "lid", "hinge", "badge", "handle", "rim", "extra"))   # a new export
    st = CK.strict_status(_subject(dff), {"rows": []})
    assert "leak: bin9.leak.json is older than the model: run look.leak on the new export" in st["blocking"]
