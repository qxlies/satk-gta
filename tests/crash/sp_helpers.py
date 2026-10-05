"""Helpers for the single-player crash tests (M3 C1): synthetic game folders, logs and dumps.

Everything is invented text and bytes in the shapes satk reads (no file of the game, a mod or a real log).
"""

from __future__ import annotations

import struct
from pathlib import Path

from satk.crash.synth import DumpBuilder

DEFAULT_INI = """;
;           Mod Loader Folder Config File (test)
;
[Folder.Config]
Profile = Default                 ; Profile to be used

[Profiles.Default.Config]
IgnoreAllMods  = false
ExcludeAllMods = false

[Profiles.Default.Priority]
;MyMod=50

[Profiles.Default.IgnoreFiles]

[Profiles.Default.IgnoreMods]
; Put wildcard to mods to be ignored here
_ignore

[Profiles.Default.IncludeMods]

[Profiles.Default.ExclusiveMods]
"""


def ide(models: dict[int, str], sec: str = "objs") -> str:
    lines = [sec] + [f"{mid}, {name}, {name}, 100, 0" for mid, name in models.items()] + ["end", ""]
    return "\n".join(lines)


def make_game(root: Path, mods: dict[str, dict[str, str | bytes]], *, ini: str | None = DEFAULT_INI,
              logs: dict[str, str] | None = None) -> Path:
    """``root`` with ``modloader/<mod>/<file>`` and logs (``{"scrlog.log": text, ...}``)."""
    ml = root / "modloader"
    ml.mkdir(parents=True, exist_ok=True)
    if ini is not None:
        (ml / "modloader.ini").write_text(ini, encoding="utf-8", newline="\n")
    (ml / ".data").mkdir(exist_ok=True)                  # modloader's own folder: never a mod
    (ml / ".data" / "plugins.ini").write_text("x\n", encoding="utf-8")
    for mod, files in mods.items():
        for rel, data in files.items():
            p = ml / mod / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                p.write_bytes(data)
            else:
                p.write_text(data, encoding="utf-8", newline="\n")
    for rel, text in (logs or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    return root


def sp_dump(exe_path: str, *, ip: int = 0x456809, regs: dict[str, int] | None = None,
            stack_words: dict[int, int] | None = None) -> bytes:
    """A single-player x86 dump: gta_sa.exe at ``exe_path``, one thread crashing at ``ip``."""
    b = DumpBuilder("x86")
    b.add_module(exe_path, 0x400000, 0x1177000, timestamp=0x427101CA)
    b.add_module(str(Path(exe_path).with_name("modloader.asi")), 0x10000000, 0x80000, timestamp=0x5A5E0001)
    stack_va = 0x0019E000
    stack = bytearray(0x800)
    esp = stack_va + 0x400
    for off, v in (stack_words or {}).items():
        struct.pack_into("<I", stack, 0x400 + off, v)
    r = {"eip": ip, "esp": esp, "ebp": 0, "eax": 0, "ecx": 0x9788C0, "esi": 0x9788C0}
    r.update(regs or {})
    b.add_thread(7, r, stack_va, bytes(stack))
    b.set_exception(7, 0xC0000005, ip, [0, 0x2C])
    return b.build()


def modloader_log(*, ip: int = 0x456809, eax: int = 20101, mods: tuple[str, ...] = ("CoolPickups",),
                  reports: int = 1, extra_lines: tuple[str, ...] = ()) -> str:
    """A modloader.log with the mod files it installed and ``reports`` crash reports."""
    lines = ["========================== Mod Loader 0.3.7 ==========================", "",
             "Game version: GTA SA 1.0 US", 'Using profile named "Default".', "",
             'Scanning mods at "modloader\\"...']
    lines += [f'Installing file "modloader\\{m}\\{m.lower()}.ide"' for m in mods]
    lines += ['Failed to install file "modloader\\Broken\\broken.dff"', *extra_lines, "", "Mod Loader has started up!", ""]
    for i in range(reports):
        a = ip + 0x10 * i
        lines += ["========================== Unhandled Exception ==========================",
                  f"Exception Address: 0x{a:08X}", "Exception Code: 0xC0000005",
                  "Module: C:\\Games\\GTA San Andreas\\gta_sa.exe", "Access violation reading location 0x0000002C",
                  f"EAX: 0x{eax:08X}   EBX: 0x00000000   ECX: 0x009788C0", "Backtrace:",
                  f"  => 0x{a:08X} gta_sa.exe+0x{a - 0x400000:X}", "     0x00458E69 gta_sa.exe+0x58E69",
                  "     0x0053C0B2 gta_sa.exe+0x13C0B2", ""]
    return "\n".join(lines)
