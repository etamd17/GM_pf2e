"""Explicit forward-only data-safe schema transition from PR4 to PR5."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import inspect

from core.persistence import Database, User


def test_upgrade_preserves_old_rows_and_downgrade_refuses_receipts(tmp_path):
    url = 'sqlite+pysqlite:///' + str(tmp_path / 'migration.sqlite')
    environment = dict(os.environ, DATABASE_URL=url)
    root = Path(__file__).parents[2]
    def migrate(*args):
        return subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=root,
            env=environment, capture_output=True, text=True, encoding='utf-8')
    old = migrate('upgrade', '20260930_0001')
    assert old.returncode == 0, old.stderr
    db = Database(url)
    with db.transaction() as session:
        session.add(User(id='1' * 32, username='retained', normalized_username='retained',
                         display_name='Retained', password_hash='test-only'))
    upgraded = migrate('upgrade', 'head')
    assert upgraded.returncode == 0, upgraded.stderr
    assert 'character_workflow_receipts' in inspect(db.engine).get_table_names()
    from core.persistence.models import CharacterWorkflowReceipt
    with db.transaction() as session:
        assert session.get(User, '1' * 32).display_name == 'Retained'
        session.add(CharacterWorkflowReceipt(id='e' * 32, campaign_id='a' * 32,
            author_id='1' * 32, draft_id='d' * 32, target_id=None,
            operation='create_draft', key_hash='1' * 64, request_digest='2' * 64,
            state='committed', details={}, created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)))
    refused = migrate('downgrade', '20260930_0001')
    assert refused.returncode != 0
    assert 'workflow' in refused.stderr.lower()
    with db.session() as session:
        assert session.get(CharacterWorkflowReceipt, 'e' * 32) is not None
    db.dispose()
