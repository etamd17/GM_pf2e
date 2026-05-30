"""Server-side feat-prerequisite enforcement in /api/submit_levelup.

Part 2 of the level-up legality work. A feat whose prerequisite isn't met
(e.g. Battle Medicine without trained Medicine) is rejected at submit time
with an ``illegal`` list, *unless* ``force_save`` is passed — the homebrew /
"Ignore Pre-Reqs" override that already existed for the missing-slot gate.

Only feats listed in ``SKILL_FEAT_PREREQS`` are gated; anything unknown
(class / ancestry / archetype feats, most general feats) always passes, so
there are no false positives. The check runs against the proficiency table
*after* this level's skill increases are applied, so a skill trained this
same level satisfies a same-level skill feat (PF2e RAW).

The PC is staged in-memory (Kyle / Druid fixture reduced to L1) and the
save path is stubbed, so nothing touches party_data on disk — the same
harness ``test_levelup_engine`` / ``test_levelup_chain`` use, so this runs
in CI without live PCs.
"""

from __future__ import annotations

import copy
import json

import pytest

from tests.test_pc_snapshots import _FIXTURES_DIR
from tests.test_level_walk import _reduce_build_to_level


@pytest.fixture(scope="module")
def app_module():
    import app
    return app


def _stage_druid_l1(app_module, monkeypatch, tmp_path):
    """Stage the Kyle (Druid) fixture reduced to L1 and stub persistence.
    Returns (pc_name, base_feats) — base_feats are the L1 feats to carry
    forward so the submit looks like a real next-level pick."""
    raw = json.loads((_FIXTURES_DIR / "kyle_l10.json").read_text(encoding="utf-8"))
    full = raw["build"]
    start = _reduce_build_to_level(full, 1)
    Character = app_module.Character

    pc_name = "_PREREQ_DRUID"
    pc_file = tmp_path / f"{pc_name}.json"
    pc_file.write_text(json.dumps({"build": copy.deepcopy(start)}), encoding="utf-8")

    monkeypatch.setitem(
        app_module.PARTY_LIBRARY, pc_name,
        Character({"build": copy.deepcopy(start)}, file_path=str(pc_file)),
    )
    monkeypatch.setattr(
        app_module, "get_pc_file_path",
        lambda n: str(pc_file) if n == pc_name else None,
    )

    def _fake_save(name, pc_json, fp):
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(pc_json, f)
        app_module.PARTY_LIBRARY[name] = Character(pc_json, file_path=fp)
    monkeypatch.setattr(app_module, "save_and_reload_character", _fake_save)

    return pc_name, copy.deepcopy(start.get("feats", []))


def _post_l2(app_module, pc_name, base_feats, *, skill_feat, medicine_rank, force=False):
    """Submit a L1 -> L2 level-up. L2 owes 1 class feat + 1 skill feat; we
    supply both (Pathbuilder format ``[name, sub, type, level]``) so the
    missing-slot gate is satisfied and only the prereq gate is exercised."""
    feats = list(base_feats) + [
        ["Animal Companion", None, "Class Feat", 2],
        [skill_feat, None, "Skill Feat", 2],
    ]
    payload = {
        "new_level": 2,
        "feats": feats,
        "skills": {"medicine": medicine_rank},
    }
    if force:
        payload["force_save"] = True
    with app_module.app.test_client() as c:
        return c.post(f"/api/submit_levelup/{pc_name}", json=payload)


def test_unmet_feat_prereq_is_rejected(app_module, monkeypatch, tmp_path):
    """Battle Medicine without trained Medicine -> 400 with an `illegal`
    list naming the offending feat."""
    pc_name, base = _stage_druid_l1(app_module, monkeypatch, tmp_path)
    resp = _post_l2(app_module, pc_name, base, skill_feat="Battle Medicine", medicine_rank=0)
    assert resp.status_code == 400, resp.get_json()
    data = resp.get_json()
    assert data["success"] is False
    assert any("Battle Medicine" in s for s in data.get("illegal", [])), data


def test_force_save_overrides_unmet_prereq(app_module, monkeypatch, tmp_path):
    """The homebrew / Ignore-Pre-Reqs override (force_save) lets the same
    illegal pick through."""
    pc_name, base = _stage_druid_l1(app_module, monkeypatch, tmp_path)
    resp = _post_l2(app_module, pc_name, base, skill_feat="Battle Medicine", medicine_rank=0, force=True)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("success") is True


def test_met_feat_prereq_passes(app_module, monkeypatch, tmp_path):
    """Medicine trained THIS level satisfies Battle Medicine (same-level
    RAW) -> 200, no force_save needed."""
    pc_name, base = _stage_druid_l1(app_module, monkeypatch, tmp_path)
    resp = _post_l2(app_module, pc_name, base, skill_feat="Battle Medicine", medicine_rank=2)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("success") is True


def test_unknown_feat_never_blocked(app_module, monkeypatch, tmp_path):
    """A feat absent from SKILL_FEAT_PREREQS must pass even with no relevant
    training — no false positives on the long tail of feats."""
    pc_name, base = _stage_druid_l1(app_module, monkeypatch, tmp_path)
    resp = _post_l2(app_module, pc_name, base, skill_feat="Fleet", medicine_rank=0)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("success") is True


def test_wildcard_prereq_feat_passes(app_module, monkeypatch, tmp_path):
    """Assurance's prereq is "trained in any skill" (skill == '*'), which
    check_feat_prereqs always reports as met — it must not be blocked."""
    pc_name, base = _stage_druid_l1(app_module, monkeypatch, tmp_path)
    resp = _post_l2(app_module, pc_name, base, skill_feat="Assurance", medicine_rank=0)
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json().get("success") is True
