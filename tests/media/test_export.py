"""``export_all``: every unique image of a profile into the PNG cache (SPEC §4.4; WP-04 acceptance 2)."""

from __future__ import annotations

from satk.core.registry import progress_handler
from satk.index.api import override_index
from satk.media import texture as T


def _n_images(db) -> int:
    return int(db.query("SELECT count(*) FROM image")["rows"][0][0])


def test_export_all_in_process_then_skip(fake_db, satk_home, mh):
    n = _n_images(fake_db)
    assert len(T.list_pix(fake_db)) == n and n >= 20
    seen = []
    with progress_handler(lambda d, t, m: seen.append((d, t))):
        r = T.export_all(jobs=1)
    assert r["written"] + r["skipped"] == r["images"] == n
    assert r["written"] == n and "failed" not in r and r["backend"] in ("pillow", "numpy", "python")
    assert seen[0] == (0, n) and seen[-1] == (n, n)
    files = sorted((satk_home / "work" / "cache" / "tex").rglob("*.png"))
    assert len(files) == n
    w, h, _ = mh.png_pixels(T.cache_path(fake_db.texture_ref("tex:bistro/vent_64").pix))
    assert (w, h) == (64, 64)
    again = T.export_all(jobs=1)
    assert again["written"] == 0 and again["skipped"] == n


def test_export_all_process_pool(fake_db, satk_home, monkeypatch):
    monkeypatch.setattr(T, "POOL_MIN", 1)
    T.texture_png("tex:bistro/vent_64", size=0)  # one already cached
    r = T.export_all(jobs=2)
    n = _n_images(fake_db)
    assert r["jobs"] == 2 and r["skipped"] == 1 and r["written"] == n - 1 and "failed" not in r


def test_export_all_reports_failures(satk_home, tmp_path, mh):
    db = mh.make_fake(tmp_path / "game", skip_txd=("vehicle",))  # the loose vehicle.txd is missing
    with override_index(db):
        r = T.export_all(jobs=1)
    n_vehicle = int(db.query("SELECT count(*) FROM texture x JOIN txd t ON t.id = x.txd_id "
                             "WHERE t.name = 'vehicle'")["rows"][0][0])
    assert r["failed"] == n_vehicle and r["written"] == r["images"] - n_vehicle
    assert all(e[1] == "NOT_FOUND" and e[0].startswith("tex:vehicle/") for e in r["errors"])


def test_export_without_pillow_matches_pixels(fake_db, satk_home, monkeypatch, mh):
    """The stdlib PNG path (and a non-Pillow DXT decoder) gives the same pixels."""
    ref = fake_db.texture_ref("tex:infernus/infernus92interior128")
    with_pil = mh.png_pixels(T.png_for_ref(ref, None))
    T.cache_path(ref.pix).unlink()
    monkeypatch.setenv("SATK_MEDIA_NO_PILLOW", "1")
    assert T.dxt_backend() != "pillow"
    p = T.png_for_ref(ref, None)
    assert mh.png_pixels(p) == with_pil
