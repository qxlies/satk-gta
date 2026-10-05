"""IMG archive reader/writer for GTA III, Vice City, and San Andreas."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

SECTOR_SIZE = 2048


@dataclass
class ImgEntry:
    """A single file entry inside an IMG archive."""
    name: str = ""
    offset: int = 0       # offset in bytes (not sectors)
    size: int = 0         # size in bytes
    _stream_size: int = 0  # streaming size (v2 only, in sectors)


class Img:
    """IMG archive reader/writer.

    Supports:
      - Version 1 (GTA III / Vice City): separate .dir + .img files
      - Version 2 (GTA San Andreas): single .img with VER2 header
    """

    def __init__(self):
        self.version: int = 0  # 1 or 2
        self.entries: list[ImgEntry] = []
        self._img_path: str = ""
        self._data: bytes | None = None

    @classmethod
    def from_file(cls, path: str) -> Img:
        """Open an IMG archive. Auto-detects v1 vs v2.

        For v1, pass either the .img or .dir path (the other is found automatically).
        For v2, pass the .img path.
        """
        img = cls()
        img._img_path = path

        # Check if v1 (separate .dir/.img) or v2 (single .img with VER2 header)
        base, ext = os.path.splitext(path)
        ext_lower = ext.lower()

        if ext_lower == ".dir":
            # V1: user passed the .dir file
            dir_path = path
            img_path = base + ".img"
            if not os.path.exists(img_path):
                img_path = base + ".IMG"
            img._img_path = img_path
            img._parse_v1(dir_path, img_path)
            return img

        # Read first 4 bytes to check for VER2 magic
        with open(path, "rb") as f:
            magic = f.read(4)

        if magic == b"VER2":
            img._parse_v2(path)
        else:
            # V1: look for matching .dir
            dir_path = base + ".dir"
            if not os.path.exists(dir_path):
                dir_path = base + ".DIR"
            if not os.path.exists(dir_path):
                raise FileNotFoundError(
                    f"No .dir file found for v1 IMG archive: {path}")
            img._parse_v1(dir_path, path)

        return img

    def list_files(self) -> list[str]:
        """Return sorted list of all file names in the archive."""
        return sorted(e.name for e in self.entries)

    def find(self, name: str) -> ImgEntry | None:
        """Find an entry by name (case-insensitive)."""
        name_lower = name.lower()
        for entry in self.entries:
            if entry.name.lower() == name_lower:
                return entry
        return None

    def read(self, name: str) -> bytes:
        """Read file data by name (case-insensitive)."""
        entry = self.find(name)
        if entry is None:
            raise KeyError(f"File not found in IMG: {name}")
        return self._read_entry(entry)

    def extract(self, name: str, output_folder: str) -> str:
        """Extract a single file by name. Returns the output file path."""
        data = self.read(name)
        entry = self.find(name)
        os.makedirs(output_folder, exist_ok=True)
        out_path = os.path.join(output_folder, entry.name)
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path

    def extract_all(self, output_folder: str) -> list[str]:
        """Extract all files to a folder. Returns list of written paths."""
        os.makedirs(output_folder, exist_ok=True)
        written = []
        for entry in self.entries:
            data = self._read_entry(entry)
            out_path = os.path.join(output_folder, entry.name)
            with open(out_path, "wb") as f:
                f.write(data)
            written.append(out_path)
        return written

    @classmethod
    def create_v2(cls, entries: dict[str, bytes], path: str):
        """Create a new VER2 IMG archive from a dict of {name: data}.

        Args:
            entries: Dict mapping file names to their binary content.
            path: Output .img file path.
        """
        img_entries: list[tuple[str, bytes]] = list(entries.items())
        num_entries = len(img_entries)

        # Directory starts after header (8 bytes), each entry is 32 bytes
        dir_size = 8 + num_entries * 32
        # Data starts at next sector boundary after directory
        data_offset_sectors = (dir_size + SECTOR_SIZE - 1) // SECTOR_SIZE

        with open(path, "wb") as f:
            # Header
            f.write(b"VER2")
            f.write(struct.pack("<I", num_entries))

            # Build directory entries
            current_sector = data_offset_sectors
            dir_entries = []
            for name, data in img_entries:
                size_sectors = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE
                name_bytes = name.encode("ascii", errors="replace")[:23]
                name_bytes = name_bytes.ljust(24, b"\x00")

                f.write(struct.pack("<I", current_sector))
                f.write(struct.pack("<HH", size_sectors, size_sectors))
                f.write(name_bytes)

                dir_entries.append((current_sector, data))
                current_sector += size_sectors

            # Pad to data start
            current_pos = f.tell()
            pad = data_offset_sectors * SECTOR_SIZE - current_pos
            if pad > 0:
                f.write(b"\x00" * pad)

            # Write file data (sector-aligned)
            for sector_offset, data in dir_entries:
                f.seek(sector_offset * SECTOR_SIZE)
                f.write(data)
                # Pad to sector boundary
                remainder = len(data) % SECTOR_SIZE
                if remainder:
                    f.write(b"\x00" * (SECTOR_SIZE - remainder))

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_v1(self, dir_path: str, img_path: str):
        """Parse v1 IMG (GTA III / Vice City): separate .dir + .img."""
        self.version = 1
        self._img_path = img_path

        with open(dir_path, "rb") as f:
            dir_data = f.read()

        # Each entry: 4 bytes offset (sectors), 4 bytes size (sectors), 24 bytes name
        entry_size = 32
        num_entries = len(dir_data) // entry_size

        for i in range(num_entries):
            off = i * entry_size
            offset_sectors = struct.unpack_from("<I", dir_data, off)[0]
            size_sectors = struct.unpack_from("<I", dir_data, off + 4)[0]
            name_raw = dir_data[off + 8:off + 32]
            name = name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

            self.entries.append(ImgEntry(
                name=name,
                offset=offset_sectors * SECTOR_SIZE,
                size=size_sectors * SECTOR_SIZE,
            ))

    def _parse_v2(self, path: str):
        """Parse v2 IMG (GTA San Andreas): single .img with VER2 header."""
        self.version = 2
        self._img_path = path

        with open(path, "rb") as f:
            magic = f.read(4)
            assert magic == b"VER2", f"Expected VER2, got {magic!r}"
            num_entries = struct.unpack("<I", f.read(4))[0]

            for _ in range(num_entries):
                offset_sectors = struct.unpack("<I", f.read(4))[0]
                stream_size = struct.unpack("<H", f.read(2))[0]
                size_sectors = struct.unpack("<H", f.read(2))[0]
                name_raw = f.read(24)
                name = name_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

                # size_sectors can be 0 for very large files, use stream_size
                actual_size = size_sectors if size_sectors else stream_size

                self.entries.append(ImgEntry(
                    name=name,
                    offset=offset_sectors * SECTOR_SIZE,
                    size=actual_size * SECTOR_SIZE,
                    _stream_size=stream_size,
                ))

    def _read_entry(self, entry: ImgEntry) -> bytes:
        """Read raw bytes for an entry from the IMG file."""
        with open(self._img_path, "rb") as f:
            f.seek(entry.offset)
            return f.read(entry.size)
