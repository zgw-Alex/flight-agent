"""M5 minimal filter seam and M6 complete filtering engine foundation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from flight_agent.domain.decision.evaluation import (
    CandidateEligibility,
    CandidatePoolDirection,
    CandidatePoolPartition,
    ConstraintEvaluation,
    ConstraintEvaluationLineage,
    ConstraintEvaluationScope,
    ConstraintEvaluationStatus,
    ConstraintReasonCode,
    DecisionConstraintScope,
    EvaluationValueEvidence,
    OfferBackedItineraryCandidate,
    SegmentSelection,
    aggregate_candidate_eligibility,
    classify_candidate_pool_direction,
    partition_candidate_pool,
)
from flight_agent.domain.decision.features import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    STOP_COUNT,
    TOTAL_PRICE,
    DerivedFeatureSet,
    FeatureValue,
    FeatureValueType,
)
from flight_agent.domain.decision.identity import (
    ConstraintEvaluationId,
    FilterResultId,
    FilterRunId,
)
from flight_agent.domain.decision.policy import DecisionPolicyVersion
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    ItineraryId,
    Money,
    Offer,
    OfferId,
    PriceSemantics,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
    StopCount,
)
from flight_agent.domain.shared import (
    DomainInvariantViolation,
    DomainValue,
    RequirementVersion,
    SnapshotVersion,
    ValueState,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource

FilterEvaluationStatus = ConstraintEvaluationStatus


@dataclass(frozen=True)
class FilterEvaluation:
    offer_id: OfferId
    itinerary_id: ItineraryId | None
    status: FilterEvaluationStatus
    reason: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class FilterResult:
    snapshot_id: str
    max_price: Money
    evaluations: tuple[FilterEvaluation, ...]

    @property
    def eligible_offer_ids(self) -> tuple[OfferId, ...]:
        return tuple(
            evaluation.offer_id
            for evaluation in self.evaluations
            if evaluation.status is FilterEvaluationStatus.PASS
        )

    @property
    def has_eligible_candidates(self) -> bool:
        return len(self.eligible_offer_ids) > 0


@dataclass(frozen=True)
class MaxPriceFilter:
    max_price: Money

    @classmethod
    def cny(cls, amount: int) -> MaxPriceFilter:
        return cls(Money(Decimal(amount), "CNY"))

    def evaluate_snapshot(self, snapshot: CandidateSnapshot) -> FilterResult:
        return FilterResult(
            snapshot_id=snapshot.snapshot_id.value,
            max_price=self.max_price,
            evaluations=tuple(self.evaluate_offer(offer) for offer in snapshot.offers),
        )

    def evaluate_offer(self, offer: Offer) -> FilterEvaluation:
        if offer.total_price.currency != self.max_price.currency:
            return FilterEvaluation(
                offer_id=offer.offer_id,
                itinerary_id=offer.itinerary_id,
                status=FilterEvaluationStatus.UNKNOWN,
                reason="MAX_PRICE currency is not comparable",
                evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
            )
        if offer.total_price.amount <= self.max_price.amount:
            return FilterEvaluation(
                offer_id=offer.offer_id,
                itinerary_id=offer.itinerary_id,
                status=FilterEvaluationStatus.PASS,
                reason="MAX_PRICE passed",
                evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
            )
        return FilterEvaluation(
            offer_id=offer.offer_id,
            itinerary_id=offer.itinerary_id,
            status=FilterEvaluationStatus.FAIL,
            reason="MAX_PRICE failed",
            evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
        )

    def evaluate_missing_price(self, offer_id: OfferId) -> FilterEvaluation:
        return FilterEvaluation(
            offer_id=offer_id,
            itinerary_id=None,
            status=FilterEvaluationStatus.UNKNOWN,
            reason="MAX_PRICE cannot evaluate a missing canonical price",
            evidence=(EvidenceRef(EvidenceSource.OFFER, offer_id),),
        )


class FilterResultDirection(str, Enum):
    QUALIFIED_AVAILABLE = "QUALIFIED_AVAILABLE"
    FILTER_EMPTY = "FILTER_EMPTY"
    QUALIFICATION_UNRESOLVED = "QUALIFICATION_UNRESOLVED"
    SEARCH_EMPTY_SOURCE = "SEARCH_EMPTY_SOURCE"


@dataclass(frozen=True)
class FilterRun:
    run_id: FilterRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    derived_feature_run_id: str
    filter_policy_version: DecisionPolicyVersion
    evaluator_registry_version: DecisionPolicyVersion
    applicable_constraint_ids: tuple[ConstraintId, ...]


@dataclass(frozen=True, init=False)
class CompleteFilterResult:
    filter_result_id: FilterResultId
    run_id: FilterRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    filter_policy_version: DecisionPolicyVersion
    evaluations: tuple[ConstraintEvaluation, ...]
    candidate_eligibilities: tuple[CandidateEligibility, ...]
    partition: CandidatePoolPartition
    direction: FilterResultDirection

    def __init__(
        self,
        filter_result_id: FilterResultId,
        run_id: FilterRunId,
        requirement_id: RequirementId,
        requirement_version: RequirementVersion,
        snapshot_id: CandidateSnapshotId,
        snapshot_version: SnapshotVersion,
        derived_feature_set_id: str,
        filter_policy_version: DecisionPolicyVersion,
        evaluations: tuple[ConstraintEvaluation, ...],
        candidate_eligibilities: tuple[CandidateEligibility, ...],
        partition: CandidatePoolPartition,
        direction: FilterResultDirection,
    ) -> None:
        eligibilities_tuple = tuple(candidate_eligibilities)
        evaluated = {eligibility.candidate for eligibility in eligibilities_tuple}
        partitioned = set(partition.qualified + partition.uncertain + partition.rejected)
        if evaluated != partitioned:
            raise DomainInvariantViolation("FilterResult partition must cover evaluated candidates")
        object.__setattr__(self, "filter_result_id", filter_result_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "requirement_version", requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "derived_feature_set_id", derived_feature_set_id)
        object.__setattr__(self, "filter_policy_version", filter_policy_version)
        object.__setattr__(self, "evaluations", tuple(evaluations))
        object.__setattr__(self, "candidate_eligibilities", eligibilities_tuple)
        object.__setattr__(self, "partition", partition)
        object.__setattr__(self, "direction", direction)

    @property
    def qualified_candidates(self) -> tuple[OfferBackedItineraryCandidate, ...]:
        return self.partition.qualified

    @property
    def uncertain_candidates(self) -> tuple[OfferBackedItineraryCandidate, ...]:
        return self.partition.uncertain

    @property
    def rejected_candidates(self) -> tuple[OfferBackedItineraryCandidate, ...]:
        return self.partition.rejected


class ConstraintEvaluator(Protocol):
    @property
    def constraint_scope(self) -> ConstraintScope:
        ...

    @property
    def required_feature_keys(self) -> tuple:
        ...

    def evaluate(
        self,
        *,
        constraint: HardConstraint,
        candidate: OfferBackedItineraryCandidate,
        offer: Offer,
        feature_set: DerivedFeatureSet,
        lineage: ConstraintEvaluationLineage,
        evaluation_id: ConstraintEvaluationId,
    ) -> ConstraintEvaluation:
        ...


@dataclass(frozen=True, init=False)
class FilterEvaluatorRegistry:
    evaluators: tuple[ConstraintEvaluator, ...]
    registry_version: DecisionPolicyVersion

    def __init__(
        self,
        evaluators: tuple[ConstraintEvaluator, ...],
        registry_version: DecisionPolicyVersion,
    ) -> None:
        evaluators_tuple = tuple(evaluators)
        scopes = tuple(evaluator.constraint_scope for evaluator in evaluators_tuple)
        if len(frozenset(scopes)) != len(scopes):
            raise DomainInvariantViolation("FilterEvaluatorRegistry requires unique constraint scopes")
        object.__setattr__(
            self,
            "evaluators",
            tuple(sorted(evaluators_tuple, key=lambda evaluator: evaluator.constraint_scope.value)),
        )
        object.__setattr__(self, "registry_version", registry_version)

    def evaluator_for(self, constraint: HardConstraint) -> ConstraintEvaluator:
        for evaluator in self.evaluators:
            if evaluator.constraint_scope is constraint.scope:
                return evaluator
        raise DomainInvariantViolation(f"Unsupported filter constraint scope: {constraint.scope.value}")


@dataclass(frozen=True)
class DepartureDateConstraintEvaluator:
    constraint_scope: ConstraintScope = ConstraintScope.DEPARTURE_DATE
    required_feature_keys: tuple = (DEPARTURE_DATE_MATCHES_REQUIREMENT,)

    def evaluate(
        self,
        *,
        constraint: HardConstraint,
        candidate: OfferBackedItineraryCandidate,
        offer: Offer,
        feature_set: DerivedFeatureSet,
        lineage: ConstraintEvaluationLineage,
        evaluation_id: ConstraintEvaluationId,
    ) -> ConstraintEvaluation:
        del offer
        if constraint.operator is not ConstraintOperator.EQUALS or not isinstance(
            constraint.value,
            LocalDate,
        ):
            raise DomainInvariantViolation("Departure date evaluator requires EQUALS LocalDate")
        feature_value = feature_set.value_for(candidate, DEPARTURE_DATE_MATCHES_REQUIREMENT)
        _validate_feature_value(feature_value, FeatureValueType.BOOLEAN)
        status = _status_from_bool_feature(feature_value)
        return _constraint_evaluation(
            evaluation_id=evaluation_id,
            constraint_id=constraint.constraint_id,
            candidate=candidate,
            scope=ConstraintEvaluationScope(DecisionConstraintScope.ITINERARY),
            status=status,
            expected=DomainValue.known(constraint.value.value),
            actual=_actual_departure_date_match(feature_value),
            expected_label="departure_date",
            actual_label="departure_date_matches_requirement",
            evidence=(
                *feature_value.evidence,
                EvidenceRef(EvidenceSource.CONSTRAINT, constraint.constraint_id),
            ),
            lineage=lineage,
        )


@dataclass(frozen=True)
class MaxPriceConstraintEvaluator:
    constraint_scope: ConstraintScope = ConstraintScope.MAX_PRICE
    required_feature_keys: tuple = (TOTAL_PRICE,)

    def evaluate(
        self,
        *,
        constraint: HardConstraint,
        candidate: OfferBackedItineraryCandidate,
        offer: Offer,
        feature_set: DerivedFeatureSet,
        lineage: ConstraintEvaluationLineage,
        evaluation_id: ConstraintEvaluationId,
    ) -> ConstraintEvaluation:
        if constraint.operator is not ConstraintOperator.AT_OR_BEFORE or not isinstance(
            constraint.value,
            Money,
        ):
            raise DomainInvariantViolation("Max price evaluator requires AT_OR_BEFORE Money")
        feature_value = feature_set.value_for(candidate, TOTAL_PRICE)
        _validate_feature_value(feature_value, FeatureValueType.MONEY)
        actual = _actual_total_price(feature_value)
        status = _status_from_money_threshold(feature_value, constraint.value, offer.price_semantics)
        return _constraint_evaluation(
            evaluation_id=evaluation_id,
            constraint_id=constraint.constraint_id,
            candidate=candidate,
            scope=ConstraintEvaluationScope(DecisionConstraintScope.OFFER),
            status=status,
            expected=DomainValue.known(constraint.value),
            actual=actual,
            expected_label="max_price",
            actual_label="total_price",
            evidence=(
                *feature_value.evidence,
                EvidenceRef(EvidenceSource.CONSTRAINT, constraint.constraint_id),
            ),
            lineage=lineage,
            reason_code={
                ConstraintEvaluationStatus.PASS: ConstraintReasonCode.MAX_PRICE_SATISFIED,
                ConstraintEvaluationStatus.FAIL: ConstraintReasonCode.MAX_PRICE_EXCEEDED,
                ConstraintEvaluationStatus.UNKNOWN: ConstraintReasonCode.MAX_PRICE_INSUFFICIENT_EVIDENCE,
            }[status],
        )


@dataclass(frozen=True)
class MaxStopsConstraintEvaluator:
    constraint_scope: ConstraintScope = ConstraintScope.MAX_STOPS
    required_feature_keys: tuple = (STOP_COUNT,)

    def evaluate(
        self,
        *,
        constraint: HardConstraint,
        candidate: OfferBackedItineraryCandidate,
        offer: Offer,
        feature_set: DerivedFeatureSet,
        lineage: ConstraintEvaluationLineage,
        evaluation_id: ConstraintEvaluationId,
    ) -> ConstraintEvaluation:
        del offer
        if constraint.operator is not ConstraintOperator.AT_OR_BEFORE or not isinstance(
            constraint.value,
            StopCount,
        ):
            raise DomainInvariantViolation("Max stops evaluator requires AT_OR_BEFORE StopCount")
        feature_value = feature_set.value_for(candidate, STOP_COUNT)
        _validate_feature_value(feature_value, FeatureValueType.INTEGER)
        status = _status_from_integer_threshold(feature_value, constraint.value.value)
        return _constraint_evaluation(
            evaluation_id=evaluation_id,
            constraint_id=constraint.constraint_id,
            candidate=candidate,
            scope=ConstraintEvaluationScope(DecisionConstraintScope.ITINERARY),
            status=status,
            expected=DomainValue.known(constraint.value.value),
            actual=_actual_integer_feature(feature_value),
            expected_label="max_stops",
            actual_label="stop_count",
            evidence=(
                *feature_value.evidence,
                EvidenceRef(EvidenceSource.CONSTRAINT, constraint.constraint_id),
            ),
            lineage=lineage,
            reason_code={
                ConstraintEvaluationStatus.PASS: ConstraintReasonCode.MAX_STOPS_SATISFIED,
                ConstraintEvaluationStatus.FAIL: ConstraintReasonCode.MAX_STOPS_EXCEEDED,
                ConstraintEvaluationStatus.UNKNOWN: ConstraintReasonCode.MAX_STOPS_INSUFFICIENT_EVIDENCE,
            }[status],
        )


@dataclass(frozen=True)
class CompleteFilteringEngine:
    evaluator_registry: FilterEvaluatorRegistry

    def filter(
        self,
        *,
        filter_result_id: FilterResultId,
        filter_run_id: FilterRunId,
        requirement: RequirementState,
        snapshot: CandidateSnapshot,
        feature_set: DerivedFeatureSet,
        filter_policy_version: DecisionPolicyVersion,
    ) -> tuple[FilterRun, CompleteFilterResult]:
        _validate_feature_set_lineage(feature_set, requirement, snapshot)
        candidates = _candidates_from_snapshot(snapshot)
        offers_by_candidate = {_candidate_for_offer(offer): offer for offer in snapshot.offers}
        constraints = tuple(sorted(requirement.constraints, key=lambda item: item.constraint_id.value))
        run = FilterRun(
            run_id=filter_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            derived_feature_set_id=feature_set.feature_set_id.value,
            derived_feature_run_id=feature_set.run_id.value,
            filter_policy_version=filter_policy_version,
            evaluator_registry_version=self.evaluator_registry.registry_version,
            applicable_constraint_ids=tuple(constraint.constraint_id for constraint in constraints),
        )
        evaluations_by_candidate = {
            candidate: tuple(
                self.evaluator_registry.evaluator_for(constraint).evaluate(
                    constraint=constraint,
                    candidate=candidate,
                    offer=offers_by_candidate[candidate],
                    feature_set=feature_set,
                    lineage=ConstraintEvaluationLineage(
                        requirement_id=requirement.requirement_id,
                        requirement_version=requirement.version,
                        snapshot_id=snapshot.snapshot_id,
                        snapshot_version=snapshot.version,
                        filter_policy_version=filter_policy_version,
                        filter_run_id=filter_run_id,
                    ),
                    evaluation_id=ConstraintEvaluationId(
                        f"{filter_run_id.value}:{candidate.offer_id.value}:{constraint.constraint_id.value}"
                    ),
                )
                for constraint in constraints
            )
            for candidate in candidates
        }
        eligibilities = tuple(
            aggregate_candidate_eligibility(candidate, evaluations)
            for candidate, evaluations in sorted(
                evaluations_by_candidate.items(),
                key=lambda item: item[0].offer_id.value,
            )
        )
        partition = partition_candidate_pool(eligibilities)
        direction = _direction_from_candidate_pool(
            classify_candidate_pool_direction(
                candidate_count=len(candidates),
                partition=partition,
            )
        )
        result = CompleteFilterResult(
            filter_result_id=filter_result_id,
            run_id=filter_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            derived_feature_set_id=feature_set.feature_set_id.value,
            filter_policy_version=filter_policy_version,
            evaluations=tuple(
                evaluation
                for candidate in sorted(candidates, key=lambda item: item.offer_id.value)
                for evaluation in evaluations_by_candidate[candidate]
            ),
            candidate_eligibilities=eligibilities,
            partition=partition,
            direction=direction,
        )
        return run, result


def m6_default_filter_evaluator_registry() -> FilterEvaluatorRegistry:
    return FilterEvaluatorRegistry(
        evaluators=(
            DepartureDateConstraintEvaluator(),
            MaxPriceConstraintEvaluator(),
            MaxStopsConstraintEvaluator(),
        ),
        registry_version=DecisionPolicyVersion("filter-evaluator-registry-v1"),
    )


def m6_default_complete_filtering_engine() -> CompleteFilteringEngine:
    return CompleteFilteringEngine(evaluator_registry=m6_default_filter_evaluator_registry())


def aggregate_segment_evaluations(
    statuses: Iterable[ConstraintEvaluationStatus],
    selection: SegmentSelection,
) -> ConstraintEvaluationStatus:
    statuses_tuple = tuple(statuses)
    if len(statuses_tuple) == 0:
        raise DomainInvariantViolation("Segment scope aggregation requires at least one segment")
    if selection is SegmentSelection.ANY_SEGMENT:
        if any(status is ConstraintEvaluationStatus.PASS for status in statuses_tuple):
            return ConstraintEvaluationStatus.PASS
        if any(status is ConstraintEvaluationStatus.UNKNOWN for status in statuses_tuple):
            return ConstraintEvaluationStatus.UNKNOWN
        return ConstraintEvaluationStatus.FAIL
    if selection is SegmentSelection.ALL_SEGMENTS:
        if any(status is ConstraintEvaluationStatus.FAIL for status in statuses_tuple):
            return ConstraintEvaluationStatus.FAIL
        if any(status is ConstraintEvaluationStatus.UNKNOWN for status in statuses_tuple):
            return ConstraintEvaluationStatus.UNKNOWN
        return ConstraintEvaluationStatus.PASS
    if selection in {SegmentSelection.FIRST_SEGMENT, SegmentSelection.LAST_SEGMENT}:
        return statuses_tuple[0] if selection is SegmentSelection.FIRST_SEGMENT else statuses_tuple[-1]
    raise DomainInvariantViolation("Unsupported segment selection")


def _constraint_evaluation(
    *,
    evaluation_id: ConstraintEvaluationId,
    constraint_id: ConstraintId,
    candidate: OfferBackedItineraryCandidate,
    scope: ConstraintEvaluationScope,
    status: ConstraintEvaluationStatus,
    expected: DomainValue[object],
    actual: DomainValue[object],
    expected_label: str,
    actual_label: str,
    evidence: tuple[EvidenceRef, ...],
    lineage: ConstraintEvaluationLineage,
    reason_code: ConstraintReasonCode | None = None,
) -> ConstraintEvaluation:
    return ConstraintEvaluation(
        evaluation_id=evaluation_id,
        constraint_id=constraint_id,
        candidate=candidate,
        scope=scope,
        status=status,
        expected=EvaluationValueEvidence(expected_label, expected, evidence),
        actual=EvaluationValueEvidence(actual_label, actual, evidence),
        reason_code=reason_code or {
            ConstraintEvaluationStatus.PASS: ConstraintReasonCode.CONSTRAINT_SATISFIED,
            ConstraintEvaluationStatus.FAIL: ConstraintReasonCode.CONSTRAINT_VIOLATED,
            ConstraintEvaluationStatus.UNKNOWN: ConstraintReasonCode.INSUFFICIENT_EVIDENCE,
        }[status],
        evidence=evidence,
        lineage=lineage,
    )


def _status_from_bool_feature(feature_value: FeatureValue) -> ConstraintEvaluationStatus:
    if feature_value.value_status is not ValueState.KNOWN:
        return ConstraintEvaluationStatus.UNKNOWN
    if feature_value.value.value is True:
        return ConstraintEvaluationStatus.PASS
    if feature_value.value.value is False:
        return ConstraintEvaluationStatus.FAIL
    raise DomainInvariantViolation("Boolean feature produced a non-boolean value")


def _actual_departure_date_match(feature_value: FeatureValue) -> DomainValue[object]:
    if feature_value.value_status is not ValueState.KNOWN:
        return DomainValue.not_provided()
    return DomainValue.known(feature_value.value.value)


def _status_from_money_threshold(
    feature_value: FeatureValue,
    max_price: Money,
    price_semantics: PriceSemantics,
) -> ConstraintEvaluationStatus:
    if feature_value.value_status is not ValueState.KNOWN:
        return ConstraintEvaluationStatus.UNKNOWN
    actual = feature_value.value.value
    if not isinstance(actual, Money):
        raise DomainInvariantViolation("Money feature produced a non-Money value")
    if actual.currency != max_price.currency:
        return ConstraintEvaluationStatus.UNKNOWN
    if price_semantics is PriceSemantics.LOWER_BOUND:
        if actual.amount > max_price.amount:
            return ConstraintEvaluationStatus.FAIL
        return ConstraintEvaluationStatus.UNKNOWN
    if actual.amount <= max_price.amount:
        return ConstraintEvaluationStatus.PASS
    return ConstraintEvaluationStatus.FAIL


def _actual_total_price(feature_value: FeatureValue) -> DomainValue[object]:
    if feature_value.value_status is not ValueState.KNOWN:
        return DomainValue.not_provided()
    return DomainValue.known(feature_value.value.value)


def _status_from_integer_threshold(
    feature_value: FeatureValue,
    maximum: int,
) -> ConstraintEvaluationStatus:
    if feature_value.value_status is not ValueState.KNOWN:
        return ConstraintEvaluationStatus.UNKNOWN
    actual = feature_value.value.value
    if not isinstance(actual, int) or isinstance(actual, bool):
        raise DomainInvariantViolation("Integer feature produced a non-integer value")
    if actual <= maximum:
        return ConstraintEvaluationStatus.PASS
    return ConstraintEvaluationStatus.FAIL


def _actual_integer_feature(feature_value: FeatureValue) -> DomainValue[object]:
    if feature_value.value_status is not ValueState.KNOWN:
        return DomainValue.not_provided()
    return DomainValue.known(feature_value.value.value)


def _validate_feature_value(feature_value: FeatureValue, value_type: FeatureValueType) -> None:
    if feature_value.value_type is not value_type:
        raise DomainInvariantViolation("Filter evaluator received wrong feature value type")


def _validate_feature_set_lineage(
    feature_set: DerivedFeatureSet,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
) -> None:
    if feature_set.input_lineage.snapshot_id != snapshot.snapshot_id:
        raise DomainInvariantViolation("DerivedFeatureSet snapshot lineage does not match filter input")
    if feature_set.input_lineage.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("DerivedFeatureSet snapshot version does not match filter input")
    if feature_set.input_lineage.requirement_id != requirement.requirement_id:
        raise DomainInvariantViolation("DerivedFeatureSet requirement lineage does not match filter input")
    if feature_set.input_lineage.requirement_version != requirement.version:
        raise DomainInvariantViolation("DerivedFeatureSet requirement version does not match filter input")


def _candidates_from_snapshot(snapshot: CandidateSnapshot) -> tuple[OfferBackedItineraryCandidate, ...]:
    itinerary_ids = {itinerary.itinerary_id for itinerary in snapshot.itineraries}
    candidates = []
    for offer in snapshot.offers:
        if offer.itinerary_id not in itinerary_ids:
            raise DomainInvariantViolation("Offer references missing itinerary")
        candidates.append(
            OfferBackedItineraryCandidate(offer_id=offer.offer_id, itinerary_id=offer.itinerary_id)
        )
    return tuple(sorted(candidates, key=lambda candidate: candidate.offer_id.value))


def _candidate_for_offer(offer: Offer) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(offer_id=offer.offer_id, itinerary_id=offer.itinerary_id)


def _direction_from_candidate_pool(direction: CandidatePoolDirection) -> FilterResultDirection:
    return {
        CandidatePoolDirection.QUALIFIED_AVAILABLE: FilterResultDirection.QUALIFIED_AVAILABLE,
        CandidatePoolDirection.FILTER_EMPTY: FilterResultDirection.FILTER_EMPTY,
        CandidatePoolDirection.QUALIFICATION_UNRESOLVED: FilterResultDirection.QUALIFICATION_UNRESOLVED,
        CandidatePoolDirection.SEARCH_EMPTY_SOURCE: FilterResultDirection.SEARCH_EMPTY_SOURCE,
    }[direction]
