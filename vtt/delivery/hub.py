"""Subscription and delivery hub (VTT-ADR-0002 sections 3, 5 and 6), in process.

M1 keeps subscriptions in this process, which matches production's single
gevent worker. Redis fan-out (a later milestone) replaces only ``notify``'s
wake-up path; PostgreSQL and the outbox stay the replay source, so the wire is
unchanged.

Subscribe, in the section 5 order:
1. the transport has authenticated the principal;
2. a ``Subscription`` is allocated and registered (not yet live) before the
   snapshot transaction opens, so a commit racing the snapshot is not missed;
3. one consistent transaction rechecks access, reads the projection and reads
   the committed stream position (the snapshot cursor);
4. the snapshot is emitted at seq 0;
5. ``deliver`` replays committed outbox rows after the cursor;
6. the subscription is live; later commits arrive through ``notify``.

Delivery never sends an outbox event. It re-reads the canonical rows the
events name, projects them for the subscription's audience, and compares the
result with the version tokens this subscription has already been sent.
Unchanged tokens produce nothing, so a change confined to fields the audience
cannot see emits no frame and consumes no sequence number (section 4), and a
commit seen both by the snapshot and by replay is delivered once.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_

from core.persistence.database import Database
from vtt import fog
from vtt import live
from vtt import terrain as terrain_rules
from vtt.projection import serializers as S
from vtt.protocol import ContractRegistry, ProtocolValidationError
from vtt.storage.models import VttEntity, VttScene, VttTablePointer
from vtt.storage.stream import committed_position, outbox_after, snapshot_transaction

# More committed rows than this behind one subscription means the subscription
# is too far behind to replay safely: it is told to resync (buffer-overflow).
REPLAY_LIMIT = 1024

Emit = Callable[[str, str, dict[str, Any]], None]          # (sid, event, payload)


@dataclass
class Principal:
    kind: str                      # "member" | "table"
    id: str                        # user id, or table grant id
    campaign_id: str | None = None  # a table's grant fixes its campaign
    # Tables only (M3): when storage last confirmed the grant, and when it expires.
    # The heartbeat uses them to fail closed when storage cannot be checked.
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    # Members only (M4a): the app ended this member's session. Any command still
    # arriving on the connection answers authentication-required.
    session_ended: bool = False


@dataclass
class Subscription:
    sid: str
    subscription_id: str
    audience: str                  # "gm" | "table"
    principal: Principal
    campaign_id: str
    scene_id: str | None
    cursor: int = 0
    seq: int = 0
    live: bool = False
    sent: dict[tuple[str, str], str] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class AccessLost(Exception):
    """The access recheck failed (membership removed, grant revoked or expired)."""


# A recovery that itself needs recovering this many times is ended instead:
# bad canonical data must not make the server loop.
MAX_RECOVERY_DEPTH = 2


class Hub:
    """``access_check(session, principal, audience, campaign_id) -> bool`` is the
    host's recheck. It runs inside the snapshot transaction and again before
    every live delivery. ``disconnect(sid)`` closes a connection the hub has
    ended (the transport supplies it)."""

    def __init__(self, db: Database, registry: ContractRegistry, key: S.TokenKey, emit: Emit,
                 access_check: Callable[..., bool], disconnect: Callable[[str], None] = lambda sid: None):
        self.db = db
        self.registry = registry
        self.key = key
        self.emit = emit
        self.access_check = access_check
        self.disconnect = disconnect
        self._by_sid: dict[str, Subscription] = {}
        self._lock = threading.Lock()
        self.stats = {"snapshots": 0, "patches": 0, "suppressed": 0, "resyncs": 0, "invalid": 0,
                      "delivery_errors": 0, "ended": 0, "completions": 0}

    # --- subscribe -------------------------------------------------------------------------

    def subscribe(self, sid: str, principal: Principal, audience: str, campaign_id: str,
                  scene_id: str | None) -> Subscription:
        """Run the section 5 subscribe. Raises AccessLost (the transport answers with a
        bootstrap rejection); emits the snapshot, then replays, then goes live."""
        sub = self._new_subscription(sid, principal, audience, campaign_id, scene_id)
        with sub.lock:
            self._register(sub)
            try:
                self._snapshot_locked(sub)
            except Exception:
                sub.live = False
                self._drop(sid, sub)
                raise
            self._deliver_locked(sub)
        return sub

    def unsubscribe(self, sid: str) -> None:
        with self._lock:
            sub = self._by_sid.pop(sid, None)
        if sub is not None:
            sub.live = False

    def subscription(self, sid: str) -> Subscription | None:
        return self._by_sid.get(sid)

    def end(self, sid: str) -> bool:
        """Access ended for this connection (heartbeat or revocation sweep): the
        terminal ``revoked`` control, then close. False when it has no
        subscription (the caller closes the bare connection)."""
        sub = self._by_sid.get(sid)
        if sub is None:
            return False
        with sub.lock:
            if self._by_sid.get(sid) is not sub:
                return False
            self._end(sub)
        return True

    def _new_subscription(self, sid, principal, audience, campaign_id, scene_id) -> Subscription:
        return Subscription(sid=sid, subscription_id="sub_" + secrets.token_urlsafe(18), audience=audience,
                            principal=principal, campaign_id=campaign_id, scene_id=scene_id)

    def _register(self, sub: Subscription) -> None:
        with self._lock:
            old = self._by_sid.get(sub.sid)
            self._by_sid[sub.sid] = sub     # one active projection subscription per connection
        if old is not None and old is not sub:
            old.live = False

    def _snapshot_locked(self, sub: Subscription) -> None:
        """Section 5 steps 3-4 for a registered subscription: one consistent read
        (recheck access, project, read the cursor), validate, emit. The
        subscription's cursor, sent record and live flag change only after the
        snapshot has been emitted. Raises AccessLost after dropping it."""
        with snapshot_transaction(self.db) as session:
            if not self.access_check(session, sub.principal, sub.audience, sub.campaign_id):
                self._drop(sub.sid, sub)
                raise AccessLost()
            if sub.audience == "table":
                pointer = session.get(VttTablePointer, sub.campaign_id)
                sub.scene_id = pointer.scene_id if pointer else None
            scene = self._scene(session, sub.campaign_id, sub.scene_id)
            if sub.audience == "gm" and sub.scene_id is not None and scene is None:
                self._drop(sub.sid, sub)
                raise AccessLost()
            rows = self._rows(session, sub.campaign_id, sub.scene_id) if scene is not None else []
            fog_view = fog.load(session, scene)[1] if scene is not None else None
            terrain_view = terrain_rules.load(session, scene)[1] if scene is not None else None
            actors = live.load(session, sub.campaign_id) if scene is not None else {}
            entities = (S.gm_entities(self.key, scene, rows, fog_view, terrain_view, actors) if sub.audience == "gm"
                        else S.table_entities(self.key, scene, rows, fog_view, terrain_view, actors))
            if sub.audience == "table" and not entities:
                sub.scene_id = None     # nothing published: the table sees no scene
            cursor = committed_position(session, sub.campaign_id)
        frame = S.snapshot_frame(sub.audience, sub.subscription_id, sub.campaign_id, sub.scene_id,
                                 self._principal_payload(sub.principal, sub.audience), entities)
        self.registry.validate(sub.audience, frame)
        self.emit(sub.sid, "vtt.snapshot", frame)
        sub.cursor = cursor
        sub.sent = {(e["kind"], e["id"]): e["version"] for e in entities}
        sub.seq = 0
        sub.live = True
        self.stats["snapshots"] += 1

    # --- delivery ----------------------------------------------------------------------------

    def notify(self, campaign_id: str) -> None:
        """Called after a commit for ``campaign_id``: deliver to its live subscriptions."""
        with self._lock:
            subs = [s for s in self._by_sid.values() if s.campaign_id == campaign_id]
        for sub in subs:
            with sub.lock:
                self._deliver_locked(sub)

    def _deliver_locked(self, sub: Subscription, depth: int = 0) -> None:
        """Deliver what committed after ``sub.cursor``. Any failure while projecting,
        validating, serializing or emitting leaves cursor, seq and sent record
        untouched and recovers the subscription with a fresh snapshot; it never
        leaves a live subscription whose record has passed what the client holds."""
        if not sub.live or self._by_sid.get(sub.sid) is not sub:
            return
        try:
            self._deliver_once(sub, depth)
        except Exception as error:
            if isinstance(error, ProtocolValidationError):
                self.stats["invalid"] += 1
            self.stats["delivery_errors"] += 1
            self._recover(sub, "state-lost", depth)

    def _deliver_once(self, sub: Subscription, depth: int) -> None:
        rows = outbox_after(self.db, sub.campaign_id, sub.cursor, REPLAY_LIMIT + 1)
        if not rows:
            return
        # Access is rechecked before anything else can leave (ADR-0002 sections
        # 2 and 10): a removed GM or an expired/revoked table gets nothing more.
        with self.db.session() as session:
            allowed = self.access_check(session, sub.principal, sub.audience, sub.campaign_id)
        if not allowed:
            self._end(sub)
            return
        if len(rows) > REPLAY_LIMIT:
            self._recover(sub, "buffer-overflow", depth)
            return
        if sub.audience == "table" and any(r.event.get("kind") == "table-pointer" for r in rows):
            self._recover(sub, "scene-changed", depth)
            return
        # Section 7: exactly one frame carries a command's originCommandId, to the
        # subscription that issued it. Split the batch after each row this
        # subscription originated, so that row's completion frame is its own
        # patch (empty when nothing visible here changed). Rows from other
        # subscriptions, or from a subscription this one replaced, are ordinary.
        start = 0
        for index, row in enumerate(rows):
            origin = row.event.get("origin")
            if origin and origin.get("subscriptionId") == sub.subscription_id:
                if not self._deliver_segment(sub, rows[start:index + 1], origin["commandId"], depth):
                    return
                start = index + 1
        if start < len(rows):
            self._deliver_segment(sub, rows[start:], None, depth)

    def _deliver_segment(self, sub: Subscription, rows, origin_command_id: str | None, depth: int) -> bool:
        """Deliver one run of committed rows as at most one patch. Returns False
        when the subscription was replaced (resync) and delivery must stop."""
        last = rows[-1].position
        keys: set[tuple[str, str]] = set()
        actor_ids: set[str] = set()
        scene_changed = False
        for r in rows:
            ev = r.event
            if ev.get("kind") == "actors":
                # Campaign-wide (parity-3): no scene of its own.
                if sub.scene_id is not None:
                    actor_ids.update(ev.get("changed", []))
                continue
            if ev.get("sceneId") != sub.scene_id:
                continue
            kind = ev.get("kind")
            if kind == "scene" or (kind == "table-pointer" and ev.get("scene")):
                scene_changed = True
            elif kind == "entities":
                keys.update((k, i) for k, i in ev.get("changed", []))
        if not keys and not actor_ids and not scene_changed and origin_command_id is None:
            sub.cursor = last           # nothing this subscription can see changed
            return True
        if keys or actor_ids or scene_changed:
            diff = self._diff(sub, keys, scene_changed, actor_ids)
            if diff is None:
                self._recover(sub, "scene-changed", depth)
                return False
            upserted, removed, sent_after = diff
        else:
            upserted, removed, sent_after = [], [], sub.sent
        if not upserted and not removed and origin_command_id is None:
            sub.cursor = last           # invisible change: no frame, no sequence number
            self.stats["suppressed"] += 1
            return True
        frame = S.patch_frame(sub.subscription_id, sub.seq + 1, upserted, removed, origin_command_id)
        self.registry.validate(sub.audience, frame)
        self.emit(sub.sid, "vtt.patch", frame)
        # Only now has the client been sent the change.
        sub.cursor = last
        sub.seq += 1
        sub.sent = sent_after
        self.stats["patches"] += 1
        if origin_command_id is not None:
            self.stats["completions"] += 1
        return True

    # --- recovery and ending ---------------------------------------------------------------

    def _recover(self, old: Subscription, reason: str, depth: int = 0) -> None:
        """Recoverable resync (ADR-0002 section 3), completed by the server: retire
        the old subscription, tell the client which one ended, then subscribe
        again on the same connection (fresh ID, access recheck, snapshot at seq 0,
        replay). The accepted client waits for exactly this replacement."""
        old.live = False
        self.stats["resyncs"] += 1
        if depth >= MAX_RECOVERY_DEPTH:
            self._terminate(old)
            return
        message = {"schemaVersion": 1, "subscriptionId": old.subscription_id, "reason": reason}
        try:
            self.registry.validate("resync-required", message)
            self.emit(old.sid, "vtt.resync_required", message)
        except Exception:
            self._terminate(old)
            return
        scene_id = old.scene_id if old.audience == "gm" else None
        new = self._new_subscription(old.sid, old.principal, old.audience, old.campaign_id, scene_id)
        with new.lock:
            with self._lock:
                if self._by_sid.get(old.sid) is not old:
                    return          # the connection subscribed again or went away meanwhile
                self._by_sid[old.sid] = new
            try:
                self._snapshot_locked(new)
            except AccessLost:
                self._end(new)
                return
            except Exception:
                self.stats["delivery_errors"] += 1
                self._terminate(new)
                return
            self._deliver_locked(new, depth + 1)

    def _end(self, sub: Subscription) -> None:
        """Access ended on an established connection: the terminal ``revoked``
        control (no detail), then close. No replacement is sent."""
        sub.live = False
        self._drop(sub.sid, sub)
        self.stats["ended"] += 1
        message = {"schemaVersion": 1, "subscriptionId": sub.subscription_id, "reason": "revoked"}
        try:
            self.emit(sub.sid, "vtt.resync_required", message)
        finally:
            self.disconnect(sub.sid)

    def _terminate(self, sub: Subscription) -> None:
        """Recovery itself failed: drop the subscription and close the connection.
        The client sees a lost transport and reconnects through a new subscribe."""
        sub.live = False
        self._drop(sub.sid, sub)
        self.disconnect(sub.sid)

    def _diff(self, sub: Subscription, keys: set[tuple[str, str]], scene_changed: bool,
              actor_ids: set[str] = frozenset()):
        """Project the named entities now and compare with what this subscription holds.
        Returns (upserted, removed, sent_after) without touching the subscription,
        or None when the subscription's scene itself went away. ``actor_ids``
        (parity-3) name tracker actors that changed: the GM rechecks those
        actors, the table the tokens on its scene that link to them."""
        upserted: list[dict[str, Any]] = []
        removed: list[dict[str, str]] = []
        sent_after = dict(sub.sent)
        with self.db.session() as session:
            scene = self._scene(session, sub.campaign_id, sub.scene_id)
            if scene is None or (sub.audience == "table" and not scene.published):
                return None
            current: dict[tuple[str, str], dict[str, Any]] = {}
            if scene_changed:
                # A scene change can move the grid, which moves the fog and what
                # it covers: recheck the fog entity and, for the table, every
                # token and light (scene changes are rare; this is bounded by
                # the entity caps).
                keys = keys | {("fog", "fog"), ("terrain", "terrain")}
                if sub.audience == "table":
                    keys = keys | {(k, i) for k, i in session.execute(
                        select(VttEntity.kind, VttEntity.id).where(
                            VttEntity.campaign_id == sub.campaign_id, VttEntity.scene_id == sub.scene_id,
                            VttEntity.kind.in_(("token", "light", "template")))).all()}
            actors = live.load(session, sub.campaign_id)
            if actor_ids:
                if sub.audience == "gm":
                    keys = keys | {("actor", aid) for aid in actor_ids}
                    for aid in actor_ids:
                        if aid in actors:
                            current[("actor", aid)] = S.gm_actor(self.key, sub.campaign_id, aid, actors[aid])
                else:
                    keys = keys | {("token", row.id) for row in session.execute(
                        select(VttEntity).where(
                            VttEntity.campaign_id == sub.campaign_id, VttEntity.scene_id == sub.scene_id,
                            VttEntity.kind == "token")).scalars()
                        if (row.data or {}).get("actorId") in actor_ids}
            # Fog (parity-1) is read once: the table's tokens and lights are
            # filtered by it, and the fog entity itself may be among the keys.
            fog_view = fog.load(session, scene)[1] if (sub.audience == "table" or ("fog", "fog") in keys) else None
            if ("fog", "fog") in keys and fog_view is not None:
                current[("fog", "fog")] = S.fog_entity(self.key, sub.campaign_id, sub.audience, fog_view)
            if ("terrain", "terrain") in keys:
                terrain_view = terrain_rules.load(session, scene)[1]
                if terrain_view is not None:
                    if sub.audience == "table":
                        terrain_view = terrain_view.masked(fog_view)
                    current[("terrain", "terrain")] = S.terrain_entity(
                        self.key, sub.campaign_id, sub.audience, terrain_view)
            if scene_changed:
                projected = S.gm_scene(self.key, scene) if sub.audience == "gm" else S.table_scene(self.key, scene)
                current[("scene", scene.id)] = projected
                keys = keys | {("scene", scene.id)}
            entity_keys = [k for k in keys if k[0] not in ("scene", "fog", "terrain", "actor")]
            if entity_keys:
                rows = session.execute(
                    select(VttEntity).where(
                        VttEntity.campaign_id == sub.campaign_id, VttEntity.scene_id == sub.scene_id,
                        tuple_(VttEntity.kind, VttEntity.id).in_(entity_keys),
                    )
                ).scalars().all()
                for row in rows:
                    projected = (S.gm_entity(self.key, row) if sub.audience == "gm"
                                 else S.table_entity(self.key, row, fog_view, actors))
                    if projected is not None:
                        current[(row.kind, row.id)] = projected
        for key in sorted(keys):
            now = current.get(key)
            before = sent_after.get(key)
            if now is None:
                if before is not None:
                    removed.append({"kind": key[0], "id": key[1]})
                    del sent_after[key]
            elif now["version"] != before:
                upserted.append(now)
                sent_after[key] = now["version"]
        return upserted, removed, sent_after

    # --- helpers ------------------------------------------------------------------------------

    def _drop(self, sid: str, sub: Subscription) -> None:
        with self._lock:
            if self._by_sid.get(sid) is sub:
                del self._by_sid[sid]

    @staticmethod
    def _scene(session, campaign_id: str, scene_id: str | None):
        if scene_id is None:
            return None
        return session.get(VttScene, (campaign_id, scene_id))

    @staticmethod
    def _rows(session, campaign_id: str, scene_id: str):
        return session.execute(
            select(VttEntity)
            .where(VttEntity.campaign_id == campaign_id, VttEntity.scene_id == scene_id)
            .order_by(VttEntity.kind, VttEntity.id)
        ).scalars().all()

    @staticmethod
    def _principal_payload(principal: Principal, audience: str) -> dict[str, Any]:
        if audience == "table":
            return {"kind": "table"}
        return {"kind": "member", "id": principal.id, "campaignRole": "gm"}
