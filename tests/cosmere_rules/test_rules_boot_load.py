"""MB-11: the Cosmere rules load at boot, not on someone's first Cosmere page.

A load blocks the single gevent worker, so on the first page it froze every
request and live stream for everyone at the table.
"""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from systems.cosmere.rules import datapack, profiles
from systems.cosmere.rules.schema import RulesValidationError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cosmere_rules"


def test_a_broken_bundle_never_stops_the_site_booting(monkeypatch, capsys):
    import app as host

    def broken():
        raise RulesValidationError("registry hash mismatch")

    monkeypatch.setattr(profiles, "default_registry", broken)
    assert host._preload_cosmere_rules() is None
    assert "Rules not loaded at boot" in capsys.readouterr().out


def test_the_boot_load_reports_what_it_loaded(monkeypatch, capsys):
    import app as host

    loaded = SimpleNamespace(pack_versions={("a", "1.0.0", "x"): None})
    monkeypatch.setattr(profiles, "default_registry", lambda: loaded)
    assert host._preload_cosmere_rules() is loaded
    assert "Rules loaded at boot: 1 package versions" in capsys.readouterr().out


def test_importing_the_app_loads_the_installed_rules(tmp_path):
    """Only a fresh interpreter importing the module the way gunicorn does can
    show the boot load runs: inside the suite it takes the pytest exemption."""
    import app as host

    installed = tmp_path / "cosmere"
    datapack.install(datapack.build(FIXTURE, tmp_path / "bundle.tar.gz"), installed)
    probe = (
        "import app; from systems.cosmere.rules import profiles; "
        "r = profiles._default_registry; "
        "print('HASH', r.package_hash if r is not None else None)"
    )
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["FLASK_DEBUG"] = "false"
    # Throwaway data: the import also starts the real persistence thread.
    env["DATA_DIR"] = str(tmp_path / "data")
    env["COSMERE_DATA_DIR"] = str(installed)
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            cwd=host.BASE_DIR, timeout=180, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    from systems.cosmere.rules.registry import load_registry
    want = load_registry(installed / "rules" / "registry.json").package_hash
    assert f"HASH {want}" in result.stdout, result.stdout[-2000:]
    assert "Rules loaded at boot: 5 package versions" in result.stdout


def test_the_boot_load_is_skipped_under_pytest(monkeypatch):
    import app as host

    calls = []
    monkeypatch.setattr(host, "_preload_cosmere_rules", lambda: calls.append("loaded"))
    host._autostart_cosmere_rules()
    assert calls == []


def test_the_reloader_parent_does_not_load_but_its_child_does(monkeypatch):
    import app as host

    calls = []
    monkeypatch.setattr(host, "_preload_cosmere_rules", lambda: calls.append("loaded"))
    monkeypatch.delitem(sys.modules, "pytest")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("FLASK_DEBUG", "true")
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    host._autostart_cosmere_rules()
    assert calls == []
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    host._autostart_cosmere_rules()
    assert calls == ["loaded"]


def test_no_kind_of_load_failure_stops_the_boot(monkeypatch, capsys):
    import app as host

    def broken():
        raise KeyError("cosmere.core")

    monkeypatch.setattr(profiles, "default_registry", broken)
    assert host._preload_cosmere_rules() is None
    assert "Rules not loaded at boot" in capsys.readouterr().out
