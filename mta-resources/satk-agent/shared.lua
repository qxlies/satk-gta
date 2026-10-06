-- satk-agent: shared helpers (server and client).
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- Protocol mta-lua/1 (tools/docs/ru/mta-agent.md): the server exports `rpc` over MTA's HTTP
-- server; requests and replies are plain tables, errors are {code, message, data} with SAAP/1
-- error codes. Everything here is pure Lua 5.1 plus a few MTA utility functions (toJSON).

SATK_AGENT_VERSION = "1.0.0"
SATK_PROTOCOL = "mta-lua/1"

satk = satk or {}

--- Raise a structured error that rpc() turns into {ok=false, error={code, message, data}}.
function satk.fail(code, message, data)
    error({satk_error = true, code = code, message = message, data = data}, 0)
end

function satk.isFail(e)
    return type(e) == "table" and e.satk_error == true
end

--- {code, message, data} of any error value (Lua errors become INTERNAL).
function satk.errorObject(e)
    if satk.isFail(e) then
        return {code = e.code, message = tostring(e.message), data = e.data}
    end
    return {code = "INTERNAL", message = tostring(e)}
end

--- pack(...) keeping trailing nils (Lua 5.1 has no table.pack).
function satk.pack(...)
    return {n = select("#", ...), ...}
end

function satk.isFinite(x)
    return type(x) == "number" and x == x and x ~= math.huge and x ~= -math.huge
end

--- Number check for request fields (BAD_PARAMS otherwise).
function satk.num(v, what)
    if not satk.isFinite(v) then
        satk.fail("BAD_PARAMS", what .. " must be a finite number")
    end
    return v
end

--- {x, y, z} -> x, y, z (BAD_PARAMS otherwise).
function satk.vec3(v, what)
    if type(v) ~= "table" or #v ~= 3 then
        satk.fail("BAD_PARAMS", what .. " must be [x, y, z]")
    end
    return satk.num(v[1], what), satk.num(v[2], what), satk.num(v[3], what)
end

function satk.round(x, nd)
    local m = 10 ^ (nd or 4)
    local r = math.floor(x * m + 0.5) / m
    if r == 0 then
        return 0
    end
    return r
end

function satk.r3(x, y, z, nd)
    return {satk.round(x, nd), satk.round(y, nd), satk.round(z, nd)}
end

function satk.clamp(x, lo, hi)
    if x < lo then
        return lo
    end
    if x > hi then
        return hi
    end
    return x
end

--- "HH:MM" -> h, m (BAD_PARAMS otherwise).
function satk.parseTime(s)
    if type(s) ~= "string" then
        satk.fail("BAD_PARAMS", "time must be \"HH:MM\"")
    end
    local h, m = s:match("^(%d%d):(%d%d)$")
    h, m = tonumber(h), tonumber(m)
    if not h or h > 23 or m > 59 then
        satk.fail("BAD_PARAMS", "time must be \"HH:MM\" (00:00-23:59)")
    end
    return h, m
end

function satk.formatTime(h, m)
    return string.format("%02d:%02d", h or 0, m or 0)
end

--- One Lua value as a JSON text ("null" for nil, NaN and infinities); a value toJSON cannot
--- encode (function, userdata outside MTA elements) becomes its tostring() as a JSON string.
function satk.jsonValue(v)
    if v == nil or (type(v) == "number" and not satk.isFinite(v)) then
        return "null"
    end
    local ok, s = pcall(toJSON, v, true)
    if ok and type(s) == "string" and #s >= 2 then
        s = s:match("^%s*%[(.*)%]%s*$") or s  -- toJSON wraps the value in an array
        if s ~= "" then
            return s
        end
    end
    local text = tostring(v):gsub("\\", "\\\\"):gsub("\"", "\\\""):gsub("%c", " ")
    return "\"" .. text .. "\""
end

--- "satk:12: message" -> {msg, file, line}.
function satk.luaError(e)
    local s = tostring(e)
    local file, line, msg = s:match("^(.-):(%d+): (.*)$")
    if file and line then
        return {msg = msg, file = file, line = tonumber(line)}
    end
    return {msg = s}
end

--- Run a Lua chunk; print() output is captured. Returns {values_json, prints, error?}.
--- MTA aborts chunks that run too long by itself ("Aborting; infinite running script").
function satk.execLua(code, chunkname)
    if type(code) ~= "string" or code == "" then
        satk.fail("BAD_PARAMS", "code must be a non-empty string")
    end
    local fn, cerr = loadstring(code, "=" .. (chunkname or "satk"))
    if not fn then
        return {values_json = {}, prints = {}, error = satk.luaError(cerr)}
    end
    if setfenv then
        -- the resource's globals (the same table in MTA; matters in the tests). Not getfenv: MTA
        -- disables it and logs "Unsafe function was called." on every call.
        pcall(setfenv, fn, _G)
    end
    local prints = {}
    local oldPrint = print
    print = function(...)
        local parts = {}
        for i = 1, select("#", ...) do
            parts[#parts + 1] = tostring((select(i, ...)))
        end
        prints[#prints + 1] = table.concat(parts, "\t")
    end
    local r = satk.pack(pcall(fn))
    print = oldPrint
    if not r[1] then
        return {values_json = {}, prints = prints, error = satk.luaError(r[2])}
    end
    local values, size = {}, 0
    for i = 2, r.n do
        local j = satk.jsonValue(r[i])
        size = size + #j
        if size > 65536 then
            values[#values + 1] = "\"<truncated: values exceed 64 KiB>\""
            break
        end
        values[#values + 1] = j
    end
    return {values_json = values, prints = prints}
end

--- Effective camera intrinsics from the camera position, look direction and the half-tangents
--- of the image (used by the client; kept here so the maths can be tested without a game).
--- Returns the MTA fov to set so that the horizontal half-tangent becomes `wantTan`, given the
--- measured horizontal half-tangent `tanNow` at the MTA fov `fovNow` (degrees).
function satk.fovFor(wantTan, tanNow, fovNow)
    local k = tanNow / math.tan(math.rad(fovNow) / 2)
    if not satk.isFinite(k) or k <= 0 then
        k = 1
    end
    local f = 2 * math.deg(math.atan(wantTan / k))
    return satk.clamp(f, 1, 179), k
end

--- Centered crop (in window pixels) with the aspect of an output w x h image, for a window of
--- W x H whose vertical/horizontal half-tangent ratio is r (r = H/W for square pixels).
--- Returns u, v, us, vs and the factor c = (crop horizontal half-tangent) / (window one).
function satk.cropFor(W, H, w, h, r)
    r = r or (H / W)
    if w * r >= h then
        local vs = H * h / (w * r)
        return 0, (H - vs) / 2, W, vs, 1
    end
    local us = W * r * w / h
    return (W - us) / 2, 0, us, H, us / W
end
