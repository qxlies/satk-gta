# rwfury

Python library for reading and writing GTA RenderWare **DFF** (3D model), **TXD** (texture dictionary), **IMG** (archive), **COL** (collision), **IFP** (animation package), native GTA SA **nodes*.dat** paths, and Fastman92 extended path files. Supports GTA III, Vice City, and San Andreas.

## Features

### Core game formats

- **DFF**: parse and write RenderWare clumps with round-trip support
- **TXD**: parse texture dictionaries, export DDS, and decode raw RGBA data
- **IMG**: read and write v1 (GTA III/VC) and v2 (San Andreas) archives, extract files, and parse DFF/TXD directly from memory
- **COL**: parse and write COL1, COL2, and COL3 collision files
- **IFP**: parse and write `ANP3` and chunked `ANPK` animation packages with bit-exact unedited round-trips
- **Paths**: read and write native San Andreas `nodes*.dat` and Fastman92 extended path files

### DFF support

- **Plugin coverage**: BinMesh, Skin, HAnim, 2dfx, Material Effects, Night Colors, embedded Collision, and Specular/Reflection materials
- **UV animation support**: parse, write, inspect, and export DFF UV animation dictionaries and material references
- **RenderWare lights**: read and write `RW_LIGHT` chunks attached to frames
- **Version-aware parsing**: handles RenderWare 3.1 (GTA III) through 3.6 (San Andreas) struct differences automatically

### Export and authoring

- **Generic mesh export**: `GenericMesh` output with flat arrays and byte-packing helpers for glTF, FBX, or custom pipelines
- **Generic animation export**: source-ordered clips, tracks, keyframes, and typed channel buffers with no target-format dependency
- **BinMesh-aware export**: `to_generic_meshes()` preserves material splits and triangle strips
- **Named collision materials**: `ColMaterial` enum for readable collision surface IDs

### Practical scope

- **COL coverage**: spheres, boxes, face groups, and shadow meshes
- **IFP coverage**: compressed and float tracks, cutscene/object animations, translation, scale, validation, interpolation, and local transform evaluation
- **Ped skeleton binding**: canonical `SaBoneTag` IDs bridge ANP3 names, DFF HAnim nodes, and skin bone indices
- **Path coverage**: native SA and Fastman92 nodes, navi nodes, links, navi links, link lengths, intersection flags, extended coordinates, and format metadata
- **Pure Python**: zero external dependencies

## Installation

```bash
pip install rwfury
```

Requires Python 3.10+. No external dependencies.

## Quick start

### Read files from an IMG archive

```python
from rwfury import Img, Dff, Txd

# Open an IMG (auto-detects v1 or v2)
img = Img.from_file("gta3.img")

# List contents
print(f"{len(img.entries)} files")
for name in img.list_files()[:10]:
    print(f"  {name}")

# Find a file (case-insensitive)
entry = img.find("cop.dff")
print(f"{entry.name}: {entry.size} bytes")

# Parse DFF/TXD directly from IMG (no temp files)
dff = Dff.from_bytes(img.read("cop.dff"))
txd = Txd.from_bytes(img.read("cop.txd"))

# Extract files to disk
img.extract("cop.dff", "output/")       # single file
img.extract_all("output/")              # everything
```

### Parse a DFF and extract meshes

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

# High-level mesh extraction (grouped by material)
for entry in dff.get_meshes():
    print(entry["name"], entry["texture"])
    for mesh in entry["meshes"]:
        print(f"  {len(mesh.positions)} vertices, {len(mesh.indices)//3} triangles")
        print(f"  UV sets: {len(mesh.texcoords)}")
```

### Format-agnostic export with GenericMesh

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

for mesh in dff.to_generic_meshes():
    # Flat arrays ready for GPU upload or any format conversion
    pos_bytes = mesh.positions_as_bytes()      # float32 LE, 3 per vertex
    idx_bytes = mesh.indices_as_bytes("u16")   # uint16 LE (or "u32")
    uv_bytes  = mesh.texcoords_as_bytes(0)     # float32 LE, 2 per vertex
    norm_bytes = mesh.normals_as_bytes()       # float32 LE, 3 per vertex

    # Material info
    print(mesh.texture_name, mesh.diffuse_color)

    # 4x4 transform matrix (row-major, 16 floats)
    print(mesh.transform)

    # Skinning data (if present)
    if mesh.has_skinning:
        bi = mesh.bone_indices_as_bytes()   # uint8, 4 per vertex
        bw = mesh.bone_weights_as_bytes()   # float32, 4 per vertex
```

### Read and write a COL collision file

```python
from rwfury import Col, ColMaterial

col = Col.from_file("model.col")

for model in col.models:
    print(model.name, model.version)
    print(f"{len(model.spheres)} spheres, {len(model.boxes)} boxes")
    print(f"{len(model.vertices)} verts, {len(model.faces)} faces")

    if model.faces:
        face = model.faces[0]
        print(face.material, ColMaterial(face.material).label)

col.to_file("roundtrip.col")
```

### Parse embedded collision from a DFF

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

if dff.collision:
    col_model = dff.collision.parse()
    if col_model:
        print(col_model.name, len(col_model.faces))
```

### Read and write GTA SA path nodes

```python
from rwfury import SaPaths, PathFileFormat, PathNodeFlag, PathTrafficLevel

paths = SaPaths.from_file("nodes5.dat")
print(paths.area_id, paths.node_count, paths.link_count)
print(paths.source_format)
print(SaPaths.area_origin(paths.area_id))

for node in paths.vehicle_nodes[:10]:
    print(node.node_id, node.position, node.link_count, node.traffic_level)
    for link in paths.links_for_node(node):
        print(" ->", link.link.area_id, link.link.node_id, "length", link.length)

# Bitfield helpers keep the raw flags editable without manual masks.
node = paths.vehicle_nodes[0]
node.traffic_level = PathTrafficLevel.LOW
node.flags |= PathNodeFlag.PARKING
node.spawn_probability = 10

paths.to_file("nodes5_roundtrip.dat")

# Fastman92 files are detected automatically and can be written explicitly.
fm = SaPaths.from_file("nodes120.dat")
fm.to_file("nodes120_roundtrip.dat", format=PathFileFormat.FASTMAN92_VER2)
```

### Extract textures from a TXD

```python
from rwfury import Txd

txd = Txd.from_file("textures.txd")

# Export all textures as .dds files
txd.export_to_dds("output_folder/")

# Or get raw RGBA pixel data (for compression, processing, etc.)
for tex in txd.textures:
    rgba_mipmaps, has_alpha = tex.to_rgba()
    # rgba_mipmaps[0] = bytes, width * height * 4, R,G,B,A order
    print(f"{tex.name}: {tex.width}x{tex.height}, alpha={has_alpha}")
```

### Write a modified DFF

```python
from rwfury import Dff

dff = Dff.from_file("original.dff")

# Modify frames, geometry, materials, etc.
dff.frames[0].name = "renamed_frame"

# Write back to binary
dff.to_file("modified.dff")
```

### Create a new IMG archive

```python
from rwfury import Img

# Build a v2 IMG from files
files = {
    "model.dff": open("model.dff", "rb").read(),
    "texture.txd": open("texture.txd", "rb").read(),
}
Img.create_v2(files, "output.img")
```

### Access low-level data

```python
from rwfury import Dff

dff = Dff.from_file("model.dff")

# Frames (skeleton/hierarchy)
for frame in dff.frames:
    print(frame.name, frame.position, frame.parent)
    if frame.hanim:
        print(f"  HAnim: {len(frame.hanim.bones)} bones")

# Geometry details
for geom in dff.geometries:
    print(f"{len(geom.vertices)} verts, {len(geom.triangles)} tris")
    print(f"UV sets: {geom.num_uv_sets}, flags: 0x{geom.flags:04X}")

    # Plugins
    if geom.bin_mesh:
        print(f"BinMesh: {len(geom.bin_mesh.splits)} splits")
    if geom.skin:
        print(f"Skin: {geom.skin.num_bones} bones")
    if geom.night_colors:
        print(f"Night colors: {len(geom.night_colors)} vertices")
    if geom.effects_2dfx:
        print(f"2dfx: {len(geom.effects_2dfx)} effects")

    # Materials
    for mat in geom.materials:
        print(f"  {mat.texture_name} color={mat.color}")
        if mat.specular_texture:
            print(f"    specular: {mat.specular_texture} level={mat.specular_level}")
        if mat.reflection:
            print(f"    reflection: intensity={mat.reflection['intensity']}")

# Collision data
if dff.collision:
    print(f"Embedded collision: {len(dff.collision.raw)} bytes")
    parsed = dff.collision.parse()
    if parsed:
        print(f"  Parsed COL model: {parsed.name}")

# RenderWare real-time lights, distinct from Rockstar 2dfx light effects
for light, frame in dff.iter_lights_with_frames():
    frame_name = frame.name if frame else ""
    print(frame_name, light.type_name, light.radius, light.color)
    print(light.flags_enum, light.affects_scene, light.affects_world)
    print(f"spot angle: {light.spot_angle_degrees:.1f} degrees")

# Or use high-level dictionaries.
for entry in dff.get_lights():
    print(entry["frame_name"], entry["type"], entry["color"])
```

### Create RenderWare DFF lights

```python
from rwfury import Dff, DffFrame, DffLightFlags

dff = Dff()
dff.frames.append(DffFrame(name="lamp"))

dff.add_point_light(
    frame_index=0,
    radius=20.0,
    color=(1.0, 0.85, 0.55),
    flags=DffLightFlags.SCENE,
)
dff.add_spot_light(
    frame_index=0,
    radius=35.0,
    angle_degrees=45.0,
    soft=True,
)
```

### Read an IFP animation package

```python
from rwfury import Dff, Ifp, Img

ifp = Ifp.from_file("anim/ped.ifp")
img = Img.from_file("models/gta3.img")
dff = Dff.from_bytes(img.read("bmycr.dff"))

print(ifp.internal_name)
print(ifp.get_animation_names()[:5])

anim = ifp.get_animation("ARRESTgun")
obj = ifp.get_object("ARRESTgun", "Root")

print(len(anim.objects), int(obj.frame_type), len(obj.frames))

# Convert in memory without JSON or a target-format dependency.
generic = ifp.to_generic_animation_set()
clip = generic.get_animation("ARRESTgun")
for track in clip.tracks:
    buffers = track.to_buffers("f32")
    # bone_id is the effective SA HAnim tag. source_bone_id preserves the file.
    bone_index = dff.get_hanim_bone_index(track.bone_id)
    print(track.name, track.bone_id, bone_index, buffers.keyframe_count)

# Validate without preventing recovery of structurally readable tracks.
for issue in ifp.validate():
    print(issue.code, issue.animation_name, issue.object_name)

# Evaluate every object track at one second. Shorter tracks are held.
print(ifp.get_animation_duration("ARRESTgun"))
for track, frame in ifp.sample_animation("ARRESTgun", 1.0):
    if frame is not None:
        print(track.name, frame.to_matrix())
```

`ANPK` packages use the same API and retain `KR00`, `KRT0`, and `KRTS` data, including scale tracks and opaque metadata needed for lossless rewriting. Generic conversion preserves clip, track, and keyframe order; source bone IDs; original timestamps; compressed `raw_time`; non-finite values; and the distinction between absent and present translation/scale channels. Use `iter_generic_animations()` to convert large packages one clip at a time.

ANP3 pedestrian tracks commonly store `bone_id = -1`. GTA SA binds those tracks through canonical HAnim names rather than literal DFF frame names. Generic tracks therefore expose the resolved HAnim tag as `bone_id` and the untouched file value as `source_bone_id`; `Dff.get_hanim_bone_index()` maps that tag to the skinning index. Times are absolute seconds and quaternions are `xyzw` absolute local rotations. When a translation or scale channel is `None`, preserve the corresponding bind-pose component instead of replacing it with zero or identity.

## API reference

### Core classes

| Class | Description |
|-------|-------------|
| `Dff` | DFF parser/writer. `from_file(path)`, `from_bytes(data)`, `to_file(path)`, `get_meshes()`, `to_generic_meshes()`, `get_lights()`, `add_*_light()` |
| `Txd` | TXD parser. `from_file(path)`, `from_bytes(data)`, `export_to_dds(folder)` |
| `TxdTexture` | Single texture entry. `to_rgba()` decodes any format to raw RGBA bytes |
| `Img` | IMG archive. `from_file(path)`, `read(name)`, `find(name)`, `extract()`, `extract_all()`, `create_v2()` |
| `ImgEntry` | Archive entry with `name`, `offset`, `size` |
| `Col` | COL parser/writer. `from_file(path)`, `from_bytes(data)`, `to_file(path)`, `to_bytes()` |
| `ColModel` | One collision model with bounds, primitives, mesh, face groups, and optional shadow mesh |
| `ColMaterial` | Named `IntEnum` for COL surface material IDs |
| `Ifp` | ANP3/ANPK parser and writer with lookup, validation, generic animation conversion, duration, sampling, and transform evaluation |
| `SaPaths` / `SaPathFile` | GTA SA native/Fastman92 path parser/writer with section-aware helpers |
| `GenericMesh` | Flat-array mesh with `*_as_bytes()` helpers for format-agnostic export |
| `GenericAnimationSet` | In-memory animation package with source format and coordinate conventions |
| `GenericAnimation` | Named clip with source-ordered tracks and duplicate-safe lookup |
| `GenericAnimationTrack` | Flat time/rotation/translation/scale channels and portable binary buffers |
| `GenericAnimationKeyframe` | Indexed view of one source keyframe without fabricated channels |
| `GenericAnimationBuffers` | Deinterleaved `f32`/`f64` channel bytes in little- or big-endian order |

### DFF data classes

| Class | Description |
|-------|-------------|
| `DffGeometry` | Vertices, normals, triangles, texcoords, vertex colors, materials, and plugins |
| `DffMaterial` | Color, texture name, ambient/diffuse/specular, material effects, specular/reflection extensions |
| `DffFrame` | Name, position, rotation matrix, parent index, HAnim skeleton data |
| `DffAtomic` | Links a frame to a geometry |
| `DffLight` | RenderWare real-time light with frame index, radius, RGB color, spotlight angle helpers, flags, and type |
| `DffLightType` | Named RenderWare light types: directional, ambient, point, spot, spot soft |
| `DffLightFlags` | Named RenderWare light flags: scene/world scope |
| `MorphTarget` | Per-morph-target vertices and normals with bounding sphere |
| `BinMeshPLG` | Triangle strip/list splits by material |
| `SkinPLG` | Bone indices, weights, and inverse bind matrices per vertex |
| `HAnimPLG` | Skeleton hierarchy with bone IDs and flags |
| `Effect2dfxEntry` | 2dfx effect (lights, particles, etc.) with position and typed data |
| `CollisionData` | Raw embedded COL data blob |

### COL data classes

| Class | Description |
|-------|-------------|
| `ColBounds` | Bounding sphere + AABB data |
| `ColSurface` | Collision surface properties: material, flag, brightness, light |
| `ColSphere` | Collision sphere primitive |
| `ColBox` | Collision box primitive |
| `ColFace` | Collision triangle face with material/light |
| `ColFaceGroup` | Spatial grouping metadata for COL2/3 |

### GTA SA path data classes

| Class | Description |
|-------|-------------|
| `SaPathFile` / `SaPaths` | One native or Fastman92 path file with vehicle nodes, ped nodes, navi nodes, links, filler, link lengths, intersection flags, and source format metadata |
| `PathFileFormat` | Explicit format selector for auto, native SA, Fastman92 VER2, and Fastman92 VER3 |
| `PathNode` | Section 1 graph node with scaled XYZ position and helpers for link count, traffic level, spawn probability, and behavior flags |
| `NaviNode` | Section 2 vehicle navi node with scaled XY position, normalized direction, lane counts, traffic light behavior, and train crossing flag |
| `PathLink` | Section 3 adjacent path node reference |
| `NaviLink` | Section 5 packed navi node reference with 6-bit area and 10-bit node ID helpers |
| `PathLinkRecord` | Combined high-level view of one link across sections 3, 5, 6, and 7 |
| `PathNodeFlag` | Named path node behavior flags such as boats, emergency-only, highway, parking, and road blocks |
| `PathTrafficLevel` | Named traffic levels: full, high, medium, low |
| `PathIntersectionFlag` | Section 7 road-cross and pedestrian-traffic-light flags |

### GenericMesh properties and methods

| Member | Description |
|--------|-------------|
| `vertex_count`, `triangle_count` | Derived from flat array lengths |
| `has_normals`, `has_colors`, `has_skinning` | Quick checks for optional data |
| `uv_set_count` | Number of UV coordinate sets |
| `positions_as_bytes()` | Packed `float32` LE (12 bytes/vertex) |
| `normals_as_bytes()` | Packed `float32` LE (12 bytes/vertex) |
| `texcoords_as_bytes(uv_set)` | Packed `float32` LE (8 bytes/vertex) |
| `colors_as_bytes()` | Packed `uint8` RGBA (4 bytes/vertex) |
| `indices_as_bytes(fmt)` | `"u16"` or `"u32"` packed indices |
| `bone_indices_as_bytes()` | Packed `uint8` (4 bytes/vertex) |
| `bone_weights_as_bytes()` | Packed `float32` LE (16 bytes/vertex) |

### Generic animation properties and methods

| Member | Description |
|--------|-------------|
| `Ifp.iter_generic_animations()` | Lazily convert one clip at a time |
| `Ifp.to_generic_animations()` | Return independent generic clips in source order |
| `Ifp.to_generic_animation_set()` | Return the complete package and source conventions in memory |
| `times`, `rotations` | Flat source-ordered time and quaternion (`xyzw`) arrays |
| `translations`, `scales` | Flat XYZ arrays, or `None` when the source channel is absent |
| `raw_times` | Original compressed time ticks when available |
| `bone_id`, `source_bone_id`, `bone_binding` | Effective HAnim tag, untouched source ID, and resolution method |
| `iter_keyframes()` | Iterate indexed keyframe views without changing source order |
| `to_buffers(fmt, byte_order)` | Pack deinterleaved channels as `f32` or `f64`, little- or big-endian |

## Supported formats

| Game | RW Version | DFF | TXD | IMG | COL | IFP | Paths |
|------|-----------|-----|-----|-----|-----|-----|-------|
| GTA III | 3.1 - 3.3 | Read/Write | Read + DDS export | v1 (Read/Write) | COL1 (Read/Write) | - | - |
| GTA Vice City | 3.4 - 3.5 | Read/Write | Read + DDS export | v1 (Read/Write) | COL1 (Read/Write) | - | - |
| GTA San Andreas | 3.6 | Read/Write | Read + DDS export | v2 (Read/Write) | COL2/COL3 (Read/Write) | ANP3 + ANPK (Read/Write) | native `nodes*.dat` + Fastman92 VER2/VER3 (Read/Write) |

TXD supports D3D8/D3D9 platform textures: PAL4, PAL8, 16-bit (R5G6B5, A1R5G5B5, A4R4G4B4), 32-bit (A8R8G8B8, X8R8G8B8), and DXT1/DXT3/DXT5 compressed.

IMG v1 uses separate `.dir` + `.img` files. IMG v2 uses a single `.img` file with `VER2` header.

## License

MIT
