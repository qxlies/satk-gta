# rwfury 0.6.1 (vendored, unmodified)

- Upstream: https://github.com/Hancapo/rwfury (author: Hancapo), PyPI project `rwfury`.
- Release: **0.6.1**, sdist `rwfury-0.6.1.tar.gz` uploaded to PyPI on 2026-08-02,
  sha256 `152f90ec0b81b2d88dd6350c86c9177f9a9d612561bcad739f6dd91a173c7999`.
- License: MIT (`LICENSE` here is the upstream file; notice: `data/notices/rwfury.txt`, `NOTICE.md`).
- Contents: the `rwfury/` package, `LICENSE` and `README.md` of the sdist, byte for byte
  (CRLF line endings kept; `.gitattributes` here disables conversion). `SHA256SUMS` lists every
  vendored file; `tests/rw/test_vendor.py` checks it. Do not edit these files: fixes go into
  `src/satk/rw/`, a new upstream release replaces the whole directory.

How satk uses it (`satk.rw.vendor`): satk's own writers (`satk.rw`, librw semantics) do the
writing. rwfury is loaded lazily from this directory and serves as

1. the table of collision surface names (`rwfury.col_materials.ColMaterial`) for `satk col write`;
2. an independent reader in the tests (our DFF/COL decoding is cross-checked against rwfury's);
3. the baseline in `tests/golden/rw_roundtrip.json`: rwfury's own model-rebuild writers reproduce
   0 of the vanilla DFF and COL files bit for bit, which is why satk does not write through them.
