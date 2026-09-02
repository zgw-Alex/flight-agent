from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.ctrip.assisted_capture import (
    AssistedCaptureClassification,
    AssistedCaptureInput,
    AssistedCaptureMode,
    AssistedEvidenceLevel,
    UnsafeAssistedEvidenceError,
    import_ctrip_assisted_capture,
    validate_sanitized_ctrip_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


BATCH_SEARCH_PAYLOAD = {
    "data": {
        "flightItineraryList": [
            {
                "itineraryId": "itinerary-ctrip-u2-1",
                "flightSegments": [
                    {
                        "flightList": [
                            {
                                "flightNo": "MU5100",
                                "marketAirlineCode": "MU",
                                "marketAirlineName": "China Eastern",
                                "departureAirportName": "Beijing Capital",
                                "arrivalAirportName": "Shanghai Hongqiao",
                                "departureDateTime": "2026-09-14 07:00",
                                "arrivalDateTime": "2026-09-14 09:10",
                                "departureTerminal": "T2",
                                "arrivalTerminal": "T2",
                                "aircraftName": "Airbus 320",
                                "durationMinutes": 130,
                                "stopCount": 0,
                            }
                        ]
                    }
                ],
                "priceList": [
                    {
                        "adultPrice": 791,
                        "childPrice": 420,
                        "infantPrice": 120,
                        "cabin": "Economy",
                        "baggageInfo": "20kg checked baggage",
                        "refundChangeRule": "Conditional refund/change",
                        "productType": "standard",
                        "groupType": "public",
                        "restrictionList": ["non-transferable"],
                        "priceUnitList": [{"currency": "CNY", "amount": 791}],
                        "soldOut": False,
                    }
                ],
            }
        ]
    }
}


LEVEL2_PAYLOAD = {
    "data": {
        "offerGroups": [
            {
                "products": [
                    {
                        "productId": "product-u2-1",
                        "productName": "Economy Standard",
                        "fareFamily": "ECONOMY_STANDARD",
                        "cabinName": "Economy",
                        "supplierName": "CTRIP",
                        "adultPrice": 820,
                        "ticketLeft": "2 seats left",
                        "baggageInfo": "20kg checked baggage",
                        "refundRule": "Refund before departure with fee",
                        "changeRule": "Changeable with fare difference",
                        "restriction": "Non-transferable",
                        "bookingId": "booking-evidence-1",
                    }
                ]
            }
        ]
    }
}


def test_manual_batch_search_import_extracts_itinerary_segment_and_price_list(tmp_path: Path) -> None:
    source = tmp_path / "sanitized-batch-search.json"
    source.write_text(
        json.dumps(
            {
                "request_label": "batchSearch",
                "capture_mode": "MANUAL_EXPORT",
                "payload": BATCH_SEARCH_PAYLOAD,
            }
        ),
        encoding="utf-8",
    )

    result = import_ctrip_assisted_capture(_input(source))

    assert result.provider_identity == "CTRIP"
    assert result.acquisition_mode.value == "BROWSER"
    assert result.acquisition_strategy == "BROWSER_ASSISTED"
    assert result.capture_mode is AssistedCaptureMode.MANUAL_EXPORT
    assert result.evidence_level is AssistedEvidenceLevel.LEVEL1
    assert result.classification is AssistedCaptureClassification.MANUAL_EXPORT_IMPORT_CONFIRMED
    assert result.observed_level1_count == 1
    assert result.observed_level2_offer_count == 0
    item = result.level1_evidence[0]
    assert item.itinerary_id.raw_value == "itinerary-ctrip-u2-1"
    assert item.flight_no.raw_value == "MU5100"
    assert item.market_airline_code.raw_value == "MU"
    assert item.price_list.raw_value["count"] == 1
    assert result.canonical_mapping_feasibility == {
        "FlightSegment": "CONFIRMED",
        "Itinerary": "CONFIRMED",
        "Offer": "PARTIAL",
        "PurchaseAccess": "UNKNOWN",
    }


def test_manual_level2_import_extracts_offer_supplier_and_purchase_access(tmp_path: Path) -> None:
    source = tmp_path / "sanitized-level2.json"
    source.write_text(json.dumps({"request_label": "productPrice", "payload": LEVEL2_PAYLOAD}), encoding="utf-8")

    result = import_ctrip_assisted_capture(_input(source, evidence_level=AssistedEvidenceLevel.LEVEL2))

    assert result.evidence_level is AssistedEvidenceLevel.LEVEL2
    assert result.observed_level1_count == 0
    assert result.observed_level2_offer_count == 1
    offer = result.level2_offer_evidence[0]
    assert offer.product_or_fare_identity.raw_value == "product-u2-1"
    assert offer.seller_supplier.raw_value == "CTRIP"
    assert offer.price.raw_value == 820
    assert offer.purchase_access.raw_value == "booking-evidence-1"
    assert result.canonical_mapping_feasibility["Offer"] == "CONFIRMED"
    assert result.canonical_mapping_feasibility["PurchaseAccess"] == "PARTIAL"


def test_missing_fields_remain_missing_without_invented_values(tmp_path: Path) -> None:
    source = tmp_path / "partial.json"
    partial = {
        "data": {
            "flightItineraryList": [
                {
                    "itineraryId": "itinerary-partial",
                    "flightSegments": [{"flightList": [{"flightNo": "MU5100"}]}],
                }
            ]
        }
    }
    source.write_text(json.dumps({"request_label": "batchSearch", "payload": partial}), encoding="utf-8")

    result = import_ctrip_assisted_capture(_input(source))

    assert result.observed_level1_count == 1
    item = result.level1_evidence[0]
    assert item.departure_airport.status == "MISSING"
    assert item.price_list.status == "MISSING"
    assert result.canonical_mapping_feasibility["FlightSegment"] == "PARTIAL"


@pytest.mark.parametrize(
    "unsafe",
    [
        {"Cookie": "a=b"},
        {"headers": {"Authorization": "Bearer dummy"}},
        {"headers": {"Set-Cookie": "a=b"}},
        {"session": "dummy"},
        {"nested": {"token": "dummy"}},
        {"csrf": "dummy"},
        {"localStorage": {"x": "y"}},
        {"profile": {"path": "dummy"}},
        {"har": {"log": {}}},
        {"text": "Cookie: a=b"},
    ],
)
def test_unsafe_manual_evidence_is_rejected(unsafe: dict[str, object], tmp_path: Path) -> None:
    source = tmp_path / "unsafe.json"
    source.write_text(json.dumps({"payload": unsafe}), encoding="utf-8")

    with pytest.raises(UnsafeAssistedEvidenceError):
        import_ctrip_assisted_capture(_input(source))


def test_malformed_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed sanitized evidence JSON"):
        import_ctrip_assisted_capture(_input(source))


def test_utf8_bom_manual_export_is_accepted(tmp_path: Path) -> None:
    source = tmp_path / "bom.json"
    source.write_text(json.dumps({"request_label": "batchSearch", "payload": BATCH_SEARCH_PAYLOAD}), encoding="utf-8-sig")

    result = import_ctrip_assisted_capture(_input(source))

    assert result.classification is AssistedCaptureClassification.MANUAL_EXPORT_IMPORT_CONFIRMED


def test_validation_returns_sanitized_payload_for_safe_evidence() -> None:
    assert validate_sanitized_ctrip_evidence({"safe": "value", "price": 100}) == {"safe": "value", "price": 100}


def test_assisted_capture_source_has_no_network_browser_or_canonical_dependency() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "ctrip"
        / "assisted_capture.py"
    ).read_text(encoding="utf-8")

    forbidden = (
        "requests",
        "httpx",
        "urllib.request",
        "socket",
        "playwright",
        "async_playwright",
        ".launch(",
        "from flight_agent.domain",
        "from flight_agent.ports.flight_providers",
        "FlightSegment(",
        "Itinerary(",
        "Offer(",
        "ProviderSearchResult(",
    )
    assert all(item not in source for item in forbidden)


def test_assisted_capture_cli_is_local_import_only() -> None:
    cli = (REPO_ROOT / "scripts" / "smoke" / "ctrip_assisted_capture.py").read_text(encoding="utf-8")

    assert "assisted_capture import main" in cli
    assert "playwright" not in cli
    assert "requests" not in cli
    assert "httpx" not in cli


def _input(
    source: Path, *, evidence_level: AssistedEvidenceLevel = AssistedEvidenceLevel.UNKNOWN
) -> AssistedCaptureInput:
    return AssistedCaptureInput(
        source_path=source,
        origin_text="BJS",
        destination_text="SHA",
        departure_date=date(2026, 9, 14),
        evidence_level=evidence_level,
    )
