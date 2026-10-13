"""Where the D&D 5e rules data lives.

The data (built from 5etools) is NOT in git: the repo is public and most of
it is non-SRD. It sits in one directory:

    <data dir>/
        dnd5e.db          SQLite content database   (tools/dnd5e/build_dnd5e_db.py)
        _tags.json        5etools tag registry       (copied from resolved/)
        images.json       art index                  (tools/dnd5e/fetch_images.py)
        img/tokens/*.webp, img/art/*.webp
        resolved/, raw/   build inputs; only needed to rebuild, never at runtime

Resolution order:
1. ``DND5E_DATA_DIR`` if set;
2. ``<DATA_DIR>/dnd5e_data`` if it holds a database -- production, where
   DATA_DIR is the Railway volume and the admin installs a bundle there
   (``/admin/dnd5e-data``);
3. ``<repo>/dnd5e_data`` -- a dev checkout (gitignored).

``DND5E_DB_PATH`` still overrides just the database file.
"""
from __future__ import annotations

import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_DATA = os.path.join(REPO, 'dnd5e_data')


def data_dir() -> str:
    env = os.environ.get('DND5E_DATA_DIR')
    if env:
        return env
    vol = os.environ.get('DATA_DIR')
    if vol:
        cand = os.path.join(vol, 'dnd5e_data')
        if os.path.isfile(os.path.join(cand, 'dnd5e.db')):
            return cand
    return REPO_DATA


def install_dir() -> str:
    """Where an uploaded bundle is installed: the volume when there is one."""
    env = os.environ.get('DND5E_DATA_DIR')
    if env:
        return env
    vol = os.environ.get('DATA_DIR')
    return os.path.join(vol, 'dnd5e_data') if vol else REPO_DATA


def db_path() -> str:
    return os.environ.get('DND5E_DB_PATH') or os.path.join(data_dir(), 'dnd5e.db')


def tags_path() -> str:
    d = data_dir()
    p = os.path.join(d, '_tags.json')
    return p if os.path.isfile(p) else os.path.join(d, 'resolved', '_tags.json')


def images_index_path() -> str:
    return os.path.join(data_dir(), 'images.json')


def img_dir() -> str:
    return os.path.join(data_dir(), 'img')
