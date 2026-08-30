from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.fliggy.browser_probe import (
    FLIGGY_BROWSER_PROBE_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    BrowserProbeStage,
    DomTraversalAssessment,
    ExperimentDiagnosis,
    FliggyPageIdentity,
    ProviderMarketCompleteness,
    ProbeInput,
    ProbeRunResult,
    StageDiagnostic,
    assess_dom_coverage,
    classify_fliggy_page_identity,
    classify_experiment_diagnosis,
    classify_result_state,
    extract_level1_evidence,
    sanitize_probe_payload,
    summarize_detector_state,
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
