-- satk index, schema v3. One file per load profile: work/index/<profile>.sqlite
PRAGMA user_version = 3;

-- ================= meta, layers, sources
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
-- keys: schema_version, satk_version, profile, root, dat_files(json), img_order(json), assumptions(json),
--       built_at, build_seconds, content_hash, game_exe_sha256

CREATE TABLE layer(
  id       INTEGER PRIMARY KEY,
  name     TEXT NOT NULL UNIQUE,      -- vanilla | samp | modded | modloader:<mod> | mta:<res> | project:<name>
  kind     TEXT NOT NULL CHECK (kind IN ('vanilla','samp','modded','modloader','mta','project')),
  priority INTEGER NOT NULL,          -- larger = applied later (wins IDE overrides)
  root     TEXT NOT NULL              -- absolute path, display only; never part of a SID
);

CREATE TABLE source(                  -- every physical file the indexer looked at
  id         INTEGER PRIMARY KEY,
  layer_id   INTEGER NOT NULL REFERENCES layer(id),
  relpath    TEXT NOT NULL COLLATE NOCASE,   -- 'models/gta3.img', 'data/maps/la/lae2.ipl' (forward slashes)
  kind       TEXT NOT NULL CHECK (kind IN ('img','dat','ide','ipl','zon','txd','dff','col','ifp','other')),
  size       INTEGER NOT NULL,
  mtime_ns   INTEGER NOT NULL,
  sha256     TEXT,                    -- copied from MANIFEST.sha256 / hashcache when available
  load_ref   TEXT,                    -- 'exe:InitImageList' | 'data/gta.dat:42' | 'data/default.two:9' | NULL = present, not loaded
  load_order INTEGER,                 -- IMG registration order in this profile (0 = first = wins)
  UNIQUE(layer_id, relpath)
);

CREATE TABLE blob(                    -- an IMG directory entry or a loose file
  id              INTEGER PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES source(id),
  idx             INTEGER NOT NULL,   -- index in IMG directory; 0 for loose files
  name            TEXT NOT NULL COLLATE NOCASE,  -- 'infernus.dff'
  stem            TEXT NOT NULL COLLATE NOCASE,  -- 'infernus'
  ext             TEXT NOT NULL COLLATE NOCASE,  -- 'dff'
  abs_off         INTEGER NOT NULL,   -- byte offset inside source
  size            INTEGER NOT NULL,   -- bytes per directory (sectors*2048) or file size
  rw_size         INTEGER,            -- real payload size by RW chunk header (NULL for non-RW)
  stream_sectors  INTEGER,            -- raw VER2 u16 'Size'
  archive_sectors INTEGER,            -- raw VER2 u16 'SizeInArchive' (engine prefers it when != 0)
  hash            BLOB,               -- blake2b-128 of the first rw_size (or size) bytes
  ns              TEXT NOT NULL CHECK (ns IN ('main','player','anim','cuts','loose')),
  active          INTEGER NOT NULL DEFAULT 1,     -- 1 = winner of 'first registered archive wins' in (ns, stem, ext)
  shadowed_by     INTEGER REFERENCES blob(id),
  UNIQUE(source_id, idx)
);
CREATE INDEX blob_ns_stem ON blob(ns, stem, ext);
CREATE INDEX blob_hash    ON blob(hash);

-- ================= textures
CREATE TABLE txd(
  id         INTEGER PRIMARY KEY,
  blob_id    INTEGER NOT NULL UNIQUE REFERENCES blob(id),
  name       TEXT NOT NULL COLLATE NOCASE,
  rw_version INTEGER NOT NULL,
  device_id  INTEGER,                 -- unreliable (38 empty TXDs claim PS2): informational only
  tex_count  INTEGER NOT NULL,
  parent     TEXT COLLATE NOCASE,     -- resolved parent TXD name (txdp or implicit 'vehicle')
  parent_via TEXT CHECK (parent_via IN ('txdp','vehicle'))
);
CREATE INDEX txd_name ON txd(name);

CREATE TABLE image(                   -- unique pixel content of mip0 (+palette); SID pix:<hex>
  hash      BLOB PRIMARY KEY,         -- blake2b-96
  w         INTEGER NOT NULL,
  h         INTEGER NOT NULL,
  d3dfmt    TEXT NOT NULL,
  nbytes    INTEGER NOT NULL,
  mean_rgba INTEGER                   -- average colour from DXT block endpoints (cheap preview/search)
) WITHOUT ROWID;
-- PNG cache path is derived, never stored: work/cache/tex/<hex[0:2]>/<hex>.png (+ @<size>.png)

CREATE TABLE texture(
  id         INTEGER PRIMARY KEY,
  txd_id     INTEGER NOT NULL REFERENCES txd(id),
  idx        INTEGER NOT NULL,
  name       TEXT NOT NULL COLLATE NOCASE,
  mask       TEXT COLLATE NOCASE,
  platform   INTEGER NOT NULL,        -- 8 (D3D8) | 9 (D3D9) | other = unsupported (row kept, no pixels)
  raster_fmt INTEGER NOT NULL,
  d3dfmt     TEXT NOT NULL,           -- DXT1|DXT3|DXT5|A8R8G8B8|X8R8G8B8|R5G6B5|A1R5G5B5|A4R4G4B4|L8|A8L8|PAL8|PAL4
  w INTEGER NOT NULL, h INTEGER NOT NULL, depth INTEGER, levels INTEGER NOT NULL,
  alpha      INTEGER NOT NULL,        -- effective alpha: DXT1 + raster 565 => 0
  filter INTEGER, uaddr INTEGER, vaddr INTEGER,
  data_off   INTEGER NOT NULL,        -- absolute offset of mip0 inside source file
  data_size  INTEGER NOT NULL,
  pal_off    INTEGER,                 -- absolute offset of palette (PAL4/PAL8)
  hash       BLOB NOT NULL REFERENCES image(hash),
  UNIQUE(txd_id, idx)
);
CREATE INDEX texture_name ON texture(name);
CREATE INDEX texture_hash ON texture(hash);

-- ================= models (DFF)
CREATE TABLE dff(
  id INTEGER PRIMARY KEY,
  blob_id INTEGER NOT NULL UNIQUE REFERENCES blob(id),
  name TEXT NOT NULL COLLATE NOCASE,
  rw_version INTEGER NOT NULL,
  clumps INTEGER, atomics INTEGER, frames INTEGER, geoms INTEGER, materials INTEGER,
  verts INTEGER, tris INTEGER,        -- tris after strip expansion (degenerates dropped)
  bmin_x REAL, bmin_y REAL, bmin_z REAL, bmax_x REAL, bmax_y REAL, bmax_z REAL,
  bs_x REAL, bs_y REAL, bs_z REAL, bs_r REAL,
  flags INTEGER NOT NULL DEFAULT 0,   -- 1 skin|2 hanim|4 prelit|8 night|16 normals|32 2dfx|64 embedded_col
                                      -- |128 uvanim|256 matfx|512 reflection|1024 specular|2048 breakable|4096 multi_clump
  plugins TEXT                        -- sorted hex plugin ids, informational: '0x50e,0x253f2f8'
);
CREATE INDEX dff_name ON dff(name);

CREATE TABLE dff_frame(
  dff_id INTEGER NOT NULL REFERENCES dff(id), idx INTEGER NOT NULL,
  parent INTEGER, name TEXT COLLATE NOCASE, atomic INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(dff_id, idx)) WITHOUT ROWID;

CREATE TABLE dff_geom(
  dff_id INTEGER NOT NULL REFERENCES dff(id), idx INTEGER NOT NULL,
  rw_flags INTEGER NOT NULL, verts INTEGER, tris INTEGER, uv_sets INTEGER, strip INTEGER,
  frame INTEGER, bs_x REAL, bs_y REAL, bs_z REAL, bs_r REAL,
  geom_off INTEGER NOT NULL,          -- offset of the Geometry chunk inside the blob (lazy mesh decode)
  PRIMARY KEY(dff_id, idx)) WITHOUT ROWID;

CREATE TABLE dff_mat(
  dff_id INTEGER NOT NULL REFERENCES dff(id), geom INTEGER NOT NULL, idx INTEGER NOT NULL,
  rgba INTEGER NOT NULL, texture TEXT COLLATE NOCASE, mask TEXT COLLATE NOCASE,
  fx INTEGER NOT NULL DEFAULT 0,      -- bits: 1 env|2 bump|4 dual|8 uvanim|16 reflection|32 specular
  color_slot INTEGER,                 -- 1..4 recolourable vehicle material (VehicleModelInfo.cpp:805-812)
  PRIMARY KEY(dff_id, geom, idx)) WITHOUT ROWID;
CREATE INDEX dff_mat_tex ON dff_mat(texture);

CREATE TABLE fx2d(                    -- 2dEffect plugin 0x253F2F8 and IDE '2dfx' section
  id INTEGER PRIMARY KEY,
  dff_id INTEGER REFERENCES dff(id),
  model_rid INTEGER REFERENCES model(rid),
  origin TEXT NOT NULL CHECK (origin IN ('dff','ide')),
  idx INTEGER NOT NULL, type INTEGER NOT NULL, type_name TEXT NOT NULL,
  x REAL, y REAL, z REAL,
  data TEXT);                         -- compact JSON of effect fields

-- ================= collisions
CREATE TABLE col(
  id INTEGER PRIMARY KEY,
  blob_id INTEGER NOT NULL REFERENCES blob(id), idx INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 4),
  name TEXT NOT NULL COLLATE NOCASE,
  hdr_model_id INTEGER,               -- do not trust (report 14 sec.9.14)
  bmin_x REAL, bmin_y REAL, bmin_z REAL, bmax_x REAL, bmax_y REAL, bmax_z REAL,
  bs_x REAL, bs_y REAL, bs_z REAL, bs_r REAL,
  spheres INTEGER, boxes INTEGER, verts INTEGER, faces INTEGER, shadow_faces INTEGER,
  surfaces TEXT,                      -- JSON {surface_id: count}
  active INTEGER NOT NULL DEFAULT 1,  -- first COLFILE/IMG occurrence of a name wins
  UNIQUE(blob_id, idx));
CREATE INDEX col_name ON col(name);

-- ================= definitions (IDE)
CREATE TABLE ide(id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL UNIQUE REFERENCES source(id));

CREATE TABLE model(
  rid INTEGER PRIMARY KEY,
  id INTEGER NOT NULL,                -- game model ID
  layer_id INTEGER NOT NULL REFERENCES layer(id),
  name TEXT NOT NULL COLLATE NOCASE,
  txd TEXT COLLATE NOCASE,
  sec TEXT NOT NULL CHECK (sec IN ('objs','tobj','anim','cars','peds','weap','hier')),
  ide_id INTEGER NOT NULL REFERENCES ide(id), line INTEGER NOT NULL,
  draw REAL, flags INTEGER, time_on INTEGER, time_off INTEGER, anim TEXT COLLATE NOCASE,
  extra TEXT,                         -- JSON: cars/peds/weap specific fields
  active INTEGER NOT NULL,            -- winner in this profile (SA-MP overrides ID 300+ etc.)
  UNIQUE(id, layer_id, ide_id));
CREATE UNIQUE INDEX model_active ON model(id) WHERE active = 1;
CREATE INDEX model_name ON model(name);

CREATE TABLE model_link(              -- materialized links of the ACTIVE definition
  id INTEGER PRIMARY KEY,             -- game model ID
  dff_id INTEGER REFERENCES dff(id),
  txd_id INTEGER REFERENCES txd(id),
  col_id INTEGER REFERENCES col(id),
  col_via TEXT CHECK (col_via IN ('colfile','embedded')),
  n_inst INTEGER NOT NULL DEFAULT 0,
  tex_total INTEGER NOT NULL DEFAULT 0,
  tex_missing INTEGER NOT NULL DEFAULT 0);

CREATE TABLE model_tex(               -- material texture name -> concrete texture via the TXD chain
  model_id INTEGER NOT NULL,
  texture TEXT NOT NULL COLLATE NOCASE,
  texture_id INTEGER REFERENCES texture(id),   -- NULL = unresolved (vanilla norm ~0.8 %)
  via TEXT NOT NULL CHECK (via IN ('own','txdp','vehicle','missing')),
  uses INTEGER NOT NULL,
  PRIMARY KEY(model_id, texture)) WITHOUT ROWID;
CREATE INDEX model_tex_tid ON model_tex(texture_id);

-- ================= world (IPL, zones)
CREATE TABLE ipl(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE,  -- 'lae2' (text) / 'lae2_stream0' (binary)
  kind TEXT NOT NULL CHECK (kind IN ('text','binary')),
  source_id INTEGER REFERENCES source(id),     -- text IPL file
  blob_id INTEGER REFERENCES blob(id),         -- binary IPL inside an IMG
  parent_id INTEGER REFERENCES ipl(id),        -- xxx_streamN -> xxx
  loaded INTEGER NOT NULL,            -- 0 for the 5 script-only bnry without parent (barriers1/2, truthsfarm, crack, carter)
  layer_id INTEGER NOT NULL REFERENCES layer(id),
  UNIQUE(name, layer_id));

CREATE TABLE inst(
  id INTEGER PRIMARY KEY,
  ipl_id INTEGER NOT NULL REFERENCES ipl(id), idx INTEGER NOT NULL,
  model_id INTEGER NOT NULL,
  area INTEGER NOT NULL,              -- interior & 0xFF
  iflags INTEGER NOT NULL,            -- interior >> 8: 1 redundant_stream|2 dont_stream|4 underwater|8 tunnel|16 tunnel_transition
  x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
  qx REAL NOT NULL, qy REAL NOT NULL, qz REAL NOT NULL, qw REAL NOT NULL,
                                      -- quaternion EXACTLY as stored in the IPL; world rotation = conjugate
                                      -- (gta-reversed FileLoader.cpp:1036-1049 negates the imaginary part)
  lod_idx INTEGER NOT NULL,           -- raw field (-1 = none)
  lod_id INTEGER REFERENCES inst(id), -- resolved LOD parent
  is_lod INTEGER NOT NULL DEFAULT 0,  -- someone references it via lod (NOT 'lod == -1')
  bbox_src TEXT NOT NULL CHECK (bbox_src IN ('col','dff','point')),
  UNIQUE(ipl_id, idx));
CREATE INDEX inst_model ON inst(model_id);
CREATE INDEX inst_lod   ON inst(lod_id);
CREATE INDEX inst_pos   ON inst(model_id, x, y, z);   -- runtime entity (model,pos) -> inst match
CREATE VIRTUAL TABLE inst_rtree USING rtree(id, minx, maxx, miny, maxy, minz, maxz);  -- world AABB

CREATE TABLE ipl_item(                -- cull, occl, grge, enex, pick, auzo, tcyc, jump, cars, mult, zone, path(legacy)
  id INTEGER PRIMARY KEY,
  ipl_id INTEGER NOT NULL REFERENCES ipl(id),
  sec TEXT NOT NULL, idx INTEGER NOT NULL,
  x REAL, y REAL, z REAL,
  data TEXT NOT NULL,                 -- compact JSON of fields
  UNIQUE(ipl_id, sec, idx));
CREATE VIRTUAL TABLE item_rtree USING rtree(id, minx, maxx, miny, maxy, minz, maxz);

CREATE TABLE zone(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE, label TEXT,
  title TEXT,                         -- in-game name: label looked up in text/american.gxt (v2)
  type INTEGER, level INTEGER,
  minx REAL, miny REAL, minz REAL, maxx REAL, maxy REAL, maxz REAL,
  source_id INTEGER NOT NULL REFERENCES source(id));
CREATE INDEX zone_name ON zone(name);

-- ================= animations
CREATE TABLE ifp(
  id INTEGER PRIMARY KEY,
  blob_id INTEGER NOT NULL UNIQUE REFERENCES blob(id),
  name TEXT NOT NULL COLLATE NOCASE,
  format TEXT NOT NULL CHECK (format IN ('ANP3','ANPK')),
  anim_count INTEGER NOT NULL);
CREATE TABLE anim(
  ifp_id INTEGER NOT NULL REFERENCES ifp(id), idx INTEGER NOT NULL,
  name TEXT NOT NULL COLLATE NOCASE, bones INTEGER, frames INTEGER,
  PRIMARY KEY(ifp_id, idx)) WITHOUT ROWID;

-- ================= exe-loaded data files (v3): water, time cycle, vehicles, ped types, objects
CREATE TABLE water_quad(              -- data/water.dat (config 0), data/water1.dat (config 1); SID water:<idx> | water:water1/<idx>
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(id),
  config INTEGER NOT NULL CHECK (config IN (0, 1)),  -- CWaterLevel::m_nWaterConfiguration (1 only after a script switches it)
  idx INTEGER NOT NULL,               -- polygon order in its file (0-based)
  line INTEGER NOT NULL,
  nverts INTEGER NOT NULL CHECK (nverts IN (3, 4)),  -- 4 quad | 3 triangle
  flags INTEGER,                      -- 1 visible|2 shallow; NULL = the line has no flags (water1.dat)
  minx REAL NOT NULL, miny REAL NOT NULL, minz REAL NOT NULL, maxx REAL NOT NULL, maxy REAL NOT NULL, maxz REAL NOT NULL,
  verts TEXT NOT NULL,                -- JSON [[x, y, z, flow_x, flow_y, big_waves, small_waves], ...] as stored
  UNIQUE(config, idx));
CREATE VIRTUAL TABLE water_rtree USING rtree(id, minx, maxx, miny, maxy, minz, maxz);  -- id = water_quad.id

CREATE TABLE timecyc(                 -- data/timecyc.dat (CTimeCycle::Initialise); SID tcyc:<weather>/<hour>
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(id),
  weather INTEGER NOT NULL,           -- 0..22 = eWeatherType, file order
  weather_name TEXT NOT NULL COLLATE NOCASE,  -- from the '//////////// EXTRASUNNY_LA' comment of the block
  hour INTEGER NOT NULL,              -- 0 5 6 7 12 19 20 22 (a 24-hour file: 0..23)
  line INTEGER NOT NULL,
  nread INTEGER NOT NULL,             -- values read from the line: 51 (+ dir_mult); fewer = the rest repeats the
                                      -- previous line, as in the engine (vanilla line 320: 20)
  amb INTEGER, amb_obj INTEGER, dir INTEGER, sky_top INTEGER, sky_bot INTEGER, sun_core INTEGER,
  sun_corona INTEGER, low_clouds INTEGER, bottom_clouds INTEGER,   -- 0xRRGGBB
  water INTEGER,                      -- 0xRRGGBBAA
  sun_size REAL, sprite_size REAL, sprite_bright REAL,
  shadow INTEGER, light_shadow INTEGER, pole_shadow INTEGER,
  far_clip REAL, fog_start REAL, light_on_ground REAL,
  data TEXT NOT NULL,                 -- JSON of every value group as read (postfx1/postfx2 [a,r,g,b], cloud_alpha ...)
  UNIQUE(weather, hour));

CREATE TABLE handling(                -- data/handling.cfg (cHandlingDataMgr::LoadHandlingData); SID handling:<id>
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(id),
  name TEXT NOT NULL COLLATE NOCASE,  -- handling id = the 'handling' column of vehicles.ide ('INFERNUS')
  kind TEXT NOT NULL CHECK (kind IN ('car','bike','boat','flying')),  -- standard line | '!' | '%' | '$'
  line INTEGER NOT NULL,              -- winning line: a later line with the same id and kind replaces it
  mass REAL, turn_mass REAL, drag REAL, max_vel REAL, accel REAL, gears INTEGER,
  drive TEXT, engine TEXT,            -- F|R|4, P|D|E
  brake REAL, steer_lock REAL, value INTEGER, model_flags INTEGER, handling_flags INTEGER,  -- 'car' rows only
  data TEXT NOT NULL,                 -- JSON of every field of the line, file units (km/h, ms^-2 ...)
  UNIQUE(name, kind));

CREATE TABLE carcol(                  -- data/carcols.dat 'col': the vehicle colour palette
  idx INTEGER PRIMARY KEY,            -- palette index (0..126 in vanilla)
  rgb INTEGER NOT NULL,               -- 0xRRGGBB
  name TEXT, radio TEXT,              -- from the line comment: 'police car blue', 'blue'
  source_id INTEGER NOT NULL REFERENCES source(id), line INTEGER NOT NULL);

CREATE TABLE car_color(               -- data/carcols.dat 'car' / 'car4': colour variations per vehicle model name
  model TEXT NOT NULL COLLATE NOCASE, -- model name ('infernus'), looked up among the definitions like the engine does
  idx INTEGER NOT NULL,               -- variation 0..7
  c1 INTEGER NOT NULL, c2 INTEGER NOT NULL, c3 INTEGER, c4 INTEGER,  -- palette indexes; c3/c4 only from 'car4'
  source_id INTEGER NOT NULL REFERENCES source(id), line INTEGER NOT NULL,
  PRIMARY KEY(model, idx)) WITHOUT ROWID;

CREATE TABLE ped_rel(                 -- data/ped.dat (CPedType::LoadPedData): acquaintances of ped types
  pedtype TEXT NOT NULL COLLATE NOCASE,  -- block header: CIVMALE, COP, GANG1 ... (= the peds IDE 'pedtype')
  rel TEXT NOT NULL CHECK (rel IN ('hate','dislike','like','respect')),
  other TEXT NOT NULL COLLATE NOCASE, -- one ped type named on that line
  source_id INTEGER NOT NULL REFERENCES source(id), line INTEGER NOT NULL,
  PRIMARY KEY(pedtype, rel, other)) WITHOUT ROWID;

CREATE TABLE object_data(             -- data/object.dat (CObjectData::Initialise): physics of dynamic objects
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(id),
  name TEXT NOT NULL COLLATE NOCASE,  -- model name
  line INTEGER NOT NULL,
  loaded INTEGER NOT NULL,            -- 0 = after the first '*' line, where the engine stops reading (vanilla: 1)
  mass REAL, turn_mass REAL, air_res REAL, elasticity REAL, submerged REAL, uproot REAL, dmg_mult REAL,
  dmg_effect INTEGER,                 -- 0 none|1 change_model|20 smash|21 change_then_smash|200 breakable|202 breakable_remove
  col_response INTEGER,               -- 0 none|1 lamppost|2 smallbox|3 bigbox|4 fencepart|5 grenade|6 swingdoor
                                      -- |7 lockdoor|8 hanging|9 poolball
  cam_avoid INTEGER, explodes INTEGER, fx_type INTEGER, fx_name TEXT,
  data TEXT NOT NULL);                -- JSON of every field on the line (FX offset, breakable info)
CREATE INDEX object_data_name ON object_data(name);

-- ================= search
CREATE VIRTUAL TABLE fts_name USING fts5(sid UNINDEXED, kind UNINDEXED, name, tokenize = 'trigram');
-- queries shorter than 3 chars fall back to LIKE prefix on the b-tree name indexes

-- ================= SID views (what `index_query` users should read first)
CREATE VIEW v_model AS
  SELECT 'model:'||m.id AS sid, m.id, m.name, m.sec, m.txd, m.draw, m.flags,
         l.n_inst, l.tex_total, l.tex_missing, l.col_via,
         (SELECT name FROM layer WHERE id = m.layer_id) AS layer
  FROM model m LEFT JOIN model_link l ON l.id = m.id WHERE m.active = 1;

CREATE VIEW v_inst AS
  SELECT 'inst:'||lower(p.name)||'#'||i.idx AS sid, i.id AS rid, i.model_id,
         i.x, i.y, i.z, i.area, i.iflags, i.is_lod,
         CASE WHEN i.lod_id IS NULL THEN NULL ELSE
           (SELECT 'inst:'||lower(p2.name)||'#'||i2.idx FROM inst i2 JOIN ipl p2 ON p2.id = i2.ipl_id
             WHERE i2.id = i.lod_id) END AS lod_sid
  FROM inst i JOIN ipl p ON p.id = i.ipl_id;

CREATE VIEW v_tex AS
  SELECT 'tex:'||lower(t.name)||'/'||lower(x.name) AS sid, x.id AS rid, x.w, x.h, x.d3dfmt, x.alpha,
         'pix:'||lower(hex(x.hash)) AS pix, b.active
  FROM texture x JOIN txd t ON t.id = x.txd_id JOIN blob b ON b.id = t.blob_id;

CREATE VIEW v_file AS
  SELECT 'file:'||lower(s.relpath)||CASE WHEN s.kind = 'img' THEN '/'||lower(b.name) ELSE '' END AS sid,
         b.id AS rid, b.ns, b.active, b.size, b.rw_size, (SELECT name FROM layer WHERE id = s.layer_id) AS layer
  FROM blob b JOIN source s ON s.id = b.source_id;

CREATE VIEW v_vehicle AS              -- vehicles.ide fields + the standard handling line + colour variations (v3)
  SELECT 'model:'||m.id AS sid, m.id, m.name,
         json_extract(m.extra, '$.type') AS type, json_extract(m.extra, '$.class') AS class,
         json_extract(m.extra, '$.handling') AS handling, json_extract(m.extra, '$.gxt') AS gxt,
         h.mass, h.max_vel, h.accel, h.drive, h.engine, h.value,
         (SELECT count(*) FROM car_color c WHERE c.model = m.name) AS colors
  FROM model m LEFT JOIN handling h ON h.name = json_extract(m.extra, '$.handling') AND h.kind = 'car'
  WHERE m.active = 1 AND m.sec = 'cars';

CREATE VIEW v_ped AS                  -- peds.ide fields of the active ped definitions (v3)
  SELECT 'model:'||m.id AS sid, m.id, m.name,
         json_extract(m.extra, '$.pedtype') AS pedtype, json_extract(m.extra, '$.stat') AS stat,
         json_extract(m.extra, '$.animgroup') AS animgroup, json_extract(m.extra, '$.cars_mask') AS cars_mask,
         json_extract(m.extra, '$.radio1') AS radio1, json_extract(m.extra, '$.radio2') AS radio2,
         json_extract(m.extra, '$.voice_type') AS voice_type, json_extract(m.extra, '$.voice1') AS voice1,
         json_extract(m.extra, '$.voice2') AS voice2
  FROM model m WHERE m.active = 1 AND m.sec = 'peds';

-- P2: project_model (path nodes: work/index/paths-<profile>.sqlite of satk.paths)
