-- satk-testdrive: shared helpers (server and client).
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- Pure Lua 5.1: geometry of test spots and lanes, rounding, time strings, the bone segments of the
-- ped check and the series statistics of the vehicle checks. No MTA calls here, so the maths is
-- tested by satk with the server's own Lua (tests/ingame).

TD = TD or {}
TD.VERSION = "1.0.0"
--- The generated resource with the models and the manifest (satk ingame start / reload).
TD.CONTENT = "satk-testdrive-mod"
--- Element data key that marks server elements made for a manifest model.
TD.KEY = "satk.td.key"
--- 1 unit of MTA velocity (metres per 1/50 s) in km/h.
TD.KMH = 180

--- Forward vector of a GTA heading (degrees; 0 = north/+y, 90 = west/-x).
function TD.fwd(h)
    local r = math.rad(h or 0)
    return -math.sin(r), math.cos(r)
end

--- Right-hand vector of a GTA heading.
function TD.right(h)
    local r = math.rad(h or 0)
    return math.cos(r), math.sin(r)
end

--- (x, y) moved `along` metres forward and `side` metres to the right of heading h.
function TD.offset(x, y, h, along, side)
    local fx, fy = TD.fwd(h)
    local rx, ry = TD.right(h)
    along, side = along or 0, side or 0
    return x + fx * along + rx * side, y + fy * along + ry * side
end

--- Signed distance of (px, py) along heading h from (x, y), and its signed sideways offset.
function TD.project(x, y, h, px, py)
    local fx, fy = TD.fwd(h)
    local rx, ry = TD.right(h)
    local dx, dy = px - x, py - y
    return dx * fx + dy * fy, dx * rx + dy * ry
end

--- Heading (degrees) of a direction vector.
function TD.headingOf(dx, dy)
    return (math.deg(math.atan2(-dx, dy)) + 360) % 360
end

--- Smallest signed difference a - b of two headings, in -180..180.
function TD.angleDiff(a, b)
    local d = (a - b) % 360
    if d > 180 then
        d = d - 360
    end
    return d
end

function TD.round(x, nd)
    if type(x) ~= "number" or x ~= x or x == math.huge or x == -math.huge then
        return nil
    end
    local m = 10 ^ (nd or 2)
    local r = math.floor(x * m + 0.5) / m
    if r == 0 then
        return 0
    end
    return r
end

function TD.r3(x, y, z, nd)
    return {TD.round(x, nd), TD.round(y, nd), TD.round(z, nd)}
end

function TD.clamp(x, lo, hi)
    if x < lo then
        return lo
    end
    if x > hi then
        return hi
    end
    return x
end

function TD.kmh(vx, vy, vz)
    return math.sqrt(vx * vx + vy * vy + (vz or 0) * (vz or 0)) * TD.KMH
end

--- "HH:MM" -> h, m (nil for anything else).
function TD.parseTime(s)
    if type(s) ~= "string" then
        return nil
    end
    local h, m = s:match("^(%d%d?):(%d%d)$")
    h, m = tonumber(h), tonumber(m)
    if not h or h > 23 or m > 59 then
        return nil
    end
    return h, m
end

function TD.formatTime(h, m)
    return string.format("%02d:%02d", h or 0, m or 0)
end

function TD.copy(t)
    if type(t) ~= "table" then
        return t
    end
    local out = {}
    for k, v in pairs(t) do
        out[k] = TD.copy(v)
    end
    return out
end

function TD.sortedKeys(t)
    local keys = {}
    for k in pairs(t or {}) do
        keys[#keys + 1] = k
    end
    table.sort(keys, function(a, b)
        return tostring(a) < tostring(b)
    end)
    return keys
end

function TD.find(list, fn)
    for i, v in ipairs(list or {}) do
        if fn(v, i) then
            return v, i
        end
    end
    return nil
end

--- The manifest model with this key (or the first one of `kind` when key is nil or "mod").
function TD.modelOf(manifest, key, kind)
    if type(manifest) ~= "table" or type(manifest.models) ~= "table" then
        return nil
    end
    for _, m in ipairs(manifest.models) do
        if key and key ~= "mod" and m.key == key then
            return m
        end
    end
    if key and key ~= "mod" then
        return nil
    end
    for _, m in ipairs(manifest.models) do
        if not kind or m.kind == kind then
            return m
        end
    end
    return nil
end

------------------------------------------------------------------------------ ped bones

--- Bone segments of the ped check: name, parent bone, child bone (getPedBonePosition ids). Only
--- direct parent -> child pairs: their length cannot change while an animation plays.
TD.SEGMENTS = {
    {"spine", 2, 3}, {"spine_upper", 3, 4}, {"neck", 4, 5},
    {"r_upper_arm", 22, 23}, {"r_forearm", 23, 24}, {"l_upper_arm", 32, 33}, {"l_forearm", 33, 34},
    {"l_thigh", 41, 42}, {"l_shin", 42, 43}, {"r_thigh", 51, 52}, {"r_shin", 52, 53},
}

--- {segment = length} for one pose; bonePos(id) returns x, y, z (or nil).
function TD.segments(bonePos)
    local out = {}
    for _, s in ipairs(TD.SEGMENTS) do
        local ax, ay, az = bonePos(s[2])
        local bx, by, bz = bonePos(s[3])
        if ax and bx then
            local dx, dy, dz = bx - ax, by - ay, bz - az
            out[s[1]] = math.sqrt(dx * dx + dy * dy + dz * dz)
        end
    end
    return out
end

--- Per segment: mean length and the largest relative deviation over a list of poses.
function TD.segmentStats(poses)
    local out = {}
    for _, s in ipairs(TD.SEGMENTS) do
        local name = s[1]
        local sum, n, lo, hi = 0, 0, math.huge, -math.huge
        for _, p in ipairs(poses or {}) do
            local v = p[name]
            if v then
                sum, n = sum + v, n + 1
                lo, hi = math.min(lo, v), math.max(hi, v)
            end
        end
        if n > 0 then
            local mean = sum / n
            local dev = 0
            if mean > 1e-6 then
                dev = math.max(hi - mean, mean - lo) / mean
            end
            out[name] = {len = TD.round(mean, 3), dev = TD.round(dev, 3), n = n}
        end
    end
    return out
end

------------------------------------------------------------------------------ series

--- Seconds after which every later sample stays within `tol` of the last one (series: {{t, v}}).
function TD.settleTime(series, tol)
    local n = #(series or {})
    if n == 0 then
        return nil
    end
    local final = series[n][2]
    local t0 = series[1][1]
    local settled = series[n][1]
    for i = n, 1, -1 do
        if math.abs(series[i][2] - final) > tol then
            break
        end
        settled = series[i][1]
    end
    return settled - t0
end

--- Largest |v - final| after the first `skip` seconds (bounce amplitude).
function TD.amplitude(series, skip)
    local n = #(series or {})
    if n == 0 then
        return 0
    end
    local final, t0 = series[n][2], series[1][1]
    local amp = 0
    for _, s in ipairs(series) do
        if s[1] - t0 >= (skip or 0) then
            amp = math.max(amp, math.abs(s[2] - final))
        end
    end
    return amp
end

--- True when the speed rose less than `tol` km/h over the last `window` seconds (series {{t, kmh}}).
function TD.plateau(series, window, tol)
    local n = #(series or {})
    if n < 2 then
        return false
    end
    local tEnd = series[n][1]
    if tEnd - series[1][1] < window then
        return false
    end
    local lo, hi = math.huge, -math.huge
    for i = n, 1, -1 do
        local s = series[i]
        if tEnd - s[1] > window then
            break
        end
        lo, hi = math.min(lo, s[2]), math.max(hi, s[2])
    end
    return hi - lo <= tol
end

--- Time (s) when the speed first reached `target` km/h, linearly interpolated (nil if never).
function TD.timeTo(series, target)
    local prev = nil
    for _, s in ipairs(series or {}) do
        if s[2] >= target then
            if not prev or s[2] == prev[2] then
                return s[1] - series[1][1]
            end
            local f = (target - prev[2]) / (s[2] - prev[2])
            return prev[1] + f * (s[1] - prev[1]) - series[1][1]
        end
        prev = s
    end
    return nil
end
