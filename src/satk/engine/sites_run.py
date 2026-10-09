"""Glue of the site tools: paths of the fork, result envelopes of check / gen / scan. Stdlib only."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath
from .common import layout
from .sites_check import check_manifest, read_header
from .sites_data import SiteInputs, ghidra_dir, load_callers, load_functions, load_hoodlum_map, reloc_extents, \
    workspace_inputs
from .sites_gen import generate_header, header_path, manifest_path
from .sites_manifest import Finding, Manifest, load_manifest, site_is_readonly, validate_structure

__all__ = ["run_check", "run_gen", "run_scan", "FINDING_COLS"]

FINDING_COLS = ["check", "where", "va", "msg"]
_MAX_ROWS = 60


def _fork(fork: str | None) -> Path:
    return Path(fork) if fork else layout().fork


def _manifest(fork: Path, manifest: str | None) -> tuple[Path, Manifest, list[Finding]]:
    mp = Path(manifest) if manifest else manifest_path(fork)
    if not mp.is_file():
        raise SatkError("NOT_FOUND", f"patch-site manifest not found: {jpath(mp)}",
                        hint="the foundation lane creates docs/sae/patch-sites.toml in the fork; "
                             "pass --manifest PATH for another file")
    man, errs = load_manifest(mp)
    if man is None:
        raise _failed(errs, [], "manifest cannot be read")
    return mp, man, errs


def _failed(errors: list[Finding], warnings: list[Finding], what: str) -> SatkError:
    first = errors[0] if errors else None
    msg = f"{what}: {len(errors)} problem(s)" + (f"; first: {first.check} {first.where}: {first.msg}" if first else "")
    rows = [f.row() for f in errors[:_MAX_ROWS]]
    data = {"cols": FINDING_COLS, "rows": rows, "total": len(errors)}
    if warnings:
        data["warnings"] = [f"{w.check} {w.where}: {w.msg}" for w in warnings[:10]]
    return SatkError("CHECK_FAILED", msg[:600], hint="fix the manifest and rerun `satk engine sites-check`", data=data)


def run_check(fork: str | None = None, manifest: str | None = None, *, inputs: SiteInputs | None = None) -> dict:
    """``engine.sites_check`` body; ``inputs`` lets tests supply a fake workspace."""
    forkp = _fork(fork)
    mp, man, errs = _manifest(forkp, manifest)
    if errs:  # schema problems: the deeper checks would only add noise
        raise _failed(errs, [], "manifest schema")
    inp = inputs if inputs is not None else workspace_inputs()
    hp = header_path(forkp)
    res = check_manifest(man, inp, read_header(hp), header_label=jpath(hp))
    if not res.ok:
        raise _failed(res.errors, res.warnings, "sites-check failed")
    rows = [[g.id, g.report_id, g.phase, len(g.sites), sum(1 for s in g.sites if not site_is_readonly(g, s))] for g in man.groups]
    env = table(["group", "report_id", "phase", "sites", "written"], rows,
                warn=[f"{w.check}: {w.where}: {w.msg}" for w in res.warnings[:10]])
    env.update({
        "manifest": jpath(mp), "header": jpath(hp), "sites": res.sites, "written": res.written_sites,
        "securom_keys": len(inp.keys.keys), "stolen_sites": len(inp.stolen), "relocated_functions": len(inp.relocs),
        "trunk_patches": len(inp.trunk), "toml_sha256": man.sha256,
    })
    return env


def run_gen(fork: str | None = None, manifest: str | None = None) -> dict:
    forkp = _fork(fork)
    mp, man, errs = _manifest(forkp, manifest)
    errs = errs + validate_structure(man)
    if errs:
        raise _failed(errs, [], "manifest is invalid, header not written")
    text = generate_header(man)
    hp = header_path(forkp)
    before = read_header(hp)
    changed = before is None or before.replace("\r\n", "\n") != text
    if changed:
        hp.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(hp, text.encode("utf-8"))
    return {"ok": True, "header": jpath(hp), "changed": changed, "groups": len(man.groups),
            "sites": sum(len(g.sites) for g in man.groups), "toml_sha256": man.sha256,
            "header_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def run_scan(base: int | None, size: int | None, stride: int | None, *, only: str | None = None,
             limit: int | None = None, offset: int = 0, flag: str | None = None,
             image=None, xrefs: Path | None = None) -> dict:
    """``engine.sites_scan`` body (``image``/``xrefs`` are overridden by tests)."""
    from ..re.pe import PeImage
    from .sites_data import stock_exe_path
    from .sites_scan import CLASSES, scan_array

    if base is None or size is None or stride is None:
        raise SatkError("BAD_PARAMS", "--base, --size and --stride are required",
                        hint="satk engine sites-scan --base 0xB748F8 --size 4000 --stride 4")
    if size <= 0 or stride <= 0 or base <= 0:
        raise SatkError("BAD_PARAMS", "base, size and stride must be positive")
    if only is not None and only not in CLASSES:
        raise SatkError("BAD_PARAMS", f"unknown class {only!r}", did_you_mean=list(CLASSES))
    lim = clamp_limit(limit, default=20)
    if image is None:
        exe = stock_exe_path()
        if not exe.is_file():
            raise SatkError("NOT_READY", f"stock exe not found: {jpath(exe)}", hint="satk init --game <folder> --clean-copy")
        image = PeImage.open(exe)
    gd = ghidra_dir()
    xr = xrefs if xrefs is not None else gd / "export" / "xrefs_data.jsonl"
    if not Path(xr).is_file():
        raise SatkError("NOT_READY", f"Ghidra xref export not found: {jpath(Path(xr))}",
                        hint="run the Ghidra export (work/re/ghidra/README.md)")
    funcs = callers = relocs = None
    if xrefs is None:
        funcs = load_functions(gd / "export" / "functions.jsonl")
        callers = load_callers(gd / "export" / "calls.jsonl")
        raw_relocs, _ = load_hoodlum_map(gd / "symbols" / "hoodlum_map.json")
        relocs = reloc_extents(raw_relocs, funcs, callers)
    res = scan_array(image, Path(xr), base, size, stride, funcs=funcs, callers=callers, relocs=relocs)
    cands = [c for c in res.candidates if (only is None or c.cls == only) and (flag is None or flag in c.flags)]
    page = cands[offset:offset + lim]
    rows = [[f"0x{c.va:X}", f"0x{c.insn:X}", f"0x{c.value:X}", c.value - base, c.cls, c.form,
             ",".join(c.flags) or None, c.func_name or None, c.raw] for c in page]
    nxt = str(offset + lim) if offset + lim < len(cands) else None
    env = table(["operand", "insn", "value", "off", "class", "form", "flags", "func", "bytes"], rows,
                total=len(cands), next=nxt)
    env.update({"array": f"0x{base:X}+0x{size:X} stride 0x{stride:X}", "end": f"0x{res.end:X}",
                "window_end": f"0x{res.end + stride:X}", "counts": res.counts(), "candidates": len(res.candidates)})
    return env
