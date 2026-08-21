"""Typed opaque identities for flight and snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared import DomainId


@dataclass(frozen=True)
class SegmentId(DomainId):
    """Opaque identity for a flight segment."""


@dataclass(frozen=True)
class ItineraryId(DomainId):
    """Opaque identity for an ordered segment composition."""


@dataclass(frozen=True)
class OfferId(DomainId):
    """Opaque identity for commercial facts attached to an itinerary."""


@dataclass(frozen=True)
class CandidateSnapshotId(DomainId):
    """Opaque identity for a self-contained candidate snapshot."""
