from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

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
    CoverageLimitation,
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
    ImpactAssetKind,
    ImpactCompatibility,
    ImpactReasonCode,
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
    RequirementSemanticDiffer,
    SnapshotCompatibilityFacts,
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
    DomainInstant,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)


def test_ranking_importance_change_reuses_snapshot_and_filter_recomputes_ranking_recommendation() -> None:
    decision = impact(
        before=requirement(version=1, preferences=(price_preference(PreferenceImportance.LOW),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            preferences=(price_preference(PreferenceImportance.HIGH),),
        ),
    )

    assert decision.primary_data_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).required_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.FILTER_RESULT).required_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.RANKING_RESULT).required_action is DataAction.RECOMPUTE
    assert decision.impact_for(ImpactAssetKind.RECOMMENDATION_RESULT).required_action is DataAction.RECOMPUTE


def test_max_price_tighten_with_sufficient_coverage_reuses_snapshot_and_recomputes_filter_downstream() -> None:
    decision = impact(
        before=requirement(version=1, constraints=(max_price(1500),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1200),),
        ),
    )

    assert decision.primary_data_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).compatibility is ImpactCompatibility.COMPATIBLE
    assert decision.impact_for(ImpactAssetKind.FILTER_RESULT).reason_codes == (
        ImpactReasonCode.FILTER_HARD_CONSTRAINT_CHANGED,
    )
    assert decision.impact_for(ImpactAssetKind.RANKING_RESULT).required_action is DataAction.RECOMPUTE


def test_max_price_relaxation_does_not_force_search_with_compatible_snapshot() -> None:
    decision = impact(
        before=requirement(version=1, constraints=(max_price(1200),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1500),),
        ),
    )

    assert decision.primary_data_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).required_action is not DataAction.SEARCH


def test_route_or_date_change_searches_when_actual_coverage_is_insufficient() -> None:
    decision = impact(
        before=requirement(version=1, constraints=(origin("PVG"),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(origin("SHA"),),
        ),
        required_scope_covered=False,
    )

    snapshot = decision.impact_for(ImpactAssetKind.SNAPSHOT)

    assert snapshot.required_action is DataAction.SEARCH
    assert snapshot.reason_codes == (ImpactReasonCode.SNAPSHOT_COVERAGE_INSUFFICIENT,)


def test_route_or_date_change_reuses_snapshot_when_actual_coverage_still_covers_scope() -> None:
    decision = impact(
        before=requirement(version=1, constraints=(departure_date(2026, 9, 1),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(departure_date(2026, 9, 2),),
        ),
        required_scope_covered=True,
    )

    assert decision.primary_data_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).required_action is DataAction.REUSE
    assert decision.impact_for(ImpactAssetKind.DERIVED_FEATURE_SET).required_action is DataAction.RECOMPUTE


def test_offer_stale_with_fresh_structure_prefers_refresh() -> None:
    decision = impact(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        snapshot=sample_snapshot(offer_freshness=FreshnessState.STALE),
    )

    assert decision.primary_data_action is DataAction.REFRESH
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).reason_codes == (
        ImpactReasonCode.SNAPSHOT_OFFER_STALE,
    )


def test_required_external_fact_missing_requires_enrich_not_derived_recompute() -> None:
    decision = impact(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        missing_external_fact_keys=("baggage_fee",),
    )

    assert decision.primary_data_action is DataAction.ENRICH
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).required_action is DataAction.ENRICH


def test_mapper_or_normalizer_incompatibility_with_usable_raw_rebuilds_from_raw() -> None:
    decision = impact(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        pipeline_compatible=False,
        raw_evidence_usable=True,
    )

    assert decision.primary_data_action is DataAction.REBUILD_FROM_RAW
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).reason_codes == (
        ImpactReasonCode.SNAPSHOT_PIPELINE_INCOMPATIBLE_RAW_USABLE,
    )


def test_partial_coverage_can_be_sufficient_or_insufficient_for_required_scope() -> None:
    sufficient = impact(
        before=requirement(version=1, constraints=(origin("PVG"),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(origin("SHA"),),
        ),
        snapshot=sample_snapshot(coverage_status=CoverageStatus.PARTIAL),
        required_scope_covered=True,
    )
    insufficient = impact(
        before=requirement(version=1, constraints=(origin("PVG"),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(origin("SHA"),),
        ),
        snapshot=sample_snapshot(coverage_status=CoverageStatus.PARTIAL),
        required_scope_covered=False,
    )

    assert sufficient.primary_data_action is DataAction.REUSE
    assert insufficient.primary_data_action is DataAction.SEARCH


def test_policy_reference_version_invalidation_is_conservative_and_per_asset() -> None:
    decision = impact(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        feature_reference_compatible=False,
        recommendation_policy_compatible=False,
    )

    assert decision.impact_for(ImpactAssetKind.DERIVED_FEATURE_SET).required_action is DataAction.RECOMPUTE
    assert decision.impact_for(ImpactAssetKind.RECOMMENDATION_RESULT).reason_codes == (
        ImpactReasonCode.RECOMMENDATION_POLICY_CHANGED,
    )
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).required_action is DataAction.REUSE


def test_unknown_compatibility_uses_unknown_and_safe_recomputation_or_reacquisition() -> None:
    decision = impact(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        pipeline_compatible=None,
        feature_policy_compatible=None,
    )

    assert decision.primary_data_action is DataAction.SEARCH
    assert decision.impact_for(ImpactAssetKind.SNAPSHOT).compatibility is ImpactCompatibility.UNKNOWN
    assert decision.impact_for(ImpactAssetKind.DERIVED_FEATURE_SET).compatibility is ImpactCompatibility.UNKNOWN
    assert decision.impact_for(ImpactAssetKind.DERIVED_FEATURE_SET).required_action is DataAction.RECOMPUTE


def test_historical_artifacts_are_immutable_and_not_deleted_or_mutated() -> None:
    snapshot = sample_snapshot()
    before_snapshot = snapshot
    decision = impact(
        before=requirement(version=1, constraints=(max_price(1500),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1200),),
        ),
        snapshot=snapshot,
    )

    with pytest.raises(FrozenInstanceError):
        decision.asset_impacts = ()  # type: ignore[misc]
    assert snapshot == before_snapshot
    assert not hasattr(snapshot, "invalidated")
    assert not hasattr(snapshot, "deleted")


def test_impact_decision_does_not_leak_execution_plan_or_runtime_publication() -> None:
    decision = impact(
        before=requirement(version=1, preferences=(price_preference(PreferenceImportance.LOW),)),
        after=requirement(
            version=2,
            predecessor=RequirementVersion(1),
            preferences=(price_preference(PreferenceImportance.HIGH),),
        ),
    )
    source = repr(decision)

    assert not hasattr(decision, "execution_plan")
    assert not hasattr(decision, "stage_dispositions")
    assert not hasattr(decision, "provider_invocation")
    assert not hasattr(decision, "publication_guard")
    assert "GUARDED_ATTEMPT" not in source


def impact(
    *,
    before: RequirementState,
    after: RequirementState,
    snapshot: CandidateSnapshot | None = None,
    required_scope_covered: bool | None = True,
    pipeline_compatible: bool | None = True,
    raw_evidence_usable: bool = False,
    missing_external_fact_keys: tuple[str, ...] = (),
    feature_policy_compatible: bool | None = True,
    feature_reference_compatible: bool | None = True,
    recommendation_policy_compatible: bool | None = True,
):
    diff = RequirementSemanticDiffer().compare(before, after)
    return ImpactResolver().resolve(
        ImpactResolverInput(
            semantic_diff=diff,
            snapshot=SnapshotCompatibilityFacts(
                snapshot=snapshot or sample_snapshot(),
                required_scope_covered=required_scope_covered,
                pipeline_compatible=pipeline_compatible,
                raw_evidence_usable=raw_evidence_usable,
                missing_external_fact_keys=missing_external_fact_keys,
            ),
            artifacts=M6ArtifactFacts(
                feature_registry=m6_default_feature_registry(),
                ranking_policy_set=m6_default_ranking_policy_set(),
                active_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT,),
                feature_policy_compatible=feature_policy_compatible,
                feature_reference_compatible=feature_reference_compatible,
                recommendation_policy_compatible=recommendation_policy_compatible,
            ),
        )
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
    coverage_status: CoverageStatus = CoverageStatus.COMPLETE,
    structural_freshness: FreshnessState = FreshnessState.FRESH,
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
    limitations = (
        (CoverageLimitation("limited_market", "Fixture only covers selected market"),)
        if coverage_status is CoverageStatus.PARTIAL
        else ()
    )
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(structural_freshness),
        coverage=Coverage(
            requested_scope="PVG-LAX",
            actual_coverage="PVG-LAX",
            status=coverage_status,
            limitations=limitations,
        ),
        segments=(segment,),
        itineraries=(itinerary,),
        offers=(offer,),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
