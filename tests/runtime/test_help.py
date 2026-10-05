"""``satk help`` / MCP ``satk_help`` (SPEC §4.7 #1, WP-06 acceptance 3)."""

from __future__ import annotations

import re

import pytest

from satk.core import ids as I
from satk.core import registry as R
from satk.core.errors import SatkError
from satk.runtime import help as H


def test_help_json_lists_all_operations_by_group(run_cli):
    r = run_cli(["help", "start", "--json"])
    assert r.code == 0
    env = r.json
    assert env["topic"] == "start" and "satk_status" in env["text"]
    groups = env["groups"]
    assert "help [satk_help]" in groups["core"] and "status [satk_status]" in groups["core"]
    assert "note [note]" in groups["note"] and "doctor" in groups["core"]
    listed = [x.split(" [")[0] for g in groups.values() for x in g]
    assert sorted(listed) == sorted(o.cli for o in R.all_ops())  # every operation exactly once


def test_help_ids_has_sid_table(run_cli):
    text = run_cli(["help", "ids"]).json["text"]
    assert "| kind | key | example |" in text
    for kind in I.KINDS:
        assert re.search(rf"\|[^|\n]*\b{kind}\b[^|\n]*\|", text), kind
    assert "inst:lae2_stream0#4" in text and "note:17" in text
    assert "Providers loaded now:" in text and "note" in text


def test_blender_help_points_to_job_artifacts(run_cli):
    text = run_cli(["help", "blender"]).json["text"]
    assert "PNGs under work/blender/jobs/<id>/" in text
    assert "work/out/exports" in text
    # Extension ZIPs still use work/out/blender; .blend and PNG jobs do not.
    assert "work/out/blender and work/out/exports" not in text


def test_help_all_ru_is_russian_command_table(run_cli):
    env = run_cli(["help", "--all", "--ru"]).json
    assert env["cols"] == ["group", "command", "mcp", "summary_ru"]
    assert len(env["rows"]) == len(R.all_ops())
    assert any(re.search("[а-яА-Я]", row[3]) for row in env["rows"])
    assert ["core", "satk status", "satk_status"] in [row[:3] for row in env["rows"]]
    tty = run_cli(["help", "--all", "--ru"], tty=True)
    assert "satk_help" in tty.out and "Справка" in tty.out


@pytest.mark.parametrize("topic", list(H.TOPICS))
def test_every_topic_renders_within_budget(topic):
    env = H.render(topic)
    assert env["topic"] == topic
    text = env.get("text")
    assert isinstance(text, str) and text.strip()
    assert len(text) <= H.MAX_TOPIC_CHARS, (topic, len(text))


@pytest.mark.parametrize("name,expect", [
    ("satk_status", "status"), ("status", "status"), ("note add", "note.add"), ("note.add", "note.add"),
    ("satk note add", "note.add"), ("dev gen-docs", "dev.gen_docs"), ("dev.gen_docs", "dev.gen_docs"),
    ("mcp selftest", "mcp.selftest"),
])
def test_operation_topics(name, expect):
    env = H.render(name)
    assert env["op"] == expect
    assert "Parameters" in env["text"] or not R.get_op(expect).params


def test_operation_topic_cli(run_cli):
    env = run_cli(["help", "note"]).json
    assert env["mcp"] == "note" and "query" in env["text"] and "Examples:" in env["text"]


def test_unknown_topic_suggests():
    with pytest.raises(SatkError) as e:
        H.render("idz")
    assert e.value.code == "BAD_PARAMS" and "ids" in e.value.did_you_mean


def test_planned_tool_not_merged_is_not_ready(isolated_ops):
    with pytest.raises(SatkError) as e:
        H.render("asset_find")
    assert e.value.code == "NOT_READY" and "satk.index" in e.value.msg


def test_domain_topics_pick_up_new_operations(isolated_ops):
    @R.op("view.capture", summary="Capture a frame.", summary_ru="Снять кадр.")
    def _cap(w: int = 960) -> dict:
        return {}

    @R.op("re.addr", summary="Resolve addresses.", summary_ru="Разрешить адреса.")
    def _addr(text: str) -> dict:
        return {}

    assert "`view_capture`" in H.render("viewer")["text"]
    assert "`re_addr`" in H.render("re")["text"]
    assert "(none yet" in H.render("blender")["text"]
    tools = H.render("tools")["text"]
    assert "view_capture(w=960)" in tools and "re_addr(text)" in tools
    assert H.render("view_capture")["op"] == "view.capture"


def test_register_topic():
    H.register_topic("zz_test_topic", "test", "hello")
    try:
        assert H.render("zz_test_topic") == {"topic": "zz_test_topic", "text": "hello"}
    finally:
        H.TOPICS.pop("zz_test_topic")


def test_schema_topic_lists_notes_tables():
    text = H.render("schema")["text"]
    assert "note(id, sid, author, lang, text, tags, confidence, evidence, created_at)" in text
    assert "note_fts [fts5]" in text and "bookmark(" in text and "capture(" in text
    assert "note_fts_data" not in text  # FTS shadow tables hidden


def test_op_signature():
    sig = H.op_signature(R.op_by_mcp("note"))
    assert sig.startswith('note(action="add", id=null, text=null')
