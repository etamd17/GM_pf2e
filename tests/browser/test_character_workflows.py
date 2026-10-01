"""Focused PR5 real user journeys, not a whole-site UI audit."""
import io
import json
import re
import uuid

import pytest

pytestmark = pytest.mark.browser


def expect_saved(page):
    from playwright.sync_api import expect
    try:
        expect(page.locator('#draft-status')).to_have_attribute('data-state', 'saved', timeout=12000)
    except AssertionError as error:
        raise AssertionError(str(error) + '\nBrowser errors: ' + repr(page.workflow_errors) +
                             '\nWorkflow requests: ' + repr(page.workflow_network[-12:])) from None


def pdf_bytes(name):
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    fields = []
    for key, value in {'char_name':name,'char_level':'1','char_ancestry':'Human','char_paths':'Warrior'}.items():
        fields.append(writer._add_object(DictionaryObject({NameObject('/FT'):NameObject('/Tx'),
            NameObject('/T'):TextStringObject(key),NameObject('/V'):TextStringObject(value)})))
    writer._root_object[NameObject('/AcroForm')] = writer._add_object(DictionaryObject({NameObject('/Fields'):ArrayObject(fields)}))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def picker(page, opener, name):
    page.locator(opener).click()
    page.locator('#mod-search').fill(name)
    page.locator('#mod-list').get_by_text(name, exact=True).first.click()
    page.get_by_role('button', name=re.compile('Lock In')).click()


def complete_builder(page, site, name):
    page.locator(site.name_input).fill(name)
    if site.system == 'cosmere':
        page.locator('.wiz-tab[data-i="3"]').click()
        page.locator('#f-path').select_option('warrior')
        page.locator('#btn-save-top').click()
        return
    page.locator('#nav-ancestry').click()
    picker(page, '[onclick="openModal(\'ancestry\')"]', 'Human')
    picker(page, '#wrap-heritage', 'Skilled Human')
    page.locator('#sel-anc-method').select_option('alternate')
    page.locator('#anc-fb-str').click()
    page.locator('#anc-fb-dex').click()
    picker(page, '[onclick="openModal(\'ancestry_feat\')"]', 'Haughty Obstinacy')
    page.locator('#nav-background').click()
    picker(page, '[onclick="openModal(\'background\')"]', 'Guard')
    page.locator('#bg-spec-str').click()
    page.locator('#bg-free-con').click()
    page.locator('#nav-class').click()
    picker(page, '[onclick="openModal(\'class\')"]', 'Fighter')
    page.locator('#key-str').click()
    if page.locator('#wrap-subclass').is_visible():
        picker(page, '#wrap-subclass', 'Two-Handed')
    picker(page, '#wrap-cls-feat-container .choice-card', 'Sudden Charge')
    page.locator('#nav-abilities').click()
    for stat in ('str','dex','con','wis'):
        page.locator('#fin-btn-' + stat).click()
    page.locator('#nav-skills').click()
    remaining = int(page.locator('#lbl-skill-remaining').inner_text().split()[0])
    for checkbox in page.locator('.sk-chk').all()[:remaining]:
        checkbox.check()
    choice = page.get_by_placeholder('Type choice here...')
    if choice.count():
        choice.fill('City guard')
    assert page.locator('#btn-finish').is_enabled(), page.evaluate("JSON.stringify({state,checks:Array.from(document.querySelectorAll('[id^=chk-]')).map(e=>[e.id,e.innerText]),classData:bData.classes[state.cls]})")
    page.locator('#draft-save').click()
    expect_saved(page)
    chosen = page.evaluate('collectPf2eDraft()')
    page.reload()
    expect_saved(page)
    assert page.evaluate('collectPf2eDraft()') == chosen
    page.locator('#btn-finish').click()


def test_player_builder_and_import_journeys(site, pages):
    page = pages()
    mutations = []
    page.on('request', lambda request: mutations.append(request.url) if request.method == 'POST' else None)
    page.goto(site.url + site.builder)
    def inspect_publication(route):
        # A delayed/lost publication response must not accept edits that the
        # acknowledged publication snapshot cannot possibly contain.
        assert page.locator(site.name_input).evaluate("el => !!el.closest('[inert]')")
        route.continue_()
    page.route('**/api/character-workflows/drafts/*/publish', inspect_publication)
    name = 'Browser builder ' + uuid.uuid4().hex[:8]
    complete_builder(page, site, name)
    page.wait_for_url('**/characters/*')
    page.unroute('**/api/character-workflows/drafts/*/publish', inspect_publication)
    assert name in page.locator('body').inner_text()
    page.goto(site.url + site.roster)
    page.locator('#workflow-import summary').click()
    imported = 'Browser import ' + uuid.uuid4().hex[:8]
    if site.system == 'pf2e':
        page.locator('#workflow-import-paste').fill(json.dumps({'build':{
            'name':imported,'class':'Fighter','ancestry':'Human','level':1}}))
    else:
        page.locator('#workflow-import-file').set_input_files({'name':'hero.pdf','mimeType':'application/pdf','buffer':pdf_bytes(imported)})
    page.locator('#workflow-import-prepare').click()
    page.wait_for_url('**/*builder?draft_id=*')
    assert 'Confirm character import' in page.locator('body').inner_text()
    page.locator('#import-confirm').click()
    page.wait_for_url('**/characters/*')
    assert imported in page.locator('body').inner_text()
    assert not any('/api/save_new_character' in url or '/api/import_pathbuilder' in url or url.endswith('/cosmere/builder') for url in mutations)


def test_refresh_and_restart_resume(site, pages):
    page = pages()
    page.goto(site.url + site.builder)
    name = 'Resume ' + uuid.uuid4().hex[:8]
    page.locator(site.name_input).fill(name)
    if site.system == 'pf2e':
        page.locator('#inp-deity').fill('Iomedae')
        page.locator('#nav-ancestry').click()
        picker(page, '[onclick="openModal(\'ancestry\')"]', 'Human')
        page.locator('#sel-anc-method').select_option('alternate')
        page.locator('#anc-fb-dex').click()
    else:
        page.locator('.wiz-tab[data-i="3"]').click()
        page.locator('#f-path').select_option('warrior')
        page.locator('.tnode.avail').first.click()
        page.get_by_role('button', name='Add to build', exact=True).click()
        assert page.evaluate('TALENTS.length') >= 2
    page.locator('#draft-save').click()
    expect_saved(page)
    url = page.url
    saved = page.evaluate('collectPf2eDraft()' if site.system == 'pf2e' else 'collectCosmereDraft()')
    page.reload()
    expect_saved(page)
    assert page.evaluate('collectPf2eDraft()' if site.system == 'pf2e' else 'collectCosmereDraft()') == saved
    site.restart()
    page.goto(url)
    expect_saved(page)
    assert page.evaluate('collectPf2eDraft()' if site.system == 'pf2e' else 'collectCosmereDraft()') == saved
    assert page.locator(site.name_input).input_value() == name


def test_two_tab_conflict_and_retry(site, pages):
    from playwright.sync_api import expect
    page = pages()
    page.goto(site.url + site.builder)
    page.locator(site.name_input).fill('Conflict ' + uuid.uuid4().hex[:8])
    page.locator('#draft-save').click()
    expect_saved(page)
    second = pages()
    second.goto(page.url)
    expect_saved(second)
    page.locator(site.name_input).fill('First tab')
    page.locator('#draft-save').click()
    expect_saved(page)
    second.locator(site.name_input).fill('Keep second tab')
    second.locator('#draft-save').click()
    expect(second.locator('#draft-status')).to_have_attribute('data-state','conflict')
    assert second.locator(site.name_input).input_value() == 'Keep second tab'
    second.locator('#draft-copy').click()
    expect_saved(second)
    assert second.url != page.url
    assert second.locator(site.name_input).input_value() == 'Keep second tab'
    # Lose a genuine successful PATCH response, then reconcile via GET.
    dropped = []
    def lose_response(route):
        if route.request.method == 'PATCH' and not dropped:
            dropped.append(True)
            response = route.fetch(timeout=4000)
            assert response.status == 200, response.text()
            route.abort()
        else:
            route.continue_()
    # Keep interception installed for reconciliation: removing a one-shot
    # handler during abort can strand Chromium's immediately following GET.
    second.route('**/api/character-workflows/drafts/*', lose_response)
    second.locator(site.name_input).fill('Recovered response')
    second.locator('#draft-save').click()
    expect_saved(second)
    second.reload()
    expect_saved(second)
    assert second.locator(site.name_input).input_value() == 'Recovered response'


def test_keyboard_status_and_capability_controls(site, pages):
    from playwright.sync_api import expect
    page = pages()
    page.goto(site.url + site.builder)
    page.locator(site.name_input).fill('Keyboard ' + uuid.uuid4().hex[:8])
    page.locator('#draft-save').focus()
    page.keyboard.press('Enter')
    expect_saved(page)
    assert page.locator('#draft-status').get_attribute('role') == 'status'
    assert page.locator('#draft-status').get_attribute('aria-live') == 'polite'
    assert page.locator('#chk-gm-override').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.on('dialog', lambda dialog: dialog.dismiss())
    page.locator('#draft-discard').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#draft-discard')).to_be_focused()
    # Server refusal is visible, focused and retains editable input.
    page.route('**/api/character-workflows/drafts/*', lambda route:route.fulfill(
        status=403,content_type='application/json',body=json.dumps({'error':{'code':'membership_required','message':'Access ended','issues':[]}})),times=1)
    page.locator(site.name_input).fill('Preserved after access loss')
    page.locator('#draft-save').click()
    expect(page.locator('#draft-status')).to_have_attribute('data-state','forbidden')
    expect(page.locator('#draft-status')).to_be_focused()
    expect(page.locator('#draft-save')).to_be_disabled()
    for button in page.locator('[data-workflow-finish]').all():
        expect(button).to_be_hidden()
    assert page.locator(site.name_input).input_value() == 'Preserved after access loss'
    # Real server grants, distinct account contexts, and rendered private data.
    name = 'Roles ' + uuid.uuid4().hex[:8]
    submission = ({'name':name,'class_name':'Fighter','ancestry':'Human','abilities':{},'feats':[]}
                  if site.system == 'pf2e' else {'build':{'name':name,'level':1,'path':'warrior'}})
    result = page.evaluate('''async inputs => {
      const created=await fetch('/api/character-workflows/drafts',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({inputs,request_key:crypto.randomUUID()})});
      const d=await created.json();
      if(!created.ok) throw new Error(JSON.stringify(d));
      const result=await fetch('/api/character-workflows/drafts/'+d.draft.id+'/publish',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({expected_revision:0,request_key:crypto.randomUUID()})});
      const body=await result.json();if(!result.ok) throw new Error(JSON.stringify(body));return body;
    }''', {'kind':site.system+'_builder','payload_version':1,'form':{},'ui':{},'submission':submission})
    site.python('''
from pathlib import Path
import json
from core import storage
from core.persistence import runtime
from core.persistence.models import CharacterAssignment
cid, chid, system, users = ''' + repr((site.cid,result['character_id'],site.system,site.users)) + '''
directory = storage.party_dir(cid) if system == 'pf2e' else storage.cosmere_pc_dir(cid)
path = Path(directory) / (chid + '.json')
document = json.loads(path.read_text())
document['build']['notes'] = 'BROWSER OWNER PRIVATE NOTE'
storage.atomic_write_json(str(path), document)
if runtime.sql_enabled():
    with runtime.database().transaction() as db:
        for role in ('editor','viewer'):
            db.add(CharacterAssignment(campaign_id=cid,character_id=chid,user_id=users[role],role=role))
''')
    # Reload runtime from the test-only file update before inspecting privacy.
    site.restart()
    for role in ('editor','viewer'):
        member = pages(role)
        response = member.goto(site.url + result['url'])
        permitted = site.backend == 'sql' and (role == 'editor' or site.system == 'pf2e')
        assert response.status == (200 if permitted else 403)
        assert 'BROWSER OWNER PRIVATE NOTE' not in member.locator('body').inner_text()
        if permitted and role == 'viewer':
            assert 'Read-only character sheet' in member.locator('body').inner_text()
