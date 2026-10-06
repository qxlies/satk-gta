"""Operations of satk.addon: add-on models for Mod Loader and data-file records (lane addon).

CLI: ``satk mod add``, ``satk data get|patch|explain``. All CLI-only (``mcp=False``); MCP reaches them through
``satk_op``. Module-level imports are stdlib-only.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import op

__all__ = ["mod_add", "data_get", "data_patch", "data_explain"]


@op("mod.add", mcp=False, group="asset",
    summary="Make a Mod Loader folder that adds a NEW vehicle/ped/weapon/object like a donor: copies DFF/TXD(/COL), "
            "writes the donor's data lines with a free id and new names (IDE, handling, carcols, carmods, cargrp, "
            "weapon.dat, object.dat, FXT name); checks ids and names. Never writes the game.",
    summary_ru="Папка Mod Loader с НОВЫМ транспортом/педом/оружием/объектом по образцу донора: DFF/TXD(/COL), строки "
               "данных донора со свободным ID и новыми именами (IDE, handling, carcols, carmods, cargrp, weapon.dat, "
               "object.dat, имя FXT); проверки ID и имён. В игру не пишет.",
    examples=("satk mod add vehicle --dff mycar.dff --txd mycar.txd --like 411 --name mycar --game-name \"My Car\"",
              "satk mod add ped --dff guy.dff --txd guy.txd --like male01 --name myguy --id 20001",
              "satk mod add object --dff box.dff --col box.col --like 1220 --name mybox --dry-run",
              "satk mod add object --dff bin.dff --lod-dff lodbin.dff --col bin.col --like 1300 --name mybin "
              "--place 2495,-1687,13"))
def mod_add(kind: Literal["vehicle", "ped", "weapon", "object"], dff: str | None = None, txd: str | None = None,
            col: str | None = None, like: str | None = None, name: str | None = None, id: str = "auto",  # noqa: A002
            game_name: str | None = None, handling: Literal["own", "donor"] = "own",
            cargrp: list[str] | None = None, weapon_type: str | None = None, profile: str = "installed",
            out: str | None = None, force: bool = False, dry_run: bool = False, lod_dff: str | None = None,
            lod_txd: str | None = None, lod_name: str | None = None, place: list[float] | None = None) -> dict:
    """Create an add-on folder for Mod Loader under work/out/addon/<name>/.

    Args:
        kind: vehicle, ped, weapon or object.
        dff: the model (.dff).
        txd: its textures (.txd); objects may omit it to use the donor's TXD.
        col: collision (.col) for objects and weapons; a single model inside is renamed to the new name.
        like: donor model whose data lines are copied: model:411, 411 or a name (infernus).
        name: new model name (letters, digits, _; at most 19 characters); also the TXD, file and handling name.
        id: model id: auto (first free id of the kind, idmgr) or a number.
        game_name: text shown in game for a vehicle (FXT entry; default: the name).
        handling: own = copy the donor's handling lines under a new handling id; donor = share the donor's.
        cargrp: add the vehicle to car groups of cargrp.dat: donor (the donor's groups), group numbers (0-based) or
            labels from the file's comments (POPCYCLE_GROUP_WORKERS, workers).
        weapon_type: weapon.dat type that gets the new model: the donor's (default), none, or a NEW type name
            (needs fastman92 LA's weapon type loader).
        profile: game whose data, ids and names are checked (installed = the user's game).
        out: output folder name under work/out/addon (default: the name).
        force: replace an existing output folder.
        dry_run: check and show the lines, write nothing.
        lod_dff: objects: the LOD model (.dff); it gets its own IDE line (draw 800, next free id) next to the object's.
        lod_txd: its textures (.txd); omitted = the LOD uses the object's TXD.
        lod_name: LOD model name (default: the stem of --lod-dff, e.g. lodmybin).
        place: objects: X,Y,Z; also writes a data/maps/<name>.ipl whose HD placement holds the index of the LOD one.
    """
    from .add import add

    return add(kind, dff=dff, txd=txd, col=col, like=like, name=name, id_=id, game_name=game_name,
               handling=handling, cargrp=cargrp, weapon_type=weapon_type, profile=profile, out=out, force=force,
               dry_run=dry_run, lod_dff=lod_dff, lod_txd=lod_txd, lod_name=lod_name, place=place)


@op("data.get", mcp=False, group="formats",
    summary="One record of a game data file with every field, value and unit: handling (vehicle or handling id), "
            "carcols (colour sets with RGB), peds (peds.ide + pedstats) or weapon (weapon.dat lines per skill); "
            "flags decoded. --field narrows it and adds meanings.",
    summary_ru="Одна запись файла данных игры со всеми полями, значениями и единицами: handling, carcols, peds или "
               "weapon (по уровням навыка); флаги расшифрованы. --field сужает и добавляет пояснения.",
    examples=("satk data get handling infernus", "satk data get handling model:522 --field fMass fTractionMultiplier",
              "satk data get weapon PISTOL", "satk data get carcols 411", "satk data get peds male01"))
def data_get(file: Literal["handling", "carcols", "peds", "weapon"], key: str, field: list[str] | None = None,
             profile: str = "vanilla", limit: int = 60, cursor: str | None = None) -> dict:
    """Show one data record.

    Args:
        file: handling, carcols, peds or weapon.
        key: handling: model:411, 411, infernus or a handling id (INFERNUS); carcols/peds: a model; weapon: a type
            (PISTOL), weapon:22, a weapon model (colt45, model:346) or aim:<anim group>.
        field: only these fields (handling-editor names like fMass, satk keys like mass, MTA names); adds meanings.
        profile: game whose data files are read.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .data import get

    return get(file, key, field=field, profile=profile, limit=limit, cursor=cursor)


@op("data.patch", mcp=False, group="formats",
    summary="Change fields of a handling.cfg or weapon.dat record (fMass=1500 ...): --target modloader = a Mod Loader "
            "folder with only the changed lines, mta = a Lua setModelHandling/setWeaponProperty snippet, full = a "
            "patched copy of the file; formatting of untouched text kept. Writes work/out/addon/.",
    summary_ru="Изменить поля записи handling.cfg или weapon.dat (fMass=1500 ...): modloader = папка с одними "
               "изменёнными строками, mta = Lua (setModelHandling/setWeaponProperty), full = исправленная копия файла.",
    examples=("satk data patch handling infernus fMass=1500 fTractionMultiplier=0.8",
              "satk data patch handling 411 maxVelocity=260 --target mta",
              "satk data patch weapon PISTOL damage=30 --skill pro --target full"))
def data_patch(file: Literal["handling", "weapon"], key: str, set: list[str],  # noqa: A002
               target: Literal["modloader", "mta", "full"] = "modloader", skill: str = "all",
               profile: str = "vanilla", name: str | None = None, dry_run: bool = False) -> dict:
    """Patch one data record.

    Args:
        file: handling or weapon.
        key: handling: model:411, 411, infernus or a handling id; weapon: a type (PISTOL), weapon:22, a weapon model
            or aim:<anim group>.
        set: changes field=value (fMass=1500, boat.fThrustY=0.7, damage=30); names as in 'satk data explain'.
        target: modloader (readme lines Mod Loader merges), mta (Lua snippet) or full (patched copy of the file).
        skill: weapon lines to change: all, poor, std, pro, cop (or 0-3).
        profile: game whose data file is patched.
        name: output folder name under work/out/addon (default <key>-<file>-<target>).
        dry_run: show the old and new lines, write nothing.
    """
    from .data import patch

    return patch(file, key, set, target=target, profile=profile, name=name, skill=skill, dry_run=dry_run)


@op("data.explain", mcp=False, group="formats",
    summary="What a field of handling.cfg, weapon.dat or peds.ide/pedstats.dat means: unit, range, type, column "
            "letter, handling-editor and MTA names, flag bits. Without a field: every field as a table.",
    summary_ru="Что значит поле handling.cfg, weapon.dat или peds.ide/pedstats.dat: единицы, диапазон, тип, буква "
               "столбца, имена редакторов и MTA, биты флагов. Без поля - таблица всех полей.",
    examples=("satk data explain handling fTractionBias", "satk data explain handling modelFlags",
              "satk data explain weapon flags", "satk data explain handling IS_LOW", "satk data explain peds"))
def data_explain(file: Literal["handling", "weapon", "peds"], field: str | None, limit: int = 20,
                 cursor: str | None = None) -> dict:
    """Explain data-file fields.

    Args:
        file: handling, weapon or peds.
        field: field name (fMass, mass, tractionMultiplier, boat.fThrustY, damage) or a flag bit name (IS_LOW).
        limit: rows per page when listing every field (max 500).
        cursor: value of `next` from the previous page.
    """
    from .data import explain

    return explain(file, field, limit=limit, cursor=cursor)
