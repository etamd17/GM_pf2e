"""The operation-status read and ``operation.abandon`` (task PE-14f; contract C:253-307).

Each is one short transaction holding the campaign's stream lock, the lock
every command commit, token revocation and ``stream_epoch`` change holds
(C:263-268, PE-14d).  In order: ``SET LOCAL lock_timeout`` (PostgreSQL) and
the stream lock (``streams.lock_stream_for_write``), the reader's
authorization re-read from SQL (``authorizer.load``, never ``flask.g``), the
receipt lookup, then, with no receipt, the original token's SQL revocation
and the stream epoch.  So a command either committed before this observation
or has not taken the lock yet.

Authorized readers: the campaign's GM (site admin included), the original
principal itself while it is still valid (a user still a campaign member, a
token not revoked), or a token recorded as the original token's replacement
(``is_recorded_replacement``).  Anyone else gets ``404 not_found`` whether or
not a receipt exists (C:277), and the transaction rolls back.

Neither runs the command fence (check 2): a reader with a stale stored epoch
still gets the receipt or ``absent_final`` (C:275).  Malformed input is
``400 bad_envelope`` before any transaction.  The queried ``kind:id`` is a
receipt key parsed here, never an authenticated principal.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.persistence import runtime, streams
from core.persistence.models import CampaignStateAuthority, CommandReceipt
from core.request_context import Principal, PrincipalKind

from .authorizer import CommandAuthorizer, held_projections, load
from .checks import CommandRefusal
from .envelope import OPERATION_ID, bad
from .event_visibility import visible_event_ids
# The executor's code-table mapping (C:352-366), so a lock timeout (55P03) or a
# raising COMMIT is ``503 temporarily_unavailable`` and anything unexpected
# ``500 server_error``.
from .executor import CommandResponse, _CommitFailed, _failure

_TOKEN = PrincipalKind.INTEGRATION_TOKEN.value
_KINDS = frozenset({PrincipalKind.USER.value, _TOKEN})
# Printable and encodable (no NUL, no lone surrogate from a JSON body), bounded by
# the receipt and stream columns.
_PRINCIPAL_ID = re.compile(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,128}")
_CAMPAIGN_ID = re.compile(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,64}")
_EPOCH = re.compile(r"-?[0-9]{1,18}")


def read_status(*, campaign_id: str, reader: Principal, original: Any, operation_id: Any,
                epoch: Any) -> CommandResponse:
    """``GET .../operations/<operation_id>?principal=<kind>:<id>&epoch=<n>`` (C:261-298).

    ``reader`` is the verified principal of the request; ``original`` and
    ``epoch`` are the raw query values.
    """

    return _run(campaign_id, reader, original, operation_id, epoch, write=False)


def abandon(*, campaign_id: str, reader: Principal, original: Any,
            operation_id: Any) -> CommandResponse:
    """``operation.abandon {principal}`` (C:299-304): answer the receipt, or write ``abandoned``."""

    return _run(campaign_id, reader, original, operation_id, None, write=True)


def _run(campaign_id: Any, reader: Principal, original: Any, operation_id: Any, epoch: Any,
         *, write: bool) -> CommandResponse:
    echo = (operation_id if isinstance(operation_id, str) and OPERATION_ID.fullmatch(operation_id)
            else None)
    finished = False
    try:
        key = _key(campaign_id, original, operation_id)
        if not write:
            epoch = _epoch(epoch)
        with runtime.database().transaction() as session:
            answer = _observe(session, campaign_id, reader, key, epoch, write)
            finished = True
        return answer
    except Exception as exc:
        # A raising COMMIT leaves the outcome unknown (an abandon's receipt may have
        # landed): 503 ``temporarily_unavailable``, never "nothing written" (C:364-365).
        return _failure(_CommitFailed() if finished else exc, echo)


def _key(campaign_id: Any, original: Any, operation_id: Any) -> tuple[str, str, str]:
    """The receipt key's principal kind, principal id and operation id (Decision 3)."""

    if not isinstance(campaign_id, str) or not _CAMPAIGN_ID.fullmatch(campaign_id):
        raise CommandRefusal("not_found")
    if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(operation_id):
        raise bad("malformed operation_id")
    kind, colon, principal_id = (original.partition(":") if isinstance(original, str)
                                 else ("", "", ""))
    if colon != ":" or kind not in _KINDS or not _PRINCIPAL_ID.fullmatch(principal_id):
        raise bad("principal must be user:<id> or integration_token:<id>")
    return kind, principal_id, operation_id


def _epoch(value: Any) -> int:
    """The stored epoch: required, an integer (C:278-279); never defaulted."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _EPOCH.fullmatch(value):
        return int(value)
    raise bad("epoch must be an integer")


def _observe(session: Any, campaign_id: str, reader: Principal, key: tuple[str, str, str],
             epoch: int | None, write: bool) -> CommandResponse:
    kind, principal_id, operation_id = key
    try:
        streams.lock_stream_for_write(session, campaign_id)
    except IntegrityError:  # no such campaign row for the stream row's foreign key
        raise CommandRefusal("not_found") from None
    authorizer = load(session, reader, campaign_id)
    if not _authorized(authorizer, reader, kind, principal_id):
        raise CommandRefusal("not_found")
    receipt = session.get(CommandReceipt, (campaign_id, kind, principal_id, operation_id))
    if receipt is not None:
        return _from_receipt(session, authorizer, campaign_id, receipt)
    if write:
        current = _stream_epoch(session, campaign_id)
        session.add(CommandReceipt(
            campaign_id=campaign_id, principal_kind=kind, principal_id=principal_id,
            operation_id=operation_id, digest=None, status="rejected", applied_status=None,
            code="abandoned", stream_epoch=1 if current is None else current))
        session.flush()
        return _answer(operation_id, "abandoned", code="abandoned", event_ids=[])
    if _final(session, authorizer, campaign_id, kind, principal_id, epoch):
        return _answer(operation_id, "absent_final", epoch=epoch)
    return _answer(operation_id, "absent_pending")


def _authorized(authorizer: CommandAuthorizer, reader: Principal, kind: str,
                principal_id: str) -> bool:
    """The GM, the original while still valid, or the original token's recorded replacement."""

    if authorizer.is_gm:
        return True
    reader_id = reader.user_id if reader.kind is PrincipalKind.USER else reader.credential_id
    # A valid principal always holds some projection; an invalid one holds none.
    if (reader.kind.value, reader_id) == (kind, principal_id) and held_projections(
            authorizer, subjects=[], encounter_id=None):
        return True
    return kind == _TOKEN and authorizer.is_recorded_replacement(principal_id)


def _stream_epoch(session: Any, campaign_id: str) -> int | None:
    t = CampaignStateAuthority.__table__
    return session.scalar(select(t.c.stream_epoch).where(t.c.campaign_id == campaign_id))


def _final(session: Any, authorizer: CommandAuthorizer, campaign_id: str, kind: str,
           principal_id: str, epoch: int) -> bool:
    """No receipt, and in this observation the original can never commit (C:275).

    A token revocation committed to SQL (``revoked_at``, never cleared) is final
    under any authority and with no authority row; a request epoch below the
    current ``stream_epoch`` is final only where an authority row exists.
    """

    if kind == _TOKEN and authorizer.token_revoked(principal_id):
        return True
    current = _stream_epoch(session, campaign_id)
    return current is not None and epoch < current


def _from_receipt(session: Any, authorizer: CommandAuthorizer, campaign_id: str,
                  receipt: CommandReceipt) -> CommandResponse:
    if receipt.status == "committed":  # ``applied_status`` committed or pending
        state = "committed"
    else:
        state = "abandoned" if receipt.code == "abandoned" else "rejected"
    return _answer(receipt.operation_id, state, code=receipt.code,
                   event_ids=visible_event_ids(session, authorizer, campaign_id,
                                               receipt.event_ids))


def _answer(operation_id: str, state: str, **fields: Any) -> CommandResponse:
    return CommandResponse(200, {"ok": True, "operation_id": operation_id, "state": state,
                                 **fields})
