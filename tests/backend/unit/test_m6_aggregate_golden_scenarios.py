from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

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
    FeatureValue,
    FilterResultDirection,
    FilterResultId,
    FilterRunId,
    OfferBackedItineraryCandidate,
    RankingResultId,
    RankingRunId,
    RankingViewKind,
    RecommendationRunId,
    RelaxationResult,
    RelaxationResultDirection,
    RelaxationResultId,
    RelaxationRunId,
    m6_default_complete_filtering_engine,
    m6_default_complete_ranking_engine,
    m6_default_complete_recommendation_selector,
    m6_default_derived_feature_engine,
    m6_default_deterministic_relaxation_engine,
    m6_default_ranking_policy_set,
    m6_default_recommendation_policy,
    m6_default_relaxation_policy,
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
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.domain.workflow import (
    ExecutionId,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def test_gs_01_complete_happy_decision_pipeline_is_deterministic_and_lineaged() -> None:
    result = complete_decision()
    replay = complete_decision()

    assert result.filtered.direction is FilterResultDirection.QUALIFIED_AVAILABLE
    assert len(result.filtered.qualified_candidates) > 0
    assert [entry.rank_position for entry in result.ranked.entries] == list(range(1, len(result.ranked.entries) + 1))
    assert result.recommended.status is RecommendationResultStatus.EXACT_MATCH
    assert result.recommended.filter_result_id == result.filtered.filter_result_id.value
    assert result.recommended.ranking_result_id == result.ranked.ranking_result_id.value
    assert semantic_decision(result) == semantic_decision(replay)


def test_gs_02_hard_constraint_failure_is_rejected_not_ranked_or_recommended() -> None:
    result = complete_decision()

    rejected_offer_ids = {candidate.offer_id for candidate in result.filtered.rejected_candidates}
    ranked_offer_ids = {entry.candidate.offer_id for entry in result.ranked.entries}
    recommended_offer_ids = {item.primary_offer_id for item in result.recommended.items}

    assert OfferId("offer-rejected") in rejected_offer_ids
    assert OfferId("offer-rejected") not in ranked_offer_ids
    assert OfferId("offer-rejected") not in recommended_offer_ids


def test_gs_03_unknown_eligibility_is_uncertain_not_qualified_or_main_recommended() -> None:
    result = complete_decision(
        snapshot=snapshot_with_uncertain_candidate(),
        feature_set_transform=unknown_departure_for_uncertain,
    )

    uncertain = candidate("offer-uncertain", "itinerary-uncertain")

    assert uncertain in result.filtered.uncertain_candidates
    assert uncertain not in result.filtered.qualified_candidates
    assert all(entry.candidate != uncertain for entry in result.ranked.entries)
    assert all(item.primary_offer_id != OfferId("offer-uncertain") for item in result.recommended.items)


def test_gs_04_multi_preference_ranking_tracks_contribution_weight_and_coverage() -> None:
    result = complete_decision()
    first = result.ranked.entries[0]
    scopes = {contribution.preference_scope for contribution in first.preference_contributions}

    assert scopes == {PreferenceScope.PRICE, PreferenceScope.FEWER_STOPS}
    assert all(contribution.raw_feature_value is not None for contribution in first.preference_contributions)
    assert first.coverage.total_applicable_preference_count == 2
    assert first.coverage.total_applicable_weight == Decimal("2.5")
    assert first.aggregate_score >= Decimal(0)
    assert result.ranked.pool_relative_normalization


def test_gs_05_deterministic_tie_break_uses_coverage_then_candidate_identity() -> None:
    result = complete_decision(snapshot=tie_snapshot(), requirement=price_only_requirement())
    replay = complete_decision(snapshot=reversed_tie_snapshot(), requirement=price_only_requirement())

    assert ranked_order(result.ranked) == ("offer-a|itinerary-a", "offer-b|itinerary-b")
    assert ranked_order(result.ranked) == ranked_order(replay.ranked)


def test_gs_06_multi_role_recommendation_is_not_top_n_truncation() -> None:
    result = complete_decision()
    ranked = ranked_order(result.ranked)

    assert ranked[:2] == ("offer-a|itinerary-a", "offer-b|itinerary-b")
    assert {item.primary_offer_id for item in result.recommended.items} == {
        OfferId("offer-a"),
        OfferId("offer-c"),
    }
    cheapest = next(item for item in result.recommended.items if item.primary_offer_id == OfferId("offer-c"))
    assert cheapest.roles == (RecommendationRole.CHEAPEST,)
    assert cheapest.source_rank == 3
    assert cheapest.selection_order == 2


def test_gs_07_filter_empty_generates_max_price_relaxation_without_mutation_or_search() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850), 0), ("b", Decimal(920), 0)))
    result = complete_decision(snapshot=snapshot, requirement=requirement, include_recommendation=False)
    before_requirement = requirement
    before_snapshot = snapshot
    before_feature_set = result.feature_set

    relaxation = result.relaxation

    assert result.filtered.direction is FilterResultDirection.FILTER_EMPTY
    assert result.filtered.qualified_candidates == ()
    assert result.filtered.uncertain_candidates == ()
    assert relaxation is not None
    assert relaxation.direction is RelaxationResultDirection.PROPOSALS_AVAILABLE
    assert relaxation.proposals[0].current_value == Money(Decimal(800), "CNY")
    assert relaxation.proposals[0].proposed_value == Money(Decimal(850), "CNY")
    assert relaxation.proposals[0].recovered_candidates == (candidate("offer-a", "itinerary-a"),)
    assert requirement == before_requirement
    assert snapshot == before_snapshot
    assert result.feature_set == before_feature_set
    assert not hasattr(relaxation, "search_plan")


def test_gs_08_replay_lineage_is_semantically_equivalent_across_all_decision_artifacts() -> None:
    first = complete_decision(include_relaxation_fixture=True)
    second = complete_decision(include_relaxation_fixture=True)

    assert semantic_decision(first) == semantic_decision(second)
    assert first.feature_set.input_lineage.requirement_version == RequirementVersion(1)
    assert first.filtered.requirement_version == RequirementVersion(1)
    assert first.ranked.requirement_version == RequirementVersion(1)
    assert first.recommended.requirement_id == RequirementId("requirement-1")
    assert first.relaxation is not None
    assert first.relaxation.requirement_version == RequirementVersion(1)
    assert first.relaxation.relaxation_policy_version == DecisionPolicyVersion("relaxation-policy-v1")


class DecisionBundle:
    def __init__(
        self,
        *,
        requirement: RequirementState,
        snapshot: CandidateSnapshot,
        feature_set: DerivedFeatureSet,
        filtered: CompleteFilterResult,
        ranked: CompleteRankingResult,
        recommended: RecommendationResult,
        relaxation: RelaxationResult | None,
    ) -> None:
        self.requirement = requirement
        self.snapshot = snapshot
        self.feature_set = feature_set
        self.filtered = filtered
        self.ranked = ranked
        self.recommended = recommended
        self.relaxation = relaxation


def complete_decision(
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None = None,
    feature_set_transform=None,
    include_recommendation: bool = True,
    include_relaxation_fixture: bool = False,
) -> DecisionBundle:
    resolved_requirement = requirement or sample_requirement()
    resolved_snapshot = snapshot or sample_snapshot()
    feature_set = feature_set_for(resolved_snapshot, resolved_requirement)
    if feature_set_transform is not None:
        feature_set = feature_set_transform(feature_set)
    filtered = filter_result(resolved_snapshot, resolved_requirement, feature_set)
    ranked = ranking_result(resolved_snapshot, resolved_requirement, feature_set, filtered)
    recommended = (
        recommendation_result(resolved_snapshot, resolved_requirement, feature_set, filtered, ranked)
        if include_recommendation
        else empty_recommendation()
    )
    relaxation = None
    if filtered.direction is FilterResultDirection.FILTER_EMPTY or include_relaxation_fixture:
        relaxation = relaxation_result(resolved_snapshot, resolved_requirement, feature_set, filtered)
    return DecisionBundle(
        requirement=resolved_requirement,
        snapshot=resolved_snapshot,
        feature_set=feature_set,
        filtered=filtered,
        ranked=ranked,
        recommended=recommended,
        relaxation=relaxation,
    )


def filter_result(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
    feature_set: DerivedFeatureSet,
) -> CompleteFilterResult:
    return m6_default_complete_filtering_engine().filter(
        filter_result_id=FilterResultId("filter-result-1"),
        filter_run_id=FilterRunId("filter-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_policy_version=DecisionPolicyVersion("filter-policy-v1"),
    )[1]


def ranking_result(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
    feature_set: DerivedFeatureSet,
    filtered: CompleteFilterResult,
) -> CompleteRankingResult:
    return m6_default_complete_ranking_engine().rank(
        ranking_result_id=RankingResultId("ranking-result-1"),
        ranking_run_id=RankingRunId("ranking-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_result=filtered,
        ranking_view_kind=RankingViewKind.QUALIFIED,
        ranking_policy_set=m6_default_ranking_policy_set(),
    )[1]


def recommendation_result(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
    feature_set: DerivedFeatureSet,
    filtered: CompleteFilterResult,
    ranked: CompleteRankingResult,
) -> RecommendationResult:
    return m6_default_complete_recommendation_selector().select(
        recommendation_result_id=RecommendationResultId("recommendation-result-1"),
        recommendation_run_id=RecommendationRunId("recommendation-run-1"),
        execution_id=ExecutionId("execution-1"),
        generated_at=instant(2026, 8, 26, 9, 0),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_result=filtered,
        ranking_result=ranked,
        recommendation_policy=m6_default_recommendation_policy(),
    )[1]


def relaxation_result(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
    feature_set: DerivedFeatureSet,
    filtered: CompleteFilterResult,
) -> RelaxationResult:
    return m6_default_deterministic_relaxation_engine(
        m6_default_complete_filtering_engine().evaluator_registry
    ).analyze(
        relaxation_result_id=RelaxationResultId("relaxation-result-1"),
        relaxation_run_id=RelaxationRunId("relaxation-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_result=filtered,
        relaxation_policy=m6_default_relaxation_policy(),
    )[1]


def feature_set_for(snapshot: CandidateSnapshot, requirement: RequirementState) -> DerivedFeatureSet:
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT),
        snapshot=snapshot,
        requirement=requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]


def unknown_departure_for_uncertain(feature_set: DerivedFeatureSet) -> DerivedFeatureSet:
    target = candidate("offer-uncertain", "itinerary-uncertain")
    original = feature_set.value_for(target, DEPARTURE_DATE_MATCHES_REQUIREMENT)
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
    return replace_feature(feature_set, replacement)


def replace_feature(feature_set: DerivedFeatureSet, replacement: FeatureValue) -> DerivedFeatureSet:
    return DerivedFeatureSet(
        feature_set_id=feature_set.feature_set_id,
        run_id=feature_set.run_id,
        input_lineage=feature_set.input_lineage,
        feature_definition_versions=feature_set.feature_definition_versions,
        reference_data_versions=feature_set.reference_data_versions,
        values=tuple(replacement if value.candidate == replacement.candidate and value.feature_key == replacement.feature_key else value for value in feature_set.values),
    )


def empty_recommendation() -> RecommendationResult:
    return RecommendationResult(
        RecommendationResultId("recommendation-result-empty"),
        RecommendationResultStatus.NO_MATCH,
        ExecutionId("execution-empty"),
        RequirementVersion(1),
        CandidateSnapshotId("snapshot-1"),
        SnapshotVersion(1),
        instant(2026, 8, 26, 9, 0),
    )


def sample_requirement() -> RequirementState:
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
        preferences=(
            SoftPreference(PreferenceId("prefer-price"), PreferenceScope.PRICE, PreferenceImportance.LOW),
            SoftPreference(PreferenceId("prefer-fewer-stops"), PreferenceScope.FEWER_STOPS, PreferenceImportance.HIGH),
        ),
    )


def price_only_requirement() -> RequirementState:
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
        preferences=(
            SoftPreference(PreferenceId("prefer-price"), PreferenceScope.PRICE, PreferenceImportance.HIGH),
        ),
    )


def max_price_requirement(amount: Decimal) -> RequirementState:
    base = sample_requirement()
    return RequirementState.initial(
        requirement_id=base.requirement_id,
        recorded_at=base.recorded_at,
        constraints=(
            *base.constraints,
            HardConstraint(
                ConstraintId("max-price"),
                ConstraintScope.MAX_PRICE,
                ConstraintOperator.AT_OR_BEFORE,
                Money(amount, "CNY"),
            ),
        ),
        preferences=base.preferences,
    )


def sample_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("a", Decimal(1000), 0),
            ("b", Decimal(1100), 0),
            ("c", Decimal(900), 2),
            ("rejected", Decimal(100), 0, date(2026, 9, 2)),
        )
    )


def snapshot_with_uncertain_candidate() -> CandidateSnapshot:
    return snapshot_from_specs(
        (
            ("a", Decimal(1000), 0),
            ("b", Decimal(1100), 0),
            ("c", Decimal(900), 2),
            ("rejected", Decimal(100), 0, date(2026, 9, 2)),
            ("uncertain", Decimal(800), 0),
        )
    )


def tie_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs((("a", Decimal(1000), 0), ("b", Decimal(1000), 0)))


def reversed_tie_snapshot() -> CandidateSnapshot:
    return snapshot_from_specs((("b", Decimal(1000), 0), ("a", Decimal(1000), 0)))


def snapshot_from_specs(specs: tuple[tuple, ...]) -> CandidateSnapshot:
    segments: list[FlightSegment] = []
    itineraries: list[Itinerary] = []
    offers: list[Offer] = []
    for item in specs:
        suffix, price, stop_count = item[:3]
        departure_date = item[3] if len(item) > 3 else date(2026, 9, 1)
        segment_ids: list[SegmentId] = []
        for index in range(stop_count + 1):
            segment = FlightSegment(
                segment_id=SegmentId(f"segment-{suffix}-{index + 1}"),
                marketing_carrier="MU",
                flight_number=f"51{len(segments) + 1:02d}",
                departure_airport=("PEK", "NKG", "HGH")[index],
                arrival_airport=("NKG", "HGH", "SHA")[index],
                departure_at=instant(departure_date.year, departure_date.month, departure_date.day, 8 + index * 3, 30),
                arrival_at=instant(departure_date.year, departure_date.month, departure_date.day, 10 + index * 3, 30),
                operating_carrier=DomainValue.known("MU"),
                aircraft_type=DomainValue.not_provided(),
                provenance=(ProvenanceRef("canonical", f"segment-{suffix}-{index + 1}"),),
            )
            segment_ids.append(segment.segment_id)
            segments.append(segment)
        itinerary = Itinerary(
            ItineraryId(f"itinerary-{suffix}"),
            tuple(segment_ids),
            provenance=(ProvenanceRef("canonical", f"itinerary-{suffix}"),),
        )
        offer = Offer(
            OfferId(f"offer-{suffix}"),
            itinerary.itinerary_id,
            Money(price, "CNY"),
            OfferFreshness(FreshnessState.FRESH),
            DomainValue.known(f"BOOK-{suffix}"),
            provenance=(ProvenanceRef("canonical", f"offer-{suffix}"),),
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
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def candidate(offer_id: str, itinerary_id: str) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(OfferId(offer_id), ItineraryId(itinerary_id))


def ranked_order(result: CompleteRankingResult) -> tuple[str, ...]:
    return tuple(f"{entry.candidate.offer_id.value}|{entry.candidate.itinerary_id.value}" for entry in result.entries)


def semantic_decision(bundle: DecisionBundle) -> tuple:
    return (
        tuple((value.candidate, value.feature_key, value.value.state, value.value.value if value.value.is_known else None) for value in bundle.feature_set.values),
        bundle.filtered.partition,
        ranked_order(bundle.ranked),
        tuple((item.primary_offer_id, item.roles, item.source_rank, item.selection_order) for item in bundle.recommended.items),
        tuple(
            (
                proposal.current_value,
                proposal.proposed_value,
                proposal.native_magnitude,
                proposal.recovered_candidates,
            )
            for proposal in bundle.relaxation.proposals
        )
        if bundle.relaxation is not None
        else (),
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
