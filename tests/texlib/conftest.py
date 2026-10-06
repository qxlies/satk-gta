"""Synthetic index records and images; no game files or pixels are stored here."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def texture_index(satk_home, monkeypatch):
    from satk.index.api import IndexDB
    from satk.index.ddl import create_schema

    path = satk_home / "work" / "textures.sqlite"
    conn = sqlite3.connect(path)
    create_schema(conn)

    class Fixture:
        txds = {}
        count = 0

        def txd(self, name, parent=None, via="txdp", active=1):
            bid = len(self.txds) + 1
            conn.execute("INSERT INTO blob(id,source_id,idx,name,stem,ext,abs_off,size,ns,active) "
                         "VALUES (?,1,?,?,?,'txd',0,0,'main',?)", (bid, bid, name + ".txd", name, active))
            conn.execute("INSERT INTO txd(id,blob_id,name,rw_version,tex_count,parent,parent_via) "
                         "VALUES (?,?,?,0,0,?,?)", (bid, bid, name, parent, via if parent else None))
            self.txds[name] = bid
            conn.commit()

        def texture(self, txd, name, rgb=(140, 100, 80), sec="objs", size=128, used=True, active=1):
            self.count += 1
            tid = self.count
            digest = tid.to_bytes(12, "big")
            mean = None if rgb is None else (rgb[0] << 24) | (rgb[1] << 16) | (rgb[2] << 8) | 255
            conn.execute("INSERT INTO image VALUES (?,?,?,'DXT1',0,?)", (digest, size, size, mean))
            conn.execute("INSERT INTO texture(id,txd_id,idx,name,platform,raster_fmt,d3dfmt,w,h,levels,alpha,data_off,data_size,hash) "
                         "VALUES (?,?,?,?,9,512,'DXT1',?,?,1,0,0,0,?)", (tid, self.txds[txd], tid, name, size, size, digest))
            if used:
                mid = 10000 + tid
                conn.execute("INSERT INTO model(id,layer_id,name,txd,sec,ide_id,line,active) VALUES (?,1,?,?,?,1,1,?)",
                             (mid, "object_" + str(tid), txd, sec, active))
                conn.execute("INSERT INTO model_link(id,n_inst) VALUES (?,?)", (mid, 10))
                conn.execute("INSERT INTO model_tex VALUES (?,?,?,'own',1)", (mid, name, tid))
            conn.commit()
            return f"tex:{txd}/{name}"

        def sql(self, sql, params=()):
            conn.execute(sql, params)
            conn.commit()

    fixture = Fixture()
    fixture.txd("district")
    fixture.txd("bluewalls")
    fixture.txd("car")
    fixture.txd("inactive", active=0)
    fixture.texture("district", "brick_red", rgb=(152, 72, 60))
    fixture.texture("district", "brick_old", rgb=(128, 96, 64))
    fixture.texture("district", "asphalt_road", rgb=(128, 128, 128))
    fixture.texture("district", "brick_lod", size=32)
    fixture.texture("district", "brick_unused", used=False)
    fixture.texture("district", "brick_missing_mean", rgb=None)
    fixture.texture("district", "brick_inactive_model", active=0)
    fixture.texture("bluewalls", "brick_blue", rgb=(80, 112, 152))
    fixture.texture("car", "brick_vehicle", sec="cars")
    fixture.texture("car", "brick_ped", sec="peds")
    fixture.texture("inactive", "brick_override")
    fixture.db = IndexDB("vanilla", path=path)
    monkeypatch.setattr(fixture.db, "stale_warnings", lambda: ["INDEX_STALE: synthetic source changed"])

    def no_pixels(*args, **kwargs):
        pytest.fail("vanilla reuse must not read game pixels")

    monkeypatch.setattr(fixture.db, "read_blob", no_pixels)
    from satk.media import texture

    monkeypatch.setattr(texture, "decode_ref", no_pixels)
    yield fixture
    fixture.db.close()
    conn.close()


@pytest.fixture
def synthetic_bake(tmp_path):
    import numpy as np
    from PIL import Image

    def create(recipe, size, seed):
        n = size * 4
        y, x = np.mgrid[:n, :n] / n
        field = 0.55 + 0.15 * np.sin(2 * np.pi * x * 4) + 0.13 * np.cos(2 * np.pi * y * 3)
        rgb = np.clip(field[..., None] * np.array([190, 175, 160]), 0, 255).astype(np.uint8)
        path = tmp_path / (recipe["name"] + "-raw.png")
        Image.fromarray(rgb).save(path)
        mask = tmp_path / (recipe["name"] + "-mask.png")
        Image.fromarray(np.clip((field - 0.4) * 800, 0, 255).astype(np.uint8)).save(mask)
        return {"colour": str(path), "mask": str(mask), "bake_seconds": 0.01,
                "process_seconds": 0.01, "blender": "synthetic-test", "log": str(tmp_path / "bake.log")}

    return create
