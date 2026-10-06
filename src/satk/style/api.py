"""Python API of satk.style (contract K2 and the check/anatomy entry points).

* ``profile(cls_or_sid, tier) -> {metric: {p10, p50, p90, n, peer_set, lo, hi, status}}``;
* ``band(metric, cls, tier) -> (lo, hi) | None``;
* ``describe(cls_or_sid, tier)`` - the ``satk style profile`` answer;
* :func:`check` - ``asset.check`` results of a ``.dff`` path, a mod folder or a SID;
* :func:`anatomy` - ``asset.anatomy`` of one model.

``cls_or_sid`` is a class (``car.sedan``, ``sedan``, ``prop@1-2m``, ``ped``) or a model (``model:426``,
``premier``, ``426``). ``tier`` is ``vanilla`` or ``sa_plus`` (default ``sa_plus``, the tier of new assets;
its numbers are a proposal). The style cache is built on first use (``satk style build``).

Example::

    from satk.style import api
    api.profile("car.sedan", "vanilla")["shade.normal_bend"]["p50"]     # 9.98
    api.band("veh.hd_tris", "car", "sa_plus")                           # (3000, 4500)
    r = api.check("mymod/premier.dff", tier="sa_plus")[0]
    r["verdict"], [row for row in r["rows"] if row[6] in ("error", "low", "high")]
"""

from __future__ import annotations

from .profile import band, describe, profile

__all__ = ["profile", "band", "describe", "check", "anatomy"]


def check(target: str, *, like: str | None = None, cls: str | None = None, tier: str | None = None,
          profile_name: str = "vanilla", all_rows: bool = False) -> list[dict]:
    """``asset.check`` of every model of ``target`` (one dict per DFF; see :func:`satk.style.check.check_subject`)."""
    from . import cache, check as CK, subject

    c = cache.load(profile_name)
    return [CK.check_subject(s, c, cls=cls, like=like, tier=tier, all_rows=all_rows, profile=profile_name)
            for s in subject.load(target, profile_name)]


def anatomy(target: str, *, profile_name: str = "vanilla") -> dict:
    """``asset.anatomy`` of one model (a ``.dff`` path or a SID)."""
    from . import anatomy as A, subject

    subs = subject.load(target, profile_name)
    return A.anatomy(subs[0])
