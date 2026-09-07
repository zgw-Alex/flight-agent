from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from flight_agent.adapters.flight_providers.fliggy import browser_probe as fliggy_browser_probe
from flight_agent.adapters.flight_providers.fliggy.browser_probe import (
    FLIGGY_BROWSER_PROBE_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    BrowserProbeStage,
    ControlReadiness,
    DestinationSuggestionCandidate,
    DestinationSuggestionSnapshot,
    DestinationSuggestionSurfaceClassification,
    DomTraversalAssessment,
    ExperimentDiagnosis,
    FliggyPageIdentity,
    ProbeInput,
    ProbeRunResult,
    ProviderMarketCompleteness,
    PublicDestinationActivationClass,
    PublicQueryClassification,
    PublicSearchQueryState,
    ResultContextCandidate,
    SearchFormReadiness,
    StageDiagnostic,
    _annotate_destination_s8,
    _annotate_post_submit_query_propagation,
    _annotated_region_svg,
    _assign_public_hit_region_ids,
    _bind_public_destination_target,
    _browser_failure_taxonomy,
    _build_post_submit_query_state_diagnostics,
    _classify_destination_suggestion_surface,
    _classify_human_hit_differential,
    _classify_public_destination_activation,
    _classify_public_query_state,
    _combine_destination_extension_reasons,
    _commit_public_destination,
    _destination_commitment_result,
    _destination_commitment_status,
    _destination_readback_matches,
    _destination_readback_transition_count,
    _destination_stability_diagnostics,
    _destination_stability_forward_progress,
    _destination_stability_root_cause,
    _destination_suggestion_lifecycle_diagnostics,
    _diag_context_inventory,
    _diag_u4_p0_p7,
    _diag_u4_root_cause,
    _diag_u6_h0_h8,
    _diag_u6_root_cause_class,
    _diag_u7_root_cause_class,
    _finalize_diagnostics,
    _fliggy_default_query_signature,
    _live_observation_preflight,
    _marker_transition_count,
    _public_commit_state_classification,
    _public_date_commitment,
    _public_destination_activation_root_class,
    _public_hit_region_relationships,
    _public_hit_target_root_class,
    _resolve_destination_candidate,
    _resolve_public_destination_city_candidate,
    _result_state_extension_reason,
    _result_state_failure_taxonomy,
    _result_state_forward_progress,
    _result_state_retryable,
    _result_state_sample,
    _settled_state_reached,
    _stable_stale_or_default_result_state,
    _StageRecorder,
    _submit_verified_public_flight_search,
    _verify_pre_submit_query_state,
    assess_dom_coverage,
    choose_result_context_candidate,
    classify_destination_activation_mode,
    classify_destination_suggestion_mode,
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

    sanitized = sanitize_probe_payload({"destination_commitment": commitment.to_dict()})["destination_commitment"]

    assert sanitized["candidate_labels"] == ["[REDACTED]"]
    assert sanitized["selected_candidate_label"] == "[REDACTED]"
    assert sanitized["commit_readback"] == "[REDACTED]"
    assert sanitized["destination_match"] is True
    assert sanitized["commitment_status"] == "confirmed"
    assert sanitized["failure_taxonomy"] is None
    assert sanitized["destination_stability_diagnostics"]["d4_option_inventory"]["candidate_labels"] == ["[REDACTED]"]


def test_dst12_placeholder_destination_can_never_equal_shanghai() -> None:
    assert _destination_readback_matches("到达城市，可直接输入城市名称搜索", "上海") is False


def test_ds01_write_shanghai_unique_suggestion_selects_stable_shanghai() -> None:
    candidate = _destination_candidate("上海")
    diagnostics = _destination_stability_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        input_text_after_write="上海",
        candidates=(candidate,),
        selected_candidate=candidate,
        selection_method="click",
        commit_readback="上海",
        commitment_status="confirmed",
        failure_taxonomy=None,
        readback_sequence=("上海", "上海"),
        extension_used=False,
        extension_reason="none",
    )

    assert diagnostics["d5_option_match"]["match_decision"] == "unique"
    assert diagnostics["d9_pre_submit_stability"]["stable_readback"] == "上海"
    assert diagnostics["root_cause_class"] == "INCONCLUSIVE"


def test_ds02_stale_history_suggestion_inventory_preserves_hangzhou_vs_shanghai() -> None:
    candidates = (_destination_candidate("杭州"), _destination_candidate("上海", index=1))
    diagnostics = _destination_stability_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        input_text_after_write="上海",
        candidates=candidates,
        selected_candidate=candidates[1],
        selection_method="click",
        commit_readback="上海",
        commitment_status="confirmed",
        failure_taxonomy=None,
        readback_sequence=("上海",),
        extension_used=False,
        extension_reason="none",
    )

    labels = diagnostics["d4_option_inventory"]["candidate_labels"]
    assert labels == ["杭州", "上海"]
    assert diagnostics["d4_option_inventory"]["suggestion_classes"][0]["matches_requested"] is False
    assert diagnostics["d4_option_inventory"]["suggestion_classes"][1]["matches_requested"] is True


def test_ds03_matched_shanghai_action_lands_on_hangzhou_is_selection_misapplied() -> None:
    candidate = _destination_candidate("上海")
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write="上海",
            candidates=(candidate,),
            selected_candidate=candidate,
            selection_method="click",
            commit_readback="杭州",
            commitment_status="mismatch",
            failure_taxonomy="FORM_DESTINATION_MISMATCH",
            readback_sequence=("杭州",),
            stable_readback="杭州",
        )
        == "DESTINATION_SELECTION_ACTION_MISAPPLIED"
    )


def test_ds04_first_shanghai_then_reset_hangzhou_is_post_commit_reset() -> None:
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write="上海",
            candidates=(_destination_candidate("上海"),),
            selected_candidate=_destination_candidate("上海"),
            selection_method="click",
            commit_readback="上海",
            commitment_status="confirmed",
            failure_taxonomy=None,
            readback_sequence=("上海", "杭州"),
            stable_readback="杭州",
        )
        == "DESTINATION_POST_COMMIT_RESET"
    )


def test_ds05_visible_committed_shanghai_but_readback_wrong_control_hangzhou_is_misapplied() -> None:
    diagnostics = _destination_stability_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        input_text_after_write="上海",
        candidates=(_destination_candidate("上海"),),
        selected_candidate=_destination_candidate("上海"),
        selection_method="click",
        commit_readback="杭州",
        commitment_status="mismatch",
        failure_taxonomy="FORM_DESTINATION_MISMATCH",
        readback_sequence=("杭州",),
        extension_used=False,
        extension_reason="none",
    )

    assert diagnostics["d8_control_readback"]["unexpected_value"] == "杭州"
    assert diagnostics["root_cause_class"] == "DESTINATION_SELECTION_ACTION_MISAPPLIED"


def test_ds06_multiple_shanghai_like_options_are_ambiguous() -> None:
    candidates = (_destination_candidate("上海虹桥"), _destination_candidate("上海浦东", index=1))
    resolution = _resolve_destination_candidate(candidates, "上海", suggestion_surface_present=True)

    assert resolution.failure_taxonomy == "DESTINATION_OPTION_AMBIGUOUS"
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write="上海",
            candidates=candidates,
            selected_candidate=None,
            selection_method="none",
            commit_readback="上海",
            commitment_status="mismatch",
            failure_taxonomy="DESTINATION_OPTION_AMBIGUOUS",
            readback_sequence=("上海",),
            stable_readback="上海",
        )
        == "DESTINATION_OPTION_AMBIGUOUS"
    )


def test_ds07_no_shanghai_option_is_not_found() -> None:
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write="上海",
            candidates=(_destination_candidate("杭州"),),
            selected_candidate=None,
            selection_method="none",
            commit_readback="上海",
            commitment_status="mismatch",
            failure_taxonomy="DESTINATION_OPTION_NOT_FOUND",
            readback_sequence=("上海",),
            stable_readback="上海",
        )
        == "DESTINATION_OPTION_NOT_FOUND"
    )


def test_ds08_write_itself_becomes_hangzhou_before_suggestion() -> None:
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write="杭州",
            candidates=(),
            selected_candidate=None,
            selection_method="none",
            commit_readback="杭州",
            commitment_status="mismatch",
            failure_taxonomy="FORM_DESTINATION_MISMATCH",
            readback_sequence=("杭州",),
            stable_readback="杭州",
        )
        == "DESTINATION_INPUT_WRITE_DRIFT"
    )


def test_ds09_control_identity_changes_when_destination_control_not_ready() -> None:
    assert (
        _destination_stability_root_cause(
            requested_destination="上海",
            input_text_after_write=None,
            candidates=(),
            selected_candidate=None,
            selection_method="none",
            commit_readback=None,
            commitment_status="failed",
            failure_taxonomy="DESTINATION_CONTROL_NOT_READY",
            readback_sequence=(),
            stable_readback=None,
        )
        == "DESTINATION_CONTROL_IDENTITY_DRIFT"
    )


def test_ds10_stable_shanghai_after_progress_extension_records_timing_evidence() -> None:
    sequence = ["上海", "杭州", "上海"]

    assert _destination_stability_forward_progress(sequence) is True
    assert _destination_readback_transition_count(tuple(sequence)) == 2


def test_ds11_no_progress_means_no_extension_signal() -> None:
    sequence = ["上海", "上海"]

    assert _destination_stability_forward_progress(sequence) is False


def test_ds12_hangzhou_never_equals_shanghai_and_q1_blocks() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="杭州", form_date="2026-09-14")
    )

    assert _destination_readback_matches("杭州", "上海") is False
    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_ROUTE_MISMATCH"


def test_ds13_headed_and_headless_timing_evidence_can_be_recorded_independently() -> None:
    headed = _destination_stability_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        input_text_after_write="上海",
        candidates=(),
        selected_candidate=None,
        selection_method="none",
        commit_readback="杭州",
        commitment_status="mismatch",
        failure_taxonomy="DESTINATION_SUGGESTION_NOT_READY",
        readback_sequence=("杭州",),
        extension_used=False,
        extension_reason="none",
    )
    headless = {**headed, "headless": True}

    assert headed["d9_pre_submit_stability"]["tdest_ms"] == 1500
    assert headless["headless"] is True


def test_ds14_destination_diagnostics_sanitized_and_downstream_unchanged() -> None:
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
        readback_sequence=("上海 Cookie: a=b",),
    )

    sanitized = sanitize_probe_payload({"destination_commitment": commitment.to_dict()})

    assert sanitized["destination_commitment"]["destination_stability_diagnostics"]["d4_option_inventory"]["candidate_labels"] == [
        "[REDACTED]"
    ]
    assert "destination_stability_diagnostics" in sanitized["destination_commitment"]
    assert _verify_pre_submit_query_state(_query_state()).submit_allowed is True


def test_hu5_01_stable_wrong_destination_write_blocks_before_suggestions() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="杭州",
        candidates=(),
        suggestion_surface_present=False,
        selected_candidate=None,
        selection_method="none",
        commit_readback="杭州",
        failure_taxonomy="FORM_DESTINATION_MISMATCH",
        readback_sequence=("杭州", "杭州"),
        input_text_after_write="杭州",
    )

    diagnostics = commitment.destination_stability_diagnostics

    assert commitment.commitment_status == "mismatch"
    assert commitment.failure_taxonomy == "FORM_DESTINATION_MISMATCH"
    assert diagnostics["d2_input_write"]["input_write_match"] is False
    assert diagnostics["d3_suggestion_ready"]["suggestion_count"] == 0
    assert diagnostics["root_cause_class"] == "DESTINATION_INPUT_WRITE_DRIFT"


def test_hu5_02_destination_write_can_settle_to_shanghai_without_stale_failure_but_not_commit() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(),
        suggestion_surface_present=False,
        selected_candidate=None,
        selection_method="none",
        commit_readback="上海",
        failure_taxonomy="DESTINATION_SUGGESTION_NOT_READY",
        readback_sequence=("杭州", "上海", "上海"),
        input_text_after_write="上海",
        extension_used=True,
        extension_reason="destination_input_write_changed",
    )

    diagnostics = commitment.destination_stability_diagnostics

    assert commitment.commitment_status == "insufficient"
    assert commitment.failure_taxonomy == "DESTINATION_SUGGESTION_NOT_READY"
    assert diagnostics["d2_input_write"]["input_write_match"] is True
    assert diagnostics["d9_pre_submit_stability"]["stable_readback"] == "上海"
    assert diagnostics["d9_pre_submit_stability"]["extension_reason"] == "destination_input_write_changed"


def test_hu5_03_destination_write_repair_keeps_tdest_and_two_window_bound() -> None:
    diagnostics = _destination_stability_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        input_text_after_write="上海",
        candidates=(),
        selected_candidate=None,
        selection_method="none",
        commit_readback="上海",
        commitment_status="confirmed",
        failure_taxonomy=None,
        readback_sequence=("上海",),
        extension_used=False,
        extension_reason="none",
    )

    assert diagnostics["d9_pre_submit_stability"]["tdest_ms"] == 1500
    assert diagnostics["d9_pre_submit_stability"]["max_observation_ms"] == 3000
    assert (
        fliggy_browser_probe._FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS
        * fliggy_browser_probe._FLIGGY_DESTINATION_SUGGESTION_WAIT_MS
        * 2
        <= 3000
    )


def test_hu5_04_extension_reasons_preserve_write_and_post_commit_evidence() -> None:
    assert (
        _combine_destination_extension_reasons("destination_input_write_changed", "destination_readback_changed")
        == "destination_input_write_changed+destination_readback_changed"
    )
    assert _combine_destination_extension_reasons("none", "destination_readback_changed") == "destination_readback_changed"
    assert _combine_destination_extension_reasons("none", "none") == "none"


def test_hu5_05_repair_preserves_u2_pre_submit_route_date_strictness() -> None:
    verification = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="杭州", form_date="2026-09-14")
    )

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_ROUTE_MISMATCH"


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


def test_ru6_01_q1_verified_source_query_still_allows_public_submit() -> None:
    verification = _verify_pre_submit_query_state(_query_state())

    assert verification.submit_allowed is True
    assert verification.failure_taxonomy is None


def test_ru6_02_q1_route_or_date_mismatch_still_blocks_public_submit() -> None:
    wrong_route = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="杭州", form_date="2026-09-14")
    )
    wrong_date = _verify_pre_submit_query_state(
        _query_state(origin="北京", destination="上海", form_origin="北京", form_destination="上海", form_date="2026-01-08")
    )

    assert wrong_route.submit_allowed is False
    assert wrong_route.failure_taxonomy == "FORM_ROUTE_MISMATCH"
    assert wrong_date.submit_allowed is False
    assert wrong_date.failure_taxonomy == "FORM_DATE_MISMATCH"


def test_ru6_03_q5_requires_route_date_and_result_surface_match() -> None:
    route_date_only = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        result_surface=False,
    )

    assert route_date_only.context_matches() is False
    assert choose_result_context_candidate((route_date_only,)) is None


def test_ru6_04_stale_default_result_is_not_selected_as_context() -> None:
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

    assert choose_result_context_candidate((stale,)) is None
    assert _result_state_failure_taxonomy((stale,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_ru6_05_stable_stale_default_samples_do_not_trigger_extension() -> None:
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
    sample = _result_state_sample(
        attempt=1,
        window="base",
        candidates=(stale,),
        selected=None,
        failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
    )

    assert _stable_stale_or_default_result_state((sample, sample)) is True
    assert _result_state_forward_progress([sample, sample]) is False


def test_ru6_06_single_stale_default_sample_is_not_objective_forward_progress() -> None:
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

    assert _stable_stale_or_default_result_state((sample,)) is False
    assert _result_state_forward_progress([sample]) is False


def test_ru6_07_stale_default_persistence_maps_to_result_state_initialization_root() -> None:
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
    sample = _result_state_sample(
        attempt=1,
        window="base",
        candidates=(stale,),
        selected=None,
        failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
    )

    assert (
        _diag_u4_root_cause(
            candidates=(stale,),
            selected=None,
            result_state_failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
            extension_used=False,
            samples=(sample, sample),
        )
        == "STALE_DEFAULT_CONTEXT_PERSISTED"
    )


def test_ru6_08_loading_transition_with_marker_change_can_still_extend_once() -> None:
    loading = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="航班查询",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    loading.search_plan_evidence["date_match"] = "insufficient"
    ready_wrong = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        date_conflict=True,
    )
    first = _result_state_sample(
        attempt=1,
        window="base",
        candidates=(loading,),
        selected=None,
        failure_taxonomy="RESULT_STATE_QUERY_UNREADABLE",
    )
    second = _result_state_sample(
        attempt=2,
        window="base",
        candidates=(ready_wrong,),
        selected=None,
        failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
    )

    assert _result_state_forward_progress([first, second]) is True
    assert _result_state_extension_reason([first, second]) == "route_date_marker_changed"


def test_ru6_09_deterministic_same_page_result_context_survives() -> None:
    same_page = _result_context_candidate(
        index=0,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海航班查询 09月14日",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=True,
    )

    assert choose_result_context_candidate((same_page,)) == same_page


def test_ru6_10_deterministic_popup_result_context_survives() -> None:
    entry = _result_context_candidate(
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

    assert choose_result_context_candidate((entry, popup)) == popup


def test_ru6_11_ambiguous_matching_contexts_are_never_guessed() -> None:
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


def test_ru6_12_wrong_route_result_context_stays_propagation_failure() -> None:
    wrong_route = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
    )

    assert _result_state_failure_taxonomy((wrong_route,), None) == "RESULT_STATE_ROUTE_MISMATCH"


def test_ru6_13_wrong_date_result_context_stays_propagation_failure() -> None:
    wrong_date = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    wrong_date.search_plan_evidence["date_match"] = False
    wrong_date.search_plan_evidence["date_conflict"] = False
    wrong_date.search_plan_evidence["normalized_observed_date"] = "2026-09-15"

    assert _result_state_failure_taxonomy((wrong_date,), None) == "RESULT_STATE_DATE_MISMATCH"


def test_ru6_14_closed_or_replaced_context_is_lifecycle_gap_not_guess() -> None:
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


def test_ru6_15_p0_p7_diagnostics_preserve_requested_query_identity() -> None:
    payload = _diag_u4_p0_p7(_post_submit_base_diagnostics(), _post_submit_handoff(route_match=True, date_match=True, context_match=True))

    assert payload["p0_source_pre_submit"]["query"]["origin"] == "北京"
    assert payload["p0_source_pre_submit"]["query"]["destination"] == "上海"
    assert payload["p7_query_identity"]["route_match"] is True
    assert payload["p7_query_identity"]["date_match"] is True


def test_ru6_16_result_state_diagnostics_keep_stale_20260108_sanitized_and_rejected() -> None:
    payload = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(observed_date_text="2026-01-08 Cookie: a=b", date_match=False, context_match=False),
    )
    sanitized = sanitize_probe_payload({"payload": payload})["payload"]

    assert sanitized["q4_result_state_init"]["observed_date_text"] == "[REDACTED]"
    assert sanitized["q5_result_context"]["context_match"] is False


def test_ru6_17_u5_destination_commitment_path_remains_intact() -> None:
    result = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(DestinationSuggestionCandidate(selector=".city", index=0, label="上海"),),
        suggestion_surface_present=True,
        selected_candidate=DestinationSuggestionCandidate(selector=".city", index=0, label="上海"),
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=None,
    )

    assert result.commitment_status == "confirmed"
    assert result.destination_stability_diagnostics["d9_pre_submit_stability"]["stable_readback"] == "上海"


def test_ru6_18_scope_remains_provider_local_without_downstream_or_shared_changes() -> None:
    source = (REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py").read_text(
        encoding="utf-8"
    )

    assert "_select_result_context_page" in source
    assert "_stable_stale_or_default_result_state" in source
    assert "CommonNormalizer" not in source
    assert "CandidateMerger" not in source


def test_ru7_01_verified_public_submit_clicks_once_after_input_context_growth(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def nth(self, index: int) -> FakeButton:
            assert index == 0
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str) -> FakeButton:
            assert selector == ".rc-flight-searchbar button.search-button"
            return FakeButton(self)

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [object()]

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        assert probe_input.destination_text == "上海"
        context.pages.append(object())
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state()

    context = FakeContext()
    page = FakePage()
    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(context, page, ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is True
    assert page.clicks == 1
    assert diagnostics["submit_executed"] is True
    assert diagnostics["public_submit_button_clicked_once"] is True
    assert diagnostics["page_count_before_submit"] == 1
    assert diagnostics["page_count_after_input_before_submit"] == 2
    assert diagnostics["pre_submit_context_count_changed"] is True


def test_ru7_02_verified_public_submit_does_not_click_when_q1_fails(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def nth(self, index: int) -> FakeButton:
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str) -> FakeButton:
            return FakeButton(self)

    class FakeContext:
        pages: ClassVar[list[object]] = [object()]

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state(form_destination="杭州")

    page = FakePage()
    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(FakeContext(), page, ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is False
    assert page.clicks == 0
    assert diagnostics["submit_executed"] is False
    assert diagnostics["public_submit_button_clicked_once"] is False
    assert diagnostics["pre_submit_query_verification"]["failure_taxonomy"] == "FORM_ROUTE_MISMATCH"


def test_ru7_03_newest_page_heuristic_remains_insufficient() -> None:
    correct_first = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    stale_newest = _result_context_candidate(
        index=2,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到杭州机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
        route_conflict=True,
        date_conflict=True,
    )
    stale_newest.search_plan_evidence["normalized_expected_date"] = "2026-09-14"
    stale_newest.search_plan_evidence["normalized_observed_date"] = "2026-01-08"

    assert choose_result_context_candidate((correct_first, stale_newest)) == correct_first


def test_ru7_04_correct_deterministic_successor_can_be_followed() -> None:
    source = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞机票查询-机票预订【飞猪旅行】",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        result_surface=False,
    )
    successor = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海航班查询 09月14日",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((source, successor)) == successor


def test_ru7_05_ambiguous_successors_are_not_guessed() -> None:
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


def test_ru7_06_stale_default_20260108_never_passes_requested_20260914() -> None:
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

    assert choose_result_context_candidate((stale,)) is None
    assert _result_state_failure_taxonomy((stale,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_ru7_07_all_stale_candidates_fail_explicitly() -> None:
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

    assert _result_state_failure_taxonomy((stale,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_ru7_08_stabilized_stale_context_does_not_extend_window() -> None:
    stale = _stale_default_sample(attempt=1)

    assert _stable_stale_or_default_result_state((stale, stale)) is True
    assert _result_state_forward_progress([stale, stale]) is False


def test_ru7_09_loading_then_query_correct_candidate_can_extend_once() -> None:
    loading = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="航班查询",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    loading.search_plan_evidence["date_match"] = "insufficient"
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海航班查询 09月14日",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    first = _result_state_sample(attempt=1, window="base", candidates=(loading,), selected=None, failure_taxonomy="RESULT_STATE_QUERY_UNREADABLE")
    second = _result_state_sample(attempt=2, window="base", candidates=(correct,), selected=correct, failure_taxonomy="RESULT_STATE_READY")

    assert _result_state_forward_progress([first, second]) is True
    assert _result_state_extension_reason([first, second]) == "route_date_marker_changed"


def test_ru7_10_q1_strict_route_date_verification_preserved() -> None:
    verification = _verify_pre_submit_query_state(_query_state())

    assert verification.submit_allowed is True
    assert verification.query_state_decision == "match"


def test_ru7_11_q1_strict_destination_mismatch_blocks_submit() -> None:
    verification = _verify_pre_submit_query_state(_query_state(form_destination="杭州"))

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_ROUTE_MISMATCH"


def test_ru7_12_q1_strict_date_mismatch_blocks_submit() -> None:
    verification = _verify_pre_submit_query_state(_query_state(form_date="2026-01-08"))

    assert verification.submit_allowed is False
    assert verification.failure_taxonomy == "FORM_DATE_MISMATCH"


def test_ru7_13_q5_requires_route_and_date_identity() -> None:
    handoff = _post_submit_handoff(route_match=True, date_match=False, context_match=False)
    diagnostics = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), handoff)

    assert diagnostics["q5_result_context"]["route_match"] is True
    assert diagnostics["q5_result_context"]["date_match"] is False
    assert diagnostics["q5_result_context"]["context_match"] is False


def test_ru7_14_source_identity_remains_comparison_only_not_injected() -> None:
    source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "_capture_public_search_query_state" in source
    assert "write_verified_source_query" not in source
    assert "inject" not in source.lower()


def test_ru7_15_verified_submit_no_longer_uses_page_count_as_skip_gate() -> None:
    source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py"
    ).read_text(encoding="utf-8")
    function_body = source.split("async def _submit_verified_public_flight_search", 1)[1].split("async def _commit_public_destination", 1)[0]

    assert "len(context.pages) == page_count_before_submit" not in function_body
    assert "public_submit_button_clicked_once" in function_body


def test_ru7_16_submit_sequence_documents_verified_public_button_once() -> None:
    source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "verified_public_search_button_once" in source
    assert "submit_fallback_if_needed" not in source


def test_ru7_17_handoff_diagnostics_keep_input_before_submit_boundary() -> None:
    handoff = _post_submit_handoff()
    handoff["page_count_before_submit"] = 1
    handoff["page_count_after_input_before_submit"] = 2
    handoff["pre_submit_context_count_changed"] = True

    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h3_handoff_event"]["page_count_before_submit"] == 1
    assert payload["h3_handoff_event"]["page_count_after_input_before_submit"] == 2
    assert payload["h3_handoff_event"]["pre_submit_context_count_changed"] is True
    assert payload["h3_handoff_event"]["page_count_after_submit"] == handoff["page_count_after_submit"]


def test_ru7_18_d0_d9_destination_commitment_path_is_unchanged() -> None:
    result = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(DestinationSuggestionCandidate(selector=".city", index=0, label="上海"),),
        suggestion_surface_present=True,
        selected_candidate=DestinationSuggestionCandidate(selector=".city", index=0, label="上海"),
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=None,
    )

    assert result.commitment_status == "confirmed"
    assert result.destination_stability_diagnostics["d9_pre_submit_stability"]["stable_readback"] == "上海"


def test_ru7_19_h0_h8_diagnostics_remain_available() -> None:
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), _post_submit_handoff())

    assert set(payload) == {
        "h0_verified_source_query",
        "h1_source_public_state",
        "h2_submit_trigger",
        "h3_handoff_event",
        "h4_new_context_initial_state",
        "h5_initialization_transitions",
        "h6_stale_default_introduction",
        "h7_settled_context_state",
        "h8_strict_identity",
        "public_overlay_evidence",
    }


def test_ru7_20_scope_remains_fliggy_provider_local_without_shared_changes() -> None:
    source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py"
    ).read_text(encoding="utf-8")

    assert "_submit_verified_public_flight_search" in source
    assert "CommonNormalizer" not in source
    assert "CandidateMerger" not in source
    assert "localStorage" not in source
    assert "HAR" not in source


def test_du7_01_requested_query_classifies_when_committed_and_result_matches() -> None:
    evidence = _classification_evidence(route=("北京", "上海"), observed_date="2026-09-14", route_match=True, date_match=True)

    assert _classify_public_query_state(evidence, requested_query=_requested_query(), default_signature=_default_signature()) is PublicQueryClassification.REQUESTED_QUERY


def test_du7_02_default_query_classifies_beijing_hangzhou_run_local_tomorrow() -> None:
    evidence = _classification_evidence(route=("北京", "杭州"), observed_date="2026-09-07", route_match=False, date_match=False)

    assert _classify_public_query_state(evidence, requested_query=_requested_query(), default_signature=_default_signature()) is PublicQueryClassification.DEFAULT_QUERY


def test_du7_03_historical_non_default_wrong_query_is_stale_not_default() -> None:
    evidence = _classification_evidence(route=("广州", "深圳"), observed_date="2026-01-08", route_match=False, date_match=False)

    assert _classify_public_query_state(evidence, requested_query=_requested_query(), default_signature=_default_signature()) is PublicQueryClassification.STALE_QUERY


def test_du7_04_requested_route_default_date_is_partial_and_q5_fail() -> None:
    evidence = _classification_evidence(route=("北京", "上海"), observed_date="2026-09-07", route_match=True, date_match=False)
    handoff = _post_submit_handoff(route_match=True, date_match=False, context_match=False)

    assert _classify_public_query_state(evidence, requested_query=_requested_query(), default_signature=_default_signature()) is PublicQueryClassification.PARTIAL_QUERY
    assert _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), handoff)["q5_result_context"]["context_match"] is False


def test_du7_05_insufficient_public_query_evidence_is_unknown() -> None:
    assert _classify_public_query_state({}, requested_query=_requested_query(), default_signature=_default_signature()) is PublicQueryClassification.UNKNOWN_QUERY


def test_du7_06_default_date_is_run_local_tomorrow_not_hard_coded() -> None:
    assert _fliggy_default_query_signature(date(2026, 9, 6))["departure_date"] == "2026-09-07"
    assert _fliggy_default_query_signature(date(2026, 9, 8))["departure_date"] == "2026-09-09"


def test_du7_07_actual_public_click_remains_exactly_once(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def nth(self, index: int) -> FakeButton:
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str) -> FakeButton:
            return FakeButton(self)

    class FakeContext:
        pages: ClassVar[list[object]] = [object()]

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state()

    page = FakePage()
    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(FakeContext(), page, ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is True
    assert page.clicks == 1
    assert diagnostics["public_submit_button_clicked_once"] is True


def test_du7_08_context_created_during_input_does_not_count_as_executed_submit(monkeypatch) -> None:
    class FakeButton:
        def nth(self, index: int) -> FakeButton:
            return self

        async def click(self) -> None:
            return None

    class FakePage:
        def locator(self, selector: str) -> FakeButton:
            return FakeButton()

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [object()]

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        context.pages.append(object())
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state(form_date="2026-01-08")

    context = FakeContext()
    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(context, FakePage(), ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is False
    assert diagnostics["pre_submit_context_count_changed"] is True
    assert diagnostics["submit_executed"] is False


def test_du7_09_c0_c8_diagnostics_preserve_existing_p_and_h_layers() -> None:
    payload = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff())

    assert "diag_u4_p0_p7" in payload
    assert "diag_u6_h0_h8" in payload
    assert set(payload["diag_u7_c0_c8"]) >= {
        "c0_requested_query",
        "c1_visible_pre_q1_state",
        "c2_public_commit_state",
        "c3_immediate_pre_click_state",
        "c4_public_click",
        "c5_earliest_post_click_context",
        "c6_context_lifecycle_transitions",
        "c7_settled_result_classification",
        "c8_strict_q5",
    }


def test_du7_10_strict_q1_and_q5_remain_unchanged() -> None:
    q1 = _verify_pre_submit_query_state(_query_state())
    q5 = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff(date_match=False, context_match=False))

    assert q1.submit_allowed is True
    assert q5["q5_result_context"]["context_match"] is False


def test_du7_11_requested_identity_is_comparison_only_not_injected() -> None:
    source = _fliggy_source_text()

    assert "_classify_public_query_state" in source
    assert "write_verified_source_query" not in source
    assert "provider-state injection" not in source


def test_du7_12_default_query_is_negative_evidence_and_cannot_satisfy_q5() -> None:
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False)
    handoff.update(_classification_evidence(route=("北京", "杭州"), observed_date="2026-09-07", route_match=False, date_match=False))
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["acquired_at"] = "2026-09-06T08:00:00+00:00"

    payload = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert payload["diag_u7_c0_c8"]["c7_settled_result_classification"]["classification"]["classification"] == "DEFAULT_QUERY"
    assert payload["q5_result_context"]["context_match"] is False


def test_du7_13_context_replacement_remains_deterministic_no_newest_page_guess() -> None:
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


def test_du7_14_overlay_not_root_cause_without_evidence() -> None:
    payload = _diag_u7_root_cause_class(_build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff())["diag_u7_c0_c8"])

    assert payload != "MODAL_BLOCKS_QUERY_INITIALIZATION"


def test_du7_15_no_forbidden_provider_state_manipulation() -> None:
    source = _fliggy_source_text()

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "route(" not in source


def test_du7_16_sanitization_redacts_sensitive_diagnostics() -> None:
    payload = sanitize_probe_payload({"cookie": "secret", "authorization": "token", "safe": "visible"})

    assert payload["cookie"] == "[REDACTED]"
    assert payload["authorization"] == "[REDACTED]"
    assert payload["safe"] == "visible"


def test_du7_17_l1_l2_and_shared_contracts_untouched_by_diag_u7_scope() -> None:
    changed_files = {path.replace("\\", "/") for path in _tracked_diff_names()}

    assert "apps/backend/src/flight_agent/adapters/flight_providers/fliggy/mapper.py" not in changed_files
    assert "apps/backend/src/flight_agent/adapters/flight_providers/fliggy/level2_mapper.py" not in changed_files
    assert not any(path.startswith("apps/backend/src/flight_agent/domain/") for path in changed_files)


def test_du7_18_live_capture_output_path_is_cwd_independent() -> None:
    script = (REPO_ROOT / "scripts" / "ci" / "fliggy-browser-probe-smoke.ps1").read_text(encoding="utf-8")

    assert "$OutputPath" in script
    assert "GetUnresolvedProviderPathFromPSPath" in script
    assert '"--output-json", $ResolvedOutputPath' in script
    assert "Tee-Object -FilePath $ConsoleOutputPath" in script


def test_ru8_01_confirmed_public_write_path_preserves_requested_query() -> None:
    diagnostics = _confirmed_public_write_diagnostics()
    diagnostics["pre_submit_query_state"] = _query_state().to_dict()
    diagnostics["pre_submit_query_verification"] = _verify_pre_submit_query_state(_query_state()).to_dict()

    assert _public_commit_state_classification(
        diagnostics,
        diagnostics["pre_submit_query_state"],
        diagnostics["pre_submit_query_verification"],
    ) is PublicQueryClassification.REQUESTED_QUERY


def test_ru8_02_visible_destination_without_option_selection_is_not_commit_success() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(),
        suggestion_surface_present=False,
        selected_candidate=None,
        selection_method="none",
        commit_readback="上海",
        failure_taxonomy="DESTINATION_SUGGESTION_NOT_READY",
    )

    assert commitment.commitment_status == "insufficient"
    assert commitment.failure_taxonomy == "DESTINATION_SUGGESTION_NOT_READY"


def test_ru8_03_visible_date_without_commit_action_is_not_commit_success() -> None:
    commitment = _public_date_commitment(
        requested_date="2026-09-14",
        typed_date="2026-09-14",
        commit_readback="2026-09-14",
        action_performed=False,
    )

    assert commitment["commitment_status"] == "insufficient"
    assert commitment["failure_taxonomy"] == "DATE_COMMIT_NOT_CONFIRMED"


def test_ru8_04_destination_option_click_is_the_only_confirming_destination_action() -> None:
    candidate = _destination_candidate("上海")
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(candidate,),
        suggestion_surface_present=True,
        selected_candidate=candidate,
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=None,
    )

    assert commitment.commitment_status == "confirmed"
    assert commitment.destination_stability_diagnostics["d6_selection_action"]["selection_method"] == "click"


def test_ru8_05_date_enter_commit_records_public_commit_evidence() -> None:
    commitment = _public_date_commitment(
        requested_date="2026-09-14",
        typed_date="2026-09-14",
        commit_readback="2026-09-14",
        action_performed=True,
    )

    assert commitment["commitment_status"] == "confirmed"
    assert commitment["selection_method"] == "keyboard_enter_after_public_date_fill"


def test_ru8_06_q1_is_revalidated_after_public_commit_before_submit(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def nth(self, index: int) -> FakeButton:
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str) -> FakeButton:
            return FakeButton(self)

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state(form_date="2026-01-08")

    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    page = FakePage()
    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(_FakeContext(), page, ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is False
    assert page.clicks == 0
    assert diagnostics["pre_submit_query_verification"]["failure_taxonomy"] == "FORM_DATE_MISMATCH"


def test_ru8_07_public_search_button_is_clicked_once_when_commit_and_q1_pass(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page: FakePage) -> None:
            self.page = page

        def nth(self, index: int) -> FakeButton:
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str) -> FakeButton:
            return FakeButton(self)

    async def fake_write(page: FakePage, probe_input: ProbeInput) -> dict[str, object]:
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page: FakePage, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state()

    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    page = FakePage()
    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(_FakeContext(), page, ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is True
    assert page.clicks == 1
    assert diagnostics["public_submit_button_clicked_once"] is True


def test_ru8_08_input_stage_context_change_does_not_substitute_for_submit(monkeypatch) -> None:
    async def fake_write(page: object, probe_input: ProbeInput) -> dict[str, object]:
        context.pages.append(object())
        return {"destination_commitment": {"commitment_status": "insufficient"}}

    async def fake_capture(page: object, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state()

    context = _FakeContext()
    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)

    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(context, object(), ProbeInput("北京", "上海", date(2026, 9, 14))))

    assert allowed is False
    assert diagnostics["pre_submit_context_count_changed"] is True
    assert diagnostics["submit_executed"] is False


def test_ru8_09_default_query_remains_terminal_negative_evidence_for_q5() -> None:
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False)
    handoff.update(_classification_evidence(route=("北京", "杭州"), observed_date="2026-09-07", route_match=False, date_match=False))
    diagnostics = _confirmed_public_write_diagnostics()
    diagnostics.update(_post_submit_base_diagnostics())
    diagnostics["acquired_at"] = "2026-09-06T08:00:00+00:00"

    payload = _build_post_submit_query_state_diagnostics(diagnostics, handoff)

    assert payload["diag_u7_c0_c8"]["c7_settled_result_classification"]["classification"]["classification"] == "DEFAULT_QUERY"
    assert payload["q5_result_context"]["context_match"] is False


def test_ru8_10_partial_query_remains_q5_fail() -> None:
    handoff = _post_submit_handoff(route_match=True, date_match=False, context_match=False)
    handoff.update(_classification_evidence(route=("北京", "上海"), observed_date="2026-09-07", route_match=True, date_match=False))

    payload = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), handoff)

    assert payload["diag_u7_c0_c8"]["c7_settled_result_classification"]["classification"]["classification"] == "PARTIAL_QUERY"
    assert payload["q5_result_context"]["context_match"] is False


def test_ru8_11_stale_query_remains_q5_fail() -> None:
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False)
    handoff.update(_classification_evidence(route=("广州", "深圳"), observed_date="2026-01-08", route_match=False, date_match=False))

    payload = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), handoff)

    assert payload["diag_u7_c0_c8"]["c7_settled_result_classification"]["classification"]["classification"] == "STALE_QUERY"
    assert payload["q5_result_context"]["context_match"] is False


def test_ru8_12_requested_result_context_selection_remains_deterministic() -> None:
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((correct,)) == correct


def test_ru8_13_context_replacement_successor_tracking_remains_available() -> None:
    source = _result_context_candidate(
        index=0,
        url="https://www.fliggy.com/?tab=flight",
        title="飞猪",
        identity=FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH,
        is_current=True,
        result_surface=False,
    )
    successor = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )

    assert choose_result_context_candidate((source, successor)) == successor


def test_ru8_14_requested_identity_is_comparison_only() -> None:
    source = _fliggy_source_text()

    assert "_q0_requested_query" in source
    assert "write_verified_source_query" not in source
    assert "route_url = " not in source


def test_ru8_15_existing_d_p_h_c_diagnostics_remain_available() -> None:
    payload = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff())

    assert "diag_u4_p0_p7" in payload
    assert "diag_u6_h0_h8" in payload
    assert "diag_u7_c0_c8" in payload


def test_ru8_16_existing_2t_and_retries_zero_are_preserved() -> None:
    handoff = _post_submit_handoff()
    handoff["result_state_base_window_ms"] = 5000
    handoff["result_state_max_observation_ms"] = 10000
    handoff["result_state_extension_used"] = True

    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h5_initialization_transitions"]["base_window_ms"] == 5000
    assert payload["h5_initialization_transitions"]["max_observation_ms"] == 10000
    assert payload["h5_initialization_transitions"]["retries"] == 0


def test_ru8_17_overlay_presence_does_not_trigger_improvised_interaction() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["search_form_readiness"] = {"overlay_evidence": ["location-permission:1"]}
    payload = _diag_u6_h0_h8(diagnostics, _post_submit_handoff())

    assert payload["public_overlay_evidence"]["permission_prompt_presence"] is True
    assert payload["public_overlay_evidence"]["blocking_evidence"] == "not_proven"


def test_ru8_18_no_forbidden_state_or_url_workaround() -> None:
    source = _fliggy_source_text()

    assert "localStorage" not in source
    assert "sessionStorage" not in source
    assert "document.cookie" not in source
    assert "depCityName=北京&arrCityName=上海&depDate=2026-09-14" not in source


def test_ru8_19_sanitization_prevents_sensitive_artifacts() -> None:
    sanitized = sanitize_probe_payload({"token": "abc", "session_id": "xyz", "visible": "北京"})

    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["session_id"] == "[REDACTED]"
    assert sanitized["visible"] == "北京"


def test_ru8_20_l1_l2_and_shared_contracts_are_untouched() -> None:
    changed_files = {path.replace("\\", "/") for path in _tracked_diff_names()}

    assert all("level2" not in path for path in changed_files)
    assert not any(path.startswith("apps/backend/src/flight_agent/domain/") for path in changed_files)
    assert not any(path.startswith("apps/backend/src/flight_agent/adapters/flight_providers/fliggy/mapper") for path in changed_files)


def test_ru8_21_default_signature_remains_run_local_date_derived() -> None:
    assert _fliggy_default_query_signature(date(2026, 9, 6))["departure_date"] == "2026-09-07"
    assert _fliggy_default_query_signature(date(2026, 9, 13))["departure_date"] == "2026-09-14"


def test_ru8_22_unestablished_public_commit_returns_blocker_not_false_pass() -> None:
    classification = _public_commit_state_classification(
        {
            "destination_commitment": {"commitment_status": "insufficient"},
            "date_commitment": {"commitment_status": "confirmed"},
        },
        _query_state().to_dict(),
        _verify_pre_submit_query_state(_query_state()).to_dict(),
    )

    assert classification is PublicQueryClassification.PARTIAL_QUERY


def test_hd6_01_h1_source_public_state_matches_q1_at_submit_boundary() -> None:
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), _post_submit_handoff())

    assert payload["h0_verified_source_query"]["verified"] is True
    assert payload["h1_source_public_state"]["matches_h0"] is True
    assert _diag_u6_root_cause_class(payload) == "INCONCLUSIVE"


def test_hd6_02_source_state_reset_on_submit_is_classified() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["pre_submit_query_state"] = _query_state(form_destination="杭州").to_dict()
    diagnostics["pre_submit_query_verification"] = {"query_state_decision": "match"}
    payload = _diag_u6_h0_h8(diagnostics, _post_submit_handoff())

    assert payload["h2_submit_trigger"]["source_state_changed_at_submit"] is True
    assert _diag_u6_root_cause_class(payload) == "SOURCE_STATE_RESET_ON_SUBMIT"


def test_hd6_03_new_context_initial_state_already_stale_is_classified() -> None:
    sample = _stale_default_sample(attempt=1)
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both")
    handoff["result_state_samples"] = [sample]
    handoff["result_state_failure_taxonomy"] = "RESULT_STATE_STALE_OR_DEFAULT"
    handoff["stale_or_default_result_stabilized"] = True
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h6_stale_default_introduction"]["first_stale_default"]["attempt"] == 1
    assert _diag_u6_root_cause_class(payload) == "NEW_CONTEXT_INITIALIZED_STALE"


def test_hd6_04_loading_then_stale_default_is_classified_as_restoration() -> None:
    loading = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="航班查询",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    loading.search_plan_evidence["date_match"] = "insufficient"
    first = _result_state_sample(
        attempt=1,
        window="base",
        candidates=(loading,),
        selected=None,
        failure_taxonomy="RESULT_STATE_QUERY_UNREADABLE",
    )
    second = _stale_default_sample(attempt=2)
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both")
    handoff["result_state_samples"] = [first, second]
    handoff["result_state_failure_taxonomy"] = "RESULT_STATE_STALE_OR_DEFAULT"
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h6_stale_default_introduction"]["first_stale_default"]["attempt"] == 2
    assert _diag_u6_root_cause_class(payload) == "STALE_DEFAULT_RESTORED_DURING_INITIALIZATION"


def test_hd6_05_correct_context_exists_but_not_selected_is_classified() -> None:
    correct = _result_context_candidate(
        index=1,
        url="https://sjipiao.fliggy.com/homeow/trip_flight_search.htm",
        title="北京到上海机票预订",
        identity=FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE,
        is_current=False,
    )
    sample = _result_state_sample(attempt=1, window="base", candidates=(correct,), selected=None, failure_taxonomy=None)
    handoff = _post_submit_handoff(route_match=True, date_match=True, context_match=False, mismatch_dimension="none")
    handoff["result_state_samples"] = [sample]
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h6_stale_default_introduction"]["first_correct_identity"]["attempt"] == 1
    assert _diag_u6_root_cause_class(payload) == "CORRECT_CONTEXT_EXISTS_BUT_NOT_SELECTED"


def test_hd6_06_correct_intermediate_context_replaced_stays_lifecycle_gap() -> None:
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


def test_hd6_07_reader_observes_stale_context_while_correct_identity_exists() -> None:
    correct_sample = _stale_default_sample(attempt=1)
    correct_sample["route_match"] = True
    correct_sample["date_match"] = True
    correct_sample["result_surface_present"] = True
    handoff = _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both")
    handoff["result_state_samples"] = [correct_sample]
    handoff["result_state_failure_taxonomy"] = "RESULT_STATE_STALE_OR_DEFAULT"
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert _diag_u6_root_cause_class(payload) == "RESULT_STATE_READER_OBSERVES_WRONG_CONTEXT"


def test_hd6_08_multiple_equally_plausible_contexts_remain_ambiguous_no_guess() -> None:
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


def test_hd6_09_modal_presence_alone_does_not_classify_blocking() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["search_form_readiness"] = {"overlay_evidence": ["modal:1"]}
    payload = _diag_u6_h0_h8(diagnostics, _post_submit_handoff())

    assert payload["public_overlay_evidence"]["modal_presence"] is True
    assert payload["public_overlay_evidence"]["blocking_evidence"] == "not_proven"
    assert _diag_u6_root_cause_class(payload) == "INCONCLUSIVE"


def test_hd6_10_modal_demonstrably_preventing_initialization_has_root_vocabulary() -> None:
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), _post_submit_handoff())
    payload["public_overlay_evidence"]["blocking_evidence"] = "modal"

    assert _diag_u6_root_cause_class(payload) == "MODAL_BLOCKS_QUERY_INITIALIZATION"


def test_hd6_11_permission_prompt_presence_alone_does_not_classify_blocking() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["search_form_readiness"] = {"overlay_evidence": ["location-permission:1"]}
    payload = _diag_u6_h0_h8(diagnostics, _post_submit_handoff())

    assert payload["public_overlay_evidence"]["permission_prompt_presence"] is True
    assert payload["public_overlay_evidence"]["blocking_evidence"] == "not_proven"
    assert _diag_u6_root_cause_class(payload) == "INCONCLUSIVE"


def test_hd6_12_permission_prompt_demonstrably_blocking_has_root_vocabulary() -> None:
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), _post_submit_handoff())
    payload["public_overlay_evidence"]["blocking_evidence"] = "permission"

    assert _diag_u6_root_cause_class(payload) == "PERMISSION_PROMPT_BLOCKS_QUERY_INITIALIZATION"


def test_hd6_13_20260108_stale_default_never_passes_requested_20260914() -> None:
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

    assert choose_result_context_candidate((stale,)) is None
    assert _result_state_failure_taxonomy((stale,), None) == "RESULT_STATE_STALE_OR_DEFAULT"


def test_hd6_14_strict_q1_and_q5_remain_unchanged() -> None:
    assert _verify_pre_submit_query_state(_query_state()).submit_allowed is True
    mismatch = _build_post_submit_query_state_diagnostics(
        _post_submit_base_diagnostics(),
        _post_submit_handoff(route_match=False, date_match=False, context_match=False, mismatch_dimension="both"),
    )

    assert mismatch["q5_result_context"]["context_match"] is False
    assert mismatch["q5_result_context"]["query_identity_decision"] == "match"


def test_hd6_15_diag_u6_does_not_use_private_network_or_session_evidence() -> None:
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), _post_submit_handoff())
    rendered = str(payload).lower()

    assert "cookie" not in rendered
    assert "authorization" not in rendered
    assert "localstorage" not in rendered
    assert "har" not in rendered
    assert "signature" not in rendered


def test_hd6_16_diag_u6_preserves_2t_and_retries_zero() -> None:
    handoff = _post_submit_handoff()
    handoff["result_state_base_window_ms"] = 5000
    handoff["result_state_max_observation_ms"] = 10000
    payload = _diag_u6_h0_h8(_post_submit_base_diagnostics(), handoff)

    assert payload["h5_initialization_transitions"]["base_window_ms"] == 5000
    assert payload["h5_initialization_transitions"]["max_observation_ms"] == 10000
    assert payload["h5_initialization_transitions"]["retries"] == 0


def test_hd6_17_p0_p7_and_d0_d9_diagnostics_are_preserved() -> None:
    diagnostics = _post_submit_base_diagnostics()
    diagnostics["destination_commitment"] = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(_destination_candidate("上海"),),
        suggestion_surface_present=True,
        selected_candidate=_destination_candidate("上海"),
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=None,
    ).to_dict()
    payload = _build_post_submit_query_state_diagnostics(diagnostics, _post_submit_handoff())

    assert "diag_u4_p0_p7" in payload
    assert diagnostics["destination_commitment"]["destination_stability_diagnostics"]["d9_pre_submit_stability"]["stable_readback"] == "上海"


def test_hd6_18_final_diag_u6_output_is_sanitized() -> None:
    handoff = _post_submit_handoff(observed_date_text="2026-01-08 Cookie: a=b", date_match=False, context_match=False)
    payload = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), handoff)
    sanitized = sanitize_probe_payload({"payload": payload})["payload"]

    assert sanitized["diag_u6_h0_h8"]["h8_strict_identity"]["context_match"] is False
    assert "Cookie: a=b" not in str(sanitized)


def test_di8_01_s0_s8_timestamps_order_and_labels_are_sanitized() -> None:
    snapshots = (
        DestinationSuggestionSnapshot(120, (DestinationSuggestionCandidate(".city", 0, "上海"),)),
        DestinationSuggestionSnapshot(420, (DestinationSuggestionCandidate(".city", 0, "Cookie: a=b"),)),
    )
    payload = _destination_suggestion_lifecycle_diagnostics(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        focused_elapsed_ms=0,
        typed_elapsed_ms=80,
        snapshots=snapshots,
        selected_candidate=None,
        selection_method="none",
        commit_readback="上海",
        commitment_status="insufficient",
        failure_taxonomy="DESTINATION_COMMIT_NOT_CONFIRMED",
        d9_stable_readback="上海",
        headed_pause_ms=0,
        overlay_evidence=(),
    )
    sanitized = sanitize_probe_payload(payload)

    assert [key.split("_", 1)[0] for key in payload if key.startswith("s")] == [f"s{i}" for i in range(9)]
    assert [item["elapsed_ms"] for item in payload["s3_surface_observation"]["observations"]] == [120, 420]
    assert "Cookie: a=b" not in str(sanitized)


def test_di8_02_no_surface_is_classified() -> None:
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(DestinationSuggestionSnapshot(0, ()), DestinationSuggestionSnapshot(300, ())),
        selected_candidate=None,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.NO_SURFACE


def test_di8_03_delayed_surface_is_classified() -> None:
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(
            DestinationSuggestionSnapshot(0, ()),
            DestinationSuggestionSnapshot(300, (_destination_candidate("北京"),)),
        ),
        selected_candidate=None,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.SURFACE_DELAYED


def test_di8_04_transient_surface_is_classified() -> None:
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(
            DestinationSuggestionSnapshot(0, (_destination_candidate("北京"),)),
            DestinationSuggestionSnapshot(300, ()),
        ),
        selected_candidate=None,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.SURFACE_TRANSIENT


def test_di8_05_ready_surface_without_requested_match_is_classified() -> None:
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(DestinationSuggestionSnapshot(0, (_destination_candidate("北京"),)),),
        selected_candidate=None,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.SURFACE_READY_NO_MATCH


def test_di8_06_visible_requested_match_without_selectability_is_classified() -> None:
    candidate = DestinationSuggestionCandidate(".city", 0, "上海", selectable=False)
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(DestinationSuggestionSnapshot(0, (candidate,)),),
        selected_candidate=candidate,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.MATCH_VISIBLE_NOT_SELECTABLE


def test_di8_07_legitimate_selection_opportunity_is_classified() -> None:
    candidate = DestinationSuggestionCandidate(".city", 0, "上海", selectable=True)
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(DestinationSuggestionSnapshot(0, (candidate,)),),
        selected_candidate=candidate,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.COMMITTABLE_MATCH_FOUND


def test_di8_08_existing_valid_commit_evidence_is_classified() -> None:
    candidate = DestinationSuggestionCandidate(".city", 0, "上海", selectable=True)
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(DestinationSuggestionSnapshot(0, (candidate,)),),
        selected_candidate=candidate,
        commitment_status="confirmed",
    ) is DestinationSuggestionSurfaceClassification.COMMIT_EVIDENCE_OBTAINED


def test_di8_09_headed_headless_difference_is_mode_dependent() -> None:
    def result(headless: bool, classification: str) -> dict[str, object]:
        return {
            "diagnostics": {
                "headless": headless,
                "destination_commitment": {
                    "destination_suggestion_diagnostics": {"classification": classification}
                },
            }
        }

    assert classify_destination_suggestion_mode(
        (
            result(False, "COMMITTABLE_MATCH_FOUND"),
            result(True, "NO_SURFACE"),
        )
    ) is DestinationSuggestionSurfaceClassification.MODE_DEPENDENT_SURFACE


def test_di8_10_missing_observations_remain_unknown() -> None:
    assert _classify_destination_suggestion_surface(
        requested_destination="上海",
        snapshots=(),
        selected_candidate=None,
        commitment_status="insufficient",
    ) is DestinationSuggestionSurfaceClassification.UNKNOWN_SURFACE


def test_di8_11_d9_q1_and_u8_commit_gate_are_preserved() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination="上海",
        candidates=(),
        suggestion_surface_present=False,
        selected_candidate=None,
        selection_method="none",
        commit_readback="上海",
        failure_taxonomy="DESTINATION_SUGGESTION_NOT_READY",
        readback_sequence=("上海", "上海"),
        suggestion_snapshots=(DestinationSuggestionSnapshot(0, ()),),
    ).to_dict()
    diagnostics = {
        "destination_commitment": commitment,
        "date_commitment": {"commitment_status": "confirmed"},
        "submit_allowed": False,
        "submit_executed": False,
    }
    _annotate_destination_s8(diagnostics, _verify_pre_submit_query_state(_query_state()))
    s8 = commitment["destination_suggestion_diagnostics"]["s8_pre_submit_gate"]

    assert commitment["destination_stability_diagnostics"]["d9_pre_submit_stability"]["stable_readback"] == "上海"
    assert s8["q1_route_match"] is True
    assert s8["q1_date_match"] is True
    assert s8["destination_commit_gate"] is False


def test_di8_12_submit_stays_blocked_without_destination_commit_evidence(monkeypatch) -> None:
    async def fake_write(page: object, probe_input: ProbeInput) -> dict[str, object]:
        return {
            "destination_commitment": {"commitment_status": "insufficient"},
            "date_commitment": {"commitment_status": "confirmed"},
        }

    async def fake_capture(page: object, probe_input: ProbeInput) -> PublicSearchQueryState:
        return _query_state()

    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)
    allowed, diagnostics = asyncio.run(
        _submit_verified_public_flight_search(
            _FakeContext(),
            object(),
            ProbeInput("北京", "上海", date(2026, 9, 14)),
        )
    )

    assert allowed is False
    assert diagnostics["submit_executed"] is False


def test_di8_13_unicode_and_output_preflight_precedes_planned_provider_access() -> None:
    output_path = REPO_ROOT / "apps" / "backend" / ".tmp-pytest" / "diag-u8.json"
    payload = _live_observation_preflight(
        ProbeInput(
            "北京",
            "上海",
            date(2026, 9, 14),
            planned_observation=1,
            evidence_output_path=str(output_path),
        )
    )

    assert payload["unicode_safe"] is True
    assert payload["output_path_absolute"] is True
    assert payload["provider_access_started"] is False
    with pytest.raises(ValueError, match="absolute evidence output path"):
        _live_observation_preflight(
            ProbeInput("北京", "上海", date(2026, 9, 14), planned_observation=1)
        )
    with pytest.raises(ValueError, match="query identity preflight"):
        _live_observation_preflight(
            ProbeInput(
                "����",
                "�Ϻ�",
                date(2026, 9, 14),
                planned_observation=1,
                evidence_output_path=str(output_path),
            )
        )


def test_di8_14_planned_observation_is_not_a_retry_counter() -> None:
    payload = _live_observation_preflight(
        ProbeInput(
            "北京",
            "上海",
            date(2026, 9, 14),
            planned_observation=6,
            evidence_output_path=str(REPO_ROOT / "apps" / "backend" / ".tmp-pytest" / "observation.json"),
        )
    )

    assert payload["planned_observation"] == 6
    assert payload["retries"] == 0


def test_di8_15_headed_pause_is_local_only_and_adds_no_interaction() -> None:
    script = (REPO_ROOT / "scripts" / "ci" / "fliggy-browser-probe-smoke.ps1").read_text(encoding="utf-8")

    assert "HeadedObservationPauseSeconds" in script
    assert "--headed-observation-pause-seconds" in script
    assert '"--evidence-output-path", $ResolvedOutputPath' in script
    assert "keyboard.press" not in script.lower()
    assert "mouse.click" not in script.lower()


def test_di8_16_forbidden_private_state_is_not_added_and_payload_is_sanitized() -> None:
    source = _fliggy_source_text()
    sanitized = sanitize_probe_payload(
        {"candidate": "上海", "cookie": "a=b", "authorization": "Bearer secret"}
    )

    assert sanitized == {
        "candidate": "上海",
        "cookie": "[REDACTED]",
        "authorization": "[REDACTED]",
    }
    assert "document.cookie" not in source
    assert "localStorage" not in source
    assert "sessionStorage" not in source


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


def _requested_query() -> dict[str, str]:
    return {"origin": "北京", "destination": "上海", "departure_date": "2026-09-14"}


def _default_signature() -> dict[str, str]:
    return {"origin": "北京", "destination": "杭州", "departure_date": "2026-09-07"}


def _classification_evidence(
    *,
    route: tuple[str, str] | None,
    observed_date: str | None,
    route_match: bool | str = "insufficient",
    date_match: bool | str = "insufficient",
) -> dict[str, object]:
    return {
        "observed_route_origin": route[0] if route is not None else None,
        "observed_route_destination": route[1] if route is not None else None,
        "observed_route_text": f"{route[0]}到{route[1]}" if route is not None else None,
        "normalized_observed_date": observed_date,
        "route_match": route_match,
        "date_match": date_match,
    }


def _fliggy_source_text() -> str:
    return (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "adapters" / "flight_providers" / "fliggy" / "browser_probe.py"
    ).read_text(encoding="utf-8")


class _FakeContext:
    def __init__(self) -> None:
        self.pages = [object()]


def _tracked_diff_names() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def _destination_candidate(label: str, *, selector: str = ".next-overlay-wrapper li", index: int = 0) -> DestinationSuggestionCandidate:
    return DestinationSuggestionCandidate(selector=selector, index=index, label=label)


def _post_submit_base_diagnostics(*, submit_executed: bool = True) -> dict[str, object]:
    return {
        "pre_submit_query_state": _query_state().to_dict(),
        "pre_submit_query_verification": _verify_pre_submit_query_state(_query_state()).to_dict(),
        "destination_commitment": {"commitment_status": "confirmed", "failure_taxonomy": None},
        "date_commitment": {
            "commitment_status": "confirmed",
            "failure_taxonomy": None,
            "date_match": True,
            "selection_method": "keyboard_enter_after_public_date_fill",
        },
        "submit_executed": submit_executed,
    }


def _confirmed_public_write_diagnostics() -> dict[str, object]:
    return {
        "destination_commitment": {"commitment_status": "confirmed", "failure_taxonomy": None},
        "date_commitment": {
            "commitment_status": "confirmed",
            "failure_taxonomy": None,
            "date_match": True,
            "selection_method": "keyboard_enter_after_public_date_fill",
        },
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


def _stale_default_sample(*, attempt: int) -> dict[str, object]:
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
    return _result_state_sample(
        attempt=attempt,
        window="base",
        candidates=(stale,),
        selected=None,
        failure_taxonomy="RESULT_STATE_STALE_OR_DEFAULT",
    )


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


def test_ru9_01_public_arrival_control_opens_selector_and_records_surface(monkeypatch) -> None:
    class FakeLocator:
        def __init__(self, page: FakePage, selector: str) -> None:
            self.page = page
            self.selector = selector

        def nth(self, index: int) -> FakeLocator:
            assert index == 0
            return self

        async def count(self) -> int:
            return 1

        async def is_visible(self) -> bool:
            return True

        async def is_enabled(self) -> bool:
            return True

        async def click(self) -> None:
            self.page.clicks.append(self.selector)

    class FakePage:
        def __init__(self) -> None:
            self.clicks: list[str] = []

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(self, selector)

        async def wait_for_timeout(self, milliseconds: int) -> None:
            assert milliseconds >= 0

    candidate = DestinationSuggestionCandidate(".city", 0, "上海", selectable=True)

    async def fake_wait(*args, **kwargs):
        return (candidate,), (DestinationSuggestionSnapshot(1, (candidate,)),)

    readbacks = iter(("杭州", "上海"))

    async def fake_read(*args, **kwargs):
        return next(readbacks)

    async def fake_stability(*args, **kwargs):
        return {"readback_sequence": ["上海", "上海"], "extension_used": False, "extension_reason": "none"}

    async def fake_overlay(*args, **kwargs):
        return ()

    monkeypatch.setattr(fliggy_browser_probe, "_wait_for_destination_suggestion_candidates", fake_wait)
    monkeypatch.setattr(fliggy_browser_probe, "_read_control_text", fake_read)
    monkeypatch.setattr(fliggy_browser_probe, "_observe_destination_readback_stability", fake_stability)
    monkeypatch.setattr(fliggy_browser_probe, "_overlay_evidence", fake_overlay)
    page = FakePage()
    result = asyncio.run(_commit_public_destination(page, "上海"))
    assert page.clicks == [".rc-flight-searchbar input#form_arrCity", ".city"]
    assert result.commitment_status == "confirmed"
    assert result.destination_suggestion_diagnostics["u9_public_city_selector"]["selector_opened"] is True


def test_ru9_02_unique_shanghai_candidate_resolves() -> None:
    result = _resolve_public_destination_city_candidate((_destination_candidate("北京"), _destination_candidate("上海", index=1)), "上海", selector_surface_present=True)
    assert result.selected_candidate is not None and result.selected_candidate.label == "上海"


def test_ru9_03_candidate_order_does_not_control_semantic_resolution() -> None:
    first = _resolve_public_destination_city_candidate((_destination_candidate("上海"), _destination_candidate("杭州", index=1)), "上海", selector_surface_present=True)
    second = _resolve_public_destination_city_candidate((_destination_candidate("杭州"), _destination_candidate("上海", index=1)), "上海", selector_surface_present=True)
    assert first.selected_candidate is not None and second.selected_candidate is not None
    assert first.selected_candidate.label == second.selected_candidate.label == "上海"


def test_ru9_04_missing_target_blocks_without_selection() -> None:
    result = _resolve_public_destination_city_candidate((_destination_candidate("杭州"),), "上海", selector_surface_present=True)
    assert result.selected_candidate is None and result.failure_taxonomy == "DESTINATION_CITY_NOT_FOUND"


def test_ru9_05_ambiguous_target_blocks_without_guessing() -> None:
    result = _resolve_public_destination_city_candidate((_destination_candidate("上海虹桥"), _destination_candidate("上海浦东", index=1)), "上海", selector_surface_present=True)
    assert result.selected_candidate is None and result.failure_taxonomy == "DESTINATION_CITY_SELECTOR_AMBIGUOUS"


def test_ru9_06_absent_selector_surface_has_explicit_failure() -> None:
    result = _resolve_public_destination_city_candidate((), "上海", selector_surface_present=False)
    assert result.failure_taxonomy == "DESTINATION_CITY_SELECTOR_NOT_READY"


def test_ru9_07_wrong_post_selection_readback_fails_commit() -> None:
    result = _destination_commitment_result(requested_destination="上海", destination_control_ready=True, typed_destination=None, candidates=(_destination_candidate("上海"),), suggestion_surface_present=True, selected_candidate=_destination_candidate("上海"), selection_method="click", commit_readback="杭州", failure_taxonomy=None, city_selector_opened=True)
    assert result.commitment_status == "mismatch" and result.failure_taxonomy == "FORM_DESTINATION_MISMATCH"


def test_ru9_08_stable_shanghai_readback_confirms_d9_commit() -> None:
    result = _destination_commitment_result(requested_destination="上海", destination_control_ready=True, typed_destination=None, candidates=(_destination_candidate("上海"),), suggestion_surface_present=True, selected_candidate=_destination_candidate("上海"), selection_method="click", commit_readback="上海", failure_taxonomy=None, readback_sequence=("上海", "上海"), city_selector_opened=True)
    assert result.commitment_status == "confirmed"
    assert result.destination_stability_diagnostics["d9_pre_submit_stability"]["stable_readback"] == "上海"


def test_ru9_09_input_triggered_suggestion_is_not_required() -> None:
    body = _fliggy_source_text().split("async def _commit_public_destination", 1)[1].split("async def _write_destination_input_text", 1)[0]
    assert "_write_destination_input_text" not in body and "field.click()" in body


def test_ru9_10_visible_text_without_public_selection_is_insufficient() -> None:
    assert _destination_commitment_status("上海", "上海", action_performed=False, failure_taxonomy=None) == "insufficient"


def test_ru9_11_existing_date_commit_contract_is_preserved() -> None:
    assert _public_date_commitment(requested_date="2026-09-14", typed_date="2026-09-14", commit_readback="2026-09-14", action_performed=True)["commitment_status"] == "confirmed"


def test_ru9_12_submit_waits_for_destination_date_and_q1(monkeypatch) -> None:
    class FakePage:
        def locator(self, selector: str):
            raise AssertionError(f"submit must remain blocked: {selector}")

    async def fake_write(page, probe_input):
        return {"destination_commitment": {"commitment_status": "insufficient"}, "date_commitment": {"commitment_status": "confirmed"}}

    async def fake_capture(page, probe_input):
        return _query_state()

    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)
    allowed, diagnostics = asyncio.run(_submit_verified_public_flight_search(_FakeContext(), FakePage(), ProbeInput("北京", "上海", date(2026, 9, 14))))
    assert allowed is False and diagnostics["submit_executed"] is False


def test_ru9_13_public_submit_is_clicked_exactly_once(monkeypatch) -> None:
    class FakeButton:
        def __init__(self, page) -> None:
            self.page = page

        def nth(self, index: int):
            assert index == 0
            return self

        async def click(self) -> None:
            self.page.clicks += 1

    class FakePage:
        def __init__(self) -> None:
            self.clicks = 0

        def locator(self, selector: str):
            assert selector == ".rc-flight-searchbar button.search-button"
            return FakeButton(self)

    async def fake_write(page, probe_input):
        return _confirmed_public_write_diagnostics()

    async def fake_capture(page, probe_input):
        return _query_state()

    monkeypatch.setattr(fliggy_browser_probe, "_write_public_flight_search_fields", fake_write)
    monkeypatch.setattr(fliggy_browser_probe, "_capture_public_search_query_state", fake_capture)
    page = FakePage()
    allowed, _ = asyncio.run(_submit_verified_public_flight_search(_FakeContext(), page, ProbeInput("北京", "上海", date(2026, 9, 14))))
    assert allowed is True and page.clicks == 1


def test_ru9_14_correct_route_and_date_preserve_q5_pass() -> None:
    assert _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff())["q5_result_context"]["context_match"] is True


def test_ru9_15_default_hangzhou_result_preserves_q5_failure() -> None:
    q5 = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff(route_match=False, context_match=False, mismatch_dimension="route"))["q5_result_context"]
    assert q5["context_match"] is False and q5["route_match"] is False


def test_ru9_16_stale_date_preserves_q5_failure() -> None:
    q5 = _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff(date_match=False, context_match=False, mismatch_dimension="date", observed_date_text="2026-01-08"))["q5_result_context"]
    assert q5["context_match"] is False and q5["date_match"] is False


def test_ru9_17_destination_repair_does_not_construct_result_url() -> None:
    body = _fliggy_source_text().split("async def _commit_public_destination", 1)[1].split("async def _write_destination_input_text", 1)[0].lower()
    assert "url" not in body and "goto(" not in body


def test_ru9_18_non_shanghai_city_uses_same_generic_resolution() -> None:
    result = _resolve_public_destination_city_candidate((_destination_candidate("深圳"), _destination_candidate("广州", index=1)), "广州", selector_surface_present=True)
    assert result.selected_candidate is not None and result.selected_candidate.label == "广州"


def test_ru9_19_city_selector_diagnostics_are_sanitized() -> None:
    result = _destination_commitment_result(requested_destination="上海", destination_control_ready=True, typed_destination=None, candidates=(_destination_candidate("上海 Cookie: a=b"),), suggestion_surface_present=True, selected_candidate=None, selection_method="none", commit_readback="杭州", failure_taxonomy="DESTINATION_CITY_NOT_FOUND", city_selector_opened=True)
    assert "Cookie: a=b" not in str(sanitize_probe_payload(result.to_dict()))


def test_ru9_20_existing_diagnostics_contracts_remain_present() -> None:
    result = _destination_commitment_result(requested_destination="上海", destination_control_ready=True, typed_destination=None, candidates=(_destination_candidate("上海"),), suggestion_surface_present=True, selected_candidate=_destination_candidate("上海"), selection_method="click", commit_readback="上海", failure_taxonomy=None, city_selector_opened=True).to_dict()
    stability = result["destination_stability_diagnostics"]
    lifecycle = result["destination_suggestion_diagnostics"]
    assert all(any(key.startswith(f"d{i}_") for key in stability) for i in range(10))
    assert all(any(key.startswith(f"s{i}_") for key in lifecycle) for i in range(9))


def test_ru9_21_recovery_diff_stays_provider_local() -> None:
    assert set(_tracked_diff_names()) <= {"apps/backend/src/flight_agent/adapters/flight_providers/fliggy/browser_probe.py", "tests/backend/unit/test_m9_fliggy_browser_probe.py", "scripts/ci/fliggy-browser-probe-smoke.ps1"}


def test_ru9_22_unicode_live_preflight_does_not_change_query_semantics() -> None:
    output_path = str((REPO_ROOT / ".u9-local-evidence.json").resolve())
    query = ProbeInput("北京", "上海", date(2026, 9, 14), planned_observation=1, evidence_output_path=output_path)
    preflight = _live_observation_preflight(query)
    assert preflight["unicode_safe"] is True
    assert (preflight["origin"], preflight["destination"], preflight["departure_date"]) == ("北京", "上海", "2026-09-14")


def test_du10_01_single_correct_visible_destination_target_is_bound() -> None:
    binding = _bind_public_destination_target(({"index": 0, "visible": True, "current_locator_match": True},))
    assert binding == {"status": "BOUND", "bound_index": 0, "plausible_count": 1, "exact_match_count": 1}


def test_du10_02_multiple_plausible_targets_are_ambiguous() -> None:
    binding = _bind_public_destination_target(
        (
            {"index": 0, "visible": True, "current_locator_match": True},
            {"index": 1, "visible": True, "current_locator_match": True},
        )
    )
    assert binding["status"] == "TARGET_AMBIGUOUS" and binding["bound_index"] is None


def test_du10_03_hidden_or_wrong_target_is_not_human_equivalent() -> None:
    hidden = _bind_public_destination_target(({"index": 0, "visible": False, "current_locator_match": True},))
    wrong = _bind_public_destination_target(({"index": 0, "visible": True, "current_locator_match": False},))
    assert hidden["status"] == wrong["status"] == "TARGET_MISMATCH"


def test_du10_04_target_readiness_requires_all_public_signals() -> None:
    classification = _classify_public_destination_activation(
        binding_status="BOUND", target_ready=False, target_replaced=False, interaction_completed=False, samples=()
    )
    assert classification is PublicDestinationActivationClass.TARGET_MISMATCH


def test_du10_05_replaced_target_is_classified_before_activation() -> None:
    classification = _classify_public_destination_activation(
        binding_status="BOUND", target_ready=True, target_replaced=True, interaction_completed=True, samples=()
    )
    assert classification is PublicDestinationActivationClass.TARGET_REPLACED


def test_du10_06_completed_click_without_selector_is_classified() -> None:
    classification = _classify_public_destination_activation(
        binding_status="BOUND",
        target_ready=True,
        target_replaced=False,
        interaction_completed=True,
        samples=({"surface_present": False},),
    )
    assert classification is PublicDestinationActivationClass.CLICK_NO_ACTIVATION


def test_du10_07_immediate_selector_activation_is_opened() -> None:
    classification = _classify_public_destination_activation(
        binding_status="BOUND",
        target_ready=True,
        target_replaced=False,
        interaction_completed=True,
        samples=({"surface_present": True},),
    )
    assert classification is PublicDestinationActivationClass.OPENED


def test_du10_08_bounded_later_selector_activation_is_delayed() -> None:
    classification = _classify_public_destination_activation(
        binding_status="BOUND",
        target_ready=True,
        target_replaced=False,
        interaction_completed=True,
        samples=({"surface_present": False}, {"surface_present": True}),
    )
    assert classification is PublicDestinationActivationClass.DELAYED_ACTIVATION
    assert _public_destination_activation_root_class(classification, "current_click") == "PUBLIC_SELECTOR_ACTIVATION_DELAY_LOCALIZED"


def test_du10_09_focus_evidence_is_public_and_read_only() -> None:
    source = _fliggy_source_text()
    assert "node === document.activeElement" in source
    assert "private provider" not in source.lower()


def test_du10_10_target_and_candidate_labels_are_sanitized() -> None:
    payload = {"a0_target_inventory": {"targets": [{"visible_label": "杭州 Cookie: a=b"}]}}
    assert "Cookie: a=b" not in str(sanitize_probe_payload(payload))


def test_du10_11_diagnostic_probe_does_not_replace_default_u9_path() -> None:
    source = _fliggy_source_text()
    signature = source.split("async def _commit_public_destination", 1)[1].split(") -> DestinationCommitmentResult", 1)[0]
    assert "diagnostic_activation_probe: str | None = None" in signature


def test_du10_12_no_forced_javascript_event_or_dom_mutation_helper() -> None:
    body = _fliggy_source_text().split("async def _diagnose_public_destination_activation", 1)[1].split("async def _write_destination_input_text", 1)[0]
    assert "dispatchEvent" not in body
    assert ".evaluate(\"node => node.click" not in body
    assert "style." not in body and "classList" not in body


def test_du10_13_one_interaction_attempt_has_no_click_retry_loop() -> None:
    body = _fliggy_source_text().split("async def _diagnose_public_destination_activation", 1)[1].split("async def _write_destination_input_text", 1)[0]
    assert body.count("await target.click()") == 1
    assert body.count("await page.mouse.down()") == 1
    assert body.count("await page.mouse.up()") == 1


def test_du10_14_human_visual_metadata_is_not_semantic_truth() -> None:
    body = _fliggy_source_text().split("async def _diagnose_public_destination_activation", 1)[1].split("async def _write_destination_input_text", 1)[0]
    assert '"screenshot_supplied": False' in body
    assert '"human_observation_is_semantic_truth": False' in body


def test_du10_15_headed_headless_difference_is_explicit() -> None:
    headed = {"diagnostics": {"headless": False, "destination_commitment": {"destination_activation_diagnostics": {"a10_root_class": {"root_class": "PUBLIC_INTERACTION_SEMANTICS_LOCALIZED"}}}}}
    headless = {"diagnostics": {"headless": True, "destination_commitment": {"destination_activation_diagnostics": {"a10_root_class": {"root_class": "DIAGNOSTIC_INSUFFICIENT"}}}}}
    assert classify_destination_activation_mode((headed, headless)) == "MODE_DEPENDENT_SELECTOR_ACTIVATION_LOCALIZED"


def test_du10_16_existing_u9_destination_commit_gate_is_unchanged() -> None:
    body = _fliggy_source_text().split("async def _commit_public_destination", 1)[1].split("def _bind_public_destination_target", 1)[0]
    assert "await field.click()" in body
    assert "await page.locator(resolution.selected_candidate.selector).nth(resolution.selected_candidate.index).click()" in body


def test_du10_17_date_d0_d9_q1_q5_contracts_are_preserved() -> None:
    commitment = _destination_commitment_result(
        requested_destination="上海",
        destination_control_ready=True,
        typed_destination=None,
        candidates=(_destination_candidate("上海"),),
        suggestion_surface_present=True,
        selected_candidate=_destination_candidate("上海"),
        selection_method="click",
        commit_readback="上海",
        failure_taxonomy=None,
    ).to_dict()
    assert all(any(key.startswith(f"d{i}_") for key in commitment["destination_stability_diagnostics"]) for i in range(10))
    assert _public_date_commitment(requested_date="2026-09-14", typed_date="2026-09-14", commit_readback="2026-09-14", action_performed=True)["commitment_status"] == "confirmed"
    assert _verify_pre_submit_query_state(_query_state()).submit_allowed is True
    assert _build_post_submit_query_state_diagnostics(_post_submit_base_diagnostics(), _post_submit_handoff())["q5_result_context"]["context_match"] is True


def test_du10_18_p_h_c_diagnostic_builders_are_preserved() -> None:
    source = _fliggy_source_text()
    assert all(name in source for name in ("_diag_u4_p0_p7", "_diag_u6_h0_h8", "_diag_u7_root_cause_class", "_public_commit_state_classification"))


def test_du10_19_diff_scope_excludes_l1_l2_and_shared_contracts() -> None:
    assert set(_tracked_diff_names()) <= {
        "apps/backend/src/flight_agent/adapters/flight_providers/fliggy/browser_probe.py",
        "tests/backend/unit/test_m9_fliggy_browser_probe.py",
        "scripts/ci/fliggy-browser-probe-smoke.ps1",
    }


def test_du10_20_unicode_preflight_failure_aborts_locally() -> None:
    query = ProbeInput("\ud800", "上海", date(2026, 9, 14))
    with pytest.raises(UnicodeError):
        _live_observation_preflight(query)


def _hit_regions() -> tuple[dict[str, object], ...]:
    return _assign_public_hit_region_ids(
        (
            {
                "kind": "ancestor",
                "depth": 1,
                "tag": "div",
                "id": "form_arrCity",
                "class": "arrival-wrapper",
                "text": "杭州",
                "rect": {"x": 10.0, "y": 10.0, "width": 140.0, "height": 40.0},
            },
            {
                "kind": "input",
                "depth": 0,
                "tag": "input",
                "id": "form_arrCity",
                "class": "arrival-input",
                "text": "杭州",
                "rect": {"x": 40.0, "y": 10.0, "width": 90.0, "height": 40.0},
            },
            {
                "kind": "overlap",
                "depth": 0,
                "tag": "label",
                "id": "",
                "class": "arrival-label",
                "text": "到达城市",
                "rect": {"x": 10.0, "y": 10.0, "width": 30.0, "height": 40.0},
            },
        )
    )


def test_dr1_01_nearby_public_inventory_is_deterministic_and_sanitized() -> None:
    first = _hit_regions()
    second = _hit_regions()
    assert first == second
    assert [item["region_id"] for item in first] == ["REGION-00", "REGION-01", "REGION-02"]
    assert "Cookie:" not in str(sanitize_probe_payload(first))


def test_dr1_02_input_and_wrapper_geometry_relationships_are_recorded() -> None:
    relationships = _public_hit_region_relationships(_hit_regions())
    input_relation, wrapper_relation = relationships[:2]
    assert input_relation["contains_input"] is True
    assert wrapper_relation["contains_input"] is True
    assert wrapper_relation["input_overlap_ratio"] == 1.0


def test_dr1_03_point_inside_input_classifies_same_geometric_region() -> None:
    assert (
        _classify_human_hit_differential(
            _hit_regions(), point=(80.0, 30.0), hit_matches_input=True
        )
        == "SAME_GEOMETRIC_INPUT_REGION"
    )


def test_dr1_04_point_outside_input_inside_wrapper_is_classified() -> None:
    assert (
        _classify_human_hit_differential(
            _hit_regions(), point=(20.0, 30.0), hit_matches_input=False
        )
        == "HUMAN_POINT_OUTSIDE_INPUT"
    )


def test_dr1_05_standard_hit_test_uses_only_public_dom_summary() -> None:
    source = _fliggy_source_text().split("async def _public_element_summary", 1)[1]
    source = source.split("async def _diagnose_public_destination_hit_target", 1)[0]
    assert "document.elementFromPoint" in source
    assert all(field in source for field in ("tag", "role", "id", "class", "text"))
    assert "getEventListeners" not in source and "__react" not in source.lower()


def test_dr1_06_different_hit_element_classifies_human_target_differs() -> None:
    assert (
        _classify_human_hit_differential(
            _hit_regions(), point=(160.0, 30.0), hit_matches_input=False
        )
        == "HUMAN_TARGET_DIFFERS"
    )


def test_dr1_07_missing_or_unresolved_hit_target_is_ambiguous() -> None:
    assert (
        _classify_human_hit_differential(_hit_regions(), point=None, hit_matches_input=None)
        == "HUMAN_TARGET_AMBIGUOUS"
    )


def test_dr1_08_highlighted_region_ids_map_to_inventory() -> None:
    regions = _hit_regions()
    svg = _annotated_region_svg(
        b"png",
        clip={"x": 0.0, "y": 0.0, "width": 180.0, "height": 70.0},
        regions=regions,
    )
    assert all(str(item["region_id"]) in svg for item in regions)


def test_dr1_09_human_annotation_requires_explicit_local_diagnostic_mode() -> None:
    output_path = str((REPO_ROOT / ".u10-r1-local-evidence.json").resolve())
    query = ProbeInput(
        "北京",
        "上海",
        date(2026, 9, 14),
        headless=False,
        planned_observation=2,
        evidence_output_path=output_path,
        destination_hit_target_probe="differential_click",
        human_hit_region_id="REGION-01",
    )
    assert query.human_hit_region_id == "REGION-01"
    with pytest.raises(ValueError):
        ProbeInput("北京", "上海", date(2026, 9, 14), human_hit_region_id="REGION-01")


def test_dr1_10_controlled_click_targets_only_resolved_hit_element() -> None:
    body = _fliggy_source_text().split("async def _diagnose_public_destination_hit_target", 1)[1]
    body = body.split("async def _write_destination_input_text", 1)[0]
    assert body.count("await region_target.click()") == 1
    assert "region_target_matches_input is False" in body
    assert "_public_hit_region_element(page, selected_region)" in body


def test_dr1_11_no_ancestor_cascade_or_repeated_click_loop() -> None:
    body = _fliggy_source_text().split("async def _diagnose_public_destination_hit_target", 1)[1]
    body = body.split("async def _write_destination_input_text", 1)[0]
    assert body.count(".click()") == 1
    assert "parentElement.click" not in body and "for ancestor" not in body


def test_dr1_12_wrapper_activation_localizes_root() -> None:
    assert (
        _public_hit_target_root_class("HUMAN_POINT_OUTSIDE_INPUT", selector_opened=True)
        == "PUBLIC_WRAPPER_ACTIVATION_LOCALIZED"
    )


def test_dr1_13_same_target_without_activation_is_classified() -> None:
    assert (
        _public_hit_target_root_class("SAME_GEOMETRIC_INPUT_REGION", selector_opened=False)
        == "SAME_PUBLIC_TARGET_NO_ACTIVATION"
    )


def test_dr1_14_default_u9_interaction_path_is_not_replaced() -> None:
    body = _fliggy_source_text().split("async def _commit_public_destination", 1)[1]
    body = body.split("def _bind_public_destination_target", 1)[0]
    assert "if diagnostic_hit_target_probe is not None" in body
    assert "await field.click()" in body
    assert "resolution.selected_candidate.selector" in body


def test_dr1_15_u9_destination_commit_gate_remains_unchanged() -> None:
    body = _fliggy_source_text().split("async def _submit_verified_public_flight_search", 1)[1]
    body = body.split("async def _commit_public_destination", 1)[0]
    assert 'destination_commitment.get("commitment_status") == "confirmed"' in body
    assert "verification.submit_allowed and destination_committed and date_committed" in body


def test_dr1_16_prior_diagnostic_contracts_remain_regression_safe() -> None:
    source = _fliggy_source_text()
    assert all(
        marker in source
        for marker in (
            '"d9_pre_submit_stability"',
            '"p7_query_identity"',
            '"h8_strict_identity"',
            '"c8_strict_q5"',
            '"a10_root_class"',
        )
    )


def test_dr1_17_screenshot_and_diagnostics_have_no_private_session_fields() -> None:
    source = _fliggy_source_text().split("def _annotated_region_svg", 1)[1]
    source = source.split("async def _write_destination_input_text", 1)[0]
    assert not any(term in source.lower() for term in ("cookie", "localstorage", "sessionstorage", "token"))


def test_dr1_18_unicode_preflight_aborts_before_provider_access() -> None:
    query = ProbeInput(
        "\ud800",
        "上海",
        date(2026, 9, 14),
        headless=False,
        planned_observation=1,
        evidence_output_path=str((REPO_ROOT / ".u10-r1-evidence.json").resolve()),
        destination_hit_target_probe="visual_map",
    )
    with pytest.raises(UnicodeError):
        _live_observation_preflight(query)
