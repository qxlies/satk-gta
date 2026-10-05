-- satk-agent server: token-checked HTTP export `rpc`, relay to the agent client, log ring.
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- POST http://127.0.0.1:<httpport>/satk-agent/call/rpc with the JSON body
--   [{"token": "...", "id": "r1", "method": "camera.set", "params": {...}}]
-- answers [{"ok": true, "result": {...}}], [{"ok": false, "error": {code, message, data}}] or,
-- for methods that run on the game client, [{"ok": true, "pending": "<rid>"}]: poll with
-- method "_poll" and params {"rid": "<rid>"} until the answer is final.

local TOKEN = nil
local LOG_MAX = 2000
local PENDING_TTL_MS = 120000
local logRing = {}      -- {seq, t, stream, level, msg, file, line, resource}
local logSeq = 0
local logDropped = 0
local pending = {}      -- rid -> {player, method, t0, done, reply}
local ridCounter = 0
local clients = {}      -- player -> info sent by the client on start ("satk:hello")
local savedMinuteDuration = nil
local inLog = false

-- Methods that run on the agent client (the camera, the frame, the world).
local CLIENT_METHODS = {
    ["camera.get"] = true, ["camera.set"] = true, ["camera.release"] = true,
    ["world.settle"] = true, ["capture"] = true, ["pick"] = true, ["raycast"] = true,
    ["_rays"] = true, ["_lua"] = true,
}

local LEVELS = {[0] = "info", [1] = "error", [2] = "warn", [3] = "info", [4] = "info"}

---------------------------------------------------------------------------- log ring

local function pushLog(stream, level, msg, file, line, resource)
    if inLog then
        return
    end
    inLog = true
    logSeq = logSeq + 1
    logRing[#logRing + 1] = {seq = logSeq, t = getTickCount() / 1000, stream = stream, level = level,
                             msg = tostring(msg or ""), file = file, line = line, resource = resource}
    if #logRing > LOG_MAX then
        table.remove(logRing, 1)
        logDropped = logDropped + 1
    end
    inLog = false
end

addEventHandler("onDebugMessage", root, function(message, level, file, line)
    pushLog("server", LEVELS[level] or "info", message, file, line)
end)

addEventHandler("onPlayerJoin", root, function()
    pushLog("server", "info", "player joined: " .. getPlayerName(source))
end)

addEventHandler("onPlayerQuit", root, function(reason)
    pushLog("server", "info", "player quit: " .. getPlayerName(source) .. " (" .. tostring(reason) .. ")")
    clients[source] = nil
    for rid, p in pairs(pending) do
        if p.player == source and not p.done then
            p.done = true
            p.reply = {ok = false, error = {code = "NOT_READY", message = "the game client left during " .. p.method}}
        end
    end
end)

---------------------------------------------------------------------------- the agent client

addEvent("satk:hello", true)
addEventHandler("satk:hello", resourceRoot, function(info)
    if not client then
        return
    end
    info = type(info) == "table" and info or {}
    info.name = getPlayerName(client)
    info.t = getTickCount()
    clients[client] = info
    pushLog("server", "info", string.format("agent client ready: %s %sx%s", info.name, tostring(info.w), tostring(info.h)))
    -- A camera-only agent: spawn the ped out of the way and let the camera be driven by rpc.
    if not isPedDead(client) and getElementHealth(client) > 0 then
        return
    end
    spawnPlayer(client, 2495.0, -1687.0, 13.5)
    fadeCamera(client, true)
    setCameraTarget(client, client)
end)

addEvent("satk:log", true)
addEventHandler("satk:log", resourceRoot, function(items)
    if not client or type(items) ~= "table" then
        return
    end
    for _, it in ipairs(items) do
        if type(it) == "table" then
            pushLog(it.stream or "script", it.level or "info", it.msg, it.file, it.line, getPlayerName(client))
        end
    end
end)

local function agentClient(name)
    local best, bestT = nil, nil
    for p, info in pairs(clients) do
        if isElement(p) and (not name or info.name == name) and (not bestT or info.t < bestT) then
            best, bestT = p, info.t
        end
    end
    return best
end

local function captureDir(rid)
    return "captures/" .. rid .. ".png"
end

addEvent("satk:res", true)
addEventHandler("satk:res", resourceRoot, function(rid, ok, payload, blob)
    local p = pending[rid]
    if not p or p.done or client ~= p.player then
        return
    end
    p.done = true
    if not ok then
        p.reply = {ok = false, error = type(payload) == "table" and payload or {code = "INTERNAL", message = tostring(payload)}}
        return
    end
    payload = type(payload) == "table" and payload or {}
    if type(blob) == "string" and #blob > 0 then
        local path = captureDir(rid)
        if fileExists(path) then
            fileDelete(path)
        end
        local f = fileCreate(path)
        if not f then
            p.reply = {ok = false, error = {code = "INTERNAL", message = "cannot write " .. path .. " in the resource"}}
            return
        end
        fileWrite(f, blob)
        fileClose(f)
        payload.file = path
        payload.bytes = #blob
    end
    p.reply = {ok = true, result = payload}
end)

local function relay(method, params, req)
    local player = agentClient(params and params._client)
    if not player then
        satk.fail("NOT_READY", "no game client has joined the agent server",
            {hint = "start the client: python -m satk.viewer.backends.mta_lua client"})
    end
    ridCounter = ridCounter + 1
    local rid = string.format("q%d-%d", getTickCount(), ridCounter)
    pending[rid] = {player = player, method = method, t0 = getTickCount(), done = false}
    triggerClientEvent(player, "satk:req", resourceRoot, rid, method, params or {})
    return rid
end

local function cleanupPending()
    local now = getTickCount()
    for rid, p in pairs(pending) do
        if now - p.t0 > PENDING_TTL_MS then
            pending[rid] = nil
            local path = captureDir(rid)
            if fileExists(path) then
                fileDelete(path)
            end
        end
    end
end
setTimer(cleanupPending, 10000, 0)

---------------------------------------------------------------------------- server methods

local S = {}

function S.ping(params)
    return {t_ms = getTickCount()}
end

function S.hello(params)
    local v = getVersion() or {}
    local agent = agentClient()
    local info = agent and clients[agent] or nil
    local players = {}
    for _, p in ipairs(getElementsByType("player")) do
        players[#players + 1] = {name = getPlayerName(p), agent = clients[p] ~= nil}
    end
    return {
        agent = "satk-agent", agent_version = SATK_AGENT_VERSION, protocol = SATK_PROTOCOL,
        server = {version = v.sortable or v.mta, name = getServerName(), port = getServerPort(),
                  http_port = getServerHttpPort()},
        players = players,
        client = info and {name = info.name, w = info.w, h = info.h, version = info.version,
                           screen_upload = info.screen_upload} or false,
    }
end

local function envNow()
    local h, m = getTime()
    local w, blendTo = getWeather()
    local out = {time = satk.formatTime(h, m), weather = w or 0, freeze = savedMinuteDuration ~= nil}
    if blendTo then
        out.weather_b = blendTo
    end
    return out
end

S["env.get"] = function(params)
    return envNow()
end

S["status"] = function(params, req)
    if agentClient(params._client) then
        return nil, relay("status", params, req)
    end
    -- No game client yet: what the server alone knows (frame 0 = no client frames).
    return {frame = 0, fps = 0, env = envNow(), streaming = {pending = 0}, rev = {scene = 0, camera = 0},
            client = false}
end

S["env.set"] = function(params)
    local h, m
    if params.time ~= nil then
        h, m = satk.parseTime(params.time)
    end
    if params.weather ~= nil then
        local w = satk.num(params.weather, "weather")
        if w < 0 or w > 255 then
            satk.fail("BAD_PARAMS", "weather must be 0..255")
        end
        setWeather(w)
    end
    if params.weather_b ~= nil then
        setWeatherBlended(satk.num(params.weather_b, "weather_b"))
    end
    if h then
        setTime(h, m)
    end
    if params.freeze == true and not savedMinuteDuration then
        savedMinuteDuration = getMinuteDuration()
        setMinuteDuration(2147483647)
    elseif params.freeze == false and savedMinuteDuration then
        setMinuteDuration(savedMinuteDuration)
        savedMinuteDuration = nil
    end
    return envNow()
end

S["log.poll"] = function(params)
    local since = tonumber(params.since) or 0
    local max = satk.clamp(tonumber(params.max) or 100, 1, 1000)
    local order = {debug = 0, info = 1, warn = 2, error = 3}
    local minLevel = order[params.min_level or "debug"] or 0
    local streams = nil
    if type(params.streams) == "table" and #params.streams > 0 then
        streams = {}
        for _, s in ipairs(params.streams) do
            streams[s] = true
        end
    end
    local items, nextSeq = {}, since
    for _, e in ipairs(logRing) do
        if e.seq > since then
            nextSeq = e.seq
            if (not streams or streams[e.stream]) and (order[e.level] or 1) >= minLevel then
                items[#items + 1] = e
                if #items >= max then
                    break
                end
            end
        end
    end
    if #items < max then
        nextSeq = logSeq
    end
    return {items = items, next_seq = nextSeq, dropped = logDropped}
end

S["console.exec"] = function(params)
    local line = params.line
    if type(line) ~= "string" or line == "" then
        satk.fail("BAD_PARAMS", "line must be a non-empty string")
    end
    local cmd, args = line:match("^%s*(%S+)%s*(.-)%s*$")
    if not cmd then
        satk.fail("BAD_PARAMS", "line has no command")
    end
    local console = getElementsByType("console")[1]
    local called, ok = pcall(executeCommandHandler, cmd, console, args ~= "" and args or nil)
    ok = called and ok == true
    pushLog("console", "info", "> " .. line .. (ok and "" or " (no script command handler)"))
    return {accepted = ok == true}
end

S["lua.exec"] = function(params, req)
    local res = params.resource
    if res ~= nil and res ~= getResourceName(resource) then
        satk.fail("UNSUPPORTED", "lua.exec runs only in the satk-agent resource (G0)", {resource = res})
    end
    if params.side == "client" then
        return nil, relay("_lua", {code = params.code}, req)
    end
    local r = satk.execLua(params.code, "satk")
    r.side = "server"
    return r
end

S["quit"] = function(params)
    -- Answer first, then stop the server (the HTTP reply would be lost otherwise).
    setTimer(function()
        shutdown("satk-agent: quit requested over rpc")
    end, 200, 1)
    return {stopping = true}
end

S["_poll"] = function(params)
    local rid = params.rid
    local p = pending[rid]
    if not p then
        satk.fail("NOT_FOUND", "no pending request " .. tostring(rid))
    end
    if not p.done then
        return nil, rid
    end
    pending[rid] = nil
    return p.reply
end

S["_cancel"] = function(params)
    local p = pending[params.rid]
    if p then
        pending[params.rid] = nil
        triggerClientEvent(p.player, "satk:cancel", resourceRoot, params.rid)
    end
    return {cancelled = p ~= nil}
end

S["_drop"] = function(params)
    local path = type(params.file) == "string" and params.file or ""
    if path:match("^captures/[%w%-]+%.png$") and fileExists(path) then
        fileDelete(path)
    end
    return {}
end

---------------------------------------------------------------------------- rpc

local function loopback(addr)
    return addr == nil or addr == "127.0.0.1" or addr == "::1" or addr == "::ffff:127.0.0.1"
end

local function dispatch(req)
    local method = req.method
    local params = type(req.params) == "table" and req.params or {}
    local handler = S[method]
    if handler then
        local result, rid = handler(params, req)
        if rid then
            return {ok = true, pending = rid}
        end
        if type(result) == "table" and result.ok ~= nil and (result.result ~= nil or result.error ~= nil) then
            return result  -- a finished relayed reply from _poll
        end
        return {ok = true, result = result or {}}
    end
    if CLIENT_METHODS[method] then
        return {ok = true, pending = relay(method, params, req)}
    end
    satk.fail("UNKNOWN_METHOD", "unknown method " .. tostring(method))
end

--- The HTTP export (meta.xml: <export function="rpc" http="true"/>).
function rpc(req)
    if type(req) ~= "table" then
        return {ok = false, error = {code = "PROTOCOL", message = "the request must be a JSON object"}}
    end
    if not loopback(hostname) then
        return {ok = false, error = {code = "AUTH", message = "loopback only"}}
    end
    if not TOKEN or TOKEN == "" then
        return {ok = false, error = {code = "NOT_READY", message = "satk-agent has no token (settings @token)"}}
    end
    if req.token ~= TOKEN then
        return {ok = false, error = {code = "AUTH", message = "bad token"}}
    end
    local ok, reply = pcall(dispatch, req)
    if not ok then
        reply = {ok = false, error = satk.errorObject(reply)}
        if reply.error.code == "INTERNAL" then
            pushLog("debug", "error", "rpc " .. tostring(req.method) .. ": " .. reply.error.message)
        end
    end
    reply.id = req.id
    return reply
end

---------------------------------------------------------------------------- start

addEventHandler("onResourceStart", resourceRoot, function()
    TOKEN = get("@token")
    if type(TOKEN) ~= "string" or #TOKEN < 32 then
        TOKEN = nil
        outputServerLog("satk-agent: no @token setting (32+ chars); rpc refuses every request")
    end
    for _, p in ipairs(getElementsByType("player")) do
        fadeCamera(p, true)
    end
    pushLog("server", "info", "satk-agent " .. SATK_AGENT_VERSION .. " started")
end)
