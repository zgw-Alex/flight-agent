"""CTRIP provider-local evidence mapper for the M9 mapped boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeGuard

from flight_agent.adapters.flight_providers.ctrip.browser_probe import CTRIP_PROVIDER_ID
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

CTRIP_PROVIDER_MAPPER_VERSION = MapperVersion("ctrip-evidence-to-mapped-mapper-v1")


@dataclass(frozen=True)
class _MappedSegmentDraft:
    segment: MappedSegment
    source_id: str


class CtripProviderMapper(ProviderMapper):
    """Map sanitized CTRIP assisted/probe evidence into existing M4 mapped types."""

    def __init__(self, mapper_version: MapperVersion = CTRIP_PROVIDER_MAPPER_VERSION) -> None:
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
                detail="CTRIP raw payload root is not an object",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        if _provider_identity(payload) not in ("", CTRIP_PROVIDER_ID):
            context.issue(
                raw_record_ref="raw:payload",
                raw_path="provider_identity",
                category=MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
                detail="Raw evidence is not identified as CTRIP evidence",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        level1 = _evidence_list(payload, "level1_evidence")
        level2 = _evidence_list(payload, "level2_offer_evidence")

        segments_by_source_id: dict[str, MappedSegment] = {}
        for index, raw_level1 in enumerate(level1):
            raw_record_ref = _record_ref("level1", raw_level1, index)
            segment = context.map_level1_segment(raw_level1, raw_record_ref)
            if segment is not None:
                segments_by_source_id[segment.source_id] = segment.segment

        itineraries_by_source_id: dict[str, MappedItinerary] = {}
        for index, raw_level1 in enumerate(level1):
            raw_record_ref = _record_ref("level1", raw_level1, index)
            itinerary = context.map_level1_itinerary(
                raw_level1,
                raw_record_ref,
                segments_by_source_id,
            )
            if itinerary is not None:
                itineraries_by_source_id[itinerary.provider_itinerary_id] = itinerary

        for index, raw_level1 in enumerate(level1):
            context.note_unmapped_level1_price_list(
                raw_level1,
                _record_ref("level1", raw_level1, index),
            )

        for index, raw_level2 in enumerate(level2):
            context.map_level2_offer(
                raw_level2,
                _record_ref("level2-offer", raw_level2, index),
                itineraries_by_source_id,
            )

        return context.result(
            mapper_version=self.mapper_version,
            data_status=_mapped_data_status(provider_result, context),
        )


class _MappingContext:
    def __init__(self, raw_evidence: ProviderRawEvidence) -> None:
        self.raw_evidence = raw_evidence
        self.issues: list[MappingIssue] = []
        self.segments: list[MappedSegment] = []
        self.itineraries: list[MappedItinerary] = []
        self.offers: list[MappedOffer] = []
        self.raw_segment_count = 0
        self.raw_itinerary_count = 0
        self.raw_offer_count = 0
        self.dropped_segment_count = 0
        self.dropped_itinerary_count = 0
        self.dropped_offer_count = 0

    def map_level1_segment(
        self,
        payload: object,
        raw_record_ref: str,
    ) -> _MappedSegmentDraft | None:
        self.raw_segment_count += 1
        if not isinstance(payload, dict):
            self._drop_segment(raw_record_ref, "level1_evidence", "Level-1 evidence is not an object")
            return None

        provider_itinerary_id = self.required_field_string(payload, "itinerary_id", raw_record_ref)
        marketing_carrier = self.required_field_string(payload, "market_airline_code", raw_record_ref)
        flight_number = self.required_field_string(payload, "flight_no", raw_record_ref)
        departure_airport = self.required_field_string(payload, "departure_airport", raw_record_ref)
        arrival_airport = self.required_field_string(payload, "arrival_airport", raw_record_ref)
        departure_local = self.required_field_string(payload, "departure_datetime", raw_record_ref)
        arrival_local = self.required_field_string(payload, "arrival_datetime", raw_record_ref)
        if (
            provider_itinerary_id is None
            or marketing_carrier is None
            or flight_number is None
            or departure_airport is None
            or arrival_airport is None
            or departure_local is None
            or arrival_local is None
        ):
            self.dropped_segment_count += 1
            return None

        provider_segment_id = f"{provider_itinerary_id}:segment:1"
        segment = MappedSegment(
            mapped_segment_ref=MappedSegmentRef(f"mapped-segment:ctrip:{provider_segment_id}"),
            provider_segment_id=provider_segment_id,
            marketing_carrier=marketing_carrier.strip().upper(),
            flight_number=flight_number.strip().upper(),
            departure_airport=departure_airport.strip().upper(),
            arrival_airport=arrival_airport.strip().upper(),
            departure_local=departure_local.strip(),
            arrival_local=arrival_local.strip(),
            operating_carrier=DomainValue.not_provided(),
            aircraft_type=self.optional_field_string(payload, "aircraft", raw_record_ref),
            checked_baggage_pieces=DomainValue.not_provided(),
            overnight=DomainValue.not_provided(),
            provenance=self.provenance(raw_record_ref, provider_segment_id),
        )
        self.segments.append(segment)
        return _MappedSegmentDraft(segment=segment, source_id=provider_segment_id)

    def map_level1_itinerary(
        self,
        payload: object,
        raw_record_ref: str,
        segments_by_source_id: dict[str, MappedSegment],
    ) -> MappedItinerary | None:
        self.raw_itinerary_count += 1
        if not isinstance(payload, dict):
            self._drop_itinerary(raw_record_ref, "level1_evidence", "Level-1 evidence is not an object")
            return None

        provider_itinerary_id = self.required_field_string(payload, "itinerary_id", raw_record_ref)
        if provider_itinerary_id is None:
            self.dropped_itinerary_count += 1
            return None
        provider_segment_id = f"{provider_itinerary_id}:segment:1"
        segment = segments_by_source_id.get(provider_segment_id)
        if segment is None:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="itinerary_id",
                category=MappingIssueCategory.BROKEN_GRAPH_REFERENCE,
                detail="CTRIP itinerary references a segment that was not mapped",
            )
            self.dropped_itinerary_count += 1
            return None
        itinerary = MappedItinerary(
            mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:ctrip:{provider_itinerary_id}"),
            provider_itinerary_id=provider_itinerary_id,
            segment_refs=(segment.mapped_segment_ref,),
            provenance=self.provenance(raw_record_ref, provider_itinerary_id),
        )
        self.itineraries.append(itinerary)
        return itinerary

    def note_unmapped_level1_price_list(self, payload: object, raw_record_ref: str) -> None:
        if not isinstance(payload, dict):
            return
        price_list = _field(payload, "price_list")
        if not _field_observed(price_list):
            return
        self.raw_offer_count += 1
        self.dropped_offer_count += 1
        self.issue(
            raw_record_ref=raw_record_ref,
            raw_path="price_list",
            category=MappingIssueCategory.UNSUPPORTED_RAW_SHAPE,
            detail=(
                "CTRIP Level-1 priceList is provider raw structure only; "
                "it is not mapped as an independent priced Offer"
            ),
        )

    def map_level2_offer(
        self,
        payload: object,
        raw_record_ref: str,
        itineraries_by_source_id: dict[str, MappedItinerary],
    ) -> MappedOffer | None:
        self.raw_offer_count += 1
        if not isinstance(payload, dict):
            self._drop_offer(raw_record_ref, "level2_offer_evidence", "Level-2 offer evidence is not an object")
            return None
        if len(itineraries_by_source_id) != 1:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="itinerary_ref",
                category=MappingIssueCategory.ORPHAN_RECORD,
                detail="CTRIP Level-2 offer evidence cannot be attached to exactly one mapped itinerary",
            )
            self.dropped_offer_count += 1
            return None

        provider_offer_id = self.required_field_string(
            payload,
            "product_or_fare_identity",
            raw_record_ref,
        )
        amount = self.required_price_amount(payload, raw_record_ref)
        if provider_offer_id is None or amount is None:
            self.dropped_offer_count += 1
            return None

        itinerary = next(iter(itineraries_by_source_id.values()))
        price_semantics = _price_semantics(payload)
        offer = MappedOffer(
            mapped_offer_ref=MappedOfferRef(f"mapped-offer:ctrip:{provider_offer_id}"),
            provider_offer_id=provider_offer_id.strip(),
            itinerary_ref=itinerary.mapped_itinerary_ref,
            total_amount=DomainValue.known(amount),
            currency=DomainValue.known("CNY"),
            refundable=DomainValue.not_provided(),
            booking_reference=self.optional_field_string(
                payload,
                "booking_action_identity",
                raw_record_ref,
            ),
            provenance=self.provenance(raw_record_ref, provider_offer_id),
            price_semantics=price_semantics,
        )
        self.offers.append(offer)
        return offer

    def required_field_string(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
    ) -> str | None:
        evidence = _field(payload, path)
        if not _field_observed(evidence):
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="Required CTRIP evidence field is missing",
            )
            return None
        value = evidence.get("raw_value")
        if not isinstance(value, str) or value.strip() == "":
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="Required CTRIP evidence field is not a non-empty string",
            )
            return None
        return value

    def optional_field_string(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
    ) -> DomainValue[str]:
        evidence = _field(payload, path)
        if evidence is None or evidence.get("status") == "MISSING":
            return DomainValue.not_provided()
        if evidence.get("status") != "OBSERVED":
            return DomainValue.unknown()
        value = evidence.get("raw_value")
        if not isinstance(value, str):
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_OPTIONAL_FIELD,
                detail="Optional CTRIP evidence field is malformed",
            )
            return DomainValue.unknown()
        if value.strip() == "":
            return DomainValue.not_provided()
        return DomainValue.known(value.strip().upper())

    def required_price_amount(
        self,
        payload: dict[str, object],
        raw_record_ref: str,
    ) -> int | None:
        evidence = _field(payload, "price")
        if not _field_observed(evidence):
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="price",
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="CTRIP Level-2 offer is missing a supported shopping price fact",
            )
            return None
        amount = _money_amount(evidence.get("raw_value"))
        if amount is None:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="price",
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="CTRIP Level-2 price evidence is not a supported positive integer amount",
            )
            return None
        return amount

    def provenance(self, raw_record_ref: str, provider_source_id: str) -> MappedProvenance:
        return MappedProvenance(
            provider_id=self.raw_evidence.provider_id,
            acquisition_id=self.raw_evidence.acquisition_id,
            raw_evidence_refs=raw_evidence_ref(self.raw_evidence),
            raw_record_ref=raw_record_ref,
            provider_source_id=provider_source_id,
        )

    def result(
        self,
        mapper_version: MapperVersion,
        data_status: ProviderDataStatus,
    ) -> ProviderMappingResult:
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
    def statistics_has_drops_or_issues(self) -> bool:
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

    def _drop_itinerary(self, raw_record_ref: str, raw_path: str, detail: str) -> None:
        self.issue(raw_record_ref, raw_path, MappingIssueCategory.MALFORMED_REQUIRED_FIELD, detail)
        self.dropped_itinerary_count += 1

    def _drop_offer(self, raw_record_ref: str, raw_path: str, detail: str) -> None:
        self.issue(raw_record_ref, raw_path, MappingIssueCategory.MALFORMED_REQUIRED_FIELD, detail)
        self.dropped_offer_count += 1


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


def _mapped_data_status(
    provider_result: ProviderSearchResult,
    context: _MappingContext,
) -> ProviderDataStatus:
    if provider_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    if context.statistics_has_drops_or_issues:
        if len(context.segments) == 0 and len(context.itineraries) == 0 and len(context.offers) == 0:
            return ProviderDataStatus.UNUSABLE
        return ProviderDataStatus.PARTIAL
    return provider_result.data_status


def _provider_identity(payload: dict[str, object]) -> str:
    value = payload.get("provider_identity")
    return value if isinstance(value, str) else ""


def _evidence_list(payload: dict[str, object], path: str) -> tuple[object, ...]:
    value = payload.get(path)
    if isinstance(value, list):
        return tuple(value)
    return ()


def _field(payload: dict[str, object], path: str) -> dict[str, object] | None:
    value = payload.get(path)
    return value if isinstance(value, dict) else None


def _field_observed(evidence: dict[str, object] | None) -> TypeGuard[dict[str, object]]:
    return evidence is not None and evidence.get("status") == "OBSERVED"


def _money_amount(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, float) and value > 0 and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
        if match is None:
            return None
        parsed = float(match.group(0))
        if parsed <= 0 or not parsed.is_integer():
            return None
        return int(parsed)
    return None


def _price_semantics(payload: dict[str, object]) -> PriceSemantics:
    price = _field(payload, "price")
    if price is None:
        return PriceSemantics.EXACT
    text = " ".join(
        str(value)
        for key in ("raw_value", "diagnostic", "evidence_path")
        if (value := price.get(key)) is not None
    ).lower()
    if any(token in text for token in ("起", "最低", "from", "starting", "lower_bound")):
        return PriceSemantics.LOWER_BOUND
    return PriceSemantics.EXACT


def _record_ref(kind: str, payload: object, index: int) -> str:
    if isinstance(payload, dict):
        if kind == "level1":
            itinerary_id = _field(payload, "itinerary_id")
            if _field_observed(itinerary_id):
                value = itinerary_id.get("raw_value")
                if isinstance(value, str) and value.strip():
                    return f"ctrip-level1:{value}"
        if kind == "level2-offer":
            product = _field(payload, "product_or_fare_identity")
            if _field_observed(product):
                value = product.get("raw_value")
                if isinstance(value, str) and value.strip():
                    return f"ctrip-level2-offer:{value}"
    return f"{kind}:index:{index}"


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
