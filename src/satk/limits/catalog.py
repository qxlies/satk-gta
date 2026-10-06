"""Capacity identities and provenance, sharing the existing KB and mod-check facts."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.resources import read_json


@dataclass(frozen=True)
class Limit:
    key: str
    title: str
    stock: int | None
    unit: str
    source: dict
    f92: tuple[str, str] | None
    ola: str | None
    advice: str
    crashes: tuple[int, ...]
    ola_max: int | None = None


def load() -> list[Limit]:
    from ..kb.facts import all_facts
    from ..modinspect.check import STORES
    from ..txdopt.budget import stream_limit

    facts = {f["key"]: f for f in all_facts()}
    out = []
    catalog = read_json("limits/catalog.json")
    for row in catalog["limits"]:
        stock = row.get("stock")
        source = {"ref": row.get("ref", "")}
        if "fact" in row:
            fact = facts[row["fact"]]
            source.update(fact=fact["key"], refs=fact.get("refs", []),
                          addresses=[f"0x{a:08X}" for a in fact.get("addrs", [])])
            if "check" in row:
                check = next(c for c in fact["checks"] if c[0] in ("limit", "const") and c[1] == row["check"])
                stock = int(check[2])
                source["check"] = list(check)
            if "operand" in row:
                va = int(row["operand"], 16)
                check = next(c for c in fact["checks"] if c[0] in ("u8", "u16", "u32") and c[1] == va)
                stock = int(check[2])
                source["operand"] = {"address": f"0x{va:08X}", "type": check[0], "expected": stock}
        if "store" in row:
            stock = STORES[row["store"]][0]
            source["capacity_provider"] = f"satk.modinspect.check.STORES[{row['store']}]"
        if row["key"] == "streaming.memory":
            stock, _ = stream_limit()
        if row["key"] == "cargrp.members":
            from ..addon.add import CARGRP_MAX

            stock = CARGRP_MAX
            source["capacity_provider"] = "satk.addon.add.CARGRP_MAX"
        if row["key"] == "timecyc.entries":
            from ..formats.timecyc import HOURS, WEATHERS

            stock = len(WEATHERS) * len(HOURS)
            source["capacity_provider"] = "satk.formats.timecyc.WEATHERS * HOURS"
        if "address" in row:
            source["addresses"] = [row["address"]]
        source["adjuster_refs"] = {k: v for k, v in catalog["adjuster_references"].items()
                                   if row.get("f92" if k == "fastman92" else k)}
        out.append(Limit(row["key"], row["title"], stock, row.get("unit", "slots"), source,
                         tuple(row["f92"]) if row.get("f92") else None, row.get("ola"),
                         row.get("advice", "Check this option and its prerequisites in your adjuster version."
                                 if row.get("f92") or row.get("ola") else
                                 "No verified INI setting in this catalog; reduce content or check the adjuster's documentation."),
                         tuple(row.get("crashes", [])), row.get("ola_max")))
    return out
