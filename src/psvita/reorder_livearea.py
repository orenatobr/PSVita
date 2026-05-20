#!/usr/bin/env python3
"""Reorders the PS Vita LiveArea by manipulating app.db via FTP.

Pages after reorder:
  pageNo 0-1  System apps (NPXS*)  -- preserved, never touched
  pageNo 2    CFW apps             -- 7 items, fixed operational order
  pageNo 3-6  Games                -- 37 items, alphabetical + series order

Usage:
  reorder-livearea           # download, reorder, upload
  reorder-livearea --dry-run # preview without writing anything

Requirements: curl in PATH, VitaShell running on device.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import psvita.ftp as ftp
from psvita.config import ReorderConfig, get_config


def ftp_download(remote: str, local: Path) -> None:
    """Download a file from FTP, raising on failure.

    Args:
        remote: Full FTP URL of the source file.
        local: Destination path for the downloaded file.

    Raises:
        subprocess.CalledProcessError: If curl exits with a non-zero status.
        FileNotFoundError: If curl is not installed or not found in PATH.
    """
    if not ftp.download(remote, local):
        raise subprocess.CalledProcessError(1, ["curl", "-s", "--ftp-pasv", "-o", str(local), remote])


def ftp_upload(local: Path, remote: str) -> None:
    """Upload a local file to a remote FTP path.

    Args:
        local: Path to the local file to upload.
        remote: Full FTP URL of the destination.

    Raises:
        subprocess.CalledProcessError: If curl exits with a non-zero status.
        FileNotFoundError: If curl is not installed or not found in PATH.
    """
    ftp.upload(local, remote)


def _discover_schema(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Discover column names for the two icon/page tables at runtime.

    Reading columns via PRAGMA lets the script preserve any extra columns
    (command, reserved, future firmware additions) without hardcoding the schema.

    Args:
        conn: Open SQLite connection to app.db.

    Returns:
        A tuple ``(icon_meta_cols, page_extra_cols)`` where:
        - ``icon_meta_cols``: all tbl_appinfo_icon columns except the composite
          key (pageId, pos) and the title identifier (titleId).
        - ``page_extra_cols``: all tbl_appinfo_page columns except (pageId, pageNo).
    """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(tbl_appinfo_icon)")
    icon_all_cols = [row[1] for row in cur.fetchall()]
    icon_meta_cols = [c for c in icon_all_cols if c not in ("pageId", "pos", "titleId")]

    cur.execute("PRAGMA table_info(tbl_appinfo_page)")
    page_all_cols = [row[1] for row in cur.fetchall()]
    page_extra_cols = [c for c in page_all_cols if c not in ("pageId", "pageNo")]

    return icon_meta_cols, page_extra_cols


def _load_icon_metadata(
    conn: sqlite3.Connection,
    meta_cols: list[str],
) -> dict[str, tuple[object, ...]]:
    """Load metadata for all non-system icons from the database.

    Args:
        conn: Open SQLite connection to app.db.
        meta_cols: Column names to select, as returned by ``_discover_schema``.

    Returns:
        Mapping from titleId to a tuple of metadata values in the order of
        ``meta_cols``.
    """
    cur = conn.cursor()
    icon_select = "titleId, " + ", ".join(meta_cols)
    cur.execute(f"SELECT {icon_select} FROM tbl_appinfo_icon WHERE titleId NOT LIKE 'NPXS%'")
    return {row[0]: row[1:] for row in cur.fetchall()}


def _resolve_ordered_lists(
    cfg: ReorderConfig,
    metadata: dict[str, tuple[object, ...]],
) -> tuple[list[str], list[str]]:
    """Build the final ordered CFW and game lists, filtering missing titles.

    Titles present in the database but absent from the config lists are appended
    at the end of the game list with a ``[WARN]`` message.  Titles in the config
    lists but not installed on the device are skipped with a ``[WARN]`` message.

    Args:
        cfg: Reorder configuration with ``cfw_order`` and ``game_order``.
        metadata: Mapping of installed title IDs to their icon metadata.

    Returns:
        Tuple ``(cfw_list, game_list)`` with only installed titles, in order.
    """
    db_ids = set(metadata)
    known_ids = set(cfg.cfw_order + cfg.game_order)

    unknown_ids = sorted(db_ids - known_ids)
    missing_ids = sorted(known_ids - db_ids)

    if unknown_ids:
        print(f"[WARN] In db but not in ordered list (will be appended to games): {unknown_ids}")
    if missing_ids:
        print(f"[WARN] In ordered list but not installed (skipped): {missing_ids}")

    cfw_list = [t for t in cfg.cfw_order if t in metadata]
    game_list = [t for t in cfg.game_order if t in metadata] + unknown_ids
    return cfw_list, game_list


def _get_system_page_ids(conn: sqlite3.Connection) -> set[int]:
    """Return the set of pageIds that contain at least one system (NPXS*) icon.

    Exits the process with code 1 if no system pages are found, to prevent
    accidental deletion of all LiveArea entries.

    Args:
        conn: Open SQLite connection to app.db.

    Returns:
        Set of integer pageIds belonging to system pages.
    """
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT pageId FROM tbl_appinfo_icon WHERE titleId LIKE 'NPXS%'")
    system_page_ids: set[int] = {row[0] for row in cur.fetchall()}

    if not system_page_ids:
        print("[ERROR] No system pages detected. Aborting to prevent data loss.")
        sys.exit(1)

    return system_page_ids


def _read_page_template(
    conn: sqlite3.Connection,
    system_page_ids: set[int],
    extra_cols: list[str],
) -> tuple[object, ...]:
    """Read extra page column values from a system page to use as a template.

    System pages carry firmware-specific rendering attributes (bgColor,
    reserved01, etc.) that must be copied to new user pages so that those pages
    render with the correct background and label colours.

    Args:
        conn: Open SQLite connection to app.db.
        system_page_ids: Set of pageIds to query for the template row.
        extra_cols: Extra column names beyond (pageId, pageNo), as returned by
            ``_discover_schema``.

    Returns:
        Tuple of extra column values from the first matching system page, or a
        tuple of ``None`` values if no template row is found or if there are no
        extra columns.
    """
    if not extra_cols:
        return ()

    cur = conn.cursor()
    sys_ph = ",".join("?" * len(system_page_ids))
    extra_str = ", ".join(extra_cols)
    cur.execute(
        f"SELECT {extra_str} FROM tbl_appinfo_page WHERE pageId IN ({sys_ph}) LIMIT 1",
        list(system_page_ids),
    )
    row_raw = cur.fetchone()
    if row_raw is not None:
        row: tuple[object, ...] = tuple(row_raw)
        vals = dict(zip(extra_cols, row, strict=False))
        print(
            f"[INFO] Page template read from system page "
            f"(bgColor={vals.get('bgColor')}, reserved01={vals.get('reserved01')})."
        )
        return row

    print("[INFO] No system page found; new pages will use NULL attributes.")
    return tuple(None for _ in extra_cols)


def _delete_user_entries(conn: sqlite3.Connection, system_page_ids: set[int]) -> None:
    """Remove all non-system icons and their pages from the database.

    Only pages and icons belonging to NPXS* titles are preserved.

    Args:
        conn: Open SQLite connection to app.db.
        system_page_ids: Set of pageIds that must not be deleted.
    """
    cur = conn.cursor()
    sys_ph = ",".join("?" * len(system_page_ids))
    sys_id_list = list(system_page_ids)
    cur.execute("DELETE FROM tbl_appinfo_icon WHERE titleId NOT LIKE 'NPXS%'")
    cur.execute(
        f"DELETE FROM tbl_appinfo_page WHERE pageId NOT IN ({sys_ph})",
        sys_id_list,
    )


def _insert_group(
    conn: sqlite3.Connection,
    title_ids: list[str],
    label: str,
    *,
    page_id: int,
    page_no: int,
    items_per_page: int,
    metadata: dict[str, tuple[object, ...]],
    icon_insert_sql: str,
    page_insert_sql: str,
    page_template: tuple[object, ...],
    title_idx: int | None,
) -> tuple[int, int]:
    """Insert pages and icons for a group of title IDs.

    Titles are split into pages of ``items_per_page`` entries.  Each page
    receives its own row in tbl_appinfo_page and its icons are inserted with
    sequential positions starting at 0.

    Args:
        conn: Open SQLite connection to app.db.
        title_ids: Ordered list of title IDs to insert.
        label: Human-readable group name printed as a section header.
        page_id: Starting pageId for the first new page (inclusive).
        page_no: Starting pageNo for the first new page (inclusive).
        items_per_page: Maximum number of icons per page.
        metadata: Mapping of titleId to extra icon column values.
        icon_insert_sql: Parameterised INSERT statement for tbl_appinfo_icon.
        page_insert_sql: Parameterised INSERT statement for tbl_appinfo_page.
        page_template: Extra page attribute values copied from the system page template.
        title_idx: Index of the ``title`` column in the metadata tuple, or ``None``
            if the column is absent.

    Returns:
        Tuple ``(next_page_id, next_page_no)`` after all pages have been inserted.
    """
    cur = conn.cursor()
    print(f"\n--- {label} ---")
    for chunk_start in range(0, len(title_ids), items_per_page):
        chunk = title_ids[chunk_start : chunk_start + items_per_page]
        # Extra page attributes (themeFile, bgColor, reserved*, ...) are copied from
        # the system page template.  The original backup has themeFile=NULL for every
        # user page; overriding it with synthetic values caused rendering issues.
        cur.execute(page_insert_sql, (page_id, page_no) + page_template)
        for pos, tid in enumerate(chunk):
            meta = metadata[tid]
            cur.execute(icon_insert_sql, (page_id, pos, tid) + meta)
            title_display = (meta[title_idx] if title_idx is not None else None) or tid
            print(f"  page {page_no}  pos {pos:02d}  {tid}  {title_display}")
        page_id += 1
        page_no += 1
    return page_id, page_no


def _cleanup_livearea(conn: sqlite3.Connection) -> None:
    """Remove stale NULL-artwork livearea rows and update titleColor.

    Stale rows (NULL background_image AND NULL gate_startupImage) were inserted
    by a previous buggy run.  Without those rows the firmware reads artwork
    directly from the game's livearea directory, which is the correct behaviour.

    Existing rows (with real artwork) get their ``titleColor`` set to white
    (0xFFFFFF) so label text is legible on the dark LiveArea background.

    Args:
        conn: Open SQLite connection to app.db.
    """
    cur = conn.cursor()
    # Deleting NULL rows is safe: the tgr_livearea_del_img trigger fires only when
    # background_image or gate_startupImage starts with a space character (an integer
    # file reference), not when the values are NULL or plain filename strings.
    cur.execute(
        "DELETE FROM tbl_livearea"
        " WHERE titleId NOT LIKE 'NPXS%'"
        " AND background_image IS NULL"
        " AND gate_startupImage IS NULL",
    )
    stale = cur.rowcount
    if stale:
        print(f"[INFO] tbl_livearea: {stale} stale NULL-artwork rows removed.")

    # Update the title label colour for titles that already have a livearea row.
    # Titles with no row are left untouched: inserting a minimal row with NULL
    # artwork causes a blank gate image; without a row the firmware reads artwork
    # directly from the game's livearea/contents directory.
    cur.execute(
        "UPDATE tbl_livearea SET titleColor = ? WHERE titleId NOT LIKE 'NPXS%'",
        (0xFFFFFF,),
    )
    print(f"[INFO] tbl_livearea: {cur.rowcount} existing rows updated with titleColor=0xFFFFFF.")


def reorder(db_path: Path, dry_run: bool = False) -> None:
    """Reorder LiveArea pages in a local copy of app.db.

    After calling this function the database reflects the page layout defined
    in the configuration: system pages are preserved as-is, CFW apps occupy one
    page, and games are distributed across as many pages as needed.

    Args:
        db_path: Path to the local app.db file to modify.
        dry_run: When ``True``, all changes are rolled back at the end so the
            file on disk is left unchanged.
    """
    cfg = get_config().reorder

    conn = sqlite3.connect(str(db_path))
    # The PS Vita app.db has a trigger chain on tbl_livearea that ends in a call to
    # add_rm_list, a C extension registered by the Vita shell.  Python's sqlite3
    # resolves ALL function references at prepare time, so any DELETE on tbl_livearea
    # raises OperationalError unless we register a stub first.  The stub is never
    # actually invoked for our NULL-artwork rows: the WHEN condition on
    # tgr_livearea_del_img evaluates to NULL (false) when both artwork fields are NULL.
    conn.create_function("add_rm_list", 1, lambda path: None)

    icon_meta_cols, page_extra_cols = _discover_schema(conn)
    metadata = _load_icon_metadata(conn, icon_meta_cols)
    cfw_list, game_list = _resolve_ordered_lists(cfg, metadata)
    system_page_ids = _get_system_page_ids(conn)
    page_template = _read_page_template(conn, system_page_ids, page_extra_cols)

    _delete_user_entries(conn, system_page_ids)

    # Compute starting IDs for the new pages.
    base_page_id: int = max(system_page_ids) + 1
    cur = conn.cursor()
    cur.execute("SELECT MAX(pageNo) FROM tbl_appinfo_page")
    base_page_no: int = (cur.fetchone()[0] or -1) + 1

    # Pre-build parameterised INSERT statements from the discovered column lists.
    icon_insert_cols = ["pageId", "pos", "titleId"] + icon_meta_cols
    icon_insert_sql = (
        f"INSERT INTO tbl_appinfo_icon ({', '.join(icon_insert_cols)})"
        f" VALUES ({', '.join('?' * len(icon_insert_cols))})"
    )
    page_insert_cols = ["pageId", "pageNo"] + page_extra_cols
    page_insert_sql = (
        f"INSERT INTO tbl_appinfo_page ({', '.join(page_insert_cols)})"
        f" VALUES ({', '.join('?' * len(page_insert_cols))})"
    )
    title_idx: int | None = icon_meta_cols.index("title") if "title" in icon_meta_cols else None

    page_id, page_no = _insert_group(
        conn,
        cfw_list,
        "CFW apps",
        page_id=base_page_id,
        page_no=base_page_no,
        items_per_page=cfg.items_per_page,
        metadata=metadata,
        icon_insert_sql=icon_insert_sql,
        page_insert_sql=page_insert_sql,
        page_template=page_template,
        title_idx=title_idx,
    )
    _insert_group(
        conn,
        game_list,
        "Games",
        page_id=page_id,
        page_no=page_no,
        items_per_page=cfg.items_per_page,
        metadata=metadata,
        icon_insert_sql=icon_insert_sql,
        page_insert_sql=page_insert_sql,
        page_template=page_template,
        title_idx=title_idx,
    )

    _cleanup_livearea(conn)

    if dry_run:
        print("\n[DRY RUN] All changes rolled back. Nothing was written.")
        conn.rollback()
    else:
        conn.commit()
        print("\nDatabase updated successfully.")

    conn.close()


def main() -> None:
    """Entry point for reorder-livearea CLI.

    Downloads app.db from the device (or uses a local backup), runs the
    reorder logic, and uploads the updated database back to the device.
    With ``--dry-run``, all database changes are rolled back before upload.
    """
    cfg = get_config()
    db_remote = f"ftp://{cfg.vita.ip}:{cfg.vita.port}/ur0:/shell/db/app.db"
    db_local = cfg.paths.db_local
    db_backup = cfg.paths.db_backup

    parser = argparse.ArgumentParser(
        description="Reorder PS Vita LiveArea pages via app.db.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="After upload, power off the Vita completely and power back on (do not use standby).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the new order without writing any changes.",
    )
    parser.add_argument(
        "--restore-backup",
        action="store_true",
        help=(
            "Use the local backup (app.db.bak) as source instead of downloading from the Vita. "
            "Use this to undo a broken reorder: restores original data then applies the fixed reorder."
        ),
    )
    args = parser.parse_args()

    if args.restore_backup:
        if not db_backup.exists():
            print(f"[ERROR] Backup file {db_backup} not found. Run without --restore-backup first to create one.")
            sys.exit(1)
        shutil.copy(db_backup, db_local)
        print(f"Using local backup {db_backup} as source (skipping download).")
    else:
        print(f"Downloading {db_remote} ...")
        try:
            ftp_download(db_remote, db_local)
        except FileNotFoundError:
            print("[ERROR] curl is not installed or not found in PATH.")
            sys.exit(1)
        shutil.copy(db_local, db_backup)
        print(f"Backup saved to {db_backup}")

    reorder(db_local, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"\nUploading to {db_remote} ...")
        try:
            ftp_upload(db_local, db_remote)
        except FileNotFoundError:
            print("[ERROR] curl is not installed or not found in PATH.")
            sys.exit(1)
        except subprocess.CalledProcessError as exc:
            print(f"\n[ERROR] Upload failed (curl exit {exc.returncode}).")
            print(f"  The database was updated locally at: {db_local}")
            print("  Retry with:  reorder-livearea --restore-backup")
            sys.exit(1)
        print("Upload complete.")
        print("\nNext step: power off the Vita completely and power back on.")
        print("Changes to app.db require a full reboot; 'Refresh livearea' alone is not sufficient.")


if __name__ == "__main__":  # pragma: no cover
    main()
