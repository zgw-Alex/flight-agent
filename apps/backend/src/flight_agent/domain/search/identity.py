"""Typed identities for provider-neutral search planning."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainId


@dataclass(frozen=True)
class SearchPlanId(DomainId):
    """Opaque identity for a requested search scope artifact."""
