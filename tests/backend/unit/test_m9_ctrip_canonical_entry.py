from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.ctrip import (
    CTRIP_PROVIDER_MAPPER_VERSION,
    CtripCanonicalEntry,
    CtripProviderMapper,
)
from flight_agent.domain.flights import PriceSemantics
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInvariantViolation, RequirementVersion, ValueState
from flight_agent.ports import (
    CommonNormalizer,
    CoverageCompleteness,
    NormalizationContext,
    NormalizationIssueCategory,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
    ReferenceData,
    ReferenceDataVersion,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def ctrip_entry() -> CtripCanonicalEntry:
    return CtripCanonicalEntry(
        common_normalizer=CommonNormalizer(),
        normalization_context=NormalizationContext(
            normalizer_version=NormalizerVersion("common-normalizer-v1"),
            reference_data=ReferenceData(
                version=ReferenceDataVersion("reference-data-v1"),
                airports=frozenset({"PEK", "SHA"}),
                carriers=frozenset({"MU"}),
            ),
        ),
    )


def mapped(payload_value: dict[str, object] | None = None):
    return CtripProviderMapper().map(provider_result(payload=payload_value))


def canonicalize(payload_value: dict[str, object] | None = None):
    return ctrip_entry().canonicalize(mapped(payload_value))


def test_ctrip_level1_maps_to_canonical_segment_itinerary_without_offer() -> None:
    result = canonicalize(payload(level2=()))

    normalized = result.normalization_result
    assert normalized.mapper_version == CTRIP_PROVIDER_MAPPER_VERSION
    assert normalized.normalizer_version == NormalizerVersion("common-normalizer-v1")
    assert normalized.reference_data_version == ReferenceDataVersion("reference-data-v1")
    assert len(normalized.segments) == 1
    assert len(normalized.itineraries) == 1
    assert normalized.offers == ()
    assert normalized.statistics.mapped_offer_count == 0
    assert normalized.statistics.normalized_offer_count == 0
    assert normalized.itineraries[0].segment_ids == (normalized.segments[0].segment_id,)


def test_ctrip_level1_raw_price_list_does_not_create_mapped_or_canonical_offer() -> None:
    result = canonicalize(payload(level2=()))

    assert result.mapping_result.statistics.raw_offer_count == 1
    assert result.mapping_result.statistics.mapped_offer_count == 0
    assert result.mapping_result.statistics.dropped_offer_count == 1
    assert result.normalization_result.offers == ()
    assert any(issue.raw_path == "price_list" for issue in result.provider_mapping_issues)


def test_ctrip_level2_mapped_offer_enters_existing_common_normalizer() -> None:
    result = canonicalize()

    normalized = result.normalization_result
    assert len(result.mapping_result.offers) == 1
    assert len(normalized.offers) == 1
    assert normalized.offers[0].itinerary_id == normalized.itineraries[0].itinerary_id
    assert normalized.offers[0].total_price.amount == 820
    assert normalized.offers[0].total_price.currency == "CNY"
    assert normalized.offer_sources == (
        (normalized.offers[0].offer_id, result.mapping_result.offers[0].mapped_offer_ref),
    )


def test_numeric_level1_adult_price_is_not_auto_upgraded_to_exact_offer() -> None:
    result = canonicalize(payload(level2=()))

    assert result.mapping_result.offers == ()
    assert result.normalization_result.offers == ()
    assert any("priceList is provider raw structure only" in issue.detail for issue in result.provider_mapping_issues)


def test_supported_exact_mapped_price_semantics_are_preserved() -> None:
    result = canonicalize()

    assert result.mapping_result.offers[0].price_semantics is PriceSemantics.EXACT
    assert result.normalization_result.offers[0].price_semantics is PriceSemantics.EXACT


def test_lower_bound_mapped_price_semantics_are_not_upgraded_to_exact() -> None:
    lower_bound_offer = {
        **level2_offer(),
        "price": field("OBSERVED", "from CNY 820", "$.products[0].adultPrice"),
    }

    result = canonicalize(payload(level2=(lower_bound_offer,)))

    assert result.mapping_result.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert result.normalization_result.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert result.normalization_result.offers[0].price_semantics is not PriceSemantics.EXACT


def test_missing_booking_identity_and_purchase_access_are_not_fabricated() -> None:
    offer_without_booking = {
        **level2_offer(),
        "booking_action_identity": field("MISSING"),
        "purchase_access": field("MISSING"),
    }

    result = canonicalize(payload(level2=(offer_without_booking,)))

    assert result.mapping_result.offers[0].booking_reference.state is ValueState.NOT_PROVIDED
    assert result.normalization_result.offers[0].booking_reference.state is ValueState.NOT_PROVIDED


def test_absent_operating_codeshare_evidence_is_not_inferred() -> None:
    result = canonicalize()

    assert result.mapping_result.segments[0].operating_carrier.state is ValueState.NOT_PROVIDED
    assert result.normalization_result.segments[0].operating_carrier.state is ValueState.NOT_PROVIDED
    assert result.normalization_result.segments[0].marketing_carrier == "MU"


def test_mapping_issues_and_normalization_issues_remain_traceable() -> None:
    bad_level1 = {**level1_itinerary(), "departure_airport": field("OBSERVED", "ZZZ", "$.bad")}

    result = canonicalize(payload(level1=(bad_level1,), level2=()))

    assert any(issue.raw_path == "price_list" for issue in result.provider_mapping_issues)
    assert any(
        issue.category is NormalizationIssueCategory.UNRESOLVABLE_REFERENCE
        and issue.provenance[0].source_ref == "CTRIP:ctrip-acquisition-1"
        and issue.provenance[0].detail_ref == "ctrip-level1:ctrip-itin-1"
        for issue in result.normalization_result.issues
    )


def test_provenance_and_lineage_versions_are_preserved() -> None:
    result = canonicalize()

    segment = result.normalization_result.segments[0]
    itinerary = result.normalization_result.itineraries[0]
    offer = result.normalization_result.offers[0]
    assert result.normalization_result.mapper_version == CTRIP_PROVIDER_MAPPER_VERSION
    assert result.normalization_result.normalizer_version == NormalizerVersion("common-normalizer-v1")
    assert result.normalization_result.reference_data_version == ReferenceDataVersion("reference-data-v1")
    assert segment.provenance[0].source_ref == "CTRIP:ctrip-acquisition-1"
    assert itinerary.provenance[0].detail_ref == "ctrip-level1:ctrip-itin-1"
    assert offer.provenance[0].detail_ref == "ctrip-level2-offer:product-1"


def test_ctrip_canonical_entry_is_deterministic_for_same_mapped_input() -> None:
    mapping = mapped()

    first = ctrip_entry().canonicalize(mapping)
    second = ctrip_entry().canonicalize(mapping)

    assert first == second


def test_ctrip_canonical_entry_rejects_non_ctrip_mapped_input() -> None:
    mapping = mapped()
    non_ctrip = type(mapping)(
        provider_id=ProviderId("OTHER"),
        acquisition_id=mapping.acquisition_id,
        search_plan_id=mapping.search_plan_id,
        mapper_version=mapping.mapper_version,
        data_status=mapping.data_status,
        segments=mapping.segments,
        itineraries=mapping.itineraries,
        offers=mapping.offers,
        issues=mapping.issues,
        statistics=mapping.statistics,
    )

    with pytest.raises(DomainInvariantViolation, match="CTRIP mapped input"):
        ctrip_entry().canonicalize(non_ctrip)


def test_ctrip_canonical_entry_source_preserves_architecture_boundary() -> None:
    ctrip_entry_source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "ctrip"
        / "canonical_entry.py"
    ).read_text(encoding="utf-8")
    common_normalizer_source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "ports"
        / "candidate_normalization.py"
    ).read_text(encoding="utf-8")

    assert "ProviderRawEvidence" not in ctrip_entry_source
    assert "level1_evidence" not in ctrip_entry_source
    assert "level2_offer_evidence" not in ctrip_entry_source
    assert "CandidateMerger" not in ctrip_entry_source
    assert "CandidateSnapshot" not in ctrip_entry_source
    assert "requests" not in ctrip_entry_source
    assert "httpx" not in ctrip_entry_source
    assert "playwright" not in ctrip_entry_source
    assert "flight_agent.adapters.flight_providers.ctrip" not in common_normalizer_source
    assert "level1_evidence" not in common_normalizer_source
    assert "level2_offer_evidence" not in common_normalizer_source


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
