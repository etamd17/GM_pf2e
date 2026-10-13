"""Pure, exact, one-level Cosmere advancement transactions."""
from copy import deepcopy
import uuid

import pytest

from systems.cosmere import SKILL_ATTR, SURGE_SKILLS
from systems.cosmere.origins import path_key_talent
from systems.cosmere.stormlight_build import (ATTR_INCREASE_LEVELS, TIER_START_LEVELS,
                                               max_skill_rank)


PID = "3520ca4cb56243f095ec8a5209174a68"
ATTRS = ("str", "spd", "int", "wil", "awa", "pre")
SKILLS = tuple(code for code in SKILL_ATTR if code not in SURGE_SKILLS)


def creation_build():
    return {
        "name": "Progression", "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1, "prc": 1, "sur": 1},
        "talents": [path_key_talent("warrior"), {"id": "test.creation-bonus", "name": "Creation Bonus"}],
        "expertises": [], "epic_choices": [],
    }


def document(build=None, *, runtime=None):
    from systems.cosmere.documents import normalize_character_document
    return normalize_character_document({"id": PID, "system": "cosmere",
        "name": "Progression", "build": build or creation_build(),
        "runtime_state": runtime or {"health": 4, "focus": 1}}, actor_id=PID)


def add_skill_ranks(build, count):
    skills = dict(build.get("skills") or {})
    cap = max_skill_rank(build["level"])
    for _ in range(count):
        code = next(code for code in SKILLS if skills.get(code, 0) < cap)
        skills[code] = skills.get(code, 0) + 1
    build["skills"] = skills


def candidate(previous, target, *, epic=None, first_ideal=False, bonus_talents=0):
    build = deepcopy(previous)
    build["level"] = target
    if target in ATTR_INCREASE_LEVELS:
        key = next(key for key in ATTRS if build["attributes"][key] < 5)
        build["attributes"][key] += 1
    talents = 1 if target <= 20 else 0
    skill_ranks = 2 if target <= 20 else 0
    if target > 20:
        epic = epic or ("skill" if target % 2 else "talent")
        build.setdefault("epic_choices", []).append(epic)
        skill_ranks += int(epic == "skill")
        talents += int(epic == "talent")
    if target in TIER_START_LEVELS:
        talents += 1
    if first_ideal:
        build.update(radiant_order="windrunners", ideals_sworn=1, is_radiant=True)
        build.setdefault("skills", {})["adh"] = build.get("skills", {}).get("adh", 0) + 1
        build.setdefault("skills", {})["grv"] = build.get("skills", {}).get("grv", 0) + 1
    add_skill_ranks(build, skill_ranks)
    for index in range(talents + bonus_talents):
        build.setdefault("talents", []).append({
            "id": f"test.level-{target}-talent-{index}",
            "name": f"Level {target} Talent {index}",
        })
    return build


def request(doc, build, *, goal_ids=(), request_id=None):
    return {"expected_revision": doc["revision"], "target_level": build["level"],
            "build": build, "goal_ids": list(goal_ids),
            "request_id": request_id or uuid.uuid4().hex}


def homebrew_store():
    return {
        "surge": [{"type": "surge", "name": "Sandmastery", "code": "snd",
                   "attribute": "wil"}],
        "radiant_path": [{"type": "radiant_path", "name": "Sandbearer",
                           "slug": "sandbearer", "spren": "Sandspren",
                           "surges": ["snd", "grv"], "philosophy": "Endure."}],
        "talent": [{"type": "talent", "id": "hb:sand-heart",
                    "name": "Sand Heart", "path": "any",
                    "effects": [{"target": "health", "value": 3}]}],
    }


def test_golden_exact_advancement_for_levels_2_through_22():
    from systems.cosmere.advancement import commit_advancement, preview_advancement

    doc = document()
    for level in range(2, 23):
        build = candidate(doc["build"], level)
        before = deepcopy(doc)
        preview = preview_advancement(doc, request(doc, build))
        assert doc == before
        assert preview["eligible"] is True, (level, preview)
        doc = commit_advancement(doc, request(doc, build))
        assert doc["revision"] == level - 1
        assert doc["build"]["level"] == level
        assert doc["runtime_state"]["health"] == 4
        assert len(doc["advancement_history"]) == level - 1


@pytest.mark.parametrize("target", [6, 11, 16, 21])
def test_tier_transitions_require_the_ancestry_bonus_talent(target):
    from systems.cosmere.advancement import AdvancementError, commit_advancement

    build = creation_build()
    build["level"] = target - 1
    # The transaction validator is delta-based: prior historical allocations do
    # not need to be reconstructed just to prove this tier row's exact grants.
    doc = document(build)
    proposed = candidate(doc["build"], target)
    proposed["talents"].pop()
    with pytest.raises(AdvancementError, match="talent") as error:
        commit_advancement(doc, request(doc, proposed))
    assert error.value.code == "missing_required_grants"


def test_preview_reports_the_actor_specific_health_grant_at_a_tier_transition():
    from systems.cosmere.advancement import commit_advancement, preview_advancement
    from systems.cosmere.stormlight_build import health_gain_at

    doc = document()
    for level in range(2, 6):
        proposed = candidate(doc["build"], level)
        doc = commit_advancement(doc, request(doc, proposed))
    proposed = candidate(doc["build"], 6)
    preview = preview_advancement(doc, request(doc, proposed))

    assert preview["grants"]["health"] == health_gain_at(
        6, proposed["attributes"]["str"])


def test_first_ideal_free_surge_ranks_are_required_beyond_level_grants():
    from systems.cosmere.advancement import AdvancementError, preview_advancement

    doc = document()
    proper = candidate(doc["build"], 2, first_ideal=True)
    assert preview_advancement(doc, request(doc, proper))["eligible"] is True
    missing = deepcopy(proper)
    missing["skills"].pop("grv")
    with pytest.raises(AdvancementError, match="Surge|skill"):
        preview_advancement(doc, request(doc, missing))


def test_missing_grant_stale_revision_replay_and_multi_level_are_rejected():
    from systems.cosmere.advancement import AdvancementError, commit_advancement

    doc = document()
    proposed = candidate(doc["build"], 2)
    proposed["skills"]["agi"] -= 1
    with pytest.raises(AdvancementError) as missing:
        commit_advancement(doc, request(doc, proposed))
    assert missing.value.code == "missing_required_grants"

    proposed = candidate(doc["build"], 2)
    stale = request(doc, proposed)
    stale["expected_revision"] = 99
    with pytest.raises(AdvancementError) as conflict:
        commit_advancement(doc, stale)
    assert conflict.value.code == "stale_revision" and conflict.value.status == 409
    multi = request(doc, deepcopy(proposed))
    multi["target_level"] = multi["build"]["level"] = 3
    with pytest.raises(AdvancementError, match="one level"):
        commit_advancement(doc, multi)

    replay_request = request(doc, proposed, request_id=uuid.uuid4().hex)
    advanced = commit_advancement(doc, replay_request)
    retry = {**replay_request, "expected_revision": advanced["revision"]}
    with pytest.raises(AdvancementError) as replay:
        commit_advancement(advanced, retry)
    assert replay.value.code == "replay_detected" and replay.value.status == 409


def test_completed_goal_grant_is_consumed_once_and_revert_applies_inverse_event():
    from systems.cosmere.advancement import (AdvancementError, commit_advancement,
                                             revert_advancement)
    from systems.cosmere.documents import apply_revisioned_mutation

    runtime = {"health": 4, "goals": [{"id": "goal.one", "completed": True,
                                        "rewards": {"talent": 1}}]}
    doc = document(runtime=runtime)
    proposed = candidate(doc["build"], 2, bonus_talents=1)
    advanced = commit_advancement(doc, request(doc, proposed, goal_ids=("goal.one",)))
    assert advanced["runtime_state"]["goals"][0]["consumed_by"] == \
        advanced["advancement_history"][-1]["id"]
    assert advanced["advancement_history"][-1]["consumed_goal_ids"] == ["goal.one"]
    assert advanced["advancement_history"][-1]["choices"]["goal_rewards"][
        "goal.one"]["talents"] == 1

    proposed_again = candidate(advanced["build"], 3, bonus_talents=1)
    with pytest.raises(AdvancementError) as consumed:
        commit_advancement(advanced, request(advanced, proposed_again, goal_ids=("goal.one",)))
    assert consumed.value.code == "goal_unavailable"

    # An unrelated runtime tick after advancement is preserved because revert
    # applies recorded inverse operations instead of restoring a whole snapshot.
    ticked = apply_revisioned_mutation(
        advanced, expected_revision=advanced["revision"],
        transform=lambda current: {
            **current,
            "runtime_state": {**current["runtime_state"], "health": 2},
        },
    )
    reverted = revert_advancement(ticked, {
        "expected_revision": ticked["revision"], "request_id": uuid.uuid4().hex})
    assert reverted["build"] == doc["build"]
    assert reverted["runtime_state"]["health"] == 2
    assert "consumed_by" not in reverted["runtime_state"]["goals"][0]
    assert reverted["revision"] == ticked["revision"] + 1
    event = reverted["advancement_history"][-1]
    assert event["action"] == "revert"
    assert event["reverts_event_id"] == advanced["advancement_history"][-1]["id"]


def test_profile_prerequisites_and_package_pin_are_authoritative():
    from systems.cosmere.advancement import AdvancementError, commit_advancement

    doc = document()
    proposed = candidate(doc["build"], 2)
    proposed["rules_binding"]["enabled_packs"][0]["package_hash"] = "0" * 64
    with pytest.raises(AdvancementError, match="binding|package"):
        commit_advancement(doc, request(doc, proposed))

    proposed = candidate(doc["build"], 2)
    proposed["talents"].append({"id": "QK47QQJLUvv3dSOL", "name": "Anatomical Insight"})
    proposed["talents"].pop(2)  # preserve the exact talent count but introduce a gated talent
    with pytest.raises(AdvancementError, match="needs"):
        commit_advancement(doc, request(doc, proposed))


def test_advancement_preserves_homebrew_skill_ranks_and_homebrew_derived_values():
    from systems.cosmere.advancement import commit_advancement
    from systems.cosmere.documents import normalize_character_document

    build = creation_build()
    build.update(radiant_order="sandbearer", ideals_sworn=1, is_radiant=True)
    build["skills"].update(snd=1, grv=1)
    build["talents"][-1] = {"id": "hb:sand-heart", "name": "Sand Heart"}
    doc = normalize_character_document(
        {"id": PID, "system": "cosmere", "build": build}, actor_id=PID,
        homebrew=homebrew_store())
    proposed = candidate(doc["build"], 2)

    advanced = commit_advancement(
        doc, request(doc, proposed), homebrew=homebrew_store())

    assert advanced["build"]["skills"]["snd"] == 1
    # L2 Health is 10 + STR 2 + the tier-1 gain 5, plus Sand Heart's +3.
    assert advanced["derived"]["health_max"] == 20


def test_advancement_preserves_owner_private_fields_and_never_audits_them():
    from systems.cosmere.advancement import commit_advancement

    build = creation_build()
    build["notes"] = "OWNER PRIVATE NOTE"
    build["session_notes"] = [{"date": "today", "text": "OWNER SESSION SECRET"}]
    doc = document(build)
    proposed = candidate(doc["build"], 2)
    proposed["notes"] = "FORGED REPLACEMENT"
    proposed.pop("session_notes")

    advanced = commit_advancement(doc, request(doc, proposed))

    assert advanced["build"]["notes"] == "OWNER PRIVATE NOTE"
    assert advanced["build"]["session_notes"] == [
        {"date": "today", "text": "OWNER SESSION SECRET"}]
    operations = advanced["advancement_history"][-1]["choices"]["operations"]
    assert not any(operation["path"][:2] in (
        ["build", "notes"], ["build", "session_notes"])
                   for operation in operations)
