"""Ten-minute rest block: Treat Wounds / Refocus / shield Repair (queue #2).

Spec: docs/superpowers/specs/2026-07-07-ten-minute-rest-design.md
Plan: docs/superpowers/plans/2026-07-07-ten-minute-rest.md (Task 1)

Server-rolled activity routes -- this arc exists because the sheet's Treat
Wounds shipped as a client-side Math.random roll with RAW-divergent math
that applied nothing. RAW pinned from the local Foundry pack
(compendium_data/actions/skill/treat-wounds.json + repair.json):

  * Treat Wounds: DC 15 trained; expert/master/legendary may INSTEAD roll
    DC 20/30/40 for +10/+30/+50 healing (tier gated by the healer's rank).
    Success 2d8+bonus AND the target loses wounded; crit 4d8+bonus + loses
    wounded; crit failure: target takes 1d8 (through the real damage path,
    so dying-entry math applies). Target immune for 1 hour (enforced,
    override flag bypasses -- user-locked fork).
  * Refocus: +1 Focus Point up to max.
  * Repair: GM-set DC (default 15); success restores 5 + 5/Crafting rank,
    crit 10 + 10/rank, crit failure 2d6 minus the item's Hardness; can't
    Repair a destroyed shield.

Fixture ground truth (committed builds): Kyle Medicine M +21 / Crafting U
+2 / focus 3 of 3; Go'el Medicine T +17 / Crafting U +0 / shield 20 of 20,
BT 10, hardness 5.
"""
from __future__ import annotations

import json
import pathlib
import time

import pytest

import app as app_module
from app import Character

_FIX_DIR = pathlib.Path(__file__).parent / 'fixtures'
_AJAX = {'X-Requested-With': 'XMLHttpRequest'}


def _register(monkeypatch, fixture, tmp_path, fname):
    raw = json.loads((_FIX_DIR / fixture).read_text())
    pc_file = tmp_path / fname
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    pc = Character(raw, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, pc.name, pc)
    return pc, pc_file


@pytest.fixture
def duo(tmp_path, monkeypatch):
    """Kyle (healer, Medicine M +21) + Go'el (target, shield 20/20)."""
    kyle, kyle_file = _register(monkeypatch, 'kyle_l10.json', tmp_path, 'Kyle.json')
    goel, goel_file = _register(monkeypatch, 'goel_l10.json', tmp_path, 'Goel.json')
    files = {kyle.name: str(kyle_file), goel.name: str(goel_file)}
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda n: files.get(n))
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    # The healing log writes session_state.json, whose local-dev fallback is
    # the REPO directory -- point it at tmp so tests don't pollute the repo.
    monkeypatch.setattr(app_module, '_SESSION_STATE_PATH', str(tmp_path / 'session_state.json'))
    return {'kyle': kyle, 'goel': goel, 'files': files}


@pytest.fixture
def client():
    return app_module.app.test_client()


def _treat(client, healer, target=None, tier='trained', d20=None, override=False, dc=None):
    body = {'tier': tier}
    if target is not None: body['target'] = target
    if d20 is not None: body['d20'] = d20
    if override: body['override'] = True
    return client.post(f'/api/pc/{healer}/treat_wounds', json=body, headers=_AJAX)


def _repair(client, name, d20=None, dc=None):
    # Canonical route (was the free full-repair; now the RAW Repair check).
    body = {}
    if d20 is not None: body['d20'] = d20
    if dc is not None: body['dc'] = dc
    return client.post(f'/api/repair_shield/{name}', json=body, headers=_AJAX)


# ==========================================================================
# Treat Wounds
# ==========================================================================

def test_success_heals_and_clears_wounded(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    goel.conditions['wounded'] = 1
    # Kyle +21, trained DC 15: d20=3 -> total 24, success band [15, 24]
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['degree'] == 'success' and j['dc'] == 15
    assert 2 <= j['healing'] <= 16          # 2d8, no tier bonus
    assert goel.current_hp == 50 + j['healing']
    assert goel.conditions.get('wounded', 0) == 0, 'success must clear wounded'


def test_master_tier_crit_adds_flat_bonus(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 10
    # master DC 30: d20=19 -> total 40 = DC+10 -> crit success; 4d8 + 30
    r = _treat(client, kyle.name, target=goel.name, tier='master', d20=19)
    j = r.get_json()
    assert j['degree'] == 'crit_success'
    assert 4 + 30 <= j['healing'] <= 32 + 30
    assert goel.current_hp == min(goel.hp, 10 + j['healing'])


def test_healing_clamps_at_max_hp(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = goel.hp - 1
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.get_json()['degree'] == 'success'
    assert goel.current_hp == goel.hp


def test_crit_failure_damages_through_real_path(duo, client):
    """Crit fail deals 1d8 THROUGH the shared damage internals -- a target
    at 1 HP drops to 0 and enters dying like any other damage."""
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 1
    goel.conditions['wounded'] = 0
    # master DC 30, d20=1: total 22 is a plain failure band-wise (22 > DC-10),
    # and the NATURAL 1 steps it down one degree to crit failure
    r = _treat(client, kyle.name, target=goel.name, tier='master', d20=1)
    j = r.get_json()
    assert j['degree'] == 'crit_failure'
    assert -8 <= j['healing'] <= -1
    assert goel.current_hp == 0
    assert goel.conditions['dying'] == 1, 'dying-entry math must apply'


def test_self_target_defaults_to_healer(duo, client):
    kyle = duo['kyle']
    kyle.current_hp = 40
    r = _treat(client, kyle.name, tier='trained', d20=3)   # no target
    j = r.get_json()
    assert j['target'] == kyle.name
    assert kyle.current_hp == 40 + j['healing']


def test_tier_above_healer_rank_rejected(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    r = _treat(client, kyle.name, target=goel.name, tier='legendary', d20=10)
    assert r.status_code == 400                       # Kyle is Master
    r = _treat(client, goel.name, target=kyle.name, tier='expert', d20=10)
    assert r.status_code == 400                       # Go'el is Trained


def test_unknown_tier_rejected(duo, client):
    r = _treat(client, duo['kyle'].name, tier='mythic', d20=10)
    assert r.status_code == 400


def test_immunity_set_and_blocks_second_attempt(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert getattr(goel, 'treat_wounds_immune_until', 0) > time.time()
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.status_code == 409
    j = r.get_json()
    assert j.get('needs_override') is True
    assert 0 < j.get('remaining_minutes', 0) <= 60


def test_immunity_set_even_on_failure(duo, client):
    """RAW: the target becomes immune after being treated, success or not."""
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    # master DC 30, d20=3 -> total 24, failure band
    j = _treat(client, kyle.name, target=goel.name, tier='master', d20=3).get_json()
    assert j['degree'] == 'failure' and j['healing'] == 0
    assert goel.current_hp == 50
    assert getattr(goel, 'treat_wounds_immune_until', 0) > time.time()


def test_override_bypasses_immunity(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    hp_after_first = goel.current_hp
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3, override=True)
    assert r.status_code == 200
    assert goel.current_hp > hp_after_first


def test_immunity_survives_persistence_roundtrip(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    app_module._flush_pc_dirty(goel.name)
    saved = json.loads(pathlib.Path(duo['files'][goel.name]).read_text())
    build = saved.get('build', saved)
    assert build.get('treat_wounds_immune_until', 0) > time.time() - 5


def test_unknown_target_404(duo, client):
    r = _treat(client, duo['kyle'].name, target='Nobody', d20=3)
    assert r.status_code == 404


def test_other_player_cannot_act_as_healer(duo, client, monkeypatch):
    monkeypatch.setattr(app_module, 'GM_PASSWORD', 'sekrit')
    with client.session_transaction() as s:
        s['player_name'] = 'Somebody Else'
        s.pop('is_gm', None)
    r = _treat(client, duo['kyle'].name, target=duo['goel'].name, d20=3)
    assert r.status_code == 403


# ==========================================================================
# Refocus
# ==========================================================================

def test_refocus_adds_one_up_to_max(duo, client):
    # Canonical pre-existing route -- the panel reuses it, so its cap
    # semantics are pinned here alongside the other activities.
    kyle = duo['kyle']
    kyle.current_focus = 1
    r = client.post(f'/api/refocus/{kyle.name}', json={}, headers=_AJAX)
    assert r.status_code == 200, r.data
    assert r.get_json()['current_focus'] == 2
    assert kyle.current_focus == 2


def test_refocus_at_max_400(duo, client):
    kyle = duo['kyle']
    kyle.current_focus = kyle.focus_max
    r = client.post(f'/api/refocus/{kyle.name}', json={}, headers=_AJAX)
    assert r.status_code == 400
    assert kyle.current_focus == kyle.focus_max


def test_refocus_without_pool_400(duo, client):
    goel = duo['goel']
    goel.focus_max = 0
    r = client.post(f'/api/refocus/{goel.name}', json={}, headers=_AJAX)
    assert r.status_code == 400


# ==========================================================================
# Shield Repair
# ==========================================================================

def test_repair_success_restores_five_plus_five_per_rank(duo, client):
    goel = duo['goel']                     # Crafting U (+0, rank 0)
    goel.shield_hp = 10
    r = _repair(client, goel.name, d20=15)  # DC 15 default -> success
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['degree'] == 'success'
    assert j['restored'] == 5              # 5 + 5*0
    assert goel.shield_hp == 15


def test_repair_crit_restores_ten_plus_ten_per_rank_clamped(duo, client):
    goel = duo['goel']
    goel.shield_hp = 15
    r = _repair(client, goel.name, d20=20)  # nat 20 steps success -> crit
    j = r.get_json()
    assert j['degree'] == 'crit_success'
    assert goel.shield_hp == 20             # 15 + 10 clamped at max 20


def test_repair_crit_failure_damages_minus_hardness(duo, client):
    goel = duo['goel']                     # hardness 5
    goel.shield_hp = 10
    r = _repair(client, goel.name, d20=1)   # total 1 <= 5 -> crit fail
    j = r.get_json()
    assert j['degree'] == 'crit_failure'
    # 2d6 (2..12) minus hardness 5, floored at 0 -> hp in [3, 10]
    assert 3 <= goel.shield_hp <= 10


def test_repair_full_shield_400(duo, client):
    goel = duo['goel']
    goel.shield_hp = goel.shield_max_hp
    assert _repair(client, goel.name, d20=15).status_code == 400


def test_repair_destroyed_shield_400(duo, client):
    goel = duo['goel']
    goel.shield_hp = 0
    assert _repair(client, goel.name, d20=15).status_code == 400
    assert goel.shield_hp == 0


def test_repair_without_shield_400(duo, client):
    kyle = duo['kyle']
    kyle.shield_max_hp = 0
    assert _repair(client, kyle.name, d20=15).status_code == 400


def test_repair_custom_dc_honored(duo, client):
    goel = duo['goel']
    goel.shield_hp = 10
    r = _repair(client, goel.name, d20=15, dc=20)   # total 15 vs 20 -> failure
    j = r.get_json()
    assert j['degree'] == 'failure'
    assert goel.shield_hp == 10


# ==========================================================================
# Final-review fixes: dead targets, deterministic dice, immunity expiry,
# higher-rank tiers, and the dying-target stabilize flow.
# ==========================================================================

def _fix_dice(monkeypatch, value):
    """Pin every die the activities roll (healing d8s, repair 2d6) so the
    RAW formulas are asserted EXACTLY -- range-only assertions let a
    collapsed crit branch (the historical client-side bug) pass ~95% of
    runs. The d20 stays injectable via the request body."""
    import random as _random
    monkeypatch.setattr(_random, 'randint', lambda a, b: value)


def test_dead_target_cannot_be_treated(duo, client):
    """Same derived-dead guard as the recovery check: a success on a dead
    PC (heal clears dying, route clears wounded) would silently revive the
    corpse through a player-driven auto-applied path."""
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 0
    goel.conditions['dying'] = 4
    goel.conditions['wounded'] = 2
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=20)
    assert r.status_code == 400
    assert goel.current_hp == 0
    assert goel.conditions['dying'] == 4 and goel.conditions['wounded'] == 2


def test_doomed_dead_target_cannot_be_treated(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 0
    goel.conditions['dying'] = 2
    goel.conditions['doomed'] = 2          # dead at threshold 2
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=20)
    assert r.status_code == 400
    assert goel.conditions['dying'] == 2


def test_dying_but_alive_target_can_be_treated_and_stabilizes(duo, client):
    """The standard stabilize flow: success on a dying-1 target heals
    through the real heal path (dying cleared, wounded bumped) and then
    RAW's success rider clears wounded entirely."""
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 0
    goel.conditions['dying'] = 1
    goel.conditions['wounded'] = 0
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['degree'] == 'success'
    assert goel.current_hp == j['healing'] and goel.current_hp > 0
    assert goel.conditions['dying'] == 0
    assert goel.conditions['wounded'] == 0, 'RAW: success removes wounded'


def test_success_healing_exact_with_pinned_dice(duo, client, monkeypatch):
    _fix_dice(monkeypatch, 8)
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 10
    j = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3).get_json()
    assert j['degree'] == 'success'
    assert j['healing'] == 16              # exactly 2d8, no tier bonus
    assert goel.current_hp == 26


def test_crit_doubles_dice_and_keeps_flat_bonus_exact(duo, client, monkeypatch):
    """Crit = 4d8 + tier bonus. The old client-side copy rolled flat +10
    instead of doubling -- with pinned dice a collapse to 2d8+30 (46) can
    never pass this 62 assertion."""
    _fix_dice(monkeypatch, 8)
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 10
    j = _treat(client, kyle.name, target=goel.name, tier='master', d20=19).get_json()
    assert j['degree'] == 'crit_success'
    assert j['healing'] == 4 * 8 + 30      # 62
    assert goel.current_hp == min(goel.hp, 10 + 62)


def test_crit_failure_damage_exact_with_pinned_dice(duo, client, monkeypatch):
    _fix_dice(monkeypatch, 8)
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    j = _treat(client, kyle.name, target=goel.name, tier='master', d20=1).get_json()
    assert j['degree'] == 'crit_failure'
    assert j['healing'] == -8              # exactly 1d8, tier-independent
    assert goel.current_hp == 42


def test_expert_and_legendary_tiers_with_injected_rank(duo, client, monkeypatch):
    """Neither fixture has expert+ Medicine for the two untested tier rows,
    so inject the rank source: expert DC 20 -> +10, legendary DC 40 -> +50."""
    _fix_dice(monkeypatch, 8)
    kyle, goel = duo['kyle'], duo['goel']
    monkeypatch.setattr(app_module, '_pc_skill_mod_rank', lambda pc, skill: (21, 4))
    goel.current_hp = 10
    # expert success: DC 20, total 3+21=24 lands in the success band [20, 29]
    j = _treat(client, kyle.name, target=goel.name, tier='expert', d20=3).get_json()
    assert j['dc'] == 20 and j['degree'] == 'success'
    assert j['healing'] == 16 + 10
    # legendary: an out-of-range 25 clamps to 20, which IS a natural 20 --
    # total 41 vs DC 40 is a plain success stepped up to crit: 4d8 + 50.
    j = _treat(client, kyle.name, target=goel.name, tier='legendary', d20=25, override=True).get_json()
    assert j['dc'] == 40
    assert j['degree'] == 'crit_success'
    assert j['healing'] == 32 + 50


def test_expired_immunity_allows_retreat_without_override(duo, client):
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    goel.treat_wounds_immune_until = time.time() - 5    # expired
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.status_code == 200
    assert r.get_json()['degree'] == 'success'


def test_lower_tier_always_allowed_for_high_rank(duo, client):
    """RAW: higher ranks 'can instead' attempt higher DCs -- the trained
    tier stays legal for a Master healer."""
    kyle, goel = duo['kyle'], duo['goel']
    goel.current_hp = 50
    r = _treat(client, kyle.name, target=goel.name, tier='trained', d20=3)
    assert r.status_code == 200


def test_repair_rank_scaling_with_injected_rank(duo, client, monkeypatch):
    """Both fixtures are Crafting Untrained, so the 5/rank term was never
    exercised: expert (rank 2) success restores 15."""
    goel = duo['goel']
    goel.shield_hp = 2
    monkeypatch.setattr(app_module, '_pc_skill_mod_rank', lambda pc, skill: (6, 2))
    r = _repair(client, goel.name, d20=10)   # 16 vs DC 15 -> success
    j = r.get_json()
    assert j['degree'] == 'success'
    assert j['restored'] == 5 + 5 * 2
    assert goel.shield_hp == 17


def test_repair_crit_failure_exact_minus_hardness(duo, client, monkeypatch):
    _fix_dice(monkeypatch, 6)
    goel = duo['goel']                      # hardness 5
    goel.shield_hp = 10
    j = _repair(client, goel.name, d20=1).get_json()
    assert j['degree'] == 'crit_failure'
    assert j['damage'] == 6 + 6 - 5         # 7
    assert goel.shield_hp == 3


def test_legacy_client_reported_dispatch_route_is_gone(duo, client):
    """The old /api/treat_wounds/<name> accepted CLIENT-computed healing
    and appended it to the GM log -- removed with the server-rolled route
    so players can't inject bogus rows."""
    r = client.post(f"/api/treat_wounds/{duo['kyle'].name}",
                    json={'target': 'X', 'healing': 999}, headers=_AJAX)
    assert r.status_code in (404, 405)
