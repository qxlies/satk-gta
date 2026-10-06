"""Operations of satk.worldfiles (CLI only, ``mcp=False``; agents reach them through ``satk_ops``/``satk_op``):

* ``gxt get|export|write|patch|keys`` -- GTA SA text (GXT v4): bit-exact writer, JSON/folder documents,
  Mod Loader/CLEO FXT overrides;
* ``fxt write`` -- FXT text files;
* ``zone list|check|add|remove|export|write`` -- ``info.zon``/``map.zon``;
* ``water list|add|remove|export|write`` -- ``water.dat``/``water1.dat`` (and MTA ``createWater`` Lua);
* ``timecyc get|patch|diff`` -- ``timecyc.dat`` by named fields;
* ``popcycle get|patch`` -- ``popcycle.dat``;
* ``radar export|build`` -- the 144 ``radarNN.txd`` tiles.

Game files are only read; outputs go to ``<work>/out/worldfiles/``. Module-level imports are stdlib/satk only.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import op

Charset = Literal["gta", "latin1", "cp1251"]

# --------------------------------------------------------------------------- GXT / FXT


@op("gxt.get", mcp=False, group="formats",
    summary="Read GTA SA text (GXT): texts of keys in every table, a text search, or the table list with counts. "
            "Key names come from a shipped list of stock keys (unknown ones show as 0x<hash>).",
    summary_ru="Тексты GXT: значения ключей во всех таблицах, поиск по тексту или список таблиц со счётчиками.",
    examples=("satk gxt get CRED001 FEM_OK", "satk gxt get --search \"grove street\"", "satk gxt get --gxt german"))
def gxt_get(key: list[str] | None, search: str | None = None, table_name: str | None = None,
            gxt: str = "american", charset: Charset = "gta", full: bool = False, profile: str = "vanilla",
            limit: int = 20) -> dict:
    """Show GXT texts.

    Args:
        key: key names (CRED001) or hashes (0x1A2B3C4D); every table is searched.
        search: case-insensitive text to find in the texts (or an exact key name).
        table_name: only this table (MAIN, mission tables like INTRO1).
        gxt: language of the profile (american, french, german, italian, spanish) or a .gxt path.
        charset: gta (game font code page, accents decoded), latin1 (raw bytes) or cp1251.
        full: whole texts (default: cut at 120 characters).
        profile: game whose text folder is read.
        limit: rows to show (max 500).
    """
    from .api import gxt_get as f

    return f(key, search, table_name, gxt, charset, profile, limit, full)


@op("gxt.export", mcp=False, group="formats",
    summary="Export a GXT to an editable JSON document (or a folder: gxt.json + one <TABLE>.txt per table with "
            "'KEY text' lines, UTF-8). Entries keep the file's string order, so writing it back is bit-exact.",
    summary_ru="GXT в редактируемый JSON (или папку: gxt.json + <TABLE>.txt со строками 'KEY текст'); порядок "
               "строк сохраняется, обратная запись бит-в-бит.",
    examples=("satk gxt export american --out american.json", "satk gxt export german --format dir --out de"))
def gxt_export(gxt: str | None, out: str | None = None, format: Literal["json", "dir"] = "json",  # noqa: A002
               charset: Charset = "gta", profile: str = "vanilla") -> dict:
    """Export a GXT.

    Args:
        gxt: language of the profile (american, default; french, german, italian, spanish) or a .gxt path.
        out: output file (json) or folder (dir) under <work>/out/worldfiles/ (or an absolute path).
        format: json (one file) or dir (gxt.json + <TABLE>.txt files).
        charset: how 8-bit texts are decoded: gta (font code page), latin1 (raw bytes), cp1251.
        profile: game whose text folder is read.
    """
    from .api import gxt_export as f

    return f(gxt, out, format, charset, profile)


@op("gxt.write", mcp=False, group="formats",
    summary="Write a GTA SA GXT (version 4) from a JSON document or a folder of <TABLE>.txt files: JAMCRC32 keys, "
            "sorted TKEY, aligned tables; verified by parsing it back. Output under <work>/out/worldfiles/.",
    summary_ru="Собрать GXT (версия 4) из JSON или папки <TABLE>.txt: ключи JAMCRC32, сортировка TKEY, "
               "выравнивание таблиц; проверка обратным чтением.",
    examples=("satk gxt write american.json --out american.gxt", "satk gxt write de --out german.gxt"))
def gxt_write(src: str, out: str | None = None, charset: Charset | None = None) -> dict:
    """Write a GXT.

    Args:
        src: JSON document (satk-gxt/1 or a plain {"KEY": "text"} object = one MAIN table) or a folder from
            'satk gxt export --format dir'.
        out: output .gxt under <work>/out/worldfiles/ (or an absolute path); default american.gxt.
        charset: override the document's charset for encoding the texts (gta, latin1, cp1251).
    """
    from .api import gxt_write as f

    return f(src, out, charset)


@op("gxt.patch", mcp=False, group="formats",
    summary="Change or add GXT texts (KEY=\"text\") for Mod Loader/CLEO: --target fxt writes an FXT whose keys "
            "override the GXT (no game file replaced), full writes a patched copy of the .gxt.",
    summary_ru="Изменить или добавить тексты GXT (KEY=\"текст\"): fxt = FXT поверх GXT (Mod Loader/CLEO), full = "
               "исправленная копия .gxt.",
    examples=("satk gxt patch american CRED001=\"My mod\" MYZONE=\"Downtown\"",
              "satk gxt patch american FEM_OK=\"Fine\" --target full --out mytext"))
def gxt_patch(gxt: str, set: list[str], target: Literal["fxt", "full"] = "fxt",  # noqa: A002
              table_name: str | None = None, out: str | None = None, charset: Charset = "gta",
              profile: str = "vanilla", dry_run: bool = False) -> dict:
    """Patch texts.

    Args:
        gxt: language of the profile (american, ...) or a .gxt path.
        set: changes KEY="text" (new keys are added; use ~n~ for a new line in game text).
        target: fxt (an FXT file Mod Loader and CLEO load over the GXT) or full (a whole patched .gxt).
        table_name: table for new keys with --target full (default MAIN; a new name adds a mission table).
        out: output folder name under <work>/out/worldfiles/.
        charset: encoding of the new texts: gta (accented letters of the SA fonts), latin1, cp1251.
        profile: game whose GXT is the base.
        dry_run: show the changes, write nothing.
    """
    from .api import gxt_patch as f

    return f(gxt, set, target, table_name, out, charset, profile, dry_run)


@op("gxt.keys", mcp=False, group="formats",
    summary="How many GXT keys have known names; --scan finds more in a (modded) game's scripts, cutscenes, data "
            "files and exe and caches them for later exports.",
    summary_ru="Сколько ключей GXT имеют известные имена; --scan ищет новые в скриптах, катсценах, данных и exe игры.",
    examples=("satk gxt keys", "satk gxt keys --scan --profile installed"))
def gxt_keys(gxt: str = "american", scan: bool = False, profile: str = "vanilla") -> dict:
    """Key-name coverage of a GXT.

    Args:
        gxt: language of the profile or a .gxt path.
        scan: search the profile's game files for key names and cache them (about 15 s).
        profile: game to scan and whose GXT is checked.
    """
    from .api import gxt_keys as f

    return f(gxt, scan, profile)


@op("fxt.write", mcp=False, group="formats",
    summary="Write an FXT text file for CLEO or Mod Loader from KEY=\"text\" items, 'KEY text' files or JSON "
            "({\"KEY\": \"text\"} or a GXT document table); texts encoded in the SA font code page.",
    summary_ru="FXT для CLEO/Mod Loader из пар KEY=\"текст\", файлов 'KEY текст' или JSON; кодировка шрифтов SA.",
    examples=("satk fxt write MYCAR=\"Super GT\" MYZONE=\"Old Town\" --out mymod", "satk fxt write texts.json"))
def fxt_write(item: list[str], out: str | None = None, charset: Charset = "gta", table_name: str | None = None) -> dict:
    """Write an FXT.

    Args:
        item: KEY="text" pairs and/or files (.json, .txt/.fxt with 'KEY text' lines in UTF-8); later ones win.
        out: output file name under <work>/out/worldfiles/ (.fxt added).
        charset: gta (SA font code page), latin1 or cp1251.
        table_name: table to take from a GXT JSON document (default MAIN).
    """
    from .api import fxt_write as f

    return f(item, out, charset, table_name)


# --------------------------------------------------------------------------- zones


@op("zone.list", mcp=False, group="formats",
    summary="Zones of info.zon/map.zon with labels and shown names; --at X,Y lists the zones holding a point "
            "and which name the game shows there (smallest zone wins).",
    summary_ru="Зоны info.zon/map.zon с метками и названиями; --at X,Y: зоны в точке и какое имя покажет игра.",
    examples=("satk zone list --at 2495,-1687", "satk zone list --name \"GAN*\"", "satk zone list --file map"))
def zone_list(file: str = "info", at: list[float] | None = None, name: str | None = None,
              profile: str = "vanilla", limit: int = 20, cursor: str | None = None) -> dict:
    """List zones.

    Args:
        file: info, map or a .zon path.
        at: X,Y or X,Y,Z: zones containing the point, smallest first.
        name: glob on zone names or labels (GAN*).
        profile: game whose zone files are read.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .api import zone_list as f

    return f(file, at, name, profile, limit, cursor)


@op("zone.check", mcp=False, group="formats",
    summary="Check a zone file against the engine: 7-character names/labels, types, int16 boxes, labels missing "
            "from the GXT, duplicates, partial overlaps, and the fixed pools (380 navigation zones, 39 map zones).",
    summary_ru="Проверка файла зон: имена до 7 символов, типы, int16, метки без текста в GXT, дубли, частичные "
               "перекрытия и лимиты (380 навигационных, 39 карт).",
    examples=("satk zone check", "satk zone check --file work/out/worldfiles/zone-mytown/data/info.zon"))
def zone_check(file: str = "info", profile: str = "vanilla", limit: int = 20) -> dict:
    """Check zones.

    Args:
        file: info, map or a .zon path.
        profile: game whose GXT and other zone file are used for the checks.
        limit: rows to show (max 500).
    """
    from .api import zone_check as f

    return f(file, profile, limit)


@op("zone.add", mcp=False, group="formats",
    summary="Add a zone to info.zon (or map.zon) for Mod Loader: checks names, overlaps and the zone pools; --text "
            "adds the shown name as an FXT entry. Writes <work>/out/worldfiles/<out>/data/info.zon (+ .fxt).",
    summary_ru="Добавить зону в info.zon (или map.zon) для Mod Loader: проверки имён, перекрытий и лимитов; --text "
               "добавляет название через FXT.",
    examples=("satk zone add MYTOWN --box 2400,-1720,0,2530,-1620,200 --text \"My Town\"",
              "satk zone add DOCKS2 --box 2600,-2600,2800,-2400 --label SFDWT --dry-run"))
def zone_add(name: str, box: list[float] | None = None, label: str | None = None, text: str | None = None,
             type: int | None = None, level: int | None = None, file: str = "info",  # noqa: A002
             profile: str = "vanilla", out: str | None = None, charset: Charset = "gta",
             dry_run: bool = False) -> dict:
    """Add a zone.

    Args:
        name: zone (info) name, 1..7 letters/digits/_; zones with one name share population and gang data.
        box: X0,Y0,Z0,X1,Y1,Z1 (or X0,Y0,X1,Y1 = from z -100 to 900).
        label: GXT key of the shown name (default: the name).
        text: shown name; written as an FXT entry for the label.
        type: 0 navigation (default for info.zon), 1 local navigation, 3 map (default for map.zon).
        level: 0 none, 1 LS, 2 SF, 3 LV (default 1 for info.zon, as all stock zones).
        file: info, map or a .zon path to extend.
        profile: game whose files are the base.
        out: output folder name under <work>/out/worldfiles/ (default zone-<name>).
        charset: encoding of --text (gta, latin1, cp1251).
        dry_run: show the checks, write nothing.
    """
    from .api import zone_add as f

    return f(name, box, label, text, type, level, file, profile, out, charset, dry_run)


@op("zone.remove", mcp=False, group="formats",
    summary="Remove zones (by name, glob or index number) from info.zon/map.zon; writes a Mod Loader copy of the file.",
    summary_ru="Удалить зоны (по имени, маске или номеру) из info.zon/map.zon; копия файла для Mod Loader.",
    examples=("satk zone remove MYTOWN --file work/out/worldfiles/zone-mytown/data/info.zon", "satk zone remove 12"))
def zone_remove(zone: list[str], file: str = "info", profile: str = "vanilla", out: str | None = None,
                dry_run: bool = False) -> dict:
    """Remove zones.

    Args:
        zone: names, globs (GAN*) or index numbers (from 'satk zone list').
        file: info, map or a .zon path.
        profile: game whose files are the base.
        out: output folder name under <work>/out/worldfiles/ (default zone-remove).
        dry_run: show what would go, write nothing.
    """
    from .api import zone_remove as f

    return f(zone, file, profile, out, dry_run)


@op("zone.export", mcp=False, group="formats",
    summary="Export info.zon/map.zon to a JSON document (satk-zon/1) for editing; 'satk zone write' turns it back "
            "into the same bytes.",
    summary_ru="info.zon/map.zon в JSON (satk-zon/1) для правки; 'satk zone write' возвращает те же байты.",
    examples=("satk zone export info --out info.zon.json",))
def zone_export(file: str | None, out: str | None = None, profile: str = "vanilla") -> dict:
    """Export zones.

    Args:
        file: info (default), map or a .zon path.
        out: output file under <work>/out/worldfiles/ (default <file>.zon.json).
        profile: game whose files are read.
    """
    from .api import zone_export as f

    return f(file, out, profile)


@op("zone.write", mcp=False, group="formats",
    summary="Write a .zon from a JSON document (satk-zon/1 or a list of zones) in the stock format; checks every "
            "zone against the engine rules first.",
    summary_ru="Собрать .zon из JSON (satk-zon/1 или список зон) в стоковом формате; сначала проверка зон.",
    examples=("satk zone write info.zon.json --out info.zon",))
def zone_write(src: str, out: str | None = None) -> dict:
    """Write a zone file.

    Args:
        src: JSON document.
        out: output file under <work>/out/worldfiles/ (default from the source name).
    """
    from .api import zone_write as f

    return f(src, out)


# --------------------------------------------------------------------------- water


@op("water.list", mcp=False, group="formats",
    summary="Water polygons of water.dat/water1.dat (bounds, z, flags), pool use (301 quads, 6 triangles, 1021 "
            "vertices) and engine issues; --at X,Y,R limits to an area.",
    summary_ru="Полигоны воды water.dat/water1.dat (границы, z, флаги), занятость лимитов и проблемы; --at X,Y,R.",
    examples=("satk water list --at 1000,-2000,300", "satk water list --file water1"))
def water_list(file: str = "water", at: list[float] | None = None, profile: str = "vanilla", limit: int = 20,
               cursor: str | None = None) -> dict:
    """List water.

    Args:
        file: water, water1 or a path.
        at: X,Y[,R]: polygons within R metres of the point.
        profile: game whose water file is read.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .api import water_list as f

    return f(file, at, profile, limit, cursor)


@op("water.add", mcp=False, group="formats",
    summary="Add rectangular water: --target modloader writes a full water.dat copy (warns: vanilla uses all 301 "
            "quads), mta writes createWater Lua (MTA has 512). Checks overlaps with existing water.",
    summary_ru="Добавить прямоугольную воду: modloader = копия water.dat (в ванили 301 квад занят), mta = Lua "
               "createWater; проверка перекрытий.",
    examples=("satk water add --rect 100,-200,300,0 --z 5 --target mta",
              "satk water add --rect 100,-200,300,0 120,0,300,50 --z 5 --shallow --dry-run"))
def water_add(rect: list[list[float]] | None = None, z: float = 0.0, shallow: bool = False, waves: list[float] | None = None,
              flow: list[float] | None = None, invisible: bool = False,
              target: Literal["modloader", "mta"] = "modloader", file: str = "water", profile: str = "vanilla",
              out: str | None = None, dry_run: bool = False) -> dict:
    """Add water quads.

    Args:
        rect: X0,Y0,X1,Y1 per quad (several separated by spaces).
        z: water level.
        shallow: shallow water (flag 2: limited depth).
        waves: BIG,SMALL wave heights (default 0,0; the sea uses up to 1).
        flow: X,Y flow speed (default 0,0).
        invisible: no surface drawn (flag 1 off).
        target: modloader (full water.dat copy) or mta (water.lua with createWater).
        file: water, water1 or a path to extend.
        profile: game whose water is the base.
        out: output folder name under <work>/out/worldfiles/ (default water-<target>).
        dry_run: show the checks, write nothing.
    """
    from .api import water_add as f

    return f(rect, z, shallow, waves, flow, invisible, target, file, profile, out, dry_run)


@op("water.remove", mcp=False, group="formats",
    summary="Remove water polygons by number (from 'satk water list') and write a Mod Loader copy of water.dat.",
    summary_ru="Удалить полигоны воды по номерам (из 'satk water list'); копия water.dat для Mod Loader.",
    examples=("satk water remove 12 13", "satk water remove 5 --file water1"))
def water_remove(idx: list[int], file: str = "water", profile: str = "vanilla", out: str | None = None,
                 dry_run: bool = False) -> dict:
    """Remove water.

    Args:
        idx: polygon numbers (0-based file order).
        file: water, water1 or a path.
        profile: game whose water is the base.
        out: output folder name under <work>/out/worldfiles/ (default water-remove).
        dry_run: show what would go, write nothing.
    """
    from .api import water_remove as f

    return f(idx, file, profile, out, dry_run)


@op("water.export", mcp=False, group="formats",
    summary="Export water.dat/water1.dat to a JSON document (satk-water/1); 'satk water write' gives the same bytes "
            "back.",
    summary_ru="water.dat/water1.dat в JSON (satk-water/1); 'satk water write' возвращает те же байты.",
    examples=("satk water export water --out water.json", "satk water export water1"))
def water_export(file: str | None, out: str | None = None, profile: str = "vanilla") -> dict:
    """Export water.

    Args:
        file: water (default), water1 or a path.
        out: output file under <work>/out/worldfiles/ (default <file>.json).
        profile: game whose water file is read.
    """
    from .api import water_export as f

    return f(file, out, profile)


@op("water.write", mcp=False, group="formats",
    summary="Write water.dat from a JSON document (satk-water/1 or a list of polygons) in the stock number format; "
            "reports pool use and engine issues.",
    summary_ru="Собрать water.dat из JSON (satk-water/1 или список полигонов) в стоковом формате; лимиты и проблемы.",
    examples=("satk water write water.json --out water.dat",))
def water_write(src: str, out: str | None = None) -> dict:
    """Write a water file.

    Args:
        src: JSON document.
        out: output file under <work>/out/worldfiles/ (default water.dat).
    """
    from .api import water_write as f

    return f(src, out)


# --------------------------------------------------------------------------- timecyc / popcycle


@op("timecyc.get", mcp=False, group="formats",
    summary="Time cycle values by name (sky_top, far_clip, water, postfx1, ...) per weather and hour; one point "
            "shows every field. Without arguments: weathers, hours and field names.",
    summary_ru="Значения timecyc по именам (sky_top, far_clip, water, ...) по погоде и часу; одна точка = все поля.",
    examples=("satk timecyc get SUNNY_LA 12", "satk timecyc get \"*_LA\" all --field far_clip fog_start",
              "satk timecyc get"))
def timecyc_get(weather: str | None, hour: str | None, field: list[str] | None = None,
                file: str | None = None, profile: str = "vanilla", limit: int = 20,
                cursor: str | None = None) -> dict:
    """Read timecyc.dat.

    Args:
        weather: name (SUNNY_LA), glob (*_LA), index (0-22) or all.
        hour: a time point of the file (0 5 6 7 12 19 20 22), several (6,7) or all.
        field: fields to show (default sky_top sky_bot amb dir far_clip fog_start water); sky_top.r picks one.
        file: a timecyc.dat path (default: the profile's).
        profile: game whose file is read.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .api import timecyc_get as f

    return f(weather, hour, field, file, profile, limit, cursor)


@op("timecyc.patch", mcp=False, group="formats",
    summary="Change timecyc.dat fields (sky_top=30,117,210 far_clip*=1.5 water.a=200) for weathers/hours; only the "
            "changed numbers are rewritten. Writes a Mod Loader folder with data/timecyc.dat.",
    summary_ru="Изменить поля timecyc.dat (sky_top=30,117,210 far_clip*=1.5) для погод/часов; меняются только "
               "нужные числа. Папка Mod Loader с data/timecyc.dat.",
    examples=("satk timecyc patch SUNNY_LA 12 sky_top=30,117,210", "satk timecyc patch all all far_clip*=1.5",
              "satk timecyc patch \"*_SF\" 0,22 amb=40,40,60 --dry-run"))
def timecyc_patch(weather: str, hour: str, set: list[str], file: str | None = None,  # noqa: A002
                  profile: str = "vanilla", out: str | None = None, dry_run: bool = False, limit: int = 20) -> dict:
    """Patch timecyc.dat.

    Args:
        weather: name, glob, index or all.
        hour: time point(s) of the file or all.
        set: field=value (one number for all components or one per component), field*=k, field+=k, field-=k;
            sky_top.g=100 changes one component.
        file: a timecyc.dat path to patch (default: the profile's).
        profile: game whose file is the base.
        out: output folder name under <work>/out/worldfiles/ (default timecyc-patch).
        dry_run: show the changes, write nothing.
        limit: changed values to list (max 500).
    """
    from .api import timecyc_patch as f

    return f(weather, hour, set, file, profile, out, dry_run, limit)


@op("timecyc.diff", mcp=False, group="formats",
    summary="Compare two timecyc.dat files field by field (a path or a profile name; b defaults to vanilla): "
            "changed values per weather and hour, counts per field.",
    summary_ru="Сравнить два timecyc.dat по полям (путь или профиль; b по умолчанию vanilla): изменения и счётчики.",
    examples=("satk timecyc diff installed", "satk timecyc diff mymod/data/timecyc.dat --field far_clip"))
def timecyc_diff(a: str, b: str | None, field: list[str] | None = None, profile: str = "vanilla",
                 limit: int = 20, cursor: str | None = None) -> dict:
    """Diff two timecycs.

    Args:
        a: a timecyc.dat path or a profile name (installed).
        b: the other file or profile (default vanilla).
        field: only these fields.
        profile: profile for relative paths.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .api import timecyc_diff as f

    return f(a, b, field, profile, limit, cursor)


@op("popcycle.get", mcp=False, group="formats",
    summary="Population cycle (popcycle.dat): max peds/cars, dealer/gang/cop/other percentages and the 18 ped "
            "groups per zone type, weekday/weekend and 2-hour slot.",
    summary_ru="popcycle.dat: максимум педов/машин, проценты и 18 групп педов по типу зоны, дню и 2-часовому слоту.",
    examples=("satk popcycle get GANGLAND weekday 20", "satk popcycle get all weekend 12 --field max_peds max_cars",
              "satk popcycle get"))
def popcycle_get(zone: str | None, day: str | None, hour: str | None,
                 field: list[str] | None = None, file: str | None = None, profile: str = "vanilla",
                 limit: int = 20, cursor: str | None = None) -> dict:
    """Read popcycle.dat.

    Args:
        zone: zone type (GANGLAND, BEACH, ... or 0-19) or all.
        day: weekday, weekend or all.
        hour: an hour 0-23 (its 2-hour slot), several (8,20) or all.
        field: fields to show (default max_peds max_cars dealers gang cops other).
        file: a popcycle.dat path (default: the profile's).
        profile: game whose file is read.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .api import popcycle_get as f

    return f(zone, day, hour, field, file, profile, limit, cursor)


@op("popcycle.patch", mcp=False, group="formats",
    summary="Change popcycle.dat values (max_peds=25 gang*=1.5) for zone types/days/hours; only the changed "
            "numbers are rewritten. Writes a Mod Loader folder with data/popcycle.dat.",
    summary_ru="Изменить значения popcycle.dat (max_peds=25 gang*=1.5); меняются только нужные числа. Папка Mod Loader.",
    examples=("satk popcycle patch GANGLAND all all max_peds*=1.5", "satk popcycle patch BEACH weekend 14 max_cars=20"))
def popcycle_patch(zone: str, day: str, hour: str, set: list[str], file: str | None = None,  # noqa: A002
                   profile: str = "vanilla", out: str | None = None, dry_run: bool = False,
                   limit: int = 20) -> dict:
    """Patch popcycle.dat.

    Args:
        zone: zone type or all.
        day: weekday, weekend or all.
        hour: hour 0-23 (its slot), several or all.
        set: field=value, field*=k, field+=k, field-=k (values are bytes 0..255).
        file: a popcycle.dat path to patch (default: the profile's).
        profile: game whose file is the base.
        out: output folder name under <work>/out/worldfiles/ (default popcycle-patch).
        dry_run: show the changes, write nothing.
        limit: changed values to list (max 500).
    """
    from .api import popcycle_patch as f

    return f(zone, day, hour, set, file, profile, out, dry_run, limit)


# --------------------------------------------------------------------------- radar


@op("radar.export", mcp=False, group="map",
    summary="Export the radar (144 radarNN.txd tiles of gta3.img) as one PNG mosaic to edit; north up, 500 m per "
            "tile.",
    summary_ru="Радар (144 тайла radarNN.txd из gta3.img) одной PNG-мозаикой для правки; север сверху, 500 м на тайл.",
    examples=("satk radar export --out radar.png", "satk radar export --tile 64 --profile installed"))
def radar_export(out: str | None = None, tile: int = 128, profile: str = "vanilla") -> dict:
    """Export the radar mosaic.

    Args:
        out: PNG under <work>/out/worldfiles/ (default radar-<profile>.png).
        tile: pixels per tile in the PNG (32..512; stock tiles are 128).
        profile: game whose gta3.img is read.
    """
    from .api import radar_export as f

    return f(out, tile, profile)


@op("radar.build", mcp=False, group="map", long_running=True,
    summary="Build the 144 radarNN.txd tiles (DXT1, stock container) from an image or --from-map (schematic: "
            "water.dat, building footprints, car paths); verified by decoding (PSNR). Mod Loader folder + preview.",
    summary_ru="144 тайла radarNN.txd (DXT1) из картинки или --from-map (схема: вода, здания, дороги); проверка "
               "декодированием (PSNR). Папка Mod Loader + превью.",
    examples=("satk radar build work/out/worldfiles/radar-vanilla.png --out myradar",
              "satk radar build --from-map --out schematic"))
def radar_build(image: str | None, from_map: bool = False, out: str | None = None, tile: int = 128,
                quality: Literal["fast", "normal", "high"] = "normal", layers: list[str] | None = None,
                profile: str = "vanilla") -> dict:
    """Build radar tiles.

    Args:
        image: picture of the whole map (square; north up; PNG/JPG/BMP/TGA); resampled to 12 x tile pixels.
        from_map: draw a schematic map from the profile instead of reading an image.
        out: output folder name under <work>/out/worldfiles/ (default radar).
        tile: tile size in pixels (stock 128).
        quality: DXT1 encoder effort.
        layers: --from-map layers: water, buildings, roads (default all).
        profile: game for --from-map (its water.dat, index and paths).
    """
    from .api import radar_build as f

    return f(image, from_map, out, tile, quality, layers, profile)
