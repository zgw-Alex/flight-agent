"""Structured evidence references for generated artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights import ItineraryId, OfferId
from flight_agent.domain.requirements import ConstraintId, PreferenceId, RequirementId
from flight_agent.domain.shared import DomainId, DomainInvariantViolation
from flight_agent.domain.workflow.identity import RecommendationResultId


class EvidenceSource(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    CONSTRAINT = "CONSTRAINT"
    PREFERENCE = "PREFERENCE"
    ITINERARY = "ITINERARY"
    OFFER = "OFFER"
    RECOMMENDATION = "RECOMMENDATION"


EvidenceIdentity = (
    RequirementId
    | ConstraintId
    | PreferenceId
    | ItineraryId
    | OfferId
    | RecommendationResultId
)


@dataclass(frozen=True)
class EvidenceRef:
    source: EvidenceSource
    identity: EvidenceIdentity
    note: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.identity, DomainId):
            _validate_source_identity(self.source, self.identity)
        else:
            raise DomainInvariantViolation("EvidenceRef requires a typed domain identity")
        if self.note is not None and self.note.strip() == "":
            raise DomainInvariantViolation("EvidenceRef note must be non-empty when provided")


def _validate_source_identity(source: EvidenceSource, identity: EvidenceIdentity) -> None:
    expected = _EXPECTED_IDENTITY[source]
    if not isinstance(identity, expected):
        raise DomainInvariantViolation("EvidenceRef source does not match identity type")


_EXPECTED_IDENTITY = {
    EvidenceSource.REQUIREMENT: RequirementId,
    EvidenceSource.CONSTRAINT: ConstraintId,
    EvidenceSource.PREFERENCE: PreferenceId,
    EvidenceSource.ITINERARY: ItineraryId,
    EvidenceSource.OFFER: OfferId,
    EvidenceSource.RECOMMENDATION: RecommendationResultId,
}
