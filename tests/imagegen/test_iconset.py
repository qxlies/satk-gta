"""generate, key, icon_set and finalize with a mocked endpoint; the fake key must appear nowhere."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PIL")
pytest.importorskip("numpy")

from satk.core.errors import SatkError  # noqa: E402
from satk.imagegen import client as C  # noqa: E402
from satk.imagegen import keying as K  # noqa: E402
from satk.imagegen.ops import finalize, generate, icon_set, key, placeholder  # noqa: E402

from .conftest import FAKE_KEY, FakeEndpoint, all_text, badge_image, ok_body, png_of, text_only_body  # noqa: E402


@pytest.fixture
def endpoint(monkeypatch, fake_key):
    def install(script):
        ep = FakeEndpoint(script)
        monkeypatch.setattr(C, "TRANSPORT", ep)
        return ep

    return install


def no_key_anywhere(root: Path):
    hits = [str(p) for p, data in all_text(root) if FAKE_KEY.encode() in data]
    assert hits == []
    # base64 of a Bearer header would also be a leak
    import base64

    needle = base64.b64encode(f"Bearer {FAKE_KEY}".encode())[:24]
    assert [p for p, data in all_text(root) if needle in data] == []


def test_generate_writes_pngs_and_sidecars(satk_home, endpoint):
    png = png_of(badge_image(64))
    ep = endpoint([(200, ok_body(png))])
    env = generate("flat padlock icon", n=2, name="Lock Test")
    assert env["calls"] == 2 and len(ep.requests) == 2
    out = Path(env["dir"])
    assert (out / "lock_test_1.png").read_bytes() == png
    rec = json.loads((out / "lock_test_1.png.provenance.json").read_text(encoding="utf-8"))
    assert rec["tool"] == "satk imagegen.generate" and rec["endpoint_host"] == "api.rout.my"
    assert rec["model"] == C.DEFAULT_MODEL and rec["prompt"] == "flat padlock icon"
    assert rec["params"] == {"aspect_ratio": "1:1", "candidate": 1, "image_size": "1K"}
    assert rec["response_text"] == "Here is your icon." and rec["finish_reason"] == "stop"
    assert len(rec["sha256_raw"]) == 64 and len(rec["sha256"]) == 64 and rec["time_utc"].endswith("Z")
    assert "steps" in rec and "Authorization" not in json.dumps(rec)
    no_key_anywhere(out)


def test_generate_no_image_keeps_a_failed_sidecar_and_continues(satk_home, endpoint):
    png = png_of(badge_image(64))
    ep = endpoint([(200, text_only_body("I will not.", "content_filter")), (200, ok_body(png))])
    env = generate("x", n=2)
    assert [r[1] for r in env["rows"]] == [False, True]
    assert any("NO_IMAGE" in w for w in env["warn"])
    out = Path(env["dir"])
    rec = json.loads((out / "image_1.failed.provenance.json").read_text(encoding="utf-8"))
    assert rec["error_code"] == "NO_IMAGE" and rec["response_text"] == "I will not."
    assert rec["finish_reason"] == "content_filter" and rec["ok"] is False
    assert not (out / "image_1.png").exists() and (out / "image_2.png").exists()
    no_key_anywhere(out)


def test_generate_all_failed_raises_with_codes(satk_home, endpoint):
    endpoint([(200, text_only_body("no"))])
    with pytest.raises(SatkError) as e:
        generate("x", n=2)
    assert e.value.data["imagegen_code"] == "NO_IMAGE" and e.value.data["calls"] == 2


def test_generate_401_stops_the_run(satk_home, endpoint):
    ep = endpoint([(401, b"nope")])
    with pytest.raises(SatkError) as e:
        generate("x", n=4)
    assert e.value.code == "AUTH" and e.value.data["imagegen_code"] == "AUTH_FAILED"
    assert len(ep.requests) == 1
    assert FAKE_KEY not in json.dumps(e.value.to_dict())
    no_key_anywhere(satk_home)


def test_generate_5xx_retry_counts_two_calls_and_gives_endpoint_failed(satk_home, endpoint):
    ep = endpoint([(500, b"x")])
    with pytest.raises(SatkError) as e:
        generate("x", n=1)
    assert e.value.data["imagegen_code"] == "ENDPOINT_FAILED" and len(ep.requests) == 2 and e.value.data["calls"] == 2


def test_generate_without_key(satk_home, monkeypatch):
    monkeypatch.delenv("SATK_IMAGEGEN_API_KEY", raising=False)
    with pytest.raises(SatkError) as e:
        generate("x")
    assert e.value.data["imagegen_code"] == "NO_API_KEY"


def test_generate_checks_params(satk_home, fake_key):
    for kw in ({"prompt": " "}, {"prompt": "x", "n": 5}, {"prompt": "x", "aspect": "7:3"}, {"prompt": "x", "size": "9K"}):
        with pytest.raises(SatkError) as e:
            generate(**kw)
        assert e.value.code == "BAD_PARAMS"


def test_key_op_on_a_raw_file(satk_home, tmp_path):
    raw = Path(satk_home) / "work" / "tmp" / "raw.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(png_of(badge_image(512)))
    env = key([str(raw)], sizes=[24, 48], name="Lock")
    assert [r[0].rsplit("/", 1)[1] for r in env["rows"]] == ["lock_24.png", "lock_48.png"]
    assert all(r[2] for r in env["rows"])
    rec = json.loads(Path(env["rows"][0][0] + ".provenance.json").read_text(encoding="utf-8"))
    assert [s["step"] for s in rec["steps"]] == ["key", "despill", "trim", "pad", "resize", "sharpen"]
    assert rec["key_params"]["hard"] == 90 and rec["key_params"]["soft"] == 130 and rec["validation"]["ok"]
    with pytest.raises(SatkError):
        key([])
    with pytest.raises(SatkError) as e:
        key([str(raw.with_name("missing.png"))])
    assert e.value.code == "NOT_FOUND"


def test_key_strict_fails_on_invalid_and_warns_otherwise(satk_home, monkeypatch):
    raw = Path(satk_home) / "work" / "tmp" / "tiny.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(png_of(badge_image(256)))
    monkeypatch.setattr(K, "validate_image", lambda im: {"ok": False, "corners": [0, 0, 0, 0], "magenta_pixels": 3,
                                                         "coverage": 0.9, "errors": ["3 magenta pixels left"]})
    env = key([str(raw)], sizes=[64])
    assert env["rows"][0][2] is False and any("CHECK_FAILED" in w for w in env["warn"])
    with pytest.raises(SatkError) as e:
        key([str(raw)], sizes=[64], strict=True)
    assert e.value.code == "CHECK_FAILED"


def responder(per_icon_png):
    """Answer by looking at the prompt: a different valid badge per request."""
    state = {"n": 0}

    def f(req):
        state["n"] += 1
        return 200, ok_body(png_of(badge_image(384, fill=0.78 + 0.01 * (state["n"] % 3))))

    return f


def test_icon_set_run_sheet_and_finalize(satk_home, endpoint):
    ep = endpoint([responder(None)])
    env = icon_set(candidates=2, max_calls=24)
    assert env["calls"] == 10 and len(ep.requests) == 10
    assert len(env["rows"]) == 10 and all(r[3] for r in env["rows"])
    assert env["icons_without"] == [] and Path(env["sheet"]).is_file() and Path(env["small_sheet"]).is_file()
    run = Path(env["dir"])
    for name in ("run.json", "candidates.json"):
        assert (run / name).is_file()
    assert (run / "raw" / "lock_c2.png.provenance.json").is_file()
    rec = json.loads((run / "keyed" / "lock_c2_24.png.provenance.json").read_text(encoding="utf-8"))
    assert rec["icon"] == "lock" and rec["size"] == 24 and rec["validation"]["ok"] and rec["prompt_id"] == "sae-v1-style-1"
    assert "a closed padlock" in rec["prompt"] and "#FBBC04" in rec["prompt"] and "{" not in rec["prompt"]
    assert rec["sha256_raw"] != rec["sha256"] and rec["raw_file"] == "raw/lock_c2.png"
    # the request carried the filled style prompt
    assert "single low rounded hill" in ep.requests[0]["body"]["messages"][0]["content"]
    # pick: classic from candidate 2, the rest default; lock from the placeholder
    fin = finalize(picks={"classic": 2}, use_placeholder=["lock"])
    fdir = Path(fin["dir"])
    names = {r[0] for r in fin["rows"]}
    assert len(names) == 13 and fin["chosen"]["classic"] == 2 and fin["chosen"]["lock"] == "placeholder"
    assert (fdir / "PROVENANCE.md").is_file()
    md = (fdir / "PROVENANCE.md").read_text(encoding="utf-8")
    assert "google/gemini-3.1-flash-image-preview" in md and "placeholder" in md and "sae-v1-style-1" in md
    assert (fdir / "preset_classic_128.png").read_bytes() == (run / "keyed" / "preset_classic_c2_128.png").read_bytes()
    for r in fin["rows"]:
        assert K.validate_image(K.load_image(fdir / r[0]))["ok"]
    no_key_anywhere(run)


def test_icon_set_budget_is_a_hard_cap(satk_home, endpoint):
    ep = endpoint([responder(None)])
    env = icon_set(candidates=3, max_calls=4)
    assert env["calls"] == 4 and len(ep.requests) == 4
    codes = [r[4] for r in env["rows"]]
    assert codes.count("SKIPPED") == 11


def test_icon_set_retry_counts_against_the_budget(satk_home, endpoint):
    png_ok = (200, ok_body(png_of(badge_image(384))))
    ep = endpoint([(500, b"x"), png_ok])
    env = icon_set(candidates=1, max_calls=3, only=["lock", "high"])
    assert env["calls"] == 3  # lock: 500 + retry ok (2), high: 1 request, budget exhausted after
    assert [r[3] for r in env["rows"]] == [True, True]


def test_icon_set_no_image_continues_and_records_codes(satk_home, endpoint):
    ok = (200, ok_body(png_of(badge_image(384))))
    endpoint([(200, text_only_body("sorry")), ok, ok])
    env = icon_set(candidates=3, only=["lock"])
    assert env["calls"] == 3 and [r[3] for r in env["rows"]] == [False, True, True]
    assert env["error_codes"] == ["NO_IMAGE"] and env["icons_with_valid_candidate"] == ["lock"]
    run = Path(env["dir"])
    assert json.loads((run / "raw" / "lock_c1.failed.provenance.json").read_text(encoding="utf-8"))["response_text"] == "sorry"


def test_icon_set_auth_failure_stops_everything(satk_home, endpoint):
    ep = endpoint([(403, b"denied")])
    with pytest.raises(SatkError) as e:
        icon_set(candidates=3)
    assert e.value.data["imagegen_code"] == "AUTH_FAILED" and len(ep.requests) == 1
    no_key_anywhere(satk_home)


def test_icon_set_all_no_image_raises_with_codes(satk_home, endpoint):
    endpoint([(200, text_only_body("no"))])
    with pytest.raises(SatkError) as e:
        icon_set(candidates=1, only=["lock"])
    assert e.value.data["imagegen_code"] == "NO_IMAGE" and e.value.data["codes"] == ["NO_IMAGE"]
    run = Path(satk_home) / "work" / "out" / "imagegen" / "sae-v1"
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["error_codes"] == ["NO_IMAGE"]


def test_icon_set_two_endpoint_failures_in_a_row_stop_the_run(satk_home, endpoint):
    ep = endpoint([(503, b"down")])
    with pytest.raises(SatkError) as e:
        icon_set(candidates=3)
    assert e.value.data["imagegen_code"] == "ENDPOINT_FAILED" and len(ep.requests) == 4  # 2 candidates x 2 tries


def test_icon_set_dry_run_needs_no_key_and_makes_no_request(satk_home, monkeypatch):
    monkeypatch.delenv("SATK_IMAGEGEN_API_KEY", raising=False)
    env = icon_set(dry_run=True)
    assert env["dry_run"] and env["planned_calls"] == 15 and env["endpoint_host"] == "api.rout.my"


def test_icon_set_invalid_candidates_are_flagged_not_shipped(satk_home, endpoint):
    from PIL import Image

    # a raw image that is one flat colour: no badge after keying
    flat = png_of(Image.new("RGB", (64, 64), (255, 0, 255)))
    endpoint([(200, ok_body(flat))])
    with pytest.raises(SatkError):
        icon_set(candidates=1, only=["lock"])
    run = Path(satk_home) / "work" / "out" / "imagegen" / "sae-v1"
    leg = json.loads((run / "candidates.json").read_text(encoding="utf-8"))["legend"]
    assert leg[0]["valid"] is False and "KEY_FAILED" in leg[0]["errors"][0]
    with pytest.raises(SatkError) as e:
        finalize()
    assert e.value.code == "CHECK_FAILED"


def test_finalize_without_run_fails_cleanly(satk_home):
    with pytest.raises(SatkError) as e:
        finalize()
    assert e.value.code == "CHECK_FAILED" and "classic" in e.value.data["missing"]
    # the placeholder substitution makes it complete without any AI run
    placeholder()
    env = finalize(use_placeholder=["classic", "balanced", "high", "extreme", "lock"])
    assert len(env["rows"]) == 13 and set(env["chosen"].values()) == {"placeholder"}


def test_finalize_picks_are_contact_sheet_numbers(satk_home, endpoint):
    endpoint([responder(None)])
    icon_set(candidates=2, only=["classic", "balanced"])
    # sheet numbers: 1,2 = classic; 3,4 = balanced
    with pytest.raises(SatkError) as e:
        finalize(picks={"balanced": 2}, use_placeholder=["high", "extreme", "lock"])
    assert e.value.code == "BAD_PARAMS" and "classic" in e.value.msg and "balanced=3" in e.value.did_you_mean
    env = finalize(picks={"classic": 1, "balanced": 4}, use_placeholder=["high", "extreme", "lock"])
    assert env["chosen"]["balanced"] == 2 and env["chosen"]["classic"] == 1
    run = Path(satk_home) / "work" / "out" / "imagegen" / "sae-v1"
    assert (Path(env["dir"]) / "preset_balanced_64.png").read_bytes() == (run / "keyed" / "preset_balanced_c2_64.png").read_bytes()
