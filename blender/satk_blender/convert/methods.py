# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly loaded K3 conversion methods. The normal studio journal and checkpoints apply."""

from __future__ import annotations

import sys

from satk.core.errors import SatkError
from satk.studio.core import methods_from_module

from .assemble import assemble_method
from .bake import bake_method
from .geometry import clean_method, import_method, normalize_method, reduce_method

METHODS = {"convert.import": import_method, "convert.normalize": normalize_method, "convert.clean": clean_method,
           "convert.reduce": reduce_method, "convert.bake": bake_method, "convert.assemble": assemble_method}


def install(ctx) -> list[str]:
    """Load this plug-in into the K3 method table once, without replacing any other plug-in's methods."""
    wanted, errors = {}, []
    methods_from_module(sys.modules[__name__], wanted, errors)
    for name, method in wanted.items():
        existing = ctx.methods.get(name)
        if existing is not None and existing.fn is not method.fn:
            raise SatkError("EXISTS", f"studio method {name} is already registered by another plug-in")
    ctx.methods.update(wanted)
    return sorted(wanted)
