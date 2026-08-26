from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from flight_agent.domain.decision import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    STOP_COUNT,
    TOTAL_PRICE,
    CompleteFilterResult,
    CompleteRankingResult,
    DecisionPolicyVersion,
    DerivedFeatureRunId,
    DerivedFeatureSet,
    DerivedFeatureSetId,
    FeatureKey,
    FeatureValue,
    FeatureValueType,
    FilterResultId,
    FilterRunId,
    NormalizerRegistry,
    OfferBackedItineraryCandidate,
    PoolRelativeFeatureNormalizer,
    PreferenceContributionStatus,
    RankingPreferenceDirection,
    RankingPreferencePolicy,
    RankingResultId,
    RankingRunId,
    RankingViewKind,
    m6_default_complete_filtering_engine,
    m6_default_complete_ranking_engine,
    m6_default_derived_feature_engine,
    m6_default_ranking_policy_set,
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
    SegmentId,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
)
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


def test_qualified_ranking_excludes_uncertain_and_rejected_candidates() -> None:
    result = ranking_result(RankingViewKind.QUALIFIED)

    assert candidate_order(result) == ("offer-cheap|itinerary-cheap", "offer-direct|itinerary-direct")
    assert all(entry.candidate not in filter_result().uncertain_candidates for entry in result.entries)
    assert all(entry.candidate not in filter_result().rejected_candidates for entry in result.entries)


def test_uncertain_ranking_is_separate_and_never_contains_rejected_candidates() -> None:
    result = ranking_result(RankingViewKind.UNCERTAIN)

    assert result.ranking_view_kind is RankingViewKind.UNCERTAIN
    assert candidate_order(result) == ("offer-uncertain|itinerary-uncertain",)
    assert result.entries[0].candidate not in filter_result().qualified_candidates
    assert result.entries[0].candidate not in filter_result().rejected_candidates


def test_rejected_candidate_with_best_price_cannot_resurrect_into_ranking() -> None:
    result = ranking_result(RankingViewKind.QUALIFIED)

    assert "offer-rejected|itinerary-rejected" not in candidate_order(result)


def test_normalization_weight_contribution_and_aggregate_score_are_traceable() -> None:
    result = ranking_result(RankingViewKind.QUALIFIED)
    cheap = result.entries[0]
    by_scope = {contribution.preference_scope: contribution for contribution in cheap.preference_contributions}

    price = by_scope[PreferenceScope.PRICE]
    stops = by_scope[PreferenceScope.FEWER_STOPS]

    assert price.raw_feature_value is not None
    assert price.raw_feature_value.feature_key == TOTAL_PRICE
    assert price.normalized_value.value.value == Decimal(1)
    assert price.resolved_weight == Decimal(2)
    assert price.weighted_contribution.value == Decimal(2)
    assert stops.normalized_value.value.value == Decimal(0)
    assert stops.resolved_weight == Decimal(1)
    assert cheap.aggregate_score == Decimal(2) / Decimal(3)


def test_missing_feature_uses_available_weight_renormalization_and_records_coverage() -> None:
    full = ranking_result(RankingViewKind.QUALIFIED)
    missing_stop = feature_set_without(candidate("offer-cheap", "itinerary-cheap"), STOP_COUNT)
    result = ranking_result(RankingViewKind.QUALIFIED, feature_set=missing_stop)
    cheap = next(entry for entry in result.entries if entry.candidate.offer_id == OfferId("offer-cheap"))

    assert cheap.aggregate_score == Decimal(1)
    assert full.entries[0].aggregate_score != cheap.aggregate_score
    assert cheap.coverage.evaluated_preference_count == 1
    assert cheap.coverage.total_applicable_preference_count == 2
    assert cheap.coverage.evaluated_weight == Decimal(2)
    assert cheap.coverage.total_applicable_weight == Decimal(3)
    assert cheap.coverage.missing_preference_evidence == (PreferenceId("prefer-fewer-stops"),)

    missing = next(
        contribution
        for contribution in cheap.preference_contributions
        if contribution.preference_scope is PreferenceScope.FEWER_STOPS
    )
    assert missing.status is PreferenceContributionStatus.MISSING_EVIDENCE
    assert not missing.normalized_value.value.is_known
    assert not missing.weighted_contribution.is_known


def test_score_tie_breaks_by_coverage_before_canonical_identity() -> None:
    result = ranking_result(
        RankingViewKind.QUALIFIED,
        snapshot=tie_snapshot(),
        feature_set=feature_set_without(candidate("offer-b", "itinerary-b"), STOP_COUNT, snapshot=tie_snapshot()),
    )

    assert candidate_order(result) == ("offer-a|itinerary-a", "offer-b|itinerary-b")
    assert result.entries[0].aggregate_score == result.entries[1].aggregate_score
    assert result.entries[0].coverage.evaluated_preference_coverage > result.entries[1].coverage.evaluated_preference_coverage


def test_score_and_coverage_tie_breaks_by_canonical_candidate_identity_not_input_order() -> None:
    result = ranking_result(RankingViewKind.QUALIFIED, snapshot=reversed_equal_snapshot())

    assert candidate_order(result) == ("offer-a|itinerary-a", "offer-b|itinerary-b")


def test_no_soft_preferences_still_orders_by_stable_canonical_identity() -> None:
    requirement = requirement_with_preferences(())
    result = ranking_result(RankingViewKind.QUALIFIED, requirement=requirement)

    assert candidate_order(result) == (
        "offer-cheap|itinerary-cheap",
        "offer-direct|itinerary-direct",
    )
    assert all(entry.aggregate_score == Decimal(0) for entry in result.entries)
    assert all(entry.coverage.evaluated_preference_coverage == Decimal(1) for entry in result.entries)


def test_max_price_hard_constraint_does_not_enter_soft_ranking_contributions() -> None:
    requirement = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            HardConstraint(
                constraint_id=ConstraintId("departure-date"),
                scope=ConstraintScope.DEPARTURE_DATE,
                operator=ConstraintOperator.EQUALS,
                value=LocalDate(date(2026, 9, 1)),
            ),
            HardConstraint(
                constraint_id=ConstraintId("max-price"),
                scope=ConstraintScope.MAX_PRICE,
                operator=ConstraintOperator.AT_OR_BEFORE,
                value=Money(Decimal(2000), "CNY"),
            ),
        ),
        preferences=(
            SoftPreference(
                preference_id=PreferenceId("prefer-price"),
                scope=PreferenceScope.PRICE,
                importance=PreferenceImportance.HIGH,
            ),
        ),
    )
    snapshot = sample_snapshot()
    feature_set = feature_set_for(snapshot=snapshot, requirement=requirement)
    filtered = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)

    result = ranking_result(
        RankingViewKind.QUALIFIED,
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
    )

    assert all(
        contribution.preference_scope is PreferenceScope.PRICE
        for entry in result.entries
        for contribution in entry.preference_contributions
    )


def test_degenerate_pool_has_deterministic_normalization_without_division_by_zero() -> None:
    result = ranking_result(RankingViewKind.QUALIFIED, snapshot=equal_snapshot())

    assert [entry.aggregate_score for entry in result.entries] == [Decimal(1), Decimal(1)]
    assert candidate_order(result) == ("offer-a|itinerary-a", "offer-b|itinerary-b")
    assert result.pool_relative_normalization[0].min_value == result.pool_relative_normalization[0].max_value


def test_ranking_run_result_lineage_and_immutability() -> None:
    run, result = run_ranking(RankingViewKind.QUALIFIED)

    assert run.run_id == RankingRunId("ranking-run-1")
    assert result.ranking_result_id == RankingResultId("ranking-result-1")
    assert result.run_id == run.run_id
    assert result.requirement_id == RequirementId("requirement-1")
    assert result.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert result.derived_feature_set_id == "feature-set-1"
    assert result.filter_result_id == "filter-result-1"
    assert result.ranking_policy_version == DecisionPolicyVersion("ranking-policy-v1")
    assert run.pool_relative_normalization == result.pool_relative_normalization

    with pytest.raises(FrozenInstanceError):
        result.ranking_view_kind = RankingViewKind.UNCERTAIN  # type: ignore[misc]


def test_ranking_is_non_destructive_local_and_deterministic_replay() -> None:
    requirement = sample_requirement()
    snapshot = sample_snapshot()
    feature_set = feature_set_for(snapshot=snapshot, requirement=requirement)
    filtered = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)

    first = ranking_result(
        RankingViewKind.QUALIFIED,
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
    )
    second = ranking_result(
        RankingViewKind.QUALIFIED,
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
    )

    assert semantic_ranking(first) == semantic_ranking(second)
    assert requirement == sample_requirement()
    assert snapshot == sample_snapshot()
    assert feature_set == feature_set_for(snapshot=snapshot, requirement=requirement)
    assert not hasattr(feature_set.values[0], "normalized_value")
    assert not hasattr(feature_set.values[0], "aggregate_score")
    assert not hasattr(first.entries[0], "recommendation_role")
    assert not hasattr(first.entries[0], "relaxation_proposal")


def test_registry_and_policy_reject_duplicates_and_unsupported_preferences() -> None:
    normalizer = PoolRelativeFeatureNormalizer(
        preference_scope=PreferenceScope.PRICE,
        feature_key=TOTAL_PRICE,
        value_type=FeatureValueType.MONEY,
        normalizer_version=DecisionPolicyVersion("price-v1"),
    )
    with pytest.raises(DomainInvariantViolation, match="unique preference scopes"):
        NormalizerRegistry(
            (normalizer, normalizer),
            registry_version=DecisionPolicyVersion("bad-registry"),
        )

    unsupported_requirement = requirement_with_preferences(
        (
            SoftPreference(
                preference_id=PreferenceId("prefer-departure-time"),
                scope=PreferenceScope.DEPARTURE_TIME,
                importance=PreferenceImportance.MEDIUM,
            ),
        )
    )
    with pytest.raises(DomainInvariantViolation, match="Unsupported ranking preference scope"):
        ranking_result(RankingViewKind.QUALIFIED, requirement=unsupported_requirement)

    with pytest.raises(DomainInvariantViolation, match="unique preference scopes"):
        m6_default_ranking_policy_set().__class__(
            policy_version=DecisionPolicyVersion("bad-policy"),
            preference_policies=(
                RankingPreferencePolicy(
                    PreferenceScope.PRICE,
                    TOTAL_PRICE,
                    RankingPreferenceDirection.LOWER_IS_BETTER,
                    DecisionPolicyVersion("price-v1"),
                ),
                RankingPreferencePolicy(
                    PreferenceScope.PRICE,
                    TOTAL_PRICE,
                    RankingPreferenceDirection.LOWER_IS_BETTER,
                    DecisionPolicyVersion("price-v1"),
                ),
            ),
        )


def run_ranking(
    view: RankingViewKind,
    *,
    requirement: RequirementState | None = None,
    snapshot: CandidateSnapshot | None = None,
    feature_set: DerivedFeatureSet | None = None,
    filtered: CompleteFilterResult | None = None,
) -> tuple:
    resolved_requirement = requirement or sample_requirement()
    resolved_snapshot = snapshot or sample_snapshot()
    resolved_feature_set = feature_set or feature_set_for(
        snapshot=resolved_snapshot,
        requirement=resolved_requirement,
    )
    resolved_filtered = filtered or filter_result(
        snapshot=resolved_snapshot,
        requirement=resolved_requirement,
        feature_set=resolved_feature_set,
    )
    return m6_default_complete_ranking_engine().rank(
        ranking_result_id=RankingResultId("ranking-result-1"),
        ranking_run_id=RankingRunId("ranking-run-1"),
        requirement=resolved_requirement,
        snapshot=resolved_snapshot,
        feature_set=resolved_feature_set,
        filter_result=resolved_filtered,
        ranking_view_kind=view,
        ranking_policy_set=m6_default_ranking_policy_set(),
    )


def ranking_result(view: RankingViewKind, **kwargs) -> CompleteRankingResult:
    return run_ranking(view, **kwargs)[1]


def filter_result(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
    feature_set: DerivedFeatureSet | None = None,
) -> CompleteFilterResult:
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
    )[1]


def feature_set_for(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
) -> DerivedFeatureSet:
    resolved_snapshot = snapshot or sample_snapshot()
    resolved_requirement = requirement or sample_requirement()
    feature_set = m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT),
        snapshot=resolved_snapshot,
        requirement=resolved_requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]
    if any(offer.offer_id == OfferId("offer-uncertain") for offer in resolved_snapshot.offers):
        return feature_set_with_unknown_departure(feature_set)
    return feature_set


def feature_set_without(
    removed_candidate: OfferBackedItineraryCandidate,
    removed_feature_key: FeatureKey,
    *,
    snapshot: CandidateSnapshot | None = None,
) -> DerivedFeatureSet:
    feature_set = feature_set_for(snapshot=snapshot or sample_snapshot())
    return DerivedFeatureSet(
        feature_set_id=feature_set.feature_set_id,
        run_id=feature_set.run_id,
        input_lineage=feature_set.input_lineage,
        feature_definition_versions=feature_set.feature_definition_versions,
        reference_data_versions=feature_set.reference_data_versions,
        values=tuple(
            value
            for value in feature_set.values
            if not (value.candidate == removed_candidate and value.feature_key == removed_feature_key)
        ),
    )


def feature_set_with_unknown_departure(feature_set: DerivedFeatureSet) -> DerivedFeatureSet:
    original = feature_set.value_for(
        candidate("offer-uncertain", "itinerary-uncertain"),
        DEPARTURE_DATE_MATCHES_REQUIREMENT,
    )
    replacement = FeatureValue(
        feature_key=original.feature_key,
        candidate=original.candidate,
        value=DomainValue.not_provided(),
        value_type=original.value_type,
        unit=original.unit,
        evidence=original.evidence,
        canonical_dependencies=original.canonical_dependencies,
        requirement_dependencies=original.requirement_dependencies,
        reference_data_dependencies=original.reference_data_dependencies,
        definition_version=original.definition_version,
    )
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


def sample_requirement() -> RequirementState:
    return requirement_with_preferences(
        (
            SoftPreference(
                preference_id=PreferenceId("prefer-price"),
                scope=PreferenceScope.PRICE,
                importance=PreferenceImportance.HIGH,
            ),
            SoftPreference(
                preference_id=PreferenceId("prefer-fewer-stops"),
                scope=PreferenceScope.FEWER_STOPS,
                importance=PreferenceImportance.MEDIUM,
            ),
        )
    )


def requirement_with_preferences(preferences: tuple[SoftPreference, ...]) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            HardConstraint(
                constraint_id=ConstraintId("departure-date"),
                scope=ConstraintScope.DEPARTURE_DATE,
                operator=ConstraintOperator.EQUALS,
                value=LocalDate(date(2026, 9, 1)),
            ),
        ),
        preferences=preferences,
    )


def sample_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("cheap", date(2026, 9, 1), Decimal(980), 1),
            ("direct", date(2026, 9, 1), Decimal(1080), 0),
            ("rejected", date(2026, 9, 2), Decimal(100), 0),
            ("uncertain", date(2026, 9, 1), Decimal(900), 0),
        ),
        unknown_departure=True,
    )


def tie_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("a", date(2026, 9, 1), Decimal(1000), 0),
            ("b", date(2026, 9, 1), Decimal(1000), 0),
        )
    )


def equal_snapshot() -> CandidateSnapshot:
    return tie_snapshot()


def reversed_equal_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("b", date(2026, 9, 1), Decimal(1000), 0),
            ("a", date(2026, 9, 1), Decimal(1000), 0),
        )
    )


def snapshot_from_specs(
    specs: tuple[tuple[str, date, Decimal, int], ...],
    *,
    unknown_departure: bool = False,
) -> CandidateSnapshot:
    segments: list[FlightSegment] = []
    itineraries: list[Itinerary] = []
    offers: list[Offer] = []
    for suffix, departure_date, price, stop_count in specs:
        segment_ids: list[SegmentId] = []
        departure = departure_date if suffix != "uncertain" or not unknown_departure else date(2026, 9, 1)
        for index in range(stop_count + 1):
            segment = FlightSegment(
                segment_id=SegmentId(f"segment-{suffix}-{index + 1}"),
                marketing_carrier="MU",
                flight_number=f"51{len(segments) + 1:02d}",
                departure_airport="PEK" if index == 0 else "NKG",
                arrival_airport="SHA" if index == stop_count else "NKG",
                departure_at=instant(departure.year, departure.month, departure.day, 8 + index * 3, 30),
                arrival_at=instant(departure.year, departure.month, departure.day, 10 + index * 3, 30),
                operating_carrier=DomainValue.known("MU"),
                aircraft_type=DomainValue.not_provided(),
                provenance=(ProvenanceRef("canonical", f"segment-{suffix}-{index + 1}"),),
            )
            segment_ids.append(segment.segment_id)
            segments.append(segment)
        itinerary = Itinerary(
            itinerary_id=ItineraryId(f"itinerary-{suffix}"),
            segment_ids=tuple(segment_ids),
            provenance=(ProvenanceRef("canonical", f"itinerary-{suffix}"),),
        )
        offer = Offer(
            offer_id=OfferId(f"offer-{suffix}"),
            itinerary_id=itinerary.itinerary_id,
            total_price=Money(price, "CNY"),
            offer_freshness=OfferFreshness(FreshnessState.FRESH),
            booking_reference=DomainValue.known(f"BOOK-{suffix}"),
            provenance=(ProvenanceRef("canonical", f"offer-{suffix}"),),
        )
        itineraries.append(itinerary)
        offers.append(offer)
    snapshot = CandidateSnapshot(
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
    if not unknown_departure:
        return snapshot
    return snapshot


def candidate(offer_id: str, itinerary_id: str) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(OfferId(offer_id), ItineraryId(itinerary_id))


def candidate_order(result: CompleteRankingResult) -> tuple[str, ...]:
    return tuple(
        f"{entry.candidate.offer_id.value}|{entry.candidate.itinerary_id.value}"
        for entry in result.entries
    )


def semantic_ranking(result: CompleteRankingResult) -> tuple:
    return tuple(
        (
            entry.candidate,
            entry.rank_position,
            entry.aggregate_score,
            entry.coverage,
            tuple(
                (
                    contribution.preference_id,
                    contribution.status,
                    contribution.normalized_value.value.state,
                    contribution.normalized_value.value.value
                    if contribution.normalized_value.value.is_known
                    else None,
                )
                for contribution in entry.preference_contributions
            ),
        )
        for entry in result.entries
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
