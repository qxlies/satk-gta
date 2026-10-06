"""Read the MTA facts of ``data/shader/mta.json`` from an MTA source tree (test helper; names only).

``extract(root)`` parses ``Client/core/Graphics/CRenderItem.EffectParameters.cpp`` (state groups, the register
list per group, the automatic semantics) and ``Client/mods/deathmatch/logic/lua/CLuaFunctionParseHelpers.cpp``
(element type names of ``dxCreateShader``). ``python tests/shader/mta_source.py <mtasa root>`` prints the JSON.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PARAMS = Path("Client/core/Graphics/CRenderItem.EffectParameters.cpp")
HELPERS = Path("Client/mods/deathmatch/logic/lua/CLuaFunctionParseHelpers.cpp")
STAGE_GROUPS = ("stageState", "samplerState", "textureState", "lightState", "lightEnableState")


def extract(root: Path) -> dict:
    src = (root / PARAMS).read_text(encoding="utf-8", errors="replace")
    groups = dict((m.group(2), m.group(1)) for m in re.finditer(r'ADD_ENUM\((STATE_GROUP_\w+),\s*"(\w+)"\)', src))
    by_list = {g[0].upper() + g[1:]: g for g in groups}       # "RenderState" -> "renderState"
    regs: dict[str, dict[str, str]] = {g: {} for g in groups}
    cur = None
    for line in src.splitlines():
        m = re.match(r'#define USING_LIST\s+"(\w+)"', line.strip())
        if m:
            cur = by_list.get(m.group(1))
            continue
        m = re.match(r"ADD_REGISTER\((TYPE_\w+),\s*(\w+)\)", line.strip())
        if m and cur:
            regs[cur][m.group(2)] = m.group(1)[5:].lower()
    sem_block = src[src.index("ReadCommonHandles()\n{"):]
    sem_block = sem_block[:sem_block.index("};")]
    semantics = re.findall(r'\{m_CommonHandles\.\w+,\s*"(\w+)"\}', sem_block)
    helpers = (root / HELPERS).read_text(encoding="utf-8", errors="replace")
    blk = helpers[helpers.index("IMPLEMENT_ENUM_BEGIN(EEntityTypeMask)"):]
    blk = blk[:blk.index("IMPLEMENT_ENUM_END")]
    etypes = re.findall(r'ADD_ENUM\(TYPE_MASK_\w+,\s*"(\w+)"\)', blk)
    return {
        "format": "satk-shader-mta",
        "version": 1,
        "about": "MTA:SA effect facts for 'satk shader check': parameters MTA fills by semantic (or name), the state "
                 "groups of parameter annotations (exact case) with their register names (any case) and readable "
                 "types, groups that take a 'stage,' prefix, the macros MTA defines and the element types of "
                 "dxCreateShader. Extracted from the MTA client source (CRenderItem.EffectParameters.cpp, "
                 "CLuaFunctionParseHelpers.cpp).",
        "semantics": semantics,
        "state_groups": {g: {"stage": g in STAGE_GROUPS, "registers": dict(sorted(regs[g].items()))}
                         for g in sorted(groups)},
        "macros": ["IS_DEPTHBUFFER_RAWZ"],
        "element_types": etypes,
        "profiles": {"vs": ["vs_1_1", "vs_2_0", "vs_2_a", "vs_2_sw", "vs_3_0", "vs_3_sw"],
                     "ps": ["ps_1_1", "ps_1_2", "ps_1_3", "ps_1_4", "ps_2_0", "ps_2_a", "ps_2_b", "ps_2_sw",
                            "ps_3_0", "ps_3_sw"]},
    }


if __name__ == "__main__":
    print(json.dumps(extract(Path(sys.argv[1])), indent=1))
