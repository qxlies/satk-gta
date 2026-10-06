"""Operations of satk.viewscene: ``satk view place|vehicle|ped|reload|remove|list``.

The agent's own models, vehicles and peds in the running viewer (SAAP ``scene.*``,
``proto/SAAP-v1.md`` §11). CLI and ``satk_op`` only (``mcp=False``). The work is in
:mod:`satk.viewscene.api`.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import op

Target = Literal["ariane", "game", "mock"]


@op("view.place", summary="Place a model from files (DFF + TXDs, or --model for a game object) into the running "
                          "viewer; answers a handle and the SID el:scene/<handle> that pick and capture legends show. "
                          "--watch reloads it when the files change.",
    summary_ru="Поставить свою модель (DFF + TXD или --model) в работающий вьювер; --watch перезагружает при изменении "
               "файлов.",
    mcp=False, examples=("satk view place work/tmp/test/bench.dff --txd work/tmp/test/bench.txd --pos 2493,-1671,13.4 "
                         "--ground --watch --id bench",
                         "satk view place --model 1280 --pos 2493,-1671,13.4 --heading 30 --target mock"))
def view_place(dff: str | None, txd: list[str] | None = None, model: str | None = None,
               pos: list[float] | None = None, rot: list[float] | None = None, heading: float | None = None,
               scale: list[float] | None = None, ground: bool = False, watch: bool = False,
               id: str | None = None, target: Target = "ariane") -> dict:  # noqa: A002
    """Place a model.

    Args:
        dff: the model file (absolute or relative to the current folder).
        txd: texture dictionaries, searched in this order before the game's.
        model: game object id or name (alone: the game's model; with dff: its TXD as a fallback).
        pos: x,y,z of the entity origin (required).
        rot: rx,ry,rz degrees (X, then Y, then Z) or a quaternion x,y,z,w.
        heading: degrees about Z (counter-clockwise from above).
        scale: 1 or 3 factors.
        ground: drop onto the collision below pos.
        watch: reload when the files change (about 0.5 s).
        id: handle to use; placing the same id again replaces that entity.
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.place(target, dff=dff, txd=txd, model=model, pos=pos, rot=rot, heading=heading, scale=scale,
                     ground=ground, watch=watch, id=id)


@op("view.vehicle", summary="Place a vehicle (game model or DFF+TXD) in the running viewer: frame hierarchy, wheels "
                            "from the IDE, carcols colours, dirt 0-15, lamps on, parts ok/dam/off. Lands on the ground "
                            "unless --no-ground.",
    summary_ru="Поставить машину во вьювер: цвета carcols, грязь 0-15, фары, детали ok/dam/off, колёса из IDE.",
    mcp=False, examples=("satk view vehicle 426 --pos 2495,-1675,13.4 --heading 90 --dirt 2 --colors 3 1",
                         "satk view vehicle premier --pos 2495,-1675,13.4 --lights --parts door_lf=dam bonnet=off "
                         "--target mock"))
def view_vehicle(model: str | None, dff: str | None = None, txd: list[str] | None = None,
                 pos: list[float] | None = None, heading: float | None = None, rot: list[float] | None = None,
                 colors: list[str] | None = None, dirt: int = 0, lights: bool = False, parts: list[str] | None = None,
                 wheel_model: str | None = None, wheel_scale: list[float] | None = None, ground: bool = True,
                 watch: bool = False, id: str | None = None, target: Target = "ariane") -> dict:  # noqa: A002
    """Place a vehicle.

    Args:
        model: vehicle id or name from data/vehicles.ide (with dff: its IDE data and TXD).
        dff: a vehicle DFF file instead of the game's.
        txd: texture dictionaries searched before the game's (vehicle.txd is always added).
        pos: x,y,z of the vehicle centre (required).
        heading: degrees about Z.
        rot: rx,ry,rz degrees or a quaternion x,y,z,w.
        colors: paint slots 1-4: carcols.dat indices or #rrggbb (default: the first carcols set).
        dirt: dirt level 0-15.
        lights: lamps on.
        parts: NAME=ok|dam|off, e.g. door_lf=dam bonnet=off.
        wheel_model: wheel model id or name (veh_mods wheels).
        wheel_scale: wheel diameter in m, or front,rear.
        ground: put the wheels on the collision below pos.
        watch: reload when the files change.
        id: handle to use; the same id replaces that vehicle.
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.vehicle(target, model=model, dff=dff, txd=txd, pos=pos, rot=rot, heading=heading, colors=colors,
                       dirt=dirt, lights=lights, parts=parts, wheel_model=wheel_model, wheel_scale=wheel_scale,
                       ground=ground, watch=watch, id=id)


@op("view.ped", summary="Place a skinned ped (game model or DFF+TXD) in the running viewer, posed by an IFP frame "
                        "(default ped.ifp idle_stance at 0 s). Lands on the ground unless --no-ground.",
    summary_ru="Поставить педа во вьювер в позе из кадра IFP (по умолчанию idle_stance).",
    mcp=False, examples=("satk view ped 105 --pos 2497.5,-1673,13.4 --heading 180",
                         "satk view ped fam1 --pos 2497.5,-1673,13.4 --anim walk_civi --anim-time 0.4 --target mock"))
def view_ped(model: str | None, dff: str | None = None, txd: list[str] | None = None, pos: list[float] | None = None,
             heading: float | None = None, rot: list[float] | None = None, anim: str | None = None,
             ifp: str | None = None, anim_time: float = 0.0, ground: bool = True, watch: bool = False,
             id: str | None = None, target: Target = "ariane") -> dict:  # noqa: A002
    """Place a ped.

    Args:
        model: ped id or name from data/peds.ide (CJ, model 0, is not supported).
        dff: a skinned ped DFF instead of the game's.
        txd: texture dictionaries searched before the game's.
        pos: x,y,z of the ped root, about 1 m above the feet (required).
        heading: degrees about Z.
        rot: rx,ry,rz degrees or a quaternion x,y,z,w.
        anim: animation name (default idle_stance).
        ifp: animation file: a game name (ped, or an anim.img entry) or a .ifp path (default ped).
        anim_time: seconds into the animation.
        ground: put the feet on the collision below pos.
        watch: reload when the files change.
        id: handle to use; the same id replaces that ped.
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.ped(target, model=model, dff=dff, txd=txd, pos=pos, rot=rot, heading=heading, anim=anim, ifp=ifp,
                   anim_time=anim_time, ground=ground, watch=watch, id=id)


@op("view.reload", summary="Re-read the files of a placed entity (or of all): rows [handle,ok,load_ms,error]. A failed "
                           "reload keeps the previous model.",
    summary_ru="Перечитать файлы поставленной сущности (или всех).",
    mcp=False, examples=("satk view reload bench", "satk view reload --target mock"))
def view_reload(handle: str | None, target: Target = "ariane") -> dict:
    """Reload placed entities.

    Args:
        handle: the entity to reload (default: all).
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.reload(target, handle)


@op("view.remove", summary="Remove placed entities by handle, or all of them with --all.",
    summary_ru="Убрать поставленные сущности (по handle или все с --all).",
    mcp=False, examples=("satk view remove bench car", "satk view remove --all --target mock"))
def view_remove(handles: list[str] | None, all: bool = False, target: Target = "ariane") -> dict:  # noqa: A002
    """Remove placed entities.

    Args:
        handles: handles (or @refs) to remove.
        all: remove every placed entity.
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.remove(target, handles, all)


@op("view.list", summary="Placed entities of the running viewer: rows [handle,sid,kind,model,x,y,z,heading,watch,"
                         "reloads,load_ms,file,error].",
    summary_ru="Список поставленных во вьювер сущностей.",
    mcp=False, examples=("satk view list", "satk view list --target mock"))
def view_list(target: Target = "ariane") -> dict:
    """List placed entities.

    Args:
        target: ariane (the viewer) or mock.
    """
    from . import api

    return api.list_scene(target)
