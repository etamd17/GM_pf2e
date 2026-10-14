"""Private bundle installation and the no-data Stormlight boundary."""
from pathlib import Path
import io
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
