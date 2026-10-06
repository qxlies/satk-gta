"""Host for ``tests/ingame/mta_sim.lua``: the real satk-testdrive scripts in a simulated MTA.

:class:`TdSim` loads ``shared.lua`` + ``server.lua`` into the simulated server and ``shared.lua`` +
``client.lua`` + ``checks.lua`` into the simulated client of one Lua 5.1 state (the fork's own DLL),
gives the server a manifest (what ``satk-testdrive-mod`` exports) and starts both sides.
:class:`SimBridge` has the interface of :class:`satk.ingame.session.Bridge` (``server``/``client``/
``capture``), so :mod:`satk.ingame.checks` runs unchanged against it; every client call advances the
simulated clock, so the checks' waits pass in milliseconds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lua51 import Lua

from satk.core.errors import SatkError
from satk.ingame import resource as R

HERE = Path(__file__).resolve().parent


class TdSim:
    def __init__(self, dll: Path, manifest: dict, *, knobs: str = ""):
        self.lua = Lua(dll)
        self.lua.run((HERE / "mta_sim.lua").read_text(encoding="utf-8"), "mta_sim.lua")
        src = R.logic_source()
        for side, files in (("server", ("shared.lua", "server.lua")),
                            ("client", ("shared.lua", "client.lua", "checks.lua"))):
            for f in files:
                code = (src / f).read_text(encoding="utf-8")
                self.lua.run(f"Sim.load(Sim.{side}, {R.lua(code)}, {R.lua(f)})", f)
        self.set_manifest(manifest)
        if knobs:
            self.lua.run(knobs, "knobs")
        self.lua.run("Sim.startServer()")
        self.lua.run("Sim.startClient()")
        self.pump(10)

    def set_manifest(self, m: dict) -> None:
        files = {f":{R.CONTENT}/{rel}": True for mm in m.get("models") or [] for rel in (mm.get("files") or {}).values()}
        self.lua.run(f"Sim.manifest = {R.lua(m)}; Sim.files = {R.lua(files)}")

    def run(self, code: str) -> str | None:
        return self.lua.run(code)

    def eval(self, expr: str) -> Any:
        return json.loads(self.lua.run(f"return Sim.encode({expr})"))

    def pump(self, n: int = 1) -> None:
        self.lua.run(f"Sim.pump({int(n)})")

    def td(self, cmd: str, args: dict | None = None) -> Any:
        return self.eval(f"Sim.server.td({R.lua(cmd)}, {R.lua(args or {})})")

    def tdc(self, cmd: str, args: dict | None = None) -> Any:
        return self.eval(f"Sim.client.tdc({R.lua(cmd)}, {R.lua(args or {})})")

    def close(self) -> None:
        self.lua.close()


class SimBridge:
    """``satk.ingame.session.Bridge`` on top of :class:`TdSim`."""

    def __init__(self, sim: TdSim, frames_per_poll: int = 25):
        self.sim = sim
        self.frames = frames_per_poll
        self.captures: list[dict] = []

    @staticmethod
    def _check(side: str, cmd: str, out: Any) -> Any:
        if out is None or out is False:
            raise SatkError("NOT_READY", f"satk-testdrive did not answer {cmd!r} on the {side}")
        if isinstance(out, dict) and out.get("error"):
            raise SatkError("EXTERNAL_TOOL", f"satk-testdrive {cmd}: {out['error']}")
        return out

    def server(self, cmd: str, args: dict | None = None) -> Any:
        out = self.sim.td(cmd, args)
        self.sim.pump(2)
        return self._check("server", cmd, out)

    def client(self, cmd: str, args: dict | None = None) -> Any:
        self.sim.pump(self.frames if cmd == "job" else 1)
        return self._check("client", cmd, self.sim.tdc(cmd, args))

    def client_joined(self) -> bool:
        return True

    def capture(self, pose: dict | None, prefix: Path, *, w: int, h: int, settle: int | None = None) -> dict:
        from satk.saap import png as P

        px = bytes((len(self.captures) * 37 + x) & 0xFF for x in range(w * h * 3))
        path = P.write(str(prefix) + ".png", w, h, px)
        self.captures.append({"pose": pose, "file": path.as_posix(), "settle": settle})
        return {"files": {"color": path.as_posix()}, "w": w, "h": h}
