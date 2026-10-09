# Imagegen: UI icons from an image API, with a Pillow fallback

[Русская версия](../ru/imagegen.md)

Package: `satk.imagegen`.

## What it is

`satk imagegen` makes small UI assets (icons, badges, status dots) for a game or tool: it asks an image
endpoint for a badge on a flat magenta background, removes the background (chroma key), trims, resizes and
validates the result, and records where every file came from. Without an API key it draws the same set with
Pillow (`satk imagegen placeholder`), so a project never waits for the network. Everything is written under
`<workspace>/work/out/imagegen/`.

The icon set `sae_v1` (the Engine tab of the sa-engine client: four draw-distance presets, a "limited by server"
padlock and three status dots) is shipped as a spec; you can write your own TOML spec.

## Quick example

```powershell
satk imagegen icon-set --dry-run --only lock
```

This lists the planned requests (3 candidates of the padlock, 3 calls) and sends nothing. The fallback needs no key:

<!-- docs-smoke: skip rewrites the shipped placeholder folder and its sidecars; safe to run by hand -->
```powershell
satk imagegen placeholder --only lock
```

It draws `lock_24.png` and `lock_48.png` into `<workspace>/work/out/imagegen/sae-v1/placeholder/` with sidecars;
every row says `valid: true`:

```json
{"ok":true,"cols":["file","size","valid","sha256","errors"],"rows":[["lock_24.png",24,true,"83a84b4deb3d6efc",""],["lock_48.png",48,true,"c062113fb04084ce",""]],"n":2}
```

A real run (needs the key, spends API credit, CLI only):

<!-- docs-smoke: skip needs the API key and spends credit -->
```powershell
satk imagegen icon-set --max-calls 24
satk imagegen finalize --picks '{"classic":2,"balanced":4,"high":7,"extreme":11,"lock":13}'
```

`icon-set` writes `contact_sheet.png` (keyed candidates, numbered, one row per icon) and `contact_small.png` (the
smallest shipped size, enlarged); look at them, then `finalize` copies the numbered picks into `final` with
`PROVENANCE.md`.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk imagegen generate "<prompt>" [--n 1-4] [--aspect 1:1] [--size 1K] [--model M] [--out DIR] [--name STEM]` | CLI only (consent) | Call the endpoint `n` times; PNG + `.provenance.json` per attempt, also for failures. |
| `satk imagegen key <png...> [--sizes 64,128] [--name STEM] [--strict]` | `satk_op` → `imagegen.key` | Chroma key, trim, pad, Lanczos resize, unsharp (<= 48 px), validate. |
| `satk imagegen icon-set [--spec sae_v1] [--candidates 3] [--max-calls 24] [--only lock] [--dry-run]` | CLI only (consent) | Generate every icon of a spec, key and validate it, write contact sheets. |
| `satk imagegen placeholder [--spec sae_v1] [--only NAME] [--out DIR]` | `satk_op` → `imagegen.placeholder` | Draw the whole set with Pillow; no network, no key. |
| `satk imagegen finalize [--spec sae_v1] [--picks JSON] [--use-placeholder NAMES]` | `satk_op` → `imagegen.finalize` | Build `final` from the picked candidates, add the status dots, validate, write `PROVENANCE.md`. |

`generate` and `icon-set` are CLI only because they send a prompt to a third-party service with the user's key
and spend credit; an agent asks the user first. Parameters of `--spec` are a packaged name or a path to a `.toml`.

## How it works

**Key.** The key is read only from the environment variable `SATK_IMAGEGEN_API_KEY`. There is no option, file or
config for it; satk never prints, logs or stores it. Every error, sidecar and `repr` passes through a scrubber that
also masks `Bearer ...` text, and the HTTP client does not follow redirects, so the `Authorization` header cannot
leave the endpoint host. The endpoint is `https://api.rout.my/v1/chat/completions`; `SATK_IMAGEGEN_ENDPOINT`
overrides it (https only; http is accepted for `localhost`).

**Request.** `POST` with `{"model", "messages": [{"role": "user", "content": PROMPT}], "modalities": ["image",
"text"], "image_config": {"aspect_ratio", "image_size"}}`; the image is the first usable
`choices[0].message.images[i].image_url.url` (`data:image/...;base64,...`). Default model
`google/gemini-3.1-flash-image-preview` (about 14 s per 1K image), timeout 120 s. Prompts never contain game
screenshots or third-party material.

**Failures.** The envelope code is one of the frozen satk codes; the imagegen code is the first word of the
message and in `error.data.imagegen_code`:

| imagegen code | envelope code | When | What happens |
|---|---|---|---|
| `NO_API_KEY` | `NOT_READY` (exit 3) | the variable is absent or empty | nothing is sent; use `placeholder` |
| `AUTH_FAILED` | `AUTH` | HTTP 401/403 | the run stops at once |
| `ENDPOINT_FAILED` | `EXTERNAL_TOOL` | 429, 5xx or a network error twice (one retry after 10 s), other HTTP errors | the candidate fails; two in a row stop the run |
| `NO_IMAGE` | `EXTERNAL_TOOL` | HTTP 200 without a usable `images[]` | the first 300 characters of the answer text and `finish_reason` go into the attempt's sidecar; the next candidate runs |

Every HTTP request counts against `--max-calls` (a retry counts too); when the cap is reached the remaining
candidates are `SKIPPED`.

**Post-processing** (`imagegen.key`, same steps in every sidecar): chroma key `#FF00FF` (RGB distance <= 90
transparent, 90 to 130 a linear alpha ramp, edge pixels decontaminated and blended toward the badge colour),
despill (`min(r, b) > g` is pulled to `g`), trim to the badge box, pad 4 %, Lanczos resize (premultiplied alpha),
unsharp mask at <= 48 px, alpha below 4 snapped to 0.

**Validation.** The four corner pixels have alpha 0; no pixel with alpha >= 16 has a hue within 15 degrees of
magenta and saturation above 0.5; the badge (alpha >= 128) covers at least 50 % of the canvas. A candidate that
fails is flagged `valid: false` and never reaches `final`.

**Provenance.** Each file has `<file>.provenance.json`: tool, endpoint host, model, prompt (and prompt id), the
request parameters, UTC time, response text and finish reason, SHA-256 of the raw and the final image, the steps
and the validation result. `PROVENANCE.md` in `placeholder` and `final` has one row per shipped file: file,
SHA-256, generator, model, prompt id, date, steps. Generated outputs are our own assets; the repository never
commits the PNGs (the asset guard flags them): they go to the project that ships them.

**Spec** (`src/satk/imagegen/specs/sae_v1.toml`): the fixed style prompt with `{subject}` and `{accent}`, the
icons (`id`, `subject`, `accent`, shipped `sizes`, placeholder shape) and the status dots. Files are named
`<id>_<size>.png`: `preset_{classic,balanced,high,extreme}_{64,128}`, `lock_{24,48}`, `status_{ok,warn,error}_12`.

**Placeholder.** Badge `#1F2428` with ring `#3A4148`; the emblem is off-white `#E8EAED` plus the accent: one to
four layered hills (classic `#9AA0A6`, balanced `#34A853`, high `#4285F4`, extreme `#FB8C00` with a tiny skyline)
and a dashed sight line that gets longer with the preset, or a `#FBBC04` padlock (rectangle and arc). Status dots
are flat circles (`#34A853`, `#FBBC04`, `#EA4335`) with a 1 px darker ring. Drawing is 8x or more oversampled and
deterministic: the same call writes the same bytes.

## Limitations and known issues

- The model draws a slightly different badge every time (corner radius, ring width); use the contact sheet to pick,
  and `--use-placeholder lock` when an icon is unreadable at its smallest size.
- A first request may time out at 120 s; the one retry then makes the call count two.
- `--out` and `--spec` take paths; outputs must be under `<workspace>/work` (the write guard refuses other places).
- Needs Pillow and numpy (`scripts\bootstrap.ps1 -Deps`).

## Python API

```python
from satk.imagegen.client import ImageClient
from satk.imagegen.keying import key_image, shrink, validate_image, load_image

master, steps = key_image(load_image("raw.png"))
icon, _ = shrink(master, 64)
assert validate_image(icon)["ok"]
client = ImageClient.from_env()          # NO_API_KEY when the variable is absent
attempt = client.generate("prompt")      # attempt.ok / attempt.code / attempt.image
```
