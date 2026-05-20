#!/usr/bin/env python3
"""Sync PS Vita game data from the local backup to the device via FTP using lftp.

Commands:
  sync-app           # mirror ./app/ -> /ux0:/app/ on the device
  sync-app --dry-run # preview without transferring anything
  sync-psp           # mirror ./pspemu/ISO/ -> /ux0:/pspemu/ISO/
  sync-psp --dry-run # preview without transferring anything

Requirements: lftp in PATH, VitaShell running on device.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from psvita.config import get_config


def _lftp_script(
    local: Path,
    remote: str,
    *,
    excludes: list[str],
    delete: bool,
    dry_run: bool,
) -> str:
    """Build the lftp script string for a mirror operation.

    Args:
        local: Local directory to mirror from (will be resolved to an absolute path).
        remote: Remote path on the device, e.g. ``/ux0:/app/``.
        excludes: List of regex patterns passed as ``--exclude`` flags to lftp.
        delete: When True, add ``--delete`` so the remote mirrors the local exactly.
        dry_run: When True, add ``--dry-run`` to preview without transferring.

    Returns:
        A semicolon-separated lftp command string ready for ``lftp <url> -e "..."``.
    """
    parts = list(get_config().sync.lftp_settings)

    mirror_flags = ["mirror", "--reverse", "--no-perms"]
    if delete:
        mirror_flags.append("--delete")
    if dry_run:
        mirror_flags.append("--dry-run")
    for pattern in excludes:
        mirror_flags += ["--exclude", pattern]
    mirror_flags += [str(local.resolve()), remote]

    parts.append(" ".join(mirror_flags))
    parts.append("quit")
    return "; ".join(parts)


def _run_lftp(vita_ftp_url: str, script: str) -> None:
    """Execute an lftp script against the device.

    Args:
        vita_ftp_url: Base FTP URL of the device, e.g. ``ftp://192.168.1.196:1337``.
        script: Semicolon-separated lftp commands to pass via ``-e``.

    Raises:
        subprocess.CalledProcessError: If lftp exits with a non-zero status.
    """
    subprocess.run(["lftp", vita_ftp_url, "-e", script], check=True)


def sync_app(local: Path, vita_ftp_url: str, *, dry_run: bool = False) -> None:
    """Mirror the local app directory to the device.

    Does **not** delete remote files that are absent locally, to avoid
    accidentally wiping game data that was installed directly on the device.

    Args:
        local: Local app directory to sync from (typically ``./app``).
        vita_ftp_url: Base FTP URL of the device.
        dry_run: When True, preview the sync without transferring.
    """
    cfg = get_config().sync
    excludes = cfg.excludes.common + cfg.excludes.app_extra
    script = _lftp_script(local, cfg.app_remote, excludes=excludes, delete=False, dry_run=dry_run)
    _run_lftp(vita_ftp_url, script)


def sync_psp(local: Path, vita_ftp_url: str, *, dry_run: bool = False) -> None:
    """Mirror the local PSP ISO directory to the device.

    Uses ``--delete`` so the remote ISO folder stays in sync with the local one.

    Args:
        local: Local PSP ISO directory to sync from (typically ``./pspemu/ISO``).
        vita_ftp_url: Base FTP URL of the device.
        dry_run: When True, preview the sync without transferring.
    """
    cfg = get_config().sync
    script = _lftp_script(local, cfg.psp_remote, excludes=cfg.excludes.common, delete=True, dry_run=dry_run)
    _run_lftp(vita_ftp_url, script)


def _make_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=description,
        epilog="After syncing new titles, open VitaShell > Triangle > Refresh livearea.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be transferred without making any changes.",
    )
    return parser


def main_app() -> None:
    """Entry point for sync-app."""
    args = _make_parser("Mirror ./app/ to /ux0:/app/ on the PS Vita.").parse_args()
    cfg = get_config()
    vita_ftp_url = f"ftp://{cfg.vita.ip}:{cfg.vita.port}"
    app_local = cfg.paths.app_local
    label = "[DRY RUN] " if args.dry_run else ""
    print(f"{label}Syncing {app_local} -> {vita_ftp_url}{cfg.sync.app_remote} ...")
    try:
        sync_app(app_local, vita_ftp_url, dry_run=args.dry_run)
    except FileNotFoundError:
        print("[ERROR] lftp is not installed or not found in PATH.")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERROR] Sync failed (lftp exit {exc.returncode}).")
        print("  Check that VitaShell is open and the device is reachable.")
        sys.exit(1)
    print("Sync complete.")
    if not args.dry_run:
        print("Open VitaShell > Triangle > Refresh livearea for new titles to appear.")


def main_psp() -> None:
    """Entry point for sync-psp."""
    args = _make_parser("Mirror ./pspemu/ISO/ to /ux0:/pspemu/ISO/ on the PS Vita.").parse_args()
    cfg = get_config()
    vita_ftp_url = f"ftp://{cfg.vita.ip}:{cfg.vita.port}"
    psp_local = cfg.paths.psp_local
    label = "[DRY RUN] " if args.dry_run else ""
    print(f"{label}Syncing {psp_local} -> {vita_ftp_url}{cfg.sync.psp_remote} ...")
    try:
        sync_psp(psp_local, vita_ftp_url, dry_run=args.dry_run)
    except FileNotFoundError:
        print("[ERROR] lftp is not installed or not found in PATH.")
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        print(f"\n[ERROR] Sync failed (lftp exit {exc.returncode}).")
        print("  Check that VitaShell is open and the device is reachable.")
        sys.exit(1)
    print("Sync complete.")
    if not args.dry_run:
        print("Open VitaShell > Triangle > Refresh livearea for new titles to appear.")


if __name__ == "__main__":  # pragma: no cover
    main_app()
