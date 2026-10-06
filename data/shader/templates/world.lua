-- {{NAME}}: {{ABOUT}}
-- Made by satk (template {{TEMPLATE}}); MIT licence. Client script of an MTA:SA resource.
-- World textures: {{SELECTION}}
-- Toggle in game with the command /{{COMMAND}}.

-- dxCreateShader arguments: PRIORITY (higher draws over other shaders on the same texture), MAX_DISTANCE in
-- metres (0 = no limit), LAYERED (true = an extra layer over the original texture) and the element types.
local FX_FILE = "{{FX}}"
local PRIORITY = {{PRIORITY}}
local MAX_DISTANCE = {{MAX_DISTANCE}}
local LAYERED = {{LAYERED}}
local ELEMENT_TYPES = "{{ELEMENT_TYPES}}"

-- MTA matches lower-case texture names with * and ?; the last matching call wins, so removals come last.
local APPLY = {{APPLY}}
local REMOVE = {{REMOVE}}

local shader
{{VALUES}}
{{UPDATE}}
local function enable()
    shader = dxCreateShader(FX_FILE, PRIORITY, MAX_DISTANCE, LAYERED, ELEMENT_TYPES)
    if not shader then
        outputDebugString("{{NAME}}: dxCreateShader failed, see the debug console for the effect error", 1)
        return false
    end
{{SETUP}}
    for _, pattern in ipairs(APPLY) do
        engineApplyShaderToWorldTexture(shader, pattern)
    end
    for _, pattern in ipairs(REMOVE) do
        engineRemoveShaderFromWorldTexture(shader, pattern)
    end
    update()
    return true
end

local function disable()
    if shader then
        destroyElement(shader)
        shader = nil
    end
{{TEARDOWN}}
end

addEventHandler("onClientResourceStart", resourceRoot, function()
    enable()
{{TIMER}}end)

addEventHandler("onClientResourceStop", resourceRoot, disable)

addCommandHandler("{{COMMAND}}", function()
    if shader then
        disable()
    else
        enable()
    end
    outputChatBox("{{NAME}}: " .. (shader and "on" or "off"))
end)
