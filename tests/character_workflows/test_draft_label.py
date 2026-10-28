"""Saved drafts show the character's name, wherever each builder keeps it."""
from types import SimpleNamespace

import pytest

import app


@pytest.mark.parametrize(('submission', 'form', 'expected'), [
    ({'build': {'name': 'Kelsier'}}, {'fields': {'f-name': 'Kelsier'}}, 'Kelsier'),   # Cosmere builders
    ({'name': 'Valeros'}, {'name': 'Valeros'}, 'Valeros'),                            # PF2e builder
    ({}, {'build': {'name': 'From form'}}, 'From form'),
    ({'build': {'name': '   '}}, {}, 'Untitled character'),
    ({'build': {'name': 7}}, {}, 'Untitled character'),
])
def test_draft_label_reads_submission_then_form(submission, form, expected):
    inputs = SimpleNamespace(submission=submission, form=form)
    assert app._workflow_draft_label(inputs) == expected


def test_draft_label_without_inputs():
    assert app._workflow_draft_label(None) == 'Untitled character'
