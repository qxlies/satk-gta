-- A simulated MTA for the satk-agent tests: one Lua state, a server side and a client side, each
-- with its own environment of stub MTA functions, events between them and a pinhole camera.
-- Not MTA: only what satk-agent calls, with the semantics documented on the MTA wiki
-- (camera changes show up one frame later, latent events, resource-relative files).

Sim = {
    clock = 1000000, frame = 0, queue = {}, timers = {}, W = 1280, H = 720, K = 1.15,
    resourceDir = nil, token = nil, clientStarted = false, shutdownRequested = false,
    screenUpload = true, lastSection = nil, preloads = 0, log = {},
}

local function newEnv()
    local env = {}
    setmetatable(env, {__index = _G})
    env._G = env
    return env
end

Sim.server = newEnv()
Sim.client = newEnv()

---------------------------------------------------------------------------- JSON (MTA-like)

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
        return string.format("%.17g", v)
    elseif t == "string" then
        return "\"" .. v:gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t") .. "\""
    elseif t == "table" then
        local parts = {}
        if isArray(v) then
            for i = 1, #v do
                parts[#parts + 1] = enc(v[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        local keys = {}
        for k in pairs(v) do
            keys[#keys + 1] = tostring(k)
        end
        table.sort(keys)
        for _, k in ipairs(keys) do
            local val = v[k]
            if val == nil then
                val = v[tonumber(k)]
            end
            parts[#parts + 1] = enc(k) .. ":" .. enc(val)
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return "\"" .. tostring(v) .. "\""
end
Sim.encode = enc

local function toJSON(v, compact)
    if type(v) == "function" or type(v) == "thread" then
        return nil
    end
    return "[" .. enc(v) .. "]"
end
Sim.server.toJSON = toJSON
Sim.client.toJSON = toJSON

---------------------------------------------------------------------------- elements and events

local function element(kind, props)
    local e = props or {}
    e._type = kind
    return e
end

Sim.root = element("root")
Sim.resourceRoot = element("resource")
Sim.consoleEl = element("console")
Sim.player = element("player", {name = "satk", health = 0, dead = true, alpha = 255})
Sim.players = {}

local function handlers(side)
    side._handlers = side._handlers or {}
    return side._handlers
end

local function addEvents(side, sideName)
    side.addEvent = function(name, remote)
        return true
    end
    side.addEventHandler = function(name, attachedTo, fn)
        local h = handlers(side)
        h[name] = h[name] or {}
        table.insert(h[name], {el = attachedTo, fn = fn})
        return true
    end
    side.root = Sim.root
    side.resourceRoot = Sim.resourceRoot
    side.resource = {name = "satk-agent"}
    side.isElement = function(e)
        return type(e) == "table" and e._type ~= nil and not e._destroyed
    end
    side.destroyElement = function(e)
        e._destroyed = true
        return true
    end
    side.getElementType = function(e)
        return e._type
    end
    side.getTickCount = function()
        return Sim.clock
    end
    side.getVersion = function()
        return {sortable = "1.7.0-9.00000.0", mta = "1.7.0"}
    end
    side.setTimer = function(fn, ms, times, ...)
        local t = {fn = fn, ms = ms, times = times, left = times, due = Sim.clock + ms, args = {...}, side = sideName}
        table.insert(Sim.timers, t)
        return t
    end
    side.outputServerLog = function(msg)
        table.insert(Sim.log, msg)
    end
    side.outputDebugString = function(msg, level)
        table.insert(Sim.log, msg)
    end
    side.getResourceName = function(r)
        return "satk-agent"
    end
end

addEvents(Sim.server, "server")
addEvents(Sim.client, "client")

function Sim.fire(side, name, source, extra, ...)
    local list = handlers(side)[name] or {}
    for _, h in ipairs(list) do
        side.source = source
        side.client = extra
        local ok, err = pcall(h.fn, ...)
        side.client = nil
        if not ok then
            table.insert(Sim.log, "handler " .. name .. ": " .. tostring(err))
            if handlers(side)["onDebugMessage"] and side == Sim.server then
                for _, d in ipairs(handlers(side)["onDebugMessage"]) do
                    pcall(d.fn, tostring(err), 1, "sim", 0)
                end
            end
        end
    end
end

local function enqueue(to, name, source, extra, args)
    table.insert(Sim.queue, {to = to, name = name, source = source, extra = extra, args = args})
end

-- server -> client
Sim.server.triggerClientEvent = function(player, name, source, ...)
    if not Sim.clientStarted then
        return false
    end
    enqueue(Sim.client, name, source, nil, {...})
    return true
end
-- client -> server (client = the local player)
Sim.client.triggerServerEvent = function(name, source, ...)
    enqueue(Sim.server, name, source, Sim.player, {...})
    return true
end
Sim.client.triggerLatentServerEvent = function(name, bps, persist, source, ...)
    enqueue(Sim.server, name, source, Sim.player, {...})
    return true
end

---------------------------------------------------------------------------- server stubs

local S = Sim.server
S.get = function(name)
    if name == "@token" then
        return Sim.token
    end
    return false
end
S.getServerName = function() return "sim" end
S.getServerPort = function() return 22003 end
S.getServerHttpPort = function() return 22005 end
S.getElementsByType = function(t)
    if t == "player" then
        return Sim.players
    elseif t == "console" then
        return {Sim.consoleEl}
    end
    return {}
end
S.getPlayerName = function(p) return p.name end
S.isPedDead = function(p) return p.dead end
S.getElementHealth = function(p) return p.health end
S.spawnPlayer = function(p, x, y, z)
    p.dead, p.health, p.pos = false, 100, {x, y, z}
    enqueue(Sim.client, "onClientPlayerSpawn", Sim.player, nil, {})
    return true
end
S.fadeCamera = function() return true end
S.setCameraTarget = function() return true end
Sim.time = {12, 0}
Sim.weather = {0, false}
Sim.minuteDuration = 1000
S.getTime = function() return Sim.time[1], Sim.time[2] end
S.setTime = function(h, m) Sim.time = {h, m} return true end
S.getWeather = function() return Sim.weather[1], Sim.weather[2] end
S.setWeather = function(w) Sim.weather = {w, false} return true end
S.setWeatherBlended = function(w) Sim.weather[2] = w return true end
S.getMinuteDuration = function() return Sim.minuteDuration end
S.setMinuteDuration = function(d) Sim.minuteDuration = d return true end
S.executeCommandHandler = function(cmd, el, args)
    return cmd == "simcmd"
end
S.shutdown = function(reason)
    Sim.shutdownRequested = reason or true
    return true
end
local function rpath(p)
    return Sim.resourceDir .. "/" .. p
end
S.fileExists = function(p)
    local f = io.open(rpath(p), "rb")
    if f then f:close() return true end
    return false
end
S.fileDelete = function(p) return os.remove(rpath(p)) ~= nil end
S.fileCreate = function(p)
    local f = io.open(rpath(p), "wb")
    if not f then return false end
    return {f = f}
end
S.fileWrite = function(h, data) h.f:write(data) return #data end
S.fileClose = function(h) h.f:close() return true end

---------------------------------------------------------------------------- client: camera and world

local C = Sim.client
C.localPlayer = Sim.player
Sim.cam = {pos = {0, 0, 100}, look = {0, 50, 0}, roll = 0, fov = 70}
Sim.camApplied = {pos = {0, 0, 100}, look = {0, 50, 0}, roll = 0, fov = 70}
Sim.camTarget = Sim.player
Sim.time = Sim.time
C.guiGetScreenSize = function() return Sim.W, Sim.H end
C.getCameraMatrix = function()
    local c = Sim.camApplied
    return c.pos[1], c.pos[2], c.pos[3], c.look[1], c.look[2], c.look[3], c.roll, c.fov
end
C.setCameraMatrix = function(x, y, z, lx, ly, lz, roll, fov)
    Sim.cam = {pos = {x, y, z}, look = {lx, ly, lz}, roll = roll or 0, fov = fov or 70}
    Sim.camTarget = false
    return true
end
C.getCameraTarget = function() return Sim.camTarget end
C.setCameraTarget = function(el)
    Sim.camTarget = el
    Sim.cam = {pos = {2495, -1695, 16}, look = {2495, -1687, 13.5}, roll = 0, fov = 70}
    return true
end

local function sub(a, b) return {a[1] - b[1], a[2] - b[2], a[3] - b[3]} end
local function add(a, b) return {a[1] + b[1], a[2] + b[2], a[3] + b[3]} end
local function mul(a, s) return {a[1] * s, a[2] * s, a[3] * s} end
local function dot(a, b) return a[1] * b[1] + a[2] * b[2] + a[3] * b[3] end
local function cross(a, b) return {a[2] * b[3] - a[3] * b[2], a[3] * b[1] - a[1] * b[3], a[1] * b[2] - a[2] * b[1]} end
local function nrm(a) local l = math.sqrt(dot(a, a)) return {a[1] / l, a[2] / l, a[3] / l} end
Sim.v = {sub = sub, add = add, mul = mul, dot = dot, cross = cross, nrm = nrm}

--- Rendered half-tangents: tan(hfov/2) = K * tan(fov/2); square pixels.
function Sim.tangents()
    local tx = Sim.K * math.tan(math.rad(Sim.camApplied.fov) / 2)
    return tx, tx * Sim.H / Sim.W
end

C.getWorldFromScreenPosition = function(sx, sy, depth)
    local c = Sim.camApplied
    local f = nrm(sub(c.look, c.pos))
    local up = math.abs(f[3]) < 0.995 and {0, 0, 1} or {0, 1, 0}
    local r = nrm(cross(f, up))
    local u = cross(r, f)
    local tx, ty = Sim.tangents()
    local x = (2 * sx / Sim.W - 1) * tx
    local y = (1 - 2 * sy / Sim.H) * ty
    local d = nrm(add(f, add(mul(r, x), mul(u, y))))
    local p = add(c.pos, mul(d, depth))
    return p[1], p[2], p[3]
end

-- World: ground z = 0 (no building info) and one building box (model 17700).
Sim.box = {lo = {2480, -1680, 0}, hi = {2500, -1660, 20}, model = 17700, pos = {2490, -1670, 12}, lod = 17800}

local function rayBox(o, d, lo, hi)
    local tmin, tmax = 0, math.huge
    for i = 1, 3 do
        if math.abs(d[i]) < 1e-12 then
            if o[i] < lo[i] or o[i] > hi[i] then return nil end
        else
            local t1, t2 = (lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]
            if t1 > t2 then t1, t2 = t2, t1 end
            tmin, tmax = math.max(tmin, t1), math.min(tmax, t2)
            if tmin > tmax then return nil end
        end
    end
    return tmin
end

C.processLineOfSight = function(x1, y1, z1, x2, y2, z2, b, v, p, o, d, see, cam, shoot, ignored, info)
    local o_ = {x1, y1, z1}
    local seg = sub({x2, y2, z2}, o_)
    local len = math.sqrt(dot(seg, seg))
    local dir = mul(seg, 1 / len)
    local tb = rayBox(o_, dir, Sim.box.lo, Sim.box.hi)
    local tg = nil
    if math.abs(dir[3]) > 1e-12 then
        local t = -o_[3] / dir[3]
        if t >= 0 then tg = t end
    end
    if tb and tb <= len and (not tg or tb <= tg) then
        local h = add(o_, mul(dir, tb))
        local bx = Sim.box
        if info then
            return true, h[1], h[2], h[3], nil, 0, 0, 1, 0, 1, 0, bx.model, bx.pos[1], bx.pos[2], bx.pos[3], 0, 0, 0, bx.lod
        end
        return true, h[1], h[2], h[3], nil, 0, 0, 1, 0, 1, 0
    end
    if tg and tg <= len then
        local h = add(o_, mul(dir, tg))
        return true, h[1], h[2], h[3], nil, 0, 0, 1, 0, 1, 0
    end
    return false
end
C.engineGetModelNameFromID = function(id) return id == 17700 and "SimBuilding" or false end
C.enginePreloadWorldArea = function() Sim.preloads = Sim.preloads + 1 return true end
C.getNearClipDistance = function() return 0.3 end
C.getFarClipDistance = function() return 800 end
C.isMTAWindowActive = function() return true end
C.getFPSLimit = function() return 60 end
C.isChatVisible = function() return Sim.chat ~= false end
C.showChat = function(v) Sim.chat = v return true end
Sim.hud = true
C.setPlayerHudComponentVisible = function(c, v) Sim.hud = v return true end
C.getTime = function() return Sim.time[1], Sim.time[2] end
C.setTime = function(h, m) Sim.time = {h, m} return true end
C.getWeather = function() return Sim.weather[1] end
C.setWeather = function(w) Sim.weather = {w, false} return true end
C.setElementAlpha = function(e, a) e.alpha = a return true end
C.setElementFrozen = function(e, f) e.frozen = f return true end
C.setElementCollisionsEnabled = function(e, c) e.collisions = c return true end
C.getElementID = function(e) return "" end
C.getElementPosition = function(e) local p = e.pos or {0, 0, 0} return p[1], p[2], p[3] end
C.getElementModel = function(e) return 0 end
C.dxGetStatus = function() return {AllowScreenUpload = Sim.screenUpload} end
C.dxCreateScreenSource = function(w, h) return element("screensource", {w = w, h = h}) end
C.dxUpdateScreenSource = function(src, now) src.updated = Sim.frame return true end
C.dxCreateRenderTarget = function(w, h, alpha) return element("rendertarget", {w = w, h = h}) end
C.dxSetRenderTarget = function(rt, clear) Sim.rt = rt return true end
C.dxDrawImageSection = function(x, y, w, h, u, v, us, vs, img)
    Sim.lastSection = {x, y, w, h, u, v, us, vs}
    return true
end
C.dxGetTexturePixels = function(tex)
    if not Sim.screenUpload then
        return {w = 32, h = 32}
    end
    return {w = tex.w, h = tex.h}
end
C.dxGetPixelsSize = function(px) return px.w, px.h end
C.dxConvertPixels = function(px, fmt)
    return py_png(px.w, px.h)
end

---------------------------------------------------------------------------- loading and pumping

function Sim.load(side, code, name)
    local fn, err = loadstring(code, "@" .. name)
    if not fn then
        error(err)
    end
    setfenv(fn, side)
    fn()
end

function Sim.startServer()
    Sim.fire(Sim.server, "onResourceStart", Sim.resourceRoot, nil, Sim.resourceRoot)
end

function Sim.startClient()
    Sim.clientStarted = true
    Sim.player.name = "satk"
    table.insert(Sim.players, Sim.player)
    Sim.fire(Sim.server, "onPlayerJoin", Sim.player, nil)
    Sim.fire(Sim.client, "onClientResourceStart", Sim.resourceRoot, nil, Sim.resourceRoot)
end

function Sim.deliver()
    local q = Sim.queue
    Sim.queue = {}
    for _, ev in ipairs(q) do
        Sim.fire(ev.to, ev.name, ev.source, ev.extra, unpack(ev.args))
    end
end

--- One frame: deliver events, run due timers, apply the camera of the last frame, render.
function Sim.pump()
    Sim.clock = Sim.clock + 16
    Sim.deliver()
    for i = #Sim.timers, 1, -1 do
        local t = Sim.timers[i]
        if (t.side == "server" or Sim.clientStarted) and Sim.clock >= t.due then
            pcall(t.fn, unpack(t.args))
            t.due = Sim.clock + t.ms
            if t.times and t.times > 0 then
                t.left = t.left - 1
                if t.left <= 0 then
                    table.remove(Sim.timers, i)
                end
            end
        end
    end
    if Sim.clientStarted then
        Sim.camApplied = Sim.cam
        Sim.frame = Sim.frame + 1
        Sim.fire(Sim.client, "onClientRender", Sim.root, nil)
    end
    Sim.deliver()
end

function Sim.http(req)
    Sim.server.hostname = "127.0.0.1"
    local reply = Sim.server.rpc(req)
    return enc({reply})
end
