"""Templates (bundled core opcode subset) and the opcode database."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.script.asm import assemble, parse_text
from satk.script.check import check_program
from satk.script.disasm import disassemble
from satk.script.opdb import OpcodeDB, load_db, parse_param
from satk.script.templates import TEMPLATES, render, script_name, templates


@pytest.fixture(scope="module")
def core():
    return load_db("core")


def _commands(text: str, db) -> list[tuple[int, list]]:
    """(opcode word, parameters without label names) of every instruction in a text."""
    ps, _ = parse_text(text, db)
    assert not ps.errors, ps.errors
    out = []
    for _sec, it in ps.items:
        if hasattr(it, "opw"):
            out.append((it.opw, [("label",) if getattr(a, "kind", "") == "label" else (a.t, a.v) for a in it.args]))
    return out


def test_template_list_matches_index():
    assert set(TEMPLATES) == set(templates())


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_assembles_cleanly_and_disassembles_back(name, core):
    src = render(name, f"t_{name}")
    r = assemble(src, core)
    assert r.warnings == [] and r.kind == "cleo" and r.ext == ".cs"
    assert [f for f in check_program(r.program, r.lines) if f[0] != "info"] == []
    d = disassemble(r.data, "cleo", core)
    assert assemble(d.text, core).data == r.data
    assert disassemble(assemble(d.text, core).data, "cleo", core).text == d.text
    assert _commands(d.text, core) == _commands(src, core)      # the same commands, only labels renamed


def test_template_values_and_validation(core):
    src = render("spawn_car", "mycar", cheat="bike", model=522, text='say "hi"')
    assert "0247: request_model 522" in src and 'test_cheat "BIKE"' in src and r'"say \"hi\""' in src
    assert "script_name 'MYCAR'" in src
    src = render("teleport", "tp", pos=[1, -2.5, 3.25])
    assert "00A1: set_char_coordinates $3 1.0 -2.5 3.25" in src
    warn: list[str] = []
    render("hello", "h", model=400, warn=warn)
    assert warn and warn[0].startswith("UNUSED: --model")
    for kw, msg in ((dict(cheat="no spaces!"), "cheat"), (dict(key=999), "key"), (dict(pos=[1, 2]), "--pos"),
                    (dict(text="Ж"), "latin-1"), (dict(model=-5), "model")):
        tmpl = "teleport" if "pos" in kw else "spawn_car" if "model" in kw or "cheat" in kw else \
            "text" if "key" in kw else "hello"
        with pytest.raises(SatkError) as e:
            render(tmpl, "x", **kw)
        assert e.value.code == "BAD_PARAMS" and msg in e.value.msg + (e.value.hint or "")
    with pytest.raises(SatkError):
        render("hello", "bad name")
    assert script_name("my-long_script") == "MYLONG_"


def test_parse_param_forms():
    p = parse_param("x: float (variable)")
    assert (p.kind, p.type, p.source, p.name) == ("float", "float", "var", "x")
    assert parse_param("label").kind == "label" and parse_param("args: arguments").kind == "args"
    assert parse_param("modelId: model_vehicle").kind == "model" and parse_param("Car").kind == "int"
    assert parse_param("key: gxt_key").kind == "string" and parse_param("anim: AnimGroup").kind == "string"
    assert parse_param("int (global var)").describe() == "int (global var)"


def test_db_lookup_and_suggestions(core):
    c = core.find("WAIT")
    assert c is core.get(0x0001) and c.nfixed == 1 and not c.variadic and core.get(0x8001) is c
    call = core.find("cleo_call")
    assert call.variadic and call.nfixed == 2
    assert "wait" in core.suggest("wiat")
    assert core.describe().startswith("core (")
    with pytest.raises(SatkError) as e:
        load_db("nonsense")
    assert e.value.code == "BAD_PARAMS"


def test_extension_order_resolves_shared_ids():
    rows = [{"op": "0B20", "name": "A", "ext": "SAMPFUNCS", "input": ["int"]},
            {"op": "0B20", "name": "B", "ext": "CLEO+", "input": ["int", "int"]}]
    db = OpcodeDB.from_rows(rows, "t")
    assert db.get(0x0B20).name == "B" and db.preferring(("SAMPFUNCS",)).get(0x0B20).name == "A"
    assert [c.name for c in db.alternatives(0x0B20)] == ["A", "B"]


def test_auto_falls_back_to_core_without_kb(satk_home):
    from satk.script.opdb import reset_cache

    reset_cache()
    warn: list[str] = []
    db = load_db("auto", warn=warn)
    assert db.source == "core" and warn and warn[0].startswith("OPDB:")


def test_core_subset_agrees_with_the_full_db(core):
    try:
        full = load_db("kb")
    except SatkError:
        try:
            full = load_db("cleo-ai")
        except SatkError:
            pytest.skip("no kb and no cleo-ai clone")
    for c in core.commands:
        f = full.get(c.op)
        assert f is not None and f.name == c.name, c.name
        assert (f.nfixed, f.variadic) == (c.nfixed, c.variadic), c.name
        assert [p.kind for p in f.params] == [p.kind for p in c.params], c.name
