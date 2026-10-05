"""SID grammar, canonical form and providers (SPEC §3.2)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.core.ids import (
    KIND_OWNER,
    KINDS,
    LAYERED_KINDS,
    Sid,
    is_sid,
    provider_for,
    providers,
    register_provider,
    unregister_provider,
)

# Every kind from the SPEC §3.2 table, with the table's own examples (already canonical).
EXAMPLES: dict[str, list[str]] = {
    "model": ["model:411", "model:infernus"],
    "dff": ["dff:infernus"],
    "txd": ["txd:vehicle"],
    "tex": ["tex:bistro/vent_64"],
    "pix": ["pix:3fa2" + "0" * 20],
    "file": ["file:models/gta3.img/infernus.dff"],
    "col": ["col:sm_bush"],
    "ide": ["ide:data/maps/la/lae2.ide"],
    "ipl": ["ipl:lae2_stream0"],
    "inst": ["inst:lae2_stream0#4"],
    "item": ["item:lae2#enex#3"],
    "zone": ["zone:gan1"],
    "ifp": ["ifp:ped"],
    "anim": ["anim:ped/walk_civi"],
    "fn": ["fn:0x53bf09", "fn:cped::update"],
    "g": ["g:0xc8d4c0"],
    "vt": ["vt:0x86c538"],
    "patch": ["patch:upstream/hookpos_cstreaming_update_caller"],
    "el": ["el:object/412"],
    "bm": ["bm:grove_center"],
    "cap": ["cap:20261004-153201-ab12"],
    "note": ["note:17"],
}
ALL_EXAMPLES = [s for v in EXAMPLES.values() for s in v]


def test_examples_cover_every_kind():
    assert set(EXAMPLES) == set(KINDS) == set(KIND_OWNER)


@pytest.mark.parametrize("s", ALL_EXAMPLES)
def test_roundtrip_canonical(s):
    x = Sid.parse(s)
    assert str(x) == s
    assert Sid.parse(str(x)) == x
    assert hash(Sid.parse(str(x))) == hash(x)


@pytest.mark.parametrize("kind", sorted(LAYERED_KINDS))
def test_roundtrip_with_layer(kind):
    key = EXAMPLES[kind][0].split(":", 1)[1]
    x = Sid(kind, key, "modloader:hd_cars")
    s = str(x)
    assert s.endswith("@modloader:hd_cars")
    assert Sid.parse(s) == x
    assert x.without_layer() == Sid(kind, key)


@pytest.mark.parametrize(
    "raw, canonical",
    [
        ("MODEL:0411", "model:411"),
        ("model:Infernus", "model:infernus"),
        ("FN:0x0053BF09", "fn:0x53bf09"),
        ("g:0X00C8D4C0", "g:0xc8d4c0"),
        ("tex:Bistro/Vent_64", "tex:bistro/vent_64"),
        ("file:models\\gta3.img\\INFERNUS.DFF", "file:models/gta3.img/infernus.dff"),
        ("file:./models//gta3.img", "file:models/gta3.img"),
        ("inst:lae2_stream0#04", "inst:lae2_stream0#4"),
        ("  txd:vehicle  ", "txd:vehicle"),
        ("dff:infernus@SAMP", "dff:infernus@samp"),
    ],
)
def test_normalization(raw, canonical):
    assert str(Sid.parse(raw)) == canonical


@pytest.mark.parametrize("kind", ["file", "ide"])
@pytest.mark.parametrize("key", ["models/ /", ". /", "c0x:c.\x85/", "./\u2003/", "models/ /x.ide"])
def test_path_components_cannot_hide_whitespace_from_normalization(kind, key):
    with pytest.raises(SatkError) as exc:
        Sid(kind, key)
    assert exc.value.code == "BAD_ID"


@pytest.mark.parametrize("kind", ["file", "ide"])
def test_path_normalization_roundtrips_with_spaces_and_unicode(kind):
    for key in ("./models//my car.dff/", "./data/maps/улица.ide/", "data/some map.ide", "./models/./",
                "data/ some map /entry.ide", "models/\x85named\x85/file.dff"):
        sid = Sid(kind, key, "modloader:custom")
        assert Sid.parse(str(sid)) == sid
        assert Sid.parse(str(sid.without_layer())) == sid.without_layer()


@pytest.mark.parametrize(
    "bad",
    [
        "infernus",                 # no kind
        "modle:411",                # unknown kind
        "model:",                   # empty key
        "file:D:/ws/x.dff",         # absolute path
        "file:/models/x.dff",       # absolute path
        "file:models/../x.dff",     # traversal
        "inst:lae2_stream0",        # no #idx
        "inst:lae2#x",              # idx not a number
        "item:lae2#3",              # needs ipl#sec#idx
        "tex:vent_64",              # needs txd/texture
        "pix:abc",                  # not 24 hex
        "note:abc",                 # not an int
        "fn:0xZZ",                  # bad hex
        "fn:0x1@samp",              # layer on a non-asset kind
        "bm:x\x01y",                # control char
    ],
)
def test_bad_id(bad):
    with pytest.raises(SatkError) as ei:
        Sid.parse(bad)
    e = ei.value
    assert e.code == "BAD_ID"
    assert e.hint == "satk help ids"
    assert e.to_dict()["ok"] is False


def test_bad_kind_lists_valid_kinds_and_suggests():
    with pytest.raises(SatkError) as ei:
        Sid.parse("modle:411")
    assert ei.value.data["valid_kinds"] == list(KINDS)
    assert "model:411" in ei.value.did_you_mean


def test_layer_rejected_on_non_layered_kind():
    with pytest.raises(SatkError) as ei:
        Sid("bm", "x", layer="samp")
    assert ei.value.code == "BAD_ID"


def test_helpers():
    assert Sid.parse("fn:0x53bf09").addr == 0x53BF09
    assert Sid.parse("fn:cped::update").addr is None
    assert Sid.parse("model:411").num == 411
    assert Sid.parse("model:infernus").num is None
    assert Sid.parse("item:lae2#enex#3").parts == ("lae2", "enex", "3")
    assert Sid.parse("tex:bistro/vent_64").parts == ("bistro", "vent_64")
    assert Sid.parse("fn:0x1").owner == "re"
    assert Sid.parse("note:1").owner == "notes"
    assert is_sid("model:411") and not is_sid("nope")
    assert Sid.parse(Sid.parse("txd:vehicle")) == Sid("txd", "vehicle")


def test_sid_is_immutable():
    x = Sid.parse("model:411")
    with pytest.raises(Exception):
        x.key = "1"  # type: ignore[misc]


class _FakeProvider:
    kinds = ("el", "bm")

    def get(self, sid, fields, profile):
        return {"ok": True, "id": str(sid)}

    def find(self, q, kind, limit, cursor, profile):
        return {"ok": True, "cols": ["id"], "rows": [], "n": 0, "total": 0, "next": None}

    def refs(self, sid, rel, limit, cursor, profile):
        return {"ok": True, "rels": {}}


@pytest.fixture
def no_providers(monkeypatch, isolated_ops):
    """Empty provider table and no discovery (independent of what other WPs register)."""
    import satk.core.ids as ids

    monkeypatch.setattr(ids, "_providers", {})


def test_provider_registry(no_providers):
    p = _FakeProvider()
    try:
        register_provider(p)
        assert provider_for("el") is p and provider_for("BM") is p
        assert providers()["el"] is p
        register_provider(p)  # same object again: no-op
        other = _FakeProvider()
        with pytest.raises(ValueError):
            register_provider(other)
        register_provider(other, replace=True)
        assert provider_for("el") is other
        unregister_provider(other)
    finally:
        unregister_provider(p)
    assert "el" not in providers()


def test_provider_missing_is_not_ready(no_providers):
    assert "cap" not in providers()
    with pytest.raises(SatkError) as ei:
        provider_for("cap")
    assert ei.value.code == "NOT_READY"
    assert ei.value.data["owner"] == "satk.viewer"


def test_provider_unknown_kind(no_providers):
    with pytest.raises(SatkError) as ei:
        provider_for("nope")
    assert ei.value.code == "BAD_ID"
    with pytest.raises(ValueError):
        register_provider(type("P", (), {"kinds": ("nope",)})())
