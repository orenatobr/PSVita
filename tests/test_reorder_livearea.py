"""Tests for psvita.reorder_livearea."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import psvita.reorder_livearea as rl
from psvita.config import Config

# ---------------------------------------------------------------------------
# ftp_download wrapper
# ---------------------------------------------------------------------------


def test_ftp_download_raises_on_curl_failure(tmp_path: Path) -> None:
    """ftp_download wrapper raises CalledProcessError when ftp.download returns False."""
    local = tmp_path / "app.db"
    with patch("psvita.ftp.download", return_value=False), pytest.raises(subprocess.CalledProcessError):
        rl.ftp_download("ftp://host/app.db", local)


def test_ftp_download_succeeds_when_download_returns_true(tmp_path: Path) -> None:
    """ftp_download wrapper does not raise when ftp.download returns True."""
    local = tmp_path / "app.db"
    with patch("psvita.ftp.download", return_value=True):
        rl.ftp_download("ftp://host/app.db", local)  # must not raise


def test_ftp_upload_delegates_to_ftp_module(tmp_path: Path) -> None:
    """ftp_upload delegates to psvita.ftp.upload."""
    local = tmp_path / "app.db"
    local.write_bytes(b"dummy")
    with patch("psvita.ftp.upload") as mock_upload:
        rl.ftp_upload(local, "ftp://host/app.db")
    mock_upload.assert_called_once_with(local, "ftp://host/app.db")


# ---------------------------------------------------------------------------
# reorder()
# ---------------------------------------------------------------------------


def test_reorder_normal(sample_db: Path, apply_config: Config) -> None:
    """Normal run commits 5 new pages (1 CFW + 4 game) and preserves the system page."""
    rl.reorder(sample_db)

    conn = sqlite3.connect(str(sample_db))
    cur = conn.cursor()

    # 1 system page + 1 CFW page + 4 game pages = 6
    cur.execute("SELECT COUNT(*) FROM tbl_appinfo_page")
    assert cur.fetchone()[0] == 6

    # All CFW and game icons are re-inserted.
    cur.execute("SELECT COUNT(*) FROM tbl_appinfo_icon WHERE titleId NOT LIKE 'NPXS%'")
    assert cur.fetchone()[0] == len(apply_config.reorder.cfw_order) + len(apply_config.reorder.game_order)

    conn.close()


def test_reorder_dry_run(sample_db: Path, apply_config: Config) -> None:
    """dry_run=True rolls back all changes; the DB state remains unchanged."""
    conn = sqlite3.connect(str(sample_db))
    pages_before = conn.execute("SELECT COUNT(*) FROM tbl_appinfo_page").fetchone()[0]
    conn.close()

    rl.reorder(sample_db, dry_run=True)

    conn = sqlite3.connect(str(sample_db))
    pages_after = conn.execute("SELECT COUNT(*) FROM tbl_appinfo_page").fetchone()[0]
    conn.close()

    assert pages_before == pages_after


def test_reorder_unknown_ids(sample_db: Path, capsys: pytest.CaptureFixture[str], apply_config: Config) -> None:
    """Titles present in the DB but absent from the ordered lists are appended with a warning."""
    conn = sqlite3.connect(str(sample_db))
    conn.execute(
        "INSERT INTO tbl_appinfo_icon"
        " (pageId, pos, titleId, type, icon0Type, iconPath, parentalLockLv, status, title)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (10, 99, "XXXUNKNOWN", 1, 0, "", 0, 0, "Unknown Game"),
    )
    conn.commit()
    conn.close()

    rl.reorder(sample_db, dry_run=True)

    out = capsys.readouterr().out
    assert "[WARN] In db but not in ordered list" in out
    assert "XXXUNKNOWN" in out


def test_reorder_missing_ids(sample_db: Path, capsys: pytest.CaptureFixture[str], apply_config: Config) -> None:
    """Titles in the ordered list but absent from the DB emit a warning and are skipped."""
    conn = sqlite3.connect(str(sample_db))
    conn.execute("DELETE FROM tbl_appinfo_icon WHERE titleId = 'VITASHELL'")
    conn.execute("DELETE FROM tbl_appinfo_icon WHERE titleId = 'PCSE00700'")
    conn.commit()
    conn.close()

    rl.reorder(sample_db, dry_run=True)

    out = capsys.readouterr().out
    assert "[WARN] In ordered list but not installed (skipped)" in out
    assert "VITASHELL" in out


def test_reorder_no_system_pages(sample_db_no_system_pages: Path, apply_config: Config) -> None:
    """When no NPXS* entries exist, reorder() aborts with sys.exit(1)."""
    with pytest.raises(SystemExit) as exc_info:
        rl.reorder(sample_db_no_system_pages)
    assert exc_info.value.code == 1


def test_reorder_extra_cols_with_template(
    sample_db: Path, capsys: pytest.CaptureFixture[str], apply_config: Config
) -> None:
    """Template row found on the system page -- bgColor and reserved01 are reported."""
    rl.reorder(sample_db, dry_run=True)

    out = capsys.readouterr().out
    assert "[INFO] Page template read from system page" in out


def test_reorder_extra_cols_no_template_row(
    sample_db_no_template_row: Path, capsys: pytest.CaptureFixture[str], apply_config: Config
) -> None:
    """Extra cols present but no system page row in tbl_appinfo_page -- NULL fallback message."""
    rl.reorder(sample_db_no_template_row, dry_run=True)

    out = capsys.readouterr().out
    assert "[INFO] No system page found; new pages will use NULL attributes." in out


def test_reorder_no_extra_cols(sample_db_no_extra_cols: Path, apply_config: Config) -> None:
    """Schema without extra page columns runs without error and commits correctly."""
    rl.reorder(sample_db_no_extra_cols)

    conn = sqlite3.connect(str(sample_db_no_extra_cols))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tbl_appinfo_page")
    # 1 system + 1 CFW + 4 game pages = 6
    assert cur.fetchone()[0] == 6
    conn.close()


def test_reorder_title_col_absent(sample_db_no_title_col: Path, apply_config: Config) -> None:
    """When tbl_appinfo_icon has no title column, titleId is used as display fallback."""
    rl.reorder(sample_db_no_title_col)

    conn = sqlite3.connect(str(sample_db_no_title_col))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tbl_appinfo_icon WHERE titleId NOT LIKE 'NPXS%'")
    assert cur.fetchone()[0] == len(apply_config.reorder.cfw_order) + len(apply_config.reorder.game_order)
    conn.close()


def test_reorder_pagination(sample_db: Path, apply_config: Config) -> None:
    """37 games across items_per_page=10 produces 4 game pages; last page has 7 items."""
    rl.reorder(sample_db)

    conn = sqlite3.connect(str(sample_db))
    cur = conn.cursor()

    # 1 system + 1 CFW + 4 game pages = 6
    cur.execute("SELECT COUNT(*) FROM tbl_appinfo_page")
    assert cur.fetchone()[0] == 6

    # Last page contains 37 % 10 = 7 items (positions 0 through 6).
    cur.execute("SELECT MAX(pos) FROM tbl_appinfo_icon WHERE pageId = (SELECT MAX(pageId) FROM tbl_appinfo_page)")
    assert cur.fetchone()[0] == 6

    conn.close()


def test_reorder_livearea_existing_rows_updated(sample_db: Path, apply_config: Config) -> None:
    """Only pre-existing livearea rows get titleColor=0xFFFFFF; no new rows are inserted."""
    rl.reorder(sample_db)

    conn = sqlite3.connect(str(sample_db))
    cur = conn.cursor()

    # The 3 pre-existing rows are still the only non-NPXS rows (none were added).
    cur.execute("SELECT titleId FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%'")
    livearea_ids = {row[0] for row in cur.fetchall()}
    assert livearea_ids == {"VITASHELL", "PSPEMUCFW", "AUTOPLUG2"}

    # The 3 existing rows were updated to white label colour.
    cur.execute(
        "SELECT COUNT(*) FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%' AND titleColor = ?",
        (0xFFFFFF,),
    )
    assert cur.fetchone()[0] == 3

    # Artwork fields on existing rows were not overwritten.
    cur.execute("SELECT background_image, gate_startupImage FROM tbl_livearea WHERE titleId = 'VITASHELL'")
    assert cur.fetchone() == ("bg.png", "startup.png")

    conn.close()


def test_reorder_livearea_cleans_stale_rows(
    sample_db_stale_livearea: Path, capsys: pytest.CaptureFixture[str], apply_config: Config
) -> None:
    """Stale NULL-artwork rows are deleted and an [INFO] message is printed."""
    rl.reorder(sample_db_stale_livearea)
    out = capsys.readouterr().out
    assert "[INFO] tbl_livearea: 4 stale NULL-artwork rows removed." in out

    conn = sqlite3.connect(str(sample_db_stale_livearea))
    cur = conn.cursor()

    # Only the 3 original good rows remain; the 4 stale rows were removed.
    cur.execute("SELECT titleId FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%'")
    assert {row[0] for row in cur.fetchall()} == {"VITASHELL", "PSPEMUCFW", "AUTOPLUG2"}

    # Good rows keep their artwork.
    cur.execute("SELECT background_image FROM tbl_livearea WHERE titleId = 'VITASHELL'")
    assert cur.fetchone()[0] == "bg.png"

    conn.close()


def test_reorder_livearea_full_rows_updated(sample_db_full_livearea: Path, apply_config: Config) -> None:
    """When all 44 titles have livearea rows, all get titleColor=0xFFFFFF and artwork is preserved."""
    rl.reorder(sample_db_full_livearea)

    conn = sqlite3.connect(str(sample_db_full_livearea))
    cur = conn.cursor()

    # Row count stays at 44 (no rows added or removed).
    cur.execute("SELECT COUNT(*) FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%'")
    assert cur.fetchone()[0] == len(apply_config.reorder.cfw_order) + len(apply_config.reorder.game_order)

    # All rows updated to white label colour.
    cur.execute(
        "SELECT COUNT(*) FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%' AND titleColor = ?",
        (0xFFFFFF,),
    )
    assert cur.fetchone()[0] == len(apply_config.reorder.cfw_order) + len(apply_config.reorder.game_order)

    # Artwork fields preserved.
    cur.execute("SELECT COUNT(*) FROM tbl_livearea WHERE titleId NOT LIKE 'NPXS%' AND background_image = 'bg.png'")
    assert cur.fetchone()[0] == len(apply_config.reorder.cfw_order) + len(apply_config.reorder.game_order)

    conn.close()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _run_main(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["reorder-livearea"] + args)
    rl.main()


class TestMain:
    def test_default_run(
        self,
        tmp_path: Path,
        sample_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        apply_config: Config,
    ) -> None:
        """Normal run: downloads DB, creates backup, reorders, and uploads."""
        monkeypatch.chdir(tmp_path)

        def fake_download(remote: str, local: Path) -> None:
            shutil.copy(sample_db, local)

        with (
            patch("psvita.reorder_livearea.ftp_download", side_effect=fake_download),
            patch("psvita.reorder_livearea.ftp_upload") as mock_upload,
        ):
            _run_main([], monkeypatch)

        mock_upload.assert_called_once()
        assert (tmp_path / "app.db.bak").exists()

    def test_dry_run(
        self,
        tmp_path: Path,
        sample_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        apply_config: Config,
    ) -> None:
        """--dry-run: reorder runs but upload is never called."""
        monkeypatch.chdir(tmp_path)

        def fake_download(remote: str, local: Path) -> None:
            shutil.copy(sample_db, local)

        with (
            patch("psvita.reorder_livearea.ftp_download", side_effect=fake_download),
            patch("psvita.reorder_livearea.ftp_upload") as mock_upload,
        ):
            _run_main(["--dry-run"], monkeypatch)

        mock_upload.assert_not_called()

    def test_restore_backup_exists(
        self,
        tmp_path: Path,
        sample_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        apply_config: Config,
    ) -> None:
        """--restore-backup: uses the local backup; ftp_download is not called."""
        monkeypatch.chdir(tmp_path)
        shutil.copy(sample_db, tmp_path / "app.db.bak")

        with (
            patch("psvita.reorder_livearea.ftp_download") as mock_download,
            patch("psvita.reorder_livearea.ftp_upload") as mock_upload,
        ):
            _run_main(["--restore-backup"], monkeypatch)

        mock_download.assert_not_called()
        mock_upload.assert_called_once()

    def test_restore_backup_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        apply_config: Config,
    ) -> None:
        """--restore-backup with no backup file present exits with code 1."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            _run_main(["--restore-backup"], monkeypatch)

        assert exc_info.value.code == 1

    def test_upload_failure(
        self,
        tmp_path: Path,
        sample_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """When ftp_upload raises CalledProcessError, exit 1 with a friendly message."""
        import subprocess

        monkeypatch.chdir(tmp_path)

        def fake_download(remote: str, local: Path) -> None:
            shutil.copy(sample_db, local)

        with (
            patch("psvita.reorder_livearea.ftp_download", side_effect=fake_download),
            patch(
                "psvita.reorder_livearea.ftp_upload",
                side_effect=subprocess.CalledProcessError(28, "curl"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main([], monkeypatch)

        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "[ERROR] Upload failed" in out
        assert "--restore-backup" in out

    def test_download_curl_not_found(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """FileNotFoundError during download exits with code 1 and prints a message."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("psvita.reorder_livearea.ftp_download", side_effect=FileNotFoundError),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main([], monkeypatch)

        assert exc_info.value.code == 1
        assert "curl" in capsys.readouterr().out

    def test_upload_curl_not_found(
        self,
        tmp_path: Path,
        sample_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """FileNotFoundError during upload exits with code 1 and prints a message."""
        monkeypatch.chdir(tmp_path)

        def fake_download(remote: str, local: Path) -> None:
            shutil.copy(sample_db, local)

        with (
            patch("psvita.reorder_livearea.ftp_download", side_effect=fake_download),
            patch("psvita.reorder_livearea.ftp_upload", side_effect=FileNotFoundError),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main([], monkeypatch)

        assert exc_info.value.code == 1
        assert "curl" in capsys.readouterr().out
