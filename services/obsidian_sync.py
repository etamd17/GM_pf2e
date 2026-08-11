"""Flask routes for the Obsidian Session Operations integration.

The route layer owns transport concerns (bearer auth, optimistic revisions,
idempotency, and event recording).  PF2e mutations are delegated to callbacks
from app.py so this integration cannot become a second combat rules engine.
"""
from __future__ import annotations

import copy
import re
import uuid

from flask import Blueprint, g, jsonify, render_template, request

from core import obsidian_sync as sync_store


API_PREFIX = "/api/integrations/obsidian/v1"
COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,120}$")
NUMERIC_CONDITIONS = {
    "frightened", "sickened", "dying", "wounded", "doomed", "stunned",
    "slowed", "enfeebled", "clumsy", "drained", "stupefied",
}
BOOLEAN_CONDITIONS = {"prone", "off_guard", "concealed", "hidden", "undetected"}


def _json_error(message: str, status: int, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()


# What an event records about its target. Deliberately a fixed projection of
# the VOLATILE fields, never the whole record.
#
# This is the difference between a durable log and an unbounded one. Every
# accepted command appends before/after context to events.jsonl (which the vault
# plugin then mirrors into Combat Events.jsonl), and neither file rotates. A
# deepcopy of the full combatant wrote the target's entire statblock -- strikes,
# feats, spell lists -- into the log twice per command, and the statblock does
# not change between them, so it was pure duplication that grew forever.
#
# The projection is also the honest one for the post-session digest, whose only
# question is "what changed": HP, temp HP, conditions, initiative. Anything
# static belongs in the statblock, not in a per-event snapshot of it.
_EVENT_TARGET_FIELDS = (
    "instance_id", "name", "is_pc", "current_hp", "max_hp", "temp_hp",
    "conditions", "initiative", "persistent_damage",
)


def _project_target(record: dict) -> dict:
    return {k: copy.deepcopy(record[k]) for k in _EVENT_TARGET_FIELDS if k in record}


def _target_from_snapshot(snapshot: dict, payload: dict) -> dict | None:
    target_id = payload.get("target_id")
    if target_id:
        for combatant in snapshot.get("encounter", {}).get("combatants", []):
            if combatant.get("instance_id") == target_id:
                return _project_target(combatant)
    pc_name = payload.get("pc_name")
    if pc_name:
        for pc in snapshot.get("party", []):
            if pc.get("name") == pc_name:
                return _project_target(pc)
    return None


def _event_context(snapshot: dict, payload: dict) -> dict:
    encounter = snapshot.get("encounter", {})
    return {
        "round": encounter.get("round"),
        "turn_index": encounter.get("turn_index"),
        "active_id": encounter.get("active_id"),
        "active_name": encounter.get("active_name"),
        "target": _target_from_snapshot(snapshot, payload),
    }


def _sync_observed_state(runtime: dict, snapshot: dict) -> bool:
    """Notice mutations made by the website UI between Obsidian commands."""
    digest = sync_store.snapshot_hash(snapshot)
    prior = runtime.get("state_hash")
    if prior is None:
        runtime["state_hash"] = digest
        return True
    if prior != digest:
        runtime["state_hash"] = digest
        runtime["revision"] = int(runtime.get("revision", 0) or 0) + 1
        return True
    return False


def _client_state(snapshot: dict, runtime: dict) -> dict:
    """Merge website-owned PF2e state with integration-owned operations state."""
    state = copy.deepcopy(snapshot)
    state["operations"] = {
        "active_room": copy.deepcopy(runtime.get("active_room")),
        "player_requests": copy.deepcopy(runtime.get("player_requests", [])),
        "reveal_history": copy.deepcopy(runtime.get("reveal_history", [])),
    }
    return state


def _dispatch(
    adapter: dict,
    kind: str,
    payload: dict,
    *,
    runtime: dict,
    campaign_id: str,
) -> dict:
    if kind == "adjust_hp":
        target_id = str(payload.get("target_id") or "")
        target = adapter["find_combatant"](target_id)
        if target is None:
            raise LookupError("combatant not found")
        try:
            amount = int(payload.get("amount", 0))
        except (TypeError, ValueError):
            raise ValueError("amount must be an integer")
        if amount < 1 or amount > 100000:
            raise ValueError("amount must be between 1 and 100000")
        action = str(payload.get("action") or "").lower()
        if action not in ("damage", "heal"):
            raise ValueError("action must be damage or heal")
        damage_type = str(payload.get("damage_type") or "untyped")[:80]
        old_hp = adapter["apply_hp"](target_id, amount, action, damage_type)
        new_target = adapter["find_combatant"](target_id)
        new_hp = int(getattr(new_target, "current_hp", 0) or 0) if new_target else 0
        defeated = adapter["maybe_remove_defeated"](target_id, old_hp, action)
        if defeated:
            adapter["persist_encounter"]()
            adapter["broadcast_encounter"]()
        return {
            "target_id": target_id,
            "action": action,
            "raw_amount": amount,
            "old_hp": old_hp,
            "new_hp": new_hp,
            "defeated": defeated,
        }

    if kind == "adjust_party_hp":
        pc_name = str(payload.get("pc_name") or "")
        try:
            amount = int(payload.get("amount", 0))
        except (TypeError, ValueError):
            raise ValueError("amount must be an integer")
        action = str(payload.get("action") or "").lower()
        if not pc_name:
            raise ValueError("pc_name is required")
        if amount < 1 or amount > 100000:
            raise ValueError("amount must be between 1 and 100000")
        if action not in ("damage", "heal"):
            raise ValueError("action must be damage or heal")
        result = adapter["adjust_party_hp"](pc_name, amount, action)
        if result is None:
            raise LookupError("party member not found")
        return result

    if kind == "condition_action":
        target_id = str(payload.get("target_id") or "")
        if adapter["find_combatant"](target_id) is None:
            raise LookupError("combatant not found")
        condition = str(payload.get("condition") or "").lower().replace("-", "_")
        if condition not in NUMERIC_CONDITIONS | BOOLEAN_CONDITIONS:
            raise ValueError("unsupported PF2e condition")
        action = str(payload.get("action") or "").lower()
        allowed = {"increase", "add", "decrease", "toggle", "remove"}
        if action not in allowed:
            raise ValueError("unsupported condition action")
        try:
            rounds = max(0, min(int(payload.get("rounds", 0) or 0), 1000))
        except (TypeError, ValueError):
            raise ValueError("rounds must be an integer")
        if not adapter["apply_condition"](target_id, condition, action, rounds):
            raise LookupError("combatant not found")
        target = adapter["find_combatant"](target_id)
        return {
            "target_id": target_id,
            "condition": condition,
            "value": (getattr(target, "conditions", {}) or {}).get(condition, 0),
            "rounds": rounds,
        }

    if kind == "set_initiative":
        target_id = str(payload.get("target_id") or "")
        target = adapter["find_combatant"](target_id)
        if target is None:
            raise LookupError("combatant not found")
        try:
            value = int(payload.get("initiative"))
        except (TypeError, ValueError):
            raise ValueError("initiative must be an integer")
        if value < -1000 or value > 1000:
            raise ValueError("initiative must be between -1000 and 1000")
        target.initiative = value
        adapter["sort_encounter"]()
        adapter["persist_encounter"]()
        adapter["broadcast_encounter"]()
        return {"target_id": target_id, "initiative": value}

    if kind == "advance_turn":
        direction = str(payload.get("direction") or "next").lower()
        if direction not in ("next", "prev"):
            raise ValueError("direction must be next or prev")
        if not adapter["has_encounter"]():
            raise ValueError("there is no active encounter")
        adapter["advance_turn"](direction)
        return {"direction": direction}

    if kind == "capture_note":
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("note text is required")
        if len(text) > 4000:
            raise ValueError("note text must be 4000 characters or fewer")
        category = str(payload.get("category") or "table_note")[:80]
        room_id = str(payload.get("room_id") or "")[:120] or None
        return {"text": text, "category": category, "room_id": room_id}

    if kind == "roll":
        return adapter["resolve_roll"](payload)

    if kind == "set_active_room":
        area_id = str(payload.get("area_id") or "").strip()[:160]
        title = str(payload.get("title") or "").strip()[:240]
        path = str(payload.get("path") or "").strip()[:500]
        if not area_id or not title:
            raise ValueError("area_id and title are required")
        room = {
            "area_id": area_id,
            "title": title,
            "path": path,
            "entered_at": sync_store.utc_now(),
            "manifest_version": int(payload.get("manifest_version", 1) or 1),
        }
        runtime["active_room"] = room
        return {"active_room": copy.deepcopy(room)}

    if kind == "launch_room_encounter":
        if adapter["has_encounter"]() and not bool(payload.get("replace_active")):
            raise ValueError("an encounter is already active; explicit replacement confirmation is required")
        result = adapter["launch_room_encounter"](payload)
        if not isinstance(result, dict):
            raise ValueError("encounter launch failed")
        return result

    if kind == "sync_room_reminders":
        area_id = str(payload.get("area_id") or "").strip()[:160]
        reminders = payload.get("reminders") or []
        if not area_id:
            raise ValueError("area_id is required")
        if not isinstance(reminders, list):
            raise ValueError("reminders must be a list")
        return adapter["sync_room_reminders"](area_id, reminders)

    if kind == "resolve_player_request":
        request_id = str(payload.get("request_id") or "").strip()
        action = str(payload.get("action") or "resolve").strip().lower()
        if action not in ("resolve", "dismiss", "reopen"):
            raise ValueError("action must be resolve, dismiss, or reopen")
        found = next((item for item in runtime.get("player_requests", [])
                      if item.get("id") == request_id), None)
        if found is None:
            raise LookupError("player request not found")
        found["status"] = {
            "reopen": "open",
            "resolve": "resolved",
            "dismiss": "dismissed",
        }[action]
        found["resolved_at"] = None if action == "reopen" else sync_store.utc_now()
        found["resolution"] = (
            None if action == "reopen"
            else str(payload.get("resolution") or "").strip()[:1000] or None
        )
        return {"player_request": copy.deepcopy(found)}

    if kind == "create_player_reveal":
        title = str(payload.get("title") or "Handout").strip()[:160]
        content = str(payload.get("content") or "").strip()[:20000]
        image = payload.get("image")
        recipients = payload.get("recipients") or ["all"]
        if not isinstance(recipients, list) or not recipients:
            raise ValueError("recipients must be a non-empty list")
        if not content and not image:
            raise ValueError("reveal content or image is required")
        reveal = adapter["create_player_reveal"]({
            "title": title,
            "content": content,
            "image": image,
            "recipients": [str(value)[:120] for value in recipients[:50]],
            "source": str(payload.get("source") or "Obsidian")[:500],
        })
        sync_store.remember_reveal(runtime, reveal)
        return {"reveal": reveal}

    raise ValueError(f"unsupported command type: {kind}")


def create_obsidian_sync_blueprint(adapter: dict) -> Blueprint:
    required = {
        "active_campaign_id", "campaign_doc", "snapshot", "find_combatant",
        "apply_hp", "maybe_remove_defeated", "adjust_party_hp", "apply_condition",
        "sort_encounter", "persist_encounter", "broadcast_encounter",
        "advance_turn", "has_encounter", "gm_required", "resolve_roll",
        "launch_room_encounter", "sync_room_reminders", "create_player_reveal",
    }
    missing = sorted(required - set(adapter))
    if missing:
        raise ValueError("missing Obsidian sync adapter callbacks: " + ", ".join(missing))

    bp = Blueprint("obsidian_sync", __name__)

    def token_required(fn):
        def wrapped(*args, **kwargs):
            campaign_id = (request.headers.get("X-Campaign-ID") or "").strip()
            token = _bearer_token()
            if not campaign_id or not token:
                return _json_error("campaign id and bearer token are required", 401)
            try:
                token_record = sync_store.verify_token(campaign_id, token)
            except ValueError:
                return _json_error("invalid campaign id", 400)
            if token_record is None:
                return _json_error("invalid or revoked integration token", 401)
            active_id = adapter["active_campaign_id"]()
            if active_id != campaign_id:
                return _json_error(
                    "this token's campaign is not the website's active table",
                    409,
                    campaign_id=campaign_id,
                    active_campaign_id=active_id,
                )
            g.obsidian_campaign_id = campaign_id
            g.obsidian_token = token_record
            return fn(*args, **kwargs)
        wrapped.__name__ = fn.__name__
        return wrapped

    @bp.after_request
    def integration_no_store(response):
        if request.path.startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        return response

    @bp.get("/gm/integrations/obsidian")
    @adapter["gm_required"]
    def integration_page():
        campaign_id = adapter["active_campaign_id"]()
        campaign = adapter["campaign_doc"](campaign_id) if campaign_id else None
        tokens = sync_store.list_tokens(campaign_id) if campaign_id else []
        return render_template(
            "obsidian_integration.html",
            campaign=campaign,
            campaign_id=campaign_id,
            integration_tokens=tokens,
        )

    @bp.post(API_PREFIX + "/tokens")
    @adapter["gm_required"]
    def issue_token():
        # Requiring a non-simple request header prevents a cross-site form POST
        # from minting a token with the GM's ambient session cookie.
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return _json_error("same-origin integration request required", 403)
        campaign_id = adapter["active_campaign_id"]()
        if not campaign_id:
            return _json_error("activate a campaign first", 409)
        body = request.get_json(silent=True) or {}
        raw_token, record = sync_store.create_token(campaign_id, body.get("label") or "Obsidian")
        return jsonify({
            "ok": True,
            "campaign_id": campaign_id,
            "token": raw_token,
            "record": record,
        }), 201

    @bp.delete(API_PREFIX + "/tokens/<token_id>")
    @adapter["gm_required"]
    def revoke_token(token_id):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return _json_error("same-origin integration request required", 403)
        campaign_id = adapter["active_campaign_id"]()
        if not campaign_id:
            return _json_error("activate a campaign first", 409)
        if not sync_store.revoke_token(campaign_id, token_id):
            return _json_error("token not found", 404)
        return jsonify({"ok": True})

    def current_state(campaign_id: str) -> tuple[dict, dict]:
        runtime = sync_store.load_runtime(campaign_id)
        snapshot = _client_state(adapter["snapshot"](), runtime)
        if _sync_observed_state(runtime, snapshot):
            sync_store.save_runtime(campaign_id, runtime)
        response = {
            "ok": True,
            "schema_version": sync_store.SCHEMA_VERSION,
            "revision": int(runtime.get("revision", 0) or 0),
            "event_sequence": int(runtime.get("event_sequence", 0) or 0),
            "session": runtime.get("current_session"),
            "state": snapshot,
        }
        return response, runtime

    @bp.get(API_PREFIX + "/status")
    @token_required
    def status():
        campaign_id = g.obsidian_campaign_id
        state, _ = current_state(campaign_id)
        campaign = adapter["campaign_doc"](campaign_id) or {}
        return jsonify({
            "ok": True,
            "connected": True,
            "campaign": {
                "id": campaign_id,
                "name": campaign.get("name", ""),
                "system": campaign.get("system", "pf2e"),
            },
            "revision": state["revision"],
            "event_sequence": state["event_sequence"],
            "session": state["session"],
        })

    @bp.get(API_PREFIX + "/state")
    @token_required
    def state():
        response, _ = current_state(g.obsidian_campaign_id)
        return jsonify(response)

    def process_command(campaign_id: str, body: dict):
        command_id = str(body.get("command_id") or "")
        if not COMMAND_ID_RE.match(command_id):
            return _json_error("command_id must be 8-120 safe characters", 400)
        kind = str(body.get("type") or "")
        payload = body.get("payload") or {}
        if not isinstance(payload, dict):
            return _json_error("payload must be an object", 400)

        with sync_store.campaign_lock(campaign_id):
            runtime = sync_store.load_runtime(campaign_id)
            prior_response = runtime.get("recent_commands", {}).get(command_id)
            if prior_response is not None:
                replay = copy.deepcopy(prior_response)
                replay["idempotent_replay"] = True
                return jsonify(replay)

            before = _client_state(adapter["snapshot"](), runtime)
            _sync_observed_state(runtime, before)
            try:
                expected_revision = int(body.get("expected_revision"))
            except (TypeError, ValueError):
                return _json_error("expected_revision is required", 400)
            current_revision = int(runtime.get("revision", 0) or 0)
            if expected_revision != current_revision:
                sync_store.save_runtime(campaign_id, runtime)
                return _json_error(
                    "state changed since this command was prepared",
                    409,
                    conflict=True,
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                    state=before,
                    session=runtime.get("current_session"),
                )

            session_before = copy.deepcopy(runtime.get("current_session"))
            try:
                if kind == "start_session":
                    current = runtime.get("current_session")
                    if current and current.get("status") == "live":
                        raise ValueError("a session is already live")
                    session_id = str(payload.get("session_id") or (
                        "session-" + sync_store.utc_now()[:10] + "-" + uuid.uuid4().hex[:8]
                    ))[:120]
                    session_doc = {
                        "id": session_id,
                        "label": str(payload.get("label") or session_id)[:160],
                        "status": "live",
                        "started_at": sync_store.utc_now(),
                        "ended_at": None,
                    }
                    runtime["current_session"] = session_doc
                    result = {"session": copy.deepcopy(session_doc)}
                elif kind == "end_session":
                    current = runtime.get("current_session")
                    if not current or current.get("status") != "live":
                        raise ValueError("there is no live session to end")
                    requested_id = payload.get("session_id")
                    if requested_id and requested_id != current.get("id"):
                        raise ValueError("session_id does not match the live session")
                    current["status"] = "awaiting_processing"
                    current["ended_at"] = sync_store.utc_now()
                    result = {"session": copy.deepcopy(current)}
                else:
                    result = _dispatch(
                        adapter,
                        kind,
                        payload,
                        runtime=runtime,
                        campaign_id=campaign_id,
                    )
            except LookupError as exc:
                return _json_error(str(exc), 404)
            except ValueError as exc:
                return _json_error(str(exc), 400)

            after = _client_state(adapter["snapshot"](), runtime)
            runtime["revision"] = current_revision + 1
            runtime["state_hash"] = sync_store.snapshot_hash(after)
            event_type = {
                "start_session": "session_start",
                "end_session": "session_end",
                "capture_note": "table_note",
                "roll": "roll",
                "set_active_room": "room_entered",
                "launch_room_encounter": "encounter_launched",
                "sync_room_reminders": "room_reminders_synced",
                "resolve_player_request": "player_request_resolved",
                "create_player_reveal": "player_reveal",
            }.get(kind, "combat_command")
            active_session = runtime.get("current_session") or session_before
            event = sync_store.append_event(campaign_id, runtime, {
                "type": event_type,
                "command_id": command_id,
                "command_type": kind,
                "actor": "obsidian",
                "campaign_id": campaign_id,
                "session_id": (active_session or {}).get("id"),
                "revision": runtime["revision"],
                "payload": payload,
                "result": result,
                "before": _event_context(before, payload),
                "after": _event_context(after, payload),
            })
            response = {
                "ok": True,
                "command_id": command_id,
                "revision": runtime["revision"],
                "event_sequence": event["sequence"],
                "result": result,
                "session": runtime.get("current_session"),
                "state": after,
            }
            sync_store.remember_command(runtime, command_id, copy.deepcopy(response))
            sync_store.save_runtime(campaign_id, runtime)
            return jsonify(response)

    @bp.post(API_PREFIX + "/commands")
    @token_required
    def commands():
        return process_command(g.obsidian_campaign_id, request.get_json(silent=True) or {})

    @bp.post(API_PREFIX + "/sessions/start")
    @token_required
    def start_session():
        body = request.get_json(silent=True) or {}
        body["type"] = "start_session"
        body.setdefault("payload", {})
        return process_command(g.obsidian_campaign_id, body)

    @bp.post(API_PREFIX + "/sessions/end")
    @token_required
    def end_session():
        body = request.get_json(silent=True) or {}
        body["type"] = "end_session"
        body.setdefault("payload", {})
        return process_command(g.obsidian_campaign_id, body)

    @bp.get(API_PREFIX + "/events")
    @token_required
    def events():
        try:
            after = max(0, int(request.args.get("after", 0)))
            limit = max(1, min(int(request.args.get("limit", 250)), 1000))
        except (TypeError, ValueError):
            return _json_error("after and limit must be integers", 400)
        campaign_id = g.obsidian_campaign_id
        runtime = sync_store.load_runtime(campaign_id)
        found = sync_store.read_events(campaign_id, after=after, limit=limit)
        return jsonify({
            "ok": True,
            "events": found,
            "event_sequence": int(runtime.get("event_sequence", 0) or 0),
            "revision": int(runtime.get("revision", 0) or 0),
        })

    return bp


def register_obsidian_sync(app, adapter: dict) -> Blueprint:
    bp = create_obsidian_sync_blueprint(adapter)
    app.register_blueprint(bp)
    return bp
