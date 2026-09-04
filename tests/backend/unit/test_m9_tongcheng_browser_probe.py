from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.tongcheng.browser_probe import (
    TONGCHENG_BROWSER_PROBE_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    BrowserProbeStage,
    CapturedPayload,
    DomTraversalAssessment,
    ProviderMarketCompleteness,
    TongchengLevel2OfferEvidence,
    TongchengProbeInput,
    TongchengProbeRunResult,
    _StageRecorder,
    classify_tongcheng_result_state,
    cross_check_structured_evidence_with_dom,
    extract_level1_evidence_from_payloads,
    extract_level2_offer_evidence,
    sanitize_probe_payload,
    summarize_tongcheng_detector_state,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


GET_FLIGHT_LIST_PAYLOAD = {
    "data": {
        "flightList": [
            {
                "flightNo": "MU5138",
                "airCompanyCode": "MU",
                "airCompanyName": "东方航空",
                "flyOffAirportCode": "PKX",
                "flyOffAirportName": "北京大兴国际机场",
                "arriveAirportCode": "SHA",
                "arriveAirportName": "上海虹桥国际机场",
                "flyOffOnlyTime": "07:00",
                "flyOffTime": "2026-09-15 07:00",
                "arrivalOnlyTime": "09:10",
                "arrivalTime": "2026-09-15 09:10",
                "arriveTerminal": "T2",
                "planeType": "空客321(窄)",
                "flyTime": "2小时10分",
                "AirLowestPrice": "¥450起",
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
                        "supplierName": "同程",
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


def test_tongcheng_getflightlist_level1_evidence_stays_raw_provider_evidence() -> None:
    evidence = extract_level1_evidence_from_payloads(
        (
            CapturedPayload(
                "LEVEL1",
                "https://www.ly.com/flights/api/getflightlist",
                "getflightlist",
                GET_FLIGHT_LIST_PAYLOAD,
            ),
        )
    )

    assert len(evidence) == 1
    item = evidence[0]
    assert item.itinerary_id.raw_value == "MU5138"
    assert item.flight_no.raw_value == "MU5138"
    assert item.market_airline_code.raw_value == "MU"
    assert item.market_airline_name.raw_value == "东方航空"
    assert item.departure_airport.raw_value == "北京大兴国际机场"
    assert item.arrival_airport.raw_value == "上海虹桥国际机场"
    assert item.aircraft.raw_value == "空客321(窄)"
    assert item.price_list.raw_value == "¥450起"
    assert item.mapping_feasibility["flight_segment"] == "STRONG_CANDIDATE"
    assert item.mapping_feasibility["offer"] == "OFFER_LIKE_PRICE_SEAM_OBSERVED"
    assert "FlightSegment(" not in item.to_dict()


def test_tongcheng_structured_evidence_can_cross_check_rendered_dom() -> None:
    evidence = extract_level1_evidence_from_payloads(
        (
            CapturedPayload(
                "LEVEL1",
                "https://www.ly.com/flights/api/getflightlist",
                "getflightlist",
                GET_FLIGHT_LIST_PAYLOAD,
            ),
        )
    )
    html = """
    <main>
      <section class="flight-card">
        东方航空MU5138 07:00 大兴机场 09:10 虹桥机场T2 空客321(窄) ¥450起 选择
      </section>
    </main>
    """

    cross_check = cross_check_structured_evidence_with_dom(evidence, html)

    assert cross_check is not None
    assert cross_check.flight_identity_match is True
    assert cross_check.carrier_match is True
    assert cross_check.departure_time_match is True
    assert cross_check.arrival_time_match is True
    assert cross_check.summary_price_match is True
    assert cross_check.is_representative_match() is True


def test_tongcheng_level2_offer_evidence_extracts_commercial_fields_without_canonical_mapping() -> None:
    evidence = extract_level2_offer_evidence(
        (
            CapturedPayload(
                "LEVEL2",
                "https://flights.tongcheng.com/restapi/soa2/productPrice",
                "post_booking_offer_candidate",
                LEVEL2_PAYLOAD,
            ),
        )
    )

    assert len(evidence) == 1
    offer = evidence[0]
    assert offer.product_or_fare_identity.status == "OBSERVED"
    assert offer.cabin.raw_value == "经济舱"
    assert offer.seller_supplier.raw_value == "同程"
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
    assert isinstance(offer, TongchengLevel2OfferEvidence)
    assert offer.price.raw_value == "￥820"
    assert offer.purchase_access.status == "OBSERVED"
    assert offer.raw_payload_path == "visible_offer_panel_text"


def test_tongcheng_classifier_prioritizes_access_challenge_and_zero_rows_are_not_empty() -> None:
    assert (
        classify_tongcheng_result_state(html="<main>拖动滑块完成安全验证 订票 ￥820</main>", level1_count=1)
        is BrowserProbeOutcome.ACCESS_CHALLENGE
    )
    assert classify_tongcheng_result_state(html="<main>暂无航班</main>") is BrowserProbeOutcome.SUCCESS_EMPTY
    assert (
        classify_tongcheng_result_state(html="<main>北京 上海 查询中</main>", level1_count=0, level2_count=0)
        is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    )
    assert classify_tongcheng_result_state(html="<main></main>", timed_out=True) is BrowserProbeOutcome.TIMEOUT


def test_tongcheng_detector_state_is_machine_checkable() -> None:
    state = summarize_tongcheng_detector_state("<main>航班 MU5100 起飞 到达 ￥820 订票</main>")

    assert state == {
        "access_challenge": False,
        "weak_challenge_marker": False,
        "login_required": False,
        "provider_error": False,
        "result_container": True,
        "explicit_empty": False,
    }


def test_tongcheng_homepage_login_and_promo_price_are_not_terminal_result_state() -> None:
    html = "<main>您好，请 登录 免费注册 机票 国内机票 上海首尔 含税价¥465起 搜索</main>"

    state = summarize_tongcheng_detector_state(html)

    assert state["login_required"] is False
    assert state["result_container"] is False
    assert classify_tongcheng_result_state(html=html) is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def test_tongcheng_normal_login_verification_text_is_not_access_challenge() -> None:
    html = "<main>同程账号登录 账号密码登录 验证码登录 登录后预订</main>"

    state = summarize_tongcheng_detector_state(html)

    assert state["weak_challenge_marker"] is True
    assert state["access_challenge"] is False
    assert state["login_required"] is True
    assert classify_tongcheng_result_state(html=html) is BrowserProbeOutcome.LOGIN_REQUIRED


def test_tongcheng_whaleguard_block_is_access_challenge() -> None:
    assert classify_tongcheng_result_state(html="<main>whaleguard block</main>") is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_tongcheng_visible_blocking_challenge_remains_access_challenge() -> None:
    html = "<main>访问受限 请完成安全验证 拖动滑块 verify you are human</main>"

    state = summarize_tongcheng_detector_state(html)

    assert state["access_challenge"] is True
    assert classify_tongcheng_result_state(html=html) is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_tongcheng_stage_progression_records_success_failure_and_last_stage() -> None:
    recorder = _StageRecorder(started=0)

    recorder.mark(BrowserProbeStage.HOME_READY, "home ready", success=True, page_title="同程旅行")
    recorder.mark(
        BrowserProbeStage.SEARCH_SUBMITTED,
        "submit search",
        success=False,
        sanitized_url="https://www.ly.com/flights/home",
        url_category="ENTRY",
        failure_reason="no visible enabled search button matched",
    )

    stages = [stage.to_dict() for stage in recorder.stages]

    assert stages[0]["stage"] == "HOME_READY"
    assert stages[0]["success"] is True
    assert stages[0]["page_title"] == "同程旅行"
    assert stages[1]["stage"] == "SEARCH_SUBMITTED"
    assert stages[1]["success"] is False
    assert stages[1]["failure_reason"] == "no visible enabled search button matched"
    assert recorder.last_stage() is BrowserProbeStage.SEARCH_SUBMITTED
    assert recorder.last_successful_stage() is BrowserProbeStage.HOME_READY


def test_tongcheng_response_listener_is_registered_before_navigation() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "tongcheng"
        / "browser_probe.py"
    ).read_text(encoding="utf-8")

    listener_index = source.index('page.on("response"')
    navigation_index = source.index("await page.goto")

    assert listener_index < navigation_index


def test_tongcheng_result_preserves_provider_market_completeness_unknown() -> None:
    result = TongchengProbeRunResult(
        provider_identity="TONGCHENG",
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
        parser_selector_probe_version=TONGCHENG_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://www.ly.com/flights/home",
        level1_evidence=extract_level1_evidence_from_payloads(
            (
                CapturedPayload(
                    "LEVEL1",
                    "https://www.ly.com/flights/api/getflightlist?token=secret",
                    "getflightlist",
                    GET_FLIGHT_LIST_PAYLOAD,
                ),
            )
        ),
        level2_offer_evidence=extract_level2_offer_evidence(
            (CapturedPayload("LEVEL2", "https://flights.tongcheng.com/restapi/soa2/productPrice", "post_booking_offer_candidate", LEVEL2_PAYLOAD),)
        ),
        dom_cross_check=None,
        diagnostics={"cookie": "abc", "final_sanitized_url": "https://flights.tongcheng.com/?session=secret"},
    ).to_dict()

    assert result["provider_market_completeness"] == "UNKNOWN_NOT_PROVEN"
    assert result["diagnostics"]["cookie"] == "[REDACTED]"
    assert "token=secret" not in str(result)


def test_tongcheng_sanitizer_excludes_sensitive_material() -> None:
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


def test_tongcheng_probe_path_does_not_construct_canonical_domain_objects() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "tongcheng"
        / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "from flight_agent.domain.flights" not in source
    assert "from flight_agent.ports.flight_providers" not in source
    assert "FlightSegment(" not in source
    assert "Itinerary(" not in source
    assert "Offer(" not in source
    assert "ProviderSearchResult(" not in source


def test_real_tongcheng_smoke_is_explicit_opt_in_and_outside_ordinary_ci() -> None:
    smoke = REPO_ROOT / "scripts" / "ci" / "tongcheng-browser-probe-smoke.ps1"
    backend_ci = (REPO_ROOT / "scripts" / "ci" / "backend.ps1").read_text(encoding="utf-8")
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")

    assert smoke.exists()
    assert "tongcheng-browser-probe-smoke" not in backend_ci
    assert "tongcheng-browser-probe-smoke" not in all_ci


def test_tongcheng_probe_input_keeps_browser_mode_runtime_only() -> None:
    probe_input = TongchengProbeInput("北京", "上海", date(2026, 9, 14), headless=False)

    assert probe_input.headless is False
    assert BrowserAcquisitionMode.BROWSER.value == "BROWSER"
