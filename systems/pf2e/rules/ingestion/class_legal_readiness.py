"""Offline, fail-closed verification of class legal-readiness observations."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata

from ..manifest import canonical_json, digest, read_file, read_json, reject_links
from ..validation import (
    RulesValidationError, bounded_json, choice, https_url, integer, iso_date,
    require, sequence, shape, strings, text, timestamp,
)
from .class_review_overlay import verify_class_review_overlay
from .class_source_evidence import verify_class_source_evidence_bundle


CLASS_LEGAL_READINESS_SCHEMA_VERSION = 1
CLASS_LEGAL_READINESS_AUTHORITY = "paizo"
CLASS_LEGAL_READINESS_SCOPE = "class-license-readiness"
MAX_CLASS_LEGAL_READINESS_DOSSIERS = 8
MAX_PATH_COMPONENTS = 256
MAX_ERRATA_LOCATORS = 4096
MAX_ERRATA_PAGES = 4096
MAX_LOCATOR_LABEL = 64
SHA256 = r"[0-9a-f]{64}"
BUNDLE_ID = r"pf2e-class-legal-readiness-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+"
EVIDENCE_ID = r"paizo\.class-legal-readiness\.[a-z0-9]+(?:-[a-z0-9]+)*"
SOURCE_EVIDENCE_ID = r"paizo\.class-source\.[a-z0-9]+(?:-[a-z0-9]+)*"
SOURCE_ID = r"pf2e\.source\.[a-z0-9]+(?:-[a-z0-9]+)*"
PRODUCT_HARDCOVER_ID = r"PZO[0-9]+-HC"
PRODUCT_PDF_ID = r"PZO[0-9]+E"
ARTIFACT_STATUSES = frozenset({"hash_observed", "missing"})
LEGAL_NOTICE_STATUSES = frozenset({"observed", "missing"})
ERRATA_INVENTORY_STATUSES = frozenset({"complete_as_of_capture", "unverified"})
ERRATA_ENTRY_TYPES = frozenset({"erratum", "clarification"})
UNVERIFIED_STATUSES = frozenset({"unverified"})
ERRATA_APPLICABILITY_STATUSES = frozenset({"unresolved"})
DOWNSTREAM_COMPLIANCE_STATUSES = frozenset({"missing"})
REVIEW_READINESS_STATUSES = frozenset({"blocked"})
LEGAL_NOTICE_FAMILIES = frozenset({"orc"})
SUPPORTED_REVIEW_SCOPES = ("license_readiness",)
DOWNSTREAM_BLOCKERS = (
    "attribution-review", "branding-review", "distribution-review",
    "notice-review", "reserved-material-review", "trademark-review",
)
READINESS_BLOCKERS = frozenset({
    "final-artifact-missing", "final-legal-notice-missing", "printing-unverified",
    "revision-unverified", "errata-inventory-unverified", "errata-applicability-unresolved",
    "downstream-compliance-missing", "named-reviewer-missing",
})
SOURCE_SLUGS = (
    "battlecry", "dark-archive-remastered", "guns-and-gears-remastered",
    "impossible-magic", "player-core", "player-core-2", "rage-of-elements",
    "war-of-immortals",
)
EXACT_SOURCE_ROSTER = frozenset("pf2e.source." + slug for slug in SOURCE_SLUGS)
DOSSIER_FILES = frozenset(f"dossiers/{slug}.json" for slug in SOURCE_SLUGS)
OVERLAY_FILES = frozenset({"manifest.json", "authoring.json", "sources.json", "records.json"})
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
)
PRODUCTION_CLASS_REVIEW_OVERLAY_ID = "pf2e-class-source-review-2026-10-10.1"
PRODUCTION_CLASS_REVIEW_OVERLAY_HASH = "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb"
PRODUCTION_SOURCE_BUNDLE_ID = "pf2e-class-sources-2026-10-10.1"
PRODUCTION_SOURCE_BUNDLE_HASH = "4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3"
PRODUCTION_PLAYER_CORE_ARTIFACT_HASH = "494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9"
PRODUCTION_PLAYER_CORE_NOTICE_LOCATOR = {"printed_page": 463, "pdf_page": 464}
OFFICIAL_ERRATA_URL = "https://paizo.com/pathfinder/faq"
COUNT_FIELDS = (
    "sources", "artifact_hashes_observed", "final_artifacts_missing",
    "final_legal_notices_observed", "final_legal_notices_missing", "printings_unverified",
    "revisions_unverified", "errata_inventories_complete_as_captured",
    "errata_inventories_unverified", "license_reviews_ready", "license_reviews_blocked",
    "license_reviews_approved", "enabled_mechanics",
)


def _sha256(value, path: str) -> str:
    text(value, path)
    require(re.fullmatch(SHA256, value) is not None, "invalid_value", path)
    return value


def _relative_dossier_path(value, path: str) -> str:
    text(value, path)
    relative = PurePosixPath(value)
    require(
        len(value) <= 128 and "\\" not in value and ":" not in value
        and not relative.is_absolute() and relative.as_posix() == value
        and len(relative.parts) == 2 and relative.parts[0] == "dossiers"
        and all(p not in {"", ".", ".."} and not p.endswith((" ", "."))
                and p.split(".", 1)[0].casefold() not in WINDOWS_RESERVED_NAMES
                for p in relative.parts)
        and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.json", relative.name) is not None,
        "invalid_package_layout", path,
    )
    return value


def _product_ids(value, path: str) -> dict:
    shape(value, "hardcover pdf", path)
    text(value["hardcover"], path + ".hardcover", pattern=PRODUCT_HARDCOVER_ID)
    text(value["pdf"], path + ".pdf", pattern=PRODUCT_PDF_ID)
    require(value["hardcover"].removesuffix("-HC") == value["pdf"].removesuffix("E"),
            "evidence_mismatch", path)
    return value


def _notice_locator(value, path: str) -> dict:
    shape(value, "printed_page pdf_page", path)
    for field in ("printed_page", "pdf_page"):
        integer(value[field], 1, 9999, path + "." + field)
    return value


def _errata_locator(value, path: str) -> dict:
    shape(value, "release_label printing_label pages entry_type", path)
    for field in ("release_label", "printing_label"):
        label = text(value[field], path + "." + field)
        require(len(label) <= MAX_LOCATOR_LABEL and not any(c in label for c in "\u0085\u2028\u2029"),
                "invalid_value", path + "." + field)
    pages = sequence(value["pages"], path + ".pages")
    require(1 <= len(pages) <= MAX_ERRATA_PAGES, "limit_exceeded", path + ".pages")
    for index, page in enumerate(pages):
        integer(page, 1, 9999, f"{path}.pages[{index}]")
    require(pages == sorted(set(pages)), "invalid_value", path + ".pages")
    choice(value["entry_type"], ERRATA_ENTRY_TYPES, path + ".entry_type")
    return value


def _artifact_observation(value, product_ids: dict, path: str) -> dict:
    shape(value, "status product_id sha256 retained", path)
    status = choice(value["status"], ARTIFACT_STATUSES, path + ".status")
    require(type(value["retained"]) is bool, "invalid_type", path + ".retained")
    require(value["retained"] is False, "invalid_observation", path + ".retained")
    if status == "hash_observed":
        require(value["product_id"] == product_ids["pdf"], "evidence_mismatch", path + ".product_id")
        _sha256(value["sha256"], path + ".sha256")
    else:
        require(value["product_id"] is None and value["sha256"] is None, "invalid_observation", path)
    return value


def _unverified_observation(value, path: str) -> dict:
    shape(value, "status designation", path)
    choice(value["status"], UNVERIFIED_STATUSES, path + ".status")
    require(value["designation"] is None, "invalid_observation", path + ".designation")
    return value


def _legal_notice_observation(value, artifact: dict, path: str) -> dict:
    shape(value, "status family notice_locator", path)
    status = choice(value["status"], LEGAL_NOTICE_STATUSES, path + ".status")
    if status == "observed":
        require(artifact["status"] == "hash_observed", "evidence_mismatch", path)
        choice(value["family"], LEGAL_NOTICE_FAMILIES, path + ".family")
        _notice_locator(value["notice_locator"], path + ".notice_locator")
    else:
        require(artifact["status"] == "missing" and value["family"] is None
                and value["notice_locator"] is None, "invalid_observation", path)
    return value


def _errata_key(locator: dict) -> tuple:
    return (locator["release_label"], locator["printing_label"], locator["entry_type"], tuple(locator["pages"]))


def _errata_observation(value, path: str) -> dict:
    shape(value, "inventory_status applicability_status official_url captured_at response_sha256 locators", path)
    status = choice(value["inventory_status"], ERRATA_INVENTORY_STATUSES, path + ".inventory_status")
    choice(value["applicability_status"], ERRATA_APPLICABILITY_STATUSES, path + ".applicability_status")
    https_url(value["official_url"], path + ".official_url")
    require(value["official_url"] == OFFICIAL_ERRATA_URL, "evidence_mismatch", path + ".official_url")
    locators = sequence(value["locators"], path + ".locators")
    require(len(locators) <= MAX_ERRATA_LOCATORS, "limit_exceeded", path + ".locators")
    if status == "complete_as_of_capture":
        timestamp(value["captured_at"], path + ".captured_at")
        _sha256(value["response_sha256"], path + ".response_sha256")
        require(bool(locators), "invalid_observation", path + ".locators")
        for index, locator in enumerate(locators):
            _errata_locator(locator, f"{path}.locators[{index}]")
        keys = [_errata_key(locator) for locator in locators]
        require(keys == sorted(set(keys)), "invalid_value", path + ".locators")
    else:
        require(value["captured_at"] is None and value["response_sha256"] is None
                and locators == [], "invalid_observation", path)
    return value


def _blockers(value, allowed, path: str) -> list[str]:
    normalized = strings(value, path)
    require(value == normalized and bool(normalized) and set(normalized) <= set(allowed),
            "invalid_value", path)
    return normalized


def _downstream_compliance(value, path: str) -> dict:
    shape(value, "status blockers", path)
    choice(value["status"], DOWNSTREAM_COMPLIANCE_STATUSES, path + ".status")
    blockers = _blockers(value["blockers"], DOWNSTREAM_BLOCKERS, path + ".blockers")
    require(blockers == list(DOWNSTREAM_BLOCKERS), "evidence_mismatch", path + ".blockers")
    return value


def _review_readiness(value, dossier: dict, path: str) -> dict:
    shape(value, "status blockers", path)
    choice(value["status"], REVIEW_READINESS_STATUSES, path + ".status")
    blockers = _blockers(value["blockers"], READINESS_BLOCKERS, path + ".blockers")
    expected = {"named-reviewer-missing"}
    if dossier["artifact_observation"]["status"] == "missing":
        expected.add("final-artifact-missing")
    if dossier["legal_notice_observation"]["status"] == "missing":
        expected.add("final-legal-notice-missing")
    for field, code in (("printing_observation", "printing-unverified"),
                        ("revision_observation", "revision-unverified")):
        if dossier[field]["status"] == "unverified":
            expected.add(code)
    if dossier["errata_observation"]["inventory_status"] == "unverified":
        expected.add("errata-inventory-unverified")
    if dossier["errata_observation"]["applicability_status"] == "unresolved":
        expected.add("errata-applicability-unresolved")
    if dossier["downstream_compliance"]["status"] == "missing":
        expected.add("downstream-compliance-missing")
    require(blockers == sorted(expected), "evidence_mismatch", path + ".blockers")
    return value


def _dossier(document: dict, path: str) -> dict:
    bounded_json(document)
    value = deepcopy(document)
    shape(value, "schema_version evidence_id source_id source_evidence_id source_dossier_sha256 "
          "product_ids artifact_observation printing_observation revision_observation "
          "legal_notice_observation errata_observation downstream_compliance review_readiness "
          "supported_review_scopes", path)
    integer(value["schema_version"], CLASS_LEGAL_READINESS_SCHEMA_VERSION,
            CLASS_LEGAL_READINESS_SCHEMA_VERSION, path + ".schema_version")
    text(value["source_id"], path + ".source_id", pattern=SOURCE_ID)
    require(value["source_id"] in EXACT_SOURCE_ROSTER, "source_mismatch", path + ".source_id")
    text(value["evidence_id"], path + ".evidence_id", pattern=EVIDENCE_ID)
    text(value["source_evidence_id"], path + ".source_evidence_id", pattern=SOURCE_EVIDENCE_ID)
    slug = value["source_id"].removeprefix("pf2e.source.")
    require(value["evidence_id"] == "paizo.class-legal-readiness." + slug,
            "evidence_mismatch", path + ".evidence_id")
    require(value["source_evidence_id"] == "paizo.class-source." + slug,
            "evidence_mismatch", path + ".source_evidence_id")
    _sha256(value["source_dossier_sha256"], path + ".source_dossier_sha256")
    _product_ids(value["product_ids"], path + ".product_ids")
    _artifact_observation(value["artifact_observation"], value["product_ids"], path + ".artifact_observation")
    _unverified_observation(value["printing_observation"], path + ".printing_observation")
    _unverified_observation(value["revision_observation"], path + ".revision_observation")
    _legal_notice_observation(value["legal_notice_observation"], value["artifact_observation"], path + ".legal_notice_observation")
    _errata_observation(value["errata_observation"], path + ".errata_observation")
    _downstream_compliance(value["downstream_compliance"], path + ".downstream_compliance")
    _review_readiness(value["review_readiness"], value, path + ".review_readiness")
    scopes = strings(value["supported_review_scopes"], path + ".supported_review_scopes")
    require(scopes == list(SUPPORTED_REVIEW_SCOPES), "invalid_value", path + ".supported_review_scopes")
    return value


def _manifest(document: dict) -> dict:
    bounded_json(document)
    value = deepcopy(document)
    path = "$.manifest"
    shape(value, "schema_version bundle_id scope authority created_at class_review_overlay_id "
          "class_review_overlay_hash source_bundle_id source_bundle_hash counts dossiers bundle_hash", path)
    integer(value["schema_version"], CLASS_LEGAL_READINESS_SCHEMA_VERSION,
            CLASS_LEGAL_READINESS_SCHEMA_VERSION, path + ".schema_version")
    text(value["bundle_id"], path + ".bundle_id", pattern=BUNDLE_ID)
    iso_date(value["bundle_id"].removeprefix("pf2e-class-legal-readiness-").split(".")[0], path + ".bundle_id")
    choice(value["scope"], {CLASS_LEGAL_READINESS_SCOPE}, path + ".scope")
    choice(value["authority"], {CLASS_LEGAL_READINESS_AUTHORITY}, path + ".authority")
    timestamp(value["created_at"], path + ".created_at")
    for field, expected in (("class_review_overlay_id", PRODUCTION_CLASS_REVIEW_OVERLAY_ID),
                            ("class_review_overlay_hash", PRODUCTION_CLASS_REVIEW_OVERLAY_HASH),
                            ("source_bundle_id", PRODUCTION_SOURCE_BUNDLE_ID),
                            ("source_bundle_hash", PRODUCTION_SOURCE_BUNDLE_HASH)):
        text(value[field], path + "." + field)
        if field.endswith("hash"):
            _sha256(value[field], path + "." + field)
        require(value[field] == expected, "binding_mismatch", path + "." + field)
    _sha256(value["bundle_hash"], path + ".bundle_hash")
    shape(value["counts"], " ".join(COUNT_FIELDS), path + ".counts")
    for field in COUNT_FIELDS:
        integer(value["counts"][field], 0, MAX_CLASS_LEGAL_READINESS_DOSSIERS, path + ".counts." + field)
    references = sequence(value["dossiers"], path + ".dossiers")
    require(len(references) == MAX_CLASS_LEGAL_READINESS_DOSSIERS, "count_mismatch", path + ".dossiers")
    seen = {field: set() for field in ("evidence_id", "source_id", "path", "sha256")}
    for index, reference in enumerate(references):
        refpath = f"{path}.dossiers[{index}]"
        shape(reference, "evidence_id source_id path sha256", refpath)
        text(reference["source_id"], refpath + ".source_id", pattern=SOURCE_ID)
        text(reference["evidence_id"], refpath + ".evidence_id", pattern=EVIDENCE_ID)
        _relative_dossier_path(reference["path"], refpath + ".path")
        _sha256(reference["sha256"], refpath + ".sha256")
        for field in seen:
            require(reference[field] not in seen[field], "duplicate_id", refpath + "." + field)
            seen[field].add(reference[field])
        slug = reference["source_id"].removeprefix("pf2e.source.")
        require(reference["path"] == f"dossiers/{slug}.json"
                and reference["evidence_id"] == "paizo.class-legal-readiness." + slug,
                "evidence_mismatch", refpath)
    require(seen["source_id"] == EXACT_SOURCE_ROSTER, "source_mismatch", path + ".dossiers")
    references.sort(key=lambda item: item["source_id"])
    return value


def _normalized_parts(path: Path) -> tuple[str, ...]:
    require(0 < len(path.parts) <= MAX_PATH_COMPONENTS, "path_overlap", "$.paths")
    return tuple(unicodedata.normalize("NFC", unicodedata.normalize("NFC", part).casefold())
                 for part in path.parts)


def _prefix_overlap(left: tuple, right: tuple) -> bool:
    length = min(len(left), len(right))
    return left[:length] == right[:length]


def _safe_info(info, *, directory: bool) -> bool:
    return (not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)))


def _identity_suffixes(path: Path) -> dict:
    """Bounded ancestor identities distinguish true siblings from aliases."""
    identities = {}
    suffix = ()
    current = path
    for _ in range(MAX_PATH_COMPONENTS):
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        else:
            require(_safe_info(info, directory=stat.S_ISDIR(info.st_mode)), "path_overlap", "$.paths")
            identities.setdefault((info.st_dev, info.st_ino), []).append(suffix)
        if current.parent == current:
            return identities
        suffix = (_normalized_parts(current)[-1], *suffix)
        current = current.parent
    raise RulesValidationError("path_overlap", "$.paths")


def _require_disjoint_trees(roots: tuple[Path, ...]) -> None:
    resolved = []
    for root in roots:
        _normalized_parts(root)
        reject_links(root)
        resolved.append(root.resolve(strict=False))
    identities = [_identity_suffixes(root) for root in resolved]
    for index, left in enumerate(roots):
        for right_index in range(index + 1, len(roots)):
            right = roots[right_index]
            require(not _prefix_overlap(_normalized_parts(left), _normalized_parts(right))
                    and not _prefix_overlap(_normalized_parts(resolved[index]), _normalized_parts(resolved[right_index])),
                    "path_overlap", "$.paths")
            for identity in identities[index].keys() & identities[right_index].keys():
                require(not any(_prefix_overlap(a, b) for a in identities[index][identity]
                                for b in identities[right_index][identity]), "path_overlap", "$.paths")


def _inventory(root: Path, expected: frozenset[str]) -> None:
    reject_links(root)
    require(_safe_info(root.lstat(), directory=True), "invalid_package_layout", "$files")
    names = set()
    with os.scandir(root) as entries:
        for entry in entries:
            require(len(names) < len(expected), "invalid_package_layout", "$files")
            require(entry.name in expected, "invalid_package_layout", "$files")
            names.add(entry.name)
    require(names == expected, "invalid_package_layout", "$files")


def _preflight_class_legal_readiness_inputs(
    manifest_path: Path,
    class_review_overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    parent_overlay_path: Path,
    source_evidence_manifest_path: Path,
) -> None:
    """Reject unsafe layouts and shared identities before any payload read."""
    readiness_root = manifest_path.absolute().parent
    overlay_root = class_review_overlay_path.absolute()
    snapshot_root = snapshot_manifest_path.absolute().parent
    corpus_tree = corpus_root.absolute()
    parent_root = parent_overlay_path.absolute()
    source_root = source_evidence_manifest_path.absolute().parent
    try:
        _require_disjoint_trees((readiness_root, overlay_root, snapshot_root, corpus_tree, parent_root, source_root))
        for root in (snapshot_root, corpus_tree):
            require(_safe_info(root.lstat(), directory=True), "invalid_package_layout", "$files")
        require(manifest_path.absolute() == readiness_root / "manifest.json"
                and source_evidence_manifest_path.absolute() == source_root / "manifest.json",
                "invalid_package_layout", "$files")
        leaves = [snapshot_manifest_path.absolute()]
        for root in (readiness_root, source_root):
            _inventory(root, frozenset({"manifest.json", "dossiers"}))
            _inventory(root / "dossiers", frozenset(f"{slug}.json" for slug in SOURCE_SLUGS))
            leaves.extend(root / name for name in ("manifest.json", *sorted(DOSSIER_FILES)))
        for root in (overlay_root, parent_root):
            _inventory(root, OVERLAY_FILES)
            leaves.extend(root / name for name in sorted(OVERLAY_FILES))
        identities = set()
        for leaf in leaves:
            reject_links(leaf)
            info = leaf.lstat()
            require(_safe_info(info, directory=False), "invalid_package_layout", "$files")
            identity = (info.st_dev, info.st_ino)
            require(info.st_nlink == 1 and identity not in identities, "path_overlap", "$.paths")
            identities.add(identity)
    except RulesValidationError:
        raise
    except (OSError, RuntimeError):
        raise RulesValidationError("invalid_package_layout", "$files") from None


def _read_layout(manifest_path: Path) -> tuple[bytes, dict[str, bytes]]:
    try:
        return read_file(manifest_path), {relative: read_file(manifest_path.parent / relative)
                                          for relative in sorted(DOSSIER_FILES)}
    except OSError:
        raise RulesValidationError("invalid_package_layout", "$files") from None


def _counts(dossiers: list[dict]) -> dict:
    def count(field, key, status):
        return sum(dossier[field][key] == status for dossier in dossiers)
    return dict(zip(COUNT_FIELDS, (
        len(dossiers), count("artifact_observation", "status", "hash_observed"),
        count("artifact_observation", "status", "missing"),
        count("legal_notice_observation", "status", "observed"),
        count("legal_notice_observation", "status", "missing"),
        count("printing_observation", "status", "unverified"),
        count("revision_observation", "status", "unverified"),
        count("errata_observation", "inventory_status", "complete_as_of_capture"),
        count("errata_observation", "inventory_status", "unverified"),
        count("review_readiness", "status", "ready"), count("review_readiness", "status", "blocked"),
        0, 0,
    )))


def _bind_dossier(dossier: dict, upstream: dict, upstream_hash: str, path: str) -> None:
    for field, expected in (("source_id", upstream["source_id"]),
                            ("source_evidence_id", upstream["evidence_id"]),
                            ("source_dossier_sha256", upstream_hash),
                            ("product_ids", upstream["product_ids"]),
                            ("printing_observation", upstream["printing_observation"]),
                            ("revision_observation", upstream["revision_observation"])):
        require(dossier[field] == expected, "evidence_mismatch", path + "." + field)
    artifact = deepcopy(upstream["artifact_observation"])
    locator = artifact.pop("notice_locator")
    if artifact["status"] == "not_captured":
        artifact["status"] = "missing"
    require(dossier["artifact_observation"] == artifact, "evidence_mismatch", path + ".artifact_observation")
    pc = dossier["source_id"] == "pf2e.source.player-core"
    expected_notice = {"status": "observed" if pc else "missing", "family": "orc" if pc else None,
                       "notice_locator": locator}
    require(dossier["legal_notice_observation"] == expected_notice, "evidence_mismatch", path + ".legal_notice_observation")
    require(dossier["errata_observation"]["inventory_status"] == ("complete_as_of_capture" if pc else "unverified"),
            "evidence_mismatch", path + ".errata_observation.inventory_status")
    if pc:
        require(artifact == {"status": "hash_observed", "product_id": "PZO12001E",
                             "sha256": PRODUCTION_PLAYER_CORE_ARTIFACT_HASH, "retained": False}
                and locator == PRODUCTION_PLAYER_CORE_NOTICE_LOCATOR, "evidence_mismatch", path)
    else:
        require(artifact == {"status": "missing", "product_id": None, "sha256": None, "retained": False}
                and locator is None, "evidence_mismatch", path)


def verify_class_legal_readiness_bundle(
    manifest_path: Path,
    class_review_overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    expected_hash: str | None = None,
    *,
    parent_overlay_path: Path,
    source_evidence_manifest_path: Path,
) -> dict:
    """Verify canonical readiness bytes and their entire frozen upstream chain."""
    manifest_path = Path(manifest_path)
    class_review_overlay_path = Path(class_review_overlay_path)
    snapshot_manifest_path = Path(snapshot_manifest_path)
    corpus_root = Path(corpus_root)
    parent_overlay_path = Path(parent_overlay_path)
    source_evidence_manifest_path = Path(source_evidence_manifest_path)
    _preflight_class_legal_readiness_inputs(manifest_path, class_review_overlay_path, snapshot_manifest_path,
                                          corpus_root, parent_overlay_path, source_evidence_manifest_path)
    try:
        overlay = verify_class_review_overlay(
            class_review_overlay_path, snapshot_manifest_path, corpus_root,
            expected_hash=PRODUCTION_CLASS_REVIEW_OVERLAY_HASH,
            parent_overlay_path=parent_overlay_path, evidence_manifest_path=source_evidence_manifest_path,
        )
        source = verify_class_source_evidence_bundle(source_evidence_manifest_path,
                                                    expected_hash=PRODUCTION_SOURCE_BUNDLE_HASH)
        raw_manifest, files = _read_layout(manifest_path)
        manifest = _manifest(read_json(raw_manifest))
        require(raw_manifest == canonical_json(manifest), "noncanonical_bytes", "$.files.manifest.json")
        unhashed = deepcopy(manifest)
        bundle_hash = unhashed.pop("bundle_hash")
        require(digest(canonical_json(unhashed)) == bundle_hash, "integrity_mismatch", "$.files.manifest.json")
        require(manifest["class_review_overlay_id"] == overlay["overlay_id"]
                and manifest["class_review_overlay_hash"] == overlay["overlay_hash"]
                and manifest["source_bundle_id"] == source["manifest"]["bundle_id"]
                and manifest["source_bundle_hash"] == source["manifest"]["bundle_hash"],
                "binding_mismatch", "$.manifest")
        upstream_hashes = {ref["source_id"]: ref["sha256"] for ref in source["manifest"]["dossiers"]}
        by_hash, by_source = {}, {}
        for index, reference in enumerate(manifest["dossiers"]):
            path = f"$.dossiers[{index}]"
            file_path = "$.files." + reference["path"]
            raw = files[reference["path"]]
            dossier = _dossier(read_json(raw), path)
            require(raw == canonical_json(dossier), "noncanonical_bytes", file_path)
            dossier_hash = digest(raw)
            require(dossier_hash == reference["sha256"], "integrity_mismatch", file_path)
            require(dossier["source_id"] == reference["source_id"]
                    and dossier["evidence_id"] == reference["evidence_id"], "evidence_mismatch", path)
            _bind_dossier(dossier, source["dossiers_by_source"][reference["source_id"]],
                          upstream_hashes[reference["source_id"]], path)
            by_hash[dossier_hash] = deepcopy(dossier)
            by_source[dossier["source_id"]] = deepcopy(dossier)
        require(manifest["counts"] == _counts(list(by_source.values())), "count_mismatch", "$.manifest.counts")
        if expected_hash is not None:
            _sha256(expected_hash, "$.binding.bundle_hash")
            require(expected_hash == bundle_hash, "binding_mismatch", "$.binding.bundle_hash")
        return {"manifest": deepcopy(manifest), "dossiers_by_hash": by_hash, "dossiers_by_source": by_source}
    except OSError:
        raise RulesValidationError("invalid_package_layout", "$files") from None


def class_legal_readiness_report(bundle: dict) -> dict:
    """Return detached counts and source-ordered blocker codes, without any I/O."""
    # Each document is bounded separately: the two detached indexes deliberately
    # contain the same dossiers and must not double the per-document node budget.
    shape(bundle, "manifest dossiers_by_hash dossiers_by_source", "$")
    manifest = _manifest(bundle["manifest"])
    unhashed = deepcopy(manifest)
    bundle_hash = unhashed.pop("bundle_hash")
    require(digest(canonical_json(unhashed)) == bundle_hash,
            "integrity_mismatch", "$.manifest.bundle_hash")
    by_source = bundle["dossiers_by_source"]
    by_hash = bundle["dossiers_by_hash"]
    require(type(by_source) is dict, "invalid_type", "$.dossiers_by_source")
    require(type(by_hash) is dict, "invalid_type", "$.dossiers_by_hash")
    require(set(by_source) == EXACT_SOURCE_ROSTER, "source_mismatch", "$.dossiers_by_source")
    require(set(by_hash) == {reference["sha256"] for reference in manifest["dossiers"]},
            "evidence_mismatch", "$.dossiers_by_hash")
    dossiers = []
    for index, reference in enumerate(manifest["dossiers"]):
        path = f"$.dossiers[{index}]"
        dossier = _dossier(by_source[reference["source_id"]], path)
        hashed_dossier = _dossier(by_hash[reference["sha256"]], f"$.dossiers_by_hash[{index}]")
        require(dossier["source_id"] == reference["source_id"]
                and dossier["evidence_id"] == reference["evidence_id"], "evidence_mismatch", path)
        require(dossier == hashed_dossier, "evidence_mismatch", f"$.dossiers_by_hash[{index}]")
        require(digest(canonical_json(dossier)) == reference["sha256"],
                "integrity_mismatch", path)
        dossiers.append(dossier)
    counts = _counts(dossiers)
    require(manifest["counts"] == counts, "count_mismatch", "$.manifest.counts")
    return {"bundle_id": manifest["bundle_id"], "bundle_hash": manifest["bundle_hash"], "counts": counts,
            "sources": [{"source_id": dossier["source_id"], "blockers": deepcopy(dossier["review_readiness"]["blockers"])}
                        for dossier in dossiers]}
