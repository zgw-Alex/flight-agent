from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import apply_patch_proposal, commit_requirement_transition
from flight_agent.domain.decision import (
    STOP_COUNT,
    ConstraintEvaluationStatus,
    ConstraintReasonCode,
    DecisionPolicyVersion,
    DerivedFeatureRunId,
    DerivedFeatureSet,
    DerivedFeatureSetId,
    FilterResultDirection,
    FilterResultId,
    FilterRunId,
    MaxStopsConstraintEvaluator,
    RelaxationReasonCode,
    RelaxationResultDirection,
    RelaxationResultId,
    RelaxationRunId,
    m6_default_complete_filtering_engine,
    m6_default_derived_feature_engine,
    m6_default_deterministic_relaxation_engine,
    m6_default_feature_registry,
    m6_default_filter_evaluator_registry,
    m6_default_ranking_policy_set,
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
from flight_agent.domain.impact import (
    DataAction,
    HardConstraintSemanticEffect,
    ImpactAssetKind,
    ImpactReasonCode,
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
    RequirementDependencyKey,
    RequirementSemanticDiffer,
    SnapshotCompatibilityFacts,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    StopCount,
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
from flight_agent.ports import PatchProposalAction, PatchProposalOperation, PatchRequirementProposal


def test_ca01_gs01_to_gs04_domain_accepts_only_non_negative_integer_max_stops() -> None:
    assert max_stops_constraint(0).value == StopCount(0)
    assert max_stops_constraint(1).value == StopCount(1)

    with pytest.raises(DomainInvariantViolation):
        max_stops_constraint(-1)
    with pytest.raises(DomainInvariantViolation):
        StopCount(1.5)  # type: ignore[arg-type]
    with pytest.raises(DomainInvariantViolation):
        StopCount(True)  # type: ignore[arg-type]

    assert not hasattr(ConstraintScope, "DIRECT_FLIGHT")


def test_ca01_gs05_to_gs08_m3_patch_add_replace_remove_uses_existing_authority() -> None:
    repository = InMemoryRequirementRepository()
    current = requirement_with()
    repository.commit_initial(current, operation_id="initial")

    add = apply_patch_proposal(
        current,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.ADD_CONSTRAINT,
                    item=max_stops_constraint(1, "proposal-max-stops"),
                ),
            )
        ),
        recorded_at=instant(2),
    )
    assert add.requirement is not None
    committed = commit_requirement_transition(repository, current, add.requirement, "add-max-stops")
    assert committed.requirement is not None
    assert committed.requirement.version == RequirementVersion(2)
    assert committed.requirement.constraints[-1].constraint_id == ConstraintId("constraint-v2-1")

    replace_result = apply_patch_proposal(
        committed.requirement,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REPLACE_CONSTRAINT,
                    target_id=ConstraintId("constraint-v2-1"),
                    item=max_stops_constraint(0, "proposal-replace"),
                ),
            )
        ),
        recorded_at=instant(3),
    )
    assert replace_result.requirement is not None
    assert replace_result.requirement.version == RequirementVersion(3)
    assert max_stops_value(replace_result.requirement) == 0

    remove_result = apply_patch_proposal(
        replace_result.requirement,
        PatchRequirementProposal(
            operations=(
                PatchProposalOperation(
                    PatchProposalAction.REMOVE_CONSTRAINT,
                    target_id=ConstraintId("constraint-v2-1"),
                ),
            )
        ),
        recorded_at=instant(4),
    )
    assert remove_result.requirement is not None
    assert remove_result.requirement.constraints == current.constraints


def test_ca01_gs06_gs07_gs15_m7_comparator_and_impact_dependency_are_registered() -> None:
    before = requirement_with(max_stops_constraint(1))
    tightened = RequirementState(
        requirement_id=before.requirement_id,
        version=RequirementVersion(2),
        predecessor_version=RequirementVersion(1),
        recorded_at=instant(2),
        constraints=(max_stops_constraint(0),),
    )
    relaxed = RequirementState(
        requirement_id=before.requirement_id,
        version=RequirementVersion(2),
        predecessor_version=RequirementVersion(1),
        recorded_at=instant(2),
        constraints=(max_stops_constraint(2),),
    )

    tight_diff = RequirementSemanticDiffer().compare(before, tightened)
    relaxed_diff = RequirementSemanticDiffer().compare(before, relaxed)

    assert tight_diff.changes[0].hard_effect is HardConstraintSemanticEffect.TIGHTENED
    assert relaxed_diff.changes[0].hard_effect is HardConstraintSemanticEffect.RELAXED
    assert tight_diff.affected_dependency_keys == (RequirementDependencyKey("constraint.max_stops"),)

    impact = ImpactResolver().resolve(
        ImpactResolverInput(
            semantic_diff=tight_diff,
            snapshot=SnapshotCompatibilityFacts(snapshot=snapshot_from_stop_counts((0, 1)), required_scope_covered=True),
            artifacts=M6ArtifactFacts(
                feature_registry=m6_default_feature_registry(),
                ranking_policy_set=m6_default_ranking_policy_set(),
            ),
        )
    )
    filter_impact = impact.impact_for(ImpactAssetKind.FILTER_RESULT)
    assert filter_impact.required_action is DataAction.RECOMPUTE
    assert filter_impact.reason_codes == (ImpactReasonCode.FILTER_HARD_CONSTRAINT_CHANGED,)
    assert filter_impact.affected_dependency_keys == (RequirementDependencyKey("constraint.max_stops"),)


def test_ca01_gs09_to_gs12_filter_pass_fail_unknown_and_reason_lineage() -> None:
    snapshot = snapshot_from_stop_counts((0, 1, 2))
    requirement = requirement_with(max_stops_constraint(1))
    result = filter_result(snapshot, requirement)
    by_offer = {evaluation.candidate.offer_id: evaluation for evaluation in result.evaluations}

    assert isinstance(m6_default_filter_evaluator_registry().evaluator_for(max_stops_constraint(0)), MaxStopsConstraintEvaluator)
    assert by_offer[OfferId("offer-0")].status is ConstraintEvaluationStatus.PASS
    assert by_offer[OfferId("offer-1")].status is ConstraintEvaluationStatus.PASS
    assert by_offer[OfferId("offer-2")].status is ConstraintEvaluationStatus.FAIL
    assert by_offer[OfferId("offer-0")].reason_code is ConstraintReasonCode.MAX_STOPS_SATISFIED
    assert by_offer[OfferId("offer-2")].reason_code is ConstraintReasonCode.MAX_STOPS_EXCEEDED
    assert by_offer[OfferId("offer-1")].expected.label == "max_stops"
    assert by_offer[OfferId("offer-1")].actual.value.value == 1
    assert by_offer[OfferId("offer-1")].lineage.requirement_version == RequirementVersion(1)

    unknown = filter_result(
        snapshot,
        requirement_with(max_stops_constraint(0)),
        feature_set=feature_set_with_unknown_stop_count(snapshot, requirement_with(max_stops_constraint(0))),
    )
    unknown_eval = unknown.evaluations[0]
    assert unknown_eval.status is ConstraintEvaluationStatus.UNKNOWN
    assert unknown_eval.reason_code is ConstraintReasonCode.MAX_STOPS_INSUFFICIENT_EVIDENCE
    assert unknown.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED


def test_ca01_gs13_gs14_relaxation_uses_definitive_failures_and_excludes_unknown() -> None:
    snapshot = snapshot_from_stop_counts((1, 2))
    requirement = requirement_with(max_stops_constraint(0))
    feature_set = feature_set_for(snapshot, requirement)
    filtered = filter_result(snapshot, requirement, feature_set=feature_set)

    _, relaxation = m6_default_deterministic_relaxation_engine(
        m6_default_filter_evaluator_registry()
    ).analyze(
        relaxation_result_id=RelaxationResultId("relaxation-result-1"),
        relaxation_run_id=RelaxationRunId("relaxation-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=feature_set,
        filter_result=filtered,
        relaxation_policy=m6_default_relaxation_policy(),
    )

    assert relaxation.direction is RelaxationResultDirection.PROPOSALS_AVAILABLE
    assert relaxation.proposals[0].current_value == 0
    assert relaxation.proposals[0].proposed_value == 1
    assert relaxation.proposals[0].native_magnitude == 1
    assert relaxation.proposals[0].reason_code is RelaxationReasonCode.FILTER_EMPTY_MAX_STOPS_RELAXATION

    unknown_feature_set = feature_set_with_unknown_stop_count(snapshot, requirement)
    unknown_filter = filter_result(snapshot, requirement, feature_set=unknown_feature_set)
    _, unknown_relaxation = m6_default_deterministic_relaxation_engine(
        m6_default_filter_evaluator_registry()
    ).analyze(
        relaxation_result_id=RelaxationResultId("relaxation-result-unknown"),
        relaxation_run_id=RelaxationRunId("relaxation-run-unknown"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=unknown_feature_set,
        filter_result=unknown_filter,
        relaxation_policy=m6_default_relaxation_policy(),
    )

    assert unknown_filter.direction is FilterResultDirection.QUALIFICATION_UNRESOLVED
    assert unknown_relaxation.direction is RelaxationResultDirection.NOT_TRIGGERED
    assert unknown_relaxation.proposals == ()


def test_ca01_gs16_soft_fewer_stops_remains_ranking_preference_not_filter_constraint() -> None:
    snapshot = snapshot_from_stop_counts((0, 1))
    requirement = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        preferences=(
            SoftPreference(
                PreferenceId("prefer-direct"),
                PreferenceScope.FEWER_STOPS,
                PreferenceImportance.HIGH,
            ),
        ),
    )
    result = filter_result(snapshot, requirement)

    assert result.evaluations == ()
    assert result.direction is FilterResultDirection.QUALIFIED_AVAILABLE
    assert result.qualified_candidates == (
        candidate(0),
        candidate(1),
    )


def filter_result(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
    *,
    feature_set: DerivedFeatureSet | None = None,
):
    resolved_feature_set = feature_set or feature_set_for(snapshot, requirement)
    return m6_default_complete_filtering_engine().filter(
        filter_result_id=FilterResultId("filter-result-1"),
        filter_run_id=FilterRunId("filter-run-1"),
        requirement=requirement,
        snapshot=snapshot,
        feature_set=resolved_feature_set,
        filter_policy_version=DecisionPolicyVersion("filter-policy-v1"),
    )[1]


def feature_set_for(snapshot: CandidateSnapshot, requirement: RequirementState) -> DerivedFeatureSet:
    return m6_default_derived_feature_engine().compute(
        feature_set_id=DerivedFeatureSetId("feature-set-1"),
        run_id=DerivedFeatureRunId("feature-run-1"),
        requested_feature_keys=(STOP_COUNT,),
        snapshot=snapshot,
        requirement=requirement,
        feature_policy_version=DecisionPolicyVersion("derived-feature-policy-v1"),
    )[1]


def feature_set_with_unknown_stop_count(
    snapshot: CandidateSnapshot,
    requirement: RequirementState,
) -> DerivedFeatureSet:
    feature_set = feature_set_for(snapshot, requirement)
    first = feature_set.values[0]
    replacement = replace(first, value=DomainValue.not_provided())
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


def requirement_with(*constraints: HardConstraint) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=constraints,
    )


def max_stops_constraint(value: int, raw_id: str = "max-stops") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_STOPS,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=StopCount(value),
    )


def max_stops_value(requirement: RequirementState) -> int:
    value = next(
        constraint.value
        for constraint in requirement.constraints
        if constraint.scope is ConstraintScope.MAX_STOPS
    )
    assert isinstance(value, StopCount)
    return value.value


def snapshot_from_stop_counts(stop_counts: tuple[int, ...]) -> CandidateSnapshot:
    segments: list[FlightSegment] = []
    itineraries: list[Itinerary] = []
    offers: list[Offer] = []
    for stop_count in stop_counts:
        route = ("PEK", "NKG", "CAN", "SHA")
        segment_ids = tuple(
            SegmentId(f"segment-{stop_count}-{index}")
            for index in range(stop_count + 1)
        )
        for index, segment_id in enumerate(segment_ids):
            segments.append(
                FlightSegment(
                    segment_id=segment_id,
                    marketing_carrier="MU",
                    flight_number=f"5{stop_count}{index}",
                    departure_airport=route[index],
                    arrival_airport=route[index + 1],
                    departure_at=instant(1),
                    arrival_at=instant(2),
                    operating_carrier=DomainValue.known("MU"),
                    aircraft_type=DomainValue.not_provided(),
                    provenance=(ProvenanceRef("canonical", segment_id.value),),
                )
            )
        itinerary = Itinerary(
            itinerary_id=ItineraryId(f"itinerary-{stop_count}"),
            segment_ids=segment_ids,
            provenance=(ProvenanceRef("canonical", f"itinerary-{stop_count}"),),
        )
        offer = Offer(
            offer_id=OfferId(f"offer-{stop_count}"),
            itinerary_id=itinerary.itinerary_id,
            total_price=Money(Decimal(800 + stop_count), "CNY"),
            offer_freshness=OfferFreshness(FreshnessState.FRESH),
            booking_reference=DomainValue.known(f"BOOK-{stop_count}"),
            provenance=(ProvenanceRef("canonical", f"offer-{stop_count}"),),
        )
        itineraries.append(itinerary)
        offers.append(offer)
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(1),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("requested", "actual", CoverageStatus.COMPLETE),
        segments=tuple(segments),
        itineraries=tuple(itineraries),
        offers=tuple(offers),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def candidate(stop_count: int):
    return flight_candidate(f"offer-{stop_count}", f"itinerary-{stop_count}")


def flight_candidate(offer_id: str, itinerary_id: str):
    from flight_agent.domain.decision import OfferBackedItineraryCandidate

    return OfferBackedItineraryCandidate(OfferId(offer_id), ItineraryId(itinerary_id))


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))
