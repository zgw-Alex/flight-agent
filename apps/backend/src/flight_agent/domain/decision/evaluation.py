"""M6 hard-constraint evaluation and eligibility foundation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.decision.identity import ConstraintEvaluationId, FilterRunId
from flight_agent.domain.decision.policy import DecisionPolicyVersion
from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId, SegmentId
from flight_agent.domain.requirements import ConstraintId, RequirementId
from flight_agent.domain.shared import (
    DomainInvariantViolation,
    DomainValue,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow.evidence import EvidenceRef


class ConstraintEvaluationStatus(str, Enum):
    """Three-valued hard-constraint conclusion."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class DecisionConstraintScope(str, Enum):
    """Candidate fact scope addressed by a hard-constraint evaluation."""

    OFFER = "OFFER"
    ITINERARY = "ITINERARY"
    SEGMENT = "SEGMENT"


class SegmentSelection(str, Enum):
    """Typed segment selection contract for later segment evaluators."""

    FIRST_SEGMENT = "FIRST_SEGMENT"
    LAST_SEGMENT = "LAST_SEGMENT"
    ANY_SEGMENT = "ANY_SEGMENT"
    ALL_SEGMENTS = "ALL_SEGMENTS"


class ConstraintReasonCode(str, Enum):
    """Stable machine-readable reason categories, separate from UI copy."""

    CONSTRAINT_SATISFIED = "CONSTRAINT_SATISFIED"
    CONSTRAINT_VIOLATED = "CONSTRAINT_VIOLATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MAX_PRICE_SATISFIED = "MAX_PRICE_SATISFIED"
    MAX_PRICE_EXCEEDED = "MAX_PRICE_EXCEEDED"
    MAX_PRICE_INSUFFICIENT_EVIDENCE = "MAX_PRICE_INSUFFICIENT_EVIDENCE"
    MAX_STOPS_SATISFIED = "MAX_STOPS_SATISFIED"
    MAX_STOPS_EXCEEDED = "MAX_STOPS_EXCEEDED"
    MAX_STOPS_INSUFFICIENT_EVIDENCE = "MAX_STOPS_INSUFFICIENT_EVIDENCE"


class CandidateEligibilityStatus(str, Enum):
    """Candidate-level aggregation result across applicable hard constraints."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN_ELIGIBILITY = "UNKNOWN_ELIGIBILITY"


@dataclass(frozen=True)
class OfferBackedItineraryCandidate:
    """Decision candidate identity derived from canonical business identities."""

    offer_id: OfferId
    itinerary_id: ItineraryId


@dataclass(frozen=True, init=False)
class ConstraintEvaluationScope:
    scope: DecisionConstraintScope
    segment_selection: SegmentSelection | None
    segment_id: SegmentId | None

    def __init__(
        self,
        scope: DecisionConstraintScope,
        segment_selection: SegmentSelection | None = None,
        segment_id: SegmentId | None = None,
    ) -> None:
        if scope is not DecisionConstraintScope.SEGMENT and (
            segment_selection is not None or segment_id is not None
        ):
            raise DomainInvariantViolation("Only SEGMENT scope may carry segment selection")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "segment_selection", segment_selection)
        object.__setattr__(self, "segment_id", segment_id)


@dataclass(frozen=True, init=False)
class EvaluationValueEvidence:
    label: str
    value: DomainValue[object]
    evidence: tuple[EvidenceRef, ...]

    def __init__(
        self,
        label: str,
        value: DomainValue[object],
        evidence: tuple[EvidenceRef, ...] = (),
    ) -> None:
        if label.strip() == "":
            raise DomainInvariantViolation("EvaluationValueEvidence requires a label")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "evidence", tuple(evidence))


@dataclass(frozen=True)
class ConstraintEvaluationLineage:
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    filter_policy_version: DecisionPolicyVersion
    filter_run_id: FilterRunId


@dataclass(frozen=True, init=False)
class ConstraintEvaluation:
    evaluation_id: ConstraintEvaluationId
    constraint_id: ConstraintId
    candidate: OfferBackedItineraryCandidate
    scope: ConstraintEvaluationScope
    status: ConstraintEvaluationStatus
    expected: EvaluationValueEvidence
    actual: EvaluationValueEvidence
    reason_code: ConstraintReasonCode
    evidence: tuple[EvidenceRef, ...]
    lineage: ConstraintEvaluationLineage

    def __init__(
        self,
        evaluation_id: ConstraintEvaluationId,
        constraint_id: ConstraintId,
        candidate: OfferBackedItineraryCandidate,
        scope: ConstraintEvaluationScope,
        status: ConstraintEvaluationStatus,
        expected: EvaluationValueEvidence,
        actual: EvaluationValueEvidence,
        reason_code: ConstraintReasonCode,
        evidence: tuple[EvidenceRef, ...],
        lineage: ConstraintEvaluationLineage,
    ) -> None:
        evidence_tuple = tuple(evidence)
        if len(evidence_tuple) == 0:
            raise DomainInvariantViolation("ConstraintEvaluation requires evidence references")
        _validate_reason_matches_status(status, reason_code)
        object.__setattr__(self, "evaluation_id", evaluation_id)
        object.__setattr__(self, "constraint_id", constraint_id)
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "expected", expected)
        object.__setattr__(self, "actual", actual)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "evidence", evidence_tuple)
        object.__setattr__(self, "lineage", lineage)


@dataclass(frozen=True, init=False)
class CandidateEligibility:
    candidate: OfferBackedItineraryCandidate
    status: CandidateEligibilityStatus
    evaluations: tuple[ConstraintEvaluation, ...]

    def __init__(
        self,
        candidate: OfferBackedItineraryCandidate,
        status: CandidateEligibilityStatus,
        evaluations: tuple[ConstraintEvaluation, ...] = (),
    ) -> None:
        evaluations_tuple = tuple(evaluations)
        for evaluation in evaluations_tuple:
            if evaluation.candidate != candidate:
                raise DomainInvariantViolation("CandidateEligibility evaluations must match candidate")
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evaluations", evaluations_tuple)


@dataclass(frozen=True)
class CandidatePoolPartition:
    qualified: tuple[OfferBackedItineraryCandidate, ...]
    uncertain: tuple[OfferBackedItineraryCandidate, ...]
    rejected: tuple[OfferBackedItineraryCandidate, ...]


class CandidatePoolDirection(str, Enum):
    QUALIFIED_AVAILABLE = "QUALIFIED_AVAILABLE"
    FILTER_EMPTY = "FILTER_EMPTY"
    QUALIFICATION_UNRESOLVED = "QUALIFICATION_UNRESOLVED"
    SEARCH_EMPTY_SOURCE = "SEARCH_EMPTY_SOURCE"


def aggregate_candidate_eligibility(
    candidate: OfferBackedItineraryCandidate,
    evaluations: Iterable[ConstraintEvaluation],
) -> CandidateEligibility:
    """Aggregate full hard-constraint evidence for one candidate without side effects."""

    evaluations_tuple = tuple(evaluations)
    if any(evaluation.candidate != candidate for evaluation in evaluations_tuple):
        raise DomainInvariantViolation("Cannot aggregate evaluations for a different candidate")
    statuses = tuple(evaluation.status for evaluation in evaluations_tuple)
    if any(status is ConstraintEvaluationStatus.FAIL for status in statuses):
        status = CandidateEligibilityStatus.INELIGIBLE
    elif any(status is ConstraintEvaluationStatus.UNKNOWN for status in statuses):
        status = CandidateEligibilityStatus.UNKNOWN_ELIGIBILITY
    else:
        status = CandidateEligibilityStatus.ELIGIBLE
    return CandidateEligibility(candidate, status, evaluations_tuple)


def partition_candidate_pool(
    eligibilities: Iterable[CandidateEligibility],
) -> CandidatePoolPartition:
    qualified: list[OfferBackedItineraryCandidate] = []
    uncertain: list[OfferBackedItineraryCandidate] = []
    rejected: list[OfferBackedItineraryCandidate] = []
    seen: set[OfferBackedItineraryCandidate] = set()
    for eligibility in eligibilities:
        if eligibility.candidate in seen:
            raise DomainInvariantViolation("Candidate pool partition requires unique candidates")
        seen.add(eligibility.candidate)
        if eligibility.status is CandidateEligibilityStatus.ELIGIBLE:
            qualified.append(eligibility.candidate)
        elif eligibility.status is CandidateEligibilityStatus.UNKNOWN_ELIGIBILITY:
            uncertain.append(eligibility.candidate)
        else:
            rejected.append(eligibility.candidate)
    return CandidatePoolPartition(tuple(qualified), tuple(uncertain), tuple(rejected))


def classify_candidate_pool_direction(
    *,
    candidate_count: int,
    partition: CandidatePoolPartition,
) -> CandidatePoolDirection:
    if candidate_count < 0:
        raise DomainInvariantViolation("candidate_count must not be negative")
    if candidate_count == 0:
        return CandidatePoolDirection.SEARCH_EMPTY_SOURCE
    if len(partition.qualified) > 0:
        return CandidatePoolDirection.QUALIFIED_AVAILABLE
    if len(partition.uncertain) > 0:
        return CandidatePoolDirection.QUALIFICATION_UNRESOLVED
    return CandidatePoolDirection.FILTER_EMPTY


def _validate_reason_matches_status(
    status: ConstraintEvaluationStatus,
    reason_code: ConstraintReasonCode,
) -> None:
    allowed = {
        ConstraintEvaluationStatus.PASS: {
            ConstraintReasonCode.CONSTRAINT_SATISFIED,
            ConstraintReasonCode.MAX_PRICE_SATISFIED,
            ConstraintReasonCode.MAX_STOPS_SATISFIED,
        },
        ConstraintEvaluationStatus.FAIL: {
            ConstraintReasonCode.CONSTRAINT_VIOLATED,
            ConstraintReasonCode.MAX_PRICE_EXCEEDED,
            ConstraintReasonCode.MAX_STOPS_EXCEEDED,
        },
        ConstraintEvaluationStatus.UNKNOWN: {
            ConstraintReasonCode.INSUFFICIENT_EVIDENCE,
            ConstraintReasonCode.MAX_PRICE_INSUFFICIENT_EVIDENCE,
            ConstraintReasonCode.MAX_STOPS_INSUFFICIENT_EVIDENCE,
        },
    }[status]
    if reason_code not in allowed:
        raise DomainInvariantViolation("ConstraintEvaluation reason code must match status")
