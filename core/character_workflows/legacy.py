"""Reviewed legacy endpoints which actually change retained character state.

Authorization references alone are insufficient: healers and log authors are
not necessarily write targets. These sets apply only to unsafe HTTP methods.
"""

PC_NAME_WRITES = frozenset('''
add_item add_pc_effect add_pet add_weapon adjust_consumable adjust_focus
adjust_hero adjust_party_hp adjust_temp_hp cast_spell daily_preparations
delete_character delete_session_note delete_weapon equip_armor forget_spell
learn_spell long_rest pc_recovery_check persistent_damage_add
persistent_damage_flat_check persistent_damage_remove refocus remove_item
remove_pc_effect remove_pet repair_shield revert_level save_notes
save_session_note set_exploration_activity set_focus_spells set_reaction
set_shield_stats set_signature_spells shield_block spell_slots submit_levelup
sync_spell_slots toggle_feature toggle_shield toggle_two_hand update_pc_condition
update_portrait_focus update_sheet update_wealth upload_portrait
'''.split())

COMBATANT_WRITES = frozenset('''
adjust_hp toggle_condition api_cosmere_combatant_condition use_action
recovery_check update_initiative cosmere_speed_choice reenter_initiative
set_combatant_tactics api_scene_combatant_action
'''.split())

COSMERE_PID_WRITES = frozenset('''
cosmere_pc_notes cosmere_pc_state cosmere_pc_rest cosmere_pc_release cosmere_pc_delete
'''.split())

PARTY_WRITES = frozenset('''
daily_preparations_all api_rest_apply api_award_xp add_party
'''.split())
