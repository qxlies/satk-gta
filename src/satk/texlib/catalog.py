"""Shipped node recipes and their measured, pixel-free finishing targets."""

from __future__ import annotations

import copy
import re

from ..core import resources
from ..core.errors import SatkError


def catalog() -> dict:
    return resources.read_json("texlib", "recipes.json")


def recipe(name: str) -> dict:
    key = name.strip().lower().replace("-", "_").replace(" ", "_")
    data = catalog()
    if key not in data["presets"]:
        raise SatkError("BAD_PARAMS", f"unknown texlib preset {name!r}",
                        hint="satk texlib list --no-previews", did_you_mean=sorted(data["presets"]))
    return {"name": key, **copy.deepcopy(data["presets"][key])}


COLOURS = {
    "black": (32, 32, 32), "white": (224, 224, 224), "grey": (128, 128, 128),
    "gray": (128, 128, 128), "red": (152, 72, 60), "brown": (128, 96, 64),
    "orange": (188, 120, 64), "yellow": (184, 168, 88), "green": (88, 120, 72),
    "blue": (80, 112, 152), "beige": (164, 148, 120), "rust": (144, 88, 60),
}


def colour(value: str | None) -> tuple[int, int, int] | None:
    """A named colour or #RRGGBB; shared by tinting and index colour search."""
    if value is None:
        return None
    text = value.strip().lower()
    if text in COLOURS:
        return COLOURS[text]
    if re.fullmatch(r"#?[0-9a-f]{6}", text):
        text = text.lstrip("#")
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    raise SatkError("BAD_PARAMS", f"invalid colour {value!r}",
                    hint="use #RRGGBB or " + ", ".join(sorted(COLOURS)))


def validate_size_seed(size: int, seed: int) -> None:
    if type(size) is not int or size not in (128, 256):
        raise SatkError("BAD_PARAMS", "texture size must be 128 or 256 pixels")
    if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
        raise SatkError("BAD_PARAMS", "seed must be an integer from 0 to 4294967295")
