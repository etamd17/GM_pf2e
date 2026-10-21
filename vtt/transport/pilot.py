"""The pilot gate (P-3, production-plan.md G5): which campaigns may use the VTT.

``VTT_ENABLED`` makes the VTT available to every GM; the beta needs rollout
control, so ``VTT_PILOT_CAMPAIGNS`` lists the campaigns allowed in:

- a comma-separated list of campaign ids;
- ``*`` for every campaign (development, compose, staging and the e2e suite;
  never production during the pilot);
- unset or empty: no campaign. Safe by default, so a production deploy that
  turns the VTT on before the list is set exposes nothing.

The list is read once at startup, like ``VTT_ENABLED``: a change needs a
restart, and on restart every connection is rechecked against it.

One gate, applied where every VTT authorization already passes: the host's
``is_campaign_gm``, used by the GM page, every ``/vtt/api`` route, the
asset routes, pairing, subscriptions, commands and transients. A table's
grant is checked separately (it is not a member), in ``allows`` calls at
connect, at every subscription and delivery, and on the heartbeat.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

ENV = "VTT_PILOT_CAMPAIGNS"
NOT_IN_PILOT = "not-in-pilot"


class Pilot:
    def __init__(self, campaigns: frozenset[str] = frozenset(), everyone: bool = False):
        self.campaigns = campaigns
        self.everyone = everyone

    def allows(self, campaign_id: Any) -> bool:
        if not isinstance(campaign_id, str) or not campaign_id:
            return False
        return self.everyone or campaign_id in self.campaigns

    def describe(self) -> str:
        """For the startup log: never the ids, only how many."""
        if self.everyone:
            return "every campaign (*)"
        return f"{len(self.campaigns)} campaign(s)" if self.campaigns else "no campaigns (unset)"


EVERYONE = Pilot(everyone=True)


def from_env(environ: Mapping[str, str] | None = None) -> Pilot:
    raw = (os.environ if environ is None else environ).get(ENV, "")
    items = {item.strip() for item in raw.split(",") if item.strip()}
    if "*" in items:
        return EVERYONE
    return Pilot(frozenset(items))


class PilotHost:
    """The app host with the pilot applied to its GM check. Everything else is
    the wrapped host's own."""

    def __init__(self, host, pilot: Pilot):
        self._host = host
        self.pilot = pilot

    def __getattr__(self, name):
        return getattr(self._host, name)

    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool:
        return self.pilot.allows(campaign_id) and self._host.is_campaign_gm(session, user_id, campaign_id)

    def outside_pilot(self, session, user_id: str | None, campaign_id: Any) -> bool:
        """True only for a GM of this campaign whom the pilot alone keeps out,
        so the refusal can say so. Anyone else learns nothing about the list."""
        return (bool(user_id) and isinstance(campaign_id, str) and not self.pilot.allows(campaign_id)
                and self._host.is_campaign_gm(session, user_id, campaign_id))


def refusal(host, session, user_id, campaign_id) -> str:
    """The refusal code for a member denied this campaign: ``not-in-pilot``
    for its GM when only the list keeps them out, else ``unauthorized``."""
    check = getattr(host, "outside_pilot", None)
    return NOT_IN_PILOT if check is not None and check(session, user_id, campaign_id) else "unauthorized"


def gate(host, pilot: Pilot | None):
    """``host`` with the pilot applied; ``None`` (tests that predate P-3) applies none."""
    return host if pilot is None else PilotHost(host, pilot)
