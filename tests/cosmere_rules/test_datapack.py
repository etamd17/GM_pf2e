"""Private bundle installation and the no-data Stormlight boundary."""
from pathlib import Path
import io
import shutil
import tarfile

import pytest

from systems.cosmere.rules import datapack
from systems.cosmere.rules.profiles import default_registry, resolve_profile
from systems.cosmere.rules.schema import RulesValidationError
from systems.cosmere.rules.seeds import load_seed_registry


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cosmere_rules"


def test_invented_bundle_installs_and_replaces_only_after_validation(tmp_path):
    archive = datapack.build(FIXTURE, tmp_path / "test-rules.tar.gz")
    dest = tmp_path / "installed"
    result = datapack.install(archive, dest)
    assert result["packages"] == 5
    assert result["records"] == 4
    old_registry = (dest / "rules" / "registry.json").read_bytes()

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as bundle:
        payload = b"do not write this"
        member = tarfile.TarInfo("../escape.txt")
        member.size = len(payload)
        bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(datapack.BundleError, match="unexpected"):
        datapack.install(bad, dest)
    assert (dest / "rules" / "registry.json").read_bytes() == old_registry
    assert not (tmp_path / "escape.txt").exists()


def test_no_private_package_keeps_stormlight_and_reports_mistborn_missing(
        tmp_path, monkeypatch):
    monkeypatch.setenv("COSMERE_DATA_DIR", str(tmp_path / "not-installed"))
    default_registry.cache_clear()
    try:
        registry = load_seed_registry()
        assert set(registry.packs) == {
            "cosmere.core", "cosmere.stormlight.legacy"}
        assert registry.packs["cosmere.core"].package_hash == (
            "2d6e6d6018059ccccbf3f86b51e1cc054744f352f253f9fa5129c9270712a24b")
        assert registry.packs["cosmere.stormlight.legacy"].package_hash == (
            "ffae36c5e5d882a52d9fe71f38a7c17dcf03e22b20cc7baf690a03d349402edb")
        assert type(resolve_profile()).__name__ == "StormlightRulesProfile"
        assert resolve_profile().catalog()["source_references"]
        with pytest.raises(RulesValidationError, match="Mistborn rules data is not installed"):
            resolve_profile({"origin_world": "scadrial", "era": "era-1"})
    finally:
        default_registry.cache_clear()


def test_site_admin_upload_refreshes_rules_and_rejects_non_admin(tmp_path, monkeypatch):
    import app as host

    archive = datapack.build(FIXTURE, tmp_path / "upload.tar.gz")
    monkeypatch.setenv("COSMERE_DATA_DIR", str(tmp_path / "installed"))
    monkeypatch.setattr(host, "_account_mode", lambda: False)
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    monkeypatch.setattr(host, "_cosmere_site_admin", lambda: False)
    client = host.app.test_client()
    assert client.get("/cosmere/admin/data").status_code == 403
    assert client.post("/api/cosmere/admin/data", data={
        "file": (io.BytesIO(archive.read_bytes()), "upload.tar.gz")}).status_code == 403

    monkeypatch.setattr(host, "_cosmere_site_admin", lambda: True)
    default_registry.cache_clear()
    try:
        assert client.get("/cosmere/admin/data").status_code == 200
        response = client.post("/api/cosmere/admin/data", data={
            "file": (io.BytesIO(archive.read_bytes()), "upload.tar.gz")})
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["records"] == 4
        assert "cosmere.mistborn.core" in default_registry().packs
    finally:
        default_registry.cache_clear()


def _admin_client(host, monkeypatch, installed):
    monkeypatch.setenv("COSMERE_DATA_DIR", str(installed))
    monkeypatch.setattr(host, "_account_mode", lambda: False)
    monkeypatch.setattr(host, "_cosmere_site_admin", lambda: True)
    return host.app.test_client()


def test_upload_serves_the_registry_it_verified_without_loading_it_again(tmp_path, monkeypatch):
    """MB-11: a load blocks the single worker, so an install hands its
    verified registry over instead of making the next page verify it again."""
    import app as host
    from systems.cosmere.rules import profiles

    archive = datapack.build(FIXTURE, tmp_path / "upload.tar.gz")
    client = _admin_client(host, monkeypatch, tmp_path / "installed")
    default_registry.cache_clear()

    def second_load():
        raise AssertionError("the installed registry was verified a second time")

    monkeypatch.setattr(profiles, "load_seed_registry", second_load)
    try:
        response = client.post("/api/cosmere/admin/data", data={
            "file": (io.BytesIO(archive.read_bytes()), "upload.tar.gz")})
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert (body["release"], body["older_releases"], body["packages"]) == ("1.1.0", [], 5)
        assert body["hash"] == default_registry().package_hash
        assert "cosmere.mistborn.core" in default_registry().packs
    finally:
        default_registry.cache_clear()


def test_admin_page_names_the_installed_release(tmp_path, monkeypatch):
    import app as host

    installed = tmp_path / "installed"
    datapack.install(datapack.build(FIXTURE, tmp_path / "bundle.tar.gz"), installed)
    client = _admin_client(host, monkeypatch, installed)
    default_registry.cache_clear()
    try:
        page = client.get("/cosmere/admin/data").get_data(as_text=True)
        assert "Installed: Mistborn rules 1.1.0" in page
        # One release, so the detail line ends at the hash with nothing older listed.
        short = default_registry().package_hash[:8]
        assert f"5 package versions, registry <code>{short}</code></div>" in page
    finally:
        default_registry.cache_clear()


def test_admin_page_still_renders_when_the_installed_rules_do_not_load(tmp_path, monkeypatch):
    import app as host

    installed = tmp_path / "installed"
    datapack.install(datapack.build(FIXTURE, tmp_path / "bundle.tar.gz"), installed)
    (installed / "rules" / "registry.json").write_bytes(b"{}")
    client = _admin_client(host, monkeypatch, installed)
    default_registry.cache_clear()
    try:
        response = client.get("/cosmere/admin/data")
        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "Installed, but it does not load: invalid compiled registry fields" in page
        assert 'id="install"' in page
    finally:
        default_registry.cache_clear()


def test_a_bundle_holds_many_releases_but_not_unlimited_files(tmp_path):
    """MB-11: each release adds three package files; 16 refused 1.1.4."""
    tree = tmp_path / "tree"
    shutil.copytree(FIXTURE, tree)
    packages = tree / "rules" / "mistborn"
    packages.mkdir()
    for minor in range(20):
        (packages / f"core-1.{minor}.0.json").write_text("{}", encoding="utf-8")
    result = datapack.install(datapack.build(tree, tmp_path / "many.tar.gz"), tmp_path / "a")
    assert result["packages"] == 5
    for minor in range(20, 70):
        (packages / f"core-1.{minor}.0.json").write_text("{}", encoding="utf-8")
    with pytest.raises(datapack.BundleError, match="too many files"):
        datapack.install(datapack.build(tree, tmp_path / "too-many.tar.gz"), tmp_path / "b")
