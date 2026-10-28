"""MB-08: a talent in a power's talent tree needs that power (synthetic rules only)."""
import pytest

from systems.cosmere.mistborn_build import MistbornBuild

ORPHAN = 'mistborn.feruchemy.iron.talent.test-orphan'
MIXED = 'mistborn.path.talent.test-mixed'
PATH_ONLY = 'mistborn.path.talent.test-path'
TALENT_RULES = {
    ORPHAN: {'id': ORPHAN, 'prerequisites': ('currently_tapping_weight',),
             'tree_memberships': ['feruchemy.iron']},
    MIXED: {'id': MIXED, 'prerequisites': (), 'tree_memberships': ['twinborn', 'allomancy.iron']},
    PATH_ONLY: {'id': PATH_ONLY, 'prerequisites': (), 'tree_memberships': ['misting']},
}
TREE_ISSUE = 'belongs to the talent tree of a power'


def _build(talent_id, *, feruchemy=None, entitlements=()):
    data = {
        'name': 'Tree test', 'level': 1, 'origin_world': 'scadrial', 'era': 'era-2',
        'ancestry': 'Human', 'starting_path': 'agent', 'heroic_paths': ['agent'],
        'metalborn_keys': ['mistborn.path.key.ferring-heritage'] if feruchemy else
                          ['mistborn.path.key.misting-snap'],
        'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2},
        'skills': {'feruchemy': 1} if feruchemy else {'allomancy': 1},
        'talents': [{'id': talent_id}], 'expertises': [],
        'allomantic_powers': {} if feruchemy else {'tin': 'nascent'},
        'feruchemical_powers': feruchemy or {},
        'goal_entitlements': list(entitlements),
    }
    return MistbornBuild(data, talent_rules=TALENT_RULES,
                         trusted_goal_entitlements=entitlements)


def _tree_issues(build):
    return [issue for issue in build.hard_violations() if TREE_ISSUE in issue]


def test_power_tree_talent_is_refused_without_the_power():
    assert _tree_issues(_build(ORPHAN)) == [
        f'Talent {ORPHAN} belongs to the talent tree of a power '
        'this character does not have unlocked.']


@pytest.mark.parametrize('state', ['nascent', 'goal_active'])
def test_a_power_that_is_not_yet_unlocked_does_not_open_its_tree(state):
    assert _tree_issues(_build(ORPHAN, feruchemy={'iron': state}))


def test_an_unlocked_power_opens_its_tree():
    entitlement = 'mistborn.entitlement.power.feruchemy.iron.unlock'
    build = _build(ORPHAN, feruchemy={'iron': 'unlocked'}, entitlements=(entitlement,))
    assert _tree_issues(build) == []


def test_talents_also_reachable_through_a_path_tree_are_left_to_their_prerequisites():
    assert _tree_issues(_build(MIXED)) == []
    assert _tree_issues(_build(PATH_ONLY)) == []


def test_without_catalog_rules_nothing_changes():
    build = MistbornBuild({'name': 'x', 'era': 'era-2', 'talents': [{'id': ORPHAN}]})
    assert _tree_issues(build) == []
