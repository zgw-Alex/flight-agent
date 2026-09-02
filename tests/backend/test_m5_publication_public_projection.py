from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fastapi.testclient import TestClient

from flight_agent.application.minimal_decision import MinimalDecisionResult, MinimalDecisionStatus
from flight_agent.application.publication import PublicWorkflowOutcome, PublishRecommendation
from flight_agent.bootstrap.app import create_app
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
from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.domain.workflow import (
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def test_happy_path_publishes_current_recommendation_public_projection() -> None:
    app = create_app()
    client = TestClient(app)

    body = start(
        client,
        origin="PEK",
        destination="SHA",
        departure_date="2026-09-01",
        max_price_cny=1200,
    )
    read_model = read(client, body["conversation_id"])

    assert read_model["outcome"] == "PUBLISHED"
    published = cast(dict[str, Any], read_model["current_published_recommendation"])
    assert published is not None
    assert published["publication_id"] != published["recommendation_result_id"]
    assert published["requirement_id"] == body["requirement_id"]
    assert published["requirement_version"] == 1
    assert published["execution_id"] == body["execution_id"]
    assert published["snapshot_id"]
    assert published["snapshot_version"] == 1
    assert published["route_origin"] == "PEK"
    assert published["route_destination"] == "SHA"
    assert published["departure_date"] == "2026-09-01"
    assert published["selected_price_amount"] == "980"
    assert published["selected_price_currency"] == "CNY"
    assert published["selected_price_semantics"] == "EXACT"
    assert published["role"] == "BEST_OVERALL"
    assert published["reason"]
    assert published["evidence"]
    assert app.state.publication_repository.get_conversation(body["conversation_id"]) is not None


def test_publication_record_preserves_lower_bound_price_semantics() -> None:
    snapshot = candidate_snapshot_with_price_semantics(PriceSemantics.LOWER_BOUND)
    decision_result = recommended_decision(snapshot)
    published_at = DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
    publisher = PublishRecommendation(
        id_factory=lambda: "publication-1",
        published_at=lambda: published_at,
    )

    record = publisher.publish(
        conversation_id="conversation-1",
        requirement_id=RequirementId("requirement-1"),
        decision_result=decision_result,
        snapshot=snapshot,
    )

    assert record is not None
    assert record.selected_price_amount == Decimal(980)
    assert record.selected_price_currency == "CNY"
    assert record.selected_price_semantics is PriceSemantics.LOWER_BOUND


def test_public_projection_does_not_expose_raw_provider_or_decision_internals() -> None:
    client = TestClient(create_app())
    body = start(
        client,
        origin="PEK",
        destination="SHA",
        departure_date="2026-09-01",
        max_price_cny=1200,
    )

    read_model = read(client, body["conversation_id"])

    keys = flattened_keys(read_model)
    assert "provider_result" not in keys
    assert "mapping_result" not in keys
    assert "filter_result" not in keys
    assert "ranking_result" not in keys
    assert "booking_reference" not in keys
    assert "offer_freshness" not in keys
    assert "fixture" not in str(read_model).lower()
    assert "raw" not in str(read_model).lower()


def test_search_empty_filter_empty_provider_error_and_not_ready_do_not_publish() -> None:
    cases = (
        (
            {
                "origin": "PEK",
                "destination": "LAX",
                "departure_date": "2026-09-01",
                "max_price_cny": 1200,
            },
            "SEARCH_EMPTY",
        ),
        (
            {
                "origin": "PEK",
                "destination": "SHA",
                "departure_date": "2026-09-01",
                "max_price_cny": 900,
            },
            "FILTER_EMPTY",
        ),
        (
            {
                "origin": "SHA",
                "destination": "LAX",
                "departure_date": "2026-09-01",
                "max_price_cny": 1200,
            },
            "PROVIDER_ERROR",
        ),
        (
            {"origin": "PEK", "destination": "SHA", "max_price_cny": 1200},
            "NOT_READY",
        ),
    )

    for request_json, expected_outcome in cases:
        client = TestClient(create_app())
        response = client.post("/conversations", json=request_json)
        assert response.status_code == 201

        read_model = read(client, response.json()["conversation_id"])

        assert read_model["outcome"] == expected_outcome
        assert read_model["current_published_recommendation"] is None


def test_current_publication_is_not_replaced_by_unpublished_latest_outcome() -> None:
    app = create_app()
    client = TestClient(app)
    body = start(
        client,
        origin="PEK",
        destination="SHA",
        departure_date="2026-09-01",
        max_price_cny=1200,
    )
    first_publication = read(client, body["conversation_id"])["current_published_recommendation"]
    assert first_publication is not None

    app.state.publication_repository.record_outcome(
        conversation_id=body["conversation_id"],
        outcome=PublicWorkflowOutcome.FILTER_EMPTY,
        requirement_id=None,
        requirement_version=None,
        execution_id="later-execution-without-publication",
    )

    after_unpublished_result = read(client, body["conversation_id"])

    assert after_unpublished_result["outcome"] == "FILTER_EMPTY"
    assert after_unpublished_result["current_published_recommendation"] == first_publication


def start(
    client: TestClient,
    *,
    origin: str,
    destination: str,
    departure_date: str,
    max_price_cny: int,
) -> dict[str, Any]:
    response = client.post(
        "/conversations",
        json={
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "max_price_cny": max_price_cny,
            "lower_price_preferred": True,
        },
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def read(client: TestClient, conversation_id: object) -> dict[str, Any]:
    response = client.get(f"/conversations/{conversation_id}")
    assert response.status_code == 200
    return cast(dict[str, Any], response.json())


def flattened_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            child for child_value in value.values() for child in flattened_keys(child_value)
        }
    if isinstance(value, list):
        return {child for item in value for child in flattened_keys(item)}
    return set()


def candidate_snapshot_with_price_semantics(price_semantics: PriceSemantics) -> CandidateSnapshot:
    created_at = DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
    segment = FlightSegment(
        segment_id=SegmentId("segment-1"),
        marketing_carrier="MU",
        flight_number="5101",
        departure_airport="PEK",
        arrival_airport="SHA",
        departure_at=DomainInstant(datetime(2026, 9, 1, 8, 0, tzinfo=UTC)),
        arrival_at=DomainInstant(datetime(2026, 9, 1, 10, 0, tzinfo=UTC)),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.known("A320"),
    )
    itinerary = Itinerary(
        itinerary_id=ItineraryId("itinerary-1"),
        segment_ids=(segment.segment_id,),
    )
    offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=itinerary.itinerary_id,
        total_price=Money(Decimal(980), "CNY"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue.known("booking-1"),
        price_semantics=price_semantics,
    )
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=created_at,
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage(
            requested_scope="PEK-SHA 2026-09-01",
            actual_coverage="PEK-SHA 2026-09-01",
            status=CoverageStatus.COMPLETE,
        ),
        segments=(segment,),
        itineraries=(itinerary,),
        offers=(offer,),
    )


def recommended_decision(snapshot: CandidateSnapshot) -> MinimalDecisionResult:
    offer_id = snapshot.offers[0].offer_id
    itinerary_id = snapshot.itineraries[0].itinerary_id
    recommendation = RecommendationResult(
        recommendation_result_id=RecommendationResultId("recommendation-result-1"),
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(1),
        snapshot_id=snapshot.snapshot_id,
        snapshot_version=snapshot.version,
        generated_at=DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC)),
        items=(
            RecommendationItem(
                itinerary_id=itinerary_id,
                primary_offer_id=offer_id,
                roles=(RecommendationRole.BEST_OVERALL,),
                evidence=(
                    EvidenceRef(
                        EvidenceSource.OFFER,
                        offer_id,
                        "Selected from rank 1 lower-price result",
                    ),
                ),
            ),
        ),
    )
    return MinimalDecisionResult(
        status=MinimalDecisionStatus.RECOMMENDED,
        filter_result=None,
        ranking_result=None,
        recommendation_result=recommendation,
    )
