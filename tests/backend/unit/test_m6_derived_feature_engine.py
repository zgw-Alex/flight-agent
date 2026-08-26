from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from flight_agent.domain.decision import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    STOP_COUNT,
    TOTAL_PRICE,
    DecisionPolicyVersion,
    DerivedFeatureRunId,
    DerivedFeatureSetId,
    FeatureClassification,
    FeatureDefinitionRegistry,
    FeatureDefinitionVersion,
    FeatureDependency,
    FeatureKey,
    FeatureValue,
    FeatureValueType,
    OfferBackedItineraryCandidate,
    RequirementFeatureDependency,
    m6_default_derived_feature_engine,
    m6_default_feature_registry,
)
from flight_agent.domain.decision.evaluation import DecisionConstraintScope
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
    RequirementId,
    RequirementState,
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
    ValueState,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource


class _DefaultRequirement:
    pass


DEFAULT_REQUIREMENT = _DefaultRequirement()


def test_feature_definition_and_feature_value_are_separate_contracts() -> None:
    definition = m6_default_feature_registry().get(TOTAL_PRICE)
    _, feature_set = compute_features((TOTAL_PRICE,))
    value = feature_set.values[0]

    assert definition.feature_key == value.feature_key
    assert definition.classification is FeatureClassification.CANDIDATE_INTRINSIC
    assert not hasattr(definition, "candidate")
    assert value.candidate == candidate_identity()
    assert not hasattr(value, "calculator_id")


def test_intrinsic_feature_replay_is_deterministic() -> None:
    first = compute_features((TOTAL_PRICE, STOP_COUNT))[1]
    second = compute_features((TOTAL_PRICE, STOP_COUNT))[1]

    assert first.values == second.values
    assert first.value_for(candidate_identity(), TOTAL_PRICE).value.value == Money(Decimal(980), "CNY")
    assert first.value_for(candidate_identity(), STOP_COUNT).value.value == 1


def test_requirement_relative_feature_replay_is_deterministic_and_lineaged() -> None:
    first_run, first_set = compute_features((DEPARTURE_DATE_MATCHES_REQUIREMENT,))
    _second_run, second_set = compute_features((DEPARTURE_DATE_MATCHES_REQUIREMENT,))
    value = first_set.value_for(candidate_identity(), DEPARTURE_DATE_MATCHES_REQUIREMENT)

    assert first_set.values == second_set.values
    assert value.value.value is True
    assert value.requirement_dependencies == (
        RequirementFeatureDependency("constraint", ConstraintScope.DEPARTURE_DATE.value),
    )
    assert first_run.input_lineage.requirement_id == RequirementId("requirement-1")
    assert first_run.input_lineage.requirement_version == RequirementVersion(1)


def test_typed_feature_values_are_not_preemptively_float_normalized() -> None:
    _, feature_set = compute_features((TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT))

    total_price = feature_set.value_for(candidate_identity(), TOTAL_PRICE)
    stop_count = feature_set.value_for(candidate_identity(), STOP_COUNT)
    date_match = feature_set.value_for(candidate_identity(), DEPARTURE_DATE_MATCHES_REQUIREMENT)

    assert isinstance(total_price.value.value, Money)
    assert isinstance(stop_count.value.value, int)
    assert isinstance(date_match.value.value, bool)
    assert not isinstance(stop_count.value.value, float)
    assert not hasattr(total_price, "normalized_value")
    assert not hasattr(stop_count, "weighted_contribution")
    assert not hasattr(date_match, "aggregate_score")


def test_missing_requirement_context_produces_typed_not_applicable_not_constraint_evaluation() -> None:
    _, feature_set = compute_features((DEPARTURE_DATE_MATCHES_REQUIREMENT,), requirement=no_date_requirement())
    value = feature_set.value_for(candidate_identity(), DEPARTURE_DATE_MATCHES_REQUIREMENT)

    assert value.value_status is ValueState.NOT_APPLICABLE
    with pytest.raises(DomainInvariantViolation):
        _ = value.value.value
    assert not hasattr(value, "status")
    assert not hasattr(value, "reason_code")


def test_programming_failures_are_not_swallowed_as_unknown_feature_values() -> None:
    with pytest.raises(DomainInvariantViolation):
        FeatureDefinitionRegistry(
            (
                m6_default_feature_registry().get(TOTAL_PRICE),
                m6_default_feature_registry().get(TOTAL_PRICE),
            )
        )
    with pytest.raises(DomainInvariantViolation):
        FeatureValue(
            feature_key=FeatureKey("bad"),
            candidate=candidate_identity(),
            value=DomainValue.known(True),
            value_type=FeatureValueType.INTEGER,
            evidence=(EvidenceRef(EvidenceSource.OFFER, OfferId("offer-1")),),
            canonical_dependencies=(FeatureDependency("offer", "total_price"),),
            definition_version=FeatureDefinitionVersion("bad-v1"),
        )


def test_required_feature_resolution_computes_only_requested_feature_keys() -> None:
    run, feature_set = compute_features((STOP_COUNT,))

    assert run.requested_feature_keys == (STOP_COUNT,)
    assert run.required_feature_keys == (STOP_COUNT,)
    assert tuple(value.feature_key for value in feature_set.values) == (STOP_COUNT,)


def test_unknown_feature_key_fails_explicitly() -> None:
    with pytest.raises(DomainInvariantViolation, match="Unknown FeatureKey"):
        compute_features((FeatureKey("unknown_feature"),))


def test_reference_data_lineage_is_empty_when_no_selected_feature_depends_on_reference_data() -> None:
    run, feature_set = compute_features((TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT))

    assert run.reference_data_versions == ()
    assert feature_set.reference_data_versions == ()
    assert all(value.reference_data_dependencies == () for value in feature_set.values)


def test_derived_feature_set_lineage_preserves_snapshot_requirement_versions_and_artifact_identity() -> None:
    run, feature_set = compute_features((TOTAL_PRICE,))

    assert run.run_id == DerivedFeatureRunId("feature-run-1")
    assert feature_set.feature_set_id == DerivedFeatureSetId("feature-set-1")
    assert feature_set.run_id == run.run_id
    assert feature_set.input_lineage.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert feature_set.input_lineage.snapshot_version == SnapshotVersion(1)
    assert feature_set.input_lineage.requirement_id == RequirementId("requirement-1")
    assert feature_set.feature_definition_versions == (
        (TOTAL_PRICE, FeatureDefinitionVersion("total-price-v1")),
    )


def test_feature_artifacts_are_immutable_and_do_not_mutate_snapshot_or_requirement() -> None:
    snapshot = sample_snapshot()
    requirement = sample_requirement()
    before_snapshot = snapshot
    before_requirement = requirement

    _, feature_set = compute_features((TOTAL_PRICE, STOP_COUNT), snapshot=snapshot, requirement=requirement)

    with pytest.raises(FrozenInstanceError):
        feature_set.values = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        feature_set.values[0].value = DomainValue.known(1)  # type: ignore[misc]
    assert snapshot == before_snapshot
    assert requirement == before_requirement
    assert not hasattr(snapshot.offers[0], "feature_values")
    assert not hasattr(requirement, "derived_features")


def test_intrinsic_features_do_not_depend_on_unrelated_requirement_changes() -> None:
    first = compute_features((TOTAL_PRICE, STOP_COUNT), requirement=sample_requirement())[1]
    second = compute_features((TOTAL_PRICE, STOP_COUNT), requirement=other_requirement())[1]

    assert first.values == second.values


def test_relative_feature_requires_requirement_state() -> None:
    with pytest.raises(DomainInvariantViolation, match="Requirement-relative"):
        compute_features((DEPARTURE_DATE_MATCHES_REQUIREMENT,), requirement=None)


def test_feature_definition_contract_records_scope_dependencies_version_and_calculator_identity() -> None:
    definition = m6_default_feature_registry().get(DEPARTURE_DATE_MATCHES_REQUIREMENT)

    assert definition.feature_key == DEPARTURE_DATE_MATCHES_REQUIREMENT
    assert definition.value_type is FeatureValueType.BOOLEAN
    assert definition.scope is DecisionConstraintScope.ITINERARY
    assert definition.classification is FeatureClassification.REQUIREMENT_RELATIVE
    assert definition.canonical_dependencies == (
        FeatureDependency("segment", "first.departure_at.date"),
    )
    assert definition.requirement_dependencies == (
        RequirementFeatureDependency("constraint", ConstraintScope.DEPARTURE_DATE.value),
    )
    assert definition.definition_version == FeatureDefinitionVersion("departure-date-match-v1")
    assert definition.calculator_id == "departure_date_matches_requirement_calculator"


def test_feature_engine_has_no_downstream_decision_outputs() -> None:
    _, feature_set = compute_features((TOTAL_PRICE, STOP_COUNT, DEPARTURE_DATE_MATCHES_REQUIREMENT))

    for value in feature_set.values:
        assert not hasattr(value, "eligibility")
        assert not hasattr(value, "rank")
        assert not hasattr(value, "recommendation_role")
        assert not hasattr(value, "relaxation_result")


def compute_features(
    requested_feature_keys: tuple[FeatureKey, ...],
    *,
    snapshot: CandidateSnapshot | None = None,
    requirement: RequirementState | None | _DefaultRequirement = DEFAULT_REQUIREMENT,
):
    resolved_requirement = sample_requirement() if isinstance(requirement, _DefaultRequirement) else requirement
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=requested_feature_keys,
        snapshot=snapshot or sample_snapshot(),
        requirement=resolved_requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )


def sample_snapshot() -> CandidateSnapshot:
    first = FlightSegment(
        segment_id=SegmentId("segment-1"),
        marketing_carrier="MU",
        flight_number="5101",
        departure_airport="PEK",
        arrival_airport="NKG",
        departure_at=instant(2026, 9, 1, 8, 30),
        arrival_at=instant(2026, 9, 1, 10, 30),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.not_provided(),
        provenance=(ProvenanceRef("canonical", "segment-1"),),
    )
    second = FlightSegment(
        segment_id=SegmentId("segment-2"),
        marketing_carrier="MU",
        flight_number="5102",
        departure_airport="NKG",
        arrival_airport="SHA",
        departure_at=instant(2026, 9, 1, 11, 30),
        arrival_at=instant(2026, 9, 1, 13, 30),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.not_provided(),
        provenance=(ProvenanceRef("canonical", "segment-2"),),
    )
    itinerary = Itinerary(
        itinerary_id=ItineraryId("itinerary-1"),
        segment_ids=(first.segment_id, second.segment_id),
        provenance=(ProvenanceRef("canonical", "itinerary-1"),),
    )
    offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=itinerary.itinerary_id,
        total_price=Money(Decimal(980), "CNY"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue.known("BOOK-1"),
        provenance=(ProvenanceRef("canonical", "offer-1"),),
    )
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(2026, 8, 26, 8, 0),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("requested", "actual", CoverageStatus.COMPLETE),
        segments=(first, second),
        itineraries=(itinerary,),
        offers=(offer,),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def sample_requirement() -> RequirementState:
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
    )


def no_date_requirement() -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(2026, 8, 26, 8, 0),
    )


def other_requirement() -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-2"),
        recorded_at=instant(2026, 8, 26, 8, 0),
        constraints=(
            HardConstraint(
                constraint_id=ConstraintId("departure-date-other"),
                scope=ConstraintScope.DEPARTURE_DATE,
                operator=ConstraintOperator.EQUALS,
                value=LocalDate(date(2026, 9, 2)),
            ),
        ),
    )


def candidate_identity() -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(
        offer_id=OfferId("offer-1"),
        itinerary_id=ItineraryId("itinerary-1"),
    )


def instant(year: int, month: int, day: int, hour: int, minute: int) -> DomainInstant:
    return DomainInstant(datetime(year, month, day, hour, minute, tzinfo=UTC))
