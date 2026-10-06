-- satk-testdrive (server): the manifest of satk-testdrive-mod, test spots, spawned test elements,
-- per-player load reports and the export `td` that satk calls through the satk-agent bridge.
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- The models themselves are loaded by client.lua (engineRequestModel ids exist only on clients):
-- the server creates vehicles, objects and peds with the base model and marks them with the
-- element data TD.KEY; clients swap them to their custom ids. Handling (setVehicleHandling) is set
-- here so that it is synced.

local M = nil               -- manifest table of the content resource (nil while it is not running)
local reports = {}          -- player -> {rev, ms, models, ...} sent by client.lua after loading
local ready = {}            -- player -> true once client.lua runs
local spawned = {}          -- elements made by td spawn / chat commands (removed by td clear)
local current = {}          -- player -> the vehicle /car or td drive gave them
local props = {}            -- spot name -> {objects}
local restartAt = nil       -- getTickCount() of the last content restart request
local restartRev = nil      -- manifest rev before that restart
local counter = 0

addEvent("satk:td:ready", true)
addEvent("satk:td:applied", true)
addEvent("satk:td:reapply", true)

local function contentResource()
    return getResourceFromName(TD.CONTENT)
end

local function say(player, text)
    if isElement(player) then
        outputChatBox(text, player, 150, 210, 255, true)
    end
end

local function log(text, level)
    outputDebugString("satk-testdrive: " .. text, level or 3)
end

local function loadManifest()
    local res = contentResource()
    M = nil
    if not res or getResourceState(res) ~= "running" then
        return nil
    end
    local ok, m = pcall(call, res, "manifest")
    if ok and type(m) == "table" and type(m.models) == "table" then
        M = m
    else
        log("no manifest from " .. TD.CONTENT .. " (" .. tostring(m) .. ")", 2)
    end
    return M
end

local function pushManifest(player)
    if not M then
        return
    end
    if player then
        triggerClientEvent(player, "satk:td:manifest", resourceRoot, M)
        return
    end
    for p in pairs(ready) do
        if isElement(p) then
            triggerClientEvent(p, "satk:td:manifest", resourceRoot, M)
        end
    end
end

local function firstPlayer(name)
    local best = nil
    for _, p in ipairs(getElementsByType("player")) do
        if name and getPlayerName(p) == name then
            return p
        end
        if not best then
            best = p
        end
    end
    if name then
        return nil
    end
    return best
end

local function spotOf(name)
    if not M or type(M.spots) ~= "table" then
        return nil
    end
    return M.spots[name or ""]
end

local function defaultSpot()
    local d = M and M.defaults or {}
    return d.spot or "grove"
end

---------------------------------------------------------------------------- environment

local function applyEnv(env)
    if type(env) ~= "table" then
        return
    end
    local h, m = TD.parseTime(env.time)
    if h then
        setTime(h, m)
    end
    if tonumber(env.weather) then
        setWeather(tonumber(env.weather))
    end
end

local function envNow()
    local h, m = getTime()
    return {time = TD.formatTime(h, m), weather = getWeather()}
end

---------------------------------------------------------------------------- spots and props

local function makeProps(name)
    local spot = spotOf(name)
    if not spot or type(spot.props) ~= "table" or props[name] then
        return
    end
    local list = {}
    for _, p in ipairs(spot.props) do
        local o = createObject(p[1], p[2], p[3], p[4], p[5] or 0, p[6] or 0, p[7] or 0)
        if o then
            setElementData(o, "satk.td.prop", name, false)
            list[#list + 1] = o
        else
            log(("prop %s of spot %s: createObject failed"):format(tostring(p[1]), name), 2)
        end
    end
    props[name] = list
end

local function clearProps()
    for _, list in pairs(props) do
        for _, o in ipairs(list) do
            if isElement(o) then
                destroyElement(o)
            end
        end
    end
    props = {}
end

local function spawnPlayerAt(player, name)
    local spot = spotOf(name) or spotOf(defaultSpot())
    local x, y, z, h = 2495.0, -1687.0, 13.5, 0
    if spot then
        x, y, z, h = spot.pos[1], spot.pos[2], spot.pos[3], spot.h or 0
    end
    local sx, sy = TD.offset(x, y, h, -2.0, -3.0)
    spawnPlayer(player, sx, sy, z + 0.5, h, 0)
    fadeCamera(player, true)
    setCameraTarget(player, player)
end

---------------------------------------------------------------------------- models

local function applyHandling(veh, m)
    if not m or type(m.handling) ~= "table" then
        return 0
    end
    local bad = 0
    for _, prop in ipairs(TD.sortedKeys(m.handling)) do
        local value = m.handling[prop]
        if not setVehicleHandling(veh, prop, value) then
            bad = bad + 1
            log(("%s: handling %s = %s rejected"):format(m.key, prop, tostring(value)), 2)
        end
    end
    return bad
end

local function decorate(el, m)
    if not m then
        return
    end
    setElementData(el, TD.KEY, m.key)
    if getElementType(el) == "vehicle" then
        applyHandling(el, m)
        if type(m.colors) == "table" and #m.colors >= 2 then
            setVehicleColor(el, m.colors[1], m.colors[2], m.colors[3] or 0, m.colors[4] or 0)
        end
    end
end

--- {model = manifest model or nil, id = base/server model id, kind = ...} for a spawn request.
local function resolve(what, kind)
    if type(what) == "number" or (type(what) == "string" and tonumber(what)) then
        return {id = tonumber(what), kind = kind or "vehicle"}
    end
    local m = TD.modelOf(M, what or "mod", kind)
    if not m then
        return nil
    end
    return {model = m, id = m.base, kind = m.kind}
end

local function track(el)
    counter = counter + 1
    setElementID(el, "satk.td." .. counter)
    spawned[#spawned + 1] = el
    return getElementID(el)
end

local function vehicleOf(player)
    local v = getPedOccupiedVehicle(player)
    if not v then
        say(player, "Get into a vehicle first.")
    end
    return v
end

--- Spawn a test element for `player`. Returns a result table or nil, error.
local function spawnFor(player, a)
    a = a or {}
    local r = resolve(a.model, a.kind)
    if not r then
        return nil, "unknown model " .. tostring(a.model)
    end
    local spotName = a.spot
    local spot = spotOf(spotName)
    local x, y, z, h
    if type(a.pos) == "table" then
        x, y, z, h = a.pos[1], a.pos[2], a.pos[3], tonumber(a.h) or 0
    elseif spot then
        x, y, z, h = spot.pos[1], spot.pos[2], spot.pos[3], tonumber(a.h) or spot.h or 0
    elseif player then
        local px, py, pz = getElementPosition(player)
        local _, _, rz = getElementRotation(player)
        x, y = TD.offset(px, py, rz, 6, 0)
        z, h = pz, rz
    else
        return nil, "no position: give spot or pos"
    end
    if spot then
        makeProps(spotName)
        applyEnv(spot.env)
    end
    applyEnv({time = a.time, weather = a.weather})
    local out = {kind = r.kind, model = r.id, key = r.model and r.model.key or nil, pos = TD.r3(x, y, z), h = TD.round(h, 1),
                 spot = spot and spotName or nil}
    if r.kind == "vehicle" then
        local veh = createVehicle(r.id, x, y, z + 0.6, 0, 0, h)
        if not veh then
            return nil, "createVehicle(" .. tostring(r.id) .. ") failed"
        end
        decorate(veh, r.model)
        out.element = track(veh)
        if player and a.drive then
            local old = current[player]
            if isElement(old) and old ~= veh then
                destroyElement(old)
            end
            current[player] = veh
            if isPedDead(player) then
                spawnPlayerAt(player, spotName)
            end
            warpPedIntoVehicle(player, veh)
            out.driving = true
        elseif player and spot then
            local sx, sy = TD.offset(x, y, h, 0, -4.0)
            setElementPosition(player, sx, sy, z + 0.5)
        end
    elseif r.kind == "object" then
        local o = createObject(r.id, x, y, z, 0, 0, h)
        if not o then
            return nil, "createObject(" .. tostring(r.id) .. ") failed"
        end
        decorate(o, r.model)
        out.element = track(o)
        if player and spot then
            local sx, sy = TD.offset(x, y, h, -8.0, 0)
            setElementPosition(player, sx, sy, z + 0.5)
            setElementRotation(player, 0, 0, h)
        end
    elseif r.kind == "ped" then
        local p = createPed(r.id, x, y, z + 0.5, h)
        if not p then
            return nil, "createPed(" .. tostring(r.id) .. ") failed"
        end
        decorate(p, r.model)
        out.element = track(p)
        if player and spot then
            local sx, sy = TD.offset(x, y, h, 4.0, 0)
            setElementPosition(player, sx, sy, z + 0.5)
            setElementRotation(player, 0, 0, (h + 180) % 360)
        end
    elseif r.kind == "weapon" then
        local wid = r.model and r.model.weapon or tonumber(a.weapon)
        if not player or not wid then
            return nil, "a weapon needs a player and a weapon id"
        end
        if spot then
            setElementPosition(player, x, y, z + 0.5)
            setElementRotation(player, 0, 0, h)
        end
        giveWeapon(player, wid, 500, true)
        out.weapon = wid
    else
        return nil, "unknown kind " .. tostring(r.kind)
    end
    if player and a.camera then
        triggerClientEvent(player, "satk:td:camera", resourceRoot, a.camera, out.element)
    end
    return out
end

local function clearAll()
    local n = 0
    for _, el in ipairs(spawned) do
        if isElement(el) then
            destroyElement(el)
            n = n + 1
        end
    end
    spawned = {}
    current = {}
    clearProps()
    return n
end

---------------------------------------------------------------------------- the content resource

local function restartContent()
    local res = contentResource()
    restartRev = M and M.rev or nil
    restartAt = getTickCount()
    if not res then
        -- a new resource folder: the refresh is queued, start it once the server has loaded it
        refreshResources(false)
        setTimer(function()
            local r = contentResource()
            if r then
                startResource(r, true)
            else
                log(TD.CONTENT .. " is not in the server's resources after a refresh", 1)
            end
        end, 300, 1)
        return "refresh"
    end
    local state = getResourceState(res)
    if state == "running" then
        restartResource(res)
        return "restart"
    end
    startResource(res, true)
    return "start"
end

addEventHandler("onResourceStart", root, function(res)
    if res == resource then
        setGameType("satk test drive")
        setMapName("San Andreas")
        loadManifest()
        if M then
            applyEnv(M.defaults)
        end
        for _, p in ipairs(getElementsByType("player")) do
            setElementData(p, "satk.keep_ped", true)
            if isPedDead(p) then
                spawnPlayerAt(p, defaultSpot())
            end
        end
        log("started " .. TD.VERSION .. (M and (", manifest " .. tostring(M.rev)) or ", no manifest yet"))
    elseif getResourceName(res) == TD.CONTENT then
        loadManifest()
        for _, veh in ipairs(getElementsByType("vehicle", resourceRoot)) do
            local key = getElementData(veh, TD.KEY)
            local m = key and TD.modelOf(M, key)
            if m then
                applyHandling(veh, m)
            end
        end
        pushManifest()
        log("content " .. tostring(M and M.rev) .. " loaded (" .. tostring(M and #M.models or 0) .. " models)")
    end
end)

addEventHandler("onResourceStop", root, function(res)
    if res ~= resource and getResourceName(res) == TD.CONTENT then
        M = nil
    end
end)

---------------------------------------------------------------------------- players

addEventHandler("onPlayerJoin", root, function()
    setElementData(source, "satk.keep_ped", true)
    spawnPlayerAt(source, defaultSpot())
end)

addEventHandler("onPlayerWasted", root, function()
    local p = source
    setTimer(function()
        if isElement(p) then
            spawnPlayerAt(p, defaultSpot())
        end
    end, 3000, 1)
end)

addEventHandler("onPlayerQuit", root, function()
    local v = current[source]
    if isElement(v) then
        destroyElement(v)
    end
    current[source] = nil
    ready[source] = nil
    reports[source] = nil
end)

addEventHandler("satk:td:ready", resourceRoot, function()
    if not client then
        return
    end
    local first = not ready[client]
    ready[client] = true
    if not M then
        loadManifest()
    end
    pushManifest(client)
    if first then
        say(client, "#ffcc00satk test drive#ffffff: /tdhelp lists the commands" ..
            (M and (", model set " .. tostring(M.label or M.rev)) or ", no models loaded"))
    end
end)

addEventHandler("satk:td:applied", resourceRoot, function(rep)
    if not client or type(rep) ~= "table" then
        return
    end
    rep.t = getTickCount()
    if restartAt and rep.rev and rep.rev ~= restartRev then
        rep.total_ms = getTickCount() - restartAt
    end
    reports[client] = rep
    log(("%s loaded %s in %s ms"):format(getPlayerName(client), tostring(rep.rev), tostring(rep.ms)))
end)

-- a client swapped a vehicle to its custom id: send the handling again so that it is synced
addEventHandler("satk:td:reapply", root, function()
    if getElementType(source) ~= "vehicle" then
        return
    end
    local key = getElementData(source, TD.KEY)
    local m = key and TD.modelOf(M, key)
    if m then
        applyHandling(source, m)
    end
end)

---------------------------------------------------------------------------- the export for satk

local T = {}

function T.status(a)
    local players = {}
    for _, p in ipairs(getElementsByType("player")) do
        local x, y, z = getElementPosition(p)
        local veh = getPedOccupiedVehicle(p)
        local rep = reports[p]
        players[#players + 1] = {
            name = getPlayerName(p), ready = ready[p] == true, pos = TD.r3(x, y, z),
            vehicle = veh and getElementModel(veh) or nil, dead = isPedDead(p),
            rev = rep and rep.rev or nil, ms = rep and rep.ms or nil, total_ms = rep and rep.total_ms or nil,
            models = rep and rep.models or nil, request_model = rep and rep.request_model or nil,
        }
    end
    local models = {}
    for _, m in ipairs(M and M.models or {}) do
        models[#models + 1] = {key = m.key, kind = m.kind, mode = m.mode, base = m.base}
    end
    local res = contentResource()
    return {version = TD.VERSION, content = res and getResourceState(res) or "missing", rev = M and M.rev or nil,
            label = M and M.label or nil, models = models, players = players, spawned = #spawned,
            env = envNow(), restart_ms = restartAt and (getTickCount() - restartAt) or nil}
end

function T.manifest(a)
    return M or {}
end

function T.spawn(a)
    local player = firstPlayer(a.player)
    if not player and (a.drive or a.kind == "weapon") then
        return {error = "no player has joined the server"}
    end
    local out, err = spawnFor(player, a)
    if not out then
        return {error = err}
    end
    return out
end

function T.clear(a)
    return {removed = clearAll()}
end

function T.env(a)
    applyEnv(a)
    return envNow()
end

function T.restart(a)
    local how, err = restartContent()
    if not how then
        return {error = err}
    end
    return {queued = how, rev = restartRev}
end

function T.teleport(a)
    local player = firstPlayer(a.player)
    if not player then
        return {error = "no player has joined the server"}
    end
    local spot = spotOf(a.spot)
    if not spot then
        return {error = "unknown spot " .. tostring(a.spot)}
    end
    makeProps(a.spot)
    applyEnv(spot.env)
    local veh = getPedOccupiedVehicle(player)
    local el = veh or player
    setElementPosition(el, spot.pos[1], spot.pos[2], spot.pos[3] + 0.5)
    setElementRotation(el, 0, 0, spot.h or 0)
    if veh then
        setElementVelocity(veh, 0, 0, 0)
    end
    return {spot = a.spot, pos = spot.pos}
end

--- Export: td(cmd, args) -> table. Errors come back as {error = "..."}.
function td(cmd, args)
    local fn = T[cmd]
    if not fn then
        return {error = "unknown td command " .. tostring(cmd)}
    end
    local ok, res = pcall(fn, type(args) == "table" and args or {})
    if not ok then
        return {error = tostring(res)}
    end
    return res
end

---------------------------------------------------------------------------- chat commands

local function help(player)
    say(player, "#ffcc00satk test drive#ffffff: /mod - the mod model, /car <key|id>, /cars, /tp <spot>, /spots,")
    say(player, "#ffffff/fix, /flip, /dmg, /god, /lights, /color <c1> [c2], /time <h>, /weather <id>, /handling, /clear")
end

addCommandHandler("tdhelp", help)

addCommandHandler("cars", function(player)
    for _, m in ipairs(M and M.models or {}) do
        say(player, ("#ffcc00/car %s#ffffff - %s %s (%s, base %s)"):format(m.key, m.kind, m.label or m.key, m.mode,
            tostring(m.base)))
    end
end)

addCommandHandler("spots", function(player)
    say(player, "#ffffffSpots: " .. table.concat(TD.sortedKeys(M and M.spots or {}), ", "))
end)

addCommandHandler("mod", function(player)
    local m = TD.modelOf(M, "mod")
    if not m then
        say(player, "No mod model is loaded.")
        return
    end
    local _, err = spawnFor(player, {model = m.key, drive = m.kind == "vehicle"})
    if err then
        say(player, err)
    end
end)

addCommandHandler("car", function(player, _, key)
    local _, err = spawnFor(player, {model = key or "mod", kind = "vehicle", drive = true})
    if err then
        say(player, err .. ": /cars lists the models")
    end
end)

addCommandHandler("tp", function(player, _, name)
    local r = T.teleport({player = getPlayerName(player), spot = name})
    if r.error then
        say(player, r.error .. ": /spots lists them")
    end
end)

addCommandHandler("clear", function(player)
    say(player, ("Removed %d test elements."):format(clearAll()))
end)

addCommandHandler("fix", function(player)
    local v = vehicleOf(player)
    if v then
        fixVehicle(v)
    end
end)

addCommandHandler("flip", function(player)
    local v = vehicleOf(player)
    if v then
        local x, y, z = getElementPosition(v)
        local _, _, rz = getElementRotation(v)
        setElementPosition(v, x, y, z + 1.0)
        setElementRotation(v, 0, 0, rz)
        setElementVelocity(v, 0, 0, 0)
    end
end)

addCommandHandler("color", function(player, _, a, b, c, d)
    local v = vehicleOf(player)
    if v and tonumber(a) then
        setVehicleColor(v, tonumber(a), tonumber(b) or tonumber(a), tonumber(c) or 0, tonumber(d) or 0)
    end
end)

addCommandHandler("time", function(_, _, h, m)
    setTime(tonumber(h) or 12, tonumber(m) or 0)
end)

addCommandHandler("weather", function(_, _, w)
    setWeather(tonumber(w) or 0)
end)

addCommandHandler("dmg", function(player)
    local v = vehicleOf(player)
    if v then
        for door = 0, 5 do
            setVehicleDoorState(v, door, 3)
        end
        for panel = 0, 6 do
            setVehiclePanelState(v, panel, 3)
        end
        setElementHealth(v, 450)
    end
end)

addCommandHandler("god", function(player)
    local v = vehicleOf(player)
    if v then
        local on = not isVehicleDamageProof(v)
        setVehicleDamageProof(v, on)
        say(player, "Damage proof: " .. tostring(on))
    end
end)

addCommandHandler("lights", function(player)
    local v = vehicleOf(player)
    if v then
        setVehicleOverrideLights(v, getVehicleOverrideLights(v) == 2 and 1 or 2)
    end
end)

addCommandHandler("handling", function(player)
    local v = vehicleOf(player)
    if v then
        local h = getVehicleHandling(v)
        say(player, ("mass %.0f, maxVelocity %.0f, engineAcceleration %.2f, drive %s, engine %s"):format(
            h.mass, h.maxVelocity, h.engineAcceleration, tostring(h.driveType), tostring(h.engineType)))
    end
end)
