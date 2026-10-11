"""Strict offline verification for retained Paizo class-source evidence."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit

from ..manifest import canonical_json, digest, read_file, read_json, reject_links
from ..validation import (
    RulesValidationError,
    bounded_json,
    choice,
    https_url,
    integer,
    iso_date,
    require,
    rule_id,
    sequence,
    shape,
    strings,
    text,
    timestamp,
)


CLASS_SOURCE_EVIDENCE_SCHEMA_VERSION = 1
CLASS_SOURCE_EVIDENCE_AUTHORITY = "paizo"
CLASS_SOURCE_EVIDENCE_SCOPE = "class-publication-identity"
MAX_CLASS_SOURCE_DOSSIERS = 8
SHA256 = r"[0-9a-f]{64}"
BUNDLE_ID = r"pf2e-class-sources-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+"
EVIDENCE_ID = r"paizo\.class-source\.[a-z0-9]+(?:-[a-z0-9]+)*"
SOURCE_ID = r"pf2e\.source\.[a-z0-9]+(?:-[a-z0-9]+)*"
PRODUCT_HARDCOVER_ID = r"PZO[0-9]+-HC"
PRODUCT_PDF_ID = r"PZO[0-9]+E"
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
COMMON_URLS = {
    "faq_errata": "https://paizo.com/pathfinder/faq",
    "licenses": "https://paizo.com/licenses",
    "orc": "https://paizo.com/orclicense",
}
RIGHTS_BLOCKERS = frozenset(
    {
        "downstream-attribution-review",
        "downstream-compliance-review",
        "downstream-notice-review",
        "final-book-legal-page",
        "reserved-material-review",
        "trademark-review",
    }
)
RIGHTS_CONTEXTS = frozenset(
    {
        "final-book-legal-page-not-captured",
        "orc-notice-observed-compliance-unreviewed",
        "transition_schedule_unverified",
    }
)
PRODUCTION_CLASS_ASSERTIONS = {
    "pf2e.source.battlecry": (
        "Battlecry!",
        ("pf2e.class.commander", "pf2e.class.guardian"),
    ),
    "pf2e.source.dark-archive-remastered": (
        "Dark Archive (Remastered)",
        ("pf2e.class.psychic", "pf2e.class.thaumaturge"),
    ),
    "pf2e.source.guns-and-gears-remastered": (
        "Guns & Gears (Remastered)",
        ("pf2e.class.gunslinger", "pf2e.class.inventor"),
    ),
    "pf2e.source.impossible-magic": (
        "Impossible Magic",
        (
            "pf2e.class.magus",
            "pf2e.class.necromancer",
            "pf2e.class.runesmith",
            "pf2e.class.summoner",
        ),
    ),
    "pf2e.source.player-core": (
        "Player Core",
        (
            "pf2e.class.bard",
            "pf2e.class.cleric",
            "pf2e.class.druid",
            "pf2e.class.fighter",
            "pf2e.class.ranger",
            "pf2e.class.rogue",
            "pf2e.class.witch",
            "pf2e.class.wizard",
        ),
    ),
    "pf2e.source.player-core-2": (
        "Player Core 2",
        (
            "pf2e.class.alchemist",
            "pf2e.class.barbarian",
            "pf2e.class.champion",
            "pf2e.class.investigator",
            "pf2e.class.monk",
            "pf2e.class.oracle",
            "pf2e.class.sorcerer",
            "pf2e.class.swashbuckler",
        ),
    ),
    "pf2e.source.rage-of-elements": (
        "Rage of Elements",
        ("pf2e.class.kineticist",),
    ),
    "pf2e.source.war-of-immortals": (
        "War of Immortals",
        ("pf2e.class.animist", "pf2e.class.exemplar"),
    ),
}


def _sha256(value, path: str) -> str:
    value = text(value, path)
    require(re.fullmatch(SHA256, value) is not None, "invalid_value", path)
    return value


def _relative_dossier_path(value, path: str) -> str:
    require(type(value) is str, "invalid_type", path)
    require(value.strip() == value, "invalid_package_layout", path)
    text(value, path)
    require("\\" not in value and ":" not in value,
            "invalid_package_layout", path)
    relative = PurePosixPath(value)
    safe_parts = all(
        part not in {"", ".", ".."}
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].casefold() not in WINDOWS_RESERVED_NAMES
        for part in relative.parts
    )
    require(
        not relative.is_absolute()
        and value == relative.as_posix()
        and len(relative.parts) == 2
        and relative.parts[0] == "dossiers"
        and safe_parts
        and relative.suffix == ".json"
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.json", relative.name)
        is not None,
        "invalid_package_layout",
        path,
    )
    return value


def _paizo_url(value, path: str) -> str:
    https_url(value, path)
    host = urlsplit(value).hostname
    require(host in {"paizo.com", "store.paizo.com"}, "invalid_url", path)
    return value


def _nullable_paizo_url(value, path: str) -> str | None:
    if value is None:
        return None
    return _paizo_url(value, path)


def _product_ids(value, path: str) -> dict:
    shape(value, "hardcover pdf", path)
    text(value["hardcover"], path + ".hardcover", pattern=PRODUCT_HARDCOVER_ID)
    text(value["pdf"], path + ".pdf", pattern=PRODUCT_PDF_ID)
    require(
        value["hardcover"].removesuffix("-HC")
        == value["pdf"].removesuffix("E"),
        "evidence_mismatch",
        path,
    )
    return value


def _official_urls(value, path: str) -> dict:
    shape(
        value,
        "product pdf release_evidence release_corroboration licenses orc "
        "faq_errata",
        path,
    )
    for field in ("product", "pdf", "release_evidence"):
        _paizo_url(value[field], path + "." + field)
    _nullable_paizo_url(
        value["release_corroboration"], path + ".release_corroboration"
    )
    for field, expected in COMMON_URLS.items():
        require(value[field] == expected, "evidence_mismatch", path + "." + field)
    return value


def _notice_locator(value, path: str) -> dict | None:
    if value is None:
        return None
    shape(value, "printed_page pdf_page", path)
    integer(value["printed_page"], 1, 9_999, path + ".printed_page")
    integer(value["pdf_page"], 1, 9_999, path + ".pdf_page")
    return value


def _artifact_observation(value, product_ids: dict, path: str) -> dict:
    shape(value, "status product_id sha256 retained notice_locator", path)
    status = choice(value["status"], {"hash_observed", "not_captured"},
                    path + ".status")
    require(type(value["retained"]) is bool, "invalid_type", path + ".retained")
    require(value["retained"] is False, "invalid_value", path + ".retained")
    if status == "hash_observed":
        require(
            value["product_id"] in product_ids.values(),
            "evidence_mismatch",
            path + ".product_id",
        )
        _sha256(value["sha256"], path + ".sha256")
        require(value["notice_locator"] is not None,
                "missing_field", path + ".notice_locator")
        _notice_locator(value["notice_locator"], path + ".notice_locator")
        return value
    require(
        value["product_id"] is None
        and value["sha256"] is None
        and value["notice_locator"] is None,
        "invalid_observation",
        path,
    )
    return value


def _unverified_observation(value, path: str) -> dict:
    shape(value, "status designation", path)
    choice(value["status"], {"unverified"}, path + ".status")
    require(value["designation"] is None, "invalid_observation", path)
    return value


def _rights_observation(value, artifact: dict, path: str) -> dict:
    shape(value, "review_status context book_notice_status blockers", path)
    choice(value["review_status"], {"pending"}, path + ".review_status")
    choice(value["context"], RIGHTS_CONTEXTS, path + ".context")
    notice = choice(
        value["book_notice_status"], {"not_captured", "observed"},
        path + ".book_notice_status",
    )
    blockers = strings(value["blockers"], path + ".blockers")
    require(bool(blockers), "invalid_value", path + ".blockers")
    require(set(blockers) <= RIGHTS_BLOCKERS,
            "invalid_value", path + ".blockers")
    value["blockers"] = blockers
    if notice == "observed":
        require(artifact["status"] == "hash_observed",
                "evidence_mismatch", path + ".book_notice_status")
    else:
        require(artifact["status"] == "not_captured",
                "evidence_mismatch", path + ".book_notice_status")
    return value


def _dossier(document: dict, path: str) -> dict:
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version evidence_id source_id overlay_title "
        "official_product_title publisher product_ids release_date "
        "class_rule_ids official_urls artifact_observation "
        "printing_observation revision_observation rights_observation "
        "supported_review_scopes",
        path,
    )
    require(type(value["schema_version"]) is int,
            "invalid_type", path + ".schema_version")
    require(
        value["schema_version"] == CLASS_SOURCE_EVIDENCE_SCHEMA_VERSION,
        "unsupported_version",
        path + ".schema_version",
    )
    text(value["evidence_id"], path + ".evidence_id", pattern=EVIDENCE_ID)
    text(value["source_id"], path + ".source_id", pattern=SOURCE_ID)
    text(value["overlay_title"], path + ".overlay_title")
    text(value["official_product_title"], path + ".official_product_title")
    require(value["publisher"] == "Paizo Inc.",
            "evidence_mismatch", path + ".publisher")
    _product_ids(value["product_ids"], path + ".product_ids")
    iso_date(value["release_date"], path + ".release_date")
    class_rule_ids = sequence(value["class_rule_ids"], path + ".class_rule_ids")
    require(1 <= len(class_rule_ids) <= 8,
            "limit_exceeded", path + ".class_rule_ids")
    for index, item in enumerate(class_rule_ids):
        rule_id(item, f"{path}.class_rule_ids[{index}]", "class")
    require(len(class_rule_ids) == len(set(class_rule_ids)),
            "duplicate_id", path + ".class_rule_ids")
    value["class_rule_ids"] = sorted(class_rule_ids)
    _official_urls(value["official_urls"], path + ".official_urls")
    _artifact_observation(
        value["artifact_observation"], value["product_ids"],
        path + ".artifact_observation",
    )
    _unverified_observation(
        value["printing_observation"], path + ".printing_observation"
    )
    _unverified_observation(
        value["revision_observation"], path + ".revision_observation"
    )
    _rights_observation(
        value["rights_observation"], value["artifact_observation"],
        path + ".rights_observation",
    )
    scopes = strings(
        value["supported_review_scopes"], path + ".supported_review_scopes"
    )
    require(scopes == ["source_review"],
            "invalid_value", path + ".supported_review_scopes[0]")
    value["supported_review_scopes"] = scopes
    return value


def _manifest(document: dict) -> dict:
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version bundle_id created_at authority scope dossiers bundle_hash",
        "$.manifest",
    )
    require(type(value["schema_version"]) is int,
            "invalid_type", "$.manifest.schema_version")
    require(
        value["schema_version"] == CLASS_SOURCE_EVIDENCE_SCHEMA_VERSION,
        "unsupported_version",
        "$.manifest.schema_version",
    )
    text(value["bundle_id"], "$.manifest.bundle_id", pattern=BUNDLE_ID)
    timestamp(value["created_at"], "$.manifest.created_at")
    choice(value["authority"], {CLASS_SOURCE_EVIDENCE_AUTHORITY},
           "$.manifest.authority")
    choice(value["scope"], {CLASS_SOURCE_EVIDENCE_SCOPE}, "$.manifest.scope")
    _sha256(value["bundle_hash"], "$.manifest.bundle_hash")
    references = sequence(value["dossiers"], "$.manifest.dossiers")
    require(len(references) == MAX_CLASS_SOURCE_DOSSIERS,
            "count_mismatch", "$.manifest.dossiers")
    seen = {field: set() for field in ("evidence_id", "source_id", "path", "sha256")}
    for index, reference in enumerate(references):
        path = f"$.manifest.dossiers[{index}]"
        shape(reference, "evidence_id source_id path sha256", path)
        text(reference["evidence_id"], path + ".evidence_id", pattern=EVIDENCE_ID)
        text(reference["source_id"], path + ".source_id", pattern=SOURCE_ID)
        _relative_dossier_path(reference["path"], path + ".path")
        _sha256(reference["sha256"], path + ".sha256")
        for field in seen:
            identity = (
                reference[field].casefold()
                if field == "path"
                else reference[field]
            )
            require(identity not in seen[field], "duplicate_id", path + "." + field)
            seen[field].add(identity)
    references.sort(key=lambda item: item["source_id"])
    require(set(seen["source_id"]) == set(PRODUCTION_CLASS_ASSERTIONS),
            "evidence_mismatch", "$.manifest.dossiers")
    return value


def _safe_entry(entry, *, want_directory: bool) -> bool:
    info = entry.stat(follow_symlinks=False)
    return (
        not stat.S_ISLNK(info.st_mode)
        and not (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        )
        and (stat.S_ISDIR(info.st_mode) if want_directory else stat.S_ISREG(info.st_mode))
    )


def _read_layout(manifest_path: Path) -> tuple[bytes, dict[str, bytes]]:
    manifest_path = Path(manifest_path).absolute()
    root = manifest_path.parent
    try:
        reject_links(root)
        root_info = root.lstat()
        require(stat.S_ISDIR(root_info.st_mode), "invalid_package_layout", "$files")
        root_entries = {}
        root_casefolded = set()
        with os.scandir(root) as iterator:
            for entry in iterator:
                require(len(root_entries) < 2, "invalid_package_layout", "$files")
                folded = entry.name.casefold()
                require(folded not in root_casefolded,
                        "invalid_package_layout", "$files")
                root_casefolded.add(folded)
                root_entries[entry.name] = entry
        require(set(root_entries) == {"manifest.json", "dossiers"},
                "invalid_package_layout", "$files")
        require(_safe_entry(root_entries["manifest.json"], want_directory=False),
                "invalid_package_layout", "$files")
        require(_safe_entry(root_entries["dossiers"], want_directory=True),
                "invalid_package_layout", "$files")
        require(manifest_path == root / "manifest.json",
                "invalid_package_layout", "$files")

        dossier_files = {}
        dossier_casefolded = set()
        with os.scandir(root / "dossiers") as iterator:
            for entry in iterator:
                require(len(dossier_files) < MAX_CLASS_SOURCE_DOSSIERS,
                        "invalid_package_layout", "$files")
                folded = entry.name.casefold()
                require(folded not in dossier_casefolded,
                        "invalid_package_layout", "$files")
                dossier_casefolded.add(folded)
                require(_safe_entry(entry, want_directory=False),
                        "invalid_package_layout", "$files")
                dossier_files[f"dossiers/{entry.name}"] = Path(entry.path)
        require(len(dossier_files) == MAX_CLASS_SOURCE_DOSSIERS,
                "invalid_package_layout", "$files")
        manifest_bytes = read_file(root / "manifest.json")
        return manifest_bytes, {
            relative: read_file(path)
            for relative, path in sorted(dossier_files.items())
        }
    except RulesValidationError:
        raise
    except OSError:
        raise RulesValidationError("invalid_package_layout", "$files") from None


def verify_class_source_evidence_bundle(
    manifest_path: Path,
    expected_hash: str | None = None,
) -> dict:
    """Verify one exact canonical Paizo class-publication evidence bundle."""
    manifest_bytes, dossier_files = _read_layout(manifest_path)
    try:
        manifest_document = read_json(manifest_bytes)
        manifest = _manifest(manifest_document)
    except RulesValidationError as error:
        if error.path == "$":
            raise RulesValidationError(error.code, "$.files.manifest.json") from None
        raise
    require(
        manifest_bytes == canonical_json(manifest),
        "noncanonical_bytes",
        "$.files.manifest.json",
    )
    without_hash = deepcopy(manifest)
    bundle_hash = without_hash.pop("bundle_hash")
    require(
        digest(canonical_json(without_hash)) == bundle_hash,
        "integrity_mismatch",
        "$.files.manifest.json",
    )

    referenced_paths = {reference["path"] for reference in manifest["dossiers"]}
    require(referenced_paths == set(dossier_files),
            "invalid_package_layout", "$files")
    dossiers_by_hash = {}
    dossiers_by_source = {}
    for index, reference in enumerate(manifest["dossiers"]):
        relative = reference["path"]
        file_path = f"$.files.{relative}"
        raw = dossier_files[relative]
        try:
            dossier_document = read_json(raw)
            dossier = _dossier(dossier_document, f"$.dossiers[{index}]")
        except RulesValidationError as error:
            if error.path == "$":
                raise RulesValidationError(error.code, file_path) from None
            raise
        require(raw == canonical_json(dossier), "noncanonical_bytes", file_path)
        dossier_hash = digest(raw)
        require(dossier_hash == reference["sha256"],
                "integrity_mismatch", file_path)
        require(dossier["evidence_id"] == reference["evidence_id"],
                "evidence_mismatch", f"$.dossiers[{index}].evidence_id")
        require(dossier["source_id"] == reference["source_id"],
                "source_mismatch", f"$.dossiers[{index}].source_id")
        expected_name = dossier["source_id"].removeprefix("pf2e.source.") + ".json"
        require(PurePosixPath(relative).name == expected_name,
                "evidence_mismatch", f"$.manifest.dossiers[{index}].path")
        expected_title, expected_rules = PRODUCTION_CLASS_ASSERTIONS[
            dossier["source_id"]
        ]
        require(dossier["overlay_title"] == expected_title,
                "evidence_mismatch", f"$.dossiers[{index}].overlay_title")
        require(tuple(dossier["class_rule_ids"]) == expected_rules,
                "evidence_mismatch", f"$.dossiers[{index}].class_rule_ids")
        dossiers_by_hash[dossier_hash] = deepcopy(dossier)
        dossiers_by_source[dossier["source_id"]] = deepcopy(dossier)

    require(len(dossiers_by_hash) == MAX_CLASS_SOURCE_DOSSIERS,
            "duplicate_id", "$.manifest.dossiers")
    require(len(dossiers_by_source) == MAX_CLASS_SOURCE_DOSSIERS,
            "duplicate_id", "$.manifest.dossiers")
    if expected_hash is not None:
        _sha256(expected_hash, "$.binding.bundle_hash")
        require(expected_hash == bundle_hash,
                "binding_mismatch", "$.binding.bundle_hash")
    return {
        "manifest": deepcopy(manifest),
        "dossiers_by_hash": dossiers_by_hash,
        "dossiers_by_source": dossiers_by_source,
    }


def class_source_evidence_hashes(
    bundle: dict,
    source_id: str,
    review_scope: str,
) -> frozenset[str]:
    """Return retained hashes that support one source-scoped review gate."""
    if type(bundle) is not dict or type(source_id) is not str or type(review_scope) is not str:
        return frozenset()
    dossiers = bundle.get("dossiers_by_hash")
    if type(dossiers) is not dict:
        return frozenset()
    return frozenset(
        dossier_hash
        for dossier_hash, dossier in dossiers.items()
        if type(dossier) is dict
        and dossier.get("source_id") == source_id
        and review_scope in dossier.get("supported_review_scopes", [])
    )
