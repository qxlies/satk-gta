"""Operations of ``satk.imagegen``: AI image generation for UI assets with a Pillow placeholder fallback.

* ``imagegen.generate``    -> ``satk imagegen generate "<prompt>"``      (network, CLI only);
* ``imagegen.key``         -> ``satk imagegen key <png...>``             chroma key + trim + resize + validation;
* ``imagegen.icon_set``    -> ``satk imagegen icon-set [--spec ...]``          (network, CLI only);
* ``imagegen.placeholder`` -> ``satk imagegen placeholder [--spec ...]``       Pillow-drawn set, no API;
* ``imagegen.finalize``    -> ``satk imagegen finalize [--spec ...]``          pick candidates -> ``final/`` + PROVENANCE.md.

The API key comes only from the environment variable ``SATK_IMAGEGEN_API_KEY``; there is no option to pass it. Pillow and
numpy are imported inside the helpers (stdlib-only module level).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.envelope import table, with_warn
from ..core.errors import SatkError
from ..core.paths import ensure_writable, jpath, work
from ..core.registry import op
from ..mcp.generic import cli_only
from .client import ASPECTS, DEFAULT_MODEL, SIZES


def _out_dir(out: Path | str | None, *default: str) -> Path:
    """``out`` (checked with the write guard) or ``work/out/imagegen/<default...>``."""
    if out is None:
        return Path(work("out", "imagegen", *default))
    return ensure_writable(Path(out))


def _set_dir(spec_name: str, out: Path | str | None, sub: str = "") -> tuple[Any, Path]:
    from .spec import load_spec

    spec = load_spec(spec_name)
    if out is not None:
        return spec, ensure_writable(Path(out))
    base = Path(work("out", "imagegen", spec.out))
    return spec, (base / sub if sub else base)


@cli_only("sends the prompt to a paid third-party endpoint with the user's key", consent=True)
@op("imagegen.generate", mcp=False, long_running=True,
    summary="Generate up to 4 images from one prompt with the image endpoint (key from env SATK_IMAGEGEN_API_KEY only). "
            "Writes PNG files and .provenance.json sidecars, also for failed attempts. Errors: NO_API_KEY, "
            "AUTH_FAILED, ENDPOINT_FAILED, NO_IMAGE (in error.data.imagegen_code).",
    summary_ru="Генерация до 4 изображений по запросу через сервис картинок (ключ только из SATK_IMAGEGEN_API_KEY); "
               "PNG и файлы происхождения рядом, в том числе для неудачных попыток.",
    examples=('satk imagegen generate "flat icon of a padlock on magenta background" --n 2',))
def generate(prompt: str, model: str = DEFAULT_MODEL, aspect: str = "1:1", size: str = "1K", n: int = 1,
             out: Path | None = None, name: str = "image") -> dict:
    """Generate ``n`` images for one prompt.

    Args:
        prompt: the text prompt (never include game screenshots or third-party material).
        model: image model id of the endpoint.
        aspect: aspect ratio (1:1, 3:2, 16:9, ...).
        size: image size class: 0.5K, 1K, 2K or 4K.
        n: number of candidates, 1-4 (one call each).
        out: output folder (default work/out/imagegen/generate).
        name: file stem; files are <name>_<k>.png.
    """
    from .client import ImageClient
    from .iconset import generate_images, _slug

    if not prompt.strip():
        raise SatkError("BAD_PARAMS", "prompt is empty")
    if not 1 <= n <= 4:
        raise SatkError("BAD_PARAMS", f"n must be 1..4, got {n}")
    if aspect not in ASPECTS or size not in SIZES:
        raise SatkError("BAD_PARAMS", f"aspect {aspect!r} / size {size!r} not allowed",
                        hint=f"aspect: {', '.join(ASPECTS)}; size: {', '.join(SIZES)}")
    folder = _out_dir(out, "generate")
    client = ImageClient.from_env()
    res = generate_images(client, prompt, folder, _slug(name), n=n, model=model, aspect=aspect, size=size)
    rows = [[a["k"], a["ok"], a["code"] or "", jpath(a["file"]) if a["file"] else "", jpath(a["sidecar"])]
            for a in res["attempts"]]
    fatal = res["fatal"]
    if fatal is not None:
        from .client import imagegen_error

        raise imagegen_error("AUTH_FAILED", fatal.message, hint="check SATK_IMAGEGEN_API_KEY (the user rotates it)",
                             data={"calls": res["calls"], "dir": jpath(folder)})
    good = [a for a in res["attempts"] if a["ok"]]
    if not good:
        codes = sorted({a["code"] for a in res["attempts"] if a["code"]})
        first = next((a for a in res["attempts"] if a.get("message")), None)
        from .client import imagegen_error

        raise imagegen_error(codes[0] if codes else "NO_IMAGE", (first or {}).get("message", "no image produced"),
                             hint="see the .failed.provenance.json sidecars in the output folder",
                             data={"calls": res["calls"], "codes": codes, "dir": jpath(folder)})
    env = table(["k", "ok", "code", "file", "sidecar"], rows,
                warn=[f"{a['code']}: candidate {a['k']} failed" for a in res["attempts"] if not a["ok"]])
    env.update({"calls": res["calls"], "model": model, "dir": jpath(folder)})
    return env


@op("imagegen.key", mcp=False,
    summary="Chroma-key generated badge images (magenta #FF00FF background): alpha ramp, despill, trim to the badge, "
            "pad 4%, Lanczos resize to --sizes, unsharp at <=48 px, then validate (corners transparent, no magenta "
            "left, badge >= 50% of canvas). Writes PNGs and provenance sidecars.",
    summary_ru="Хромакей сгенерированных значков (фон #FF00FF): прозрачность, подавление розового, обрезка по значку, "
               "поля 4%, масштаб Ланцош до --sizes, резкость до 48 px и проверка.",
    examples=("satk imagegen key work/out/imagegen/sae-v1/raw/lock_c1.png --sizes 24,48 --name lock",))
def key(src: list[str], out: Path | None = None, sizes: list[int] | None = None, name: str | None = None,
        key_color: str = "#FF00FF", hard: float = 90.0, soft: float = 130.0, badge_color: str = "#1F2428",
        pad: float = 0.04, unsharp_max: int = 48, strict: bool = False) -> dict:
    """Key one or more raw images into shipped sizes.

    Args:
        src: PNG/JPEG files with a flat key-colour background.
        out: output folder (default work/out/imagegen/key).
        sizes: output sizes in px (default 64,128).
        name: file stem for a single source (default: the source stem); several sources use their own stems.
        key_color: background colour to remove (#RRGGBB).
        hard: RGB distance up to which a pixel is fully transparent.
        soft: RGB distance from which a pixel is fully opaque (linear ramp between hard and soft).
        badge_color: colour that edge pixels are blended toward (the badge fill).
        pad: padding around the trimmed badge, as a fraction of its side.
        unsharp_max: apply the unsharp mask at sizes up to this many px.
        strict: fail with CHECK_FAILED when an output does not pass validation.
    """
    from .iconset import _slug
    from .keying import KeyParams, key_image, load_image, png_bytes, shrink, validate_image
    from .provenance import sha256_bytes, sha256_file, utc_now, write_sidecar
    from ..core.paths import atomic_write

    if not src:
        raise SatkError("BAD_PARAMS", "no source images given")
    params = KeyParams(key_color=key_color, hard=hard, soft=soft, badge_color=badge_color, pad=pad,
                       unsharp_max=unsharp_max).check()
    sizes = [int(s) for s in (sizes or [64, 128])]
    folder = _out_dir(out, "key")
    folder.mkdir(parents=True, exist_ok=True)
    rows, bad = [], []
    for s in src:
        sp = Path(s)
        if not sp.is_file():
            raise SatkError("NOT_FOUND", f"no such file: {s}")
        stem = _slug(name) if (name and len(src) == 1) else _slug(sp.stem)
        master, steps = key_image(load_image(sp), params)
        raw_sha = sha256_file(sp)
        for size in sizes:
            im, shrink_steps = shrink(master, size, params)
            data = png_bytes(im)
            v = validate_image(im)
            path = atomic_write(folder / f"{stem}_{size}.png", data)
            write_sidecar(path, {"tool": "satk imagegen.key", "generator": "keyed", "source": sp.name,
                                 "sha256_raw": raw_sha, "sha256": sha256_bytes(data), "size": size,
                                 "key_params": params.asdict(), "steps": steps + shrink_steps, "validation": v,
                                 "time_utc": utc_now()})
            rows.append([jpath(path), size, v["ok"], v["coverage"], v["magenta_pixels"], "; ".join(v["errors"])])
            if not v["ok"]:
                bad.append(path.name)
    env = table(["file", "size", "valid", "coverage", "magenta", "errors"], rows)
    if bad and strict:
        raise SatkError("CHECK_FAILED", f"{len(bad)} output(s) failed validation: {', '.join(bad)}",
                        data={"files": bad})
    if bad:
        with_warn(env, f"CHECK_FAILED: {len(bad)} output(s) do not pass validation: {', '.join(bad)}")
    env["dir"] = jpath(folder)
    return env


@cli_only("sends prompts to a paid third-party endpoint with the user's key", consent=True)
@op("imagegen.icon_set", mcp=False, long_running=True,
    summary="Generate an icon set from a TOML spec: 3 candidates per icon (<= --max-calls requests, default 24), key "
            "and resize each, validate, write contact sheets and provenance. Status dots are never generated. "
            "--dry-run prints the plan without a key.",
    summary_ru="Набор значков по TOML-спецификации: по 3 кандидата на значок (до --max-calls запросов), хромакей, "
               "масштаб, проверка, контакт-листы и файлы происхождения; --dry-run показывает план.",
    examples=("satk imagegen icon-set --dry-run", "satk imagegen icon-set --only lock --candidates 3"))
def icon_set(spec: str = "sae_v1", out: Path | None = None, candidates: int | None = None, max_calls: int = 24,
             model: str | None = None, only: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run the generation of a whole icon set.

    Args:
        spec: packaged spec name (sae_v1) or path to a .toml spec.
        out: run folder (default work/out/imagegen/<spec out>).
        candidates: candidates per icon (default from the spec, 1-8).
        max_calls: hard cap on HTTP requests of the run (a retry counts).
        model: override the model of the spec.
        only: icon names to run (default all).
        dry_run: print the plan (prompts, call count) and make no request.
    """
    from .client import ImageClient
    from .iconset import run_icon_set
    from .spec import load_spec

    sp, folder = _set_dir(spec, out)
    if dry_run:
        plan = run_icon_set(sp, folder, client=None, candidates=candidates, max_calls=max_calls, model=model,
                            only=only, dry_run=True)
        env = table(["icon", "candidate", "prompt"], [[p["icon"], p["candidate"], p["prompt"][:90] + "..."]
                                                       for p in plan["plan"]])
        env.update({"dry_run": True, "planned_calls": plan["planned_calls"], "max_calls": max_calls,
                    "model": plan["model"], "endpoint_host": plan["endpoint_host"]})
        return env
    load_spec(spec)
    client = ImageClient.from_env()
    res = run_icon_set(sp, folder, client=client, candidates=candidates, max_calls=max_calls, model=model, only=only)
    rows = [[e["n"], e["icon"], e["candidate"], e["valid"], e.get("code") or "", "; ".join(e["errors"])[:120]]
            for e in res["legend"]]
    env = table(["n", "icon", "candidate", "valid", "code", "errors"], rows)
    env.update({"calls": res["calls"], "max_calls": res["max_calls"], "model": res["model"],
                "icons_with_valid_candidate": res["icons_with_valid_candidate"],
                "icons_without": res["icons_without"], "dir": jpath(folder)})
    if res["error_codes"]:
        env["error_codes"] = res["error_codes"]
    if res.get("sheet"):
        env["sheet"] = jpath(res["sheet"])
        env["small_sheet"] = jpath(res["small_sheet"])
    if not res["icons_with_valid_candidate"]:
        from .client import imagegen_error

        raise imagegen_error((res["error_codes"] or ["NO_IMAGE"])[0], "no icon got a valid candidate",
                             hint="see run.json and the .failed.provenance.json sidecars",
                             data={"calls": res["calls"], "codes": res["error_codes"], "dir": jpath(folder)})
    return env


@op("imagegen.placeholder", mcp=False,
    summary="Draw the whole icon set with Pillow (no API call, no key): rounded badge, hills + dashed sight line "
            "presets, padlock, status dots, already transparent, with provenance sidecars (generator: placeholder) "
            "and PROVENANCE.md. Every file is validated.",
    summary_ru="Рисует весь набор значков в Pillow (без сети и ключа): значки пресетов, замок, точки статуса, "
               "прозрачные, с файлами происхождения и PROVENANCE.md; каждый файл проверяется.",
    examples=("satk imagegen placeholder", "satk imagegen placeholder --only lock"))
def placeholder(spec: str = "sae_v1", out: Path | None = None, only: list[str] | None = None) -> dict:
    """Write the fallback set.

    Args:
        spec: packaged spec name (sae_v1) or path to a .toml spec.
        out: output folder (default work/out/imagegen/<spec out>/placeholder).
        only: icon or status names to draw (default all).
    """
    from .iconset import write_set_md
    from .placeholder import write_set

    sp, folder = _set_dir(spec, out, "placeholder")
    rows = write_set(sp, folder, only=only)
    if not rows:
        raise SatkError("NOT_FOUND", f"nothing matches {only}", did_you_mean=[i.name for i in sp.icons]
                        + [s.name for s in sp.status])
    md = None
    if not only:
        md = write_set_md(folder, sp, rows, kind="placeholder", note="Every file is drawn with Pillow; no API was used.")
    env = table(["file", "size", "valid", "sha256", "errors"],
                [[r["file"], r["size"], r["valid"], r["sha256"][:16], "; ".join(r["errors"])] for r in rows])
    bad = [r["file"] for r in rows if not r["valid"]]
    if bad:
        with_warn(env, f"CHECK_FAILED: {len(bad)} file(s) do not pass validation: {', '.join(bad)}")
    env["dir"] = jpath(folder)
    if md:
        env["provenance"] = jpath(md)
    return env


@op("imagegen.finalize", mcp=False,
    summary="Build final/ of an icon-set run: copy the picked candidate per icon (default the first valid one), draw "
            "the status dots, optionally take named icons from the Pillow placeholder, validate every file and "
            "write PROVENANCE.md.",
    summary_ru="Собирает final/ из прогона icon-set: выбранный кандидат на значок (по умолчанию первый годный), "
               "точки статуса, при необходимости заглушка Pillow для названных значков, проверка и PROVENANCE.md.",
    examples=('satk imagegen finalize --picks {"classic":2,"lock":13}',
              "satk imagegen finalize --use-placeholder lock"))
def finalize(spec: str = "sae_v1", run: Path | None = None, final: Path | None = None,
             picks: dict[str, Any] | None = None, use_placeholder: list[str] | None = None) -> dict:
    """Write the shipped set from the candidates of a run.

    Args:
        spec: packaged spec name (sae_v1) or path to a .toml spec.
        run: run folder of imagegen.icon_set (default work/out/imagegen/<spec out>).
        final: output folder (default <run>/final).
        picks: JSON object icon name -> number printed on contact_sheet.png (default: first valid candidate).
        use_placeholder: icon names that take the Pillow-drawn version (e.g. a lock unreadable at 24 px).
    """
    from .iconset import finalize as _finalize

    sp, base = _set_dir(spec, run)
    dest = ensure_writable(Path(final)) if final is not None else base / "final"
    res = _finalize(sp, base, dest, picks=picks, use_placeholder=use_placeholder)
    env = table(["file", "size", "valid", "generator", "sha256"],
                [[r["file"], r.get("size"), bool(r.get("valid")), r.get("generator", "placeholder"), r["sha256"][:16]]
                 for r in res["rows"]])
    env.update({"dir": jpath(dest), "chosen": res["chosen"], "provenance": jpath(res["provenance"])})
    return env
