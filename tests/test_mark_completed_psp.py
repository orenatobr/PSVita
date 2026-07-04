"""Tests for psp.mark_completed."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

import psp.iso9660 as iso9660
import psp.mark_completed as mc
from psvita.config import Config

_ICON_OFFSET = 19 * iso9660.SECTOR_SIZE


def _run_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mark-completed-psp", *argv])
    mc.main()


def _place_iso(tmp_path: Path, make_psp_iso: Callable[..., Path], icon: bytes, name: str = "My Game (USA).iso") -> Path:
    return make_psp_iso(tmp_path / "pspemu" / "ISO" / name, icon)


# ---------------------------------------------------------------------------
# _resolve_iso
# ---------------------------------------------------------------------------


def test_resolve_exact_match(tmp_path: Path) -> None:
    iso_dir = tmp_path / "iso"
    iso_dir.mkdir()
    (iso_dir / "Crisis Core.iso").write_bytes(b"x")
    assert mc._resolve_iso("Crisis Core", iso_dir).name == "Crisis Core.iso"


def test_resolve_substring_match(tmp_path: Path) -> None:
    iso_dir = tmp_path / "iso"
    iso_dir.mkdir()
    (iso_dir / "God of War Ghost of Sparta.iso").write_bytes(b"x")
    assert mc._resolve_iso("Ghost", iso_dir).name == "God of War Ghost of Sparta.iso"


def test_resolve_no_match_exits(tmp_path: Path) -> None:
    iso_dir = tmp_path / "iso"
    iso_dir.mkdir()
    (iso_dir / "Bastion.iso").write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        mc._resolve_iso("Nope", iso_dir)
    assert exc.value.code == 1


def test_resolve_ambiguous_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    iso_dir = tmp_path / "iso"
    iso_dir.mkdir()
    (iso_dir / "GTA Liberty City.iso").write_bytes(b"x")
    (iso_dir / "GTA Vice City.iso").write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        mc._resolve_iso("GTA", iso_dir)
    assert exc.value.code == 1
    assert "ambiguous" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_remote_url_percent_encodes(apply_config: Config) -> None:
    url = mc._remote_url("My Game (USA).iso")
    expected = f"ftp://{apply_config.vita.ip}:{apply_config.vita.port}/ux0:/pspemu/ISO/My%20Game%20%28USA%29.iso"
    assert url == expected


def test_encode_fitted_returns_none_when_too_small(real_icon_png: bytes) -> None:
    img = Image.open(io.BytesIO(real_icon_png))
    assert mc._encode_fitted(img, max_len=0) is None


def test_encode_fitted_returns_bytes(real_icon_png: bytes) -> None:
    img = Image.open(io.BytesIO(real_icon_png))
    data = mc._encode_fitted(img, max_len=len(real_icon_png))
    assert data is not None and len(data) <= len(real_icon_png)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_dry_run_leaves_iso_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
) -> None:
    monkeypatch.chdir(tmp_path)
    iso = _place_iso(tmp_path, make_psp_iso, real_icon_png)
    original = iso9660.read_range(iso, _ICON_OFFSET, len(real_icon_png))

    with patch("subprocess.run") as mock_run:
        _run_main(["My Game", "--dry-run"], monkeypatch)

    mock_run.assert_not_called()
    assert iso9660.read_range(iso, _ICON_OFFSET, len(real_icon_png)) == original
    assert (tmp_path / "My Game (USA)_ICON0_modified.png").exists()
    assert (tmp_path / "originals" / "My Game (USA)_ICON0.png").exists()


def test_main_local_only_patches_without_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
) -> None:
    monkeypatch.chdir(tmp_path)
    iso = _place_iso(tmp_path, make_psp_iso, real_icon_png)
    before_size = iso.stat().st_size

    with patch("subprocess.run") as mock_run:
        _run_main(["My Game", "--local-only"], monkeypatch)

    mock_run.assert_not_called()
    assert iso.stat().st_size == before_size
    region = iso9660.read_range(iso, _ICON_OFFSET, len(real_icon_png))
    assert region != real_icon_png
    # The patched region is still a decodable PNG.
    assert Image.open(io.BytesIO(region)).size == (144, 80)


def test_main_uploads_full_iso(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
) -> None:
    monkeypatch.chdir(tmp_path)
    iso = _place_iso(tmp_path, make_psp_iso, real_icon_png)

    with patch("subprocess.run") as mock_run:
        _run_main(["My Game"], monkeypatch)

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "-T" in cmd
    assert cmd[cmd.index("-T") + 1].endswith(iso.name)
    assert cmd[-1].endswith("/ux0:/pspemu/ISO/My%20Game%20%28USA%29.iso")


def test_main_upload_curl_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _place_iso(tmp_path, make_psp_iso, real_icon_png)

    with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(SystemExit) as exc:
        _run_main(["My Game"], monkeypatch)

    assert exc.value.code == 1
    assert "curl is not installed" in capsys.readouterr().out


def test_main_aborts_when_icon_cannot_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    iso = _place_iso(tmp_path, make_psp_iso, real_icon_png)
    original = iso9660.read_range(iso, _ICON_OFFSET, len(real_icon_png))
    monkeypatch.setattr(mc, "_encode_fitted", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exc:
        _run_main(["My Game"], monkeypatch)

    assert exc.value.code == 1
    assert "cannot be compressed to fit" in capsys.readouterr().out
    assert iso9660.read_range(iso, _ICON_OFFSET, len(real_icon_png)) == original


def test_main_reuses_cached_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    apply_config: Config,
    make_psp_iso: Callable[..., Path],
    real_icon_png: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _place_iso(tmp_path, make_psp_iso, real_icon_png)
    backup = tmp_path / "originals"
    backup.mkdir()
    (backup / "My Game (USA)_ICON0.png").write_bytes(real_icon_png)

    _run_main(["My Game", "--local-only"], monkeypatch)

    assert "Using cached original" in capsys.readouterr().out
