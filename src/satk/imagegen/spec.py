"""Icon-set specs: TOML files in ``specs/`` (or any path) with the style prompt, icons and status dots."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from .client import ASPECTS, DEFAULT_MODEL, SIZES
from .keying import parse_color

__all__ = ["SPEC_DIR", "Icon", "Status", "Spec", "load_spec", "list_specs", "prompt_for"]

SPEC_DIR = Path(__file__).resolve().parent / "specs"
MAX_CANDIDATES = 8


@dataclass(frozen=True)
class Icon:
    id: str                 # file stem, e.g. "preset_classic"
    name: str               # short name, e.g. "classic"
    subject: str
    accent: str
    sizes: tuple[int, ...]
    kind: str = "hills"     # placeholder drawing: hills | lock
    hills: int = 1
    sight: float = 0.5
    skyline: bool = False

    def files(self) -> list[str]:
        return [f"{self.id}_{s}.png" for s in self.sizes]


@dataclass(frozen=True)
class Status:
    id: str
    name: str
    color: str
    size: int = 12

    def file(self) -> str:
        return f"{self.id}_{self.size}.png"


@dataclass(frozen=True)
class Spec:
    name: str
    out: str
    model: str
    aspect: str
    size: str
    candidates: int
    key_color: str
    badge_color: str
    ring_color: str
    emblem_color: str
    prompt_id: str
    style_prompt: str
    icons: tuple[Icon, ...]
    status: tuple[Status, ...] = ()
    source: str = ""

    def icon(self, name: str) -> Icon:
        for i in self.icons:
            if name in (i.id, i.name):
                return i
        raise SatkError("NOT_FOUND", f"no icon {name!r} in spec {self.name}",
                        did_you_mean=[i.name for i in self.icons])

    def shipped_files(self) -> list[str]:
        """Every file of a complete set, in spec order."""
        out = [f for i in self.icons for f in i.files()]
        return out + [s.file() for s in self.status]


def list_specs() -> list[str]:
    return sorted(p.stem for p in SPEC_DIR.glob("*.toml"))


def prompt_for(spec: Spec, icon: Icon) -> str:
    """The style prompt with the icon's subject and accent filled in."""
    return spec.style_prompt.format(subject=icon.subject, accent=icon.accent)


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise SatkError("BAD_PARAMS", f"{where}: missing '{key}'")
    return d[key]


def load_spec(spec: str | Path = "sae_v1") -> Spec:
    """Load a spec by packaged name (``sae_v1``) or by path to a ``.toml`` file; validates it."""
    s = str(spec)
    p = Path(s)
    if not (p.suffix.lower() == ".toml" or p.is_absolute() or "/" in s or "\\" in s):
        p = SPEC_DIR / f"{s.replace('-', '_')}.toml"
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no spec {s!r}", hint="satk imagegen icon-set --dry-run",
                        did_you_mean=list_specs())
    try:
        doc = tomllib.loads(p.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read spec {p.name}: {e}") from None
    meta = doc.get("spec") or {}
    prompt = str(_req(meta, "style_prompt", "[spec]")).strip()
    if "{subject}" not in prompt or "{accent}" not in prompt:
        raise SatkError("BAD_PARAMS", "[spec] style_prompt must contain {subject} and {accent}")
    icons = []
    seen: set[str] = set()
    for i, d in enumerate(doc.get("icon") or []):
        w = f"[[icon]] #{i + 1}"
        ic = Icon(id=str(_req(d, "id", w)), name=str(_req(d, "name", w)), subject=str(_req(d, "subject", w)),
                  accent=str(_req(d, "accent", w)), sizes=tuple(int(x) for x in _req(d, "sizes", w)),
                  kind=str(d.get("kind", "hills")), hills=int(d.get("hills", 1)), sight=float(d.get("sight", 0.5)),
                  skyline=bool(d.get("skyline", False)))
        parse_color(ic.accent)
        if ic.kind not in ("hills", "lock"):
            raise SatkError("BAD_PARAMS", f"{w}: kind must be hills or lock, got {ic.kind!r}")
        if not ic.sizes or any(x < 8 for x in ic.sizes):
            raise SatkError("BAD_PARAMS", f"{w}: sizes must be a list of integers >= 8")
        if ic.id in seen:
            raise SatkError("BAD_PARAMS", f"{w}: duplicate id {ic.id}")
        seen.add(ic.id)
        icons.append(ic)
    if not icons:
        raise SatkError("BAD_PARAMS", "the spec has no [[icon]] entries")
    status = []
    for i, d in enumerate(doc.get("status") or []):
        w = f"[[status]] #{i + 1}"
        st = Status(id=str(_req(d, "id", w)), name=str(_req(d, "name", w)), color=str(_req(d, "color", w)),
                    size=int(d.get("size", 12)))
        parse_color(st.color)
        if st.id in seen:
            raise SatkError("BAD_PARAMS", f"{w}: duplicate id {st.id}")
        seen.add(st.id)
        status.append(st)
    aspect, size = str(meta.get("aspect", "1:1")), str(meta.get("size", "1K"))
    if aspect not in ASPECTS or size not in SIZES:
        raise SatkError("BAD_PARAMS", f"[spec] aspect {aspect!r} / size {size!r} not allowed",
                        hint=f"aspect: {', '.join(ASPECTS)}; size: {', '.join(SIZES)}")
    cand = int(meta.get("candidates", 3))
    if not 1 <= cand <= MAX_CANDIDATES:
        raise SatkError("BAD_PARAMS", f"[spec] candidates must be 1..{MAX_CANDIDATES}")
    name = str(meta.get("name", p.stem))
    for c in ("key_color", "badge_color", "ring_color", "emblem_color"):
        parse_color(str(meta.get(c, "#000000")))
    return Spec(name=name, out=str(meta.get("out", name.replace("_", "-"))), model=str(meta.get("model", DEFAULT_MODEL)),
                aspect=aspect, size=size, candidates=cand, key_color=str(meta.get("key_color", "#FF00FF")),
                badge_color=str(meta.get("badge_color", "#1F2428")), ring_color=str(meta.get("ring_color", "#3A4148")),
                emblem_color=str(meta.get("emblem_color", "#E8EAED")), prompt_id=str(meta.get("prompt_id", name)),
                style_prompt=prompt, icons=tuple(icons), status=tuple(status), source=p.name)
