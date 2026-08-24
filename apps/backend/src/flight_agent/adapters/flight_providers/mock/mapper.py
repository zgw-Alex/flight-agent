"""Mock provider raw-evidence mapper for the M4 anti-corruption layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeGuard

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

MOCK_PROVIDER_MAPPER_VERSION = MapperVersion("mock-provider-mapper-v1")


@dataclass(frozen=True)
class _MappedSegmentDraft:
    segment: MappedSegment
    source_id: str


class MockProviderMapper(ProviderMapper):
    """Provider-specific mapper for the checked-in mock raw schema."""

    def __init__(self, mapper_version: MapperVersion = MOCK_PROVIDER_MAPPER_VERSION) -> None:
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
                detail="Raw payload root is not an object",
            )
            return context.result(self.mapper_version, ProviderDataStatus.UNUSABLE)

        raw_segments = _logical_segments(payload)
        raw_itineraries = _logical_itineraries(payload)
        raw_offers = _logical_offers(payload)

        segments_by_source_id: dict[str, MappedSegment] = {}
        for index, raw_segment in enumerate(raw_segments):
            raw_record_ref = _record_ref("segment", raw_segment, index)
            segment = context.map_segment(raw_segment, raw_record_ref)
            if segment is not None:
                segments_by_source_id[segment.source_id] = segment.segment

        itineraries_by_source_id: dict[str, MappedItinerary] = {}
        for index, raw_itinerary in enumerate(raw_itineraries):
            raw_record_ref = _record_ref("itinerary", raw_itinerary, index)
            itinerary = context.map_itinerary(
                raw_itinerary,
                raw_record_ref,
                segments_by_source_id,
            )
            if itinerary is not None:
                itineraries_by_source_id[itinerary.provider_itinerary_id] = itinerary

        offers: list[MappedOffer] = []
        for index, raw_offer in enumerate(raw_offers):
            raw_record_ref = _record_ref("offer", raw_offer, index)
            offer = context.map_offer(raw_offer, raw_record_ref, itineraries_by_source_id)
            if offer is not None:
                offers.append(offer)

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

    def map_segment(
        self,
        payload: object,
        raw_record_ref: str,
    ) -> _MappedSegmentDraft | None:
        self.raw_segment_count += 1
        if not isinstance(payload, dict):
            self._drop_segment(raw_record_ref, "segment", "Segment record is not an object")
            return None

        provider_segment_id = self.required_string(payload, "provider_segment_id", raw_record_ref)
        marketing_carrier = self.required_string(payload, "carrier", raw_record_ref)
        flight_number = self.required_string(payload, "flight_number", raw_record_ref)
        departure_airport = self.required_string(payload, "departure_airport", raw_record_ref)
        arrival_airport = self.required_string(payload, "arrival_airport", raw_record_ref)
        departure_local = self.required_string(payload, "depart_local", raw_record_ref)
        arrival_local = self.required_string(payload, "arrive_local", raw_record_ref)
        if (
            provider_segment_id is None
            or marketing_carrier is None
            or flight_number is None
            or departure_airport is None
            or arrival_airport is None
            or departure_local is None
            or arrival_local is None
        ):
            self.dropped_segment_count += 1
            return None

        aircraft_type = self.optional_string_value(
            payload,
            "aircraft_type",
            raw_record_ref,
            sentinel_unknown={"UNKNOWN_TOKEN"},
        )
        operating_carrier = self.optional_string_value(payload, "operating_carrier", raw_record_ref)
        checked_baggage_pieces = self.optional_int_value(
            payload,
            "checked_baggage_pieces",
            raw_record_ref,
            sentinel_not_applicable={"NOT_APPLICABLE"},
        )
        overnight = self.optional_bool_value(payload, "overnight", raw_record_ref)
        provenance = self.provenance(raw_record_ref, provider_segment_id)
        segment = MappedSegment(
            mapped_segment_ref=MappedSegmentRef(f"mapped-segment:{provider_segment_id}"),
            provider_segment_id=provider_segment_id,
            marketing_carrier=marketing_carrier.strip().upper(),
            flight_number=flight_number.strip().upper(),
            departure_airport=departure_airport.strip().upper(),
            arrival_airport=arrival_airport.strip().upper(),
            departure_local=departure_local.strip(),
            arrival_local=arrival_local.strip(),
            operating_carrier=operating_carrier,
            aircraft_type=aircraft_type,
            checked_baggage_pieces=checked_baggage_pieces,
            overnight=overnight,
            provenance=provenance,
        )
        self.segments.append(segment)
        return _MappedSegmentDraft(segment=segment, source_id=provider_segment_id)

    def map_itinerary(
        self,
        payload: object,
        raw_record_ref: str,
        segments_by_source_id: dict[str, MappedSegment],
    ) -> MappedItinerary | None:
        self.raw_itinerary_count += 1
        if not isinstance(payload, dict):
            self._drop_itinerary(raw_record_ref, "itinerary", "Itinerary record is not an object")
            return None
        provider_itinerary_id = self.required_string(payload, "provider_itinerary_id", raw_record_ref)
        if provider_itinerary_id is None:
            self.dropped_itinerary_count += 1
            return None
        segment_ids = _segment_refs_from_itinerary(payload)
        if len(segment_ids) == 0:
            self._drop_itinerary(raw_record_ref, "segment_refs", "Itinerary has no declared segments")
            return None
        missing_segment_ids = [
            provider_segment_id
            for provider_segment_id in segment_ids
            if provider_segment_id not in segments_by_source_id
        ]
        if len(missing_segment_ids) > 0:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="segment_refs",
                category=MappingIssueCategory.BROKEN_GRAPH_REFERENCE,
                detail=f"Itinerary references unmapped segments: {', '.join(missing_segment_ids)}",
            )
            self.dropped_itinerary_count += 1
            return None
        itinerary = MappedItinerary(
            mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{provider_itinerary_id}"),
            provider_itinerary_id=provider_itinerary_id,
            segment_refs=tuple(
                segments_by_source_id[provider_segment_id].mapped_segment_ref
                for provider_segment_id in segment_ids
            ),
            provenance=self.provenance(raw_record_ref, provider_itinerary_id),
        )
        self.itineraries.append(itinerary)
        return itinerary

    def map_offer(
        self,
        payload: object,
        raw_record_ref: str,
        itineraries_by_source_id: dict[str, MappedItinerary],
    ) -> MappedOffer | None:
        self.raw_offer_count += 1
        if not isinstance(payload, dict):
            self._drop_offer(raw_record_ref, "offer", "Offer record is not an object")
            return None
        provider_offer_id = self.required_string(payload, "provider_offer_id", raw_record_ref)
        provider_itinerary_id = self.required_string(payload, "provider_itinerary_id", raw_record_ref)
        if provider_offer_id is None or provider_itinerary_id is None:
            self.dropped_offer_count += 1
            return None
        itinerary = itineraries_by_source_id.get(provider_itinerary_id)
        if itinerary is None:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path="provider_itinerary_id",
                category=MappingIssueCategory.ORPHAN_RECORD,
                detail="Offer references an unmapped itinerary",
            )
            self.dropped_offer_count += 1
            return None

        amount = self.required_int_value(payload, "amount", raw_record_ref)
        currency = self.required_string_value(payload, "currency", raw_record_ref)
        if amount is None or currency is None:
            self.dropped_offer_count += 1
            return None
        refundable = self.optional_bool_value(payload, "refundable", raw_record_ref)
        booking_reference = self.optional_string_value(
            payload,
            "booking_reference",
            raw_record_ref,
            sentinel_not_applicable={"NOT_APPLICABLE"},
        )
        offer = MappedOffer(
            mapped_offer_ref=MappedOfferRef(f"mapped-offer:{provider_offer_id}"),
            provider_offer_id=provider_offer_id,
            itinerary_ref=itinerary.mapped_itinerary_ref,
            total_amount=amount,
            currency=currency,
            refundable=refundable,
            booking_reference=booking_reference,
            provenance=self.provenance(raw_record_ref, provider_offer_id),
        )
        self.offers.append(offer)
        return offer

    def required_string(self, payload: dict[str, object], path: str, raw_record_ref: str) -> str | None:
        value = payload.get(path)
        if not isinstance(value, str) or value.strip() == "":
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="Required provider field is missing or not a non-empty string",
            )
            return None
        return value

    def required_int_value(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
    ) -> DomainValue[int] | None:
        value = payload.get(path)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_REQUIRED_FIELD,
                detail="Required provider integer field is malformed",
            )
            return None
        return DomainValue.known(value)

    def required_string_value(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
    ) -> DomainValue[str] | None:
        value = self.required_string(payload, path, raw_record_ref)
        if value is None:
            return None
        return DomainValue.known(value.strip().upper())

    def optional_string_value(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
        *,
        sentinel_unknown: set[str] | None = None,
        sentinel_not_applicable: set[str] | None = None,
    ) -> DomainValue[str]:
        if path not in payload or payload[path] is None:
            return DomainValue.not_provided()
        value = payload[path]
        if not isinstance(value, str):
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_OPTIONAL_FIELD,
                detail="Optional provider string field is malformed",
            )
            return DomainValue.unknown()
        normalized = value.strip().upper()
        if normalized == "":
            return DomainValue.not_provided()
        if sentinel_unknown is not None and normalized in sentinel_unknown:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_OPTIONAL_FIELD,
                detail="Provider sentinel maps to unknown optional value",
            )
            return DomainValue.unknown()
        if sentinel_not_applicable is not None and normalized in sentinel_not_applicable:
            return DomainValue.not_applicable()
        return DomainValue.known(normalized)

    def optional_int_value(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
        *,
        sentinel_not_applicable: set[str] | None = None,
    ) -> DomainValue[int]:
        if path not in payload or payload[path] is None:
            return DomainValue.not_provided()
        value = payload[path]
        if isinstance(value, str):
            normalized = value.strip().upper()
            if sentinel_not_applicable is not None and normalized in sentinel_not_applicable:
                return DomainValue.not_applicable()
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_OPTIONAL_FIELD,
                detail="Optional provider integer field is malformed",
            )
            return DomainValue.unknown()
        return DomainValue.known(value)

    def optional_bool_value(
        self,
        payload: dict[str, object],
        path: str,
        raw_record_ref: str,
    ) -> DomainValue[bool]:
        if path not in payload or payload[path] is None:
            return DomainValue.not_provided()
        value = payload[path]
        if not isinstance(value, bool):
            self.issue(
                raw_record_ref=raw_record_ref,
                raw_path=path,
                category=MappingIssueCategory.MALFORMED_OPTIONAL_FIELD,
                detail="Optional provider boolean field is malformed",
            )
            return DomainValue.unknown()
        return DomainValue.known(value)

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
        statistics = MappingStatistics(
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
        )
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
            statistics=statistics,
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


def _logical_segments(payload: dict[str, object]) -> tuple[object, ...]:
    if isinstance(payload.get("provider_segments"), list):
        return tuple(payload["provider_segments"])  # type: ignore[index]
    segments: list[object] = []
    route_defaults = _provider_request_route_defaults(payload)
    for itinerary in _logical_itineraries(payload):
        if isinstance(itinerary, dict) and isinstance(itinerary.get("segments"), list):
            for segment in itinerary["segments"]:  # type: ignore[index]
                if isinstance(segment, dict) and route_defaults is not None:
                    segment = {**route_defaults, **segment}
                segments.append(segment)
    return tuple(segments)


def _logical_itineraries(payload: dict[str, object]) -> tuple[object, ...]:
    value = payload.get("provider_itineraries")
    if not isinstance(value, list):
        return ()
    return tuple(value)


def _logical_offers(payload: dict[str, object]) -> tuple[object, ...]:
    if isinstance(payload.get("provider_offers"), list):
        return tuple(payload["provider_offers"])  # type: ignore[index]
    offers: list[object] = []
    for itinerary in _logical_itineraries(payload):
        if isinstance(itinerary, dict) and isinstance(itinerary.get("offers"), list):
            provider_itinerary_id = itinerary.get("provider_itinerary_id")
            for offer in itinerary["offers"]:  # type: ignore[index]
                if isinstance(offer, dict) and isinstance(provider_itinerary_id, str):
                    offer = dict(offer)
                    offer.setdefault("provider_itinerary_id", provider_itinerary_id)
                offers.append(offer)
    return tuple(offers)


def _segment_refs_from_itinerary(payload: dict[str, object]) -> tuple[str, ...]:
    if isinstance(payload.get("segment_refs"), list):
        return tuple(item for item in payload["segment_refs"] if isinstance(item, str))  # type: ignore[index]
    if isinstance(payload.get("segments"), list):
        segment_ids: list[str] = []
        for raw_segment in payload["segments"]:  # type: ignore[index]
            if isinstance(raw_segment, dict) and isinstance(raw_segment.get("provider_segment_id"), str):
                segment_ids.append(raw_segment["provider_segment_id"])  # type: ignore[arg-type,index]
        return tuple(segment_ids)
    return ()


def _record_ref(kind: str, payload: object, index: int) -> str:
    if isinstance(payload, dict):
        if kind == "segment" and isinstance(payload.get("provider_segment_id"), str):
            return f"segment:{payload['provider_segment_id']}"
        if kind == "itinerary" and isinstance(payload.get("provider_itinerary_id"), str):
            return f"itinerary:{payload['provider_itinerary_id']}"
        if kind == "offer" and isinstance(payload.get("provider_offer_id"), str):
            return f"offer:{payload['provider_offer_id']}"
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


def _provider_request_route_defaults(payload: dict[str, object]) -> dict[str, object] | None:
    provider_request = payload.get("provider_request")
    if not isinstance(provider_request, dict):
        return None
    origin = provider_request.get("from")
    destination = provider_request.get("to")
    if not isinstance(origin, str) or not isinstance(destination, str):
        return None
    return {
        "departure_airport": origin,
        "arrival_airport": destination,
    }
