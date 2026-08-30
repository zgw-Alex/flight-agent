"""Probe-local FLIGGY browser evidence acquisition.

This module intentionally stays outside the formal FlightProvider adapter path.
It produces provider-side raw evidence for M9-BP5-U1 and does not construct
canonical FlightSegment, Itinerary, Offer, or ProviderSearchResult objects.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FLIGGY_BROWSER_PROBE_VERSION = "m9-bp5-u1-fliggy-browser-probe-v0.1"
FLIGGY_PROVIDER_ID = "FLIGGY"
FLIGGY_ACQUISITION_MODE = "BROWSER"
_FLIGGY_FLIGHT_ENTRY_URL = "https://www.fliggy.com/?tab=flight"

_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "session",
    "token",
)


class BrowserAcquisitionMode(str, Enum):
    BROWSER = "BROWSER"


class BrowserProbeOutcome(str, Enum):
    SUCCESS_COMPLETE = "SUCCESS_COMPLETE"
    SUCCESS_PARTIAL = "SUCCESS_PARTIAL"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    ACCESS_CHALLENGE = "ACCESS_CHALLENGE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    TIMEOUT = "TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"


class BrowserProbeStage(str, Enum):
    BROWSER_LAUNCH = "BROWSER_LAUNCH"
    ENTRY_NAVIGATION = "ENTRY_NAVIGATION"
    SEARCH_INPUT_READY = "SEARCH_INPUT_READY"
    SEARCH_SUBMITTED = "SEARCH_SUBMITTED"
    RESULT_STATE_WAIT = "RESULT_STATE_WAIT"
    RESULT_CONTAINER_DETECTED = "RESULT_CONTAINER_DETECTED"
    LEVEL1_EXTRACTION = "LEVEL1_EXTRACTION"
    COVERAGE_TRAVERSAL = "COVERAGE_TRAVERSAL"
    COMPLETED = "COMPLETED"


class ExperimentDiagnosis(str, Enum):
    STABLE_SUCCESS = "STABLE_SUCCESS"
    INTERMITTENT_SUCCESS = "INTERMITTENT_SUCCESS"
    HEADLESS_SPECIFIC_FAILURE = "HEADLESS_SPECIFIC_FAILURE"
    STABLE_TIMEOUT = "STABLE_TIMEOUT"
    ACCESS_CHALLENGE = "ACCESS_CHALLENGE"
    SELECTOR_MISMATCH = "SELECTOR_MISMATCH"
    SEARCH_INTERACTION_FAILURE = "SEARCH_INTERACTION_FAILURE"
    NETWORK_ENVIRONMENT_FAILURE = "NETWORK_ENVIRONMENT_FAILURE"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"


class FliggyPageIdentity(str, Enum):
    EXPECTED_FLIGHT_SEARCH = "EXPECTED_FLIGHT_SEARCH"
    FLIGHT_RESULT_CANDIDATE = "FLIGHT_RESULT_CANDIDATE"
    WRONG_NAVIGATION_TARGET = "WRONG_NAVIGATION_TARGET"
    ACCESS_CHALLENGE = "ACCESS_CHALLENGE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    UNKNOWN = "UNKNOWN"


class DomTraversalAssessment(str, Enum):
    COMPLETE_OBSERVED = "COMPLETE_OBSERVED"
    PARTIAL_OBSERVED = "PARTIAL_OBSERVED"
    UNKNOWN = "UNKNOWN"


class ProviderMarketCompleteness(str, Enum):
    UNKNOWN_NOT_PROVEN = "UNKNOWN_NOT_PROVEN"


@dataclass(frozen=True)
class ProbeInput:
    origin_text: str
    destination_text: str
    departure_date: date
    experiment_run_id: str | None = None
    search_plan_id: str | None = None
    execution_id: str | None = None
    overall_deadline_seconds: float = 30.0
    headless: bool = True

    def __post_init__(self) -> None:
        if self.origin_text.strip() == "":
            raise ValueError("ProbeInput origin_text is required")
        if self.destination_text.strip() == "":
            raise ValueError("ProbeInput destination_text is required")
        if self.overall_deadline_seconds <= 0:
            raise ValueError("ProbeInput overall_deadline_seconds must be positive")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        return cls(
            origin_text=args.origin,
            destination_text=args.destination,
            departure_date=date.fromisoformat(args.departure_date),
            experiment_run_id=args.experiment_run_id,
            search_plan_id=args.search_plan_id,
            execution_id=args.execution_id,
            overall_deadline_seconds=args.deadline_seconds,
            headless=not args.headed,
        )


@dataclass(frozen=True)
class FieldEvidence:
    status: str
    raw_text: str | None = None
    selector: str | None = None
    diagnostic: str | None = None

    @classmethod
    def observed(cls, raw_text: str, selector: str, diagnostic: str | None = None) -> Self:
        return cls(status="OBSERVED", raw_text=_normalize_space(raw_text), selector=selector, diagnostic=diagnostic)

    @classmethod
    def missing(cls, diagnostic: str) -> Self:
        return cls(status="MISSING", diagnostic=diagnostic)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "raw_text": self.raw_text,
            "selector": self.selector,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True)
class StageDiagnostic:
    stage: BrowserProbeStage
    elapsed_ms: int
    detail: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "stage": self.stage.value,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ResultContextCandidate:
    page_index: int
    sanitized_url: str
    title: str
    identity: FliggyPageIdentity
    search_plan_evidence: dict[str, bool]
    is_current_page: bool

    def score(self) -> int:
        score = 0
        if self.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE:
            score += 8
        if self.search_plan_evidence.get("origin"):
            score += 2
        if self.search_plan_evidence.get("destination"):
            score += 2
        if self.search_plan_evidence.get("departure_date"):
            score += 1
        if "flight_search_result" in self.sanitized_url:
            score += 1
        if not self.is_current_page:
            score += 1
        return score

    def route_matches(self) -> bool:
        return self.search_plan_evidence.get("origin") is True and self.search_plan_evidence.get("destination") is True

    def signature(self) -> tuple[str, str, str]:
        return (self.sanitized_url, self.title, self.identity.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "sanitized_url": self.sanitized_url,
            "title": self.title,
            "identity": self.identity.value,
            "search_plan_evidence": self.search_plan_evidence,
            "is_current_page": self.is_current_page,
            "score": self.score(),
        }


@dataclass(frozen=True)
class FliggyFlightEvidence:
    evidence_index: int
    raw_displayed_flight_identity: FieldEvidence
    raw_accessible_flight_label: FieldEvidence
    raw_aircraft_text: FieldEvidence
    raw_departure_time: FieldEvidence
    raw_arrival_time: FieldEvidence
    raw_departure_airport_terminal: FieldEvidence
    raw_arrival_airport_terminal: FieldEvidence
    raw_duration_text: FieldEvidence
    raw_on_time_rate_text: FieldEvidence
    raw_displayed_lowest_price: FieldEvidence
    raw_discount_text: FieldEvidence
    raw_availability_tag: FieldEvidence
    raw_codeshare_indicator: FieldEvidence
    raw_codeshare_detail_text: FieldEvidence
    booking_offer_expansion_action_present: bool
    booking_action_diagnostic: dict[str, str | bool | None]
    container_diagnostic: dict[str, str | int | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_index": self.evidence_index,
            "raw_displayed_flight_identity": self.raw_displayed_flight_identity.to_dict(),
            "raw_accessible_flight_label": self.raw_accessible_flight_label.to_dict(),
            "raw_aircraft_text": self.raw_aircraft_text.to_dict(),
            "raw_departure_time": self.raw_departure_time.to_dict(),
            "raw_arrival_time": self.raw_arrival_time.to_dict(),
            "raw_departure_airport_terminal": self.raw_departure_airport_terminal.to_dict(),
            "raw_arrival_airport_terminal": self.raw_arrival_airport_terminal.to_dict(),
            "raw_duration_text": self.raw_duration_text.to_dict(),
            "raw_on_time_rate_text": self.raw_on_time_rate_text.to_dict(),
            "raw_displayed_lowest_price": self.raw_displayed_lowest_price.to_dict(),
            "raw_discount_text": self.raw_discount_text.to_dict(),
            "raw_availability_tag": self.raw_availability_tag.to_dict(),
            "raw_codeshare_indicator": self.raw_codeshare_indicator.to_dict(),
            "raw_codeshare_detail_text": self.raw_codeshare_detail_text.to_dict(),
            "booking_offer_expansion_action_present": self.booking_offer_expansion_action_present,
            "booking_action_diagnostic": self.booking_action_diagnostic,
            "container_diagnostic": self.container_diagnostic,
        }


@dataclass(frozen=True)
class ProbeRunResult:
    provider_identity: str
    acquisition_mode: BrowserAcquisitionMode
    acquired_at: datetime
    experiment_run_id: str | None
    search_scope: dict[str, str]
    search_plan_id: str | None
    execution_id: str | None
    outcome: BrowserProbeOutcome
    observed_result_count: int
    duration_ms: int
    dom_traversal_assessment: DomTraversalAssessment
    provider_market_completeness: ProviderMarketCompleteness
    terminal_boundary_observed: bool
    terminal_boundary_evidence: str | None
    parser_selector_probe_version: str
    sanitized_source_ref: str | None
    evidence: tuple[FliggyFlightEvidence, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return sanitize_probe_payload(
            {
                "provider_identity": self.provider_identity,
                "acquisition_mode": self.acquisition_mode.value,
                "acquired_at": self.acquired_at.isoformat(),
                "experiment_run_id": self.experiment_run_id,
                "search_scope": self.search_scope,
                "search_plan_id": self.search_plan_id,
                "execution_id": self.execution_id,
                "outcome": self.outcome.value,
                "observed_result_count": self.observed_result_count,
                "duration_ms": self.duration_ms,
                "dom_traversal_assessment": self.dom_traversal_assessment.value,
                "provider_market_completeness": self.provider_market_completeness.value,
                "terminal_boundary_observed": self.terminal_boundary_observed,
                "terminal_boundary_evidence": self.terminal_boundary_evidence,
                "parser_selector_probe_version": self.parser_selector_probe_version,
                "sanitized_source_ref": self.sanitized_source_ref,
                "evidence": [item.to_dict() for item in self.evidence],
                "diagnostics": self.diagnostics,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def classify_result_state(html: str, *, timed_out: bool = False) -> BrowserProbeOutcome:
    detector_state = summarize_detector_state(html)
    if detector_state["access_challenge"]:
        return BrowserProbeOutcome.ACCESS_CHALLENGE
    if detector_state["login_required"]:
        return BrowserProbeOutcome.LOGIN_REQUIRED
    if detector_state["provider_error"]:
        return BrowserProbeOutcome.PROVIDER_ERROR
    if detector_state["result_container"]:
        return BrowserProbeOutcome.SUCCESS_COMPLETE
    if detector_state["explicit_empty"]:
        return BrowserProbeOutcome.SUCCESS_EMPTY
    if timed_out:
        return BrowserProbeOutcome.TIMEOUT
    return BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def summarize_detector_state(html: str) -> dict[str, bool | int]:
    root = parse_html(html)
    page_text = root.text_content().lower()
    result_count = len(_result_rows(root))
    terminal_observed, _ = _terminal_boundary_from_html(html)
    return {
        "access_challenge": _contains_any(page_text, ("captcha", "验证码", "滑块", "安全验证", "访问验证", "拖动滑块")),
        "login_required": _contains_any(page_text, ("请登录", "登录后", "login required", "sign in", "请先登录")),
        "provider_error": _contains_any(page_text, ("系统繁忙", "服务异常", "出错了", "provider error", "upstream error")),
        "result_container": result_count > 0,
        "explicit_empty": _contains_any(page_text, ("暂无航班", "无航班", "没有找到", "no flights", "empty result")),
        "observed_row_count": result_count,
        "terminal_boundary_observed": terminal_observed,
    }


def classify_fliggy_page_identity(*, url: str, title: str, html: str) -> FliggyPageIdentity:
    root = parse_html(html)
    page_text = root.text_content()
    lowered_text = page_text.lower()
    host = urlsplit(url).netloc.lower()
    if _contains_any(lowered_text, ("captcha", "验证码", "滑块", "安全验证", "访问验证", "拖动滑块")):
        return FliggyPageIdentity.ACCESS_CHALLENGE
    if _contains_any(page_text, ("没有找到相应的店铺信息", "没有找到店铺", "店铺不存在", "找不到店铺")):
        return FliggyPageIdentity.WRONG_NAVIGATION_TARGET
    if "taobao.com" in host and _contains_any(title + page_text, ("店铺浏览", "店铺", "宝贝")):
        return FliggyPageIdentity.WRONG_NAVIGATION_TARGET
    if _contains_any(lowered_text, ("login required", "sign in")):
        return FliggyPageIdentity.LOGIN_REQUIRED

    expected_origin = "fliggy.com" in host or "alitrip.com" in host
    result_path = _contains_any(urlsplit(url).path, ("flight_search_result", "trip_flight_search"))
    result_title = _contains_any(title + page_text, ("航班查询", "特价机票", "国内机票", "起飞", "经济舱", "直飞"))
    if expected_origin and result_path and result_title:
        return FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE
    expected_title = _contains_any(title, ("飞猪", "机票", "Fliggy"))
    expected_controls = bool(root.select(".rc-flight-searchbar")) and bool(root.select("input#form_depCity"))
    expected_text = _contains_any(page_text, ("搜索机票", "出发城市", "到达城市", "出发日期", "单程", "往返"))
    if expected_origin and (expected_controls or (expected_title and expected_text)):
        return FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH
    if _contains_any(page_text, ("请登录", "登录后", "请先登录")) and not expected_origin:
        return FliggyPageIdentity.LOGIN_REQUIRED
    return FliggyPageIdentity.UNKNOWN


def summarize_search_plan_evidence(*, title: str, html: str, probe_input: ProbeInput) -> dict[str, bool]:
    text = title + " " + parse_html(html).text_content()
    departure_date = probe_input.departure_date.isoformat()
    dotted_date = departure_date.replace("-", ".")
    slash_date = departure_date.replace("-", "/")
    chinese_date = f"{probe_input.departure_date.month}月{probe_input.departure_date.day}日"
    compact_route = f"{probe_input.origin_text}到{probe_input.destination_text}"
    return {
        "origin": probe_input.origin_text in text or compact_route in text,
        "destination": probe_input.destination_text in text or compact_route in text,
        "departure_date": any(candidate in text for candidate in (departure_date, dotted_date, slash_date, chinese_date)),
    }


def choose_result_context_candidate(
    candidates: tuple[ResultContextCandidate, ...],
) -> ResultContextCandidate | None:
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE and candidate.route_matches()
    )
    if not eligible:
        return None
    ranked = sorted(eligible, key=lambda candidate: candidate.score(), reverse=True)
    best = ranked[0]
    tied = tuple(candidate for candidate in ranked if candidate.score() == best.score())
    if len({candidate.signature() for candidate in tied}) > 1:
        return None
    return best


def extract_level1_evidence(html: str) -> tuple[FliggyFlightEvidence, ...]:
    root = parse_html(html)
    evidence: list[FliggyFlightEvidence] = []
    for index, row in enumerate(_result_rows(root), start=1):
        times = _texts_for(row, (".flight-time .time", ".flight-time span", ".flight-time"))
        ports = _texts_for(row, (".flight-port .port", ".flight-port span", ".flight-port"))
        accessible_label = _first_attr(row, ("[aria-label^='航班号']", "[aria-label*='航班']"), "aria-label")
        booking_selector = _first_selector(
            row,
            ("[data-testid='select-flight-btn']", "button[aria-label='订票']", ".flight-operate button", "button"),
        )
        codeshare_detail = _first_field(
            row,
            (
                "[data-testid='share-flight-tip']",
                ".share-flight-tip",
                "[aria-label*='实际乘坐']",
                "[aria-label*='共享']",
            ),
        )
        has_codeshare = codeshare_detail.status == "OBSERVED" or _contains_any(
            row.text_content(), ("共享", "实际乘坐", "实际承运", "codeshare")
        )
        evidence.append(
            FliggyFlightEvidence(
                evidence_index=index,
                raw_displayed_flight_identity=_first_field(
                    row, (".J_line.J_TestFlight", "[aria-label^='航班号']", ".flight-line", ".J_TestFlight")
                ),
                raw_accessible_flight_label=(
                    FieldEvidence.observed(accessible_label[1], accessible_label[0], "aria-label")
                    if accessible_label is not None
                    else FieldEvidence.missing("no accessible flight label observed")
                ),
                raw_aircraft_text=_first_field(row, ("[aria-label^='机型']", ".flight-aircraft", ".aircraft")),
                raw_departure_time=_indexed_field(times, 0, ".flight-time", "departure time"),
                raw_arrival_time=_indexed_field(times, 1, ".flight-time", "arrival time"),
                raw_departure_airport_terminal=_indexed_field(ports, 0, ".flight-port", "departure airport/terminal"),
                raw_arrival_airport_terminal=_indexed_field(ports, 1, ".flight-port", "arrival airport/terminal"),
                raw_duration_text=_first_field(row, (".flight-total-time", ".total-time", "[aria-label*='时长']")),
                raw_on_time_rate_text=_first_field(row, (".flight-ontime-rate", ".ontime-rate", "[aria-label*='准点']")),
                raw_displayed_lowest_price=_first_field(
                    row, ("[aria-label^='票价']", ".J_FlightListPrice", ".flight-price", "[class*='price']")
                ),
                raw_discount_text=_first_field(row, (".flight-discount", ".discount", "[aria-label*='折扣']")),
                raw_availability_tag=_first_field(row, (".flight-availability", ".availability", "[aria-label*='余']")),
                raw_codeshare_indicator=(
                    FieldEvidence.observed("codeshare indicator present", "[data-testid='share-flight-tip']")
                    if has_codeshare
                    else FieldEvidence.missing("no codeshare indicator observed")
                ),
                raw_codeshare_detail_text=codeshare_detail,
                booking_offer_expansion_action_present=booking_selector is not None,
                booking_action_diagnostic={
                    "selector": booking_selector,
                    "read_only": True,
                    "clicked": False,
                },
                container_diagnostic={
                    "selector": _row_selector(row),
                    "run_local_index": index,
                    "text_length": len(row.text_content()),
                },
            )
        )
    return tuple(evidence)


def assess_dom_coverage(
    *,
    initial_count: int,
    final_count: int,
    terminal_boundary_observed: bool,
    stabilization_rounds: int,
) -> DomTraversalAssessment:
    _ = initial_count
    if final_count <= 0:
        return DomTraversalAssessment.UNKNOWN
    if terminal_boundary_observed and stabilization_rounds > 0:
        return DomTraversalAssessment.COMPLETE_OBSERVED
    return DomTraversalAssessment.PARTIAL_OBSERVED


def sanitize_probe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_probe_payload(item)
        return sanitized
    if isinstance(value, list | tuple):
        return [sanitize_probe_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def classify_experiment_diagnosis(results: tuple[ProbeRunResult, ...]) -> ExperimentDiagnosis:
    if not results:
        return ExperimentDiagnosis.EVIDENCE_INSUFFICIENT
    outcomes = tuple(result.outcome for result in results)
    if any(outcome is BrowserProbeOutcome.ACCESS_CHALLENGE for outcome in outcomes):
        return ExperimentDiagnosis.ACCESS_CHALLENGE
    if all(outcome is BrowserProbeOutcome.TIMEOUT for outcome in outcomes):
        return ExperimentDiagnosis.STABLE_TIMEOUT
    if all(outcome is BrowserProbeOutcome.NETWORK_ERROR for outcome in outcomes):
        return ExperimentDiagnosis.NETWORK_ENVIRONMENT_FAILURE
    success_outcomes = {BrowserProbeOutcome.SUCCESS_COMPLETE, BrowserProbeOutcome.SUCCESS_PARTIAL, BrowserProbeOutcome.SUCCESS_EMPTY}
    success_flags = tuple(outcome in success_outcomes for outcome in outcomes)
    if all(success_flags):
        return ExperimentDiagnosis.STABLE_SUCCESS
    if any(success_flags):
        headless_results = [result for result in results if result.diagnostics.get("headless") is True]
        headed_results = [result for result in results if result.diagnostics.get("headless") is False]
        if headless_results and headed_results:
            headless_success = any(result.outcome in success_outcomes for result in headless_results)
            headed_success = any(result.outcome in success_outcomes for result in headed_results)
            if headed_success and not headless_success:
                return ExperimentDiagnosis.HEADLESS_SPECIFIC_FAILURE
        return ExperimentDiagnosis.INTERMITTENT_SUCCESS
    if any(result.diagnostics.get("search_interaction_failed") is True for result in results):
        return ExperimentDiagnosis.SEARCH_INTERACTION_FAILURE
    if any(result.diagnostics.get("selector_mismatch_suspected") is True for result in results):
        return ExperimentDiagnosis.SELECTOR_MISMATCH
    return ExperimentDiagnosis.EVIDENCE_INSUFFICIENT


async def run_fliggy_browser_probe(probe_input: ProbeInput) -> ProbeRunResult:
    """Run the opt-in live browser probe without persisting browser session state."""

    started = time.monotonic()
    acquired_at = datetime.now(UTC)
    url = _build_fliggy_search_url(probe_input)
    recorder = _StageRecorder(started)
    diagnostics: dict[str, Any] = {
        "read_only": True,
        "clicked": False,
        "retries": 0,
        "headless": probe_input.headless,
        "entry_url_strategy": "public_fliggy_flight_entry_tab",
        "stage_diagnostics": [],
        "last_stage": None,
        "detector_state": summarize_detector_state(""),
        "page_identity": FliggyPageIdentity.UNKNOWN.value,
        "wrong_navigation_target": False,
        "search_interaction_failed": False,
        "search_submission_attempted": False,
        "visible_public_form_used": False,
        "result_context_handoff": {
            "page_count_before_submit": None,
            "page_count_after_submit": None,
            "popup_or_new_page_event": False,
            "candidate_pages": [],
            "selected_page_index": None,
            "selected_page_url": None,
            "selected_page_identity": None,
            "selection_reason": None,
        },
        "document_ready_state": None,
        "final_sanitized_url": None,
    }
    html = ""
    outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    terminal_observed = False
    terminal_evidence: str | None = None
    stabilization_rounds = 0
    evidence: tuple[FliggyFlightEvidence, ...] = ()

    try:
        recorder.mark(BrowserProbeStage.BROWSER_LAUNCH, "launching Playwright Chromium")
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the opt-in live FLIGGY browser probe") from exc

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=probe_input.headless)
            context = await browser.new_context(storage_state=None)
            page = await context.new_page()
            page.set_default_timeout(_remaining_ms(started, probe_input.overall_deadline_seconds))
            recorder.mark(BrowserProbeStage.ENTRY_NAVIGATION, "opening FLIGGY public search page")
            await page.goto(url, wait_until="domcontentloaded", timeout=_remaining_ms(started, probe_input.overall_deadline_seconds))
            html = await page.content()
            title = await page.title()
            diagnostics["document_ready_state"] = await page.evaluate("document.readyState")
            diagnostics["final_sanitized_url"] = _sanitize_source_ref(page.url)
            diagnostics["detector_state"] = summarize_detector_state(html)
            page_identity = classify_fliggy_page_identity(url=page.url, title=title, html=html)
            diagnostics["page_identity"] = page_identity.value
            diagnostics["page_title"] = title
            should_wait_for_result_state = False
            if page_identity is FliggyPageIdentity.ACCESS_CHALLENGE:
                outcome = BrowserProbeOutcome.ACCESS_CHALLENGE
                await context.close()
                await browser.close()
            elif page_identity is FliggyPageIdentity.LOGIN_REQUIRED:
                outcome = BrowserProbeOutcome.LOGIN_REQUIRED
                await context.close()
                await browser.close()
            elif page_identity is FliggyPageIdentity.WRONG_NAVIGATION_TARGET:
                outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                diagnostics["wrong_navigation_target"] = True
                diagnostics["search_interaction_failed"] = True
                await context.close()
                await browser.close()
            elif page_identity is FliggyPageIdentity.UNKNOWN:
                diagnostics["search_interaction_failed"] = True
                outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                await context.close()
                await browser.close()
            else:
                recorder.mark(BrowserProbeStage.SEARCH_INPUT_READY, "public flight-search controls detected")
                page_count_before_submit = len(context.pages)
                diagnostics["result_context_handoff"]["page_count_before_submit"] = page_count_before_submit
                await _submit_public_flight_search(page, probe_input)
                diagnostics["clicked"] = True
                diagnostics["search_submission_attempted"] = True
                diagnostics["visible_public_form_used"] = True
                recorder.mark(BrowserProbeStage.SEARCH_SUBMITTED, "search submitted through public visible flight form")
                page, handoff_diagnostics = await _select_result_context_page(
                    context=context,
                    current_page=page,
                    probe_input=probe_input,
                    page_error_type=PlaywrightError,
                    page_count_before_submit=page_count_before_submit,
                    wait_ms=min(5000, max(500, _remaining_ms(started, probe_input.overall_deadline_seconds) - 500)),
                )
                diagnostics["result_context_handoff"] = handoff_diagnostics
                if handoff_diagnostics["selected_page_index"] is None:
                    diagnostics["search_interaction_failed"] = True
                else:
                    diagnostics["final_sanitized_url"] = handoff_diagnostics["selected_page_url"]
                    diagnostics["page_identity"] = handoff_diagnostics["selected_page_identity"]
                should_wait_for_result_state = True
            if should_wait_for_result_state:
                recorder.mark(BrowserProbeStage.RESULT_STATE_WAIT, "waiting for terminal/result state")
                await page.wait_for_load_state("networkidle", timeout=_remaining_ms(started, probe_input.overall_deadline_seconds))
                html = await page.content()
                title = await page.title()
                diagnostics["document_ready_state"] = await page.evaluate("document.readyState")
                diagnostics["final_sanitized_url"] = _sanitize_source_ref(page.url)
                diagnostics["detector_state"] = summarize_detector_state(html)
                page_identity = classify_fliggy_page_identity(url=page.url, title=title, html=html)
                diagnostics["page_identity"] = page_identity.value
                diagnostics["page_title"] = title
                if page_identity is FliggyPageIdentity.WRONG_NAVIGATION_TARGET:
                    diagnostics["wrong_navigation_target"] = True
                    diagnostics["search_interaction_failed"] = True
                    outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                elif page_identity is FliggyPageIdentity.ACCESS_CHALLENGE:
                    outcome = BrowserProbeOutcome.ACCESS_CHALLENGE
                elif page_identity is FliggyPageIdentity.LOGIN_REQUIRED:
                    outcome = BrowserProbeOutcome.LOGIN_REQUIRED
                else:
                    outcome = classify_result_state(html)
                if (
                    diagnostics["search_submission_attempted"] is True
                    and page_identity is FliggyPageIdentity.EXPECTED_FLIGHT_SEARCH
                    and outcome is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                ):
                    diagnostics["search_interaction_failed"] = True
            if outcome in {
                BrowserProbeOutcome.ACCESS_CHALLENGE,
                BrowserProbeOutcome.LOGIN_REQUIRED,
                BrowserProbeOutcome.PROVIDER_ERROR,
                BrowserProbeOutcome.SUCCESS_EMPTY,
                BrowserProbeOutcome.EVIDENCE_INSUFFICIENT,
            }:
                await context.close()
                await browser.close()
            else:
                recorder.mark(BrowserProbeStage.RESULT_CONTAINER_DETECTED, "result container detected")
                recorder.mark(BrowserProbeStage.LEVEL1_EXTRACTION, "extracting Level-1 raw evidence")
                initial_count = len(extract_level1_evidence(html))
                previous_count = initial_count
                recorder.mark(BrowserProbeStage.COVERAGE_TRAVERSAL, "starting bounded coverage traversal")
                for _ in range(6):
                    if _remaining_ms(started, probe_input.overall_deadline_seconds) <= 250:
                        raise PlaywrightTimeoutError("overall deadline exhausted during traversal")
                    await page.mouse.wheel(0, 1600)
                    await page.wait_for_timeout(500)
                    html = await page.content()
                    diagnostics["detector_state"] = summarize_detector_state(html)
                    current_count = len(extract_level1_evidence(html))
                    terminal_observed, terminal_evidence = _terminal_boundary_from_html(html)
                    if current_count == previous_count:
                        stabilization_rounds += 1
                    else:
                        stabilization_rounds = 0
                    previous_count = current_count
                    if stabilization_rounds >= 2 or terminal_observed:
                        break
                evidence = extract_level1_evidence(html)
                coverage = assess_dom_coverage(
                    initial_count=initial_count,
                    final_count=len(evidence),
                    terminal_boundary_observed=terminal_observed,
                    stabilization_rounds=max(stabilization_rounds, 1 if terminal_observed else 0),
                )
                outcome = (
                    BrowserProbeOutcome.SUCCESS_COMPLETE
                    if coverage is DomTraversalAssessment.COMPLETE_OBSERVED
                    else BrowserProbeOutcome.SUCCESS_PARTIAL
                )
                diagnostics["initial_result_count"] = initial_count
                diagnostics["stabilization_rounds"] = stabilization_rounds
                recorder.mark(BrowserProbeStage.COMPLETED, "probe result completed")
                await context.close()
                await browser.close()
    except PlaywrightTimeoutError:
        outcome = BrowserProbeOutcome.TIMEOUT
        diagnostics["failure_kind"] = "timeout"
        diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if html:
            diagnostics["detector_state"] = summarize_detector_state(html)
    except PlaywrightError as exc:
        outcome = BrowserProbeOutcome.NETWORK_ERROR
        diagnostics["failure_kind"] = "playwright_error"
        diagnostics["failure_message"] = str(exc)

    if not evidence and html:
        evidence = extract_level1_evidence(html)
    coverage = assess_dom_coverage(
        initial_count=len(evidence),
        final_count=len(evidence),
        terminal_boundary_observed=terminal_observed,
        stabilization_rounds=stabilization_rounds,
    )
    last_stage = recorder.last_stage()
    if last_stage is not BrowserProbeStage.COMPLETED and outcome not in {
        BrowserProbeOutcome.TIMEOUT,
        BrowserProbeOutcome.NETWORK_ERROR,
    }:
        recorder.mark(BrowserProbeStage.COMPLETED, "probe result completed")
    last_stage = recorder.last_stage()
    diagnostics["last_stage"] = last_stage.value if last_stage is not None else None
    diagnostics["stage_diagnostics"] = [stage.to_dict() for stage in recorder.stages]
    diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return ProbeRunResult(
        provider_identity=FLIGGY_PROVIDER_ID,
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=acquired_at,
        experiment_run_id=probe_input.experiment_run_id,
        search_scope={
            "origin_text": probe_input.origin_text,
            "destination_text": probe_input.destination_text,
            "departure_date": probe_input.departure_date.isoformat(),
            "trip_type": "ONE_WAY",
            "market": "CHINA_DOMESTIC",
        },
        search_plan_id=probe_input.search_plan_id,
        execution_id=probe_input.execution_id,
        outcome=outcome,
        observed_result_count=len(evidence),
        duration_ms=int((time.monotonic() - started) * 1000),
        dom_traversal_assessment=coverage,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=terminal_observed,
        terminal_boundary_evidence=terminal_evidence,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        sanitized_source_ref=_sanitize_source_ref(url),
        evidence=evidence,
        diagnostics=diagnostics,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in FLIGGY read-only browser acquisition probe")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-date", required=True)
    parser.add_argument("--experiment-run-id")
    parser.add_argument("--search-plan-id")
    parser.add_argument("--execution-id")
    parser.add_argument("--deadline-seconds", type=float, default=30.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    import asyncio

    result = asyncio.run(run_fliggy_browser_probe(ProbeInput.from_args(args)))
    rendered = result.to_json()
    print(rendered)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.outcome in {BrowserProbeOutcome.SUCCESS_COMPLETE, BrowserProbeOutcome.SUCCESS_PARTIAL, BrowserProbeOutcome.SUCCESS_EMPTY} else 2


class _StageRecorder:
    def __init__(self, started: float) -> None:
        self._started = started
        self.stages: list[StageDiagnostic] = []

    def mark(self, stage: BrowserProbeStage, detail: str) -> None:
        self.stages.append(
            StageDiagnostic(
                stage=stage,
                elapsed_ms=int((time.monotonic() - self._started) * 1000),
                detail=detail,
            )
        )

    def last_stage(self) -> BrowserProbeStage | None:
        if not self.stages:
            return None
        return self.stages[-1].stage


class HtmlNode:
    def __init__(self, tag: str, attrs: dict[str, str], parent: HtmlNode | None = None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[HtmlNode] = []
        self.text_parts: list[str] = []

    def text_content(self) -> str:
        parts = [*self.text_parts]
        for child in self.children:
            parts.append(child.text_content())
        return _normalize_space(" ".join(parts))

    def select(self, selector: str) -> list[HtmlNode]:
        current: list[HtmlNode] = [self]
        for part in selector.split():
            matched: list[HtmlNode] = []
            for node in current:
                matched.extend(descendant for descendant in node.descendants() if descendant.matches(part))
            current = matched
        return current

    def descendants(self) -> list[HtmlNode]:
        items: list[HtmlNode] = []
        for child in self.children:
            items.append(child)
            items.extend(child.descendants())
        return items

    def matches(self, selector: str) -> bool:
        tag, classes, attr_match = _parse_simple_selector(selector)
        if tag is not None and self.tag != tag:
            return False
        if classes:
            actual = set(self.attrs.get("class", "").split())
            if not set(classes).issubset(actual):
                return False
        if attr_match is not None:
            name, operator, expected = attr_match
            actual_value = self.attrs.get(name)
            if actual_value is None:
                return False
            if operator == "=":
                return actual_value == expected
            if operator == "^=":
                return actual_value.startswith(expected)
            if operator == "*=":
                return expected in actual_value
        return True


class _FlightHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag.lower(), {name.lower(): value or "" for name, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].text_parts.append(data)


def parse_html(html: str) -> HtmlNode:
    parser = _FlightHtmlParser()
    parser.feed(html)
    return parser.root


def _result_rows(root: HtmlNode) -> list[HtmlNode]:
    selectors = ("tr.flight-item-tr", ".flight-item-tr", "[data-testid='flight-item']", ".flight-item")
    rows: list[HtmlNode] = []
    seen: set[int] = set()
    for selector in selectors:
        for row in root.select(selector):
            identity_like = _contains_any(row.text_content(), ("MU", "CA", "CZ", "航班", "票价", "订票"))
            if id(row) not in seen and identity_like:
                rows.append(row)
                seen.add(id(row))
    return rows


def _first_field(row: HtmlNode, selectors: tuple[str, ...]) -> FieldEvidence:
    for selector in selectors:
        for node in row.select(selector):
            visible_text = node.text_content()
            text: str | None = (
                node.attrs.get("aria-label")
                if "aria-label" in selector or visible_text.strip() == ""
                else visible_text
            )
            if text is not None and text.strip():
                return FieldEvidence.observed(text, selector)
    return FieldEvidence.missing(f"unresolved selectors: {', '.join(selectors)}")


def _first_attr(row: HtmlNode, selectors: tuple[str, ...], attr: str) -> tuple[str, str] | None:
    for selector in selectors:
        for node in row.select(selector):
            value = node.attrs.get(attr)
            if value is not None and value.strip():
                return selector, value
    return None


def _first_selector(row: HtmlNode, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        if row.select(selector):
            return selector
    return None


def _texts_for(row: HtmlNode, selectors: tuple[str, ...]) -> list[str]:
    for selector in selectors:
        texts = [node.text_content() for node in row.select(selector) if node.text_content()]
        if len(texts) >= 2:
            return texts
    return []


def _indexed_field(texts: list[str], index: int, selector: str, label: str) -> FieldEvidence:
    if len(texts) > index:
        return FieldEvidence.observed(texts[index], selector, f"{label} by index")
    return FieldEvidence.missing(f"{label} not observed")


def _row_selector(row: HtmlNode) -> str:
    classes = row.attrs.get("class", "")
    if classes:
        return f"{row.tag}.{'.'.join(classes.split())}"
    testid = row.attrs.get("data-testid")
    if testid:
        return f"{row.tag}[data-testid='{testid}']"
    return row.tag


def _terminal_boundary_from_html(html: str) -> tuple[bool, str | None]:
    root = parse_html(html)
    text = root.text_content()
    if _contains_any(text, ("没有更多", "到底了", "footer", "页脚")) or root.select("footer"):
        return True, "footer-or-terminal-text-observed"
    return False, None


def _build_fliggy_search_url(probe_input: ProbeInput) -> str:
    return _FLIGGY_FLIGHT_ENTRY_URL


def _sanitize_source_ref(url: str) -> str:
    parts = urlsplit(url)
    allowed_query = tuple((key, value) for key, value in parse_qsl(parts.query) if key == "tab")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(allowed_query), ""))


async def _submit_public_flight_search(page: Any, probe_input: ProbeInput) -> None:
    async def fill_input(selector: str, value: str) -> None:
        field = page.locator(selector).nth(0)
        await field.click()
        await field.fill(value)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)

    await page.wait_for_selector(".rc-flight-searchbar input#form_depCity")
    await fill_input(".rc-flight-searchbar input#form_depCity", probe_input.origin_text)
    await fill_input(".rc-flight-searchbar input#form_arrCity", probe_input.destination_text)
    await fill_input(".rc-flight-searchbar input#form_depDate", probe_input.departure_date.isoformat())
    await page.locator(".rc-flight-searchbar button.search-button").nth(0).click()


async def _select_result_context_page(
    *,
    context: Any,
    current_page: Any,
    probe_input: ProbeInput,
    page_error_type: type[Exception],
    page_count_before_submit: int,
    wait_ms: int,
) -> tuple[Any, dict[str, Any]]:
    await current_page.wait_for_timeout(wait_ms)
    candidates: list[ResultContextCandidate] = []
    pages = list(context.pages)
    for index, candidate_page in enumerate(pages):
        try:
            title = await candidate_page.title()
            html = await candidate_page.content()
            identity = classify_fliggy_page_identity(url=candidate_page.url, title=title, html=html)
            search_plan_evidence = summarize_search_plan_evidence(title=title, html=html, probe_input=probe_input)
            candidates.append(
                ResultContextCandidate(
                    page_index=index,
                    sanitized_url=_sanitize_source_ref(candidate_page.url),
                    title=title,
                    identity=identity,
                    search_plan_evidence=search_plan_evidence,
                    is_current_page=candidate_page == current_page,
                )
            )
        except page_error_type:
            candidates.append(
                ResultContextCandidate(
                    page_index=index,
                    sanitized_url="<unavailable>",
                    title="<unavailable>",
                    identity=FliggyPageIdentity.UNKNOWN,
                    search_plan_evidence={"origin": False, "destination": False, "departure_date": False},
                    is_current_page=candidate_page == current_page,
                )
            )

    selected = choose_result_context_candidate(tuple(candidates))
    diagnostics = {
        "page_count_before_submit": page_count_before_submit,
        "page_count_after_submit": len(pages),
        "popup_or_new_page_event": len(pages) > 1,
        "candidate_pages": [candidate.to_dict() for candidate in candidates],
        "selected_page_index": selected.page_index if selected is not None else None,
        "selected_page_url": selected.sanitized_url if selected is not None else None,
        "selected_page_identity": selected.identity.value if selected is not None else None,
        "selection_reason": "result_identity_and_route_match" if selected is not None else "no_deterministic_result_context",
    }
    if selected is None:
        return current_page, diagnostics
    return pages[selected.page_index], diagnostics


def _remaining_ms(started: float, deadline_seconds: float) -> int:
    remaining = deadline_seconds - (time.monotonic() - started)
    if remaining <= 0:
        return 1
    return max(1, int(remaining * 1000))


def _parse_simple_selector(selector: str) -> tuple[str | None, tuple[str, ...], tuple[str, str, str] | None]:
    attr_match: tuple[str, str, str] | None = None
    selector_without_attr = selector
    if "[" in selector and selector.endswith("]"):
        selector_without_attr, raw_attr = selector.split("[", 1)
        raw_attr = raw_attr[:-1]
        for operator in ("^=", "*=", "="):
            if operator in raw_attr:
                name, expected = raw_attr.split(operator, 1)
                attr_match = (name.lower(), operator, expected.strip("\"'"))
                break
        if attr_match is None:
            attr_match = (raw_attr.lower(), "*=", "")
    pieces = selector_without_attr.split(".")
    tag = pieces[0].lower() if pieces[0] else None
    classes = tuple(piece for piece in pieces[1:] if piece)
    return tag, classes, attr_match


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(needle.lower() in lowered for needle in needles)


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_sensitive_text(value: str) -> str:
    if _contains_any(value, ("authorization:", "cookie:", "session=", "token=")):
        return "[REDACTED]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
