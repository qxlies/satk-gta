-- satk sa-re symbol DB, schema v1: work/re/symdb.sqlite (rebuilt in seconds; never hand-edited)
-- SPEC §4.9.2 (owner WP-09). Additive extensions over the SPEC DDL are marked "WP-09 ext":
--   * func.origin also allows 'data_ptr' (16-aligned pointer from .rdata/.data into .text) and
--     'padding' (16-aligned address after ret/jmp + nop/int3 padding): extra *unnamed* starts
--     that make address->function attribution honest without Ghidra (SPEC §4.9.3 step 3/4);
--   * func.origin 'gta_reversed' supplements hooks.json with source Install declarations;
--     'thunk_scan' adds E9 entries into .HOODLUM. These starts have tentative confidence;
--   * func_range preserves optional Ghidra ranges, including holes and detached blocks,
--     with canonical thunk owners. Readers still accept v1 databases without this table;
--   * limit_def.kind also allows 'world' (world bounds/sectors from engine/mtasa/docs/limits.toml);
--   * extra indexes (func_alias_qual, vtable_slot_target, patch_symbol, callsite_*).
-- patch.origin 'satk' is reserved for our own patches (SPEC).
PRAGMA user_version = 1;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
-- keys: schema_version, satk_version, built_at, exe_path, exe_sha256, image_base, stats (JSON)

CREATE TABLE source_rev(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('gta-reversed','plugin-sdk','mta-upstream','mta-trunk','neon','exe','ghidra','manual')),
  repo TEXT NOT NULL,                 -- 'src/gta-reversed' | 'engine/mtasa' | 'gta-sa-clean/gta_sa.exe'
  rev TEXT NOT NULL,                  -- git sha or file sha256
  scanned_at TEXT NOT NULL,
  UNIQUE(kind, rev));

CREATE TABLE section(
  name TEXT NOT NULL, va_start INTEGER NOT NULL PRIMARY KEY, va_end INTEGER NOT NULL,
  file_off INTEGER NOT NULL, flags INTEGER NOT NULL) WITHOUT ROWID;

CREATE TABLE func(
  addr     INTEGER PRIMARY KEY,
  end_addr INTEGER,                   -- exclusive maximum; exact ranges may have holes (func_range)
  bounds   TEXT NOT NULL DEFAULT 'next_start' CHECK (bounds IN ('next_start','ghidra','manual')),
  name     TEXT,                      -- 'Update'
  cls      TEXT,                      -- 'CPed'
  qual     TEXT,                      -- 'CPed::Update' (NULL for anonymous call targets)
  origin   TEXT NOT NULL CHECK (origin IN ('hooks_json','plugin_sdk','vtable','call_target','ghidra','manual',
                                           'data_ptr','padding','gta_reversed','thunk_scan')),
  rev_id   INTEGER REFERENCES source_rev(id),
  src_file TEXT, src_line INTEGER,    -- gta-reversed InstallSourceLocation
  reversed INTEGER, hook_state TEXT, locked INTEGER, category TEXT,
  hoodlum_body INTEGER                -- body VA in .HOODLUM when the entry is a JMP thunk
) WITHOUT ROWID;
CREATE INDEX func_qual ON func(qual COLLATE NOCASE);

-- Optional exact, discontiguous code ranges (half-open). Old v1 readers ignore this table.
CREATE TABLE func_range(
  start_addr INTEGER PRIMARY KEY, end_addr INTEGER NOT NULL,
  func_addr INTEGER NOT NULL REFERENCES func(addr), body_addr INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX func_range_func ON func_range(func_addr);

CREATE TABLE func_alias(addr INTEGER NOT NULL, qual TEXT NOT NULL, origin TEXT NOT NULL,
  PRIMARY KEY(addr, qual, origin)) WITHOUT ROWID;
CREATE INDEX func_alias_qual ON func_alias(qual COLLATE NOCASE);

CREATE TABLE global(
  addr INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT, elem_type TEXT,
  array_len INTEGER, elem_size INTEGER, byte_size INTEGER,
  origin TEXT NOT NULL, src_file TEXT, src_line INTEGER) WITHOUT ROWID;
CREATE INDEX global_name ON global(name COLLATE NOCASE);

CREATE TABLE vtable(addr INTEGER PRIMARY KEY, cls TEXT NOT NULL, slots INTEGER NOT NULL,
  src_file TEXT, src_line INTEGER) WITHOUT ROWID;
CREATE TABLE vtable_slot(vt INTEGER NOT NULL REFERENCES vtable(addr), slot INTEGER NOT NULL,
  target INTEGER NOT NULL, PRIMARY KEY(vt, slot)) WITHOUT ROWID;
CREATE INDEX vtable_slot_target ON vtable_slot(target);

CREATE TABLE struct_size(name TEXT PRIMARY KEY, size INTEGER NOT NULL,
  src_file TEXT, src_line INTEGER) WITHOUT ROWID;

CREATE TABLE thunk(addr INTEGER PRIMARY KEY, target INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('hoodlum','seh_push_jmp','other_jmp'))) WITHOUT ROWID;
CREATE INDEX thunk_target ON thunk(target);

CREATE TABLE patch(
  id INTEGER PRIMARY KEY,
  rev_id INTEGER NOT NULL REFERENCES source_rev(id),
  origin TEXT NOT NULL CHECK (origin IN ('upstream','trunk','neon','satk')),
  addr INTEGER NOT NULL, len INTEGER,
  kind TEXT NOT NULL CHECK (kind IN ('hookpos','hookinstall','hookinstallcall','memput','memset','memcpy','vtable_slot','reloc_manifest','code_mover')),
  symbol TEXT,                        -- 'HOOKPOS_CStreaming_Update_Caller'
  src_file TEXT NOT NULL, src_line INTEGER NOT NULL,
  func_addr INTEGER, func_off INTEGER, note TEXT);
CREATE INDEX patch_addr ON patch(addr);
CREATE INDEX patch_func ON patch(func_addr);
CREATE INDEX patch_symbol ON patch(symbol COLLATE NOCASE);

CREATE TABLE callsite(site INTEGER NOT NULL, callee INTEGER NOT NULL, caller INTEGER,
  kind TEXT NOT NULL, origin TEXT NOT NULL, PRIMARY KEY(site, callee)) WITHOUT ROWID;
CREATE INDEX callsite_callee ON callsite(callee);
CREATE INDEX callsite_caller ON callsite(caller);

CREATE TABLE limit_def(               -- arrays/pools/id ranges for the asset linter and engine work
  name TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('pool','array','id_range','streaming','store','world')),  -- WP-09 ext: world
  vanilla INTEGER NOT NULL, global_addr INTEGER,
  trunk INTEGER,                      -- value in our fork (engine/mtasa/docs/limits.toml), NULL until set
  neon INTEGER, source TEXT) WITHOUT ROWID;

CREATE VIRTUAL TABLE sym_fts USING fts5(sid UNINDEXED, kind UNINDEXED, qual, tokenize = 'trigram');
