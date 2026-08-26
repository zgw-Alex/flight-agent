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
    DecisionPolicyVersion,
    DerivedFeatureRunId,
    DerivedFeatureSet,
    DerivedFeatureSetId,
    FeatureValue,
    FilterResultDirection,
    FilterResultId,
    FilterRunId,
    OfferBackedItineraryCandidate,
    RelaxationPolicy,
    RelaxationProposalKind,
    RelaxationReasonCode,
    RelaxationResult,
    RelaxationResultDirection,
    RelaxationResultId,
    RelaxationRunId,
    m6_default_complete_filtering_engine,
    m6_default_derived_feature_engine,
    m6_default_deterministic_relaxation_engine,
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
from flight_agent.domain.workflow import EvidenceSource


def test_enters_only_definitive_filter_empty_and_proposes_exact_minimum_boundary() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850)), ("b", Decimal(920))))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set)

    run, result = run_relaxation(requirement, snapshot, feature_set, filtered)

    assert filtered.direction is FilterResultDirection.FILTER_EMPTY
    assert result.direction is RelaxationResultDirection.PROPOSALS_AVAILABLE
    assert run.entry_direction is FilterResultDirection.FILTER_EMPTY
    assert result.relaxation_policy_version == DecisionPolicyVersion("relaxation-policy-v1")
    assert [proposal.proposed_value for proposal in result.proposals] == [
        Money(Decimal(850), "CNY"),
        Money(Decimal(920), "CNY"),
    ]
    first = result.proposals[0]
    assert first.proposal_kind is RelaxationProposalKind.SINGLE
    assert first.source_constraint_id == ConstraintId("max-price")
    assert first.current_value == Money(Decimal(800), "CNY")
    assert first.proposed_value == Money(Decimal(850), "CNY")
    assert first.native_magnitude == Money(Decimal(50), "CNY")
    assert first.recovered_candidates == (candidate("offer-a", "itinerary-a"),)
    assert first.reason_code is RelaxationReasonCode.FILTER_EMPTY_MAX_PRICE_RELAXATION
    assert {ref.source for ref in first.evidence} == {EvidenceSource.CONSTRAINT, EvidenceSource.OFFER}
    assert first.source_evaluation_ids
    assert first.counterfactual_evaluation_ids


def test_below_minimum_boundary_does_not_recover_candidate() -> None:
    snapshot = snapshot_from_specs((("a", Decimal(850)), ("b", Decimal(920))))
    below = max_price_requirement(Decimal("849.99"))
    feature_set = feature_set_for(snapshot, below)
    filtered = filter_result(snapshot, below, feature_set)

    assert filtered.direction is FilterResultDirection.FILTER_EMPTY
    assert filtered.qualified_candidates == ()

    result = relaxation_result(below, snapshot, feature_set, filtered)

    assert result.proposals[0].proposed_value == Money(Decimal(850), "CNY")


def test_no_entry_when_uncertain_candidate_exists_or_source_is_not_filter_empty() -> None:
    requirement = max_price_requirement(Decimal(800), currency="USD")
    snapshot = snapshot_from_specs((("a", Decimal(850)),))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set)

    result = relaxation_result(requirement, snapshot, feature_set, filtered)

    assert filtered.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED
    assert result.direction is RelaxationResultDirection.NOT_TRIGGERED
    assert result.reason_code is RelaxationReasonCode.RELAXATION_NOT_TRIGGERED
    assert result.proposals == ()

    happy = max_price_requirement(Decimal(900))
    happy_features = feature_set_for(snapshot, happy)
    happy_filter = filter_result(snapshot, happy, happy_features)

    assert relaxation_result(happy, snapshot, happy_features, happy_filter).direction is (
        RelaxationResultDirection.NOT_TRIGGERED
    )


def test_unknown_total_price_is_not_fail_only_relaxation_evidence() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850)),))
    feature_set = replace_total_price_with_unknown(feature_set_for(snapshot, requirement), candidate("offer-a", "itinerary-a"))
    filtered = filter_result(snapshot, requirement, feature_set)

    result = relaxation_result(requirement, snapshot, feature_set, filtered)

    assert filtered.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED
    assert result.proposals == ()


def test_policy_bound_and_pairwise_empty_safety_are_deterministic() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850)), ("b", Decimal(920))))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set)
    policy = RelaxationPolicy(
        DecisionPolicyVersion("relaxation-policy-test"),
        (ConstraintScope.MAX_PRICE,),
        max_single_proposals=1,
        max_pairwise_proposals=0,
        max_total_proposals=1,
    )

    first = relaxation_result(requirement, snapshot, feature_set, filtered, policy=policy)
    second = relaxation_result(requirement, snapshot, feature_set, filtered, policy=policy)

    assert semantic_relaxation(first) == semantic_relaxation(second)
    assert len(first.proposals) == 1
    assert all(proposal.proposal_kind is RelaxationProposalKind.SINGLE for proposal in first.proposals)
    assert first.proposals[0].proposed_value == Money(Decimal(850), "CNY")


def test_relaxation_is_non_mutating_and_preserves_input_universe() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850)), ("b", Decimal(920))))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set)
    before_requirement = requirement
    before_snapshot = snapshot
    before_feature_set = feature_set
    before_candidates = tuple(offer.offer_id for offer in snapshot.offers)

    result = relaxation_result(requirement, snapshot, feature_set, filtered)

    assert requirement == before_requirement
    assert snapshot == before_snapshot
    assert feature_set == before_feature_set
    assert tuple(offer.offer_id for offer in snapshot.offers) == before_candidates
    assert not hasattr(result, "patch_set")
    assert not hasattr(result, "search_plan")
    with pytest.raises(FrozenInstanceError):
        result.proposals = ()  # type: ignore[misc]


def test_relaxation_lineage_rejects_mismatched_inputs() -> None:
    requirement = max_price_requirement(Decimal(800))
    snapshot = snapshot_from_specs((("a", Decimal(850)),))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set)
    other_requirement = max_price_requirement(Decimal(800), requirement_id="other-requirement")

    with pytest.raises(Exception, match="requirement lineage"):
        relaxation_result(other_requirement, snapshot, feature_set, filtered)


def run_relaxation(
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filtered: CompleteFilterResult,
    *,
    policy: RelaxationPolicy | None = None,
) -> tuple:
    return m6_default_deterministic_relaxation_engine(
        m6_default_complete_filtering_engine().evaluator_registry
    ).analyze(
        relaxation_result_id=RelaxationResultId("relaxation-result-1"),
        relaxation_run_id=RelaxationRunId("relaxation-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_result=filtered,
        relaxation_policy=policy or m6_default_relaxation_policy(),
    )


def relaxation_result(
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    feature_set: DerivedFeatureSet,
    filtered: CompleteFilterResult,
    *,
    policy: RelaxationPolicy | None = None,
) -> RelaxationResult:
    return run_relaxation(requirement, snapshot, feature_set, filtered, policy=policy)[1]


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


def feature_set_for(snapshot: CandidateSnapshot, requirement: RequirementState) -> DerivedFeatureSet:
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT),
        snapshot=snapshot,
        requirement=requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]


def replace_total_price_with_unknown(
    feature_set: DerivedFeatureSet,
    target_candidate: OfferBackedItineraryCandidate,
) -> DerivedFeatureSet:
    original = feature_set.value_for(target_candidate, TOTAL_PRICE)
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
        values=tuple(replacement if value.candidate == target_candidate and value.feature_key == TOTAL_PRICE else value for value in feature_set.values),
    )


def max_price_requirement(
    amount: Decimal,
    *,
    currency: str = "CNY",
    requirement_id: str = "requirement-1",
) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId(requirement_id),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            HardConstraint(
                ConstraintId("departure-date"),
                ConstraintScope.DEPARTURE_DATE,
                ConstraintOperator.EQUALS,
                LocalDate(date(2026, 9, 1)),
            ),
            HardConstraint(
                ConstraintId("max-price"),
                ConstraintScope.MAX_PRICE,
                ConstraintOperator.AT_OR_BEFORE,
                Money(amount, currency),
            ),
        ),
        preferences=(
            SoftPreference(PreferenceId("prefer-price"), PreferenceScope.PRICE, PreferenceImportance.HIGH),
        ),
    )


def snapshot_from_specs(specs: tuple[tuple[str, Decimal], ...]) -> CandidateSnapshot:
    segments: list[FlightSegment] = []
    itineraries: list[Itinerary] = []
    offers: list[Offer] = []
    for suffix, price in specs:
        segment = FlightSegment(
            segment_id=SegmentId(f"segment-{suffix}"),
            marketing_carrier="MU",
            flight_number=f"51{len(segments) + 1:02d}",
            departure_airport="PEK",
            arrival_airport="SHA",
            departure_at=instant(2026, 9, 1, 8, 30),
            arrival_at=instant(2026, 9, 1, 10, 30),
            operating_carrier=DomainValue.known("MU"),
            aircraft_type=DomainValue.not_provided(),
            provenance=(ProvenanceRef("canonical", f"segment-{suffix}"),),
        )
        itinerary = Itinerary(
            ItineraryId(f"itinerary-{suffix}"),
            (segment.segment_id,),
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
        segments.append(segment)
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


def semantic_relaxation(result: RelaxationResult) -> tuple:
    return tuple(
        (
            proposal.proposal_kind,
            proposal.source_constraint_id,
            proposal.current_value,
            proposal.proposed_value,
            proposal.native_magnitude,
            proposal.recovered_candidates,
        )
        for proposal in result.proposals
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
