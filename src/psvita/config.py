"""Configuration loader for psvita tools.

The configuration is read from a YAML file (default: config.yaml in the
working directory) and exposed as a hierarchy of frozen dataclasses.

Use get_config() to obtain the singleton instance. In tests, call
reset_config() and patch the module-level _instance variable to inject
custom configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class VitaConfig:
    """Network settings for the PS Vita device."""

    ip: str
    port: int


@dataclass(frozen=True)
class BadgeConfig:
    """Visual parameters for the completed-badge overlay.

    Colors follow the Pillow convention: RGB tuples with values in [0, 255].
    """

    cx: int
    cy: int
    radius: int
    fill: tuple[int, int, int]
    check_color: tuple[int, int, int]
    check_width: int


@dataclass(frozen=True)
class PathsConfig:
    """Local filesystem paths used by the tools."""

    db_local: Path
    db_backup: Path
    app_local: Path
    psp_local: Path
    originals_dir: Path


@dataclass(frozen=True)
class ReorderConfig:
    """LiveArea reorder configuration."""

    items_per_page: int
    default_title_id: str
    cfw_order: list[str]
    game_order: list[str]


@dataclass(frozen=True)
class SyncExcludesConfig:
    """Regex exclude patterns for lftp mirror operations."""

    common: list[str]
    app_extra: list[str]


@dataclass(frozen=True)
class SyncConfig:
    """Configuration for the lftp sync operations."""

    app_remote: str
    psp_remote: str
    excludes: SyncExcludesConfig
    lftp_settings: list[str]


@dataclass(frozen=True)
class Config:
    """Top-level configuration container."""

    vita: VitaConfig
    paths: PathsConfig
    badge: BadgeConfig
    psp_badge: BadgeConfig
    reorder: ReorderConfig
    sync: SyncConfig


def _parse_badge(badge_raw: Any) -> BadgeConfig:
    """Build a BadgeConfig from a raw YAML ``badge`` mapping.

    Args:
        badge_raw: Mapping with ``cx``, ``cy``, ``radius``, ``fill`` (3-list),
            ``check_color`` (3-list), and ``check_width`` keys.

    Returns:
        A populated BadgeConfig with ``fill``/``check_color`` coerced to 3-tuples.

    Raises:
        KeyError: If a required badge key is missing (caught by the caller and
            re-raised as a ValueError naming the key).
    """
    fill_raw = list(badge_raw["fill"])
    check_raw = list(badge_raw["check_color"])
    if len(fill_raw) != 3 or len(check_raw) != 3:
        raise ValueError("fill and check_color must be 3-item RGB lists")
    return BadgeConfig(
        cx=int(badge_raw["cx"]),
        cy=int(badge_raw["cy"]),
        radius=int(badge_raw["radius"]),
        fill=(fill_raw[0], fill_raw[1], fill_raw[2]),
        check_color=(check_raw[0], check_raw[1], check_raw[2]),
        check_width=int(badge_raw["check_width"]),
    )


def _load(path: Path) -> Config:
    """Load configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A fully-populated Config instance.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If a required configuration key is missing from the YAML file.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    try:
        vita_raw = raw["vita"]
        paths_raw = raw["paths"]
        badge_raw = raw["badge"]
        psp_badge_raw = raw.get("psp_badge", badge_raw)
        reorder_raw = raw["reorder"]
        sync_raw = raw["sync"]
        excludes_raw = sync_raw["excludes"]

        badge = _parse_badge(badge_raw)
        psp_badge = _parse_badge(psp_badge_raw)
    except KeyError as exc:
        raise ValueError(f"Missing required configuration key: {exc}") from exc

    return Config(
        vita=VitaConfig(
            ip=str(vita_raw["ip"]),
            port=int(vita_raw["port"]),
        ),
        paths=PathsConfig(
            db_local=Path(paths_raw["db_local"]),
            db_backup=Path(paths_raw["db_backup"]),
            app_local=Path(paths_raw["app_local"]),
            psp_local=Path(paths_raw["psp_local"]),
            originals_dir=Path(paths_raw["originals_dir"]),
        ),
        badge=badge,
        psp_badge=psp_badge,
        reorder=ReorderConfig(
            items_per_page=int(reorder_raw["items_per_page"]),
            default_title_id=str(reorder_raw["default_title_id"]),
            cfw_order=list(reorder_raw["cfw_order"]),
            game_order=list(reorder_raw["game_order"]),
        ),
        sync=SyncConfig(
            app_remote=str(sync_raw["app_remote"]),
            psp_remote=str(sync_raw["psp_remote"]),
            excludes=SyncExcludesConfig(
                common=list(excludes_raw["common"]),
                app_extra=list(excludes_raw["app_extra"]),
            ),
            lftp_settings=list(sync_raw["lftp_settings"]),
        ),
    )


_instance: Config | None = None


def get_config(path: Path = Path("config.yaml")) -> Config:
    """Return the global Config singleton, loading it on the first call.

    Args:
        path: Path to the YAML file. Used only on the first call; subsequent
            calls return the cached instance regardless of this argument.

    Returns:
        The loaded Config instance.

    Raises:
        FileNotFoundError: If the file does not exist on the first call.
    """
    global _instance
    if _instance is None:
        _instance = _load(path)
    return _instance


def reset_config() -> None:
    """Clear the cached Config singleton.

    Intended for use in tests only. After calling this function, the next
    call to get_config() will reload configuration from disk.
    """
    global _instance
    _instance = None
