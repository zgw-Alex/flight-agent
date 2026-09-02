"""FLIGGY Level-1 evidence mapper for M9-FLIGGY-MAPPER-U1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeGuard

from flight_agent.domain.flights import PriceSemantics
from flight_agent.domain.shared import DomainValue
from flight_agent.ports import (
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    MappingIssue,
    MappingIssueCategory,
    MappingStatistics,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderMapper,
    ProviderMappingResult,
    ProviderRawEvidence,
    ProviderSearchResult,
    raw_evidence_ref,
)
from flight_agent.ports.flight_providers import RawEvidenceValue

FLIGGY_PROVIDER_MAPPER_VERSION = MapperVersion("m9-fliggy-evidence-mapper-v1")

_SUCCESS_OUTCOMES = {"SUCCESS_COMPLETE", "SUCCESS_PARTIAL"}
_FLIGHT_IDENTITY_PATTERN = re.compile(r"([A-Z]{2})\s*([0-9]{2,5}[A-Z]?)", re.IGNORECASE)
_PRICE_PATTERN = re.compile(r"([0-9][0-9,]*)")


@dataclass(frozen=True)
class _MappedSegmentDraft:
    segment: MappedSegment
    provider_segment_id: str
    raw_record_ref: str


class FliggyEvidenceMapper(ProviderMapper):
    """Provider-specific mapper for sanitized FLIGGY browser probe evidence."""

    def __init__(self, mapper_version: MapperVersion = FLIGGY_PROVIDER_MAPPER_VERSION) -> None:
        self._mapper_version = mapper_version

    @property
    def mapper_version(self) -> MapperVersion:
        return self._mapper_version

    def map(self, provider_result: ProviderSearchResult) -> ProviderMappingResult:
        if provider_result.raw_evidence is None:
            return _empty_mapping_result(provider_result, self.mapper_version)
        if provider_result.execution_status is not ProviderExecutionStatus.SUCCESS:
            return _empty_mapping_result(provider_result, self.mapper_version)

        context = _MappingContext(provider_result.raw_evidence)
        payload = _thaw_raw_value(provider_result.raw_evidence.payload)
        if not isinstance(payload, dict):
            context.issue(
                raw_record_ref="raw:payload",
                raw_path="payload",
                category=MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
                detail="FLIGGY probe payload root is not an object",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        if payload.get("outcome") not in _SUCCESS_OUTCOMES:
            return context.result(self.mapper_version, _data_status_for_unmapped_outcome(provider_result))

        evidence_items = payload.get("evidence")
        if not isinstance(evidence_items, list):
            context.issue(
                raw_record_ref="raw:evidence",
                raw_path="evidence",
                category=MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
                detail="FLIGGY probe evidence is missing or not a list",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        search_scope = payload.get("search_scope")
        if not isinstance(search_scope, dict):
            search_scope = {}

        for index, item in enumerate(evidence_items, start=1):
            raw_record_ref = _record_ref(item, index)
            segment = context.map_segment(item, raw_record_ref, search_scope)
            if segment is None:
                continue
            itinerary = context.map_itinerary(segment)
            if itinerary is not None:
                context.map_offer(item, raw_record_ref, itinerary)

        return context.result(self.mapper_version, _mapped_data_status(provider_result, context))


class _MappingContext:
    def __init__(self, raw_evidence: ProviderRawEvidence) -> None:
        self.raw_evidence = raw_evidence
        self.issues: list[MappingIssue] = []
        self.segments: list[MappedSegment] = []
        self.itineraries: list[MappedItinerary] = []
        self.offers: list[MappedOffer] = []
        self.raw_segment_count = 0
        self.dropped_segment_count = 0
        self.raw_itinerary_count = 0
        self.dropped_itinerary_count = 0
        self.raw_offer_count = 0
        self.dropped_offer_count = 0

    def map_segment(
        self,
        payload: object,
        raw_record_ref: str,
        search_scope: dict[str, object],
    ) -> _MappedSegmentDraft | None:
        self.raw_segment_count += 1
        if not isinstance(payload, dict):
            self._drop_segment(raw_record_ref, "evidence", "FLIGGY evidence record is not an object")
            return None

        identity_text = _field_text(payload, "raw_displayed_flight_identity") or _field_text(
            payload, "raw_accessible_flight_label"
        )
        flight_identity = _flight_identity(identity_text)
        departure_time = _field_text(payload, "raw_departure_time")
        arrival_time = _field_text(payload, "raw_arrival_time")
        departure_airport = _field_text(payload, "raw_departure_airport_terminal")
        arrival_airport = _field_text(payload, "raw_arrival_airport_terminal")
        departure_date = _departure_date(search_scope)
        missing = []
        if flight_identity is None:
            missing.append("raw_displayed_flight_identity")
        if departure_time is None:
            missing.append("raw_departure_time")
        if arrival_time is None:
            missing.append("raw_arrival_time")
        if departure_airport is None:
            missing.append("raw_departure_airport_terminal")
        if arrival_airport is None:
            missing.append("raw_arrival_airport_terminal")
        if departure_date is None:
            missing.append("search_scope.departure_date")
        if missing:
            self._drop_segment(
                raw_record_ref,
                ",".join(missing),
                "FLIGGY Level-1 segment is missing identity-critical evidence",
            )
            return None

        assert flight_identity is not None
        assert departure_time is not None
        assert arrival_time is not None
        assert departure_airport is not None
        assert arrival_airport is not None
        assert departure_date is not None

        provider_segment_id = f"fliggy-level1-segment-{_evidence_index(payload)}-{flight_identity.full}"
        operating_carrier = _operating_carrier(payload)
        aircraft = _optional_field_value(payload, "raw_aircraft_text")
        segment = MappedSegment(
            mapped_segment_ref=MappedSegmentRef(f"mapped-segment:{provider_segment_id}"),
            provider_segment_id=provider_segment_id,
            marketing_carrier=flight_identity.carrier,
            flight_number=flight_identity.full,
            departure_airport=departure_airport,
            arrival_airport=arrival_airport,
            departure_local=_local_datetime(departure_date, departure_time),
            arrival_local=_arrival_datetime(departure_date, departure_time, arrival_time),
            operating_carrier=operating_carrier,
            aircraft_type=aircraft,
            checked_baggage_pieces=DomainValue.not_provided(),
            overnight=DomainValue.not_provided(),
            provenance=self.provenance(raw_record_ref, provider_segment_id),
        )
        self.segments.append(segment)
        return _MappedSegmentDraft(
            segment=segment,
            provider_segment_id=provider_segment_id,
            raw_record_ref=raw_record_ref,
        )

    def map_itinerary(self, segment: _MappedSegmentDraft) -> MappedItinerary | None:
        self.raw_itinerary_count += 1
        provider_itinerary_id = f"fliggy-level1-itinerary-{segment.provider_segment_id}"
        itinerary = MappedItinerary(
            mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{provider_itinerary_id}"),
            provider_itinerary_id=provider_itinerary_id,
            segment_refs=(segment.segment.mapped_segment_ref,),
            provenance=self.provenance(segment.raw_record_ref, provider_itinerary_id),
        )
        self.itineraries.append(itinerary)
        return itinerary

    def map_offer(
        self,
        payload: object,
        raw_record_ref: str,
        itinerary: MappedItinerary,
    ) -> MappedOffer | None:
        self.raw_offer_count += 1
        if not isinstance(payload, dict):
            self._drop_offer(raw_record_ref, "evidence", "FLIGGY evidence record is not an object")
            return None

        price_text = _field_text(payload, "raw_displayed_lowest_price")
        amount = _price_amount(price_text)
        if amount is None:
            self._drop_offer(
                raw_record_ref,
                "raw_displayed_lowest_price",
                "FLIGGY Level-1 offer has no supported shopping price evidence",
            )
            return None
        currency = _currency(price_text)
        if currency is None:
            self._drop_offer(
                raw_record_ref,
                "raw_displayed_lowest_price",
                "FLIGGY Level-1 offer price currency is not supported by evidence",
            )
            return None

        provider_offer_id = f"fliggy-level1-offer-{_evidence_index(payload)}-{amount}"
        offer = MappedOffer(
            mapped_offer_ref=MappedOfferRef(f"mapped-offer:{provider_offer_id}"),
            provider_offer_id=provider_offer_id,
            itinerary_ref=itinerary.mapped_itinerary_ref,
            total_amount=DomainValue.known(amount),
            currency=DomainValue.known(currency),
            refundable=DomainValue.not_provided(),
            booking_reference=DomainValue.not_provided(),
            provenance=self.provenance(raw_record_ref, provider_offer_id),
            price_semantics=PriceSemantics.LOWER_BOUND,
        )
        self.offers.append(offer)
        return offer

    def provenance(self, raw_record_ref: str, provider_source_id: str) -> MappedProvenance:
        return MappedProvenance(
            provider_id=self.raw_evidence.provider_id,
            acquisition_id=self.raw_evidence.acquisition_id,
            raw_evidence_refs=raw_evidence_ref(self.raw_evidence),
            raw_record_ref=raw_record_ref,
            provider_source_id=provider_source_id,
        )

    def result(self, mapper_version: MapperVersion, data_status: ProviderDataStatus) -> ProviderMappingResult:
        return ProviderMappingResult(
            provider_id=self.raw_evidence.provider_id,
            acquisition_id=self.raw_evidence.acquisition_id,
            search_plan_id=self.raw_evidence.search_plan_id,
            mapper_version=mapper_version,
            data_status=data_status,
            segments=tuple(self.segments),
            itineraries=tuple(self.itineraries),
            offers=tuple(self.offers),
            issues=tuple(self.issues),
            statistics=MappingStatistics(
                raw_segment_count=self.raw_segment_count,
                mapped_segment_count=len(self.segments),
                dropped_segment_count=self.dropped_segment_count,
                raw_itinerary_count=self.raw_itinerary_count,
                mapped_itinerary_count=len(self.itineraries),
                dropped_itinerary_count=self.dropped_itinerary_count,
                raw_offer_count=self.raw_offer_count,
                mapped_offer_count=len(self.offers),
                dropped_offer_count=self.dropped_offer_count,
                issue_count=len(self.issues),
            ),
        )

    @property
    def has_drops_or_issues(self) -> bool:
        return (
            self.dropped_segment_count
            + self.dropped_itinerary_count
            + self.dropped_offer_count
            + len(self.issues)
            > 0
        )

    def issue(
        self,
        raw_record_ref: str,
        raw_path: str,
        category: MappingIssueCategory,
        detail: str,
    ) -> None:
        self.issues.append(
            MappingIssue(
                provider_id=self.raw_evidence.provider_id,
                acquisition_id=self.raw_evidence.acquisition_id,
                raw_record_ref=raw_record_ref,
                raw_path=raw_path,
                category=category,
                detail=detail,
            )
        )

    def _drop_segment(self, raw_record_ref: str, raw_path: str, detail: str) -> None:
        self.issue(raw_record_ref, raw_path, MappingIssueCategory.MALFORMED_REQUIRED_FIELD, detail)
        self.dropped_segment_count += 1

    def _drop_offer(self, raw_record_ref: str, raw_path: str, detail: str) -> None:
        self.issue(raw_record_ref, raw_path, MappingIssueCategory.MALFORMED_REQUIRED_FIELD, detail)
        self.dropped_offer_count += 1


@dataclass(frozen=True)
class _FlightIdentity:
    carrier: str
    full: str


def _empty_mapping_result(
    provider_result: ProviderSearchResult,
    mapper_version: MapperVersion,
) -> ProviderMappingResult:
    return ProviderMappingResult(
        provider_id=provider_result.provider_id,
        acquisition_id=provider_result.acquisition_id,
        search_plan_id=provider_result.search_plan_id,
        mapper_version=mapper_version,
        data_status=provider_result.data_status,
        segments=(),
        itineraries=(),
        offers=(),
        issues=(),
        statistics=MappingStatistics(
            raw_segment_count=0,
            mapped_segment_count=0,
            dropped_segment_count=0,
            raw_itinerary_count=0,
            mapped_itinerary_count=0,
            dropped_itinerary_count=0,
            raw_offer_count=0,
            mapped_offer_count=0,
            dropped_offer_count=0,
            issue_count=0,
        ),
    )


def _data_status_for_unmapped_outcome(provider_result: ProviderSearchResult) -> ProviderDataStatus:
    if provider_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    return provider_result.data_status


def _mapped_data_status(
    provider_result: ProviderSearchResult,
    context: _MappingContext,
) -> ProviderDataStatus:
    if provider_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    if context.has_drops_or_issues:
        if len(context.segments) == 0 and len(context.itineraries) == 0 and len(context.offers) == 0:
            return ProviderDataStatus.UNUSABLE
        return ProviderDataStatus.PARTIAL
    return provider_result.data_status


def _field_text(payload: dict[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if not isinstance(value, dict) or value.get("status") != "OBSERVED":
        return None
    raw_text = value.get("raw_text")
    if not isinstance(raw_text, str) or raw_text.strip() == "":
        return None
    return " ".join(raw_text.split())


def _optional_field_value(payload: dict[str, object], field_name: str) -> DomainValue[str]:
    text = _field_text(payload, field_name)
    if text is None:
        return DomainValue.not_provided()
    return DomainValue.known(text)


def _flight_identity(value: str | None) -> _FlightIdentity | None:
    if value is None:
        return None
    match = _FLIGHT_IDENTITY_PATTERN.search(value)
    if match is None:
        return None
    carrier = match.group(1).upper()
    return _FlightIdentity(carrier=carrier, full=f"{carrier}{match.group(2).upper()}")


def _operating_carrier(payload: dict[str, object]) -> DomainValue[str]:
    text = _field_text(payload, "raw_codeshare_detail_text")
    identity = _flight_identity(text)
    if identity is None:
        return DomainValue.not_provided()
    return DomainValue.known(identity.carrier)


def _departure_date(search_scope: dict[str, object]) -> date | None:
    value = search_scope.get("departure_date")
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _local_datetime(service_date: date, time_text: str) -> str:
    return f"{service_date.isoformat()}T{_time_text(time_text)}:00"


def _arrival_datetime(service_date: date, departure_time: str, arrival_time: str) -> str:
    arrival_date = service_date
    if _time_text(arrival_time) <= _time_text(departure_time):
        arrival_date = service_date + timedelta(days=1)
    return f"{arrival_date.isoformat()}T{_time_text(arrival_time)}:00"


def _time_text(value: str) -> str:
    match = re.search(r"([0-2]?[0-9]):([0-5][0-9])", value)
    if match is None:
        return value.strip()
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _price_amount(value: str | None) -> int | None:
    if value is None:
        return None
    match = _PRICE_PATTERN.search(value)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _currency(value: str | None) -> str | None:
    if value is None:
        return None
    if any(token in value for token in ("¥", "￥", "元", "CNY")):
        return "CNY"
    return None


def _record_ref(payload: object, index: int) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("evidence_index"), int):
        return f"fliggy-level1-evidence:{payload['evidence_index']}"
    return f"fliggy-level1-evidence:index:{index}"


def _evidence_index(payload: dict[str, object]) -> str:
    index = payload.get("evidence_index")
    if isinstance(index, int) and not isinstance(index, bool):
        return str(index)
    return "unknown"


def _thaw_raw_value(value: RawEvidenceValue) -> object:
    if isinstance(value, tuple):
        if all(_is_raw_mapping_item(item) for item in value):
            mapped_value: dict[str, object] = {}
            for item in value:
                if _is_raw_mapping_item(item):
                    mapped_value[item[0]] = _thaw_raw_value(item[1])
            return mapped_value
        return [_thaw_raw_value(item) for item in value]
    return value


def _is_raw_mapping_item(value: object) -> TypeGuard[tuple[str, RawEvidenceValue]]:
    return isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str)
