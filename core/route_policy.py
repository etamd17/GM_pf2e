"""Declarative access-policy inventory for the Flask application.

This module is deliberately descriptive. Importing it does not register a
Flask hook, decorate a view, inspect a session, or enforce authorization. The
first remediation PR needs a reviewable map of the current route surface before
later PRs can safely centralize enforcement.

Policies are keyed by Flask endpoint name rather than URL text. Endpoint names
survive route aliases (``/m`` and ``/mobile`` both use ``mobile_combat``) and are
what Flask resolves before dispatch. A small method-override table handles the
few views whose read and write methods intentionally need different policies.

The registry describes the target minimum policy identified by the security
audit, not a claim that every route already enforces it correctly. That gap is
the work of subsequent remediation PRs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


_IMPLICIT_METHODS = frozenset({"HEAD", "OPTIONS"})


class RoutePolicy(str, Enum):
    """Minimum identity and scope required before a view may be dispatched."""

    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    SITE_ADMIN = "site_admin"
    CAMPAIGN_MEMBER = "campaign_member"
    CAMPAIGN_GM = "campaign_gm"
    CHARACTER_OWNER_OR_GM = "character_owner_or_gm"
    LIVE_CAMPAIGN_MEMBER = "live_campaign_member"
    LIVE_CAMPAIGN_GM = "live_campaign_gm"
    INTEGRATION_TOKEN = "integration_token"
    LIVE_CAMPAIGN_GM_OR_PUBLISH_TOKEN = "live_campaign_gm_or_publish_token"


PolicyGroup = tuple[RoutePolicy, Iterable[str]]
EndpointMethod = tuple[str, str]


def build_policy_registry(groups: Iterable[PolicyGroup]) -> Mapping[str, RoutePolicy]:
    """Build an immutable endpoint registry and reject ambiguous declarations."""

    registry: dict[str, RoutePolicy] = {}
    for policy, endpoints in groups:
        if not isinstance(policy, RoutePolicy):
            raise TypeError(f"invalid route policy: {policy!r}")
        for endpoint in endpoints:
            if not isinstance(endpoint, str) or not endpoint:
                raise ValueError(f"invalid Flask endpoint name: {endpoint!r}")
            if endpoint in registry:
                raise ValueError(
                    f"duplicate policy declaration for {endpoint!r}: "
                    f"{registry[endpoint].value} and {policy.value}"
                )
            registry[endpoint] = policy
    return MappingProxyType(registry)


# Grouping keeps the endpoint declarations reviewable while the builder still
# guarantees that every endpoint has exactly one default policy.
_POLICY_GROUPS: tuple[PolicyGroup, ...] = (
    (
        RoutePolicy.PUBLIC,
        (
            "api_campaign",  # GET is public; POST has a method override below.
            "gm_login",
            "gm_logout",
            "health_check",
            "index",
            "join",
            "login",
            "logout",
            "register",
            "service_worker",
            "setup",
            "static",
            "web_manifest",
        ),
    ),
    (
        RoutePolicy.AUTHENTICATED,
        (
            "account_home",
            "api_active_system",
            "api_my_campaigns",
            "campaign_import",
            "change_my_password",
            "new_campaign",
            "stop_active_campaign",
        ),
    ),
    (
        RoutePolicy.SITE_ADMIN,
        (
            "admin_campaigns",
            "admin_reset_password",
            "admin_set_campaign_system",
            "admin_users",
            "perf_metrics",
        ),
    ),
    (RoutePolicy.CAMPAIGN_MEMBER, ("activate_campaign",)),
    (
        RoutePolicy.CAMPAIGN_GM,
        (
            "backup_now",
            "campaign_backup_latest",
            "campaign_delete",
            "campaign_export",
            "campaign_invites",
            "campaign_mint_invite",
            "campaign_purge",
            "campaign_remove_member",
            "campaign_restore",
            "campaign_revoke_invite",
            "campaign_set_role",
            "campaign_set_system",
        ),
    ),
    (
        RoutePolicy.INTEGRATION_TOKEN,
        (
            "obsidian_sync.combatant_detail",
            "obsidian_sync.commands",
            "obsidian_sync.end_session",
            "obsidian_sync.events",
            "obsidian_sync.start_session",
            "obsidian_sync.state",
            "obsidian_sync.status",
        ),
    ),
    (
        RoutePolicy.LIVE_CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        (
            "chronicle_doc_api",
            "chronicle_docs_api",
            "chronicle_publish",
            "chronicle_rollback",
            "chronicle_status",
            "chronicle_unpublish",
        ),
    ),
    (
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        (
            "add_item",
            "add_pc_effect",
            "add_pet",
            "add_weapon",
            "adjust_consumable",
            "adjust_focus",
            "adjust_hero",
            "adjust_party_hp",
            "adjust_temp_hp",
            "api_cosmere_my_combat",
            "api_cosmere_my_initiative",
            "api_cosmere_my_speed",
            "api_cosmere_roll",
            "api_journal_append",
            "api_journal_delete",
            "api_journal_get",
            "api_notes",
            "api_pc_state",
            "cast_spell",
            "cosmere_pc_delete",
            "cosmere_pc_notes",
            "cosmere_pc_rest",
            "cosmere_pc_sheet",
            "cosmere_pc_state",
            "daily_preparations",
            "delete_session_note",
            "delete_weapon",
            "equip_armor",
            "export_character",
            "export_pdf",
            "forget_spell",
            "learn_spell",
            "levelup_validate",
            "list_pc_effects",
            "log_roll",
            "log_spell_cast",
            "long_rest",
            "pc_recovery_check",
            "pc_treat_wounds",
            "persistent_damage_add",
            "persistent_damage_flat_check",
            "persistent_damage_remove",
            "player_levelup",
            "player_sheet",
            "player_whisper",
            "recall_knowledge",
            "refocus",
            "remove_item",
            "remove_pc_effect",
            "remove_pet",
            "repair_shield",
            "revert_level",
            "save_notes",
            "save_session_note",
            "send_initiative",
            "session_journal_add",
            "session_journal_get",
            "session_notes_page",
            "set_exploration_activity",
            "set_focus_spells",
            "set_reaction",
            "set_shield_stats",
            "set_signature_spells",
            "shield_block",
            "spell_slots",
            "submit_levelup",
            "sync_spell_slots",
            "toggle_feature",
            "toggle_shield",
            "toggle_two_hand",
            "update_pc_condition",
            "update_portrait_focus",
            "update_sheet",
            "update_wealth",
            "upload_portrait",
        ),
    ),
    (
        RoutePolicy.LIVE_CAMPAIGN_MEMBER,
        (
            "api_chat_get",
            "api_chat_send",
            "api_cosmere_combat_state",
            "api_exploration_activities",
            "api_join_campaign",
            "api_leave_campaign",
            "api_safety",
            "api_safety_xcard",
            "api_plot_die",

            "chronicle_asset",
            "chronicle_home",
            "chronicle_journal",
            "chronicle_page",
            "chronicle_section",
            "compendium_detail",
            "compendium_search",
            "condition_info",
            "cosmere_builder",
            "cosmere_builder_preview",
            "cosmere_combat_view",
            "cosmere_import_pdf",
            "cosmere_pcs",
            "cosmere_player_hub",
            "get_combat_log",
            "get_handouts",
            "get_logs",
            "healing_log_get",
            "hero_nomination",
            "item_bulk",
            "mobile_combat",
            "pack_detail",
            "party_list",
            "party_view",
            "player_builder",
            "player_state",
            "player_view",
            "recall_knowledge_info",
            "serve_campaign_asset",
            "serve_handout_image",
            "serve_portrait",
            "sse_stream",
            "status_board",
        ),
    ),
    (
        RoutePolicy.LIVE_CAMPAIGN_GM,
        (
            "add_combatant",
            "add_cosmere_custom_adversary",
            "add_cosmere_party",
            "add_custom_monster",
            "add_party",
            "adjust_hp",
            "api_award_xp",
            "api_calendar_advance",
            "api_calendar_event",
            "api_calendar_get",
            "api_calendar_set",
            "api_campaign_crest",
            "api_campaign_free_archetype",
            "api_campaign_hero_image",
            "api_campaign_stats",
            "api_cosmere_combatant_condition",
            "api_cosmere_generate",
            "api_cosmere_hub_status",
            "api_cosmere_initiative_mode",
            "api_cosmere_loot",
            "api_cosmere_loot_add",
            "api_cosmere_loot_delete",
            "api_cosmere_world",
            "api_encounter_template_creatures",
            "api_generate",
            "api_list_pinned_generators",
            "api_loot_ledger",
            "api_loot_ledger_add",
            "api_loot_ledger_award",
            "api_loot_ledger_delete",
            "api_mark_ready",
            "api_modules_list",
            "api_modules_toggle",
            "api_monster_details",
            "api_monster_search",
            "api_monster_statblock",
            "api_party_stats",
            "api_pin_generator",
            "api_rest_apply",
            "api_session_boss_reveal",
            "api_session_mood",
            "api_session_roll_initiative",
            "api_set_advancement_mode",
            "api_skill_challenge_create",
            "api_skill_challenge_end",
            "api_skill_challenge_get",
            "api_skill_challenge_mark",
            "api_stage_encounter",
            "api_threads_upload",
            "api_tracker_state",
            "api_treat_wounds",
            "api_unpin_generator",
            "approve_hero_nomination",
            "chronicle_manage",
            "chronicle_preview",
            "clear_combat_log",
            "clear_encounter",
            "clear_gm_secret_log",
            "clear_log",
            "combatant_stats",
            "cosmere_bestiary",
            "cosmere_generator",
            "cosmere_gm_hub",
            "cosmere_gm_vitals",
            "cosmere_gmscreen",
            "cosmere_homebrew",
            "cosmere_homebrew_delete",
            "cosmere_homebrew_save",
            "cosmere_loot_view",
            "cosmere_pc_release",
            "cosmere_rest",
            "cosmere_sheet",
            "cosmere_speed_choice",
            "create_custom_monster",
            "create_handout",
            "create_round_event",
            "cycle_turn",
            "daily_preparations_all",
            "delay_turn",
            "delete_character",
            "delete_encounter",
            "delete_handout",
            "delete_round_event",
            "dm_generator",
            "encounter_builder",
            "encounter_notes_api",
            "get_full_log",
            "get_gm_secret_log",
            "gm_calendar",
            "gm_encounter_templates",
            "gm_hub",
            "gm_party_state",
            "gm_rest_wizard",
            "gm_screen",
            "gm_secret_roll",
            "gm_skill_challenge",
            "gm_stats",
            "gm_threads",
            "hazard_disable",
            "hazard_reset",
            "hazard_trigger",
            "import_monster",
            "import_pathbuilder",
            "list_encounters",
            "load_encounter",
            "load_stage",
            "loot_ledger_view",
            "multi_save_damage",
            "obsidian_sync.integration_page",
            "obsidian_sync.issue_token",
            "obsidian_sync.revoke_token",
            "recovery_check",
            "reenter_initiative",
            "remove_combatant",
            "reorder_initiative",
            "request_check_from_players",
            "restore_defeated",
            "roll_all_initiative",
            "roll_npc_initiative",
            "save_encounter",
            "save_new_character",
            "save_stage",
            "api_scene",
            "api_scene_activate",
            "api_scene_add_token",
            "api_scene_background",
            "api_scene_beacon",
            "api_scene_bulk_combat",
            "api_scene_combatant_action",
            "api_scene_delete",
            "api_scene_elements",
            "api_scene_sync_encounter",
            "api_scene_token",
            "api_scene_token_image",
            "api_scenes",
            "scene_map_gm",
            "scene_map_home",
            "scene_map_table",
            "send_loot_to_player",
            "session_timer",
            "set_combatant_epithet",
            "set_combatant_tactics",
            "set_persistent_damage",
            "sort_initiative",
            "toggle_combatant_visibility",
            "toggle_condition",
            "toggle_elite_weak",
            "tracker_view",
            "update_initiative",
            "update_round_event",
            "upload_handout_image",
            "use_action",
        ),
    ),
)


ROUTE_POLICIES = build_policy_registry(_POLICY_GROUPS)


# Most views have one minimum policy. This reviewed exception needs a stricter
# policy for mutation than for its read method.
METHOD_POLICY_OVERRIDES: Mapping[EndpointMethod, RoutePolicy] = MappingProxyType(
    {
        ("api_campaign", "POST"): RoutePolicy.LIVE_CAMPAIGN_GM,
    }
)


# Notes identify resource-level conditions and unresolved boundaries that an
# endpoint-level inventory cannot encode. The reporting tool emits them; no
# runtime code consumes them.
POLICY_NOTES: Mapping[str, str] = MappingProxyType(
    {
        "api_campaign": "GET is intentionally public; POST requires the live campaign GM.",
        "backup_now": (
            "The current route has no campaign id and snapshots every campaign; future "
            "enforcement must add explicit scope rather than trust any membership."
        ),
        "chronicle_doc_api": (
            "The current publish-token prefix accepts more than publish itself; a later "
            "PR should narrow token capabilities per operation."
        ),
        "chronicle_docs_api": (
            "The current publish-token prefix accepts document management operations; "
            "review whether headless publishing needs this breadth."
        ),
        "cosmere_builder": (
            "Campaign membership is the route baseline; editing an existing pc/id also "
            "requires character ownership or GM authority."
        ),
        "register": (
            "Public means unauthenticated entry point; production may disable open registration."
        ),
        "setup": "Public only during bootstrap and must also require the setup token.",
    }
)


@dataclass(frozen=True)
class RoutePolicyAudit:
    """Difference between the live Flask URL map and this declaration."""

    unclassified_endpoints: tuple[str, ...]
    stale_endpoints: tuple[str, ...]
    stale_method_overrides: tuple[EndpointMethod, ...]

    @property
    def ok(self) -> bool:
        return not (
            self.unclassified_endpoints
            or self.stale_endpoints
            or self.stale_method_overrides
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.unclassified_endpoints:
            parts.append("unclassified endpoints: " + ", ".join(self.unclassified_endpoints))
        if self.stale_endpoints:
            parts.append("stale policy endpoints: " + ", ".join(self.stale_endpoints))
        if self.stale_method_overrides:
            rendered = ", ".join(
                f"{endpoint}:{method}"
                for endpoint, method in self.stale_method_overrides
            )
            parts.append("stale method overrides: " + rendered)
        return "; ".join(parts) if parts else "route policy inventory is complete"


class RoutePolicyCoverageError(AssertionError):
    """Raised when a Flask URL map and policy registry do not match exactly."""


def _registered_endpoint_methods(url_map) -> set[EndpointMethod]:
    pairs: set[EndpointMethod] = set()
    for rule in url_map.iter_rules():
        for method in set(rule.methods or ()) - _IMPLICIT_METHODS:
            pairs.add((rule.endpoint, method.upper()))
    return pairs


def audit_url_map(
    url_map,
    *,
    policies: Mapping[str, RoutePolicy] = ROUTE_POLICIES,
    method_overrides: Mapping[EndpointMethod, RoutePolicy] = METHOD_POLICY_OVERRIDES,
) -> RoutePolicyAudit:
    """Return deterministic coverage differences for a Flask/Werkzeug URL map."""

    actual_pairs = _registered_endpoint_methods(url_map)
    actual_endpoints = {endpoint for endpoint, _method in actual_pairs}
    declared_endpoints = set(policies)
    return RoutePolicyAudit(
        unclassified_endpoints=tuple(sorted(actual_endpoints - declared_endpoints)),
        stale_endpoints=tuple(sorted(declared_endpoints - actual_endpoints)),
        stale_method_overrides=tuple(sorted(set(method_overrides) - actual_pairs)),
    )


def require_complete_inventory(
    url_map,
    *,
    policies: Mapping[str, RoutePolicy] = ROUTE_POLICIES,
    method_overrides: Mapping[EndpointMethod, RoutePolicy] = METHOD_POLICY_OVERRIDES,
) -> None:
    """Raise with a stable, review-friendly message when coverage has drifted."""

    audit = audit_url_map(
        url_map,
        policies=policies,
        method_overrides=method_overrides,
    )
    if not audit.ok:
        raise RoutePolicyCoverageError(audit.describe())


def policy_for(
    endpoint: str,
    method: str,
    *,
    policies: Mapping[str, RoutePolicy] = ROUTE_POLICIES,
    method_overrides: Mapping[EndpointMethod, RoutePolicy] = METHOD_POLICY_OVERRIDES,
) -> RoutePolicy | None:
    """Look up a declared policy without enforcing it."""

    method = method.upper()
    return method_overrides.get((endpoint, method), policies.get(endpoint))


@dataclass(frozen=True, order=True)
class RouteInventoryRow:
    endpoint: str
    method: str
    rule: str
    policy: str
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "rule": self.rule,
            "policy": self.policy,
            "note": self.note,
        }


def inventory_rows(
    url_map,
    *,
    policies: Mapping[str, RoutePolicy] = ROUTE_POLICIES,
    method_overrides: Mapping[EndpointMethod, RoutePolicy] = METHOD_POLICY_OVERRIDES,
) -> tuple[RouteInventoryRow, ...]:
    """Return one deterministically sorted row per explicit method and URL rule."""

    rows: list[RouteInventoryRow] = []
    for rule in url_map.iter_rules():
        for method in sorted(set(rule.methods or ()) - _IMPLICIT_METHODS):
            policy = policy_for(
                rule.endpoint,
                method,
                policies=policies,
                method_overrides=method_overrides,
            )
            rows.append(
                RouteInventoryRow(
                    endpoint=rule.endpoint,
                    method=method,
                    rule=str(rule),
                    policy=policy.value if policy else "UNCLASSIFIED",
                    note=POLICY_NOTES.get(rule.endpoint, ""),
                )
            )
    return tuple(sorted(rows))
