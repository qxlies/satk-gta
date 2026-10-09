"""Host for ``tests/ingame/bench_sim.lua``: the real satk-bench scripts in a simulated MTA client.

:class:`BenchSim` loads ``stats.lua`` and ``client.lua`` of ``mta-resources/satk-bench`` into one Lua 5.1 state (the
fork's own DLL) after the simulated client, and calls the ``bench`` export. :class:`SimClient` has the interface of
:class:`satk.ingame.bench.BenchClient` (``call``); every ``status`` call advances the simulated frames, so a whole scene
runs in milliseconds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lua51 import Lua

from satk.core.errors import SatkError
from satk.ingame import resource as R

HERE = Path(__file__).resolve().parent


class BenchSim:
    def __init__(self, dll: Path, setup: str = ""):
        self.lua = Lua(dll)
        self.lua.run((HERE / "bench_sim.lua").read_text(encoding="utf-8"), "bench_sim.lua")
        if setup:
            self.lua.run(setup, "setup")
        src = R.bench_source()
        for f in ("stats.lua", "client.lua"):
            self.lua.run((src / f).read_text(encoding="utf-8"), f)

    def run(self, code: str) -> str | None:
        return self.lua.run(code)

    def eval(self, expr: str) -> Any:
        return json.loads(self.lua.run(f"return Sim.encode({expr})"))

    def bench(self, cmd: str, args: dict | None = None) -> Any:
        return self.eval(f"bench({R.lua(cmd)}, {R.lua(args or {})})")

    def pump(self, frames: int, slice_ms: float = 16.0) -> None:
        self.lua.run(f"Sim.run({int(frames)}, {slice_ms!r})")

    def close(self) -> None:
        self.lua.close()


class SimClient:
    """``BenchClient`` on top of :class:`BenchSim`; ``status`` pumps ``seconds`` of frames (``fps``)."""

    def __init__(self, sim: BenchSim, fps: float = 60.0, seconds: float = 1.0):
        self.sim = sim
        self.frames = max(1, int(fps * seconds))
        self.slice = 1000.0 / fps
        self.log: list[tuple[str, dict]] = []

    def call(self, cmd: str, args: dict | None = None) -> dict:
        self.log.append((cmd, args or {}))
        if cmd == "status":
            self.sim.pump(self.frames, self.slice)
        out = self.sim.bench(cmd, args)
        if out is None or out is False:
            raise SatkError("NOT_READY", f"satk-bench did not answer {cmd!r}")
        if isinstance(out, dict) and out.get("error"):
            raise SatkError("EXTERNAL_TOOL", f"satk-bench {cmd}: {out['error']}")
        return out
