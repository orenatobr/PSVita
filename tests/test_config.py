"""Tests for psvita.config."""

from __future__ import annotations

from pathlib import Path

import pytest

import psvita.config as config_mod
from psvita.config import Config, _load, get_config, reset_config

_MINIMAL_YAML = """\
vita:
  ip: "10.0.0.1"
  port: 9999

paths:
  db_local: "test.db"
  db_backup: "test.db.bak"
  app_local: "app"
  psp_local: "psp/ISO"
  originals_dir: "orig"

badge:
  cx: 32
  cy: 32
  radius: 8
  fill: [10, 20, 30]
  check_color: [255, 254, 253]
  check_width: 1

reorder:
  items_per_page: 5
  default_title_id: "PCSA00001"
  cfw_order:
    - VITASHELL
  game_order:
    - PCSE00001
    - PCSE00002

sync:
  app_remote: "/ux0:/app/"
  psp_remote: "/ux0:/psp/ISO/"
  excludes:
    common:
      - '^\\\\_'
    app_extra:
      - '^\\\\.DS_Store$'
  lftp_settings:
    - "set ftp:passive-mode true"
"""


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the singleton is cleared before and after each test."""
    monkeypatch.setattr(config_mod, "_instance", None)


# ---------------------------------------------------------------------------
# _load
# ---------------------------------------------------------------------------


def test_load_vita_fields(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    cfg = _load(yaml_file)

    assert isinstance(cfg, Config)
    assert cfg.vita.ip == "10.0.0.1"
    assert cfg.vita.port == 9999


def test_load_paths_fields(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    cfg = _load(yaml_file)

    assert cfg.paths.db_local == Path("test.db")
    assert cfg.paths.db_backup == Path("test.db.bak")
    assert cfg.paths.app_local == Path("app")
    assert cfg.paths.psp_local == Path("psp/ISO")
    assert cfg.paths.originals_dir == Path("orig")


def test_load_badge_fields(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    cfg = _load(yaml_file)

    assert cfg.badge.cx == 32
    assert cfg.badge.cy == 32
    assert cfg.badge.radius == 8
    assert cfg.badge.fill == (10, 20, 30)
    assert cfg.badge.check_color == (255, 254, 253)
    assert cfg.badge.check_width == 1


def test_load_reorder_fields(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    cfg = _load(yaml_file)

    assert cfg.reorder.items_per_page == 5
    assert cfg.reorder.default_title_id == "PCSA00001"
    assert cfg.reorder.cfw_order == ["VITASHELL"]
    assert cfg.reorder.game_order == ["PCSE00001", "PCSE00002"]


def test_load_sync_fields(tmp_path: Path) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")

    cfg = _load(yaml_file)

    assert cfg.sync.app_remote == "/ux0:/app/"
    assert cfg.sync.psp_remote == "/ux0:/psp/ISO/"
    assert cfg.sync.lftp_settings == ["set ftp:passive-mode true"]


def test_load_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _load(tmp_path / "nonexistent.yaml")


def test_load_missing_key_raises_value_error(tmp_path: Path) -> None:
    """Missing required key in YAML raises ValueError with the key name."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("vita:\n  ip: '1.2.3.4'\n  port: 1337\n", encoding="utf-8")

    with pytest.raises(ValueError, match="paths"):
        _load(yaml_file)


# ---------------------------------------------------------------------------
# get_config (singleton)
# ---------------------------------------------------------------------------


def test_get_config_returns_config_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = get_config()

    assert isinstance(cfg, Config)


def test_get_config_returns_same_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    first = get_config()
    second = get_config()

    assert first is second


def test_get_config_caches_after_first_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Overwriting the YAML after the first load must not change the cached instance."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cached = get_config()
    yaml_file.write_text(_MINIMAL_YAML.replace("10.0.0.1", "9.9.9.9"), encoding="utf-8")

    assert get_config() is cached
    assert get_config().vita.ip == "10.0.0.1"


# ---------------------------------------------------------------------------
# reset_config
# ---------------------------------------------------------------------------


def test_reset_config_clears_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(_MINIMAL_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    first = get_config()
    reset_config()
    second = get_config()

    assert first is not second
