"""Flight and CandidateSnapshot contract objects for M2-U3."""

from flight_agent.domain.flights.entities import FlightSegment, Itinerary, Money, Offer
from flight_agent.domain.flights.identity import (
    CandidateSnapshotId,
    ItineraryId,
    OfferId,
    SegmentId,
)
from flight_agent.domain.flights.snapshot import (
    CandidateSnapshot,
    Coverage,
    CoverageLimitation,
    CoverageStatus,
)

__all__ = [
    "CandidateSnapshot",
    "CandidateSnapshotId",
    "Coverage",
    "CoverageLimitation",
    "CoverageStatus",
    "FlightSegment",
    "Itinerary",
    "ItineraryId",
    "Money",
    "Offer",
    "OfferId",
    "SegmentId",
]
