"""Stormlight catalog and legacy builder context owned by the rules profile."""
import systems.cosmere


def cultures():
    """All cultures for the builder -- base system + the ingested handbook
    (Iriali, Kharbranthian, Listener, Natan, Reshi, Shin, Wayfarer, ...)."""
    names = {d.get('name', '') for pack in ('cultures', 'handbook-cultures')
             for d in systems.cosmere.load_pack(pack) if d.get('name')}
    return sorted(n for n in names if n)


def _talent_prereq_summary(pr):
    """A short human-readable summary of a talent's Foundry prerequisites."""
    if not isinstance(pr, dict) or not pr:
        return ''
    parts = []
    for grp in pr.values():
        if not isinstance(grp, dict):
            continue
        t = grp.get('type')
        if t == 'skill':
            parts.append('%s rank %s' % (systems.cosmere.SKILL_NAMES.get(grp.get('skill'), grp.get('skill')), grp.get('rank')))
        elif t == 'talent':
            parts.append(' or '.join(x.get('label', '?') for x in (grp.get('talents') or [])))
        elif t == 'attribute':
            parts.append('%s %s' % (grp.get('attribute'), grp.get('value')))
        elif t == 'goal':
            parts.append('a completed goal')
        elif t == 'connection':
            parts.append('a connection')
        elif t:
            parts.append(str(t))
    return ' + '.join(p for p in parts if p)


def path_talents():
    """{path_id: [{id, name, key, prereq}]} for the builder's talent picker
    (key talents first; each carries a prerequisite summary)."""
    import systems.cosmere.origins as _o
    import systems.cosmere.talents as _ct
    from systems.cosmere.radiant_talents import _effect as _talent_effect   # readable one-line summary
    # The real key talent per path is the one named in PATH_INFO (handbook trees
    # have many prereq-less roots, so "no prerequisite" is NOT the discriminator).
    key_names = {p: (info.get('key_talent') or '').strip().lower()
                 for p, info in _o.PATH_INFO.items()}
    out, seen = {}, set()
    # Base system talents first, then the fuller handbook trees; a name clash
    # per path keeps the BASE talent (so origins.PATH_INFO key-talent ids +
    # talents.py prereqs still match), and the handbook adds the rest.
    for pack in ('heroic-paths', 'handbook-heroic-paths'):
        for d in systems.cosmere.load_pack(pack):
            if d.get('type') != 'talent':
                continue
            s = d.get('system', {})
            p = _ct.norm_path(s.get('path'))     # champion (a Leader specialty) -> leader
            if not p:
                continue
            name = d.get('name', '')
            dkey = (p, name.strip().lower())
            if not name or dkey in seen:
                continue
            seen.add(dkey)
            # Resolve prereqs from the talent-tree graph (authoritative); this
            # corrects the null / swapped / stale-label item-level prerequisites.
            out.setdefault(p, []).append({
                'id': d.get('_id'), 'name': name,
                'key': name.strip().lower() == key_names.get(p, '\x00'),
                'specialty': _ct.talent_specialty().get(d.get('_id'), ''),   # '' = core path talent
                'prereq': _talent_prereq_summary(_ct.resolved_prereqs(d.get('_id'))),
                'effect': _talent_effect(s.get('description')),   # readable text for the inspector panel
            })
    for p in out:
        # Key talents first, then group by specialty (core '' first), then name.
        out[p].sort(key=lambda t: (not t['key'], t['specialty'] or '', t['name'].lower()))
    return out


def starting_kits():
    """The rulebook starting kits, with armor/weapons resolved to catalog items
    (id+name) so the builder can add them; equipment/marks/bonus are notes."""
    import systems.cosmere.origins as _o
    import systems.cosmere.items as _it
    out = []
    for key, k in _o.STARTING_KITS.items():
        items = []
        if k['armor']:
            a = _it.by_name(k['armor'])
            if a:
                items.append({'id': a['id'], 'name': a['name']})
        for wn in k['weapons']:
            w = _it.by_name(wn)
            if w:
                items.append({'id': w['id'], 'name': w['name']})
        out.append({'key': key, 'name': k['name'], 'items': items,
                    'equipment': k['equipment'], 'marks': k['marks'], 'bonus': k['bonus']})
    return out


def builder_context(build, hb_store=None):
    import systems.cosmere.stormlight_build as _cb
    import systems.cosmere.radiant as _rad
    import systems.cosmere.radiant_talents as _rt
    import systems.cosmere.infected as _inf
    if hb_store is None:
        hb_store = {}
    return dict(
        build=build.to_dict(),
        infected_arts=_inf.load_arts(),     # the Infected Arts catalog (disease cost + abilities)
        catalog=systems.cosmere.items.catalog(),
        fabrials=systems.cosmere.items.fabrials(),
        skill_names=build.eff_skill_names(), skill_attr=build.eff_skill_attr(),
        surge_skills=list(build.eff_surge_skills()),
        attr_names=systems.cosmere.ATTR_NAMES,
        attr_desc=systems.cosmere.ATTR_DESC, skill_desc=systems.cosmere.SKILL_DESC,
        culture_info=systems.cosmere.origins.CULTURE_INFO,
        ancestry_info=systems.cosmere.origins.ANCESTRY_INFO,
        paths=list(systems.cosmere.PATHS), cultures=cultures(),
        path_talents=path_talents(),
        path_trees=systems.cosmere.talents.tree_graphs(),   # positioned DAG per path (visual tree)
        radiant_orders=_rad.RADIANT_ORDERS, radiant_variants=_rad.RADIANT_VARIANTS,
        surges=_rad.SURGES, first_ideal=_rad.FIRST_IDEAL,
        radiant_ideals=_rad.ORDER_IDEALS, ideal_personal=list(_rad.IDEAL_PERSONAL),
        fourth_ideal_level=_rad.FOURTH_IDEAL_LEVEL,
        path_info=systems.cosmere.origins.PATH_INFO,
        singer_forms=systems.cosmere.origins.SINGER_FORMS,
        singer_change_form=systems.cosmere.origins.SINGER_CHANGE_FORM,
        starting_kits=starting_kits(),
        radiant_surge_talents=_rt.SURGE_TALENTS, radiant_order_talents=_rt.ORDER_TALENTS,
        radiant_surge_powers=_rt.SURGE_POWERS,
        radiant_trees=_rt.radiant_tree_graphs(),    # positioned Radiant DAGs (visual tree)
        homebrew=hb_store,            # {type: [entry]} -- merged into the pickers client-side
        budgets=dict(
            attr_points=build.attr_points_available(),
            skill_ranks=build.skill_ranks_available(),
            max_skill_rank=_cb.max_skill_rank(build.level),
            talents=build.talents_available(),
            expertises=build.expertises_available(),
        ),
    )
