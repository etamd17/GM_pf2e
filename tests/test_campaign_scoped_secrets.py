"""End-to-end isolation for campaign-scoped handouts and live session secrets."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


_REPO = Path(__file__).resolve().parent.parent


def _run_isolated(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = dict(os.environ)
    environment.update(
        DATA_DIR=str(data_dir),
        GM_PASSWORD="",
        SETUP_TOKEN="",
        SECRET_KEY="campaign-secret-isolation-test",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        cwd=_REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"isolated scenario failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_selected_campaign_controls_handout_records_chronicle_and_bytes(tmp_path):
    result = _run_isolated(
        tmp_path,
        r'''
        import json
        import os

        import app as application
        from core import auth, campaigns, storage

        application.app.config.update(TESTING=True)
        gm = auth.create_user('gm', 'password-123', display_name='GM', is_admin=True)
        player = auth.create_user('player', 'password-123', display_name='Player')
        campaign_a = campaigns.create_campaign('Campaign A', 'pf2e', gm['id'])
        campaign_b = campaigns.create_campaign('Campaign B', 'pf2e', gm['id'])
        campaigns.add_member(campaign_a['id'], player['id'], 'player')
        campaigns.add_member(campaign_b['id'], player['id'], 'player')

        for campaign, sentinel, payload in (
            (campaign_a, 'A HANDOUT SENTINEL', b'A IMAGE BYTES'),
            (campaign_b, 'B HANDOUT SENTINEL', b'B IMAGE BYTES'),
        ):
            storage.atomic_write_json(storage.handouts_file(campaign['id']), [{
                'id': sentinel[0].lower(),
                'title': sentinel,
                'content': sentinel,
                'image_url': '/handouts/shared.bin',
                'recipients': ['all'],
            }], indent=2)
            with open(os.path.join(storage.handouts_dir(campaign['id']), 'shared.bin'), 'wb') as handle:
                handle.write(payload)

        # Chronicle needs a published manifest for its section block to render.
        chronicle_current = os.path.join(storage.chronicle_dir(campaign_a['id']), 'current')
        os.makedirs(chronicle_current, exist_ok=True)
        storage.atomic_write_json(os.path.join(chronicle_current, 'manifest.json'), {
            'schema_version': application.CHRONICLE_SCHEMA_VERSION,
            'pages': [],
            'session_number': 1,
        }, indent=2)

        # Keep B in the process-wide live slot while this player browses A.
        application.load_campaign(campaign_b['id'])
        storage.set_live_campaign_id(campaign_b['id'])
        client = application.app.test_client()
        assert client.post('/login', data={
            'username': 'player', 'password': 'password-123'
        }).status_code == 302
        assert client.post('/campaign/%s/activate' % campaign_a['id']).status_code == 302

        handouts = client.get('/api/handouts')
        assert handouts.status_code == 200, handouts.get_data(as_text=True)
        handout_text = json.dumps(handouts.get_json(), sort_keys=True)
        assert 'A HANDOUT SENTINEL' in handout_text
        assert 'B HANDOUT SENTINEL' not in handout_text

        chronicle = client.get('/chronicle/handouts')
        assert chronicle.status_code == 200, chronicle.status_code
        chronicle_text = chronicle.get_data(as_text=True)
        assert 'A HANDOUT SENTINEL' in chronicle_text
        assert 'B HANDOUT SENTINEL' not in chronicle_text

        image = client.get('/handouts/shared.bin')
        assert image.status_code == 200, image.status_code
        assert image.data == b'A IMAGE BYTES', image.data
        print('HANDOUT_SCOPE_OK')
        ''',
    )
    _assert_ok(result)
    assert "HANDOUT_SCOPE_OK" in result.stdout


def test_live_campaign_switch_reloads_only_that_campaign_session_state(tmp_path):
    result = _run_isolated(
        tmp_path,
        r'''
        import app as application
        from core import campaigns

        campaign_a = campaigns.create_campaign('Campaign A', 'pf2e', 'gm-a')
        campaign_b = campaigns.create_campaign('Campaign B', 'pf2e', 'gm-b')

        application.load_campaign(campaign_a['id'])
        application.SESSION_HEALING_LOG.append({'source': 'A SECRET'})
        application.SESSION_JOURNAL['Hero A'] = [{'text': 'A JOURNAL SECRET'}]
        application._save_session_state()

        application.load_campaign(campaign_b['id'])
        assert application.SESSION_HEALING_LOG == []
        assert application.SESSION_JOURNAL == {}
        application.SESSION_HEALING_LOG.append({'source': 'B SECRET'})
        application.SESSION_JOURNAL['Hero B'] = [{'text': 'B JOURNAL SECRET'}]
        application._save_session_state()

        application.load_campaign(campaign_a['id'])
        assert application.SESSION_HEALING_LOG == [{'source': 'A SECRET'}]
        assert application.SESSION_JOURNAL == {
            'Hero A': [{'text': 'A JOURNAL SECRET'}]
        }
        print('SESSION_STATE_SCOPE_OK')
        ''',
    )
    _assert_ok(result)
    assert "SESSION_STATE_SCOPE_OK" in result.stdout
