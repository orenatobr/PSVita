#!/usr/bin/env python3
"""Marks a PS Vita game as completed by adding a checkmark badge to its LiveArea thumbnail.

The badge is applied to both icon locations to ensure persistence across reboots:

  ur0:appmeta/{titleId}/icon0.png
      LiveArea cache managed by the firmware. Updated immediately via FTP so the
      badge appears as soon as VitaShell refreshes the livearea.

  ux0:app/{titleId}/sce_sys/icon0.png  (FTP + local)
      Permanent source used by SceShell to regenerate the cache on every reboot.
      Updated via FTP for immediate persistence and also written to the local
      app/{titleId}/sce_sys/icon0.png so the next sync-app keeps it in sync.

Without updating sce_sys/, the firmware overwrites the appmeta cache on reboot,
removing the badge.

Usage:
    mark-completed [TITLE_ID]           # apply overlay and upload to both locations
    mark-completed [TITLE_ID] --dry-run # save preview locally, no upload

Requirements: Pillow, curl in PATH, VitaShell running on device.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

import psvita.ftp as ftp
from psvita.config import get_config


def _remote_url(title_id: str) -> str:
    """Return the FTP URL for the LiveArea cache icon in ur0:appmeta/.

    Args:
        title_id: Game title ID, e.g. ``PCSA00029``.

    Returns:
        FTP URL pointing to ``ur0:appmeta/{title_id}/icon0.png``.
    """
    cfg = get_config().vita
    return f"ftp://{cfg.ip}:{cfg.port}/ur0:/appmeta/{title_id}/icon0.png"


def _sce_sys_url(title_id: str) -> str:
    """Return the FTP URL for the permanent icon source in ux0:app/.

    Args:
        title_id: Game title ID, e.g. ``PCSA00029``.

    Returns:
        FTP URL pointing to ``ux0:app/{title_id}/sce_sys/icon0.png``.
    """
    cfg = get_config().vita
    return f"ftp://{cfg.ip}:{cfg.port}/ux0:/app/{title_id}/sce_sys/icon0.png"


_ftp_download = ftp.download
_ftp_upload = ftp.upload


def _make_placeholder(size: int = 128) -> Image.Image:
    """Return a plain dark icon used when the original cannot be fetched."""
    img = Image.new("RGB", (size, size), color=(30, 30, 30))
    return img


def _ensure_original(title_id: str, backup: Path, remote_url: str) -> None:
    """Ensure the original unmodified icon is available at ``backup``.

    Uses the cached file if it already exists.  Otherwise, downloads from
    ``remote_url``.  If the download fails or the result is suspiciously
    small (< 100 bytes), a dark placeholder image is generated instead.

    The backup file is written exactly once.  Subsequent calls with the same
    ``backup`` path are no-ops.

    Args:
        title_id: Game title ID used in user-facing messages, e.g. ``PCSA00029``.
        backup: Destination path for the cached original icon.
        remote_url: FTP URL to download the icon from when the backup is absent.
    """
    if backup.exists():
        print(f"Using cached original {backup}")
        return

    # Download the LiveArea cache icon as the original baseline.
    # This icon is always a valid 128x128 PNG decoded by the Vita firmware.
    # Once saved, do NOT delete -- it is the permanent clean source.
    print(f"Downloading {remote_url} ...")
    ok = _ftp_download(remote_url, backup)

    if not ok or not backup.exists() or backup.stat().st_size < 100:
        # Remote icon missing (e.g., ur0:appmeta/ was cleared or never populated).
        # Generate a dark placeholder so the badge is still visible on the Vita.
        # To restore the real icon: launch the game once on the Vita, which forces
        # the firmware to regenerate ur0:appmeta/{title_id}/icon0.png, then
        # delete originals/{title_id}_icon0.png and run again.
        if backup.exists():
            backup.unlink()
        print(
            f"[WARN] Remote icon not found at {remote_url}.\n"
            "       Generating a dark placeholder. The badge will be visible but\n"
            "       the game artwork will be missing until the icon is restored.\n"
            "       To restore: launch the game once on the Vita, then delete\n"
            f"       originals/{title_id}_icon0.png and run again."
        )
        _make_placeholder().save(backup)
    else:
        print(f"Original saved to {backup}")


def apply_badge(img: Image.Image) -> Image.Image:
    """Return a copy of img with a green checkmark badge in the top-right corner.

    Args:
        img: Source icon image. Any mode is accepted; output is RGB.

    Returns:
        New RGB image with the badge composited on top of the source icon.
    """
    badge = get_config().badge
    out = img.convert("RGB")
    draw = ImageDraw.Draw(out)

    cx, cy, r = badge.cx, badge.cy, badge.radius

    # Filled green circle.
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=badge.fill)

    # White checkmark as two line segments scaled to the circle radius.
    # p1 -> p2: short descending left leg.
    # p2 -> p3: longer ascending right leg.
    p1 = (int(cx - r * 0.70), int(cy + r * 0.10))
    p2 = (int(cx - r * 0.05), int(cy + r * 0.62))
    p3 = (int(cx + r * 0.68), int(cy - r * 0.62))

    draw.line([p1, p2], fill=badge.check_color, width=badge.check_width)
    draw.line([p2, p3], fill=badge.check_color, width=badge.check_width)

    return out


def main() -> None:
    """Entry point for mark-completed CLI.

    Parses the title ID from the command line, downloads (or reuses) the
    original icon, applies the green checkmark badge, and uploads the result
    to both LiveArea cache locations on the device.  With ``--dry-run``, only
    a local preview file is written.
    """
    parser = argparse.ArgumentParser(
        description="Add a completed checkmark badge to a PS Vita LiveArea thumbnail.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "After upload, open VitaShell > Triangle > Refresh livearea. "
            "If the icon does not appear, power off the Vita completely and power back on."
        ),
    )
    cfg = get_config()
    default_title_id = cfg.reorder.default_title_id
    originals_dir = cfg.paths.originals_dir
    parser.add_argument(
        "title_id",
        nargs="?",
        default=default_title_id,
        help=f"Game title ID (default: {default_title_id})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Save the modified icon locally as a preview without uploading.",
    )
    args = parser.parse_args()

    title_id: str = args.title_id
    remote_appmeta = _remote_url(title_id)
    remote_sce_sys = _sce_sys_url(title_id)
    local_sce_sys = cfg.paths.app_local / title_id / "sce_sys" / "icon0.png"

    originals_dir.mkdir(exist_ok=True)
    backup = originals_dir / f"{title_id}_icon0.png"
    modified = Path(f"{title_id}_icon0_modified.png")

    _ensure_original(title_id, backup, remote_appmeta)

    img = Image.open(backup)
    print(f"Icon: {img.size[0]}x{img.size[1]} px, mode {img.mode}")

    result = apply_badge(img)
    result.save(modified)
    print(f"Modified icon saved to {modified}")

    if args.dry_run:
        print(
            f"\n[DRY RUN] Upload skipped. Inspect {modified} to validate the overlay,"
            f" then run without --dry-run to apply."
        )
        print(f"  Would upload to: {remote_appmeta}")
        print(f"  Would upload to: {remote_sce_sys}")
        print(f"  Would copy locally to: {local_sce_sys}")
    else:
        try:
            print(f"\nUploading to {remote_appmeta} ...")
            _ftp_upload(modified, remote_appmeta)
            print(f"Uploading to {remote_sce_sys} ...")
            _ftp_upload(modified, remote_sce_sys)
        except FileNotFoundError:
            print("[ERROR] curl is not installed or not found in PATH.")
            sys.exit(1)
        local_sce_sys.parent.mkdir(parents=True, exist_ok=True)
        result.save(local_sce_sys)
        print(f"Local source updated: {local_sce_sys}")
        print("Upload complete.")
        print("\nNext step: open VitaShell > Triangle > Refresh livearea.")
        print("If the icon does not appear, power off the Vita completely and power back on.")


if __name__ == "__main__":  # pragma: no cover
    main()
