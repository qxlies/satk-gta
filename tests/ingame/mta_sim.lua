-- A small simulated MTA for the satk-testdrive tests: a server side and one client side in one Lua
-- state, each with its own globals, events between them, timers, a frame clock and toy physics
-- (vehicles accelerate, steer, brake and settle on a flat ground; peds walk; frozen objects block
-- both by their bounding box; armed peds fire). Not MTA: only what satk-testdrive calls, with the
-- argument conventions of the MTA wiki. Test knobs live in Sim (vmax, components, dummies, stretch).

Sim = {
    clock = 100000, dt = 20, frame = 0, timers = {}, queue = {}, els = {}, nextN = 0, debug = {},
    groundZ = 12.55, files = {}, replaced = {}, requested = {}, nextModel = 20000, nextTxd = 5000, lod = {},
    vmax = {}, components = {}, dummies = {}, stretch = {}, noWheels = {}, softCol = {}, shots = 0,
    manifest = nil, contentState = "running", restarts = 0, failLoad = {}, wheelSize = {},
}

local function newEnv()
    local env = setmetatable({}, {__index = _G})
    env._G = env
    return env
end
Sim.server = newEnv()
Sim.client = newEnv()

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
        return "\"" .. v:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t") .. "\""
    elseif t == "table" then
        if v._type then
            return enc("element:" .. v._type .. ":" .. tostring(v._n))
        end
        local parts = {}
        if next(v) == nil then
            return "[]"
        end
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

--- MTA copies tables that cross resources and the network (elements keep their identity).
local function copy(v, seen)
    if type(v) ~= "table" or v._type then
        return v
    end
    seen = seen or {}
    if seen[v] then
        return seen[v]
    end
    local out = {}
    seen[v] = out
    for k, x in pairs(v) do
        out[copy(k, seen)] = copy(x, seen)
    end
    return out
end
Sim.copy = copy

---------------------------------------------------------------------------- elements

local function element(kind, props)
    Sim.nextN = Sim.nextN + 1
    local e = props or {}
    e._type = kind
    e._n = Sim.nextN
    e.data = e.data or {}
    e.pos = e.pos or {0, 0, 0}
    e.rot = e.rot or {0, 0, 0}
    e.speed = 0
    e.vz = 0
    e.alpha = 255
    e.alive = true
    e.controls = {}
    e.analog = {}
    Sim.els[#Sim.els + 1] = e
    return e
end
Sim.element = element

Sim.root = element("root")
Sim.resourceRoot = element("resource-root")
Sim.player = element("player", {name = "tester", pos = {2494.0, -1676.0, 13.3}, model = 0, health = 100, dead = false})
Sim.resource = {name = "satk-testdrive", _res = true}
Sim.contentRes = {name = "satk-testdrive-mod", _res = true}

local function alive(e)
    return type(e) == "table" and e._type ~= nil and e.alive
end

local function rad(d)
    return math.rad(d or 0)
end

local function fwd(h)
    return -math.sin(rad(h)), math.cos(rad(h))
end

local BBOX = {
    [8650] = {-0.4, -15.25, -1.12, 0.41, 15.25, 1.12},
    [1337] = {-0.5, -0.5, -0.6, 0.5, 0.5, 0.6},
}
local VEH_BBOX = {-1.1, -2.6, -0.9, 1.1, 2.6, 0.9}

local function modelBase(id)
    local r = Sim.requested[id]
    return r and r.parent or id
end

local function bboxOf(e)
    if e._type == "vehicle" then
        return VEH_BBOX
    end
    if e._type == "ped" or e._type == "player" then
        return {-0.4, -0.4, -1.0, 0.4, 0.4, 0.9}
    end
    local id = e.model
    local file = Sim.replaced[id]
    if file and Sim.softCol[file] then
        return {0, 0, 0, 0, 0, 0}
    end
    return BBOX[modelBase(id)] or {-1, -1, -1, 1, 1, 1}
end

--- Would a body at (x, y, z) be inside a frozen object (other than itself)?
local function blocked(self, x, y, z)
    for _, o in ipairs(Sim.els) do
        if o.alive and o._type == "object" and o ~= self and not Sim.softCol[Sim.replaced[o.model] or ""] then
            local b = bboxOf(o)
            local h = o.rot[3]
            local fx, fy = fwd(h)
            local rx, ry = math.cos(rad(h)), math.sin(rad(h))
            local dx, dy, dz = x - o.pos[1], y - o.pos[2], z - o.pos[3]
            local lx, ly = dx * rx + dy * ry, dx * fx + dy * fy
            local m = 0.9
            if lx > b[1] - m and lx < b[4] + m and ly > b[2] - m and ly < b[5] + m and dz > b[3] - 1.5 and dz < b[6] + 1.5 then
                return true
            end
        end
    end
    return false
end

---------------------------------------------------------------------------- events and timers

local function handlers(env)
    env._handlers = env._handlers or {}
    return env._handlers
end

local function matches(attached, src)
    if attached == Sim.root or attached == src then
        return true
    end
    return false
end

local function fire(env, name, src, extra, ...)
    local list = handlers(env)[name]
    if not list then
        return
    end
    for _, h in ipairs({unpack(list)}) do
        if not h.removed and matches(h.el, src) then
            env.source = src
            if extra then
                env.client = extra
            end
            h.fn(...)
            env.client = nil
        end
    end
end
Sim.fire = fire

local function deliver()
    local q = Sim.queue
    Sim.queue = {}
    for _, item in ipairs(q) do
        fire(item.env, item.name, item.src, item.client, unpack(item.args, 1, item.n))
    end
end
Sim.deliver = deliver

local function commonApi(env, side)
    env.root = Sim.root
    env.resourceRoot = Sim.resourceRoot
    env.resource = Sim.resource
    env.addEvent = function() return true end
    env.addEventHandler = function(name, el, fn)
        local h = handlers(env)
        h[name] = h[name] or {}
        table.insert(h[name], {el = el, fn = fn})
        return true
    end
    env.removeEventHandler = function(name, el, fn)
        for _, h in ipairs(handlers(env)[name] or {}) do
            if h.fn == fn and h.el == el then
                h.removed = true
            end
        end
        return true
    end
    env.getTickCount = function() return Sim.clock end
    env.outputDebugString = function(text, level)
        table.insert(Sim.debug, {side = side, level = level or 3, msg = tostring(text)})
        return true
    end
    env.setTimer = function(fn, ms, times)
        table.insert(Sim.timers, {fn = fn, at = Sim.clock + math.max(ms, 1), ms = math.max(ms, 1), times = times or 1})
        return {}
    end
    env.isElement = alive
    env.getElementType = function(e) return e._type end
    env.destroyElement = function(e)
        if not alive(e) then
            return false
        end
        e.alive = false
        if e._type == "dff" then
            for id, f in pairs(Sim.replaced) do
                if f == e.file then
                    Sim.replaced[id] = nil
                end
            end
        end
        if e.vehicle then
            e.vehicle.driver = nil
        end
        if e.driver then
            e.driver.vehicle = nil
        end
        return true
    end
    env.getElementPosition = function(e) return e.pos[1], e.pos[2], e.pos[3] end
    env.getElementRotation = function(e) return e.rot[1], e.rot[2], e.rot[3] end
    env.setElementPosition = function(e, x, y, z)
        e.pos = {x, y, z}
        return true
    end
    env.setElementRotation = function(e, rx, ry, rz)
        e.rot = {rx or 0, ry or 0, (rz or 0) % 360}
        return true
    end
    env.getElementVelocity = function(e)
        local fx, fy = fwd(e.rot[3])
        local v = (e.speed or 0) / 50
        return fx * v, fy * v, (e.vz or 0) / 50
    end
    env.setElementVelocity = function(e, vx, vy, vz)
        e.speed = math.sqrt(vx * vx + vy * vy) * 50
        e.vz = (vz or 0) * 50
        return true
    end
    env.getElementData = function(e, k) return e.data[k] end
    env.setElementData = function(e, k, v)
        local old = e.data[k]
        e.data[k] = v
        if side == "server" then
            table.insert(Sim.queue, {env = Sim.client, name = "onClientElementDataChange", src = e, n = 3,
                                     args = {k, old, v}})
        end
        return true
    end
    env.getElementModel = function(e) return e.model end
    env.setElementModel = function(e, m)
        e.model = m
        return true
    end
    env.getElementID = function(e) return e.id or "" end
    env.setElementID = function(e, id)
        e.id = id
        return true
    end
    env.getElementByID = function(id)
        for _, e in ipairs(Sim.els) do
            if e.alive and e.id == id then
                return e
            end
        end
        return false
    end
    env.getElementsByType = function(t)
        local out = {}
        for _, e in ipairs(Sim.els) do
            if e.alive and e._type == t then
                out[#out + 1] = e
            end
        end
        return out
    end
    env.getElementHealth = function(e) return e.health or 1000 end
    env.setElementHealth = function(e, h)
        e.health = h
        return true
    end
    env.setElementFrozen = function(e, f)
        e.frozen = f and true or false
        return true
    end
    env.getPedOccupiedVehicle = function(p) return p.vehicle or false end
    env.warpPedIntoVehicle = function(p, v)
        p.vehicle = v
        v.driver = p
        p.pos = {v.pos[1], v.pos[2], v.pos[3]}
        return true
    end
    env.getResourceFromName = function(name)
        if name == "satk-testdrive" then
            return Sim.resource
        end
        if name == "satk-testdrive-mod" and Sim.contentState == "running" then
            return Sim.contentRes
        end
        return false
    end
    env.getResourceName = function(r) return r.name end
    env.getTime = function() return Sim.hour or 12, Sim.minute or 0 end
    env.setTime = function(h, m)
        Sim.hour, Sim.minute = h, m
        return true
    end
    env.getWeather = function() return Sim.weather or 0 end
    env.setWeather = function(w)
        Sim.weather = w
        return true
    end
    env.createVehicle = function(model, x, y, z, rx, ry, rz)
        if type(model) ~= "number" then
            return false
        end
        local v = element("vehicle", {model = model, pos = {x, y, z}, rot = {rx or 0, ry or 0, (rz or 0) % 360},
                                      health = 1000, panels = {}, doors = {}, lights = {}, handling = {}})
        return v
    end
    env.createPed = function(model, x, y, z, rot)
        return element("ped", {model = model, pos = {x, y, z}, rot = {0, 0, (rot or 0) % 360}, health = 100})
    end
    env.createObject = function(model, x, y, z, rx, ry, rz)
        return element("object", {model = model, pos = {x, y, z}, rot = {rx or 0, ry or 0, (rz or 0) % 360}})
    end
    env.setVehicleHandling = function(v, prop, value)
        if v._type ~= "vehicle" then
            return false
        end
        v.handling[prop] = value
        return true
    end
    env.getVehicleHandling = function(v)
        return {mass = 1500, maxVelocity = v.handling.maxVelocity or 200, engineAcceleration = 10, driveType = "rwd",
                engineType = "petrol", ABS = false}
    end
    env.fixVehicle = function(v)
        v.health = 1000
        v.panels, v.doors = {}, {}
        return true
    end
    env.setVehicleDamageProof = function(v, on)
        v.proof = on
        return true
    end
    env.isVehicleDamageProof = function(v) return v.proof == true end
    env.setVehiclePanelState = function(v, p, s)
        v.panels[p] = s
        return true
    end
    env.getVehiclePanelState = function(v, p) return v.panels[p] or 0 end
    env.setVehicleDoorState = function(v, d, s)
        v.doors[d] = s
        return true
    end
    env.getVehicleDoorState = function(v, d) return v.doors[d] or 0 end
    env.setVehicleLightState = function(v, l, s)
        v.lights[l] = s
        return true
    end
    env.setVehicleOverrideLights = function(v, s)
        v.override = s
        return true
    end
    env.getVehicleOverrideLights = function(v) return v.override or 0 end
    env.setVehicleColor = function() return true end
end

---------------------------------------------------------------------------- server API

do
    local S = Sim.server
    commonApi(S, "server")
    S.triggerClientEvent = function(target, name, src, ...)
        table.insert(Sim.queue, {env = Sim.client, name = name, src = src, n = select("#", ...), args = copy({...})})
        return true
    end
    S.getResourceState = function(r)
        if r == Sim.contentRes then
            return Sim.contentState
        end
        return "running"
    end
    S.call = function(r, fn, ...)
        if r == Sim.contentRes and fn == "manifest" then
            return copy(Sim.manifest)
        end
        error("call: no function " .. tostring(fn))
    end
    S.restartResource = function(r)
        Sim.restarts = Sim.restarts + 1
        Sim.restartContent()
        return true
    end
    S.startResource = function(r)
        Sim.restartContent()
        return true
    end
    S.refreshResources = function() return true end
    S.getPlayerName = function(p) return p.name end
    S.isPedDead = function(p) return p.dead == true end
    S.spawnPlayer = function(p, x, y, z, rot)
        p.pos = {x, y, z}
        p.rot = {0, 0, rot or 0}
        p.dead = false
        return true
    end
    S.fadeCamera = function() return true end
    S.setCameraTarget = function() return true end
    S.outputChatBox = function(text) table.insert(Sim.debug, {side = "chat", msg = text}) return true end
    S.setGameType = function() return true end
    S.setMapName = function() return true end
    S.giveWeapon = function(p, w)
        p.weapon = w
        return true
    end
    S.addCommandHandler = function() return true end
end

---------------------------------------------------------------------------- client API

do
    local C = Sim.client
    commonApi(C, "client")
    C.localPlayer = Sim.player
    C.triggerServerEvent = function(name, src, ...)
        table.insert(Sim.queue, {env = Sim.server, name = name, src = src, client = Sim.player, n = select("#", ...),
                                 args = copy({...})})
        return true
    end
    C.guiGetScreenSize = function() return 1280, 720 end
    C.dxDrawText = function() return true end
    C.tocolor = function() return 0 end
    C.isElementStreamedIn = function(e) return alive(e) end
    C.getElementRadius = function(e)
        local b = bboxOf(e)
        return math.max(b[4] - b[1], b[5] - b[2], b[6] - b[3]) / 2
    end
    C.getElementBoundingBox = function(e)
        local b = bboxOf(e)
        return b[1], b[2], b[3], b[4], b[5], b[6]
    end
    C.getElementAlpha = function(e) return e.alpha end
    C.setElementAlpha = function(e, a)
        e.alpha = a
        return true
    end
    C.getGroundPosition = function() return Sim.groundZ end
    C.enginePreloadWorldArea = function() return true end
    C.getMinuteDuration = function() return Sim.minuteDuration or 1000 end
    C.setMinuteDuration = function(ms)
        Sim.minuteDuration = ms
        return true
    end
    C.toggleAllControls = function(on)
        Sim.controls = on
        return true
    end
    C.setCameraMatrix = function(px, py, pz, lx, ly, lz, roll, fov)
        Sim.camera = {px, py, pz, lx, ly, lz, fov}
        return true
    end
    C.setCameraTarget = function(t)
        Sim.camera = {target = t}
        return true
    end
    C.setPedControlState = function(p, c, s)
        p.controls[c] = s and true or false
        return true
    end
    C.setPedAnalogControlState = function(p, c, s)
        p.analog[c] = s
        return true
    end
    C.getElementAngularVelocity = function() return 0, 0, 0 end
    C.setElementAngularVelocity = function() return true end
    C.isVehicleBlown = function(v) return (v.health or 1000) <= 0 end
    C.setVehicleEngineState = function(v, on)
        v.engine = on
        return true
    end
    C.isVehicleWheelOnGround = function(v, w)
        if Sim.noWheels[Sim.replaced[v.model] or ""] then
            return false
        end
        return math.abs(v.pos[3] - (Sim.groundZ + 1.0)) < 0.2
    end
    C.isVehicleOnGround = function(v) return math.abs(v.pos[3] - (Sim.groundZ + 1.0)) < 0.2 end
    C.getVehicleComponents = function(v)
        local file = Sim.replaced[v.model]
        local list = (file and Sim.components[file]) or Sim.components.vanilla
        local out = {}
        for _, n in ipairs(list) do
            out[n] = true
        end
        return out
    end
    C.getVehicleModelDummyPosition = function(id, name)
        local file = Sim.replaced[id]
        local d = ((file and Sim.dummies[file]) or Sim.dummies.vanilla)[name]
        if not d then
            return false
        end
        return d[1], d[2], d[3]
    end
    C.setVehicleDirtLevel = function(v, l)
        v.dirt = l
        return true
    end
    C.setVehicleModelWheelSize = function(id, axle, size)
        Sim.wheelLog = Sim.wheelLog or {}
        table.insert(Sim.wheelLog, {id, axle, size})
        Sim.wheelSize[id .. axle] = size
        return true
    end
    C.getVehicleModelWheelSize = function(id, axle) return Sim.wheelSize[id .. axle] or 0.7 end
    C.engineGetModelLODDistance = function(id) return Sim.lod[id] or 150 end
    C.engineSetModelLODDistance = function(id, d)
        Sim.lod[id] = d
        return true
    end
    C.setPedAnimation = function(p, block, anim)
        p.anim = block and {block, anim} or nil
        p.animT = Sim.clock
        return true
    end
    C.getPedAnimation = function(p)
        if p.anim then
            return p.anim[1], p.anim[2]
        end
        return false
    end
    local BONES = {
        [2] = {0, 0, 0}, [3] = {0, 0, 0.15}, [4] = {0, 0, 0.4}, [5] = {0, 0, 0.6}, [8] = {0, 0, 0.75},
        [22] = {0.2, 0, 0.5}, [23] = {0.45, 0, 0.5}, [24] = {0.7, 0, 0.5}, [25] = {0.8, 0, 0.5},
        [32] = {-0.2, 0, 0.5}, [33] = {-0.45, 0, 0.5}, [34] = {-0.7, 0, 0.5},
        [41] = {-0.1, 0, -0.05}, [42] = {-0.1, 0, -0.5}, [43] = {-0.1, 0, -0.95},
        [51] = {0.1, 0, -0.05}, [52] = {0.1, 0, -0.5}, [53] = {0.1, 0, -0.95},
    }
    C.getPedBonePosition = function(p, b)
        local o = BONES[b]
        if not o then
            return false
        end
        local t = (Sim.clock - (p.animT or 0)) / 1000
        local a = p.anim and 0.3 * math.sin(t * 4) or 0
        local x, z = o[1] * math.cos(a) - o[3] * math.sin(a), o[1] * math.sin(a) + o[3] * math.cos(a)
        local s = 1
        if Sim.stretch[Sim.replaced[p.model] or ""] and b == 23 then
            s = 1 + 0.25 * math.sin(t * 6)
        end
        return p.pos[1] + x * s, p.pos[2] + o[2], p.pos[3] + z * s
    end
    C.givePedWeapon = function(p, w)
        p.weapon = w
        return true
    end
    C.getPedWeapon = function(p) return p.weapon or 0 end
    C.setPedAimTarget = function(p, x, y, z)
        p.aim = {x, y, z}
        return true
    end
    C.getPedWeaponMuzzlePosition = function(p)
        if not p.weapon then
            return false
        end
        local off = 0.25
        if Sim.replaced[(Sim.weaponModel or {})[p.weapon] or -1] then
            off = Sim.muzzleOffset or 0.25
        end
        return p.pos[1] + 0.7 + off, p.pos[2], p.pos[3] + 0.5
    end
    -- engine models
    C.engineRequestModel = function(kind, parent)
        Sim.nextModel = Sim.nextModel + 1
        Sim.requested[Sim.nextModel] = {kind = kind, parent = parent}
        return Sim.nextModel
    end
    local function loader(kind)
        return function(path)
            if not Sim.files[path] or Sim.failLoad[path] then
                return false
            end
            return element(kind, {file = path})
        end
    end
    C.engineLoadDFF = loader("dff")
    C.engineLoadTXD = loader("txd")
    C.engineLoadCOL = loader("col")
    C.engineReplaceModel = function(dff, id)
        Sim.replaced[id] = dff.file
        return true
    end
    C.engineImportTXD = function() return true end
    C.engineReplaceCOL = function() return true end
end

---------------------------------------------------------------------------- loading and running

function Sim.load(env, code, name)
    local fn, err = loadstring(code, "@" .. name)
    if not fn then
        error(err, 0)
    end
    setfenv(fn, env)
    fn()
end

--- The server restarts the content resource (stop, then start with the current manifest).
function Sim.restartContent()
    Sim.contentState = "loaded"
    fire(Sim.server, "onResourceStop", Sim.contentRes, nil, Sim.contentRes)
    table.insert(Sim.queue, {env = Sim.client, name = "onClientResourceStop", src = Sim.root, n = 1,
                             args = {Sim.contentRes}})
    Sim.contentState = "running"
    fire(Sim.server, "onResourceStart", Sim.contentRes, nil, Sim.contentRes)
    table.insert(Sim.queue, {env = Sim.client, name = "onClientResourceStart", src = Sim.root, n = 1,
                             args = {Sim.contentRes}})
end

function Sim.startServer()
    fire(Sim.server, "onResourceStart", Sim.resource, nil, Sim.resource)
    fire(Sim.server, "onResourceStart", Sim.contentRes, nil, Sim.contentRes)
    fire(Sim.server, "onPlayerJoin", Sim.player)
end

function Sim.startClient()
    fire(Sim.client, "onClientResourceStart", Sim.root, nil, Sim.resource)
    fire(Sim.client, "onClientResourceStart", Sim.root, nil, Sim.contentRes)
end

local function physics(dt)
    for _, e in ipairs(Sim.els) do
        if e.alive and not e.frozen and e._type == "vehicle" then
            local d = e.driver
            local vmax = (e.handling.maxVelocity or Sim.vmax[Sim.replaced[e.model] or "vanilla"] or 200) / 3.6 * 0.92
            if d and d.alive then
                if d.controls.accelerate then
                    e.speed = math.min(vmax, e.speed + 7 * dt * math.max(0.2, 1 - e.speed / vmax))
                elseif d.controls.brake_reverse then
                    e.speed = math.max(0, e.speed - 9 * dt)
                end
                local steer = (d.analog.vehicle_left or 0) - (d.analog.vehicle_right or 0)
                e.rot[3] = (e.rot[3] + steer * 25 * dt) % 360
            end
            local fx, fy = fwd(e.rot[3])
            local nx, ny = e.pos[1] + fx * e.speed * dt, e.pos[2] + fy * e.speed * dt
            if e.speed > 0 and blocked(e, nx + fx * 2.6, ny + fy * 2.6, e.pos[3]) then
                e.speed = 0
                e.health = (e.health or 1000) - 120
                e.panels[5] = 2
                e.panels[0] = 1
            else
                e.pos[1], e.pos[2] = nx, ny
            end
            local target = Sim.groundZ + 1.0
            e.pos[3] = e.pos[3] + (target - e.pos[3]) * 0.12
            if d then
                d.pos = {e.pos[1], e.pos[2], e.pos[3]}
            end
        elseif e.alive and not e.frozen and e._type == "ped" and not e.vehicle then
            if e.controls.forwards then
                local fx, fy = fwd(e.rot[3])
                local nx, ny = e.pos[1] + fx * 1.4 * dt, e.pos[2] + fy * 1.4 * dt
                if not blocked(e, nx + fx * 0.4, ny + fy * 0.4, e.pos[3]) then
                    e.pos[1], e.pos[2] = nx, ny
                end
            end
            if e.weapon and e.controls.aim_weapon and e.controls.fire then
                e.fireT = (e.fireT or 0) + dt
                if e.fireT >= 0.2 then
                    e.fireT = 0
                    fire(Sim.client, "onClientPedWeaponFire", e, nil, e.weapon, 10, 5)
                end
            end
        end
    end
end

function Sim.pump(n)
    for _ = 1, (n or 1) do
        Sim.clock = Sim.clock + Sim.dt
        Sim.frame = Sim.frame + 1
        physics(Sim.dt / 1000)
        local due = {}
        for i = #Sim.timers, 1, -1 do
            local t = Sim.timers[i]
            if Sim.clock >= t.at then
                due[#due + 1] = t
                t.times = t.times - 1
                if t.times == 0 then
                    table.remove(Sim.timers, i)
                else
                    t.at = Sim.clock + t.ms
                end
            end
        end
        for _, t in ipairs(due) do
            t.fn()
        end
        deliver()
        fire(Sim.client, "onClientRender", Sim.root)
    end
end
