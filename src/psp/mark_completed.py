#!/usr/bin/env python3
"""Marks a PSP (Adrenaline) game as completed by badging its in-ISO ICON0.PNG.

Games launched from Adrenaline's own menu take their icon from
``PSP_GAME/ICON0.PNG`` *inside* each ISO in ``ux0:pspemu/ISO/``.  VitaShell's FTP
server cannot overwrite a byte range in place (``STOR`` either truncates the
whole file or appends at EOF -- there is no seek-to-offset write), so the icon
inside the on-device ISO cannot be patched remotely.

Instead this tool patches the **local** copy of the ISO in place -- the badged
PNG is re-encoded to fit within the original icon's byte length and zero-padded
so the disc layout is untouched -- and then uploads the whole ISO over FTP.  The
local patch is instant; the only cost is transferring the full image (~1-1.6 GB).

There is no upload resume: a dropped Wi-Fi transfer must be restarted.

Usage:
    mark-completed-psp "ISO name or substring"   # patch local ISO + upload full ISO
    mark-completed-psp "ISO name" --dry-run       # preview PNG only, ISO untouched
    mark-completed-psp "ISO name" --local-only    # patch local ISO, skip FTP upload

Requirements: Pillow, curl in PATH (for upload), VitaShell running on device.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from urllib.parse import quote

from PIL import Image

import psp.iso9660 as iso9660
import psvita.ftp as ftp
from psvita.config import get_config
from psvita.mark_completed import apply_badge


def _resolve_iso(name: str, iso_dir: Path) -> Path:
    """Return the local ISO matching ``name`` (exact filename or unique substring).

    Args:
        name: ISO basename (``.iso`` optional) or a unique case-insensitive
            substring of one.
        iso_dir: Directory holding the local ISO mirror.

    Returns:
        The resolved ISO path.

    Exits:
        With status 1 when nothing matches or the substring is ambiguous.
    """
    stem = name[:-4] if name.lower().endswith(".iso") else name
    candidates = sorted(iso_dir.glob("*.iso"))

    for candidate in candidates:
        if candidate.name.lower() == f"{stem}.iso".lower():
            return candidate

    matches = [c for c in candidates if stem.lower() in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        print(f"[ERROR] No ISO in {iso_dir} matches '{name}'.")
        sys.exit(1)
    listing = "\n".join(f"  - {m.name}" for m in matches)
    print(f"[ERROR] '{name}' is ambiguous; matches:\n{listing}\nBe more specific.")
    sys.exit(1)


def _ensure_original(backup: Path, iso: Path, offset: int, length: int) -> None:
    """Ensure the pristine icon region is cached at ``backup`` (written once).

    Args:
        backup: Destination path for the cached original icon bytes.
        iso: Source ISO to read the original icon region from.
        offset: Absolute byte offset of the icon region within the ISO.
        length: Length of the icon region in bytes.
    """
    if backup.exists():
        print(f"Using cached original {backup}")
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(iso9660.read_range(iso, offset, length))
    print(f"Original icon saved to {backup}")


def _png_bytes(img: Image.Image) -> bytes:
    """Encode ``img`` as optimized PNG bytes."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _encode_fitted(img: Image.Image, max_len: int) -> bytes | None:
    """Encode ``img`` as PNG bytes no larger than ``max_len``.

    Tries full colour first, then progressively coarser adaptive palettes so the
    badged icon fits within the original icon's byte region without overrunning
    the following file in the ISO.

    Args:
        img: The badged icon image.
        max_len: Maximum allowed byte length (the original icon's data length).

    Returns:
        PNG bytes that fit, or None if even the coarsest encoding is too large.
    """
    candidates = [img]
    for colors in (256, 64, 16):
        candidates.append(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors))
    for candidate in candidates:
        data = _png_bytes(candidate)
        if len(data) <= max_len:
            return data
    return None


def _remote_url(iso_name: str) -> str:
    """Return the FTP URL for the ISO under the device's pspemu ISO directory.

    Args:
        iso_name: ISO filename (percent-encoded for the URL path).

    Returns:
        FTP URL pointing to ``{psp_remote}{iso_name}`` on the device.
    """
    cfg = get_config()
    base = f"ftp://{cfg.vita.ip}:{cfg.vita.port}{cfg.sync.psp_remote}"
    return base + quote(iso_name, safe="")


def main() -> None:
    """Entry point for the mark-completed-psp CLI.

    Resolves the target ISO, badges its ``PSP_GAME/ICON0.PNG`` in place in the
    local copy, and (unless ``--dry-run``/``--local-only``) uploads the whole ISO.
    """
    parser = argparse.ArgumentParser(
        description="Add a completed checkmark badge to a PSP game's in-ISO icon.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The whole ISO is re-uploaded because the FTP server cannot patch a file "
            "in place. After upload, re-enter Adrenaline's game menu (or reboot "
            "Adrenaline) so the PSP XMB re-reads the icon."
        ),
    )
    parser.add_argument("iso", help="ISO filename (.iso optional) or a unique substring")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Save a preview PNG only; do not modify the ISO or upload.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Patch the local ISO but skip the FTP upload (push later via sync-psp).",
    )
    args = parser.parse_args()

    cfg = get_config()
    iso = _resolve_iso(args.iso, cfg.paths.psp_local)
    print(f"Target ISO: {iso}")

    offset, length = iso9660.find_icon0(iso)
    print(f"ICON0.PNG at byte offset {offset}, region length {length} bytes")

    cfg.paths.originals_dir.mkdir(parents=True, exist_ok=True)
    backup = cfg.paths.originals_dir / f"{iso.stem}_ICON0.png"
    _ensure_original(backup, iso, offset, length)

    original = Image.open(backup)
    print(f"Icon: {original.size[0]}x{original.size[1]} px, mode {original.mode}")
    badged = apply_badge(original, cfg.psp_badge)

    preview = Path(f"{iso.stem}_ICON0_modified.png")
    badged.save(preview)
    print(f"Preview saved to {preview}")

    encoded = _encode_fitted(badged, length)
    if encoded is None:
        print(f"[ERROR] Badged icon cannot be compressed to fit {length} bytes; ISO left untouched.")
        sys.exit(1)

    if args.dry_run:
        print("\n[DRY RUN] ISO not modified, no upload.")
        print(f"  Would patch {iso.name} at offset {offset} ({len(encoded)} bytes, padded to {length})")
        print(f"  Would upload to: {_remote_url(iso.name)}")
        return

    padded = encoded + b"\x00" * (length - len(encoded))
    iso9660.write_range_in_place(iso, offset, padded)
    print(f"Patched {iso.name} in place ({len(encoded)} bytes of PNG, padded to {length}).")

    if args.local_only:
        print("\n[LOCAL ONLY] Upload skipped. Run sync-psp to push the ISO to the Vita.")
        return

    remote = _remote_url(iso.name)
    try:
        print(f"\nUploading full ISO to {remote} ...")
        print("This transfers the entire image and can take many minutes over Wi-Fi.")
        ftp.upload(iso, remote)
    except FileNotFoundError:
        print("[ERROR] curl is not installed or not found in PATH.")
        sys.exit(1)
    print("Upload complete.")
    print("\nNext step: on the Vita open Adrenaline and re-enter the game menu.")
    print("If the old icon persists, reboot Adrenaline so the PSP XMB re-reads the ISO.")


if __name__ == "__main__":  # pragma: no cover
    main()
