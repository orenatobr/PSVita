"""Tests for psvita.ftp."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import psvita.ftp as ftp_mod

# ---------------------------------------------------------------------------
# ftp_mod.download
# ---------------------------------------------------------------------------


def test_download_returns_true_on_success(tmp_path: Path) -> None:
    """Returns True when curl exits with code 0."""
    local = tmp_path / "icon0.png"
    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = ftp_mod.download("ftp://host/file.png", local)

    assert result is True
    mock_run.assert_called_once_with(["curl", "-s", "--ftp-pasv", "-o", str(local), "ftp://host/file.png"])


def test_download_returns_false_on_failure(tmp_path: Path) -> None:
    """Returns False when curl exits with a non-zero code."""
    local = tmp_path / "icon0.png"
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("subprocess.run", return_value=mock_result):
        result = ftp_mod.download("ftp://host/file.png", local)

    assert result is False


def test_download_propagates_file_not_found(tmp_path: Path) -> None:
    """Raises FileNotFoundError when curl is not installed."""
    local = tmp_path / "icon0.png"
    with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(FileNotFoundError):
        ftp_mod.download("ftp://host/file.png", local)


# ---------------------------------------------------------------------------
# ftp_mod.upload
# ---------------------------------------------------------------------------


def test_upload_calls_curl_with_T_flag(tmp_path: Path) -> None:
    """Calls curl with -T for upload and check=True."""
    local = tmp_path / "icon0.png"
    local.write_bytes(b"dummy")
    with patch("subprocess.run") as mock_run:
        ftp_mod.upload(local, "ftp://host/dest.png")

    mock_run.assert_called_once_with(
        ["curl", "-s", "--ftp-pasv", "-T", str(local), "ftp://host/dest.png"],
        check=True,
    )


def test_upload_raises_on_curl_failure(tmp_path: Path) -> None:
    """Propagates CalledProcessError when curl exits non-zero."""
    local = tmp_path / "icon0.png"
    local.write_bytes(b"dummy")
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["curl"]),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        ftp_mod.upload(local, "ftp://host/dest.png")


def test_upload_propagates_file_not_found(tmp_path: Path) -> None:
    """Raises FileNotFoundError when curl is not installed."""
    local = tmp_path / "icon0.png"
    local.write_bytes(b"dummy")
    with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(FileNotFoundError):
        ftp_mod.upload(local, "ftp://host/dest.png")
