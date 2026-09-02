"""Probe-local CTRIP browser evidence acquisition.

This module intentionally stays outside the formal FlightProvider adapter path.
It produces provider-side raw evidence for M9-BP5-CTRIP-U1 and does not
construct canonical FlightSegment, Itinerary, Offer, ProviderSearchResult, or
PurchaseAccess objects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

CTRIP_BROWSER_PROBE_VERSION = "m9-bp5-ctrip-u1-browser-offer-probe-v0.1"
CTRIP_PROVIDER_ID = "CTRIP"
CTRIP_ACQUISITION_MODE = "BROWSER"
_CTRIP_FLIGHT_ENTRY_URL = "https://flights.ctrip.com/online/channel/domestic"
_MAX_CAPTURED_RESPONSE_BYTES = 1_500_000
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "passenger",
    "password",
    "phone",
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
    LEVEL1_WAIT = "LEVEL1_WAIT"
    LEVEL1_EXTRACTION = "LEVEL1_EXTRACTION"
    BOOKING_ACTION_DISCOVERY = "BOOKING_ACTION_DISCOVERY"
    BOOKING_ACTION_CLICKED = "BOOKING_ACTION_CLICKED"
    LEVEL2_WAIT = "LEVEL2_WAIT"
    LEVEL2_EXTRACTION = "LEVEL2_EXTRACTION"
    COMPLETED = "COMPLETED"


class DomTraversalAssessment(str, Enum):
    COMPLETE_OBSERVED = "COMPLETE_OBSERVED"
    PARTIAL_OBSERVED = "PARTIAL_OBSERVED"
    UNKNOWN = "UNKNOWN"


class ProviderMarketCompleteness(str, Enum):
    UNKNOWN_NOT_PROVEN = "UNKNOWN_NOT_PROVEN"


@dataclass(frozen=True)
class CtripProbeInput:
    origin_text: str
    destination_text: str
    departure_date: date
    experiment_run_id: str | None = None
    search_plan_id: str | None = None
    execution_id: str | None = None
    overall_deadline_seconds: float = 45.0
    headless: bool = True
    entry_url: str = _CTRIP_FLIGHT_ENTRY_URL

    def __post_init__(self) -> None:
        if not self.origin_text.strip():
            raise ValueError("CtripProbeInput origin_text is required")
        if not self.destination_text.strip():
            raise ValueError("CtripProbeInput destination_text is required")
        if self.overall_deadline_seconds <= 0:
            raise ValueError("CtripProbeInput overall_deadline_seconds must be positive")

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
            entry_url=args.entry_url,
        )


@dataclass(frozen=True)
class FieldEvidence:
    status: str
    raw_value: Any = None
    evidence_path: str | None = None
    diagnostic: str | None = None

    @classmethod
    def observed(cls, raw_value: Any, evidence_path: str, diagnostic: str | None = None) -> Self:
        return cls(
            status="OBSERVED",
            raw_value=_normalize_evidence_value(raw_value),
            evidence_path=evidence_path,
            diagnostic=diagnostic,
        )

    @classmethod
    def missing(cls, diagnostic: str) -> Self:
        return cls(status="MISSING", diagnostic=diagnostic)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "raw_value": self.raw_value,
            "evidence_path": self.evidence_path,
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
class CapturedPayload:
    stage: str
    url: str
    label: str
    payload: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "url": _sanitize_source_ref(self.url),
            "label": self.label,
            "shape": _payload_shape(self.payload),
        }


@dataclass(frozen=True)
class CtripLevel1Evidence:
    evidence_index: int
    itinerary_id: FieldEvidence
    flight_no: FieldEvidence
    market_airline_code: FieldEvidence
    market_airline_name: FieldEvidence
    departure_airport: FieldEvidence
    arrival_airport: FieldEvidence
    departure_datetime: FieldEvidence
    arrival_datetime: FieldEvidence
    terminal: FieldEvidence
    aircraft: FieldEvidence
    duration: FieldEvidence
    stop_transfer_semantics: FieldEvidence
    price_list: FieldEvidence
    raw_payload_path: str
    mapping_feasibility: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_index": self.evidence_index,
            "itinerary_id": self.itinerary_id.to_dict(),
            "flight_no": self.flight_no.to_dict(),
            "market_airline_code": self.market_airline_code.to_dict(),
            "market_airline_name": self.market_airline_name.to_dict(),
            "departure_airport": self.departure_airport.to_dict(),
            "arrival_airport": self.arrival_airport.to_dict(),
            "departure_datetime": self.departure_datetime.to_dict(),
            "arrival_datetime": self.arrival_datetime.to_dict(),
            "terminal": self.terminal.to_dict(),
            "aircraft": self.aircraft.to_dict(),
            "duration": self.duration.to_dict(),
            "stop_transfer_semantics": self.stop_transfer_semantics.to_dict(),
            "price_list": self.price_list.to_dict(),
            "raw_payload_path": self.raw_payload_path,
            "mapping_feasibility": self.mapping_feasibility,
        }


@dataclass(frozen=True)
class CtripLevel2OfferEvidence:
    evidence_index: int
    product_or_fare_identity: FieldEvidence
    cabin: FieldEvidence
    seller_supplier: FieldEvidence
    price: FieldEvidence
    inventory_availability: FieldEvidence
    baggage: FieldEvidence
    refund_change_rules: FieldEvidence
    restrictions: FieldEvidence
    booking_action_identity: FieldEvidence
    purchase_access: FieldEvidence
    raw_payload_path: str
    mapping_feasibility: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_index": self.evidence_index,
            "product_or_fare_identity": self.product_or_fare_identity.to_dict(),
            "cabin": self.cabin.to_dict(),
            "seller_supplier": self.seller_supplier.to_dict(),
            "price": self.price.to_dict(),
            "inventory_availability": self.inventory_availability.to_dict(),
            "baggage": self.baggage.to_dict(),
            "refund_change_rules": self.refund_change_rules.to_dict(),
            "restrictions": self.restrictions.to_dict(),
            "booking_action_identity": self.booking_action_identity.to_dict(),
            "purchase_access": self.purchase_access.to_dict(),
            "raw_payload_path": self.raw_payload_path,
            "mapping_feasibility": self.mapping_feasibility,
        }


@dataclass(frozen=True)
class CtripProbeRunResult:
    provider_identity: str
    acquisition_mode: BrowserAcquisitionMode
    acquired_at: datetime
    experiment_run_id: str | None
    search_scope: dict[str, str]
    search_plan_id: str | None
    execution_id: str | None
    outcome: BrowserProbeOutcome
    observed_level1_count: int
    observed_level2_offer_count: int
    duration_ms: int
    dom_traversal_assessment: DomTraversalAssessment
    provider_market_completeness: ProviderMarketCompleteness
    terminal_boundary_observed: bool
    terminal_boundary_evidence: str | None
    parser_selector_probe_version: str
    sanitized_source_ref: str | None
    level1_evidence: tuple[CtripLevel1Evidence, ...]
    level2_offer_evidence: tuple[CtripLevel2OfferEvidence, ...]
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
                "observed_level1_count": self.observed_level1_count,
                "observed_level2_offer_count": self.observed_level2_offer_count,
                "duration_ms": self.duration_ms,
                "dom_traversal_assessment": self.dom_traversal_assessment.value,
                "provider_market_completeness": self.provider_market_completeness.value,
                "terminal_boundary_observed": self.terminal_boundary_observed,
                "terminal_boundary_evidence": self.terminal_boundary_evidence,
                "parser_selector_probe_version": self.parser_selector_probe_version,
                "sanitized_source_ref": self.sanitized_source_ref,
                "level1_evidence": [item.to_dict() for item in self.level1_evidence],
                "level2_offer_evidence": [item.to_dict() for item in self.level2_offer_evidence],
                "diagnostics": self.diagnostics,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


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
        return self.stages[-1].stage if self.stages else None


def classify_ctrip_result_state(
    *, html: str, level1_count: int = 0, level2_count: int = 0, timed_out: bool = False
) -> BrowserProbeOutcome:
    detector = summarize_ctrip_detector_state(html)
    if detector["access_challenge"]:
        return BrowserProbeOutcome.ACCESS_CHALLENGE
    if detector["login_required"]:
        return BrowserProbeOutcome.LOGIN_REQUIRED
    if detector["provider_error"]:
        return BrowserProbeOutcome.PROVIDER_ERROR
    if detector["explicit_empty"]:
        return BrowserProbeOutcome.SUCCESS_EMPTY
    if level2_count > 0:
        return BrowserProbeOutcome.SUCCESS_COMPLETE
    if level1_count > 0 or detector["result_container"]:
        return BrowserProbeOutcome.SUCCESS_PARTIAL
    if timed_out:
        return BrowserProbeOutcome.TIMEOUT
    return BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def summarize_ctrip_detector_state(html: str) -> dict[str, bool]:
    text = parse_html_text(html).lower()
    return {
        "access_challenge": _contains_any(
            text,
            (
                "captcha",
                "whaleguard block",
                "access denied",
                "access block",
                "验证码",
                "滑块",
                "安全验证",
                "访问验证",
                "拖动滑块",
                "verify you are human",
            ),
        ),
        "login_required": _contains_any(
            text, ("请登录", "登录后", "login required", "sign in", "携程账号")
        ),
        "provider_error": _contains_any(
            text, ("系统繁忙", "服务异常", "出错了", "网络开小差", "provider error", "upstream error")
        ),
        "result_container": _contains_any(text, ("订票", "预订", "航班", "起飞", "到达", "¥", "￥")),
        "explicit_empty": _contains_any(text, ("暂无航班", "无航班", "没有找到", "no flights")),
    }


def extract_level1_evidence_from_payloads(
    payloads: tuple[CapturedPayload, ...] | list[CapturedPayload],
) -> tuple[CtripLevel1Evidence, ...]:
    evidence: list[CtripLevel1Evidence] = []
    for captured in payloads:
        for itinerary_path, itinerary in _iter_dicts_with_any_key(captured.payload, ("itineraryId", "itineraryID")):
            if not _looks_like_itinerary(itinerary):
                continue
            segment_path, segment = _first_nested_dict_with_any_key(
                itinerary, ("flightNo", "marketAirlineCode", "departureAirportCode")
            )
            price_path, price_list = _find_first_value_by_keys(itinerary, ("priceList", "prices", "productList"))
            segment = segment or itinerary
            evidence.append(
                CtripLevel1Evidence(
                    evidence_index=len(evidence) + 1,
                    itinerary_id=_field_from_keys(itinerary, ("itineraryId", "itineraryID"), itinerary_path),
                    flight_no=_field_from_keys(segment, ("flightNo", "flightNumber"), segment_path or itinerary_path),
                    market_airline_code=_field_from_keys(
                        segment, ("marketAirlineCode", "airlineCode"), segment_path or itinerary_path
                    ),
                    market_airline_name=_field_from_keys(
                        segment, ("marketAirlineName", "airlineName"), segment_path or itinerary_path
                    ),
                    departure_airport=_field_from_keys(
                        segment,
                        ("departureAirportCode", "departureAirportName", "departAirportName"),
                        segment_path or itinerary_path,
                    ),
                    arrival_airport=_field_from_keys(
                        segment,
                        ("arrivalAirportCode", "arrivalAirportName", "arriveAirportName"),
                        segment_path or itinerary_path,
                    ),
                    departure_datetime=_field_from_keys(
                        segment, ("departureDateTime", "departureTime", "departDateTime"), segment_path or itinerary_path
                    ),
                    arrival_datetime=_field_from_keys(
                        segment, ("arrivalDateTime", "arrivalTime", "arriveDateTime"), segment_path or itinerary_path
                    ),
                    terminal=_field_from_keys(
                        segment,
                        ("departureTerminal", "arrivalTerminal", "terminal"),
                        segment_path or itinerary_path,
                    ),
                    aircraft=_field_from_keys(segment, ("aircraft", "aircraftName", "aircraftCode"), segment_path or itinerary_path),
                    duration=_field_from_keys(segment, ("duration", "durationMinutes", "flightDuration"), segment_path or itinerary_path),
                    stop_transfer_semantics=_field_from_keys(
                        segment, ("stopInfo", "transferInfo", "stopCount", "transferCount"), segment_path or itinerary_path
                    ),
                    price_list=(
                        FieldEvidence.observed(_summarize_value(price_list), f"{itinerary_path}.{price_path}")
                        if price_path
                        else FieldEvidence.missing("no priceList/productList seam observed")
                    ),
                    raw_payload_path=f"{captured.label}:{itinerary_path}",
                    mapping_feasibility=_level1_mapping_feasibility(segment, price_list),
                )
            )
    return tuple(evidence)


def extract_level2_offer_evidence(
    payloads: tuple[CapturedPayload, ...] | list[CapturedPayload], *, visible_text: str = ""
) -> tuple[CtripLevel2OfferEvidence, ...]:
    evidence: list[CtripLevel2OfferEvidence] = []
    for captured in payloads:
        for offer_path, offer in _iter_offer_like_dicts(captured.payload):
            evidence.append(_offer_evidence_from_dict(len(evidence) + 1, offer, f"{captured.label}:{offer_path}"))
    if not evidence and visible_text.strip():
        maybe = _offer_evidence_from_visible_text(visible_text)
        if maybe is not None:
            evidence.append(maybe)
    return tuple(evidence)


async def run_ctrip_browser_probe(probe_input: CtripProbeInput) -> CtripProbeRunResult:
    """Run the opt-in live CTRIP browser probe without persisting browser session state."""

    started = time.monotonic()
    acquired_at = datetime.now(UTC)
    recorder = _StageRecorder(started)
    captured_payloads: list[CapturedPayload] = []
    level2_capture_enabled = False
    clicked_selector: str | None = None
    html = ""
    visible_level2_text = ""
    outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
    diagnostics: dict[str, Any] = {
        "read_only": True,
        "headless": probe_input.headless,
        "clicked_booking_action": False,
        "booking_action_selector": None,
        "booking_action_label": None,
        "entry_url_strategy": "public_ctrip_flight_entry_or_url",
        "stage_diagnostics": [],
        "last_stage": None,
        "detector_state": summarize_ctrip_detector_state(""),
        "captured_payloads": [],
        "captured_payload_count": 0,
        "level1_payload_count": 0,
        "level2_payload_count": 0,
        "final_sanitized_url": None,
        "purchase_access_behavior": "UNOBSERVED",
        "canonical_mapping_feasibility": {
            "flight_segment": "UNKNOWN",
            "itinerary": "UNKNOWN",
            "offer": "UNKNOWN",
            "purchase_access": "UNKNOWN",
        },
    }

    try:
        recorder.mark(BrowserProbeStage.BROWSER_LAUNCH, "launching Playwright Chromium")
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the opt-in live CTRIP browser probe") from exc

    async def capture_response(response: Any) -> None:
        nonlocal level2_capture_enabled
        label = _classify_response_url(response.url, level2_capture_enabled=level2_capture_enabled)
        if label is None:
            return
        try:
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            content_type = headers.get("content-type", "")
            if "json" not in content_type and "text" not in content_type:
                return
            body = await response.body()
        except PlaywrightError:
            return
        if len(body) > _MAX_CAPTURED_RESPONSE_BYTES:
            return
        payload = _json_from_bytes(body)
        if payload is None:
            return
        captured_payloads.append(
            CapturedPayload(
                stage="LEVEL2" if level2_capture_enabled else "LEVEL1",
                url=response.url,
                label=label,
                payload=payload,
            )
        )

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=probe_input.headless)
            context = await browser.new_context(storage_state=None, locale="zh-CN")
            page = await context.new_page()
            page.set_default_timeout(_remaining_ms(started, probe_input.overall_deadline_seconds))
            page.on("response", lambda response: asyncio.create_task(capture_response(response)))

            recorder.mark(BrowserProbeStage.ENTRY_NAVIGATION, "opening CTRIP public flight page")
            await page.goto(
                _build_ctrip_search_url(probe_input),
                wait_until="domcontentloaded",
                timeout=_remaining_ms(started, probe_input.overall_deadline_seconds),
            )
            await _wait_for_network_capture(page, started, probe_input.overall_deadline_seconds)
            html = await page.content()
            diagnostics["final_sanitized_url"] = _sanitize_source_ref(page.url)
            diagnostics["detector_state"] = summarize_ctrip_detector_state(html)

            recorder.mark(BrowserProbeStage.LEVEL1_WAIT, "waiting for CTRIP batchSearch-like evidence")
            await _attempt_public_search_interaction(page, probe_input)
            await _wait_for_network_capture(page, started, probe_input.overall_deadline_seconds)
            html = await page.content()

            recorder.mark(BrowserProbeStage.LEVEL1_EXTRACTION, "extracting Level-1 raw CTRIP evidence")
            diagnostics["level1_observed_before_booking_click"] = len(
                extract_level1_evidence_from_payloads(tuple(captured_payloads))
            )

            recorder.mark(BrowserProbeStage.BOOKING_ACTION_DISCOVERY, "locating bounded booking action")
            clicked_selector, clicked_label = await _click_first_booking_action(page)
            diagnostics["booking_action_selector"] = clicked_selector
            diagnostics["booking_action_label"] = clicked_label
            if clicked_selector is not None:
                level2_capture_enabled = True
                diagnostics["clicked_booking_action"] = True
                diagnostics["purchase_access_behavior"] = "BOOKING_ACTION_CLICKED_WITHOUT_PURCHASE"
                recorder.mark(BrowserProbeStage.BOOKING_ACTION_CLICKED, "clicked one visible CTRIP booking action")
                await _wait_for_network_capture(page, started, probe_input.overall_deadline_seconds, wait_ms=4000)
                recorder.mark(BrowserProbeStage.LEVEL2_WAIT, "waiting for post-click Level-2 evidence")
                await _wait_for_network_capture(page, started, probe_input.overall_deadline_seconds, wait_ms=3000)
            else:
                diagnostics["purchase_access_behavior"] = "NO_BOOKING_ACTION_OBSERVED"

            html = await page.content()
            visible_level2_text = await _visible_offer_panel_text(page)
            recorder.mark(BrowserProbeStage.LEVEL2_EXTRACTION, "extracting bounded Level-2 offer evidence")
            await context.close()
            await browser.close()
    except PlaywrightTimeoutError:
        outcome = BrowserProbeOutcome.TIMEOUT
        diagnostics["failure_kind"] = "timeout"
    except PlaywrightError as exc:
        outcome = BrowserProbeOutcome.NETWORK_ERROR
        diagnostics["failure_kind"] = "playwright_error"
        diagnostics["failure_message"] = str(exc)

    level1_evidence = extract_level1_evidence_from_payloads(tuple(captured_payloads))
    level2_payloads = tuple(payload for payload in captured_payloads if payload.stage == "LEVEL2")
    level2_evidence = extract_level2_offer_evidence(level2_payloads, visible_text=visible_level2_text)
    terminal_observed, terminal_evidence = _terminal_boundary_from_html(html)
    if outcome is BrowserProbeOutcome.EVIDENCE_INSUFFICIENT:
        outcome = classify_ctrip_result_state(
            html=html,
            level1_count=len(level1_evidence),
            level2_count=len(level2_evidence),
            timed_out=False,
        )

    diagnostics["detector_state"] = summarize_ctrip_detector_state(html)
    diagnostics["captured_payloads"] = [item.to_dict() for item in captured_payloads]
    diagnostics["captured_payload_count"] = len(captured_payloads)
    diagnostics["level1_payload_count"] = len([item for item in captured_payloads if item.stage == "LEVEL1"])
    diagnostics["level2_payload_count"] = len(level2_payloads)
    diagnostics["visible_level2_text_observed"] = bool(visible_level2_text.strip())
    diagnostics["canonical_mapping_feasibility"] = _aggregate_mapping_feasibility(level1_evidence, level2_evidence)
    last_stage = recorder.last_stage()
    if last_stage is not BrowserProbeStage.COMPLETED:
        recorder.mark(BrowserProbeStage.COMPLETED, "probe result completed")
    completed_stage = recorder.last_stage()
    diagnostics["last_stage"] = completed_stage.value if completed_stage is not None else None
    diagnostics["stage_diagnostics"] = [stage.to_dict() for stage in recorder.stages]
    diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)

    return CtripProbeRunResult(
        provider_identity=CTRIP_PROVIDER_ID,
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
        observed_level1_count=len(level1_evidence),
        observed_level2_offer_count=len(level2_evidence),
        duration_ms=int((time.monotonic() - started) * 1000),
        dom_traversal_assessment=(
            DomTraversalAssessment.PARTIAL_OBSERVED
            if len(level1_evidence) or len(level2_evidence)
            else DomTraversalAssessment.UNKNOWN
        ),
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=terminal_observed,
        terminal_boundary_evidence=terminal_evidence,
        parser_selector_probe_version=CTRIP_BROWSER_PROBE_VERSION,
        sanitized_source_ref=_sanitize_source_ref(_build_ctrip_search_url(probe_input)),
        level1_evidence=level1_evidence,
        level2_offer_evidence=level2_evidence,
        diagnostics=diagnostics,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in CTRIP bounded browser offer probe")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--departure-date", required=True)
    parser.add_argument("--experiment-run-id")
    parser.add_argument("--search-plan-id")
    parser.add_argument("--execution-id")
    parser.add_argument("--deadline-seconds", type=float, default=45.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--entry-url", default=_CTRIP_FLIGHT_ENTRY_URL)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    result = asyncio.run(run_ctrip_browser_probe(CtripProbeInput.from_args(args)))
    rendered = result.to_json()
    print(rendered)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return (
        0
        if result.outcome
        in {
            BrowserProbeOutcome.SUCCESS_COMPLETE,
            BrowserProbeOutcome.SUCCESS_PARTIAL,
            BrowserProbeOutcome.SUCCESS_EMPTY,
        }
        else 2
    )


def sanitize_probe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_probe_payload(item)
        return sanitized
    if isinstance(value, list | tuple):
        return [sanitize_probe_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _offer_evidence_from_dict(index: int, offer: dict[str, Any], path: str) -> CtripLevel2OfferEvidence:
    return CtripLevel2OfferEvidence(
        evidence_index=index,
        product_or_fare_identity=_field_from_keys(
            offer, ("productId", "productID", "productName", "fareId", "fareFamily", "priceClass"), path
        ),
        cabin=_field_from_keys(offer, ("cabin", "cabinName", "cabinCode", "seatClass"), path),
        seller_supplier=_field_from_keys(
            offer, ("seller", "sellerName", "supplier", "supplierName", "vendorName", "storeName"), path
        ),
        price=_field_from_keys(
            offer, ("adultPrice", "price", "salePrice", "totalPrice", "amount", "displayPrice"), path
        ),
        inventory_availability=_field_from_keys(
            offer, ("inventory", "availability", "seatCount", "ticketLeft", "remainTicketCount"), path
        ),
        baggage=_field_from_keys(offer, ("baggage", "baggageInfo", "luggage", "luggageInfo"), path),
        refund_change_rules=_field_from_keys(
            offer, ("refund", "refundRule", "changeRule", "refundChange", "refundChangeRule"), path
        ),
        restrictions=_field_from_keys(offer, ("restriction", "restrictions", "limitInfo", "rule", "rules"), path),
        booking_action_identity=_field_from_keys(
            offer, ("bookingId", "bookingCode", "actionId", "selectId", "productId"), path
        ),
        purchase_access=_purchase_access_field(offer, path),
        raw_payload_path=path,
        mapping_feasibility=_level2_mapping_feasibility(offer),
    )


def _offer_evidence_from_visible_text(visible_text: str) -> CtripLevel2OfferEvidence | None:
    normalized = _normalize_space(visible_text)
    if not _contains_any(normalized, ("退", "改", "行李", "舱", "票", "¥", "￥", "预订", "订票")):
        return None
    price_match = re.search(r"[¥￥]\s?\d+(?:\.\d+)?|\bCNY\s?\d+(?:\.\d+)?", normalized)
    return CtripLevel2OfferEvidence(
        evidence_index=1,
        product_or_fare_identity=FieldEvidence.observed(
            normalized[:240], "visible_offer_panel_text", "visible bounded post-click offer panel"
        ),
        cabin=_field_from_text(normalized, ("经济舱", "公务舱", "头等舱", "舱")),
        seller_supplier=_field_from_text(normalized, ("携程", "供应商", "航司", "代理")),
        price=(
            FieldEvidence.observed(price_match.group(0), "visible_offer_panel_text.price")
            if price_match
            else FieldEvidence.missing("no visible price text observed")
        ),
        inventory_availability=_field_from_text(normalized, ("余票", "仅剩", "充足", "有票")),
        baggage=_field_from_text(normalized, ("行李", "托运", "手提")),
        refund_change_rules=_field_from_text(normalized, ("退", "改签", "改期")),
        restrictions=_field_from_text(normalized, ("限制", "不可", "须知", "规则")),
        booking_action_identity=_field_from_text(normalized, ("预订", "订票", "下一步")),
        purchase_access=FieldEvidence.observed(
            "BOOKING_ACTION_CLICKED_WITHOUT_PURCHASE",
            "visible_offer_panel_text",
            "raw PurchaseAccess seam only; no final implementation",
        ),
        raw_payload_path="visible_offer_panel_text",
        mapping_feasibility={
            "canonical_offer": "CANDIDATE_RAW_EVIDENCE_ONLY",
            "purchase_access": "RAW_SEAM_OBSERVED_ONLY",
        },
    )


def _field_from_text(text: str, needles: tuple[str, ...]) -> FieldEvidence:
    for needle in needles:
        idx = text.find(needle)
        if idx >= 0:
            return FieldEvidence.observed(text[max(0, idx - 80) : idx + 160], "visible_offer_panel_text")
    return FieldEvidence.missing(f"none of {', '.join(needles)} observed")


def _purchase_access_field(offer: dict[str, Any], path: str) -> FieldEvidence:
    value_path, value = _find_first_value_by_keys(
        offer, ("bookingUrl", "bookingURL", "deepLink", "actionUrl", "orderUrl", "selectToken", "bookingId")
    )
    if value_path:
        return FieldEvidence.observed(
            _summarize_value(value),
            f"{path}.{value_path}",
            "raw PurchaseAccess seam only; no final implementation",
        )
    return FieldEvidence.missing("no PurchaseAccess seam observed")


def _field_from_keys(source: dict[str, Any], keys: tuple[str, ...], path: str) -> FieldEvidence:
    value_path, value = _find_first_value_by_keys(source, keys)
    if value_path:
        return FieldEvidence.observed(_summarize_value(value), f"{path}.{value_path}")
    return FieldEvidence.missing(f"none of {', '.join(keys)} observed")


def _find_first_value_by_keys(value: Any, keys: tuple[str, ...], prefix: str = "$") -> tuple[str | None, Any]:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] not in (None, "", [], {}):
                return f"{prefix}.{key}", value[key]
        for key, item in value.items():
            found_path, found = _find_first_value_by_keys(item, keys, f"{prefix}.{key}")
            if found_path:
                return found_path, found
    elif isinstance(value, list):
        for index, item in enumerate(value[:30]):
            found_path, found = _find_first_value_by_keys(item, keys, f"{prefix}[{index}]")
            if found_path:
                return found_path, found
    return None, None


def _first_nested_dict_with_any_key(value: Any, keys: tuple[str, ...], prefix: str = "$") -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(value, dict):
        if any(key in value and value[key] not in (None, "", [], {}) for key in keys):
            return prefix, value
        for key, item in value.items():
            found_path, found = _first_nested_dict_with_any_key(item, keys, f"{prefix}.{key}")
            if found is not None:
                return found_path, found
    elif isinstance(value, list):
        for index, item in enumerate(value[:30]):
            found_path, found = _first_nested_dict_with_any_key(item, keys, f"{prefix}[{index}]")
            if found is not None:
                return found_path, found
    return None, None


def _iter_dicts_with_any_key(value: Any, keys: tuple[str, ...], prefix: str = "$"):
    if isinstance(value, dict):
        if any(key in value for key in keys):
            yield prefix, value
        for key, item in value.items():
            yield from _iter_dicts_with_any_key(item, keys, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:80]):
            yield from _iter_dicts_with_any_key(item, keys, f"{prefix}[{index}]")


def _iter_offer_like_dicts(value: Any, prefix: str = "$"):
    if isinstance(value, dict):
        keyset = set(value)
        price_like = bool(keyset & {"adultPrice", "price", "salePrice", "totalPrice", "displayPrice"})
        commercial_like = bool(
            keyset
            & {
                "productId",
                "productName",
                "fareId",
                "fareFamily",
                "cabin",
                "cabinName",
                "supplierName",
                "sellerName",
                "refundRule",
                "changeRule",
                "baggageInfo",
            }
        )
        if price_like and commercial_like:
            yield prefix, value
        for key, item in value.items():
            yield from _iter_offer_like_dicts(item, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value[:80]):
            yield from _iter_offer_like_dicts(item, f"{prefix}[{index}]")


def _looks_like_itinerary(value: dict[str, Any]) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return _contains_any(text, ("flightNo", "flightList", "flightSegments", "priceList", "marketAirline"))


def _level1_mapping_feasibility(segment: dict[str, Any], price_list: Any) -> dict[str, str]:
    flight_no = _find_first_value_by_keys(segment, ("flightNo", "flightNumber"))[0] is not None
    dep = _find_first_value_by_keys(segment, ("departureAirportCode", "departureAirportName", "departAirportName"))[0] is not None
    arr = _find_first_value_by_keys(segment, ("arrivalAirportCode", "arrivalAirportName", "arriveAirportName"))[0] is not None
    times = _find_first_value_by_keys(segment, ("departureDateTime", "departureTime", "departDateTime"))[0] is not None
    return {
        "flight_segment": "STRONG_CANDIDATE" if flight_no and dep and arr and times else "PARTIAL_CANDIDATE",
        "itinerary": "STRONG_CANDIDATE" if flight_no and price_list is not None else "PARTIAL_CANDIDATE",
        "offer": "OFFER_LIKE_PRICE_SEAM_OBSERVED" if price_list is not None else "UNKNOWN",
    }


def _level2_mapping_feasibility(offer: dict[str, Any]) -> dict[str, str]:
    price = _find_first_value_by_keys(offer, ("adultPrice", "price", "salePrice", "totalPrice", "displayPrice"))[0]
    cabin = _find_first_value_by_keys(offer, ("cabin", "cabinName", "cabinCode", "seatClass"))[0]
    seller = _find_first_value_by_keys(offer, ("seller", "sellerName", "supplier", "supplierName", "vendorName"))[0]
    return {
        "canonical_offer": "STRONG_CANDIDATE_RAW_EVIDENCE" if price and cabin else "PARTIAL_CANDIDATE",
        "seller_supplier": "OBSERVED" if seller else "PENDING",
        "purchase_access": "RAW_SEAM_OBSERVED_ONLY"
        if _purchase_access_field(offer, "$").status == "OBSERVED"
        else "PENDING",
    }


def _aggregate_mapping_feasibility(
    level1: tuple[CtripLevel1Evidence, ...], level2: tuple[CtripLevel2OfferEvidence, ...]
) -> dict[str, str]:
    return {
        "flight_segment": "STRONG_CANDIDATE" if any(item.mapping_feasibility["flight_segment"] == "STRONG_CANDIDATE" for item in level1) else "UNKNOWN",
        "itinerary": "STRONG_CANDIDATE" if any(item.mapping_feasibility["itinerary"] == "STRONG_CANDIDATE" for item in level1) else "UNKNOWN",
        "offer": "STRONG_CANDIDATE_RAW_EVIDENCE" if level2 else ("OFFER_LIKE_PRICE_SEAM_OBSERVED" if level1 else "UNKNOWN"),
        "purchase_access": "RAW_SEAM_OBSERVED_ONLY" if any(item.purchase_access.status == "OBSERVED" for item in level2) else "PENDING",
    }


def _classify_response_url(url: str, *, level2_capture_enabled: bool) -> str | None:
    lowered = url.lower()
    if "batchsearch" in lowered:
        return "batchSearch"
    if level2_capture_enabled and any(
        token in lowered for token in ("price", "product", "shopping", "booking", "order", "rule", "recommend")
    ):
        return "post_booking_offer_candidate"
    return None


async def _attempt_public_search_interaction(page: Any, probe_input: CtripProbeInput) -> None:
    # CTRIP pages vary; this is a conservative helper and failure is diagnostic, not fatal.
    for label, value in (("from", probe_input.origin_text), ("to", probe_input.destination_text)):
        selectors = (
            f"input[placeholder*='{label}']",
            "input[placeholder*='出发']" if label == "from" else "input[placeholder*='到达']",
            "input[aria-label*='出发']" if label == "from" else "input[aria-label*='到达']",
        )
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    await locator.click()
                    await locator.fill(value)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(300)
                    break
            except PlaywrightError:
                continue
    for text in ("搜索", "查询", "Search"):
        try:
            button = page.get_by_text(text, exact=False).first
            if await button.count() and await button.is_visible():
                await button.click()
                await page.wait_for_timeout(1000)
                return
        except PlaywrightError:
            continue


async def _click_first_booking_action(page: Any) -> tuple[str | None, str | None]:
    selectors = (
        "button:has-text('订票')",
        "a:has-text('订票')",
        "button:has-text('预订')",
        "a:has-text('预订')",
        "[role='button']:has-text('订票')",
        "[role='button']:has-text('预订')",
        "button:has-text('Book')",
        "a:has-text('Book')",
    )
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = min(await locator.count(), 8)
            for index in range(count):
                item = locator.nth(index)
                if await item.is_visible() and await item.is_enabled():
                    label = _normalize_space(await item.inner_text(timeout=1000))
                    await item.click()
                    return f"{selector} nth={index}", label or selector
        except PlaywrightError:
            continue
    return None, None


async def _visible_offer_panel_text(page: Any) -> str:
    selectors = (
        "[class*='price']",
        "[class*='product']",
        "[class*='cabin']",
        "[class*='booking']",
        "[class*='rule']",
        "[class*='refund']",
        "[class*='baggage']",
        "body",
    )
    parts: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                text = await locator.inner_text(timeout=1200)
                if text:
                    parts.append(text[:2000])
        except PlaywrightError:
            continue
    return _normalize_space(" ".join(parts))[:6000]


async def _wait_for_network_capture(
    page: Any, started: float, deadline_seconds: float, *, wait_ms: int = 2500
) -> None:
    await page.wait_for_timeout(min(wait_ms, max(250, _remaining_ms(started, deadline_seconds) - 250)))


def _build_ctrip_search_url(probe_input: CtripProbeInput) -> str:
    return probe_input.entry_url


def _sanitize_source_ref(url: str) -> str:
    parts = urlsplit(url)
    allowed_query = tuple(
        (key, value)
        for key, value in parse_qsl(parts.query)
        if key.lower() in {"dcity", "acity", "ddate", "triptype"}
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(allowed_query), ""))


def _json_from_bytes(body: bytes) -> Any | None:
    try:
        text = body.decode("utf-8", errors="replace")
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _payload_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _payload_shape(item) for key, item in list(value.items())[:12]}
    if isinstance(value, list):
        return [f"{len(value)} items", _payload_shape(value[0]) if value else None]
    return type(value).__name__


def _summarize_value(value: Any) -> Any:
    if isinstance(value, list):
        return {"count": len(value), "sample": _summarize_value(value[0]) if value else None}
    if isinstance(value, dict):
        return {str(key): _summarize_value(item) for key, item in list(value.items())[:12]}
    return value


def _normalize_evidence_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_space(value)
    if isinstance(value, dict | list | tuple):
        return sanitize_probe_payload(value)
    return value


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def parse_html_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return _normalize_space(" ".join(parser.parts))


def _terminal_boundary_from_html(html: str) -> tuple[bool, str | None]:
    text = parse_html_text(html)
    if _contains_any(text, ("没有更多", "到底了", "footer", "页脚")) or "<footer" in html.lower():
        return True, "footer-or-terminal-text-observed"
    return False, None


def _remaining_ms(started: float, deadline_seconds: float) -> int:
    remaining = deadline_seconds - (time.monotonic() - started)
    return max(1, int(max(0.001, remaining) * 1000))


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(needle.lower() in lowered for needle in needles)


def _normalize_space(value: str) -> str:
    return " ".join(value.split())


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_sensitive_text(value: str) -> str:
    if _contains_any(value, ("authorization:", "cookie:", "session=", "token=", "passenger")):
        return "[REDACTED]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
