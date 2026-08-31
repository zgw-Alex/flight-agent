from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.ctrip.browser_probe import (
    CTRIP_BROWSER_PROBE_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    CapturedPayload,
    CtripLevel2OfferEvidence,
    CtripProbeInput,
    CtripProbeRunResult,
    DomTraversalAssessment,
    ProviderMarketCompleteness,
    classify_ctrip_result_state,
    extract_level1_evidence_from_payloads,
    extract_level2_offer_evidence,
    sanitize_probe_payload,
    summarize_ctrip_detector_state,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


BATCH_SEARCH_PAYLOAD = {
    "data": {
        "flightItineraryList": [
            {
                "itineraryId": "itinerary-ctrip-1",
                "flightSegments": [
                    {
                        "flightList": [
                            {
                                "flightNo": "MU5100",
                                "marketAirlineCode": "MU",
                                "marketAirlineName": "东方航空",
                                "departureAirportName": "首都国际机场",
                                "arrivalAirportName": "虹桥国际机场",
                                "departureDateTime": "2026-09-14 07:00",
                                "arrivalDateTime": "2026-09-14 09:10",
                                "departureTerminal": "T2",
                                "arrivalTerminal": "T2",
                                "aircraftName": "空客320",
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
                        "cabin": "经济舱",
                        "baggageInfo": "20kg托运行李",
                        "refundChangeRule": "退改有条件",
                        "productName": "标准经济舱",
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
                        "productId": "product-1",
                        "productName": "标准经济舱",
                        "fareFamily": "ECONOMY_STANDARD",
                        "cabinName": "经济舱",
                        "supplierName": "携程",
                        "adultPrice": 820,
                        "ticketLeft": "仅剩2张",
                        "baggageInfo": "20kg托运行李",
                        "refundRule": "起飞前可退，收取手续费",
                        "changeRule": "可改签，收取差价",
                        "restriction": "不可签转",
                        "bookingId": "book-raw-1",
                    }
                ]
            }
        ]
    }
}


def test_ctrip_batch_search_level1_evidence_stays_raw_provider_evidence() -> None:
    evidence = extract_level1_evidence_from_payloads(
        (CapturedPayload("LEVEL1", "https://flights.ctrip.com/restapi/soa2/batchSearch", "batchSearch", BATCH_SEARCH_PAYLOAD),)
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.itinerary_id.raw_value == "itinerary-ctrip-1"
    assert item.flight_no.raw_value == "MU5100"
    assert item.market_airline_code.raw_value == "MU"
    assert item.market_airline_name.raw_value == "东方航空"
    assert item.departure_airport.raw_value == "首都国际机场"
    assert item.arrival_airport.raw_value == "虹桥国际机场"
    assert item.aircraft.raw_value == "空客320"
    assert item.price_list.raw_value["count"] == 1
    assert item.mapping_feasibility["flight_segment"] == "STRONG_CANDIDATE"
    assert item.mapping_feasibility["offer"] == "OFFER_LIKE_PRICE_SEAM_OBSERVED"
    assert "FlightSegment(" not in item.to_dict()


def test_ctrip_level2_offer_evidence_extracts_commercial_fields_without_canonical_mapping() -> None:
    evidence = extract_level2_offer_evidence(
        (
            CapturedPayload(
                "LEVEL2",
                "https://flights.ctrip.com/restapi/soa2/productPrice",
                "post_booking_offer_candidate",
                LEVEL2_PAYLOAD,
            ),
        )
    )

    assert len(evidence) == 1
    offer = evidence[0]
    assert offer.product_or_fare_identity.status == "OBSERVED"
    assert offer.cabin.raw_value == "经济舱"
    assert offer.seller_supplier.raw_value == "携程"
    assert offer.price.raw_value == 820
    assert offer.inventory_availability.raw_value == "仅剩2张"
    assert offer.baggage.raw_value == "20kg托运行李"
    assert offer.refund_change_rules.status == "OBSERVED"
    assert offer.restrictions.raw_value == "不可签转"
    assert offer.purchase_access.raw_value == "book-raw-1"
    assert offer.mapping_feasibility["canonical_offer"] == "STRONG_CANDIDATE_RAW_EVIDENCE"
    assert offer.mapping_feasibility["purchase_access"] == "RAW_SEAM_OBSERVED_ONLY"


def test_visible_level2_panel_can_yield_bounded_raw_offer_evidence() -> None:
    evidence = extract_level2_offer_evidence(
        (),
        visible_text="标准经济舱 ￥820 仅剩2张 20kg托运行李 退改签规则 不可签转 继续预订",
    )

    assert len(evidence) == 1
    offer = evidence[0]
    assert isinstance(offer, CtripLevel2OfferEvidence)
    assert offer.price.raw_value == "￥820"
    assert offer.purchase_access.status == "OBSERVED"
    assert offer.raw_payload_path == "visible_offer_panel_text"


def test_ctrip_classifier_prioritizes_access_challenge_and_zero_rows_are_not_empty() -> None:
    assert (
        classify_ctrip_result_state(html="<main>拖动滑块完成安全验证 订票 ￥820</main>", level1_count=1)
        is BrowserProbeOutcome.ACCESS_CHALLENGE
    )
    assert classify_ctrip_result_state(html="<main>暂无航班</main>") is BrowserProbeOutcome.SUCCESS_EMPTY
    assert (
        classify_ctrip_result_state(html="<main>北京 上海 查询中</main>", level1_count=0, level2_count=0)
        is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    )
    assert classify_ctrip_result_state(html="<main></main>", timed_out=True) is BrowserProbeOutcome.TIMEOUT


def test_ctrip_detector_state_is_machine_checkable() -> None:
    state = summarize_ctrip_detector_state("<main>航班 MU5100 起飞 到达 ￥820 订票</main>")

    assert state == {
        "access_challenge": False,
        "login_required": False,
        "provider_error": False,
        "result_container": True,
        "explicit_empty": False,
    }


def test_ctrip_whaleguard_block_is_access_challenge() -> None:
    assert classify_ctrip_result_state(html="<main>whaleguard block</main>") is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_ctrip_result_preserves_provider_market_completeness_unknown() -> None:
    result = CtripProbeRunResult(
        provider_identity="CTRIP",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=BrowserProbeOutcome.SUCCESS_COMPLETE,
        observed_level1_count=1,
        observed_level2_offer_count=1,
        duration_ms=100,
        dom_traversal_assessment=DomTraversalAssessment.PARTIAL_OBSERVED,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version=CTRIP_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://flights.ctrip.com/online/channel/domestic",
        level1_evidence=extract_level1_evidence_from_payloads(
            (CapturedPayload("LEVEL1", "https://flights.ctrip.com/restapi/soa2/batchSearch?token=secret", "batchSearch", BATCH_SEARCH_PAYLOAD),)
        ),
        level2_offer_evidence=extract_level2_offer_evidence(
            (CapturedPayload("LEVEL2", "https://flights.ctrip.com/restapi/soa2/productPrice", "post_booking_offer_candidate", LEVEL2_PAYLOAD),)
        ),
        diagnostics={"cookie": "abc", "final_sanitized_url": "https://flights.ctrip.com/?session=secret"},
    ).to_dict()

    assert result["provider_market_completeness"] == "UNKNOWN_NOT_PROVEN"
    assert result["diagnostics"]["cookie"] == "[REDACTED]"
    assert "token=secret" not in str(result)


def test_ctrip_sanitizer_excludes_sensitive_material() -> None:
    payload = {
        "cookie": "a=b",
        "Authorization": "Bearer abc",
        "nested": {"session_token": "secret", "safe": "￥820"},
        "text": "Cookie: a=b",
        "passengerName": "张三",
    }

    assert sanitize_probe_payload(payload) == {
        "cookie": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "nested": {"session_token": "[REDACTED]", "safe": "￥820"},
        "text": "[REDACTED]",
        "passengerName": "[REDACTED]",
    }


def test_ctrip_probe_path_does_not_construct_canonical_domain_objects() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "ctrip"
        / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "from flight_agent.domain.flights" not in source
    assert "from flight_agent.ports.flight_providers" not in source
    assert "FlightSegment(" not in source
    assert "Itinerary(" not in source
    assert "Offer(" not in source
    assert "ProviderSearchResult(" not in source


def test_real_ctrip_smoke_is_explicit_opt_in_and_outside_ordinary_ci() -> None:
    smoke = REPO_ROOT / "scripts" / "ci" / "ctrip-browser-probe-smoke.ps1"
    backend_ci = (REPO_ROOT / "scripts" / "ci" / "backend.ps1").read_text(encoding="utf-8")
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")

    assert smoke.exists()
    assert "ctrip-browser-probe-smoke" not in backend_ci
    assert "ctrip-browser-probe-smoke" not in all_ci


def test_ctrip_probe_input_keeps_browser_mode_runtime_only() -> None:
    probe_input = CtripProbeInput("北京", "上海", date(2026, 9, 14), headless=False)

    assert probe_input.headless is False
    assert BrowserAcquisitionMode.BROWSER.value == "BROWSER"
