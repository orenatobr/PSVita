"""Shared fixtures for the psvita test suite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from PIL import Image

import psvita.config as config_mod
from psvita.config import (
    BadgeConfig,
    Config,
    PathsConfig,
    ReorderConfig,
    SyncConfig,
    SyncExcludesConfig,
    VitaConfig,
)

# ---------------------------------------------------------------------------
# Image fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_icon() -> Image.Image:
    """Return a solid-colour 128x128 RGB image usable as a fake icon."""
    return Image.new("RGB", (128, 128), color=(100, 149, 237))


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vita_config() -> Config:
    """Return a fully-populated Config instance with the standard test values."""
    return Config(
        vita=VitaConfig(ip="192.168.1.196", port=1337),
        paths=PathsConfig(
            db_local=Path("app.db"),
            db_backup=Path("app.db.bak"),
            app_local=Path("app"),
            psp_local=Path("pspemu") / "ISO",
            originals_dir=Path("originals"),
        ),
        badge=BadgeConfig(
            cx=64,
            cy=64,
            radius=13,
            fill=(34, 197, 94),
            check_color=(255, 255, 255),
            check_width=2,
        ),
        reorder=ReorderConfig(
            items_per_page=10,
            default_title_id="PCSA00029",
            cfw_order=list(_CFW_TITLES),
            game_order=list(_GAME_TITLES),
        ),
        sync=SyncConfig(
            app_remote="/ux0:/app/",
            psp_remote="/ux0:/pspemu/ISO/",
            excludes=SyncExcludesConfig(
                common=[r"^\._", r"^\.DS_Store$", r"^\.AppleDouble$"],
                app_extra=[r"^\.Spotlight-V100$", r"^\.fseventsd$", r"^games\.md$"],
            ),
            lftp_settings=[
                "set ftp:use-feat false",
                "set ftp:use-mlsd false",
                "set ftp:passive-mode true",
                "set ftp:use-size false",
                "set ftp:use-mdtm false",
                "set ftp:use-site-utime false",
                "set ftp:list-empty-ok true",
                "set mirror:parallel-transfer-count 1",
            ],
        ),
    )


@pytest.fixture()
def apply_config(monkeypatch: pytest.MonkeyPatch, vita_config: Config) -> Config:
    """Inject vita_config as the module-level singleton and return it.

    Use this fixture in tests that call any psvita function that internally
    calls get_config(). The monkeypatch ensures the singleton is restored to
    its previous state after the test completes.
    """
    monkeypatch.setattr(config_mod, "_instance", vita_config)
    return vita_config


# ---------------------------------------------------------------------------
# SQLite database fixture
# ---------------------------------------------------------------------------

# Minimal set of titles used across tests.
_SYSTEM_TITLES = ["NPXS10000", "NPXS10001", "NPXS10002"]
_CFW_TITLES = [
    "VITASHELL",
    "PSPEMUCFW",
    "AUTOPLUG2",
    "SKGD3PL0Y",
    "SKGTLSE12",
    "SKGB4TF1X",
    "SKGYAMT01",
]
_GAME_TITLES = [
    "PCSE00700",
    "PCSE00790",
    "PCSE00480",
    "PCSE01232",
    "PCSE01171",
    "PCSE00293",
    "PCSE00283",
    "PCSA00126",
    "PCSA00011",
    "PCSE00547",
    "PCSE00033",
    "PCSA00080",
    "PCSE00088",
    "PCSA00107",
    "PCSE00786",
    "PCSE00896",
    "PCSA00017",
    "PCSA00133",
    "PCSE00052",
    "PCSE00277",
    "PCSA00063",
    "PCSE00449",
    "PCSE01337",
    "PCSE00950",
    "PCSE00640",
    "PCSA00096",
    "PCSA00097",
    "PCSA00098",
    "PCSA00068",
    "PCSA00152",
    "PCSA00144",
    "PCSE00317",
    "PCSA00029",
    "PCSE00880",
    "PCSE01033",
    "PCSE00245",
    "PCSE01103",
]


def _build_db(
    path: Path,
    *,
    include_extra_cols: bool = True,
    include_title_col: bool = True,
    include_system_pages: bool = True,
    system_page_has_template_row: bool = True,
    include_full_livearea: bool = False,
) -> Path:
    """Create a minimal app.db replica at *path* and return it.

    Args:
        path: Destination file path for the SQLite database.
        include_extra_cols: When True, tbl_appinfo_page has bgColor/reserved01/themeFile
            columns (mimics the real Vita schema).
        include_title_col: When True, tbl_appinfo_icon has a ``title`` column so the
            script can use it for display output.
        include_system_pages: When False, the DB contains no NPXS* entries, triggering
            the safety-abort branch in reorder().
        system_page_has_template_row: When False, tbl_appinfo_page has no rows for the
            system pageId, triggering the "fetchone returns None" branch.
        include_full_livearea: When True, all 44 user titles get a pre-existing livearea
            row with background_image and gate_startupImage set, exercising the
            "all rows known, no INSERT" path in reorder().

    Returns:
        The path passed in, for convenience.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()

    # --- tbl_appinfo_icon ------------------------------------------------
    extra_icon = ", title TEXT" if include_title_col else ""
    cur.execute(
        f"CREATE TABLE tbl_appinfo_icon ("
        f"  pageId INTEGER, pos INTEGER, titleId TEXT,"
        f"  type INTEGER, icon0Type INTEGER, iconPath TEXT,"
        f"  parentalLockLv INTEGER, status INTEGER{extra_icon},"
        f"  PRIMARY KEY (pageId, pos)"
        f")"
    )

    # --- tbl_appinfo_page ------------------------------------------------
    extra_page = ", themeFile TEXT, bgColor INTEGER, reserved01 INTEGER" if include_extra_cols else ""
    cur.execute(f"CREATE TABLE tbl_appinfo_page (  pageId INTEGER PRIMARY KEY, pageNo INTEGER NOT NULL{extra_page})")

    # --- tbl_livearea -----------------------------------------------------
    cur.execute(
        "CREATE TABLE tbl_livearea ("
        "  titleId TEXT PRIMARY KEY, org_path TEXT, style TEXT,"
        "  formatVer TEXT, background_image TEXT, gate_startupImage TEXT,"
        "  titleColor INTEGER"
        ")"
    )

    # --- Insert system pages and icons -----------------------------------
    if include_system_pages:
        sys_page_id = 2
        # Insert system page row only when the template branch should succeed.
        if system_page_has_template_row:
            if include_extra_cols:
                cur.execute(
                    "INSERT INTO tbl_appinfo_page (pageId, pageNo, themeFile, bgColor, reserved01)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (sys_page_id, 0, None, 0, 33554431),
                )
            else:
                cur.execute(
                    "INSERT INTO tbl_appinfo_page (pageId, pageNo) VALUES (?, ?)",
                    (sys_page_id, 0),
                )
        # System icons always present (they're what detect the system page).
        for pos, tid in enumerate(_SYSTEM_TITLES):
            title_val = (tid,) if include_title_col else ()
            cur.execute(
                f"INSERT INTO tbl_appinfo_icon VALUES (?, ?, ?, ?, ?, ?, ?, ?{', ?' if include_title_col else ''})",
                (sys_page_id, pos, tid, 0, 0, "", 0, 0) + title_val,
            )

    # --- Insert user pages and icons (CFW + games) -----------------------
    user_page_id = 10
    user_page_no = 2
    for pos, tid in enumerate(_CFW_TITLES + _GAME_TITLES):
        title_val = (tid,) if include_title_col else ()
        cur.execute(
            f"INSERT INTO tbl_appinfo_icon VALUES (?, ?, ?, ?, ?, ?, ?, ?{', ?' if include_title_col else ''})",
            (user_page_id, pos, tid, 1, 0, "", 0, 0) + title_val,
        )
    cur.execute(
        "INSERT INTO tbl_appinfo_page (pageId, pageNo) VALUES (?, ?)",
        (user_page_id, user_page_no),
    )

    # Pre-populate tbl_livearea for some titles (others are absent on purpose
    # to exercise the INSERT path for missing rows).
    for tid in _CFW_TITLES[:3]:
        cur.execute(
            "INSERT INTO tbl_livearea"
            " (titleId, org_path, style, formatVer, background_image, gate_startupImage, titleColor)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tid, f"ur0:appmeta/{tid}/livearea/contents", "a1", "01.00", "bg.png", "startup.png", 0x000000),
        )
    if include_full_livearea:
        for tid in _CFW_TITLES[3:] + _GAME_TITLES:
            cur.execute(
                "INSERT INTO tbl_livearea"
                " (titleId, org_path, style, formatVer, background_image, gate_startupImage, titleColor)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tid, f"ur0:appmeta/{tid}/livearea/contents", "a1", "01.00", "bg.png", "startup.png", 0x000000),
            )

    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def sample_db(tmp_path: Path) -> Path:
    """Full-schema app.db replica with system pages, CFW, and 37 game entries."""
    return _build_db(tmp_path / "fixtures" / "app.db")


@pytest.fixture()
def sample_db_no_extra_cols(tmp_path: Path) -> Path:
    """app.db without extra page columns (bgColor/reserved01/themeFile)."""
    return _build_db(tmp_path / "fixtures" / "app.db", include_extra_cols=False)


@pytest.fixture()
def sample_db_no_title_col(tmp_path: Path) -> Path:
    """app.db without the title column in tbl_appinfo_icon."""
    return _build_db(tmp_path / "fixtures" / "app.db", include_title_col=False)


@pytest.fixture()
def sample_db_no_system_pages(tmp_path: Path) -> Path:
    """app.db with no NPXS* entries -- triggers the safety-abort path."""
    return _build_db(tmp_path / "fixtures" / "app.db", include_system_pages=False)


@pytest.fixture()
def sample_db_no_template_row(tmp_path: Path) -> Path:
    """app.db with extra cols but no system page row -- fetchone returns None."""
    return _build_db(
        tmp_path / "fixtures" / "app.db",
        include_extra_cols=True,
        system_page_has_template_row=False,
    )


@pytest.fixture()
def sample_db_full_livearea(tmp_path: Path) -> Path:
    """app.db where all 44 user titles have pre-existing livearea rows with artwork fields set."""
    return _build_db(tmp_path / "fixtures" / "app.db", include_full_livearea=True)


@pytest.fixture()
def sample_db_stale_livearea(tmp_path: Path) -> Path:
    """app.db with 3 good livearea rows plus 4 stale NULL-artwork rows from a previous buggy run."""
    db = _build_db(tmp_path / "fixtures" / "app.db")
    conn = sqlite3.connect(str(db))
    # Simulate the broken INSERT: minimal rows with no artwork fields.
    for tid in _CFW_TITLES[3:]:
        conn.execute(
            "INSERT INTO tbl_livearea (titleId, org_path, style, formatVer, titleColor) VALUES (?, ?, ?, ?, ?)",
            (tid, f"ur0:appmeta/{tid}/livearea/contents", "a1", "01.00", 0xFFFFFF),
        )
    conn.commit()
    conn.close()
    return db
