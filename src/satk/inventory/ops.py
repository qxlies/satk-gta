"""Operations of satk.inventory: the task list of an asset (contract K6).

``asset.inventory`` (report: built, missing, unattached, rejected; or the plan as a checklist), ``inventory.starter``
(the starter list of a kind and detail level, written into a project), ``inventory.validate`` (readable errors)
and ``inventory.mark`` (a reviewer rejects or accepts an item). All ``mcp=False``; stdlib imports only.
"""

from __future__ import annotations

from typing import Any, Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op

Detail = Literal["simple", "standard", "hero"]
StatusFilter = Literal["open", "all", "built", "missing", "unattached", "rejected"]
_COLS = ["id", "status", "name", "stage", "region", "note"]


def _note(r: dict) -> str:
    bits = []
    if r.get("reason"):
        bits.append(r["reason"])
    if r.get("note"):
        bits.append(r["note"])
    if r.get("optional"):
        bits.append("optional")
    if r.get("objects") and r["status"] == "built":
        bits.append("in " + ", ".join(r["objects"][:3]))
    return "; ".join(bits)[:220]


def _plan_rows(inv: dict) -> list[dict]:
    out = []
    for it in inv.get("items") or []:
        if not isinstance(it, dict):
            continue
        out.append({"id": it.get("id"), "status": "planned", "name": it.get("name", ""), "stage": it.get("stage"),
                    "region": it.get("region"), "required": it.get("required", True) is not False,
                    "construction": it.get("construction", ""), "attaches_to": it.get("attaches_to"),
                    "count": it.get("count"), "category": it.get("category")})
    return out


def _checklist(title: str, rows: list[dict], items_by_id: dict[str, dict], kind: str | None = None) -> str:
    lines = [f"# {title}", ""]
    if kind:
        from .starter import views

        vs = views(kind)
        used = sorted({str(r.get("region")) for r in rows if vs.get(str(r.get("region")))})
        if used:
            lines += ["Close-ups (blender preview --regions): " + "; ".join(f"{r} = {', '.join(vs[r])}" for r in used),
                      ""]
    for stage in ("G1", "G2", "G3", "G4"):
        rs = [r for r in rows if r.get("stage") == stage]
        if not rs:
            continue
        lines.append(f"## {stage} {dict(G1='form', G2='compose', G3='detail', G4='surface')[stage]}")
        for r in rs:
            it = items_by_id.get(r["id"], {})
            box = "x" if r["status"] == "built" else " "
            att = it.get("attaches_to")
            att_l = att if isinstance(att, list) else [att] if att else []
            att_s = (" Attaches to " + " or ".join(f"{q} {items_by_id.get(q, {}).get('name', '')}".strip()
                                                   for q in att_l) + ".") if att_l else ""
            cnt = f" {it['count']} pieces." if it.get("count") else ""
            opt = " (optional)" if it.get("required") is False else ""
            st = "" if r["status"] in ("built", "planned") else f" **{r['status'].upper()}**: {r.get('reason', '')}"
            lines.append(f"- [{box}] {r['id']} {it.get('name', r.get('name', ''))}{opt} ({it.get('region')}, "
                         f"{it.get('category')}): {it.get('construction', '')}.{att_s}{cnt}{st}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@op("asset.inventory",
    summary="Inventory of an asset project (design/inventory.json): every item built, missing, unattached or rejected, "
            "measured in the live Blender session (or the newest checkpoint) or from an exported DFF and its sidecar; "
            "--plan lists the items as a checklist without measuring.",
    summary_ru="Инвентарь проекта ассета: какие детали построены, отсутствуют, не прикреплены или отклонены (по живой "
               "сессии Blender или по экспортированному DFF); --plan даёт чек-лист без измерений.",
    mcp=False, group="asset",
    examples=("satk asset inventory mycar", "satk asset inventory mycar --status all",
              "satk asset inventory mycar --plan --stage G3 --md",
              "satk asset inventory mycar --dff <work>/out/kit/premier/modloader/premier/premier.dff"))
def asset_inventory(dir: str, session: str | None = None, dff: str | None = None, blend: str | None = None,
                    status: StatusFilter = "open", stage: Literal["G1", "G2", "G3", "G4"] | None = None,
                    plan: bool = False, md: bool = False, strict: bool = False, limit: int = 100) -> dict:
    """Which inventory items are built (contract K6).

    An item is built when geometry tagged with its id (scene.tag) exists, has its pieces and touches what it attaches
    to; missing, unattached and rejected items name what to fix. complete = every required item built.

    Args:
        dir: asset project name or folder (its design/inventory.json).
        session: Blender session to measure (default: the project's session, else its newest checkpoint).
        dff: an exported DFF: verdicts from its <stem>.inventory.json sidecar, checked against the DFF.
        blend: a .blend to measure in a one-shot Blender run.
        status: rows to list: open (not built, the default), all, or one status.
        stage: only the items of one gate (G1 form, G2 compose, G3 detail, G4 surface).
        plan: list the inventory as planned (no measuring, no Blender).
        md: a Markdown checklist (with plan: the stage prompt of a builder).
        strict: starter items dropped without a waiver make the inventory invalid.
        limit: rows to list.
    """
    from . import schema as SC
    from .api import report

    if plan:
        p, inv = SC.load(dir)
        rows = _plan_rows(inv)
        if stage:
            rows = [r for r in rows if r["stage"] == stage]
        by = {it.get("id"): it for it in inv.get("items") or [] if isinstance(it, dict)}
        val = SC.validate(inv, strict=strict)
        if md:
            title = f"Inventory {p.parent.parent.name} ({inv.get('kind')}, {inv.get('detail')})" + \
                    (f": {stage} items" if stage else "")
            return obj(None, topic="asset.inventory", text=_checklist(title, rows, by, inv.get("kind")),
                       items=len(rows), problems=val["errors"][:10])
        env = table(["id", "name", "stage", "region", "category", "attaches_to", "construction"],
                    [[r["id"], r["name"], r["stage"], r["region"], r["category"],
                      ",".join(r["attaches_to"]) if isinstance(r["attaches_to"], list) else (r["attaches_to"] or ""),
                      (r["construction"] or "")[:160]] for r in rows][: max(1, limit)], total=len(rows),
                    warn=[f"INVENTORY: {w}" for w in val["warnings"][:6]])
        env.update(kind=inv.get("kind"), detail=inv.get("detail"))
        if val["errors"]:
            env["problems"] = val["errors"][:10]
        return env
    rep = report(dir, dff=dff, session=session, blend=blend, strict=strict)
    rows = rep["items"]
    if stage:
        rows = [r for r in rows if r.get("stage") == stage]
    if md:
        p, inv = SC.load(dir) if rep.get("inventory") else (None, {"items": []})
        by = {it.get("id"): it for it in inv.get("items") or [] if isinstance(it, dict)}
        c = rep["counts"]
        title = (f"Inventory ({rep.get('kind')}, {rep.get('detail')}): {c['built']}/{c['total']} built, "
                 f"{c['missing']} missing, {c['unattached']} unattached, {c['rejected']} rejected"
                 f"{'; COMPLETE' if rep['complete'] else ''}")
        return obj(None, topic="asset.inventory", text=_checklist(title, rows, by, rep.get("kind")),
                   complete=rep["complete"], counts=rep["counts"], gates=rep["gates"], warn=rep.get("warn"))
    if status == "open":
        shown = [r for r in rows if r["status"] != "built"]
    elif status == "all":
        shown = rows
    else:
        shown = [r for r in rows if r["status"] == status]
    order = {"rejected": 0, "unattached": 1, "missing": 2, "built": 3}
    shown = sorted(shown, key=lambda r: (order.get(r["status"], 9), not r["required"], r["id"]))
    env = table(_COLS, [[r["id"], r["status"], r["name"], r["stage"], r["region"], _note(r)]
                        for r in shown][: max(1, limit)], total=len(shown), warn=rep.get("warn") or ())
    env.update(complete=rep["complete"], counts=rep["counts"], gates=rep["gates"], done_through=rep["done_through"],
               source=rep["source"], kind=rep.get("kind"), detail=rep.get("detail"))
    for k in ("inventory", "untagged", "problems"):
        if rep.get(k):
            env[k] = rep[k]
    if not rep["complete"]:
        env["hint"] = ("build the open items, tag them (satk blender call scene.tag --params "
                       "'{\"objects\":[\"<name>\"],\"item\":\"I05\"}'), then run this again")
    return env


@op("inventory.starter",
    summary="Starter inventory of an asset kind at a detail level (simple: what vanilla always has; standard: a full "
            "vanilla-class model; hero: richer than vanilla): list it, or write it as design/inventory.json of a "
            "project for the design stage to edit.",
    summary_ru="Стартовый инвентарь вида ассета для уровня детализации (simple/standard/hero): показать или записать в "
               "design/inventory.json проекта.",
    mcp=False, group="asset",
    examples=("satk inventory starter automobile --detail hero", "satk inventory starter all",
              "satk inventory starter building --project mybld",
              "satk inventory starter weapon --detail standard --project mygun --force"))
def inventory_starter(kind: str, detail: Detail = "hero", project: str | None = None,
                      force: bool = False, limit: int = 100) -> dict:
    """Starter list of a kind (data/kit/inventory/<kind>.json).

    Args:
        kind: asset kind (automobile, bike, boat, heli, plane, trailer, train, prop, building, interior_shell, weapon,
            ped, pickup, vehicle_upgrade, ...); 'all' lists the kinds with their item counts.
        detail: simple, standard or hero (default hero: richer than vanilla).
        project: write it as <project>/design/inventory.json.
        force: overwrite an existing inventory.
        limit: rows to list.
    """
    from . import schema as SC
    from . import starter as ST

    if kind == "all":
        rows = []
        for k in ST.kinds():
            s = ST.summary(k)
            rows.append([k, s["items"]["simple"], s["items"]["standard"], s["items"]["hero"], ", ".join(s["regions"])])
        return table(["kind", "simple", "standard", "hero", "regions"], rows)
    inv = ST.instantiate(kind, detail)
    if project:
        import json

        from ..core import paths
        from ..studio.project import project_dir

        pdir = project_dir(project)
        if not (pdir / "asset.json").is_file():
            raise SatkError("NOT_FOUND", f"no asset project {paths.jpath(pdir)}",
                            hint=f"satk asset init {pdir.name} --kind {kind}")
        pkind = str(json.loads((pdir / "asset.json").read_text(encoding="utf-8")).get("kind") or "")
        if pkind and pkind != inv["kind"]:
            raise SatkError("BAD_PARAMS", f"project {pdir.name} is a {pkind}, not a {inv['kind']}",
                            hint=f"satk inventory starter {pkind} --project {pdir.name}")
        f = pdir / SC.FILE
        if f.is_file() and not force:
            raise SatkError("EXISTS", f"{paths.jpath(f)} exists", hint="edit it, or --force to start from the starter "
                            "again")
        SC.save(pdir, inv)
        return obj(None, inventory=paths.jpath(f), kind=inv["kind"], detail=detail, items=len(inv["items"]),
                   next="edit the items to the real design (names, construction, added parts; waive with a reason), "
                        f"then satk inventory validate {pdir.name}")
    st = ST.load(kind)
    env = table(["id", "key", "name", "stage", "region", "category", "attaches_to"],
                [[i["id"], i["key"], i["name"], i["stage"], i["region"], i["category"],
                  ",".join(i["attaches_to"]) if isinstance(i["attaches_to"], list) else (i["attaches_to"] or "")]
                 for i in inv["items"]][: max(1, limit)], total=len(inv["items"]))
    env.update(kind=inv["kind"], detail=detail, regions=list(st.get("regions") or {}))
    return env


@op("inventory.validate",
    summary="Check an inventory (a project's design/inventory.json or a JSON file): fields, ids, attaches_to targets "
            "and cycles, categories and stages, regions of the kind, starter items dropped without a waiver.",
    summary_ru="Проверить инвентарь проекта или JSON-файл: поля, id, ссылки attaches_to и циклы, категории, этапы, "
               "регионы, пропущенные без причины пункты стартового списка.",
    mcp=False, group="asset",
    examples=("satk inventory validate mycar", "satk inventory validate design.json --strict"))
def inventory_validate(target: str, strict: bool = False) -> dict:
    """Validate an inventory with readable errors.

    Args:
        target: asset project name or folder, or a .json file.
        strict: starter items dropped without a waiver are errors (not warnings).
    """
    from pathlib import Path

    from ..core import paths
    from . import schema as SC

    f = Path(target)
    if f.suffix.lower() == ".json" and f.is_file():
        data = SC.load_file(f)
    else:
        f, data = SC.load(target)
    res = SC.validate(data, strict=strict)
    rows = [["error", e] for e in res["errors"]] + [["warning", w] for w in res["warnings"]]
    env = table(["level", "message"], rows[:200], total=len(rows))
    env.update(file=paths.jpath(f.absolute()), valid=not res["errors"], items=res["items"], required=res["required"],
               kind=data.get("kind"), detail=data.get("detail"))
    if res.get("dropped"):
        env["dropped"] = res["dropped"][:40]
    return env


@op("inventory.mark",
    summary="A reviewer rejects an inventory item with the reason (it reports 'rejected' until accepted, whatever its "
            "geometry) or accepts it again; the builder never clears a rejection itself.",
    summary_ru="Ревьюер отклоняет пункт инвентаря с причиной (он остаётся rejected до принятия) или принимает его.",
    mcp=False, group="asset",
    examples=("satk inventory mark mycar I23 --reject \"mirror head is a box; round it and put it on a stalk\"",
              "satk inventory mark mycar I23 --accept --by critic"))
def inventory_mark(project: str, item: str, reject: str | None = None, accept: bool = False,
                   by: str = "reviewer") -> dict:
    """Reject or accept one item.

    Args:
        project: asset project name or folder.
        item: item id (I23).
        reject: the reason: what is wrong and what to do.
        accept: clear a rejection.
        by: who marks it (critic, polisher, user).
    """
    from .api import mark

    res: dict[str, Any] = mark(project, item, reject=reject, accept=accept, by=by)
    return obj(None, **res)
