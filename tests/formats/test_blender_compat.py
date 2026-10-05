"""Acceptance 6 of WP-02: satk.formats is stdlib-only and runs in Blender's bundled Python 3.13.

Marked ``blender`` + ``game`` (skipped without Blender or the game copy).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from satk.core import config

pytestmark = [pytest.mark.blender, pytest.mark.game]

SRC = Path(__file__).resolve().parents[2] / "src"


def _blender_python() -> Path:
    exe = config.build().paths.get("blender")
    hits = sorted(Path(exe).parent.glob("*/python/bin/python.exe")) if exe else []
    if not hits:
        pytest.skip(f"Blender's bundled python not found next to {exe}")
    return hits[-1]


_SCRIPT = """
import sys, json
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from satk.formats import selftest
rc = selftest.main(['--root', sys.argv[2], '--quick'])      # acceptance 6, verbatim; prints one JSON line
from satk.formats.img import ImgArchive
from satk.formats.dff import scan_dff, decode_geometries
from satk.formats.txd import parse_txd, mip0_bytes
from satk.formats.dxt import decode_rgba, available_backends
root = Path(sys.argv[2])
with ImgArchive.open(root / "models" / "gta3.img") as a:
    dff = a.read(a.find("infernus.dff"))
    txd = a.read(a.find("vgnpwrmainbld.txd"))
t = [x for x in parse_txd(txd).textures if x.name.lower() == "sw_wallbrick_01"][0]
outs = {b: decode_rgba(t, mip0_bytes(txd, t), backend=b) for b in available_backends()}
print(json.dumps({"rc": rc, "tris": scan_dff(dff).tris, "dec": sum(len(m.tris) // 3 for m in decode_geometries(dff)),
                  "backends": available_backends(), "same": len(set(outs.values())) == 1,
                  "py": sys.version_info[:2]}))
"""


@pytest.fixture(scope="module")
def blender_run() -> tuple[dict, dict]:
    """ONE run of Blender's Python (interpreter + numpy start once): ``(selftest JSON, F2/F3 JSON)``."""
    py = _blender_python()
    root = config.build().paths.game
    if not (root / "gta_sa.exe").is_file():
        pytest.skip(f"game root not found: {root}")
    p = subprocess.run([str(py), "-c", _SCRIPT, str(SRC), str(root)], capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
    assert p.returncode == 0, (p.stdout[-2000:], p.stderr[-2000:])
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    assert len(lines) == 2, p.stdout[-2000:]
    return json.loads(lines[0]), json.loads(lines[1])


def test_selftest_quick_in_blender_python(blender_run):
    res, extra = blender_run
    assert extra["rc"] == 0                                 # selftest.main(...) exit code
    assert res["ok"] and res["quick"] and res["counts"]["col.models"] == 10169


def test_dff_and_dxt_in_blender_python(blender_run):
    """F2/F3 code paths (DFF decode, DXT python/numpy backends) also run under 3.13 (numpy, no Pillow)."""
    _res, res = blender_run
    assert res["tris"] == res["dec"] == 3072 and res["same"] and "python" in res["backends"]
    assert res["py"][0] == 3 and res["py"][1] >= 12
