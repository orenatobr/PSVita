"""Tests for psp.iso9660."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

import psp.iso9660 as iso9660

_SECTOR = iso9660.SECTOR_SIZE
_ICON_OFFSET = 19 * _SECTOR


# ---------------------------------------------------------------------------
# find_icon0 / read_range / write_range_in_place
# ---------------------------------------------------------------------------


def test_find_icon0_offset_and_length(tmp_path: Path, make_psp_iso: Callable[..., Path], real_icon_png: bytes) -> None:
    iso = make_psp_iso(tmp_path / "game.iso", real_icon_png)
    offset, length = iso9660.find_icon0(iso)
    assert offset == _ICON_OFFSET
    assert length == len(real_icon_png)


def test_read_range_returns_icon_bytes(tmp_path: Path, make_psp_iso: Callable[..., Path], real_icon_png: bytes) -> None:
    iso = make_psp_iso(tmp_path / "game.iso", real_icon_png)
    offset, length = iso9660.find_icon0(iso)
    assert iso9660.read_range(iso, offset, length) == real_icon_png


def test_write_range_in_place_preserves_size(
    tmp_path: Path, make_psp_iso: Callable[..., Path], real_icon_png: bytes
) -> None:
    iso = make_psp_iso(tmp_path / "game.iso", real_icon_png)
    before = iso.stat().st_size
    payload = b"\xaa\xbb\xcc\xdd"
    iso9660.write_range_in_place(iso, _ICON_OFFSET, payload)
    assert iso.stat().st_size == before
    assert iso9660.read_range(iso, _ICON_OFFSET, len(payload)) == payload


def test_find_icon0_missing_psp_game(tmp_path: Path, make_psp_iso: Callable[..., Path], real_icon_png: bytes) -> None:
    iso = make_psp_iso(tmp_path / "game.iso", real_icon_png, with_psp_game=False)
    with pytest.raises(ValueError, match="PSP_GAME directory not found"):
        iso9660.find_icon0(iso)


def test_find_icon0_missing_icon(tmp_path: Path, make_psp_iso: Callable[..., Path], real_icon_png: bytes) -> None:
    iso = make_psp_iso(tmp_path / "game.iso", real_icon_png, with_icon=False)
    with pytest.raises(ValueError, match="ICON0.PNG not found"):
        iso9660.find_icon0(iso)


# ---------------------------------------------------------------------------
# _find_pvd
# ---------------------------------------------------------------------------


def _write_sectors(path: Path, sectors: list[bytes]) -> Path:
    """Write 16 empty system sectors followed by the given descriptor sectors."""
    with path.open("wb") as fp:
        fp.write(b"\x00" * (16 * _SECTOR))
        for sector in sectors:
            fp.write(sector.ljust(_SECTOR, b"\x00"))
    return path


def _descriptor(descriptor_type: int) -> bytes:
    return bytes([descriptor_type]) + b"CD001" + b"\x01"


def test_find_pvd_immediate(tmp_path: Path) -> None:
    iso = _write_sectors(tmp_path / "x.iso", [_descriptor(1)])
    with iso.open("rb") as fp:
        pvd = iso9660._find_pvd(fp)
    assert pvd[0] == 1


def test_find_pvd_skips_boot_record(tmp_path: Path) -> None:
    # Sector 16 is a boot record (type 0); the PVD is at sector 17.
    iso = _write_sectors(tmp_path / "x.iso", [_descriptor(0), _descriptor(1)])
    with iso.open("rb") as fp:
        pvd = iso9660._find_pvd(fp)
    assert pvd[0] == 1


def test_find_pvd_not_iso9660(tmp_path: Path) -> None:
    iso = _write_sectors(tmp_path / "x.iso", [b"NOPE!"])
    with iso.open("rb") as fp, pytest.raises(ValueError, match="not an ISO9660"):
        iso9660._find_pvd(fp)


def test_find_pvd_terminator_before_pvd(tmp_path: Path) -> None:
    iso = _write_sectors(tmp_path / "x.iso", [_descriptor(255)])
    with iso.open("rb") as fp, pytest.raises(ValueError, match="no Primary Volume Descriptor"):
        iso9660._find_pvd(fp)


# ---------------------------------------------------------------------------
# _iter_records edge cases
# ---------------------------------------------------------------------------


def test_iter_records_truncated_record() -> None:
    # A record claiming length 40 but with too few bytes to hold a name is dropped.
    data = bytes([40, 0, 0, 0, 0, 0])
    assert list(iso9660._iter_records(data)) == []


def test_iter_records_exits_without_trailing_padding() -> None:
    # Two records that fill the buffer exactly: the loop ends via `pos == size`,
    # not via an early return on a zero-length padding byte.
    from tests.conftest import _iso_dir_record

    r1 = _iso_dir_record(b"AAAAAAAA", 100, _SECTOR, is_dir=True)
    r2 = _iso_dir_record(b"BBBBBBBB", 200, _SECTOR, is_dir=False)
    names = [name for name, *_ in iso9660._iter_records(r1 + r2)]
    assert names == [b"AAAAAAAA", b"BBBBBBBB"]


def test_iter_records_spans_sectors() -> None:
    # Records in sector 0, zero padding, then more records in sector 1.
    from tests.conftest import _iso_dir_record  # reuse the builder

    sector0 = _iso_dir_record(b"AAAAAAAA", 100, _SECTOR, is_dir=True).ljust(_SECTOR, b"\x00")
    sector1 = _iso_dir_record(b"BBBBBBBB", 200, _SECTOR, is_dir=True).ljust(_SECTOR, b"\x00")
    names = [name for name, *_ in iso9660._iter_records(sector0 + sector1)]
    assert names == [b"AAAAAAAA", b"BBBBBBBB"]
