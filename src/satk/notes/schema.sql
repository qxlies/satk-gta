-- satk persistent knowledge, schema v1: work/notes.sqlite (NOT rebuilt by index build; exported to tools/data/notes/*.jsonl)
-- SPEC §4.5. Owner: WP-06. Change only together with SCHEMA_VERSION in satk/notes/db.py (migrations there).
PRAGMA user_version = 1;
CREATE TABLE note(
  id INTEGER PRIMARY KEY,
  sid TEXT NOT NULL,                  -- any SID: model:411, tex:bistro/vent_64, fn:0x53bf09, inst:lae2_stream0#4
  author TEXT NOT NULL,               -- 'claude' | 'human:<name>' | 'import:gta-scout'
  lang TEXT NOT NULL DEFAULT 'ru',
  text TEXT NOT NULL,
  tags TEXT,                          -- space separated
  confidence REAL,
  evidence TEXT,                      -- capture id / png sha256 / file:line
  created_at TEXT NOT NULL,
  UNIQUE(sid, author, text));
CREATE INDEX note_sid ON note(sid);
CREATE VIRTUAL TABLE note_fts USING fts5(sid UNINDEXED, text, tags, content = 'note', content_rowid = 'id',
  tokenize = 'unicode61 remove_diacritics 2');
CREATE TRIGGER note_ai AFTER INSERT ON note BEGIN
  INSERT INTO note_fts(rowid, sid, text, tags) VALUES (new.id, new.sid, new.text, new.tags); END;
CREATE TRIGGER note_ad AFTER DELETE ON note BEGIN
  INSERT INTO note_fts(note_fts, rowid, sid, text, tags) VALUES ('delete', old.id, old.sid, old.text, old.tags); END;

CREATE TABLE bookmark(
  name TEXT PRIMARY KEY,              -- 'grove_center'
  pose TEXT NOT NULL,                 -- JSON SAAP Pose
  env TEXT,                           -- JSON {time, weather}
  note TEXT, created_at TEXT NOT NULL) WITHOUT ROWID;

CREATE TABLE capture(
  id TEXT PRIMARY KEY,                -- 'cap:20261004-153201-ab12'
  path TEXT NOT NULL, target TEXT NOT NULL, pose TEXT NOT NULL, env TEXT,
  backend_build TEXT, w INTEGER, h INTEGER, created_at TEXT NOT NULL) WITHOUT ROWID;
