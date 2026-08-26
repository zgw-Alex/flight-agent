from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from flight_agent.domain.decision import (
    CandidateEligibilityStatus,
    CandidatePoolDirection,
    ConstraintEvaluation,
    ConstraintEvaluationId,
    ConstraintEvaluationLineage,
    ConstraintEvaluationScope,
    ConstraintEvaluationStatus,
    ConstraintReasonCode,
    DecisionConstraintScope,
    DecisionPolicySet,
    DecisionPolicyVersion,
    DerivedFeatureSetId,
    EvaluationValueEvidence,
    FilterResultId,
    FilterRunId,
    OfferBackedItineraryCandidate,
    RankingResultId,
    RankingRunId,
    RecommendationRunId,
    RelaxationResultId,
    SegmentSelection,
    aggregate_candidate_eligibility,
    classify_candidate_pool_direction,
    partition_candidate_pool,
)
from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId, SegmentId
from flight_agent.domain.requirements import ConstraintId, RequirementId
from flight_agent.domain.shared import (
    DomainInvariantViolation,
    DomainValue,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource, RecommendationResultId


def test_evaluation_states_are_three_independent_legal_states() -> None:
    assert ConstraintEvaluationStatus.PASS.value == "PASS"
    assert ConstraintEvaluationStatus.FAIL.value == "FAIL"
    assert ConstraintEvaluationStatus.UNKNOWN.value == "UNKNOWN"
    assert ConstraintEvaluationStatus.UNKNOWN is not ConstraintEvaluationStatus.PASS
    assert ConstraintEvaluationStatus.UNKNOWN is not ConstraintEvaluationStatus.FAIL


def test_fail_dominates_candidate_eligibility_aggregation() -> None:
    candidate = candidate_identity()
    result = aggregate_candidate_eligibility(
        candidate,
        (
            evaluation("pass", candidate, ConstraintEvaluationStatus.PASS),
            evaluation("fail", candidate, ConstraintEvaluationStatus.FAIL),
            evaluation("unknown", candidate, ConstraintEvaluationStatus.UNKNOWN),
        ),
    )

    assert result.status is CandidateEligibilityStatus.INELIGIBLE
    assert len(result.evaluations) == 3


def test_all_pass_returns_eligible() -> None:
    candidate = candidate_identity()

    result = aggregate_candidate_eligibility(
        candidate,
        (
            evaluation("pass-1", candidate, ConstraintEvaluationStatus.PASS),
            evaluation("pass-2", candidate, ConstraintEvaluationStatus.PASS),
            evaluation("pass-3", candidate, ConstraintEvaluationStatus.PASS),
        ),
    )

    assert result.status is CandidateEligibilityStatus.ELIGIBLE


def test_unknown_without_fail_returns_unknown_eligibility() -> None:
    candidate = candidate_identity()

    result = aggregate_candidate_eligibility(
        candidate,
        (
            evaluation("pass-1", candidate, ConstraintEvaluationStatus.PASS),
            evaluation("unknown", candidate, ConstraintEvaluationStatus.UNKNOWN),
            evaluation("pass-2", candidate, ConstraintEvaluationStatus.PASS),
        ),
    )

    assert result.status is CandidateEligibilityStatus.UNKNOWN_ELIGIBILITY


def test_zero_applicable_constraints_returns_eligible() -> None:
    result = aggregate_candidate_eligibility(candidate_identity(), ())

    assert result.status is CandidateEligibilityStatus.ELIGIBLE
    assert result.evaluations == ()


def test_unknown_is_not_ineligible_or_eligible() -> None:
    candidate = candidate_identity()

    result = aggregate_candidate_eligibility(
        candidate,
        (evaluation("unknown", candidate, ConstraintEvaluationStatus.UNKNOWN),),
    )

    assert result.status is CandidateEligibilityStatus.UNKNOWN_ELIGIBILITY
    assert result.status is not CandidateEligibilityStatus.INELIGIBLE
    assert result.status is not CandidateEligibilityStatus.ELIGIBLE


def test_evaluation_preserves_constraint_candidate_scope_value_reason_evidence_and_lineage() -> None:
    candidate = candidate_identity()
    constraint_evaluation = evaluation("price", candidate, ConstraintEvaluationStatus.UNKNOWN)

    assert constraint_evaluation.evaluation_id == ConstraintEvaluationId("eval-price")
    assert constraint_evaluation.constraint_id == ConstraintId("constraint-price")
    assert constraint_evaluation.candidate == candidate
    assert constraint_evaluation.scope == ConstraintEvaluationScope(DecisionConstraintScope.OFFER)
    assert constraint_evaluation.expected.value.value == 1200
    assert constraint_evaluation.actual.value.state.name == "NOT_PROVIDED"
    assert constraint_evaluation.reason_code is ConstraintReasonCode.INSUFFICIENT_EVIDENCE
    assert constraint_evaluation.evidence == (
        EvidenceRef(EvidenceSource.OFFER, candidate.offer_id),
        EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId("constraint-price")),
    )
    assert constraint_evaluation.lineage.filter_policy_version == DecisionPolicyVersion(
        "filter-policy-v1"
    )
    assert constraint_evaluation.lineage.filter_run_id == FilterRunId("filter-run-price")


def test_run_identity_artifact_identity_and_business_identity_are_typed_separate_concepts() -> None:
    assert FilterRunId("same") != FilterResultId("same")
    assert RankingRunId("same") != RankingResultId("same")
    assert RecommendationRunId("same") != RecommendationResultId("same")
    assert OfferId("same") != FilterResultId("same")

    artifact_ids = (
        DerivedFeatureSetId("features-1"),
        FilterResultId("filter-result-1"),
        RankingResultId("ranking-result-1"),
        RecommendationResultId("recommendation-result-1"),
        RelaxationResultId("relaxation-result-1"),
    )
    assert len(artifact_ids) == 5


def test_component_policy_versions_are_independently_expressible() -> None:
    policy = DecisionPolicySet(
        derived_feature_policy_version=DecisionPolicyVersion("feature-policy-v1"),
        filter_policy_version=DecisionPolicyVersion("filter-policy-v1"),
        ranking_policy_version=DecisionPolicyVersion("ranking-policy-v1"),
        recommendation_policy_version=DecisionPolicyVersion("recommendation-policy-v1"),
        relaxation_policy_version=DecisionPolicyVersion("relaxation-policy-v1"),
        decision_pipeline_version=DecisionPolicyVersion("pipeline-v1"),
    )

    assert policy.derived_feature_policy_version != policy.filter_policy_version
    assert policy.filter_policy_version != policy.ranking_policy_version
    assert policy.recommendation_policy_version != policy.relaxation_policy_version
    assert policy.decision_pipeline_version == DecisionPolicyVersion("pipeline-v1")


def test_evaluation_and_eligibility_value_objects_are_immutable() -> None:
    candidate = candidate_identity()
    constraint_evaluation = evaluation("immutable", candidate, ConstraintEvaluationStatus.PASS)
    eligibility = aggregate_candidate_eligibility(candidate, (constraint_evaluation,))

    with pytest.raises(FrozenInstanceError):
        constraint_evaluation.status = ConstraintEvaluationStatus.FAIL  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        eligibility.status = CandidateEligibilityStatus.INELIGIBLE  # type: ignore[misc]


def test_canonical_candidate_identity_uses_offer_backed_itinerary_ids_only() -> None:
    candidate = OfferBackedItineraryCandidate(
        offer_id=OfferId("canonical-offer-1"),
        itinerary_id=ItineraryId("canonical-itinerary-1"),
    )

    assert candidate.offer_id == OfferId("canonical-offer-1")
    assert candidate.itinerary_id == ItineraryId("canonical-itinerary-1")
    assert not hasattr(candidate, "provider_raw_id")
    assert not hasattr(candidate, "fixture_index")


def test_constraint_scope_supports_offer_itinerary_and_typed_segment_selection() -> None:
    offer_scope = ConstraintEvaluationScope(DecisionConstraintScope.OFFER)
    itinerary_scope = ConstraintEvaluationScope(DecisionConstraintScope.ITINERARY)
    segment_scope = ConstraintEvaluationScope(
        DecisionConstraintScope.SEGMENT,
        segment_selection=SegmentSelection.ANY_SEGMENT,
        segment_id=SegmentId("segment-1"),
    )

    assert offer_scope.scope is DecisionConstraintScope.OFFER
    assert itinerary_scope.scope is DecisionConstraintScope.ITINERARY
    assert segment_scope.segment_selection is SegmentSelection.ANY_SEGMENT
    with pytest.raises(DomainInvariantViolation):
        ConstraintEvaluationScope(
            DecisionConstraintScope.OFFER,
            segment_selection=SegmentSelection.ALL_SEGMENTS,
        )


def test_candidate_pool_partition_and_filter_empty_boundary() -> None:
    qualified = candidate_identity("qualified")
    uncertain = candidate_identity("uncertain")
    rejected = candidate_identity("rejected")

    partition = partition_candidate_pool(
        (
            aggregate_candidate_eligibility(
                qualified,
                (evaluation("q", qualified, ConstraintEvaluationStatus.PASS),),
            ),
            aggregate_candidate_eligibility(
                uncertain,
                (evaluation("u", uncertain, ConstraintEvaluationStatus.UNKNOWN),),
            ),
            aggregate_candidate_eligibility(
                rejected,
                (evaluation("r", rejected, ConstraintEvaluationStatus.FAIL),),
            ),
        )
    )

    assert partition.qualified == (qualified,)
    assert partition.uncertain == (uncertain,)
    assert partition.rejected == (rejected,)
    assert classify_candidate_pool_direction(candidate_count=3, partition=partition) is (
        CandidatePoolDirection.QUALIFIED_AVAILABLE
    )


def test_filter_empty_requires_no_qualified_or_uncertain_candidates() -> None:
    rejected = candidate_identity("rejected")
    rejected_partition = partition_candidate_pool(
        (
            aggregate_candidate_eligibility(
                rejected,
                (evaluation("r", rejected, ConstraintEvaluationStatus.FAIL),),
            ),
        )
    )
    unresolved_partition = partition_candidate_pool(
        (
            aggregate_candidate_eligibility(
                candidate_identity("unknown"),
                (evaluation("u", candidate_identity("unknown"), ConstraintEvaluationStatus.UNKNOWN),),
            ),
        )
    )

    assert classify_candidate_pool_direction(candidate_count=1, partition=rejected_partition) is (
        CandidatePoolDirection.FILTER_EMPTY
    )
    assert classify_candidate_pool_direction(candidate_count=1, partition=unresolved_partition) is (
        CandidatePoolDirection.QUALIFICATION_UNRESOLVED
    )
    assert classify_candidate_pool_direction(
        candidate_count=0,
        partition=partition_candidate_pool(()),
    ) is CandidatePoolDirection.SEARCH_EMPTY_SOURCE


def test_aggregation_does_not_mutate_input_evaluations() -> None:
    candidate = candidate_identity()
    evaluations = (
        evaluation("pass", candidate, ConstraintEvaluationStatus.PASS),
        evaluation("unknown", candidate, ConstraintEvaluationStatus.UNKNOWN),
    )

    before = evaluations
    result = aggregate_candidate_eligibility(candidate, evaluations)

    assert evaluations == before
    assert result.evaluations == evaluations


def test_aggregation_rejects_evaluations_for_a_different_candidate() -> None:
    with pytest.raises(DomainInvariantViolation):
        aggregate_candidate_eligibility(
            candidate_identity("left"),
            (evaluation("right", candidate_identity("right"), ConstraintEvaluationStatus.PASS),),
        )


def candidate_identity(suffix: str = "candidate") -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(
        offer_id=OfferId(f"offer-{suffix}"),
        itinerary_id=ItineraryId(f"itinerary-{suffix}"),
    )


def evaluation(
    suffix: str,
    candidate: OfferBackedItineraryCandidate,
    status: ConstraintEvaluationStatus,
) -> ConstraintEvaluation:
    return ConstraintEvaluation(
        evaluation_id=ConstraintEvaluationId(f"eval-{suffix}"),
        constraint_id=ConstraintId(f"constraint-{suffix}"),
        candidate=candidate,
        scope=ConstraintEvaluationScope(DecisionConstraintScope.OFFER),
        status=status,
        expected=EvaluationValueEvidence(
            label="max_price",
            value=DomainValue.known(1200),
            evidence=(EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId(f"constraint-{suffix}")),),
        ),
        actual=EvaluationValueEvidence(
            label="canonical_price",
            value=actual_value_for(status),
            evidence=(EvidenceRef(EvidenceSource.OFFER, candidate.offer_id),),
        ),
        reason_code=reason_code_for(status),
        evidence=(
            EvidenceRef(EvidenceSource.OFFER, candidate.offer_id),
            EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId(f"constraint-{suffix}")),
        ),
        lineage=ConstraintEvaluationLineage(
            requirement_id=RequirementId("requirement-1"),
            requirement_version=RequirementVersion(1),
            snapshot_id=CandidateSnapshotId("snapshot-1"),
            snapshot_version=SnapshotVersion(1),
            filter_policy_version=DecisionPolicyVersion("filter-policy-v1"),
            filter_run_id=FilterRunId(f"filter-run-{suffix}"),
        ),
    )


def actual_value_for(status: ConstraintEvaluationStatus) -> DomainValue[object]:
    if status is ConstraintEvaluationStatus.PASS:
        return DomainValue.known(980)
    if status is ConstraintEvaluationStatus.FAIL:
        return DomainValue.known(1350)
    return DomainValue.not_provided()


def reason_code_for(status: ConstraintEvaluationStatus) -> ConstraintReasonCode:
    return {
        ConstraintEvaluationStatus.PASS: ConstraintReasonCode.CONSTRAINT_SATISFIED,
        ConstraintEvaluationStatus.FAIL: ConstraintReasonCode.CONSTRAINT_VIOLATED,
        ConstraintEvaluationStatus.UNKNOWN: ConstraintReasonCode.INSUFFICIENT_EVIDENCE,
    }[status]
