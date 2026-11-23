"""PE-14d: static inventory of authorization writers, plus spy-order tests.

``scan`` walks Python sources for writes to authorization rows (membership,
assignment, trashed status, grants, integration tokens).  Each write must
follow a stream taker in its own function, or be listed in ``EXEMPT`` with a
reason.  The spy tests cover the writers with no natural home elsewhere.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from core import storage
from core.persistence import integration_tokens, runtime
from core.persistence.models import Campaign, User
from tests.persistence.lock_order import assert_stream_first, first, record_locks
from tests.persistence.test_campaign_sql_import import sql_import, stage_campaign  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
# Decision 4 needs the lock_timeout form: a bare ``lock_stream`` is not a taker.
TAKERS = {"lock_stream_for_write", "lock_invitation_for_write"}
ROW_CLASSES = ("CampaignGrant", "IntegrationToken")
HELPERS = {"_lock_campaign", "_locked_campaign"}  # campaign-lock helpers that take the stream
ROW_MODULES = ("core/persistence/grants.py", "core/persistence/integration_tokens.py")
AUTH_WORDS = ("member", "assign", "owner", "character")
GRANT_HELPERS = ("consume", "issue", "revoke")

_STREAM_HELD = "every caller holds the stream row; covered by the spy test"
_NEW_CAMPAIGN = "campaign created in this transaction; lock_stream follows the insert"
_OFFLINE = "offline migration tool, no server running"
_COMMAND = "command transaction only"
EXEMPT = {
    ("core/persistence/campaign_runtime.py", "create_campaign"): _NEW_CAMPAIGN,
    ("core/persistence/campaign_import.py", "_import"): _NEW_CAMPAIGN,
    ("tools/migrate_transactional_store.py", "import_store"): _OFFLINE,
    ("core/persistence/repositories.py", "CampaignRepository.upsert"): _STREAM_HELD,
    ("core/persistence/repositories.py", "CampaignRepository.upsert_membership"): _STREAM_HELD,
    ("core/persistence/repositories.py", "CharacterRepository.set_assignment"): _STREAM_HELD,
    ("core/persistence/services.py", "TransactionalStore._claim_locked_character"): _STREAM_HELD,
    ("core/persistence/integration_tokens.py", "_insert_if_absent"): _STREAM_HELD,
    ("core/persistence/integration_tokens.py", "_revoke_live"): _STREAM_HELD,
    ("core/persistence/integration_tokens.py", "_reconcile"): _STREAM_HELD,
    ("core/commands/grant_commands.py", "_apply_issue"): _COMMAND,
    ("core/commands/grant_commands.py", "_apply_revoke"): _COMMAND,
    ("core/persistence/grants.py", "consume"): _COMMAND,
    ("core/persistence/grants.py", "issue"): _COMMAND,
    ("core/persistence/grants.py", "revoke"): _COMMAND,
    ("core/persistence/integration_tokens.py", "touch_last_used"):
        "not an authorization input (C:460-465)",
}


def _name(node):
    """The called or referenced name: ``f`` for ``f(...)`` and ``x.f(...)``."""

    node = node.func if isinstance(node, ast.Call) else node
    return getattr(node, "attr", None) or getattr(node, "id", None)


def _functions(tree):
    """``(qualname, node)`` for every function, innermost last."""

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    yield name, child
                yield from walk(child, name + ".")
            else:
                yield from walk(child, prefix)
    return list(walk(tree, ""))


def _pos(node):
    return (node.lineno, node.col_offset)


def _for_update(node):
    return isinstance(node, ast.Call) and (_name(node) == "with_for_update" or any(
        k.arg == "for_update" and getattr(k.value, "value", None) is True for k in node.keywords))


def _mentions(node, words=AUTH_WORDS):
    text = ast.unparse(node).lower()
    return any(word in text for word in words)


def _grant_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in GRANT_HELPERS
            and ast.unparse(node.func.value).split(".")[-1] == "grants")


def _write(node, path, function):
    """A label when ``node`` writes an authorization row, else None."""

    if isinstance(node, ast.Call):
        name = _name(node)
        if name in ("CampaignMembership", "CharacterAssignment", "upsert_membership",
                    "set_assignment", "_insert_if_absent", "_revoke_live", "_reconcile",
                    *ROW_CLASSES):
            return name
        # A grant or token row written from any module, not only its row module.
        if name in ("insert", "update", "delete") and node.args and any(
                row in ast.unparse(node.args[0]) for row in ROW_CLASSES):
            return f"{name}({ast.unparse(node.args[0])})"
        if _grant_call(node):
            return f"grants.{name}"
        if name == "delete" and isinstance(node.func, ast.Attribute) and node.args \
                and "session" in ast.unparse(node.func.value) and _mentions(node.args[0]):
            return "session.delete"
        if path.endswith(ROW_MODULES) and name in ("insert", "update", "delete") \
                and node.args and _name(node.args[0]) == "_T":
            return f"{name}(_T)"
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        for target in getattr(node, "targets", None) or [node.target]:
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr == "trashed_at":
                return ".trashed_at ="
            # .role on a membership or assignment: the receiver, or (for a bare
            # name such as ``existing``) the function, must name one.
            if target.attr == "role" and "invit" not in ast.unparse(target.value).lower() \
                    and (_mentions(target.value, AUTH_WORDS[:2])
                         or _mentions(function, AUTH_WORDS[:2])):
                return ".role ="
    return None


def scan(sources, exempt=None):
    """``(problems, hits, helpers)`` for ``{relative_path: source_text}``.

    ``problems`` is empty when every write follows a stream taker or is
    exempt; ``hits`` is ``{(path, qualname)}`` of every writing function;
    ``helpers`` the campaign-lock helpers verified to take the stream first.
    """

    exempt = EXEMPT if exempt is None else exempt
    problems, hits, verified = [], set(), set()
    for path, source in sorted(sources.items()):
        functions = _functions(ast.parse(source))
        helpers = set()
        owner = {}  # node -> its innermost function (later entries are deeper)
        deferred = set()  # nodes inside a lambda: not run where they are written
        for qualname, function in functions:
            for node in ast.walk(function):
                owner[node] = (qualname, function)
                if isinstance(node, ast.Lambda):
                    deferred.update(ast.walk(node.body))

        def own(function):
            """The function's own statements: no nested def's, no lambda's."""

            return [n for n in ast.walk(function)
                    if owner[n][1] is function and n not in deferred]

        for qualname, function in functions:
            locks = [_pos(n) for n in ast.walk(function) if _for_update(n)]
            if function.name not in HELPERS or not locks:
                continue  # no FOR UPDATE: not a campaign-row helper (a command lock_plan, say)
            takes = [_pos(n) for n in own(function)
                     if isinstance(n, ast.Call) and _name(n) == "lock_stream_for_write"]
            if not takes or min(takes) > min(locks):
                problems.append(f"{path}:{qualname} locks the campaign before the stream")
            else:
                helpers.add(function.name)
                verified.add((path, qualname))

        def taker(node):
            if not isinstance(node, ast.Call):
                return False
            # A helper call passing stream=False takes no stream row.
            return _name(node) in TAKERS or (_name(node) in helpers and not any(
                k.arg == "stream" and getattr(k.value, "value", None) is False
                for k in node.keywords))

        for node, (qualname, function) in owner.items():
            label = _write(node, path, function)
            if label is None:
                continue
            hits.add((path, qualname))
            # Only a taker in the write's own function counts: one inside a
            # nested def or a lambda may never run before the write.
            if (path, qualname) not in exempt and not any(
                    taker(n) and _pos(n) < _pos(node) for n in own(function)):
                problems.append(f"{path}:{node.lineno} {qualname}: {label} with no stream "
                                "lock earlier in the function and no EXEMPT entry")
    problems += [f"stale EXEMPT entry {key}" for key in sorted(set(exempt) - hits)]
    return problems, hits, verified


def _apply_names(tree):
    """Names passed as ``apply=`` to ``CommandType(...)``, resolved through a loop table."""

    names = set()
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and _name(call) == "CommandType"):
            continue
        for keyword in call.keywords:
            if keyword.arg != "apply" or not isinstance(keyword.value, ast.Name):
                continue
            names.add(keyword.value.id)
            for loop in loops:  # for ..., _apply in ((..., _apply_issue), ...):
                targets = [getattr(t, "id", None) for t in getattr(loop.target, "elts", [])]
                if keyword.value.id in targets and isinstance(loop.iter, ast.Tuple):
                    column = targets.index(keyword.value.id)
                    names |= {row.elts[column].id for row in loop.iter.elts}
    return names


def grant_helper_misuse(sources):
    """Calls to ``grants.consume/issue/revoke`` outside a registered ``apply``."""

    problems = []
    for path, source in sorted(sources.items()):
        if path == ROW_MODULES[0]:
            continue  # grants.py itself
        tree = ast.parse(source)
        allowed, inside = _apply_names(tree), {}
        for qualname, function in _functions(tree):
            for node in ast.walk(function):
                inside[node] = qualname
        problems += [f"{path}:{node.lineno} {ast.unparse(node.func)} called from "
                     f"{inside.get(node, '<module>')}, not a registered apply"
                     for node in ast.walk(tree)
                     if _grant_call(node) and inside.get(node) not in allowed]
    return problems


def repo_sources():
    files = [ROOT / "app.py"]
    for tree in ("core", "services", "tools"):
        files += sorted((ROOT / tree).rglob("*.py"))
    return {f.relative_to(ROOT).as_posix(): f.read_text(encoding="utf-8") for f in files}


@pytest.fixture(scope="module")
def sources():
    found = repo_sources()
    assert {path.split("/")[0] for path in found} == {"app.py", "core", "services", "tools"}
    return found


def test_every_authorization_write_follows_the_stream_or_is_exempt(sources):
    problems, hits, helpers = scan(sources)
    assert problems == []
    assert all(reason for reason in EXEMPT.values())
    # The helpers that count as takers were each verified stream-before-FOR UPDATE.
    assert helpers == {
        ("core/persistence/services.py", "TransactionalStore._lock_campaign"),
        ("core/persistence/campaign_runtime.py", "_locked_campaign"),
        ("core/persistence/ownership.py", "_lock_campaign")}
    # Non-vacuous: the writers the brief names are seen as locked hits.
    assert hits >= {("core/persistence/campaign_runtime.py", "_change_membership"),
                    ("core/persistence/services.py", "TransactionalStore.remove_membership"),
                    ("core/persistence/ownership.py", "release_character.release"),
                    ("core/persistence/ownership.py", "delete_character.delete"),
                    ("core/persistence/integration_tokens.py", "revoke_row")}
    assert ("core/persistence/repositories.py", "InvitationRepository.upsert") not in hits


@pytest.mark.parametrize("path, insert", [
    ("core/persistence/campaign_runtime.py", "upsert"),
    ("core/persistence/campaign_import.py", "Campaign")])
def test_new_campaign_writers_take_the_stream_after_the_insert(sources, path, insert):
    (function,) = [f for name, f in _functions(ast.parse(sources[path]))
                   if (path, name) in EXEMPT]
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)]
    (stream,) = [_pos(n) for n in calls if _name(n) == "lock_stream_for_write"]
    assert min(_pos(n) for n in calls if _name(n) == insert) < stream
    # ... and before everything after the creator's membership.
    assert max(_pos(n) for n in calls if _name(n) == "upsert_membership") < stream


_WRITES = {
    "membership": "session.add(CampaignMembership(campaign_id=cid))",
    "repository": "repo.upsert_membership(campaign_id=cid)",
    "trash": "campaign.trashed_at = None",
    "delete": "session.delete(assignment)",
}


def _planted(write, before="pass", after="pass", extra=""):
    return f"def writer(session, cid):\n    {before}\n    {write}\n    {after}\n{extra}"


@pytest.mark.parametrize("path, write", list(zip(
    ("core/planted.py", "services/planted.py", "tools/planted.py", "app.py"), _WRITES.values())),
    ids=("core", "services", "tools", "app"))
def test_scanner_fails_on_a_planted_unlocked_write_in_each_tree(path, write):
    problems, hits, _ = scan({path: _planted(write)}, exempt={})
    assert hits == {(path, "writer")} and len(problems) == 1 and path in problems[0]
    assert scan({path: _planted(write, before="lock_stream_for_write(session, cid)")},
                exempt={})[0] == []


_HELPER = ("def _lock_campaign(session, cid, *, stream=True):\n    {first}\n    {second}\n")
_GOOD_HELPER = _HELPER.format(first="lock_stream_for_write(session, cid)",
                              second="session.scalar(select(C).with_for_update())")
_BAD_HELPER = _HELPER.format(first="repo.get(cid, for_update=True)",
                             second="lock_stream_for_write(session, cid)")


@pytest.mark.parametrize("source, clean", [
    (_planted(_WRITES["trash"], after="lock_stream(session, cid)"), False),
    (_planted(_WRITES["delete"], before="lock_invitation_for_write(session, code)"), True),
    (_planted(_WRITES["trash"], before="_lock_campaign(session, cid)", extra=_GOOD_HELPER), True),
    (_planted(_WRITES["trash"], before="_lock_campaign(session, cid, stream=False)",
              extra=_GOOD_HELPER), False),
    (_planted(_WRITES["trash"], before="_lock_campaign(session, cid)", extra=_BAD_HELPER), False),
    (_planted(_WRITES["trash"], before="_lock_campaign(session, cid)"), False),  # no such helper
    (_planted("membership.role = role"), False),
    (_planted("invitation.role = role"), True),
    (_planted("grants.issue(session, cid, kind='level_unlock')"), False),
    (_planted(_WRITES["trash"], before="streams.lock_stream(session, cid)"), False),
    (_planted(_WRITES["trash"], before="def later():\n        lock_stream_for_write(session, cid)"),
     False),
    (_planted(_WRITES["trash"], before="later = lambda: lock_stream_for_write(session, cid)"),
     False),
    (_planted(_WRITES["trash"], before="run(lambda: _lock_campaign(session, cid))",
              extra=_GOOD_HELPER), False),
    (_HELPER.format(first="defer(lambda: lock_stream_for_write(session, cid))",
                    second="session.scalar(select(C).with_for_update())"), False),
], ids=("lock-after-write", "invitation-helper", "campaign-helper", "stream-false",
        "helper-locks-campaign-first", "unverified-helper", "role", "invitation-role",
        "grant-call", "bare-lock-stream", "taker-in-nested-def", "taker-in-lambda",
        "helper-in-lambda", "helper-takes-stream-in-lambda"))
def test_scanner_taker_rules(source, clean):
    assert (scan({"core/planted.py": source}, exempt={})[0] == []) is clean


@pytest.mark.parametrize("write", [
    "session.add(IntegrationToken(campaign_id=cid))",
    "session.add(CampaignGrant(campaign_id=cid))",
    "session.execute(update(IntegrationToken.__table__).values(revoked_at=None))",
    "session.execute(insert(CampaignGrant).values(campaign_id=cid))",
    "session.execute(delete(models.CampaignGrant.__table__))",
], ids=("token-row", "grant-row", "token-update", "grant-insert", "grant-delete"))
def test_scanner_sees_grant_and_token_rows_outside_the_row_modules(write):
    path = "services/planted.py"
    problems, hits, _ = scan({path: _planted(write)}, exempt={})
    assert hits == {(path, "writer")} and len(problems) == 1 and path in problems[0]
    assert scan({path: _planted(write, before="lock_stream_for_write(session, cid)")},
                exempt={})[0] == []


def test_scanner_ignores_grant_and_token_models_and_reads():
    quiet = ("class IntegrationToken(Base):\n    __tablename__ = 'integration_tokens'\n"
             "class CampaignGrant(Base):\n    __tablename__ = 'campaign_grants'\n"
             "_T = IntegrationToken.__table__\n"
             "def read(session, cid):\n"
             "    session.scalars(select(CampaignGrant).where(CampaignGrant.campaign_id == cid))\n"
             "    session.get(IntegrationToken, cid)\n"
             "    return session.execute(select(_T).where(_T.c.campaign_id == cid)).first()\n")
    assert scan({"core/persistence/models.py": quiet}, exempt={}) == ([], set(), set())


def test_scanner_row_module_writes_and_stale_exemption(sources):
    row = "def touch(session):\n    session.execute(update(_T).values(x=1))\n"
    assert len(scan({ROW_MODULES[1]: row}, exempt={})[0]) == 1
    assert scan({"core/other.py": row}, exempt={})[0] == []  # another module's own table
    stale = ("core/persistence/services.py", "TransactionalStore.gone")
    assert scan(sources, {**EXEMPT, stale: "left behind"})[0] == [f"stale EXEMPT entry {stale}"]


def test_grant_helpers_called_only_from_apply(sources):
    assert grant_helper_misuse(sources) == []
    module = "core/commands/grant_commands.py"
    assert _apply_names(ast.parse(sources[module])) >= {"_apply_issue", "_apply_revoke"}
    planted = sources[module] + "\n\ndef plain(session):\n    grants.consume(session, 'g', 'o')\n"
    (problem,) = grant_helper_misuse({module: planted})
    assert "grants.consume called from plain" in problem
    direct = ("def spend(ctx):\n    grants.consume(ctx.session, 'g', 'o')\n"
              "register(CommandType(type='x', apply=spend))\n")
    assert grant_helper_misuse({"core/commands/later.py": direct}) == []
    assert len(grant_helper_misuse({"app.py": "from core.persistence import grants\n"
                                    "grants.revoke(s, 'c', 'g')\n"})) == 1


@pytest.mark.parametrize("code", [
    "import core.persistence",
    "import core.commands.executor",
    "import sys, core.commands.grant_commands; assert 'core.obsidian_sync' not in sys.modules",
])
def test_fresh_interpreter_imports(code):
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    if code.startswith("import sys"):  # ... and the module names no mirror function
        source = (ROOT / "core/commands/grant_commands.py").read_text(encoding="utf-8")
        assert not [name for name in ("repair_token_mirror", "_write_tokens", "_load_tokens",
                                      "obsidian_sync") if name in source]


# ---- spy order (SQLite) ---------------------------------------------------


@pytest.fixture
def sql(sqlite_database, tmp_path, monkeypatch):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    with sqlite_database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm",
                         display_name="GM", password_hash="hash"))
        session.add(Campaign(id="A", slug="a", name="A", system="pf2e"))
    return sqlite_database


def _record(credential_id, **fields):
    return {"id": credential_id, "label": "Pane", "token_hash": "0" * 63 + credential_id[-1],
            "last_four": "abcd", **fields}


@pytest.mark.parametrize("write", [
    lambda: integration_tokens.insert_minted("A", _record("t2")),
    lambda: integration_tokens.revoke_row("A", "t1", None),
    lambda: integration_tokens.revoke_row("A", "t3", _record("t3")),
    lambda: integration_tokens.import_file_records(
        "A", [_record("t4"), _record("t1", revoked_at="2026-01-01T00:00:00Z")]),
], ids=("mint", "revoke", "revoke-file-only", "import"))
def test_token_writers_take_the_stream_before_any_token_write(sql, write):
    integration_tokens.insert_minted("A", _record("t1"))
    with record_locks() as events:
        write()
    assert_stream_first(events, "A", before=())
    assert events.count("stream:A") == 1 and "write:integration_tokens" in events
    assert "lock_timeout" not in events  # PostgreSQL only


def test_token_reads_and_last_used_take_no_stream(sql):
    integration_tokens.insert_minted("A", _record("t1"))
    with record_locks() as events:
        assert integration_tokens.get_row("A", "t1")["label"] == "Pane"
        assert len(integration_tokens.list_rows("A")) == 1
        assert integration_tokens.find_by_digest("A", "0" * 63 + "1") is not None
        assert integration_tokens.import_file_records("A", [_record("t1")]) == 0  # read only
        assert integration_tokens.touch_last_used("A", "t1") is not None
    assert events == ["write:integration_tokens"]


def test_create_campaign_inserts_then_takes_the_stream(sql):
    from core.persistence import campaign_runtime

    with record_locks() as events:
        cid = campaign_runtime.create_campaign("New", "pf2e", "gm")["id"]
    stream = first(events, f"stream:{cid}")
    assert first(events, "write:campaigns") < stream
    assert (first(events, "write:campaign_memberships") < stream
            < first(events, "write:audit_events"))
    assert "for_update:campaigns" not in events


def test_campaign_import_inserts_then_takes_the_stream(sql_import):
    from core.persistence.campaign_import import import_staged_campaign

    stage, final, doc = stage_campaign()
    with record_locks() as events:
        import_staged_campaign(str(stage), str(final), doc, "importer")
    stream = first(events, f"stream:{doc['id']}")
    assert first(events, "write:campaigns") < stream
    assert first(events, "write:campaign_memberships") < stream < first(events, "write:characters")


def test_workflow_transaction_process_lock_then_stream_then_campaign(sql, monkeypatch):
    from types import SimpleNamespace

    import core.campaigns
    from core.persistence.workflow_store import SqlWorkflowStore

    real = core.campaigns._CAMPAIGN_STORE_LOCK

    class Spy:
        def __enter__(self):
            events.append("process_lock")
            return real.__enter__()

        def __exit__(self, *exc):
            events.append("process_unlock")
            return real.__exit__(*exc)

    monkeypatch.setattr(core.campaigns, "_CAMPAIGN_STORE_LOCK", Spy())
    with record_locks() as events:
        with SqlWorkflowStore(sql).transaction(SimpleNamespace(campaign_id="A")) as tx:
            assert tx.campaign.id == "A"
        with SqlWorkflowStore(sql).transaction(SimpleNamespace(campaign_id="missing")) as tx:
            assert tx.campaign is None  # no campaign, so no stream row to take
    events = [name for name in events if name != "for_update:campaign_streams"]
    assert events == ["process_lock", "stream:A", "for_update:campaigns", "process_unlock",
                      "process_lock", "for_update:campaigns", "process_unlock"]
