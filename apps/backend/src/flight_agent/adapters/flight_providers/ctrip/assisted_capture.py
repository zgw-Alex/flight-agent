"""CTRIP assisted browser evidence import.

This module is the M9-BP5-CTRIP-U2 manual-export/import path. It never opens a
browser, never navigates CTRIP, and never replays observed provider endpoints.
It accepts only local, sanitized browser-originated evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Self

from flight_agent.adapters.flight_providers.ctrip.browser_probe import (
    CTRIP_PROVIDER_ID,
    BrowserAcquisitionMode,
    CapturedPayload,
    CtripLevel1Evidence,
    CtripLevel2OfferEvidence,
    extract_level1_evidence_from_payloads,
    extract_level2_offer_evidence,
    sanitize_probe_payload,
)

CTRIP_ASSISTED_CAPTURE_VERSION = "m9-bp5-ctrip-u2-assisted-capture-v0.1"
CTRIP_ASSISTED_ACQUISITION_STRATEGY = "BROWSER_ASSISTED"

_UNSAFE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "csrf",
    "localstorage",
    "passenger",
    "password",
    "payment",
    "profile",
    "secret",
    "session",
    "set-cookie",
    "storage",
    "token",
)
_UNSAFE_TEXT_FRAGMENTS = (
    "authorization:",
    "bearer ",
    "cookie:",
    "set-cookie:",
    "csrf",
    "localstorage",
    "sessionstorage",
    "token=",
)
_PAYLOAD_KEYS = ("payload", "response_body", "responseBody", "body", "data")


class AssistedCaptureMode(str, Enum):
    MANUAL_EXPORT = "MANUAL_EXPORT"
    READ_ONLY_BROWSER_OBSERVATION = "READ_ONLY_BROWSER_OBSERVATION"


class AssistedEvidenceLevel(str, Enum):
    LEVEL1 = "LEVEL1"
    LEVEL2 = "LEVEL2"
    UNKNOWN = "UNKNOWN"


class AssistedCaptureClassification(str, Enum):
    MANUAL_EXPORT_IMPORT_CONFIRMED = "MANUAL_EXPORT_IMPORT_CONFIRMED"
    READ_ONLY_BROWSER_OBSERVATION_CONFIRMED = "READ_ONLY_BROWSER_OBSERVATION_CONFIRMED"
    ASSISTED_CAPTURE_PARTIAL = "ASSISTED_CAPTURE_PARTIAL"
    ASSISTED_CAPTURE_NOT_FEASIBLE = "ASSISTED_CAPTURE_NOT_FEASIBLE"
    AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"


class UnsafeAssistedEvidenceError(ValueError):
    """Raised when manual evidence contains forbidden browser/session material."""


@dataclass(frozen=True)
class AssistedCaptureInput:
    source_path: Path
    origin_text: str
    destination_text: str
    departure_date: date
    request_label: str = "manual_export"
    capture_mode: AssistedCaptureMode = AssistedCaptureMode.MANUAL_EXPORT
    evidence_level: AssistedEvidenceLevel = AssistedEvidenceLevel.UNKNOWN
    acquired_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.origin_text.strip():
            raise ValueError("AssistedCaptureInput origin_text is required")
        if not self.destination_text.strip():
            raise ValueError("AssistedCaptureInput destination_text is required")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Self:
        return cls(
            source_path=Path(args.sanitized_json_path),
            origin_text=args.origin,
            destination_text=args.destination,
            departure_date=date.fromisoformat(args.departure_date),
            request_label=args.request_label,
            capture_mode=AssistedCaptureMode(args.capture_mode),
            evidence_level=AssistedEvidenceLevel(args.evidence_level),
        )


@dataclass(frozen=True)
class AssistedCaptureResult:
    provider_identity: str
    acquisition_mode: BrowserAcquisitionMode
    acquisition_strategy: str
    capture_mode: AssistedCaptureMode
    acquired_at: datetime
    search_scope: dict[str, str]
    request_label: str
    source_ref: str
    evidence_level: AssistedEvidenceLevel
    classification: AssistedCaptureClassification
    observed_level1_count: int
    observed_level2_offer_count: int
    level1_evidence: tuple[CtripLevel1Evidence, ...]
    level2_offer_evidence: tuple[CtripLevel2OfferEvidence, ...]
    canonical_mapping_feasibility: dict[str, str]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return sanitize_probe_payload(
            {
                "provider_identity": self.provider_identity,
                "acquisition_mode": self.acquisition_mode.value,
                "acquisition_strategy": self.acquisition_strategy,
                "capture_mode": self.capture_mode.value,
                "acquired_at": self.acquired_at.isoformat(),
                "search_scope": self.search_scope,
                "request_label": self.request_label,
                "source_ref": self.source_ref,
                "evidence_level": self.evidence_level.value,
                "classification": self.classification.value,
                "observed_level1_count": self.observed_level1_count,
                "observed_level2_offer_count": self.observed_level2_offer_count,
                "level1_evidence": [item.to_dict() for item in self.level1_evidence],
                "level2_offer_evidence": [item.to_dict() for item in self.level2_offer_evidence],
                "canonical_mapping_feasibility": self.canonical_mapping_feasibility,
                "diagnostics": self.diagnostics,
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def import_ctrip_assisted_capture(capture_input: AssistedCaptureInput) -> AssistedCaptureResult:
    raw = _load_json_file(capture_input.source_path)
    _reject_unsafe_evidence(raw)
    payload, metadata = _extract_payload_and_metadata(raw)
    _reject_unsafe_evidence(payload)

    request_label = str(metadata.get("request_label") or capture_input.request_label)
    capture_mode = AssistedCaptureMode(str(metadata.get("capture_mode") or capture_input.capture_mode.value))
    evidence_level = _classify_imported_level(
        request_label=request_label,
        requested_level=capture_input.evidence_level,
        payload=payload,
    )
    captured = CapturedPayload(
        stage="LEVEL2" if evidence_level is AssistedEvidenceLevel.LEVEL2 else "LEVEL1",
        url=str(metadata.get("source_ref") or "manual-export"),
        label=request_label,
        payload=payload,
    )

    level1_evidence = (
        ()
        if evidence_level is AssistedEvidenceLevel.LEVEL2
        else extract_level1_evidence_from_payloads((captured,))
    )
    level2_evidence = (
        extract_level2_offer_evidence((captured,))
        if evidence_level is AssistedEvidenceLevel.LEVEL2
        else ()
    )
    classification = _classify_result(
        capture_mode=capture_mode,
        level1_count=len(level1_evidence),
        level2_count=len(level2_evidence),
    )
    return AssistedCaptureResult(
        provider_identity=CTRIP_PROVIDER_ID,
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquisition_strategy=CTRIP_ASSISTED_ACQUISITION_STRATEGY,
        capture_mode=capture_mode,
        acquired_at=capture_input.acquired_at or datetime.now(UTC),
        search_scope={
            "origin_text": capture_input.origin_text,
            "destination_text": capture_input.destination_text,
            "departure_date": capture_input.departure_date.isoformat(),
            "trip_type": "ONE_WAY",
            "market": "CHINA_DOMESTIC",
        },
        request_label=request_label,
        source_ref="local_manual_export",
        evidence_level=evidence_level,
        classification=classification,
        observed_level1_count=len(level1_evidence),
        observed_level2_offer_count=len(level2_evidence),
        level1_evidence=level1_evidence,
        level2_offer_evidence=level2_evidence,
        canonical_mapping_feasibility=_mapping_feasibility(level1_evidence, level2_evidence),
        diagnostics={
            "network_access": False,
            "browser_automation": False,
            "anti_bot_bypass": False,
            "direct_internal_endpoint_replay": False,
            "unsafe_evidence_rejected": False,
            "input_shape": _payload_shape(payload),
        },
    )


def validate_sanitized_ctrip_evidence(value: Any) -> Any:
    _reject_unsafe_evidence(value)
    return sanitize_probe_payload(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import sanitized CTRIP manual browser evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="import a local sanitized JSON payload")
    import_parser.add_argument("sanitized_json_path")
    import_parser.add_argument("--origin", required=True)
    import_parser.add_argument("--destination", required=True)
    import_parser.add_argument("--departure-date", required=True)
    import_parser.add_argument("--request-label", default="manual_export")
    import_parser.add_argument("--capture-mode", choices=[item.value for item in AssistedCaptureMode], default="MANUAL_EXPORT")
    import_parser.add_argument("--evidence-level", choices=[item.value for item in AssistedEvidenceLevel], default="UNKNOWN")
    import_parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    result = import_ctrip_assisted_capture(AssistedCaptureInput.from_args(args))
    rendered = result.to_json()
    print(rendered)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result.classification in {
        AssistedCaptureClassification.MANUAL_EXPORT_IMPORT_CONFIRMED,
        AssistedCaptureClassification.READ_ONLY_BROWSER_OBSERVATION_CONFIRMED,
        AssistedCaptureClassification.ASSISTED_CAPTURE_PARTIAL,
    } else 2


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"sanitized evidence file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed sanitized evidence JSON: {exc.msg}") from exc


def _extract_payload_and_metadata(value: Any) -> tuple[Any, dict[str, Any]]:
    if not isinstance(value, dict):
        return value, {}
    metadata = {
        key: value[key]
        for key in ("request_label", "capture_mode", "source_ref")
        if key in value
    }
    for key in _PAYLOAD_KEYS:
        if key in value:
            return value[key], metadata
    return value, metadata


def _classify_imported_level(
    *, request_label: str, requested_level: AssistedEvidenceLevel, payload: Any
) -> AssistedEvidenceLevel:
    if requested_level is not AssistedEvidenceLevel.UNKNOWN:
        return requested_level
    lowered = request_label.lower()
    if "batchsearch" in lowered or "batch_search" in lowered:
        return AssistedEvidenceLevel.LEVEL1
    if "level2" in lowered or any(token in lowered for token in ("product", "price", "booking", "rule")):
        return AssistedEvidenceLevel.LEVEL2
    if extract_level1_evidence_from_payloads((CapturedPayload("LEVEL1", "manual-export", request_label, payload),)):
        return AssistedEvidenceLevel.LEVEL1
    if extract_level2_offer_evidence((CapturedPayload("LEVEL2", "manual-export", request_label, payload),)):
        return AssistedEvidenceLevel.LEVEL2
    return AssistedEvidenceLevel.UNKNOWN


def _classify_result(
    *, capture_mode: AssistedCaptureMode, level1_count: int, level2_count: int
) -> AssistedCaptureClassification:
    if level1_count == 0 and level2_count == 0:
        return AssistedCaptureClassification.ASSISTED_CAPTURE_PARTIAL
    if capture_mode is AssistedCaptureMode.READ_ONLY_BROWSER_OBSERVATION:
        return AssistedCaptureClassification.READ_ONLY_BROWSER_OBSERVATION_CONFIRMED
    return AssistedCaptureClassification.MANUAL_EXPORT_IMPORT_CONFIRMED


def _mapping_feasibility(
    level1: tuple[CtripLevel1Evidence, ...], level2: tuple[CtripLevel2OfferEvidence, ...]
) -> dict[str, str]:
    flight_segment = "CONFIRMED" if any(
        item.mapping_feasibility.get("flight_segment") == "STRONG_CANDIDATE" for item in level1
    ) else ("PARTIAL" if level1 else "UNKNOWN")
    itinerary = "CONFIRMED" if any(
        item.mapping_feasibility.get("itinerary") == "STRONG_CANDIDATE" for item in level1
    ) else ("PARTIAL" if level1 else "UNKNOWN")
    offer = "CONFIRMED" if level2 else ("PARTIAL" if any(
        item.mapping_feasibility.get("offer") == "OFFER_LIKE_PRICE_SEAM_OBSERVED" for item in level1
    ) else "UNKNOWN")
    return {
        "FlightSegment": flight_segment,
        "Itinerary": itinerary,
        "Offer": offer,
        "PurchaseAccess": "PARTIAL" if any(item.purchase_access.status == "OBSERVED" for item in level2) else "UNKNOWN",
    }


def _reject_unsafe_evidence(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower().replace("_", "-")
            if lowered in {"har", "full-har"} or any(fragment in lowered for fragment in _UNSAFE_KEY_FRAGMENTS):
                raise UnsafeAssistedEvidenceError(f"unsafe evidence key rejected at {path}.{key_text}")
            _reject_unsafe_evidence(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_evidence(item, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.lower()
        if any(fragment in lowered for fragment in _UNSAFE_TEXT_FRAGMENTS):
            raise UnsafeAssistedEvidenceError(f"unsafe evidence text rejected at {path}")


def _payload_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _payload_shape(item) for key, item in list(value.items())[:12]}
    if isinstance(value, list):
        return [f"{len(value)} items", _payload_shape(value[0]) if value else None]
    return type(value).__name__


if __name__ == "__main__":
    raise SystemExit(main())
