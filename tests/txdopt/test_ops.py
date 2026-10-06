"""Registration of the txdopt operations: CLI only, English summaries, help, fast imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import satk
from satk.core.registry import MAX_SUMMARY, all_ops, get_op

OPS = ("texture.optimize", "texture.audit", "texture.budget")


def test_registered_cli_only_with_english_summaries():
    names = {o.name for o in all_ops()}
    for name in OPS:
        assert name in names
        o = get_op(name)
        assert o.mcp is False and o.group == "texture"
        assert o.summary.isascii() and len(o.summary) <= MAX_SUMMARY and o.summary_ru and o.examples
        for p in o.params:
            assert p.help, (name, p.name)
    assert [p.name for p in get_op("texture.optimize").params if p.positional] == ["target"]
    assert [p.name for p in get_op("texture.budget").params if p.positional] == []


def test_help_lists_the_flags(run_cli):
    r = run_cli(["texture", "optimize", "-h"])
    assert r.code == 0 and "--drop-unused" in r.out and "--max" in r.out and "--share" in r.out
    r = run_cli(["texture", "budget", "-h"])
    assert r.code == 0 and "--area" in r.out


def test_ops_module_imports_no_heavy_modules():
    code = ("import sys, satk.txdopt.ops, satk.txdopt.optimize, satk.txdopt.audit, satk.txdopt.budget; "
            "print(sorted(m for m in ('numpy', 'PIL') if m in sys.modules))")
    env = {**os.environ, "PYTHONPATH": str(Path(satk.__file__).resolve().parents[1])}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env).stdout
    assert out.strip() == "[]"
