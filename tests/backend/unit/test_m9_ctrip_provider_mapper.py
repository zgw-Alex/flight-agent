from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.flight_providers.ctrip import (
    CTRIP_PROVIDER_MAPPER_VERSION,
    CtripProviderMapper,
)
from flight_agent.domain.flights import CandidateSnapshot, PriceSemantics
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlanId,
)
from flight_agent.domain.shared import RequirementVersion, ValueState
from flight_agent.ports import (
    CoverageCompleteness,
    MappedItineraryRef,
    MappedOfferRef,
    MappedSegmentRef,
    MapperVersion,
    MappingIssueCategory,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)


def test_ctrip_level1_and_level2_evidence_maps_to_m4_mapped_graph() -> None:
    result = CtripProviderMapper().map(provider_result())

    assert result.mapper_version == CTRIP_PROVIDER_MAPPER_VERSION
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert len(result.offers) == 1
    assert not isinstance(result, CandidateSnapshot)

    segment = result.segments[0]
    itinerary = result.itineraries[0]
    offer = result.offers[0]
    assert segment.mapped_segment_ref == MappedSegmentRef(
        "mapped-segment:ctrip:ctrip-itin-1:segment:1"
    )
    assert segment.provider_segment_id == "ctrip-itin-1:segment:1"
    assert segment.marketing_carrier == "MU"
    assert segment.flight_number == "MU5100"
    assert segment.departure_airport == "PEK"
    assert segment.arrival_airport == "SHA"
    assert segment.departure_local == "2026-09-14 07:00"
    assert segment.arrival_local == "2026-09-14 09:10"
    assert segment.operating_carrier.state is ValueState.NOT_PROVIDED
    assert segment.aircraft_type.value == "AIRBUS 320"
    assert itinerary.mapped_itinerary_ref == MappedItineraryRef(
        "mapped-itinerary:ctrip:ctrip-itin-1"
    )
    assert itinerary.provider_itinerary_id == "ctrip-itin-1"
    assert itinerary.segment_refs == (segment.mapped_segment_ref,)
    assert offer.mapped_offer_ref == MappedOfferRef("mapped-offer:ctrip:product-1")
    assert offer.itinerary_ref == itinerary.mapped_itinerary_ref
    assert offer.total_amount.value == 820
    assert offer.currency.value == "CNY"
    assert offer.price_semantics is PriceSemantics.EXACT
    assert offer.booking_reference.value == "BOOKING-ACTION-1"


def test_ctrip_level1_price_list_is_not_automatically_a_mapped_offer() -> None:
    result = CtripProviderMapper().map(provider_result(payload=payload(level2=())))

    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert result.offers == ()
    assert result.statistics.raw_offer_count == 1
    assert result.statistics.dropped_offer_count == 1
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert any(
        issue.category is MappingIssueCategory.UNSUPPORTED_RAW_SHAPE
        and issue.raw_path == "price_list"
        and "priceList is provider raw structure only" in issue.detail
        for issue in result.issues
    )


def test_ctrip_level2_lower_bound_price_semantics_are_preserved() -> None:
    offer = {
        **level2_offer(),
        "price": field("OBSERVED", "起 ¥820", "$.data.products[0].adultPrice"),
    }

    result = CtripProviderMapper().map(provider_result(payload=payload(level2=(offer,))))

    assert result.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert result.offers[0].price_semantics is not PriceSemantics.EXACT


def test_ctrip_optional_malformed_field_retains_segment_with_issue() -> None:
    level1 = {
        **level1_itinerary(price_list=field("MISSING")),
        "aircraft": field("OBSERVED", {"name": "Airbus 320"}),
    }

    result = CtripProviderMapper().map(provider_result(payload=payload(level1=(level1,), level2=())))

    assert len(result.segments) == 1
    assert result.segments[0].aircraft_type.state is ValueState.UNKNOWN
    assert any(
        issue.category is MappingIssueCategory.MALFORMED_OPTIONAL_FIELD
        and issue.raw_record_ref == "ctrip-level1:ctrip-itin-1"
        and issue.raw_path == "aircraft"
        for issue in result.issues
    )


def test_ctrip_structural_missing_drops_dependent_graph_without_rebuilding() -> None:
    broken = {**level1_itinerary(), "departure_airport": field("MISSING")}

    result = CtripProviderMapper().map(provider_result(payload=payload(level1=(broken,))))

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.statistics.dropped_segment_count == 1
    assert result.statistics.dropped_itinerary_count == 1
    assert result.statistics.dropped_offer_count == 2
    assert any(
        issue.category is MappingIssueCategory.BROKEN_GRAPH_REFERENCE
        and issue.raw_record_ref == "ctrip-level1:ctrip-itin-1"
        for issue in result.issues
    )


def test_ctrip_level2_offer_without_unique_itinerary_is_dropped_as_orphan() -> None:
    result = CtripProviderMapper().map(provider_result(payload=payload(level1=())))

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.statistics.raw_offer_count == 1
    assert result.statistics.dropped_offer_count == 1
    assert any(
        issue.category is MappingIssueCategory.ORPHAN_RECORD
        and issue.raw_path == "itinerary_ref"
        for issue in result.issues
    )


def test_ctrip_mapped_artifacts_and_issues_preserve_provider_provenance() -> None:
    result = CtripProviderMapper().map(provider_result())
    segment = result.segments[0]
    issue = next(issue for issue in result.issues if issue.raw_path == "price_list")

    assert segment.provenance.provider_id == ProviderId("CTRIP")
    assert segment.provenance.acquisition_id == ProviderAcquisitionId("ctrip-acquisition-1")
    assert segment.provenance.raw_evidence_refs == ("assisted-capture:batch-search",)
    assert segment.provenance.raw_record_ref == "ctrip-level1:ctrip-itin-1"
    assert segment.provenance.provider_source_id == "ctrip-itin-1:segment:1"
    assert issue.provider_id == segment.provenance.provider_id
    assert issue.acquisition_id == segment.provenance.acquisition_id
    assert not hasattr(segment.provenance, "payload")


def test_ctrip_mapper_version_replay_is_deterministic_and_non_destructive() -> None:
    acquisition = provider_result()
    assert acquisition.raw_evidence is not None
    raw_before = acquisition.raw_evidence

    mapper_v1 = CtripProviderMapper(MapperVersion("ctrip-mapper-v1"))
    first = mapper_v1.map(acquisition)
    second = mapper_v1.map(acquisition)
    replay = CtripProviderMapper(MapperVersion("ctrip-mapper-v2")).map(acquisition)

    assert first == second
    assert first.mapper_version == MapperVersion("ctrip-mapper-v1")
    assert replay.mapper_version == MapperVersion("ctrip-mapper-v2")
    assert acquisition.raw_evidence == raw_before
    assert first.segments[0].mapped_segment_ref == replay.segments[0].mapped_segment_ref
    assert "ctrip-mapper-v1" not in first.segments[0].mapped_segment_ref.value


def test_ctrip_failed_acquisition_does_not_become_fake_empty_mapping() -> None:
    acquisition = provider_result(execution_status=ProviderExecutionStatus.UPSTREAM_ERROR)

    result = CtripProviderMapper().map(acquisition)

    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.data_status is not ProviderDataStatus.EMPTY
    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()


def test_ctrip_mapper_source_stays_inside_provider_mapping_boundary() -> None:
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "ctrip"
        / "mapper.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "CandidateSnapshot",
        "CandidateMerger",
        "CommonNormalizer",
        "Ranking",
        "Recommendation",
        "PurchaseGuidance",
        "requests",
        "httpx",
        "playwright",
        "async_playwright",
    )
    assert all(item not in source for item in forbidden)


def provider_result(
    *,
    payload: dict[str, object] | None = None,
    execution_status: ProviderExecutionStatus = ProviderExecutionStatus.SUCCESS,
) -> ProviderSearchResult:
    raw = ProviderRawEvidence(
        provider_id=ProviderId("CTRIP"),
        acquisition_id=ProviderAcquisitionId("ctrip-acquisition-1"),
        search_plan_id=SearchPlanId("search-plan-ctrip-1"),
        retrieved_at=datetime(2026, 9, 14, 1, 5, tzinfo=UTC),
        source_refs=("assisted-capture:batch-search",),
        payload=payload or globals()["payload"](),
    )
    return ProviderSearchResult(
        provider_id=raw.provider_id,
        acquisition_id=raw.acquisition_id,
        search_plan_id=raw.search_plan_id,
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(5),
        execution_status=execution_status,
        data_status=ProviderDataStatus.COMPLETE
        if execution_status is ProviderExecutionStatus.SUCCESS
        else ProviderDataStatus.UNUSABLE,
        coverage=ProviderCoverage(
            requested_scope=RequestedSearchScope(
                origin=OriginScope(AirportCode("PEK")),
                destination=DestinationScope(AirportCode("SHA")),
                departure_date=DepartureDateScope(LocalDate(date(2026, 9, 14))),
            ),
            actual_scope=RequestedSearchScope(
                origin=OriginScope(AirportCode("PEK")),
                destination=DestinationScope(AirportCode("SHA")),
                departure_date=DepartureDateScope(LocalDate(date(2026, 9, 14))),
            ),
            completeness=CoverageCompleteness.COMPLETE,
        ),
        raw_evidence=raw,
    )


def payload(
    *,
    level1: tuple[dict[str, object], ...] | None = None,
    level2: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    return {
        "provider_identity": "CTRIP",
        "acquisition_strategy": "BROWSER_ASSISTED",
        "acquired_at": "2026-09-14T01:05:00+00:00",
        "level1_evidence": list((level1_itinerary(),) if level1 is None else level1),
        "level2_offer_evidence": list((level2_offer(),) if level2 is None else level2),
    }


def level1_itinerary(*, price_list: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "evidence_index": 1,
        "itinerary_id": field("OBSERVED", "ctrip-itin-1", "$.data.flightItineraryList[0].itineraryId"),
        "flight_no": field("OBSERVED", "MU5100", "$.flightSegments[0].flightList[0].flightNo"),
        "market_airline_code": field("OBSERVED", "MU", "$.marketAirlineCode"),
        "market_airline_name": field("OBSERVED", "China Eastern", "$.marketAirlineName"),
        "departure_airport": field("OBSERVED", "PEK", "$.departureAirportCode"),
        "arrival_airport": field("OBSERVED", "SHA", "$.arrivalAirportCode"),
        "departure_datetime": field("OBSERVED", "2026-09-14 07:00", "$.departureDateTime"),
        "arrival_datetime": field("OBSERVED", "2026-09-14 09:10", "$.arrivalDateTime"),
        "terminal": field("OBSERVED", "T2", "$.departureTerminal"),
        "aircraft": field("OBSERVED", "Airbus 320", "$.aircraftName"),
        "duration": field("OBSERVED", 130, "$.durationMinutes"),
        "stop_transfer_semantics": field("OBSERVED", 0, "$.stopCount"),
        "price_list": price_list
        or field("OBSERVED", {"count": 1, "sample": {"adultPrice": 791}}, "$.priceList"),
        "raw_payload_path": "batchSearch:$.data.flightItineraryList[0]",
        "mapping_feasibility": {
            "flight_segment": "STRONG_CANDIDATE",
            "itinerary": "STRONG_CANDIDATE",
            "offer": "OFFER_LIKE_PRICE_SEAM_OBSERVED",
        },
    }


def level2_offer() -> dict[str, object]:
    return {
        "evidence_index": 1,
        "product_or_fare_identity": field("OBSERVED", "product-1", "$.products[0].productId"),
        "cabin": field("OBSERVED", "Economy", "$.products[0].cabinName"),
        "seller_supplier": field("OBSERVED", "CTRIP", "$.products[0].supplierName"),
        "price": field("OBSERVED", 820, "$.products[0].adultPrice"),
        "inventory_availability": field("OBSERVED", "2 seats left", "$.products[0].ticketLeft"),
        "baggage": field("OBSERVED", "20kg checked baggage", "$.products[0].baggageInfo"),
        "refund_change_rules": field("OBSERVED", "Refund before departure with fee", "$.products[0].refundRule"),
        "restrictions": field("OBSERVED", "Non-transferable", "$.products[0].restriction"),
        "booking_action_identity": field("OBSERVED", "booking-action-1", "$.products[0].bookingId"),
        "purchase_access": field("OBSERVED", "booking-action-1", "$.products[0].bookingId"),
        "raw_payload_path": "productPrice:$.data.offerGroups[0].products[0]",
        "mapping_feasibility": {
            "canonical_offer": "STRONG_CANDIDATE_RAW_EVIDENCE",
            "seller_supplier": "OBSERVED",
            "purchase_access": "RAW_SEAM_OBSERVED_ONLY",
        },
    }


def field(
    status: str,
    raw_value: object | None = None,
    evidence_path: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {"status": status}
    if raw_value is not None:
        value["raw_value"] = raw_value
    if evidence_path is not None:
        value["evidence_path"] = evidence_path
    return value
