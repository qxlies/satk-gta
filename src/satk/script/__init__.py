"""satk.script -- GTA:SA SCM/CLEO scripts: disassembler, assembler, static checks, templates.

Reads CLEO 4/5 custom scripts (``.cs .cs4 .cs5 .cm .s``), streamed scripts (``.scm`` entries of
``script.img``) and ``main.scm`` with its headers, prints a low-level text form close to Sanny
Builder's opcode syntax (``0001: wait 0``), and assembles that text back to the same bytes.
Stdlib only. Modules:

* :mod:`~satk.script.opdb` - opcode database (the kb, the cleo-ai reference, or the bundled core subset);
* :mod:`~satk.script.scm` - binary parameter types, instruction decoding and encoding;
* :mod:`~satk.script.disasm` - control-flow disassembler and the text writer;
* :mod:`~satk.script.asm` - text parser and two-pass assembler with line-numbered errors;
* :mod:`~satk.script.check` - static checks over a program;
* :mod:`~satk.script.templates` - starter scripts (``satk script new``);
* :mod:`~satk.script.ops` - ``satk script disasm|asm|check|new``.

Example::

    from satk.script.opdb import load_db
    from satk.script.disasm import disassemble
    from satk.script.asm import assemble
    db = load_db("auto")
    text = disassemble(data, kind="cleo", db=db).text
    assert assemble(text, db=db).data == data
"""
