"""Probe-local FLIGGY browser evidence acquisition.

This module intentionally stays outside the formal FlightProvider adapter path.
It produces provider-side raw evidence for M9-BP5-U1 and does not construct
canonical FlightSegment, Itinerary, Offer, or ProviderSearchResult objects.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError

FLIGGY_BROWSER_PROBE_VERSION = "m9-bp5-u1-fliggy-browser-probe-v0.1"
FLIGGY_PROVIDER_ID = "FLIGGY"
FLIGGY_ACQUISITION_MODE = "BROWSER"
_FLIGGY_FLIGHT_ENTRY_URL = "https://www.fliggy.com/?tab=flight"
_FLIGGY_DEFAULT_ORIGIN_TEXT = "北京"
_FLIGGY_DEFAULT_DESTINATION_TEXT = "杭州"

_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "full_dom",
    "password",
    "secret",
    "session",
    "token",
)

_FLIGGY_DESTINATION_INPUT_SELECTOR = ".rc-flight-searchbar input#form_arrCity"
_FLIGGY_PUBLIC_DESTINATION_TARGET_SELECTOR = (
    ".rc-flight-searchbar input#form_arrCity,"
    ".rc-flight-searchbar input[name='arrCity'],"
    ".rc-flight-searchbar input[placeholder*='到达'],"
    ".rc-flight-searchbar [aria-label*='到达城市']"
)
_FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS = 5
_FLIGGY_DESTINATION_SUGGESTION_WAIT_MS = 300
_FLIGGY_DESTINATION_CITY_SELECTOR_SURFACES = (
    ".next-overlay-wrapper .citys-flight",
    ".next-overlay-wrapper .city-list",
    ".next-overlay-wrapper .J_CityList",
    ".next-overlay-wrapper [class*='city-list']",
    ".next-overlay-wrapper [class*='cityList']",
)
_FLIGGY_DESTINATION_SUGGESTION_SELECTORS = (
    ".next-overlay-wrapper [data-city]",
    ".next-overlay-wrapper [class*='city-item']",
    ".next-overlay-wrapper .citys-flight li",
    ".next-overlay-wrapper .city-list li",
    ".next-overlay-wrapper .J_CityList li",
    ".next-overlay-wrapper [class*='city-list'] li",
    ".next-overlay-wrapper [class*='cityList'] li",
    ".next-overlay-wrapper [role='option']",
    ".next-overlay-wrapper .next-menu-item",
    ".next-overlay-wrapper li",
    ".rc-flight-searchbar [role='option']",
    ".rc-flight-searchbar .next-menu-item",
    ".rc-flight-searchbar .city-item",
    ".rc-flight-searchbar .suggestion-item",
    ".rc-flight-searchbar .autocomplete-item",
    ".citys-flight li",
    ".city-list li",
    ".J_CityList li",
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


class Level2ExpansionOutcome(str, Enum):
    SUCCESS_EXPANDED = "SUCCESS_EXPANDED"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    ACTION_NOT_AVAILABLE = "ACTION_NOT_AVAILABLE"
    ACCESS_CHALLENGE = "ACCESS_CHALLENGE"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class BrowserProbeStage(str, Enum):
    BROWSER_LAUNCH = "BROWSER_LAUNCH"
    ENTRY_NAVIGATION = "ENTRY_NAVIGATION"
    SEARCH_INPUT_READINESS = "SEARCH_INPUT_READINESS"
    SEARCH_INPUT = "SEARCH_INPUT"
    SEARCH_SUBMIT = "SEARCH_SUBMIT"
    RESULT_TRANSITION = "RESULT_TRANSITION"
    RESULT_READINESS = "RESULT_READINESS"
    LEVEL1_DISCOVERY = "LEVEL1_DISCOVERY"
    TARGET_SELECTION = "TARGET_SELECTION"
    BOOKING_ACTION_DISCOVERY = "BOOKING_ACTION_DISCOVERY"
    BOOKING_ACTION = "BOOKING_ACTION"
    LEVEL2_READINESS = "LEVEL2_READINESS"
    LEVEL2_EXTRACTION = "LEVEL2_EXTRACTION"
    SANITIZATION = "SANITIZATION"
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


class PublicQueryClassification(str, Enum):
    REQUESTED_QUERY = "REQUESTED_QUERY"
    DEFAULT_QUERY = "DEFAULT_QUERY"
    STALE_QUERY = "STALE_QUERY"
    PARTIAL_QUERY = "PARTIAL_QUERY"
    UNKNOWN_QUERY = "UNKNOWN_QUERY"


class DestinationSuggestionSurfaceClassification(str, Enum):
    NO_SURFACE = "NO_SURFACE"
    SURFACE_DELAYED = "SURFACE_DELAYED"
    SURFACE_TRANSIENT = "SURFACE_TRANSIENT"
    SURFACE_READY_NO_MATCH = "SURFACE_READY_NO_MATCH"
    MATCH_VISIBLE_NOT_SELECTABLE = "MATCH_VISIBLE_NOT_SELECTABLE"
    COMMITTABLE_MATCH_FOUND = "COMMITTABLE_MATCH_FOUND"
    COMMIT_EVIDENCE_OBTAINED = "COMMIT_EVIDENCE_OBTAINED"
    MODE_DEPENDENT_SURFACE = "MODE_DEPENDENT_SURFACE"
    UNKNOWN_SURFACE = "UNKNOWN_SURFACE"


class PublicDestinationActivationClass(str, Enum):
    OPENED = "OPENED"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TARGET_REPLACED = "TARGET_REPLACED"
    CLICK_NO_ACTIVATION = "CLICK_NO_ACTIVATION"
    DELAYED_ACTIVATION = "DELAYED_ACTIVATION"
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
    planned_observation: int | None = None
    evidence_output_path: str | None = None
    headed_observation_pause_seconds: float = 0.0
    destination_activation_probe: str | None = None
    destination_hit_target_probe: str | None = None
    human_hit_region_id: str | None = None
    human_hit_point_x: float | None = None
    human_hit_point_y: float | None = None

    def __post_init__(self) -> None:
        if self.origin_text.strip() == "":
            raise ValueError("ProbeInput origin_text is required")
        if self.destination_text.strip() == "":
            raise ValueError("ProbeInput destination_text is required")
        if self.overall_deadline_seconds <= 0:
            raise ValueError("ProbeInput overall_deadline_seconds must be positive")
        if self.planned_observation is not None and not 1 <= self.planned_observation <= 6:
            raise ValueError("ProbeInput planned_observation must be between 1 and 6")
        if self.headed_observation_pause_seconds < 0:
            raise ValueError("ProbeInput headed_observation_pause_seconds must be non-negative")
        if self.destination_activation_probe not in {None, "current_click", "pointer_mouse"}:
            raise ValueError("ProbeInput destination_activation_probe must be current_click or pointer_mouse")
        if self.destination_activation_probe is not None and self.planned_observation is None:
            raise ValueError("ProbeInput destination_activation_probe requires a planned observation")
        if self.destination_hit_target_probe not in {None, "visual_map", "differential_click"}:
            raise ValueError("ProbeInput destination_hit_target_probe must be visual_map or differential_click")
        if self.destination_activation_probe is not None and self.destination_hit_target_probe is not None:
            raise ValueError("ProbeInput diagnostic probe modes are mutually exclusive")
        if self.destination_hit_target_probe is not None and self.planned_observation is None:
            raise ValueError("ProbeInput destination_hit_target_probe requires a planned observation")
        if self.destination_hit_target_probe == "visual_map" and self.headless:
            raise ValueError("ProbeInput visual_map requires headed mode")
        point_supplied = self.human_hit_point_x is not None or self.human_hit_point_y is not None
        if point_supplied and (self.human_hit_point_x is None or self.human_hit_point_y is None):
            raise ValueError("ProbeInput human hit point requires both x and y")
        if self.human_hit_region_id is not None and point_supplied:
            raise ValueError("ProbeInput human hit region and point are mutually exclusive")
        if self.destination_hit_target_probe == "differential_click" and not (
            self.human_hit_region_id is not None or point_supplied
        ):
            raise ValueError("ProbeInput differential_click requires a human-supplied region or point")
        if self.destination_hit_target_probe != "differential_click" and (
            self.human_hit_region_id is not None or point_supplied
        ):
            raise ValueError("ProbeInput human hit evidence requires differential_click")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        evidence_output = args.evidence_output_path or args.output_json
        return cls(
            origin_text=args.origin,
            destination_text=args.destination,
            departure_date=date.fromisoformat(args.departure_date),
            experiment_run_id=args.experiment_run_id,
            search_plan_id=args.search_plan_id,
            execution_id=args.execution_id,
            overall_deadline_seconds=args.deadline_seconds,
            headless=not args.headed,
            planned_observation=args.planned_observation,
            evidence_output_path=str(evidence_output.resolve()) if evidence_output is not None else None,
            headed_observation_pause_seconds=args.headed_observation_pause_seconds,
            destination_activation_probe=args.destination_activation_probe,
            destination_hit_target_probe=args.destination_hit_target_probe,
            human_hit_region_id=args.human_hit_region_id,
            human_hit_point_x=args.human_hit_point_x,
            human_hit_point_y=args.human_hit_point_y,
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
    search_plan_evidence: dict[str, Any]
    is_current_page: bool
    transient_id: str | None = None
    creation_order: int | None = None
    opener_relation: str = "unknown"
    alive: bool = True
    document_ready_state: str | None = None

    def score(self) -> int:
        score = 0
        if self.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE:
            score += 8
        if self.search_plan_evidence.get("origin"):
            score += 2
        if self.search_plan_evidence.get("destination"):
            score += 2
        if self.search_plan_evidence.get("departure_date"):
            score += 2
        if self.search_plan_evidence.get("result_surface"):
            score += 2
        if "flight_search_result" in self.sanitized_url:
            score += 1
        if not self.is_current_page:
            score += 1
        return score

    def route_matches(self) -> bool:
        return (
            self.search_plan_evidence.get("origin") is True
            and self.search_plan_evidence.get("destination") is True
            and self.search_plan_evidence.get("route_conflict") is not True
        )

    def date_matches(self) -> bool:
        return (
            self.search_plan_evidence.get("departure_date") is True
            and self.search_plan_evidence.get("date_conflict") is not True
        )

    def result_surface_matches(self) -> bool:
        return self.search_plan_evidence.get("result_surface") is True

    def context_matches(self) -> bool:
        return self.route_matches() and self.date_matches() and self.result_surface_matches()

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
            "transient_id": self.transient_id or f"context-{self.page_index}",
            "creation_order": self.creation_order if self.creation_order is not None else self.page_index,
            "opener_relation": self.opener_relation,
            "alive": self.alive,
            "closed": not self.alive,
            "document_ready_state": self.document_ready_state,
            "url_class": _url_class(self.sanitized_url),
            "result_like_surface": self.search_plan_evidence.get("result_surface_present") is True,
            "route_match": self.search_plan_evidence.get("route_match"),
            "observed_route_text": self.search_plan_evidence.get("observed_route_text"),
            "date_match": self.search_plan_evidence.get("date_match"),
            "observed_date_text": self.search_plan_evidence.get("observed_date_text"),
            "observed_date_source": self.search_plan_evidence.get("observed_date_source"),
            "score": self.score(),
        }


@dataclass(frozen=True)
class ControlReadiness:
    count: int
    visible: bool
    enabled: bool
    editable: bool

    def is_text_input_ready(self) -> bool:
        return self.count > 0 and self.visible and self.enabled and self.editable

    def is_button_ready(self) -> bool:
        return self.count > 0 and self.visible and self.enabled

    def to_dict(self) -> dict[str, bool | int]:
        return {
            "count": self.count,
            "visible": self.visible,
            "enabled": self.enabled,
            "editable": self.editable,
        }


@dataclass(frozen=True)
class SearchFormReadiness:
    origin: ControlReadiness
    destination: ControlReadiness
    date: ControlReadiness
    search_button: ControlReadiness
    iframe_count: int
    overlay_evidence: tuple[str, ...]

    def is_ready(self) -> bool:
        return (
            self.origin.is_text_input_ready()
            and self.destination.is_text_input_ready()
            and self.date.is_text_input_ready()
            and self.search_button.is_button_ready()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.to_dict(),
            "destination": self.destination.to_dict(),
            "date": self.date.to_dict(),
            "search_button": self.search_button.to_dict(),
            "iframe_count": self.iframe_count,
            "overlay_evidence": list(self.overlay_evidence),
            "form_ready": self.is_ready(),
        }


@dataclass(frozen=True)
class PublicSearchQueryState:
    requested_origin: str
    requested_destination: str
    requested_departure_date: str
    form_origin_readback: str | None
    form_destination_readback: str | None
    form_date_readback: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "requested_origin": self.requested_origin,
            "requested_destination": self.requested_destination,
            "requested_departure_date": self.requested_departure_date,
            "form_origin_readback": self.form_origin_readback,
            "form_destination_readback": self.form_destination_readback,
            "form_date_readback": self.form_date_readback,
        }


@dataclass(frozen=True)
class PreSubmitQueryVerification:
    pre_submit_route_match: bool | str
    pre_submit_date_match: bool | str
    submit_allowed: bool
    failure_taxonomy: str | None
    query_state_decision: str

    def to_dict(self) -> dict[str, bool | str | None]:
        return {
            "pre_submit_route_match": self.pre_submit_route_match,
            "pre_submit_date_match": self.pre_submit_date_match,
            "submit_allowed": self.submit_allowed,
            "failure_taxonomy": self.failure_taxonomy,
            "query_state_decision": self.query_state_decision,
        }


@dataclass(frozen=True)
class DestinationSuggestionCandidate:
    selector: str
    index: int
    label: str
    selectable: bool | str = "unknown"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "selector": self.selector,
            "index": self.index,
            "label": self.label,
            "selectable": self.selectable,
        }


@dataclass(frozen=True)
class DestinationSuggestionSnapshot:
    elapsed_ms: int
    candidates: tuple[DestinationSuggestionCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_ms": self.elapsed_ms,
            "surface_present": bool(self.candidates),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class DestinationOptionResolution:
    selected_candidate: DestinationSuggestionCandidate | None
    failure_taxonomy: str | None


@dataclass(frozen=True)
class DestinationCommitmentResult:
    requested_destination: str
    destination_control_ready: bool
    typed_destination: str | None
    suggestion_surface_present: bool
    suggestion_candidate_count: int
    candidate_labels: tuple[str, ...]
    selected_candidate_label: str | None
    selection_method: str
    commit_readback: str | None
    destination_match: bool | str
    commitment_status: str
    failure_taxonomy: str | None
    destination_stability_diagnostics: dict[str, Any] = field(default_factory=dict)
    destination_suggestion_diagnostics: dict[str, Any] = field(default_factory=dict)
    destination_activation_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_destination": self.requested_destination,
            "destination_control_ready": self.destination_control_ready,
            "typed_destination": self.typed_destination,
            "suggestion_surface_present": self.suggestion_surface_present,
            "suggestion_candidate_count": self.suggestion_candidate_count,
            "candidate_labels": list(self.candidate_labels),
            "selected_candidate_label": self.selected_candidate_label,
            "selection_method": self.selection_method,
            "commit_readback": self.commit_readback,
            "destination_match": self.destination_match,
            "commitment_status": self.commitment_status,
            "failure_taxonomy": self.failure_taxonomy,
            "destination_stability_diagnostics": self.destination_stability_diagnostics,
            "destination_suggestion_diagnostics": self.destination_suggestion_diagnostics,
            "destination_activation_diagnostics": self.destination_activation_diagnostics,
        }


@dataclass(frozen=True)
class DestinationInputWriteResult:
    input_text_after_write: str | None
    readback_sequence: tuple[str | None, ...]
    extension_used: bool
    extension_reason: str


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
class Level2ExpansionTarget:
    parent_level1_ref: str
    level1_evidence_index: int | None = None
    provider_row_ref: str | None = None

    def __post_init__(self) -> None:
        if self.parent_level1_ref.strip() == "":
            raise ValueError("Level2ExpansionTarget parent_level1_ref is required")
        if self.level1_evidence_index is not None and self.level1_evidence_index <= 0:
            raise ValueError("Level2ExpansionTarget level1_evidence_index must be positive")

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "parent_level1_ref": self.parent_level1_ref,
            "level1_evidence_index": self.level1_evidence_index,
            "provider_row_ref": self.provider_row_ref,
        }


@dataclass(frozen=True)
class Level2ExpansionBounds:
    max_offer_rows: int = 20
    max_wait_ms: int = 5000
    max_retries: int = 1

    def __post_init__(self) -> None:
        if self.max_offer_rows <= 0:
            raise ValueError("Level2ExpansionBounds max_offer_rows must be positive")
        if self.max_wait_ms <= 0:
            raise ValueError("Level2ExpansionBounds max_wait_ms must be positive")
        if self.max_retries < 0:
            raise ValueError("Level2ExpansionBounds max_retries must not be negative")

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "max_offer_rows": self.max_offer_rows,
            "max_wait_ms": self.max_wait_ms,
            "max_retries": self.max_retries,
            "bounded_expansion": True,
        }


@dataclass(frozen=True)
class FliggyLevel2OfferRowEvidence:
    offer_row_ref: str
    sequence: int
    parent_level1_ref: str
    raw_seller_text: FieldEvidence
    raw_seller_marker_text: FieldEvidence
    raw_price_text: FieldEvidence
    price_amount: int | None
    price_currency: str | None
    raw_cabin_product_text: FieldEvidence
    raw_baggage_text: FieldEvidence
    raw_refund_change_rule_text: FieldEvidence
    raw_availability_text: FieldEvidence
    action_evidence: FieldEvidence
    row_diagnostic: dict[str, str | int | bool | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_row_ref": self.offer_row_ref,
            "sequence": self.sequence,
            "parent_level1_ref": self.parent_level1_ref,
            "raw_seller_text": self.raw_seller_text.to_dict(),
            "raw_seller_marker_text": self.raw_seller_marker_text.to_dict(),
            "raw_price_text": self.raw_price_text.to_dict(),
            "price_amount": self.price_amount,
            "price_currency": self.price_currency,
            "raw_cabin_product_text": self.raw_cabin_product_text.to_dict(),
            "raw_baggage_text": self.raw_baggage_text.to_dict(),
            "raw_refund_change_rule_text": self.raw_refund_change_rule_text.to_dict(),
            "raw_availability_text": self.raw_availability_text.to_dict(),
            "action_evidence": self.action_evidence.to_dict(),
            "row_diagnostic": self.row_diagnostic,
        }


@dataclass(frozen=True)
class Level2ExpansionResult:
    provider_identity: str
    acquisition_mode: BrowserAcquisitionMode
    acquired_at: datetime
    experiment_run_id: str | None
    search_plan_id: str | None
    execution_id: str | None
    target: Level2ExpansionTarget
    outcome: Level2ExpansionOutcome
    observed_offer_row_count: int
    duration_ms: int
    sanitized_source_ref: str | None
    parser_selector_probe_version: str
    bounds: Level2ExpansionBounds
    offer_rows: tuple[FliggyLevel2OfferRowEvidence, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return sanitize_probe_payload(
            {
                "provider_identity": self.provider_identity,
                "acquisition_mode": self.acquisition_mode.value,
                "acquired_at": self.acquired_at.isoformat(),
                "experiment_run_id": self.experiment_run_id,
                "search_plan_id": self.search_plan_id,
                "execution_id": self.execution_id,
                "target": self.target.to_dict(),
                "parent_level1_ref": self.target.parent_level1_ref,
                "outcome": self.outcome.value,
                "observed_offer_row_count": self.observed_offer_row_count,
                "duration_ms": self.duration_ms,
                "sanitized_source_ref": self.sanitized_source_ref,
                "parser_selector_probe_version": self.parser_selector_probe_version,
                "bounds": self.bounds.to_dict(),
                "offer_rows": [item.to_dict() for item in self.offer_rows],
                "diagnostics": self.diagnostics,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


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
        "access_challenge": _active_access_challenge_detected(page_text),
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
    if _active_access_challenge_detected(lowered_text):
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


def _active_access_challenge_detected(page_text: str) -> bool:
    lowered = page_text.lower()
    if _contains_any(lowered, ("拖动滑块", "安全验证", "访问验证", "人机验证", "完成验证", "security check")):
        return True
    if "captcha" in lowered and _contains_any(lowered, ("complete", "verify", "challenge", "blocked")):
        return True
    if "验证码" not in lowered:
        return False
    if _contains_any(lowered, ("验证码登录", "登录验证码", "短信验证码", "获取验证码")):
        return False
    return _contains_any(lowered, ("输入验证码", "请完成", "校验", "验证后", "验证通过"))


def summarize_search_plan_evidence(*, title: str, html: str, probe_input: ProbeInput, url: str = "") -> dict[str, Any]:
    root = parse_html(html)
    page_text = root.text_content()
    text = " ".join((title, page_text, _decoded_url_evidence(url)))
    date_diagnostics = _date_identity_diagnostics(title=title, page_text=page_text, url=url, probe_input=probe_input)
    compact_route = f"{probe_input.origin_text}到{probe_input.destination_text}"
    detector_state = summarize_detector_state(html)
    result_surface = (
        detector_state["result_container"] is True
        or detector_state["explicit_empty"] is True
        or _contains_any(text, ("航班查询", "特价机票", "最低价格", "机票价格", "起飞", "到达", "经济舱", "暂无航班"))
        or _contains_any(urlsplit(url).path, ("flight_search_result", "trip_flight_search"))
    )
    observed_routes = _observed_route_pairs(text)
    observed_route = observed_routes[0] if observed_routes else None
    route_conflict = _route_conflicts_with_query(text, probe_input)
    date_conflict = _date_conflicts_with_query(text, probe_input)
    origin_match = probe_input.origin_text in text or compact_route in text
    destination_match = probe_input.destination_text in text or compact_route in text
    route_match = origin_match and destination_match and not route_conflict
    date_match = date_diagnostics["date_match"] is True and not date_conflict
    query_identity_decision = "match" if route_match and date_match and result_surface else "insufficient"
    return {
        "origin": origin_match,
        "destination": destination_match,
        "observed_route_origin": observed_route[0] if observed_route is not None else None,
        "observed_route_destination": observed_route[1] if observed_route is not None else None,
        "observed_route_text": f"{observed_route[0]}到{observed_route[1]}" if observed_route is not None else None,
        "departure_date": date_match,
        "result_surface": result_surface,
        "explicit_empty": detector_state["explicit_empty"] is True,
        "route_conflict": route_conflict,
        "date_conflict": date_conflict,
        "date_marker_candidates_count": date_diagnostics["date_marker_candidates_count"],
        "selected_date_marker_class": date_diagnostics["selected_date_marker_class"],
        "observed_date_text": date_diagnostics["observed_date_text"],
        "observed_date_source": date_diagnostics["observed_date_source"],
        "date_parse_status": date_diagnostics["date_parse_status"],
        "submitted_date": date_diagnostics["submitted_date"],
        "normalized_expected_date": date_diagnostics["normalized_expected_date"],
        "normalized_observed_date": date_diagnostics["normalized_observed_date"],
        "date_match": date_diagnostics["date_match"],
        "route_match": route_match,
        "result_surface_present": result_surface,
        "query_identity_decision": query_identity_decision,
        "mismatch_dimension": _candidate_mismatch_dimension(route_match=route_match, date_match=date_match),
        "timing_state": "ready",
    }


def choose_result_context_candidate(
    candidates: tuple[ResultContextCandidate, ...],
) -> ResultContextCandidate | None:
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE and candidate.context_matches()
    )
    if not eligible:
        return None
    ranked = sorted(eligible, key=lambda candidate: candidate.score(), reverse=True)
    best = ranked[0]
    tied = tuple(candidate for candidate in ranked if candidate.score() == best.score())
    if len({candidate.signature() for candidate in tied}) > 1:
        return None
    return best


def _decoded_url_evidence(url: str) -> str:
    parts = urlsplit(url)
    values = [parts.path, parts.query, parts.fragment]
    for key, value in parse_qsl(parts.query):
        values.extend((key, value))
    for key, value in parse_qsl(parts.fragment):
        values.extend((key, value))
    return " ".join(values)


def _date_identity_diagnostics(*, title: str, page_text: str, url: str, probe_input: ProbeInput) -> dict[str, Any]:
    candidates = _date_marker_candidates(title=title, page_text=page_text, url=url, probe_input=probe_input)
    expected = probe_input.departure_date.isoformat()
    parsed_values = tuple(candidate for candidate in candidates if candidate["normalized_observed_date"] is not None)
    matching_values = tuple(candidate for candidate in parsed_values if candidate["normalized_observed_date"] == expected)
    selected = matching_values[0] if matching_values else (parsed_values[0] if parsed_values else (candidates[0] if candidates else None))
    normalized_values = {str(candidate["normalized_observed_date"]) for candidate in parsed_values}
    if selected is None:
        parse_status = "absent"
        date_match: bool | str = "insufficient"
    elif not parsed_values:
        parse_status = "unparsable"
        date_match = "insufficient"
    elif len(normalized_values) > 1:
        parse_status = "ambiguous"
        date_match = expected in normalized_values
    else:
        parse_status = "parsed"
        date_match = expected in normalized_values
    return {
        "submitted_date": expected,
        "date_marker_candidates_count": min(len(candidates), 20),
        "selected_date_marker_class": selected["class"] if selected is not None else None,
        "observed_date_text": selected["text"] if selected is not None else None,
        "observed_date_source": selected["source"] if selected is not None else "none",
        "date_parse_status": parse_status,
        "normalized_expected_date": expected,
        "normalized_observed_date": selected["normalized_observed_date"] if selected is not None else None,
        "date_match": date_match,
    }


def _date_marker_candidates(*, title: str, page_text: str, url: str, probe_input: ProbeInput) -> list[dict[str, str | None]]:
    candidates: list[dict[str, str | None]] = []

    def append_candidate(raw_text: str, source: str, marker_class: str) -> None:
        text = _truncate_diagnostic_text(raw_text)
        if not text:
            return
        candidates.append(
            {
                "text": text,
                "source": source,
                "class": marker_class,
                "normalized_observed_date": _normalize_date_marker(text, probe_input),
            }
        )

    for source, value in (("title", title), ("visible_text", page_text)):
        for match in re.finditer(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", value):
            append_candidate(match.group(0), source, "full_date")
        for match in re.finditer(r"(?<!\d)\d{1,2}月\d{1,2}日", value):
            append_candidate(match.group(0), source, "month_day")
    parts = urlsplit(url)
    for key, value in (*parse_qsl(parts.query), *parse_qsl(parts.fragment)):
        if _contains_any(key, ("date", "day", "dep")):
            append_candidate(value, "page_state", f"url_param:{_truncate_diagnostic_text(key, limit=24)}")
    return candidates[:20]


def _normalize_date_marker(value: str, probe_input: ProbeInput) -> str | None:
    full = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value)
    if full:
        year, month, day = (int(part) for part in full.groups())
        return _date_or_none(year, month, day)
    month_day = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", value)
    if month_day:
        month, day = (int(part) for part in month_day.groups())
        return _date_or_none(probe_input.departure_date.year, month, day)
    compact = re.search(r"(20\d{2})(\d{2})(\d{2})", value)
    if compact:
        year, month, day = (int(part) for part in compact.groups())
        return _date_or_none(year, month, day)
    return None


def _date_or_none(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _candidate_mismatch_dimension(*, route_match: bool, date_match: bool) -> str:
    if route_match and date_match:
        return "none"
    if route_match and not date_match:
        return "date"
    if not route_match and date_match:
        return "route"
    return "both"


def _truncate_diagnostic_text(value: str, *, limit: int = 48) -> str:
    normalized = _normalize_space(_redact_sensitive_text(value))
    return normalized[:limit]


def _route_conflicts_with_query(text: str, probe_input: ProbeInput) -> bool:
    expected_route = f"{probe_input.origin_text}到{probe_input.destination_text}"
    observed_routes = set(_observed_route_pairs(text))
    return any(f"{origin}到{destination}" != expected_route for origin, destination in observed_routes)


def _observed_route_pairs(text: str) -> tuple[tuple[str, str], ...]:
    return tuple(re.findall(r"([\u4e00-\u9fff]{2,8}?)到([\u4e00-\u9fff]{2,8}?)(?:机票|航班|特价|预订|查询|$)", text))


def _date_conflicts_with_query(text: str, probe_input: ProbeInput) -> bool:
    expected = probe_input.departure_date
    for year, month, day in re.findall(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text):
        if (int(year), int(month), int(day)) != (expected.year, expected.month, expected.day):
            return True
    for month, day in re.findall(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text):
        if (int(month), int(day)) != (expected.month, expected.day):
            return True
    return False


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


def extract_level2_offer_evidence(
    html: str,
    *,
    parent_level1_ref: str,
    bounds: Level2ExpansionBounds | None = None,
) -> tuple[FliggyLevel2OfferRowEvidence, ...]:
    target_bounds = bounds or Level2ExpansionBounds()
    root = parse_html(html)
    evidence: list[FliggyLevel2OfferRowEvidence] = []
    for sequence, row in enumerate(_level2_offer_rows(root)[: target_bounds.max_offer_rows], start=1):
        raw_price_text = _first_field(
            row,
            (
                "[data-testid='offer-price']",
                ".offer-price",
                ".J_OfferPrice",
                ".price",
                "[aria-label*='价格']",
                "[aria-label*='票价']",
            ),
        )
        action_selector = _first_selector(
            row,
            (
                "[data-testid='select-offer-btn']",
                "button[aria-label*='预订']",
                "button[aria-label*='订']",
                ".book-button",
                "button",
            ),
        )
        evidence.append(
            FliggyLevel2OfferRowEvidence(
                offer_row_ref=_level2_offer_row_ref(row, parent_level1_ref=parent_level1_ref, sequence=sequence),
                sequence=sequence,
                parent_level1_ref=parent_level1_ref,
                raw_seller_text=_first_field(
                    row,
                    (
                        "[data-testid='seller-name']",
                        ".seller-name",
                        ".shop-name",
                        ".seller",
                        "[aria-label*='供应商']",
                        "[aria-label*='商家']",
                    ),
                ),
                raw_seller_marker_text=_first_field(
                    row,
                    (
                        "[data-testid='seller-marker']",
                        ".seller-marker",
                        ".seller-tag",
                        ".tag",
                        ".badge",
                        "[aria-label*='直营']",
                    ),
                ),
                raw_price_text=raw_price_text,
                price_amount=_parse_price_amount(raw_price_text.raw_text),
                price_currency=_parse_price_currency(raw_price_text.raw_text),
                raw_cabin_product_text=_first_field(
                    row,
                    (
                        "[data-testid='cabin-product']",
                        ".cabin-product",
                        ".product-name",
                        ".cabin",
                        "[aria-label*='舱']",
                    ),
                ),
                raw_baggage_text=_first_field(
                    row,
                    ("[data-testid='baggage']", ".baggage", ".luggage", "[aria-label*='行李']"),
                ),
                raw_refund_change_rule_text=_first_field(
                    row,
                    (
                        "[data-testid='fare-rule']",
                        ".fare-rule",
                        ".refund-change",
                        ".rule",
                        "[aria-label*='退改']",
                    ),
                ),
                raw_availability_text=_first_field(
                    row,
                    (
                        "[data-testid='availability']",
                        ".availability",
                        ".stock",
                        "[aria-label*='仅剩']",
                        "[aria-label*='余']",
                    ),
                ),
                action_evidence=(
                    FieldEvidence.observed("level2 offer action present", action_selector)
                    if action_selector is not None
                    else FieldEvidence.missing("no level2 offer action observed")
                ),
                row_diagnostic={
                    "selector": _row_selector(row),
                    "run_local_sequence": sequence,
                    "text_length": len(row.text_content()),
                    "provider_local_identity": True,
                },
            )
        )
    return tuple(evidence)


def classify_level2_expansion_state(
    html: str,
    *,
    action_available: bool = True,
    timed_out: bool = False,
) -> Level2ExpansionOutcome:
    detector_state = summarize_detector_state(html)
    if detector_state["access_challenge"] or detector_state["login_required"]:
        return Level2ExpansionOutcome.ACCESS_CHALLENGE
    if detector_state["provider_error"]:
        return Level2ExpansionOutcome.PROVIDER_ERROR
    if timed_out:
        return Level2ExpansionOutcome.TIMEOUT
    if not action_available:
        return Level2ExpansionOutcome.ACTION_NOT_AVAILABLE
    if _level2_offer_rows(parse_html(html)):
        return Level2ExpansionOutcome.SUCCESS_EXPANDED
    if _contains_any(html, ("暂无可订", "暂无舱位", "无可售报价", "已售罄", "no offers", "sold out")):
        return Level2ExpansionOutcome.SUCCESS_EMPTY
    return Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT


def build_level2_expansion_result_from_html(
    html: str,
    *,
    target: Level2ExpansionTarget,
    acquired_at: datetime,
    experiment_run_id: str | None = None,
    search_plan_id: str | None = None,
    execution_id: str | None = None,
    duration_ms: int = 0,
    source_url: str | None = None,
    action_available: bool = True,
    timed_out: bool = False,
    bounds: Level2ExpansionBounds | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> Level2ExpansionResult:
    target_bounds = bounds or Level2ExpansionBounds()
    outcome = classify_level2_expansion_state(html, action_available=action_available, timed_out=timed_out)
    offer_rows = (
        extract_level2_offer_evidence(html, parent_level1_ref=target.parent_level1_ref, bounds=target_bounds)
        if outcome is Level2ExpansionOutcome.SUCCESS_EXPANDED
        else ()
    )
    return Level2ExpansionResult(
        provider_identity=FLIGGY_PROVIDER_ID,
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=acquired_at,
        experiment_run_id=experiment_run_id,
        search_plan_id=search_plan_id,
        execution_id=execution_id,
        target=target,
        outcome=outcome,
        observed_offer_row_count=len(offer_rows),
        duration_ms=duration_ms,
        sanitized_source_ref=_sanitize_source_ref(source_url) if source_url is not None else None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        bounds=target_bounds,
        offer_rows=offer_rows,
        diagnostics={
            "read_only": True,
            "clicked": False,
            "parent_level1_evidence_preserved": True,
            "level2_mapping_performed": False,
            "bounded_expansion": target_bounds.to_dict(),
            **(diagnostics or {}),
        },
    )


def build_level2_expansion_failure_result(
    *,
    target: Level2ExpansionTarget,
    outcome: Level2ExpansionOutcome,
    acquired_at: datetime,
    experiment_run_id: str | None = None,
    search_plan_id: str | None = None,
    execution_id: str | None = None,
    duration_ms: int = 0,
    source_url: str | None = None,
    bounds: Level2ExpansionBounds | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> Level2ExpansionResult:
    if outcome is Level2ExpansionOutcome.SUCCESS_EXPANDED:
        raise ValueError("build_level2_expansion_failure_result cannot emit SUCCESS_EXPANDED")
    target_bounds = bounds or Level2ExpansionBounds()
    return Level2ExpansionResult(
        provider_identity=FLIGGY_PROVIDER_ID,
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=acquired_at,
        experiment_run_id=experiment_run_id,
        search_plan_id=search_plan_id,
        execution_id=execution_id,
        target=target,
        outcome=outcome,
        observed_offer_row_count=0,
        duration_ms=duration_ms,
        sanitized_source_ref=_sanitize_source_ref(source_url) if source_url is not None else None,
        parser_selector_probe_version=FLIGGY_BROWSER_PROBE_VERSION,
        bounds=target_bounds,
        offer_rows=(),
        diagnostics={
            "read_only": True,
            "clicked": False,
            "parent_level1_evidence_preserved": True,
            "level2_mapping_performed": False,
            "bounded_expansion": target_bounds.to_dict(),
            **(diagnostics or {}),
        },
    )


def build_level2_live_parent_ref(level1_evidence: FliggyFlightEvidence) -> str:
    raw_identity = level1_evidence.raw_displayed_flight_identity.raw_text or "unknown"
    compact_identity = "-".join(raw_identity.split()) or "unknown"
    return f"fliggy-level1-live:{level1_evidence.evidence_index}:{compact_identity}"


def map_level1_outcome_to_level2_failure(outcome: BrowserProbeOutcome) -> Level2ExpansionOutcome:
    if outcome in {BrowserProbeOutcome.ACCESS_CHALLENGE, BrowserProbeOutcome.LOGIN_REQUIRED}:
        return Level2ExpansionOutcome.ACCESS_CHALLENGE
    if outcome is BrowserProbeOutcome.TIMEOUT:
        return Level2ExpansionOutcome.TIMEOUT
    if outcome is BrowserProbeOutcome.NETWORK_ERROR:
        return Level2ExpansionOutcome.NETWORK_ERROR
    if outcome is BrowserProbeOutcome.PROVIDER_ERROR:
        return Level2ExpansionOutcome.PROVIDER_ERROR
    if outcome is BrowserProbeOutcome.SUCCESS_EMPTY:
        return Level2ExpansionOutcome.SUCCESS_EMPTY
    return Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT


def _level2_as_browser_outcome(outcome: Level2ExpansionOutcome) -> BrowserProbeOutcome:
    if outcome is Level2ExpansionOutcome.SUCCESS_EXPANDED:
        return BrowserProbeOutcome.SUCCESS_COMPLETE
    if outcome is Level2ExpansionOutcome.SUCCESS_EMPTY:
        return BrowserProbeOutcome.SUCCESS_EMPTY
    if outcome is Level2ExpansionOutcome.ACCESS_CHALLENGE:
        return BrowserProbeOutcome.ACCESS_CHALLENGE
    if outcome is Level2ExpansionOutcome.TIMEOUT:
        return BrowserProbeOutcome.TIMEOUT
    if outcome is Level2ExpansionOutcome.NETWORK_ERROR:
        return BrowserProbeOutcome.NETWORK_ERROR
    if outcome is Level2ExpansionOutcome.PROVIDER_ERROR:
        return BrowserProbeOutcome.PROVIDER_ERROR
    return BrowserProbeOutcome.EVIDENCE_INSUFFICIENT


def _finalize_level2_live_diagnostics(
    *,
    diagnostics: dict[str, Any],
    recorder: _StageRecorder,
    outcome: Level2ExpansionOutcome,
    started: float,
) -> dict[str, Any]:
    finalized = dict(diagnostics)
    _finalize_diagnostics(
        diagnostics=finalized,
        recorder=recorder,
        outcome=_level2_as_browser_outcome(outcome),
        started=started,
    )
    if outcome is Level2ExpansionOutcome.ACTION_NOT_AVAILABLE:
        finalized["failure_taxonomy"] = "BOOKING_ACTION_NOT_FOUND"
    elif outcome is Level2ExpansionOutcome.SUCCESS_EMPTY:
        finalized["failure_taxonomy"] = None
    elif outcome is Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT and finalized.get("failed_stage") == BrowserProbeStage.LEVEL2_EXTRACTION.value:
        finalized["failure_taxonomy"] = "LEVEL2_ROWS_NOT_FOUND"
    return finalized


async def run_fliggy_level2_live_validation(
    probe_input: ProbeInput,
    *,
    bounds: Level2ExpansionBounds | None = None,
    max_level1_targets: int = 1,
) -> Level2ExpansionResult:
    """Run an opt-in bounded live validation of FLIGGY Level-2 evidence shape."""

    if max_level1_targets <= 0 or max_level1_targets > 2:
        raise ValueError("max_level1_targets must be between 1 and 2")
    target_bounds = bounds or Level2ExpansionBounds(max_offer_rows=5, max_wait_ms=3000, max_retries=0)
    started = time.monotonic()
    acquired_at = datetime.now(UTC)
    url = _build_fliggy_search_url(probe_input)
    recorder = _StageRecorder(started)
    diagnostics: dict[str, Any] = {
        "live_validation_unit": "M9-FLIGGY-LEVEL2-LIVE-U1",
        "read_only": True,
        "headless": probe_input.headless,
        "level1_targets_attempted": 0,
        "max_level1_targets": max_level1_targets,
        "level2_mapping_performed": False,
        "clicked": False,
        "retries": 0,
        "stop_policy": "stop_on_challenge_or_first_sufficient_evidence",
        "stage_diagnostics": [],
        "last_stage": None,
        "last_successful_stage": None,
        "failed_stage": None,
        "failure_taxonomy": None,
        "url_class": "UNAVAILABLE",
        "challenge_detected": False,
    }

    try:
        recorder.mark(BrowserProbeStage.BROWSER_LAUNCH, "launching Playwright Chromium")
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the opt-in live FLIGGY Level-2 validation") from exc

    target = Level2ExpansionTarget(parent_level1_ref="fliggy-level1-live:unavailable")
    html = ""
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
            diagnostics["entry_page_identity"] = classify_fliggy_page_identity(url=page.url, title=title, html=html).value
            diagnostics["entry_detector_state"] = summarize_detector_state(html)
            diagnostics["final_sanitized_url"] = _sanitize_source_ref(page.url)
            if diagnostics["entry_page_identity"] in {
                FliggyPageIdentity.ACCESS_CHALLENGE.value,
                FliggyPageIdentity.LOGIN_REQUIRED.value,
            }:
                await context.close()
                await browser.close()
                return build_level2_expansion_failure_result(
                    target=target,
                    outcome=Level2ExpansionOutcome.ACCESS_CHALLENGE,
                    acquired_at=acquired_at,
                    experiment_run_id=probe_input.experiment_run_id,
                    search_plan_id=probe_input.search_plan_id,
                    execution_id=probe_input.execution_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    source_url=page.url,
                    bounds=target_bounds,
                    diagnostics=_finalize_level2_live_diagnostics(
                        diagnostics=diagnostics,
                        recorder=recorder,
                        outcome=Level2ExpansionOutcome.ACCESS_CHALLENGE,
                        started=started,
                    ),
                )

            recorder.mark(BrowserProbeStage.SEARCH_INPUT_READINESS, "checking public flight-search controls")
            readiness = await _capture_search_form_readiness(page)
            diagnostics["search_form_readiness"] = readiness.to_dict()
            diagnostics["search_form_ready"] = readiness.is_ready()
            if not readiness.is_ready():
                await context.close()
                await browser.close()
                return build_level2_expansion_failure_result(
                    target=target,
                    outcome=Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT,
                    acquired_at=acquired_at,
                    experiment_run_id=probe_input.experiment_run_id,
                    search_plan_id=probe_input.search_plan_id,
                    execution_id=probe_input.execution_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    source_url=page.url,
                    bounds=target_bounds,
                    diagnostics=_finalize_level2_live_diagnostics(
                        diagnostics={**diagnostics, "failure_kind": "search_form_not_ready"},
                        recorder=recorder,
                        outcome=Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT,
                        started=started,
                    ),
                )

            page_count_before_submit = len(context.pages)
            recorder.mark(BrowserProbeStage.SEARCH_INPUT, "public flight-search controls detected")
            submit_allowed, query_state_diagnostics = await _submit_verified_public_flight_search(context, page, probe_input)
            diagnostics.update(query_state_diagnostics)
            diagnostics["search_input_succeeded"] = submit_allowed
            if not submit_allowed:
                await context.close()
                await browser.close()
                return build_level2_expansion_failure_result(
                    target=target,
                    outcome=Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT,
                    acquired_at=acquired_at,
                    experiment_run_id=probe_input.experiment_run_id,
                    search_plan_id=probe_input.search_plan_id,
                    execution_id=probe_input.execution_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    source_url=page.url,
                    bounds=target_bounds,
                    diagnostics=_finalize_level2_live_diagnostics(
                        diagnostics={**diagnostics, "failure_kind": "pre_submit_query_state_verification_failed"},
                        recorder=recorder,
                        outcome=Level2ExpansionOutcome.EVIDENCE_INSUFFICIENT,
                        started=started,
                    ),
                )
            recorder.mark(BrowserProbeStage.SEARCH_SUBMIT, "search submitted through public visible flight form")
            diagnostics["search_submission_attempted"] = True
            recorder.mark(BrowserProbeStage.RESULT_TRANSITION, "selecting deterministic result context")
            page, handoff_diagnostics = await _select_result_context_page(
                context=context,
                current_page=page,
                probe_input=probe_input,
                page_error_type=PlaywrightError,
                page_count_before_submit=page_count_before_submit,
                page_count_after_input_before_submit=query_state_diagnostics.get("page_count_after_input_before_submit"),
                wait_ms=min(5000, max(500, _remaining_ms(started, probe_input.overall_deadline_seconds) - 500)),
            )
            diagnostics["result_context_handoff"] = handoff_diagnostics
            diagnostics["result_context_selected"] = handoff_diagnostics["selected_page_index"] is not None
            _annotate_post_submit_query_propagation(diagnostics, handoff_diagnostics)
            diagnostics["post_submit_query_state_diagnostics"] = _build_post_submit_query_state_diagnostics(
                diagnostics,
                handoff_diagnostics,
            )
            recorder.mark(BrowserProbeStage.RESULT_READINESS, "waiting for terminal/result state")
            await page.wait_for_load_state("networkidle", timeout=_remaining_ms(started, probe_input.overall_deadline_seconds))
            html = await page.content()
            title = await page.title()
            diagnostics["result_page_identity"] = classify_fliggy_page_identity(url=page.url, title=title, html=html).value
            diagnostics["result_detector_state"] = summarize_detector_state(html)
            diagnostics["final_sanitized_url"] = _sanitize_source_ref(page.url)
            level1_outcome = classify_result_state(html)
            diagnostics["level1_outcome"] = level1_outcome.value
            recorder.mark(BrowserProbeStage.LEVEL1_DISCOVERY, "discovering Level-1 rows")
            level1_evidence = extract_level1_evidence(html)
            diagnostics["level1_observed_result_count"] = len(level1_evidence)
            if not level1_evidence or level1_outcome not in {
                BrowserProbeOutcome.SUCCESS_COMPLETE,
                BrowserProbeOutcome.SUCCESS_PARTIAL,
            }:
                await context.close()
                await browser.close()
                return build_level2_expansion_failure_result(
                    target=target,
                    outcome=map_level1_outcome_to_level2_failure(level1_outcome),
                    acquired_at=acquired_at,
                    experiment_run_id=probe_input.experiment_run_id,
                    search_plan_id=probe_input.search_plan_id,
                    execution_id=probe_input.execution_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    source_url=page.url,
                    bounds=target_bounds,
                    diagnostics=_finalize_level2_live_diagnostics(
                        diagnostics=diagnostics,
                        recorder=recorder,
                        outcome=map_level1_outcome_to_level2_failure(level1_outcome),
                        started=started,
                    ),
                )

            recorder.mark(BrowserProbeStage.TARGET_SELECTION, "selecting first bounded Level-1 target")
            selected_level1 = level1_evidence[0]
            target = Level2ExpansionTarget(
                parent_level1_ref=build_level2_live_parent_ref(selected_level1),
                level1_evidence_index=selected_level1.evidence_index,
                provider_row_ref=str(selected_level1.container_diagnostic.get("selector")),
            )
            diagnostics["level1_targets_attempted"] = 1
            recorder.mark(BrowserProbeStage.BOOKING_ACTION_DISCOVERY, "checking Level-1 booking action")
            action_selector = selected_level1.booking_action_diagnostic.get("selector")
            diagnostics["target_booking_action_selector"] = action_selector
            if not isinstance(action_selector, str) or not action_selector.strip():
                await context.close()
                await browser.close()
                return build_level2_expansion_failure_result(
                    target=target,
                    outcome=Level2ExpansionOutcome.ACTION_NOT_AVAILABLE,
                    acquired_at=acquired_at,
                    experiment_run_id=probe_input.experiment_run_id,
                    search_plan_id=probe_input.search_plan_id,
                    execution_id=probe_input.execution_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    source_url=page.url,
                    bounds=target_bounds,
                    diagnostics=_finalize_level2_live_diagnostics(
                        diagnostics=diagnostics,
                        recorder=recorder,
                        outcome=Level2ExpansionOutcome.ACTION_NOT_AVAILABLE,
                        started=started,
                    ),
                )

            row_selector = str(selected_level1.container_diagnostic.get("selector") or ".flight-item-tr")
            action = page.locator(row_selector).nth(selected_level1.evidence_index - 1).locator(action_selector).first
            recorder.mark(BrowserProbeStage.BOOKING_ACTION, "clicking bounded Level-1 booking action")
            await action.click(timeout=min(target_bounds.max_wait_ms, _remaining_ms(started, probe_input.overall_deadline_seconds)))
            diagnostics["clicked"] = True
            recorder.mark(BrowserProbeStage.LEVEL2_READINESS, "waiting for bounded Level-2 expansion")
            await page.wait_for_timeout(min(target_bounds.max_wait_ms, _remaining_ms(started, probe_input.overall_deadline_seconds)))
            html = await page.content()
            diagnostics["post_expansion_detector_state"] = summarize_detector_state(html)
            recorder.mark(BrowserProbeStage.LEVEL2_EXTRACTION, "extracting Level-2 offer evidence")
            level2_outcome = classify_level2_expansion_state(html)
            if level2_outcome in {Level2ExpansionOutcome.SUCCESS_EXPANDED, Level2ExpansionOutcome.SUCCESS_EMPTY}:
                recorder.mark(BrowserProbeStage.SANITIZATION, "sanitizing probe diagnostics")
                recorder.mark(BrowserProbeStage.COMPLETED, "probe result completed")
            result = build_level2_expansion_result_from_html(
                html,
                target=target,
                acquired_at=acquired_at,
                experiment_run_id=probe_input.experiment_run_id,
                search_plan_id=probe_input.search_plan_id,
                execution_id=probe_input.execution_id,
                duration_ms=int((time.monotonic() - started) * 1000),
                source_url=page.url,
                bounds=target_bounds,
                diagnostics=_finalize_level2_live_diagnostics(
                    diagnostics=diagnostics,
                    recorder=recorder,
                    outcome=level2_outcome,
                    started=started,
                ),
            )
            await context.close()
            await browser.close()
            return result
    except PlaywrightTimeoutError:
        return build_level2_expansion_failure_result(
            target=target,
            outcome=Level2ExpansionOutcome.TIMEOUT,
            acquired_at=acquired_at,
            experiment_run_id=probe_input.experiment_run_id,
            search_plan_id=probe_input.search_plan_id,
            execution_id=probe_input.execution_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            source_url=url,
            bounds=target_bounds,
            diagnostics=_finalize_level2_live_diagnostics(
                diagnostics={
                    **diagnostics,
                    "failure_kind": "timeout",
                    "last_html_classification": classify_level2_expansion_state(html).value,
                },
                recorder=recorder,
                outcome=Level2ExpansionOutcome.TIMEOUT,
                started=started,
            ),
        )
    except PlaywrightError as exc:
        return build_level2_expansion_failure_result(
            target=target,
            outcome=Level2ExpansionOutcome.NETWORK_ERROR,
            acquired_at=acquired_at,
            experiment_run_id=probe_input.experiment_run_id,
            search_plan_id=probe_input.search_plan_id,
            execution_id=probe_input.execution_id,
            duration_ms=int((time.monotonic() - started) * 1000),
            source_url=url,
            bounds=target_bounds,
            diagnostics=_finalize_level2_live_diagnostics(
                diagnostics={**diagnostics, "failure_kind": "playwright_error", "failure_message": str(exc)},
                recorder=recorder,
                outcome=Level2ExpansionOutcome.NETWORK_ERROR,
                started=started,
            ),
        )


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


def _url_class(url: str | None) -> str:
    if not url:
        return "UNAVAILABLE"
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path.lower()
    if not host:
        return "UNAVAILABLE"
    if "fliggy.com" not in host and "alitrip.com" not in host:
        return "NON_FLIGGY"
    if _contains_any(path, ("flight_search_result", "trip_flight_search")):
        return "FLIGGY_RESULT"
    if "fliggy.com" in host and parts.query:
        query_keys = {key for key, _ in parse_qsl(parts.query)}
        if "tab" in query_keys:
            return "FLIGGY_PUBLIC_ENTRY"
    return "FLIGGY_OTHER"


def _stage_value(stage: BrowserProbeStage | None) -> str | None:
    return stage.value if stage is not None else None


def _diagnostic_stage_summary(recorder: _StageRecorder, outcome: BrowserProbeOutcome) -> tuple[str | None, str | None]:
    if outcome in {
        BrowserProbeOutcome.SUCCESS_COMPLETE,
        BrowserProbeOutcome.SUCCESS_PARTIAL,
        BrowserProbeOutcome.SUCCESS_EMPTY,
    }:
        return _stage_value(recorder.last_stage()), None
    return _stage_value(recorder.last_successful_stage()), _stage_value(recorder.last_stage())


def _browser_failure_taxonomy(
    *,
    outcome: BrowserProbeOutcome,
    failed_stage: str | None,
    diagnostics: dict[str, Any],
) -> str | None:
    if outcome in {
        BrowserProbeOutcome.SUCCESS_COMPLETE,
        BrowserProbeOutcome.SUCCESS_PARTIAL,
        BrowserProbeOutcome.SUCCESS_EMPTY,
    }:
        return None
    if outcome in {BrowserProbeOutcome.ACCESS_CHALLENGE, BrowserProbeOutcome.LOGIN_REQUIRED}:
        return "ACCESS_CHALLENGE"
    if outcome is BrowserProbeOutcome.PROVIDER_ERROR:
        return "PROVIDER_ERROR"
    if outcome is BrowserProbeOutcome.NETWORK_ERROR:
        if failed_stage == BrowserProbeStage.BROWSER_LAUNCH.value:
            return "BROWSER_LAUNCH_FAILURE"
        return "NETWORK_FAILURE"
    if outcome is BrowserProbeOutcome.TIMEOUT:
        if failed_stage == BrowserProbeStage.ENTRY_NAVIGATION.value:
            return "ENTRY_NAVIGATION_TIMEOUT"
        if failed_stage in {BrowserProbeStage.RESULT_TRANSITION.value, BrowserProbeStage.RESULT_READINESS.value}:
            return "RESULT_CONTAINER_NOT_READY"
        if failed_stage == BrowserProbeStage.LEVEL2_READINESS.value:
            return "LEVEL2_CONTAINER_NOT_READY"
        return "NETWORK_FAILURE"
    if diagnostics.get("wrong_navigation_target") is True:
        return "RESULT_PAGE_LAYOUT_DRIFT"
    if diagnostics.get("post_submit_propagation_failed") is True:
        post_submit_failure = diagnostics.get("post_submit_failure_taxonomy")
        return post_submit_failure if isinstance(post_submit_failure, str) else "SUBMIT_STATE_PROPAGATION_FAILED"
    if isinstance(diagnostics.get("pre_submit_query_verification"), dict):
        destination_commitment = diagnostics.get("destination_commitment")
        if isinstance(destination_commitment, dict) and destination_commitment.get("commitment_status") != "confirmed":
            destination_failure = destination_commitment.get("failure_taxonomy")
            if isinstance(destination_failure, str):
                return destination_failure
        pre_submit_failure = diagnostics["pre_submit_query_verification"].get("failure_taxonomy")
        if isinstance(pre_submit_failure, str):
            return pre_submit_failure
    if diagnostics.get("search_form_ready") is False:
        return "SEARCH_FORM_NOT_READY"
    if diagnostics.get("search_input_succeeded") is False:
        return "SEARCH_INPUT_FAILED"
    if diagnostics.get("search_submission_attempted") is True and diagnostics.get("result_context_selected") is False:
        return "SEARCH_SUBMIT_NO_TRANSITION"
    if failed_stage == BrowserProbeStage.LEVEL1_DISCOVERY.value:
        return "LEVEL1_ROWS_NOT_FOUND"
    if failed_stage == BrowserProbeStage.TARGET_SELECTION.value:
        return "LEVEL1_TARGET_NOT_FOUND"
    if failed_stage == BrowserProbeStage.BOOKING_ACTION_DISCOVERY.value:
        return "BOOKING_ACTION_NOT_FOUND"
    if failed_stage == BrowserProbeStage.BOOKING_ACTION.value:
        return "BOOKING_ACTION_FAILED"
    if failed_stage == BrowserProbeStage.LEVEL2_EXTRACTION.value:
        return "LEVEL2_ROWS_NOT_FOUND"
    return "DIAGNOSTIC_INSUFFICIENT"


def _finalize_diagnostics(
    *,
    diagnostics: dict[str, Any],
    recorder: _StageRecorder,
    outcome: BrowserProbeOutcome,
    started: float,
) -> None:
    last_successful_stage, failed_stage = _diagnostic_stage_summary(recorder, outcome)
    if diagnostics.get("search_submission_attempted") is True and diagnostics.get("result_context_selected") is False:
        last_successful_stage = BrowserProbeStage.SEARCH_SUBMIT.value
        failed_stage = BrowserProbeStage.RESULT_TRANSITION.value
    diagnostics["last_successful_stage"] = last_successful_stage
    diagnostics["failed_stage"] = failed_stage
    diagnostics["last_stage"] = _stage_value(recorder.last_stage())
    diagnostics["stage_diagnostics"] = [stage.to_dict() for stage in recorder.stages]
    diagnostics["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    diagnostics["failure_taxonomy"] = _browser_failure_taxonomy(
        outcome=outcome,
        failed_stage=failed_stage,
        diagnostics=diagnostics,
    )
    diagnostics["url_class"] = _url_class(str(diagnostics.get("final_sanitized_url") or ""))
    diagnostics["challenge_detected"] = bool(
        isinstance(diagnostics.get("detector_state"), dict)
        and diagnostics["detector_state"].get("access_challenge") is True
    )


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


def _live_observation_preflight(probe_input: ProbeInput) -> dict[str, Any]:
    unicode_values = (
        probe_input.origin_text,
        probe_input.destination_text,
        probe_input.departure_date.isoformat(),
    )
    unicode_safe = all(value.encode("utf-8").decode("utf-8") == value for value in unicode_values)
    output_path = Path(probe_input.evidence_output_path).resolve() if probe_input.evidence_output_path else None
    output_path_absolute = output_path is not None and output_path.is_absolute()
    if not unicode_safe:
        raise ValueError("Unicode-safe live preflight failed before provider access")
    if probe_input.planned_observation is not None and (
        probe_input.origin_text != "\u5317\u4eac"
        or probe_input.destination_text != "\u4e0a\u6d77"
        or probe_input.departure_date != date(2026, 9, 14)
    ):
        raise ValueError("Planned observation query identity preflight failed before provider access")
    if probe_input.planned_observation is not None and not output_path_absolute:
        raise ValueError("A planned diagnostic observation requires an absolute evidence output path")
    return {
        "unicode_safe": True,
        "origin": probe_input.origin_text,
        "destination": probe_input.destination_text,
        "departure_date": probe_input.departure_date.isoformat(),
        "planned_observation": probe_input.planned_observation,
        "mode": "headless" if probe_input.headless else "headed",
        "retries": 0,
        "output_path_absolute": output_path_absolute,
        "output_path": str(output_path) if output_path is not None else None,
        "provider_access_started": False,
    }


async def run_fliggy_browser_probe(probe_input: ProbeInput) -> ProbeRunResult:
    """Run the opt-in live browser probe without persisting browser session state."""

    live_preflight = _live_observation_preflight(probe_input)
    started = time.monotonic()
    acquired_at = datetime.now(UTC)
    url = _build_fliggy_search_url(probe_input)
    recorder = _StageRecorder(started)
    diagnostics: dict[str, Any] = {
        "read_only": True,
        "acquired_at": acquired_at.isoformat(),
        "clicked": False,
        "retries": 0,
        "headless": probe_input.headless,
        "planned_observation": probe_input.planned_observation,
        "live_preflight": live_preflight,
        "entry_url_strategy": "public_fliggy_flight_entry_tab",
        "stage_diagnostics": [],
        "last_stage": None,
        "last_successful_stage": None,
        "failed_stage": None,
        "failure_taxonomy": None,
        "url_class": "UNAVAILABLE",
        "challenge_detected": False,
        "detector_state": summarize_detector_state(""),
        "page_identity": FliggyPageIdentity.UNKNOWN.value,
        "wrong_navigation_target": False,
        "search_interaction_failed": False,
        "search_form_ready": None,
        "search_input_succeeded": None,
        "search_submission_attempted": False,
        "result_context_selected": None,
        "visible_public_form_used": False,
        "search_form_readiness": None,
        "search_form_ready_ms": None,
        "submit_sequence": None,
        "result_context_handoff": {
            "page_count_before_submit": None,
            "page_count_after_input_before_submit": None,
            "page_count_after_submit": None,
            "pre_submit_context_count_changed": None,
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
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for the opt-in live FLIGGY browser probe") from exc

    try:
        async with async_playwright() as playwright:
            live_preflight["provider_access_started"] = True
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
                recorder.mark(BrowserProbeStage.SEARCH_INPUT_READINESS, "checking public flight-search controls")
                form_readiness = await _capture_search_form_readiness(page)
                diagnostics["search_form_readiness"] = form_readiness.to_dict()
                diagnostics["search_form_ready"] = form_readiness.is_ready()
                if not form_readiness.is_ready():
                    diagnostics["search_interaction_failed"] = True
                    outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                    await context.close()
                    await browser.close()
                    should_wait_for_result_state = False
                else:
                    diagnostics["search_form_ready_ms"] = int((time.monotonic() - started) * 1000)
                    recorder.mark(BrowserProbeStage.SEARCH_INPUT, "public flight-search controls detected")
                    page_count_before_submit = len(context.pages)
                    diagnostics["result_context_handoff"]["page_count_before_submit"] = page_count_before_submit
                    diagnostics["submit_sequence"] = "origin_enter,date_force_fill_enter,destination_enter,verified_public_search_button_once"
                    submit_allowed, query_state_diagnostics = await _submit_verified_public_flight_search(context, page, probe_input)
                    diagnostics.update(query_state_diagnostics)
                    diagnostics["search_input_succeeded"] = submit_allowed
                    diagnostics["visible_public_form_used"] = True
                    if not submit_allowed:
                        diagnostics["search_interaction_failed"] = True
                        outcome = BrowserProbeOutcome.EVIDENCE_INSUFFICIENT
                        should_wait_for_result_state = False
                        await context.close()
                        await browser.close()
                    else:
                        diagnostics["clicked"] = True
                        diagnostics["search_submission_attempted"] = True
                        recorder.mark(BrowserProbeStage.SEARCH_SUBMIT, "search submitted through public visible flight form")
                        recorder.mark(BrowserProbeStage.RESULT_TRANSITION, "selecting deterministic result context")
                        page, handoff_diagnostics = await _select_result_context_page(
                            context=context,
                            current_page=page,
                            probe_input=probe_input,
                            page_error_type=PlaywrightError,
                            page_count_before_submit=page_count_before_submit,
                            page_count_after_input_before_submit=query_state_diagnostics.get("page_count_after_input_before_submit"),
                            wait_ms=min(5000, max(500, _remaining_ms(started, probe_input.overall_deadline_seconds) - 500)),
                        )
                        diagnostics["result_context_handoff"] = handoff_diagnostics
                        diagnostics["result_context_selected"] = handoff_diagnostics["selected_page_index"] is not None
                        _annotate_post_submit_query_propagation(diagnostics, handoff_diagnostics)
                        diagnostics["post_submit_query_state_diagnostics"] = _build_post_submit_query_state_diagnostics(
                            diagnostics,
                            handoff_diagnostics,
                        )
                        if handoff_diagnostics["selected_page_index"] is None:
                            diagnostics["search_interaction_failed"] = True
                        else:
                            diagnostics["final_sanitized_url"] = handoff_diagnostics["selected_page_url"]
                            diagnostics["page_identity"] = handoff_diagnostics["selected_page_identity"]
                        should_wait_for_result_state = True
            if should_wait_for_result_state:
                recorder.mark(BrowserProbeStage.RESULT_READINESS, "waiting for terminal/result state")
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
                recorder.mark(BrowserProbeStage.LEVEL1_DISCOVERY, "result container detected")
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
                recorder.mark(BrowserProbeStage.SANITIZATION, "sanitizing probe diagnostics")
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
    if outcome in {
        BrowserProbeOutcome.SUCCESS_COMPLETE,
        BrowserProbeOutcome.SUCCESS_PARTIAL,
        BrowserProbeOutcome.SUCCESS_EMPTY,
    } and last_stage is not BrowserProbeStage.COMPLETED:
        recorder.mark(BrowserProbeStage.SANITIZATION, "sanitizing probe diagnostics")
        recorder.mark(BrowserProbeStage.COMPLETED, "probe result completed")
    last_stage = recorder.last_stage()
    _ = last_stage
    _finalize_diagnostics(diagnostics=diagnostics, recorder=recorder, outcome=outcome, started=started)
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
    parser.add_argument("--evidence-output-path", type=Path)
    parser.add_argument("--planned-observation", type=int)
    parser.add_argument("--headed-observation-pause-seconds", type=float, default=0.0)
    parser.add_argument("--destination-activation-probe", choices=("current_click", "pointer_mouse"))
    parser.add_argument("--destination-hit-target-probe", choices=("visual_map", "differential_click"))
    parser.add_argument("--human-hit-region-id")
    parser.add_argument("--human-hit-point-x", type=float)
    parser.add_argument("--human-hit-point-y", type=float)
    args = parser.parse_args(argv)
    if args.output_json is not None:
        args.output_json = args.output_json.resolve()
    if args.evidence_output_path is not None:
        args.evidence_output_path = args.evidence_output_path.resolve()

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

    def last_successful_stage(self) -> BrowserProbeStage | None:
        if not self.stages:
            return None
        if len(self.stages) == 1:
            return None
        return self.stages[-2].stage


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


def _level2_offer_rows(root: HtmlNode) -> list[HtmlNode]:
    selectors = (
        "[data-testid='fliggy-offer-row']",
        "[data-testid='offer-row']",
        ".expanded-offer-row",
        ".level2-offer-row",
        ".offer-row",
        ".seller-row",
    )
    rows: list[HtmlNode] = []
    seen: set[int] = set()
    for selector in selectors:
        for row in root.select(selector):
            offer_like = _contains_any(
                row.text_content(),
                ("¥", "￥", "元", "预订", "订票", "退改", "行李", "舱", "商家", "供应商", "直营"),
            )
            if id(row) not in seen and offer_like:
                rows.append(row)
                seen.add(id(row))
    return rows


def _level2_offer_row_ref(row: HtmlNode, *, parent_level1_ref: str, sequence: int) -> str:
    for attr in ("data-offer-id", "data-offer-ref", "data-row-id", "id"):
        value = row.attrs.get(attr)
        if value is not None and value.strip():
            return f"fliggy-level2-offer:{parent_level1_ref}:{_normalize_space(value)}"
    return f"fliggy-level2-offer:{parent_level1_ref}:{sequence}"


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


def _parse_price_amount(raw_text: str | None) -> int | None:
    if raw_text is None:
        return None
    digits = "".join(character for character in raw_text if character.isdigit())
    return int(digits) if digits else None


def _parse_price_currency(raw_text: str | None) -> str | None:
    if raw_text is None:
        return None
    if _contains_any(raw_text, ("¥", "￥", "元", "cny", "rmb")):
        return "CNY"
    return None


async def _write_public_flight_search_fields(page: Any, probe_input: ProbeInput) -> dict[str, Any]:
    async def fill_input(selector: str, value: str, *, force: bool = False, press_enter: bool = True) -> str | None:
        field = page.locator(selector).nth(0)
        if force:
            await field.fill(value, force=True)
        else:
            await field.click()
            await field.fill(value)
        if press_enter:
            await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        return await _read_control_text(page, selector)

    await page.wait_for_selector(".rc-flight-searchbar input#form_depCity")
    await fill_input(".rc-flight-searchbar input#form_depCity", probe_input.origin_text)
    date_readback = await fill_input(".rc-flight-searchbar input#form_depDate", probe_input.departure_date.isoformat(), force=True)
    destination_commitment = await _commit_public_destination(
        page,
        probe_input.destination_text,
        headed_observation_pause_seconds=(
            probe_input.headed_observation_pause_seconds if not probe_input.headless else 0.0
        ),
        diagnostic_activation_probe=probe_input.destination_activation_probe,
        diagnostic_hit_target_probe=probe_input.destination_hit_target_probe,
        evidence_output_path=probe_input.evidence_output_path,
        human_hit_region_id=probe_input.human_hit_region_id,
        human_hit_point=(
            (probe_input.human_hit_point_x, probe_input.human_hit_point_y)
            if probe_input.human_hit_point_x is not None and probe_input.human_hit_point_y is not None
            else None
        ),
        headless=probe_input.headless,
    )
    return {
        "destination_commitment": destination_commitment.to_dict(),
        "date_commitment": _public_date_commitment(
            requested_date=probe_input.departure_date.isoformat(),
            typed_date=probe_input.departure_date.isoformat(),
            commit_readback=date_readback,
            action_performed=True,
        ),
    }


async def _submit_public_flight_search(context: Any, page: Any, probe_input: ProbeInput) -> None:
    write_diagnostics = await _write_public_flight_search_fields(page, probe_input)
    destination_commitment = write_diagnostics.get("destination_commitment")
    if not (isinstance(destination_commitment, dict) and destination_commitment.get("commitment_status") == "confirmed"):
        return
    await page.locator(".rc-flight-searchbar button.search-button").nth(0).click()


async def _submit_verified_public_flight_search(context: Any, page: Any, probe_input: ProbeInput) -> tuple[bool, dict[str, Any]]:
    page_count_before_submit = len(context.pages)
    write_diagnostics = await _write_public_flight_search_fields(page, probe_input)
    page_count_after_input_before_submit = len(context.pages)
    query_state = await _capture_public_search_query_state(page, probe_input)
    verification = _verify_pre_submit_query_state(query_state)
    destination_commitment = write_diagnostics.get("destination_commitment")
    destination_committed = isinstance(destination_commitment, dict) and destination_commitment.get("commitment_status") == "confirmed"
    date_commitment = write_diagnostics.get("date_commitment")
    date_committed = isinstance(date_commitment, dict) and date_commitment.get("commitment_status") == "confirmed"
    diagnostics: dict[str, Any] = {
        **write_diagnostics,
        "pre_submit_query_state": query_state.to_dict(),
        "pre_submit_query_verification": verification.to_dict(),
        "page_count_before_submit": page_count_before_submit,
        "page_count_after_input_before_submit": page_count_after_input_before_submit,
        "pre_submit_context_count_changed": page_count_after_input_before_submit != page_count_before_submit,
        "submit_allowed": verification.submit_allowed and destination_committed and date_committed,
        "submit_executed": False,
        "public_submit_button_clicked_once": False,
    }
    _annotate_destination_s8(diagnostics, verification)
    if diagnostics["submit_allowed"] is not True:
        return False, diagnostics
    await page.locator(".rc-flight-searchbar button.search-button").nth(0).click()
    diagnostics["submit_executed"] = True
    diagnostics["public_submit_button_clicked_once"] = True
    _annotate_destination_s8(diagnostics, verification)
    return True, diagnostics


async def _commit_public_destination(
    page: Any,
    requested_destination: str,
    *,
    headed_observation_pause_seconds: float = 0.0,
    diagnostic_activation_probe: str | None = None,
    diagnostic_hit_target_probe: str | None = None,
    evidence_output_path: str | None = None,
    human_hit_region_id: str | None = None,
    human_hit_point: tuple[float, float] | None = None,
    headless: bool = True,
) -> DestinationCommitmentResult:
    if diagnostic_hit_target_probe is not None:
        return await _diagnose_public_destination_hit_target(
            page,
            requested_destination,
            probe_mode=diagnostic_hit_target_probe,
            evidence_output_path=evidence_output_path,
            human_hit_region_id=human_hit_region_id,
            human_hit_point=human_hit_point,
            headed_observation_pause_seconds=headed_observation_pause_seconds,
        )
    if diagnostic_activation_probe is not None:
        return await _diagnose_public_destination_activation(
            page,
            requested_destination,
            interaction_path=diagnostic_activation_probe,
            headless=headless,
            headed_observation_pause_seconds=headed_observation_pause_seconds,
        )
    observation_started = time.monotonic()
    control = page.locator(_FLIGGY_DESTINATION_INPUT_SELECTOR)
    destination_control_ready = False
    initial_readback: str | None = None
    try:
        if await control.count() > 0:
            field = control.nth(0)
            destination_control_ready = await field.is_visible() and await field.is_enabled()
        if not destination_control_ready:
            return _destination_commitment_result(
                requested_destination=requested_destination,
                destination_control_ready=False,
                typed_destination=None,
                candidates=(),
                suggestion_surface_present=False,
                selected_candidate=None,
                selection_method="none",
                commit_readback=None,
                failure_taxonomy="DESTINATION_CONTROL_NOT_READY",
            )
        initial_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
        await field.click()
        selector_opened_elapsed_ms = int((time.monotonic() - observation_started) * 1000)
    except PlaywrightError:
        return _destination_commitment_result(
            requested_destination=requested_destination,
            destination_control_ready=destination_control_ready,
            typed_destination=None,
            candidates=(),
            suggestion_surface_present=False,
            selected_candidate=None,
            selection_method="none",
            commit_readback=initial_readback,
            failure_taxonomy="DESTINATION_CITY_SELECTOR_OPEN_FAILED",
        )

    candidates, suggestion_snapshots = await _wait_for_destination_suggestion_candidates(
        page,
        max_candidates=64,
        observation_started=observation_started,
    )
    suggestion_surface_present = bool(candidates) or await _public_destination_city_selector_surface_present(page)
    resolution = _resolve_public_destination_city_candidate(
        candidates,
        requested_destination,
        selector_surface_present=suggestion_surface_present,
    )
    if resolution.selected_candidate is None:
        if candidates and len(suggestion_snapshots) < _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS:
            suggestion_snapshots += await _observe_destination_suggestion_tail(
                page,
                observation_started=observation_started,
                attempts=_FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS - len(suggestion_snapshots),
            )
        if headed_observation_pause_seconds > 0:
            await page.wait_for_timeout(int(headed_observation_pause_seconds * 1000))
        commit_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
        return _destination_commitment_result(
            requested_destination=requested_destination,
            destination_control_ready=True,
            typed_destination=None,
            candidates=candidates,
            suggestion_surface_present=suggestion_surface_present,
            selected_candidate=None,
            selection_method="none",
            commit_readback=commit_readback,
            failure_taxonomy=resolution.failure_taxonomy,
            readback_sequence=(initial_readback, commit_readback),
            input_text_after_write=None,
            suggestion_snapshots=suggestion_snapshots,
            focused_elapsed_ms=selector_opened_elapsed_ms,
            typed_elapsed_ms=None,
            headed_pause_ms=int(headed_observation_pause_seconds * 1000),
            overlay_evidence=await _overlay_evidence(page),
            city_selector_opened=suggestion_surface_present,
            initial_destination_readback=initial_readback,
        )

    if headed_observation_pause_seconds > 0:
        await page.wait_for_timeout(int(headed_observation_pause_seconds * 1000))
    try:
        await page.locator(resolution.selected_candidate.selector).nth(resolution.selected_candidate.index).click()
        await page.wait_for_timeout(300)
    except PlaywrightError:
        return _destination_commitment_result(
            requested_destination=requested_destination,
            destination_control_ready=True,
            typed_destination=None,
            candidates=candidates,
            suggestion_surface_present=suggestion_surface_present,
            selected_candidate=resolution.selected_candidate,
            selection_method="click",
            commit_readback=await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR),
            failure_taxonomy="DESTINATION_OPTION_SELECTION_FAILED",
            readback_sequence=(initial_readback,),
            input_text_after_write=None,
            suggestion_snapshots=suggestion_snapshots,
            focused_elapsed_ms=selector_opened_elapsed_ms,
            typed_elapsed_ms=None,
            headed_pause_ms=int(headed_observation_pause_seconds * 1000),
            overlay_evidence=await _overlay_evidence(page),
            city_selector_opened=suggestion_surface_present,
            initial_destination_readback=initial_readback,
        )

    commit_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    stability = await _observe_destination_readback_stability(
        page,
        requested_destination=requested_destination,
        initial_readback=commit_readback,
    )
    return _destination_commitment_result(
        requested_destination=requested_destination,
        destination_control_ready=True,
        typed_destination=None,
        candidates=candidates,
        suggestion_surface_present=suggestion_surface_present,
        selected_candidate=resolution.selected_candidate,
        selection_method="click",
        commit_readback=commit_readback,
        failure_taxonomy=None,
        readback_sequence=(initial_readback,) + tuple(stability["readback_sequence"]),
        input_text_after_write=None,
        extension_used=bool(stability["extension_used"]),
        extension_reason=str(stability["extension_reason"]),
        suggestion_snapshots=suggestion_snapshots,
        focused_elapsed_ms=selector_opened_elapsed_ms,
        typed_elapsed_ms=None,
        headed_pause_ms=int(headed_observation_pause_seconds * 1000),
        overlay_evidence=await _overlay_evidence(page),
        city_selector_opened=suggestion_surface_present,
        initial_destination_readback=initial_readback,
    )


def _bind_public_destination_target(inventory: Sequence[dict[str, Any]]) -> dict[str, Any]:
    plausible = [item for item in inventory if item.get("visible") is True and item.get("semantic_excluded") is not True]
    exact_matches = [item for item in plausible if item.get("current_locator_match") is True]
    if len(exact_matches) > 1:
        return {
            "status": "TARGET_AMBIGUOUS",
            "bound_index": None,
            "plausible_count": len(plausible),
            "exact_match_count": len(exact_matches),
        }
    if len(exact_matches) == 1:
        return {
            "status": "BOUND",
            "bound_index": exact_matches[0]["index"],
            "plausible_count": len(plausible),
            "exact_match_count": 1,
        }
    status = "TARGET_AMBIGUOUS" if len(plausible) > 1 else "TARGET_MISMATCH"
    return {"status": status, "bound_index": None, "plausible_count": len(plausible), "exact_match_count": 0}


def _classify_public_destination_activation(
    *,
    binding_status: str,
    target_ready: bool,
    target_replaced: bool,
    interaction_completed: bool,
    samples: Sequence[dict[str, Any]],
) -> PublicDestinationActivationClass:
    if binding_status == "TARGET_AMBIGUOUS":
        return PublicDestinationActivationClass.TARGET_AMBIGUOUS
    if binding_status != "BOUND" or not target_ready:
        return PublicDestinationActivationClass.TARGET_MISMATCH
    if target_replaced:
        return PublicDestinationActivationClass.TARGET_REPLACED
    opened_indexes = [index for index, sample in enumerate(samples) if sample.get("surface_present") is True]
    if opened_indexes:
        return PublicDestinationActivationClass.OPENED if opened_indexes[0] == 0 else PublicDestinationActivationClass.DELAYED_ACTIVATION
    if interaction_completed:
        return PublicDestinationActivationClass.CLICK_NO_ACTIVATION
    return PublicDestinationActivationClass.UNKNOWN


def _public_destination_activation_root_class(
    activation_class: PublicDestinationActivationClass,
    interaction_path: str,
) -> str:
    if activation_class is PublicDestinationActivationClass.TARGET_MISMATCH:
        return "PUBLIC_TARGET_MISMATCH_LOCALIZED"
    if activation_class is PublicDestinationActivationClass.TARGET_AMBIGUOUS:
        return "PUBLIC_TARGET_AMBIGUITY_LOCALIZED"
    if activation_class is PublicDestinationActivationClass.TARGET_REPLACED:
        return "PUBLIC_TARGET_LIFECYCLE_REPLACEMENT_LOCALIZED"
    if activation_class is PublicDestinationActivationClass.DELAYED_ACTIVATION:
        return "PUBLIC_SELECTOR_ACTIVATION_DELAY_LOCALIZED"
    if activation_class is PublicDestinationActivationClass.OPENED and interaction_path == "pointer_mouse":
        return "PUBLIC_INTERACTION_SEMANTICS_LOCALIZED"
    return "DIAGNOSTIC_INSUFFICIENT"


def classify_destination_activation_mode(results: Sequence[dict[str, Any]]) -> str:
    roots_by_mode: dict[bool, set[str]] = {True: set(), False: set()}
    for result in results:
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
        if not isinstance(diagnostics, dict) or not isinstance(diagnostics.get("headless"), bool):
            continue
        commitment = diagnostics.get("destination_commitment")
        activation = commitment.get("destination_activation_diagnostics") if isinstance(commitment, dict) else None
        root_class = activation.get("a10_root_class", {}).get("root_class") if isinstance(activation, dict) else None
        if isinstance(root_class, str):
            roots_by_mode[diagnostics["headless"]].add(root_class)
    if roots_by_mode[True] and roots_by_mode[False] and roots_by_mode[True] != roots_by_mode[False]:
        return "MODE_DEPENDENT_SELECTOR_ACTIVATION_LOCALIZED"
    roots = roots_by_mode[True] | roots_by_mode[False]
    return next(iter(roots)) if len(roots) == 1 else "DIAGNOSTIC_INSUFFICIENT"


async def _public_destination_target_inventory(page: Any) -> tuple[dict[str, Any], ...]:
    locator = page.locator(_FLIGGY_PUBLIC_DESTINATION_TARGET_SELECTOR)
    count = min(await locator.count(), 8)
    inventory: list[dict[str, Any]] = []
    for index in range(count):
        item = locator.nth(index)
        evidence: dict[str, Any] = {
            "index": index,
            "visible_label": None,
            "tag": None,
            "role": None,
            "visible": False,
            "enabled": False,
            "attached": False,
            "bounding_box_present": False,
            "current_locator_match": False,
            "semantic_excluded": False,
        }
        with suppress(PlaywrightError):
            evidence["visible_label"] = await _read_locator_text(item)
            evidence["tag"] = await item.evaluate("node => node.tagName.toLowerCase()")
            evidence["role"] = await item.get_attribute("role")
            evidence["visible"] = await item.is_visible()
            evidence["enabled"] = await item.is_enabled()
            evidence["attached"] = await item.evaluate("node => node.isConnected")
            evidence["bounding_box_present"] = await item.bounding_box() is not None
            evidence["current_locator_match"] = await item.evaluate(
                f"node => node.matches({json.dumps(_FLIGGY_DESTINATION_INPUT_SELECTOR)})"
            )
            evidence["semantic_excluded"] = "交换" in str(evidence["visible_label"] or "")
        inventory.append(evidence)
    return tuple(inventory)


async def _public_destination_target_readiness(target: Any) -> dict[str, bool]:
    readiness = {"visible": False, "enabled": False, "attached": False, "bounding_box_present": False, "ready": False}
    with suppress(PlaywrightError):
        readiness.update(
            {
                "visible": await target.is_visible(),
                "enabled": await target.is_enabled(),
                "attached": await target.evaluate("node => node.isConnected"),
                "bounding_box_present": await target.bounding_box() is not None,
            }
        )
    readiness["ready"] = all(readiness[key] for key in ("visible", "enabled", "attached", "bounding_box_present"))
    return readiness


async def _public_target_has_focus(target: Any) -> bool | str:
    try:
        return bool(await target.evaluate("node => node === document.activeElement"))
    except PlaywrightError:
        return "unknown"


async def _diagnose_public_destination_activation(
    page: Any,
    requested_destination: str,
    *,
    interaction_path: str,
    headless: bool,
    headed_observation_pause_seconds: float,
) -> DestinationCommitmentResult:
    started = time.monotonic()
    inventory = await _public_destination_target_inventory(page)
    binding = _bind_public_destination_target(inventory)
    target = None
    if binding["status"] == "BOUND" and isinstance(binding["bound_index"], int):
        target = page.locator(_FLIGGY_PUBLIC_DESTINATION_TARGET_SELECTOR).nth(binding["bound_index"])
    readiness_before = await _public_destination_target_readiness(target) if target is not None else {}
    focus_before = await _public_target_has_focus(target) if target is not None else "unknown"
    initial_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    context_count_before = len(page.context.pages)
    target_handle = await target.element_handle() if target is not None else None
    pause_ms = int(headed_observation_pause_seconds * 1000) if not headless else 0
    if pause_ms > 0:
        await page.wait_for_timeout(pause_ms)

    interaction_completed = False
    interaction_error: str | None = None
    if target is not None and readiness_before.get("ready") is True:
        try:
            if interaction_path == "current_click":
                await target.click()
            else:
                box = await target.bounding_box()
                if box is None:
                    raise PlaywrightError("public target bounding box unavailable")
                await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                await page.mouse.down()
                await page.mouse.up()
            interaction_completed = True
        except PlaywrightError as exc:
            interaction_error = type(exc).__name__
    if pause_ms > 0:
        await page.wait_for_timeout(pause_ms)

    focus_after = await _public_target_has_focus(target) if target is not None else "unknown"
    target_connected_after: bool | str = "unknown"
    if target_handle is not None:
        with suppress(PlaywrightError):
            target_connected_after = bool(await target_handle.evaluate("node => node.isConnected"))
    readiness_after = await _public_destination_target_readiness(target) if target is not None else {}
    target_replaced = target_connected_after is False
    samples: list[dict[str, Any]] = []
    all_candidates: list[DestinationSuggestionCandidate] = []
    for attempt in range(_FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS):
        candidates = await _collect_destination_suggestion_candidates(page, max_candidates=64)
        surface_present = bool(candidates) or await _public_destination_city_selector_surface_present(page)
        samples.append(
            {
                "sample_index": attempt,
                "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                "surface_present": surface_present,
                "candidate_count": len(candidates),
                "candidate_labels": [candidate.label for candidate in candidates],
            }
        )
        all_candidates.extend(candidates)
        if surface_present:
            if pause_ms > 0:
                await page.wait_for_timeout(pause_ms)
            break
        if attempt < _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS - 1:
            await page.wait_for_timeout(_FLIGGY_DESTINATION_SUGGESTION_WAIT_MS)

    activation_class = _classify_public_destination_activation(
        binding_status=str(binding["status"]),
        target_ready=readiness_before.get("ready") is True,
        target_replaced=target_replaced,
        interaction_completed=interaction_completed,
        samples=samples,
    )
    root_class = _public_destination_activation_root_class(activation_class, interaction_path)
    activation_diagnostics = {
        "a0_target_inventory": {"locator_multiplicity": len(inventory), "targets": list(inventory)},
        "a1_target_bind": binding,
        "a2_target_readiness": {"before": readiness_before, "after": readiness_after},
        "a3_pre_click_focus": {"target_had_focus": focus_before},
        "a4_interaction_path": {"path": interaction_path, "same_semantic_public_control": binding["status"] == "BOUND"},
        "a5_post_click_receipt": {
            "interaction_completed": interaction_completed,
            "framework_error": interaction_error,
            "target_connected_after": target_connected_after,
        },
        "a6_focus_lifecycle_delta": {
            "focus_before": focus_before,
            "focus_after": focus_after,
            "context_count_before": context_count_before,
            "context_count_after": len(page.context.pages),
            "target_replaced": target_replaced,
        },
        "a7_selector_marker_sampling": {"samples": samples, "bounded_attempts": len(samples), "retries": 0},
        "a8_activation_class": {"activation_class": activation_class.value},
        "a9_human_visual_correlation": {
            "headed": not headless,
            "observation_pause_ms": pause_ms,
            "screenshot_supplied": False,
            "human_observation_is_semantic_truth": False,
        },
        "a10_root_class": {"root_class": root_class},
    }
    unique_candidates = tuple(dict.fromkeys((candidate.selector, candidate.index, candidate.label, candidate.selectable) for candidate in all_candidates))
    candidates = tuple(DestinationSuggestionCandidate(*candidate) for candidate in unique_candidates)
    commit_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    return _destination_commitment_result(
        requested_destination=requested_destination,
        destination_control_ready=readiness_before.get("ready") is True,
        typed_destination=None,
        candidates=candidates,
        suggestion_surface_present=any(sample["surface_present"] for sample in samples),
        selected_candidate=None,
        selection_method="diagnostic_only_no_candidate_selection",
        commit_readback=commit_readback,
        failure_taxonomy=f"DIAG_U10_{activation_class.value}",
        readback_sequence=(initial_readback, commit_readback),
        suggestion_snapshots=tuple(
            DestinationSuggestionSnapshot(sample["elapsed_ms"], ()) for sample in samples
        ),
        focused_elapsed_ms=samples[0]["elapsed_ms"] if samples else None,
        typed_elapsed_ms=None,
        headed_pause_ms=pause_ms,
        overlay_evidence=await _overlay_evidence(page),
        city_selector_opened=any(sample["surface_present"] for sample in samples),
        initial_destination_readback=initial_readback,
        activation_diagnostics=activation_diagnostics,
    )


def _rect_contains_point(rect: dict[str, float], point: tuple[float, float]) -> bool:
    x, y = point
    return (
        rect["x"] <= x <= rect["x"] + rect["width"]
        and rect["y"] <= y <= rect["y"] + rect["height"]
    )


def _rect_contains_rect(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return (
        outer["x"] <= inner["x"]
        and outer["y"] <= inner["y"]
        and outer["x"] + outer["width"] >= inner["x"] + inner["width"]
        and outer["y"] + outer["height"] >= inner["y"] + inner["height"]
    )


def _rect_overlap_ratio(first: dict[str, float], second: dict[str, float]) -> float:
    overlap_width = max(
        0.0,
        min(first["x"] + first["width"], second["x"] + second["width"])
        - max(first["x"], second["x"]),
    )
    overlap_height = max(
        0.0,
        min(first["y"] + first["height"], second["y"] + second["height"])
        - max(first["y"], second["y"]),
    )
    smaller_area = min(first["width"] * first["height"], second["width"] * second["height"])
    return round((overlap_width * overlap_height) / smaller_area, 4) if smaller_area > 0 else 0.0


def _assign_public_hit_region_ids(candidates: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    kind_rank = {"input": 0, "ancestor": 1, "overlap": 2, "nearby": 3}
    ordered = sorted(
        candidates,
        key=lambda item: (
            kind_rank.get(str(item.get("kind")), 9),
            int(item.get("depth", 99)),
            str(item.get("tag") or ""),
            str(item.get("id") or ""),
            str(item.get("class") or ""),
            float(item.get("rect", {}).get("x", 0)),
            float(item.get("rect", {}).get("y", 0)),
        ),
    )
    return tuple({**item, "region_id": f"REGION-{index:02d}"} for index, item in enumerate(ordered))


def _public_hit_region_relationships(regions: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    input_region = next((item for item in regions if item.get("kind") == "input"), None)
    if input_region is None:
        return ()
    input_rect = input_region["rect"]
    return tuple(
        {
            "region_id": item["region_id"],
            "contains_input": _rect_contains_rect(item["rect"], input_rect),
            "inside_input": _rect_contains_rect(input_rect, item["rect"]),
            "input_overlap_ratio": _rect_overlap_ratio(item["rect"], input_rect),
        }
        for item in regions
    )


def _human_hit_point_from_evidence(
    regions: Sequence[dict[str, Any]],
    *,
    region_id: str | None,
    point: tuple[float, float] | None,
) -> tuple[tuple[float, float] | None, str | None]:
    if point is not None:
        return point, None
    region = next((item for item in regions if item.get("region_id") == region_id), None)
    if region is None:
        return None, None
    rect = region["rect"]
    return (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2), str(region_id)


def _classify_human_hit_differential(
    regions: Sequence[dict[str, Any]],
    *,
    point: tuple[float, float] | None,
    hit_matches_input: bool | None,
) -> str:
    if point is None or hit_matches_input is None:
        return "HUMAN_TARGET_AMBIGUOUS"
    input_region = next((item for item in regions if item.get("kind") == "input"), None)
    if input_region is None:
        return "HUMAN_TARGET_AMBIGUOUS"
    if hit_matches_input and _rect_contains_point(input_region["rect"], point):
        return "SAME_GEOMETRIC_INPUT_REGION"
    ancestor_hits = [
        item
        for item in regions
        if item.get("kind") == "ancestor" and _rect_contains_point(item["rect"], point)
    ]
    if not _rect_contains_point(input_region["rect"], point) and ancestor_hits:
        return "HUMAN_POINT_OUTSIDE_INPUT"
    if not hit_matches_input:
        return "HUMAN_TARGET_DIFFERS"
    return "HUMAN_TARGET_AMBIGUOUS"


def _public_hit_target_root_class(differential_class: str, *, selector_opened: bool) -> str:
    if selector_opened and differential_class in {"HUMAN_POINT_OUTSIDE_INPUT", "HUMAN_TARGET_DIFFERS"}:
        return "PUBLIC_WRAPPER_ACTIVATION_LOCALIZED"
    if differential_class in {"HUMAN_POINT_OUTSIDE_INPUT", "HUMAN_TARGET_DIFFERS"}:
        return "PUBLIC_HIT_TARGET_MISMATCH_LOCALIZED"
    if differential_class == "SAME_GEOMETRIC_INPUT_REGION":
        return "SAME_PUBLIC_TARGET_NO_ACTIVATION"
    return "PUBLIC_HIT_TARGET_AMBIGUITY_LOCALIZED"


async def _public_destination_hit_region_inventory(page: Any) -> tuple[dict[str, Any], ...]:
    candidates = await page.evaluate(
        """() => {
            const input = document.querySelector('.rc-flight-searchbar input#form_arrCity');
            if (!input) return [];
            const inputRect = input.getBoundingClientRect();
            const scope = input.closest('.rc-flight-searchbar') || input.parentElement;
            const seen = new Set();
            const rows = [];
            const add = (node, kind, depth) => {
                if (!node || seen.has(node)) return;
                const rect = node.getBoundingClientRect();
                const computed = getComputedStyle(node);
                if (rect.width <= 0 || rect.height <= 0 || computed.visibility === 'hidden' || computed.display === 'none') return;
                seen.add(node);
                const rawText = node.value || node.getAttribute('aria-label') || node.innerText || '';
                rows.push({
                    kind, depth, tag: node.tagName.toLowerCase(), role: node.getAttribute('role'),
                    id: node.id || null, class: typeof node.className === 'string' ? node.className : null,
                    text: String(rawText).replace(/\\s+/g, ' ').trim().slice(0, 120),
                    cursor: computed.cursor, pointer_events: computed.pointerEvents,
                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}
                });
            };
            add(input, 'input', 0);
            let ancestor = input.parentElement;
            for (let depth = 1; ancestor && depth <= 4; depth += 1, ancestor = ancestor.parentElement) {
                add(ancestor, 'ancestor', depth);
                if (ancestor === scope) break;
            }
            if (scope) {
                for (const node of scope.querySelectorAll('*')) {
                    if (seen.has(node)) continue;
                    const rect = node.getBoundingClientRect();
                    const overlap = Math.max(0, Math.min(rect.right, inputRect.right) - Math.max(rect.left, inputRect.left))
                        * Math.max(0, Math.min(rect.bottom, inputRect.bottom) - Math.max(rect.top, inputRect.top));
                    const near = rect.right >= inputRect.left - 24 && rect.left <= inputRect.right + 24
                        && rect.bottom >= inputRect.top - 24 && rect.top <= inputRect.bottom + 24;
                    if (overlap > 0 || near) add(node, overlap > 0 ? 'overlap' : 'nearby', 0);
                    if (rows.length >= 24) break;
                }
            }
            return rows;
        }"""
    )
    normalized: list[dict[str, Any]] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("rect"), dict):
            continue
        normalized.append(
            {
                **candidate,
                "text": _truncate_diagnostic_text(str(candidate.get("text") or "")),
                "class": _truncate_diagnostic_text(str(candidate.get("class") or "")),
                "id": _truncate_diagnostic_text(str(candidate.get("id") or "")),
            }
        )
    return _assign_public_hit_region_ids(normalized)


def _annotated_region_svg(
    png_bytes: bytes,
    *,
    clip: dict[str, float],
    regions: Sequence[dict[str, Any]],
) -> str:
    encoded = base64.b64encode(png_bytes).decode("ascii")
    overlays: list[str] = []
    colors = ("#e53935", "#1e88e5", "#43a047", "#8e24aa", "#fb8c00", "#00897b")
    for index, region in enumerate(regions):
        rect = region["rect"]
        x = rect["x"] - clip["x"]
        y = rect["y"] - clip["y"]
        color = colors[index % len(colors)]
        label = html.escape(str(region["region_id"]))
        overlays.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{rect["width"]:.2f}" height="{rect["height"]:.2f}" '
            f'fill="none" stroke="{color}" stroke-width="3"/><text x="{x + 3:.2f}" y="{max(14, y + 14):.2f}" '
            f'font-family="Arial" font-size="13" font-weight="bold" fill="{color}">{label}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{clip["width"]:.0f}" height="{clip["height"]:.0f}" '
        f'viewBox="0 0 {clip["width"]:.2f} {clip["height"]:.2f}">'
        f'<image href="data:image/png;base64,{encoded}" width="100%" height="100%"/>{"".join(overlays)}</svg>'
    )


async def _write_public_hit_region_visual_map(
    page: Any,
    regions: Sequence[dict[str, Any]],
    evidence_output_path: str,
) -> dict[str, str]:
    output = Path(evidence_output_path)
    raw_path = output.with_name(f"{output.stem}.regions.raw.png")
    annotated_path = output.with_name(f"{output.stem}.regions.svg")
    left = min(item["rect"]["x"] for item in regions)
    top = min(item["rect"]["y"] for item in regions)
    right = max(item["rect"]["x"] + item["rect"]["width"] for item in regions)
    bottom = max(item["rect"]["y"] + item["rect"]["height"] for item in regions)
    clip = {
        "x": max(0.0, left - 12),
        "y": max(0.0, top - 12),
        "width": right - left + 24,
        "height": bottom - top + 24,
    }
    await page.screenshot(path=str(raw_path), clip=clip)
    annotated_path.write_text(
        _annotated_region_svg(raw_path.read_bytes(), clip=clip, regions=regions),
        encoding="utf-8",
    )
    return {"raw_screenshot": str(raw_path), "annotated_screenshot": str(annotated_path)}


async def _public_element_summary(element: Any) -> dict[str, Any]:
    summary = await element.evaluate(
        """node => {
            const input = document.querySelector('.rc-flight-searchbar input#form_arrCity');
            const rawText = node.value || node.getAttribute('aria-label') || node.innerText || '';
            return {
                tag: node.tagName.toLowerCase(), role: node.getAttribute('role'), id: node.id || null,
                class: typeof node.className === 'string' ? node.className : null,
                text: String(rawText).replace(/\\s+/g, ' ').trim().slice(0, 120),
                matches_input: node === input,
                contains_input: Boolean(input && node.contains(input))
            };
        }"""
    )
    return {
        **summary,
        "text": _truncate_diagnostic_text(str(summary.get("text") or "")),
        "class": _truncate_diagnostic_text(str(summary.get("class") or "")),
        "id": _truncate_diagnostic_text(str(summary.get("id") or "")),
    }


async def _public_hit_test(page: Any, point: tuple[float, float]) -> tuple[Any | None, dict[str, Any] | None]:
    handle = await page.evaluate_handle("([x, y]) => document.elementFromPoint(x, y)", list(point))
    element = handle.as_element()
    if element is None:
        await handle.dispose()
        return None, None
    return element, await _public_element_summary(element)


async def _public_hit_region_element(page: Any, region: dict[str, Any] | None) -> Any | None:
    if region is None:
        return None
    kind = region.get("kind")
    if kind == "input":
        handle = await page.evaluate_handle(
            "() => document.querySelector('.rc-flight-searchbar input#form_arrCity')"
        )
    elif kind == "ancestor" and isinstance(region.get("depth"), int):
        handle = await page.evaluate_handle(
            """depth => {
                let node = document.querySelector('.rc-flight-searchbar input#form_arrCity');
                for (let index = 0; node && index < depth; index += 1) node = node.parentElement;
                return node;
            }""",
            region["depth"],
        )
    else:
        return None
    element = handle.as_element()
    if element is None:
        await handle.dispose()
    return element


async def _public_city_panel_snapshot(page: Any, requested_destination: str) -> dict[str, Any]:
    snapshot = await page.evaluate(
        """requested => {
            const visible = node => {
                const rect = node.getBoundingClientRect();
                const computed = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0
                    && computed.display !== 'none' && computed.visibility !== 'hidden';
            };
            const markerSets = ['热门城市', 'ABCDE', 'FGHJ', 'KLMNP', 'QRSTW', 'XYZ'];
            const surfaces = [...document.querySelectorAll('body *')].filter(node => {
                if (!visible(node)) return false;
                const text = String(node.innerText || '').replace(/\\s+/g, ' ').trim();
                return markerSets.every(marker => text.includes(marker));
            }).sort((left, right) => {
                const a = left.getBoundingClientRect();
                const b = right.getBoundingClientRect();
                return a.width * a.height - b.width * b.height;
            });
            const surface = surfaces[0];
            if (!surface) return {surface_present: false, candidate_labels: [], requested_visible: false};
            const labels = [];
            for (const node of surface.querySelectorAll('*')) {
                if (!visible(node)) continue;
                const text = String(node.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!/^[\\u4e00-\\u9fff]{2,8}$/.test(text)) continue;
                if (['热门城市', '出发城市', '到达城市'].includes(text)) continue;
                const childHasSameText = [...node.children].some(child => String(child.innerText || '').trim() === text);
                if (!childHasSameText && !labels.includes(text)) labels.push(text);
            }
            return {
                surface_present: true,
                candidate_labels: labels.slice(0, 64),
                requested_visible: labels.includes(requested)
            };
        }""",
        requested_destination,
    )
    if not isinstance(snapshot, dict):
        return {"surface_present": False, "candidate_labels": [], "requested_visible": False}
    labels = snapshot.get("candidate_labels")
    safe_labels = [
        _truncate_diagnostic_text(str(label))
        for label in labels
        if isinstance(label, str)
    ] if isinstance(labels, list) else []
    return {
        "surface_present": snapshot.get("surface_present") is True,
        "candidate_labels": safe_labels,
        "requested_visible": snapshot.get("requested_visible") is True,
    }


async def _diagnose_public_destination_hit_target(
    page: Any,
    requested_destination: str,
    *,
    probe_mode: str,
    evidence_output_path: str | None,
    human_hit_region_id: str | None,
    human_hit_point: tuple[float, float] | None,
    headed_observation_pause_seconds: float,
) -> DestinationCommitmentResult:
    started = time.monotonic()
    regions = await _public_destination_hit_region_inventory(page)
    relationships = _public_hit_region_relationships(regions)
    initial_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    visual_map: dict[str, str] = {}
    if regions and evidence_output_path is not None:
        visual_map = await _write_public_hit_region_visual_map(page, regions, evidence_output_path)
    if headed_observation_pause_seconds > 0:
        await page.wait_for_timeout(int(headed_observation_pause_seconds * 1000))

    resolved_point, resolved_region_id = _human_hit_point_from_evidence(
        regions,
        region_id=human_hit_region_id,
        point=human_hit_point,
    )
    selected_region = next(
        (item for item in regions if item.get("region_id") == resolved_region_id),
        None,
    )
    region_target = None
    region_target_summary = None
    if probe_mode == "differential_click" and selected_region is not None:
        region_target = await _public_hit_region_element(page, selected_region)
        if region_target is not None:
            region_target_summary = await _public_element_summary(region_target)
    hit_summary = None
    if probe_mode == "differential_click" and resolved_point is not None:
        _, hit_summary = await _public_hit_test(page, resolved_point)
    hit_matches_input = hit_summary.get("matches_input") if isinstance(hit_summary, dict) else None
    region_target_matches_input = (
        region_target_summary.get("matches_input")
        if isinstance(region_target_summary, dict)
        else hit_matches_input
    )
    differential_class = _classify_human_hit_differential(
        regions,
        point=resolved_point,
        hit_matches_input=(
            region_target_matches_input if isinstance(region_target_matches_input, bool) else None
        ),
    )
    interaction_completed = False
    interaction_error: str | None = None
    samples: list[dict[str, Any]] = []
    if (
        probe_mode == "differential_click"
        and region_target is not None
        and region_target_matches_input is False
        and differential_class in {"HUMAN_POINT_OUTSIDE_INPUT", "HUMAN_TARGET_DIFFERS"}
    ):
        try:
            await region_target.click()
            interaction_completed = True
        except PlaywrightError as exc:
            interaction_error = type(exc).__name__
        for attempt in range(_FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS):
            candidates = await _collect_destination_suggestion_candidates(page, max_candidates=64)
            panel = await _public_city_panel_snapshot(page, requested_destination)
            surface_present = (
                bool(candidates)
                or await _public_destination_city_selector_surface_present(page)
                or panel["surface_present"]
            )
            candidate_labels = panel["candidate_labels"] or [candidate.label for candidate in candidates]
            samples.append(
                {
                    "sample_index": attempt,
                    "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "surface_present": surface_present,
                    "candidate_count": len(candidate_labels),
                    "candidate_labels": candidate_labels,
                    "requested_candidate_visible": panel["requested_visible"],
                }
            )
            if surface_present:
                break
            if attempt < _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS - 1:
                await page.wait_for_timeout(_FLIGGY_DESTINATION_SUGGESTION_WAIT_MS)
    selector_opened = any(sample["surface_present"] for sample in samples)
    root_class = (
        "HUMAN_REFERENCE_INSUFFICIENT"
        if probe_mode == "visual_map"
        else _public_hit_target_root_class(differential_class, selector_opened=selector_opened)
    )
    diagnostics = {
        "b0_target_snapshot": {"regions": list(regions)},
        "b1_geometry_map": {"relationships": list(relationships)},
        "b2_visual_label_map": {
            "labels": [item["text"] for item in regions if item.get("text")],
        },
        "b3_human_reference_frame": {
            **visual_map,
            "headed": True,
            "human_observation_is_semantic_truth": False,
        },
        "b4_human_point_region": {
            "evidence_type": "HUMAN-SUPPLIED EVIDENCE" if resolved_point is not None else "NOT SUPPLIED",
            "region_id": resolved_region_id,
            "point": list(resolved_point) if resolved_point is not None else None,
        },
        "b5_standard_hit_test": {"target": hit_summary},
        "b6_automated_target_compare": {
            "automated_target": "input#form_arrCity",
            "human_region_target": region_target_summary,
            "region_target_matches_input": region_target_matches_input,
            "standard_hit_target_matches_input": hit_matches_input,
        },
        "b7_optional_controlled_click_probe": {
            "attempted": interaction_completed or interaction_error is not None,
            "interaction_completed": interaction_completed,
            "framework_error": interaction_error,
            "target_count": 1 if interaction_completed or interaction_error is not None else 0,
            "target_region_id": resolved_region_id,
            "retries": 0,
        },
        "b8_selector_observe": {"samples": samples, "selector_opened": selector_opened},
        "b9_differential_class": {"differential_class": differential_class},
        "b10_root_verdict": {"root_class": root_class},
    }
    commit_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    return _destination_commitment_result(
        requested_destination=requested_destination,
        destination_control_ready=bool(regions),
        typed_destination=None,
        candidates=(),
        suggestion_surface_present=selector_opened,
        selected_candidate=None,
        selection_method="diagnostic_only_hit_target_differential",
        commit_readback=commit_readback,
        failure_taxonomy=f"DIAG_U10_R1_{root_class}",
        readback_sequence=(initial_readback, commit_readback),
        suggestion_snapshots=tuple(DestinationSuggestionSnapshot(sample["elapsed_ms"], ()) for sample in samples),
        focused_elapsed_ms=samples[0]["elapsed_ms"] if samples else None,
        typed_elapsed_ms=None,
        headed_pause_ms=int(headed_observation_pause_seconds * 1000),
        overlay_evidence=await _overlay_evidence(page),
        city_selector_opened=selector_opened,
        initial_destination_readback=initial_readback,
        activation_diagnostics=diagnostics,
    )


async def _write_destination_input_text(page: Any, field: Any, requested_destination: str) -> DestinationInputWriteResult:
    await field.click()
    await field.fill("")
    await field.press_sequentially(requested_destination, delay=25)
    first_readback = await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR)
    stability = await _observe_destination_input_write_stability(
        page,
        requested_destination=requested_destination,
        initial_readback=first_readback,
    )
    return DestinationInputWriteResult(
        input_text_after_write=stability["stable_readback"],
        readback_sequence=tuple(stability["readback_sequence"]),
        extension_used=bool(stability["extension_used"]),
        extension_reason=str(stability["extension_reason"]),
    )


async def _collect_destination_suggestion_candidates(page: Any, *, max_candidates: int = 64) -> tuple[DestinationSuggestionCandidate, ...]:
    candidates: list[DestinationSuggestionCandidate] = []
    seen_labels: set[str] = set()
    for selector in _FLIGGY_DESTINATION_SUGGESTION_SELECTORS:
        locator = page.locator(selector)
        count = 0
        with suppress(PlaywrightError):
            count = min(await locator.count(), max_candidates)
        for index in range(count):
            item = locator.nth(index)
            label = None
            with suppress(PlaywrightError):
                if not await item.is_visible():
                    continue
                label = await _read_locator_text(item)
            if label is None:
                continue
            normalized_label = _normalize_destination_label(label)
            if normalized_label in seen_labels:
                continue
            seen_labels.add(normalized_label)
            selectable: bool | str = "unknown"
            with suppress(PlaywrightError):
                selectable = await item.is_enabled()
            candidates.append(
                DestinationSuggestionCandidate(
                    selector=selector,
                    index=index,
                    label=label,
                    selectable=selectable,
                )
            )
            if len(candidates) >= max_candidates:
                return tuple(candidates)
    return tuple(candidates)


async def _public_destination_city_selector_surface_present(page: Any) -> bool:
    for selector in _FLIGGY_DESTINATION_CITY_SELECTOR_SURFACES:
        locator = page.locator(selector)
        with suppress(PlaywrightError):
            if await locator.count() > 0 and await locator.nth(0).is_visible():
                return True
    return False


async def _wait_for_destination_suggestion_candidates(
    page: Any,
    *,
    max_candidates: int = 8,
    attempts: int = _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS,
    wait_ms: int = _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS,
    observation_started: float | None = None,
) -> tuple[tuple[DestinationSuggestionCandidate, ...], tuple[DestinationSuggestionSnapshot, ...]]:
    started = observation_started if observation_started is not None else time.monotonic()
    snapshots: list[DestinationSuggestionSnapshot] = []
    for attempt in range(attempts):
        candidates = await _collect_destination_suggestion_candidates(page, max_candidates=max_candidates)
        snapshots.append(
            DestinationSuggestionSnapshot(
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                candidates=candidates,
            )
        )
        if candidates or attempt == attempts - 1:
            return candidates, tuple(snapshots)
        await page.wait_for_timeout(wait_ms)
    return (), tuple(snapshots)


async def _observe_destination_suggestion_tail(
    page: Any,
    *,
    observation_started: float,
    attempts: int,
    wait_ms: int = _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS,
) -> tuple[DestinationSuggestionSnapshot, ...]:
    snapshots: list[DestinationSuggestionSnapshot] = []
    for _ in range(max(0, attempts)):
        await page.wait_for_timeout(wait_ms)
        candidates = await _collect_destination_suggestion_candidates(page)
        snapshots.append(
            DestinationSuggestionSnapshot(
                elapsed_ms=max(0, int((time.monotonic() - observation_started) * 1000)),
                candidates=candidates,
            )
        )
    return tuple(snapshots)


async def _read_locator_text(locator: Any) -> str | None:
    for expression in (
        "node => node.getAttribute('aria-label')",
        "node => node.getAttribute('title')",
        "node => node.textContent",
        "node => node.value",
    ):
        value = await locator.evaluate(expression)
        if isinstance(value, str) and value.strip():
            return _truncate_diagnostic_text(value)
    return None


def _resolve_destination_candidate(
    candidates: tuple[DestinationSuggestionCandidate, ...],
    requested_destination: str,
    *,
    suggestion_surface_present: bool,
) -> DestinationOptionResolution:
    if not suggestion_surface_present:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_SUGGESTION_NOT_READY")
    exact_matches = [
        candidate
        for candidate in candidates
        if _normalize_destination_label(candidate.label) == _normalize_destination_label(requested_destination)
    ]
    if len(exact_matches) == 1:
        return DestinationOptionResolution(selected_candidate=exact_matches[0], failure_taxonomy=None)
    if len(exact_matches) > 1:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_OPTION_AMBIGUOUS")
    plausible_matches = [
        candidate
        for candidate in candidates
        if _destination_label_contains_requested(candidate.label, requested_destination)
    ]
    if len(plausible_matches) == 1:
        return DestinationOptionResolution(selected_candidate=plausible_matches[0], failure_taxonomy=None)
    if len(plausible_matches) > 1:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_OPTION_AMBIGUOUS")
    return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_OPTION_NOT_FOUND")


def _resolve_public_destination_city_candidate(
    candidates: tuple[DestinationSuggestionCandidate, ...],
    requested_destination: str,
    *,
    selector_surface_present: bool,
) -> DestinationOptionResolution:
    if not selector_surface_present:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_CITY_SELECTOR_NOT_READY")
    exact_matches = tuple(
        candidate for candidate in candidates if _normalize_destination_label(candidate.label) == _normalize_destination_label(requested_destination)
    )
    matches = exact_matches or tuple(
        candidate for candidate in candidates if _destination_label_contains_requested(candidate.label, requested_destination)
    )
    if len(matches) > 1:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_CITY_SELECTOR_AMBIGUOUS")
    if not matches:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_CITY_NOT_FOUND")
    if matches[0].selectable is False:
        return DestinationOptionResolution(selected_candidate=None, failure_taxonomy="DESTINATION_CITY_CANDIDATE_NOT_SELECTABLE")
    return DestinationOptionResolution(selected_candidate=matches[0], failure_taxonomy=None)


def _destination_commitment_result(
    *,
    requested_destination: str,
    destination_control_ready: bool,
    typed_destination: str | None,
    candidates: tuple[DestinationSuggestionCandidate, ...],
    suggestion_surface_present: bool,
    selected_candidate: DestinationSuggestionCandidate | None,
    selection_method: str,
    commit_readback: str | None,
    failure_taxonomy: str | None,
    readback_sequence: tuple[str | None, ...] = (),
    input_text_after_write: str | None = None,
    extension_used: bool = False,
    extension_reason: str = "none",
    suggestion_snapshots: tuple[DestinationSuggestionSnapshot, ...] = (),
    focused_elapsed_ms: int | None = None,
    typed_elapsed_ms: int | None = None,
    headed_pause_ms: int = 0,
    overlay_evidence: tuple[str, ...] = (),
    city_selector_opened: bool = False,
    initial_destination_readback: str | None = None,
    activation_diagnostics: dict[str, Any] | None = None,
) -> DestinationCommitmentResult:
    destination_match = _destination_readback_matches(commit_readback, requested_destination)
    commitment_status = _destination_commitment_status(
        commit_readback,
        requested_destination,
        action_performed=selected_candidate is not None,
        failure_taxonomy=failure_taxonomy,
    )
    effective_failure = failure_taxonomy
    if commitment_status == "confirmed":
        effective_failure = None
    if effective_failure is None and commitment_status != "confirmed":
        effective_failure = "FORM_DESTINATION_MISMATCH" if destination_match is False else "DESTINATION_COMMIT_NOT_CONFIRMED"
    stability_diagnostics = _destination_stability_diagnostics(
        requested_destination=requested_destination,
        destination_control_ready=destination_control_ready,
        input_text_after_write=input_text_after_write if input_text_after_write is not None else typed_destination,
        candidates=candidates,
        selected_candidate=selected_candidate,
        selection_method=selection_method,
        commit_readback=commit_readback,
        commitment_status=commitment_status,
        failure_taxonomy=effective_failure,
        readback_sequence=readback_sequence or ((commit_readback,) if commit_readback is not None else ()),
        extension_used=extension_used,
        extension_reason=extension_reason,
    )
    suggestion_diagnostics = _destination_suggestion_lifecycle_diagnostics(
        requested_destination=requested_destination,
        destination_control_ready=destination_control_ready,
        typed_destination=typed_destination,
        focused_elapsed_ms=focused_elapsed_ms,
        typed_elapsed_ms=typed_elapsed_ms,
        snapshots=suggestion_snapshots,
        selected_candidate=selected_candidate,
        selection_method=selection_method,
        commit_readback=commit_readback,
        commitment_status=commitment_status,
        failure_taxonomy=effective_failure,
        d9_stable_readback=stability_diagnostics["d9_pre_submit_stability"]["stable_readback"],
        headed_pause_ms=headed_pause_ms,
        overlay_evidence=overlay_evidence,
        city_selector_opened=city_selector_opened,
        initial_destination_readback=initial_destination_readback,
    )
    return DestinationCommitmentResult(
        requested_destination=requested_destination,
        destination_control_ready=destination_control_ready,
        typed_destination=typed_destination,
        suggestion_surface_present=suggestion_surface_present,
        suggestion_candidate_count=len(candidates),
        candidate_labels=tuple(candidate.label for candidate in candidates),
        selected_candidate_label=selected_candidate.label if selected_candidate is not None else None,
        selection_method=selection_method,
        commit_readback=commit_readback,
        destination_match=destination_match,
        commitment_status=commitment_status,
        failure_taxonomy=effective_failure,
        destination_stability_diagnostics=stability_diagnostics,
        destination_suggestion_diagnostics=suggestion_diagnostics,
        destination_activation_diagnostics=activation_diagnostics or {},
    )


def _destination_commitment_status(
    readback: str | None,
    requested_destination: str,
    *,
    action_performed: bool,
    failure_taxonomy: str | None,
) -> str:
    destination_match = _destination_readback_matches(readback, requested_destination)
    if destination_match is True and action_performed:
        return "confirmed"
    if destination_match is True:
        return "insufficient"
    if failure_taxonomy in {"DESTINATION_CONTROL_NOT_READY", "DESTINATION_INPUT_WRITE_FAILED", "DESTINATION_OPTION_SELECTION_FAILED"}:
        return "failed"
    if destination_match == "insufficient":
        return "insufficient"
    if action_performed and _is_destination_placeholder(readback):
        return "failed"
    return "mismatch"


def _destination_readback_matches(readback: str | None, requested_destination: str) -> bool | str:
    if readback is None or not readback.strip():
        return "insufficient"
    if _is_destination_placeholder(readback):
        return False
    return _normalize_destination_label(requested_destination) in _normalize_destination_label(readback)


def _public_date_commitment(
    *,
    requested_date: str,
    typed_date: str | None,
    commit_readback: str | None,
    action_performed: bool,
) -> dict[str, str | bool | None]:
    date_match = commit_readback == requested_date
    if date_match and action_performed:
        status = "confirmed"
        failure_taxonomy = None
    elif date_match:
        status = "insufficient"
        failure_taxonomy = "DATE_COMMIT_NOT_CONFIRMED"
    elif commit_readback:
        status = "mismatch"
        failure_taxonomy = "FORM_DATE_MISMATCH"
    else:
        status = "insufficient"
        failure_taxonomy = "DATE_COMMIT_READBACK_MISSING"
    return {
        "requested_date": requested_date,
        "typed_date": typed_date,
        "selection_method": "keyboard_enter_after_public_date_fill" if action_performed else "none",
        "commit_readback": commit_readback,
        "date_match": date_match,
        "commitment_status": status,
        "failure_taxonomy": failure_taxonomy,
    }


def _destination_label_contains_requested(label: str, requested_destination: str) -> bool:
    normalized_label = _normalize_destination_label(label)
    normalized_requested = _normalize_destination_label(requested_destination)
    return normalized_requested in normalized_label


def _normalize_destination_label(value: str) -> str:
    return re.sub(r"\s+", "", value).strip().lower()


def _is_destination_placeholder(readback: str | None) -> bool:
    if readback is None:
        return False
    return "到达城市" in readback and "输入" in readback


async def _capture_public_search_query_state(page: Any, probe_input: ProbeInput) -> PublicSearchQueryState:
    return PublicSearchQueryState(
        requested_origin=probe_input.origin_text,
        requested_destination=probe_input.destination_text,
        requested_departure_date=probe_input.departure_date.isoformat(),
        form_origin_readback=await _read_control_text(page, ".rc-flight-searchbar input#form_depCity"),
        form_destination_readback=await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR),
        form_date_readback=await _read_control_text(page, ".rc-flight-searchbar input#form_depDate"),
    )


async def _read_control_text(page: Any, selector: str) -> str | None:
    locator = page.locator(selector)
    if await locator.count() == 0:
        return None
    first = locator.nth(0)
    for expression in ("node => node.value", "node => node.getAttribute('aria-label')", "node => node.getAttribute('title')", "node => node.textContent"):
        value = await first.evaluate(expression)
        if isinstance(value, str) and value.strip():
            return _truncate_diagnostic_text(value)
    return None


async def _observe_destination_input_write_stability(
    page: Any,
    *,
    requested_destination: str,
    initial_readback: str | None,
    attempts: int = _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS,
    wait_ms: int = _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS,
) -> dict[str, Any]:
    sequence: list[str | None] = [initial_readback]
    if _destination_readback_matches(initial_readback, requested_destination) is True:
        return {
            "readback_sequence": sequence,
            "stable_readback": initial_readback,
            "extension_used": False,
            "extension_reason": "none",
        }

    for _ in range(max(0, attempts - 1)):
        await page.wait_for_timeout(wait_ms)
        sequence.append(await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR))
        if _destination_readback_matches(sequence[-1], requested_destination) is True:
            return {
                "readback_sequence": sequence,
                "stable_readback": sequence[-1],
                "extension_used": False,
                "extension_reason": "none",
            }

    extension_used = False
    extension_reason = "none"
    if _destination_stability_forward_progress(sequence):
        extension_used = True
        extension_reason = "destination_input_write_changed"
        for _ in range(attempts):
            await page.wait_for_timeout(wait_ms)
            sequence.append(await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR))
            if _destination_readback_matches(sequence[-1], requested_destination) is True:
                break

    return {
        "readback_sequence": sequence,
        "stable_readback": _stable_destination_readback(tuple(sequence), requested_destination),
        "extension_used": extension_used,
        "extension_reason": extension_reason,
    }


async def _observe_destination_readback_stability(
    page: Any,
    *,
    requested_destination: str,
    initial_readback: str | None,
    attempts: int = _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS,
    wait_ms: int = _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS,
) -> dict[str, Any]:
    sequence: list[str | None] = [initial_readback]
    if _destination_readback_matches(initial_readback, requested_destination) is not True:
        return {
            "readback_sequence": sequence,
            "stable_readback": initial_readback,
            "extension_used": False,
            "extension_reason": "none",
        }

    for _ in range(max(0, attempts - 1)):
        await page.wait_for_timeout(wait_ms)
        sequence.append(await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR))
        if _destination_readback_matches(sequence[-1], requested_destination) is not True:
            break

    extension_used = False
    extension_reason = "none"
    if _destination_stability_forward_progress(sequence):
        extension_used = True
        extension_reason = "destination_readback_changed"
        for _ in range(attempts):
            await page.wait_for_timeout(wait_ms)
            sequence.append(await _read_control_text(page, _FLIGGY_DESTINATION_INPUT_SELECTOR))
            if _destination_readback_matches(sequence[-1], requested_destination) is True:
                break

    return {
        "readback_sequence": sequence,
        "stable_readback": _stable_destination_readback(tuple(sequence), requested_destination),
        "extension_used": extension_used,
        "extension_reason": extension_reason,
    }


def _combine_destination_extension_reasons(first: str, second: str) -> str:
    reasons = [reason for reason in (first, second) if reason != "none"]
    if not reasons:
        return "none"
    return "+".join(dict.fromkeys(reasons))


def _destination_stability_diagnostics(
    *,
    requested_destination: str,
    destination_control_ready: bool,
    input_text_after_write: str | None,
    candidates: tuple[DestinationSuggestionCandidate, ...],
    selected_candidate: DestinationSuggestionCandidate | None,
    selection_method: str,
    commit_readback: str | None,
    commitment_status: str,
    failure_taxonomy: str | None,
    readback_sequence: tuple[str | None, ...],
    extension_used: bool,
    extension_reason: str,
) -> dict[str, Any]:
    match_decision = _destination_option_match_decision(
        candidates,
        requested_destination,
        selected_candidate=selected_candidate,
        failure_taxonomy=failure_taxonomy,
    )
    stable_readback = _stable_destination_readback(readback_sequence, requested_destination)
    unexpected_value = _unexpected_destination_value(readback_sequence, requested_destination)
    root_cause = _destination_stability_root_cause(
        requested_destination=requested_destination,
        input_text_after_write=input_text_after_write,
        candidates=candidates,
        selected_candidate=selected_candidate,
        selection_method=selection_method,
        commit_readback=commit_readback,
        commitment_status=commitment_status,
        failure_taxonomy=failure_taxonomy,
        readback_sequence=readback_sequence,
        stable_readback=stable_readback,
    )
    return {
        "d0_requested": {"requested_destination": requested_destination},
        "d1_control_ready": {
            "destination_control_ready": destination_control_ready,
            "control_identity_class": "fliggy_destination_input" if destination_control_ready else "unready",
        },
        "d2_input_write": {
            "input_text_after_write": input_text_after_write,
            "input_write_match": _destination_readback_matches(input_text_after_write, requested_destination),
        },
        "d3_suggestion_ready": {
            "suggestion_surface_present": bool(candidates),
            "suggestion_count": len(candidates),
        },
        "d4_option_inventory": {
            "suggestion_count": len(candidates),
            "suggestion_classes": _destination_suggestion_classes(candidates, requested_destination),
            "candidate_labels": [candidate.label for candidate in candidates],
        },
        "d5_option_match": {
            "match_decision": match_decision,
            "matched_option_text": selected_candidate.label if selected_candidate is not None else None,
        },
        "d6_selection_action": {
            "selection_method": selection_method,
            "action_target_text": selected_candidate.label if selected_candidate is not None else None,
        },
        "d7_commit_signal": {
            "commit_signal": _destination_commit_signal(commitment_status, selected_candidate, commit_readback, requested_destination),
        },
        "d8_control_readback": {
            "first_readback": commit_readback,
            "readback_match": _destination_readback_matches(commit_readback, requested_destination),
            "unexpected_value": unexpected_value,
        },
        "d9_pre_submit_stability": {
            "tdest_ms": _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS * _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS,
            "max_observation_ms": _FLIGGY_DESTINATION_SUGGESTION_ATTEMPTS * _FLIGGY_DESTINATION_SUGGESTION_WAIT_MS * 2,
            "readback_sequence": list(readback_sequence),
            "stable_readback": stable_readback,
            "transition_count": _destination_readback_transition_count(readback_sequence),
            "extension_used": extension_used,
            "extension_reason": extension_reason,
        },
        "root_cause_class": root_cause,
    }


def _destination_suggestion_lifecycle_diagnostics(
    *,
    requested_destination: str,
    destination_control_ready: bool,
    typed_destination: str | None,
    focused_elapsed_ms: int | None,
    typed_elapsed_ms: int | None,
    snapshots: tuple[DestinationSuggestionSnapshot, ...],
    selected_candidate: DestinationSuggestionCandidate | None,
    selection_method: str,
    commit_readback: str | None,
    commitment_status: str,
    failure_taxonomy: str | None,
    d9_stable_readback: str | None,
    headed_pause_ms: int,
    overlay_evidence: tuple[str, ...],
    city_selector_opened: bool = False,
    initial_destination_readback: str | None = None,
) -> dict[str, Any]:
    inventory: list[DestinationSuggestionCandidate] = []
    seen: set[tuple[str, int, str]] = set()
    for snapshot in snapshots:
        for candidate in snapshot.candidates:
            key = (candidate.selector, candidate.index, _normalize_destination_label(candidate.label))
            if key not in seen:
                seen.add(key)
                inventory.append(candidate)
    matches = tuple(
        candidate
        for candidate in inventory
        if _destination_label_contains_requested(candidate.label, requested_destination)
    )
    first_surface_elapsed_ms = next(
        (snapshot.elapsed_ms for snapshot in snapshots if snapshot.candidates),
        None,
    )
    classification = _classify_destination_suggestion_surface(
        requested_destination=requested_destination,
        snapshots=snapshots,
        selected_candidate=selected_candidate,
        commitment_status=commitment_status,
    )
    return {
        "u9_public_city_selector": {
            "selector_opened": city_selector_opened,
            "commit_evidence_source": "public_destination_city_selector",
            "initial_destination_readback": initial_destination_readback,
            "candidate_count": len(inventory),
            "candidate_labels": [candidate.label for candidate in inventory],
            "selected_candidate_label": selected_candidate.label if selected_candidate is not None else None,
            "selection_method": selection_method,
            "commit_readback": commit_readback,
            "commitment_status": commitment_status,
            "failure_taxonomy": failure_taxonomy,
        },
        "classification": classification.value,
        "s0_focus": {
            "destination_control_ready": destination_control_ready,
            "elapsed_ms": focused_elapsed_ms,
        },
        "s1_type": {
            "typed_destination": typed_destination,
            "typed_destination_matches": _destination_readback_matches(typed_destination, requested_destination),
            "elapsed_ms": typed_elapsed_ms,
            "extra_repair_action": False,
        },
        "s2_visible_readback": {
            "readback": typed_destination,
            "readback_matches": _destination_readback_matches(typed_destination, requested_destination),
            "elapsed_ms": typed_elapsed_ms,
        },
        "s3_surface_observation": {
            "first_surface_elapsed_ms": first_surface_elapsed_ms,
            "observations": [snapshot.to_dict() for snapshot in snapshots],
        },
        "s4_candidate_inventory": {
            "candidate_count": len(inventory),
            "candidates": [candidate.to_dict() for candidate in inventory],
        },
        "s5_requested_match": {
            "match_visible": bool(matches),
            "matching_labels": [candidate.label for candidate in matches],
        },
        "s6_selection_opportunity": {
            "legitimate_selection_opportunity": selected_candidate is not None,
            "selected_candidate_label": selected_candidate.label if selected_candidate is not None else None,
            "selection_method": selection_method,
            "matching_candidate_selectability": [candidate.selectable for candidate in matches],
        },
        "s7_commit_evidence": {
            "commitment_status": commitment_status,
            "commit_readback": commit_readback,
            "failure_taxonomy": failure_taxonomy,
        },
        "s8_pre_submit_gate": {
            "d9_stable_readback": d9_stable_readback,
            "q1_route_match": "not_observed",
            "q1_date_match": "not_observed",
            "destination_commit_gate": commitment_status == "confirmed",
            "date_commit_gate": "not_observed",
            "submit_allowed": False,
            "submit_executed": False,
        },
        "headed_observation_pause_ms": headed_pause_ms,
        "overlay_evidence": list(overlay_evidence),
        "timestamps_are_relative": True,
        "retries": 0,
    }


def _classify_destination_suggestion_surface(
    *,
    requested_destination: str,
    snapshots: tuple[DestinationSuggestionSnapshot, ...],
    selected_candidate: DestinationSuggestionCandidate | None,
    commitment_status: str,
) -> DestinationSuggestionSurfaceClassification:
    if commitment_status == "confirmed":
        return DestinationSuggestionSurfaceClassification.COMMIT_EVIDENCE_OBTAINED
    if selected_candidate is not None and selected_candidate.selectable is False:
        return DestinationSuggestionSurfaceClassification.MATCH_VISIBLE_NOT_SELECTABLE
    if selected_candidate is not None:
        return DestinationSuggestionSurfaceClassification.COMMITTABLE_MATCH_FOUND
    if not snapshots:
        return DestinationSuggestionSurfaceClassification.UNKNOWN_SURFACE
    surface_indexes = [index for index, snapshot in enumerate(snapshots) if snapshot.candidates]
    if not surface_indexes:
        return DestinationSuggestionSurfaceClassification.NO_SURFACE
    matching = [
        candidate
        for snapshot in snapshots
        for candidate in snapshot.candidates
        if _destination_label_contains_requested(candidate.label, requested_destination)
    ]
    if matching:
        if all(candidate.selectable is False for candidate in matching):
            return DestinationSuggestionSurfaceClassification.MATCH_VISIBLE_NOT_SELECTABLE
        if any(candidate.selectable is True for candidate in matching):
            return DestinationSuggestionSurfaceClassification.COMMITTABLE_MATCH_FOUND
        return DestinationSuggestionSurfaceClassification.UNKNOWN_SURFACE
    first_surface = surface_indexes[0]
    if any(not snapshot.candidates for snapshot in snapshots[first_surface + 1 :]):
        return DestinationSuggestionSurfaceClassification.SURFACE_TRANSIENT
    if first_surface > 0:
        return DestinationSuggestionSurfaceClassification.SURFACE_DELAYED
    return DestinationSuggestionSurfaceClassification.SURFACE_READY_NO_MATCH


def _annotate_destination_s8(
    diagnostics: dict[str, Any],
    verification: PreSubmitQueryVerification,
) -> None:
    commitment = diagnostics.get("destination_commitment")
    if not isinstance(commitment, dict):
        return
    suggestion = commitment.get("destination_suggestion_diagnostics")
    if not isinstance(suggestion, dict):
        return
    s8 = suggestion.get("s8_pre_submit_gate")
    if not isinstance(s8, dict):
        return
    date_commitment = diagnostics.get("date_commitment")
    s8.update(
        {
            "q1_route_match": verification.pre_submit_route_match,
            "q1_date_match": verification.pre_submit_date_match,
            "q1_decision": verification.query_state_decision,
            "date_commit_gate": isinstance(date_commitment, dict)
            and date_commitment.get("commitment_status") == "confirmed",
            "submit_allowed": diagnostics.get("submit_allowed") is True,
            "submit_executed": diagnostics.get("submit_executed") is True,
        }
    )


def classify_destination_suggestion_mode(
    results: Sequence[dict[str, Any]],
) -> DestinationSuggestionSurfaceClassification:
    by_mode: dict[bool, set[str]] = {True: set(), False: set()}
    for result in results:
        diagnostics = result.get("diagnostics") if isinstance(result, dict) else None
        if not isinstance(diagnostics, dict) or not isinstance(diagnostics.get("headless"), bool):
            continue
        commitment = diagnostics.get("destination_commitment")
        suggestion = commitment.get("destination_suggestion_diagnostics") if isinstance(commitment, dict) else None
        classification = suggestion.get("classification") if isinstance(suggestion, dict) else None
        if isinstance(classification, str):
            by_mode[diagnostics["headless"]].add(classification)
    if by_mode[True] and by_mode[False] and by_mode[True] != by_mode[False]:
        return DestinationSuggestionSurfaceClassification.MODE_DEPENDENT_SURFACE
    observed = by_mode[True] | by_mode[False]
    if len(observed) == 1:
        value = next(iter(observed))
        with suppress(ValueError):
            return DestinationSuggestionSurfaceClassification(value)
    return DestinationSuggestionSurfaceClassification.UNKNOWN_SURFACE


def _destination_option_match_decision(
    candidates: tuple[DestinationSuggestionCandidate, ...],
    requested_destination: str,
    *,
    selected_candidate: DestinationSuggestionCandidate | None,
    failure_taxonomy: str | None,
) -> str:
    if selected_candidate is not None:
        return "unique"
    if failure_taxonomy == "DESTINATION_OPTION_AMBIGUOUS":
        return "ambiguous"
    if failure_taxonomy in {"DESTINATION_OPTION_NOT_FOUND", "DESTINATION_SUGGESTION_NOT_READY"}:
        return "not_found"
    exact_matches = [
        candidate
        for candidate in candidates
        if _normalize_destination_label(candidate.label) == _normalize_destination_label(requested_destination)
    ]
    if len(exact_matches) > 1:
        return "ambiguous"
    return "not_found"


def _destination_suggestion_classes(
    candidates: tuple[DestinationSuggestionCandidate, ...],
    requested_destination: str,
) -> list[dict[str, Any]]:
    return [
        {
            "label": candidate.label,
            "selector_class": _truncate_diagnostic_text(candidate.selector, limit=80),
            "matches_requested": _destination_label_contains_requested(candidate.label, requested_destination),
        }
        for candidate in candidates
    ]


def _destination_commit_signal(
    commitment_status: str,
    selected_candidate: DestinationSuggestionCandidate | None,
    commit_readback: str | None,
    requested_destination: str,
) -> str:
    if commitment_status == "confirmed":
        return "readback_matches_requested"
    if selected_candidate is not None and _destination_readback_matches(commit_readback, requested_destination) is not True:
        return "selection_without_matching_readback"
    return "missing"


def _destination_stability_forward_progress(sequence: Sequence[str | None]) -> bool:
    return _destination_readback_transition_count(tuple(sequence)) > 0


def _destination_readback_transition_count(sequence: tuple[str | None, ...]) -> int:
    normalized = [_normalize_destination_label(value or "") for value in sequence]
    return sum(1 for previous, current in pairwise(normalized) if previous != current)


def _stable_destination_readback(sequence: tuple[str | None, ...], requested_destination: str) -> str | None:
    if not sequence:
        return None
    last = sequence[-1]
    if all(_destination_readback_matches(value, requested_destination) is True for value in sequence):
        return last
    if len(sequence) >= 2 and _normalize_destination_label(sequence[-1] or "") == _normalize_destination_label(sequence[-2] or ""):
        return last
    return last


def _unexpected_destination_value(sequence: tuple[str | None, ...], requested_destination: str) -> str | None:
    for value in sequence:
        if value and _destination_readback_matches(value, requested_destination) is False:
            return value
    return None


def _destination_stability_root_cause(
    *,
    requested_destination: str,
    input_text_after_write: str | None,
    candidates: tuple[DestinationSuggestionCandidate, ...],
    selected_candidate: DestinationSuggestionCandidate | None,
    selection_method: str,
    commit_readback: str | None,
    commitment_status: str,
    failure_taxonomy: str | None,
    readback_sequence: tuple[str | None, ...],
    stable_readback: str | None,
) -> str:
    if _destination_readback_matches(input_text_after_write, requested_destination) is False:
        return "DESTINATION_INPUT_WRITE_DRIFT"
    if failure_taxonomy == "DESTINATION_CONTROL_NOT_READY":
        return "DESTINATION_CONTROL_IDENTITY_DRIFT"
    if failure_taxonomy == "DESTINATION_OPTION_AMBIGUOUS":
        return "DESTINATION_OPTION_AMBIGUOUS"
    if failure_taxonomy == "DESTINATION_OPTION_NOT_FOUND":
        return "DESTINATION_OPTION_NOT_FOUND"
    if failure_taxonomy == "DESTINATION_SUGGESTION_NOT_READY":
        return "DESTINATION_SUGGESTION_STALE_OR_HISTORY"
    if selected_candidate is not None and _destination_readback_matches(commit_readback, requested_destination) is False:
        if _destination_label_contains_requested(selected_candidate.label, requested_destination):
            return "DESTINATION_SELECTION_ACTION_MISAPPLIED"
        return "DESTINATION_OPTION_MATCH_WRONG"
    if (
        len(readback_sequence) >= 2
        and _destination_readback_matches(readback_sequence[0], requested_destination) is True
        and _destination_readback_matches(readback_sequence[-1], requested_destination) is False
    ):
        return "DESTINATION_POST_COMMIT_RESET"
    if commitment_status != "confirmed" and selection_method != "none":
        return "DESTINATION_COMMIT_SIGNAL_MISSING"
    if _destination_readback_matches(stable_readback, requested_destination) is not True:
        return "DESTINATION_PRE_SUBMIT_STABILITY_GAP"
    if candidates and selected_candidate is None:
        return "MULTI_FACTOR_DESTINATION_GAP"
    return "INCONCLUSIVE"


def _verify_pre_submit_query_state(query_state: PublicSearchQueryState) -> PreSubmitQueryVerification:
    route_match = _readback_contains(query_state.form_origin_readback, query_state.requested_origin) and _readback_contains(
        query_state.form_destination_readback,
        query_state.requested_destination,
    )
    date_match = _readback_date_matches(query_state.form_date_readback, query_state.requested_departure_date)
    if route_match is None or date_match is None:
        failure = "FORM_STATE_UNREADABLE" if _query_state_unreadable(query_state) else "FORM_STATE_INSUFFICIENT"
        return PreSubmitQueryVerification(
            pre_submit_route_match=route_match if route_match is not None else "insufficient",
            pre_submit_date_match=date_match if date_match is not None else "insufficient",
            submit_allowed=False,
            failure_taxonomy=failure,
            query_state_decision="insufficient",
        )
    if route_match and date_match:
        return PreSubmitQueryVerification(
            pre_submit_route_match=True,
            pre_submit_date_match=True,
            submit_allowed=True,
            failure_taxonomy=None,
            query_state_decision="match",
        )
    failure = (
        "FORM_ROUTE_AND_DATE_MISMATCH"
        if not route_match and not date_match
        else "FORM_ROUTE_MISMATCH"
        if not route_match
        else "FORM_DATE_MISMATCH"
    )
    return PreSubmitQueryVerification(
        pre_submit_route_match=route_match,
        pre_submit_date_match=date_match,
        submit_allowed=False,
        failure_taxonomy=failure,
        query_state_decision="mismatch",
    )


def _readback_contains(readback: str | None, expected: str) -> bool | None:
    if readback is None or not readback.strip():
        return None
    return expected in readback


def _readback_date_matches(readback: str | None, expected: str) -> bool | None:
    if readback is None or not readback.strip():
        return None
    try:
        expected_date = date.fromisoformat(expected)
    except ValueError:
        return None
    observed = _normalize_date_marker(readback, ProbeInput("北京", "上海", expected_date))
    if observed is None:
        return None
    return observed == expected


def _query_state_unreadable(query_state: PublicSearchQueryState) -> bool:
    return (
        query_state.form_origin_readback is None
        or query_state.form_destination_readback is None
        or query_state.form_date_readback is None
    )


def _annotate_post_submit_query_propagation(diagnostics: dict[str, Any], handoff_diagnostics: dict[str, Any]) -> None:
    verification = diagnostics.get("pre_submit_query_verification")
    pre_submit_matched = isinstance(verification, dict) and verification.get("query_state_decision") == "match"
    post_submit_matched = handoff_diagnostics.get("context_match") is True
    diagnostics["post_submit_route_match"] = handoff_diagnostics.get("route_match")
    diagnostics["post_submit_date_match"] = handoff_diagnostics.get("date_match")
    diagnostics["post_submit_query_identity_decision"] = handoff_diagnostics.get("query_identity_decision")
    diagnostics["post_submit_mismatch_dimension"] = handoff_diagnostics.get("mismatch_dimension")
    diagnostics["post_submit_propagation_failed"] = bool(pre_submit_matched and not post_submit_matched)
    if diagnostics["post_submit_propagation_failed"]:
        result_state_failure = handoff_diagnostics.get("result_state_failure_taxonomy")
        if isinstance(result_state_failure, str):
            diagnostics["post_submit_failure_taxonomy"] = result_state_failure
        elif handoff_diagnostics.get("mismatch_dimension") in {"route", "date", "both"}:
            diagnostics["post_submit_failure_taxonomy"] = "RESULT_QUERY_MISMATCH"
        else:
            diagnostics["post_submit_failure_taxonomy"] = "SUBMIT_STATE_PROPAGATION_FAILED"
    else:
        diagnostics["post_submit_failure_taxonomy"] = None


def _build_post_submit_query_state_diagnostics(diagnostics: dict[str, Any], handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    pre_submit_state = diagnostics.get("pre_submit_query_state")
    pre_submit_verification = diagnostics.get("pre_submit_query_verification")
    destination_commitment = diagnostics.get("destination_commitment")
    q3_nav_state = _q3_post_submit_nav_state(handoff_diagnostics)
    q4_result_state = _q4_result_state_init(handoff_diagnostics)
    q5_result_context = _q5_result_context(handoff_diagnostics)
    first_mismatch = _first_post_submit_mismatch_checkpoint(q3_nav_state, q4_result_state, q5_result_context)
    mismatch_dimension = _post_submit_mismatch_dimension(q3_nav_state, q4_result_state, q5_result_context)
    stale_source = _stale_destination_taxonomy_source(destination_commitment, diagnostics)
    diag_u6_h0_h8 = _diag_u6_h0_h8(diagnostics, handoff_diagnostics)
    diag_u7_c0_c8 = _diag_u7_c0_c8(diagnostics, handoff_diagnostics)
    return {
        "q0_requested": _q0_requested_query(pre_submit_state),
        "q1_pre_submit": _q1_pre_submit_query(pre_submit_state, pre_submit_verification),
        "q2_submit_action": {
            "submit_action_observed": diagnostics.get("submit_executed") is True,
            "method": "public_search_button" if diagnostics.get("submit_executed") is True else "none",
        },
        "q3_post_submit_nav_state": q3_nav_state,
        "q4_result_state_init": q4_result_state,
        "q5_result_context": q5_result_context,
        "first_mismatch_checkpoint": first_mismatch,
        "mismatch_dimension": mismatch_dimension,
        "propagation_decision": _post_submit_propagation_decision(first_mismatch),
        "diagnostic_state_consistent": stale_source is None,
        "stale_taxonomy_source": stale_source,
        "root_cause_class": _post_submit_root_cause_class(first_mismatch, stale_source, diagnostics),
        "diag_u4_p0_p7": _diag_u4_p0_p7(diagnostics, handoff_diagnostics),
        "diag_u6_h0_h8": diag_u6_h0_h8,
        "diag_u6_root_cause_class": _diag_u6_root_cause_class(diag_u6_h0_h8),
        "diag_u7_c0_c8": diag_u7_c0_c8,
        "diag_u7_root_cause_class": _diag_u7_root_cause_class(diag_u7_c0_c8),
    }


def _q0_requested_query(pre_submit_state: Any) -> dict[str, Any]:
    if not isinstance(pre_submit_state, dict):
        return {"origin": None, "destination": None, "departure_date": None}
    return {
        "origin": pre_submit_state.get("requested_origin"),
        "destination": pre_submit_state.get("requested_destination"),
        "departure_date": pre_submit_state.get("requested_departure_date"),
    }


def _q1_pre_submit_query(pre_submit_state: Any, pre_submit_verification: Any) -> dict[str, Any]:
    if not isinstance(pre_submit_state, dict):
        query = {"origin": None, "destination": None, "departure_date": None}
    else:
        query = {
            "origin": pre_submit_state.get("form_origin_readback"),
            "destination": pre_submit_state.get("form_destination_readback"),
            "departure_date": pre_submit_state.get("form_date_readback"),
        }
    verified = isinstance(pre_submit_verification, dict) and pre_submit_verification.get("query_state_decision") == "match"
    return {"query": query, "verified": verified}


def _diag_u4_p0_p7(diagnostics: dict[str, Any], handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "p0_source_pre_submit": {
            "query": _q0_requested_query(diagnostics.get("pre_submit_query_state")),
            "verified": bool(
                isinstance(diagnostics.get("pre_submit_query_verification"), dict)
                and diagnostics["pre_submit_query_verification"].get("query_state_decision") == "match"
            ),
        },
        "p1_submit_trigger": {
            "submit_action_observed": diagnostics.get("submit_executed") is True,
            "method": "public_search_button" if diagnostics.get("submit_executed") is True else "none",
        },
        "p2_context_created": {
            "context_count": handoff_diagnostics.get("context_count", 0),
            "popup_or_new_page_event": handoff_diagnostics.get("popup_or_new_page_event"),
            "context_candidates": handoff_diagnostics.get("context_candidates", []),
        },
        "p3_context_selected": {
            "selected_context_id": handoff_diagnostics.get("selected_context_id"),
            "selected_page_identity": handoff_diagnostics.get("selected_page_identity"),
            "selection_reason": handoff_diagnostics.get("selection_reason"),
        },
        "p4_initial_page_state": _first_result_state_sample(handoff_diagnostics),
        "p5_load_progress": {
            "base_deadline_reached": handoff_diagnostics.get("result_state_base_deadline_reached"),
            "extension_used": handoff_diagnostics.get("result_state_extension_used"),
            "extension_reason": handoff_diagnostics.get("result_state_extension_reason"),
            "marker_transition_count": _marker_transition_count(tuple(handoff_diagnostics.get("result_state_samples") or ())),
        },
        "p6_settled_result_state": {
            "settled_state_reached": handoff_diagnostics.get("settled_state_reached"),
            "result_surface_present": handoff_diagnostics.get("result_surface_present"),
            "page_closed_or_replaced": handoff_diagnostics.get("page_closed_or_replaced"),
            "failure_taxonomy": handoff_diagnostics.get("result_state_failure_taxonomy"),
            "diagnostic_root_cause_class": handoff_diagnostics.get("diagnostic_root_cause_class"),
        },
        "p7_query_identity": _q4_result_state_init(handoff_diagnostics),
    }


def _diag_u6_h0_h8(diagnostics: dict[str, Any], handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    pre_submit_state = diagnostics.get("pre_submit_query_state")
    pre_submit_verification = diagnostics.get("pre_submit_query_verification")
    samples = tuple(handoff_diagnostics.get("result_state_samples") or ())
    context_candidates = handoff_diagnostics.get("context_candidates") or []
    h0_query = _q0_requested_query(pre_submit_state)
    h1_query = _q1_pre_submit_query(pre_submit_state, pre_submit_verification)
    h2_state = {
        "submit_action_observed": diagnostics.get("submit_executed") is True,
        "method": "public_search_button" if diagnostics.get("submit_executed") is True else "none",
        "source_state_after_trigger": h1_query["query"],
        "source_state_changed_at_submit": _source_state_changed_at_submit(h0_query, h1_query["query"]),
    }
    h3_event = {
        "page_count_before_submit": handoff_diagnostics.get("page_count_before_submit"),
        "page_count_after_input_before_submit": handoff_diagnostics.get("page_count_after_input_before_submit"),
        "pre_submit_context_count_changed": handoff_diagnostics.get("pre_submit_context_count_changed"),
        "page_count_after_submit": handoff_diagnostics.get("page_count_after_submit"),
        "popup_or_new_page_event": handoff_diagnostics.get("popup_or_new_page_event"),
        "context_inventory": context_candidates,
    }
    return {
        "h0_verified_source_query": {
            "checkpoint": "H0_VERIFIED_SOURCE_QUERY",
            "query": h0_query,
            "verified": h1_query["verified"],
        },
        "h1_source_public_state": {
            "checkpoint": "H1_SOURCE_PUBLIC_STATE",
            "query": h1_query["query"],
            "matches_h0": h1_query["verified"],
        },
        "h2_submit_trigger": {
            "checkpoint": "H2_SUBMIT_TRIGGER",
            **h2_state,
        },
        "h3_handoff_event": {
            "checkpoint": "H3_HANDOFF_EVENT",
            **h3_event,
        },
        "h4_new_context_initial_state": {
            "checkpoint": "H4_NEW_CONTEXT_INITIAL_STATE",
            "first_sample": samples[0] if samples else None,
            "earliest_candidate_states": context_candidates,
        },
        "h5_initialization_transitions": {
            "checkpoint": "H5_INITIALIZATION_TRANSITIONS",
            "samples": list(samples),
            "marker_transition_count": _marker_transition_count(samples),
            "extension_used": handoff_diagnostics.get("result_state_extension_used"),
            "extension_reason": handoff_diagnostics.get("result_state_extension_reason"),
            "base_window_ms": handoff_diagnostics.get("result_state_base_window_ms"),
            "max_observation_ms": handoff_diagnostics.get("result_state_max_observation_ms"),
            "retries": 0,
        },
        "h6_stale_default_introduction": {
            "checkpoint": "H6_STALE_DEFAULT_INTRODUCTION",
            "first_stale_default": _first_sample_with_failure(samples, "RESULT_STATE_STALE_OR_DEFAULT"),
            "first_correct_identity": _first_correct_identity_sample(samples),
            "stale_or_default_result_stabilized": handoff_diagnostics.get("stale_or_default_result_stabilized"),
        },
        "h7_settled_context_state": {
            "checkpoint": "H7_SETTLED_CONTEXT_STATE",
            "settled_state_reached": handoff_diagnostics.get("settled_state_reached"),
            "selected_context_id": handoff_diagnostics.get("selected_context_id"),
            "failure_taxonomy": handoff_diagnostics.get("result_state_failure_taxonomy"),
            "diagnostic_root_cause_class": handoff_diagnostics.get("diagnostic_root_cause_class"),
            "context_match": handoff_diagnostics.get("context_match"),
        },
        "h8_strict_identity": {
            "checkpoint": "H8_STRICT_IDENTITY",
            "route_match": handoff_diagnostics.get("route_match"),
            "date_match": handoff_diagnostics.get("date_match"),
            "context_match": handoff_diagnostics.get("context_match"),
            "query_identity_decision": handoff_diagnostics.get("query_identity_decision"),
            "selection_reason": handoff_diagnostics.get("selection_reason"),
            "mismatch_dimension": handoff_diagnostics.get("mismatch_dimension"),
        },
        "public_overlay_evidence": {
            "modal_presence": _modal_presence_from_diagnostics(diagnostics),
            "permission_prompt_presence": _permission_prompt_presence_from_diagnostics(diagnostics),
            "blocking_evidence": "not_proven",
        },
    }


def _source_state_changed_at_submit(requested: dict[str, Any], observed: dict[str, Any]) -> bool | str:
    keys = ("origin", "destination", "departure_date")
    if any(requested.get(key) is None or observed.get(key) is None for key in keys):
        return "insufficient"
    return any(requested.get(key) != observed.get(key) for key in keys)


def _first_sample_with_failure(samples: tuple[dict[str, Any], ...], failure_taxonomy: str) -> dict[str, Any] | None:
    return next((sample for sample in samples if sample.get("failure_taxonomy") == failure_taxonomy), None)


def _first_correct_identity_sample(samples: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    return next(
        (
            sample
            for sample in samples
            if sample.get("selected_context_id") is not None
            or (sample.get("route_match") is True and sample.get("date_match") is True and sample.get("result_surface_present") is True)
        ),
        None,
    )


def _modal_presence_from_diagnostics(diagnostics: dict[str, Any]) -> bool | str:
    readiness = diagnostics.get("search_form_readiness")
    if not isinstance(readiness, dict):
        return "insufficient"
    overlay = readiness.get("overlay_evidence")
    if not isinstance(overlay, list):
        return "insufficient"
    return any("modal" in str(item).lower() or "dialog" in str(item).lower() for item in overlay)


def _permission_prompt_presence_from_diagnostics(diagnostics: dict[str, Any]) -> bool | str:
    readiness = diagnostics.get("search_form_readiness")
    if not isinstance(readiness, dict):
        return "insufficient"
    overlay = readiness.get("overlay_evidence")
    if not isinstance(overlay, list):
        return "insufficient"
    return any("permission" in str(item).lower() or "location" in str(item).lower() for item in overlay)


def _diag_u6_root_cause_class(diag_u6: dict[str, Any]) -> str:
    h0 = diag_u6["h0_verified_source_query"]
    h1 = diag_u6["h1_source_public_state"]
    h2 = diag_u6["h2_submit_trigger"]
    h3 = diag_u6["h3_handoff_event"]
    h4 = diag_u6["h4_new_context_initial_state"]
    h6 = diag_u6["h6_stale_default_introduction"]
    h7 = diag_u6["h7_settled_context_state"]
    h8 = diag_u6["h8_strict_identity"]
    overlay = diag_u6["public_overlay_evidence"]
    if h0.get("verified") is not True or h1.get("matches_h0") is not True:
        return "SOURCE_PUBLIC_STATE_NOT_COMMITTED"
    if h2.get("source_state_changed_at_submit") is True:
        return "SOURCE_STATE_RESET_ON_SUBMIT"
    if overlay.get("blocking_evidence") == "modal":
        return "MODAL_BLOCKS_QUERY_INITIALIZATION"
    if overlay.get("blocking_evidence") == "permission":
        return "PERMISSION_PROMPT_BLOCKS_QUERY_INITIALIZATION"
    if h6.get("first_correct_identity") is not None and h8.get("context_match") is not True:
        if h7.get("failure_taxonomy") == "RESULT_STATE_STALE_OR_DEFAULT":
            return "RESULT_STATE_READER_OBSERVES_WRONG_CONTEXT"
        return "CORRECT_CONTEXT_EXISTS_BUT_NOT_SELECTED"
    first_sample = h4.get("first_sample")
    if isinstance(first_sample, dict) and first_sample.get("failure_taxonomy") == "RESULT_STATE_STALE_OR_DEFAULT":
        if first_sample.get("attempt") == 1:
            return "NEW_CONTEXT_INITIALIZED_STALE"
        return "STALE_DEFAULT_RESTORED_DURING_INITIALIZATION"
    first_stale = h6.get("first_stale_default")
    if first_stale is not None:
        return "STALE_DEFAULT_RESTORED_DURING_INITIALIZATION"
    if h3.get("popup_or_new_page_event") is True and h8.get("context_match") is not True:
        return "SUBMIT_HANDOFF_QUERY_STATE_MISSING"
    return "INCONCLUSIVE"


def _q3_post_submit_nav_state(handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    selected_url = str(handoff_diagnostics.get("selected_page_url") or "")
    params = _extract_public_route_date_params(selected_url)
    return {
        "url_class": _url_class(selected_url),
        "query_params": params,
        "route_match": _match_if_observed(params.get("origin"), params.get("destination"), handoff_diagnostics.get("route_match")),
        "date_match": _date_match_if_observed(params.get("departure_date"), handoff_diagnostics.get("date_match")),
        "page_count_after_submit": handoff_diagnostics.get("page_count_after_submit"),
        "popup_or_new_page_event": handoff_diagnostics.get("popup_or_new_page_event"),
    }


def _q4_result_state_init(handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "result_surface": handoff_diagnostics.get("result_surface_present"),
        "route_match": handoff_diagnostics.get("route_match"),
        "date_match": handoff_diagnostics.get("date_match"),
        "observed_date_text": handoff_diagnostics.get("observed_date_text"),
        "observed_date_source": handoff_diagnostics.get("observed_date_source"),
        "normalized_observed_date": handoff_diagnostics.get("normalized_observed_date"),
        "date_parse_status": handoff_diagnostics.get("date_parse_status"),
    }


def _q5_result_context(handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_match": handoff_diagnostics.get("context_match"),
        "route_match": handoff_diagnostics.get("route_match"),
        "date_match": handoff_diagnostics.get("date_match"),
        "query_identity_decision": handoff_diagnostics.get("query_identity_decision"),
        "selection_reason": handoff_diagnostics.get("selection_reason"),
        "mismatch_dimension": handoff_diagnostics.get("mismatch_dimension"),
    }


def _diag_u7_c0_c8(diagnostics: dict[str, Any], handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    run_date = _coerce_run_local_date(diagnostics.get("acquired_at")) or datetime.now(UTC).date()
    default_signature = _fliggy_default_query_signature(run_date)
    pre_submit_state = diagnostics.get("pre_submit_query_state")
    pre_submit_verification = diagnostics.get("pre_submit_query_verification")
    public_commit_classification = _public_commit_state_classification(diagnostics, pre_submit_state, pre_submit_verification)
    candidates = tuple(handoff_diagnostics.get("candidate_pages") or ())
    samples = tuple(handoff_diagnostics.get("result_state_samples") or ())
    earliest_state = samples[0] if samples else None
    settled_state = _public_query_classification_payload(
        handoff_diagnostics,
        requested_query=_q0_requested_query(pre_submit_state),
        default_signature=default_signature,
    )
    return {
        "c0_requested_query": {
            "checkpoint": "C0_REQUESTED_QUERY",
            "query": _q0_requested_query(pre_submit_state),
        },
        "c1_visible_pre_q1_state": {
            "checkpoint": "C1_VISIBLE_PRE_Q1_STATE",
            **_q1_pre_submit_query(pre_submit_state, pre_submit_verification),
        },
        "c2_public_commit_state": {
            "checkpoint": "C2_PUBLIC_COMMIT_STATE",
            "classification": public_commit_classification.value,
            "destination_commitment": diagnostics.get("destination_commitment"),
            "date_commitment": diagnostics.get("date_commitment"),
        },
        "c3_immediate_pre_click_state": {
            "checkpoint": "C3_IMMEDIATE_PRE_CLICK_STATE",
            "page_count_before_submit": handoff_diagnostics.get("page_count_before_submit"),
            "page_count_after_input_before_submit": handoff_diagnostics.get("page_count_after_input_before_submit"),
            "pre_submit_context_count_changed": handoff_diagnostics.get("pre_submit_context_count_changed"),
        },
        "c4_public_click": {
            "checkpoint": "C4_PUBLIC_CLICK",
            "submit_allowed": diagnostics.get("submit_allowed"),
            "submit_executed": diagnostics.get("submit_executed"),
            "public_submit_button_clicked_once": diagnostics.get("public_submit_button_clicked_once"),
            "retries": diagnostics.get("retries", 0),
        },
        "c5_earliest_post_click_context": {
            "checkpoint": "C5_EARLIEST_POST_CLICK_CONTEXT",
            "state": earliest_state,
            "classification": _public_query_classification_payload(
                earliest_state if isinstance(earliest_state, dict) else {},
                requested_query=_q0_requested_query(pre_submit_state),
                default_signature=default_signature,
            ),
        },
        "c6_context_lifecycle_transitions": {
            "checkpoint": "C6_CONTEXT_LIFECYCLE_TRANSITIONS",
            "context_candidates": candidates,
            "samples": list(samples),
        },
        "c7_settled_result_classification": {
            "checkpoint": "C7_SETTLED_RESULT_CLASSIFICATION",
            "classification": settled_state,
        },
        "c8_strict_q5": {
            "checkpoint": "C8_STRICT_Q5",
            **_q5_result_context(handoff_diagnostics),
        },
        "provider_default_signature": default_signature,
    }


def _public_commit_state_classification(
    diagnostics: dict[str, Any],
    pre_submit_state: Any,
    pre_submit_verification: Any,
) -> PublicQueryClassification:
    destination_commitment = diagnostics.get("destination_commitment")
    date_commitment = diagnostics.get("date_commitment")
    destination_confirmed = isinstance(destination_commitment, dict) and destination_commitment.get("commitment_status") == "confirmed"
    date_confirmed = isinstance(date_commitment, dict) and date_commitment.get("commitment_status") == "confirmed"
    q1_verified = isinstance(pre_submit_verification, dict) and pre_submit_verification.get("submit_allowed") is True
    if destination_confirmed and date_confirmed and q1_verified:
        return PublicQueryClassification.REQUESTED_QUERY
    if destination_confirmed or date_confirmed or q1_verified:
        return PublicQueryClassification.PARTIAL_QUERY
    if isinstance(pre_submit_state, dict) and any(pre_submit_state.get(key) for key in ("form_origin_readback", "form_destination_readback", "form_date_readback")):
        return PublicQueryClassification.STALE_QUERY
    return PublicQueryClassification.UNKNOWN_QUERY


def _diag_u7_root_cause_class(diag_u7: dict[str, Any]) -> str:
    c1 = diag_u7["c1_visible_pre_q1_state"]
    c4 = diag_u7["c4_public_click"]
    c7 = diag_u7["c7_settled_result_classification"]["classification"]
    c8 = diag_u7["c8_strict_q5"]
    if c1.get("verified") is not True:
        return "VISIBLE_Q1_NOT_VERIFIED"
    if c4.get("submit_executed") is not True:
        return "PUBLIC_SUBMIT_NOT_EXECUTED"
    if c7.get("classification") == PublicQueryClassification.DEFAULT_QUERY.value and c8.get("context_match") is not True:
        return "DEFAULT_QUERY_FALLBACK_LOCALIZED"
    if c7.get("classification") == PublicQueryClassification.STALE_QUERY.value and c8.get("context_match") is not True:
        return "RESULT_CONTEXT_STALE_STATE_LOCALIZED"
    if c7.get("classification") == PublicQueryClassification.PARTIAL_QUERY.value:
        return "PARTIAL_PUBLIC_QUERY_COMMIT_LOCALIZED"
    if c7.get("classification") == PublicQueryClassification.REQUESTED_QUERY.value and c8.get("context_match") is True:
        return "REQUESTED_QUERY_REACHED_RESULT_CONTEXT"
    return "DIAGNOSTIC_INSUFFICIENT"


def _public_query_classification_payload(
    evidence: dict[str, Any],
    *,
    requested_query: dict[str, Any],
    default_signature: dict[str, str],
) -> dict[str, Any]:
    classification = _classify_public_query_state(evidence, requested_query=requested_query, default_signature=default_signature)
    return {
        "classification": classification.value,
        "observed_route_origin": evidence.get("observed_route_origin"),
        "observed_route_destination": evidence.get("observed_route_destination"),
        "observed_route_text": evidence.get("observed_route_text"),
        "normalized_observed_date": evidence.get("normalized_observed_date"),
        "route_match": evidence.get("route_match"),
        "date_match": evidence.get("date_match"),
    }


def _classify_public_query_state(
    evidence: dict[str, Any],
    *,
    requested_query: dict[str, Any],
    default_signature: dict[str, str],
) -> PublicQueryClassification:
    requested_route = (
        evidence.get("route_match") is True
        or (
            evidence.get("observed_route_origin") == requested_query.get("origin")
            and evidence.get("observed_route_destination") == requested_query.get("destination")
        )
    )
    requested_date = evidence.get("date_match") is True or evidence.get("normalized_observed_date") == requested_query.get("departure_date")
    default_route = (
        evidence.get("observed_route_origin") == default_signature["origin"]
        and evidence.get("observed_route_destination") == default_signature["destination"]
    )
    default_date = evidence.get("normalized_observed_date") == default_signature["departure_date"]
    if requested_route and requested_date:
        return PublicQueryClassification.REQUESTED_QUERY
    if default_route and default_date:
        return PublicQueryClassification.DEFAULT_QUERY
    if requested_route or requested_date or default_route or default_date:
        return PublicQueryClassification.PARTIAL_QUERY
    has_route = isinstance(evidence.get("observed_route_origin"), str) and isinstance(evidence.get("observed_route_destination"), str)
    has_date = isinstance(evidence.get("normalized_observed_date"), str)
    if has_route or has_date:
        return PublicQueryClassification.STALE_QUERY
    return PublicQueryClassification.UNKNOWN_QUERY


def _fliggy_default_query_signature(run_date: date) -> dict[str, str]:
    return {
        "origin": _FLIGGY_DEFAULT_ORIGIN_TEXT,
        "destination": _FLIGGY_DEFAULT_DESTINATION_TEXT,
        "departure_date": (run_date + timedelta(days=1)).isoformat(),
    }


def _coerce_run_local_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        with suppress(ValueError):
            return datetime.fromisoformat(value).date()
    return None


def _extract_public_route_date_params(url: str) -> dict[str, str | None]:
    allowed = {
        "depCity": "origin",
        "depCityName": "origin",
        "from": "origin",
        "fromCity": "origin",
        "orgCity": "origin",
        "arrCity": "destination",
        "arrCityName": "destination",
        "to": "destination",
        "toCity": "destination",
        "dstCity": "destination",
        "depDate": "departure_date",
        "date": "departure_date",
        "departureDate": "departure_date",
    }
    extracted: dict[str, str | None] = {"origin": None, "destination": None, "departure_date": None}
    for key, value in parse_qsl(urlsplit(url).query):
        target = allowed.get(key)
        if target is not None and extracted[target] is None:
            extracted[target] = _truncate_diagnostic_text(value)
    return extracted


def _match_if_observed(origin: str | None, destination: str | None, fallback: Any) -> bool | str:
    if origin is None and destination is None:
        return "insufficient"
    if isinstance(fallback, bool):
        return fallback
    return "insufficient"


def _date_match_if_observed(departure_date: str | None, fallback: Any) -> bool | str:
    if departure_date is None:
        return "insufficient"
    if isinstance(fallback, bool):
        return fallback
    return "insufficient"


def _first_post_submit_mismatch_checkpoint(
    q3_nav_state: dict[str, Any],
    q4_result_state: dict[str, Any],
    q5_result_context: dict[str, Any],
) -> str:
    if _checkpoint_has_mismatch(q3_nav_state):
        return "Q3_POST_SUBMIT_NAV_STATE"
    if _checkpoint_has_mismatch(q4_result_state):
        return "Q4_RESULT_STATE_INIT"
    if _checkpoint_has_mismatch(q5_result_context):
        return "Q5_RESULT_CONTEXT"
    if q5_result_context.get("context_match") is True:
        return "none"
    return "unknown"


def _checkpoint_has_mismatch(checkpoint: dict[str, Any]) -> bool:
    return checkpoint.get("route_match") is False or checkpoint.get("date_match") is False or checkpoint.get("context_match") is False


def _post_submit_mismatch_dimension(
    q3_nav_state: dict[str, Any],
    q4_result_state: dict[str, Any],
    q5_result_context: dict[str, Any],
) -> str:
    for checkpoint in (q3_nav_state, q4_result_state, q5_result_context):
        dimension = _checkpoint_mismatch_dimension(checkpoint)
        if dimension != "unknown":
            return dimension
    return "unknown"


def _checkpoint_mismatch_dimension(checkpoint: dict[str, Any]) -> str:
    route = checkpoint.get("route_match")
    date_value = checkpoint.get("date_match")
    if route is False and date_value is False:
        return "both"
    if route is False:
        return "route"
    if date_value is False:
        return "date"
    if checkpoint.get("mismatch_dimension") in {"route", "date", "both"}:
        return str(checkpoint["mismatch_dimension"])
    return "unknown"


def _post_submit_propagation_decision(first_mismatch_checkpoint: str) -> str:
    if first_mismatch_checkpoint == "none":
        return "preserved"
    if first_mismatch_checkpoint in {"Q3_POST_SUBMIT_NAV_STATE", "Q4_RESULT_STATE_INIT"}:
        return "lost"
    if first_mismatch_checkpoint == "Q5_RESULT_CONTEXT":
        return "observation_gap"
    return "insufficient"


def _stale_destination_taxonomy_source(destination_commitment: Any, diagnostics: dict[str, Any]) -> str | None:
    if not isinstance(destination_commitment, dict):
        return None
    if (
        destination_commitment.get("commitment_status") == "confirmed"
        and isinstance(destination_commitment.get("failure_taxonomy"), str)
        and diagnostics.get("post_submit_propagation_failed") is True
    ):
        return "destination_commitment.failure_taxonomy"
    return None


def _post_submit_root_cause_class(first_mismatch_checkpoint: str, stale_source: str | None, diagnostics: dict[str, Any]) -> str:
    if stale_source is not None and first_mismatch_checkpoint == "unknown":
        return "STALE_DIAGNOSTIC_TAXONOMY"
    if stale_source is not None and first_mismatch_checkpoint != "none":
        return "MULTI_FACTOR_PROPAGATION_GAP"
    if first_mismatch_checkpoint == "Q3_POST_SUBMIT_NAV_STATE":
        return "POST_SUBMIT_NAV_STATE_MISMATCH"
    if first_mismatch_checkpoint == "Q4_RESULT_STATE_INIT":
        return "RESULT_STATE_INITIALIZATION_MISMATCH"
    if first_mismatch_checkpoint == "Q5_RESULT_CONTEXT":
        return "RESULT_CONTEXT_ONLY_MISMATCH"
    if diagnostics.get("submit_executed") is True and first_mismatch_checkpoint == "unknown":
        return "INCONCLUSIVE"
    return "INCONCLUSIVE"


async def _capture_search_form_readiness(page: Any) -> SearchFormReadiness:
    return SearchFormReadiness(
        origin=await _capture_control_readiness(page, ".rc-flight-searchbar input#form_depCity"),
        destination=await _capture_control_readiness(page, ".rc-flight-searchbar input#form_arrCity"),
        date=await _capture_control_readiness(page, ".rc-flight-searchbar input#form_depDate"),
        search_button=await _capture_control_readiness(page, ".rc-flight-searchbar button.search-button"),
        iframe_count=max(0, len(page.frames) - 1),
        overlay_evidence=await _overlay_evidence(page),
    )


async def _capture_control_readiness(page: Any, selector: str) -> ControlReadiness:
    locator = page.locator(selector)
    count = await locator.count()
    if count == 0:
        return ControlReadiness(count=0, visible=False, enabled=False, editable=False)
    first = locator.nth(0)
    editable = False if "button" in selector else await first.is_editable()
    return ControlReadiness(
        count=count,
        visible=await first.is_visible(),
        enabled=await first.is_enabled(),
        editable=editable,
    )


async def _overlay_evidence(page: Any) -> tuple[str, ...]:
    evidence: list[str] = []
    for name, selector in (
        ("dialog", "[role='dialog']"),
        ("modal", "[class*='modal']"),
        ("mask", "[class*='mask']"),
        ("popup", "[class*='popup']"),
        ("overlay", "[class*='overlay']"),
    ):
        count = await page.locator(selector).count()
        if count:
            evidence.append(f"{name}:{count}")
    return tuple(evidence)


async def _select_result_context_page(
    *,
    context: Any,
    current_page: Any,
    probe_input: ProbeInput,
    page_error_type: type[Exception],
    page_count_before_submit: int,
    wait_ms: int,
    page_count_after_input_before_submit: Any = None,
) -> tuple[Any, dict[str, Any]]:
    selected: ResultContextCandidate | None = None
    candidates: list[ResultContextCandidate] = []
    pages = list(context.pages)
    attempts = max(1, min(4, wait_ms // 500))
    interval_ms = max(250, wait_ms // attempts)
    base_attempts = attempts
    max_attempts = attempts * 2
    base_window_ms = interval_ms * attempts
    extension_used = False
    extension_reason = "none"
    samples: list[dict[str, Any]] = []
    result_state_failure_taxonomy = "RESULT_TRANSITION_NOT_OBSERVED"
    for attempt in range(max_attempts):
        await current_page.wait_for_timeout(interval_ms)
        pages, candidates = await _collect_result_context_candidates(
            context=context,
            current_page=current_page,
            probe_input=probe_input,
            page_error_type=page_error_type,
        )
        selected = choose_result_context_candidate(tuple(candidates))
        result_state_failure_taxonomy = _result_state_failure_taxonomy(tuple(candidates), selected)
        samples.append(
            _result_state_sample(
                attempt=attempt + 1,
                window="extension" if attempt >= base_attempts else "base",
                candidates=tuple(candidates),
                selected=selected,
                failure_taxonomy=result_state_failure_taxonomy,
            )
        )
        if selected is not None or not _result_state_retryable(result_state_failure_taxonomy):
            break
        if attempt == base_attempts - 1:
            if _result_state_forward_progress(samples):
                extension_used = True
                extension_reason = _result_state_extension_reason(samples)
                continue
            break
        if attempt == max_attempts - 1:
            break
    query_diagnostics = _query_identity_diagnostics(selected, tuple(candidates))
    stale_or_default_stabilized = _stable_stale_or_default_result_state(tuple(samples))
    root_cause_class = _diag_u4_root_cause(
        candidates=tuple(candidates),
        selected=selected,
        result_state_failure_taxonomy=result_state_failure_taxonomy,
        extension_used=extension_used,
        samples=tuple(samples),
    )
    diagnostics = {
        "page_count_before_submit": page_count_before_submit,
        "page_count_after_input_before_submit": page_count_after_input_before_submit,
        "pre_submit_context_count_changed": (
            isinstance(page_count_after_input_before_submit, int)
            and page_count_after_input_before_submit != page_count_before_submit
        ),
        "page_count_after_submit": len(pages),
        "popup_or_new_page_event": len(pages) > 1,
        "result_state_sampling_attempts": len(samples),
        "result_state_timeout_base_ms": wait_ms,
        "result_state_base_window_ms": base_window_ms,
        "result_state_max_observation_ms": base_window_ms * 2,
        "result_state_base_deadline_reached": len(samples) >= base_attempts,
        "result_state_extension_used": extension_used,
        "result_state_extension_reason": extension_reason,
        "result_state_samples": samples,
        "stale_or_default_result_stabilized": stale_or_default_stabilized,
        "query_specific_result_context_ready": selected is not None,
        "result_state_failure_taxonomy": None if selected is not None else result_state_failure_taxonomy,
        "diagnostic_root_cause_class": root_cause_class,
        "candidate_pages": [candidate.to_dict() for candidate in candidates],
        "context_count": len(candidates),
        "context_candidates": _diag_context_inventory(tuple(candidates)),
        "selected_page_index": selected.page_index if selected is not None else None,
        "selected_context_id": _candidate_context_id(selected) if selected is not None else None,
        "selected_page_url": selected.sanitized_url if selected is not None else None,
        "selected_page_identity": selected.identity.value if selected is not None else None,
        "page_closed_or_replaced": _page_closed_or_replaced(tuple(candidates), selected),
        "settled_state_reached": selected is not None or _settled_state_reached(tuple(samples)),
        "context_match": selected is not None,
        "route_evidence": _matched_evidence_value(candidates, "origin") and _matched_evidence_value(candidates, "destination"),
        "date_evidence": _matched_evidence_value(candidates, "departure_date"),
        "result_surface_evidence": _matched_evidence_value(candidates, "result_surface"),
        "route_conflict": _matched_evidence_value(candidates, "route_conflict"),
        "date_conflict": _matched_evidence_value(candidates, "date_conflict"),
        "selection_reason": _result_context_selection_reason(selected, tuple(candidates), query_diagnostics),
        **query_diagnostics,
    }
    if selected is None:
        return current_page, diagnostics
    return pages[selected.page_index], diagnostics


async def _collect_result_context_candidates(
    *,
    context: Any,
    current_page: Any,
    probe_input: ProbeInput,
    page_error_type: type[Exception],
) -> tuple[list[Any], list[ResultContextCandidate]]:
    candidates: list[ResultContextCandidate] = []
    pages = list(context.pages)
    for index, candidate_page in enumerate(pages):
        transient_id = f"context-{index}"
        is_current = candidate_page == current_page
        alive = not bool(candidate_page.is_closed())
        opener_relation = "unknown"
        document_ready_state: str | None = None
        if alive:
            with suppress(page_error_type):
                opener = await candidate_page.opener()
                if opener == current_page:
                    opener_relation = "source"
                elif opener is not None:
                    opener_relation = "other"
                else:
                    opener_relation = "none"
            with suppress(page_error_type):
                ready_state = await candidate_page.evaluate("document.readyState")
                document_ready_state = str(ready_state)
        try:
            if not alive:
                raise page_error_type("page closed")
            title = await candidate_page.title()
            html = await candidate_page.content()
            identity = classify_fliggy_page_identity(url=candidate_page.url, title=title, html=html)
            search_plan_evidence = summarize_search_plan_evidence(
                title=title,
                html=html,
                probe_input=probe_input,
                url=candidate_page.url,
            )
            candidates.append(
                ResultContextCandidate(
                    page_index=index,
                    sanitized_url=_sanitize_source_ref(candidate_page.url),
                    title=title,
                    identity=identity,
                    search_plan_evidence=search_plan_evidence,
                    is_current_page=is_current,
                    transient_id=transient_id,
                    creation_order=index,
                    opener_relation=opener_relation,
                    alive=alive,
                    document_ready_state=document_ready_state,
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
                    is_current_page=is_current,
                    transient_id=transient_id,
                    creation_order=index,
                    opener_relation=opener_relation,
                    alive=False,
                    document_ready_state=document_ready_state,
                )
            )
    return pages, candidates


def _result_state_sample(
    *,
    attempt: int,
    window: str,
    candidates: tuple[ResultContextCandidate, ...],
    selected: ResultContextCandidate | None,
    failure_taxonomy: str | None,
) -> dict[str, Any]:
    diagnostic_candidate = selected or _best_diagnostic_candidate(candidates)
    evidence = diagnostic_candidate.search_plan_evidence if diagnostic_candidate is not None else {}
    return {
        "attempt": attempt,
        "window": window,
        "context_count": len(candidates),
        "selected_context_id": _candidate_context_id(selected) if selected is not None else None,
        "failure_taxonomy": failure_taxonomy,
        "result_surface_present": any(
            candidate.search_plan_evidence.get("result_surface_present") is True for candidate in candidates
        ),
        "alive_context_count": sum(1 for candidate in candidates if candidate.alive),
        "closed_context_count": sum(1 for candidate in candidates if not candidate.alive),
        "document_ready_states": sorted(
            {
                str(candidate.document_ready_state)
                for candidate in candidates
                if candidate.document_ready_state is not None
            }
        ),
        "route_match": evidence.get("route_match", "insufficient"),
        "observed_route_origin": evidence.get("observed_route_origin"),
        "observed_route_destination": evidence.get("observed_route_destination"),
        "observed_route_text": evidence.get("observed_route_text"),
        "date_match": evidence.get("date_match", "insufficient"),
        "observed_date_text": evidence.get("observed_date_text"),
        "normalized_observed_date": evidence.get("normalized_observed_date"),
        "observed_date_source": evidence.get("observed_date_source"),
        "marker_signature": _result_marker_signature(evidence),
    }


def _diag_context_inventory(candidates: tuple[ResultContextCandidate, ...]) -> list[dict[str, Any]]:
    return [
        {
            "context_id": _candidate_context_id(candidate),
            "creation_order": candidate.creation_order if candidate.creation_order is not None else candidate.page_index,
            "opener_relation": candidate.opener_relation,
            "url_class": _url_class(candidate.sanitized_url),
            "identity": candidate.identity.value,
            "alive": candidate.alive,
            "closed": not candidate.alive,
            "document_ready_state": candidate.document_ready_state,
            "result_like_surface": candidate.search_plan_evidence.get("result_surface_present") is True,
            "route_match": candidate.search_plan_evidence.get("route_match"),
            "observed_route_text": candidate.search_plan_evidence.get("observed_route_text"),
            "date_match": candidate.search_plan_evidence.get("date_match"),
            "observed_date_text": candidate.search_plan_evidence.get("observed_date_text"),
            "observed_date_source": candidate.search_plan_evidence.get("observed_date_source"),
        }
        for candidate in candidates
    ]


def _first_result_state_sample(handoff_diagnostics: dict[str, Any]) -> dict[str, Any]:
    samples = handoff_diagnostics.get("result_state_samples")
    if isinstance(samples, list) and samples:
        return samples[0]
    return {
        "attempt": None,
        "window": "none",
        "context_count": handoff_diagnostics.get("context_count", 0),
        "result_surface_present": handoff_diagnostics.get("result_surface_present"),
    }


def _result_marker_signature(evidence: dict[str, Any]) -> tuple[Any, ...]:
    return (
        evidence.get("route_match"),
        evidence.get("date_match"),
        evidence.get("observed_date_text"),
        evidence.get("normalized_observed_date"),
        evidence.get("selected_date_marker_class"),
        evidence.get("result_surface_present"),
    )


def _marker_transition_count(samples: tuple[dict[str, Any], ...]) -> int:
    signatures = [sample.get("marker_signature") for sample in samples]
    return sum(1 for previous, current in pairwise(signatures) if previous != current)


def _result_state_forward_progress(samples: list[dict[str, Any]]) -> bool:
    if _stable_stale_or_default_result_state(tuple(samples)):
        return False
    if len(samples) < 2:
        return bool(
            samples
            and samples[-1].get("result_surface_present") is True
            and samples[-1].get("failure_taxonomy") != "RESULT_STATE_STALE_OR_DEFAULT"
        )
    previous = samples[-2]
    current = samples[-1]
    return (
        previous.get("context_count") != current.get("context_count")
        or previous.get("alive_context_count") != current.get("alive_context_count")
        or previous.get("result_surface_present") != current.get("result_surface_present")
        or previous.get("marker_signature") != current.get("marker_signature")
        or previous.get("document_ready_states") != current.get("document_ready_states")
    )


def _stable_stale_or_default_result_state(samples: tuple[dict[str, Any], ...]) -> bool:
    if len(samples) < 2:
        return False
    previous = samples[-2]
    current = samples[-1]
    return (
        previous.get("failure_taxonomy") == "RESULT_STATE_STALE_OR_DEFAULT"
        and current.get("failure_taxonomy") == "RESULT_STATE_STALE_OR_DEFAULT"
        and previous.get("result_surface_present") is True
        and current.get("result_surface_present") is True
        and previous.get("marker_signature") == current.get("marker_signature")
        and current.get("alive_context_count", 0) > 0
    )


def _result_state_extension_reason(samples: list[dict[str, Any]]) -> str:
    if len(samples) < 2:
        return "result_surface_present" if samples and samples[-1].get("result_surface_present") is True else "none"
    previous = samples[-2]
    current = samples[-1]
    if previous.get("context_count") != current.get("context_count"):
        return "context_count_changed"
    if previous.get("alive_context_count") != current.get("alive_context_count"):
        return "context_lifecycle_changed"
    if previous.get("result_surface_present") != current.get("result_surface_present"):
        return "result_surface_changed"
    if previous.get("marker_signature") != current.get("marker_signature"):
        return "route_date_marker_changed"
    if previous.get("document_ready_states") != current.get("document_ready_states"):
        return "document_ready_state_changed"
    return "none"


def _settled_state_reached(samples: tuple[dict[str, Any], ...]) -> bool | str:
    if samples and samples[-1].get("selected_context_id") is not None:
        return True
    if len(samples) < 2:
        return "insufficient"
    last = samples[-1]
    previous = samples[-2]
    stable = (
        last.get("marker_signature") == previous.get("marker_signature")
        and last.get("result_surface_present") is True
        and last.get("alive_context_count", 0) > 0
    )
    return True if stable else "insufficient"


def _page_closed_or_replaced(candidates: tuple[ResultContextCandidate, ...], selected: ResultContextCandidate | None) -> bool:
    if selected is not None:
        return not selected.alive
    return bool(candidates) and any(not candidate.alive for candidate in candidates)


def _diag_u4_root_cause(
    *,
    candidates: tuple[ResultContextCandidate, ...],
    selected: ResultContextCandidate | None,
    result_state_failure_taxonomy: str | None,
    extension_used: bool,
    samples: tuple[dict[str, Any], ...],
) -> str:
    if _page_closed_or_replaced(candidates, selected):
        return "PAGE_CLOSED_OR_REPLACED_DURING_TRANSITION"
    if selected is not None:
        return "RESULT_STATE_SETTLING_GAP" if extension_used else "INCONCLUSIVE"
    if _ambiguous_result_candidates(candidates):
        return "PAGE_CONTEXT_SELECTION_AMBIGUOUS"
    if _has_correct_alternate_candidate(candidates):
        return "WRONG_PAGE_CONTEXT_SELECTED"
    if result_state_failure_taxonomy == "RESULT_STATE_STALE_OR_DEFAULT":
        return "STALE_DEFAULT_CONTEXT_PERSISTED" if extension_used or _stable_stale_or_default_result_state(samples) else "RESULT_STATE_SETTLING_GAP"
    if result_state_failure_taxonomy in {
        "RESULT_STATE_QUERY_MISMATCH",
        "RESULT_STATE_ROUTE_MISMATCH",
        "RESULT_STATE_DATE_MISMATCH",
    }:
        return "CROSS_PAGE_QUERY_PROPAGATION_FAILED"
    if result_state_failure_taxonomy in {"RESULT_STATE_QUERY_UNREADABLE", "RESULT_STATE_NOT_READY"}:
        return "RESULT_STATE_OBSERVATION_GAP"
    if result_state_failure_taxonomy == "RESULT_TRANSITION_NOT_OBSERVED":
        return "RESULT_STATE_OBSERVATION_GAP" if samples else "INCONCLUSIVE"
    return "INCONCLUSIVE"


def _has_correct_alternate_candidate(candidates: tuple[ResultContextCandidate, ...]) -> bool:
    return any(
        candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE
        and candidate.search_plan_evidence.get("route_match") is True
        and candidate.search_plan_evidence.get("date_match") is True
        and candidate.search_plan_evidence.get("result_surface_present") is True
        and not candidate.is_current_page
        for candidate in candidates
    )


def _candidate_context_id(candidate: ResultContextCandidate) -> str:
    return candidate.transient_id or f"context-{candidate.page_index}"


def _ambiguous_result_candidates(candidates: tuple[ResultContextCandidate, ...]) -> bool:
    result_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE
        and candidate.search_plan_evidence.get("result_surface_present") is True
    )
    if len(result_candidates) < 2:
        return False
    best_score = max(candidate.score() for candidate in result_candidates)
    tied = tuple(candidate for candidate in result_candidates if candidate.score() == best_score)
    return len({candidate.signature() for candidate in tied}) > 1


def _result_state_failure_taxonomy(
    candidates: tuple[ResultContextCandidate, ...],
    selected: ResultContextCandidate | None,
) -> str | None:
    if selected is not None:
        return None
    if not candidates:
        return "RESULT_TRANSITION_NOT_OBSERVED"
    if not any(candidate.search_plan_evidence.get("result_surface_present") is True for candidate in candidates):
        return "RESULT_STATE_NOT_READY"
    diagnostic_candidate = _best_diagnostic_candidate(candidates)
    if diagnostic_candidate is None:
        return "RESULT_STATE_QUERY_UNREADABLE"
    evidence = diagnostic_candidate.search_plan_evidence
    route_match = evidence.get("route_match")
    date_match = evidence.get("date_match")
    if route_match == "insufficient" or date_match == "insufficient":
        return "RESULT_STATE_QUERY_UNREADABLE"
    if _stale_or_default_result_state(evidence):
        return "RESULT_STATE_STALE_OR_DEFAULT"
    if route_match is False and date_match is False:
        return "RESULT_STATE_QUERY_MISMATCH"
    if route_match is False:
        return "RESULT_STATE_ROUTE_MISMATCH"
    if date_match is False:
        return "RESULT_STATE_DATE_MISMATCH"
    return "SUBMIT_STATE_PROPAGATION_FAILED"


def _result_state_retryable(failure_taxonomy: str | None) -> bool:
    return failure_taxonomy in {
        "RESULT_TRANSITION_NOT_OBSERVED",
        "RESULT_STATE_NOT_READY",
        "RESULT_STATE_STALE_OR_DEFAULT",
        "RESULT_STATE_QUERY_UNREADABLE",
    }


def _stale_or_default_result_state(evidence: dict[str, Any]) -> bool:
    observed_date = evidence.get("normalized_observed_date")
    expected_date = evidence.get("normalized_expected_date")
    if not isinstance(observed_date, str) or not isinstance(expected_date, str):
        return False
    if observed_date == expected_date:
        return False
    return evidence.get("date_parse_status") == "ambiguous" or evidence.get("date_conflict") is True


def _matched_evidence_value(candidates: list[ResultContextCandidate], key: str) -> bool:
    return any(candidate.search_plan_evidence.get(key) is True for candidate in candidates)


def _result_context_selection_reason(
    selected: ResultContextCandidate | None,
    candidates: tuple[ResultContextCandidate, ...],
    query_diagnostics: dict[str, Any],
) -> str:
    if selected is not None:
        return "result_identity_route_date_surface_match"
    if query_diagnostics["mismatch_dimension"] == "date":
        return "date_mismatch"
    if query_diagnostics["mismatch_dimension"] == "route":
        return "route_mismatch"
    if query_diagnostics["mismatch_dimension"] == "both":
        return "route_and_date_mismatch"
    if any(candidate.search_plan_evidence.get("route_conflict") is True for candidate in candidates):
        return "route_mismatch"
    if any(candidate.search_plan_evidence.get("date_conflict") is True for candidate in candidates):
        return "date_mismatch"
    if any(
        candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE
        and candidate.search_plan_evidence.get("result_surface") is True
        and candidate.route_matches()
        and not candidate.date_matches()
        for candidate in candidates
    ):
        return "missing_date_evidence"
    if any(
        candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE
        and candidate.search_plan_evidence.get("result_surface") is True
        and candidate.date_matches()
        and not candidate.route_matches()
        for candidate in candidates
    ):
        return "missing_route_evidence"
    return "no_deterministic_result_context"


def _query_identity_diagnostics(
    selected: ResultContextCandidate | None,
    candidates: tuple[ResultContextCandidate, ...],
) -> dict[str, Any]:
    diagnostic_candidate = selected or _best_diagnostic_candidate(candidates)
    if diagnostic_candidate is None:
        return {
            "submitted_date": None,
            "date_marker_candidates_count": 0,
            "selected_date_marker_class": None,
            "observed_date_text": None,
            "observed_date_source": "none",
            "date_parse_status": "absent",
            "normalized_expected_date": None,
            "normalized_observed_date": None,
            "date_match": "insufficient",
            "route_match": "insufficient",
            "result_surface_present": False,
            "query_identity_decision": "insufficient",
            "mismatch_dimension": "unknown",
            "timing_state": "timed-out",
            "root_cause_class": "INCONCLUSIVE",
        }
    evidence = diagnostic_candidate.search_plan_evidence
    route_match = bool(evidence.get("route_match"))
    date_match_value = evidence.get("date_match")
    date_match = date_match_value if isinstance(date_match_value, bool) else "insufficient"
    result_surface_present = bool(evidence.get("result_surface_present"))
    query_identity_decision = "match" if selected is not None else "insufficient"
    mismatch_dimension = _candidate_mismatch_dimension(
        route_match=route_match,
        date_match=date_match is True,
    )
    if date_match == "insufficient" and route_match:
        mismatch_dimension = "date"
    if evidence.get("route_conflict") is True and (evidence.get("date_conflict") is True or date_match is False):
        mismatch_dimension = "both"
    return {
        "submitted_date": evidence.get("submitted_date"),
        "date_marker_candidates_count": evidence.get("date_marker_candidates_count", 0),
        "selected_date_marker_class": evidence.get("selected_date_marker_class"),
        "observed_date_text": evidence.get("observed_date_text"),
        "observed_date_source": evidence.get("observed_date_source", "none"),
        "date_parse_status": evidence.get("date_parse_status", "absent"),
        "normalized_expected_date": evidence.get("normalized_expected_date"),
        "normalized_observed_date": evidence.get("normalized_observed_date"),
        "date_match": date_match,
        "route_match": route_match,
        "result_surface_present": result_surface_present,
        "query_identity_decision": query_identity_decision,
        "mismatch_dimension": mismatch_dimension,
        "timing_state": evidence.get("timing_state", "ready"),
        "root_cause_class": _date_root_cause_class(evidence=evidence, route_match=route_match, date_match=date_match),
    }


def _best_diagnostic_candidate(candidates: tuple[ResultContextCandidate, ...]) -> ResultContextCandidate | None:
    if not candidates:
        return None
    result_candidates = tuple(candidate for candidate in candidates if candidate.identity is FliggyPageIdentity.FLIGHT_RESULT_CANDIDATE)
    pool = result_candidates or candidates
    return max(pool, key=lambda candidate: candidate.score())


def _date_root_cause_class(*, evidence: dict[str, Any], route_match: bool, date_match: bool | str) -> str:
    if evidence.get("result_surface_present") is True and route_match and date_match is True:
        return "INCONCLUSIVE"
    if evidence.get("date_marker_candidates_count", 0) == 0:
        return "DATE_MARKER_NOT_PRESENT"
    if evidence.get("date_parse_status") == "unparsable":
        return "DATE_FORMAT_PARSE_GAP"
    if evidence.get("date_parse_status") == "ambiguous":
        return "MULTI_FACTOR_CONTEXT_GAP"
    if date_match is False and evidence.get("normalized_observed_date") is not None:
        return "DATE_TRUE_QUERY_MISMATCH"
    if evidence.get("route_conflict") is True and date_match is not True:
        return "MULTI_FACTOR_CONTEXT_GAP"
    if route_match and date_match == "insufficient":
        return "DATE_MARKER_NOT_PRESENT"
    if not route_match and evidence.get("result_surface_present") is True:
        return "STALE_RESULT_CONTEXT"
    return "INCONCLUSIVE"


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
