-- satk-testdrive (client): loads the manifest models (COL -> TXD -> DFF), swaps server test elements
-- to their custom ids, camera presets, a small HUD and the export `tdc` for satk (via satk-agent).
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- Modes per manifest model:
--   "new"     engineRequestModel(kind, base) gives a fresh id; the vanilla base stays available as
--             the reference. The new id shares the TXD slot of its base and MTA imports textures per
--             slot, so same-named textures show on the base too (shared_txd): the visual checks run
--             the reference in a second pass with the mod unloaded;
--   "replace" the files replace the base id itself (weapons, or clients without engineRequestModel).
-- Unloading destroys the TXD/DFF/COL elements, which makes MTA restore the original model. The
-- elements load files of satk-testdrive-mod, so MTA also destroys them when that resource stops.
-- (engineRequestTXD is not used: a fresh TXD slot has no dictionary and the first import would make
-- the game stream an unbacked slot.)

TDC = TDC or {}

local M = nil                -- manifest from the server ("satk:td:manifest")
local contentUp = false      -- satk-testdrive-mod runs on this client (its files are downloaded)
local appliedRev = nil
local loaded = {}            -- key -> {id, mode, base, target, dff, txd, col, ok, err, shared_txd}
local bannerUntil, bannerText = 0, ""
local REQUEST_KIND = {vehicle = "vehicle", object = "object", ped = "ped"}

local function log(text, level)
    outputDebugString("satk-testdrive: " .. text, level or 3)
end

local function contentPath(rel)
    return ":" .. TD.CONTENT .. "/" .. rel
end

function TDC.manifest()
    return M
end

function TDC.loaded(key)
    return loaded[key]
end

--- Model id the mod lane uses for manifest model m (nil when it did not load).
function TDC.modId(m)
    local L = m and loaded[m.key]
    if L and L.ok then
        return L.target
    end
    return nil
end

---------------------------------------------------------------------------- loading

local function destroyParts(L)
    for _, k in ipairs({"dff", "txd", "col"}) do
        if L[k] and isElement(L[k]) then
            destroyElement(L[k])
        end
        L[k] = nil
    end
end

--- Undo the replacement of one model (the original comes back; requested ids keep their slot).
function TDC.unloadModel(m)
    local L = loaded[m.key]
    if L then
        destroyParts(L)
        if L.wheels0 and setVehicleModelWheelSize then
            pcall(setVehicleModelWheelSize, L.target, "front_axle", L.wheels0[1])
            pcall(setVehicleModelWheelSize, L.target, "rear_axle", L.wheels0[2])
        end
        L.ok = false
        L.unloaded = true
    end
end

local function requestId(m, L)
    if L.id then
        return L.id
    end
    if not engineRequestModel or not REQUEST_KIND[m.kind] then
        return nil, "engineRequestModel is not available for " .. tostring(m.kind)
    end
    local id = engineRequestModel(REQUEST_KIND[m.kind], m.base)
    if not id then
        return nil, "engineRequestModel(" .. m.kind .. ", " .. tostring(m.base) .. ") failed"
    end
    L.id = id
    return id
end

--- Load one manifest model: COL -> TXD -> DFF into its target id (the TXD before the DFF: MTA binds the
--- DFF's materials to the model's textures when it replaces the model).
function TDC.loadModel(m)
    local L = loaded[m.key] or {}
    loaded[m.key] = L
    destroyParts(L)
    L.mode, L.base, L.kind, L.err, L.ok, L.unloaded = m.mode, m.base, m.kind, nil, false, nil
    local id = m.base
    if m.mode == "new" then
        local err
        id, err = requestId(m, L)
        if not id then
            L.err = err
            return L
        end
    end
    L.target = id
    local f = m.files or {}
    L.shared_txd = (m.mode == "new" and f.txd ~= nil) or nil
    local errs = {}
    if f.col then
        L.col = engineLoadCOL(contentPath(f.col))
        if not L.col or not engineReplaceCOL(L.col, id) then
            errs[#errs + 1] = "COL " .. f.col
        end
    end
    if f.txd then
        L.txd = engineLoadTXD(contentPath(f.txd))
        if not L.txd or not engineImportTXD(L.txd, id) then
            errs[#errs + 1] = "TXD " .. f.txd
        end
    end
    if f.dff then
        L.dff = engineLoadDFF(contentPath(f.dff))
        if not L.dff or not engineReplaceModel(L.dff, id) then
            errs[#errs + 1] = "DFF " .. f.dff
        end
    end
    if tonumber(m.draw) and engineSetModelLODDistance then
        pcall(engineSetModelLODDistance, id, tonumber(m.draw))
    end
    if m.kind == "vehicle" and type(m.wheels) == "table" and setVehicleModelWheelSize then
        if not L.wheels0 and getVehicleModelWheelSize then
            local okF, wf = pcall(getVehicleModelWheelSize, id, "front_axle")
            local okR, wr = pcall(getVehicleModelWheelSize, id, "rear_axle")
            if okF and okR and tonumber(wf) and tonumber(wr) then
                L.wheels0 = {wf, wr}  -- restored when the mod is unloaded (reference pass)
            end
        end
        if tonumber(m.wheels[1]) then
            pcall(setVehicleModelWheelSize, id, "front_axle", m.wheels[1])
        end
        if tonumber(m.wheels[2]) then
            pcall(setVehicleModelWheelSize, id, "rear_axle", m.wheels[2])
        end
    end
    if #errs > 0 then
        L.err = "failed to load " .. table.concat(errs, ", ")
    end
    L.ok = L.err == nil
    return L
end

--- Swap a server element marked with TD.KEY to the id of its model on this client.
local function swap(el)
    if not isElement(el) then
        return
    end
    local key = getElementData(el, TD.KEY)
    if not key then
        return
    end
    local m = TD.modelOf(M, key)
    if not m then
        return
    end
    local want = TDC.modId(m) or m.base
    if want and getElementModel(el) ~= want then
        setElementModel(el, want)
        if getElementType(el) == "vehicle" then
            triggerServerEvent("satk:td:reapply", el)
        end
    end
end

local function swapAll()
    for _, t in ipairs({"vehicle", "object", "ped"}) do
        for _, el in ipairs(getElementsByType(t, root, true)) do
            swap(el)
        end
    end
end

local function report(ms)
    local models = {}
    for _, m in ipairs(M and M.models or {}) do
        local L = loaded[m.key] or {}
        models[m.key] = {ok = L.ok == true, id = L.target, mode = m.mode, err = L.err, shared_txd = L.shared_txd}
    end
    triggerServerEvent("satk:td:applied", resourceRoot, {rev = M and M.rev, ms = ms, models = models,
        request_model = engineRequestModel ~= nil})
end

function TDC.applyAll(force)
    if not M or not contentUp then
        return false
    end
    if appliedRev == M.rev and not force then
        return true
    end
    local t0 = getTickCount()
    local ok, bad = 0, 0
    for _, m in ipairs(M.models or {}) do
        local L = TDC.loadModel(m)
        if L.ok then
            ok = ok + 1
        else
            bad = bad + 1
            log(m.key .. ": " .. tostring(L.err), 1)
        end
    end
    appliedRev = M.rev
    swapAll()
    local ms = getTickCount() - t0
    bannerText = ("satk test drive: model set %s loaded (%d ok%s) in %d ms"):format(tostring(M.label or M.rev), ok,
        bad > 0 and (", " .. bad .. " failed") or "", ms)
    bannerUntil = getTickCount() + 6000
    report(ms)
    return true
end

addEvent("satk:td:manifest", true)
addEventHandler("satk:td:manifest", resourceRoot, function(m)
    if type(m) ~= "table" then
        return
    end
    M = m
    TDC.applyAll(false)
end)

addEventHandler("onClientResourceStart", root, function(res)
    if res == resource then
        contentUp = getResourceFromName(TD.CONTENT) and true or false
        triggerServerEvent("satk:td:ready", resourceRoot)
    elseif getResourceName(res) == TD.CONTENT then
        contentUp = true
        -- ask for the manifest of this content run; it applies when it arrives
        triggerServerEvent("satk:td:ready", resourceRoot)
    end
end)

addEventHandler("onClientResourceStop", root, function(res)
    if res ~= resource and getResourceName(res) == TD.CONTENT then
        contentUp = false
    end
end)

addEventHandler("onClientElementStreamIn", root, function()
    swap(source)
end)

addEventHandler("onClientElementDataChange", root, function(name)
    if name == TD.KEY and isElementStreamedIn(source) then
        swap(source)
    end
end)

---------------------------------------------------------------------------- camera presets

local PRESETS = {
    -- along (forward), side (right), up: multiples of the target's radius; fov
    front = {1.9, 0.35, 0.35, 60},
    rear = {-1.9, -0.35, 0.45, 60},
    side = {0.0, 2.0, 0.25, 60},
    three_quarter = {1.4, 1.4, 0.6, 60},
    rear_quarter = {-1.4, 1.4, 0.6, 60},
    top = {-0.3, 0.0, 3.2, 70},
    wheel = {0.9, 1.3, -0.15, 50},
}
TDC.PRESETS = PRESETS

local function headingOf(el)
    local _, _, rz = getElementRotation(el)
    return rz or 0
end

--- Camera pose of a preset around element el: px, py, pz, lx, ly, lz, fov.
function TDC.presetPose(name, el)
    local p = PRESETS[name] or PRESETS.three_quarter
    local x, y, z = getElementPosition(el)
    local r = math.max(getElementRadius(el) or 2.5, 1.2)
    local h = headingOf(el)
    local cx, cy = TD.offset(x, y, h, p[1] * r, p[2] * r)
    local cz = z + p[3] * r + 0.6
    return cx, cy, cz, x, y, z + 0.2, p[4]
end

--- The element a camera preset looks at: an element id, the vehicle the player sits in, the last
--- spawned test element or the player.
function TDC.targetOf(target)
    if type(target) == "string" and target ~= "" and target ~= "auto" and target ~= "player" then
        local el = getElementByID(target)
        if el then
            return el
        end
    end
    if target == "player" then
        return localPlayer
    end
    local veh = getPedOccupiedVehicle(localPlayer)
    if veh then
        return veh
    end
    local best, bestN = nil, -1
    for _, t in ipairs({"vehicle", "object", "ped"}) do
        for _, el in ipairs(getElementsByType(t, root, true)) do
            local id = getElementID(el) or ""
            local n = tonumber(id:match("^satk%.td%.(%d+)$"))
            if n and n > bestN then
                best, bestN = el, n
            end
        end
    end
    return best or localPlayer
end

function TDC.setCamera(name, target)
    if not name or name == "chase" or name == "free" or name == "release" then
        setCameraTarget(localPlayer)
        return true
    end
    local el = TDC.targetOf(target)
    local px, py, pz, lx, ly, lz, fov = TDC.presetPose(name, el)
    setCameraMatrix(px, py, pz, lx, ly, lz, 0, fov)
    return true
end

addEvent("satk:td:camera", true)
addEventHandler("satk:td:camera", resourceRoot, function(name, elementId)
    -- the element was created this server frame: give it time to stream in
    setTimer(function()
        TDC.setCamera(name, elementId)
    end, 600, 1)
end)

---------------------------------------------------------------------------- HUD

local sw, sh = guiGetScreenSize()
addEventHandler("onClientRender", root, function()
    if TDC.busy and TDC.busy() then
        return
    end
    if getTickCount() < bannerUntil then
        dxDrawText(bannerText, 0, 0, sw - 30, sh * 0.25, tocolor(255, 204, 0, 230), 1.2, "default-bold", "right",
            "bottom")
    end
    local veh = getPedOccupiedVehicle(localPlayer)
    if not veh then
        return
    end
    local key = getElementData(veh, TD.KEY)
    if not key then
        return
    end
    local kmh = math.floor(TD.kmh(getElementVelocity(veh)) + 0.5)
    local m = TD.modelOf(M, key)
    dxDrawText(kmh .. " km/h", 0, 0, sw - 30, sh - 70, tocolor(255, 255, 255, 230), 2.0, "pricedown", "right", "bottom")
    dxDrawText((m and (m.label or m.key) or key) .. (m and (" (" .. m.mode .. ")") or ""), 0, 0, sw - 30, sh - 40,
        tocolor(255, 204, 0, 220), 1.0, "default-bold", "right", "bottom")
end)

---------------------------------------------------------------------------- the export for satk

local C = {}

function C.status(a)
    local models = {}
    for _, m in ipairs(M and M.models or {}) do
        local L = loaded[m.key] or {}
        models[#models + 1] = {key = m.key, kind = m.kind, mode = m.mode, ok = L.ok == true, id = L.target,
                               err = L.err, shared_txd = L.shared_txd, unloaded = L.unloaded}
    end
    local job = TDC.jobInfo and TDC.jobInfo() or nil
    return {rev = M and M.rev or nil, applied = appliedRev, content = contentUp, models = models, job = job,
            request_model = engineRequestModel ~= nil, screen = {sw, sh}}
end

function C.pose(a)
    local el = TDC.targetOf(a.target)
    local px, py, pz, lx, ly, lz, fov = TDC.presetPose(a.camera or "three_quarter", el)
    return {pos = TD.r3(px, py, pz, 3), look = TD.r3(lx, ly, lz, 3), fov_h_deg = fov,
            target = getElementType(el), id = getElementID(el)}
end

function C.camera(a)
    TDC.setCamera(a.camera, a.target)
    return {camera = a.camera or "chase"}
end

function C.apply(a)
    return {applied = TDC.applyAll(a.force ~= false), rev = appliedRev}
end

--- Export: tdc(cmd, args) -> table; check jobs live in checks.lua (TDC.jobCommand).
function tdc(cmd, args)
    args = type(args) == "table" and args or {}
    local fn = C[cmd]
    if not fn and TDC.jobCommand then
        local ok, res = pcall(TDC.jobCommand, cmd, args)
        if not ok then
            return {error = tostring(res)}
        end
        return res
    end
    if not fn then
        return {error = "unknown tdc command " .. tostring(cmd)}
    end
    local ok, res = pcall(fn, args)
    if not ok then
        return {error = tostring(res)}
    end
    return res
end
