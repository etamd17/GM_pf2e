from copy import deepcopy
import json
from pathlib import Path

import pytest


@pytest.fixture
def authoring():
    return deepcopy(json.loads(
        (Path(__file__).parent / "fixtures" / "synthetic.json").read_text(encoding="utf-8")
    ))
