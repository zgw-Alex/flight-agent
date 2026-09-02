from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.fliggy.browser_probe import (
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    Level2ExpansionBounds,
    Level2ExpansionOutcome,
    Level2ExpansionTarget,
    ProbeInput,
    build_level2_expansion_failure_result,
    build_level2_expansion_result_from_html,
    build_level2_live_parent_ref,
    classify_level2_expansion_state,
    extract_level1_evidence,
    extract_level2_offer_evidence,
    map_level1_outcome_to_level2_failure,
    run_fliggy_level2_live_validation,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PARENT_LEVEL1_REF = "fliggy-level1:MU5100:2026-09-02:1"


EXPANDED_LEVEL2_HTML = """
<html><body>
  <tr class="flight-item-tr" id="level1-mu5100">
    <td class="flight-line"><span class="J_line J_TestFlight">MU5100</span></td>
    <td class="flight-price"><span class="J_FlightListPrice">¥791起</span></td>
  </tr>
  <section data-testid="expanded-offers" data-parent-row="level1-mu5100">
    <div data-testid="fliggy-offer-row" data-offer-id="mu5100-row-a">
      <span data-testid="seller-name">东方航空旗舰店</span>
      <span data-testid="seller-marker">航司直营</span>
      <span data-testid="offer-price">¥820</span>
      <span data-testid="cabin-product">经济舱 标准价</span>
      <span data-testid="baggage">托运行李20KG</span>
      <span data-testid="fare-rule">退改¥280起</span>
      <span data-testid="availability">仅剩2张</span>
      <button data-testid="select-offer-btn" aria-label="预订东方航空旗舰店报价">预订</button>
    </div>
    <div data-testid="fliggy-offer-row" data-offer-id="mu5100-row-b">
      <span data-testid="seller-name">飞猪机票自营</span>
      <span data-testid="seller-marker">平台保障</span>
      <span data-testid="offer-price">￥845元</span>
      <span data-testid="cabin-product">经济舱 灵活退改</span>
      <span data-testid="baggage">以航司规则为准</span>
      <span data-testid="fare-rule">退改签规则见页面</span>
      <span data-testid="availability">有票</span>
      <button data-testid="select-offer-btn">订票</button>
    </div>
  </section>
</body></html>
"""


def test_extracts_multiple_level2_offer_rows_with_independent_raw_evidence() -> None:
    rows = extract_level2_offer_evidence(EXPANDED_LEVEL2_HTML, parent_level1_ref=PARENT_LEVEL1_REF)

    assert len(rows) == 2
    assert rows[0].offer_row_ref.endswith(":mu5100-row-a")
    assert rows[1].offer_row_ref.endswith(":mu5100-row-b")
    assert rows[0].parent_level1_ref == PARENT_LEVEL1_REF
    assert rows[1].parent_level1_ref == PARENT_LEVEL1_REF
    assert rows[0].raw_seller_text.raw_text == "东方航空旗舰店"
    assert rows[0].raw_seller_marker_text.raw_text == "航司直营"
    assert rows[0].raw_price_text.raw_text == "¥820"
    assert rows[0].price_amount == 820
    assert rows[0].price_currency == "CNY"
    assert rows[0].raw_cabin_product_text.raw_text == "经济舱 标准价"
    assert rows[0].raw_baggage_text.raw_text == "托运行李20KG"
    assert rows[0].raw_refund_change_rule_text.raw_text == "退改¥280起"
    assert rows[0].raw_availability_text.raw_text == "仅剩2张"
    assert rows[0].action_evidence.status == "OBSERVED"
    assert rows[1].raw_seller_text.raw_text == "飞猪机票自营"
    assert rows[1].raw_price_text.raw_text == "￥845元"


def test_level2_price_remains_layered_from_level1_lower_bound() -> None:
    result = build_level2_expansion_result_from_html(
        EXPANDED_LEVEL2_HTML,
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF, level1_evidence_index=1),
        acquired_at=datetime(2026, 9, 2, tzinfo=UTC),
        source_url="https://www.fliggy.com/?tab=flight&session=secret&spm=abc",
    ).to_dict()

    assert result["outcome"] == Level2ExpansionOutcome.SUCCESS_EXPANDED.value
    assert result["parent_level1_ref"] == PARENT_LEVEL1_REF
    assert result["sanitized_source_ref"] == "https://www.fliggy.com/?tab=flight"
    assert result["offer_rows"][0]["raw_price_text"]["raw_text"] == "¥820"
    assert "raw_displayed_lowest_price" not in result["offer_rows"][0]
    assert result["offer_rows"][0]["raw_availability_text"]["raw_text"] == "仅剩2张"
    assert "inventory" not in result["offer_rows"][0]
    assert "seller_type" not in result["offer_rows"][0]


def test_missing_level2_fields_are_kept_missing_without_guessing() -> None:
    html = """
    <html><body>
      <div class="offer-row">
        <span class="seller-name">代理商A</span>
        <span class="offer-price">¥900</span>
      </div>
    </body></html>
    """

    (row,) = extract_level2_offer_evidence(html, parent_level1_ref=PARENT_LEVEL1_REF)

    assert row.raw_seller_text.status == "OBSERVED"
    assert row.raw_baggage_text.status == "MISSING"
    assert row.raw_refund_change_rule_text.status == "MISSING"
    assert row.raw_availability_text.status == "MISSING"
    assert row.raw_seller_marker_text.status == "MISSING"


def test_level2_result_preserves_parent_provenance_and_probe_metadata() -> None:
    result = build_level2_expansion_result_from_html(
        EXPANDED_LEVEL2_HTML,
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF, provider_row_ref="dom-row-1"),
        acquired_at=datetime(2026, 9, 2, 1, 2, 3, tzinfo=UTC),
        experiment_run_id="exp-1",
        search_plan_id="plan-1",
        execution_id="exec-1",
        duration_ms=321,
    ).to_dict()

    assert result["provider_identity"] == "FLIGGY"
    assert result["acquisition_mode"] == BrowserAcquisitionMode.BROWSER.value
    assert result["acquired_at"] == "2026-09-02T01:02:03+00:00"
    assert result["experiment_run_id"] == "exp-1"
    assert result["search_plan_id"] == "plan-1"
    assert result["execution_id"] == "exec-1"
    assert result["target"]["provider_row_ref"] == "dom-row-1"
    assert result["diagnostics"]["parent_level1_evidence_preserved"] is True
    assert result["diagnostics"]["level2_mapping_performed"] is False


def test_level2_failure_result_preserves_level1_reference_without_rows() -> None:
    result = build_level2_expansion_result_from_html(
        "",
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF),
        acquired_at=datetime(2026, 9, 2, tzinfo=UTC),
        timed_out=True,
    ).to_dict()

    assert result["outcome"] == Level2ExpansionOutcome.TIMEOUT.value
    assert result["parent_level1_ref"] == PARENT_LEVEL1_REF
    assert result["observed_offer_row_count"] == 0
    assert result["offer_rows"] == []
    assert result["diagnostics"]["parent_level1_evidence_preserved"] is True


def test_level2_challenge_network_and_provider_outcomes_are_explicit() -> None:
    assert classify_level2_expansion_state("<html>滑块验证 安全验证</html>") is Level2ExpansionOutcome.ACCESS_CHALLENGE
    assert classify_level2_expansion_state("<html>系统繁忙 请稍后重试</html>") is Level2ExpansionOutcome.PROVIDER_ERROR

    network_result = build_level2_expansion_failure_result(
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF),
        outcome=Level2ExpansionOutcome.NETWORK_ERROR,
        acquired_at=datetime(2026, 9, 2, tzinfo=UTC),
        diagnostics={"network_error": "connection reset"},
    ).to_dict()
    assert network_result["outcome"] == Level2ExpansionOutcome.NETWORK_ERROR.value
    assert network_result["diagnostics"]["network_error"] == "connection reset"


def test_level2_sanitization_removes_sensitive_browser_state() -> None:
    result = build_level2_expansion_result_from_html(
        EXPANDED_LEVEL2_HTML,
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF),
        acquired_at=datetime(2026, 9, 2, tzinfo=UTC),
        diagnostics={
            "Authorization": "Bearer live-token",
            "Cookie": "cookie: abc",
            "notes": "session=secret token=secret",
        },
    ).to_dict()

    assert result["diagnostics"]["Authorization"] == "[REDACTED]"
    assert result["diagnostics"]["Cookie"] == "[REDACTED]"
    assert result["diagnostics"]["notes"] == "[REDACTED]"


def test_level2_expansion_requires_explicit_target_and_positive_bounds() -> None:
    with pytest.raises(ValueError, match="parent_level1_ref"):
        Level2ExpansionTarget(parent_level1_ref=" ")
    with pytest.raises(ValueError, match="max_offer_rows"):
        Level2ExpansionBounds(max_offer_rows=0)
    with pytest.raises(ValueError, match="max_wait_ms"):
        Level2ExpansionBounds(max_wait_ms=0)
    with pytest.raises(ValueError, match="max_retries"):
        Level2ExpansionBounds(max_retries=-1)


def test_level2_bounded_expansion_limits_offer_rows() -> None:
    result = build_level2_expansion_result_from_html(
        EXPANDED_LEVEL2_HTML,
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF),
        acquired_at=datetime(2026, 9, 2, tzinfo=UTC),
        bounds=Level2ExpansionBounds(max_offer_rows=1, max_wait_ms=250, max_retries=0),
    ).to_dict()

    assert result["outcome"] == Level2ExpansionOutcome.SUCCESS_EXPANDED.value
    assert result["observed_offer_row_count"] == 1
    assert len(result["offer_rows"]) == 1
    assert result["bounds"]["max_offer_rows"] == 1
    assert result["bounds"]["max_wait_ms"] == 250
    assert result["bounds"]["max_retries"] == 0
    assert result["bounds"]["bounded_expansion"] is True


def test_level2_empty_and_action_not_available_are_distinct() -> None:
    assert classify_level2_expansion_state("<html>暂无可订报价</html>") is Level2ExpansionOutcome.SUCCESS_EMPTY
    assert (
        classify_level2_expansion_state(EXPANDED_LEVEL2_HTML, action_available=False)
        is Level2ExpansionOutcome.ACTION_NOT_AVAILABLE
    )


def test_live_validation_parent_ref_is_provider_local_and_traceable() -> None:
    level1 = extract_level1_evidence(
        """
        <html><body>
          <tr class="flight-item-tr">
            <td class="flight-line"><span class="J_line J_TestFlight">MU5100</span></td>
            <td class="flight-time"><span>07:00</span><span>09:10</span></td>
            <td class="flight-port"><span>首都T2</span><span>虹桥T2</span></td>
            <td class="flight-price"><span class="J_FlightListPrice">¥791起</span></td>
            <td class="flight-operate"><button data-testid="select-flight-btn">订票</button></td>
          </tr>
        </body></html>
        """
    )[0]

    assert build_level2_live_parent_ref(level1) == "fliggy-level1-live:1:MU5100"


def test_live_validation_maps_level1_failures_to_authorized_level2_outcomes() -> None:
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.ACCESS_CHALLENGE) is (
        Level2ExpansionOutcome.ACCESS_CHALLENGE
    )
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.LOGIN_REQUIRED) is (
        Level2ExpansionOutcome.ACCESS_CHALLENGE
    )
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.TIMEOUT) is Level2ExpansionOutcome.TIMEOUT
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.NETWORK_ERROR) is (
        Level2ExpansionOutcome.NETWORK_ERROR
    )
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.PROVIDER_ERROR) is (
        Level2ExpansionOutcome.PROVIDER_ERROR
    )
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.SUCCESS_EMPTY) is Level2ExpansionOutcome.SUCCESS_EMPTY
    assert map_level1_outcome_to_level2_failure(BrowserProbeOutcome.EVIDENCE_INSUFFICIENT) is (
        Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT
    )


@pytest.mark.asyncio
async def test_live_validation_rejects_unbounded_level1_target_count_before_browser_launch() -> None:
    with pytest.raises(ValueError, match="max_level1_targets"):
        await run_fliggy_level2_live_validation(
            ProbeInput("北京", "上海", date(2026, 9, 17)),
            max_level1_targets=3,
        )


def test_level2_live_smoke_is_explicit_opt_in_and_outside_ordinary_ci() -> None:
    smoke = REPO_ROOT / "scripts" / "smoke" / "fliggy_level2_live_validation.py"
    backend_ci = (REPO_ROOT / "scripts" / "ci" / "backend.ps1").read_text(encoding="utf-8")
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")

    assert smoke.exists()
    assert "fliggy_level2_live_validation" not in backend_ci
    assert "fliggy_level2_live_validation" not in all_ci
