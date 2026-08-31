from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from flight_agent.domain.decision import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    TOTAL_PRICE,
    CompleteFilterResult,
    ConstraintEvaluation,
    ConstraintReasonCode,
    ConstraintEvaluationStatus,
    DecisionPolicyVersion,
    DerivedFeatureRunId,
    DerivedFeatureSet,
    DerivedFeatureSetId,
    FeatureDefinitionVersion,
    FeatureValue,
    FeatureValueType,
    FilterEvaluatorRegistry,
    FilterResultDirection,
    FilterResultId,
    FilterRunId,
    MaxPriceConstraintEvaluator,
    OfferBackedItineraryCandidate,
    SegmentSelection,
    aggregate_segment_evaluations,
    m6_default_complete_filtering_engine,
    m6_default_derived_feature_engine,
    m6_default_feature_registry,
)
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    Coverage,
    CoverageStatus,
    FlightSegment,
    Itinerary,
    ItineraryId,
    Money,
    Offer,
    OfferId,
    PriceSemantics,
    SegmentId,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PassengerCount,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.search import SearchPlanId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource
from flight_agent.ports import (
    CommonNormalizer,
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    NormalizationContext,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderDataStatus,
    ProviderId,
    ProviderMappingResult,
    ReferenceData,
    ReferenceDataVersion,
)
from flight_agent.ports.provider_mapping import MappingStatistics


def test_departure_date_filter_pass_fail_and_unknown_are_three_valued() -> None:
    result = filter_result()

    statuses = statuses_by_offer(result)

    assert statuses[OfferId("offer-pass")] is ConstraintEvaluationStatus.PASS
    assert statuses[OfferId("offer-fail")] is ConstraintEvaluationStatus.FAIL

    unknown_result = filter_result(feature_set=feature_set_with_unknown_date_for("offer-pass"))
    assert statuses_by_offer(unknown_result)[OfferId("offer-pass")] is ConstraintEvaluationStatus.UNKNOWN
    assert statuses_by_offer(unknown_result)[OfferId("offer-pass")] is not ConstraintEvaluationStatus.FAIL
    assert statuses_by_offer(unknown_result)[OfferId("offer-pass")] is not ConstraintEvaluationStatus.PASS


def test_unsupported_max_price_and_max_stops_are_explicit_failures_not_guesses() -> None:
    with pytest.raises(DomainInvariantViolation, match="Unsupported filter constraint scope"):
        filter_result(requirement=requirement_with(ConstraintScope.PASSENGER_COUNT, passenger_count_constraint()))

    m5_price_preference = SoftPreference(
        preference_id=PreferenceId("structured-lower-price"),
        scope=PreferenceScope.PRICE,
        importance=PreferenceImportance.HIGH,
    )
    requirement = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(departure_date_constraint(),),
        preferences=(m5_price_preference,),
    )
    result = filter_result(requirement=requirement)

    assert len(result.evaluations) == len(sample_snapshot().offers)
    assert all(evaluation.constraint_id == ConstraintId("departure-date") for evaluation in result.evaluations)


def test_max_price_evaluator_pass_boundary_fail_and_currency_unknown() -> None:
    result = filter_result(
        snapshot=max_price_snapshot(),
        requirement=max_price_requirement(Decimal(800)),
        feature_set=feature_set_for_price(max_price_snapshot(), max_price_requirement(Decimal(800))),
    )

    by_offer = evaluations_by_offer(result)

    assert by_offer[OfferId("offer-cheap")].status is ConstraintEvaluationStatus.PASS
    assert by_offer[OfferId("offer-boundary")].status is ConstraintEvaluationStatus.PASS
    assert by_offer[OfferId("offer-expensive")].status is ConstraintEvaluationStatus.FAIL
    assert by_offer[OfferId("offer-cheap")].reason_code is ConstraintReasonCode.MAX_PRICE_SATISFIED
    assert by_offer[OfferId("offer-expensive")].reason_code is ConstraintReasonCode.MAX_PRICE_EXCEEDED
    assert by_offer[OfferId("offer-boundary")].expected.value.value == Money(Decimal(800), "CNY")
    assert by_offer[OfferId("offer-boundary")].actual.value.value == Money(Decimal(800), "CNY")
    assert result.qualified_candidates == (
        candidate("offer-boundary", "itinerary-boundary"),
        candidate("offer-cheap", "itinerary-cheap"),
    )
    assert result.rejected_candidates == (candidate("offer-expensive", "itinerary-expensive"),)

    mismatch = filter_result(
        snapshot=max_price_snapshot(),
        requirement=max_price_requirement(Decimal(800), "USD"),
        feature_set=feature_set_for_price(max_price_snapshot(), max_price_requirement(Decimal(800), "USD")),
    )

    assert all(evaluation.status is ConstraintEvaluationStatus.UNKNOWN for evaluation in mismatch.evaluations)
    assert all(
        evaluation.reason_code is ConstraintReasonCode.MAX_PRICE_INSUFFICIENT_EVIDENCE
        for evaluation in mismatch.evaluations
    )
    assert mismatch.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED


def test_max_price_evaluator_is_evidence_aware_for_price_semantics_truth_table() -> None:
    snapshot = snapshot_from_specs(
        (
            ("exact-cheap", date(2026, 9, 1), Decimal(791), PriceSemantics.EXACT),
            ("exact-expensive", date(2026, 9, 1), Decimal(850), PriceSemantics.EXACT),
            ("lower-cheap", date(2026, 9, 1), Decimal(791), PriceSemantics.LOWER_BOUND),
            ("lower-expensive", date(2026, 9, 1), Decimal(850), PriceSemantics.LOWER_BOUND),
        )
    )
    requirement = max_price_requirement(Decimal(800))

    result = filter_result(
        snapshot=snapshot,
        requirement=requirement,
        feature_set=feature_set_for_price(snapshot, requirement),
    )

    assert statuses_by_offer(result) == {
        OfferId("offer-exact-cheap"): ConstraintEvaluationStatus.PASS,
        OfferId("offer-exact-expensive"): ConstraintEvaluationStatus.FAIL,
        OfferId("offer-lower-cheap"): ConstraintEvaluationStatus.UNKNOWN,
        OfferId("offer-lower-expensive"): ConstraintEvaluationStatus.FAIL,
    }
    assert candidate("offer-lower-cheap", "itinerary-lower-cheap") in result.uncertain_candidates
    assert candidate("offer-lower-expensive", "itinerary-lower-expensive") in result.rejected_candidates


def test_max_price_evaluator_preserves_exact_boundary_and_keeps_lower_bound_boundary_unknown() -> None:
    snapshot = snapshot_from_specs(
        (
            ("exact-boundary", date(2026, 9, 1), Decimal(800), PriceSemantics.EXACT),
            ("lower-boundary", date(2026, 9, 1), Decimal(800), PriceSemantics.LOWER_BOUND),
        )
    )
    requirement = max_price_requirement(Decimal(800))

    result = filter_result(
        snapshot=snapshot,
        requirement=requirement,
        feature_set=feature_set_for_price(snapshot, requirement),
    )

    assert statuses_by_offer(result)[OfferId("offer-exact-boundary")] is ConstraintEvaluationStatus.PASS
    assert statuses_by_offer(result)[OfferId("offer-lower-boundary")] is ConstraintEvaluationStatus.UNKNOWN
    assert evaluations_by_offer(result)[OfferId("offer-lower-boundary")].reason_code is (
        ConstraintReasonCode.MAX_PRICE_INSUFFICIENT_EVIDENCE
    )


def test_m4_lower_bound_normalized_offer_reaches_m6_max_price_without_semantic_loss() -> None:
    normalized = CommonNormalizer().normalize(
        ProviderMappingResult(
            provider_id=ProviderId("provider-a"),
            acquisition_id=ProviderAcquisitionId("acquisition-a"),
            search_plan_id=SearchPlanId("search-plan-a"),
            mapper_version=MapperVersion("mapper-v1"),
            data_status=ProviderDataStatus.COMPLETE,
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a", price_semantics=PriceSemantics.LOWER_BOUND),),
            issues=(),
            statistics=MappingStatistics(
                raw_segment_count=1,
                mapped_segment_count=1,
                dropped_segment_count=0,
                raw_itinerary_count=1,
                mapped_itinerary_count=1,
                dropped_itinerary_count=0,
                raw_offer_count=1,
                mapped_offer_count=1,
                dropped_offer_count=0,
                issue_count=0,
            ),
        ),
        NormalizationContext(
            normalizer_version=NormalizerVersion("normalizer-v1"),
            reference_data=ReferenceData(
                ReferenceDataVersion("reference-data-v1"),
                frozenset({"PEK", "SHA"}),
                frozenset({"MU"}),
            ),
        ),
    )
    snapshot = CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(2026, 8, 26, 8, 0),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("requested", "actual", CoverageStatus.COMPLETE),
        segments=normalized.segments,
        itineraries=normalized.itineraries,
        offers=normalized.offers,
    )
    requirement = max_price_requirement(Decimal(800))

    result = filter_result(
        snapshot=snapshot,
        requirement=requirement,
        feature_set=feature_set_for_price(snapshot, requirement),
    )

    assert normalized.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert statuses_by_offer(result)[OfferId("normalized-offer:mapped-offer:offer-a")] is (
        ConstraintEvaluationStatus.UNKNOWN
    )


def test_max_price_unknown_feature_is_uncertain_not_filter_empty() -> None:
    snapshot = max_price_snapshot()
    requirement = max_price_requirement(Decimal(800))
    feature_set = feature_set_with_unknown_total_price(
        feature_set_for_price(snapshot, requirement),
        candidate("offer-expensive", "itinerary-expensive"),
    )

    result = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)

    assert evaluations_by_offer(result)[OfferId("offer-expensive")].status is ConstraintEvaluationStatus.UNKNOWN
    assert candidate("offer-expensive", "itinerary-expensive") in result.uncertain_candidates
    assert result.direction is FilterResultDirection.QUALIFIED_AVAILABLE


def test_max_price_definitive_filter_empty_foundation_is_real_and_snapshot_preserving() -> None:
    snapshot = snapshot_from_specs(
        (
            ("a", date(2026, 9, 1), Decimal(850)),
            ("b", date(2026, 9, 1), Decimal(920)),
        )
    )
    requirement = max_price_requirement(Decimal(800))
    feature_set = feature_set_for_price(snapshot, requirement)
    before_snapshot = snapshot
    before_requirement = requirement
    before_feature_set = feature_set

    result = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)

    assert len(snapshot.offers) == 2
    assert result.qualified_candidates == ()
    assert result.uncertain_candidates == ()
    assert result.rejected_candidates == (
        candidate("offer-a", "itinerary-a"),
        candidate("offer-b", "itinerary-b"),
    )
    assert result.direction is FilterResultDirection.FILTER_EMPTY
    assert all(evaluation.status is ConstraintEvaluationStatus.FAIL for evaluation in result.evaluations)
    assert snapshot == before_snapshot
    assert requirement == before_requirement
    assert feature_set == before_feature_set


def test_max_price_preserves_full_evaluation_with_departure_date_and_is_deterministic() -> None:
    snapshot = max_price_snapshot()
    requirement = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            departure_date_constraint(),
            max_price_constraint(Decimal(800)),
        ),
    )
    feature_set = m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT, TOTAL_PRICE),
        snapshot=snapshot,
        requirement=requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]

    first = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)
    second = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)
    expensive = [
        evaluation
        for evaluation in first.evaluations
        if evaluation.candidate.offer_id == OfferId("offer-expensive")
    ]

    assert semantic_filter_result(first) == semantic_filter_result(second)
    assert {evaluation.constraint_id for evaluation in expensive} == {
        ConstraintId("departure-date"),
        ConstraintId("max-price"),
    }
    assert any(evaluation.status is ConstraintEvaluationStatus.PASS for evaluation in expensive)
    assert any(evaluation.status is ConstraintEvaluationStatus.FAIL for evaluation in expensive)
    assert candidate("offer-expensive", "itinerary-expensive") in first.rejected_candidates


def test_max_price_registry_and_threshold_invariants_are_stable() -> None:
    registry = m6_default_complete_filtering_engine().evaluator_registry

    assert isinstance(registry.evaluator_for(max_price_constraint(Decimal(800))), MaxPriceConstraintEvaluator)

    snapshot = max_price_snapshot()
    tight = filter_result(
        snapshot=snapshot,
        requirement=max_price_requirement(Decimal(700)),
        feature_set=feature_set_for_price(snapshot, max_price_requirement(Decimal(700))),
    )
    relaxed = filter_result(
        snapshot=snapshot,
        requirement=max_price_requirement(Decimal(900)),
        feature_set=feature_set_for_price(snapshot, max_price_requirement(Decimal(900))),
    )

    assert statuses_by_offer(tight)[OfferId("offer-expensive")] is ConstraintEvaluationStatus.FAIL
    assert statuses_by_offer(relaxed)[OfferId("offer-expensive")] is ConstraintEvaluationStatus.PASS
    assert statuses_by_offer(relaxed)[OfferId("offer-boundary")] is ConstraintEvaluationStatus.PASS


def test_multiple_constraints_preserve_full_evidence_even_when_one_fails() -> None:
    requirement = requirement_with(
        ConstraintScope.DEPARTURE_DATE,
        departure_date_constraint(),
        duplicate_departure_date_constraint(),
    )

    with pytest.raises(DomainInvariantViolation, match="one departure date constraint"):
        filter_result(requirement=requirement)

    result = filter_result()

    assert len(result.evaluations) == len(sample_snapshot().offers)
    assert all(evaluation.evidence for evaluation in result.evaluations)
    assert all(evaluation.expected.label == "departure_date" for evaluation in result.evaluations)
    assert all(evaluation.actual.label == "departure_date_matches_requirement" for evaluation in result.evaluations)


def test_candidate_eligibility_aggregation_and_partition_are_stable() -> None:
    result = filter_result()

    assert result.direction is FilterResultDirection.QUALIFIED_AVAILABLE
    assert result.qualified_candidates == (candidate("offer-pass", "itinerary-pass"),)
    assert result.rejected_candidates == (candidate("offer-fail", "itinerary-fail"),)
    assert result.uncertain_candidates == ()

    statuses = {eligibility.candidate.offer_id: eligibility.status for eligibility in result.candidate_eligibilities}
    assert statuses[OfferId("offer-pass")].value == "ELIGIBLE"
    assert statuses[OfferId("offer-fail")].value == "INELIGIBLE"


def test_unknown_candidate_is_uncertain_and_not_filter_empty() -> None:
    result = filter_result(feature_set=feature_set_with_unknown_date_for("offer-pass"))

    assert result.uncertain_candidates == (candidate("offer-pass", "itinerary-pass"),)
    assert result.rejected_candidates == (candidate("offer-fail", "itinerary-fail"),)
    assert result.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED


def test_all_pass_all_fail_and_zero_constraint_semantics() -> None:
    all_pass = filter_result(snapshot=all_pass_snapshot(), feature_set=all_pass_feature_set())
    assert len(all_pass.qualified_candidates) == 2
    assert all_pass.direction is FilterResultDirection.QUALIFIED_AVAILABLE

    all_fail = filter_result(snapshot=all_fail_snapshot(), feature_set=all_fail_feature_set())
    assert len(all_fail.rejected_candidates) == 2
    assert all_fail.direction is FilterResultDirection.FILTER_EMPTY

    no_constraints = filter_result(requirement=no_constraint_requirement())
    assert no_constraints.evaluations == ()
    assert len(no_constraints.qualified_candidates) == 2
    assert no_constraints.direction is FilterResultDirection.QUALIFIED_AVAILABLE


def test_search_empty_source_is_distinct_from_filter_empty() -> None:
    snapshot = empty_snapshot()
    feature_set = feature_set_for(snapshot=snapshot, requirement=sample_requirement())

    result = filter_result(snapshot=snapshot, feature_set=feature_set)

    assert result.evaluations == ()
    assert result.candidate_eligibilities == ()
    assert result.direction is FilterResultDirection.SEARCH_EMPTY_SOURCE


def test_duplicate_evaluator_registration_fails() -> None:
    registry = m6_default_complete_filtering_engine().evaluator_registry
    evaluator = registry.evaluators[0]

    with pytest.raises(DomainInvariantViolation, match="unique constraint scopes"):
        FilterEvaluatorRegistry(
            (evaluator, evaluator),
            registry_version=DecisionPolicyVersion("duplicate-registry"),
        )


def test_scope_any_and_all_semantics() -> None:
    assert aggregate_segment_evaluations(
        (
            ConstraintEvaluationStatus.FAIL,
            ConstraintEvaluationStatus.UNKNOWN,
            ConstraintEvaluationStatus.PASS,
        ),
        SegmentSelection.ANY_SEGMENT,
    ) is ConstraintEvaluationStatus.PASS
    assert aggregate_segment_evaluations(
        (ConstraintEvaluationStatus.FAIL, ConstraintEvaluationStatus.UNKNOWN),
        SegmentSelection.ANY_SEGMENT,
    ) is ConstraintEvaluationStatus.UNKNOWN
    assert aggregate_segment_evaluations(
        (ConstraintEvaluationStatus.PASS, ConstraintEvaluationStatus.UNKNOWN),
        SegmentSelection.ALL_SEGMENTS,
    ) is ConstraintEvaluationStatus.UNKNOWN
    assert aggregate_segment_evaluations(
        (ConstraintEvaluationStatus.PASS, ConstraintEvaluationStatus.FAIL),
        SegmentSelection.ALL_SEGMENTS,
    ) is ConstraintEvaluationStatus.FAIL


def test_filter_run_result_lineage_and_immutability() -> None:
    run, result = run_filter()

    assert run.run_id == FilterRunId("filter-run-1")
    assert result.filter_result_id == FilterResultId("filter-result-1")
    assert result.run_id == run.run_id
    assert result.requirement_id == RequirementId("requirement-1")
    assert result.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert result.derived_feature_set_id == "feature-set-1"
    assert result.filter_policy_version == DecisionPolicyVersion("filter-policy-v1")

    with pytest.raises(FrozenInstanceError):
        result.direction = FilterResultDirection.FILTER_EMPTY  # type: ignore[misc]


def test_filtering_is_non_destructive_and_deterministic() -> None:
    snapshot = sample_snapshot()
    requirement = sample_requirement()
    feature_set = feature_set_for(snapshot=snapshot, requirement=requirement)
    before_snapshot = snapshot
    before_requirement = requirement
    before_feature_set = feature_set

    first = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)
    second = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)

    assert semantic_filter_result(first) == semantic_filter_result(second)
    assert snapshot == before_snapshot
    assert requirement == before_requirement
    assert feature_set == before_feature_set
    assert not hasattr(first, "ranking_score")
    assert not hasattr(first, "recommendation_role")
    assert not hasattr(first, "relaxation_proposal")


def test_wrong_feature_type_and_lineage_are_programming_failures() -> None:
    bad_value = FeatureValue(
        feature_key=DEPARTURE_DATE_MATCHES_REQUIREMENT,
        candidate=candidate("offer-pass", "itinerary-pass"),
        value=DomainValue.known(Money(Decimal(980), "CNY")),
        value_type=FeatureValueType.MONEY,
        evidence=(EvidenceRef(EvidenceSource.OFFER, OfferId("offer-pass")),),
        canonical_dependencies=(
            m6_default_feature_registry().get(DEPARTURE_DATE_MATCHES_REQUIREMENT).canonical_dependencies
        ),
        requirement_dependencies=(
            m6_default_feature_registry().get(DEPARTURE_DATE_MATCHES_REQUIREMENT).requirement_dependencies
        ),
        definition_version=FeatureDefinitionVersion("departure-date-match-v1"),
    )
    feature_set = replace_feature_value(feature_set_for(), bad_value)

    with pytest.raises(DomainInvariantViolation, match="wrong feature value type"):
        filter_result(feature_set=feature_set)

    wrong_lineage = DerivedFeatureSet(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        input_lineage=feature_set.input_lineage.__class__(
            snapshot_id=CandidateSnapshotId("other-snapshot"),
            snapshot_version=SnapshotVersion(1),
            requirement_id=RequirementId("requirement-1"),
            requirement_version=RequirementVersion(1),
        ),
        feature_definition_versions=feature_set.feature_definition_versions,
        values=feature_set.values,
    )
    with pytest.raises(DomainInvariantViolation, match="snapshot lineage"):
        filter_result(feature_set=wrong_lineage)


def run_filter(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
    feature_set: DerivedFeatureSet | None = None,
) -> tuple:
    resolved_snapshot = snapshot or sample_snapshot()
    resolved_requirement = requirement or sample_requirement()
    resolved_feature_set = feature_set or feature_set_for(
        snapshot=resolved_snapshot,
        requirement=resolved_requirement,
    )
    return m6_default_complete_filtering_engine().filter(
        filter_result_id=FilterResultId("filter-result-1"),
        filter_run_id=FilterRunId("filter-run-1"),
        requirement=resolved_requirement,
        snapshot=resolved_snapshot,
        feature_set=resolved_feature_set,
        filter_policy_version=DecisionPolicyVersion("filter-policy-v1"),
    )


def filter_result(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
    feature_set: DerivedFeatureSet | None = None,
) -> CompleteFilterResult:
    return run_filter(snapshot=snapshot, requirement=requirement, feature_set=feature_set)[1]


def feature_set_for(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
) -> DerivedFeatureSet:
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT,),
        snapshot=snapshot or sample_snapshot(),
        requirement=requirement or sample_requirement(),
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]


def feature_set_with_unknown_date_for(offer_id: str) -> DerivedFeatureSet:
    feature_set = feature_set_for()
    original = feature_set.value_for(
        candidate(offer_id, "itinerary-pass" if offer_id == "offer-pass" else "itinerary-fail"),
        DEPARTURE_DATE_MATCHES_REQUIREMENT,
    )
    unknown = FeatureValue(
        feature_key=original.feature_key,
        candidate=original.candidate,
        value=DomainValue.not_provided(),
        value_type=original.value_type,
        evidence=original.evidence,
        canonical_dependencies=original.canonical_dependencies,
        requirement_dependencies=original.requirement_dependencies,
        reference_data_dependencies=original.reference_data_dependencies,
        definition_version=original.definition_version,
    )
    return replace_feature_value(feature_set, unknown)


def feature_set_with_unknown_total_price(
    feature_set: DerivedFeatureSet,
    target_candidate: OfferBackedItineraryCandidate,
) -> DerivedFeatureSet:
    original = feature_set.value_for(target_candidate, TOTAL_PRICE)
    unknown = FeatureValue(
        feature_key=original.feature_key,
        candidate=original.candidate,
        value=DomainValue.not_provided(),
        value_type=original.value_type,
        evidence=original.evidence,
        canonical_dependencies=original.canonical_dependencies,
        requirement_dependencies=original.requirement_dependencies,
        reference_data_dependencies=original.reference_data_dependencies,
        definition_version=original.definition_version,
    )
    return replace_feature_value(feature_set, unknown)


def replace_feature_value(feature_set: DerivedFeatureSet, replacement: FeatureValue) -> DerivedFeatureSet:
    return DerivedFeatureSet(
        feature_set_id=feature_set.feature_set_id,
        run_id=feature_set.run_id,
        input_lineage=feature_set.input_lineage,
        feature_definition_versions=feature_set.feature_definition_versions,
        reference_data_versions=feature_set.reference_data_versions,
        values=tuple(
            replacement
            if value.candidate == replacement.candidate and value.feature_key == replacement.feature_key
            else value
            for value in feature_set.values
        ),
    )


def statuses_by_offer(result: CompleteFilterResult) -> dict[OfferId, ConstraintEvaluationStatus]:
    return {evaluation.candidate.offer_id: evaluation.status for evaluation in result.evaluations}


def evaluations_by_offer(result: CompleteFilterResult) -> dict[OfferId, ConstraintEvaluation]:
    return {evaluation.candidate.offer_id: evaluation for evaluation in result.evaluations}


def semantic_filter_result(result: CompleteFilterResult) -> tuple:
    return (
        tuple((evaluation.candidate, evaluation.constraint_id, evaluation.status) for evaluation in result.evaluations),
        result.partition,
        result.direction,
    )


def requirement_with(
    _scope: ConstraintScope,
    *constraints: HardConstraint,
) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=tuple(constraints),
    )


def sample_requirement() -> RequirementState:
    return requirement_with(ConstraintScope.DEPARTURE_DATE, departure_date_constraint())


def no_constraint_requirement() -> RequirementState:
    return requirement_with(ConstraintScope.DEPARTURE_DATE)


def departure_date_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("departure-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(2026, 9, 1)),
    )


def duplicate_departure_date_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("departure-date-duplicate"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(2026, 9, 1)),
    )


def passenger_count_constraint() -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("passenger-count"),
        scope=ConstraintScope.PASSENGER_COUNT,
        operator=ConstraintOperator.EQUALS,
        value=PassengerCount(1),
    )


def max_price_requirement(amount: Decimal, currency: str = "CNY") -> RequirementState:
    return requirement_with(ConstraintScope.MAX_PRICE, max_price_constraint(amount, currency))


def max_price_constraint(amount: Decimal, currency: str = "CNY") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("max-price"),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(amount, currency),
    )


def feature_set_for_price(snapshot: CandidateSnapshot, requirement: RequirementState) -> DerivedFeatureSet:
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(TOTAL_PRICE,),
        snapshot=snapshot,
        requirement=requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]


def sample_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("pass", date(2026, 9, 1), Decimal(980)),
            ("fail", date(2026, 9, 2), Decimal(1080)),
        )
    )


def max_price_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("cheap", date(2026, 9, 1), Decimal(700)),
            ("boundary", date(2026, 9, 1), Decimal(800)),
            ("expensive", date(2026, 9, 1), Decimal(850)),
        )
    )


def all_pass_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("pass-a", date(2026, 9, 1), Decimal(980)),
            ("pass-b", date(2026, 9, 1), Decimal(1080)),
        )
    )


def all_fail_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("fail-a", date(2026, 9, 2), Decimal(980)),
            ("fail-b", date(2026, 9, 3), Decimal(1080)),
        )
    )


def all_pass_feature_set() -> DerivedFeatureSet:
    return feature_set_for(snapshot=all_pass_snapshot(), requirement=sample_requirement())


def all_fail_feature_set() -> DerivedFeatureSet:
    return feature_set_for(snapshot=all_fail_snapshot(), requirement=sample_requirement())


def empty_snapshot() -> CandidateSnapshot:
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(2026, 8, 26, 8, 0),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("requested", "actual", CoverageStatus.COMPLETE),
    )


def snapshot_from_specs(
    specs: tuple[tuple[str, date, Decimal] | tuple[str, date, Decimal, PriceSemantics], ...],
) -> CandidateSnapshot:
    segments = []
    itineraries = []
    offers = []
    for spec in specs:
        suffix, departure_date, price = spec[:3]
        price_semantics = spec[3] if len(spec) == 4 else PriceSemantics.EXACT
        first = FlightSegment(
            segment_id=SegmentId(f"segment-{suffix}-1"),
            marketing_carrier="MU",
            flight_number=f"51{len(segments) + 1:02d}",
            departure_airport="PEK",
            arrival_airport="NKG",
            departure_at=instant(departure_date.year, departure_date.month, departure_date.day, 8, 30),
            arrival_at=instant(departure_date.year, departure_date.month, departure_date.day, 10, 30),
            operating_carrier=DomainValue.known("MU"),
            aircraft_type=DomainValue.not_provided(),
            provenance=(ProvenanceRef("canonical", f"segment-{suffix}-1"),),
        )
        second = FlightSegment(
            segment_id=SegmentId(f"segment-{suffix}-2"),
            marketing_carrier="MU",
            flight_number=f"52{len(segments) + 1:02d}",
            departure_airport="NKG",
            arrival_airport="SHA",
            departure_at=instant(departure_date.year, departure_date.month, departure_date.day, 11, 30),
            arrival_at=instant(departure_date.year, departure_date.month, departure_date.day, 13, 30),
            operating_carrier=DomainValue.known("MU"),
            aircraft_type=DomainValue.not_provided(),
            provenance=(ProvenanceRef("canonical", f"segment-{suffix}-2"),),
        )
        itinerary = Itinerary(
            itinerary_id=ItineraryId(f"itinerary-{suffix}"),
            segment_ids=(first.segment_id, second.segment_id),
            provenance=(ProvenanceRef("canonical", f"itinerary-{suffix}"),),
        )
        offer = Offer(
            offer_id=OfferId(f"offer-{suffix}"),
            itinerary_id=itinerary.itinerary_id,
            total_price=Money(price, "CNY"),
            offer_freshness=OfferFreshness(FreshnessState.FRESH),
            booking_reference=DomainValue.known(f"BOOK-{suffix}"),
            provenance=(ProvenanceRef("canonical", f"offer-{suffix}"),),
            price_semantics=price_semantics,
        )
        segments.extend((first, second))
        itineraries.append(itinerary)
        offers.append(offer)
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(2026, 8, 26, 8, 0),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("requested", "actual", CoverageStatus.COMPLETE),
        segments=tuple(segments),
        itineraries=tuple(itineraries),
        offers=tuple(offers),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def candidate(offer_suffix: str, itinerary_suffix: str) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(
        offer_id=OfferId(offer_suffix),
        itinerary_id=ItineraryId(itinerary_suffix),
    )


def mapped_provenance(source: str) -> MappedProvenance:
    return MappedProvenance(
        provider_id=ProviderId("provider-a"),
        acquisition_id=ProviderAcquisitionId("acquisition-a"),
        raw_evidence_refs=(f"{source}-response",),
        raw_record_ref=source,
        provider_source_id=source,
    )


def mapped_segment(source: str) -> MappedSegment:
    return MappedSegment(
        mapped_segment_ref=MappedSegmentRef(f"mapped-segment:{source}"),
        provider_segment_id=source,
        marketing_carrier="MU",
        flight_number="MU583",
        departure_airport="PEK",
        arrival_airport="SHA",
        departure_local="2026-09-01T08:00:00+00:00",
        arrival_local="2026-09-01T10:00:00+00:00",
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.not_provided(),
        checked_baggage_pieces=DomainValue.not_provided(),
        overnight=DomainValue.not_provided(),
        provenance=mapped_provenance(source),
    )


def mapped_itinerary(source: str, segment_sources: tuple[str, ...]) -> MappedItinerary:
    return MappedItinerary(
        mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{source}"),
        provider_itinerary_id=source,
        segment_refs=tuple(MappedSegmentRef(f"mapped-segment:{item}") for item in segment_sources),
        provenance=mapped_provenance(source),
    )


def mapped_offer(
    source: str,
    itinerary_source: str,
    *,
    price_semantics: PriceSemantics,
) -> MappedOffer:
    return MappedOffer(
        mapped_offer_ref=MappedOfferRef(f"mapped-offer:{source}"),
        provider_offer_id=source,
        itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{itinerary_source}"),
        total_amount=DomainValue.known(791),
        currency=DomainValue.known("CNY"),
        refundable=DomainValue.not_provided(),
        booking_reference=DomainValue.not_applicable(),
        provenance=mapped_provenance(source),
        price_semantics=price_semantics,
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
