"""Chronicle player reading routes + nav gate (PR1, Part 5). Subprocess isolation
with a throwaway DATA_DIR; GM_PASSWORD='' == legacy-open == caller is the GM."""
import os
import sys
import textwrap
import subprocess

import app as A

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


_SEED = '''
import os, json
def seed_chronicle(chronicle_dir, pages, *, session_number=3, html=None, assets=None):
    """Write chronicle_dir/current -> content/<h>/{manifest.json, html/<slug>.html, assets/*}."""
    h = 'deadbeef' * 8
    content = os.path.join(chronicle_dir, 'content', h)
    os.makedirs(os.path.join(content, 'html'), exist_ok=True)
    os.makedirs(os.path.join(content, 'assets'), exist_ok=True)
    manifest = {'schema_version': 1, 'session_number': session_number,
                'generated_at': '2026-07-15T00:00:00Z', 'pages': pages,
                'mysteries': [], 'calendar': {}, 'fieldguide': [], 'spine': []}
    with open(os.path.join(content, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)
    for slug, frag in (html or {}).items():
        with open(os.path.join(content, 'html', slug + '.html'), 'w') as f:
            f.write(frag)
    for rel, data in (assets or {}).items():
        with open(os.path.join(content, 'assets', rel), 'wb') as f:
            f.write(data)
    link = os.path.join(chronicle_dir, 'current'); tmp = link + '.tmp'
    if os.path.islink(tmp): os.unlink(tmp)
    os.symlink(content, tmp); os.replace(tmp, link)
    return content
'''


# ---- Task 1: chronicle_published context processor + nav-tab swap ---------
#
# NOTE on GM_PASSWORD: the brief's harness note says "GM_PASSWORD='' ==
# legacy-open == caller is the GM" -- but that means `_is_gm()` is
# UNCONDITIONALLY True in that mode (app.py:392-403, `(not GM_PASSWORD) or
# ...`), and the player bottom nav only renders for `not is_gm and
# player_name` (base.html:486). Under GM_PASSWORD='' a GET /notes never
# renders the player nav at all -- any '>Notes<' match in that mode is a
# false positive from unrelated page JS (a safety-tools string), not the nav
# tab. To exercise the ACTUAL nav swap we mirror
# test_chronicle_auth.py::test_chronicle_gate_legacy_password_mode: a
# non-empty GM_PASSWORD plus a `session['player_name']` (no
# `gm_authenticated`), which is a genuine non-GM joined player and makes the
# nav render for real. The /chronicle route itself doesn't exist until Task
# 24, so both before/after checks hit /notes (whose bottom nav is shared
# chrome injected by base.html on every player page, including /notes).
def test_nav_shows_notes_before_publish_and_chronicle_after():
    # NB: these lines are intentionally flush-left (not indented to match the
    # surrounding Python) -- `_run` concatenates this onto `_SEED`, which is
    # itself flush-left at module scope, and `textwrap.dedent` strips the
    # LONGEST COMMON leading whitespace across the *whole* combined string.
    # Since `_SEED` already contains flush-left top-level lines, dedent is a
    # no-op; indenting this block would desync from `_SEED`'s closing lines
    # and raise IndentationError when the subprocess parses it.
    r = _run(_SEED + '''
import tempfile, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'sekret'
import app as A
c = A.app.test_client()
with c.session_transaction() as s:
    s['player_name'] = 'Aria'

def nav_slice(html):
    # Scope assertions to the <nav id="player-nav">...</nav> element only --
    # an unrelated JS template string elsewhere on the page (_sse_hub.html's
    # safety-tools panel) also contains the literal text "<h4>Notes</h4>",
    # so a whole-page ">Notes<" search is a false positive trap.
    start = html.find(b'<nav id="player-nav"')
    end = html.find(b'</nav>', start)
    assert start != -1 and end != -1, 'player nav did not render at all'
    return html[start:end]

# pre-publish: the player nav renders, showing Notes (not Chronicle).
pre = nav_slice(c.get('/notes').data)
assert b'>Notes<' in pre and b'>Chronicle<' not in pre, 'pre-publish nav wrong'
assert b'href="/notes"' in pre and b'href="/chronicle"' not in pre

# after a publish: Chronicle replaces Notes in the same nav.
seed_chronicle(A.CHRONICLE_DIR, [{'slug':'home','section':'home','title':'Home','recipients':'all'}],
               html={'home':'<p>hi</p>'})
post = nav_slice(c.get('/notes').data)
assert b'>Chronicle<' in post and b'>Notes<' not in post, 'post-publish nav wrong'
assert b'href="/chronicle"' in post and b'href="/notes"' not in post
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ---- Supplementary: direct-render check that BOTH nav partials (pf2e and
# cosmere) got the identical gated swap, independent of the app-route
# plumbing exercised above (mirrors tests/test_nav_home_and_session_entry.py's
# render-the-partial-directly style). A tiny request stub stands in for
# Flask's `request` since the Chronicle tab's active-state check reads
# `request.path` directly (bypassing Flask's real request context, which
# `app.jinja_env.get_template(...).render()` does not push).
class _FakeRequest:
    def __init__(self, path):
        self.path = path


def _render(name, **ctx):
    ctx.setdefault('request', _FakeRequest('/notes'))
    return A.app.jinja_env.get_template(name).render(**ctx)


def test_pf2e_nav_partial_swaps_notes_for_chronicle():
    off = _render('_player_nav.html', is_gm=False, player_name='Kyle',
                  active_player_tab='sheet', chronicle_published=False, account_user=None)
    on = _render('_player_nav.html', is_gm=False, player_name='Kyle',
                 active_player_tab='sheet', chronicle_published=True, account_user=None)
    assert '>Notes<' in off and 'href="/notes"' in off and '>Chronicle<' not in off
    assert '>Chronicle<' in on and 'href="/chronicle"' in on
    assert '>Notes<' not in on and 'href="/notes"' not in on


def test_cosmere_nav_partial_swaps_notes_for_chronicle():
    off = _render('_cosmere_player_nav.html', active_player_tab='sheet',
                  cosmere_player_char='Shanadin', chronicle_published=False, account_user=None)
    on = _render('_cosmere_player_nav.html', active_player_tab='sheet',
                 cosmere_player_char='Shanadin', chronicle_published=True, account_user=None)
    assert '>Notes<' in off and 'href="/notes"' in off and '>Chronicle<' not in off
    assert '>Chronicle<' in on and 'href="/chronicle"' in on
    assert '>Notes<' not in on and 'href="/notes"' not in on


# ---- Task 2: reader helpers + `/chronicle` Home ----------------------------

def test_home_renders_after_publish():
    # NB: flush-left, same reason as test_nav_shows_notes_before_publish_and_chronicle_after
    # above -- this body is concatenated onto _SEED (itself flush-left at module
    # scope), and textwrap.dedent strips the LONGEST COMMON leading whitespace
    # across the combined string, which is zero once _SEED is in the mix.
    r = _run(_SEED + '''
import tempfile, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
seed_chronicle(A.CHRONICLE_DIR,
    [{'slug':'home','section':'home','title':'The Story So Far','recipients':'all'},
     {'slug':'s03','section':'recap','title':'Session 3','recipients':'all','session_updated':3}],
    html={'home':'<p>Home body.</p>', 's03':'<p>They fled north.</p>'})
c = A.app.test_client()
rv = c.get('/chronicle')
assert rv.status_code == 200, rv.status_code
assert b'They fled north.' in rv.data          # latest recap fragment injected
assert b'As of Session 3' in rv.data           # session stamp from chronicle_base
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


def test_home_empty_state_when_unpublished():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        rv = A.app.test_client().get('/chronicle')
        assert rv.status_code == 200 and b'opens after your first session' in rv.data, rv.status_code
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ---- Task 3: section indexes /chronicle/<story|lore|cast|handouts> --------

def test_cast_index_scopes_recipients():
    # NB: flush-left, same reason as test_nav_shows_notes_before_publish_and_chronicle_after
    # above -- this body is concatenated onto _SEED (itself flush-left at module
    # scope), and textwrap.dedent strips the LONGEST COMMON leading whitespace
    # across the combined string, which is zero once _SEED is in the mix.
    r = _run(_SEED + '''
import tempfile, os, json
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
from core import storage, auth, campaigns
c = A.app.test_client()
assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
assert c.post('/campaigns/new', data={'name':'Roshar','system':'pf2e'}).status_code == 302
cid = [x for x in storage.list_campaign_ids() if campaigns.get_campaign(x)['name']=='Roshar'][0]
assert c.post('/campaign/'+cid+'/activate').status_code == 302
auth.create_user('shai','pw123456','Shai'); shai = auth.get_user_by_username('shai')
campaigns.add_member(cid, shai['id'], 'player')
pdir = storage.party_dir(cid); os.makedirs(pdir, exist_ok=True)
doc = storage.wrap_character('c'*32, cid, 'pf2e', {'build':{'name':'Shallan'}}, owner_user_id=shai['id'])
with open(os.path.join(pdir,'shallan.json'),'w') as f: json.dump(doc, f)
seed_chronicle(storage.chronicle_dir(cid), [
    {'slug':'romi','section':'cast','title':'Romi','recipients':'all'},
    {'slug':'secret','section':'cast','title':'Kaladin-only','recipients':['kaladin']},
], html={'romi':'<p>x</p>','secret':'<p>y</p>'})
# the GM (setup session) sees both
both = c.get('/chronicle/cast').data
assert b'Romi' in both and b'Kaladin-only' in both
# Shallan's owner sees only the public card. A real player session picks
# its active campaign via POST /campaign/<cid>/activate (e.g. from "My
# Campaigns" on /me) -- login alone does not set session active_campaign_id.
p = A.app.test_client()
assert p.post('/login', data={'username':'shai','password':'pw123456'}).status_code == 302
assert p.post('/campaign/'+cid+'/activate').status_code == 302
seen = p.get('/chronicle/cast').data
assert b'Romi' in seen and b'Kaladin-only' not in seen
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ---- Task 4: page detail /chronicle/page/<slug> (404 on hidden/unknown) ----

def test_page_detail_and_hidden_404():
    # NB: flush-left, same reason as test_nav_shows_notes_before_publish_and_chronicle_after
    # above -- this body is concatenated onto _SEED (itself flush-left at module
    # scope), and textwrap.dedent strips the LONGEST COMMON leading whitespace
    # across the combined string, which is zero once _SEED is in the mix.
    r = _run(_SEED + '''
import tempfile, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
seed_chronicle(A.CHRONICLE_DIR, [{'slug':'romi','section':'cast','title':'Romi','recipients':'all'}],
               html={'romi':'<h1>Romi</h1><p>The broker.</p>'})
c = A.app.test_client()
ok = c.get('/chronicle/page/romi')
assert ok.status_code == 200 and b'The broker.' in ok.data
assert c.get('/chronicle/page/nope').status_code == 404       # unknown == not discovered
assert c.get('/chronicle/page/..%2f..%2fmanifest').status_code == 404
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ---- Task 5: /chronicle/journal folds in the existing private Notes store -

def test_journal_reuses_notes_store():
    # NB: flush-left, same reason as test_nav_shows_notes_before_publish_and_chronicle_after
    # above -- this body is concatenated onto _SEED (itself flush-left at module
    # scope), and textwrap.dedent strips the LONGEST COMMON leading whitespace
    # across the combined string, which is zero once _SEED is in the mix.
    r = _run(_SEED + '''
import tempfile, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
seed_chronicle(A.CHRONICLE_DIR, [{'slug':'home','section':'home','title':'H','recipients':'all'}], html={'home':'<p>x</p>'})
c = A.app.test_client()
assert c.post('/api/notes', json={'text':'my private theory'}).status_code == 200
body = c.get('/chronicle/journal').data
assert b'my private theory' in body      # same per-owner store as /notes
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
