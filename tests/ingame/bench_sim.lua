-- A small simulated MTA client for the satk-bench tests: only what mta-resources/satk-bench calls, with the
-- argument conventions of the MTA wiki. A virtual clock (getTickCount), events, elements (vehicles, peds,
-- textures, DFFs), a process-memory counter that grows with textures and DFFs, and optional sa-engine
-- functions (Sim.sae = true). Not MTA.

Sim = {
    now = 100000, frame = 0, handlers = {}, els = {}, nextN = 0, camera = nil, va = 1500 * 1048576, minute = 1000,
    hour = 9, min = 30, weather = 0, fps = 60, calls = {}, ground = 30, dffOk = true, vaLimit = nil,
    sae = false, gameMs = 0, frameCounter = 0, timerRatio = 1.0, fireEvery = 100, lastFire = {}, texMiB = 4, dffMiB = 2,
    maxTex = nil, mode = "real",
}

---------------------------------------------------------------------------- JSON for the host

local function isArray(t)
    local n = 0
    for k in pairs(t) do
        if type(k) ~= "number" or k < 1 or k ~= math.floor(k) then
            return false
        end
        n = n + 1
    end
    for i = 1, n do
        if t[i] == nil then
            return false
        end
    end
    return true
end

local function enc(v)
    local t = type(v)
    if v == nil then
        return "null"
    elseif t == "boolean" then
        return v and "true" or "false"
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then
            return "null"
        end
        if v == math.floor(v) and math.abs(v) < 2147483648 then
            return string.format("%d", v)
        end
        return string.format("%.10g", v)
    elseif t == "string" then
        return "\"" .. v:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\n", "\\n") .. "\""
    elseif t == "table" then
        if v._type then
            return enc("element:" .. v._type)
        end
        if next(v) == nil then
            return "[]"
        end
        local parts = {}
        if isArray(v) then
            for i = 1, #v do
                parts[#parts + 1] = enc(v[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local keys = {}
        for k in pairs(v) do
            keys[#keys + 1] = k
        end
        table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
        for _, k in ipairs(keys) do
            parts[#parts + 1] = enc(tostring(k)) .. ":" .. enc(v[k])
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return enc(tostring(v))
end
Sim.encode = enc

---------------------------------------------------------------------------- MTA globals

root = {_type = "root"}
resourceRoot = {_type = "resource"}
localPlayer = {_type = "player", x = 10, y = 20, z = 30, frozen = false, col = true}
source = nil

function outputDebugString(text, level)
    Sim.calls[#Sim.calls + 1] = {"debug", text}
end

function addEventHandler(name, el, fn)
    Sim.handlers[name] = Sim.handlers[name] or {}
    table.insert(Sim.handlers[name], fn)
    return true
end

function Sim.fire(name, src, ...)
    for _, fn in ipairs(Sim.handlers[name] or {}) do
        source = src
        fn(...)
    end
    source = nil
end

function getTickCount()
    return Sim.now
end

function isElement(e)
    return type(e) == "table" and e._type ~= nil and not e.dead
end

function destroyElement(e)
    if isElement(e) then
        e.dead = true
        if e._type == "texture" then
            Sim.va = Sim.va - Sim.texMiB * 1048576
        elseif e._type == "dff" then
            Sim.va = Sim.va - Sim.dffMiB * 1048576
        end
        return true
    end
    return false
end

local function newEl(kind, x, y, z)
    Sim.nextN = Sim.nextN + 1
    local e = {_type = kind, n = Sim.nextN, x = x or 0, y = y or 0, z = z or 0, ctl = {}}
    Sim.els[#Sim.els + 1] = e
    return e
end

function createVehicle(model, x, y, z, rx, ry, rz)
    local e = newEl("vehicle", x, y, z)
    e.model = model
    return e
end

function createPed(model, x, y, z, rot)
    local e = newEl("ped", x, y, z)
    e.model, e.rot = model, rot
    return e
end

function getElementsByType(kind, parent, streamed)
    local out = {}
    for _, e in ipairs(Sim.els) do
        if e._type == kind and not e.dead then
            out[#out + 1] = e
        end
    end
    return out
end

function getElementPosition(e)
    return e.x, e.y, e.z
end

function setElementPosition(e, x, y, z)
    e.x, e.y, e.z = x, y, z
    return true
end

function setElementFrozen(e, f)
    e.frozen = f
    return true
end

function isElementFrozen(e)
    return e.frozen == true
end

function setElementCollisionsEnabled(e, c)
    e.col = c
    return true
end

function getPedOccupiedVehicle(p)
    return nil
end

function setPedControlState(ped, ctl, state)
    ped.ctl[ctl] = state
    return true
end

function setPedRotation(ped, rot)
    ped.rot = rot
    return true
end

function giveWeapon(ped, w, ammo, current)
    ped.weapon = w
    return true
end

function setPedWeaponSlot(ped, s)
    return true
end

function setPedAimTarget(ped, x, y, z)
    ped.aim = {x, y, z}
    return true
end

function setCameraMatrix(px, py, pz, lx, ly, lz, roll, fov)
    Sim.camera = {px, py, pz, lx, ly, lz, fov}
    return true
end

function setCameraTarget(el)
    Sim.camera = nil
    return true
end

function getTime()
    return Sim.hour, Sim.min
end

function setTime(h, m)
    Sim.hour, Sim.min = h, m
    return true
end

function setMinuteDuration(ms)
    Sim.minute = ms
    return true
end

function getMinuteDuration()
    return Sim.minute
end

function setWeather(w)
    Sim.weather = w
    return true
end

function getWeather()
    return Sim.weather
end

function setFPSLimit(n)
    if n > 100 then
        return false
    end
    Sim.fps = n
    return true
end

function getFPSLimit()
    return Sim.fps
end

function guiGetScreenSize()
    return 1280, 720
end

function getGroundPosition(x, y, z)
    return Sim.ground
end

function getProcessMemoryStats()
    return {virtual = Sim.va, resident = Sim.va * 0.4, private = Sim.va * 0.6}
end

function dxGetStatus()
    return {VideoCardName = "Sim GPU", VideoCardRAM = 8192, VideoMemoryFreeForMTA = 4000, AllowScreenUpload = true}
end

function engineStreamingGetUsedMemory()
    return 200 * 1048576 + Sim.frame * 100
end

function dxCreateTexture(w, h, fmt)
    local count = 0
    for _, e in ipairs(Sim.els) do
        if e._type == "texture" and not e.dead then
            count = count + 1
        end
    end
    if Sim.maxTex and count >= Sim.maxTex then
        return false
    end
    local e = newEl("texture")
    Sim.va = Sim.va + Sim.texMiB * 1048576
    return e
end

function engineLoadDFF(path)
    if not Sim.dffOk then
        return false
    end
    local e = newEl("dff")
    Sim.va = Sim.va + Sim.dffMiB * 1048576
    return e
end

function Sim.enableSae()
    Sim.sae = true
    function getEngineStats()
        return {avgFps = 60, p50 = 16.7, gameTimeMs = Sim.gameMs, frameCounter = Sim.frameCounter, timerMode = Sim.mode,
            vaGuard = Sim.guard or "ok", cefLoaded = false, vaUsedMiB = Sim.va / 1048576}
    end
    function getEngineLimits()
        return {capacity = {visibleEntities = 1000, lodList = 4000},
            counters = {visibleEntities = 200 + (Sim.frame % 50), lodList = 100}, coronas = {native = 12, script = 3}}
    end
    function getDrawDistanceInfo()
        return {preset = "classic", farClip = 1000}
    end
    function getEnginePatchReport()
        return {profile = "sae", preset = "classic", safeMode = false}
    end
    function getEngineSettings()
        return {sae_limits = {value = "sae", source = "custom"}}
    end
end

---------------------------------------------------------------------------- stepping

--- Advance one frame of `slice` ms: the clock, the game clock (ratio Sim.timerRatio), the frame counter,
--- weapon fire of peds that aim and fire, and the onClientPreRender event.
function Sim.step(slice)
    slice = slice or 16
    Sim.now = Sim.now + slice
    Sim.frame = Sim.frame + 1
    Sim.gameMs = Sim.gameMs + slice * Sim.timerRatio
    Sim.frameCounter = Sim.frameCounter + slice * 30 / 1000
    for _, e in ipairs(Sim.els) do
        if e._type == "ped" and not e.dead and e.ctl.fire and e.ctl.aim_weapon and e.weapon then
            local last = Sim.lastFire[e] or Sim.now
            if Sim.now - last >= Sim.fireEvery then
                Sim.lastFire[e] = Sim.now
                Sim.fire("onClientPedWeaponFire", e, e.weapon, 99, 50, 0, 0, 0)
            elseif not Sim.lastFire[e] then
                Sim.lastFire[e] = Sim.now
            end
        end
    end
    Sim.fire("onClientPreRender", root, slice)
end

function Sim.run(frames, slice)
    for i = 1, frames do
        Sim.step(type(slice) == "function" and slice(i) or slice)
    end
end

function Sim.count(kind)
    local n = 0
    for _, e in ipairs(Sim.els) do
        if e._type == kind and not e.dead then
            n = n + 1
        end
    end
    return n
end
