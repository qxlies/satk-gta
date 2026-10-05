"""satk.mapconv -- mapping converters: SA-MP Pawn <-> MTA ``.map`` <-> text IPL <-> JSON (M2-04, report 23).

Stdlib only. Modules:

* :mod:`~satk.mapconv.scene` - the canonical :class:`~satk.mapconv.scene.Scene` and its JSON form;
* :mod:`~satk.mapconv.rot` - Euler (SA-MP/MTA, ``CMatrix::SetRotate``) <-> matrix <-> quaternion <-> IPL;
* :mod:`~satk.mapconv.pawn` - literal-only Pawn reader and writer;
* :mod:`~satk.mapconv.mta` - MTA ``.map`` reader and writer (+ satk material extensions);
* :mod:`~satk.mapconv.ipl` - IPL reader (text/``bnry``, engine rotation rule) and text writer;
* :mod:`~satk.mapconv.convert` - format detection, sources (files, ``ipl:`` SIDs), loss checks;
* :mod:`~satk.mapconv.validate` - structural and index checks;
* :mod:`~satk.mapconv.ops` - ``satk map convert`` and ``satk map validate``.

Example::

    from satk.mapconv.pawn import read_pawn
    from satk.mapconv.mta import write_mta
    text = write_mta(read_pawn(open("my.pwn", "rb").read()))
"""
