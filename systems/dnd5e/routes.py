"""Flask blueprint for the D&D 5e section.

The blueprint never imports app.py. The host passes a small hooks object
(``Host``) with the handful of app services it needs: the request's campaign,
GM-ness, the current user, and SSE broadcast. Authorization is NOT done here:
every endpoint is declared in core/route_policy.py and enforced by the app's
central before_request guard (CSRF is global too). Handlers only add the
in-campaign checks the policy layer cannot express (e.g. "monster stats are
GM-only").

SSE events (scoped to the campaign):
  dnd5e_pc         {pc live state}                      everyone
  dnd5e_encounter  GM: full view / players: masked view
  dnd5e_prompt     concentration / death-save prompts   GM (+ the PC's player)
  dnd5e_roll       a player's roll, for the GM log      GM only
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Callable

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file, url_for

from systems.dnd5e import combat as K
from systems.dnd5e import content as C
from systems.dnd5e import datapack, ddb_import, dice, images, paths, pdf_import, service, store
from systems.dnd5e.ids import entity_id, parse_ref, subclass_id
from systems.dnd5e.render import render_entries, render_text, strip_tags
from systems.dnd5e.rules import character as ENG
from systems.dnd5e.rules import spells as SP
from systems.dnd5e.rules.tables import (
    DIFFICULTY_2014, DIFFICULTY_2024, XP_BUDGET_2024, XP_THRESHOLDS_2014,
    ABILITIES, ABILITY_NAMES, CONDITIONS, POINT_BUY_BUDGET, POINT_BUY_COST, SCHOOL_NAMES, SIZE_NAMES,
    SKILLS, STANDARD_ARRAY, XP_BY_CR,
)

CONTENT_KINDS = ('spell', 'item', 'feat', 'race', 'background', 'class', 'subclass', 'optionalfeature',
                 'condition', 'disease', 'status', 'action', 'variantrule', 'language', 'skill', 'sense',
                 'monster', 'itemMastery', 'itemProperty', 'legendaryGroup')
GM_ONLY_KINDS = {'monster', 'legendaryGroup'}


@dataclass
class Host:
    campaign_id: Callable[[], str | None]
    is_gm: Callable[[], bool]
    current_user: Callable[[], dict | None]
    account_mode: Callable[[], bool]
    broadcast: Callable[..., object]
    campaign_doc: Callable[[], dict | None]


def create_blueprint(host: Host) -> Blueprint:
    bp = Blueprint('dnd5e', __name__)

    # --- helpers ---------------------------------------------------------
    def cid_or_404():
        cid = host.campaign_id()
        if not cid:
            abort(404, 'No active campaign')
        return cid

    def body():
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def err(e: service.ServiceError):
        return jsonify(error=e.message), e.status

    def user_id():
        u = host.current_user() if host.account_mode() else None
        return u.get('id') if u else None

    def can_edit(doc) -> bool:
        if host.is_gm() or not host.account_mode():
            return True
        uid = user_id()
        return bool(uid) and doc.get('owner_user_id') == uid

    def pc_or_404(cid, pid):
        doc = store.load_pc(cid, pid)
        if doc is None:
            abort(404, 'Unknown character')
        return doc

    def edition():
        camp = host.campaign_doc() or {}
        return 'classic' if (camp.get('dnd5e') or {}).get('edition') == 'classic' else 'one'

    def emit_pc(cid, live):
        host.broadcast('dnd5e_pc', live, campaign_id=cid)

    def emit_encounter(cid, enc=None):
        gm_view = service.encounter_view(cid, enc)
        player_view = service.encounter_view(cid, enc, for_player=True)
        host.broadcast('dnd5e_encounter', gm_view, player_filter=lambda _d: player_view, campaign_id=cid)

    def emit_prompts(cid, events, pc_id=None):
        prompts = [e for e in events if e.get('type') in ('concentration_check', 'death_save_due', 'dying',
                                                          'death', 'save_ends_reminder', 'turn')]
        if not prompts:
            return
        payload = {'events': prompts, 'pc_id': pc_id}
        enc = store.load_encounter(cid)
        host.broadcast('dnd5e_prompt', payload, campaign_id=cid,
                       player_filter=lambda d: player_prompts(d, enc))

    def encounter_pc_ids(cid):
        enc = store.load_encounter(cid)
        return {c.get('pc_id') for c in enc['combatants'] if c['kind'] == 'pc'}

    def after_pc_change(cid, pid, live, events=()):
        emit_pc(cid, live)
        if pid in encounter_pc_ids(cid):
            emit_encounter(cid)
        emit_prompts(cid, list(events), pid)

    def ctx_common(**kw):
        kw.setdefault('d5_is_gm', host.is_gm())
        kw.setdefault('d5_edition', edition())
        return kw

    @bp.app_context_processor
    def _dnd5e_nav_context():
        # Names of the viewer's own 5e characters, for the player nav's turn
        # ping. Only computed on 5e pages for non-GMs (a handful of small files).
        try:
            if not request.path.startswith('/dnd5e') or host.is_gm():
                return {}
            cid, uid = host.campaign_id(), user_id()
            if not cid or not uid:
                return {}
            return {'d5_my_pc_names': [d.get('name') for d in store.list_pcs(cid)
                                       if d.get('owner_user_id') == uid]}
        except Exception:
            return {}

    # --- pages -------------------------------------------------------------
    @bp.get('/dnd5e/gm')
    def gm_hub():
        cid = cid_or_404()
        party = [service.pc_live(d) for d in store.list_pcs(cid) if (d.get('build') or {}).get('levels')]
        enc = service.encounter_view(cid)
        return render_template('dnd5e/gm_hub.html', **ctx_common(party=party, encounter=enc,
                                                                 plans=store.list_plans(cid)))

    @bp.get('/dnd5e/player')
    def player_home():
        cid = cid_or_404()
        uid = user_id()
        if uid:
            mine = [d for d in store.list_pcs(cid) if d.get('owner_user_id') == uid]
            if len(mine) == 1:
                return redirect(url_for('dnd5e.character_sheet', pid=mine[0]['id']))
        return redirect(url_for('dnd5e.characters'))

    @bp.get('/dnd5e/characters')
    def characters():
        cid = cid_or_404()
        rows = []
        for d in store.list_pcs(cid):
            levels = (d.get('build') or {}).get('levels') or []
            row = {'id': d['id'], 'name': d.get('name'), 'owner_user_id': d.get('owner_user_id'),
                   'mine': can_edit(d), 'level': len(levels), 'portrait': images.url_for_name(d.get('portrait'))}
            if levels:
                live = service.pc_live(d)
                row.update({'classes': live['classes'], 'hp': live['hp'], 'hp_max': live['hp_max'],
                            'ac': live['ac']})
            rows.append(row)
        members = []
        camp = host.campaign_doc() or {}
        if host.is_gm():
            from core import auth as _auth
            for m in camp.get('members') or []:
                u = _auth.get_user(m.get('user_id')) if m.get('user_id') else None
                members.append({'user_id': m.get('user_id'), 'role': m.get('role'),
                                'name': (u or {}).get('display_name') or (u or {}).get('username') or m.get('user_id')})
        return render_template('dnd5e/characters.html', **ctx_common(rows=rows, members=members))

    @bp.post('/dnd5e/characters/new')
    def character_create():
        cid = cid_or_404()
        b = body() or request.form
        ed = (b.get('edition') or edition())
        ed = 'classic' if ed == 'classic' else 'one'
        owner = None if host.is_gm() and b.get('unowned') else user_id()
        doc = store.new_pc(cid, name=str(b.get('name') or 'New Character'), owner_user_id=owner, edition=ed)
        store.save_pc(cid, doc)
        if request.is_json:
            return jsonify(id=doc['id'], url=url_for('dnd5e.character_builder', pid=doc['id']))
        return redirect(url_for('dnd5e.character_builder', pid=doc['id']))

    @bp.post('/api/dnd5e/import/ddb')
    def api_import_ddb():
        """Create a character from a D&D Beyond export (pasted/uploaded JSON,
        or a public character's URL fetched from D&D Beyond's character service)."""
        cid = cid_or_404()
        b = body()
        raw = b.get('data')
        if not raw and b.get('url'):
            try:
                raw = ddb_import.fetch_public(str(b['url']))
            except ddb_import.ImportError_ as e:
                return jsonify(error=str(e)), 422
        if not raw:
            return jsonify(error='Paste the character JSON, upload the file, or give the character URL.'), 400
        if isinstance(raw, str) and len(raw) > 8_000_000:
            return jsonify(error='That file is too large to be a character.'), 413
        try:
            res = ddb_import.import_character(raw, edition=b.get('edition') if b.get('edition') in ('one', 'classic') else None)
        except ddb_import.ImportError_ as e:
            return jsonify(error=str(e)), 422
        owner = None if host.is_gm() and b.get('unowned') else user_id()
        doc = store.new_pc(cid, name=res['name'], owner_user_id=owner, edition=res['build']['edition'])
        doc['build'] = res['build']
        doc['state'].update(res['state'])
        doc['details'] = res['details']
        doc['import_report'] = dict(res['report'], source='D&D Beyond', ddb_id=res.get('ddb_id'))
        store.save_pc(cid, doc)
        return jsonify(id=doc['id'], url=url_for('dnd5e.character_builder', pid=doc['id']), report=res['report'])

    @bp.post('/api/dnd5e/import/pdf')
    def api_import_pdf():
        """Create a character from a filled character-sheet PDF (D&D Beyond's
        export or the official form). A PDF records results, not choices, so
        the build is reconstructed and the report says what was assumed."""
        cid = cid_or_404()
        f = request.files.get('file')
        if f is None:
            return jsonify(error='Choose a PDF to upload.'), 400
        data = f.stream.read(pdf_import.MAX_BYTES + 1)
        if len(data) > pdf_import.MAX_BYTES:
            return jsonify(error='That file is too large to be a character sheet.'), 413
        ed = request.form.get('edition')
        try:
            res = pdf_import.import_pdf(data, edition=ed if ed in ('one', 'classic') else None)
        except (pdf_import.PdfImportError, ddb_import.ImportError_) as e:
            return jsonify(error=str(e)), 422
        owner = None if host.is_gm() and request.form.get('unowned') else user_id()
        doc = store.new_pc(cid, name=res['name'], owner_user_id=owner, edition=res['build']['edition'])
        doc['build'] = res['build']
        doc['state'].update(res['state'])
        doc['details'] = res['details']
        doc['import_report'] = dict(res['report'], source='PDF')
        store.save_pc(cid, doc)
        return jsonify(id=doc['id'], url=url_for('dnd5e.character_builder', pid=doc['id']), report=res['report'])

    @bp.get('/dnd5e/character/<pid>')
    def character_sheet(pid):
        cid = cid_or_404()
        doc = pc_or_404(cid, pid)
        if not (doc.get('build') or {}).get('levels'):
            return redirect(url_for('dnd5e.character_builder', pid=pid))
        payload = _sheet_payload(doc)
        return render_template('dnd5e/sheet.html', **ctx_common(
            pid=pid, boot=payload, can_edit=can_edit(doc), conditions=CONDITIONS))

    @bp.get('/dnd5e/character/<pid>/build')
    def character_builder(pid):
        cid = cid_or_404()
        doc = pc_or_404(cid, pid)
        return render_template('dnd5e/builder.html', **ctx_common(
            pid=pid, boot=_sheet_payload(doc), can_edit=can_edit(doc),
            classes=_class_summaries(), races=_summaries('race'), backgrounds=_summaries('background'),
            sources=_source_rows(), standard_array=STANDARD_ARRAY, point_buy_cost=POINT_BUY_COST,
            point_buy_budget=POINT_BUY_BUDGET, ability_names=ABILITY_NAMES))

    @bp.get('/dnd5e/encounters')
    def encounters():
        cid = cid_or_404()
        party = [service.pc_live(d) for d in store.list_pcs(cid) if (d.get('build') or {}).get('levels')]
        return render_template('dnd5e/encounters.html', **ctx_common(
            party=party, plans=store.list_plans(cid), facets=C.monster_facets(),
            sizes=SIZE_NAMES, crs=list(XP_BY_CR)))

    @bp.get('/dnd5e/tracker')
    def tracker():
        cid = cid_or_404()
        party = [{'id': d['id'], 'name': d.get('name')} for d in store.list_pcs(cid)
                 if (d.get('build') or {}).get('levels')]
        return render_template('dnd5e/tracker.html', **ctx_common(
            boot=service.encounter_view(cid), party=party, conditions=CONDITIONS))

    @bp.get('/dnd5e/combat')
    def combat_view():
        cid = cid_or_404()
        uid = user_id()
        mine = [d['id'] for d in store.list_pcs(cid) if uid and d.get('owner_user_id') == uid]
        view = service.encounter_view(cid, for_player=not host.is_gm())
        return render_template('dnd5e/combat.html', **ctx_common(boot=view, my_pcs=mine))

    @bp.get('/dnd5e/bestiary')
    def bestiary():
        cid_or_404()
        return render_template('dnd5e/bestiary.html', **ctx_common(
            facets=C.monster_facets(), sizes=SIZE_NAMES, crs=list(XP_BY_CR)))

    @bp.get('/dnd5e/gmscreen')
    def gmscreen():
        cid_or_404()
        ed = request.args.get('edition') or edition()
        ed = 'classic' if ed == 'classic' else 'one'
        core = 'XPHB' if ed == 'one' else 'PHB'

        def entries(kind):
            return [{'name': r['name'], 'html': render_entries(r.get('entries'))}
                    for r in sorted(C.all_records(kind, sources=[core]), key=lambda r: r['name'])]
        if ed == 'one':
            names, table = DIFFICULTY_2024, XP_BUDGET_2024
        else:
            names, table = DIFFICULTY_2014, XP_THRESHOLDS_2014
        glossary = ([{'id': entity_id(r['name'], r['source']), 'name': r['name']}
                     for r in C.list_summaries('variantrule', sources=['XPHB'])] if ed == 'one' else [])
        return render_template('dnd5e/gmscreen.html', **ctx_common(
            ed=ed, conditions=entries('condition'), actions=entries('action'),
            budget_names=names, budget_rows=sorted(table.items()), glossary=glossary))

    @bp.get('/dnd5e/compendium')
    def compendium():
        cid_or_404()
        kinds = [k for k in CONTENT_KINDS if host.is_gm() or k not in GM_ONLY_KINDS]
        return render_template('dnd5e/compendium.html', **ctx_common(kinds=kinds, sources=_source_rows()))

    # --- content API ------------------------------------------------------------
    @bp.get('/api/dnd5e/content/<kind>')
    def api_content_search(kind):
        if kind not in CONTENT_KINDS:
            abort(404)
        # Players may browse beasts (Wild Shape, familiars, companions); the rest
        # of the bestiary stays GM-only so it can't spoil encounters.
        beasts_only = kind == 'monster' and not host.is_gm()
        if kind in GM_ONLY_KINDS and not host.is_gm() and not beasts_only:
            abort(403)
        q = (request.args.get('q') or '').strip()
        ed = request.args.get('edition') or None
        srcs = [s for s in (request.args.get('sources') or '').split(',') if s] or None
        limit = max(1, min(500, _i(request.args.get('limit'), 200)))
        if kind == 'monster':
            rows = C.search_monsters(
                q=q, edition=ed, sources=srcs, limit=limit, offset=max(0, _i(request.args.get('offset'))),
                cr_min=_f(request.args.get('cr_min')), cr_max=_f(request.args.get('cr_max')),
                types=(['beast'] if beasts_only else
                       [t for t in (request.args.get('types') or '').split(',') if t] or None),
                sizes=[s for s in (request.args.get('sizes') or '').split(',') if s] or None,
                legendary=_b(request.args.get('legendary')), environment=request.args.get('environment') or None)
        elif kind == 'spell':
            lv = request.args.get('levels')
            rows = C.search_spells(
                q=q, edition=ed, sources=srcs, limit=limit,
                levels=[_i(x) for x in lv.split(',')] if lv else None,
                school=request.args.get('school') or None,
                class_name=request.args.get('class') or None,
                class_source=request.args.get('class_source') or None,
                subclass_short=request.args.get('subclass') or None,
                ritual=_b(request.args.get('ritual')), concentration=_b(request.args.get('concentration')))
            for r in rows:
                r['school_name'] = SCHOOL_NAMES.get(r['school'], r['school'])
        elif kind == 'item':
            rows = C.search_items(q=q, edition=ed, sources=srcs, limit=limit,
                                  offset=max(0, _i(request.args.get('offset'))),
                                  rarity=request.args.get('rarity') or None, magic=_b(request.args.get('magic')),
                                  base=_b(request.args.get('base')), type_=request.args.get('type') or None,
                                  attunement=_b(request.args.get('attunement')))
        else:
            rows = C.list_summaries(kind, sources=srcs, edition=ed, q=q, limit=limit)
        if kind == 'monster':
            for r in rows:
                r['token'] = C.image_for('monster', r.get('id'), 'token')
        elif kind in ('item', 'race', 'background', 'feat', 'spell', 'class'):
            for r in rows:
                r['image'] = C.image_for(kind, r.get('id'))
        return jsonify(results=rows)

    @bp.get('/api/dnd5e/content/<kind>/<path:rid>')
    def api_content_get(kind, rid):
        if kind not in CONTENT_KINDS:
            abort(404)
        rec = C.get(kind, rid)
        if kind in GM_ONLY_KINDS and not host.is_gm():
            if not (kind == 'monster' and rec is not None and service.is_beast(rec)):
                abort(403)
        if rec is None and kind == 'item':
            rec = C.get_ref('item', rid)
        if rec is None:
            return jsonify(error='Not found'), 404
        return jsonify(render_record(kind, rec))

    @bp.post('/api/dnd5e/options')
    def api_options():
        """Resolve a builder choice query into concrete options."""
        b = body()
        return jsonify(options=_resolve_options(b.get('kind'), b.get('query') or {}, b.get('build') or {},
                                                b.get('choices') or {}))

    # --- character API ------------------------------------------------------------
    @bp.get('/api/dnd5e/pc/<pid>')
    def api_pc_get(pid):
        cid = cid_or_404()
        return jsonify(_sheet_payload(pc_or_404(cid, pid)))

    @bp.post('/api/dnd5e/pc/<pid>/preview')
    def api_pc_preview(pid):
        cid = cid_or_404()
        doc = pc_or_404(cid, pid)
        return jsonify(sheet=service.preview(doc, body()))

    @bp.post('/api/dnd5e/pc/<pid>/build')
    def api_pc_build(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.update_build(cid, pid, b.get('patch') or {}, expected_revision=b.get('revision'))
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/roll_scores')
    def api_pc_roll_scores(pid):
        cid = cid_or_404()
        doc, s = service.roll_ability_scores(cid, pid)
        host.broadcast('dnd5e_roll', {'pc_id': pid, 'name': doc.get('name'), 'label': 'ability scores',
                                      'expr': '4d6kh3 x6', 'total': ', '.join(map(str, doc['build']['rolled_scores'])),
                                      'natural': None, 'rolls': []},
                       player_filter=lambda _d: None, campaign_id=cid)
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/levelup')
    def api_pc_levelup(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.level_up(cid, pid, str(b.get('class') or '').lower(), b.get('hp'),
                                      roll_hp=bool(b.get('roll')))
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/leveldown')
    def api_pc_leveldown(pid):
        cid = cid_or_404()
        try:
            doc, s = service.level_down(cid, pid)
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/vitals')
    def api_pc_vitals(pid):
        cid = cid_or_404()
        b = body()
        try:
            live, ev = service.vitals_action(cid, pid, str(b.get('action') or ''), b)
        except service.ServiceError as e:
            return err(e)
        _log_to_encounter(cid, ev)
        after_pc_change(cid, pid, live, ev)
        return jsonify(live=live, events=ev)

    @bp.post('/api/dnd5e/pc/<pid>/resource')
    def api_pc_resource(pid):
        cid = cid_or_404()
        b = body()
        try:
            live, ev = service.resource_action(cid, pid, str(b.get('kind') or ''), b)
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, live, ev)
        return jsonify(live=live, events=ev)

    @bp.post('/api/dnd5e/pc/<pid>/rest')
    def api_pc_rest(pid):
        cid = cid_or_404()
        kind = 'long' if body().get('kind') == 'long' else 'short'
        try:
            live, ev = service.rest(cid, pid, kind)
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, live, ev)
        return jsonify(live=live, events=ev)

    @bp.post('/api/dnd5e/pc/<pid>/inventory')
    def api_pc_inventory(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.inventory_action(cid, pid, str(b.get('action') or ''), b)
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.get('/api/dnd5e/pc/<pid>/equipment')
    def api_pc_equipment(pid):
        cid = cid_or_404()
        return jsonify(service.equipment_packages(pc_or_404(cid, pid)))

    @bp.post('/api/dnd5e/pc/<pid>/equipment')
    def api_pc_equipment_take(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.grant_equipment(cid, pid, b.get('items') or [], gold_cp=b.get('gold_cp') or 0)
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/effect')
    def api_pc_effect(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.toggle_effect(cid, pid, str(b.get('key') or ''), bool(b.get('on')))
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/custom_resource')
    def api_pc_custom_resource(pid):
        cid = cid_or_404()
        try:
            doc, s = service.custom_resource(cid, pid, body())
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/extras')
    def api_pc_extras(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.extras_action(cid, pid, str(b.get('action') or ''), b, allow_any=host.is_gm())
        except service.ServiceError as e:
            return err(e)
        return jsonify(_sheet_payload(doc, s))

    @bp.post('/api/dnd5e/pc/<pid>/currency')
    def api_pc_currency(pid):
        cid = cid_or_404()
        doc, s = service.set_currency(cid, pid, body())
        return jsonify(currency=doc['state']['currency'])

    @bp.post('/api/dnd5e/pc/<pid>/notes')
    def api_pc_notes(pid):
        cid = cid_or_404()
        doc, _ = service.set_notes(cid, pid, body())
        return jsonify(notes=doc['state']['notes'])

    @bp.post('/api/dnd5e/pc/<pid>/setting')
    def api_pc_setting(pid):
        cid = cid_or_404()
        b = body()
        try:
            doc, s = service.set_flag(cid, pid, str(b.get('key')), b.get('value'))
        except service.ServiceError as e:
            return err(e)
        after_pc_change(cid, pid, service.pc_live(doc, s))
        return jsonify(_sheet_payload(doc, s))

    # --- rules data (not in git: systems/dnd5e/paths.py, datapack.py) -------------
    _NO_DATA_OK = {'dnd5e.admin_data', 'dnd5e.api_admin_data', 'dnd5e.dnd5e_image', 'dnd5e.dnd5e_art'}

    def is_site_admin():
        if not host.account_mode():
            return host.is_gm()
        u = host.current_user()
        return bool(u and u.get('is_admin'))

    @bp.before_request
    def _require_rules_data():
        if request.endpoint in _NO_DATA_OK or C.available():
            return None
        if request.path.startswith('/api/'):
            return jsonify(error='The D&D 5e rules data is not installed on this server yet.'), 503
        return render_template('dnd5e/no_data.html', is_admin=is_site_admin(),
                               d5_is_gm=host.is_gm(), d5_edition='one'), 503

    _ART_NAME = re.compile(r'^[0-9a-f]{20}\.webp$')

    @bp.get('/dnd5e/art/<sub>/<name>')
    def dnd5e_art(sub, name):
        """5etools tokens/art from the installed data (signed-in users only)."""
        if sub not in ('tokens', 'art') or not _ART_NAME.match(name):
            abort(404)
        p = os.path.join(paths.img_dir(), sub, name)
        if not os.path.isfile(p):
            abort(404)
        return send_file(p, mimetype='image/webp', max_age=30 * 86400)

    @bp.get('/dnd5e/admin/data')
    def admin_data():
        if not is_site_admin():
            abort(403)
        return render_template('dnd5e/admin_data.html', st=datapack.status(paths.install_dir()))

    @bp.post('/api/dnd5e/admin/data')
    def api_admin_data():
        """Install one bundle part (multipart ``file``)."""
        if not is_site_admin():
            abort(403)
        f = request.files.get('file')
        if f is None:
            return jsonify(error='Choose a bundle part.'), 400
        dest = paths.install_dir()
        os.makedirs(dest, exist_ok=True)
        tmp = os.path.join(dest, f'.upload-{uuid.uuid4().hex}')
        try:
            f.save(tmp)
            res = datapack.install(tmp, dest)
        except datapack.BundleError as e:
            return jsonify(error=str(e)), 422
        except OSError as e:
            return jsonify(error=f'Could not write the data ({e.strerror or e}).'), 500
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        C.reload()
        return jsonify(dict(res, status=datapack.status(dest)))

    @bp.get('/dnd5e/img/<name>')
    def dnd5e_image(name):
        """An uploaded picture of this campaign (re-encoded WebP, random name)."""
        cid = cid_or_404()
        p = images.path_for(cid, name)
        if p is None:
            abort(404)
        # names are never reused, so the file behind one never changes
        return send_file(p, mimetype='image/webp', max_age=30 * 86400)

    def _upload_bytes():
        f = request.files.get('file')
        if f is None:
            return None
        return f.stream.read(images.MAX_BYTES + 1)

    @bp.post('/api/dnd5e/pc/<pid>/portrait')
    def api_pc_portrait(pid):
        """Upload (multipart ``file``) or clear (``clear=1``) a character's portrait."""
        cid = cid_or_404()
        doc = pc_or_404(cid, pid)
        if not can_edit(doc):
            abort(403)
        name = None
        if not request.form.get('clear'):
            data = _upload_bytes()
            try:
                name = images.save_upload(cid, data or b'')
            except images.ImageError as e:
                return jsonify(error=str(e)), 422
        with store.pc_lock(cid, pid):
            doc = store.load_pc(cid, pid)
            if doc is None:
                images.delete(cid, name)
                abort(404)
            old = doc.get('portrait')
            if name:
                doc['portrait'] = name
            else:
                doc.pop('portrait', None)
            store.save_pc(cid, doc)
        if old and old != name:
            images.delete(cid, old)
        after_pc_change(cid, pid, service.pc_live(doc))
        return jsonify(portrait=images.url_for_name(name))

    @bp.post('/api/dnd5e/pc/<pid>/delete')
    def api_pc_delete(pid):
        cid = cid_or_404()
        doc = pc_or_404(cid, pid)
        if not can_edit(doc):
            abort(403)
        if pid in encounter_pc_ids(cid):
            enc = store.load_encounter(cid)
            for c in enc['combatants']:
                if c.get('pc_id') == pid:
                    service.combat_action(cid, 'remove', {'cid': c['cid']})
            emit_encounter(cid)
        store.delete_pc(cid, pid)
        images.delete(cid, doc.get('portrait'))
        return jsonify(ok=True)

    @bp.post('/api/dnd5e/pc/<pid>/initiative')
    def api_pc_initiative(pid):
        """A player enters (or rolls) their own initiative."""
        cid = cid_or_404()
        b = body()
        enc = store.load_encounter(cid)
        c = next((c for c in enc['combatants'] if c.get('pc_id') == pid), None)
        if c is None:
            return jsonify(error='Not in the encounter'), 404
        if b.get('value') is None:
            doc = pc_or_404(cid, pid)
            r = dice.d20(service.pc_live(doc)['initiative'], b.get('mode') or 'normal')
            value = r['total']
        else:
            value = _i(b.get('value'))
        enc, ev, _ = service.combat_action(cid, 'set_initiative', {'cid': c['cid'], 'initiative': value})
        emit_encounter(cid, enc)
        return jsonify(initiative=value)

    @bp.post('/api/dnd5e/pc/<pid>/roll')
    def api_pc_roll(pid):
        """Server-side roll for the sheet (so the GM can trust it) + GM feed."""
        cid = cid_or_404()
        b = body()
        doc = pc_or_404(cid, pid)
        expr = str(b.get('expr') or '1d20')
        try:
            r = dice.roll(expr)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        nat = None
        if expr.startswith('1d20') and r['rolls']:
            nat = r['rolls'][0][1]
        payload = {'pc_id': pid, 'name': doc.get('name'), 'label': str(b.get('label') or '')[:80],
                   'expr': expr, 'total': r['total'], 'natural': nat, 'rolls': r['rolls']}
        host.broadcast('dnd5e_roll', payload, player_filter=lambda _d: None, campaign_id=cid)
        return jsonify(payload)

    @bp.get('/api/dnd5e/combat')
    def api_combat_state():
        cid = cid_or_404()
        return jsonify(service.encounter_view(cid, for_player=not host.is_gm()))

    @bp.get('/api/dnd5e/party')
    def api_party():
        cid = cid_or_404()
        return jsonify(party=[service.pc_live(d) for d in store.list_pcs(cid)
                              if (d.get('build') or {}).get('levels')])

    # --- GM API -----------------------------------------------------------------
    def collect_unused_images(cid):
        try:
            # Keep the encounter reference snapshot stable until deletion ends.
            # New portrait uploads are protected by the collector's grace period.
            with store.encounter_lock(cid):
                docs = store.list_pcs(cid)
                try:
                    filenames = os.listdir(store.pc_dir(cid))
                except FileNotFoundError:
                    filenames = []
                expected = {f[:-5] for f in filenames if f.endswith('.json') and store.valid_id(f[:-5])}
                if expected - {d['id'] for d in docs}:
                    # list_pcs omits corrupt/unreadable documents; their image
                    # references are unknown, not evidence of unused files.
                    raise OSError('Character image references could not all be read')
                refs = [d.get('portrait') for d in docs]
                refs += [c.get('image') for c in store.load_encounter(cid)['combatants']]
                # Saved plans contain monster IDs, not uploaded image names. Add
                # plan image references here if plans acquire an image field.
                images.collect_garbage(cid, {name for name in refs if isinstance(name, str)})
        except OSError:
            current_app.logger.warning('Could not clean unused D&D 5e uploads', exc_info=True)

    @bp.post('/api/dnd5e/gm/combat/<action>')
    def api_gm_combat(action):
        cid = cid_or_404()
        b = body()
        if action == 'stage_plan':
            plan = next((p for p in store.list_plans(cid) if p['id'] == b.get('plan_id')), None)
            if not plan:
                return jsonify(error='Unknown plan'), 404
            if b.get('replace'):
                service.combat_action(cid, 'clear', {})
            for m in plan.get('monsters') or []:
                service.combat_action(cid, 'add_monster', {'monster_id': m['id'], 'count': m.get('count', 1),
                                                           'hidden': m.get('hidden', False)})
            enc, ev, touched = service.combat_action(cid, 'add_party', {})
        else:
            try:
                enc, ev, touched = service.combat_action(cid, action, b)
            except service.ServiceError as e:
                return err(e)
        if action in ('clear', 'remove') or (action == 'stage_plan' and b.get('replace')):
            collect_unused_images(cid)
        emit_encounter(cid, enc)
        for pid in touched:
            doc = store.load_pc(cid, pid)
            if doc:
                emit_pc(cid, service.pc_live(doc))
        emit_prompts(cid, ev)
        return jsonify(encounter=service.encounter_view(cid, enc), events=ev)

    @bp.get('/api/dnd5e/gm/combatant/<ccid>')
    def api_gm_combatant(ccid):
        """Full statblock / sheet summary for one combatant (on demand)."""
        cid = cid_or_404()
        enc = store.load_encounter(cid)
        c = K.find(enc, ccid)
        if c is None:
            return jsonify(error='No such combatant'), 404
        if c['kind'] == 'monster':
            rec = C.get('monster', c['monster_id'])
            return jsonify(kind='monster', record=render_record('monster', rec) if rec else None)
        if c['kind'] == 'pc':
            doc = store.load_pc(cid, c['pc_id'])
            return jsonify(kind='pc', sheet=_sheet_payload(doc) if doc else None)
        return jsonify(kind=c['kind'])

    @bp.post('/api/dnd5e/gm/combat/image')
    def api_gm_combat_image():
        """GM art for one combatant (a custom monster, an NPC): multipart
        ``file`` + ``cid``, or ``clear=1`` to go back to the default token."""
        cid = cid_or_404()
        ccid = request.form.get('cid') or ''
        if K.find(store.load_encounter(cid), ccid) is None:
            return jsonify(error='No such combatant'), 404
        name = None
        if not request.form.get('clear'):
            try:
                name = images.save_upload(cid, _upload_bytes() or b'', size=256)
            except images.ImageError as e:
                return jsonify(error=str(e)), 422
        found = []

        def fn(enc):
            c = K.find(enc, ccid)
            if c is None:
                return
            found.append(c)
            if name:
                c['image'] = name
            else:
                c.pop('image', None)
        store.mutate_encounter(cid, fn)
        if not found:
            images.delete(cid, name)
            return jsonify(error='No such combatant'), 404
        collect_unused_images(cid)
        emit_encounter(cid)
        return jsonify(image=images.url_for_name(name))

    @bp.route('/api/dnd5e/gm/plans', methods=['GET', 'POST'])
    def api_gm_plans():
        cid = cid_or_404()
        if request.method == 'GET':
            return jsonify(plans=store.list_plans(cid))
        b = body()
        monsters = []
        for m in (b.get('monsters') or [])[:60]:
            rec = C.get('monster', str(m.get('id') or '').lower())
            if rec:
                monsters.append({'id': entity_id(rec['name'], rec['source']), 'name': rec['name'],
                                 'count': max(1, min(20, _i(m.get('count'), 1))),
                                 'hidden': bool(m.get('hidden'))})
        plan = store.save_plan(cid, {'id': b.get('id'), 'name': str(b.get('name') or 'Encounter')[:80],
                                     'notes': str(b.get('notes') or '')[:4000], 'monsters': monsters,
                                     'edition': 'classic' if b.get('edition') == 'classic' else 'one'})
        return jsonify(plan=plan)

    @bp.post('/api/dnd5e/gm/plans/<plan_id>/delete')
    def api_gm_plan_delete(plan_id):
        cid = cid_or_404()
        return jsonify(ok=store.delete_plan(cid, plan_id))

    @bp.post('/api/dnd5e/gm/difficulty')
    def api_gm_difficulty():
        b = body()
        levels = [_i(x) for x in b.get('party_levels') or [] if str(x).isdigit() and 1 <= _i(x) <= 20]
        ids = []
        for m in b.get('monsters') or []:
            ids += [m.get('id')] * max(1, min(20, _i(m.get('count'), 1)))
        out = {ed: service.difficulty(ed, levels, ids) for ed in ('classic', 'one')}
        return jsonify(out)

    @bp.post('/api/dnd5e/gm/pc/<pid>/owner')
    def api_gm_pc_owner(pid):
        cid = cid_or_404()
        b = body()
        uid = b.get('user_id') or None
        camp = host.campaign_doc() or {}
        if uid and uid not in {m.get('user_id') for m in camp.get('members') or []}:
            return jsonify(error='Not a member of this campaign'), 422

        def fn(doc):
            doc['owner_user_id'] = uid
        try:
            store.mutate_pc(cid, pid, fn, fsync=True)
        except KeyError:
            abort(404)
        return jsonify(ok=True)

    # --- helpers that need the app context -------------------------------------
    def _log_to_encounter(cid, events):
        logs = [e for e in events if e.get('type') == 'log']
        if not logs:
            return
        enc = store.load_encounter(cid)
        if not enc['combatants']:
            return
        store.mutate_encounter(cid, lambda e: K.append_log(e, logs))

    return bp


# --- payload builders (module level: testable without a request) -----------

def player_prompts(payload: dict, enc: dict):
    """The player-facing copy of a dnd5e_prompt frame, or None to drop it.

    Players hear about turn changes of creatures they can see, and about PCs.
    Nothing naming a hidden creature gets through (its turn would otherwise
    announce it), and a monster's save reminder is GM business."""
    hidden_cids = {c['cid'] for c in enc['combatants'] if c.get('hidden')}
    hidden_names = {c['name'] for c in enc['combatants'] if c.get('hidden')}
    pc_names = {c['name'] for c in enc['combatants'] if c['kind'] == 'pc'}
    keep = [e for e in payload.get('events') or []
            if e.get('cid') not in hidden_cids and e.get('name') not in hidden_names
            and (e.get('type') == 'turn' or e.get('name') in pc_names or payload.get('pc_id'))]
    return dict(payload, events=keep) if keep else None


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _f(v):
    try:
        return float(v) if v not in (None, '') else None
    except ValueError:
        return None


def _b(v):
    if v in (None, ''):
        return None
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _summaries(kind):
    rows = C.list_summaries(kind)
    for r in rows:
        r['image'] = C.image_for(kind, r.get('id'))
    return rows


def _source_rows():
    return sorted(({'source': s['source'], 'full': s['full'], 'abv': s.get('abv') or s['source'],
                    'date': s.get('date')} for s in C.sources()), key=lambda s: (s['date'] or '9999', s['source']))


def _class_summaries():
    out = []
    for c in C.all_records('class'):
        out.append({'id': entity_id(c['name'], c['source']), 'name': c['name'], 'source': c['source'],
                    'edition': c.get('edition') or 'classic', 'hd': (c.get('hd') or {}).get('faces'),
                    'primary': c.get('primaryAbility'), 'caster': c.get('casterProgression'),
                    'saves': c.get('proficiency'), 'sidekick': c.get('isSidekick', False),
                    'image': C.image_for('class', entity_id(c['name'], c['source']))})
    out.sort(key=lambda c: (c['name'], c['source']))
    return out


def _sheet_payload(doc: dict, sheet: dict | None = None) -> dict:
    s = sheet or service.sheet_for(doc)
    return {'id': doc['id'], 'name': doc.get('name'), 'owner_user_id': doc.get('owner_user_id'),
            'revision': doc.get('revision', 0), 'build_rev': doc.get('build_rev', 0),
            'import_report': doc.get('import_report'),
            'build': doc.get('build') or {},
            'state': doc.get('state') or {}, 'details': doc.get('details') or {},
            'sheet': s, 'live': service.pc_live(doc, s),
            'features_html': [{'name': f['name'], 'level': f.get('level'), 'origin': f.get('origin'),
                               'html': render_entries(f.get('entries'))} for f in s['features']],
            'feats_html': [{'name': f['name'], 'origin': f['origin'], 'html': render_entries(f['entries'])}
                           for f in s['feats']],
            'optfeatures_html': [{'name': f['name'], 'group': f['group'], 'html': render_entries(f['entries'])}
                                 for f in s['optional_features']],
            'spells': _sheet_spells(doc, s),
            'extras': _extras(doc)}


def _extras(doc) -> list:
    out = []
    for e in (doc.get('state') or {}).get('extras') or []:
        mon = C.get('monster', e.get('monster_id') or '')
        out.append(dict(e, statblock=_statblock(mon) if mon else None))
    return out


def _sheet_spells(doc, s) -> list:
    """Every spell the character can cast/prepare, grouped per caster."""
    picks = (doc.get('build') or {}).get('choices') or {}
    out = []
    seen = set()
    mods = {a: s['abilities'][a]['mod'] for a in ABILITIES}
    fnames = {f['name'] for f in s['features']}
    invocations = {o['name'] for o in s['optional_features']}
    flags = set(s.get('option_flags') or [])

    def numbers(rec, caster):
        bonuses = SP.damage_bonuses(rec, sheet_feature_names=fnames, invocations=invocations, flags=flags,
                                    mods=mods, caster_class=caster['class'] if caster else None)
        return SP.spell_numbers(rec, char_level=s['level'],
                                ability_mod=mods[caster['ability']] if caster else 0, bonuses=bonuses)
    for caster in s['spellcasting']['casters']:
        base = f'spells:{caster["key"]}'
        ids = []
        for suffix in ('cantrips', 'prepared', 'known'):
            ids += [str(x).lower() for x in picks.get(f'{base}:{suffix}') or []]
        prepared = {str(x).lower() for x in picks.get(f'{base}:prepared') or []}
        cantrips = {str(x).lower() for x in picks.get(f'{base}:cantrips') or []}
        for sid in dict.fromkeys(ids):
            rec = C.get('spell', sid)
            if not rec:
                continue
            key = (caster['key'], sid)
            if key in seen:
                continue
            seen.add(key)
            row = _spell_row(rec, sid, caster, prepared=(sid in prepared or sid in cantrips
                                                         or caster['prepared_max'] is None))
            row['numbers'] = numbers(rec, caster)
            out.append(row)
    for a in s['spellcasting']['always']:
        rec = C.get('spell', a['id'])
        if rec:
            row = _spell_row(rec, a['id'], None, prepared=True, origin=a['origin'])
            row['numbers'] = numbers(rec, None)
            if a.get('ability') in mods:
                row['ability'] = a['ability']
            out.append(row)
    out += _item_spells(s, numbers)
    return out


def _item_spells(s, numbers) -> list:
    """Spells cast from equipped/attuned magic items (wands, staffs, rings).
    ``attachedSpells`` is either a plain list (at will / no cost given) or
    ``{"charges": {"1": [...], "5": [...]}, "will": [...], "daily": {...}}``."""
    rows = []
    for it in s['inventory']:
        if not it.get('active') or not it.get('id'):
            continue
        rec = C.get('item', it['id'])
        att = (rec or {}).get('attachedSpells')
        if not att:
            continue
        key = 'item:' + (it.get('uid') or f'i{it["index"]}')
        # Most spell items state a fixed save DC ("save DC 15") in their text.
        mdc = re.search(r'(?:\{@dc (\d+)\}|save DC (\d+))', ' '.join(map(str, _flat_text(rec.get('entries')))))
        item_dc = int(mdc.group(1) or mdc.group(2)) if mdc else None
        entries = []
        if isinstance(att, list):
            entries = [(sp, None) for sp in att]
        elif isinstance(att, dict):
            for cost, lst in (att.get('charges') or {}).items():
                entries += [(sp, int(cost)) for sp in lst if isinstance(sp, str)]
            entries += [(sp, None) for sp in att.get('will') or [] if isinstance(sp, str)]
            for _n, lst in (att.get('daily') or {}).items():
                entries += [(sp, None) for sp in lst if isinstance(sp, str)]
        for sp, cost in entries:
            sid = ENG.parse_ref(sp, 'spell')
            srec = C.get('spell', sid)
            if not srec:
                continue
            row = _spell_row(srec, sid, None, prepared=True, origin=rec['name'])
            row['numbers'] = numbers(srec, None)
            row['item'] = {'name': rec['name'], 'resource': key, 'cost': cost}
            if item_dc:
                row['dc'] = item_dc
            rows.append(row)
    return rows


def _spell_row(rec, sid, caster, *, prepared, origin=None):
    dur = rec.get('duration') or []
    conc = any(isinstance(d, dict) and d.get('concentration') for d in dur)
    t = (rec.get('time') or [{}])[0]
    rng = rec.get('range') or {}
    dist = rng.get('distance') or {}
    return {
        'id': sid, 'name': rec['name'], 'level': rec.get('level', 0), 'school': SCHOOL_NAMES.get(rec.get('school'), ''),
        'caster': caster['key'] if caster else None, 'caster_name': caster['name'] if caster else origin,
        'dc': caster['dc'] if caster else None, 'attack': caster['attack'] if caster else None,
        'prepared': prepared, 'always': caster is None, 'concentration': conc,
        'ritual': bool((rec.get('meta') or {}).get('ritual')),
        'time': f'{t.get("number", 1)} {t.get("unit", "")}'.strip(),
        'range': (f'{dist.get("amount", "")} {dist.get("type", "")}'.strip() if dist else rng.get('type', '')),
        'components': ''.join(k.upper() for k in ('v', 's', 'm') if (rec.get('components') or {}).get(k)),
        'duration': _duration_text(dur),
    }


def _duration_text(dur) -> str:
    if not dur:
        return ''
    d = dur[0]
    if d.get('type') == 'instant':
        return 'Instantaneous'
    if d.get('type') == 'timed':
        a = d.get('duration') or {}
        return f'{"Conc., " if d.get("concentration") else ""}{a.get("amount", "")} {a.get("type", "")}'.strip()
    return str(d.get('type', '')).title()


def render_record(kind: str, rec: dict) -> dict:
    """Display payload for any content record: pre-rendered HTML + key facts."""
    out = {'kind': kind, 'id': (subclass_id(rec) if kind == 'subclass' else entity_id(rec['name'], rec.get('source'))),
           'name': rec['name'], 'source': rec.get('source'), 'page': rec.get('page'),
           'edition': rec.get('edition')}
    out['image'] = C.image_for(kind, out['id'])
    if out['image'] is None and kind == 'item' and rec.get('baseItem'):
        out['image'] = C.image_for('item', parse_ref(rec['baseItem'], 'item'))      # "+1 Longsword" -> Longsword
    if kind == 'monster':
        out['token'] = C.image_for('monster', out['id'], 'token')
        out['statblock'] = _statblock(rec)
        return out
    meta = []
    if kind == 'spell':
        lvl = rec.get('level', 0)
        meta.append(f'{"Cantrip" if lvl == 0 else "Level " + str(lvl)} {SCHOOL_NAMES.get(rec.get("school"), "")}')
        t = (rec.get('time') or [{}])[0]
        meta.append(f'Casting Time: {t.get("number", 1)} {t.get("unit", "")}' + (' or Ritual' if (rec.get('meta') or {}).get('ritual') else ''))
        r = rec.get('range') or {}
        dist = r.get('distance') or {}
        meta.append('Range: ' + (f'{dist.get("amount", "")} {dist.get("type", "")}'.strip() if dist else r.get('type', '')))
        comp = rec.get('components') or {}
        cs = [k.upper() for k in ('v', 's') if comp.get(k)]
        if comp.get('m'):
            m = comp['m']
            cs.append('M (' + (m if isinstance(m, str) else m.get('text', '')) + ')')
        meta.append('Components: ' + ', '.join(cs))
        meta.append('Duration: ' + _duration_text(rec.get('duration') or []))
        body = render_entries(rec.get('entries'))
        if rec.get('entriesHigherLevel'):
            body += render_entries(rec['entriesHigherLevel'])
        out['html'] = body
        out['classes'] = sorted({f'{c["name"]}' for c in ((rec.get('classes') or {}).get('fromClassList') or [])})
    elif kind == 'item':
        bits = []
        if rec.get('rarity') and rec['rarity'] not in ('none', 'unknown'):
            bits.append(rec['rarity'].title())
        if rec.get('reqAttune'):
            bits.append('requires attunement' + (f' {strip_tags(rec["reqAttune"])}' if isinstance(rec['reqAttune'], str) else ''))
        if rec.get('weight'):
            bits.append(f'{rec["weight"]} lb.')
        if rec.get('value'):
            bits.append(_money(rec['value']))
        if rec.get('dmg1'):
            bits.append(f'{rec["dmg1"]} {rec.get("dmgType", "")}' + (f' (versatile {rec["dmg2"]})' if rec.get('dmg2') else ''))
        if rec.get('ac'):
            bits.append(f'AC {rec["ac"]}')
        meta.append(', '.join(bits))
        out['html'] = render_entries(rec.get('entries'))
    elif kind == 'feat':
        if rec.get('prerequisite'):
            meta.append('Prerequisite: ' + _prereq_text(rec['prerequisite']))
        out['html'] = render_entries(rec.get('entries'))
    elif kind == 'class':
        out['html'] = _class_overview(rec)
    else:
        out['html'] = render_entries(rec.get('entries'))
    out['meta'] = [m for m in meta if m]
    return out


def _money(cp) -> str:
    cp = int(cp)
    if cp % 100 == 0:
        return f'{cp // 100} gp'
    if cp % 10 == 0:
        return f'{cp // 10} sp'
    return f'{cp} cp'


def _prereq_text(pr) -> str:
    parts = []
    for alt in pr:
        bits = []
        for k, v in alt.items():
            if k == 'level':
                bits.append(f'Level {v if isinstance(v, int) else v.get("level")}')
            elif k == 'ability':
                bits.append(' or '.join(', '.join(f'{a.upper()} {n}' for a, n in d.items()) for d in v))
            elif k.startswith('spellcasting'):
                bits.append('Spellcasting feature')
            elif k == 'race':
                bits.append(' or '.join(r.get('name', '').title() for r in v))
            elif k == 'proficiency':
                bits.append(' or '.join(f'{list(d.values())[0]} {list(d.keys())[0]} proficiency' for d in v))
            elif k == 'feat':
                bits.append(' or '.join(f.split('|')[0].title() for f in v))
            elif k in ('other', 'otherSummary'):
                bits.append(strip_tags(v if isinstance(v, str) else v.get('entry', '')))
        parts.append(', '.join(b for b in bits if b))
    return '; or '.join(p for p in parts if p)


def _class_overview(rec) -> str:
    rows = []
    for lvl, grp in enumerate(rec.get('classFeatures') or [], start=1):
        names = [f['name'] for f in ENG._features_of(grp) if not f.get('variant')]
        rows.append(f'<tr><td>{lvl}</td><td>{render_text(", ".join(names))}</td></tr>')
    return ('<table class="d5-table"><thead><tr><th>Level</th><th>Features</th></tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table>')


def _statblock(m: dict) -> dict:
    """A monster as display-ready pieces (both 2014 and 2024 layouts)."""
    from systems.dnd5e.rules.tables import ability_mod, xp_for_cr
    t = m.get('type')
    if isinstance(t, dict):
        tags = t.get('tags') or []
        tname = t.get('type')
        if isinstance(tname, dict):
            tname = ' or '.join(tname.get('choose') or [])
        tname = f'{tname}' + (f' ({", ".join(x if isinstance(x, str) else x.get("tag", "") for x in tags)})' if tags else '')
    else:
        tname = t or ''
    size = ' or '.join(SIZE_NAMES.get(s, s) for s in m.get('size') or [])
    align = _alignment(m.get('alignment'))
    ac = m.get('ac') or []
    ac_txt = ', '.join((str(a) if not isinstance(a, dict) else
                        f'{a.get("ac")}' + (f' ({strip_tags(", ".join(a.get("from", [])))})' if a.get('from') else '')
                        + (f' {strip_tags(a.get("condition", ""))}' if a.get('condition') else ''))
                       for a in ac if not (isinstance(a, dict) and a.get('special')))
    hp = m.get('hp') or {}
    hp_txt = f'{hp.get("average")} ({hp.get("formula")})' if hp.get('average') is not None else strip_tags(hp.get('special', ''))
    sp = m.get('speed') or {}
    speed = ', '.join((f'{k} ' if k != 'walk' else '') + (f'{v} ft.' if isinstance(v, int) else
                       (f'{v.get("number")} ft. {v.get("condition", "")}'.strip() if isinstance(v, dict) else ''))
                      for k, v in sp.items() if k not in ('canHover', 'alternate', 'choose') and v not in (True, None))
    abil = {a: {'score': m.get(a, 10), 'mod': ability_mod(m.get(a, 10)),
                'save': (m.get('save') or {}).get(a)} for a in ABILITIES}
    cr = m.get('cr')
    cr_s = cr.get('cr') if isinstance(cr, dict) else cr
    lines = []

    def dict_line(label, d):
        if d:
            lines.append((label, ', '.join(f'{k.title()} {v}' for k, v in d.items() if isinstance(v, str))))

    dict_line('Saving Throws', m.get('save'))
    dict_line('Skills', m.get('skill'))
    for label, key in (('Vulnerabilities', 'vulnerable'), ('Resistances', 'resist'),
                       ('Immunities', 'immune'), ('Condition Immunities', 'conditionImmune')):
        vals = K._flatten_defense(m.get(key))
        if vals:
            lines.append((label, ', '.join(v.title() for v in vals)))
    senses = list(m.get('senses') or [])
    if m.get('passive') is not None:
        senses.append(f'Passive Perception {m["passive"]}')
    if senses:
        lines.append(('Senses', strip_tags(', '.join(senses))))
    lines.append(('Languages', strip_tags(', '.join(m.get('languages') or [])) or '—'))
    xp = xp_for_cr(cr_s)
    lines.append(('Challenge', f'{cr_s} ({xp:,} XP)' if xp is not None else str(cr_s or '—')))
    sections = []
    for key, title in (('trait', 'Traits'), ('action', 'Actions'), ('bonus', 'Bonus Actions'),
                       ('reaction', 'Reactions'), ('legendary', 'Legendary Actions'), ('mythic', 'Mythic Actions')):
        blocks = m.get(key) or []
        if key == 'trait' and m.get('spellcasting'):
            blocks = list(blocks) + [dict(s, type='spellcasting') for s in m['spellcasting']
                                     if (s.get('displayAs') or 'trait') == 'trait']
        if key == 'action' and m.get('spellcasting'):
            blocks = list(blocks) + [dict(s, type='spellcasting') for s in m['spellcasting']
                                     if s.get('displayAs') == 'action']
        if not blocks:
            continue
        html = ''
        if key == 'legendary' and m.get('legendaryHeader'):
            html += render_entries(m['legendaryHeader'])
        for b in blocks:
            if b.get('type') == 'spellcasting':
                html += render_entries([b], {'label': m['name']})
            else:
                inner = list(b.get('entries') or [])
                html += render_entries([{'type': 'entries', 'name': b.get('name'), 'entries': inner}],
                                       {'label': f'{m["name"]}: {strip_tags(b.get("name") or "")}'})
        sections.append({'title': title, 'html': html})
    return {
        'name': m['name'], 'source': m.get('source'), 'line': f'{size} {tname}'.strip() + (f', {align}' if align else ''),
        'image': C.image_for('monster', entity_id(m['name'], m.get('source'))),
        'token': C.image_for('monster', entity_id(m['name'], m.get('source')), 'token'),
        'ac': ac_txt, 'hp': hp_txt, 'speed': speed,
        'initiative': K.monster_init_bonus(m),
        'abilities': abil, 'lines': lines, 'sections': sections, 'cr': cr_s,
    }


_ALIGN = {'L': 'lawful', 'N': 'neutral', 'NX': 'neutral', 'NY': 'neutral', 'C': 'chaotic', 'G': 'good',
          'E': 'evil', 'U': 'unaligned', 'A': 'any alignment'}


def _alignment(a) -> str:
    if not a:
        return ''
    if all(isinstance(x, str) for x in a):
        if a == ['N']:
            return 'neutral'
        return ' '.join(_ALIGN.get(x, x) for x in a)
    return strip_tags(', '.join(str(x.get('special') or ' '.join(_ALIGN.get(y, y) for y in x.get('alignment') or []))
                                for x in a if isinstance(x, dict)))


def _resolve_options(kind: str, query: dict, build: dict, choices: dict) -> list:
    """Concrete options for builder choices whose pool is a query."""
    ed = build.get('edition') or 'one'
    sources = build.get('sources') or None
    if kind == 'feat':
        cats = query.get('category')
        if isinstance(cats, str):
            cats = [cats]
        out = []
        for f in C.all_records('feat', sources=sources):
            fc = f.get('category')
            # Exact category match: a Fighter's ["FS"] must not offer the
            # Paladin-only "FS:P" or Ranger-only "FS:R" styles.
            if cats and fc not in cats:
                continue
            if not cats and fc in ('EB', 'FS', 'FS:P', 'FS:R', 'D') and ed == 'one':
                # general ASI picks never offer Fighting Style / Epic Boon /
                # Dragonmark feats unless the slot asks for that category
                if fc not in ('EB',):
                    continue
            out.append({'value': entity_id(f['name'], f['source']), 'label': f['name'], 'source': f['source'],
                        'edition': f.get('edition') or ('one' if f['source'] in ('XPHB',) else 'classic'),
                        'category': fc, 'prerequisite': _prereq_text(f['prerequisite']) if f.get('prerequisite') else '',
                        'summary': strip_tags(_first_text(f.get('entries')))[:240]})
        out.sort(key=lambda o: (o['label'], o['source']))
        return out
    if kind == 'optionalfeature':
        types = set(query.get('featureType') or [])
        out = []
        for o in C.all_records('optionalfeature', sources=sources):
            if not types & set(o.get('featureType') or []):
                continue
            out.append({'value': entity_id(o['name'], o['source']), 'label': o['name'], 'source': o['source'],
                        'prerequisite': _prereq_text(o['prerequisite']) if o.get('prerequisite') else '',
                        'summary': strip_tags(_first_text(o.get('entries')))[:240]})
        out.sort(key=lambda o: (o['label'], o['source']))
        return out
    if kind == 'spell':
        if query.get('from_choice'):
            ids = [str(x).lower() for x in choices.get(query['from_choice']) or []]
            out = []
            for sid in ids:
                r = C.get('spell', sid)
                if r:
                    out.append({'value': sid, 'label': r['name'], 'source': r['source'], 'level': r.get('level')})
            return out
        if query.get('filter'):
            # "level=0|class=Wizard" style filters from additionalSpells
            f = dict(p.split('=', 1) for p in query['filter'].split('|') if '=' in p)
            levels = [int(x) for x in f.get('level', '').split(';') if x.isdigit()] or None
            cls = f.get('class')
            rows = C.search_spells(levels=levels, class_name=cls.title() if cls else None,
                                   class_source='XPHB' if ed == 'one' else 'PHB', sources=sources, limit=500)
        elif query.get('any_class') == '*':
            rows = C.search_spells(levels=query.get('levels'), sources=sources, limit=1500)
        elif query.get('any_class'):
            rows, have = [], set()
            for cls in query['any_class']:
                for r in C.search_spells(levels=query.get('levels'), class_name=cls,
                                         class_source=query.get('class_source'), sources=sources, limit=800):
                    if r['id'] not in have:
                        have.add(r['id'])
                        rows.append(r)
            rows.sort(key=lambda r: (r['level'], r['name']))
        else:
            rows = C.search_spells(levels=query.get('levels'), class_name=query.get('class'),
                                   class_source=query.get('class_source'), subclass_short=query.get('subclass'),
                                   sources=sources, limit=800)
            extra = query.get('extra') or []
            if extra:
                have = {r['id'] for r in rows}
                for sid in extra:
                    r = C.get('spell', sid)
                    if r and sid not in have and r.get('level') in (query.get('levels') or []):
                        rows.append({'id': sid, 'name': r['name'], 'source': r['source'], 'level': r.get('level'),
                                     'school': r.get('school')})
        return [{'value': r['id'], 'label': r['name'], 'source': r['source'], 'level': r.get('level'),
                 'school': SCHOOL_NAMES.get(r.get('school'), '')} for r in rows]
    return []


def _flat_text(entries) -> list:
    out = []
    if isinstance(entries, str):
        out.append(entries)
    elif isinstance(entries, list):
        for e in entries:
            out += _flat_text(e)
    elif isinstance(entries, dict):
        out += _flat_text(entries.get('entries'))
        out += _flat_text(entries.get('items'))
    return out


def _first_text(entries) -> str:
    for e in entries or []:
        if isinstance(e, str):
            return e
        if isinstance(e, dict):
            t = _first_text(e.get('entries'))
            if t:
                return t
    return ''
