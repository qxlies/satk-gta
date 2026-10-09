-- satk-bench (client): the stage runner of `satk ingame bench`. satk sends one stage at a time (a table:
-- environment, camera, client-side vehicles/peds, warm-up and sample seconds, optional probe) through the
-- satk-agent bridge (lua.exec -> export `bench`) and polls `status` until the stage is done; `result`
-- returns the stage as JSON-safe data. All scene data lives on the satk side (src/satk/ingame/bench_scenes.py).
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- Per frame (onClientPreRender) the timeSlice is stored; once a second a snapshot of the process memory
-- (getProcessMemoryStats), streaming memory and, when the build has them, getEngineStats / getEngineLimits /
-- getDrawDistanceInfo is taken. Every optional function is feature-detected with type(fn) == "function", so
-- the same resource benches a stock MTA client and the sa-engine fork.
--
-- Commands of the export bench(cmd, args): hello, begin, stage, status, result, finish, abort, probe.

BENCH = BENCH or {}

local VERSION = 1
local FREEZE_MS = 60000000      -- minute duration used to freeze the game clock
local FOLLOW_EVERY_MS = 250     -- the frozen local player follows the camera (the world streams around it)
local WARN_MAX = 20

local run = {state = "idle"}    -- whole run: state idle|running|error, id, scene, saved environment
local cur = nil                 -- the stage being run
local results = {}              -- stage index -> result
local features = {}

local FUNCTIONS = {"getEngineStats", "getEngineLimits", "getDrawDistanceInfo", "getEngineSettings",
    "getEnginePatchReport", "getProcessMemoryStats", "dxGetStatus", "engineStreamingGetUsedMemory",
    "engineStreamingGetBufferSize", "setFPSLimit", "engineLoadDFF", "dxCreateTexture", "getGroundPosition",
    "setMinuteDuration", "createPed", "createVehicle"}

local function has(name)
    return type(_G[name]) == "function"
end

--- Call a function by name when it exists; the first result or nil (errors are swallowed).
local function try(name, ...)
    local fn = _G[name]
    if type(fn) ~= "function" then
        return nil
    end
    local ok, a, b = pcall(fn, ...)
    if ok then
        return a, b
    end
    return nil
end

local function warn(R, text)
    if R and #R.warn < WARN_MAX then
        R.warn[#R.warn + 1] = text
    end
end

local function detect()
    for _, name in ipairs(FUNCTIONS) do
        features[name] = has(name)
    end
end

---------------------------------------------------------------------------- snapshots

local function memory()
    local m = try("getProcessMemoryStats")
    if type(m) ~= "table" then
        return nil
    end
    local out = {}
    for k, v in pairs(m) do
        if type(v) == "number" then
            out[tostring(k) .. "_mib"] = SB.mib(v)
        end
    end
    return out
end

local function streamedCounts()
    local out = {}
    for _, t in ipairs({"vehicle", "ped", "object"}) do
        local list = getElementsByType(t, root, true)
        out[t] = list and #list or 0
    end
    return out
end

--- One snapshot: memory, streaming, and the sa-engine tables when present.
local function snapshot(full)
    local s = {tick = getTickCount()}
    local h, m = try("getTime")
    if h then
        s.time = {h, m}
    end
    s.mem = memory()
    s.va_mib = s.mem and s.mem.virtual_mib or nil
    s.stream_mib = SB.mib(try("engineStreamingGetUsedMemory"))
    s.engine = SB.clean(try("getEngineStats"))
    local limits = try("getEngineLimits")
    if type(limits) == "table" then
        s.limits = SB.clean(limits)
    end
    if full then
        s.draw = SB.clean(try("getDrawDistanceInfo"))
        s.dx = SB.clean(try("dxGetStatus"))
        s.streamed = streamedCounts()
        s.stream_buffer_mib = SB.mib(try("engineStreamingGetBufferSize"))
    end
    return s
end

---------------------------------------------------------------------------- environment

local function subject()
    return getPedOccupiedVehicle(localPlayer) or localPlayer
end

local function saveEnvironment()
    local h, m = try("getTime")
    local w = try("getWeather")
    run.saved = {minute = try("getMinuteDuration") or 1000, time = h and {h, m} or nil, weather = w,
        fps = try("getFPSLimit")}
    local el = subject()
    run.saved.el = el
    run.saved.pos = {getElementPosition(el)}
    run.saved.frozen = isElementFrozen(el)
end

local function restoreEnvironment()
    local sv = run.saved
    if not sv then
        return
    end
    try("setCameraTarget", localPlayer)
    if sv.el and isElement(sv.el) then
        try("setElementFrozen", sv.el, sv.frozen == true)
        try("setElementCollisionsEnabled", sv.el, true)
        if sv.pos and sv.pos[1] then
            try("setElementPosition", sv.el, sv.pos[1], sv.pos[2], sv.pos[3])
        end
    end
    try("setMinuteDuration", sv.minute or 1000)
    if sv.time then
        try("setTime", sv.time[1], sv.time[2])
    end
    if sv.weather then
        try("setWeather", sv.weather)
    end
    if sv.fps then
        try("setFPSLimit", sv.fps)
    end
    run.saved = nil
end

local function applyEnv(R)
    local env = R.def.env or {}
    if env.weather then
        try("setWeather", env.weather)
    end
    local tl = env.timelapse
    local t0 = tl and tl.from or env.time
    local h, m = SB.parseTime(t0)
    if h then
        try("setTime", h, m)
    end
    if env.freeze ~= false then
        if not has("setMinuteDuration") then
            warn(R, "setMinuteDuration is not available: the game clock was not frozen")
        end
        try("setMinuteDuration", FREEZE_MS)
    end
end

local function startTimelapse(R)
    local tl = R.def.env and R.def.env.timelapse
    if not tl then
        return
    end
    local h0, m0 = SB.parseTime(tl.from)
    local h1, m1 = SB.parseTime(tl.to)
    if not (h0 and h1) then
        warn(R, "timelapse: bad from/to")
        return
    end
    local minutes = (h1 * 60 + m1) - (h0 * 60 + m0)
    if minutes <= 0 then
        minutes = minutes + 1440
    end
    try("setTime", h0, m0)
    R.timelapse = {minutes = minutes}
    try("setMinuteDuration", math.max(1, math.floor(R.sampleMs / minutes)))
end

---------------------------------------------------------------------------- spawning

local function spawnAll(R)
    local d = R.def
    for _, v in ipairs(d.vehicles or {}) do
        local el = try("createVehicle", v.model, v.x, v.y, v.z, 0, 0, v.h or 0)
        if el then
            R.els[#R.els + 1] = el
            R.vehicles = R.vehicles + 1
            try("setElementFrozen", el, v.frozen == true)
        else
            R.spawnFail = R.spawnFail + 1
        end
    end
    for _, p in ipairs(d.peds or {}) do
        local el = try("createPed", p.model, p.x, p.y, p.z, p.h or 0)
        if el then
            R.els[#R.els + 1] = el
            R.peds[#R.peds + 1] = el
            if d.walk then
                try("setPedControlState", el, "walk", true)
                try("setPedControlState", el, "forwards", true)
            end
        else
            R.spawnFail = R.spawnFail + 1
        end
    end
    if R.spawnFail > 0 then
        warn(R, R.spawnFail .. " client-side element(s) could not be created")
    end
end

local function turnPeds(R, now)
    local d = R.def
    if not d.walk or #R.peds == 0 or now < R.nextTurn then
        return
    end
    R.nextTurn = now + (d.turn_ms or 3000)
    local c = d.center or {0, 0, 0}
    local radius = d.radius or 30
    for _, ped in ipairs(R.peds) do
        if isElement(ped) then
            local x, y = getElementPosition(ped)
            local dx, dy = c[1] - x, c[2] - y
            local rz
            if dx * dx + dy * dy > radius * radius then
                rz = -math.deg(math.atan2(dx, dy))
            else
                rz = R.rnd() * 360
            end
            try("setPedRotation", ped, rz % 360)
        end
    end
end

---------------------------------------------------------------------------- probes

local function startWeapon(R)
    local p = R.def.probe
    local pos, tgt = p.pos, p.target
    local ped = try("createPed", p.skin or 7, pos[1], pos[2], pos[3], p.h or 270)
    if not ped then
        warn(R, "weapon probe: createPed failed")
        return
    end
    R.els[#R.els + 1] = ped
    R.shooter = ped
    try("giveWeapon", ped, p.weapon or 31, p.ammo or 99999, true)
    try("setPedWeaponSlot", ped, p.slot or 5)
    try("setPedAimTarget", ped, tgt[1], tgt[2], tgt[3])
    R.shots = {n = 0}
end

local function weaponOn(R, on)
    if R.shooter and isElement(R.shooter) then
        try("setPedControlState", R.shooter, "aim_weapon", on)
        try("setPedControlState", R.shooter, "fire", on)
    end
end

local function startLoader(R)
    local p = R.def.probe
    local texSide = p.tex_side or 1024            -- 1024 x 1024 argb = 4 MiB
    local m0 = try("getProcessMemoryStats")
    R.loader = {tex = {}, dff = {}, steps = 0, loadedMib = 0, points = {}, texFail = 0, dffFail = 0, nTex = 0, nDff = 0,
        vaStart = type(m0) == "table" and SB.mib(m0.virtual) or nil, texSide = texSide, nextStep = 0}
    if not has("dxCreateTexture") then
        warn(R, "dxCreateTexture is not available")
    end
    if (p.dff_per_step or 0) > 0 and not has("engineLoadDFF") then
        warn(R, "engineLoadDFF is not available")
    end
end

local function loaderStep(R, now)
    local L, p = R.loader, R.def.probe
    if not L or L.done or now < L.nextStep then
        return
    end
    L.nextStep = now + (p.step_ms or 50)
    for _ = 1, p.tex_per_step or 1 do
        local t = try("dxCreateTexture", L.texSide, L.texSide, "argb")
        if t then
            L.tex[#L.tex + 1] = t
            L.nTex = L.nTex + 1
            L.loadedMib = L.loadedMib + (p.tex_mib or 4)
        else
            L.texFail = L.texFail + 1
        end
    end
    for _ = 1, p.dff_per_step or 0 do
        local d = try("engineLoadDFF", p.dff_path or "gen/probe.dff")
        if d then
            L.dff[#L.dff + 1] = d
            L.nDff = L.nDff + 1
            L.loadedMib = L.loadedMib + (p.dff_mib or 2)
        else
            L.dffFail = L.dffFail + 1
        end
    end
    L.steps = L.steps + 1
    local m = try("getProcessMemoryStats")
    local va = type(m) == "table" and SB.mib(m.virtual) or nil
    local eng = try("getEngineStats")
    local guard = type(eng) == "table" and (eng.vaGuard or eng.vaGuardState) or nil
    if #L.points < 600 and (L.steps <= 50 or L.steps % 2 == 0) then
        L.points[#L.points + 1] = {L.steps, SB.round(L.loadedMib, 1), va}
    end
    local why
    if va and va >= (p.yellow_mib or 3072) then
        why = "yellow"
    elseif guard and guard ~= 0 and guard ~= "ok" and guard ~= "green" and guard ~= false then
        why = "guard:" .. tostring(guard)
    elseif L.steps >= (p.max_steps or 800) then
        why = "max_steps"
    elseif L.texFail + L.dffFail >= 5 then
        why = "load_failed"
    end
    L.last = {va = va, guard = guard and tostring(guard) or nil}
    if why then
        L.done, L.why = true, why
        R.earlyEnd = now + (p.hold_ms or 3000)
    end
end

local function releaseLoader(R)
    local L = R.loader
    if not L then
        return
    end
    for _, e in ipairs(L.tex) do
        if isElement(e) then
            destroyElement(e)
        end
    end
    for _, e in ipairs(L.dff) do
        if isElement(e) then
            destroyElement(e)
        end
    end
    L.tex, L.dff = {}, {}
end

---------------------------------------------------------------------------- the frame step

local function cameraFrame(R, now, u)
    local c = R.def.camera
    local subjectEl = R.follow
    if not c or c.kind == "player" then
        return
    end
    local px, py, pz, lx, ly, lz
    if c.kind == "path" then
        px, py, pz = SB.pathAt(c.points, u)
        lx, ly, lz = SB.pathAt(c.points, math.min(1, u + (c.ahead or 0.02)))
        lz = lz - (c.drop or 25)
        if c.agl then
            local g = try("getGroundPosition", px, py, 1500)
            if type(g) == "number" and g > 0 then
                local want = g + c.agl
                R.camZ = (R.camZ and R.camZ + (want - R.camZ) * math.min(1, (R.dtMs or 16) / 500)) or want
                pz = math.max(pz, R.camZ)
            end
        end
        local dx, dy, dz = lx - px, ly - py, lz - pz
        if dx * dx + dy * dy + dz * dz < 1 and R.lastLook then
            lx, ly, lz = px + R.lastLook[1], py + R.lastLook[2], pz + R.lastLook[3]
        else
            R.lastLook = {dx, dy, dz}
        end
    else
        px, py, pz = c.pos[1], c.pos[2], c.pos[3]
        lx, ly, lz = c.look[1], c.look[2], c.look[3]
    end
    setCameraMatrix(px, py, pz, lx, ly, lz, 0, c.fov or 70)
    R.camPos = {px, py, pz}
    if subjectEl and now >= R.nextFollow then
        R.nextFollow = now + FOLLOW_EVERY_MS
        local z = pz - (c.follow_drop or 6)
        try("setElementPosition", subjectEl, px, py, z)
    end
end

local function recordSecond(R, now, el)
    local b = R.bucket
    local s = snapshot(false)
    local row = {SB.round(el / 1000, 1), b.n, b.n > 0 and SB.round(b.sum / b.n, 3) or 0, SB.round(b.max, 3), s.va_mib,
        s.stream_mib}
    R.series[#R.series + 1] = row
    R.bucket = {n = 0, sum = 0, max = 0}
    if s.va_mib and (not R.vaPeak or s.va_mib > R.vaPeak) then
        R.vaPeak = s.va_mib
    end
    if s.stream_mib and (not R.streamPeak or s.stream_mib > R.streamPeak) then
        R.streamPeak = s.stream_mib
    end
    if s.limits then
        SB.flatMax(s.limits, R.limitsMax, nil, 4, 400)
        R.limitsLast = s.limits
    end
    local counts = streamedCounts()
    for k, v in pairs(counts) do
        if v > (R.streamedMax[k] or 0) then
            R.streamedMax[k] = v
        end
    end
end

local function clockOf(a, b)
    local ea, eb = a.engine, b.engine
    if type(ea) ~= "table" or type(eb) ~= "table" then
        return {note = "getEngineStats is not available", tick_ms = b.tick - a.tick}
    end
    local out = {tick_ms = b.tick - a.tick, timer_mode = eb.timerMode}
    local dt = b.tick - a.tick
    if dt > 0 and type(ea.gameTimeMs) == "number" and type(eb.gameTimeMs) == "number" then
        out.game_ms = eb.gameTimeMs - ea.gameTimeMs
        out.ratio = SB.round(out.game_ms / dt, 4)
    end
    if dt > 0 and type(ea.frameCounter) == "number" and type(eb.frameCounter) == "number" then
        out.frame_counter = eb.frameCounter - ea.frameCounter
        out.frame_counter_hz = SB.round(out.frame_counter * 1000 / dt, 2)
    end
    out.engine_frame_counter_hz = eb.frameCounterRate or eb.frameCounterHz
    return out
end

local function teardown(R)
    releaseLoader(R)
    weaponOn(R, false)
    for _, el in ipairs(R.els) do
        if isElement(el) then
            destroyElement(el)
        end
    end
    R.els, R.peds = {}, {}
    local el = R.follow
    if el and isElement(el) then
        try("setElementFrozen", el, false)
        try("setElementCollisionsEnabled", el, true)
    end
    try("setCameraTarget", localPlayer)
end

local function finalize(R, now)
    local fin = snapshot(true)
    local wall = now - R.sampleStart
    local d = R.def
    local res = {
        id = d.id, title = d.title, index = R.index, warmup_s = SB.round(R.warmupMs / 1000, 2),
        sample_s = SB.round(wall / 1000, 2), frames = SB.frameStats(R.frames, wall), series = R.series,
        va = {start_mib = R.start.va_mib, end_mib = fin.va_mib, peak_mib = R.vaPeak,
            growth_mib = (R.start.va_mib and fin.va_mib) and SB.round(fin.va_mib - R.start.va_mib, 1) or nil},
        stream = {start_mib = R.start.stream_mib, end_mib = fin.stream_mib, peak_mib = R.streamPeak},
        start = R.start, finish = fin, limits_max = R.limitsMax, limits_last = R.limitsLast or fin.limits,
        clock = clockOf(R.start, fin), counts = {spawned = {vehicles = R.vehicles, peds = #R.peds, failed = R.spawnFail},
            streamed_end = fin.streamed, streamed_max = R.streamedMax},
        camera = R.camPos and {end_pos = SB.clean(R.camPos)} or nil, warn = R.warn, features = features,
        fps_limit = R.fpsLimit,
    }
    if R.timelapse then
        res.timelapse = {minutes = R.timelapse.minutes, end_time = fin.time}
    end
    if R.shots then
        local s = R.shots
        local sec = wall / 1000
        res.probe = {kind = "weapon", shots = s.n, seconds = SB.round(sec, 2), shots_per_s = sec > 0 and SB.round(s.n / sec, 3) or nil,
            first_ms = s.first and (s.first - R.sampleStart) or nil, last_ms = s.last and (s.last - R.sampleStart) or nil,
            game_ms = (R.start.engine and fin.engine and R.start.engine.gameTimeMs and fin.engine.gameTimeMs)
                and (fin.engine.gameTimeMs - R.start.engine.gameTimeMs) or nil}
        if res.probe.game_ms and res.probe.game_ms > 0 then
            res.probe.shots_per_game_s = SB.round(s.n * 1000 / res.probe.game_ms, 3)
        end
        res.probe.weapon = d.probe.weapon or 31
    end
    if R.loader then
        local L = R.loader
        res.probe = {kind = "loader", steps = L.steps, textures = L.nTex, dffs = L.nDff, loaded_mib = SB.round(L.loadedMib, 1),
            stop_reason = L.why or "time", points = L.points, va_start_mib = L.vaStart, tex_fail = L.texFail,
            dff_fail = L.dffFail, last = L.last, tex_mib = d.probe.tex_mib or 4, dff_mib = d.probe.dff_mib or 2}
    end
    results[R.index] = res
    return res
end

local function enterSample(R, now)
    R.phase = "sample"
    R.sampleStart = now
    R.nextSecond = now + 1000
    R.start = snapshot(true)
    R.vaPeak, R.streamPeak = R.start.va_mib, R.start.stream_mib
    startTimelapse(R)
    if R.shooter then
        weaponOn(R, true)
    end
    if R.loader then
        R.loader.nextStep = now
    end
end

local function step(R, slice, now)
    R.dtMs = slice
    if R.phase == "warmup" then
        cameraFrame(R, now, 0)
        turnPeds(R, now)
        if now >= R.phaseEnd then
            enterSample(R, now)
        end
    elseif R.phase == "sample" then
        local el = now - R.sampleStart
        local u = math.min(1, el / R.sampleMs)
        cameraFrame(R, now, u)
        turnPeds(R, now)
        local b = R.bucket
        R.frames[#R.frames + 1] = slice
        b.n, b.sum = b.n + 1, b.sum + slice
        if slice > b.max then
            b.max = slice
        end
        loaderStep(R, now)
        if now >= R.nextSecond then
            R.nextSecond = R.nextSecond + 1000
            recordSecond(R, now, el)
        end
        if el >= R.sampleMs or (R.earlyEnd and now >= R.earlyEnd) then
            if R.bucket.n > 0 then
                recordSecond(R, now, el)
            end
            R.endNow = now
            R.phase = R.loader and "release" or "done"
            if R.phase == "release" then
                releaseLoader(R)
                R.phaseEnd = now + (R.def.probe.release_ms or 2000)
                R.vaAtRelease = snapshot(false).va_mib
            end
        end
    elseif R.phase == "release" then
        if now >= R.phaseEnd then
            R.afterRelease = snapshot(false).va_mib
            R.phase = "done"
        end
    end
    if R.phase == "done" then
        weaponOn(R, false)
        local res = finalize(R, R.endNow or now)
        if R.loader then
            res.probe.va_after_release_mib = R.afterRelease
            res.probe.va_at_release_mib = R.vaAtRelease
        end
        teardown(R)
        R.finished = true
        cur = nil
    end
end

addEventHandler("onClientPreRender", root, function(slice)
    local R = cur
    if not R or R.finished then
        return
    end
    local ok, err = pcall(step, R, slice, getTickCount())
    if not ok then
        run.state, run.err = "error", tostring(err)
        pcall(teardown, R)
        R.finished = true
        cur = nil
    end
end)

addEventHandler("onClientPedWeaponFire", root, function()
    local R = cur
    if R and R.shots and R.phase == "sample" and source == R.shooter then
        local now = getTickCount()
        R.shots.n = R.shots.n + 1
        R.shots.first = R.shots.first or now
        R.shots.last = now
    end
end)

addEventHandler("onClientResourceStop", resourceRoot, function()
    if cur then
        pcall(teardown, cur)
        cur = nil
    end
    pcall(restoreEnvironment)
end)

---------------------------------------------------------------------------- the export

local C = {}

function C.hello(a)
    detect()
    local sw, sh = guiGetScreenSize()
    local settings = SB.clean(try("getEngineSettings"))
    local patch = try("getEnginePatchReport")
    return {version = VERSION, tick = getTickCount(), features = features, screen = {sw, sh}, fps_limit = try("getFPSLimit"),
        sae = has("getEngineStats") or has("getEngineLimits") or has("getDrawDistanceInfo"), settings = settings,
        profile = type(patch) == "table" and patch.profile or nil, preset = type(patch) == "table" and patch.preset or nil,
        memory = memory(), busy = cur ~= nil}
end

function C.begin(a)
    if cur then
        return {error = "a stage is running"}
    end
    detect()
    if not run.saved then
        saveEnvironment()
    end
    run.state, run.err, run.id, run.scene = "running", nil, a.run, a.scene
    results = {}
    return {ok = true, features = features}
end

function C.stage(a)
    if cur then
        return {error = "a stage is running"}
    end
    local d = a.stage
    if type(d) ~= "table" or not d.id then
        return {error = "stage: a table with an id is required"}
    end
    if not run.saved then
        saveEnvironment()
    end
    run.state, run.err, run.logged = "running", nil, nil
    local warmup = tonumber(a.warmup) or d.warmup or 5
    local sample = tonumber(a.sample) or d.sample or 20
    local now = getTickCount()
    local R = {
        def = d, index = tonumber(a.index) or 1, phase = "warmup", warn = {}, els = {}, peds = {}, frames = {}, series = {},
        bucket = {n = 0, sum = 0, max = 0}, limitsMax = {}, streamedMax = {vehicle = 0, ped = 0, object = 0}, vehicles = 0, spawnFail = 0,
        warmupMs = warmup * 1000, sampleMs = sample * 1000, phaseEnd = now + warmup * 1000, nextFollow = 0, nextTurn = now,
        rnd = SB.lcg(d.seed or 1),
    }
    R.fpsLimit = {requested = d.fps_limit}
    applyEnv(R)
    if d.fps_limit then
        R.fpsLimit.lua = has("setFPSLimit") and (try("setFPSLimit", d.fps_limit) == true) or false
    end
    if d.camera and d.camera.kind ~= "player" and d.follow ~= false then
        R.follow = subject()
        try("setElementFrozen", R.follow, true)
        try("setElementCollisionsEnabled", R.follow, false)
    end
    spawnAll(R)
    if d.probe and d.probe.kind == "weapon" then
        startWeapon(R)
    elseif d.probe and d.probe.kind == "loader" then
        startLoader(R)
    end
    cur = R
    return {ok = true, id = d.id, index = R.index}
end

function C.status(a)
    local R = cur
    local out = {state = run.state, run = run.id, scene = run.scene, err = run.err, results = SB.count(results)}
    if run.err and not run.logged then
        run.logged = true
        outputDebugString("satk-bench: " .. run.err, 1)
    end
    if R then
        local now = getTickCount()
        out.stage, out.index, out.phase = R.def.id, R.index, R.phase
        out.frames = #R.frames
        if R.phase == "sample" then
            out.elapsed_s = SB.round((now - R.sampleStart) / 1000, 1)
        elseif R.phase == "warmup" then
            out.elapsed_s = SB.round(math.max(0, R.warmupMs - (R.phaseEnd - now)) / 1000, 1)
        end
        out.busy = true
    else
        out.busy = false
    end
    return out
end

function C.result(a)
    local r = results[tonumber(a.index) or 1]
    if not r then
        return {error = "no result for stage " .. tostring(a.index)}
    end
    return r
end

function C.finish(a)
    if cur then
        pcall(teardown, cur)
        cur = nil
    end
    restoreEnvironment()
    run.state = "idle"
    return {ok = true, results = SB.count(results)}
end

function C.abort(a)
    local was = cur ~= nil
    C.finish(a)
    run.state = "idle"
    return {ok = true, aborted = was}
end

function C.probe(a)
    detect()
    return snapshot(true)
end

--- Export: bench(cmd, args) -> table. Errors come back as {error = text}.
function bench(cmd, args)
    args = type(args) == "table" and args or {}
    local fn = C[cmd]
    if not fn then
        return {error = "unknown bench command " .. tostring(cmd)}
    end
    local ok, res = pcall(fn, args)
    if not ok then
        return {error = tostring(res)}
    end
    return res
end
