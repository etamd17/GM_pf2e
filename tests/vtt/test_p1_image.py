"""P-1: the production-capable image's start and pre-deploy paths.

docker/entrypoint.sh and tools/vtt/predeploy.sh run under a real ``sh`` with
``python`` and ``gunicorn`` replaced by stubs that record how they were called,
so the tests see exactly which commands each mode runs (and that a refused
start runs none)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="no sh")


def stubs(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("python", "gunicorn"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $* PGOPTIONS=${{PGOPTIONS:-}}" >> "{log.as_posix()}"\n',
                        encoding="utf-8", newline="\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, log


def run(tmp_path, script, env):
    bin_dir, log = stubs(tmp_path)
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    base = {k: v for k, v in os.environ.items()
            if k not in ("RUN_MIGRATIONS", "VTT_ENABLED", "OWNERSHIP_BACKEND", "DATABASE_URL", "SECRET_KEY",
                         "DATA_DIR", "PGOPTIONS")}
    base["PATH"] = bin_dir.as_posix() + os.pathsep + base.get("PATH", "")
    base["DATA_DIR"] = data.as_posix()
    base.update(env)
    result = subprocess.run([SH, (ROOT / script).as_posix()], env=base, capture_output=True, text=True,
                            cwd=ROOT, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


ENTRY = "docker/entrypoint.sh"
PRE = "tools/vtt/predeploy.sh"


# --- the entrypoint ----------------------------------------------------------------------------

def test_by_default_it_starts_the_app_with_no_database_change(tmp_path):
    result, calls = run(tmp_path, ENTRY, {})
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1 and calls[0].startswith("gunicorn app:app") and "--workers 1" in calls[0]
    assert not any(c.startswith("python") for c in calls)     # no Alembic: no DDL at start


def test_the_site_in_json_mode_with_the_vtt_off_needs_no_database(tmp_path):
    result, calls = run(tmp_path, ENTRY, {"VTT_ENABLED": "0", "OWNERSHIP_BACKEND": "json", "RUN_MIGRATIONS": "0"})
    assert result.returncode == 0, result.stderr
    assert calls[0].startswith("gunicorn")


def test_run_migrations_1_runs_alembic_first(tmp_path):
    result, calls = run(tmp_path, ENTRY, {"RUN_MIGRATIONS": "1", "DATABASE_URL": "postgresql://x"})
    assert result.returncode == 0, result.stderr
    assert calls[0].startswith("python -m alembic upgrade head") and calls[1].startswith("gunicorn")


@pytest.mark.parametrize("value", ["yes", "true", "2", " 1"])
def test_any_other_value_fails_at_start_and_runs_nothing(tmp_path, value):
    result, calls = run(tmp_path, ENTRY, {"RUN_MIGRATIONS": value, "DATABASE_URL": "postgresql://x"})
    assert result.returncode != 0 and "RUN_MIGRATIONS must be 0 or 1" in result.stderr
    assert calls == []


@pytest.mark.parametrize("env", [
    {"VTT_ENABLED": "1", "SECRET_KEY": "k"},
    {"OWNERSHIP_BACKEND": "sql"},
    {"OWNERSHIP_BACKEND": "shadow"},
    {"RUN_MIGRATIONS": "1"},
])
def test_what_needs_a_database_refuses_to_start_without_one(tmp_path, env):
    result, calls = run(tmp_path, ENTRY, env)
    assert result.returncode != 0 and "DATABASE_URL is required" in result.stderr
    assert calls == []


def test_the_vtt_needs_a_secret_key(tmp_path):
    result, calls = run(tmp_path, ENTRY, {"VTT_ENABLED": "1", "DATABASE_URL": "postgresql://x"})
    assert result.returncode != 0 and "SECRET_KEY is required" in result.stderr and calls == []


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_an_unwritable_data_volume_refuses_to_start(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o555)
    try:
        result, calls = run(tmp_path, ENTRY, {})
    finally:
        data.chmod(0o755)
    assert result.returncode != 0 and "is not writable" in result.stderr and calls == []


# --- the pre-deploy step -----------------------------------------------------------------------

def test_predeploy_with_no_database_and_the_vtt_off_does_nothing(tmp_path):
    result, calls = run(tmp_path, PRE, {"VTT_ENABLED": "0"})
    assert result.returncode == 0, result.stderr
    assert calls == [] and "no migrations to run" in result.stdout and "no asset store" in result.stdout


def test_predeploy_migrates_with_bounded_lock_and_statement_timeouts_then_probes(tmp_path):
    result, calls = run(tmp_path, PRE, {"DATABASE_URL": "postgresql://x", "VTT_ENABLED": "1"})
    assert result.returncode == 0, result.stderr
    assert calls[0].startswith("python -m alembic upgrade head")
    assert "lock_timeout=10s" in calls[0] and "statement_timeout=300s" in calls[0]
    assert calls[1].startswith("python tools/vtt/check_asset_store.py")


def test_the_scripts_are_in_the_image_and_keep_lf():
    allowed = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for script in ("tools/vtt/predeploy.sh", "tools/vtt/own_data_volume.sh"):
        assert f"!{script}" in allowed
        assert b"\r\n" not in (ROOT / script).read_bytes()
    assert "tools/vtt/*.sh text eol=lf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_the_r1_railway_config_has_no_start_command_and_uses_the_entrypoint():
    # deploy/railway.r1.toml replaces railway.toml at R-1 (approval); until then
    # railway.toml stays production's Nixpacks config, pinned by ci.yml.
    import tomllib
    config = tomllib.loads((ROOT / "deploy" / "railway.r1.toml").read_text(encoding="utf-8"))
    assert config["build"]["builder"] == "DOCKERFILE"
    assert "startCommand" not in config.get("deploy", {})
    assert config["deploy"]["preDeployCommand"] == ["sh tools/vtt/predeploy.sh"]
    assert config["deploy"]["healthcheckPath"] == "/ready"


def test_production_railway_config_is_unchanged_until_r1():
    import tomllib
    config = tomllib.loads((ROOT / "railway.toml").read_text(encoding="utf-8"))
    assert config["build"]["builder"] == "NIXPACKS" and "startCommand" in config["deploy"]
