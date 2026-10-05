"""Model export/preview regressions from review-3 and WP-05 verification."""

import json
import math
import struct
from array import array
from dataclasses import replace
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.model3d import api, batch, softrender, textures
from satk.model3d.gltf import build_glb, parse_glb
from satk.model3d.mesh import build_scene
from satk.model3d.obj import build_obj


def _handles(m):
    pixels = [(255, 0, 0, 255)] * 8 + [(0, 0, 255, 255)] * 8
    a = m.txd([m.native("repeat", 4, 4, pixels, addr=0x11)])
    b = m.txd([m.native("mirror", 4, 4, pixels, addr=0x12)])
    chain = textures.TxdChain([("a", a), ("b", b)])
    return chain, chain.find("repeat"), chain.find("mirror")


def test_decode_shares_pixels_but_not_sampler_state(m):
    _, a, b = _handles(m)
    for first, second in [(a, b), (b, a)]:
        textures.cache_clear()
        d1, d2 = textures.decode(first), textures.decode(second)
        assert (d1.uaddr, d1.vaddr) == (first.info.uaddr, first.info.vaddr)
        assert (d2.uaddr, d2.vaddr) == (second.info.uaddr, second.info.vaddr)
        assert d1.rgba is d2.rgba
        assert textures.cache_stats()["entries"] == 1


def test_decode_pixel_identity_includes_format_and_dimensions(m):
    _, a, _ = _handles(m)
    b = replace(a, info=replace(a.info, w=2, h=8))
    textures.cache_clear()
    assert (textures.decode(a).w, textures.decode(b).w) == (4, 2)


def test_render_and_glb_do_not_depend_on_previous_sampler(m):
    chain, a, b = _handles(m)
    sc = build_scene(m.box_dff(tex="mirror"), name="mirror")
    sc.meshes[0].uv[0] = array("f", [0.25, 1.25] * sc.verts)
    mats = textures.resolve_materials(sc.materials, chain)

    def outputs():
        prep = softrender.prepare(sc, mats)
        images, _ = softrender.render(prep, [(45, 25)], 48)
        return build_glb(sc, mats)[0], softrender.sheet_png(images)

    textures.cache_clear()
    cold = outputs()
    textures.cache_clear()
    textures.decode(a)
    warm = outputs()
    assert cold == warm
    # A wrap sampler really would produce a different image at these UVs.
    wrap_mats = [[replace(mats[0][0], tex=a)]]
    prep = softrender.prepare(sc, wrap_mats)
    wrap, _ = softrender.render(prep, [(45, 25)], 48)
    assert warm[1] != softrender.sheet_png(wrap)


def test_glb_and_soft_keep_distinct_texture_bindings(m):
    chain, _, _ = _handles(m)
    pos, tris, uv, _ = m.box()
    geom = m.geometry(pos, [(a, b, c, i % 2) for i, (a, b, c, _) in enumerate(tris)], uv=uv,
                      mats=[m.material(tex="repeat"), m.material(tex="mirror")])
    sc = build_scene(m.clump([geom], [(-1, "box", (0, 0, 0))], [(0, 0)]), name="box")
    mats = textures.resolve_materials(sc.materials, chain)
    textures.cache_clear()
    prep = softrender.prepare(sc, mats)
    assert [prep.tex_addr[i] for i in prep.mat_tex] == [(1, 1), (1, 2)]
    doc, _ = parse_glb(build_glb(sc, mats)[0])
    for mat, wrap in zip(doc["materials"], [10497, 33648]):
        tex = doc["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
        assert doc["samplers"][tex["sampler"]]["wrapT"] == wrap


def test_glb_preserves_names_for_identical_pixels(m):
    pos, tris, uv, _ = m.box()
    geom = m.geometry(pos, [(a, b, c, i % 2) for i, (a, b, c, _) in enumerate(tris)], uv=uv,
                      mats=[m.material(tex="carpback"), m.material(tex="carplate")])
    sc = build_scene(m.clump([geom], [(-1, "car", (0, 0, 0))], [(0, 0)]), name="car")
    chain = textures.TxdChain([("vehicle", m.solid_txd({
        "carpback": (10, 20, 30, 255), "carplate": (10, 20, 30, 255)}))])
    mats = textures.resolve_materials(sc.materials, chain)
    glb, stats = build_glb(sc, mats)
    doc, _ = parse_glb(glb)
    for primitive, name in zip(doc["meshes"][0]["primitives"], ["carpback", "carplate"]):
        mat = doc["materials"][primitive["material"]]
        tex = doc["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]
        assert mat["name"] == doc["images"][tex["source"]]["name"] == name
    assert stats["textures"] == build_obj(sc, mats, "car")[3]["textures"] == 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_exports_sanitize_both_uv_sets_without_mutating_source(m, bad):
    sc = build_scene(m.box_dff(), name="uv")
    mesh = sc.meshes[0]
    mesh.uv.append(array("f", mesh.uv[0]))
    for uv in mesh.uv:
        uv[0], uv[3] = bad, bad
    original = [uv.tobytes() for uv in mesh.uv]
    mats = textures.resolve_materials(sc.materials, None)
    glb, _ = build_glb(sc, mats)
    doc, binary = parse_glb(glb)
    attrs = doc["meshes"][0]["primitives"][0]["attributes"]
    for name in ("TEXCOORD_0", "TEXCOORD_1"):
        acc = doc["accessors"][attrs[name]]
        view = doc["bufferViews"][acc["bufferView"]]
        floats = struct.unpack_from(f"<{acc['count'] * 2}f", binary, view["byteOffset"])
        assert all(math.isfinite(f) for f in floats)
        assert floats[0] == floats[3] == 0.0
        assert floats[1] == mesh.uv[0][1]  # finite components survive
    text, _, _, _ = build_obj(sc, mats, "uv")
    uvs = [list(map(float, ln.split()[1:])) for ln in text.splitlines() if ln.startswith("vt ")]
    assert all(math.isfinite(f) for uv in uvs for f in uv)
    assert uvs[0][0] == 0.0 and uvs[1][1] == 1.0  # sanitize before OBJ's V flip
    assert [uv.tobytes() for uv in mesh.uv] == original


def test_model_batch_is_long_running_and_rejects_ignored_options(fake_db):
    spec = get_op("model.image")
    assert spec.long_running
    for kw in ({"backend": "blender"}, {"backend": "ariane"}, {"inline": True}):
        with pytest.raises(SatkError) as exc:
            spec.call({"all": True, "jobs": 1, **kw})
        assert exc.value.code == "BAD_PARAMS"


def test_blank_thumbnails_are_reported_on_cold_and_cached_runs(fake_db, monkeypatch, m):
    from satk.model3d import resolve

    src = resolve.resolve("model:17613", db=fake_db)
    dff = m.box_dff(tex="transparent")
    txd = m.solid_txd({"transparent": (0, 0, 0, 0)}, alpha={"transparent": [0] * 16})
    monkeypatch.setattr(api, "_read", lambda *_: (dff, [("alpha", txd)]))
    monkeypatch.setattr(resolve, "list_models", lambda _: [(17613, src.name, "objs", "alpha")])
    first = api.image(src.sid, views=4, size=32)
    assert first["stats"]["cover"] == [0.0] * 4
    assert first["stats"]["blank_views"] == [1, 2, 3, 4]
    assert any("BLANK" in w for w in first.get("warn", []))
    assert api.image(src.sid, views=4, size=32)["warn"] == first["warn"]
    for force in (True, False):
        result = batch.thumbnails(32, 4, 1, force=force)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        assert result["blank"] == manifest["blank"] == 1
        assert manifest["blank_views"] == {src.sid: [1, 2, 3, 4]}
        assert any("BLANK" in w for w in result.get("warn", []))


@pytest.mark.parametrize("sid", ["txd:vehicle", "tex:vehicle/vehiclegeneric256"])
def test_wrong_kind_does_not_suggest_nonexistent_model(fake_db, sid):
    with pytest.raises(SatkError) as exc:
        api.export(sid, "glb")
    assert exc.value.code == "BAD_ID"
    assert not exc.value.did_you_mean
    assert "texture image" in (exc.value.hint or "")


def test_legacy_ariane_views_match_saap_azimuths(fake_db, monkeypatch):
    from satk.viewer import backends
    from satk.viewer.backends.ariane_legacy import ArianeLegacyBackend
    from satk.model3d.png import encode_png

    # Exercise the real Python adapter, observing its native asset_preview camera angles.
    backend = ArianeLegacyBackend("ariane", "ariane", 0, "unused")
    backend._caps_cache = {"commands": ["asset_preview"]}
    monkeypatch.setattr(backend, "require", lambda *_: None)
    angles = []

    def cmd(command, model, path, az, size, **_):
        assert command == "asset_preview"
        angles.append(az)
        Path(path).write_bytes(encode_png(size, size, bytes((20, 40, 60)) * size * size, 3))
        return {}

    monkeypatch.setattr(backend, "cmd", cmd)
    monkeypatch.setattr(backends, "get_backend", lambda _: backend)
    result = api.image("model:17613", views=4, size=16, backend="ariane")
    for native, (_, az, _) in zip(angles, result["legend"]):
        # Native Ariane places the camera at (cos(a), sin(a)); SAAP/soft use (-sin(a), cos(a)).
        assert (math.cos(native), math.sin(native)) == pytest.approx(
            (-math.sin(math.radians(az)), math.cos(math.radians(az))))
