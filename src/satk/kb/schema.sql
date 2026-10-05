-- satk kb: knowledge base about GTA:SA internals (work/kb/kb.sqlite; owner M2-02).
-- Rebuilt from scratch by `satk kb build` (no migrations; PRAGMA user_version = schema version).
-- Code is never copied into the satk repository: this file only lives under work/ and is rebuildable.
PRAGMA user_version = 1;

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

-- One row per input tree: gta-reversed, plugin-sdk, mta-upstream, mta-neon, cleo-ai, research, facts.
CREATE TABLE source(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, title TEXT, license TEXT,
  policy TEXT,            -- local-only | mirror | ours
  repo TEXT, ref TEXT, rev TEXT, files INTEGER);

CREATE TABLE file(id INTEGER PRIMARY KEY, source_id INTEGER, path TEXT);

-- Named things with a location: func | global | vtable | struct | const | enum | define | hookpos.
CREATE TABLE sym(
  id INTEGER PRIMARY KEY, kind TEXT, name TEXT, owner TEXT, addr INTEGER,
  sig TEXT,               -- signature (func), type (global), value (const/enum/define/struct size)
  doc TEXT,               -- short comment next to the definition
  file_id INTEGER, line INTEGER,
  flags TEXT);            -- JSON: reversed, locked, install, decl, ...
CREATE INDEX sym_name ON sym(name COLLATE NOCASE);
CREATE INDEX sym_addr ON sym(addr);
CREATE VIRTUAL TABLE sym_fts USING fts5(name, words, sig, doc, tokenize="unicode61 tokenchars '_'");

-- Class/struct/union layouts. size = asserted size (VALIDATE_SIZE / static_assert), calc = computed.
CREATE TABLE struct(
  id INTEGER PRIMARY KEY, name TEXT, kind TEXT, source_id INTEGER, file_id INTEGER, line INTEGER,
  bases TEXT, size INTEGER, size_line INTEGER, calc INTEGER, vptr INTEGER, nfields INTEGER,
  layout TEXT);           -- ok | mismatch | unverified | partial
CREATE INDEX struct_name ON struct(name COLLATE NOCASE);
CREATE TABLE field(
  struct_id INTEGER, idx INTEGER, name TEXT, type TEXT, off INTEGER, size INTEGER,
  bit INTEGER, bits INTEGER,
  src TEXT,               -- assert | <other source key> | calc
  line INTEGER, note TEXT,
  PRIMARY KEY(struct_id, idx)) WITHOUT ROWID;

-- Script commands (opcodes) from the cleo-ai reference (Sanny Builder Library data).
CREATE TABLE opcode(
  id INTEGER PRIMARY KEY, op TEXT, name TEXT, ext TEXT, class TEXT, member TEXT, nparams INTEGER,
  flags TEXT, descr TEXT, input TEXT, output TEXT, details TEXT, file_id INTEGER, line INTEGER,
  handler TEXT, handler_file_id INTEGER, handler_line INTEGER);
CREATE INDEX opcode_op ON opcode(op);
CREATE INDEX opcode_name ON opcode(name COLLATE NOCASE);
CREATE VIRTUAL TABLE opcode_fts USING fts5(name, words, class, descr, tokenize="unicode61 tokenchars '_'");

-- Curated engine facts (report 24 §6) with automatic checks against the KB and gta_sa.exe bytes.
CREATE TABLE fact(
  key TEXT PRIMARY KEY, topic TEXT, title TEXT, value TEXT, addrs TEXT, refs TEXT, sources TEXT,
  confidence TEXT,        -- code | 2src | exe (letters of report 24: K, 2, E)
  status TEXT,            -- verified | mismatch | unchecked
  checks TEXT,            -- JSON [[check, expected, got, ok], ...]
  note TEXT);
CREATE VIRTUAL TABLE fact_fts USING fts5(key, title, value, note, tokenize="unicode61 tokenchars '_'");

-- Full-text chunks of source files and documents (shown as snippets only).
CREATE VIRTUAL TABLE chunk USING fts5(heading, body, file_id UNINDEXED, line UNINDEXED,
  tokenize="unicode61 tokenchars '_'");
-- gta_sa.exe addresses mentioned in chunks (0x0058... normalized).
CREATE TABLE addr_ref(addr INTEGER, chunk_id INTEGER, line INTEGER);
CREATE INDEX addr_ref_addr ON addr_ref(addr);
