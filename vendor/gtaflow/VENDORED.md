# Vendored: gta-flow core (`sa_traffic`)

Upstream: https://github.com/Dryxio/gta-flow (package `gta-sa-traffic` 0.1.0a1),
revision `c3d9ad40c0facfbe7351a134ba3601e49be8d6be` (2026-09-09).
License: MIT, copyright 2026 Dryxio. The full license text is in `LICENSE` and the upstream
provenance notice in `NOTICE.md`; both are kept verbatim as the MIT license requires.

Only the pure-Python core is vendored (no Blender bridges, no CLI, no documentation images):

| File | sha256 (LF line endings) |
|---|---|
| `LICENSE` | `087b3964f327ba0730cce414a5c9cdff677d412905a00d5d4356cbb0e4172274` |
| `NOTICE.md` | `0fb39e130827427d1b1a025b4cccb2cf0ec72563bb1f2b3a5c842caf363ac96b` |
| `sa_traffic/__init__.py` | `6cae1063e43b88bdac1b6f06ac9fa824465dae471c20a5ea5cfedf290cdd122c` |
| `sa_traffic/codec.py` | `07277b188f040314b7fc832b3a67849b764d2ab62300150b81d35ed51b6b8bb9` |
| `sa_traffic/document.py` | `7eab7d37bdaed305fd498c29cbdc6fb2690f5a37c964a19fcdcf9e93ee0aeed5` |
| `sa_traffic/compiler.py` | `1e1377d935589f71f6fda90a0f33faa19bfd607887132bf8e2cc690bf88685e8` |
| `sa_traffic/oracle.py` | `24ed08eb4714d73f8965c4e3ae25eaf3183db8ac37263d23b7f729f15a1f06e0` |
| `sa_traffic/controls.py` | `2791f280efb2e7c813a044feb473363987994eb1ee9c37258236bb73e070dfd9` |
| `sa_traffic/signals.py` | `e246d342821008fbbf6deec7ba405586e9bdf2916333048f0127ca547492cc12` |

Local modifications: none. The files are byte-identical to the upstream blobs
(`tests/paths/test_vendor.py` checks the hashes above). Fix bugs in `src/satk/paths/` around the
vendored code, or re-vendor a newer upstream revision and update this table.

How satk uses it: `satk.paths.vendor` loads this directory as the private package
`satk_vendor_gtaflow` (no `sys.path` change, no clash with an installed `sa_traffic`).
`satk paths import` decodes `nodes*.dat` with `codec.decode`; `satk paths compile` rebuilds the
network with `document.import_files` + `compiler.compile_document` and checks the bytes with the
independent `oracle.check`.

Game data is not part of this directory: no `nodes*.dat`, no imported documents.
