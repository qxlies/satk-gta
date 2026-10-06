"""Operations ``satk kb mta`` and ``satk kb native``: scripting API references in the knowledge base.

Imported by :mod:`satk.kb.ops` (only ``satk.<pkg>.ops`` modules are discovered). Both are ``mcp=False``:
MCP clients reach them through ``satk_ops``/``satk_op``. Module-level imports are stdlib-only.
"""

from __future__ import annotations

from typing import Literal

from ..core.envelope import clamp_limit
from ..core.registry import op

__all__ = ["kb_mta", "kb_native"]


@op("kb.mta", group="re", mcp=False,
    summary="MTA:SA Lua API from the local MTA source: a function gives signature, side (client/server/shared), "
            "OOP names, accepted enum strings and the C++ file:line; also Class, Class:method, events (--event), "
            "enums; words give a table; no query gives counts.",
    summary_ru="Lua API MTA:SA из исходников: сигнатура, сторона, OOP, допустимые строки enum, file:line; классы, "
               "события, слова; без запроса — счётчики",
    examples=("satk kb mta engineRequestModel", 'satk kb mta "vehicle handling"', "satk kb mta Vehicle:setHandling",
              "satk kb mta --event onClientElementStreamIn", "satk kb mta"))
def kb_mta(query: str | None, event: bool = False, side: Literal["client", "server", "shared"] | None = None,
           limit: int = 20) -> dict:
    """MTA Lua API lookup.

    Args:
        query: function (engineRequestModel), Class:method or Class.var, class (Vehicle), event, enum (client-model-type) or words; empty = counts.
        event: look up events only (onClientRender); without a query lists events.
        side: only client, server or shared entries of a word search.
        limit: rows of a table.
    """
    from .scriptapi_query import mta

    return mta(query, event=event, side=side, limit=clamp_limit(limit))


@op("kb.native", group="re", mcp=False,
    summary="SA-MP/open.mp Pawn natives, callbacks (forward) and #define constants from local include files "
            "(kb.pawn_include): a name gives the declaration with params (tags, defaults, arrays, refs) and "
            "include file:line; words give a table; typos give suggestions.",
    summary_ru="Нативы SA-MP/open.mp, колбэки и константы из .inc: параметры с тегами и значениями по умолчанию, "
               "файл и строка; поиск по словам, подсказки при опечатках",
    examples=("satk kb native SetObjectMaterial", 'satk kb native "object material"', "satk kb native OnPlayerConnect",
              "satk kb native --inc a_objects"))
def kb_native(query: str | None, kind: Literal["native", "callback", "const"] | None = None, inc: str | None = None,
              limit: int = 20) -> dict:
    """Pawn API lookup.

    Args:
        query: native, callback or constant name, or words; empty = include files with counts.
        kind: native | callback | const.
        inc: only one include file (a_objects, streamer, ...).
        limit: rows of a table.
    """
    from .scriptapi_query import native

    return native(query, kind=kind, inc=inc, limit=clamp_limit(limit))
