"""Launch-time client cvars (satk.ingame.cvars): coreconfig.xml edits, the once-per-run backup, restore, relaunch.

No MTA process and no game: the config is a temp file, the process list and the client start are replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.ingame import cvars as CV

CONFIG = """<mainconfig>
    <settings>
        <nick>Test</nick>
        <fps_limit>100</fps_limit>
        <vsync>1</vsync>
        <debugfile/>
        <display_fullscreen_style>0</display_fullscreen_style>
        <display_windowed>0</display_windowed>
    </settings>
    <binds>
        <vsync>keep</vsync>
    </binds>
</mainconfig>
"""


@pytest.fixture
def cfg(satk_home, monkeypatch) -> Path:
    p = satk_home / "client" / "coreconfig.xml"
    p.parent.mkdir(parents=True)
    p.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setattr(CV, "config_path", lambda: p)
    running: list[int] = []
    monkeypatch.setattr(CV, "gta_running", lambda: list(running))
    CV._test_running = running
    return p


def test_parse_pairs_collect_and_bench_pairs():
    assert CV.parse_pairs(["fps_limit=0", " vsync = 0 ", "fps_limit=144"]) == {"fps_limit": "144", "vsync": "0"}
    assert CV.parse_pairs({"a": 1}) == {"a": "1"} and CV.parse_pairs(None) == {}
    for bad in ("nonsense", "=1", "a b=1", "1a=1", "a=x\ny"):
        with pytest.raises(SatkError) as e:
            CV.parse_pairs([bad])
        assert e.value.code == "BAD_PARAMS"
    assert CV.collect(["vsync=0"], windowed=True) == {"display_windowed": "1", "display_fullscreen_style": "0", "vsync": "0"}
    assert CV.collect(["display_windowed=0"], windowed=True)["display_windowed"] == "0"      # explicit wins
    assert CV.WINDOWED == {"display_windowed": "1", "display_fullscreen_style": "0"}
    assert CV.bench_pairs("classic", "mta", ["sae_va_guard=0"]) == {"sae_preset": "classic", "sae_limits": "mta",
                                                                    "sae_va_guard": "0"}
    assert CV.bench_pairs(None, None, None) == {}
    with pytest.raises(SatkError):
        CV.bench_pairs("high; quit", None, None)


def test_edit_text_replaces_inserts_and_keeps_the_rest():
    out, ch = CV.edit_text(CONFIG, {"fps_limit": "0", "vsync": "0", "debugfile": "a&b", "sae_preset": "classic"})
    assert "<fps_limit>0</fps_limit>" in out and "<debugfile>a&amp;b</debugfile>" in out
    assert "        <sae_preset>classic</sae_preset>\n    </settings>" in out
    assert "<binds>\n        <vsync>keep</vsync>" in out                    # only <settings> is edited
    assert out.count("<vsync>0</vsync>") == 1
    assert [(c["key"], c["was"], c["now"]) for c in ch] == [("fps_limit", "100", "0"), ("vsync", "1", "0"),
                                                             ("debugfile", "", "a&b"), ("sae_preset", None, "classic")]
    back, _ = CV.edit_text(out, {"fps_limit": "100", "vsync": "1", "debugfile": ""})
    assert "<debugfile/>" in back and "<fps_limit>100</fps_limit>" in back
    crlf, _ = CV.edit_text(CONFIG.replace("\n", "\r\n"), {"new_key": "1"})
    assert "        <new_key>1</new_key>\r\n    </settings>" in crlf
    empty, _ = CV.edit_text("<mainconfig><settings/></mainconfig>", {"a": "1"})
    assert "<a>1</a>" in empty
    with pytest.raises(SatkError) as e:
        CV.edit_text("<mainconfig/>", {"a": "1"})
    assert e.value.code == "EXTERNAL_TOOL"


def test_apply_backs_up_once_layers_and_restores(cfg):
    r = CV.apply({"vsync": "0", "fps_limit": "0"})
    assert [c["key"] for c in r["changed"]] == ["vsync", "fps_limit"] and r["cvars"] == {"vsync": "0", "fps_limit": "0"}
    bak = Path(r["backup"])
    assert bak.is_file() and bak.read_text(encoding="utf-8") == CONFIG and bak.parent.name == "cvar-backup"
    assert "<vsync>0</vsync>" in cfg.read_text(encoding="utf-8")
    # base is merged, run replaces: the backup is kept (once per run) and the file is rebuilt from it
    r2 = CV.apply(CV.WINDOWED)
    assert r2["backup"] == r["backup"] and r2["cvars"] == {"vsync": "0", "fps_limit": "0", **CV.WINDOWED}
    r3 = CV.apply({"sae_preset": "high", "vsync": "1"}, layer="run", replace_layer=True)
    assert r3["cvars"]["vsync"] == "1" and r3["cvars"]["sae_preset"] == "high"
    assert len(list(bak.parent.glob("coreconfig.*.xml"))) == 1
    r4 = CV.apply({"sae_limits": "mta"}, layer="run", replace_layer=True)               # the old run values are gone
    text = cfg.read_text(encoding="utf-8")
    assert "sae_preset" not in text and "<sae_limits>mta</sae_limits>" in text and "<vsync>0</vsync>" in text
    assert CV.apply({"sae_limits": "mta"}, layer="run", replace_layer=True)["changed"] == []     # nothing to write
    assert CV.status()["cvars"] == r4["cvars"] and CV.status()["backup"] == r["backup"]
    out = CV.restore()
    assert out["restored"] is True and out["from"] == r["backup"] and "vsync" in out["keys"]
    assert cfg.read_text(encoding="utf-8") == CONFIG and CV.status() is None and CV.load_state() == {}
    assert CV.restore()["restored"] is False                                           # nothing recorded


def test_apply_without_a_config_creates_one_and_restore_removes_it(cfg):
    cfg.unlink()
    r = CV.apply({"vsync": "0"})
    assert r["backup"] is None and "<vsync>0</vsync>" in cfg.read_text(encoding="utf-8")
    out = CV.restore()
    assert out["restored"] is True and not cfg.exists() and "no coreconfig.xml" in out["note"]


def test_a_running_game_blocks_edits_and_restore(cfg):
    CV._test_running.append(4242)
    for call in (lambda: CV.apply({"vsync": "0"}), lambda: CV.guard({"vsync": "0"})):
        with pytest.raises(SatkError) as e:
            call()
        assert e.value.code == "NOT_READY" and "4242" in e.value.msg and e.value.data["gta_sa"] == [4242]
    assert cfg.read_text(encoding="utf-8") == CONFIG and CV.load_state() == {}
    CV._test_running.clear()
    CV.apply({"vsync": "0"})
    CV._test_running.append(4242)
    CV.guard({"vsync": "0"})                                    # the same values again: nothing to write, no refusal
    assert CV.apply({"vsync": "0"})["changed"] == []
    with pytest.raises(SatkError) as e:
        CV.apply({"vsync": "1"})
    assert e.value.code == "NOT_READY"
    with pytest.raises(SatkError) as e:
        CV.restore()
    assert e.value.code == "NOT_READY" and "ingame stop" in e.value.hint
    CV._test_running.clear()
    assert CV.restore()["restored"] is True


class FakeMta:
    """Stands in for the pieces of mta_lua the launch helpers use."""

    def __init__(self, monkeypatch, running):
        from satk.saap import client as C
        from satk.viewer.backends import mta_lua

        self.events: list[str] = []
        self.running = running
        self.session = {"client_pid": 7}
        monkeypatch.setattr(C, "read_session", lambda role: dict(self.session))
        monkeypatch.setattr(mta_lua, "_stop_client", self.stop)
        monkeypatch.setattr(mta_lua, "start_client", self.start)

    def stop(self, sess):
        self.events.append("stop")
        self.running.clear()
        return True

    def start(self, *, timeout=180.0, force=False):
        self.events.append("start")
        self.running.append(99)
        return {"target": "game", "client": {"w": 1}, "client_pid": 99}


@pytest.fixture
def mta(cfg, monkeypatch):
    return FakeMta(monkeypatch, CV._test_running)


def test_launch_client_writes_base_then_starts_and_records(cfg, mta):
    res = CV.launch_client(CV.collect(["fps_limit=0"], windowed=True))
    assert mta.events == ["start"] and res["client_pid"] == 99 and res["cvars"]["fps_limit"] == "0"
    st = CV.load_state()
    assert st["launched"]["pid"] == 99 and st["launched"]["cvars"] == CV.status()["cvars"]
    assert "<display_windowed>1</display_windowed>" in cfg.read_text(encoding="utf-8")
    CV._test_running.clear()
    mta.events.clear()
    CV.launch_client(None)                                       # nothing new to write
    assert mta.events == ["start"]


def test_relaunch_restarts_only_when_the_launch_values_differ(cfg, mta):
    CV.launch_client(CV.collect(["vsync=0"], windowed=True))
    base = CV.status()["cvars"]
    mta.events.clear()
    # nothing asked and no run layer: the running client stays
    assert CV.relaunch({}, joined=True) == {"restarted": False, "cvars": base}
    r = CV.relaunch({"sae_preset": "classic"}, joined=True)
    assert mta.events == ["stop", "start"] and r["restarted"] is True and r["cvars"] == {**base, "sae_preset": "classic"}
    assert "<sae_preset>classic</sae_preset>" in cfg.read_text(encoding="utf-8")
    assert CV.load_state()["launched"]["cvars"] == r["cvars"]
    mta.events.clear()
    assert CV.relaunch({"sae_preset": "classic"}, joined=True)["restarted"] is False        # same values: no restart
    r = CV.relaunch({"sae_preset": "high", "sae_limits": "mta"}, joined=True)
    assert mta.events == ["stop", "start"] and r["cvars"]["sae_limits"] == "mta"
    mta.events.clear()
    r = CV.relaunch({}, joined=True)                             # back to the base values: the run layer is dropped
    assert mta.events == ["stop", "start"] and r["cvars"] == base
    assert "sae_preset" not in cfg.read_text(encoding="utf-8")
    out = CV.restore() if not CV._test_running else None
    assert out is None                                           # the game still runs: restore waits for 'ingame stop'
    CV._test_running.clear()
    assert CV.restore()["restored"] is True and cfg.read_text(encoding="utf-8") == CONFIG


def test_relaunch_never_touches_a_game_satk_did_not_start(cfg, mta):
    mta.session = {}
    CV._test_running.append(5)
    with pytest.raises(SatkError) as e:
        CV.relaunch({"sae_preset": "classic"}, joined=True)
    assert e.value.code == "NOT_READY" and "satk ingame play" in e.value.hint and mta.events == []
    assert cfg.read_text(encoding="utf-8") == CONFIG
    CV._test_running.clear()
    with pytest.raises(SatkError) as e:                          # no client at all yet
        CV.relaunch({"sae_preset": "classic"}, joined=False)
    assert e.value.code == "NOT_READY" and "no client" in e.value.msg


def test_close_client_waits_for_the_exit_and_reports_a_stuck_game(cfg, mta, monkeypatch):
    CV._test_running.append(99)
    assert CV.close_client() == {"closed": True} and CV._test_running == []
    monkeypatch.setattr(mta, "stop", lambda sess: True)
    from satk.viewer.backends import mta_lua

    monkeypatch.setattr(mta_lua, "_stop_client", lambda sess: True)
    CV._test_running.append(99)
    with pytest.raises(SatkError) as e:
        CV.close_client(wait=0.0)
    assert e.value.code == "NOT_READY" and e.value.data["gta_sa"] == [99]


def test_restore_after_stop(cfg, mta):
    assert CV.restore_after_stop() is None
    CV.apply({"vsync": "0"})
    CV._test_running.append(1)
    r = CV.restore_after_stop(wait=0.0)                          # the game did not exit: a note, the backup stays
    assert r["restored"] is False and "NOT_READY" in r["note"] and "cvar-restore" in r["hint"] and CV.load_state()
    CV._test_running.clear()
    assert CV.restore_after_stop()["restored"] is True and cfg.read_text(encoding="utf-8") == CONFIG
