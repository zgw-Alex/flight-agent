"""Recommendation result artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow.evidence import EvidenceRef
from flight_agent.domain.workflow.identity import ExecutionId, RecommendationResultId


class RecommendationResultStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NO_MATCH = "NO_MATCH"


class RecommendationRole(str, Enum):
    BEST_OVERALL = "BEST_OVERALL"
    CHEAPEST = "CHEAPEST"
    EARLIEST_ARRIVAL = "EARLIEST_ARRIVAL"
    BEST_DEPARTURE_TIME = "BEST_DEPARTURE_TIME"
    BEST_AIRPORT_MATCH = "BEST_AIRPORT_MATCH"
    FALLBACK = "FALLBACK"


@dataclass(frozen=True, init=False)
class RecommendationItem:
    itinerary_id: ItineraryId
    primary_offer_id: OfferId
    roles: tuple[RecommendationRole, ...]
    evidence: tuple[EvidenceRef, ...]

    def __init__(
        self,
        itinerary_id: ItineraryId,
        primary_offer_id: OfferId,
        roles: tuple[RecommendationRole, ...],
        evidence: tuple[EvidenceRef, ...] = (),
    ) -> None:
        roles_tuple = tuple(roles)
        evidence_tuple = tuple(evidence)
        if len(roles_tuple) == 0:
            raise DomainInvariantViolation("RecommendationItem requires at least one role")
        if len(frozenset(roles_tuple)) != len(roles_tuple):
            raise DomainInvariantViolation("RecommendationItem roles must be unique")
        object.__setattr__(self, "itinerary_id", itinerary_id)
        object.__setattr__(self, "primary_offer_id", primary_offer_id)
        object.__setattr__(self, "roles", roles_tuple)
        object.__setattr__(self, "evidence", evidence_tuple)


@dataclass(frozen=True, init=False)
class RecommendationResult:
    recommendation_result_id: RecommendationResultId
    status: RecommendationResultStatus
    execution_id: ExecutionId
    based_on_requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    generated_at: DomainInstant
    items: tuple[RecommendationItem, ...]

    def __init__(
        self,
        recommendation_result_id: RecommendationResultId,
        status: RecommendationResultStatus,
        execution_id: ExecutionId,
        based_on_requirement_version: RequirementVersion,
        snapshot_id: CandidateSnapshotId,
        snapshot_version: SnapshotVersion,
        generated_at: DomainInstant,
        items: tuple[RecommendationItem, ...] = (),
    ) -> None:
        items_tuple = tuple(items)
        if status is RecommendationResultStatus.NO_MATCH and len(items_tuple) != 0:
            raise DomainInvariantViolation("NO_MATCH RecommendationResult must not contain items")
        if status is not RecommendationResultStatus.NO_MATCH and len(items_tuple) == 0:
            raise DomainInvariantViolation("Matched RecommendationResult requires at least one item")
        object.__setattr__(self, "recommendation_result_id", recommendation_result_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "based_on_requirement_version", based_on_requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "items", items_tuple)
