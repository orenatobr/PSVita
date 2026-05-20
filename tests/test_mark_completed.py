"""Tests for psvita.mark_completed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from psvita import mark_completed as mc
from psvita.config import Config

# ---------------------------------------------------------------------------
# _remote_url
# ---------------------------------------------------------------------------


def test_remote_url_format(apply_config: Config) -> None:
    url = mc._remote_url("PCSA00029")
    assert url == f"ftp://{apply_config.vita.ip}:{apply_config.vita.port}/ur0:/appmeta/PCSA00029/icon0.png"


def test_sce_sys_url_format(apply_config: Config) -> None:
    url = mc._sce_sys_url("PCSA00029")
    assert url == f"ftp://{apply_config.vita.ip}:{apply_config.vita.port}/ux0:/app/PCSA00029/sce_sys/icon0.png"


# ---------------------------------------------------------------------------
# _make_placeholder
# ---------------------------------------------------------------------------


def test_placeholder_default_size() -> None:
    img = mc._make_placeholder()
    assert img.size == (128, 128)
    assert img.mode == "RGB"


def test_placeholder_custom_size() -> None:
    img = mc._make_placeholder(size=64)
    assert img.size == (64, 64)


# ---------------------------------------------------------------------------
# apply_badge
# ---------------------------------------------------------------------------


def test_apply_badge_returns_rgb(sample_icon: Image.Image, apply_config: Config) -> None:
    result = mc.apply_badge(sample_icon)
    assert result.mode == "RGB"
    assert result.size == sample_icon.size


def test_apply_badge_converts_rgba(apply_config: Config) -> None:
    rgba = Image.new("RGBA", (128, 128), color=(0, 0, 0, 255))
    result = mc.apply_badge(rgba)
    assert result.mode == "RGB"


def test_apply_badge_badge_pixels(sample_icon: Image.Image, apply_config: Config) -> None:
    result = mc.apply_badge(sample_icon)
    cx, cy = apply_config.badge.cx, apply_config.badge.cy
    assert result.getpixel((cx, cy)) == apply_config.badge.fill


def test_apply_badge_does_not_mutate_source(sample_icon: Image.Image, apply_config: Config) -> None:
    original_pixel = sample_icon.getpixel((apply_config.badge.cx, apply_config.badge.cy))
    mc.apply_badge(sample_icon)
    assert sample_icon.getpixel((apply_config.badge.cx, apply_config.badge.cy)) == original_pixel


# ---------------------------------------------------------------------------
# main() -- all branches
# ---------------------------------------------------------------------------


def _run_main(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch sys.argv and call main()."""
    monkeypatch.setattr("sys.argv", ["mark-completed"] + args)
    mc.main()


def _make_real_png(path: Path, size: int = 128) -> None:
    """Write a minimal valid PNG to *path*."""
    img = Image.new("RGB", (size, size), color=(50, 50, 50))
    img.save(path, format="PNG")


class TestMain:
    def test_dry_run_backup_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config) -> None:
        """When the backup already exists, --dry-run skips upload."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"
        _make_real_png(backup)

        with patch("subprocess.run") as mock_run:
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        # curl must not have been called (no download needed, no upload on dry-run)
        mock_run.assert_not_called()
        # Modified file is still created locally
        assert (tmp_path / "PCSA00029_icon0_modified.png").exists()

    def test_no_dry_run_backup_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """When the backup exists and dry-run is off, both upload targets are called."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"
        _make_real_png(backup)
        # Create the local sce_sys directory so the local copy succeeds.
        (tmp_path / "app" / "PCSA00029" / "sce_sys").mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            _run_main(["PCSA00029"], monkeypatch)

        # curl called twice: once for ur0:appmeta/ and once for ux0:app/sce_sys/
        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            assert "-T" in call[0][0]

    def test_upload_curl_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """FileNotFoundError during upload exits with code 1 and prints a message."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"
        _make_real_png(backup)
        (tmp_path / "app" / "PCSA00029" / "sce_sys").mkdir(parents=True)

        with (
            patch("psvita.mark_completed._ftp_upload", side_effect=FileNotFoundError),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main(["PCSA00029"], monkeypatch)

        assert exc_info.value.code == 1
        assert "curl" in capsys.readouterr().out

    def test_no_dry_run_writes_local_sce_sys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """Running without --dry-run copies the badged icon to local app/sce_sys/."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"
        _make_real_png(backup)
        sce_sys_dir = tmp_path / "app" / "PCSA00029" / "sce_sys"
        sce_sys_dir.mkdir(parents=True)

        with patch("subprocess.run"):
            _run_main(["PCSA00029"], monkeypatch)

        local_icon = sce_sys_dir / "icon0.png"
        assert local_icon.exists()
        img = Image.open(local_icon)
        assert img.size == (128, 128)

    def test_dry_run_does_not_write_local_sce_sys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """--dry-run must not write to the local sce_sys directory."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"
        _make_real_png(backup)
        sce_sys_dir = tmp_path / "app" / "PCSA00029" / "sce_sys"
        sce_sys_dir.mkdir(parents=True)

        with patch("subprocess.run") as mock_run:
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        mock_run.assert_not_called()
        assert not (sce_sys_dir / "icon0.png").exists()

    def test_download_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config) -> None:
        """No existing backup; download succeeds and writes a valid PNG."""
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            # Simulate curl writing a PNG to the -o destination.
            if "-o" in cmd:
                out_path = Path(cmd[cmd.index("-o") + 1])
                _make_real_png(out_path)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        backup = tmp_path / "originals" / "PCSA00029_icon0.png"
        assert backup.exists()
        assert backup.stat().st_size >= 100

    def test_download_fails_returncode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """curl returns non-zero; placeholder is generated (backup did not exist)."""
        monkeypatch.chdir(tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        backup = tmp_path / "originals" / "PCSA00029_icon0.png"
        # Placeholder is saved as the backup.
        assert backup.exists()
        img = Image.open(backup)
        assert img.size == (128, 128)

    def test_download_file_too_small(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """curl succeeds but writes < 100 bytes; placeholder replaces the file."""
        monkeypatch.chdir(tmp_path)

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if "-o" in cmd:
                out_path = Path(cmd[cmd.index("-o") + 1])
                out_path.write_bytes(b"tiny")  # 4 bytes -- below the 100-byte threshold
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=fake_run):
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        backup = tmp_path / "originals" / "PCSA00029_icon0.png"
        # The tiny file was replaced by the placeholder PNG.
        assert backup.exists()
        img = Image.open(backup)
        assert img.size == (128, 128)

    def test_download_ok_false_backup_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply_config: Config
    ) -> None:
        """curl returns non-zero AND the partial file exists; it is unlinked then
        replaced by a placeholder (exercises the ``if backup.exists(): unlink`` branch)."""
        monkeypatch.chdir(tmp_path)
        originals = tmp_path / "originals"
        originals.mkdir()
        backup = originals / "PCSA00029_icon0.png"

        def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if "-o" in cmd:
                # Write a partial/corrupt file so backup.exists() is True.
                out_path = Path(cmd[cmd.index("-o") + 1])
                out_path.write_bytes(b"corrupt")
            result = MagicMock()
            result.returncode = 1  # non-zero signals failure
            return result

        with patch("subprocess.run", side_effect=fake_run):
            _run_main(["PCSA00029", "--dry-run"], monkeypatch)

        # Placeholder should have replaced the corrupt file.
        assert backup.exists()
        img = Image.open(backup)
        assert img.size == (128, 128)
