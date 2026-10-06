-- satk-testdrive (client): scripted behaviour checks of a manifest model, run as jobs.
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- A job is a coroutine resumed once per frame. It parks the player near the test area (frozen and
-- invisible), sets time and weather, creates client-side test elements in lanes and measures. With
-- a "new" model the mod lane and the vanilla reference lane run side by side in one pass; with a
-- "replace" model, and for the visual checks of a "new" model whose TXD slot is shared with its base,
-- the reference runs in a second pass after the mod is unloaded (and loaded again afterwards). When a check wants a screenshot the job pauses in state "shot" with a camera pose;
-- satk takes the frame through satk-agent and resumes the job (tdc job.resume). satk computes the
-- verdicts from the numbers (src/satk/ingame/checks.py).

local JOB_TIMEOUT_MS = 240000
local SHOT_TIMEOUT_MS = 60000
local MAX_JOBS = 20
local jobs = {}
local jobOrder = {}
local active = nil
local nextId = 0

local CHECKS = {vehicle = {}, object = {}, ped = {}, weapon = {}}
TDC.CHECKS = CHECKS
--- Default order of each suite.
TDC.SUITES = {
    vehicle = {"components", "rest", "speed", "crash", "damage", "lights", "dirt", "lod"},
    object = {"collision_ped", "collision_car", "lod", "night"},
    ped = {"anims", "walk"},
    weapon = {"held", "fire"},
}

local DUMMIES = {"light_front_main", "light_rear_main", "light_front_second", "light_rear_second", "seat_front",
                 "seat_rear", "exhaust", "exhaust_second", "engine", "gas_cap", "hand_rest"}
local DAMAGE_PARTS = {"bonnet_dummy", "boot_dummy", "door_lf_dummy", "door_rf_dummy", "door_lr_dummy",
                      "door_rr_dummy", "bump_front_dummy", "bump_rear_dummy", "windscreen_dummy", "wing_lf_dummy",
                      "wing_rf_dummy"}
local WHEELS = {"front_left", "rear_left", "front_right", "rear_right"}
local ANIMS = {{"ped", "WALK_civi"}, {"ped", "run_civi"}, {"ped", "IDLE_chat"}, {"ped", "XPRESSscratch"},
               {"ped", "handsup"}, {"ped", "FightA_1"}}
local LANE_ORDER = {"mod", "ref"}
--- Camera presets around the lane centre: along (forward), side (right), up, as multiples of the distance.
local VIEW = {
    front = {1, 0.12, 0.16}, rear = {-1, -0.12, 0.2}, side = {0.05, 1, 0.12},
    three_quarter = {0.72, 0.72, 0.28}, rear_quarter = {-0.72, 0.72, 0.28}, top = {-0.25, 0, 1.3},
    front_low = {1, 0.1, 0.02},
}

local function each(lanes, fn)
    for _, w in ipairs(LANE_ORDER) do
        if lanes[w] then
            fn(lanes[w], w)
        end
    end
end

local function bboxOf(el)
    local x0, y0, z0, x1, y1, z1 = getElementBoundingBox(el)
    if not x0 then
        return {-1, -1, -1, 1, 1, 1}
    end
    return {TD.round(x0, 3), TD.round(y0, 3), TD.round(z0, 3), TD.round(x1, 3), TD.round(y1, 3), TD.round(z1, 3)}
end

local function dist3(ax, ay, az, bx, by, bz)
    if not ax or not bx then
        return nil
    end
    local dx, dy, dz = ax - bx, ay - by, az - bz
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

---------------------------------------------------------------------------- job context

local function newCtx(job, m, g)
    local ctx = {job = job, m = m, g = g, h = g.h or 0, elements = {}, notes = {}, radius = nil, lanes = {}}

    function ctx.defer(fn)
        job.cleanups[#job.cleanups + 1] = fn
    end

    function ctx.step(text)
        job.step = text
    end

    function ctx.note(text)
        ctx.notes[#ctx.notes + 1] = text
    end

    function ctx.frames(n)
        for _ = 1, (n or 1) do
            coroutine.yield()
        end
    end

    function ctx.wait(ms)
        local t = getTickCount() + ms
        repeat
            coroutine.yield()
        until getTickCount() >= t
    end

    function ctx.own(el)
        if el then
            ctx.elements[#ctx.elements + 1] = el
        end
        return el
    end

    function ctx.clearElements()
        for i = #ctx.elements, 1, -1 do
            local el = ctx.elements[i]
            if isElement(el) then
                destroyElement(el)
            end
        end
        ctx.elements = {}
    end
    ctx.defer(function()
        ctx.clearElements()
    end)

    --- Ground height under (x, y) near zHint; falls back to zHint when no collision is loaded.
    function ctx.ground(x, y, zHint)
        zHint = zHint or g.z or 0
        pcall(enginePreloadWorldArea, x, y, zHint, "collisions")
        local gz = getGroundPosition(x, y, zHint + 15)
        if type(gz) ~= "number" or gz == 0 or math.abs(gz - zHint) > 8 then
            gz = zHint
        end
        return gz
    end

    --- Time, weather and a frozen clock for the check; restored afterwards.
    function ctx.env(env)
        local h, mi = getTime()
        local w = getWeather()
        local md = getMinuteDuration()
        ctx.defer(function()
            setTime(h, mi)
            setWeather(w)
            setMinuteDuration(md)
        end)
        env = env or {}
        local th, tm = TD.parseTime(env.time or "12:00")
        setTime(th or 12, tm or 0)
        setWeather(tonumber(env.weather) or 0)
        setMinuteDuration(3600000)
    end

    --- Move the player (or the vehicle they sit in) next to the test area, frozen and invisible.
    function ctx.park()
        local veh = getPedOccupiedVehicle(localPlayer)
        local el = veh or localPlayer
        local x, y, z = getElementPosition(el)
        local rx, ry, rz = getElementRotation(el)
        local alpha = getElementAlpha(localPlayer)
        local p = g.park or {-50, 30}
        local px, py = TD.offset(g.x, g.y, g.h, p[1], p[2])
        setElementFrozen(el, true)
        setElementPosition(el, px, py, (g.z or 0) + 1.5)
        setElementAlpha(localPlayer, 0)
        if veh then
            setElementAlpha(veh, 0)
        end
        toggleAllControls(false, true, false)
        ctx.defer(function()
            if isElement(el) then
                setElementPosition(el, x, y, z)
                setElementRotation(el, rx, ry, rz)
                setElementFrozen(el, false)
                if veh and isElement(veh) then
                    setElementAlpha(veh, 255)
                    setElementVelocity(veh, 0, 0, 0)
                end
            end
            setElementAlpha(localPlayer, alpha)
            toggleAllControls(true, true, false)
            setCameraTarget(localPlayer)
        end)
        local lx, ly = TD.offset(g.x, g.y, g.h, 20, 0)
        setCameraMatrix(px, py, (g.z or 0) + 25, lx, ly, g.z or 0, 0, 70)
        ctx.wait(900)
    end

    function ctx.vehicle(id, x, y, h)
        local gz = ctx.ground(x, y)
        local veh = createVehicle(id, x, y, gz + 1.5, 0, 0, h or ctx.h)
        if not veh then
            error("createVehicle(" .. tostring(id) .. ") failed", 0)
        end
        ctx.own(veh)
        setVehicleDamageProof(veh, false)
        return veh, gz
    end

    function ctx.driver(veh, skin)
        local x, y, z = getElementPosition(veh)
        local ped = createPed(skin or 7, x, y, z + 3)
        if not ped then
            error("createPed failed", 0)
        end
        ctx.own(ped)
        warpPedIntoVehicle(ped, veh)
        return ped
    end

    function ctx.ped(id, x, y, h)
        local gz = ctx.ground(x, y)
        local ped = createPed(id, x, y, gz + 1.0, h or ctx.h)
        if not ped then
            error("createPed(" .. tostring(id) .. ") failed", 0)
        end
        ctx.own(ped)
        return ped, gz
    end

    --- Centre of the lanes (x, y, z) at the height of the test elements.
    function ctx.center()
        local sx, sy, sz, n = 0, 0, 0, 0
        each(ctx.lanes, function(l)
            sx, sy, sz, n = sx + l.x, sy + l.y, sz + (l.gz or g.z or 0), n + 1
        end)
        if n == 0 then
            return g.x, g.y, (g.z or 0) + 1
        end
        return sx / n, sy / n, sz / n + (ctx.lift or 0.8)
    end

    --- Camera distance that fits every lane into a frame with horizontal FOV `fov`.
    function ctx.fit(fov)
        local r = ctx.radius or 3
        local half = r * 1.15
        each(ctx.lanes, function(l)
            half = math.max(half, math.abs(l.side) + r * 1.15)
        end)
        return math.max(half / math.tan(math.rad(fov / 2)) * 1.1, r * 2.4, 3)
    end

    --- A camera pose around the lane centre; also shows it on screen. o: {dist, fov, z, at}.
    function ctx.pose(preset, o)
        o = o or {}
        local cx, cy, cz = ctx.center()
        if o.at then
            cx, cy, cz = o.at[1], o.at[2], o.at[3]
        end
        local fov = o.fov or 60
        local d = o.dist or ctx.fit(fov)
        local v = VIEW[preset] or VIEW.three_quarter
        local px, py = TD.offset(cx, cy, ctx.h, v[1] * d, v[2] * d)
        local pz = cz + v[3] * d + (o.z or 0)
        local lz = cz + (o.z or 0) * 0.5
        setCameraMatrix(px, py, pz, cx, cy, lz, 0, fov)
        return {pos = TD.r3(px, py, pz, 3), look = TD.r3(cx, cy, lz, 3), fov_h_deg = fov}
    end

    --- Pause until satk took the frame (or the wait timed out).
    function ctx.shot(name, pose, settleFrames)
        local full = name .. "-" .. (ctx.passName or "mod")
        job.shot = {name = full, pose = pose, settle = settleFrames}
        job.shotFile = nil
        job.state = "shot"
        local deadline = getTickCount() + SHOT_TIMEOUT_MS
        while job.state == "shot" and getTickCount() < deadline do
            coroutine.yield()
        end
        if job.state == "shot" then
            job.state = "running"
            ctx.note("no frame for " .. full .. " (timed out)")
        end
        job.shots[#job.shots + 1] = {name = full, file = job.shotFile}
        job.shot = nil
    end

    return ctx
end

---------------------------------------------------------------------------- lane helpers

local function spawnVehicles(ctx, lanes, o)
    o = o or {}
    each(lanes, function(l)
        l.veh, l.gz = ctx.vehicle(l.id, l.x, l.y, ctx.h)
        if o.driver then
            l.ped = ctx.driver(l.veh)
        end
        if l.which == "mod" and type(ctx.m.handling) == "table" then
            local bad = 0
            for _, prop in ipairs(TD.sortedKeys(ctx.m.handling)) do
                if not setVehicleHandling(l.veh, prop, ctx.m.handling[prop]) then
                    bad = bad + 1
                end
            end
            l.handling = true
            if bad > 0 then
                ctx.note(bad .. " handling properties were rejected")
            end
        end
    end)
    ctx.frames(5)
    each(lanes, function(l)
        ctx.radius = math.max(ctx.radius or 0, getElementRadius(l.veh) or 2.5)
    end)
end

local function spawnPeds(ctx, lanes, skinOf)
    each(lanes, function(l)
        l.ped, l.gz = ctx.ped(skinOf and skinOf(l) or l.id, l.x, l.y, ctx.h)
    end)
    ctx.radius = 1.2
    ctx.lift = 0.9
end

--- Objects on the ground of each lane, frozen; lanes move apart when the objects are wide.
local function placeObjects(ctx, lanes)
    each(lanes, function(l)
        l.gz = ctx.ground(l.x, l.y)
        l.obj = ctx.own(createObject(l.id, l.x, l.y, l.gz + 3, 0, 0, ctx.h))
        if not l.obj then
            error("createObject(" .. tostring(l.id) .. ") failed", 0)
        end
        setElementFrozen(l.obj, true)
    end)
    ctx.wait(500)
    local half = 0
    each(lanes, function(l)
        l.bb = bboxOf(l.obj)
        half = math.max(half, math.abs(l.bb[1]), math.abs(l.bb[4]))
        ctx.radius = math.max(ctx.radius or 0, getElementRadius(l.obj) or 2)
    end)
    each(lanes, function(l)
        if l.side ~= 0 and math.abs(l.side) < half + 3 then
            l.side = (l.side < 0 and -1 or 1) * (half + 3)
            l.x, l.y = TD.offset(ctx.g.x, ctx.g.y, ctx.h, 0, l.side)
            l.gz = ctx.ground(l.x, l.y)
        end
        setElementPosition(l.obj, l.x, l.y, l.gz - l.bb[3])
    end)
    ctx.frames(3)
end

--- Lane-keeping for a client ped driving straight along heading g.h at sideways offset l.side.
local function steer(l, side, kmh, g)
    local _, _, rz = getElementRotation(l.veh)
    local v = math.max(kmh / 3.6, 5)
    local want = g.h + math.deg(math.atan(1.2 * (side - l.side) / v))
    local e = TD.angleDiff(want, rz or g.h)
    local u = TD.clamp(e / 20, -1, 1) / (1 + v / 40)
    if u > 0 then
        setPedAnalogControlState(l.ped, "vehicle_left", u)
        setPedAnalogControlState(l.ped, "vehicle_right", 0)
    else
        setPedAnalogControlState(l.ped, "vehicle_right", -u)
        setPedAnalogControlState(l.ped, "vehicle_left", 0)
    end
end

local function loopBack(l, g, back)
    local x, y, z = getElementPosition(l.veh)
    local vx, vy, vz = getElementVelocity(l.veh)
    local tx, ty, tz = getElementAngularVelocity(l.veh)
    local nx, ny = TD.offset(x, y, g.h, -back, 0)
    setElementPosition(l.veh, nx, ny, z)
    setElementVelocity(l.veh, vx, vy, vz)
    setElementAngularVelocity(l.veh, tx, ty, tz)
end

---------------------------------------------------------------------------- vehicle checks

local V = CHECKS.vehicle

V.components = {geom = "pad", run = function(ctx, lanes, out)
    ctx.step("spawn")
    spawnVehicles(ctx, lanes, {driver = true})
    each(lanes, function(l)
        setVehicleEngineState(l.veh, true)
    end)
    ctx.wait(1500)
    each(lanes, function(l, w)
        local comps = {}
        for name in pairs(getVehicleComponents(l.veh) or {}) do
            comps[#comps + 1] = name
        end
        table.sort(comps)
        local d = {}
        for _, name in ipairs(DUMMIES) do
            local x, y, z = getVehicleModelDummyPosition(l.id, name)
            if x then
                d[name] = TD.r3(x, y, z, 3)
            end
        end
        out[w] = {components = comps, dummies = d, bbox = bboxOf(l.veh),
                  radius = TD.round(getElementRadius(l.veh) or 0, 2)}
    end)
    ctx.shot("exhaust", ctx.pose("rear_quarter"), 30)
end}

V.rest = {geom = "pad", run = function(ctx, lanes, out)
    ctx.step("drop and settle")
    spawnVehicles(ctx, lanes)
    local series = {}
    local t0 = getTickCount()
    while getTickCount() - t0 < 4500 do
        local t = (getTickCount() - t0) / 1000
        each(lanes, function(l, w)
            local _, _, z = getElementPosition(l.veh)
            series[w] = series[w] or {}
            table.insert(series[w], {t, z})
        end)
        ctx.frames(1)
    end
    each(lanes, function(l, w)
        local n = 0
        for _, wh in ipairs(WHEELS) do
            local ok, on = pcall(isVehicleWheelOnGround, l.veh, wh)
            if ok and on then
                n = n + 1
            end
        end
        local _, _, z = getElementPosition(l.veh)
        local bb = bboxOf(l.veh)
        out[w] = {wheels = n, on_ground = isVehicleOnGround(l.veh) == true,
                  settle_s = TD.round(TD.settleTime(series[w], 0.01) or 0, 2),
                  bounce_m = TD.round(TD.amplitude(series[w], 1.5), 3),
                  ride_m = TD.round(z - l.gz, 3), gap_m = TD.round(z + bb[3] - l.gz, 3)}
    end)
    ctx.lift = 0.4
    ctx.shot("wheels", ctx.pose("front_low", {fov = 50}), 10)
end}

V.speed = {geom = "runway", run = function(ctx, lanes, out)
    local g = ctx.g
    local loopAt, back = g.loop_at or 600, g.loop_back or 520
    ctx.step("spawn")
    spawnVehicles(ctx, lanes, {driver = true})
    ctx.wait(1500)
    local st = {}
    each(lanes, function(l, w)
        st[w] = {series = {}, top = 0, dist = 0, loops = 0}
        setPedControlState(l.ped, "accelerate", true)
    end)
    ctx.step("accelerating")
    local t0 = getTickCount()
    local shot = false
    while true do
        local t = (getTickCount() - t0) / 1000
        local cx, cy, cz, n = 0, 0, 0, 0
        local done = true
        each(lanes, function(l, w)
            local s = st[w]
            local x, y, z = getElementPosition(l.veh)
            local kmh = TD.kmh(getElementVelocity(l.veh))
            local along, side = TD.project(g.x, g.y, g.h, x, y)
            if s.last then
                s.dist = s.dist + math.max(0, along - s.last)
            end
            s.last = along
            s.top = math.max(s.top, kmh)
            if not s.lost then
                if math.abs(side - l.side) > (g.lost or 9) or isVehicleBlown(l.veh) then
                    s.lost = TD.round(t, 1)
                    setPedControlState(l.ped, "accelerate", false)
                else
                    steer(l, side, kmh, g)
                    if along > loopAt then
                        loopBack(l, g, back)
                        s.last = along - back
                        s.loops = s.loops + 1
                    end
                end
            end
            if t - (s.lastT or -1) >= 0.25 then
                s.lastT = t
                s.series[#s.series + 1] = {t, kmh}
            end
            if not s.plateau and t > 8 and TD.plateau(s.series, 3, 1.0) then
                s.plateau = TD.round(t, 1)
            end
            if not (s.plateau or s.lost) then
                done = false
            end
            cx, cy, cz, n = cx + x, cy + y, cz + z, n + 1
        end)
        if n > 0 then
            cx, cy, cz = cx / n, cy / n, cz / n
            local px, py = TD.offset(cx, cy, g.h, -16, 0)
            local lx, ly = TD.offset(cx, cy, g.h, 14, 0)
            setCameraMatrix(px, py, cz + 6, lx, ly, cz, 0, 70)
            if not shot and t > 5 then
                shot = true
                ctx.shot("run", {pos = TD.r3(px, py, cz + 6, 3), look = TD.r3(lx, ly, cz, 3), fov_h_deg = 70}, 0)
            end
        end
        if done or t > (g.max_s or 45) then
            break
        end
        ctx.frames(1)
    end
    ctx.step("braking")
    each(lanes, function(l)
        local x, y = getElementPosition(l.veh)
        if TD.project(g.x, g.y, g.h, x, y) > loopAt - (g.brake_room or 250) then
            loopBack(l, g, back)
        end
        setPedControlState(l.ped, "accelerate", false)
        setPedControlState(l.ped, "brake_reverse", true)
    end)
    ctx.wait(3000)
    each(lanes, function(l, w)
        setPedControlState(l.ped, "brake_reverse", false)
        local s = st[w]
        out[w] = {top_kmh = TD.round(s.top, 1), t100_s = TD.round(TD.timeTo(s.series, 100), 2),
                  t60_s = TD.round(TD.timeTo(s.series, 60), 2), plateau_s = s.plateau, dist_m = TD.round(s.dist, 0),
                  loops = s.loops, lost_s = s.lost, handling = l.handling}
    end)
end}

V.crash = {geom = "wall", run = function(ctx, lanes, out)
    local g = ctx.g
    local half = g.wall_half or 0.4
    local wx, wy = TD.offset(g.x, g.y, g.h, half, 0)
    local wz = ctx.ground(wx, wy)
    local wall = ctx.own(createObject(g.wall_model or 8650, wx, wy, wz + (g.wall_z or 1.12), 0, 0, (g.h + 90) % 360))
    if not wall then
        error("createObject(wall) failed", 0)
    end
    setElementFrozen(wall, true)
    each(lanes, function(l)
        l.x, l.y = TD.offset(g.x, g.y, g.h, -(g.start or 22), l.side)
    end)
    ctx.step("spawn")
    spawnVehicles(ctx, lanes, {driver = true})
    ctx.wait(1500)
    local speed = g.speed or 50
    local v = speed / TD.KMH
    local fx, fy = TD.fwd(g.h)
    local st = {}
    each(lanes, function(l, w)
        fixVehicle(l.veh)
        l.health0 = getElementHealth(l.veh)
        l.bb = bboxOf(l.veh)
        st[w] = {gap = math.huge}
        setElementVelocity(l.veh, fx * v, fy * v, 0)
    end)
    ctx.step("impact")
    local t0 = getTickCount()
    while getTickCount() - t0 < 3000 do
        local t = (getTickCount() - t0) / 1000
        each(lanes, function(l, w)
            local s = st[w]
            local x, y = getElementPosition(l.veh)
            local along = TD.project(g.x, g.y, g.h, x, y)
            s.along = along
            s.gap = math.min(s.gap, -(along + l.bb[5]))
            if not s.impact and t > 0.15 and TD.kmh(getElementVelocity(l.veh)) < speed * 0.6 then
                s.impact = TD.round(t, 2)
            end
        end)
        ctx.frames(1)
    end
    ctx.lift = 0.6
    ctx.shot("impact", ctx.pose("top", {at = {g.x, g.y, wz + 1}, fov = 60}), 20)
    each(lanes, function(l, w)
        local s = st[w]
        local panels, doors, np, nd = {}, {}, 0, 0
        for p = 0, 6 do
            local ps = getVehiclePanelState(l.veh, p) or 0
            panels[p + 1] = ps
            if ps > 0 then
                np = np + 1
            end
        end
        for d = 0, 5 do
            local ds = getVehicleDoorState(l.veh, d) or 0
            doors[d + 1] = ds
            if ds > 1 then
                nd = nd + 1
            end
        end
        out[w] = {gap_m = TD.round(s.gap, 3), penetration_m = TD.round(math.max(0, -s.gap), 3),
                  passed = (s.along or -99) > half * 2 + 0.5, impact_s = s.impact, speed_kmh = speed,
                  health_loss = TD.round(l.health0 - getElementHealth(l.veh), 0), panels = panels, doors = doors,
                  panels_damaged = np, doors_damaged = nd}
    end)
end}

V.damage = {geom = "pad", visual = true, run = function(ctx, lanes, out)
    spawnVehicles(ctx, lanes)
    ctx.wait(1500)
    ctx.step("break panels and doors")
    each(lanes, function(l)
        for p = 0, 6 do
            setVehiclePanelState(l.veh, p, 3)
        end
        setVehicleDoorState(l.veh, 0, 2)
        setVehicleDoorState(l.veh, 1, 2)
        for d = 2, 5 do
            setVehicleDoorState(l.veh, d, 3)
        end
        for i = 0, 3 do
            setVehicleLightState(l.veh, i, 1)
        end
    end)
    ctx.wait(600)
    each(lanes, function(l, w)
        local comps = getVehicleComponents(l.veh) or {}
        local parts = {}
        for _, name in ipairs(DAMAGE_PARTS) do
            parts[name] = comps[name] ~= nil
        end
        local panels, doors = {}, {}
        for p = 0, 6 do
            panels[p + 1] = getVehiclePanelState(l.veh, p)
        end
        for d = 0, 5 do
            doors[d + 1] = getVehicleDoorState(l.veh, d)
        end
        out[w] = {parts = parts, panels = panels, doors = doors}
    end)
    ctx.shot("front", ctx.pose("three_quarter"), 20)
    ctx.shot("rear", ctx.pose("rear_quarter"), 5)
end}

V.lights = {geom = "pad", visual = true, env = {time = "00:00", weather = 0}, run = function(ctx, lanes, out)
    spawnVehicles(ctx, lanes, {driver = true})
    each(lanes, function(l)
        setVehicleEngineState(l.veh, true)
        setVehicleOverrideLights(l.veh, 2)
    end)
    ctx.wait(1500)
    each(lanes, function(l, w)
        local d = {}
        for _, name in ipairs({"light_front_main", "light_rear_main", "light_front_second", "light_rear_second"}) do
            local x, y, z = getVehicleModelDummyPosition(l.id, name)
            if x then
                d[name] = TD.r3(x, y, z, 3)
            end
        end
        out[w] = {dummies = d, lights = getVehicleOverrideLights(l.veh), bbox = bboxOf(l.veh)}
    end)
    ctx.shot("front", ctx.pose("front"), 20)
    ctx.shot("rear", ctx.pose("rear"), 5)
end}

V.dirt = {geom = "pad", visual = true, run = function(ctx, lanes, out)
    spawnVehicles(ctx, lanes)
    ctx.wait(1200)
    each(lanes, function(l, w)
        out[w] = {dirt = setVehicleDirtLevel(l.veh, 15) and 15 or 0}
    end)
    ctx.wait(500)
    ctx.shot("dirty", ctx.pose("three_quarter"), 20)
end}

V.lod = {geom = "pad", visual = true, run = function(ctx, lanes, out)
    spawnVehicles(ctx, lanes)
    ctx.wait(1200)
    each(lanes, function(l, w)
        out[w] = {lod_distance = TD.round(engineGetModelLODDistance(l.id) or 0, 1)}
    end)
    for _, d in ipairs(ctx.g.lod_shots or {20, 60, 140}) do
        ctx.shot("lod" .. d, ctx.pose("front", {dist = d, fov = 35}), 40)
    end
end}

---------------------------------------------------------------------------- object checks

local O = CHECKS.object

O.collision_ped = {geom = "obj", run = function(ctx, lanes, out)
    placeObjects(ctx, lanes)
    each(lanes, function(l)
        local sx, sy = TD.offset(l.x, l.y, ctx.h, l.bb[2] - 4, 0)
        l.ped = ctx.own(createPed(7, sx, sy, l.gz + 1, ctx.h))
        if not l.ped then
            error("createPed failed", 0)
        end
    end)
    ctx.wait(800)
    ctx.step("walk into the object")
    each(lanes, function(l)
        setPedControlState(l.ped, "forwards", true)
    end)
    local t0 = getTickCount()
    local shot = false
    while getTickCount() - t0 < 6000 do
        if not shot and getTickCount() - t0 > 3500 then
            shot = true
            ctx.shot("walk", ctx.pose("three_quarter"), 0)
        end
        ctx.frames(1)
    end
    each(lanes, function(l, w)
        setPedControlState(l.ped, "forwards", false)
        local x, y = getElementPosition(l.ped)
        local along = TD.project(l.x, l.y, ctx.h, x, y)
        out[w] = {ped_along = TD.round(along, 2), near = l.bb[2], far = l.bb[5],
                  height = TD.round(l.bb[6] - l.bb[3], 2), blocked = along < l.bb[5], passed = along > l.bb[5] + 0.3}
    end)
end}

O.collision_car = {geom = "obj", run = function(ctx, lanes, out)
    placeObjects(ctx, lanes)
    each(lanes, function(l)
        local sx, sy = TD.offset(l.x, l.y, ctx.h, l.bb[2] - 16, 0)
        l.veh = ctx.vehicle(ctx.g.car or 426, sx, sy, ctx.h)
        l.ped = ctx.driver(l.veh)
    end)
    ctx.wait(1500)
    local speed = ctx.g.car_speed or 30
    local v = speed / TD.KMH
    local fx, fy = TD.fwd(ctx.h)
    local st = {}
    each(lanes, function(l, w)
        l.vbb = bboxOf(l.veh)
        st[w] = {gap = math.huge}
        setElementVelocity(l.veh, fx * v, fy * v, 0)
    end)
    ctx.step("drive into the object")
    local t0 = getTickCount()
    while getTickCount() - t0 < 3500 do
        each(lanes, function(l, w)
            local x, y = getElementPosition(l.veh)
            local along = TD.project(l.x, l.y, ctx.h, x, y)
            st[w].along = along
            st[w].gap = math.min(st[w].gap, l.bb[2] - (along + l.vbb[5]))
        end)
        ctx.frames(1)
    end
    ctx.shot("impact", ctx.pose("three_quarter"), 10)
    each(lanes, function(l, w)
        local s = st[w]
        local passed = (s.along or -99) > l.bb[5] + 1
        out[w] = {gap_m = TD.round(s.gap, 3), passed = passed, blocked = not passed, speed_kmh = speed}
    end)
end}

O.lod = {geom = "obj", visual = true, run = function(ctx, lanes, out)
    placeObjects(ctx, lanes)
    local far = 0
    each(lanes, function(l, w)
        local d = engineGetModelLODDistance(l.id) or 0
        far = math.max(far, d)
        out[w] = {lod_distance = TD.round(d, 1), radius = TD.round(getElementRadius(l.obj) or 0, 2)}
    end)
    for _, f in ipairs({50, 90, 115}) do
        ctx.shot("lod" .. f, ctx.pose("front", {dist = math.max(10, far * f / 100), fov = 40}), 40)
    end
end}

O.night = {geom = "obj", visual = true, env = {time = "00:00", weather = 0}, run = function(ctx, lanes, out)
    placeObjects(ctx, lanes)
    each(lanes, function(l, w)
        out[w] = {radius = TD.round(getElementRadius(l.obj) or 0, 2), bbox = l.bb}
    end)
    ctx.wait(500)
    ctx.shot("night", ctx.pose("three_quarter"), 30)
end}

---------------------------------------------------------------------------- ped checks

local P = CHECKS.ped

P.anims = {geom = "ped", run = function(ctx, lanes, out)
    spawnPeds(ctx, lanes)
    ctx.wait(1200)
    local poses, played = {}, {}
    for i, a in ipairs(ANIMS) do
        ctx.step("animation " .. a[2])
        each(lanes, function(l)
            setPedAnimation(l.ped, a[1], a[2], -1, true, false, false, false)
        end)
        ctx.wait(250)
        local t0 = getTickCount()
        while getTickCount() - t0 < 1000 do
            each(lanes, function(l, w)
                poses[w] = poses[w] or {}
                poses[w][#poses[w] + 1] = TD.segments(function(b)
                    return getPedBonePosition(l.ped, b)
                end)
            end)
            ctx.frames(1)
        end
        each(lanes, function(l, w)
            local blk, anim = getPedAnimation(l.ped)
            played[w] = played[w] or {}
            if blk and anim and string.lower(anim) == string.lower(a[2]) then
                played[w][#played[w] + 1] = a[2]
            end
        end)
        if i == 3 then
            ctx.shot("pose", ctx.pose("front", {fov = 45}), 0)
        end
    end
    each(lanes, function(l, w)
        setPedAnimation(l.ped)
        out[w] = {segments = TD.segmentStats(poses[w]), played = played[w] or {}, anims = #ANIMS}
    end)
end}

P.walk = {geom = "ped", run = function(ctx, lanes, out)
    spawnPeds(ctx, lanes)
    ctx.wait(1200)
    each(lanes, function(l)
        l.x0, l.y0 = getElementPosition(l.ped)
        setPedControlState(l.ped, "forwards", true)
    end)
    ctx.wait(1500)
    ctx.shot("walk", ctx.pose("three_quarter", {fov = 50}), 0)
    ctx.wait(1500)
    each(lanes, function(l, w)
        setPedControlState(l.ped, "forwards", false)
        local x, y = getElementPosition(l.ped)
        out[w] = {moved_m = TD.round(math.sqrt((x - l.x0) ^ 2 + (y - l.y0) ^ 2), 2)}
    end)
end}

---------------------------------------------------------------------------- weapon checks

local W = CHECKS.weapon

local function armPeds(ctx, lanes)
    spawnPeds(ctx, lanes, function()
        return ctx.g.skin or 7
    end)
    each(lanes, function(l)
        givePedWeapon(l.ped, ctx.m.weapon, 500, true)
    end)
    ctx.wait(1500)
end

local function aim(ctx, lanes, on)
    each(lanes, function(l)
        if on then
            local tx, ty = TD.offset(l.x, l.y, ctx.h, 25, 0)
            setPedAimTarget(l.ped, tx, ty, l.gz + 1.2)
        end
        setPedControlState(l.ped, "aim_weapon", on)
    end)
end

local function muzzleToHand(l)
    local mx, my, mz = getPedWeaponMuzzlePosition(l.ped)
    local hx, hy, hz = getPedBonePosition(l.ped, 24)
    return TD.round(dist3(mx, my, mz, hx, hy, hz), 3)
end

W.held = {geom = "ped", run = function(ctx, lanes, out)
    armPeds(ctx, lanes)
    local idle = {}
    each(lanes, function(l, w)
        idle[w] = muzzleToHand(l)
    end)
    ctx.shot("idle", ctx.pose("three_quarter", {fov = 40}), 20)
    aim(ctx, lanes, true)
    ctx.wait(1000)
    each(lanes, function(l, w)
        out[w] = {weapon = getPedWeapon(l.ped), muzzle_hand_m = idle[w], muzzle_hand_aim_m = muzzleToHand(l)}
    end)
    ctx.shot("aim", ctx.pose("three_quarter", {fov = 40}), 10)
    aim(ctx, lanes, false)
end}

W.fire = {geom = "ped", run = function(ctx, lanes, out)
    armPeds(ctx, lanes)
    aim(ctx, lanes, true)
    ctx.wait(800)
    local fired = {}
    local function onFire()
        each(lanes, function(l, w)
            if source == l.ped then
                fired[w] = (fired[w] or 0) + 1
            end
        end)
    end
    addEventHandler("onClientPedWeaponFire", root, onFire)
    local attached = true
    ctx.defer(function()
        if attached then
            removeEventHandler("onClientPedWeaponFire", root, onFire)
        end
    end)
    ctx.step("firing")
    each(lanes, function(l)
        setPedControlState(l.ped, "fire", true)
    end)
    ctx.wait(300)
    ctx.shot("flash", ctx.pose("three_quarter", {fov = 40}), 0)
    ctx.wait(1200)
    each(lanes, function(l, w)
        setPedControlState(l.ped, "fire", false)
        out[w] = {fired = fired[w] or 0, weapon = getPedWeapon(l.ped)}
    end)
    removeEventHandler("onClientPedWeaponFire", root, onFire)
    attached = false
    aim(ctx, lanes, false)
end}

---------------------------------------------------------------------------- the runner

local function finish(job, ok, res)
    for i = #job.cleanups, 1, -1 do
        pcall(job.cleanups[i])
    end
    job.cleanups = {}
    if job.model then
        local L = TDC.loaded(job.model.key)
        if L and L.unloaded then
            TDC.loadModel(job.model)
        end
    end
    job.co = nil
    job.shot = nil
    if job.state == "cancelled" then
        return
    end
    if ok then
        job.state = "done"
        job.result = res
    else
        job.state = "error"
        job.err = tostring(res)
    end
end

local function runJob(job)
    local M = TDC.manifest()
    if not M then
        error("no manifest: the content resource is not loaded on this client", 0)
    end
    local m = TD.modelOf(M, job.key, job.kind)
    if not m then
        error("no manifest model " .. tostring(job.key), 0)
    end
    job.model = m
    local def = CHECKS[m.kind] and CHECKS[m.kind][job.check]
    if not def then
        error(("no check %s for a %s"):format(tostring(job.check), tostring(m.kind)), 0)
    end
    local modId = TDC.modId(m)
    if not modId then
        local L = TDC.loaded(m.key) or {}
        error("model " .. m.key .. " is not loaded: " .. tostring(L.err or "not applied yet"), 0)
    end
    if m.kind == "weapon" and not tonumber(m.weapon) then
        error("the manifest gives no weapon id for " .. m.key, 0)
    end
    local refId = nil
    if job.reference and m.ref ~= false and m.base then
        refId = m.base
    end
    local g = TD.copy((M.geom or {})[def.geom or "pad"])
    if type(g) ~= "table" or not g.x then
        error("the manifest has no geometry '" .. tostring(def.geom) .. "'", 0)
    end
    for k, v in pairs(job.opts or {}) do
        g[k] = v
    end
    local ctx = newCtx(job, m, g)
    ctx.env(def.env)
    ctx.park()
    local passes = {}
    local shared = (TDC.loaded(m.key) or {}).shared_txd
    if refId and m.mode == "new" and not (def.visual and shared) then
        passes[1] = {"mod", "ref"}
    else
        passes[1] = {"mod"}
        if refId then
            passes[2] = {"ref"}
        end
    end
    local out = {}
    for _, pass in ipairs(passes) do
        local unload = pass[1] == "ref" and #pass == 1  -- the reference alone: vanilla must look vanilla
        if unload then
            ctx.step("reference pass (vanilla)")
            TDC.unloadModel(m)
            ctx.wait(400)
        end
        local lanes = {}
        for j, which in ipairs(pass) do
            local side = 0
            if #pass == 2 then
                side = (j == 1 and -1 or 1) * (g.spacing or 8) / 2
            end
            local lx, ly = TD.offset(g.x, g.y, g.h, 0, side)
            lanes[which] = {which = which, id = which == "mod" and modId or refId, side = side, x = lx, y = ly}
        end
        ctx.lanes = lanes
        ctx.radius = nil
        ctx.lift = nil
        ctx.passName = #pass == 2 and "both" or pass[1]
        def.run(ctx, lanes, out, m)
        ctx.clearElements()
        if unload then
            TDC.loadModel(m)
            ctx.wait(300)
        end
    end
    return {check = job.check, kind = m.kind, key = m.key, mode = m.mode, mod_id = modId, ref_id = refId,
            passes = #passes, lanes = out, shots = job.shots, notes = ctx.notes}
end

local function step()
    local job = active
    if not job then
        return
    end
    if job.state == "cancelled" or getTickCount() - job.t0 > JOB_TIMEOUT_MS then
        finish(job, false, "the check ran longer than " .. JOB_TIMEOUT_MS .. " ms")
        active = nil
        return
    end
    local ok, res = coroutine.resume(job.co)
    if not ok then
        finish(job, false, res)
        active = nil
    elseif coroutine.status(job.co) == "dead" then
        finish(job, true, res)
        active = nil
    end
end

addEventHandler("onClientRender", root, step)

function TDC.busy()
    return active ~= nil
end

function TDC.jobInfo()
    if not active then
        return nil
    end
    return {id = active.id, check = active.check, state = active.state, step = active.step}
end

local function forget()
    while #jobOrder > MAX_JOBS do
        local id = table.remove(jobOrder, 1)
        if jobs[id] ~= active then
            jobs[id] = nil
        end
    end
end

local function startJob(a)
    if active then
        return {error = "a check is already running: " .. tostring(active.check) .. " (" .. active.id .. ")"}
    end
    if type(a.check) ~= "string" then
        return {error = "check is required"}
    end
    nextId = nextId + 1
    local job = {id = "j" .. nextId, check = a.check, key = a.key or "mod", kind = a.kind, opts = a.opts,
                 reference = a.reference ~= false, state = "running", step = "start", shots = {}, cleanups = {},
                 t0 = getTickCount()}
    job.co = coroutine.create(function()
        return runJob(job)
    end)
    jobs[job.id] = job
    jobOrder[#jobOrder + 1] = job.id
    forget()
    active = job
    return {id = job.id}
end

local function jobView(job)
    local out = {id = job.id, check = job.check, state = job.state, step = job.step}
    if job.state == "shot" and job.shot then
        out.shot = job.shot
    elseif job.state == "done" then
        out.result = job.result
    elseif job.state == "error" or job.state == "cancelled" then
        out.failure = job.err  -- not "error": that key means "the tdc call failed"
    end
    return out
end

--- tdc commands of the check jobs.
function TDC.jobCommand(cmd, a)
    if cmd == "checks" then
        return TDC.SUITES
    elseif cmd == "check.start" then
        return startJob(a)
    end
    local job = jobs[a.id or ""]
    if cmd == "job" then
        if not job then
            return {error = "no job " .. tostring(a.id)}
        end
        return jobView(job)
    elseif cmd == "job.resume" then
        if not job then
            return {error = "no job " .. tostring(a.id)}
        end
        if job.state == "shot" then
            job.shotFile = a.file
            job.state = "running"
        end
        return {state = job.state}
    elseif cmd == "job.cancel" then
        if job and (job.state == "running" or job.state == "shot") then
            job.state = "cancelled"
            job.err = "cancelled by satk"
        end
        return {state = job and job.state or "unknown"}
    end
    return {error = "unknown tdc command " .. tostring(cmd)}
end
