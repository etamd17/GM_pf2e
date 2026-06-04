"""Session notes scratchpad (Phase 4) — the per-owner notes API + page, and the
Cosmere PC sheet's inline notes panel.
"""
from __future__ import annotations

import os

import app
from systems.cosmere import origins as O


def _notes_file(owner='gm'):
    return os.path.join(os.path.dirname(app.JOURNAL_DIR), 'session_notes', owner + '.json')


def test_notes_page_renders():
    r = app.app.test_client().get('/notes')
    assert r.status_code == 200
    assert b'Session Notes' in r.data


def test_notes_api_roundtrip():
    c = app.app.test_client()       # no login -> owner 'gm'
    try:
        save = c.post('/api/notes', json={'text': 'remember the chasmfiend'})
        assert save.status_code == 200 and save.get_json()['ok'] is True
        assert c.get('/api/notes').get_json()['text'] == 'remember the chasmfiend'
    finally:
        if os.path.isfile(_notes_file('gm')):
            os.remove(_notes_file('gm'))


def test_cosmere_pc_notes_persist_and_render():
    c = app.app.test_client()
    build = {'name': 'Notetaker', 'level': 1, 'path': 'warrior',
             'attributes': {'str': 2, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 3, 'pre': 0},
             'skills': {'ath': 2, 'hwp': 2, 'prc': 1}, 'talents': [O.path_key_talent('warrior')]}
    pid = c.post('/cosmere/builder', json={'build': build}).get_json()['id']
    try:
        r = c.post('/cosmere/pc/%s/notes' % pid, json={'text': 'bonded a spren today'})
        assert r.status_code == 200 and r.get_json()['ok'] is True
        body = c.get('/cosmere/pc/%s' % pid).data.decode()
        assert 'bonded a spren today' in body          # rendered in the notes textarea
        assert 'Session Notes' in body
    finally:
        p = app._cosmere_pc_path(pid)
        if p and os.path.isfile(p):
            os.remove(p)


def test_cosmere_pc_notes_unknown_404():
    assert app.app.test_client().post('/cosmere/pc/deadbeef/notes', json={'text': 'x'}).status_code == 404
