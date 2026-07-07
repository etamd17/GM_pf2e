"""Recovery-check math + the player self-serve route (dying automation).

Spec: docs/superpowers/specs/2026-07-06-dying-automation-design.md
Plan: docs/superpowers/plans/2026-07-06-dying-automation.md (Task 1)

Backfills the previously-untested degree ladder of `/api/recovery_check`
(the GM tracker route, live since Wave 2 but with zero coverage) and drives
the NEW player-facing `/api/pc/<pc_name>/recovery_check` route, which must
share one mutation core with it — the sheet's old client-side copy of this
math diverged (no DC+10 crit success) and applied nothing, which is exactly
why duplication is banned here.

PF2e Remaster rules asserted (Player Core p.404):
  * Flat check DC = 10 + current dying value.
  * success: dying -1; crit success (>= DC+10): dying -2;
    failure: dying +1; crit failure (<= DC-10): dying +2.
  * natural 20 steps the degree up one; natural 1 steps it down one.
  * Recovering to dying 0 adds 1 wounded.
  * Death threshold is max(1, 4 - doomed); dying clamps there.

Same CI-safe fixture approach as tests/test_dying_state.py: committed PC
fixture -> PARTY_LIBRARY injection, get_pc_file_path redirected to tmp.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX_DIR = pathlib.Path(__file__).parent / 'fixtures'
_KYLE_FIX = _FIX_DIR / 'kyle_l10.json'

_AJAX = {'X-Requested-With': 'XMLHttpRequest'}


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    raw = json.loads(_KYLE_FIX.read_text())
    pc_file = tmp_path / 'Kyle.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    pc = Character(raw, file_path=str(pc_file))
    name = pc.name
    monkeypatch.setitem(app_module.PARTY_LIBRARY, name, pc)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == name else None)
    # Recovery checks never touch the encounter autosave path in the
    # out-of-encounter tests, but the in-encounter ones do -- keep both
    # stubbed so no test writes tracker state into the repo's DATA_DIR.
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    return name


@pytest.fixture
def client():
    return app_module.app.test_client()


class _Encounter:
    """Same pattern as test_dying_state.py: drop the library PC object into
    ACTIVE_ENCOUNTER under a known instance id, restore on exit."""

    def __init__(self, pc, instance_id='recovery-test-1'):
        self.pc = pc
        self.instance_id = instance_id

    def __enter__(self):
        self._saved = list(app_module.ACTIVE_ENCOUNTER)
        self._saved_idx = app_module.TURN_INDEX
        self.pc.instance_id = self.instance_id
        app_module.ACTIVE_ENCOUNTER[:] = [self.pc]
        app_module.TURN_INDEX = 0
        app_module._invalidate_tracker_cache()
        return self

    def __exit__(self, *exc):
        app_module.ACTIVE_ENCOUNTER[:] = self._saved
        app_module.TURN_INDEX = self._saved_idx
        app_module._invalidate_tracker_cache()
        return False


def _set_dying(pc, dying, wounded=0, doomed=0):
    pc.current_hp = 0
    pc.conditions['dying'] = dying
    pc.conditions['wounded'] = wounded
    pc.conditions['doomed'] = doomed


def _pc_roll(client, name, d20=None):
    body = {} if d20 is None else {'d20': d20}
    return client.post(f'/api/pc/{name}/recovery_check', json=body, headers=_AJAX)


# ==========================================================================
# Degree ladder through the NEW player route (out of encounter)
# ==========================================================================

def test_success_reduces_dying_by_one(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2)                      # DC 12
    r = _pc_roll(client, kyle, d20=12)
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['dc'] == 12 and j['degree'] == 'success'
    assert j['dying'] == 1 and pc.conditions['dying'] == 1
    assert not j['died']


def test_failure_increases_dying_by_one(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1)                      # DC 11
    j = _pc_roll(client, kyle, d20=5).get_json()
    assert j['degree'] == 'failure'
    assert j['dying'] == 2 and pc.conditions['dying'] == 2


def test_nat_twenty_is_crit_success(kyle, client):
    """Recovery DCs are always >= 11, so the >= DC+10 crit band is
    unreachable on a d20 -- crit success only ever arrives via the nat-20
    one-step bump. (The sheet's deleted client-side copy hardcoded 'nat 20
    only' by accident of this; the shared core gets there by the real
    rule.) Nat 20 from dying 2 -> -2 -> recovered, wounded +1."""
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2)                      # DC 12
    j = _pc_roll(client, kyle, d20=20).get_json()
    assert j['degree'] == 'crit_success'
    assert j['dying'] == 0 and pc.conditions['dying'] == 0
    assert j['wounded'] == 1 and pc.conditions['wounded'] == 1


def test_crit_failure_at_dc_minus_ten(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 3, doomed=0)            # DC 13; d20 3 <= 3 -> crit fail
    j = _pc_roll(client, kyle, d20=3).get_json()
    assert j['degree'] == 'crit_failure'
    # 3 + 2 = 5 -> clamped at the death threshold (4) and dead
    assert j['died'] is True
    assert j['dying'] == 4 and pc.conditions['dying'] == 4


def test_nat_one_is_crit_failure(kyle, client):
    """d20=1 is always <= DC-10 for any dying >= 1 (DC >= 11), and the
    nat-1 step-down keeps it at the floor -- locked so a refactor can't
    accidentally make nat 1 a plain failure."""
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1)
    j = _pc_roll(client, kyle, d20=1).get_json()
    assert j['degree'] == 'crit_failure'
    assert j['dying'] == 3 and pc.conditions['dying'] == 3


def test_recovery_to_zero_adds_wounded(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1, wounded=1)           # DC 11
    j = _pc_roll(client, kyle, d20=11).get_json()
    assert j['degree'] == 'success'
    assert j['dying'] == 0 and pc.conditions['dying'] == 0
    assert j['wounded'] == 2 and pc.conditions['wounded'] == 2


def test_doomed_lowers_death_threshold(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2, doomed=1)            # threshold 3; DC 12
    j = _pc_roll(client, kyle, d20=5).get_json()   # failure -> 3
    assert j['died'] is True
    assert j['dying'] == 3 and pc.conditions['dying'] == 3


def test_not_dying_is_400(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    pc.conditions['dying'] = 0
    r = _pc_roll(client, kyle, d20=15)
    assert r.status_code == 400


def test_unknown_pc_is_404(client):
    r = client.post('/api/pc/NoSuchHero/recovery_check', json={}, headers=_AJAX)
    assert r.status_code == 404


def test_server_roll_when_no_d20_given(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2)
    j = _pc_roll(client, kyle).get_json()
    assert 1 <= j['d20'] <= 20
    assert j['degree'] in ('crit_success', 'success', 'failure', 'crit_failure')
    assert pc.conditions['dying'] == j['dying']


def test_out_of_range_d20_clamped(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1)
    j = _pc_roll(client, kyle, d20=99).get_json()
    assert j['d20'] == 20


# ==========================================================================
# Resolution paths: in-encounter mirrors the tracker; out handled above
# ==========================================================================

def test_in_encounter_mutates_combatant_and_library(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2)
    with _Encounter(pc) as enc:
        j = _pc_roll(client, kyle, d20=12).get_json()
        assert j['degree'] == 'success'
        live = app_module.ACTIVE_ENCOUNTER[0]
        assert live.conditions['dying'] == 1
        assert app_module.PARTY_LIBRARY[kyle].conditions['dying'] == 1
        assert enc.instance_id == live.instance_id


def test_gm_tracker_route_still_works_via_shared_core(kyle, client):
    """The existing GM route thin-wraps the extracted core -- same math,
    same response shape (this also backfills its missing coverage)."""
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2)
    with _Encounter(pc) as enc:
        r = client.post(f'/api/recovery_check/{enc.instance_id}',
                        json={'d20': 12}, headers=_AJAX)
        assert r.status_code == 200, r.data
        j = r.get_json()
        assert j['degree'] == 'success' and j['dying'] == 1
        assert pc.conditions['dying'] == 1


# ==========================================================================
# Auth: owner or GM only (legacy mode with a GM password set)
# ==========================================================================

def test_other_player_gets_403(kyle, client, monkeypatch):
    # require_pc_self_or_gm reads the GM_PASSWORD module global at call time
    # (not a captured value) and _account_mode() is off in this fixture setup,
    # so patching the global exercises the legacy player_name branch exactly.
    monkeypatch.setattr(app_module, 'GM_PASSWORD', 'sekrit')
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1)
    with client.session_transaction() as s:
        s['player_name'] = 'Somebody Else'
        s.pop('is_gm', None)
    r = _pc_roll(client, kyle, d20=15)
    assert r.status_code == 403
    assert pc.conditions['dying'] == 1     # nothing applied


def test_owning_player_allowed(kyle, client, monkeypatch):
    monkeypatch.setattr(app_module, 'GM_PASSWORD', 'sekrit')
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 1)
    with client.session_transaction() as s:
        s['player_name'] = kyle
        s.pop('is_gm', None)
    r = _pc_roll(client, kyle, d20=11)
    assert r.status_code == 200
    assert pc.conditions['dying'] == 0


# ==========================================================================
# Final-review fixes: dead targets must be rejected (a recovery check can't
# un-kill), and the in-encounter mirror must hold for DISTINCT objects.
# ==========================================================================

def test_dead_pc_cannot_roll_recovery(kyle, client):
    """Dead is derived as dying >= max(1, 4 - doomed); a dead PC passing the
    old dying<=0-only guard could be revived by a nat 20 from the sheet."""
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 4)
    r = _pc_roll(client, kyle, d20=20)
    assert r.status_code == 400
    assert pc.conditions['dying'] == 4, 'dead PC was mutated'
    assert pc.conditions['wounded'] == 0


def test_doomed_dead_pc_cannot_roll_recovery(kyle, client):
    """Doomed lowers the threshold: dying 2 + doomed 2 IS dead, even though
    dying < 4."""
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 2, doomed=2)
    r = _pc_roll(client, kyle, d20=20)
    assert r.status_code == 400
    assert pc.conditions['dying'] == 2


def test_gm_route_also_rejects_dead(kyle, client):
    pc = app_module.PARTY_LIBRARY[kyle]
    _set_dying(pc, 4)
    with _Encounter(pc) as enc:
        r = client.post(f'/api/recovery_check/{enc.instance_id}',
                        json={'d20': 20}, headers=_AJAX)
        assert r.status_code == 400
        assert pc.conditions['dying'] == 4


def test_in_encounter_mirror_with_distinct_objects(kyle, client):
    """Production reality: the tracker holds a DEEPCOPY of the library PC
    (add_party deepcopies), so the mirror must copy values across two
    distinct objects — the same-object test above can't prove that."""
    import copy as _copy
    pc = app_module.PARTY_LIBRARY[kyle]
    live = _copy.deepcopy(pc)
    _set_dying(live, 1)
    pc.conditions['dying'] = 1      # library agrees he is dying
    pc.current_hp = 0
    with _Encounter(live) as enc:
        j = _pc_roll(client, kyle, d20=11).get_json()   # DC 11 -> success
        assert j['degree'] == 'success' and j['dying'] == 0
        assert live.conditions['dying'] == 0, 'live combatant not mutated'
        assert live is not pc
        assert pc.conditions['dying'] == 0, 'library PC not mirrored'
        assert pc.conditions['wounded'] == 1, 'wounded not mirrored'
