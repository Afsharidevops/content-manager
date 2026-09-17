"""Read/write profiles and duration overrides from JSON files in the data dir.

This lets the operator tune video profiles and duration targets without a
code change. The files are written by the Panel through ``PUT /profiles``
and read by the worker in ``prompts.py``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger("notebooklm.config_manager")

CONFIG_FILE = "notebooklm-config.json"


def config_path(data_dir: str) -> str:
    return os.path.join(data_dir, CONFIG_FILE)


def load_config(data_dir: str) -> dict:
    """Return the saved config dict, or an empty dict when no file exists."""
    path = config_path(data_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError) as exc:
        LOGGER.warning("Config file %s is invalid: %s", path, exc)
    return {}


def save_config(data_dir: str, config: dict) -> None:
    """Atomically write the config dict to disk."""
    path = Path(config_path(data_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
        tmp.replace(path)
        LOGGER.info("Config saved to %s", path)
    except OSError as exc:
        LOGGER.error("Failed to write config %s: %s", path, exc)


def get_profiles(data_dir: str) -> dict:
    """Return the saved profile overrides (may be empty)."""
    cfg = load_config(data_dir)
    return cfg.get("profiles") or {}


def get_duration_targets(data_dir: str) -> dict:
    """Return the saved duration target overrides (may be empty)."""
    cfg = load_config(data_dir)
    return cfg.get("duration_targets") or {}


def set_profiles(data_dir: str, profiles: dict) -> None:
    """Merge profile overrides into the saved config."""
    cfg = load_config(data_dir)
    cfg["profiles"] = profiles
    save_config(data_dir, cfg)


def set_duration_targets(data_dir: str, targets: dict) -> None:
    """Merge duration target overrides into the saved config."""
    cfg = load_config(data_dir)
    cfg["duration_targets"] = targets
    save_config(data_dir, cfg)
