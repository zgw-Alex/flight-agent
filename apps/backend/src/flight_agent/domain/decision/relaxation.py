"""M6 deterministic hard-constraint relaxation analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.decision.evaluation import (
    CandidateEligibilityStatus,
    ConstraintEvaluation,
    ConstraintEvaluationLineage,
    ConstraintEvaluationStatus,
    OfferBackedItineraryCandidate,
    aggregate_candidate_eligibility,
)
from flight_agent.domain.decision.features import (
    STOP_COUNT,
    TOTAL_PRICE,
    DerivedFeatureSet,
    FeatureValueType,
)
from flight_agent.domain.decision.filtering import (
    CompleteFilterResult,
    FilterEvaluatorRegistry,
    FilterResultDirection,
)
from flight_agent.domain.decision.identity import (
    ConstraintEvaluationId,
    RelaxationResultId,
    RelaxationRunId,
)
from flight_agent.domain.decision.policy import DecisionPolicyVersion
from flight_agent.domain.flights import CandidateSnapshot, CandidateSnapshotId, Money, Offer
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    RequirementId,
    RequirementState,
    StopCount,
)
from flight_agent.domain.shared import (
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
    ValueState,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource


class RelaxationResultDirection(str, Enum):
    PROPOSALS_AVAILABLE = "PROPOSALS_AVAILABLE"
    NOT_TRIGGERED = "NOT_TRIGGERED"
    NO_PROPOSAL = "NO_PROPOSAL"


class RelaxationProposalKind(str, Enum):
    SINGLE = "SINGLE"
    PAIRWISE = "PAIRWISE"


class RelaxationReasonCode(str, Enum):
    FILTER_EMPTY_MAX_PRICE_RELAXATION = "FILTER_EMPTY_MAX_PRICE_RELAXATION"
    FILTER_EMPTY_MAX_STOPS_RELAXATION = "FILTER_EMPTY_MAX_STOPS_RELAXATION"
    RELAXATION_NOT_TRIGGERED = "RELAXATION_NOT_TRIGGERED"
    NO_FAIL_ONLY_RELAXATION_AVAILABLE = "NO_FAIL_ONLY_RELAXATION_AVAILABLE"


RelaxationThreshold = Money | int


@dataclass(frozen=True, init=False)
class RelaxationPolicy:
    policy_version: DecisionPolicyVersion
    enabled_constraint_scopes: tuple[ConstraintScope, ...]
    max_single_proposals: int
    max_pairwise_proposals: int
    max_total_proposals: int
    ordering_policy: tuple[str, ...]
    dominance_policy: str

    def __init__(
        self,
        policy_version: DecisionPolicyVersion,
        enabled_constraint_scopes: tuple[ConstraintScope, ...],
        *,
        max_single_proposals: int = 3,
        max_pairwise_proposals: int = 0,
        max_total_proposals: int = 3,
        ordering_policy: tuple[str, ...] = (
            "proposal_kind",
            "native_magnitude_asc",
            "recovered_count_desc",
            "constraint_id_asc",
            "proposed_value_asc",
            "canonical_candidate_identity_asc",
        ),
        dominance_policy: str = "same-recovered-set-lower-or-equal-magnitude",
    ) -> None:
        scopes = tuple(enabled_constraint_scopes)
        if len(frozenset(scopes)) != len(scopes):
            raise DomainInvariantViolation("RelaxationPolicy enabled scopes must be unique")
        supported = {ConstraintScope.MAX_PRICE, ConstraintScope.MAX_STOPS}
        if any(scope not in supported for scope in scopes):
            raise DomainInvariantViolation("RelaxationPolicy only supports MAX_PRICE and MAX_STOPS in M6")
        if max_single_proposals < 0 or max_pairwise_proposals < 0 or max_total_proposals < 0:
            raise DomainInvariantViolation("RelaxationPolicy proposal bounds must not be negative")
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "enabled_constraint_scopes", tuple(sorted(scopes, key=lambda scope: scope.value)))
        object.__setattr__(self, "max_single_proposals", max_single_proposals)
        object.__setattr__(self, "max_pairwise_proposals", max_pairwise_proposals)
        object.__setattr__(self, "max_total_proposals", max_total_proposals)
        object.__setattr__(self, "ordering_policy", tuple(ordering_policy))
        object.__setattr__(self, "dominance_policy", dominance_policy)


@dataclass(frozen=True)
class RelaxationRun:
    run_id: RelaxationRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    filter_result_id: str
    relaxation_policy_version: DecisionPolicyVersion
    enabled_constraint_scopes: tuple[ConstraintScope, ...]
    entry_direction: FilterResultDirection


@dataclass(frozen=True, init=False)
class RelaxationProposal:
    proposal_id: str
    proposal_kind: RelaxationProposalKind
    proposal_order: int
    source_constraint_id: ConstraintId
    constraint_scope: ConstraintScope
    current_value: RelaxationThreshold
    proposed_value: RelaxationThreshold
    native_magnitude: RelaxationThreshold
    recovered_candidates: tuple[OfferBackedItineraryCandidate, ...]
    source_evaluation_ids: tuple[ConstraintEvaluationId, ...]
    counterfactual_evaluation_ids: tuple[ConstraintEvaluationId, ...]
    reason_code: RelaxationReasonCode
    evidence: tuple[EvidenceRef, ...]
    relaxation_policy_version: DecisionPolicyVersion

    def __init__(
        self,
        *,
        proposal_id: str,
        proposal_kind: RelaxationProposalKind,
        proposal_order: int,
        source_constraint_id: ConstraintId,
        constraint_scope: ConstraintScope,
        current_value: RelaxationThreshold,
        proposed_value: RelaxationThreshold,
        native_magnitude: RelaxationThreshold,
        recovered_candidates: tuple[OfferBackedItineraryCandidate, ...],
        source_evaluation_ids: tuple[ConstraintEvaluationId, ...],
        counterfactual_evaluation_ids: tuple[ConstraintEvaluationId, ...],
        reason_code: RelaxationReasonCode,
        evidence: tuple[EvidenceRef, ...],
        relaxation_policy_version: DecisionPolicyVersion,
    ) -> None:
        if proposal_id.strip() == "":
            raise DomainInvariantViolation("RelaxationProposal requires proposal_id")
        if proposal_order < 1:
            raise DomainInvariantViolation("RelaxationProposal order must be positive")
        _validate_threshold_delta(constraint_scope, current_value, proposed_value, native_magnitude)
        recovered = tuple(sorted(recovered_candidates, key=_candidate_key))
        if len(recovered) == 0:
            raise DomainInvariantViolation("RelaxationProposal requires recovered candidates")
        if len(frozenset(recovered)) != len(recovered):
            raise DomainInvariantViolation("RelaxationProposal recovered candidates must be unique")
        evidence_tuple = tuple(evidence)
        if len(evidence_tuple) == 0:
            raise DomainInvariantViolation("RelaxationProposal requires evidence")
        object.__setattr__(self, "proposal_id", proposal_id)
        object.__setattr__(self, "proposal_kind", proposal_kind)
        object.__setattr__(self, "proposal_order", proposal_order)
        object.__setattr__(self, "source_constraint_id", source_constraint_id)
        object.__setattr__(self, "constraint_scope", constraint_scope)
        object.__setattr__(self, "current_value", current_value)
        object.__setattr__(self, "proposed_value", proposed_value)
        object.__setattr__(self, "native_magnitude", native_magnitude)
        object.__setattr__(self, "recovered_candidates", recovered)
        object.__setattr__(self, "source_evaluation_ids", tuple(source_evaluation_ids))
        object.__setattr__(self, "counterfactual_evaluation_ids", tuple(counterfactual_evaluation_ids))
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "evidence", evidence_tuple)
        object.__setattr__(self, "relaxation_policy_version", relaxation_policy_version)


@dataclass(frozen=True, init=False)
class RelaxationResult:
    relaxation_result_id: RelaxationResultId
    run_id: RelaxationRunId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    derived_feature_set_id: str
    filter_result_id: str
    relaxation_policy_version: DecisionPolicyVersion
    direction: RelaxationResultDirection
    reason_code: RelaxationReasonCode
    proposals: tuple[RelaxationProposal, ...]
    source_rejected_candidates: tuple[OfferBackedItineraryCandidate, ...]

    def __init__(
        self,
        relaxation_result_id: RelaxationResultId,
        run_id: RelaxationRunId,
        requirement_id: RequirementId,
        requirement_version: RequirementVersion,
        snapshot_id: CandidateSnapshotId,
        snapshot_version: SnapshotVersion,
        derived_feature_set_id: str,
        filter_result_id: str,
        relaxation_policy_version: DecisionPolicyVersion,
        direction: RelaxationResultDirection,
        reason_code: RelaxationReasonCode,
        proposals: tuple[RelaxationProposal, ...],
        source_rejected_candidates: tuple[OfferBackedItineraryCandidate, ...],
    ) -> None:
        proposals_tuple = tuple(proposals)
        if direction is RelaxationResultDirection.PROPOSALS_AVAILABLE and len(proposals_tuple) == 0:
            raise DomainInvariantViolation("PROPOSALS_AVAILABLE requires proposals")
        if direction is not RelaxationResultDirection.PROPOSALS_AVAILABLE and len(proposals_tuple) > 0:
            raise DomainInvariantViolation("Non-triggered relaxation result must not carry proposals")
        object.__setattr__(self, "relaxation_result_id", relaxation_result_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "requirement_version", requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "derived_feature_set_id", derived_feature_set_id)
        object.__setattr__(self, "filter_result_id", filter_result_id)
        object.__setattr__(self, "relaxation_policy_version", relaxation_policy_version)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "proposals", proposals_tuple)
        object.__setattr__(self, "source_rejected_candidates", tuple(source_rejected_candidates))


@dataclass(frozen=True)
class DeterministicRelaxationEngine:
    evaluator_registry: FilterEvaluatorRegistry

    def analyze(
        self,
        *,
        relaxation_result_id: RelaxationResultId,
        relaxation_run_id: RelaxationRunId,
        requirement: RequirementState,
        snapshot: CandidateSnapshot,
        feature_set: DerivedFeatureSet,
        filter_result: CompleteFilterResult,
        relaxation_policy: RelaxationPolicy,
    ) -> tuple[RelaxationRun, RelaxationResult]:
        _validate_lineage(requirement, snapshot, feature_set, filter_result)
        run = RelaxationRun(
            run_id=relaxation_run_id,
            requirement_id=requirement.requirement_id,
            requirement_version=requirement.version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            derived_feature_set_id=feature_set.feature_set_id.value,
            filter_result_id=filter_result.filter_result_id.value,
            relaxation_policy_version=relaxation_policy.policy_version,
            enabled_constraint_scopes=relaxation_policy.enabled_constraint_scopes,
            entry_direction=filter_result.direction,
        )
        if not _is_definitive_filter_empty(filter_result):
            return run, _result(
                relaxation_result_id,
                relaxation_run_id,
                requirement,
                snapshot,
                feature_set,
                filter_result,
                relaxation_policy,
                RelaxationResultDirection.NOT_TRIGGERED,
                RelaxationReasonCode.RELAXATION_NOT_TRIGGERED,
                (),
            )
        raw_proposals = (
            *_max_price_single_proposals(
                requirement=requirement,
                snapshot=snapshot,
                feature_set=feature_set,
                filter_result=filter_result,
                relaxation_policy=relaxation_policy,
                evaluator_registry=self.evaluator_registry,
                relaxation_run_id=relaxation_run_id,
            ),
            *_max_stops_single_proposals(
                requirement=requirement,
                snapshot=snapshot,
                feature_set=feature_set,
                filter_result=filter_result,
                relaxation_policy=relaxation_policy,
                evaluator_registry=self.evaluator_registry,
                relaxation_run_id=relaxation_run_id,
            ),
        )
        proposals = _bounded_ordered_proposals(raw_proposals, relaxation_policy)
        if len(proposals) == 0:
            return run, _result(
                relaxation_result_id,
                relaxation_run_id,
                requirement,
                snapshot,
                feature_set,
                filter_result,
                relaxation_policy,
                RelaxationResultDirection.NO_PROPOSAL,
                RelaxationReasonCode.NO_FAIL_ONLY_RELAXATION_AVAILABLE,
                (),
            )
        ordered = tuple(_with_order(proposal, index) for index, proposal in enumerate(proposals, start=1))
        return run, _result(
            relaxation_result_id,
            relaxation_run_id,
            requirement,
            snapshot,
            feature_set,
            filter_result,
            relaxation_policy,
            RelaxationResultDirection.PROPOSALS_AVAILABLE,
            RelaxationReasonCode.FILTER_EMPTY_MAX_PRICE_RELAXATION,
            ordered,
        )


def m6_default_relaxation_policy() -> RelaxationPolicy:
    return RelaxationPolicy(
        policy_version=DecisionPolicyVersion("relaxation-policy-v1"),
        enabled_constraint_scopes=(ConstraintScope.MAX_PRICE, ConstraintScope.MAX_STOPS),
    )


def m6_default_deterministic_relaxation_engine(
    evaluator_registry: FilterEvaluatorRegistry,
) -> DeterministicRelaxationEngine:
    return DeterministicRelaxationEngine(evaluator_registry=evaluator_registry)


def _max_price_single_proposals(
    *,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    relaxation_policy: RelaxationPolicy,
    evaluator_registry: FilterEvaluatorRegistry,
    relaxation_run_id: RelaxationRunId,
) -> tuple[RelaxationProposal, ...]:
    if ConstraintScope.MAX_PRICE not in relaxation_policy.enabled_constraint_scopes:
        return ()
    proposals: list[RelaxationProposal] = []
    for constraint in _relaxable_max_price_constraints(requirement):
        current_value = constraint.value
        if not isinstance(current_value, Money):
            raise DomainInvariantViolation("MAX_PRICE relaxation requires Money value")
        failing_boundaries = _failing_max_price_boundaries(constraint, feature_set, filter_result)
        for boundary in failing_boundaries:
            proposed = Money(boundary.amount, boundary.currency)
            recovered, counterfactual_ids = _counterfactual_recovered_candidates(
                requirement=requirement,
                snapshot=snapshot,
                feature_set=feature_set,
                filter_result=filter_result,
                evaluator_registry=evaluator_registry,
                source_constraint=constraint,
                proposed_value=proposed,
                relaxation_run_id=relaxation_run_id,
            )
            if len(recovered) == 0:
                continue
            source_evaluations = _source_fail_evaluations(
                filter_result,
                constraint.constraint_id,
                recovered,
            )
            proposals.append(
                RelaxationProposal(
                    proposal_id=(
                        f"{relaxation_run_id.value}:single:{constraint.constraint_id.value}:"
                        f"{proposed.currency}:{proposed.amount}"
                    ),
                    proposal_kind=RelaxationProposalKind.SINGLE,
                    proposal_order=1,
                    source_constraint_id=constraint.constraint_id,
                    constraint_scope=constraint.scope,
                    current_value=current_value,
                    proposed_value=proposed,
                    native_magnitude=Money(proposed.amount - current_value.amount, proposed.currency),
                    recovered_candidates=recovered,
                    source_evaluation_ids=tuple(evaluation.evaluation_id for evaluation in source_evaluations),
                    counterfactual_evaluation_ids=counterfactual_ids,
                    reason_code=RelaxationReasonCode.FILTER_EMPTY_MAX_PRICE_RELAXATION,
                    evidence=(
                        EvidenceRef(EvidenceSource.CONSTRAINT, constraint.constraint_id),
                        *tuple(
                            EvidenceRef(EvidenceSource.OFFER, candidate.offer_id)
                            for candidate in recovered
                        ),
                    ),
                    relaxation_policy_version=relaxation_policy.policy_version,
                )
            )
    return tuple(proposals)


def _max_stops_single_proposals(
    *,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    relaxation_policy: RelaxationPolicy,
    evaluator_registry: FilterEvaluatorRegistry,
    relaxation_run_id: RelaxationRunId,
) -> tuple[RelaxationProposal, ...]:
    if ConstraintScope.MAX_STOPS not in relaxation_policy.enabled_constraint_scopes:
        return ()
    proposals: list[RelaxationProposal] = []
    for constraint in _relaxable_max_stops_constraints(requirement):
        current_value = constraint.value
        if not isinstance(current_value, StopCount):
            raise DomainInvariantViolation("MAX_STOPS relaxation requires StopCount value")
        for boundary in _failing_max_stops_boundaries(constraint, feature_set, filter_result):
            recovered, counterfactual_ids = _counterfactual_recovered_candidates(
                requirement=requirement,
                snapshot=snapshot,
                feature_set=feature_set,
                filter_result=filter_result,
                evaluator_registry=evaluator_registry,
                source_constraint=constraint,
                proposed_value=boundary,
                relaxation_run_id=relaxation_run_id,
            )
            if len(recovered) == 0:
                continue
            source_evaluations = _source_fail_evaluations(
                filter_result,
                constraint.constraint_id,
                recovered,
            )
            proposals.append(
                RelaxationProposal(
                    proposal_id=f"{relaxation_run_id.value}:single:{constraint.constraint_id.value}:{boundary}",
                    proposal_kind=RelaxationProposalKind.SINGLE,
                    proposal_order=1,
                    source_constraint_id=constraint.constraint_id,
                    constraint_scope=constraint.scope,
                    current_value=current_value.value,
                    proposed_value=boundary,
                    native_magnitude=boundary - current_value.value,
                    recovered_candidates=recovered,
                    source_evaluation_ids=tuple(evaluation.evaluation_id for evaluation in source_evaluations),
                    counterfactual_evaluation_ids=counterfactual_ids,
                    reason_code=RelaxationReasonCode.FILTER_EMPTY_MAX_STOPS_RELAXATION,
                    evidence=(
                        EvidenceRef(EvidenceSource.CONSTRAINT, constraint.constraint_id),
                        *tuple(
                            EvidenceRef(EvidenceSource.OFFER, candidate.offer_id)
                            for candidate in recovered
                        ),
                    ),
                    relaxation_policy_version=relaxation_policy.policy_version,
                )
            )
    return tuple(proposals)


def _counterfactual_recovered_candidates(
    *,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    evaluator_registry: FilterEvaluatorRegistry,
    source_constraint: HardConstraint,
    proposed_value: RelaxationThreshold,
    relaxation_run_id: RelaxationRunId,
) -> tuple[tuple[OfferBackedItineraryCandidate, ...], tuple[ConstraintEvaluationId, ...]]:
    recovered: list[OfferBackedItineraryCandidate] = []
    counterfactual_ids: list[ConstraintEvaluationId] = []
    constraints = tuple(
        _replace_constraint_value(constraint, proposed_value)
        if constraint.constraint_id == source_constraint.constraint_id
        else constraint
        for constraint in sorted(requirement.constraints, key=lambda item: item.constraint_id.value)
    )
    for candidate in filter_result.rejected_candidates:
        offer = _offer_for_candidate(snapshot, candidate)
        evaluations = tuple(
            evaluator_registry.evaluator_for(constraint).evaluate(
                constraint=constraint,
                candidate=candidate,
                offer=offer,
                feature_set=feature_set,
                lineage=ConstraintEvaluationLineage(
                    requirement_id=requirement.requirement_id,
                    requirement_version=requirement.version,
                    snapshot_id=filter_result.snapshot_id,
                    snapshot_version=filter_result.snapshot_version,
                    filter_policy_version=filter_result.filter_policy_version,
                    filter_run_id=filter_result.run_id,
                ),
                evaluation_id=ConstraintEvaluationId(
                    f"{relaxation_run_id.value}:counterfactual:{candidate.offer_id.value}:"
                    f"{constraint.constraint_id.value}:{_threshold_token(proposed_value)}"
                ),
            )
            for constraint in constraints
        )
        if aggregate_candidate_eligibility(candidate, evaluations).status is CandidateEligibilityStatus.ELIGIBLE:
            recovered.append(candidate)
            counterfactual_ids.extend(evaluation.evaluation_id for evaluation in evaluations)
    return tuple(sorted(recovered, key=_candidate_key)), tuple(sorted(counterfactual_ids, key=lambda item: item.value))


def _offer_for_candidate(snapshot: CandidateSnapshot, candidate: OfferBackedItineraryCandidate) -> Offer:
    matches = tuple(
        offer
        for offer in snapshot.offers
        if offer.offer_id == candidate.offer_id and offer.itinerary_id == candidate.itinerary_id
    )
    if len(matches) != 1:
        raise DomainInvariantViolation("Counterfactual evaluation requires one canonical Offer per candidate")
    return matches[0]


def _failing_max_price_boundaries(
    constraint: HardConstraint,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
) -> tuple[Money, ...]:
    current = constraint.value
    if not isinstance(current, Money):
        raise DomainInvariantViolation("MAX_PRICE relaxation requires Money value")
    values: list[Money] = []
    for evaluation in _source_fail_evaluations(filter_result, constraint.constraint_id, filter_result.rejected_candidates):
        if evaluation.status is not ConstraintEvaluationStatus.FAIL:
            continue
        feature_value = feature_set.value_for(evaluation.candidate, TOTAL_PRICE)
        if feature_value.value_type is not FeatureValueType.MONEY:
            raise DomainInvariantViolation("MAX_PRICE relaxation requires TOTAL_PRICE Money feature")
        if feature_value.value.state is not ValueState.KNOWN:
            continue
        actual = feature_value.value.value
        if not isinstance(actual, Money):
            raise DomainInvariantViolation("TOTAL_PRICE relaxation feature requires Money")
        if actual.currency == current.currency and actual.amount > current.amount:
            values.append(actual)
    return tuple(
        Money(amount, current.currency)
        for amount in sorted({value.amount for value in values})
    )


def _failing_max_stops_boundaries(
    constraint: HardConstraint,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
) -> tuple[int, ...]:
    current = constraint.value
    if not isinstance(current, StopCount):
        raise DomainInvariantViolation("MAX_STOPS relaxation requires StopCount value")
    values: list[int] = []
    for evaluation in _source_fail_evaluations(filter_result, constraint.constraint_id, filter_result.rejected_candidates):
        if evaluation.status is not ConstraintEvaluationStatus.FAIL:
            continue
        feature_value = feature_set.value_for(evaluation.candidate, STOP_COUNT)
        if feature_value.value_type is not FeatureValueType.INTEGER:
            raise DomainInvariantViolation("MAX_STOPS relaxation requires STOP_COUNT integer feature")
        if feature_value.value.state is not ValueState.KNOWN:
            continue
        actual = feature_value.value.value
        if not isinstance(actual, int) or isinstance(actual, bool):
            raise DomainInvariantViolation("STOP_COUNT relaxation feature requires integer")
        if actual > current.value:
            values.append(actual)
    return tuple(sorted(set(values)))


def _source_fail_evaluations(
    filter_result: CompleteFilterResult,
    constraint_id: ConstraintId,
    candidates: tuple[OfferBackedItineraryCandidate, ...],
) -> tuple[ConstraintEvaluation, ...]:
    candidate_set = frozenset(candidates)
    return tuple(
        sorted(
            (
                evaluation
                for evaluation in filter_result.evaluations
                if evaluation.constraint_id == constraint_id
                and evaluation.candidate in candidate_set
                and evaluation.status is ConstraintEvaluationStatus.FAIL
            ),
            key=lambda evaluation: _candidate_key(evaluation.candidate),
        )
    )


def _bounded_ordered_proposals(
    proposals: tuple[RelaxationProposal, ...],
    policy: RelaxationPolicy,
) -> tuple[RelaxationProposal, ...]:
    non_dominated = _prune_dominated(proposals)
    ordered = tuple(
        sorted(
            non_dominated,
            key=lambda proposal: (
                0 if proposal.proposal_kind is RelaxationProposalKind.SINGLE else 1,
                _threshold_amount(proposal.native_magnitude),
                -len(proposal.recovered_candidates),
                proposal.source_constraint_id.value,
                _threshold_amount(proposal.proposed_value),
                tuple(_candidate_key(candidate) for candidate in proposal.recovered_candidates),
            ),
        )
    )
    singles = [proposal for proposal in ordered if proposal.proposal_kind is RelaxationProposalKind.SINGLE]
    pairwise = [proposal for proposal in ordered if proposal.proposal_kind is RelaxationProposalKind.PAIRWISE]
    bounded = tuple(singles[: policy.max_single_proposals] + pairwise[: policy.max_pairwise_proposals])
    return bounded[: policy.max_total_proposals]


def _prune_dominated(proposals: tuple[RelaxationProposal, ...]) -> tuple[RelaxationProposal, ...]:
    kept: list[RelaxationProposal] = []
    for proposal in proposals:
        proposal_candidates = frozenset(proposal.recovered_candidates)
        if any(
            other is not proposal
            and other.source_constraint_id == proposal.source_constraint_id
            and frozenset(other.recovered_candidates) == proposal_candidates
            and _threshold_amount(other.native_magnitude) <= _threshold_amount(proposal.native_magnitude)
            and (
                _threshold_amount(other.native_magnitude) < _threshold_amount(proposal.native_magnitude)
                or _threshold_amount(other.proposed_value) < _threshold_amount(proposal.proposed_value)
            )
            for other in proposals
        ):
            continue
        kept.append(proposal)
    return tuple(kept)


def _relaxable_max_price_constraints(requirement: RequirementState) -> tuple[HardConstraint, ...]:
    constraints = tuple(
        constraint
        for constraint in requirement.constraints
        if constraint.scope is ConstraintScope.MAX_PRICE
    )
    for constraint in constraints:
        if constraint.operator is not ConstraintOperator.AT_OR_BEFORE or not isinstance(constraint.value, Money):
            raise DomainInvariantViolation("MAX_PRICE relaxation requires AT_OR_BEFORE Money constraint")
    return tuple(sorted(constraints, key=lambda constraint: constraint.constraint_id.value))


def _relaxable_max_stops_constraints(requirement: RequirementState) -> tuple[HardConstraint, ...]:
    constraints = tuple(
        constraint
        for constraint in requirement.constraints
        if constraint.scope is ConstraintScope.MAX_STOPS
    )
    for constraint in constraints:
        if constraint.operator is not ConstraintOperator.AT_OR_BEFORE or not isinstance(constraint.value, StopCount):
            raise DomainInvariantViolation("MAX_STOPS relaxation requires AT_OR_BEFORE StopCount constraint")
    return tuple(sorted(constraints, key=lambda constraint: constraint.constraint_id.value))


def _replace_constraint_value(
    constraint: HardConstraint,
    proposed_value: RelaxationThreshold,
) -> HardConstraint:
    if constraint.scope is ConstraintScope.MAX_PRICE:
        if not isinstance(proposed_value, Money):
            raise DomainInvariantViolation("MAX_PRICE counterfactual requires Money value")
        value = proposed_value
    elif constraint.scope is ConstraintScope.MAX_STOPS:
        if not isinstance(proposed_value, int) or isinstance(proposed_value, bool):
            raise DomainInvariantViolation("MAX_STOPS counterfactual requires integer value")
        value = StopCount(proposed_value)
    else:
        raise DomainInvariantViolation("Unsupported relaxation constraint scope")
    return HardConstraint(
        constraint_id=constraint.constraint_id,
        scope=constraint.scope,
        operator=constraint.operator,
        value=value,
    )


def _is_definitive_filter_empty(filter_result: CompleteFilterResult) -> bool:
    return (
        filter_result.direction is FilterResultDirection.FILTER_EMPTY
        and len(filter_result.qualified_candidates) == 0
        and len(filter_result.uncertain_candidates) == 0
        and len(filter_result.rejected_candidates) > 0
    )


def _result(
    relaxation_result_id: RelaxationResultId,
    relaxation_run_id: RelaxationRunId,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
    relaxation_policy: RelaxationPolicy,
    direction: RelaxationResultDirection,
    reason_code: RelaxationReasonCode,
    proposals: tuple[RelaxationProposal, ...],
) -> RelaxationResult:
    return RelaxationResult(
        relaxation_result_id=relaxation_result_id,
        run_id=relaxation_run_id,
        requirement_id=requirement.requirement_id,
        requirement_version=requirement.version,
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.version,
        derived_feature_set_id=feature_set.feature_set_id.value,
        filter_result_id=filter_result.filter_result_id.value,
        relaxation_policy_version=relaxation_policy.policy_version,
        direction=direction,
        reason_code=reason_code,
        proposals=proposals,
        source_rejected_candidates=filter_result.rejected_candidates,
    )


def _validate_lineage(
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filter_result: CompleteFilterResult,
) -> None:
    if feature_set.input_lineage.requirement_id != requirement.requirement_id:
        raise DomainInvariantViolation("Relaxation feature set requirement lineage mismatch")
    if feature_set.input_lineage.requirement_version != requirement.version:
        raise DomainInvariantViolation("Relaxation feature set requirement version mismatch")
    if feature_set.input_lineage.snapshot_id != snapshot.snapshot_id:
        raise DomainInvariantViolation("Relaxation feature set snapshot lineage mismatch")
    if feature_set.input_lineage.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Relaxation feature set snapshot version mismatch")
    if filter_result.requirement_id != requirement.requirement_id or filter_result.requirement_version != requirement.version:
        raise DomainInvariantViolation("Relaxation FilterResult requirement lineage mismatch")
    if filter_result.snapshot_id != snapshot.snapshot_id or filter_result.snapshot_version != snapshot.version:
        raise DomainInvariantViolation("Relaxation FilterResult snapshot lineage mismatch")
    if filter_result.derived_feature_set_id != feature_set.feature_set_id.value:
        raise DomainInvariantViolation("Relaxation FilterResult feature lineage mismatch")


def _with_order(proposal: RelaxationProposal, proposal_order: int) -> RelaxationProposal:
    return RelaxationProposal(
        proposal_id=proposal.proposal_id,
        proposal_kind=proposal.proposal_kind,
        proposal_order=proposal_order,
        source_constraint_id=proposal.source_constraint_id,
        constraint_scope=proposal.constraint_scope,
        current_value=proposal.current_value,
        proposed_value=proposal.proposed_value,
        native_magnitude=proposal.native_magnitude,
        recovered_candidates=proposal.recovered_candidates,
        source_evaluation_ids=proposal.source_evaluation_ids,
        counterfactual_evaluation_ids=proposal.counterfactual_evaluation_ids,
        reason_code=proposal.reason_code,
        evidence=proposal.evidence,
        relaxation_policy_version=proposal.relaxation_policy_version,
    )


def _candidate_key(candidate: OfferBackedItineraryCandidate) -> str:
    return f"{candidate.offer_id.value}|{candidate.itinerary_id.value}"


def _validate_threshold_delta(
    constraint_scope: ConstraintScope,
    current_value: RelaxationThreshold,
    proposed_value: RelaxationThreshold,
    native_magnitude: RelaxationThreshold,
) -> None:
    if constraint_scope is ConstraintScope.MAX_PRICE:
        if not isinstance(current_value, Money) or not isinstance(proposed_value, Money) or not isinstance(native_magnitude, Money):
            raise DomainInvariantViolation("MAX_PRICE relaxation values must be Money")
        if proposed_value.currency != current_value.currency or native_magnitude.currency != current_value.currency:
            raise DomainInvariantViolation("MAX_PRICE relaxation values must use one currency")
        if proposed_value.amount <= current_value.amount:
            raise DomainInvariantViolation("MAX_PRICE relaxation must increase the threshold")
        if native_magnitude.amount != proposed_value.amount - current_value.amount:
            raise DomainInvariantViolation("MAX_PRICE relaxation magnitude must be native delta")
        return
    if constraint_scope is ConstraintScope.MAX_STOPS:
        if (
            not isinstance(current_value, int)
            or isinstance(current_value, bool)
            or not isinstance(proposed_value, int)
            or isinstance(proposed_value, bool)
            or not isinstance(native_magnitude, int)
            or isinstance(native_magnitude, bool)
        ):
            raise DomainInvariantViolation("MAX_STOPS relaxation values must be integers")
        if proposed_value <= current_value:
            raise DomainInvariantViolation("MAX_STOPS relaxation must increase the threshold")
        if native_magnitude != proposed_value - current_value:
            raise DomainInvariantViolation("MAX_STOPS relaxation magnitude must be native delta")
        return
    raise DomainInvariantViolation("Unsupported relaxation constraint scope")


def _threshold_amount(value: RelaxationThreshold):
    return value.amount if isinstance(value, Money) else value


def _threshold_token(value: RelaxationThreshold) -> str:
    if isinstance(value, Money):
        return f"{value.currency}:{value.amount}"
    return str(value)
