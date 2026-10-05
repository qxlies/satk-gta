"""SID provider of the index (SPEC §3.2): ``model dff txd tex pix file col ide ipl inst item zone ifp anim``.

Registered from ``satk.index.ops`` (found by ``registry.discover()``). Every call goes through
:func:`satk.index.api.open_index`, so tests can swap in a :class:`~satk.index.api.FakeIndexDB`
with :func:`~satk.index.api.override_index` or ``SATK_INDEX_FAKE=1``.

Profile selection: the ``profile`` argument wins; a SID's ``@layer`` is passed through to the
index, which resolves the non-winning version inside that profile.
"""

from __future__ import annotations

from ..core.ids import Sid, SidProvider, providers, register_provider
from . import api

__all__ = ["IndexProvider", "PROVIDER", "register"]


class IndexProvider:
    """Adapter ``SidProvider`` -> ``IndexDB`` (one shared instance: :data:`PROVIDER`)."""

    kinds: tuple[str, ...] = api.INDEX_KINDS

    def get(self, sid: Sid, fields: list[str] | None, profile: str) -> dict:
        return api.open_index(profile or "vanilla").get(str(sid), fields)

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        return api.open_index(profile or "vanilla").find(q, kind, limit, cursor)

    def refs(self, sid: Sid, rel: str | None, limit: int, cursor: str | None, profile: str) -> dict:
        return api.open_index(profile or "vanilla").refs(str(sid), rel, limit, cursor)

    def __repr__(self) -> str:
        return "IndexProvider()"


PROVIDER = IndexProvider()
assert isinstance(PROVIDER, SidProvider)


def register() -> None:
    """Register :data:`PROVIDER` for all index kinds (idempotent).

    An older ``IndexProvider`` left behind by a module reload is replaced; a provider of
    another type for an index kind is a conflict (``ValueError``).
    """
    current = providers()
    stale = any(type(current.get(k)).__name__ == "IndexProvider" and current.get(k) is not PROVIDER
                for k in PROVIDER.kinds)
    register_provider(PROVIDER, replace=stale)
