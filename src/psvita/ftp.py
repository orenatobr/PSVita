"""Low-level FTP helpers backed by curl.

All functions delegate to the ``curl`` binary.  When curl is not installed,
``subprocess.run`` raises ``FileNotFoundError``; callers are responsible for
catching it and producing a user-friendly message.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def download(remote: str, local: Path) -> bool:
    """Download a remote FTP file to a local path.

    Does **not** raise on curl failure — the caller decides how to handle a
    failed download (e.g., fall back to a placeholder or abort).

    Args:
        remote: Full FTP URL of the source file, e.g.
            ``ftp://192.168.1.196:1337/ur0:/appmeta/PCSA00029/icon0.png``.
        local: Destination path where the file will be written.

    Returns:
        ``True`` if curl exited with code 0, ``False`` otherwise.

    Raises:
        FileNotFoundError: If curl is not installed or not found in PATH.
    """
    result = subprocess.run(["curl", "-s", "--ftp-pasv", "-o", str(local), remote])
    return result.returncode == 0


def upload(local: Path, remote: str) -> None:
    """Upload a local file to a remote FTP path.

    Args:
        local: Path to the local file to upload.
        remote: Full FTP URL of the destination, e.g.
            ``ftp://192.168.1.196:1337/ur0:/appmeta/PCSA00029/icon0.png``.

    Raises:
        subprocess.CalledProcessError: If curl exits with a non-zero status.
        FileNotFoundError: If curl is not installed or not found in PATH.
    """
    subprocess.run(["curl", "-s", "--ftp-pasv", "-T", str(local), remote], check=True)
