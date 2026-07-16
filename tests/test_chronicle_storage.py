import os
import json
import tempfile

# Bind app's DATA_DIR to a throwaway BEFORE any `import app` (mirrors
# tests/test_cosmere_campaign_binding.py); harmless for the pure-storage tests.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='chron-data-'))
os.environ.setdefault('GM_PASSWORD', '')

import pytest
from core import storage


def test_chronicle_dir_is_under_campaign():
    cid = storage.new_id()
    assert storage.chronicle_dir(cid) == os.path.join(storage.campaign_dir(cid), 'chronicle')


def test_chronicle_dir_rejects_traversal_id():
    with pytest.raises(ValueError):
        storage.chronicle_dir('../escape')
