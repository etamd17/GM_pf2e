"""Location of installed Cosmere rules packages outside the public checkout."""
from __future__ import annotations

import os
from pathlib import Path


REPO_DATA = Path(__file__).resolve().parents[3] / "cosmere_data"


def install_dir() -> Path:
    override = os.environ.get("COSMERE_DATA_DIR")
    if override:
        return Path(override)
    volume = os.environ.get("DATA_DIR")
    return Path(volume) / "cosmere_data" if volume else REPO_DATA


def data_dir() -> Path:
    override = os.environ.get("COSMERE_DATA_DIR")
    if override:
        return Path(override)
    volume = os.environ.get("DATA_DIR")
    if volume:
        candidate = Path(volume) / "cosmere_data"
        if (candidate / "rules" / "registry.json").is_file():
            return candidate
    return REPO_DATA


def registry_path() -> Path:
    return data_dir() / "rules" / "registry.json"
