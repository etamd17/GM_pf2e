"""Mandatory real-browser prerequisites and disposable, restartable app servers."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

from tools.smoke_production_runtime import _free_port, _stop_server

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='session')
def browser():
    # Normal unit collection neither imports nor installs Playwright.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        executable = os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
        if not executable and shutil.which('chromium'):
            executable = shutil.which('chromium')
        launched = engine.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        yield launched
        launched.close()


class Site:
    def __init__(self, root, backend, system):
        self.root, self.backend, self.system = root, backend, system
        self.port = _free_port()
        self.url = 'http://127.0.0.1:' + str(self.port)
        self.password = secrets.token_urlsafe(24)
        self.env = dict(os.environ, APP_ENV='development', DATA_DIR=str(root / 'data'),
            DATABASE_URL='sqlite+pysqlite:///' + str(root / 'identity.db'),
            OWNERSHIP_BACKEND=backend, GM_PASSWORD='', SECRET_KEY=secrets.token_urlsafe(32),
            BROWSER_PASSWORD=self.password, BROWSER_SYSTEM=system, PYTHONDONTWRITEBYTECODE='1')
        for key in ('PUBLIC_BASE_URL', 'ALLOWED_HOSTS', 'GUNICORN_CMD_ARGS', 'SESSION_STATE_PATH'):
            self.env.pop(key, None)
        (root / 'data').mkdir()
        if backend == 'sql':
            result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                                    cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
        seeded = self.python('''
import json, os
from core import auth, campaigns, storage
gm = auth.create_first_admin('gm', os.environ['BROWSER_PASSWORD'])
users = {'gm': gm}
for role in ('player','editor','viewer'):
    users[role] = auth.create_user(role, os.environ['BROWSER_PASSWORD'])
campaign = campaigns.create_campaign('Browser table', os.environ['BROWSER_SYSTEM'], gm['id'])
for role in ('player','editor','viewer'):
    campaigns.add_member(campaign['id'], users[role]['id'], 'player')
storage.set_live_campaign_id(campaign['id'])
print('BROWSER_RESULT=' + json.dumps({'cid':campaign['id'],'users':{k:v['id'] for k,v in users.items()}}))
''')
        self.cid, self.users = seeded['cid'], seeded['users']
        self.process = None
        self.start()

    def python(self, source):
        result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, env=self.env,
                                capture_output=True, text=True, timeout=40)
        assert result.returncode == 0, result.stderr[-3000:]
        lines = [line for line in result.stdout.splitlines() if line.startswith('BROWSER_RESULT=')]
        return json.loads(lines[-1].split('=', 1)[1]) if lines else None

    def start(self):
        self.log = (self.root / 'server.log').open('ab')
        self.process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--workers', '1',
            '--worker-class', 'gevent', '--bind', '127.0.0.1:' + str(self.port),
            '--no-control-socket', '--graceful-timeout', '2', '--timeout', '60', 'app:app'],
            cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.root / 'server.log').read_text()[-3000:]
            try:
                with urllib.request.urlopen(self.url + '/ready', timeout=1) as response:
                    if json.load(response) == {'status':'ready'}:
                        return
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise AssertionError('Disposable browser server did not become ready')

    def stop(self):
        if self.process:
            _stop_server(self.process)
            self.log.close()
            self.process = None

    def restart(self):
        self.stop()
        self.start()

    @property
    def builder(self):
        return '/player/builder' if self.system == 'pf2e' else '/cosmere/builder'

    @property
    def roster(self):
        return '/player?roster=1' if self.system == 'pf2e' else '/cosmere/pcs'

    @property
    def name_input(self):
        return '#inp-name' if self.system == 'pf2e' else '#f-name'

    def login(self, page, role='player'):
        page.goto(self.url + '/login')
        page.locator('[name=username]').fill(role)
        page.locator('[name=password]').fill(self.password)
        page.get_by_role('button', name='Sign in', exact=True).click()
        page.wait_for_url('**/me')
        page.locator('form[action="/campaign/' + self.cid + '/activate"] button').first.click()
        page.wait_for_url(lambda url: not url.endswith('/me'))


@pytest.fixture(scope='module', params=[('json','pf2e'),('sql','pf2e'),('json','cosmere'),('sql','cosmere')],
                ids=lambda value:'-'.join(value))
def site(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('browser-' + '-'.join(request.param))
    running = Site(root, *request.param)
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture(params=[{'width':390,'height':844},{'width':1440,'height':900}], ids=['mobile','desktop'])
def viewport(request):
    return request.param


@pytest.fixture
def pages(browser, site, viewport, request, tmp_path):
    contexts, opened = [], []
    def make(role='player'):
        context = browser.new_context(viewport=viewport)
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(12000)
        page.workflow_errors = []
        page.workflow_network = []
        page.on('pageerror', lambda error:page.workflow_errors.append(str(error)))
        for event_name in ('request','requestfinished','requestfailed'):
            page.on(event_name, lambda request, event_name=event_name:page.workflow_network.append(
                (event_name,request.method,request.url.split('/api/')[-1]))
                if '/api/character-workflows/' in request.url else None)
        site.login(page, role)
        opened.append(page)
        return page
    yield make
    if getattr(request.node, 'browser_failed', False):
        # Screenshots only: never persist cookies, request headers or passwords.
        artifact_dir = Path(os.environ.get('BROWSER_ARTIFACT_DIR', tmp_path / 'artifacts'))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(opened):
            if not page.is_closed() and '/login' not in page.url:
                page.screenshot(path=str(artifact_dir / (request.node.name + '-' + str(index) + '.png')))
    for context in contexts:
        context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    if result.get_result().when == 'call':
        item.browser_failed = result.get_result().failed
