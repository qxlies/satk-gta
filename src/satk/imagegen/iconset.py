"""Icon-set runs: generate candidates, key and resize them, contact sheets, pick and finalize.

Layout of a run folder (``work/out/imagegen/<spec out>/``)::

    raw/<icon>_c<k>.png (+ .provenance.json)        what the endpoint returned (a failed attempt leaves
    raw/<icon>_c<k>.failed.provenance.json           only the sidecar)
    keyed/<icon>_c<k>_master.png                    keyed, trimmed, padded master
    keyed/<icon>_c<k>_<size>.png (+ sidecar)        shipped sizes of a candidate
    contact_sheet.png, contact_small.png            keyed masters / smallest shipped size x4, one row per icon
    candidates.json, run.json                       legend and the run summary (calls, codes)
    placeholder/, final/                            the fallback set and the picked set (+ PROVENANCE.md)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable, jpath
from .client import Attempt, ImageClient, endpoint_host, imagegen_error
from .keying import KeyParams, key_image, load_image, png_bytes, shrink, validate_image
from .placeholder import write_set as write_placeholder_set
from .provenance import (provenance_md, sha256_bytes, sidecar_path, utc_now, write_json,
                         write_sidecar)
from .sheet import render_png_sheet
from .spec import Icon, Spec, prompt_for

__all__ = ["MAX_CALLS_DEFAULT", "key_params_of", "generate_images", "run_icon_set", "finalize", "write_set_md"]

MAX_CALLS_DEFAULT = 24
_STOP_AFTER_ENDPOINT_FAILURES = 2


def key_params_of(spec: Spec) -> KeyParams:
    return KeyParams(key_color=spec.key_color, badge_color=spec.badge_color).check()


def _slug(s: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "image")[:40]


def _png_of(attempt: Attempt) -> bytes:
    """The attempt's image as PNG bytes (re-encoded only when the endpoint sent another format)."""
    data = attempt.image or b""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data
    return png_bytes(load_image(data))


def _attempt_record(a: Attempt, *, tool: str, host: str, model: str, prompt: str, prompt_id: str | None,
                    params: dict, raw_sha: str | None) -> dict:
    rec = {"tool": tool, "generator": "ai", "endpoint_host": host, "model": model, "prompt": prompt,
           "params": params, "time_utc": a.started_utc or utc_now(), "response_text": a.response_text,
           "finish_reason": a.finish_reason, "http_status": a.http_status, "http_requests": a.http_requests,
           "elapsed_s": round(a.elapsed_s, 2), "sha256_raw": raw_sha, "steps": ["request", "decode_base64"],
           "ok": a.ok}
    if prompt_id:
        rec["prompt_id"] = prompt_id
    if not a.ok:
        rec["error_code"] = a.code
        rec["error"] = a.message
    return {k: v for k, v in rec.items() if v is not None}


def _save_attempt(a: Attempt, base: Path, *, tool: str, host: str, model: str, prompt: str, prompt_id: str | None,
                  params: dict) -> tuple[Path | None, Path, str | None]:
    """Write the PNG (when there is one) and its sidecar. Returns ``(png path, sidecar path, sha256_raw)``."""
    png_path = base.with_suffix(".png")
    if a.ok and a.image:
        raw_sha = sha256_bytes(a.image)
        try:
            png = _png_of(a)
        except SatkError:
            a.ok, a.code, a.message = False, "NO_IMAGE", "the returned data is not a readable image"
            png = b""
        if a.ok:
            atomic_write(png_path, png)
            rec = _attempt_record(a, tool=tool, host=host, model=model, prompt=prompt, prompt_id=prompt_id,
                                  params=params, raw_sha=raw_sha)
            rec["sha256"] = sha256_bytes(png)
            return png_path, write_sidecar(png_path, rec), raw_sha
    side = base.with_name(base.name + ".failed.png")  # sidecar() adds ".provenance.json" to this name's stem
    rec = _attempt_record(a, tool=tool, host=host, model=model, prompt=prompt, prompt_id=prompt_id, params=params,
                          raw_sha=None)
    return None, write_sidecar(side.with_suffix(""), rec), None


def generate_images(client: ImageClient, prompt: str, out: Path, stem: str, *, n: int, model: str, aspect: str,
                    size: str, tool: str = "satk imagegen.generate", calls_left: int | None = None) -> dict:
    """``n`` candidates for one prompt into ``out`` as ``<stem>_<k>.png`` (+ sidecars; failures too).

    Stops early on ``AUTH_FAILED`` or after two endpoint failures in a row. Returns
    ``{"attempts": [row...], "calls": int, "fatal": Attempt | None}``; a row is
    ``{"k", "ok", "code", "file", "sidecar", "http_requests"}``.
    """
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    calls, fatal, streak = 0, None, 0
    for k in range(1, n + 1):
        if calls_left is not None and calls >= calls_left:
            rows.append({"k": k, "ok": False, "code": "BUDGET", "file": None, "sidecar": None, "http_requests": 0})
            break
        budget = 2 if calls_left is None else min(2, calls_left - calls)
        a = client.generate(prompt, model=model, aspect=aspect, size=size, request_budget=budget)
        calls += a.http_requests
        params = {"aspect_ratio": aspect, "image_size": size, "candidate": k}
        png, side, _raw = _save_attempt(a, out / f"{stem}_{k}", tool=tool, host=client.host, model=model,
                                        prompt=prompt, prompt_id=None, params=params)
        rows.append({"k": k, "ok": a.ok, "code": a.code, "file": png, "sidecar": side,
                     "http_requests": a.http_requests, "message": a.message, "attempt": a})
        if a.fatal:
            fatal = a
            break
        streak = streak + 1 if a.code == "ENDPOINT_FAILED" else 0
        if streak >= _STOP_AFTER_ENDPOINT_FAILURES:
            break
    return {"attempts": rows, "calls": calls, "fatal": fatal}


# --------------------------------------------------------------------------------------- icon set run


def _candidate_files(icon: Icon, k: int, keyed: Path) -> list[Path]:
    return [keyed / f"{icon.id}_c{k}_{s}.png" for s in icon.sizes]


def _key_candidate(raw_png: Path, icon: Icon, k: int, spec: Spec, keyed: Path, raw_rec: dict,
                   params: KeyParams) -> dict[str, Any]:
    """Key + shrink one raw image into ``keyed/``; returns the candidate row."""
    row: dict[str, Any] = {"icon": icon.name, "candidate": k, "files": [], "valid": False, "errors": []}
    try:
        master, steps = key_image(load_image(raw_png), params)
    except SatkError as e:
        row["errors"] = [f"KEY_FAILED: {e.msg}"]
        return row
    master_path = atomic_write(keyed / f"{icon.id}_c{k}_master.png", png_bytes(master))
    row["master"] = master_path
    raw_sha = raw_rec.get("sha256_raw")
    all_ok = True
    for size in icon.sizes:
        im, shrink_steps = shrink(master, size, params)
        data = png_bytes(im)
        v = validate_image(im)
        path = atomic_write(keyed / f"{icon.id}_c{k}_{size}.png", data)
        rec = dict(raw_rec)
        rec.update({"tool": "satk imagegen.icon_set", "icon": icon.id, "size": size, "sha256_raw": raw_sha,
                    "sha256": sha256_bytes(data), "steps": ["request", "decode_base64"] + steps + shrink_steps,
                    "key_params": params.asdict(), "validation": v, "raw_file": f"raw/{raw_png.name}"})
        write_sidecar(path, rec)
        row["files"].append(path)
        if not v["ok"]:
            all_ok = False
            row["errors"] += [f"{size}px: {m}" for m in v["errors"]]
    row["valid"] = all_ok
    return row


def run_icon_set(spec: Spec, out: Path, *, client: ImageClient | None, candidates: int | None = None,
                 max_calls: int = MAX_CALLS_DEFAULT, model: str | None = None, only: list[str] | None = None,
                 dry_run: bool = False) -> dict:
    """Generate, key, validate and sheet every icon of ``spec`` into ``out``. Never overwrites ``final/``."""
    icons = [i for i in spec.icons if not only or i.name in only or i.id in only]
    if not icons:
        raise SatkError("NOT_FOUND", f"no icon of spec {spec.name} matches {only}",
                        did_you_mean=[i.name for i in spec.icons])
    n_cand = int(candidates or spec.candidates)
    if not 1 <= n_cand <= 8:
        raise SatkError("BAD_PARAMS", "candidates must be 1..8")
    if max_calls < 1:
        raise SatkError("BAD_PARAMS", "max_calls must be >= 1")
    model = model or spec.model
    plan = [{"icon": i.name, "candidate": k, "prompt": prompt_for(spec, i)} for i in icons for k in range(1, n_cand + 1)]
    if dry_run:
        return {"dry_run": True, "model": model, "planned_calls": len(plan), "max_calls": max_calls,
                "endpoint_host": endpoint_host(), "plan": plan}
    if client is None:
        raise SatkError("INTERNAL", "run_icon_set needs a client unless dry_run")
    ensure_writable(out)
    raw_dir, keyed = out / "raw", out / "keyed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    keyed.mkdir(parents=True, exist_ok=True)
    params = key_params_of(spec)
    cand_rows: list[dict[str, Any]] = []
    calls, fatal, codes = 0, None, []
    streak = 0
    stop = False
    for icon in icons:
        prompt = prompt_for(spec, icon)
        for k in range(1, n_cand + 1):
            row: dict[str, Any] = {"icon": icon.name, "candidate": k, "files": [], "valid": False, "errors": []}
            if stop or calls >= max_calls:
                row["code"] = "SKIPPED"
                cand_rows.append(row)
                continue
            budget = min(2, max_calls - calls)
            a = client.generate(prompt, model=model, aspect=spec.aspect, size=spec.size, request_budget=budget)
            calls += a.http_requests
            p = {"aspect_ratio": spec.aspect, "image_size": spec.size, "candidate": k, "icon": icon.id}
            png, side, _ = _save_attempt(a, raw_dir / f"{icon.id}_c{k}", tool="satk imagegen.icon_set",
                                         host=client.host, model=model, prompt=prompt, prompt_id=spec.prompt_id,
                                         params=p)
            row["http_requests"] = a.http_requests
            if a.ok and png is not None:
                rec = json.loads(side.read_text(encoding="utf-8"))
                rec.pop("file", None)
                row.update(_key_candidate(png, icon, k, spec, keyed, rec, params))
                row["raw"] = png
                streak = 0
                if not row["valid"]:
                    row["code"] = "INVALID"
            else:
                row["code"] = a.code or "NO_IMAGE"
                row["errors"] = [a.message]
                row["sidecar"] = side
                codes.append(row["code"])
                streak = streak + 1 if a.code == "ENDPOINT_FAILED" else 0
                if a.fatal:
                    fatal, stop = a, True
                elif streak >= _STOP_AFTER_ENDPOINT_FAILURES:
                    stop = True
            cand_rows.append(row)
    # contact sheets: rows = icons, columns = candidates
    grid = [[r for r in cand_rows if r["icon"] == i.name] for i in icons]
    masters = [r.get("master") for rows in grid for r in rows]
    smalls = [(r["files"][0] if r["files"] else None) for rows in grid for r in rows]
    sheet = small = None
    if any(m is not None for m in masters):
        sheet = render_png_sheet(masters, out / "contact_sheet.png", cols=n_cand, cell=160)
        small = render_png_sheet(smalls, out / "contact_small.png", cols=n_cand, cell=160)
    legend = []
    for i, r in enumerate((r for rows in grid for r in rows), start=1):
        legend.append({"n": i, "icon": r["icon"], "candidate": r["candidate"], "valid": bool(r["valid"]),
                       "code": r.get("code"), "files": [jpath(f) for f in r["files"]], "errors": r["errors"]})
    write_json(out / "candidates.json", {"spec": spec.name, "model": model, "legend": legend})
    valid_icons = sorted({r["icon"] for r in cand_rows if r["valid"]})
    summary = {"spec": spec.name, "model": model, "endpoint_host": client.host, "calls": calls,
               "max_calls": max_calls, "candidates_per_icon": n_cand, "planned_calls": len(plan),
               "icons_with_valid_candidate": valid_icons,
               "icons_without": sorted({i.name for i in icons} - set(valid_icons)),
               "error_codes": sorted(set(codes)), "time_utc": utc_now()}
    write_json(out / "run.json", summary)
    result = dict(summary, out=out, sheet=sheet, small_sheet=small, legend=legend, rows=cand_rows)
    if fatal is not None:
        raise imagegen_error("AUTH_FAILED", fatal.message,
                             hint="check the key in SATK_IMAGEGEN_API_KEY (the user rotates it); the run stopped",
                             data={"calls": calls, "dir": jpath(out)})
    return result


# --------------------------------------------------------------------------------------- finalize


def write_set_md(out: Path, spec: Spec, rows: list[dict[str, Any]], *, kind: str, note: str = "") -> Path:
    """``PROVENANCE.md`` of a shipped set: one row per file (file, SHA-256, generator/model, prompt id, date, steps)."""
    table = []
    for r in sorted(rows, key=lambda r: spec.shipped_files().index(r["file"]) if r["file"] in spec.shipped_files()
                    else 999):
        steps = ">".join(s if isinstance(s, str) else str(s.get("step", "?")) for s in r.get("steps", []))
        table.append([r["file"], r["sha256"], r.get("generator", "placeholder"), r.get("model") or "-",
                      r.get("prompt_id") or "-", (r.get("time_utc") or "")[:10], steps])
    intro = [f"Icon set `{spec.name}` ({kind}). {note}".strip(),
             "Generated images are our own assets: prompts contain no game screenshots or third-party material. "
             "Each file has a sidecar `<file>.provenance.json` with the full record (prompt text, parameters, "
             "response text, SHA-256 of the raw and the final image). The API key is never recorded."]
    md = provenance_md(f"Provenance of {spec.name} ({kind})", intro,
                       ["file", "sha256", "generator", "model", "prompt id", "date", "steps"], table)
    return atomic_write(out / "PROVENANCE.md", md)


def _parse_picks(picks: dict[str, Any] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (picks or {}).items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            raise SatkError("BAD_PARAMS", f"pick for {k!r} must be a candidate number, got {v!r}") from None
    return out


def finalize(spec: Spec, run: Path, final: Path, *, picks: dict[str, Any] | None = None,
             use_placeholder: list[str] | None = None) -> dict:
    """Copy the picked candidates (default: the first valid one per icon) into ``final/`` and draw the status dots.

    ``picks`` maps an icon name to the number printed on ``contact_sheet.png`` (``candidates.json`` maps it back to
    icon and candidate); without that file it is the candidate number of the icon.

    ``use_placeholder`` names icons that take the Pillow-drawn version instead (e.g. an unreadable lock at 24 px).
    Raises ``CHECK_FAILED`` when an icon has neither a valid candidate nor a placeholder choice.
    """
    keyed = run / "keyed"
    picked = _parse_picks(picks)
    legend: dict[int, dict] = {}
    try:
        for e in json.loads((run / "candidates.json").read_text(encoding="utf-8")).get("legend", []):
            legend[int(e["n"])] = e
    except (OSError, ValueError, KeyError):
        pass  # no run folder legend: picks are candidate numbers of the icon
    subst = set(use_placeholder or [])
    ensure_writable(final)
    final.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    chosen: dict[str, Any] = {}
    missing: list[str] = []
    for icon in spec.icons:
        if icon.name in subst or icon.id in subst:
            rows += write_placeholder_set(spec, final, only=[icon.id])
            chosen[icon.name] = "placeholder"
            continue
        k = picked.get(icon.name) or picked.get(icon.id)
        if k is not None and legend:
            hit = legend.get(k)
            if hit is None or hit["icon"] != icon.name:
                owner = hit["icon"] if hit else "no icon"
                raise SatkError("BAD_PARAMS", f"sheet number {k} is a candidate of {owner}, not of {icon.name}",
                                hint="picks are the numbers on contact_sheet.png (see candidates.json)",
                                did_you_mean=[f"{icon.name}={n}" for n, e in sorted(legend.items())
                                              if e["icon"] == icon.name])
            k = int(hit["candidate"])
        if k is None:
            for cand in range(1, 9):
                files = _candidate_files(icon, cand, keyed)
                if all(f.is_file() for f in files) and all(
                        json.loads(sidecar_path(f).read_text(encoding="utf-8")).get("validation", {}).get("ok")
                        for f in files):
                    k = cand
                    break
        if k is None:
            missing.append(icon.name)
            continue
        for size, src in zip(icon.sizes, _candidate_files(icon, k, keyed)):
            if not src.is_file():
                raise SatkError("NOT_FOUND", f"candidate {k} of {icon.name} has no {size}px file",
                                hint="satk imagegen icon-set")
            rec = json.loads(sidecar_path(src).read_text(encoding="utf-8"))
            if not rec.get("validation", {}).get("ok"):
                raise SatkError("CHECK_FAILED", f"candidate {k} of {icon.name} ({size}px) does not pass validation: "
                                f"{rec.get('validation', {}).get('errors')}")
            data = src.read_bytes()
            dest = atomic_write(final / f"{icon.id}_{size}.png", data)
            rec.update({"tool": "satk imagegen.finalize", "picked_candidate": k,
                        "picked_from": f"keyed/{src.name}", "sha256": sha256_bytes(data)})
            rec.pop("file", None)
            write_sidecar(dest, rec)
            rows.append({"file": dest.name, "sha256": rec["sha256"], "generator": "ai", "model": rec.get("model"),
                         "prompt_id": rec.get("prompt_id"), "time_utc": rec.get("time_utc"),
                         "steps": rec.get("steps", []), "path": dest, "size": size, "valid": True})
        chosen[icon.name] = k
    if missing:
        raise SatkError("CHECK_FAILED", f"no valid candidate for: {', '.join(missing)}",
                        hint="rerun `satk imagegen icon-set --only <icon>` or pass use_placeholder",
                        data={"missing": missing})
    dots = write_placeholder_set(spec, final, only=[s.id for s in spec.status]) if spec.status else []
    rows += dots
    for r in rows:
        r.setdefault("valid", True)
    names = {r["file"] for r in rows}
    gaps = [f for f in spec.shipped_files() if f not in names]
    if gaps:
        raise SatkError("CHECK_FAILED", f"final set is incomplete: {', '.join(gaps)}")
    ai = [i for i, v in chosen.items() if v != "placeholder"]
    note = (f"AI-generated icons ({', '.join(ai) or 'none'}), model {spec.model}; status dots and placeholder "
            "substitutions are drawn with Pillow." if ai else "Every file is Pillow-drawn.")
    md = write_set_md(final, spec, rows, kind="final", note=note)
    return {"final": final, "chosen": chosen, "files": len(rows), "provenance": md,
            "all_valid": all(r.get("valid") for r in rows), "rows": rows}
