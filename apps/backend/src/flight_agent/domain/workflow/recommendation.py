"""Recommendation result artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId
from flight_agent.domain.requirements import PreferenceId, RequirementId
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
class RecommendationRoleAssignment:
    role: RecommendationRole
    preference_id: PreferenceId | None
    evidence: tuple[EvidenceRef, ...]

    def __init__(
        self,
        role: RecommendationRole,
        preference_id: PreferenceId | None = None,
        evidence: tuple[EvidenceRef, ...] = (),
    ) -> None:
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "preference_id", preference_id)
        object.__setattr__(self, "evidence", tuple(evidence))


@dataclass(frozen=True)
class CandidateComparison:
    left_offer_id: OfferId
    right_offer_id: OfferId
    price_difference: str | None = None
    stop_count_difference: int | None = None
    source_rank_relation: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, init=False)
class RecommendationItem:
    itinerary_id: ItineraryId
    primary_offer_id: OfferId
    roles: tuple[RecommendationRole, ...]
    evidence: tuple[EvidenceRef, ...]
    source_rank: int | None
    selection_order: int | None
    role_assignments: tuple[RecommendationRoleAssignment, ...]
    trade_off_evidence: tuple[str, ...]

    def __init__(
        self,
        itinerary_id: ItineraryId,
        primary_offer_id: OfferId,
        roles: tuple[RecommendationRole, ...],
        evidence: tuple[EvidenceRef, ...] = (),
        source_rank: int | None = None,
        selection_order: int | None = None,
        role_assignments: tuple[RecommendationRoleAssignment, ...] = (),
        trade_off_evidence: tuple[str, ...] = (),
    ) -> None:
        roles_tuple = tuple(roles)
        evidence_tuple = tuple(evidence)
        role_assignments_tuple = tuple(role_assignments)
        if len(roles_tuple) == 0:
            raise DomainInvariantViolation("RecommendationItem requires at least one role")
        if len(frozenset(roles_tuple)) != len(roles_tuple):
            raise DomainInvariantViolation("RecommendationItem roles must be unique")
        if source_rank is not None and source_rank < 1:
            raise DomainInvariantViolation("RecommendationItem source_rank must be positive")
        if selection_order is not None and selection_order < 1:
            raise DomainInvariantViolation("RecommendationItem selection_order must be positive")
        if role_assignments_tuple and {
            assignment.role for assignment in role_assignments_tuple
        } != frozenset(roles_tuple):
            raise DomainInvariantViolation("RecommendationItem role assignments must match roles")
        object.__setattr__(self, "itinerary_id", itinerary_id)
        object.__setattr__(self, "primary_offer_id", primary_offer_id)
        object.__setattr__(self, "roles", roles_tuple)
        object.__setattr__(self, "evidence", evidence_tuple)
        object.__setattr__(self, "source_rank", source_rank)
        object.__setattr__(self, "selection_order", selection_order)
        object.__setattr__(self, "role_assignments", role_assignments_tuple)
        object.__setattr__(self, "trade_off_evidence", tuple(sorted(trade_off_evidence)))


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
    requirement_id: RequirementId | None
    recommendation_run_id: str | None
    filter_result_id: str | None
    ranking_result_id: str | None
    derived_feature_set_id: str | None
    recommendation_policy_version: str | None
    candidate_comparisons: tuple[CandidateComparison, ...]
    target_count: int | None
    max_count: int | None

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
        requirement_id: RequirementId | None = None,
        recommendation_run_id: str | None = None,
        filter_result_id: str | None = None,
        ranking_result_id: str | None = None,
        derived_feature_set_id: str | None = None,
        recommendation_policy_version: str | None = None,
        candidate_comparisons: tuple[CandidateComparison, ...] = (),
        target_count: int | None = None,
        max_count: int | None = None,
    ) -> None:
        items_tuple = tuple(items)
        comparisons_tuple = tuple(candidate_comparisons)
        if status is RecommendationResultStatus.NO_MATCH and len(items_tuple) != 0:
            raise DomainInvariantViolation("NO_MATCH RecommendationResult must not contain items")
        if status is not RecommendationResultStatus.NO_MATCH and len(items_tuple) == 0:
            raise DomainInvariantViolation("Matched RecommendationResult requires at least one item")
        identities = tuple((item.primary_offer_id, item.itinerary_id) for item in items_tuple)
        if len(frozenset(identities)) != len(identities):
            raise DomainInvariantViolation("RecommendationResult items must have unique candidates")
        if max_count is not None and len(items_tuple) > max_count:
            raise DomainInvariantViolation("RecommendationResult selected count must not exceed max_count")
        if target_count is not None and target_count < 1:
            raise DomainInvariantViolation("RecommendationResult target_count must be positive")
        if max_count is not None and max_count < 1:
            raise DomainInvariantViolation("RecommendationResult max_count must be positive")
        object.__setattr__(self, "recommendation_result_id", recommendation_result_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "based_on_requirement_version", based_on_requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "items", items_tuple)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "recommendation_run_id", recommendation_run_id)
        object.__setattr__(self, "filter_result_id", filter_result_id)
        object.__setattr__(self, "ranking_result_id", ranking_result_id)
        object.__setattr__(self, "derived_feature_set_id", derived_feature_set_id)
        object.__setattr__(self, "recommendation_policy_version", recommendation_policy_version)
        object.__setattr__(self, "candidate_comparisons", comparisons_tuple)
        object.__setattr__(self, "target_count", target_count)
        object.__setattr__(self, "max_count", max_count)
