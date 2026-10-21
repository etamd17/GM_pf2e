"""M2: commands, durable receipts, acknowledgements and origin-correlated
completion (VTT-ADR-0002 sections 6-8), through the real namespace handlers,
the command pipeline, the hub and the StrictJSON codec, on SQLite.

The PostgreSQL-only guarantees (concurrent receipt keys, lock contention and
timeouts) are in ``test_m2_postgresql.py``."""

from __future__ import annotations

import json

import secrets

import pytest
from sqlalchemy import func, select

from vtt.commands import POLICIES, CommandService, RateLimiter, gm_snapshot_bytes
from vtt.commands import service as service_module
from vtt.projection import serializers as S
from vtt.protocol import ContractRegistry
from vtt.storage import writes as W
from vtt.storage.models import VttCommandReceipt, VttEntity, VttOutbox, VttScene
from vtt.transport.socket import NAMESPACE

from .conftest import add_grant, payload, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table

REGISTRY = ContractRegistry()


# --- helpers ---------------------------------------------------------------------------------

def new_id() -> str:
    return "cmd_" + secrets.token_urlsafe(12)


def versions(stack, sid=None):
    """Current GM version tokens, from the latest frames this test has seen."""
    out = {}
    for _sid, event, data in stack.sent:
        if sid is not None and _sid != sid:
            continue
        if event == "vtt.snapshot" and data["audience"] == "gm":
            out = {(e["kind"], e["id"]): e["version"] for e in data["entities"]}
        elif event == "vtt.patch":
            for e in data["upserted"]:
                out[(e["kind"], e["id"])] = e["version"]
            for r in data["removed"]:
                out.pop((r["kind"], r["id"]), None)
    return out


def expect(stack, kind, entity_id):
    return [{"kind": kind, "id": entity_id, "version": versions(stack)[(kind, entity_id)]}]


def send(stack, client, type_, payload_, *, expected=(), scene="scene_a", campaign="camp_a", command_id=None):
    command_id = command_id or new_id()
    message = {"schemaVersion": 1, "commandId": command_id, "campaignId": campaign, "sceneId": scene,
               "type": type_, "expected": list(expected), "payload": payload_}
    start = len(stack.sent)
    client.emit("vtt.command", message, namespace=NAMESPACE)
    after = stack.sent[start:]
    acks = [d for _, e, d in after if e == "vtt.ack"]
    for ack in acks:
        REGISTRY.validate("acknowledgement", ack)
    return command_id, (acks[0] if acks else None), after


def count(stack, model, **where):
    with stack.db.session() as session:
        query = select(func.count()).select_from(model)
        for name, value in where.items():
            query = query.where(getattr(model, name) == value)
        return session.execute(query).scalar_one()


def gm(stack):
    seed_scene(stack.db)
    client, _ = subscribe_gm(stack)
    return client


TOKEN = {"x": 300, "y": 300, "size": 1, "label": "Goblin", "color": 65280, "artAssetId": None,
         "actorId": None, "hidden": False, "locked": False, "controllerIds": []}


# --- every command type ----------------------------------------------------------------------

def test_every_contract_command_type_has_exactly_one_policy():
    variants = REGISTRY.manifest["commandVariants"]
    assert sorted(variants) == sorted(POLICIES)
    assert len(variants) == 20          # commands-1 (12), parity-1 fog (3), parity-2 templates/terrain (4), e-7 (1)


def test_the_service_refuses_to_start_when_policies_and_contract_disagree(stack, monkeypatch):
    monkeypatch.setitem(REGISTRY.manifest, "commandVariants", [*REGISTRY.manifest["commandVariants"], "x.y"])
    with pytest.raises(RuntimeError):
        CommandService(stack.db, REGISTRY, stack.service.commands.key, stack.hub, stack.host.is_campaign_gm)


def test_token_create_is_acked_after_commit_and_completes_once_on_the_origin(stack):
    client = gm(stack)
    other, _ = subscribe_gm(stack)                   # a second GM window on the same scene
    stack.sent.clear()
    cid, ack, after = send(stack, client, "token.create", {"id": "tok_gob", **TOKEN})
    assert ack == {"schemaVersion": 1, "commandId": cid, "outcome": "applied"}
    # The ack is sent only after the receipt and the outbox row committed.
    assert [e for _, e, _ in after][0] == "vtt.ack"
    assert count(stack, VttCommandReceipt, command_id=cid, outcome="applied") == 1
    assert count(stack, VttOutbox) == 7               # 6 seeded + 1
    origin_sid = after[0][0]
    patches = [(sid, d) for sid, e, d in after if e == "vtt.patch"]
    assert len(patches) == 2
    mine = [d for sid, d in patches if sid == origin_sid]
    theirs = [d for sid, d in patches if sid != origin_sid]
    assert mine[0]["originCommandId"] == cid and mine[0]["seq"] == 1
    assert [e["id"] for e in mine[0]["upserted"]] == ["tok_gob"]
    assert "originCommandId" not in theirs[0] and [e["id"] for e in theirs[0]["upserted"]] == ["tok_gob"]
    for _, frame in patches:
        REGISTRY.validate("gm", frame)
    assert received(other, "vtt.ack") == []           # only the issuer is acknowledged


def test_move_update_delete_light_and_wall_commands_apply(stack):
    client = gm(stack)
    _, ack, _ = send(stack, client, "token.move", {"id": "tok_hero", "x": 128, "y": 64},
                     expected=expect(stack, "token", "tok_hero"))
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, client, "token.update", {"id": "tok_hero", "label": "Valeros the Brave", "locked": True},
                     expected=expect(stack, "token", "tok_hero"))
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, client, "light.create",
                     {"id": "light_2", "x": 5, "y": 5, "radius": 64, "color": 1, "hidden": True})
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, client, "light.update", {"id": "light_2", "radius": 96},
                     expected=expect(stack, "light", "light_2"))
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, client, "wall.create",
                     {"id": "wall_2", "x1": 0, "y1": 64, "x2": 64, "y2": 64, "doorState": "closed"})
    assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, client, "wall.update", {"id": "wall_2", "doorState": "open"},
                     expected=expect(stack, "wall", "wall_2"))
    assert ack["outcome"] == "applied"
    _, ack, after = send(stack, client, "entity.delete", {"kind": "wall", "id": "wall_2"},
                         expected=expect(stack, "wall", "wall_2"))
    assert ack["outcome"] == "applied"
    [patch] = [d for _, e, d in after if e == "vtt.patch"]
    assert patch["removed"] == [{"kind": "wall", "id": "wall_2"}] and patch["upserted"] == []
    with stack.db.session() as session:
        hero = session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_hero"))
        light = session.get(VttEntity, ("camp_a", "scene_a", "light", "light_2"))
        assert hero.data["x"] == 128 and hero.data["label"] == "Valeros the Brave" and hero.data["locked"] is True
        assert light.hidden is True and light.data["radius"] == 96
        assert session.get(VttEntity, ("camp_a", "scene_a", "wall", "wall_2")) is None
    # seq is dense on the origin: one completion frame per applied command
    seqs = [d["seq"] for _, e, d in stack.sent if e == "vtt.patch"]
    assert seqs == list(range(1, 8))


def test_hiding_a_token_removes_it_from_the_table_and_completes_on_the_gm(stack):
    seed_scene(stack.db)
    client, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    received(table)
    cid, ack, after = send(stack, client, "token.update", {"id": "tok_hero", "hidden": True},
                           expected=expect(stack, "token", "tok_hero"))
    assert ack["outcome"] == "applied"
    [table_patch] = [payload(m) for m in received(table, "vtt.patch")]
    REGISTRY.validate("table", table_patch)
    assert table_patch["removed"] == [{"kind": "token", "id": "tok_hero"}]
    assert "originCommandId" not in table_patch
    gm_patch = next(d for _, e, d in after if e == "vtt.patch" and d.get("originCommandId") == cid)
    assert gm_patch["upserted"][0]["hidden"] is True


def test_a_scene_create_elsewhere_completes_with_an_empty_patch_on_the_origin_only(stack):
    client = gm(stack)
    other, _ = subscribe_gm(stack)
    received(other)
    cid, ack, after = send(stack, client, "scene.create",
                           {"name": "Docks", "width": 1024, "height": 1024, "gridSize": 64, "showGrid": True,
                            "backgroundAssetId": None}, scene="scene_new")
    assert ack["outcome"] == "applied"
    patches = [d for _, e, d in after if e == "vtt.patch"]
    assert patches == [{"schemaVersion": 1, "frame": "patch", "subscriptionId": patches[0]["subscriptionId"],
                        "seq": 1, "upserted": [], "removed": [], "originCommandId": cid}]
    REGISTRY.validate("gm", patches[0])
    assert received(other) == []                      # no empty frame, no seq for anyone else
    with stack.db.session() as session:
        assert session.get(VttScene, ("camp_a", "scene_new")).published is False


def test_scene_update_and_table_show_and_clear(stack):
    seed_scene(stack.db, published=False, point_table=False)
    client, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    assert payload(received(table, "vtt.snapshot")[0])["sceneId"] is None
    _, ack, _ = send(stack, client, "scene.update", {"name": "Battlefield at dusk", "gridSize": 50},
                     expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    cid, ack, after = send(stack, client, "table.show", {}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    gm_patch = next(d for _, e, d in after if e == "vtt.patch" and d.get("originCommandId") == cid)
    assert gm_patch["upserted"][0]["kind"] == "scene" and gm_patch["upserted"][0]["published"] is True
    messages = received(table)
    assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
    assert payload(messages[1])["sceneId"] == "scene_a"
    assert payload(messages[1])["entities"][0]["name"] == "Battlefield at dusk"
    # unpublishing the scene on the table is a rule violation; clearing first works
    _, ack, _ = send(stack, client, "scene.update", {"published": False},
                     expected=expect(stack, "scene", "scene_a"))
    assert ack["code"] == "rule-violation"
    _, ack, _ = send(stack, client, "table.clear", {}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    messages = received(table)
    assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
    assert payload(messages[1])["sceneId"] is None and payload(messages[1])["entities"] == []
    _, ack, _ = send(stack, client, "scene.update", {"published": False},
                     expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"


# --- deterministic rejections are receipted --------------------------------------------------

def _rejected(stack, ack, cid, code):
    assert ack == {"schemaVersion": 1, "commandId": cid, "outcome": "rejected", "code": code}
    assert count(stack, VttCommandReceipt, command_id=cid, outcome="rejected", code=code) == 1


def test_a_stale_version_is_rejected_and_nothing_changes(stack):
    client = gm(stack)
    old = expect(stack, "token", "tok_hero")
    send(stack, client, "token.move", {"id": "tok_hero", "x": 100, "y": 64}, expected=old)
    outbox = count(stack, VttOutbox)
    stack.sent.clear()
    cid, ack, after = send(stack, client, "token.move", {"id": "tok_hero", "x": 999, "y": 64}, expected=old)
    _rejected(stack, ack, cid, "stale")
    assert count(stack, VttOutbox) == outbox and [e for _, e, _ in after] == ["vtt.ack"]
    with stack.db.session() as session:
        assert session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_hero")).data["x"] == 100


def test_creating_an_existing_id_is_a_conflict(stack):
    client = gm(stack)
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_hero", **TOKEN})
    _rejected(stack, ack, cid, "conflict")
    cid, ack, _ = send(stack, client, "scene.create", {"name": "X", "width": 64, "height": 64, "gridSize": 8,
                                                      "showGrid": False, "backgroundAssetId": None})
    _rejected(stack, ack, cid, "conflict")


def test_missing_and_other_campaign_targets_are_the_same_validation_failure(stack):
    client = gm(stack)
    W.create_scene(stack.db, "camp_b", "scene_b", name="Other campaign")
    W.upsert_entity(stack.db, "camp_b", "scene_b", "token", "tok_b", {"x": 1, "y": 1})
    fake = [{"kind": "token", "id": "tok_b", "version": "v" + "A" * 24}]
    cid, ack, _ = send(stack, client, "token.move", {"id": "tok_b", "x": 2, "y": 2}, expected=fake, scene="scene_b")
    _rejected(stack, ack, cid, "validation-failed")
    fake = [{"kind": "token", "id": "tok_none", "version": "v" + "A" * 24}]
    cid, ack, _ = send(stack, client, "token.move", {"id": "tok_none", "x": 2, "y": 2}, expected=fake)
    _rejected(stack, ack, cid, "validation-failed")


def test_campaign_scoped_ids_let_two_campaigns_use_the_same_ids(stack):
    # Ids are chosen by the client; a global key would make this a conflict and
    # tell campaign A that campaign B has a scene called scene_b.
    stack.host.gms.add(("user_gm", "camp_b"))
    W.create_scene(stack.db, "camp_b", "scene_b", name="B")
    client = gm(stack)
    _, ack, _ = send(stack, client, "scene.create", {"name": "Mine", "width": 64, "height": 64, "gridSize": 8,
                                                    "showGrid": False, "backgroundAssetId": None}, scene="scene_b")
    assert ack["outcome"] == "applied"


def test_a_zero_length_wall_made_by_an_update_is_a_validation_failure(stack):
    client = gm(stack)
    cid, ack, _ = send(stack, client, "wall.update", {"id": "wall_secret", "x2": 0},
                       expected=expect(stack, "wall", "wall_secret"))
    _rejected(stack, ack, cid, "validation-failed")


# --- receipts --------------------------------------------------------------------------------

def test_a_retry_returns_the_recorded_ack_without_reapplying_or_a_second_completion(stack):
    client = gm(stack)
    expected = expect(stack, "token", "tok_hero")
    cid, first, _ = send(stack, client, "token.move", {"id": "tok_hero", "x": 200, "y": 64}, expected=expected)
    outbox, patches = count(stack, VttOutbox), stack.hub.stats["patches"]
    _, again, after = send(stack, client, "token.move", {"id": "tok_hero", "x": 200, "y": 64},
                           expected=expected, command_id=cid)
    assert again == first
    assert [e for _, e, _ in after] == ["vtt.ack"]          # no frame, no seq
    assert count(stack, VttOutbox) == outbox and stack.hub.stats["patches"] == patches
    assert count(stack, VttCommandReceipt, command_id=cid) == 1
    assert stack.service.commands.stats["replayed"] == 1


def test_a_retry_of_a_rejected_command_returns_the_same_rejection(stack):
    client = gm(stack)
    stale = [{"kind": "token", "id": "tok_hero", "version": "v" + "B" * 24}]
    cid, first, _ = send(stack, client, "token.move", {"id": "tok_hero", "x": 1, "y": 1}, expected=stale)
    _, again, _ = send(stack, client, "token.move", {"id": "tok_hero", "x": 1, "y": 1}, expected=stale,
                       command_id=cid)
    assert first["code"] == "stale" and again == first
    assert count(stack, VttCommandReceipt, command_id=cid) == 1


def test_reusing_a_command_id_with_another_request_is_refused_without_a_receipt(stack):
    client = gm(stack)
    expected = expect(stack, "token", "tok_hero")
    cid, _, _ = send(stack, client, "token.move", {"id": "tok_hero", "x": 200, "y": 64}, expected=expected)
    _, ack, after = send(stack, client, "token.move", {"id": "tok_hero", "x": 999, "y": 64},
                         expected=expected, command_id=cid)
    assert ack == {"schemaVersion": 1, "commandId": cid, "outcome": "rejected", "code": "command-id-reused"}
    assert count(stack, VttCommandReceipt, command_id=cid) == 1
    with stack.db.session() as session:
        assert session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_hero")).data["x"] == 200


def test_integral_floats_hash_like_ints(stack):
    client = gm(stack)
    cid, first, _ = send(stack, client, "token.create", {"id": "tok_f", **TOKEN})
    _, again, _ = send(stack, client, "token.create", {"id": "tok_f", **TOKEN, "x": 300.0}, command_id=cid)
    assert again == first


# --- refusals that create no receipt ---------------------------------------------------------

@pytest.mark.parametrize("mutate,code", [
    (lambda m: m.update(schemaVersion=2), "unsupported-version"),
    (lambda m: m.update(type="token.teleport"), "unknown-command"),
    (lambda m: m["payload"].update(hp=3), "invalid-schema"),
    (lambda m: m.update(expected=[]), "invalid-schema"),
    (lambda m: m.update(campaignId="camp_b"), "unauthorized"),
])
def test_envelope_and_authorization_refusals_write_no_receipt(stack, mutate, code):
    client = gm(stack)
    message = {"schemaVersion": 1, "commandId": new_id(), "campaignId": "camp_a", "sceneId": "scene_a",
               "type": "token.move", "expected": expect(stack, "token", "tok_hero"),
               "payload": {"id": "tok_hero", "x": 1, "y": 1}}
    mutate(message)
    start = len(stack.sent)
    client.emit("vtt.command", message, namespace=NAMESPACE)
    [ack] = [d for _, e, d in stack.sent[start:] if e == "vtt.ack"]
    REGISTRY.validate("acknowledgement", ack)
    assert ack["code"] == code
    assert count(stack, VttCommandReceipt) == 0
    assert client.is_connected(NAMESPACE)


def test_a_removed_gm_is_unauthorized(stack):
    client = gm(stack)
    stack.host.gms.clear()
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_x", **TOKEN})
    assert ack["code"] == "unauthorized" and count(stack, VttCommandReceipt) == 0


@pytest.mark.parametrize("message", [None, "text", {"commandId": "short"}, {"commandId": 7}, {}])
def test_a_command_that_cannot_be_acknowledged_disconnects(stack, message):
    client = gm(stack)
    client.emit("vtt.command", message, namespace=NAMESPACE)
    assert not client.is_connected(NAMESPACE)
    assert count(stack, VttCommandReceipt) == 0


def test_a_table_sending_a_command_is_disconnected_and_nothing_is_written(stack):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    outbox = count(stack, VttOutbox)
    table.emit("vtt.command", {"schemaVersion": 1, "commandId": new_id(), "campaignId": "camp_a",
                               "sceneId": "scene_a", "type": "token.create", "expected": [],
                               "payload": {"id": "tok_t", **TOKEN}}, namespace=NAMESPACE)
    assert not table.is_connected(NAMESPACE)
    assert count(stack, VttOutbox) == outbox and count(stack, VttCommandReceipt) == 0


def test_a_command_without_a_live_subscription_must_resync_first(stack):
    seed_scene(stack.db)
    client = stack.gm_client()                        # connected, never subscribed
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_x", **TOKEN})
    assert ack["code"] == "resync-required" and count(stack, VttCommandReceipt) == 0


def test_the_rate_limit_refuses_without_a_receipt(stack):
    stack.service.commands.rate = RateLimiter(rate=0.0, burst=2)
    client = gm(stack)
    codes = [send(stack, client, "token.create", {"id": f"tok_{i}", **TOKEN})[1].get("code") for i in range(3)]
    assert codes == [None, None, "rate-limited"]
    assert count(stack, VttCommandReceipt) == 2


def test_an_internal_error_is_a_server_error_with_nothing_committed(stack, monkeypatch):
    client = gm(stack)

    def boom(ctx):
        raise KeyError("handler bug")

    monkeypatch.setattr(service_module, "POLICIES", POLICIES)
    monkeypatch.setitem(POLICIES, "token.create", POLICIES["token.create"].__class__(
        POLICIES["token.create"].capability, boom))
    outbox = count(stack, VttOutbox)
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_x", **TOKEN})
    assert ack["code"] == "server-error"
    assert count(stack, VttOutbox) == outbox and count(stack, VttCommandReceipt) == 0


def test_a_commit_that_raises_after_committing_still_delivers(stack, monkeypatch):
    # The worst case of an uncertain commit: the database committed, then the
    # driver raised. The ack must not claim success, but the frames must still go.
    client = gm(stack)
    from sqlalchemy.orm import Session
    real_commit = Session.commit

    def commit_then_raise(self):
        real_commit(self)
        raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(Session, "commit", commit_then_raise)
    cid, ack, after = send(stack, client, "token.create", {"id": "tok_x", **TOKEN})
    monkeypatch.setattr(Session, "commit", real_commit)
    assert ack["code"] == "temporarily-unavailable"
    [patch] = [d for _, e, d in after if e == "vtt.patch"]
    assert patch["originCommandId"] == cid and patch["upserted"][0]["id"] == "tok_x"
    _, retry, after = send(stack, client, "token.create", {"id": "tok_x", **TOKEN}, command_id=cid)
    assert retry["outcome"] == "applied" and [e for _, e, _ in after] == ["vtt.ack"]


def test_a_failed_commit_is_temporarily_unavailable_not_server_error(stack, monkeypatch):
    # The outcome of a commit that raised is unknown; the client must retry the
    # same commandId and let the receipt answer.
    client = gm(stack)
    from sqlalchemy.orm import Session
    real_commit = Session.commit
    calls = {"n": 0}

    def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection lost during commit")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_x", **TOKEN})
    assert ack["code"] == "temporarily-unavailable"
    monkeypatch.setattr(Session, "commit", real_commit)
    _, retry, _ = send(stack, client, "token.create", {"id": "tok_x", **TOKEN}, command_id=cid)
    assert retry["outcome"] == "applied", retry
    assert count(stack, VttCommandReceipt, command_id=cid) == 1


# --- origin binding --------------------------------------------------------------------------

def test_a_recovered_origin_gets_no_completion_for_the_old_subscription(stack):
    client = gm(stack)
    sid = stack.sent[-1][0]
    old = stack.hub.subscription(sid)
    expected = expect(stack, "token", "tok_hero")
    message = {"schemaVersion": 1, "commandId": new_id(), "campaignId": "camp_a", "sceneId": "scene_a",
               "type": "token.move", "expected": expected, "payload": {"id": "tok_hero", "x": 400, "y": 64}}
    result = stack.service.commands.handle(sid, old.principal, message)   # committed, not yet delivered
    assert result.ack["outcome"] == "applied"
    stack.sent.clear()
    with old.lock:
        stack.hub._recover(old, "state-lost")
    stack.hub.notify("camp_a")
    names = [e for _, e, _ in stack.sent]
    assert names == ["vtt.resync_required", "vtt.snapshot"]           # the snapshot already has the move
    snap = stack.sent[1][2]
    assert next(e for e in snap["entities"] if e["id"] == "tok_hero")["x"] == 400
    assert not any(e == "vtt.patch" for _, e, _ in stack.sent)


def test_a_client_model_converges_whether_the_ack_or_the_frame_arrives_first(stack):
    """Criterion 15 against this server's real messages: a reference model of the
    client's overlay rule (ack alone never mutates canonical state; the correlated
    frame or a snapshot clears the overlay) reaches the same state either way."""
    client = gm(stack)
    cid, ack, after = send(stack, client, "token.move", {"id": "tok_hero", "x": 512, "y": 64},
                           expected=expect(stack, "token", "tok_hero"))
    frames = [(e, d) for sid, e, d in after if sid == after[0][0]]
    sid = after[0][0]
    snapshot = next(d for s, e, d in stack.sent if s == sid and e == "vtt.snapshot")

    def run(order):
        canonical = {(e["kind"], e["id"]): e for e in snapshot["entities"]}
        overlay = {cid: {"x": 512}}
        for event, data in order:
            if event == "vtt.ack" and data["outcome"] == "rejected":
                overlay.pop(data["commandId"], None)
            elif event == "vtt.patch":
                for e in data["upserted"]:
                    canonical[(e["kind"], e["id"])] = e
                overlay.pop(data.get("originCommandId"), None)
        return canonical[("token", "tok_hero")]["x"], overlay

    assert [e for e, _ in frames] == ["vtt.ack", "vtt.patch"]
    assert run(frames) == run(list(reversed(frames))) == (512, {})


# --- the write-time size rule ----------------------------------------------------------------

def test_the_sizing_estimate_bounds_the_real_encoded_snapshot(stack):
    seed_scene(stack.db)
    for i in range(40):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", f"tok_{i:03d}",
                        {"x": i, "y": i, "label": "é" * 80, "controllerIds": [f"user_{j}" for j in range(8)]})
    client, _ = subscribe_gm(stack)
    [message] = [m for m in client.get_received(NAMESPACE) if m["name"] == "vtt.snapshot"]
    from vtt.protocol import StrictJSON
    real = len(StrictJSON.dumps(["vtt.snapshot", payload(message)], separators=(",", ":")).encode("utf-8"))
    with stack.db.session() as session:
        estimate, gm_count, table_count = gm_snapshot_bytes(session, "camp_a", "scene_a")
    # Same entities and fixed-length tokens; the estimate uses the longest ids, so
    # it is an upper bound that is tight to the id padding, plus the room it
    # reserves for a full set of tracker actors (parity-3; none exist here).
    reserve = sum(len(json.dumps(S.gm_actor(service_module._SizingKey(), "camp_a", aid, data),
                                 separators=(",", ":")).encode("utf-8")) + 1
                  for aid, data in service_module._WORST_ACTORS.items())
    assert real + reserve <= estimate <= real + reserve + 2 * 128 + 64
    # scene + 4 seeded + 40 tokens + fog + terrain, plus the reserved actors
    # (they count against the GM entity cap); the table: scene, hero, torch,
    # 40 tokens, fog, terrain, and the wall (E-6: counted as if lighting were on)
    from vtt import live
    assert gm_count == 47 + live.MAX_ACTORS and table_count == 1 + 1 + 1 + 40 + 1 + 1 + 1


def test_a_command_that_would_push_the_gm_snapshot_over_the_limit_is_refused(stack, monkeypatch):
    client = gm(stack)
    with stack.db.session() as session:
        size, _, _ = gm_snapshot_bytes(session, "camp_a", "scene_a")
    # Leave room for exactly one more small token, not two.
    small = {"id": "t_1", **TOKEN, "label": ""}
    monkeypatch.setattr(service_module, "MAX_JSON_BYTES", size + service_module.FRAME_HEADROOM + 260)
    _, ack, _ = send(stack, client, "token.create", small)
    assert ack["outcome"] == "applied"
    cid, ack, _ = send(stack, client, "token.create", {**small, "id": "t_2"})
    _rejected(stack, ack, cid, "validation-failed")
    with stack.db.session() as session:
        assert session.get(VttEntity, ("camp_a", "scene_a", "token", "t_2")) is None
    # a move that shrinks nothing still fits, and a delete is never refused
    _, ack, _ = send(stack, client, "entity.delete", {"kind": "token", "id": "t_1"},
                     expected=expect(stack, "token", "t_1"))
    assert ack["outcome"] == "applied"


@pytest.mark.parametrize("cap,hidden", [("GM_ENTITY_CAP", True), ("TABLE_ENTITY_CAP", False)])
def test_the_schema_entity_caps_are_enforced_at_write_time(stack, monkeypatch, cap, hidden):
    client = gm(stack)    # 5 GM entities (scene + 4), 3 table entities (scene, hero, torch)
    monkeypatch.setattr(service_module, cap, 5 if cap == "GM_ENTITY_CAP" else 3)
    cid, ack, _ = send(stack, client, "token.create", {"id": "tok_over", **TOKEN, "hidden": hidden})
    _rejected(stack, ack, cid, "validation-failed")


# --- a failed acknowledgement never suppresses delivery (handoff 031) -----------------------

def _fail_acks(stack, times=1):
    real = stack.hub.emit
    left = {"n": times}

    def emit(sid, event, data):
        if event == "vtt.ack" and left["n"]:
            left["n"] -= 1
            raise OSError("ack write failed")
        real(sid, event, data)

    stack.hub.emit = emit


def test_a_failed_ack_emit_still_delivers_to_observers_and_completes_on_the_issuer(stack):
    client = gm(stack)
    observer, _ = subscribe_gm(stack)
    received(observer)
    issuer_sid = stack.sent[0][0]
    stack.sent.clear()
    _fail_acks(stack)
    cid, ack, after = send(stack, client, "token.create", {"id": "tok_lost_ack", **TOKEN},
                           command_id="cmd_fixed_lost_ack_01")
    assert ack is None                                     # the ack never left
    assert stack.service.commands.stats["ack_errors"] == 1
    # without any further write: the observer has the change, the issuer its completion
    [seen] = [payload(m) for m in received(observer, "vtt.patch")]
    assert [e["id"] for e in seen["upserted"]] == ["tok_lost_ack"] and "originCommandId" not in seen
    [done] = [d for sid, e, d in after if sid == issuer_sid and e == "vtt.patch"]
    assert done["originCommandId"] == cid and done["seq"] == 1
    assert stack.hub.subscription(issuer_sid).cursor == count(stack, VttOutbox)
    # the client retries after its timeout: recorded outcome, nothing applied twice, no second frame
    outbox = count(stack, VttOutbox)
    _, retry, after = send(stack, client, "token.create", {"id": "tok_lost_ack", **TOKEN}, command_id=cid)
    assert retry == {"schemaVersion": 1, "commandId": cid, "outcome": "applied"}
    assert [e for _, e, _ in after] == ["vtt.ack"]
    assert count(stack, VttOutbox) == outbox and count(stack, VttCommandReceipt, command_id=cid) == 1


def test_an_uncertain_commit_with_a_failed_ack_still_delivers(stack, monkeypatch):
    client = gm(stack)
    observer, _ = subscribe_gm(stack)
    received(observer)
    from sqlalchemy.orm import Session
    real_commit = Session.commit

    def commit_then_raise(self):
        real_commit(self)
        raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(Session, "commit", commit_then_raise)
    _fail_acks(stack)
    send(stack, client, "token.create", {"id": "tok_uncertain", **TOKEN})
    monkeypatch.setattr(Session, "commit", real_commit)
    [seen] = [payload(m) for m in received(observer, "vtt.patch")]
    assert [e["id"] for e in seen["upserted"]] == ["tok_uncertain"]


def test_a_retry_repairs_a_lost_wake_up_without_reapplying(stack):
    # Commit through the pipeline but drop its wake-up entirely (as if the process
    # had failed between commit and notify). The retry's ack wakes delivery.
    client = gm(stack)
    issuer_sid = stack.sent[0][0]
    sub = stack.hub.subscription(issuer_sid)
    message = {"schemaVersion": 1, "commandId": "cmd_fixed_lost_wake_1", "campaignId": "camp_a",
               "sceneId": "scene_a", "type": "token.create", "expected": [],
               "payload": {"id": "tok_wake", **TOKEN}}
    assert stack.service.commands.handle(issuer_sid, sub.principal, message).ack["outcome"] == "applied"
    stack.sent.clear()
    outbox = count(stack, VttOutbox)
    _, retry, after = send(stack, client, "token.create", {"id": "tok_wake", **TOKEN},
                           command_id="cmd_fixed_lost_wake_1")
    assert retry["outcome"] == "applied"
    patches = [d for _, e, d in after if e == "vtt.patch"]
    assert len(patches) == 1 and patches[0]["originCommandId"] == "cmd_fixed_lost_wake_1"
    assert count(stack, VttOutbox) == outbox
    # and a further retry finds nothing left to send
    _, _, after = send(stack, client, "token.create", {"id": "tok_wake", **TOKEN},
                       command_id="cmd_fixed_lost_wake_1")
    assert [e for _, e, _ in after] == ["vtt.ack"]
