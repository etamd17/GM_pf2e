"""The UI capability projection must never broaden mutation authority."""

from dataclasses import replace

import pytest

from core.request_context import CampaignContext, CampaignRole, Principal, resolve_character_context


def context(user='owner', role=CampaignRole.PLAYER, *, admin=False, system='pf2e'):
    return CampaignContext(Principal.user(user, is_admin=admin), 'a' * 32,
                           'a' * 32, True, system, role)


def character(system='pf2e'):
    from core.character_workflows.types import CharacterRecord
    return CharacterRecord('a' * 32, 'c' * 32, system,
        'party_data' if system == 'pf2e' else 'cosmere_pcs', 'hero.json',
        {'build': {'name': 'Hero'}}, 'owner', frozenset({'editor'}), frozenset({'viewer'}))


def test_viewer_is_read_only():
    from core.character_workflows.capabilities import capabilities_for
    caps = capabilities_for(context('viewer'), character())
    assert caps.view and caps.create
    assert not any((caps.edit, caps.delete, caps.override, caps.owner_private,
                    caps.manage_ownership))


def test_cosmere_does_not_gain_blanket_member_access():
    from core.character_workflows.capabilities import capabilities_for
    for role in ('viewer', 'outsider'):
        caps = capabilities_for(context(role, system='cosmere'), character('cosmere'))
        assert caps.create and not caps.view and not caps.edit


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_owner_editor_and_gm_matrix(system):
    from core.character_workflows.capabilities import capabilities_for
    owner = capabilities_for(context(system=system), character(system))
    editor = capabilities_for(context('editor', system=system), character(system))
    gm = capabilities_for(context('gm', CampaignRole.GM, system=system), character(system))
    assert owner.view and owner.edit and owner.owner_private
    assert owner.delete == (system == 'cosmere')
    assert editor.view and editor.edit and not editor.delete and not editor.owner_private
    assert not owner.override and not editor.manage_ownership
    assert all((gm.create, gm.view, gm.edit, gm.delete, gm.override, gm.manage_ownership))


def test_admin_without_membership_cannot_create_personal_draft():
    from core.character_workflows.capabilities import capabilities_for
    ctx = context('admin', None, admin=True)
    assert not capabilities_for(ctx, None).create
    caps = capabilities_for(ctx, character())
    assert caps.view and caps.edit and caps.manage_ownership


@pytest.mark.parametrize('change', [
    {'membership_role': None}, {'campaign_exists': False, 'membership_role': None},
    {'campaign_id': 'b' * 32}, {'live_campaign_id': 'b' * 32}, {'system': 'cosmere'},
])
def test_invalid_scope_denies_character_actions(change):
    from core.character_workflows.capabilities import capabilities_for
    caps = capabilities_for(replace(context(), **change), character())
    assert not any((caps.view, caps.edit, caps.delete, caps.owner_private, caps.override))


def test_legacy_context_is_not_a_personal_account():
    from core.character_workflows.capabilities import capabilities_for
    ctx = replace(context(), principal=Principal.legacy_player('Hero', campaign_id='a' * 32))
    assert not capabilities_for(ctx, character()).create


def test_viewer_context_does_not_change_existing_write_policy():
    from core.access import decide_access
    from core.route_policy import RoutePolicy
    char = resolve_character_context(campaign_id='a' * 32, character_id='c' * 32,
                                     owner_user_id='owner', viewer_user_ids=['viewer'])
    assert char.viewer_user_ids == frozenset({'viewer'})
    assert not decide_access(RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
                             context('viewer'), character=char).allowed


@pytest.mark.parametrize('viewers', ['viewer', b'viewer', None, [42]])
def test_viewer_context_rejects_malformed_collections(viewers):
    with pytest.raises(ValueError):
        resolve_character_context(campaign_id='a' * 32, character_id='c' * 32,
                                  viewer_user_ids=viewers)
