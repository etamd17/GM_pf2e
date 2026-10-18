"""Load installed versioned rules packages through normal integrity checks.

The registry's legacy ID view intentionally keeps the first published package
version, while ``pack_versions`` exposes later immutable versions by exact
``(id, version, package_hash)`` identity.
"""
from pathlib import Path

from .registry import load_registry
from .paths import registry_path

SEED_DIRECTORY = registry_path().parent
BOOTSTRAP_REGISTRY = Path(__file__).with_name("bootstrap_registry.json")


def load_seed_registry():
    installed = registry_path()
    return load_registry(installed if installed.is_file() else BOOTSTRAP_REGISTRY)
