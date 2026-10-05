"""Asset linter ``satk asset lint`` (M2-06): DFF, TXD, COL, IDE and their links.

The checks live in this package (``dff``, ``txd``, ``col``, ``ide``, ``link``); which of them run, their
severity, thresholds and messages are data: ``data/lint_rules.json`` (:mod:`satk.lint.rules`).
:func:`satk.lint.runner.lint` is the Python entry point; ``ops.py`` wraps it as ``asset.lint`` and
``asset.lint_rules``. Stdlib only, the game is only read.

Example::

    from satk.lint.runner import lint
    rep = lint("models/gta3.img/infernus.dff")
    print(rep.summary, [f.row() for f in rep.findings[:5]])
"""
