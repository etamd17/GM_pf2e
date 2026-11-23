"""A recorder for lock order in SQL writers (PE-14d tests; not a test module).

``with record_locks() as events:`` collects, in order:

- ``"stream:<campaign_id>"`` for each ``streams.lock_stream`` call (the real
  function still runs, so re-entry and the lazy insert behave as in product);
- ``"lock_timeout"`` for each ``SET LOCAL lock_timeout`` statement (PostgreSQL);
- ``"for_update:<table>"`` for each ORM or Core ``SELECT ... FOR UPDATE``;
- ``"write:<table>"`` for each INSERT, UPDATE or DELETE, except the stream
  row's own lazy insert.

``FOR UPDATE`` is detected in one place (``_for_update_table``) through
``statement.is_select`` and ``_for_update_arg``, because SQLite renders no
``FOR UPDATE`` text.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from core.persistence import streams

_WRITE = re.compile(r"\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\"?([a-z_]+)\"?", re.I)


def _for_update_table(statement):
    """The first table of a ``SELECT ... FOR UPDATE`` statement, else None."""

    if not getattr(statement, "is_select", False):
        return None
    if getattr(statement, "_for_update_arg", None) is None:
        return None
    froms = statement.get_final_froms()
    return froms[0].name if froms else "?"


@contextmanager
def record_locks(monkeypatch=None):
    events: list[str] = []
    real = streams.lock_stream

    def lock_stream(session, campaign_id):
        events.append(f"stream:{campaign_id}")
        return real(session, campaign_id)

    def on_orm(state):
        table = _for_update_table(state.statement)
        if table is not None:
            events.append(f"for_update:{table}")

    def on_cursor(conn, cursor, statement, parameters, context, executemany):
        if "lock_timeout" in statement:
            events.append("lock_timeout")
        match = _WRITE.match(statement)
        if match and match.group(2) != "campaign_streams":
            events.append(f"write:{match.group(2)}")

    streams.lock_stream = lock_stream
    event.listen(Session, "do_orm_execute", on_orm)
    event.listen(Engine, "before_cursor_execute", on_cursor)
    try:
        yield events
    finally:
        streams.lock_stream = real
        event.remove(Session, "do_orm_execute", on_orm)
        event.remove(Engine, "before_cursor_execute", on_cursor)


def first(events, prefix):
    """Index of the first event starting with ``prefix``; AssertionError if none."""

    for index, name in enumerate(events):
        if name.startswith(prefix):
            return index
    raise AssertionError(f"no {prefix!r} event in {events}")


def assert_stream_first(events, campaign_id, *, before=("for_update:campaigns",)):
    """The stream of ``campaign_id`` precedes every ``before`` event and every write."""

    stream = first(events, f"stream:{campaign_id}")
    for index, name in enumerate(events):
        if name.startswith("write:") or name.startswith(tuple(before)):
            assert stream < index, events
    for prefix in before:
        first(events, prefix)
