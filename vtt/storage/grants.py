"""Table grants, pairing challenges and revocation (VTT-ADR-0002 section 10, M3).

One rule decides whether a table grant still authorizes anything, and the
connect handshake, every snapshot, every live delivery and the heartbeat all
use it (``grant_status``):

- revoked (``revoked_at`` set), or issued under an older campaign table epoch
  ("revoke every table"): ``revoked``;
- past ``expires_at``: ``expired``;
- otherwise ``ok``.

Pairing codes are eight Crockford base32 characters from a CSPRNG, single use,
valid five minutes, and stored only as a keyed HMAC of the normalized form.
Redemption consumes a code with one conditional UPDATE, so under concurrency
exactly one redeemer wins.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from .models import VttCampaignStream, VttPairingChallenge, VttTableGrant
from .stream import advance, lock_stream

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 8
CHALLENGE_TTL = timedelta(minutes=5)
# The alpha grant lifetime section 10 requires to be fixed before pairing ships:
# one session at the table. Bounded; a new session pairs again.
GRANT_LIFETIME = timedelta(hours=12)
_ALIASES = str.maketrans({"I": "1", "L": "1", "O": "0"})
_CODE_RE = re.compile(f"[{CROCKFORD}]{{{CODE_LENGTH}}}")


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def new_code() -> str:
    return "".join(secrets.choice(CROCKFORD) for _ in range(CODE_LENGTH))


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def normalize_code(raw) -> str | None:
    """Uppercase, drop spaces and hyphens, map Crockford's I/L/O aliases. None if
    the result is not exactly eight Crockford characters."""
    if not isinstance(raw, str) or len(raw) > 32:
        return None
    code = re.sub(r"[\s-]", "", raw).upper().translate(_ALIASES)
    return code if _CODE_RE.fullmatch(code) else None


def code_mac(key: bytes, code: str) -> str:
    return hmac.new(key, b"vtt-pairing-v1\0" + code.encode("ascii"), hashlib.sha256).hexdigest()


def table_epoch(session: Session, campaign_id: str) -> int:
    value = session.execute(
        select(VttCampaignStream.table_epoch).where(VttCampaignStream.campaign_id == campaign_id)
    ).scalar_one_or_none()
    return int(value or 0)


def grant_status(session: Session, grant: VttTableGrant | None, now: datetime) -> str:
    if grant is None or grant.revoked_at is not None:
        return "revoked"
    if grant.epoch != table_epoch(session, grant.campaign_id):
        return "revoked"
    if utc(grant.expires_at) <= now:
        return "expired"
    return "ok"


# --- pairing ---------------------------------------------------------------------------------

def issue_challenge(session: Session, key: bytes, campaign_id: str, created_by: str, now: datetime) -> str:
    """Create a challenge and return its code (shown once to the GM). The
    campaign's unconsumed challenges are retired first (one live code at a time),
    and its expired ones are pruned, consumed or not."""
    session.execute(delete(VttPairingChallenge).where(
        VttPairingChallenge.campaign_id == campaign_id,
        or_(VttPairingChallenge.consumed_at.is_(None), VttPairingChallenge.expires_at <= now)))
    code = new_code()
    session.add(VttPairingChallenge(campaign_id=campaign_id, code_mac=code_mac(key, code), created_by=created_by,
                                    expires_at=now + CHALLENGE_TTL))
    session.flush()
    return code


def challenge_campaign(session: Session, key: bytes, code: str, now: datetime) -> str | None:
    """The campaign a live challenge belongs to, without consuming it."""
    return session.execute(
        select(VttPairingChallenge.campaign_id).where(
            VttPairingChallenge.code_mac == code_mac(key, code), VttPairingChallenge.consumed_at.is_(None),
            VttPairingChallenge.expires_at > now)
    ).scalar_one_or_none()


def consume_challenge(session: Session, key: bytes, code: str, now: datetime) -> str | None:
    """Atomically consume a live challenge. Returns its campaign, or None when it
    is unknown, already used or expired (indistinguishable to the caller)."""
    return session.execute(
        update(VttPairingChallenge)
        .where(VttPairingChallenge.code_mac == code_mac(key, code), VttPairingChallenge.consumed_at.is_(None),
               VttPairingChallenge.expires_at > now)
        .values(consumed_at=now)
        .returning(VttPairingChallenge.campaign_id)
    ).scalar_one_or_none()


def create_grant(session: Session, verifier: str, campaign_id: str, now: datetime) -> VttTableGrant:
    # Taking the stream lock orders this against a concurrent "revoke every
    # table": a grant is never minted under an epoch that is being retired.
    stream = lock_stream(session, campaign_id)
    grant = VttTableGrant(id="grant_" + secrets.token_hex(12), campaign_id=campaign_id, verifier=verifier,
                          epoch=stream.table_epoch, expires_at=now + GRANT_LIFETIME)
    session.add(grant)
    session.flush()
    return grant


# --- revocation ------------------------------------------------------------------------------

def revoke_grant(session: Session, campaign_id: str, grant_id: str, now: datetime) -> bool:
    """Revoke one grant and record a ``table-access`` outbox row in the same
    transaction. False when no such grant exists in this campaign."""
    lock_stream(session, campaign_id)
    grant = session.get(VttTableGrant, grant_id, with_for_update=True)
    if grant is None or grant.campaign_id != campaign_id:
        return False
    if grant.revoked_at is None:
        grant.revoked_at = now
    advance(session, campaign_id, {"kind": "table-access"})
    return True


def revoke_all(session: Session, campaign_id: str) -> int:
    """Bump the campaign table epoch (every existing grant stops working) with
    its outbox row. Returns the new epoch."""
    stream = lock_stream(session, campaign_id)
    stream.table_epoch += 1
    advance(session, campaign_id, {"kind": "table-access"})
    return stream.table_epoch


def active_grants(session: Session, campaign_id: str, now: datetime) -> list[VttTableGrant]:
    epoch = table_epoch(session, campaign_id)
    rows = session.execute(
        select(VttTableGrant).where(VttTableGrant.campaign_id == campaign_id, VttTableGrant.revoked_at.is_(None),
                                    VttTableGrant.epoch == epoch)
        .order_by(VttTableGrant.created_at)
    ).scalars().all()
    return [g for g in rows if utc(g.expires_at) > now]
