"""The dedup census against the vanilla index (read-only; marker ``game``).

The golden numbers (``tests/golden/pack_census_vanilla.json``, numbers only) are the census of the stock 1.0 US copy;
the same index gives the figures the architecture contract quotes: 32,874 active textures with 624.8 MiB of base
level data, 14,559 distinct texel hashes (282.6 MiB), 14,811 distinct (hash, name) pairs (285.0 MiB).
"""

from __future__ import annotations

import json
import os

import pytest

from satk.index.api import index_path
from satk.pack.census import MIB, run_census

pytestmark = pytest.mark.game


@pytest.fixture(scope="module")
def golden() -> dict:
    from satk.core.config import REPO_ROOT

    return json.loads((REPO_ROOT / "tests" / "golden" / "pack_census_vanilla.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vanilla_index(golden):
    p = index_path("vanilla")
    if not p.is_file():
        if os.environ.get("SATK_TEST_NO_SKIP") == "1":
            pytest.fail(f"no vanilla index: {p}", pytrace=False)
        pytest.skip(f"no vanilla index: {p}")
    return p


def _same(summary: dict, want: dict, path: str = "") -> None:
    for k, v in want.items():
        assert k in summary, f"{path}{k} missing"
        if isinstance(v, dict):
            _same(summary[k], v, f"{path}{k}.")
        else:
            assert summary[k] == v, f"{path}{k}: {summary[k]} != {v}"


def test_vanilla_census_matches_the_golden_numbers(vanilla_index, golden):
    res = run_census("vanilla")
    _same(res["summary"], golden["index"])
    assert [[g["name"], g["copies"], g["txds"]] for g in res["groups"][:len(golden["top_groups"])]] == golden["top_groups"]


def test_vanilla_census_reproduces_the_contract_figures(vanilla_index):
    s = run_census("vanilla")["summary"]
    assert s["textures"] == 32874 and round(s["mip0_bytes"] / MIB, 1) == 624.8
    k = s["keys"]
    assert k["texel"]["distinct"] == 14559 and round(k["texel"]["unique_mip0_bytes"] / MIB, 1) == 282.6
    assert k["texel"]["saved_pct_mip0"] == 54.8
    assert k["texel_name"]["distinct"] == 14811 and round(k["texel_name"]["unique_mip0_bytes"] / MIB, 1) == 285.0
    assert k["texel_name"]["saved_pct_mip0"] == 54.4
    # the extra fields of the residency key cost only the mip count: 388 groups ship both with and without mips
    assert set(s["name_key_splits"]) == {"levels"} and k["full"]["distinct"] == k["texel_name"]["distinct"] + 388
    # a TXD cannot hold two textures of one name, so there is nothing to share inside a TXD
    assert s["full_key_within_txd_only"]["saved_chain_bytes"] == 0 and s["chain_formula_mismatch"] == 0


def test_vanilla_exact_chain_hashes(vanilla_index, golden):
    res = run_census("vanilla", exact=True)
    _same(res["summary"], golden["exact"])
    assert res["warn"] == ["EXACT_SKIPPED: 23 texture(s) of a platform other than D3D9 keep the index hash"]
