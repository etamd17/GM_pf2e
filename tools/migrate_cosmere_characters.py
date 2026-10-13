"""Plan or apply the recoverable Cosmere character schema migration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from systems.cosmere.migration import MigrationError, migrate_character_directory
from systems.cosmere.homebrew import normalize_store
from systems.cosmere.rules.profiles import normalize_binding, normalize_theme
from systems.cosmere.rules.schema import RulesValidationError


def parser():
    value = argparse.ArgumentParser(
        description="Dry-run or apply versioned Cosmere character document migration.")
    value.add_argument("directory", help="Campaign cosmere_pcs directory")
    value.add_argument("--apply", action="store_true",
                       help="Write changes after creating recoverable backups")
    value.add_argument("--run-id", help="Stable backup directory label for apply mode")
    value.add_argument("--theme", choices=("stormlight", "mistborn"),
                       help="Optional cosmetic campaign theme override; never selects mechanics")
    value.add_argument("--campaign-config",
                       help="Optional campaign.json whose exact rules_binding is authoritative")
    return value


def _config(path, theme):
    config = {}
    if path:
        try:
            with Path(path).open(encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, UnicodeError, ValueError):
            raise MigrationError("campaign config could not be read") from None
        if not isinstance(loaded, dict):
            raise MigrationError("campaign config must be an object")
        if "rules_binding" in loaded:
            config["rules_binding"] = loaded["rules_binding"]
        if "cosmere_theme" in loaded or "cosmere_world" in loaded:
            config["cosmere_theme"] = normalize_theme(loaded)
    if theme is not None:
        config["cosmere_theme"] = theme
    return config


def _campaign_homebrew(directory):
    path = Path(directory).resolve().parent / "homebrew.json"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, UnicodeError, ValueError):
        raise MigrationError("campaign homebrew could not be read") from None
    if not isinstance(loaded, dict):
        raise MigrationError("campaign homebrew must be an object")
    return normalize_store(loaded)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = _config(args.campaign_config, args.theme)
        report = migrate_character_directory(
            args.directory, apply=args.apply, binding=normalize_binding(config),
            cosmere_theme=(normalize_theme(config)
                           if "cosmere_theme" in config else None),
            run_id=args.run_id, homebrew=_campaign_homebrew(args.directory))
    except (MigrationError, RulesValidationError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
