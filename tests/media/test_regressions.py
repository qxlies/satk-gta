"""Media regressions from the WP-04 verification (synthetic pixels only)."""

from pathlib import Path

import pytest

from satk.media import mapplot, ops, sheet, texture


@pytest.mark.parametrize("code", ["NOT_FOUND", "UNSUPPORTED"])
def test_failed_sheet_retries_and_keeps_legend(fake_db, mh, monkeypatch, code):
    from satk.core.errors import SatkError

    refs, _ = texture.resolve_refs(fake_db, ["tex:bistro/vent_64", "tex:vehicle/carplate"])
    decode = sheet.decode_ref
    broken = True

    def read(ref, reader):
        if broken and ref.sid == refs[1].sid:
            raise SatkError(code, "temporarily unavailable texture")
        return decode(ref, reader)

    monkeypatch.setattr(sheet, "decode_ref", read)
    first, legend = sheet.render_sheet(refs)
    pixels = first.read_bytes()
    again, repeated = sheet.render_sheet(refs)
    assert again == first and repeated == legend
    assert repeated[1][2].endswith(f"({code})")
    assert first.read_bytes() == pixels
    broken = False
    restored, good = sheet.render_sheet(refs)
    assert good[1][2] == "16x16 X8R8G8B8"
    assert restored.read_bytes() != pixels
    assert restored != first  # an error placeholder is not the successful content-addressed sheet
    mtime = restored.stat().st_mtime_ns
    assert sheet.render_sheet(refs) == (restored, good)
    assert restored.stat().st_mtime_ns == mtime


def test_status_reports_selected_dxt_backend(satk_home, monkeypatch):
    monkeypatch.setenv("SATK_MEDIA_NO_PILLOW", "1")
    assert ops._status(False)["png"] == "zlib"
    assert ops._status(False)["dxt"] == texture.dxt_backend() != "pillow"


def test_zone_labels_leave_grid_coordinates_readable(fake_db, mh, monkeypatch):
    from satk.media import font

    # Two zones clipped at the top/left edges, exactly where the coordinate labels sit.
    monkeypatch.setattr(mapplot, "_zones", lambda *_: [
        ["TOPZONE", "", -52, -90, 90, 120], ["LEFTZONE", "", -120, -90, 90, 52]])
    r = mapplot.render_map(0, 0, span=200, px=600, layers=("zone",), labels=0)
    w, _, rgba = mh.png_pixels(r["file"])
    for x, y, label in [(153, 3, "-50"), (3, 153, "50")]:
        tw, th, mask = font.text_mask(label)
        for yy in range(th):
            for xx in range(tw):
                if mask[yy * tw + xx]:
                    assert mh.px(rgba, w, x + xx, y + yy) == mapplot._GRID_TEXT


def test_map_rebuild_reuses_content_and_prunes_only_same_request(fake_db, tmp_path, monkeypatch):
    stamp = "contents-a"
    monkeypatch.setattr(fake_db, "meta", lambda: {"content_hash": stamp})
    dbfile = tmp_path / "index.sqlite"
    dbfile.write_bytes(b"first index")
    monkeypatch.setattr(fake_db, "path", dbfile)
    first = mapplot.render_map(0, 0, px=128, labels=0)
    dbfile.write_bytes(b"rebuilt index with the same logical content")
    assert mapplot.render_map(0, 0, px=128, labels=0) == first
    other = mapplot.render_map(0, 0, px=128, labels=1)
    unrelated = Path(first["file"]).parent / "user-map.png"
    unrelated.write_bytes(b"keep")
    stamp = "contents-b"
    changed = mapplot.render_map(0, 0, px=128, labels=0)
    assert changed["file"] != first["file"]
    assert not Path(first["file"]).exists()
    assert not Path(first["file"]).with_suffix(".json").exists()
    assert Path(changed["file"]).is_file() and Path(other["file"]).is_file()
    assert unrelated.read_bytes() == b"keep"
