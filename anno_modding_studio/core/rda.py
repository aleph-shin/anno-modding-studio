"""Reader for Anno's ``.rda`` archives (Resource File V2.2).

Enough of the format to list an archive and pull individual files out of it,
which is all the Studio needs -- it wants two files out of an archive that may be
28 GB.  Nothing is ever written back.

Layout, verified against Anno 1800 and Anno 117 archives::

    [0]                     1024-byte header, magic "Resource File V2.2" at 0,
                            offset of the first block header as u64 at 784
    ...                     file payloads
    [block.dir_offset]      directory: fileCount * 560-byte entries
    [block_offset]          32-byte block header, points at the next block

A block header is ``flags:u32, file_count:u32, dir_size:u64,
dir_decompressed:u64, next_block:u64``; the directory sits immediately *before*
it.  An entry is a 520-byte UTF-16LE name followed by five u64s: offset,
compressed size, uncompressed size, timestamp, flags.

Block flag bits seen in the wild: ``0x1`` zlib-compressed, ``0x4`` encrypted
(unsupported -- skipped rather than decoded into garbage), ``0x8`` set on some
blocks with no effect on parsing.  Entries with both sizes zero are tombstones
marking a file deleted by that patch archive.
"""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, List, Optional

from .i18n import tr

MAGIC_V22 = b"Resource File V2.2"
MAGIC_V20 = b"Resource File V2.0"

HEADER_SIZE = 1024
FIRST_BLOCK_OFFSET_POS = 784
BLOCK_HEADER_SIZE = 32
ENTRY_SIZE = 560
NAME_BYTES = 520

FLAG_COMPRESSED = 0x1
FLAG_ENCRYPTED = 0x4

# Guard against a corrupt header sending us off to allocate nonsense.
MAX_DIRECTORY_BYTES = 512 * 1024 * 1024


class RdaError(Exception):
    """The archive could not be read."""


@dataclass(frozen=True)
class RdaEntry:
    """One file inside an archive."""

    name: str
    offset: int
    compressed_size: int
    uncompressed_size: int
    timestamp: int
    block_flags: int

    @property
    def is_compressed(self) -> bool:
        return bool(self.block_flags & FLAG_COMPRESSED)

    @property
    def is_deleted(self) -> bool:
        """A tombstone: this patch archive removes the file rather than adding it."""
        return self.compressed_size == 0 and self.uncompressed_size == 0

    @property
    def size(self) -> int:
        return self.uncompressed_size


class RdaArchive:
    """Read-only view over one ``.rda``.  Use as a context manager."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh: Optional[BinaryIO] = None
        self.encrypted_blocks = 0

    def __enter__(self) -> RdaArchive:
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> RdaArchive:
        if self._fh is None:
            # Deliberately long-lived: this class is itself the context
            # manager (__enter__/__exit__), and the archive behind the handle
            # can be 28 GB, so it must outlive this call.
            self._fh = open(self.path, "rb")  # noqa: SIM115
        return self

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # -- directory ----------------------------------------------------------
    def entries(self) -> Iterator[RdaEntry]:
        """Yield every file in the archive, in block order."""
        self.open()
        size = self.path.stat().st_size
        header = self._read_at(0, HEADER_SIZE)
        if header[: len(MAGIC_V22)] != MAGIC_V22:
            if header[: len(MAGIC_V20)] == MAGIC_V20:
                raise RdaError(tr(
                    "%s is an older V2.0 archive, which is not supported.", self.path.name
                ))
            raise RdaError(tr("%s is not an RDA archive.", self.path.name))

        offset = struct.unpack_from("<Q", header, FIRST_BLOCK_OFFSET_POS)[0]
        self.encrypted_blocks = 0
        visited = set()

        while offset and offset + BLOCK_HEADER_SIZE <= size and offset not in visited:
            visited.add(offset)
            raw = self._read_at(offset, BLOCK_HEADER_SIZE)
            flags, count = struct.unpack_from("<II", raw, 0)
            dir_size, _dir_decompressed, next_offset = struct.unpack_from("<QQQ", raw, 8)

            if flags & FLAG_ENCRYPTED:
                # The directory is enciphered; decoding it anyway would yield
                # convincing-looking nonsense, so skip the block and say so.
                self.encrypted_blocks += 1
            elif 0 < dir_size <= MAX_DIRECTORY_BYTES and dir_size <= offset:
                blob = self._read_at(offset - dir_size, dir_size)
                if flags & FLAG_COMPRESSED:
                    try:
                        blob = zlib.decompress(blob)
                    except zlib.error:
                        blob = b""
                usable = min(count, len(blob) // ENTRY_SIZE)
                for i in range(usable):
                    chunk = blob[i * ENTRY_SIZE : (i + 1) * ENTRY_SIZE]
                    entry = _parse_entry(chunk, flags)
                    if entry is not None:
                        yield entry

            if next_offset <= offset or next_offset >= size:
                break
            offset = next_offset

    def find(self, name: str) -> Optional[RdaEntry]:
        wanted = name.replace("\\", "/").lower()
        for entry in self.entries():
            if entry.name.lower() == wanted:
                return entry
        return None

    # -- payload ------------------------------------------------------------
    def read(self, entry: RdaEntry) -> bytes:
        """Pull one file's bytes out of the archive."""
        if entry.is_deleted:
            return b""
        self.open()
        data = self._read_at(entry.offset, entry.compressed_size)
        if entry.is_compressed:
            try:
                data = zlib.decompress(data)
            except zlib.error as exc:
                raise RdaError(tr(
                    "could not decompress %(name)s: %(error)s", name=entry.name, error=exc,
                )) from exc
        return data

    def extract(
        self,
        entry: RdaEntry,
        destination: Path,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """Write one file to ``destination``, creating parent folders."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = self.read(entry)
        destination.write_bytes(data)
        if progress is not None:
            progress(len(data), len(data))
        return destination

    def _read_at(self, offset: int, length: int) -> bytes:
        # Every public entry point calls open() before reaching here.
        assert self._fh is not None
        self._fh.seek(offset)
        return self._fh.read(length)


def _parse_entry(chunk: bytes, block_flags: int) -> Optional[RdaEntry]:
    name = chunk[:NAME_BYTES].decode("utf-16-le", "replace").rstrip("\x00").strip()
    if not name or "\x00" in name:
        return None
    offset, csize, usize, timestamp, _flags = struct.unpack_from("<QQQQQ", chunk, NAME_BYTES)
    return RdaEntry(
        name=name.replace("\\", "/"),
        offset=offset,
        compressed_size=csize,
        uncompressed_size=usize,
        timestamp=timestamp,
        block_flags=block_flags,
    )


def is_rda(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC_V22)) in (MAGIC_V22, MAGIC_V20)
    except OSError:
        return False


_NUM = re.compile(r"(\d+)")


def natural_key(name: str):
    """Sort ``data9`` before ``data33``.

    Archive precedence is load order, and load order is numeric: later archives
    patch earlier ones.  Plain lexicographic sorting would put ``data33`` before
    ``data9`` and pick a stale file.
    """
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in _NUM.split(name) if part != ""
    )


def archives_in_load_order(folder: Path) -> List[Path]:
    """Every ``.rda`` in ``folder``, earliest-loaded first."""
    try:
        found = [p for p in Path(folder).iterdir() if p.suffix.lower() == ".rda" and p.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda p: natural_key(p.name))
