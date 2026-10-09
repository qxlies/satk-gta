-- satk-bench (client): pure helpers (statistics, JSON-safe copies, camera paths). No MTA calls, so the
-- tests run them in a plain Lua 5.1.
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors

SB = SB or {}

local floor, ceil, max, min = math.floor, math.ceil, math.max, math.min

--- Round x to `dec` decimals (nil for NaN and infinity: JSON has neither).
function SB.round(x, dec)
    if type(x) ~= "number" or x ~= x or x == math.huge or x == -math.huge then
        return nil
    end
    local k = 10 ^ (dec or 3)
    return floor(x * k + 0.5) / k
end

--- A JSON-safe deep copy: finite numbers rounded to 3 decimals, short strings, booleans, tables of at
--- most `maxItems` entries and `depth` levels; functions and elements are dropped.
function SB.clean(v, depth, maxItems)
    local t = type(v)
    if t == "number" then
        return SB.round(v, 3)
    elseif t == "boolean" then
        return v
    elseif t == "string" then
        return #v > 200 and v:sub(1, 200) or v
    elseif t == "table" and (depth or 4) > 0 then
        local out, n, limit = {}, 0, maxItems or 300
        for k, x in pairs(v) do
            local c = SB.clean(x, (depth or 4) - 1, limit)
            if c ~= nil and n < limit then
                out[type(k) == "number" and k or tostring(k)] = c
                n = n + 1
            end
        end
        return out
    end
    return nil
end

--- Numeric leaves of table `src` as dotted keys, each kept at its maximum in `dst` (at most `cap` keys).
function SB.flatMax(src, dst, prefix, depth, cap)
    if type(src) ~= "table" or (depth or 4) <= 0 then
        return
    end
    for k, v in pairs(src) do
        local key = (prefix and prefix .. "." or "") .. tostring(k)
        if type(v) == "number" and v == v and v ~= math.huge and v ~= -math.huge then
            local old = dst[key]
            if old ~= nil then
                if v > old then
                    dst[key] = v
                end
            elseif (cap or 400) > SB.count(dst) then
                dst[key] = v
            end
        elseif type(v) == "table" then
            SB.flatMax(v, dst, key, (depth or 4) - 1, cap)
        end
    end
end

function SB.count(t)
    local n = 0
    for _ in pairs(t) do
        n = n + 1
    end
    return n
end

--- Frame statistics of an array of frame times in ms (`wallMs` = real time of the sample).
--- q[1..101] = the 0th..100th percentile; low1_fps = FPS of the mean of the slowest 1 % of the frames.
function SB.frameStats(frames, wallMs)
    local n = #frames
    if n == 0 then
        return {n = 0}
    end
    local sum, ints = 0, 0
    local s = {}
    for i = 1, n do
        local v = frames[i]
        s[i] = v
        sum = sum + v
        if v == floor(v) then
            ints = ints + 1
        end
    end
    table.sort(s)
    local worst = s[n]
    local q = {}
    for p = 0, 100 do
        q[p + 1] = SB.round(s[floor(p / 100 * (n - 1) + 0.5) + 1], 3)
    end
    local k = max(1, ceil(n * 0.01))
    local tail = 0
    for i = n - k + 1, n do
        tail = tail + s[i]
    end
    local avg = sum / n
    local out = {
        n = n, sum_ms = SB.round(sum, 1), avg_ms = SB.round(avg, 3), max_ms = SB.round(worst, 3),
        p50_ms = q[51], p95_ms = q[96], p99_ms = q[100], q = q,
        p999_ms = SB.round(s[floor(0.999 * (n - 1) + 0.5) + 1], 3),
        fps_avg = avg > 0 and SB.round(1000 / avg, 2) or nil,
        low1_fps = tail > 0 and SB.round(1000 / (tail / k), 2) or nil,
        integer_share = SB.round(ints / n, 3),
    }
    if wallMs and wallMs > 0 then
        out.wall_ms = SB.round(wallMs, 1)
        out.fps_wall = SB.round(n * 1000 / wallMs, 2)
    end
    return out
end

--- Linear interpolation of a waypoint list {{u = 0..1, x, y, z}, ...} (sorted by u) at progress u.
function SB.pathAt(pts, u)
    local n = #pts
    if n == 0 then
        return nil
    end
    if u <= pts[1].u or n == 1 then
        return pts[1].x, pts[1].y, pts[1].z
    end
    if u >= pts[n].u then
        return pts[n].x, pts[n].y, pts[n].z
    end
    for i = 2, n do
        local b = pts[i]
        if u <= b.u then
            local a = pts[i - 1]
            local span = b.u - a.u
            local f = span > 0 and (u - a.u) / span or 0
            return a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, a.z + (b.z - a.z) * f
        end
    end
    return pts[n].x, pts[n].y, pts[n].z
end

--- "HH:MM" -> hours, minutes (nil when malformed).
function SB.parseTime(s)
    local h, m = tostring(s or ""):match("^(%d+):(%d%d)$")
    h, m = tonumber(h), tonumber(m)
    if not h or h > 23 or m > 59 then
        return nil
    end
    return h, m
end

--- Deterministic pseudo-random numbers in [0, 1): returns next(), a closure over `seed`.
function SB.lcg(seed)
    local s = (tonumber(seed) or 1) % 2147483647
    if s <= 0 then
        s = 1
    end
    return function()
        s = (s * 16807) % 2147483647
        return (s - 1) / 2147483646
    end
end

--- Bytes -> MiB with one decimal (nil stays nil).
function SB.mib(bytes)
    if type(bytes) ~= "number" then
        return nil
    end
    return SB.round(bytes / 1048576, 1)
end
