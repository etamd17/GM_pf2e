"""After-commit notification for committed commands (contract C:448-454; PE-14b).

The command executor calls :func:`after_commit` once, only after ``COMMIT``
returned, with the campaign and the last seq it wrote.  Nothing subscribes
yet: PE-31b attaches the SSE hub, which treats the call as a wake hint and
still polls ``seq > last_delivered``, so a lost call delays delivery and never
drops it.  This module never calls ``sse_broadcast`` and takes no lock.
"""

from __future__ import annotations

import logging
from typing import Callable

_LOG = logging.getLogger(__name__)
_SUBSCRIBERS: list[Callable[[str, int], None]] = []


def subscribe(callback: Callable[[str, int], None]) -> None:
    _SUBSCRIBERS.append(callback)


def unsubscribe(callback: Callable[[str, int], None]) -> None:
    _SUBSCRIBERS.remove(callback)


def after_commit(campaign_id: str, last_seq: int) -> None:
    """Wake every subscriber; a failing subscriber is logged, never raised."""

    for callback in list(_SUBSCRIBERS):
        try:
            callback(campaign_id, last_seq)
        except Exception:  # the command has committed; its answer must not change
            _LOG.exception("after_commit subscriber failed for campaign %s", campaign_id)
