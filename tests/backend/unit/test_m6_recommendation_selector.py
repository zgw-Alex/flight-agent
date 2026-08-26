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
    FilterResultId,
    FilterRunId,
    OfferBackedItineraryCandidate,
    RankingResultId,
    RankingRunId,
    RankingViewKind,
    RecommendationPolicy,
    RecommendationRunId,
    m6_default_complete_filtering_engine,
    m6_default_complete_ranking_engine,
    m6_default_complete_recommendation_selector,
    m6_default_derived_feature_engine,
    m6_default_ranking_policy_set,
    m6_default_recommendation_policy,
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
from flight_agent.domain.workflow import (
    CandidateComparison,
    ExecutionId,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def test_best_overall_anchor_and_cheapest_role_merge_when_same_candidate() -> None:
    result = recommendation_result(snapshot=same_best_and_cheapest_snapshot())

    assert len(result.items) == 1
    assert result.items[0].primary_offer_id == OfferId("offer-a")
    assert result.items[0].roles == (RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAPEST)
    assert result.items[0].source_rank == 1
    assert result.items[0].selection_order == 1


def test_empty_qualified_pool_produces_no_normal_recommendation_items() -> None:
    result = recommendation_result(snapshot=all_rejected_snapshot())

    assert result.status is RecommendationResultStatus.NO_MATCH
    assert result.items == ()
    assert result.candidate_comparisons == ()


def test_rejected_and_uncertain_candidates_are_never_selected_or_used_to_fill_target() -> None:
    policy = RecommendationPolicy(
        policy_version=DecisionPolicyVersion("recommendation-policy-test"),
        target_count=3,
        max_count=3,
        enabled_roles=(RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAPEST),
        redundancy_predicate_version=DecisionPolicyVersion("canonical-candidate-dedup-test"),
    )
    result = recommendation_result(policy=policy)
    qualified = frozenset(filter_result().qualified_candidates)

    assert {candidate_key(item) for item in result.items} == {
        "offer-a|itinerary-a",
        "offer-c|itinerary-c",
    }
    assert all(item_candidate(item) in qualified for item in result.items)
    assert "offer-rejected|itinerary-rejected" not in {candidate_key(item) for item in result.items}
    assert "offer-uncertain|itinerary-uncertain" not in {candidate_key(item) for item in result.items}
    assert result.target_count is not None
    assert len(result.items) < result.target_count


def test_selector_is_not_top_n_and_preserves_source_rank_vs_selection_order() -> None:
    ranked = ranking_result()
    result = recommendation_result(ranked=ranked)

    assert ranked_order(ranked)[:2] == ("offer-a|itinerary-a", "offer-b|itinerary-b")
    assert {candidate_key(item) for item in result.items} == {
        "offer-a|itinerary-a",
        "offer-c|itinerary-c",
    }
    cheapest = next(item for item in result.items if item.primary_offer_id == OfferId("offer-c"))
    assert cheapest.roles == (RecommendationRole.CHEAPEST,)
    assert cheapest.source_rank == 3
    assert cheapest.selection_order == 2


def test_cheapest_activates_only_with_price_preference_and_known_comparable_price() -> None:
    no_price = recommendation_result(requirement=requirement_with_preferences((fewer_stops_preference(),)))

    assert {role for item in no_price.items for role in item.roles} == {RecommendationRole.BEST_OVERALL}

    missing_price = feature_set_without(candidate("offer-c", "itinerary-c"), TOTAL_PRICE)
    result = recommendation_result(feature_set=missing_price)

    assert all(
        item.primary_offer_id != OfferId("offer-c") or RecommendationRole.CHEAPEST not in item.roles
        for item in result.items
    )


def test_max_count_is_hard_and_anchor_cannot_be_removed() -> None:
    policy = RecommendationPolicy(
        policy_version=DecisionPolicyVersion("recommendation-policy-test"),
        target_count=1,
        max_count=1,
        enabled_roles=(RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAPEST),
        redundancy_predicate_version=DecisionPolicyVersion("canonical-candidate-dedup-test"),
    )
    result = recommendation_result(policy=policy)

    assert len(result.items) == 1
    assert result.items[0].primary_offer_id == OfferId("offer-a")
    assert result.items[0].roles == (RecommendationRole.BEST_OVERALL,)


def test_non_supported_roles_and_fallback_are_not_auto_activated() -> None:
    result = recommendation_result(
        policy=RecommendationPolicy(
            policy_version=DecisionPolicyVersion("recommendation-policy-test"),
            target_count=2,
            max_count=3,
            enabled_roles=(
                RecommendationRole.BEST_OVERALL,
                RecommendationRole.CHEAPEST,
                RecommendationRole.EARLIEST_ARRIVAL,
                RecommendationRole.BEST_DEPARTURE_TIME,
                RecommendationRole.BEST_AIRPORT_MATCH,
                RecommendationRole.FALLBACK,
            ),
            redundancy_predicate_version=DecisionPolicyVersion("canonical-candidate-dedup-test"),
        )
    )

    roles = {role for item in result.items for role in item.roles}
    assert RecommendationRole.EARLIEST_ARRIVAL not in roles
    assert RecommendationRole.BEST_DEPARTURE_TIME not in roles
    assert RecommendationRole.BEST_AIRPORT_MATCH not in roles
    assert RecommendationRole.FALLBACK not in roles


def test_candidate_comparison_is_generated_after_selection_without_second_score() -> None:
    result = recommendation_result()

    assert result.candidate_comparisons == (
        CandidateComparison(
            left_offer_id=OfferId("offer-a"),
            right_offer_id=OfferId("offer-c"),
            price_difference="-100 CNY",
            stop_count_difference=2,
            source_rank_relation="1->3",
            evidence=result.candidate_comparisons[0].evidence,
        ),
    )
    assert not hasattr(result, "aggregate_score")
    assert not hasattr(result.items[0], "score")


def test_result_lineage_and_run_are_complete_and_immutable() -> None:
    run, result = run_recommendation()

    assert run.run_id == RecommendationRunId("recommendation-run-1")
    assert run.requirement_id == RequirementId("requirement-1")
    assert run.filter_result_id == "filter-result-1"
    assert run.ranking_result_id == "ranking-result-1"
    assert run.derived_feature_set_id == "feature-set-1"
    assert run.recommendation_policy_version == DecisionPolicyVersion("recommendation-policy-v1")
    assert result.recommendation_run_id == "recommendation-run-1"
    assert result.requirement_id == RequirementId("requirement-1")
    assert result.filter_result_id == "filter-result-1"
    assert result.ranking_result_id == "ranking-result-1"
    assert result.derived_feature_set_id == "feature-set-1"
    assert result.recommendation_policy_version == "recommendation-policy-v1"

    with pytest.raises(FrozenInstanceError):
        result.items = ()  # type: ignore[misc]


def test_selector_rejects_uncertain_ranking_input_and_candidate_mismatch() -> None:
    with pytest.raises(DomainInvariantViolation, match="QUALIFIED RankingResult"):
        recommendation_result(ranked=ranking_result(RankingViewKind.UNCERTAIN))

    filtered = filter_result(snapshot=all_rejected_snapshot())
    with pytest.raises(DomainInvariantViolation, match="non-qualified candidate"):
        recommendation_result(filtered=filtered, ranked=ranking_result())


def test_selection_is_non_destructive_and_deterministic_for_replay_and_input_permutation() -> None:
    requirement = sample_requirement()
    snapshot = sample_snapshot()
    feature_set = feature_set_for(snapshot=snapshot, requirement=requirement)
    filtered = filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)
    ranked = ranking_result(
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
    )

    first = recommendation_result(
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
        ranked=ranked,
    )
    second = recommendation_result(
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
        ranked=ranked,
    )
    permuted = recommendation_result(snapshot=permuted_snapshot())

    assert semantic_recommendation(first) == semantic_recommendation(second)
    assert semantic_recommendation(first) == semantic_recommendation(permuted)
    assert requirement == sample_requirement()
    assert snapshot == sample_snapshot()
    assert feature_set == feature_set_for(snapshot=snapshot, requirement=requirement)
    assert filtered == filter_result(snapshot=snapshot, requirement=requirement, feature_set=feature_set)
    assert ranked == ranking_result(
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filtered=filtered,
    )


def run_recommendation(
    *,
    requirement: RequirementState | None = None,
    snapshot: CandidateSnapshot | None = None,
    feature_set: DerivedFeatureSet | None = None,
    filtered: CompleteFilterResult | None = None,
    ranked: CompleteRankingResult | None = None,
    policy: RecommendationPolicy | None = None,
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
    resolved_ranked = ranked or ranking_result(
        requirement=resolved_requirement,
        snapshot=resolved_snapshot,
        feature_set=resolved_feature_set,
        filtered=resolved_filtered,
    )
    return m6_default_complete_recommendation_selector().select(
        recommendation_result_id=RecommendationResultId("recommendation-result-1"),
        recommendation_run_id=RecommendationRunId("recommendation-run-1"),
        execution_id=ExecutionId("execution-1"),
        generated_at=instant(2026, 8, 26, 9, 0),
        requirement=resolved_requirement,
        snapshot=resolved_snapshot,
        feature_set=resolved_feature_set,
        filter_result=resolved_filtered,
        ranking_result=resolved_ranked,
        recommendation_policy=policy or m6_default_recommendation_policy(),
    )


def recommendation_result(**kwargs) -> RecommendationResult:
    return run_recommendation(**kwargs)[1]


def ranking_result(
    view: RankingViewKind = RankingViewKind.QUALIFIED,
    *,
    requirement: RequirementState | None = None,
    snapshot: CandidateSnapshot | None = None,
    feature_set: DerivedFeatureSet | None = None,
    filtered: CompleteFilterResult | None = None,
) -> CompleteRankingResult:
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
    )[1]


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
        return replace_departure_value(feature_set, candidate("offer-uncertain", "itinerary-uncertain"))
    return feature_set


def feature_set_without(
    removed_candidate: OfferBackedItineraryCandidate,
    removed_feature_key: object,
) -> DerivedFeatureSet:
    feature_set = feature_set_for()
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


def replace_departure_value(
    feature_set: DerivedFeatureSet,
    target_candidate: OfferBackedItineraryCandidate,
) -> DerivedFeatureSet:
    original = feature_set.value_for(target_candidate, DEPARTURE_DATE_MATCHES_REQUIREMENT)
    replacement = type(original)(
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
    return requirement_with_preferences((price_preference(), fewer_stops_preference()))


def requirement_with_preferences(preferences: tuple[SoftPreference, ...]) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            HardConstraint(
                ConstraintId("departure-date"),
                ConstraintScope.DEPARTURE_DATE,
                ConstraintOperator.EQUALS,
                LocalDate(date(2026, 9, 1)),
            ),
        ),
        preferences=preferences,
    )


def price_preference() -> SoftPreference:
    return SoftPreference(PreferenceId("prefer-price"), PreferenceScope.PRICE, PreferenceImportance.LOW)


def fewer_stops_preference() -> SoftPreference:
    return SoftPreference(PreferenceId("prefer-fewer-stops"), PreferenceScope.FEWER_STOPS, PreferenceImportance.HIGH)


def sample_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("a", date(2026, 9, 1), Decimal(1000), 0),
            ("b", date(2026, 9, 1), Decimal(1100), 0),
            ("c", date(2026, 9, 1), Decimal(900), 2),
            ("rejected", date(2026, 9, 2), Decimal(100), 0),
            ("uncertain", date(2026, 9, 1), Decimal(800), 0),
        )
    )


def same_best_and_cheapest_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("a", date(2026, 9, 1), Decimal(900), 0),
            ("b", date(2026, 9, 1), Decimal(1000), 1),
        )
    )


def all_rejected_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("rejected-a", date(2026, 9, 2), Decimal(900), 0),
            ("rejected-b", date(2026, 9, 3), Decimal(1000), 0),
        )
    )


def permuted_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("uncertain", date(2026, 9, 1), Decimal(800), 0),
            ("rejected", date(2026, 9, 2), Decimal(100), 0),
            ("c", date(2026, 9, 1), Decimal(900), 2),
            ("b", date(2026, 9, 1), Decimal(1100), 0),
            ("a", date(2026, 9, 1), Decimal(1000), 0),
        )
    )


def snapshot_from_specs(specs: tuple[tuple[str, date, Decimal, int], ...]) -> CandidateSnapshot:
    segments: list[FlightSegment] = []
    itineraries: list[Itinerary] = []
    offers: list[Offer] = []
    for suffix, departure_date, price, stop_count in specs:
        segment_ids = []
        airports = ("PEK", "NKG", "HGH", "SHA")
        for index in range(stop_count + 1):
            segment = FlightSegment(
                segment_id=SegmentId(f"segment-{suffix}-{index + 1}"),
                marketing_carrier="MU",
                flight_number=f"51{len(segments) + 1:02d}",
                departure_airport=airports[index],
                arrival_airport=airports[index + 1] if index < stop_count else "SHA",
                departure_at=instant(departure_date.year, departure_date.month, departure_date.day, 8 + index * 3, 30),
                arrival_at=instant(departure_date.year, departure_date.month, departure_date.day, 10 + index * 3, 30),
                operating_carrier=DomainValue.known("MU"),
                aircraft_type=DomainValue.not_provided(),
                provenance=(ProvenanceRef("canonical", f"segment-{suffix}-{index + 1}"),),
            )
            segment_ids.append(segment.segment_id)
            segments.append(segment)
        itinerary = Itinerary(ItineraryId(f"itinerary-{suffix}"), tuple(segment_ids))
        offer = Offer(
            OfferId(f"offer-{suffix}"),
            itinerary.itinerary_id,
            Money(price, "CNY"),
            OfferFreshness(FreshnessState.FRESH),
            DomainValue.known(f"BOOK-{suffix}"),
        )
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
    )


def candidate(offer_id: str, itinerary_id: str) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(OfferId(offer_id), ItineraryId(itinerary_id))


def item_candidate(item) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(item.primary_offer_id, item.itinerary_id)


def candidate_key(item) -> str:
    return f"{item.primary_offer_id.value}|{item.itinerary_id.value}"


def ranked_order(result: CompleteRankingResult) -> tuple[str, ...]:
    return tuple(f"{entry.candidate.offer_id.value}|{entry.candidate.itinerary_id.value}" for entry in result.entries)


def semantic_recommendation(result: RecommendationResult) -> tuple:
    return tuple(
        (
            item.primary_offer_id,
            item.itinerary_id,
            item.roles,
            item.source_rank,
            item.selection_order,
            item.trade_off_evidence,
        )
        for item in result.items
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
