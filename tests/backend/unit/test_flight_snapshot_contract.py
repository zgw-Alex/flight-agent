from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable

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
    PriceSemantics,
    SegmentId,
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


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 21, hour, 0, tzinfo=UTC))


def provenance(source_ref: str = "provider-search-1") -> ProvenanceRef:
    return ProvenanceRef(source_type="external-search", source_ref=source_ref)


def segment(raw_id: str = "segment-1", carrier: str = "MU") -> FlightSegment:
    return FlightSegment(
        segment_id=SegmentId(raw_id),
        marketing_carrier=carrier,
        flight_number="588",
        departure_airport="pvg",
        arrival_airport="lax",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue.known(carrier),
        aircraft_type=DomainValue[str].unknown(),
        provenance=(provenance("segment-source"),),
    )


def itinerary(raw_id: str = "itinerary-1") -> Itinerary:
    return Itinerary(itinerary_id=ItineraryId(raw_id), segment_ids=(SegmentId("segment-1"),))


def offer(
    raw_id: str = "offer-1",
    itinerary_id: ItineraryId = ItineraryId("itinerary-1"),
    freshness: FreshnessState = FreshnessState.FRESH,
) -> Offer:
    return Offer(
        offer_id=OfferId(raw_id),
        itinerary_id=itinerary_id,
        total_price=Money(Decimal("810.50"), "usd"),
        offer_freshness=OfferFreshness(freshness),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(provenance("offer-source-1"), provenance("offer-source-2")),
    )


def complete_coverage() -> Coverage:
    return Coverage(
        requested_scope="PVG-LAX 2026-08-21",
        actual_coverage="PVG-LAX 2026-08-21",
        status=CoverageStatus.COMPLETE,
    )


def snapshot(
    *,
    segments: tuple[FlightSegment, ...] = (segment(),),
    itineraries: tuple[Itinerary, ...] = (itinerary(),),
    offers: tuple[Offer, ...] = (offer(),),
    version: SnapshotVersion = SnapshotVersion(1),
    parent_snapshot_id: CandidateSnapshotId | None = None,
    parent_snapshot_version: SnapshotVersion | None = None,
    coverage: Coverage = complete_coverage(),
) -> CandidateSnapshot:
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=version,
        created_at=instant(13),
        created_from_requirement_version=RequirementVersion(2),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=coverage,
        segments=segments,
        itineraries=itineraries,
        offers=offers,
        parent_snapshot_id=parent_snapshot_id,
        parent_snapshot_version=parent_snapshot_version,
        provenance=(provenance("snapshot-source"),),
    )


def test_flight_segment_valid_construction_and_optional_domain_values() -> None:
    flight_segment = segment()

    assert flight_segment.segment_id == SegmentId("segment-1")
    assert flight_segment.departure_airport == "PVG"
    assert flight_segment.operating_carrier.state is ValueState.KNOWN
    assert flight_segment.aircraft_type.state is ValueState.UNKNOWN
    assert flight_segment.provenance == (provenance("segment-source"),)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: segment(carrier=""),
        lambda: FlightSegment(
            segment_id=SegmentId("bad-airport"),
            marketing_carrier="MU",
            flight_number="588",
            departure_airport="PVG1",
            arrival_airport="LAX",
            departure_at=instant(1),
            arrival_at=instant(12),
            operating_carrier=DomainValue.known("MU"),
            aircraft_type=DomainValue[str].unknown(),
        ),
        lambda: FlightSegment(
            segment_id=SegmentId("bad-time"),
            marketing_carrier="MU",
            flight_number="588",
            departure_airport="PVG",
            arrival_airport="LAX",
            departure_at=instant(12),
            arrival_at=instant(1),
            operating_carrier=DomainValue.known("MU"),
            aircraft_type=DomainValue[str].unknown(),
        ),
    ],
)
def test_flight_segment_rejects_invalid_structural_facts(factory: Callable[[], object]) -> None:
    with pytest.raises(DomainInvariantViolation):
        factory()


def test_flight_segment_is_immutable() -> None:
    flight_segment = segment()

    with pytest.raises(FrozenInstanceError):
        flight_segment.flight_number = "999"  # type: ignore[misc]


def test_itinerary_preserves_order_and_has_no_price_ownership() -> None:
    ordered = Itinerary(
        itinerary_id=ItineraryId("itinerary-2"),
        segment_ids=(SegmentId("segment-a"), SegmentId("segment-b")),
    )

    assert ordered.segment_ids == (SegmentId("segment-a"), SegmentId("segment-b"))
    assert not hasattr(ordered, "price")
    with pytest.raises(FrozenInstanceError):
        ordered.segment_ids = ()  # type: ignore[misc]


def test_itinerary_requires_at_least_one_segment_reference() -> None:
    with pytest.raises(DomainInvariantViolation):
        Itinerary(itinerary_id=ItineraryId("empty"), segment_ids=())


def test_offer_has_commercial_facts_freshness_and_provider_neutral_provenance() -> None:
    flight_offer = offer()

    assert flight_offer.offer_id == OfferId("offer-1")
    assert flight_offer.itinerary_id == ItineraryId("itinerary-1")
    assert flight_offer.total_price == Money(Decimal("810.50"), "USD")
    assert flight_offer.offer_freshness == OfferFreshness(FreshnessState.FRESH)
    assert len(flight_offer.provenance) == 2
    assert not hasattr(flight_offer.provenance[0], "raw_payload")
    with pytest.raises(FrozenInstanceError):
        flight_offer.total_price = Money(Decimal("1"), "USD")  # type: ignore[misc]


def test_offer_defaults_to_exact_price_semantics_for_legacy_construction() -> None:
    flight_offer = offer()

    assert flight_offer.price_semantics is PriceSemantics.EXACT


def test_offer_accepts_explicit_exact_price_semantics() -> None:
    flight_offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=ItineraryId("itinerary-1"),
        total_price=Money(Decimal("810.50"), "usd"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(provenance("offer-source-1"), provenance("offer-source-2")),
        price_semantics=PriceSemantics.EXACT,
    )

    assert flight_offer.price_semantics is PriceSemantics.EXACT


def test_offer_accepts_explicit_lower_bound_price_semantics() -> None:
    flight_offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=ItineraryId("itinerary-1"),
        total_price=Money(Decimal("810.50"), "usd"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(provenance("offer-source-1"), provenance("offer-source-2")),
        price_semantics=PriceSemantics.LOWER_BOUND,
    )

    assert flight_offer.price_semantics is PriceSemantics.LOWER_BOUND


def test_offer_equality_distinguishes_price_semantics() -> None:
    exact = offer()
    lower_bound = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=ItineraryId("itinerary-1"),
        total_price=Money(Decimal("810.50"), "usd"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(provenance("offer-source-1"), provenance("offer-source-2")),
        price_semantics=PriceSemantics.LOWER_BOUND,
    )

    assert exact != lower_bound
    assert len({exact, lower_bound}) == 2


def test_money_rejects_invalid_commercial_facts() -> None:
    assert Money(Decimal("10"), "usd") == Money(Decimal("10"), "USD")
    assert not hasattr(Money(Decimal("10"), "USD"), "price_semantics")

    with pytest.raises(DomainInvariantViolation):
        Money(Decimal("0"), "USD")
    with pytest.raises(DomainInvariantViolation):
        Money(Decimal("10"), "US1")


def test_candidate_snapshot_constructs_non_empty_self_contained_graph() -> None:
    candidates = snapshot()

    assert candidates.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert candidates.version == SnapshotVersion(1)
    assert candidates.parent_snapshot_id is None
    assert candidates.created_from_requirement_version == RequirementVersion(2)
    assert candidates.segments == (segment(),)
    assert candidates.itineraries == (itinerary(),)
    assert candidates.offers == (offer(),)
    assert not hasattr(candidates, "workflow_state")
    assert not hasattr(candidates, "recommendation")


def test_candidate_snapshot_constructs_empty_snapshot_without_workflow_outcome() -> None:
    empty = snapshot(segments=(), itineraries=(), offers=(), coverage=complete_coverage())

    assert empty.segments == ()
    assert empty.itineraries == ()
    assert empty.offers == ()
    assert not hasattr(empty, "search_empty")
    assert not hasattr(empty, "provider_failure")


def test_candidate_snapshot_partial_and_unknown_coverage_are_valid() -> None:
    partial = snapshot(
        coverage=Coverage(
            requested_scope="all requested carriers",
            actual_coverage="one provider subset",
            status=CoverageStatus.PARTIAL,
            limitations=(CoverageLimitation(code="PROVIDER_LIMIT", detail="Provider subset only"),),
        )
    )
    unknown = snapshot(coverage=Coverage("PVG-LAX", "not measured", CoverageStatus.UNKNOWN))

    assert partial.coverage.status is CoverageStatus.PARTIAL
    assert partial.coverage.requested_scope != partial.coverage.actual_coverage
    assert unknown.coverage.status is CoverageStatus.UNKNOWN
    assert not hasattr(partial, "data_incomplete")


def test_coverage_rejects_malformed_partial_structure() -> None:
    with pytest.raises(DomainInvariantViolation):
        Coverage("", "actual", CoverageStatus.COMPLETE)
    with pytest.raises(DomainInvariantViolation):
        Coverage("requested", "actual", CoverageStatus.PARTIAL)
    with pytest.raises(DomainInvariantViolation):
        CoverageLimitation("", "detail")


@pytest.mark.parametrize(
    "segments,itineraries,offers",
    [
        ((segment("segment-1"), segment("segment-1")), (itinerary(),), (offer(),)),
        ((segment(),), (itinerary("itinerary-1"), itinerary("itinerary-1")), (offer(),)),
        ((segment(),), (itinerary(),), (offer("offer-1"), offer("offer-1"))),
        ((segment(),), (Itinerary(ItineraryId("itinerary-1"), (SegmentId("missing"),)),), ()),
        ((segment(),), (itinerary(),), (offer(itinerary_id=ItineraryId("missing")),)),
    ],
)
def test_candidate_snapshot_rejects_graph_integrity_violations(
    segments: tuple[FlightSegment, ...],
    itineraries: tuple[Itinerary, ...],
    offers: tuple[Offer, ...],
) -> None:
    with pytest.raises(DomainInvariantViolation):
        snapshot(segments=segments, itineraries=itineraries, offers=offers)


def test_candidate_snapshot_rejects_invalid_parent_lineage() -> None:
    with pytest.raises(DomainInvariantViolation):
        snapshot(
            version=SnapshotVersion(1),
            parent_snapshot_id=CandidateSnapshotId("parent"),
            parent_snapshot_version=SnapshotVersion(1),
        )

    child = snapshot(
        version=SnapshotVersion(2),
        parent_snapshot_id=CandidateSnapshotId("snapshot-1"),
        parent_snapshot_version=SnapshotVersion(1),
    )

    assert child.parent_snapshot_id == CandidateSnapshotId("snapshot-1")
    assert child.parent_snapshot_version == SnapshotVersion(1)


def test_candidate_snapshot_is_immutable_and_defensively_copies_collections() -> None:
    segment_list = [segment()]
    candidates = snapshot(segments=tuple(segment_list))
    segment_list.append(segment("segment-2", carrier="UA"))

    assert candidates.segments == (segment(),)
    with pytest.raises(FrozenInstanceError):
        candidates.segments = ()  # type: ignore[misc]


def test_missing_value_four_state_semantics_remain_intact() -> None:
    known = segment()
    unknown = FlightSegment(
        segment_id=SegmentId("unknown"),
        marketing_carrier="MU",
        flight_number="588",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue[str].unknown(),
        aircraft_type=DomainValue[str].not_applicable(),
    )

    assert known.operating_carrier.value == "MU"
    assert unknown.operating_carrier.state is ValueState.UNKNOWN
    assert unknown.aircraft_type.state is ValueState.NOT_APPLICABLE
    with pytest.raises(DomainInvariantViolation):
        DomainValue(ValueState.NOT_PROVIDED, "leaked-value")


def test_structural_and_offer_freshness_are_independent() -> None:
    stale_offer = offer("offer-stale", freshness=FreshnessState.STALE)
    fresh_offer = offer("offer-fresh", freshness=FreshnessState.FRESH)
    candidates = snapshot(offers=(stale_offer, fresh_offer))

    assert candidates.structural_freshness == StructuralFreshness(FreshnessState.FRESH)
    assert {flight_offer.offer_freshness.state for flight_offer in candidates.offers} == {
        FreshnessState.FRESH,
        FreshnessState.STALE,
    }
    assert not hasattr(candidates, "ttl")
    assert not hasattr(stale_offer, "refresh_policy")


def test_similar_flight_objects_are_not_silently_deduplicated() -> None:
    first = FlightSegment(
        segment_id=SegmentId("segment-a"),
        marketing_carrier="MU",
        flight_number="588",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue[str].unknown(),
    )
    second = FlightSegment(
        segment_id=SegmentId("segment-b"),
        marketing_carrier="MU",
        flight_number="588",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue[str].unknown(),
    )
    candidates = snapshot(
        segments=(first, second),
        itineraries=(
            Itinerary(ItineraryId("itinerary-1"), (SegmentId("segment-a"), SegmentId("segment-b"))),
        ),
        offers=(offer(),),
    )

    assert candidates.segments == (first, second)
    assert not hasattr(candidates, "dedupe_policy")


def test_wrong_typed_reference_is_rejected_when_runtime_enforceable() -> None:
    with pytest.raises(DomainInvariantViolation):
        snapshot(
            segments=(segment(),),
            itineraries=(
                Itinerary(
                    itinerary_id=ItineraryId("itinerary-1"),
                    segment_ids=(ItineraryId("not-a-segment"),),  # type: ignore[arg-type]
                ),
            ),
            offers=(),
        )
