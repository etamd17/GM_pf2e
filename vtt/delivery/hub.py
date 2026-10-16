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
from typing import Any

from sqlalchemy import select, tuple_

from core.persistence.database import Database
from vtt.projection import serializers as S
from vtt.protocol import ContractRegistry
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
    """The recheck inside the snapshot transaction failed."""


class Hub:
    """``access_check(session, principal, audience, campaign_id) -> bool`` is the
    host's recheck, run inside the snapshot transaction."""

    def __init__(self, db: Database, registry: ContractRegistry, key: S.TokenKey, emit: Emit,
                 access_check: Callable[..., bool]):
        self.db = db
        self.registry = registry
        self.key = key
        self.emit = emit
        self.access_check = access_check
        self._by_sid: dict[str, Subscription] = {}
        self._lock = threading.Lock()
        self.stats = {"snapshots": 0, "patches": 0, "suppressed": 0, "resyncs": 0}

    # --- subscribe -------------------------------------------------------------------------

    def subscribe(self, sid: str, principal: Principal, audience: str, campaign_id: str,
                  scene_id: str | None) -> Subscription:
        """Run the section 5 subscribe. Raises AccessLost (the transport answers with a
        bootstrap rejection); emits the snapshot, then replays, then goes live."""
        sub = Subscription(sid=sid, subscription_id="sub_" + secrets.token_urlsafe(18), audience=audience,
                           principal=principal, campaign_id=campaign_id, scene_id=scene_id)
        with self._lock:
            old = self._by_sid.get(sid)
            self._by_sid[sid] = sub     # one active projection subscription per connection
        if old is not None:
            old.live = False
        with sub.lock:
            with snapshot_transaction(self.db) as session:
                if not self.access_check(session, principal, audience, campaign_id):
                    self._drop(sid, sub)
                    raise AccessLost()
                if audience == "table":
                    pointer = session.get(VttTablePointer, campaign_id)
                    sub.scene_id = pointer.scene_id if pointer else None
                scene = self._scene(session, campaign_id, sub.scene_id)
                if audience == "gm" and sub.scene_id is not None and scene is None:
                    self._drop(sid, sub)
                    raise AccessLost()
                rows = self._rows(session, campaign_id, sub.scene_id) if scene is not None else []
                entities = (S.gm_entities(self.key, scene, rows) if audience == "gm"
                            else S.table_entities(self.key, scene, rows))
                if audience == "table" and not entities:
                    sub.scene_id = None     # nothing published: the table sees no scene
                cursor = committed_position(session, campaign_id)
            frame = S.snapshot_frame(audience, sub.subscription_id, campaign_id, sub.scene_id,
                                     self._principal_payload(principal, audience), entities)
            self.registry.validate(audience, frame)
            sub.cursor = cursor
            sub.sent = {(e["kind"], e["id"]): e["version"] for e in entities}
            sub.seq = 0
            self.emit(sid, "vtt.snapshot", frame)
            self.stats["snapshots"] += 1
            sub.live = True
            self._deliver_locked(sub)
        return sub

    def unsubscribe(self, sid: str) -> None:
        with self._lock:
            sub = self._by_sid.pop(sid, None)
        if sub is not None:
            sub.live = False

    def subscription(self, sid: str) -> Subscription | None:
        return self._by_sid.get(sid)

    # --- delivery ----------------------------------------------------------------------------

    def notify(self, campaign_id: str) -> None:
        """Called after a commit for ``campaign_id``: deliver to its live subscriptions."""
        with self._lock:
            subs = [s for s in self._by_sid.values() if s.campaign_id == campaign_id]
        for sub in subs:
            with sub.lock:
                self._deliver_locked(sub)

    def _deliver_locked(self, sub: Subscription) -> None:
        if not sub.live or self._by_sid.get(sub.sid) is not sub:
            return
        rows = outbox_after(self.db, sub.campaign_id, sub.cursor, REPLAY_LIMIT + 1)
        if not rows:
            return
        if len(rows) > REPLAY_LIMIT:
            self._resync(sub, "buffer-overflow")
            return
        last = rows[-1].position
        if sub.audience == "table" and any(r.event.get("kind") == "table-pointer" for r in rows):
            self._resync(sub, "scene-changed")
            return
        keys: set[tuple[str, str]] = set()
        scene_changed = False
        for r in rows:
            ev = r.event
            if ev.get("sceneId") != sub.scene_id:
                continue
            if ev.get("kind") == "scene":
                scene_changed = True
            elif ev.get("kind") == "entities":
                keys.update((k, i) for k, i in ev.get("changed", []))
        sub.cursor = last
        if not keys and not scene_changed:
            return
        diff = self._diff(sub, keys, scene_changed)
        if diff is None:
            self._resync(sub, "scene-changed")
            return
        upserted, removed, sent_after = diff
        if not upserted and not removed:
            self.stats["suppressed"] += 1
            return
        frame = S.patch_frame(sub.subscription_id, sub.seq + 1, upserted, removed)
        try:
            self.registry.validate(sub.audience, frame)
        except Exception:
            # A frame the contract refuses is a server bug. Never send it, and
            # never let this subscription's record drift from what the client
            # holds: start it again from a fresh snapshot.
            self.stats["invalid"] = self.stats.get("invalid", 0) + 1
            self._resync(sub, "state-lost")
            return
        self.emit(sub.sid, "vtt.patch", frame)
        sub.seq += 1
        sub.sent = sent_after
        self.stats["patches"] += 1

    def _diff(self, sub: Subscription, keys: set[tuple[str, str]], scene_changed: bool):
        """Project the named entities now and compare with what this subscription holds.
        Returns (upserted, removed, sent_after) without touching the subscription,
        or None when the subscription's scene itself went away."""
        upserted: list[dict[str, Any]] = []
        removed: list[dict[str, str]] = []
        sent_after = dict(sub.sent)
        with self.db.session() as session:
            scene = self._scene(session, sub.campaign_id, sub.scene_id)
            if scene is None or (sub.audience == "table" and not scene.published):
                return None
            current: dict[tuple[str, str], dict[str, Any]] = {}
            if scene_changed:
                projected = S.gm_scene(self.key, scene) if sub.audience == "gm" else S.table_scene(self.key, scene)
                current[("scene", scene.id)] = projected
                keys = keys | {("scene", scene.id)}
            entity_keys = [k for k in keys if k[0] != "scene"]
            if entity_keys:
                rows = session.execute(
                    select(VttEntity).where(
                        VttEntity.campaign_id == sub.campaign_id, VttEntity.scene_id == sub.scene_id,
                        tuple_(VttEntity.kind, VttEntity.id).in_(entity_keys),
                    )
                ).scalars().all()
                for row in rows:
                    projected = S.gm_entity(self.key, row) if sub.audience == "gm" else S.table_entity(self.key, row)
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

    def _resync(self, sub: Subscription, reason: str) -> None:
        message = {"schemaVersion": 1, "subscriptionId": sub.subscription_id, "reason": reason}
        self.registry.validate("resync-required", message)
        sub.live = False
        self._drop(sub.sid, sub)
        self.emit(sub.sid, "vtt.resync_required", message)
        self.stats["resyncs"] += 1

    # --- helpers ------------------------------------------------------------------------------

    def _drop(self, sid: str, sub: Subscription) -> None:
        with self._lock:
            if self._by_sid.get(sid) is sub:
                del self._by_sid[sid]

    @staticmethod
    def _scene(session, campaign_id: str, scene_id: str | None):
        if scene_id is None:
            return None
        scene = session.get(VttScene, scene_id)
        return scene if scene is not None and scene.campaign_id == campaign_id else None

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
