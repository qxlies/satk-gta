"""satk.crash — crash dumps, crash logs, known crashes and culprits (M2-07, M3 C1; report 19). Stdlib only.

``satk crash analyze`` turns a minidump (any writer: MTA, WER, ProcDump, cdb), an MTA ``core.log`` or a
single-player log with a crash report (modloader.log, SA-MP, mod_sa) into a short report: exception,
heuristic stack, ``gta_sa.exe`` frames symbolized through the sa-re symbol DB (:mod:`satk.re`), other
modules as ``module+off``; then the known CrashInfo entry with its solution, the suspects (model IDs
from registers and SCRLog attributed to modloader mods or index layers) and the culprit.

Modules: :mod:`.minidump` (MDMP reader), :mod:`.mta` (MTA streams, trailing sections, logs, dump
names), :mod:`.images` (module files, CALL check), :mod:`.stack` (heuristic walk), :mod:`.analyze`,
:mod:`.info`, :mod:`.find`, :mod:`.synth` (synthetic dumps and game folders), :mod:`.crashlist`
(vendored CrashInfo list), :mod:`.sptext` (single-player crash reports), :mod:`.gamelogs`
(modloader.log, scrlog.log, CLEO log), :mod:`.modfolder` (modloader folder and modloader.ini),
:mod:`.culprit`, :mod:`.advise`, :mod:`.bisect`, :mod:`.ops`.

Python API::

    from satk.crash.minidump import Minidump
    from satk.crash.analyze import analyze_file
    from satk.crash import crashlist

    with Minidump.open(path) as d:
        print(d.exception, [m.name for m in d.modules])
    env = analyze_file(path, limit=10, game=None)   # the envelope of 'satk crash analyze'
    crashlist.match(ip=0x456809)[0].entry.solution()
"""
