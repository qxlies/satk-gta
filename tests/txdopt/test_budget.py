"""``satk texture budget`` on the synthetic index: totals equal the index sizes, areas, mod overlays, the limit."""

from __future__ import annotations

from satk.index.api import open_index
from satk.txdopt.budget import DEFAULT_LIMIT, stream_limit
from satk.txdopt.inputs import stream_size


def _budget(run_cli, *argv: str) -> dict:
    r = run_cli(["texture", "budget", *argv, "--json"])
    env = r.json
    assert r.code == 0 and env["ok"], r.out
    return env


def _expected(models) -> tuple[int, dict[str, int]]:
    """Unique streamed (non-loose) DFF + TXD blobs of the models, through the public index API."""
    db = open_index("vanilla")
    blobs: dict[str, int] = {}
    for mid in models:
        f = db.model_files(mid)
        for b in ([f.dff] if f.dff else []) + list(f.txd_chain):
            if b.path.suffix.lower() == ".img":
                blobs[b.sid] = b.size
    return sum(blobs.values()), blobs


def test_vanilla_totals_equal_the_index_sizes(ws, run_cli):
    env = _budget(run_cli, "--limit", "50")
    total, blobs = _expected([400, 1000, 1001, 1002])
    s = env["summary"]
    assert s["total"] == total and s["delta"] == 0 and s["vanilla"]["total"] == total
    assert {r[0]: r[2] for r in env["rows"]} == blobs
    assert "txd:vehicle" not in {r[0] for r in env["rows"]}, "vehicle.txd is loose: loaded at start, not streamed"
    assert s["models"] == 4 and s["limit"]["bytes"] == DEFAULT_LIMIT and s["largest_model"]["bytes"] > 0
    boxes = next(r for r in env["rows"] if r[0] == "txd:boxes")
    assert boxes[5] == 2 and boxes[3] == boxes[2] and boxes[4] == 0


def test_area_counts_placements(ws, run_cli):
    env = _budget(run_cli, "--area", "100", "200", "30")
    total, blobs = _expected([1000, 1001])
    s = env["summary"]
    assert s["scope"] == "area" and s["area"] == [100.0, 200.0, 30.0] and s["instances"] == 2
    assert s["total"] == total and {r[0] for r in env["rows"]} == set(blobs)
    assert next(r for r in env["rows"] if r[0] == "txd:boxes")[5] == 2
    assert s["limit"]["used_pct"] == round(100.0 * total / DEFAULT_LIMIT, 1)
    env = _budget(run_cli, "--area", "-5000", "-5000", "10")
    assert env["summary"]["total"] == 0 and env["rows"] == []
    r = run_cli(["texture", "budget", "--area", "1", "2", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_mod_overlay_adds_its_models_files_and_placements(ws, mod, run_cli):
    env = _budget(run_cli, "--profile", str(mod), "--sort", "delta", "--limit", "50")
    s = env["summary"]
    rows = {r[0]: r for r in env["rows"]}
    big = rows["txd:bigtxd"]
    assert big[2] == stream_size((mod / "models" / "bigtxd.txd").stat().st_size) and big[3] is None and big[4] == big[2]
    assert big[5] == 2 and s["profile"] == "mod:bigmap" and s["models"] == 8
    assert s["delta"] == s["total"] - s["vanilla"]["total"] > 25_000_000
    assert env["rows"][0][4] >= env["rows"][-1][4]                          # sorted by growth
    area = _budget(run_cli, "--profile", str(mod), "--area", "110", "205", "40")
    a = area["summary"]
    assert a["instances"] == 5 and {"txd:bigtxd", "txd:smalltxd", "txd:boxes"} <= {r[0] for r in area["rows"]}
    assert a["vanilla"]["total"] < a["total"]
    only = _budget(run_cli, "--profile", str(mod), "--kind", "dff", "--limit", "50")
    assert {r[1] for r in only["rows"]} == {"dff"} and only["summary"]["total"] == s["total"]


def test_over_limit_warning(ws, mod, run_cli, monkeypatch):
    import satk.txdopt.budget as b

    monkeypatch.setattr(b, "stream_limit", lambda: (1_000_000, "test"))
    env = _budget(run_cli, "--profile", str(mod), "--area", "110", "205", "40")
    assert any(w.startswith("OVER_LIMIT: this area needs") for w in env["warn"])
    env = _budget(run_cli, "--profile", str(mod))
    assert any(w.startswith("OVER_LIMIT: model") for w in env["warn"])


def test_limit_comes_from_the_kb_fact():
    n, src = stream_limit()
    assert n == 52_428_800 and src.startswith("kb fact streaming.memory")


def test_bad_profile(ws, run_cli):
    r = run_cli(["texture", "budget", "--profile", "nosuchprofile", "--json"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "mod folder" in r.json["error"]["hint"]
