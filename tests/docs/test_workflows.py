"""satk.docs.workflows and ``satk dev workflow-cost`` (WP-12): counting and cost model, no game files."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.core.registry import invoke, op
from satk.docs import workflows as W


def test_png_size_and_image_tokens(tmp_path):
    from satk.saap import png

    p = png.write(tmp_path / "x.png", 960, 540, bytes(960 * 540 * 3))
    assert W.png_size(p) == (960, 540)
    assert W.image_tokens(960, 540) == 691  # SPEC §3.5: ~690 tokens for a 960x540 capture
    (tmp_path / "bad.png").write_bytes(b"not a png at all, really")
    with pytest.raises(SatkError):
        W.png_size(tmp_path / "bad.png")


def test_session_counts_calls_chars_and_images(isolated_ops, tmp_path):
    @op("t.echo", summary="echo", summary_ru="эхо", mcp="t_echo")
    def echo(text: str) -> dict:
        """Echo.

        Args:
            text: what to return.
        """
        return {"ok": True, "text": text}

    @op("t.fail", summary="fail", summary_ru="ошибка", mcp="t_fail")
    def fail() -> dict:
        raise SatkError("NOT_FOUND", "nothing here")

    from satk.saap import png

    img = png.write(tmp_path / "i.png", 75, 100, bytes(75 * 100 * 3))
    s = W.Session()
    env = s.call("t_echo", text="x" * 70)
    assert env["text"] == "x" * 70
    s.read_image(img)
    assert s.n == 2 and s.images == 1
    text_tokens = s.calls[0].tokens
    assert text_tokens == round((len('{"text":"' + "x" * 70 + '"}') + s.calls[0].chars) / W.CHARS_PER_TOKEN)
    assert s.calls[1].tokens == 10 and s.tokens == text_tokens + 10  # 75*100/750
    with pytest.raises(SatkError) as e:
        s.call("t_fail")
    assert e.value.code == "NOT_FOUND" and s.n == 3 and s.calls[-1].ok is False
    lax = W.Session(strict=False)
    assert lax.call("t_fail")["ok"] is False


def test_run_reports_skip_and_budget(monkeypatch):
    def needs_nothing(s, **_):
        raise W.Skip("component not built")

    monkeypatch.setitem(W.WORKFLOWS, "S9", (needs_nothing, "test", 1))
    r = W.run("S9")
    assert r["status"] == "skip" and r["reason"] == "component not built" and r["calls"] == 0


def test_workflow_cost_rejects_unknown_ids():
    env = invoke("dev.workflow_cost", {"workflows": ["S42"]})
    assert env["ok"] is False and env["error"]["code"] == "BAD_PARAMS"
    assert "S1" in env["error"]["did_you_mean"]


def test_every_documented_workflow_is_executable():
    assert sorted(W.WORKFLOWS) == [f"S{i}" for i in range(1, 9)]
    for wid, (fn, question, budget) in W.WORKFLOWS.items():
        assert callable(fn) and question and 1 <= budget <= 8, wid
