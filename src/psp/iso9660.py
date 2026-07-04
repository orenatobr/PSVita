"""Minimal read/patch helpers for the ISO9660 layout of PSP game images.

Only the sliver of ISO9660 needed to locate and overwrite ``PSP_GAME/ICON0.PNG``
inside a PSP disc image is implemented -- there is no full filesystem support.

Why this exists: VitaShell's FTP server can only truncate-and-rewrite a whole
file or append at EOF, so the icon *inside* an on-device ISO cannot be patched
remotely.  Instead we patch the local copy of the ISO in place (a plain seek +
write on local disk) and re-upload the whole file.

All multi-byte fields in ISO9660 are stored "both-endian" (little-endian value
followed by the big-endian value); we always read the little-endian half.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

SECTOR_SIZE = 2048

# Offsets within a Directory Record (ECMA-119, section 9.1).
_DR_EXTENT_LBA = 2  # little-endian LBA of the file/dir extent (4 bytes)
_DR_DATA_LEN = 10  # little-endian data length in bytes (4 bytes)
_DR_FLAGS = 25  # file flags; bit 1 (0x02) marks a directory
_DR_NAME_LEN = 32  # length of the file identifier (1 byte)
_DR_NAME = 33  # start of the file identifier

_FLAG_DIRECTORY = 0x02

# The root Directory Record lives at this offset inside the Primary Volume
# Descriptor and is a fixed 34 bytes long.
_PVD_ROOT_RECORD = 156
_ROOT_RECORD_LEN = 34


def _le32(buf: bytes, offset: int) -> int:
    """Read a little-endian unsigned 32-bit integer from ``buf`` at ``offset``."""
    return int.from_bytes(buf[offset : offset + 4], "little")


def _read_extent(fp: BinaryIO, lba: int, length: int) -> bytes:
    """Read ``length`` bytes of a file/directory extent starting at sector ``lba``."""
    fp.seek(lba * SECTOR_SIZE)
    return fp.read(length)


def _normalize_name(name: bytes) -> str:
    """Return an uppercase name with any ISO9660 ``;version`` suffix stripped."""
    return name.split(b";", 1)[0].decode("ascii", "replace").upper()


def _iter_records(data: bytes) -> Iterator[tuple[bytes, int, int, bool]]:
    """Yield ``(name, lba, data_length, is_dir)`` for each record in a directory extent.

    A Directory Record never crosses a logical-sector boundary; the tail of a
    sector is zero-padded.  A zero length byte therefore means "skip to the next
    sector".

    Args:
        data: Raw bytes of a directory extent.

    Yields:
        Tuples of the raw name bytes, extent LBA, data length, and a directory flag.
    """
    pos = 0
    size = len(data)
    while pos < size:
        record_len = data[pos]
        if record_len == 0:
            # Advance to the next sector boundary; the rest of this sector is padding.
            next_boundary = ((pos // SECTOR_SIZE) + 1) * SECTOR_SIZE
            if next_boundary >= size:
                return
            pos = next_boundary
            continue
        record = data[pos : pos + record_len]
        if len(record) < _DR_NAME:
            return
        name_len = record[_DR_NAME_LEN]
        name = record[_DR_NAME : _DR_NAME + name_len]
        lba = _le32(record, _DR_EXTENT_LBA)
        data_len = _le32(record, _DR_DATA_LEN)
        is_dir = bool(record[_DR_FLAGS] & _FLAG_DIRECTORY)
        yield name, lba, data_len, is_dir
        pos += record_len


def _find_entry(data: bytes, target: str, *, want_dir: bool) -> tuple[int, int] | None:
    """Return ``(lba, length)`` of ``target`` in a directory extent, or None.

    Args:
        data: Raw bytes of the directory extent to search.
        target: Entry name to match (case-insensitive, version-suffix ignored).
        want_dir: Match a subdirectory when True, a file when False.
    """
    wanted = target.upper()
    for name, lba, length, is_dir in _iter_records(data):
        # Skip the "." (0x00) and ".." (0x01) self/parent entries.
        if name in (b"\x00", b"\x01"):
            continue
        if is_dir == want_dir and _normalize_name(name) == wanted:
            return lba, length
    return None


def _find_pvd(fp: BinaryIO) -> bytes:
    """Return the Primary Volume Descriptor sector.

    Volume descriptors begin at sector 16.  The first one is normally the PVD
    (type 1); scan forward until it is found or the descriptor set terminates.

    Raises:
        ValueError: If the image is not ISO9660 or has no Primary Volume Descriptor.
    """
    lba = 16
    while True:
        fp.seek(lba * SECTOR_SIZE)
        descriptor = fp.read(SECTOR_SIZE)
        if len(descriptor) < 7 or descriptor[1:6] != b"CD001":
            raise ValueError("not an ISO9660 image (missing 'CD001' identifier)")
        descriptor_type = descriptor[0]
        if descriptor_type == 1:  # Primary Volume Descriptor
            return descriptor
        if descriptor_type == 255:  # Volume Descriptor Set Terminator
            raise ValueError("no Primary Volume Descriptor found")
        lba += 1


def find_icon0(iso: Path) -> tuple[int, int]:
    """Locate ``PSP_GAME/ICON0.PNG`` inside a PSP ISO.

    Args:
        iso: Path to the local PSP disc image.

    Returns:
        A ``(byte_offset, length)`` tuple: the absolute byte offset of the icon
        data within the ISO and its data length as recorded in the directory.

    Raises:
        ValueError: If the image is not ISO9660, is not a PSP game (no
            ``PSP_GAME`` directory), or lacks ``PSP_GAME/ICON0.PNG``.
    """
    with iso.open("rb") as fp:
        pvd = _find_pvd(fp)
        root_record = pvd[_PVD_ROOT_RECORD : _PVD_ROOT_RECORD + _ROOT_RECORD_LEN]
        root_lba = _le32(root_record, _DR_EXTENT_LBA)
        root_len = _le32(root_record, _DR_DATA_LEN)

        root_data = _read_extent(fp, root_lba, root_len)
        psp_game = _find_entry(root_data, "PSP_GAME", want_dir=True)
        if psp_game is None:
            raise ValueError(f"{iso.name}: PSP_GAME directory not found (not a PSP game ISO?)")

        psp_data = _read_extent(fp, psp_game[0], psp_game[1])
        icon = _find_entry(psp_data, "ICON0.PNG", want_dir=False)
        if icon is None:
            raise ValueError(f"{iso.name}: PSP_GAME/ICON0.PNG not found")

    return icon[0] * SECTOR_SIZE, icon[1]


def read_range(iso: Path, offset: int, length: int) -> bytes:
    """Read ``length`` bytes from ``iso`` starting at absolute ``offset``."""
    with iso.open("rb") as fp:
        fp.seek(offset)
        return fp.read(length)


def write_range_in_place(iso: Path, offset: int, data: bytes) -> None:
    """Overwrite bytes of ``iso`` at absolute ``offset`` with ``data`` in place.

    The file is opened ``r+b`` so only the given range is rewritten and the total
    file size is preserved -- the caller must ensure ``data`` does not overrun the
    region it is patching.
    """
    with iso.open("r+b") as fp:
        fp.seek(offset)
        fp.write(data)
