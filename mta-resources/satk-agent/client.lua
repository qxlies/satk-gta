-- satk-agent client: camera, frame capture, picking and Lua for the satk agent.
-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 satk contributors
--
-- The server relays rpc requests here ("satk:req"); each request runs as a coroutine, resumed
-- once per frame in onClientRender, so a handler can wait for frames (camera changes take
-- effect one frame later, captures need the world to stream in). Replies go back with
-- "satk:res" (a capture as a latent event carrying the PNG).

local SETTLE_FRAMES = 30        -- G0 settle: frames to wait after a preload (models fade in over 16)
local SETTLE_MIN_MS = 300       -- ... and at least this long
local RAY_LEN = 3000
local LATENT_BPS = 20000000     -- bytes/s for the PNG transfer (loopback)
local JOB_TIMEOUT_MS = 60000

local frame = 0
local fps, fpsFrames, fpsT0 = 0, 0, getTickCount()
local camRev = 0
local camFov = 70               -- the MTA fov last given to setCameraMatrix
local fovK = nil                -- measured: tan(hfov/2) = fovK * tan(camFov/2)
local queue = {}
local job = nil
local current = nil
local cancelled = {}
local screenSource, screenSourceW, screenSourceH = nil, 0, 0
local logBuffer = {}
local inLog = false

---------------------------------------------------------------------------- job helpers

local function wait(n)
    for _ = 1, (n or 1) do
        coroutine.yield()
    end
end

local function defer(fn)
    if current then
        current.cleanups[#current.cleanups + 1] = fn
    end
end

local function norm(x, y, z)
    local l = math.sqrt(x * x + y * y + z * z)
    if l < 1e-9 then
        return 0, 1, 0
    end
    return x / l, y / l, z / l
end

local function screenSize()
    local w, h = guiGetScreenSize()
    return math.floor(w), math.floor(h)
end

---------------------------------------------------------------------------- camera

--- Camera position, look point and the measured half-tangents of the window image.
local function measure()
    local cx, cy, cz, lx, ly, lz = getCameraMatrix()
    local fx, fy, fz = norm(lx - cx, ly - cy, lz - cz)
    local W, H = screenSize()
    local function tanAt(sx, sy)
        local wx, wy, wz = getWorldFromScreenPosition(sx, sy, 10)
        if not wx then
            return nil
        end
        local vx, vy, vz = wx - cx, wy - cy, wz - cz
        local along = vx * fx + vy * fy + vz * fz
        if along <= 1e-6 then
            return nil
        end
        local px, py, pz = vx - along * fx, vy - along * fy, vz - along * fz
        return math.sqrt(px * px + py * py + pz * pz) / along
    end
    local tx = tanAt(W, H / 2) or math.tan(math.rad(camFov) / 2)
    local ty = tanAt(W / 2, 0) or (tx * H / W)
    return {pos = {cx, cy, cz}, look = {lx, ly, lz}, tx = tx, ty = ty, W = W, H = H}
end

local function hfovOf(tanHalf)
    return satk.round(2 * math.deg(math.atan(tanHalf)), 3)
end

local function poseOut(m, c)
    return {pos = satk.r3(m.pos[1], m.pos[2], m.pos[3]), look = satk.r3(m.look[1], m.look[2], m.look[3]),
            fov_h_deg = hfovOf(m.tx * (c or 1))}
end

--- Put the camera at pos/look; wantH = horizontal FOV (deg) of the image, whose width is the
--- fraction c of the window (crop). The FOV mapping MTA fov -> image FOV is measured on screen.
local function applyCamera(pos, look, roll, wantH, c)
    local px, py, pz = satk.vec3(pos, "pos")
    local lx, ly, lz = satk.vec3(look, "look")
    roll = tonumber(roll) or 0
    local fov = camFov
    local wantTan = nil
    if wantH then
        wantTan = math.tan(math.rad(satk.num(wantH, "fov_h_deg")) / 2) / (c or 1)
        if fovK then
            fov = satk.clamp(2 * math.deg(math.atan(wantTan / fovK)), 1, 179)
        else
            fov = satk.clamp(2 * math.deg(math.atan(wantTan)), 1, 179)
        end
    end
    camFov = fov
    setCameraMatrix(px, py, pz, lx, ly, lz, roll, fov)
    wait(2)
    local m = measure()
    local f2, k = satk.fovFor(wantTan or m.tx, m.tx, camFov)
    fovK = k
    if wantTan and math.abs(f2 - camFov) > 0.01 then
        camFov = f2
        setCameraMatrix(px, py, pz, lx, ly, lz, roll, f2)
        wait(2)
    end
end

local function saveCamera()
    local target = getCameraTarget()
    local cx, cy, cz, lx, ly, lz, roll = getCameraMatrix()
    return {target = target, m = {cx, cy, cz, lx, ly, lz, roll or 0}, fov = camFov}
end

local function restoreCamera(s)
    if s.target and isElement(s.target) then
        setCameraTarget(s.target)
    else
        local m = s.m
        camFov = s.fov
        setCameraMatrix(m[1], m[2], m[3], m[4], m[5], m[6], m[7], s.fov)
    end
end

---------------------------------------------------------------------------- settle

local function settle(maxFrames, quietFrames, timeoutMs)
    maxFrames = tonumber(maxFrames) or 120
    if maxFrames <= 0 then
        return 0
    end
    local cx, cy, cz = getCameraMatrix()
    pcall(enginePreloadWorldArea, cx, cy, cz, "all")
    local n = satk.clamp(math.min(maxFrames, SETTLE_FRAMES), tonumber(quietFrames) or 1, 1200)
    local t0 = getTickCount()
    local deadline = t0 + (tonumber(timeoutMs) or 5000)
    local waited = 0
    while (waited < n or (getTickCount() - t0 < SETTLE_MIN_MS and waited < maxFrames)) and getTickCount() < deadline do
        wait(1)
        waited = waited + 1
    end
    return waited
end

---------------------------------------------------------------------------- picking

local KINDS = {object = "object", vehicle = "vehicle", ped = "ped", player = "player", marker = "marker"}

local function elementRef(el)
    local t = getElementType(el)
    local id = getElementID(el)
    if not id or id == "" then
        id = tostring(el):match("0x(%x+)") or tostring(el):gsub("%W", "")
    end
    local ex, ey, ez = getElementPosition(el)
    local e = {ref = "el:" .. t .. "/" .. id, kind = KINDS[t] or "object", src = {kind = "runtime", type = t, id = id}}
    if ex then
        e.pos = satk.r3(ex, ey, ez)
    end
    local ok, model = pcall(getElementModel, el)
    if ok and type(model) == "number" then
        e.model_id = model
    end
    return e
end

local function castRay(ox, oy, oz, ex, ey, ez)
    local hit, x, y, z, el, nx, ny, nz, _, _, _, bModel, bx, by, bz =
        processLineOfSight(ox, oy, oz, ex, ey, ez, true, true, true, true, true, false, false, false, localPlayer, true)
    if not hit then
        return {hit = false}
    end
    local dx, dy, dz = x - ox, y - oy, z - oz
    local out = {hit = true, pos = satk.r3(x, y, z), normal = satk.r3(nx or 0, ny or 0, nz or 1, 6),
                 dist = satk.round(math.sqrt(dx * dx + dy * dy + dz * dz), 4)}
    if el and isElement(el) then
        out.entity = elementRef(el)
    elseif bModel then
        local e = {ref = string.format("b%d@%.2f,%.2f,%.2f", bModel, bx, by, bz), kind = "building",
                   model_id = bModel, pos = satk.r3(bx, by, bz), src = {kind = "model_pos"}}
        local name = engineGetModelNameFromID(bModel)
        if name and name ~= "" then
            e.model_name = string.lower(name)
        end
        out.entity = e
    end
    return out
end

---------------------------------------------------------------------------- HUD / env

local function hideHud()
    local chat = isChatVisible()
    setPlayerHudComponentVisible("all", false)
    showChat(false)
    return function()
        setPlayerHudComponentVisible("all", true)
        showChat(chat)
    end
end

local function applyEnv(env)
    local h, m = getTime()
    local w = getWeather()
    local saved = {h, m, w}
    if env.time then
        local th, tm = satk.parseTime(env.time)
        setTime(th, tm)
    end
    if env.weather then
        setWeather(env.weather)
    end
    return function()
        setTime(saved[1], saved[2])
        setWeather(saved[3])
    end
end

local function getScreenSource(W, H)
    if not screenSource or screenSourceW ~= W or screenSourceH ~= H then
        if screenSource and isElement(screenSource) then
            destroyElement(screenSource)
        end
        screenSource = dxCreateScreenSource(W, H)
        screenSourceW, screenSourceH = W, H
        if not screenSource then
            satk.fail("INTERNAL", "dxCreateScreenSource failed (out of video memory?)")
        end
    end
    return screenSource
end

---------------------------------------------------------------------------- handlers

local H = {}

H["status"] = function(p)
    local m = measure()
    local th, tm = getTime()
    local W, Hh = screenSize()
    return {frame = frame, fps = satk.round(fps, 1), pose = poseOut(m),
            env = {time = satk.formatTime(th, tm), weather = getWeather()},
            streaming = {pending = 0}, rev = {scene = 0, camera = camRev},
            window = {w = W, h = Hh, focused = isMTAWindowActive() == true}}
end

H["camera.get"] = function(p)
    local m = measure()
    local pose = poseOut(m)
    return {pose = pose, fov_h_deg = pose.fov_h_deg, near = satk.round(getNearClipDistance(), 3),
            far = satk.round(getFarClipDistance(), 1), rev = camRev}
end

H["camera.set"] = function(p)
    if p.expect_rev ~= nil and p.expect_rev ~= camRev then
        satk.fail("REVISION", "camera revision is " .. camRev .. ", not " .. tostring(p.expect_rev), {rev = camRev})
    end
    applyCamera(p.pos, p.look, p.roll, p.fov_h_deg, 1)
    camRev = camRev + 1
    local out = {rev = camRev}
    local stream = p.stream or "none"
    if stream ~= "none" then
        out.frames = settle(120, 2, 5000)
        out.settled = out.frames >= 16
    end
    out.pose = poseOut(measure())
    return out
end

H["camera.release"] = function(p)
    setCameraTarget(localPlayer)
    camRev = camRev + 1
    return {}
end

H["world.settle"] = function(p)
    local f = settle(p.max_frames or 120, p.quiet_frames or 2, p.timeout_ms or 5000)
    return {settled = f >= 16, frames = f, pending = 0}
end

H["capture"] = function(p)
    local st = dxGetStatus()
    if st and st.AllowScreenUpload == false then
        satk.fail("NOT_READY", "screen upload is disabled in the MTA settings (allow_screen_upload): captures would be blank",
            {hint = "MTA settings -> Advanced -> Allow screen upload: on"})
    end
    local W, H_ = screenSize()
    local w = math.floor(tonumber(p.w) or W)
    local h = math.floor(tonumber(p.h) or H_)
    local m0 = measure()
    local r = (m0.tx > 0 and m0.ty > 0) and (m0.ty / m0.tx) or (H_ / W)
    local u, v, us, vs, c = satk.cropFor(W, H_, w, h, r)
    if p.pos then
        local saved = saveCamera()
        defer(function() restoreCamera(saved) end)
        applyCamera(p.pos, p.look, p.roll, p.fov_h_deg, c)
        camRev = camRev + 1
        defer(function() camRev = camRev + 1 end)
    end
    if not p.keep_hud then
        defer(hideHud())
    end
    if type(p.env) == "table" and (p.env.time or p.env.weather) then
        defer(applyEnv(p.env))
    end
    local settleParams = type(p.settle) == "table" and p.settle or {}
    local maxFrames = tonumber(settleParams.max_frames) or 120
    local frames = settle(maxFrames, settleParams.quiet_frames or 2, 10000)
    if frames == 0 then
        wait(2)  -- let the hidden HUD and the new camera reach the back buffer
    end
    local src = getScreenSource(W, H_)
    dxUpdateScreenSource(src, true)
    local m = measure()
    local pixels
    if w == W and h == H_ and us == W and vs == H_ then
        pixels = dxGetTexturePixels(src)
    else
        local rt = dxCreateRenderTarget(w, h, false)
        if not rt then
            satk.fail("INTERNAL", "dxCreateRenderTarget(" .. w .. "x" .. h .. ") failed")
        end
        defer(function() destroyElement(rt) end)
        dxSetRenderTarget(rt, true)
        dxDrawImageSection(0, 0, w, h, u, v, us, vs, src)
        dxSetRenderTarget()
        wait(1)
        pixels = dxGetTexturePixels(rt)
    end
    if not pixels then
        satk.fail("INTERNAL", "dxGetTexturePixels failed")
    end
    local pw, ph = dxGetPixelsSize(pixels)
    if pw ~= w or ph ~= h then
        satk.fail("NOT_READY", string.format("got a %sx%s placeholder instead of %dx%d: screen upload is blocked",
            tostring(pw), tostring(ph), w, h))
    end
    local png = dxConvertPixels(pixels, "png")
    if not png then
        satk.fail("INTERNAL", "dxConvertPixels(png) failed")
    end
    return {w = w, h = h, pose = poseOut(m, c), frame = frame, settled = maxFrames > 0 and frames >= 16,
            frames_waited = frames, window = {w = W, h = H_}}, png
end

H["pick"] = function(p)
    local m = measure()
    local W, Hh = screenSize()
    local hits = {}
    for i, pt in ipairs(p.points or {}) do
        local px, py = tonumber(pt[1]) or -1, tonumber(pt[2]) or -1
        local hit
        if px < 0 or py < 0 or px >= W or py >= Hh then
            hit = {hit = false}
        else
            local ex, ey, ez = getWorldFromScreenPosition(px + 0.5, py + 0.5, RAY_LEN)
            if ex then
                hit = castRay(m.pos[1], m.pos[2], m.pos[3], ex, ey, ez)
            else
                hit = {hit = false}
            end
        end
        hit.px, hit.py = pt[1], pt[2]
        hits[i] = hit
    end
    return {hits = hits}
end

--- Rays built by satk (capture space / raycast): {rays = {{ox,oy,oz,ex,ey,ez}...}, preload = {x,y,z}}.
H["_rays"] = function(p)
    if type(p.preload) == "table" then
        local x, y, z = satk.vec3(p.preload, "preload")
        pcall(enginePreloadWorldArea, x, y, z, "collisions")
    end
    local hits = {}
    for i, r in ipairs(p.rays or {}) do
        hits[i] = castRay(r[1], r[2], r[3], r[4], r[5], r[6])
    end
    return {hits = hits}
end

H["_lua"] = function(p)
    local r = satk.execLua(p.code, "satk")
    r.side = "client"
    return r
end

---------------------------------------------------------------------------- scheduler

local function finish(j, ok, result, blob)
    for i = #j.cleanups, 1, -1 do
        pcall(j.cleanups[i])
    end
    if cancelled[j.rid] then
        cancelled[j.rid] = nil
        return
    end
    if ok then
        if blob then
            triggerLatentServerEvent("satk:res", LATENT_BPS, false, resourceRoot, j.rid, true, result, blob)
        else
            triggerServerEvent("satk:res", resourceRoot, j.rid, true, result or {})
        end
    else
        triggerServerEvent("satk:res", resourceRoot, j.rid, false, satk.errorObject(result))
    end
end

local function step()
    if not job then
        job = table.remove(queue, 1)
        if not job then
            return
        end
        local handler = H[job.method]
        if not handler then
            local j = job
            job = nil
            finish(j, false, {satk_error = true, code = "UNKNOWN_METHOD", message = "client has no method " .. tostring(j.method)})
            return
        end
        local params = job.params
        job.co = coroutine.create(function()
            return handler(params)
        end)
        job.t0 = getTickCount()
    end
    if cancelled[job.rid] or getTickCount() - job.t0 > JOB_TIMEOUT_MS then
        local j = job
        job = nil
        finish(j, false, {satk_error = true, code = "TIMEOUT", message = j.method .. " ran longer than " .. JOB_TIMEOUT_MS .. " ms"})
        return
    end
    current = job
    local r = satk.pack(coroutine.resume(job.co))
    current = nil
    if coroutine.status(job.co) == "dead" then
        local j = job
        job = nil
        if r[1] then
            finish(j, true, r[2], r[3])
        else
            finish(j, false, r[2])
        end
    end
end

addEventHandler("onClientRender", root, function()
    frame = frame + 1
    fpsFrames = fpsFrames + 1
    local now = getTickCount()
    if now - fpsT0 >= 1000 then
        fps = fpsFrames * 1000 / (now - fpsT0)
        fpsFrames, fpsT0 = 0, now
    end
    step()
end)

addEvent("satk:req", true)
addEventHandler("satk:req", resourceRoot, function(rid, method, params)
    queue[#queue + 1] = {rid = rid, method = method, params = type(params) == "table" and params or {}, cleanups = {}}
end)

addEvent("satk:cancel", true)
addEventHandler("satk:cancel", resourceRoot, function(rid)
    cancelled[rid] = true
    for i = #queue, 1, -1 do
        if queue[i].rid == rid then
            table.remove(queue, i)
        end
    end
end)

---------------------------------------------------------------------------- logs

local LEVELS = {[0] = "info", [1] = "error", [2] = "warn", [3] = "info", [4] = "info"}

addEventHandler("onClientDebugMessage", root, function(message, level, file, line)
    if inLog or #logBuffer >= 200 then
        return
    end
    logBuffer[#logBuffer + 1] = {stream = "script", level = LEVELS[level] or "info", msg = tostring(message),
                                 file = file, line = line}
end)

setTimer(function()
    if #logBuffer == 0 then
        return
    end
    inLog = true
    local items = logBuffer
    logBuffer = {}
    triggerServerEvent("satk:log", resourceRoot, items)
    inLog = false
end, 500, 0)

---------------------------------------------------------------------------- start

local function hidePed()
    -- A camera-only agent hides its ped; a server that lets the player play (satk-testdrive) sets
    -- the element data "satk.keep_ped" on the player and the ped stays as it is.
    if getElementData and getElementData(localPlayer, "satk.keep_ped") then
        return
    end
    setElementAlpha(localPlayer, 0)
    setElementFrozen(localPlayer, true)
    setElementCollisionsEnabled(localPlayer, false)
end

addEventHandler("onClientPlayerSpawn", localPlayer, hidePed)

addEventHandler("onClientElementDataChange", localPlayer, function(name, _, new)
    if name == "satk.keep_ped" and new then
        setElementAlpha(localPlayer, 255)
        setElementFrozen(localPlayer, false)
        setElementCollisionsEnabled(localPlayer, true)
    end
end)

addEventHandler("onClientResourceStart", resourceRoot, function()
    local W, H_ = screenSize()
    local st = dxGetStatus() or {}
    local v = getVersion() or {}
    hidePed()
    triggerServerEvent("satk:hello", resourceRoot, {w = W, h = H_, version = v.sortable or v.mta,
                                                     screen_upload = st.AllowScreenUpload ~= false,
                                                     fps_limit = getFPSLimit()})
end)
