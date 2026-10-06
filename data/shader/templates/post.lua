-- {{NAME}}: {{ABOUT}}
-- Made by satk (template {{TEMPLATE}}); MIT licence. Client script of an MTA:SA resource.
-- Toggle in game with the command /{{COMMAND}}.

local FX_FILE = "{{FX}}"
local screenWidth, screenHeight = guiGetScreenSize()
local shader, screenSource

{{VALUES}}
local function draw()
    if not shader then
        return
    end
    -- onClientHUDRender runs before the HUD is drawn, so the HUD stays untouched.
    dxUpdateScreenSource(screenSource, true)
    dxDrawImage(0, 0, screenWidth, screenHeight, shader)
end

local function enable()
    screenSource = dxCreateScreenSource(screenWidth, screenHeight)
    shader = dxCreateShader(FX_FILE)
    if not shader or not screenSource then
        outputDebugString("{{NAME}}: dxCreateShader or dxCreateScreenSource failed", 1)
        return false
    end
    dxSetShaderValue(shader, "gScreenSource", screenSource)
    applyValues()
    addEventHandler("onClientHUDRender", root, draw)
    return true
end

local function disable()
    removeEventHandler("onClientHUDRender", root, draw)
    if shader then
        destroyElement(shader)
        shader = nil
    end
    if screenSource then
        destroyElement(screenSource)
        screenSource = nil
    end
end

addEventHandler("onClientResourceStart", resourceRoot, enable)

addCommandHandler("{{COMMAND}}", function()
    if shader then
        disable()
    else
        enable()
    end
    outputChatBox("{{NAME}}: " .. (shader and "on" or "off"))
end)
