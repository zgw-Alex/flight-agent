from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.fliggy.browser_probe import (
    FLIGGY_BROWSER_PROBE_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    BrowserProbeStage,
    ControlReadiness,
    DestinationSuggestionCandidate,
    DomTraversalAssessment,
    ExperimentDiagnosis,
    FliggyPageIdentity,
    ProbeInput,
    ProbeRunResult,
    ProviderMarketCompleteness,
    PublicSearchQueryState,
    ResultContextCandidate,
    SearchFormReadiness,
    StageDiagnostic,
    _annotate_post_submit_query_propagation,
    _browser_failure_taxonomy,
    _build_post_submit_query_state_diagnostics,
    _destination_commitment_result,
    _destination_commitment_status,
    _destination_readback_matches,
    _diag_context_inventory,
    _diag_u4_p0_p7,
    _diag_u4_root_cause,
    _finalize_diagnostics,
    _marker_transition_count,
    _resolve_destination_candidate,
    _result_state_extension_reason,
    _result_state_failure_taxonomy,
    _result_state_forward_progress,
    _result_state_retryable,
    _result_state_sample,
    _settled_state_reached,
    _StageRecorder,
    _verify_pre_submit_query_state,
    assess_dom_coverage,
    choose_result_context_candidate,
    classify_experiment_diagnosis,
    classify_fliggy_page_identity,
    classify_result_state,
    extract_level1_evidence,
    sanitize_probe_payload,
    summarize_detector_state,
    summarize_search_plan_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


DIRECT_FLIGHT_HTML = """
<html><body>
  <table>
    <tr class="flight-item-tr">
      <td class="flight-line">
        <span class="J_line J_TestFlight" aria-label="航班号 东方航空 MU5100">MU5100</span>
        <span class="flight-aircraft">空客320</span>
      </td>
      <td class="flight-time"><span class="time">07:00</span><span class="time">09:10</span></td>
      <td class="flight-port"><span class="port">首都T2</span><span class="port">虹桥T2</span></td>
      <td class="flight-ontime-rate">准点率90%</td>
      <td class="flight-total-time">2小时10分</td>
      <td class="flight-price"><span class="J_FlightListPrice" aria-label="票价791元">¥791</span></td>
      <td class="flight-operate"><button class="J_SelectFlight" data-testid="select-flight-btn" aria-label="订票">订票</button></td>
    </tr>
  </table>
  <footer>没有更多结果</footer>
</body></html>
"""


CODESHARE_HTML = """
<html><body>
  <tr class="flight-item-tr">
    <td class="flight-line"><span class="J_line J_TestFlight" aria-label="航班号 厦门航空 MF3554">MF3554</span></td>
    <td class="flight-time"><span>10:00</span><span>12:15</span></td>
    <td class="flight-port"><span>大兴</span><span>浦东</span></td>
    <td class="flight-total-time">2小时15分</td>
    <td class="flight-price"><span class="J_FlightListPrice" aria-label="票价830元">¥830</span></td>
    <td><span data-testid="share-flight-tip">实际乘坐航班：东方航空MU5100</span></td>
    <td class="flight-operate"><button aria-label="订票">订票</button></td>
  </tr>
</body></html>
"""


FLIGGY_FLIGHT_ENTRY_HTML = """
<html><head><title>飞机票查询-机票预订【飞猪旅行】</title></head><body>
  <div class="rc-flight-searchbar">
    <button class="tab-item selected-tab-item">国内</button>
    <button role="radio">单程</button>
    <div id="form_depCity"><input id="form_depCity" value="北京" /></div>
    <div id="form_arrCity"><input id="form_arrCity" value="杭州" /></div>
    <input id="form_depDate" value="2026-09-01" />
    <button class="search-button">搜索机票</button>
  </div>
  <main>机票 出发城市 到达城市 出发日期 单程 往返</main>
</body></html>
"""


TAOBAO_STORE_NOT_FOUND_HTML = """
<html><head><title>店铺浏览-淘宝网</title></head><body>
  <main>亲，请登录 宝贝 店铺 输入您想要的宝贝 搜索 没有找到相应的店铺信息</main>
</body></html>
"""


FLIGGY_RESULT_PAGE_HTML = """
<html><head><title>北京到杭州机票预订，北京到杭州特价机票，北京到杭州航班查询预订【飞猪国内机票】</title></head>
<body>
  <main>北京 到 杭州 航班查询 起飞 到达 经济舱 直飞 2026-09-14</main>
</body></html>
"""


UNRELATED_FLIGGY_PAGE_HTML = """
<html><head><title>飞猪旅行</title></head><body>
  <main>酒店 火车票 旅游度假 景点门票</main>
</body></html>
"""


def test_normal_direct_flight_level1_evidence_is_raw_provider_evidence() -> None:
    evidence = extract_level1_evidence(DIRECT_FLIGHT_HTML)

    assert len(evidence) == 1
    item = evidence[0]
    assert item.evidence_index == 1
    assert item.raw_displayed_flight_identity.raw_text == "MU5100"
    assert item.raw_accessible_flight_label.raw_text == "航班号 东方航空 MU5100"
    assert item.raw_aircraft_text.raw_text == "空客320"
    assert item.raw_departure_time.raw_text == "07:00"
    assert item.raw_arrival_time.raw_text == "09:10"
    assert item.raw_departure_airport_terminal.raw_text == "首都T2"
    assert item.raw_arrival_airport_terminal.raw_text == "虹桥T2"
    assert item.raw_duration_text.raw_text == "2小时10分"
    assert item.raw_on_time_rate_text.raw_text == "准点率90%"
    assert item.raw_displayed_lowest_price.raw_text == "票价791元"
    assert item.booking_offer_expansion_action_present is True


def test_missing_optional_fields_are_marked_missing_not_failed() -> None:
    item = extract_level1_evidence(CODESHARE_HTML)[0]

    assert item.raw_aircraft_text.status == "MISSING"
    assert item.raw_on_time_rate_text.status == "MISSING"
    assert item.raw_discount_text.status == "MISSING"
    assert item.raw_availability_tag.status == "MISSING"
    assert item.booking_offer_expansion_action_present is True


def test_codeshare_raw_evidence_is_preserved_without_canonical_mapping() -> None:
    item = extract_level1_evidence(CODESHARE_HTML)[0]
    rendered = item.to_dict()

    assert item.raw_displayed_flight_identity.raw_text == "MF3554"
    assert item.raw_codeshare_indicator.status == "OBSERVED"
    assert item.raw_codeshare_detail_text.raw_text == "实际乘坐航班：东方航空MU5100"
    assert "marketing_carrier" not in rendered
    assert "operating_carrier" not in rendered


def test_classifier_detection_order_prioritizes_access_challenge() -> None:
    html = "<div class='flight-item-tr'>MU5100 票价791元</div><div>拖动滑块完成安全验证</div>"

    assert classify_result_state(html) is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_public_login_verification_code_copy_is_not_active_access_challenge() -> None:
    html = "<main>验证码登录 获取验证码 飞猪会员登录</main>"

    assert summarize_detector_state(html)["access_challenge"] is False
    assert classify_result_state(html) is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    assert (
        classify_fliggy_page_identity(
            url="https://www.fliggy.com/?tab=flight",
            title="飞机票查询-机票预订【飞猪旅行】",
            html=f"<html><body>{html}</body></html>",
        )
        is FliggyPageIdentity.UNKNOWN
    )


def test_detector_state_summary_is_sanitized_and_machine_checkable() -> None:
    state = summarize_detector_state(DIRECT_FLIGHT_HTML)

    assert state == {
        "access_challenge": False,
        "login_required": False,
        "provider_error": False,
        "result_container": True,
        "explicit_empty": False,
        "observed_row_count": 1,
        "terminal_boundary_observed": True,
    }


def test_diag_u1_required_browser_probe_stages_are_available() -> None:
    assert {stage.value for stage in BrowserProbeStage} >= {
        "BROWSER_LAUNCH",
        "ENTRY_NAVIGATION",
        "SEARCH_INPUT_READINESS",
        "SEARCH_INPUT",
        "SEARCH_SUBMIT",
        "RESULT_TRANSITION",
        "RESULT_READINESS",
        "LEVEL1_DISCOVERY",
        "TARGET_SELECTION",
        "BOOKING_ACTION_DISCOVERY",
        "BOOKING_ACTION",
        "LEVEL2_READINESS",
        "LEVEL2_EXTRACTION",
        "SANITIZATION",
    }


def test_fliggy_reference_entry_identity_is_accepted_without_tracking_params() -> None:
    identity = classify_fliggy_page_identity(
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        html=FLIGGY_FLIGHT_ENTRY_HTML,
    )

    assert identity is FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH


def test_taobao_store_not_found_identity_is_wrong_navigation_target() -> None:
    identity = classify_fliggy_page_identity(
        url="https://store.taobao.com/shop/noshop.htm",
        title="店铺浏览-淘宝网",
        html=TAOBAO_STORE_NOT_FOUND_HTML,
    )

    assert identity is FliggyPageIdentity.WRONG_NAVIGATION_TARGET


def test_fliggy_result_page_identity_is_result_candidate() -> None:
    identity = classify_fliggy_page_identity(
        url="https://sjipiao.fliggy.com/flight_search_result.htm",
        title="北京到杭州机票预订，北京到杭州特价机票，北京到杭州航班查询预订【飞猪国内机票】",
        html=FLIGGY_RESULT_PAGE_HTML,
    )

    assert identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE


def test_current_page_navigation_can_be_selected_as_result_context() -> None:
    candidate = _result_context_candidate(
        index=0,
        url="https://sjipiao.fliggy.com/flight_search_result.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=True,
    )

    assert choose_result_context_candidate((candidate,)) == candidate


def test_popup_matching_route_is_selected_over_original_entry_page() -> None:
    entry = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        origin=False,
        destination=False,
    )
    popup = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/flight_search_result.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((entry, popup)) == popup


def test_multiple_pages_select_only_route_matching_result_context() -> None:
    wrong_route = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/flight_search_result.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        destination=False,
    )
    target_route = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州航班查询预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((wrong_route, target_route)) == target_route


def test_wrong_and_unrelated_pages_are_rejected_as_result_context() -> None:
    wrong = _result_context_candidate(
        index=0,
        url="https://store.taobao.com/shop/noshop.htm",
        title="店铺浏览-淘宝网",
        identity=FliggyPageIdentity.WRONG_NAVIGATION_TARGET,
        is_current=False,
    )
    unrelated = _result_context_candidate(
        index=1,
        url="https://www.fliggy.com/?tab=hotel",
        title="飞猪旅行",
        identity=FliggyPageIdentity.UNKNOWN,
        is_current=False,
    )

    assert choose_result_context_candidate((wrong, unrelated)) is None


def test_ambiguous_multi_page_state_is_evidence_insufficient() -> None:
    first = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    second = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/alternate/trip_flight_search.htm",
        title="北京到杭州航班查询预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((first, second)) is None


def test_no_new_page_no_navigation_preserves_search_interaction_failure_semantics() -> None:
    entry = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        origin=False,
        destination=False,
    )

    assert choose_result_context_candidate((entry,)) is None


def test_rc01_same_query_route_date_and_result_surface_confirms_context() -> None:
    probe_input = ProbeInput("北京", "上海", date(2026, 9, 14))
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>2026-09-14 航班查询 起飞 到达 经济舱</main>",
        probe_input=probe_input,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        origin=evidence["origin"],
        destination=evidence["destination"],
        departure_date=evidence["departure_date"],
        result_surface=evidence["result_surface"],
    )

    assert candidate.context_matches() is True
    assert evidence["route_conflict"] is False
    assert evidence["date_conflict"] is False
    assert choose_result_context_candidate((candidate,)) == candidate


def test_rc02_spa_same_page_context_does_not_require_full_navigation() -> None:
    candidate = _result_context_candidate(
        index=0,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海航班查询 09月14日",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=True,
    )

    assert choose_result_context_candidate((candidate,)) == candidate


def test_rc03_route_match_date_conflict_blocks_context() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )

    assert candidate.context_matches() is False
    assert choose_result_context_candidate((candidate,)) is None


def test_rc04_date_match_route_conflict_blocks_context() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
    )

    assert candidate.context_matches() is False
    assert choose_result_context_candidate((candidate,)) is None


def test_rc05_visible_rows_without_route_date_identity_do_not_confirm_context() -> None:
    probe_input = ProbeInput("北京", "上海", date(2026, 9, 14))
    evidence = summarize_search_plan_evidence(
        title="特价机票",
        html=DIRECT_FLIGHT_HTML,
        probe_input=probe_input,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["result_surface"] is True
    assert evidence["origin"] is False
    assert evidence["destination"] is False
    assert evidence["departure_date"] is False


def test_rc06_matched_empty_result_surface_is_valid_context() -> None:
    probe_input = ProbeInput("北京", "上海", date(2026, 9, 14))
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>9月14日 暂无航班</main>",
        probe_input=probe_input,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["explicit_empty"] is True
    assert evidence["result_surface"] is True
    assert evidence["origin"] is True
    assert evidence["destination"] is True
    assert evidence["departure_date"] is True


def test_rc09_historical_diag_shape_confirms_when_url_supplies_query_identity() -> None:
    probe_input = ProbeInput("北京", "上海", date(2026, 9, 14))
    evidence = summarize_search_plan_evidence(
        title="特价机票",
        html="<main>航班查询 起飞 到达 经济舱</main>",
        probe_input=probe_input,
        url=(
            "https://sjipiao.fliggy.com/homeow/trip_flight_search.htm?"
            "depCityName=北京&arrCityName=上海&depDate=2026-09-14"
        ),
    )

    assert evidence["origin"] is True
    assert evidence["destination"] is True
    assert evidence["departure_date"] is True
    assert evidence["result_surface"] is True


def test_du2_01_date_marker_exists_and_matches_current_parser() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>2026-09-14 航班查询</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["date_marker_candidates_count"] == 1
    assert evidence["date_parse_status"] == "parsed"
    assert evidence["normalized_expected_date"] == "2026-09-14"
    assert evidence["normalized_observed_date"] == "2026-09-14"
    assert evidence["date_match"] is True


def test_du2_02_alternate_date_format_reports_normalized_result() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到上海航班查询 09月14日",
        html="<main>起飞 到达 经济舱</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["selected_date_marker_class"] == "month_day"
    assert evidence["observed_date_text"] == "09月14日"
    assert evidence["observed_date_source"] == "title"
    assert evidence["normalized_observed_date"] == "2026-09-14"
    assert evidence["date_match"] is True


def test_du2_03_absent_date_marker_is_not_reported_as_route_mismatch() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        departure_date=False,
        result_surface=True,
    )

    assert choose_result_context_candidate((candidate,)) is None
    assert candidate.search_plan_evidence["mismatch_dimension"] == "date"


def test_du2_04_route_true_date_false_reports_date_dimension() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>航班查询 起飞 到达</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["route_match"] is True
    assert evidence["date_match"] == "insufficient"
    assert evidence["mismatch_dimension"] == "date"


def test_du2_05_route_false_date_true_reports_route_dimension() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到杭州机票预订 9月14日",
        html="<main>航班查询 起飞 到达</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["date_match"] is True
    assert evidence["route_match"] is False
    assert evidence["mismatch_dimension"] == "route"


def test_du2_06_both_route_and_date_conflict_are_reported() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到杭州机票预订 9月15日",
        html="<main>航班查询 起飞 到达</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["route_match"] is False
    assert evidence["date_match"] is False
    assert evidence["mismatch_dimension"] == "both"


def test_du2_07_insufficient_date_evidence_keeps_query_identity_insufficient() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>航班查询 起飞 到达 经济舱</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["query_identity_decision"] == "insufficient"
    assert evidence["departure_date"] is False


def test_du2_08_stale_context_has_route_dimension_and_result_surface() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到杭州机票预订 9月14日",
        html="<main>航班查询 起飞 到达 经济舱</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["result_surface_present"] is True
    assert evidence["route_match"] is False
    assert evidence["mismatch_dimension"] == "route"


def test_du2_09_date_diagnostic_payload_stays_sanitized() -> None:
    payload = {
        "observed_date_text": "2026-09-14 Cookie: a=b",
        "session": "secret",
        "full_dom": "<html>private</html>",
    }

    assert sanitize_probe_payload(payload) == {
        "observed_date_text": "[REDACTED]",
        "session": "[REDACTED]",
        "full_dom": "[REDACTED]",
    }


def test_du2_10_challenge_detection_still_precedes_query_identity() -> None:
    html = "<main>北京到上海机票 9月14日 拖动滑块完成安全验证</main>"

    assert classify_result_state(html) is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_qs01_committed_route_and_date_allow_submit() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-09-14")
    )

    assert verification.pre_submit_route_match is True
    assert verification.pre_submit_date_match is True
    assert verification.submit_allowed is True


def test_qs02_typed_date_but_old_committed_date_blocks_submit() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-09-06")
    )

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_DATE_MISMATCH"


def test_qs03_committed_route_mismatch_blocks_submit() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="杭州", form_date="2026-09-14")
    )

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_ROUTE_MISMATCH"


def test_qs04_committed_route_and_date_mismatch_blocks_submit() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="广州", form_destination="杭州", form_date="2026-09-06")
    )

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_ROUTE_AND_DATE_MISMATCH"


def test_qs05_unreadable_or_insufficient_form_state_blocks_submit() -> None:
    unreadable = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin=None, form_destination="上海", form_date="2026-09-14")
    )
    insufficient = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="下周一")
    )

    assert unreadable.submit_allowed is False
    assert unreadable.failure_taxonomy == "FORM_STATE_UNREADABLE"
    assert insufficient.submit_allowed is False
    assert insufficient.failure_taxonomy == "FORM_STATE_INSUFFICIENT"


def test_qs06_pre_submit_match_and_post_submit_mismatch_is_propagation_failure() -> None:
    diagnostics = {"pre_submit_query_verification": _verify_pre_submit_query_state(_query_state()).to_dict()}
    handoff = {
        "context_match": False,
        "route_match": False,
        "date_match": False,
        "query_identity_decision": "insufficient",
        "mismatch_dimension": "both",
    }

    _annotate_post_submit_query_propagation(diagnostics, handoff)

    assert diagnostics["post_submit_propagation_failed"] is True
    assert diagnostics["post_submit_failure_taxonomy"] == "RESULT_QUERY_MISMATCH"


def test_qs07_pre_and_post_submit_match_advances_to_existing_result_matcher() -> None:
    diagnostics = {"pre_submit_query_verification": _verify_pre_submit_query_state(_query_state()).to_dict()}
    handoff = {
        "context_match": True,
        "route_match": True,
        "date_match": True,
        "query_identity_decision": "match",
        "mismatch_dimension": "none",
    }

    _annotate_post_submit_query_propagation(diagnostics, handoff)

    assert diagnostics["post_submit_propagation_failed"] is False
    assert diagnostics["post_submit_query_identity_decision"] == "match"


def test_qs08_result_query_mismatch_preserves_strict_result_matcher() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
    )

    assert choose_result_context_candidate((candidate,)) is None


def test_qs09_matching_empty_result_identity_is_preserved() -> None:
    evidence = summarize_search_plan_evidence(
        title="北京到上海机票预订",
        html="<main>2026-09-14 暂无航班</main>",
        probe_input=ProbeInput("北京", "上海", date(2026, 9, 14)),
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
    )

    assert evidence["explicit_empty"] is True
    assert evidence["query_identity_decision"] == "match"


def test_qs10_existing_challenge_classification_is_preserved() -> None:
    assert classify_result_state("<main>北京到上海 2026-09-14 访问验证</main>") is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_qs11_query_state_payload_stays_sanitized() -> None:
    payload = {
        "pre_submit_query_state": {"form_origin_readback": "北京 Cookie: a=b"},
        "full_dom": "<html>private</html>",
        "session": "secret",
    }

    assert sanitize_probe_payload(payload) == {
        "pre_submit_query_state": {"form_origin_readback": "[REDACTED]"},
        "full_dom": "[REDACTED]",
        "session": "[REDACTED]",
    }


def test_qs12_historical_requested_vs_result_date_cannot_be_accepted() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-09-14")
    )
    diagnostics = {"pre_submit_query_verification": verification.to_dict()}
    handoff = {
        "context_match": False,
        "route_match": False,
        "date_match": False,
        "query_identity_decision": "insufficient",
        "mismatch_dimension": "both",
        "submitted_date": "2026-09-14",
        "normalized_observed_date": "2026-09-06",
    }

    _annotate_post_submit_query_propagation(diagnostics, handoff)

    assert diagnostics["post_submit_propagation_failed"] is True
    assert diagnostics["post_submit_date_match"] is False


def test_dst01_deterministic_shanghai_suggestion_confirms_commitment() -> None:
    candidates = (_destination_candidate("北京"), _destination_candidate("上海", index=1))
    resolution = _resolve_destination_candidate(candidates, "上海", suggestion_surface_present=True)
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=candidates,
        suggestion_surface_present=True,
        selected_candidate=resolution.selected_candidate,
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=resolution.failure_taxonomy,
    )

    assert resolution.selected_candidate is not None
    assert resolution.selected_candidate.label == "上海"
    assert commitment.commitment_status == "confirmed"
    assert commitment.destination_match is True
    assert commitment.failure_taxonomy is None


def test_dst02_no_suggestion_surface_blocks_without_submit() -> None:
    resolution = _resolve_destination_candidate((), "上海", suggestion_surface_present=False)

    assert resolution.selected_candidate is None
    assert resolution.failure_taxonomy == "DESTINATION_SUGGESTION_NOT_READY"


def test_dst03_suggestion_surface_without_matching_option_is_not_found() -> None:
    candidates = (_destination_candidate("北京"), _destination_candidate("杭州", index=1))
    resolution = _resolve_destination_candidate(candidates, "上海", suggestion_surface_present=True)

    assert resolution.selected_candidate is None
    assert resolution.failure_taxonomy == "DESTINATION_OPTION_NOT_FOUND"


def test_dst04_multiple_shanghai_like_options_are_ambiguous() -> None:
    candidates = (_destination_candidate("上海虹桥"), _destination_candidate("上海浦东", index=1))
    resolution = _resolve_destination_candidate(candidates, "上海", suggestion_surface_present=True)

    assert resolution.selected_candidate is None
    assert resolution.failure_taxonomy == "DESTINATION_OPTION_AMBIGUOUS"


def test_dst05_matching_option_action_failure_is_reported() -> None:
    candidate = _destination_candidate("上海")
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(candidate,),
        suggestion_surface_present=True,
        selected_candidate=candidate,
        selection_method="click",
        commit_readback=None,
        failure_taxonomy="DESTINATION_OPTION_SELECTION_FAILED",
    )

    assert commitment.commitment_status == "failed"
    assert commitment.failure_taxonomy == "DESTINATION_OPTION_SELECTION_FAILED"


def test_dst06_selection_without_committed_readback_is_not_confirmed() -> None:
    assert (
        _destination_commitment_status(
            "到达城市，可直接输入城市名称搜索",
            "上海",
            action_performed=True,
            failure_taxonomy=None,
        )
        == "failed"
    )


def test_dst07_different_readback_is_form_destination_mismatch() -> None:
    candidate = _destination_candidate("上海")
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(candidate,),
        suggestion_surface_present=True,
        selected_candidate=candidate,
        selection_method="click",
        commit_readback="杭州",
        failure_taxonomy=None,
    )

    assert commitment.destination_match is False
    assert commitment.failure_taxonomy == "FORM_DESTINATION_MISMATCH"


def test_dst08_committed_destination_allows_existing_u2_pre_submit_verify() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-09-14")
    )

    assert verification.submit_allowed is True
    assert verification.query_state_decision == "match"


def test_dst09_origin_and_date_u2_semantics_remain_unchanged() -> None:
    old_date = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-09-06")
    )
    wrong_origin = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="广州", form_destination="上海", form_date="2026-09-14")
    )

    assert old_date.failure_taxonomy == "FORM_DATE_MISMATCH"
    assert wrong_origin.failure_taxonomy == "FORM_ROUTE_MISMATCH"


def test_dst10_challenge_classification_still_precedes_destination_repair() -> None:
    assert classify_result_state("<main>北京 上海 2026-09-14 安全验证</main>") is BrowserProbeOutcome.ACCESS_CHALLENGE


def test_dst11_destination_diagnostics_stay_bounded_and_sanitized() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(_destination_candidate("上海 Cookie: a=b"),),
        suggestion_surface_present=True,
        selected_candidate=_destination_candidate("上海 Cookie: a=b"),
        selection_method="click",
        commit_readback="上海 Cookie: a=b",
        failure_taxonomy=None,
    )

    assert sanitize_probe_payload({"destination_commitment": commitment.to_dict()}) == {
        "destination_commitment": {
            "requested_destination": "上海",
            "destination_control_ready": True,
            "typed_destination": "上海",
            "suggestion_surface_present": True,
            "suggestion_candidate_count": 1,
            "candidate_labels": ["[REDACTED]"],
            "selected_candidate_label": "[REDACTED]",
            "selection_method": "click",
            "commit_readback": "[REDACTED]",
            "destination_match": True,
            "commitment_status": "confirmed",
            "failure_taxonomy": None,
        }
    }


def test_dst12_placeholder_destination_can_never_equal_shanghai() -> None:
    assert _destination_readback_matches("到达城市，可直接输入城市名称搜索", "上海") is False


def test_ps01_q1_verified_and_q3_q4_q5_match_is_preserved() -> None:
    diagnostics = _post_submit_base_diagnostics()
    handoff = _post_submit_handoff(route_match=True, date_match=True, context_match=True)

    result = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert result["first_mismatch_checkpoint"] == "none"
    assert result["propagation_decision"] == "preserved"
    assert result["root_cause_class"] == "INCONCLUSIVE"


def test_ps02_q1_verified_and_q3_route_mismatch_localizes_nav_state() -> None:
    diagnostics = _post_submit_base_diagnostics()
    handoff = _post_submit_handoff(
        selected_page_url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm?depCity=北京&arrCity=杭州&depDate=2026-09-14",
        route_match=False,
        date_match=True,
        context_match=False,
        mismatch_dimension="route",
    )

    result = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert result["first_mismatch_checkpoint"] == "Q3_POST_SUBMIT_NAV_STATE"
    assert result["mismatch_dimension"] == "route"
    assert result["root_cause_class"] == "POST_SUBMIT_NAV_STATE_MISMATCH"


def test_ps03_q3_match_and_q4_date_mismatch_localizes_result_state_init() -> None:
    diagnostics = _post_submit_base_diagnostics()
    handoff = _post_submit_handoff(
        selected_page_url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        route_match=True,
        date_match=False,
        context_match=False,
        mismatch_dimension="date",
    )

    result = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert result["q3_post_submit_nav_state"]["route_match"] == "insufficient"
    assert result["first_mismatch_checkpoint"] == "Q4_RESULT_STATE_INIT"
    assert result["root_cause_class"] == "RESULT_STATE_INITIALIZATION_MISMATCH"


def test_ps04_provider_state_correct_but_q5_only_wrong_is_observation_gap() -> None:
    diagnostics = _post_submit_base_diagnostics()
    handoff = _post_submit_handoff(
        route_match=True,
        date_match=True,
        context_match=False,
        query_identity_decision="insufficient",
        mismatch_dimension="unknown",
    )

    result = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert result["first_mismatch_checkpoint"] == "Q5_RESULT_CONTEXT"
    assert result["propagation_decision"] == "observation_gap"
    assert result["root_cause_class"] == "RESULT_CONTEXT_ONLY_MISMATCH"


def test_ps05_q2_q4_match_and_q5_only_differs_keeps_matcher_strict() -> None:
    diagnostics = _post_submit_base_diagnostics()
    handoff = _post_submit_handoff(route_match=True, date_match=True, context_match=False)

    result = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert result["q2_submit_action"]["submit_action_observed"] is True
    assert result["q5_result_context"]["context_match"] is False
    assert result["root_cause_class"] == "RESULT_CONTEXT_ONLY_MISMATCH"


def test_ps06_route_only_post_submit_mismatch_is_precise() -> None:
    result = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(route_match=False, date_match=True, context_match=False, mismatch_dimension="route"),
    )

    assert result["mismatch_dimension"] == "route"


def test_ps07_date_only_post_submit_mismatch_is_precise() -> None:
    result = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(route_match=True, date_match=False, context_match=False, mismatch_dimension="date"),
    )

    assert result["mismatch_dimension"] == "date"


def test_ps08_both_post_submit_mismatch_is_precise() -> None:
    result = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both"),
    )

    assert result["mismatch_dimension"] == "both"


def test_ps09_confirmed_destination_stale_taxonomy_cannot_remain_final() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["destination_commitment"] = {
        "commitment_status": "confirmed",
        "failure_taxonomy": "DESTINATION_SUGGESTION_NOT_READY",
    }
    diagnostics["post_submit_propagation_failed"] = True
    diagnostics["post_submit_failure_taxonomy"] = "RESULT_QUERY_MISMATCH"

    result = _build_post_submit_query_state_diagnostics(
        diagnostics,
        _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both"),
    )

    assert result["diagnostic_state_consistent"] is False
    assert result["stale_taxonomy_source"] == "destination_commitment.failure_taxonomy"
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
            failed_stage=BrowserProbeStage.RESULT_TRANSITION.value,
            diagnostics=diagnostics,
        )
        == "RESULT_QUERY_MISMATCH"
    )


def test_ps10_headless_destination_failure_remains_independent() -> None:
    diagnostics = _post_submit_base_diagnostics(submit_executed=False)
    diagnostics["destination_commitment"] = {"commitment_status": "mismatch", "failure_taxonomy": "DESTINATION_SUGGESTION_NOT_READY"}
    diagnostics["pre_submit_query_verification"] = {
        "query_state_decision": "mismatch",
        "failure_taxonomy": "FORM_ROUTE_MISMATCH",
    }

    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
            failed_stage=BrowserProbeStage.SEARCH_INPUT.value,
            diagnostics=diagnostics,
        )
        == "DESTINATION_SUGGESTION_NOT_READY"
    )


def test_ps11_post_submit_diagnostics_stay_sanitized() -> None:
    result = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(observed_date_text="2026-09-14 Cookie: a=b"),
    )

    assert sanitize_probe_payload({"post_submit_query_state_diagnostics": result})["post_submit_query_state_diagnostics"][
        "q4_result_state_init"
    ]["observed_date_text"] == "[REDACTED]"


def test_ps12_challenge_and_network_classifications_are_preserved() -> None:
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.ACCESS_CHALLENGE,
            failed_stage=BrowserProbeStage.ENTRY_NAVIGATION.value,
            diagnostics={},
        )
        == "ACCESS_CHALLENGE"
    )
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.NETWORK_ERROR,
            failed_stage=BrowserProbeStage.SEARCH_INPUT.value,
            diagnostics={},
        )
        == "NETWORK_FAILURE"
    )


def test_rs01_transitional_stale_then_correct_state_can_reach_q5() -> None:
    stale = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=False,
        date_conflict=True,
    )
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    stale.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
    stale.search_plan_evidence["normalized_observed_date"] = "2026-01-08"
    stale.search_plan_evidence["date_parse_status"] = "ambiguous"

    assert _result_state_failure_taxonomy((stale,), None) == "RESULT_STATE_STALE_OR_DEFAULT"
    assert _result_state_retryable("RESULT_STATE_STALE_OR_DEFAULT") is True
    assert choose_result_context_candidate((correct,)) == correct
    assert _result_state_failure_taxonomy((correct,), correct) is None


def test_rs02_persistent_stale_default_state_is_specific_failure() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=False,
        date_conflict=True,
    )
    candidate.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
    candidate.search_plan_evidence["normalized_observed_date"] = "2026-01-08"
    candidate.search_plan_evidence["date_parse_status"] = "ambiguous"

    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_rs03_result_route_correct_date_wrong_is_date_mismatch() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    candidate.search_plan_evidence["date_match"] = False
    candidate.search_plan_evidence["date_conflict"] = False
    candidate.search_plan_evidence["normalized_observed_date"] = "2026-09-15"

    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_DATE_MISMATCH"


def test_rs04_result_date_correct_route_wrong_is_route_mismatch() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
    )
    candidate.search_plan_evidence["date_match"] = True
    candidate.search_plan_evidence["date_conflict"] = False

    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_ROUTE_MISMATCH"


def test_rs05_result_route_and_date_both_wrong_is_query_mismatch() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
    )
    candidate.search_plan_evidence["date_match"] = False
    candidate.search_plan_evidence["date_conflict"] = False
    candidate.search_plan_evidence["normalized_observed_date"] = "2026-09-15"

    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_QUERY_MISMATCH"


def test_rs06_unreadable_result_markers_do_not_guess() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="航班查询",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    candidate.search_plan_evidence["route_match"] = "insufficient"
    candidate.search_plan_evidence["date_match"] = "insufficient"

    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_QUERY_UNREADABLE"


def test_rs07_no_legitimate_transition_or_readiness_is_not_observed() -> None:
    entry = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        result_surface=False,
    )

    assert _result_state_failure_taxonomy((), None) == "RESULT_TRANSITION_NOT_OBSERVED"
    assert _result_state_failure_taxonomy((entry,), None) == "RESULT_STATE_NOT_READY"


def test_rs08_q4_match_allows_unchanged_q5_to_select() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert candidate.context_matches() is True
    assert choose_result_context_candidate((candidate,)) == candidate


def test_rs09_q5_mismatch_despite_q4_match_is_preserved() -> None:
    first = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    second = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/alternate/trip_flight_search.htm",
        title="北京到上海航班查询预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert _result_state_failure_taxonomy((first, second), None) == "SUBMIT_STATE_PROPAGATION_FAILED"
    assert choose_result_context_candidate((first, second)) is None


def test_rs10_historical_20260914_request_vs_20260108_result_is_rejected() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )
    candidate.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
    candidate.search_plan_evidence["normalized_observed_date"] = "2026-01-08"
    candidate.search_plan_evidence["date_parse_status"] = "ambiguous"

    assert choose_result_context_candidate((candidate,)) is None
    assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_rs11_network_and_challenge_classifications_remain_safe() -> None:
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.ACCESS_CHALLENGE,
            failed_stage=BrowserProbeStage.RESULT_TRANSITION.value,
            diagnostics={"post_submit_propagation_failed": True},
        )
        == "ACCESS_CHALLENGE"
    )
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.NETWORK_ERROR,
            failed_stage=BrowserProbeStage.RESULT_TRANSITION.value,
            diagnostics={"post_submit_propagation_failed": True},
        )
        == "NETWORK_FAILURE"
    )


def test_rs12_result_state_diagnostics_are_sanitized() -> None:
    result = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(observed_date_text="2026-01-08 Cookie: a=b", date_match=False, context_match=False),
    )

    sanitized = sanitize_probe_payload({"post_submit_query_state_diagnostics": result})

    assert sanitized["post_submit_query_state_diagnostics"]["q4_result_state_init"]["observed_date_text"] == "[REDACTED]"


def test_rs13_pre_submit_input_behavior_semantics_unchanged() -> None:
    assert _verify_pre_submit_query_state(_query_state()).submit_allowed is True
    assert (
        _verify_pre_submit_query_state(
            _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="杭州", form_date="2026-09-14")
        ).failure_taxonomy
        == "FORM_ROUTE_MISMATCH"
    )


def test_rs14_downstream_l1_booking_l2_behavior_remains_out_of_scope() -> None:
    source = (REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py").read_text(
        encoding="utf-8"
    )

    assert "_build_post_submit_query_state_diagnostics" in source
    assert "def extract_level1_evidence" in source
    assert "def map_level1_outcome_to_level2_failure" in source


def test_ph01_source_plus_one_deterministic_popup_selects_popup() -> None:
    source = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        result_surface=False,
    )
    popup = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((source, popup)) == popup
    inventory = _diag_context_inventory((source, popup))
    assert inventory[1]["context_id"] == "context-1"
    assert inventory[1]["url_class"] == "FLIGGY_RESULT"


def test_ph02_multiple_pages_one_deterministic_result_candidate_selects_it() -> None:
    unrelated = _result_context_candidate(
        index=1,
        url="https://www.fliggy.com/?tab=hotel",
        title="飞猪旅行",
        identity=FliggyPageIdentity.UNKNOWN,
        is_current=False,
        result_surface=False,
    )
    result = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((unrelated, result)) == result


def test_ph03_multiple_equally_plausible_pages_are_ambiguous() -> None:
    first = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    second = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/alternate/trip_flight_search.htm",
        title="北京到上海航班查询预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((first, second)) is None
    assert (
        _diag_u4_root_cause(
            candidates=(first, second),
            selected=None,
            result_state_failure_taxonomy="SUBMIT_STATE_PROPAGATION_FAILED",
            extension_used=False,
            samples=(),
        )
        == "PAGE_CONTEXT_SELECTION_AMBIGUOUS"
    )


def test_ph04_stale_initial_state_becomes_correct_before_base_deadline() -> None:
    stale = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )
    stale.search_plan_evidence["normalized_observed_date"] = "2026-01-08"
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    samples = (
        _result_state_sample(attempt=1, window="base", candidates=(stale,), selected=None, failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT"),
        _result_state_sample(attempt=2, window="base", candidates=(correct,), selected=correct, failure_taxonomy=None),
    )

    assert _marker_transition_count(samples) == 1
    assert _settled_state_reached(samples) is True


def test_ph05_correct_query_only_during_extension_marks_settling_gap() -> None:
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert (
        _diag_u4_root_cause(
            candidates=(correct,),
            selected=correct,
            result_state_failure_taxonomy=None,
            extension_used=True,
            samples=(),
        )
        == "RESULT_STATE_SETTLING_GAP"
    )


def test_ph06_no_forward_progress_at_base_deadline_does_not_extend() -> None:
    stale = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )
    sample = _result_state_sample(
        attempt=1,
        window="base",
        candidates=(stale,),
        selected=None,
        failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
    )

    assert _result_state_forward_progress([sample, sample]) is False
    assert _result_state_extension_reason([sample, sample]) == "none"


def test_ph07_progress_but_mismatch_persists_through_extension_is_stale_default() -> None:
    stale = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )
    stale.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
    stale.search_plan_evidence["normalized_observed_date"] = "2026-01-08"
    stale.search_plan_evidence["date_parse_status"] = "ambiguous"

    assert (
        _diag_u4_root_cause(
            candidates=(stale,),
            selected=None,
            result_state_failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
            extension_used=True,
            samples=(),
        )
        == "STALE_DEFAULT_CONTEXT_PERSISTED"
    )


def test_ph08_selected_page_closed_or_replaced_is_lifecycle_gap() -> None:
    closed = _result_context_candidate(
        index=1,
        url="<unavailable>",
        title="<unavailable>",
        identity=FliggyPageIdentity.UNKNOWN,
        is_current=False,
        alive=False,
    )

    assert (
        _diag_u4_root_cause(
            candidates=(closed,),
            selected=None,
            result_state_failure_taxonomy="RESULT_TRANSITION_NOT_OBSERVED",
            extension_used=False,
            samples=(),
        )
        == "PAGE_CLOSED_OR_REPLACED_DURING_TRANSITION"
    )


def test_ph09_another_deterministic_candidate_correct_means_wrong_context_target() -> None:
    current_wrong = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        result_surface=False,
    )
    alternate_correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert (
        _diag_u4_root_cause(
            candidates=(current_wrong, alternate_correct),
            selected=None,
            result_state_failure_taxonomy="RESULT_STATE_NOT_READY",
            extension_used=False,
            samples=(),
        )
        == "WRONG_PAGE_CONTEXT_SELECTED"
    )


def test_ph10_correct_page_exists_but_reader_unreadable_is_observation_gap() -> None:
    candidate = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="航班查询",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    candidate.search_plan_evidence["route_match"] = "insufficient"
    candidate.search_plan_evidence["date_match"] = "insufficient"

    assert (
        _diag_u4_root_cause(
            candidates=(candidate,),
            selected=None,
            result_state_failure_taxonomy="RESULT_STATE_QUERY_UNREADABLE",
            extension_used=False,
            samples=(),
        )
        == "RESULT_STATE_OBSERVATION_GAP"
    )


def test_ph11_historical_20260906_and_20260108_remain_failures() -> None:
    for observed in ("2026-09-06", "2026-01-08"):
        candidate = _result_context_candidate(
            index=1,
            url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
            title="北京到上海机票预订",
            identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
            is_current=False,
            date_conflict=True,
        )
        candidate.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
        candidate.search_plan_evidence["normalized_observed_date"] = observed
        candidate.search_plan_evidence["date_parse_status"] = "ambiguous"

        assert choose_result_context_candidate((candidate,)) is None
        assert _result_state_failure_taxonomy((candidate,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_ph12_diag_u4_payload_is_sanitized() -> None:
    handoff = _post_submit_handoff(observed_date_text="2026-01-08 Cookie: a=b", date_match=False, context_match=False)
    handoff["context_candidates"] = [{"context_id": "context-1", "observed_date_text": "Cookie: a=b"}]
    payload = _diag_u4_p0_p7(_post_submit_base_diagnostics(), handoff)

    sanitized = sanitize_probe_payload({"diag_u4": payload})

    assert sanitized["diag_u4"]["p2_context_created"]["context_candidates"][0]["observed_date_text"] == "[REDACTED]"
    assert sanitized["diag_u4"]["p7_query_identity"]["observed_date_text"] == "[REDACTED]"


def test_ph13_challenge_and_network_classifications_remain_safe_for_diag_u4() -> None:
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.ACCESS_CHALLENGE,
            failed_stage=BrowserProbeStage.RESULT_TRANSITION.value,
            diagnostics={"result_context_handoff": {"diagnostic_root_cause_class": "INCONCLUSIVE"}},
        )
        == "ACCESS_CHALLENGE"
    )
    assert (
        _browser_failure_taxonomy(
            outcome=BrowserProbeOutcome.NETWORK_ERROR,
            failed_stage=BrowserProbeStage.RESULT_TRANSITION.value,
            diagnostics={"result_context_handoff": {"diagnostic_root_cause_class": "INCONCLUSIVE"}},
        )
        == "NETWORK_FAILURE"
    )


def test_ph14_no_input_q5_downstream_or_shared_behavior_change() -> None:
    source = (REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py").read_text(
        encoding="utf-8"
    )

    assert _verify_pre_submit_query_state(_query_state()).submit_allowed is True
    assert "def extract_level1_evidence" in source
    assert "def extract_level2_offer_evidence" in source
    assert "CommonNormalizer" not in source
    assert "CandidateMerger" not in source


def test_navigation_source_ref_uses_stable_public_entry_and_sanitizes_tracking() -> None:
    result = ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run-entry",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
        observed_result_count=0,
        duration_ms=10,
        dom_traversal_assessment=DomTraversalAssessment.UNKNOWN,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://www.fliggy.com/?tab=flight",
        evidence=(),
        diagnostics={
            "entry_url_strategy": "public_fliggy_flight_entry_tab",
            "final_sanitized_url": "https://www.fliggy.com/?tab=flight",
            "source_with_spm": "https://www.fliggy.com/?spm=abc&tab=flight&session=secret",
            "page_identity": FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH.value,
        },
    ).to_dict()

    assert result["sanitized_source_ref"] == "https://www.fliggy.com/?tab=flight"
    assert result["diagnostics"]["source_with_spm"] == "[REDACTED]"
    assert result["diagnostics"]["page_identity"] == FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH.value


def test_wrong_target_diagnostic_marks_search_interaction_failure_without_new_outcome() -> None:
    result = ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run-wrong-target",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
        observed_result_count=0,
        duration_ms=10,
        dom_traversal_assessment=DomTraversalAssessment.UNKNOWN,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://www.fliggy.com/?tab=flight",
        evidence=(),
        diagnostics={
            "page_identity": FliggyPageIdentity.WRONG_NAVIGATION_TARGET.value,
            "wrong_navigation_target": True,
            "search_interaction_failed": True,
        },
    )

    assert result.outcome is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    assert classify_experiment_diagnosis((result,)) is ExperimentDiagnosis.SEARCH_INTERACTION_FAILURE


def test_search_form_readiness_serializes_and_requires_all_controls() -> None:
    ready_control = ControlReadiness(count=1, visible=True, enabled=True, editable=True)
    button = ControlReadiness(count=1, visible=True, enabled=True, editable=False)
    readiness = SearchFormReadiness(
        origin=ready_control,
        destination=ready_control,
        date=ready_control,
        search_button=button,
        iframe_count=0,
        overlay_evidence=(),
    )

    assert readiness.is_ready() is True
    assert readiness.to_dict() == {
        "origin": {"count": 1, "visible": True, "enabled": True, "editable": True},
        "destination": {"count": 1, "visible": True, "enabled": True, "editable": True},
        "date": {"count": 1, "visible": True, "enabled": True, "editable": True},
        "search_button": {"count": 1, "visible": True, "enabled": True, "editable": False},
        "iframe_count": 0,
        "overlay_evidence": [],
        "form_ready": True,
    }


def test_search_form_not_ready_when_destination_or_date_missing() -> None:
    ready_control = ControlReadiness(count=1, visible=True, enabled=True, editable=True)
    missing = ControlReadiness(count=0, visible=False, enabled=False, editable=False)
    button = ControlReadiness(count=1, visible=True, enabled=True, editable=False)

    assert (
        SearchFormReadiness(
            origin=ready_control,
            destination=missing,
            date=ready_control,
            search_button=button,
            iframe_count=0,
            overlay_evidence=(),
        ).is_ready()
        is False
    )
    assert (
        SearchFormReadiness(
            origin=ready_control,
            destination=ready_control,
            date=missing,
            search_button=button,
            iframe_count=0,
            overlay_evidence=(),
        ).is_ready()
        is False
    )


def test_search_form_not_ready_when_controls_disabled_or_invisible() -> None:
    ready_control = ControlReadiness(count=1, visible=True, enabled=True, editable=True)
    disabled = ControlReadiness(count=1, visible=True, enabled=False, editable=True)
    invisible = ControlReadiness(count=1, visible=False, enabled=True, editable=True)
    button = ControlReadiness(count=1, visible=True, enabled=True, editable=False)

    assert (
        SearchFormReadiness(
            origin=ready_control,
            destination=disabled,
            date=ready_control,
            search_button=button,
            iframe_count=0,
            overlay_evidence=(),
        ).is_ready()
        is False
    )
    assert (
        SearchFormReadiness(
            origin=ready_control,
            destination=ready_control,
            date=invisible,
            search_button=button,
            iframe_count=0,
            overlay_evidence=(),
        ).is_ready()
        is False
    )


def test_overlay_evidence_is_reported_without_becoming_access_challenge() -> None:
    ready_control = ControlReadiness(count=1, visible=True, enabled=True, editable=True)
    readiness = SearchFormReadiness(
        origin=ready_control,
        destination=ready_control,
        date=ready_control,
        search_button=ControlReadiness(count=1, visible=True, enabled=True, editable=False),
        iframe_count=0,
        overlay_evidence=("modal:1",),
    )

    assert readiness.is_ready() is True
    assert readiness.to_dict()["overlay_evidence"] == ["modal:1"]
    assert classify_result_state("<div class='modal'>旅行提醒</div>") is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def test_run_diagnostics_record_headed_and_headless_mode() -> None:
    headed = _result(BrowserProbeOutcome.TIMEOUT, headless=False).to_dict()
    headless = _result(BrowserProbeOutcome.TIMEOUT, headless=True).to_dict()

    assert headed["diagnostics"]["headless"] is False
    assert headless["diagnostics"]["headless"] is True


def test_classifier_distinguishes_explicit_states_and_zero_rows() -> None:
    assert classify_result_state("<main>暂无航班</main>") is BrowserProbeOutcome.SUCCESS_EMPTY
    assert classify_result_state("<main>请先登录后查看</main>") is BrowserProbeOutcome.LOGIN_REQUIRED
    assert classify_result_state("<main>系统繁忙，请稍后再试</main>") is BrowserProbeOutcome.PROVIDER_ERROR
    assert classify_result_state("<main></main>", timed_out=True) is BrowserProbeOutcome.TIMEOUT
    assert classify_result_state("<main></main>") is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def test_zero_parsed_rows_are_not_an_empty_shortcut() -> None:
    assert extract_level1_evidence("<main>北京 上海 搜索中</main>") == ()
    assert classify_result_state("<main>北京 上海 搜索中</main>") is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def test_coverage_does_not_claim_provider_market_completeness() -> None:
    result = ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run-1",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=BrowserProbeOutcome.SUCCESS_COMPLETE,
        observed_result_count=1,
        duration_ms=120,
        dom_traversal_assessment=assess_dom_coverage(
            initial_count=1,
            final_count=1,
            terminal_boundary_observed=True,
            stabilization_rounds=1,
        ),
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=True,
        terminal_boundary_evidence="footer",
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://flights.alitrip.com/flight_search_result.htm",
        evidence=extract_level1_evidence(DIRECT_FLIGHT_HTML),
        diagnostics={},
    ).to_dict()

    assert result["dom_traversal_assessment"] == DomTraversalAssessment.COMPLETE_OBSERVED.value
    assert result["provider_market_completeness"] == "UNKNOWN_NOT_PROVEN"


def test_sanitizer_excludes_sensitive_session_material() -> None:
    payload = {
        "cookie": "a=b",
        "Authorization": "Bearer abc",
        "nested": {"session_token": "secret", "safe": "票价791元"},
        "text": "Cookie: a=b",
    }

    assert sanitize_probe_payload(payload) == {
        "cookie": "[REDACTED]",
        "Authorization": "[REDACTED]",
        "nested": {"session_token": "[REDACTED]", "safe": "票价791元"},
        "text": "[REDACTED]",
    }


def test_stage_diagnostics_and_timeout_last_stage_are_serialized() -> None:
    result = ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run-timeout",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=BrowserProbeOutcome.TIMEOUT,
        observed_result_count=0,
        duration_ms=10000,
        dom_traversal_assessment=DomTraversalAssessment.UNKNOWN,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://flights.alitrip.com/flight_search_result.htm",
        evidence=(),
        diagnostics={
            "last_stage": BrowserProbeStage.RESULT_STATE_WAIT.value,
            "stage_diagnostics": [
                StageDiagnostic(BrowserProbeStage.BROWSER_LAUNCH, 1, "launch").to_dict(),
                StageDiagnostic(BrowserProbeStage.RESULT_STATE_WAIT, 1000, "wait").to_dict(),
            ],
            "detector_state": summarize_detector_state(""),
            "cookie": "abc",
        },
    ).to_dict()

    assert result["outcome"] == BrowserProbeOutcome.TIMEOUT.value
    assert result["diagnostics"]["last_stage"] == BrowserProbeStage.RESULT_STATE_WAIT.value
    assert result["diagnostics"]["cookie"] == "[REDACTED]"
    assert result["diagnostics"]["stage_diagnostics"][0]["stage"] == BrowserProbeStage.BROWSER_LAUNCH.value


def test_diag_finalizer_localizes_search_form_readiness_failure() -> None:
    diagnostics = {
        "final_sanitized_url": "https://www.fliggy.com/?tab=flight",
        "detector_state": summarize_detector_state(""),
        "search_form_ready": False,
    }
    recorder = _StageRecorder(time.monotonic())
    recorder.mark(BrowserProbeStage.BROWSER_LAUNCH, "launch")
    recorder.mark(BrowserProbeStage.ENTRY_NAVIGATION, "entry")
    recorder.mark(BrowserProbeStage.SEARCH_INPUT_READINESS, "readiness")

    _finalize_diagnostics(
        diagnostics=diagnostics,
        recorder=recorder,
        outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
        started=time.monotonic(),
    )

    assert diagnostics["last_successful_stage"] == BrowserProbeStage.ENTRY_NAVIGATION.value
    assert diagnostics["failed_stage"] == BrowserProbeStage.SEARCH_INPUT_READINESS.value
    assert diagnostics["failure_taxonomy"] == "SEARCH_FORM_NOT_READY"
    assert diagnostics["url_class"] == "FLIGGY_PUBLIC_ENTRY"
    assert diagnostics["challenge_detected"] is False


def test_diag_finalizer_keeps_result_transition_failure_at_handoff_stage() -> None:
    diagnostics = {
        "final_sanitized_url": "https://www.fliggy.com/?tab=flight",
        "detector_state": summarize_detector_state(""),
        "search_submission_attempted": True,
        "result_context_selected": False,
    }
    recorder = _StageRecorder(time.monotonic())
    recorder.mark(BrowserProbeStage.SEARCH_SUBMIT, "submit")
    recorder.mark(BrowserProbeStage.RESULT_TRANSITION, "handoff")
    recorder.mark(BrowserProbeStage.RESULT_READINESS, "wait")
    recorder.mark(BrowserProbeStage.LEVEL1_DISCOVERY, "rows")

    _finalize_diagnostics(
        diagnostics=diagnostics,
        recorder=recorder,
        outcome=BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
        started=time.monotonic(),
    )

    assert diagnostics["last_successful_stage"] == BrowserProbeStage.SEARCH_SUBMIT.value
    assert diagnostics["failed_stage"] == BrowserProbeStage.RESULT_TRANSITION.value
    assert diagnostics["failure_taxonomy"] == "SEARCH_SUBMIT_NO_TRANSITION"


def test_experiment_diagnosis_classification() -> None:
    timeout = _result(BrowserProbeOutcome.TIMEOUT, headless=True)
    headed_success = _result(BrowserProbeOutcome.SUCCESS_PARTIAL, headless=False)
    headless_timeout = _result(BrowserProbeOutcome.TIMEOUT, headless=True)

    assert classify_experiment_diagnosis((timeout,)) is ExperimentDiagnosis.STABLE_TIMEOUT
    assert (
        classify_experiment_diagnosis((headless_timeout, headed_success))
        is ExperimentDiagnosis.HEADLESS_SPECIFIC_FAILURE
    )
    assert classify_experiment_diagnosis((headed_success,)) is ExperimentDiagnosis.STABLE_SUCCESS
    assert classify_experiment_diagnosis((_result(BrowserProbeOutcome.ACCESS_CHALLENGE, headless=True),)) is (
        ExperimentDiagnosis.ACCESS_CHALLENGE
    )


def test_probe_input_keeps_browser_mode_runtime_only() -> None:
    probe_input = ProbeInput("北京", "上海", date(2026, 9, 14), headless=False)

    assert probe_input.headless is False
    assert BrowserAcquisitionMode.BROWSER.value == "BROWSER"


def test_all_required_probe_outcomes_are_probe_local() -> None:
    assert {outcome.value for outcome in BrowserProbeOutcome} == {
        "SUCCESS_COMPLETE",
        "SUCCESS_PARTIAL",
        "SUCCESS_EMPTY",
        "ACCESS_CHALLENGE",
        "LOGIN_REQUIRED",
        "TIMEOUT",
        "PROVIDER_ERROR",
        "NETWORK_ERROR",
        "EVIDENCE_INSUFFICIENT",
    }


def test_probe_path_does_not_construct_canonical_domain_objects() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "fliggy"
        / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "from flight_agent.domain.flights" not in source
    assert "from flight_agent.ports.flight_providers" not in source
    assert "FlightSegment(" not in source
    assert "Itinerary(" not in source
    assert "Offer(" not in source
    assert "ProviderSearchResult(" not in source


def test_real_fliggy_smoke_is_explicit_opt_in_and_outside_ordinary_ci() -> None:
    smoke = REPO_ROOT / "scripts" / "ci" / "fliggy-browser-probe-smoke.ps1"
    backend_ci = (REPO_ROOT / "scripts" / "ci" / "backend.ps1").read_text(encoding="utf-8")
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")

    assert smoke.exists()
    assert "fliggy-browser-probe-smoke" not in backend_ci
    assert "fliggy-browser-probe-smoke" not in all_ci


def _result(outcome: BrowserProbeOutcome, *, headless: bool) -> ProbeRunResult:
    return ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 8, 31, tzinfo=UTC),
        experiment_run_id="run",
        search_scope={"origin_text": "北京", "destination_text": "上海", "departure_date": "2026-09-14"},
        search_plan_id=None,
        execution_id=None,
        outcome=outcome,
        observed_result_count=1 if outcome in {BrowserProbeOutcome.SUCCESS_COMPLETE, BrowserProbeOutcome.SUCCESS_PARTIAL} else 0,
        duration_ms=100,
        dom_traversal_assessment=DomTraversalAssessment.PARTIAL_OBSERVED,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref="https://flights.alitrip.com/flight_search_result.htm",
        evidence=(),
        diagnostics={"headless": headless},
    )


def _query_state(
    *,
    origin: str = "北京",
    destination: str = "上海",
    departure_date: str = "2026-09-14",
    form_origin: str | None = "北京",
    form_destination: str | None = "上海",
    form_date: str | None = "2026-09-14",
) -> PublicSearchQueryState:
    return PublicSearchQueryState(
        requested_origin=origin,
        requested_destination=destination,
        requested_departure_date=departure_date,
        form_origin_readback=form_origin,
        form_destination_readback=form_destination,
        form_date_readback=form_date,
    )


def _destination_candidate(label: str, *, selector: str = ".next-overlay-wrapper li", index: int = 0) -> DestinationSuggestionCandidate:
    return DestinationSuggestionCandidate(selector=selector, index=index, label=label)


def _post_submit_base_diagnostics(*, submit_executed: bool = True) -> dict[str, object]:
    return {
        "pre_submit_query_state": _query_state().to_dict(),
        "pre_submit_query_verification": _verify_pre_submit_query_state(_query_state()).to_dict(),
        "destination_commitment": {"commitment_status": "confirmed", "failure_taxonomy": None},
        "submit_executed": submit_executed,
    }


def _post_submit_handoff(
    *,
    selected_page_url: str = "https://sjipiao.fliggy.com/homeow/trip_flight_search.htm?depCity=北京&arrCity=上海&depDate=2026-09-14",
    route_match: bool = True,
    date_match: bool = True,
    context_match: bool = True,
    query_identity_decision: str = "match",
    mismatch_dimension: str = "none",
    observed_date_text: str = "2026-09-14",
) -> dict[str, object]:
    return {
        "selected_page_url": selected_page_url,
        "page_count_after_submit": 2,
        "popup_or_new_page_event": True,
        "result_surface_present": True,
        "route_match": route_match,
        "date_match": date_match,
        "context_match": context_match,
        "query_identity_decision": query_identity_decision,
        "selection_reason": "result_identity_route_date_surface_match" if context_match else "route_and_date_mismatch",
        "mismatch_dimension": mismatch_dimension,
        "observed_date_text": observed_date_text,
        "observed_date_source": "visible_text",
        "normalized_observed_date": "2026-09-14" if date_match else "2026-09-06",
        "date_parse_status": "parsed",
    }


def _result_context_candidate(
    *,
    index: int,
    url: str,
    title: str,
    identity: FliggyPageIdentity,
    is_current: bool,
    origin: bool = True,
    destination: bool = True,
    departure_date: bool = True,
    result_surface: bool = True,
    route_conflict: bool = False,
    date_conflict: bool = False,
    alive: bool = True,
) -> ResultContextCandidate:
    route_match = origin and destination and not route_conflict
    date_match = departure_date and not date_conflict
    return ResultContextCandidate(
        page_index=index,
        sanitized_url=url,
        title=title,
        identity=identity,
        search_plan_evidence={
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "result_surface": result_surface,
            "route_conflict": route_conflict,
            "date_conflict": date_conflict,
            "route_match": route_match,
            "date_match": date_match,
            "result_surface_present": result_surface,
            "query_identity_decision": "match" if route_match and date_match and result_surface else "insufficient",
            "mismatch_dimension": "none"
            if route_match and date_match
            else "date"
            if route_match
            else "route"
            if date_match
            else "both",
        },
        is_current_page=is_current,
        alive=alive,
    )
