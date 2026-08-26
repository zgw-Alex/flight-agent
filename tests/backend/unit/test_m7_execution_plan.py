from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from flight_agent.domain.decision import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    m6_default_feature_registry,
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
from flight_agent.domain.impact import (
    DataAction,
    ExecutionPlan,
    ExecutionPlanArtifactRef,
    ExecutionPlanBuilder,
    ExecutionPlanInput,
    ExecutionPlanReasonCode,
    ExecutionPlanVersionRef,
    ExecutionStage,
    ImpactAssetKind,
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
    RequirementSemanticDiffer,
    SnapshotCompatibilityFacts,
    StageDisposition,
)
from flight_agent.domain.requirements import (
    AirportCode,
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
    DomainId,
    DomainInstant,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)


def test_reuse_only_plan_preserves_versions_refs_and_deterministic_stage_order() -> None:
    plan = execution_plan(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
    )

    assert plan.requirement_id == RequirementId("requirement-1")
    assert plan.requirement_version == RequirementVersion(2)
    assert plan.selected_data_action is DataAction.REUSE
    assert plan.selected_snapshot_ref.artifact_id == CandidateSnapshotId("snapshot-1")
    assert plan.policy_versions == (
        ExecutionPlanVersionRef(name="feature-registry", version="m6-default"),
        ExecutionPlanVersionRef(name="ranking-policy", version="m6-default"),
    )
    assert tuple(stage.stage for stage in plan.stages) == (
        ExecutionStage.DATA_ACQUISITION,
        ExecutionStage.DERIVED_FEATURES,
        ExecutionStage.FILTER,
        ExecutionStage.RANKING,
        ExecutionStage.RECOMMENDATION,
        ExecutionStage.RELAXATION,
        ExecutionStage.EXPLANATION,
    )
    assert {stage.disposition for stage in plan.stages} == {StageDisposition.REUSE}
    assert plan.stage(ExecutionStage.RANKING).reused_artifact_ref == artifact_ref(
        ImpactAssetKind.RANKING_RESULT,
        "ranking-result-1",
    )


def test_preference_only_plan_reuses_data_filter_and_recomputes_ranking_recommendation() -> None:
    plan = execution_plan(
        before=requirement(version=1, preferences=(price_preference(PreferenceImportance.LOW),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            preferences=(price_preference(PreferenceImportance.HIGH),),
        ),
    )

    assert plan.stage(ExecutionStage.DATA_ACQUISITION).disposition is StageDisposition.REUSE
    assert plan.stage(ExecutionStage.FILTER).disposition is StageDisposition.REUSE
    assert plan.stage(ExecutionStage.RANKING).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RECOMMENDATION).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.FILTER).reused_artifact_ref == artifact_ref(
        ImpactAssetKind.FILTER_RESULT,
        "filter-result-1",
    )


def test_hard_constraint_plan_reuses_snapshot_and_runs_filter_downstream() -> None:
    plan = execution_plan(
        before=requirement(version=1, constraints=(max_price(1500),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1200),),
        ),
    )

    assert plan.selected_data_action is DataAction.REUSE
    assert plan.stage(ExecutionStage.DERIVED_FEATURES).disposition is StageDisposition.REUSE
    assert plan.stage(ExecutionStage.FILTER).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RANKING).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RECOMMENDATION).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RELAXATION).disposition is StageDisposition.RUN


@pytest.mark.parametrize(
    ("kwargs", "expected_action", "expected_reason"),
    (
        (
            {"required_scope_covered": False, "scope_changes": True},
            DataAction.SEARCH,
            ExecutionPlanReasonCode.DATA_SEARCH,
        ),
        (
            {"offer_stale": True},
            DataAction.REFRESH,
            ExecutionPlanReasonCode.DATA_REFRESH,
        ),
        (
            {"pipeline_compatible": False, "raw_evidence_usable": True},
            DataAction.REBUILD_FROM_RAW,
            ExecutionPlanReasonCode.DATA_REBUILD_FROM_RAW,
        ),
    ),
)
def test_data_action_plan_runs_data_stage_and_downstream_artifacts(
    kwargs: dict[str, Any],
    expected_action: DataAction,
    expected_reason: ExecutionPlanReasonCode,
) -> None:
    scope_changes = bool(kwargs.pop("scope_changes", False))
    offer_stale = bool(kwargs.pop("offer_stale", False))
    before = requirement(version=1, constraints=(origin("PVG"),)) if scope_changes else requirement(version=1)
    after_constraints = (origin("SHA"),) if scope_changes else ()
    plan = execution_plan(
        before=before,
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=after_constraints,
        ),
        snapshot=sample_snapshot(offer_freshness=FreshnessState.STALE) if offer_stale else None,
        **kwargs,
    )

    assert plan.selected_data_action is expected_action
    data_stage = plan.stage(ExecutionStage.DATA_ACQUISITION)
    assert data_stage.disposition is StageDisposition.RUN
    assert expected_reason in data_stage.reason_codes
    assert plan.stage(ExecutionStage.DERIVED_FEATURES).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.FILTER).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RANKING).disposition is StageDisposition.RUN
    assert plan.stage(ExecutionStage.RECOMMENDATION).disposition is StageDisposition.RUN


def test_missing_optional_reuse_ref_becomes_not_applicable_not_skip() -> None:
    plan = execution_plan(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        include_relaxation_ref=False,
    )

    assert plan.stage(ExecutionStage.RELAXATION).disposition is StageDisposition.NOT_APPLICABLE
    assert "SKIP" not in repr(plan)


def test_execution_plan_is_immutable_and_does_not_leak_orchestrator_runtime_publication() -> None:
    plan = execution_plan(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
    )
    source = repr(plan)

    with pytest.raises(FrozenInstanceError):
        plan.stages = ()  # type: ignore[misc]
    assert not hasattr(plan, "status")
    assert not hasattr(plan, "execution_status")
    assert not hasattr(plan, "provider_invocation")
    assert not hasattr(plan, "publication_guard")
    assert not hasattr(plan, "supersede")
    assert "GUARDED_ATTEMPT" not in source


def execution_plan(
    *,
    before: RequirementState,
    after: RequirementState,
    snapshot: CandidateSnapshot | None = None,
    required_scope_covered: bool | None = True,
    pipeline_compatible: bool | None = True,
    raw_evidence_usable: bool = False,
    include_relaxation_ref: bool = True,
) -> ExecutionPlan:
    snapshot = snapshot or sample_snapshot()
    diff = RequirementSemanticDiffer().compare(before, after)
    decision = ImpactResolver().resolve(
        ImpactResolverInput(
            semantic_diff=diff,
            snapshot=SnapshotCompatibilityFacts(
                snapshot=snapshot,
                required_scope_covered=required_scope_covered,
                pipeline_compatible=pipeline_compatible,
                raw_evidence_usable=raw_evidence_usable,
            ),
            artifacts=M6ArtifactFacts(
                feature_registry=m6_default_feature_registry(),
                ranking_policy_set=m6_default_ranking_policy_set(),
                active_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT,),
            ),
        )
    )
    reusable_refs = (
        artifact_ref(ImpactAssetKind.DERIVED_FEATURE_SET, "derived-feature-set-1"),
        artifact_ref(ImpactAssetKind.FILTER_RESULT, "filter-result-1"),
        artifact_ref(ImpactAssetKind.RANKING_RESULT, "ranking-result-1"),
        artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-result-1"),
        artifact_ref(ImpactAssetKind.EXPLANATION, "explanation-1"),
    )
    if include_relaxation_ref:
        reusable_refs = (
            *reusable_refs,
            artifact_ref(ImpactAssetKind.RELAXATION_RESULT, "relaxation-result-1"),
        )
    return ExecutionPlanBuilder().build(
        ExecutionPlanInput(
            impact_decision=decision,
            selected_snapshot_ref=ExecutionPlanArtifactRef(
                asset_kind=ImpactAssetKind.SNAPSHOT,
                artifact_id=snapshot.snapshot_id,
                version=str(snapshot.version.value),
            ),
            reusable_artifact_refs=reusable_refs,
            policy_versions=(
                ExecutionPlanVersionRef(name="ranking-policy", version="m6-default"),
                ExecutionPlanVersionRef(name="feature-registry", version="m6-default"),
            ),
            reference_data_versions=(
                ExecutionPlanVersionRef(name="airports", version="2026-08-01"),
            ),
        )
    )


def artifact_ref(asset_kind: ImpactAssetKind, artifact_id: str) -> ExecutionPlanArtifactRef:
    return ExecutionPlanArtifactRef(
        asset_kind=asset_kind,
        artifact_id=DomainId(artifact_id),
        version="1",
    )


def requirement(
    *,
    version: int,
    predecessor: RequirementVersion | None = None,
    constraints: tuple[HardConstraint, ...] = (),
    preferences: tuple[SoftPreference, ...] = (),
) -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-1"),
        version=RequirementVersion(version),
        predecessor_version=predecessor,
        recorded_at=instant(),
        constraints=constraints,
        preferences=preferences,
    )


def max_price(amount: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-max-price"),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(amount), "CNY"),
    )


def origin(airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-origin"),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def departure_date(year: int, month: int, day: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(year, month, day)),
    )


def price_preference(importance: PreferenceImportance) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=importance,
    )


def sample_snapshot(
    *,
    offer_freshness: FreshnessState = FreshnessState.FRESH,
) -> CandidateSnapshot:
    segment = FlightSegment(
        segment_id=SegmentId("segment-1"),
        marketing_carrier="MU",
        flight_number="5101",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(),
        arrival_at=DomainInstant(datetime(2026, 9, 1, 20, 0, tzinfo=UTC)),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.not_provided(),
        provenance=(ProvenanceRef("canonical", "segment-1"),),
    )
    itinerary = Itinerary(
        itinerary_id=ItineraryId("itinerary-1"),
        segment_ids=(segment.segment_id,),
        provenance=(ProvenanceRef("canonical", "itinerary-1"),),
    )
    offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=itinerary.itinerary_id,
        total_price=Money(Decimal(980), "CNY"),
        offer_freshness=OfferFreshness(offer_freshness),
        booking_reference=DomainValue.known("BOOK-1"),
        provenance=(ProvenanceRef("canonical", "offer-1"),),
    )
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage(
            requested_scope="PVG-LAX",
            actual_coverage="PVG-LAX",
            status=CoverageStatus.COMPLETE,
        ),
        segments=(segment,),
        itineraries=(itinerary,),
        offers=(offer,),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
