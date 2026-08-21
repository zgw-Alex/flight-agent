from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

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
from flight_agent.domain.integrity import (
    validate_evidence_ref,
    validate_execution_requirement_lineage,
    validate_explanation_against_recommendation,
    validate_publication_lineage,
    validate_recommendation_against_snapshot,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
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
    AgentExecution,
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    ExecutionStatus,
    ExplanationResult,
    ExplanationResultId,
    ExplanationStatement,
    ExplanationStatementKind,
    PublicationId,
    PublishedRecommendation,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def instant(hour: int = 9) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 21, hour, 0, tzinfo=UTC))


def requirement(version: RequirementVersion = RequirementVersion(3)) -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-1"),
        version=version,
        predecessor_version=RequirementVersion(version.value - 1) if version.value > 1 else None,
        recorded_at=instant(7),
        constraints=(
            HardConstraint(
                constraint_id=ConstraintId("constraint-date"),
                scope=ConstraintScope.DEPARTURE_DATE,
                operator=ConstraintOperator.EQUALS,
                value=LocalDate(date(2026, 9, 1)),
            ),
        ),
        preferences=(
            SoftPreference(
                preference_id=PreferenceId("preference-time"),
                scope=PreferenceScope.DEPARTURE_TIME,
                importance=PreferenceImportance.HIGH,
                value=LocalTime(time(9, 0)),
            ),
        ),
    )


def segment(raw_id: str = "segment-1") -> FlightSegment:
    return FlightSegment(
        segment_id=SegmentId(raw_id),
        marketing_carrier="MU",
        flight_number="588",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue[str].unknown(),
    )


def itinerary(
    raw_id: str = "itinerary-1", segment_ids: tuple[SegmentId, ...] = (SegmentId("segment-1"),)
) -> Itinerary:
    return Itinerary(ItineraryId(raw_id), segment_ids)


def offer(raw_id: str = "offer-1", itinerary_id: ItineraryId = ItineraryId("itinerary-1")) -> Offer:
    return Offer(
        offer_id=OfferId(raw_id),
        itinerary_id=itinerary_id,
        total_price=Money(Decimal("900"), "USD"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(ProvenanceRef("provider-search", "offer-row"),),
    )


def snapshot(
    *,
    snapshot_id: CandidateSnapshotId = CandidateSnapshotId("snapshot-1"),
    version: SnapshotVersion = SnapshotVersion(1),
    coverage: Coverage | None = None,
    segments: tuple[FlightSegment, ...] = (segment(),),
    itineraries: tuple[Itinerary, ...] = (itinerary(),),
    offers: tuple[Offer, ...] = (offer(),),
) -> CandidateSnapshot:
    return CandidateSnapshot(
        snapshot_id=snapshot_id,
        version=version,
        created_at=instant(8),
        created_from_requirement_version=RequirementVersion(3),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=coverage
        or Coverage("requested PVG-LAX", "actual PVG-LAX", CoverageStatus.COMPLETE),
        segments=segments,
        itineraries=itineraries,
        offers=offers,
    )


def execution(requirement_version: RequirementVersion = RequirementVersion(3)) -> AgentExecution:
    return AgentExecution(
        execution_id=ExecutionId("execution-1"),
        status=ExecutionStatus.COMPLETED,
        based_on_requirement_version=requirement_version,
        created_at=instant(9),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(1),
    )


def recommendation(
    *,
    status: RecommendationResultStatus = RecommendationResultStatus.EXACT_MATCH,
    items: tuple[RecommendationItem, ...] | None = None,
    execution_id: ExecutionId = ExecutionId("execution-1"),
    requirement_version: RequirementVersion = RequirementVersion(3),
    snapshot_id: CandidateSnapshotId = CandidateSnapshotId("snapshot-1"),
    snapshot_version: SnapshotVersion = SnapshotVersion(1),
) -> RecommendationResult:
    if items is None and status is not RecommendationResultStatus.NO_MATCH:
        items = (
            RecommendationItem(
                ItineraryId("itinerary-1"),
                OfferId("offer-1"),
                roles=(RecommendationRole.BEST_OVERALL,),
                evidence=(EvidenceRef(EvidenceSource.OFFER, OfferId("offer-1")),),
            ),
        )
    return RecommendationResult(
        recommendation_result_id=RecommendationResultId("recommendation-1"),
        status=status,
        execution_id=execution_id,
        based_on_requirement_version=requirement_version,
        snapshot_id=snapshot_id,
        snapshot_version=snapshot_version,
        generated_at=instant(10),
        items=items or (),
    )


def explanation(
    *,
    recommendation_result_id: RecommendationResultId = RecommendationResultId("recommendation-1"),
    execution_id: ExecutionId = ExecutionId("execution-1"),
    requirement_version: RequirementVersion = RequirementVersion(3),
    snapshot_id: CandidateSnapshotId = CandidateSnapshotId("snapshot-1"),
    snapshot_version: SnapshotVersion = SnapshotVersion(1),
    evidence: tuple[EvidenceRef, ...] = (
        EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId("constraint-date")),
    ),
) -> ExplanationResult:
    return ExplanationResult(
        explanation_result_id=ExplanationResultId("explanation-1"),
        recommendation_result_id=recommendation_result_id,
        execution_id=execution_id,
        based_on_requirement_version=requirement_version,
        snapshot_id=snapshot_id,
        snapshot_version=snapshot_version,
        generated_at=instant(11),
        statements=(
            ExplanationStatement(
                ExplanationStatementKind.MATCH,
                evidence=evidence,
                rendered_text="Evidence projection for display.",
            ),
        ),
    )


def test_positive_full_chain_with_explanation_and_publication_passes() -> None:
    req = requirement()
    snap = snapshot()
    exec_ = execution()
    rec = recommendation()
    exp = explanation()
    pub = PublishedRecommendation.from_recommendation(
        PublicationId("publication-1"), rec, instant(12), explanation=exp
    )

    validate_execution_requirement_lineage(exec_, req)
    validate_recommendation_against_snapshot(rec, exec_, snap)
    validate_explanation_against_recommendation(exp, rec, req, snap)
    validate_publication_lineage(pub, rec, exp)


def test_positive_no_match_chain_and_publication_without_explanation_passes() -> None:
    req = requirement()
    snap = snapshot()
    exec_ = execution()
    rec = recommendation(status=RecommendationResultStatus.NO_MATCH)
    pub = PublishedRecommendation.from_recommendation(PublicationId("publication-1"), rec, instant(12))

    validate_execution_requirement_lineage(exec_, req)
    validate_recommendation_against_snapshot(rec, exec_, snap)
    validate_publication_lineage(pub, rec)


def test_empty_and_partial_snapshots_are_not_business_failures_in_u5() -> None:
    req = requirement()
    exec_ = execution()
    empty = snapshot(segments=(), itineraries=(), offers=())
    partial = snapshot(
        coverage=Coverage(
            "requested all providers",
            "actual provider subset",
            CoverageStatus.PARTIAL,
            (CoverageLimitation("PROVIDER_LIMIT", "Provider subset only"),),
        )
    )
    no_match = recommendation(status=RecommendationResultStatus.NO_MATCH)

    validate_execution_requirement_lineage(exec_, req)
    validate_recommendation_against_snapshot(no_match, exec_, empty)
    validate_recommendation_against_snapshot(recommendation(), exec_, partial)


def test_wrong_typed_identity_rejected_in_cross_contract_scenario() -> None:
    wrong_item = RecommendationItem(
        itinerary_id=ItineraryId("itinerary-1"),
        primary_offer_id=ItineraryId("itinerary-1"),  # type: ignore[arg-type]
        roles=(RecommendationRole.FALLBACK,),
    )
    rec = recommendation(items=(wrong_item,))

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(rec, execution(), snapshot())


def test_broken_snapshot_reference_rejected_for_identity_and_version() -> None:
    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(
            recommendation(snapshot_id=CandidateSnapshotId("other-snapshot")),
            execution(),
            snapshot(),
        )

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(
            recommendation(snapshot_version=SnapshotVersion(2)),
            execution(),
            snapshot(),
        )


def test_broken_offer_reference_rejected() -> None:
    rec = recommendation(
        items=(
            RecommendationItem(
                ItineraryId("itinerary-1"),
                OfferId("missing-offer"),
                roles=(RecommendationRole.BEST_OVERALL,),
            ),
        )
    )

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(rec, execution(), snapshot())


def test_recommendation_cross_snapshot_rejected() -> None:
    snapshot_b = snapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        segments=(segment("segment-b"),),
        itineraries=(itinerary("itinerary-b", (SegmentId("segment-b"),)),),
        offers=(offer("offer-b", ItineraryId("itinerary-b")),),
    )

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(recommendation(), execution(), snapshot_b)


def test_offer_itinerary_mismatch_rejected() -> None:
    snap = snapshot(
        segments=(segment(),),
        itineraries=(
            itinerary("itinerary-1"),
            itinerary("itinerary-2"),
        ),
        offers=(offer("offer-1", ItineraryId("itinerary-2")),),
    )

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(recommendation(), execution(), snap)


def test_execution_requirement_version_mismatch_rejected() -> None:
    with pytest.raises(DomainInvariantViolation):
        validate_execution_requirement_lineage(execution(RequirementVersion(2)), requirement())

    with pytest.raises(DomainInvariantViolation):
        validate_recommendation_against_snapshot(
            recommendation(requirement_version=RequirementVersion(2)),
            execution(),
            snapshot(),
        )


def test_explanation_without_evidence_and_invalid_evidence_context_rejected() -> None:
    with pytest.raises(DomainInvariantViolation):
        ExplanationStatement(ExplanationStatementKind.MATCH, evidence=())

    exp = explanation(evidence=(EvidenceRef(EvidenceSource.OFFER, OfferId("missing-offer")),))
    with pytest.raises(DomainInvariantViolation):
        validate_explanation_against_recommendation(exp, recommendation(), requirement(), snapshot())


def test_publication_cross_lineage_rejected() -> None:
    rec = recommendation()
    bad_publication = PublishedRecommendation(
        publication_id=PublicationId("publication-1"),
        recommendation_result_id=RecommendationResultId("other-recommendation"),
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(1),
        published_at=instant(12),
    )

    with pytest.raises(DomainInvariantViolation):
        validate_publication_lineage(bad_publication, rec)

    mismatched_explanation = explanation(execution_id=ExecutionId("other-execution"))
    with pytest.raises(DomainInvariantViolation):
        validate_publication_lineage(
            PublishedRecommendation(
                publication_id=PublicationId("publication-2"),
                recommendation_result_id=RecommendationResultId("recommendation-1"),
                execution_id=ExecutionId("execution-1"),
                based_on_requirement_version=RequirementVersion(3),
                snapshot_id=CandidateSnapshotId("snapshot-1"),
                snapshot_version=SnapshotVersion(1),
                published_at=instant(12),
                explanation_result_id=ExplanationResultId("explanation-1"),
            ),
            rec,
            mismatched_explanation,
        )


def test_boundary_preserves_policy_separation_and_does_not_mutate_artifacts() -> None:
    req = requirement()
    snap = snapshot()
    exec_ = execution()
    rec = recommendation()
    original_items = rec.items

    validate_recommendation_against_snapshot(rec, exec_, snap)
    validate_evidence_ref(EvidenceRef(EvidenceSource.RECOMMENDATION, rec.recommendation_result_id), req, snap, rec)

    assert rec.items is original_items
    assert not hasattr(rec.items[0].roles[0], "is_role_correct")
    assert not hasattr(snap, "reuse_policy")
    assert not hasattr(req, "search_readiness")
