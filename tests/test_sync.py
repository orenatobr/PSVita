"""Tests for psvita.sync."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import psvita.sync as s
from psvita.config import Config

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_main_app(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["sync-app"] + args)
    s.main_app()


def _run_main_psp(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["sync-psp"] + args)
    s.main_psp()


# ---------------------------------------------------------------------------
# _lftp_script
# ---------------------------------------------------------------------------


class TestLftpScript:
    def test_app_script_no_delete_no_dry_run(self, tmp_path: Path, apply_config: Config) -> None:
        """App script: --reverse --no-perms, no --delete, all 6 app excludes, absolute path."""
        local = tmp_path / "app"
        app_excludes = apply_config.sync.excludes.common + apply_config.sync.excludes.app_extra
        script = s._lftp_script(local, apply_config.sync.app_remote, excludes=app_excludes, delete=False, dry_run=False)

        assert "--reverse" in script
        assert "--no-perms" in script
        assert "--delete" not in script
        assert "--dry-run" not in script
        # All 6 app-specific exclude patterns are present.
        for pattern in app_excludes:
            assert pattern in script
        # Path is resolved to absolute.
        assert str(local.resolve()) in script
        # Remote path is present, without the trailing slash.
        assert apply_config.sync.app_remote.rstrip("/") in script
        # All 8 settings lines are present.
        for setting in apply_config.sync.lftp_settings:
            assert setting in script
        # Script ends with quit.
        assert script.endswith("quit")

    def test_app_script_dry_run(self, tmp_path: Path, apply_config: Config) -> None:
        """--dry-run flag is added to the mirror command when requested."""
        local = tmp_path / "app"
        app_excludes = apply_config.sync.excludes.common + apply_config.sync.excludes.app_extra
        script = s._lftp_script(local, apply_config.sync.app_remote, excludes=app_excludes, delete=False, dry_run=True)

        assert "--dry-run" in script

    def test_remote_trailing_slash_is_stripped(self, tmp_path: Path) -> None:
        """A trailing slash would make lftp mirror into <remote>/<basename>, e.g. ISO/ISO."""
        script = s._lftp_script(tmp_path / "ISO", "/ux0:/pspemu/ISO/", excludes=[], delete=True, dry_run=False)

        mirror = next(part for part in script.split("; ") if part.startswith("mirror"))
        assert mirror.endswith(" /ux0:/pspemu/ISO")

    def test_psp_script_with_delete(self, tmp_path: Path, apply_config: Config) -> None:
        """PSP script: --delete present, only 3 common excludes (no app-only patterns)."""
        local = tmp_path / "ISO"
        psp_excludes = apply_config.sync.excludes.common
        script = s._lftp_script(local, apply_config.sync.psp_remote, excludes=psp_excludes, delete=True, dry_run=False)

        assert "--delete" in script
        for pattern in psp_excludes:
            assert pattern in script
        # App-only excludes are absent.
        app_only = set(apply_config.sync.excludes.app_extra)
        for pattern in app_only:
            assert pattern not in script


# ---------------------------------------------------------------------------
# _run_lftp
# ---------------------------------------------------------------------------


class TestRunLftp:
    def test_calls_subprocess(self) -> None:
        """_run_lftp delegates to subprocess.run with the correct arguments."""
        with patch("subprocess.run") as mock_run:
            s._run_lftp("ftp://host:1337", "set x; quit")

        mock_run.assert_called_once_with(
            ["lftp", "ftp://host:1337", "-e", "set x; quit"],
            check=True,
        )


# ---------------------------------------------------------------------------
# sync_app / sync_psp
# ---------------------------------------------------------------------------


class TestSyncFunctions:
    def test_sync_app_delegates(self, tmp_path: Path, apply_config: Config) -> None:
        """sync_app builds a script and passes it to _run_lftp."""
        with patch("psvita.sync._run_lftp") as mock_run:
            s.sync_app(tmp_path / "app", "ftp://host:1337")

        mock_run.assert_called_once()

    def test_sync_psp_delegates(self, tmp_path: Path, apply_config: Config) -> None:
        """sync_psp builds a script and passes it to _run_lftp."""
        with patch("psvita.sync._run_lftp") as mock_run:
            s.sync_psp(tmp_path / "ISO", "ftp://host:1337")

        mock_run.assert_called_once()

    def test_sync_app_dry_run_propagates(self, tmp_path: Path, apply_config: Config) -> None:
        """dry_run=True is reflected in the script passed to _run_lftp."""
        with patch("psvita.sync._run_lftp") as mock_run:
            s.sync_app(tmp_path / "app", "ftp://host:1337", dry_run=True)

        _url, script = mock_run.call_args.args
        assert "--dry-run" in script

    def test_sync_psp_dry_run_propagates(self, tmp_path: Path, apply_config: Config) -> None:
        """dry_run=True is reflected in the script passed to _run_lftp."""
        with patch("psvita.sync._run_lftp") as mock_run:
            s.sync_psp(tmp_path / "ISO", "ftp://host:1337", dry_run=True)

        _url, script = mock_run.call_args.args
        assert "--dry-run" in script


# ---------------------------------------------------------------------------
# main_app / main_psp
# ---------------------------------------------------------------------------


class TestFtpReady:
    """ftp_ready: fail fast when VitaShell accepts TCP but never sends its banner."""

    @staticmethod
    def _conn(banner: bytes) -> MagicMock:
        sock = MagicMock()
        sock.recv.return_value = banner
        conn = MagicMock()
        conn.__enter__.return_value = sock
        return conn

    def test_banner_220_is_ready(self) -> None:
        with patch("psvita.sync.socket.create_connection", return_value=self._conn(b"220 FTPVita ready\r\n")):
            assert s.ftp_ready("127.0.0.1", 1337) is True

    def test_connection_refused_is_not_ready(self) -> None:
        with patch("psvita.sync.socket.create_connection", side_effect=ConnectionRefusedError):
            assert s.ftp_ready("127.0.0.1", 1337) is False

    def test_no_banner_timeout_is_not_ready(self) -> None:
        """TCP accepted but no banner: the wedged-server symptom."""
        sock = MagicMock()
        sock.recv.side_effect = socket.timeout
        conn = MagicMock()
        conn.__enter__.return_value = sock
        with patch("psvita.sync.socket.create_connection", return_value=conn):
            assert s.ftp_ready("127.0.0.1", 1337) is False

    def test_unexpected_banner_is_not_ready(self) -> None:
        with patch("psvita.sync.socket.create_connection", return_value=self._conn(b"421 busy\r\n")):
            assert s.ftp_ready("127.0.0.1", 1337) is False

    def test_empty_banner_is_not_ready(self) -> None:
        with patch("psvita.sync.socket.create_connection", return_value=self._conn(b"")):
            assert s.ftp_ready("127.0.0.1", 1337) is False


class TestMainPreflight:
    """main_app/main_psp stop before lftp when the FTP server does not answer."""

    @pytest.mark.parametrize("entry", ["main_app", "main_psp"])
    def test_exits_without_running_lftp(
        self,
        entry: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        monkeypatch.setattr("sys.argv", ["sync"])
        with (
            patch("psvita.sync.ftp_ready", return_value=False),
            patch("psvita.sync._run_lftp") as mock_run,
            pytest.raises(SystemExit) as exc,
        ):
            getattr(s, entry)()
        assert exc.value.code == 1
        mock_run.assert_not_called()
        assert "VitaShell" in capsys.readouterr().out


class TestMain:
    @pytest.fixture(autouse=True)
    def _vita_ftp_up(self) -> Iterator[None]:
        """Pretend the Vita FTP server answers so main() reaches lftp."""
        with patch("psvita.sync.ftp_ready", return_value=True):
            yield

    def test_main_app_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """Normal run: _run_lftp called once, no --dry-run in script."""
        with patch("psvita.sync._run_lftp") as mock_run:
            _run_main_app([], monkeypatch)

        mock_run.assert_called_once()
        _url, script = mock_run.call_args.args
        assert "--dry-run" not in script
        assert "Sync complete" in capsys.readouterr().out

    def test_main_app_dry_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """--dry-run: _run_lftp called once with --dry-run in script."""
        with patch("psvita.sync._run_lftp") as mock_run:
            _run_main_app(["--dry-run"], monkeypatch)

        _url, script = mock_run.call_args.args
        assert "--dry-run" in script
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        # Refresh note is suppressed in dry-run mode.
        assert "Refresh livearea" not in out

    def test_main_psp_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """Normal run: _run_lftp called once."""
        with patch("psvita.sync._run_lftp") as mock_run:
            _run_main_psp([], monkeypatch)

        mock_run.assert_called_once()
        assert "Sync complete" in capsys.readouterr().out

    def test_main_psp_dry_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """--dry-run: _run_lftp called once with --dry-run in script."""
        with patch("psvita.sync._run_lftp") as mock_run:
            _run_main_psp(["--dry-run"], monkeypatch)

        _url, script = mock_run.call_args.args
        assert "--dry-run" in script
        out = capsys.readouterr().out
        assert "[DRY RUN]" in out
        assert "Refresh livearea" not in out

    def test_main_app_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """lftp failure in sync-app exits with code 1 and a friendly message."""
        with (
            patch(
                "psvita.sync._run_lftp",
                side_effect=subprocess.CalledProcessError(7, "lftp"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main_app([], monkeypatch)

        assert exc_info.value.code == 1
        assert "[ERROR]" in capsys.readouterr().out

    def test_main_psp_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """lftp failure in sync-psp exits with code 1 and a friendly message."""
        with (
            patch(
                "psvita.sync._run_lftp",
                side_effect=subprocess.CalledProcessError(7, "lftp"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main_psp([], monkeypatch)

        assert exc_info.value.code == 1
        assert "[ERROR]" in capsys.readouterr().out

    def test_main_app_lftp_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """Missing lftp binary in sync-app exits with code 1."""
        with (
            patch("psvita.sync._run_lftp", side_effect=FileNotFoundError),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main_app([], monkeypatch)

        assert exc_info.value.code == 1
        assert "lftp" in capsys.readouterr().out

    def test_main_psp_lftp_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        apply_config: Config,
    ) -> None:
        """Missing lftp binary in sync-psp exits with code 1."""
        with (
            patch("psvita.sync._run_lftp", side_effect=FileNotFoundError),
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_main_psp([], monkeypatch)

        assert exc_info.value.code == 1
        assert "lftp" in capsys.readouterr().out
