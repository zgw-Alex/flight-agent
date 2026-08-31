"""Provider mapper ACL boundary types for M4-U3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from flight_agent.domain.flights import PriceSemantics
from flight_agent.domain.search import SearchPlanId
from flight_agent.domain.shared import DomainId, DomainInvariantViolation, DomainValue
from flight_agent.ports.flight_providers import (
    ProviderAcquisitionId,
    ProviderDataStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)


@dataclass(frozen=True)
class MapperVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("MapperVersion requires a non-empty value")


@dataclass(frozen=True)
class MappedSegmentRef(DomainId):
    """Mapper-local segment reference, not a canonical SegmentId."""


@dataclass(frozen=True)
class MappedItineraryRef(DomainId):
    """Mapper-local itinerary reference, not a canonical ItineraryId."""


@dataclass(frozen=True)
class MappedOfferRef(DomainId):
    """Mapper-local offer reference, not a canonical OfferId."""


class MappingIssueCategory(str, Enum):
    MALFORMED_OPTIONAL_FIELD = "MALFORMED_OPTIONAL_FIELD"
    MALFORMED_REQUIRED_FIELD = "MALFORMED_REQUIRED_FIELD"
    BROKEN_GRAPH_REFERENCE = "BROKEN_GRAPH_REFERENCE"
    ORPHAN_RECORD = "ORPHAN_RECORD"
    UNSUPPORTED_RAW_SHAPE = "UNSUPPORTED_RAW_SHAPE"


@dataclass(frozen=True)
class MappedProvenance:
    provider_id: ProviderId
    acquisition_id: ProviderAcquisitionId
    raw_evidence_refs: tuple[str, ...]
    raw_record_ref: str
    provider_source_id: str

    def __post_init__(self) -> None:
        if len(self.raw_evidence_refs) == 0:
            raise DomainInvariantViolation("MappedProvenance requires raw evidence refs")
        if self.raw_record_ref.strip() == "" or self.provider_source_id.strip() == "":
            raise DomainInvariantViolation("MappedProvenance requires record and source identity")
        object.__setattr__(self, "raw_evidence_refs", tuple(self.raw_evidence_refs))


@dataclass(frozen=True)
class MappingIssue:
    provider_id: ProviderId
    acquisition_id: ProviderAcquisitionId
    raw_record_ref: str
    raw_path: str
    category: MappingIssueCategory
    detail: str

    def __post_init__(self) -> None:
        if self.raw_record_ref.strip() == "" or self.raw_path.strip() == "" or self.detail.strip() == "":
            raise DomainInvariantViolation("MappingIssue requires record, path, and detail")


@dataclass(frozen=True)
class MappedSegment:
    mapped_segment_ref: MappedSegmentRef
    provider_segment_id: str
    marketing_carrier: str
    flight_number: str
    departure_airport: str
    arrival_airport: str
    departure_local: str
    arrival_local: str
    operating_carrier: DomainValue[str]
    aircraft_type: DomainValue[str]
    checked_baggage_pieces: DomainValue[int]
    overnight: DomainValue[bool]
    provenance: MappedProvenance


@dataclass(frozen=True, init=False)
class MappedItinerary:
    mapped_itinerary_ref: MappedItineraryRef
    provider_itinerary_id: str
    segment_refs: tuple[MappedSegmentRef, ...]
    provenance: MappedProvenance

    def __init__(
        self,
        mapped_itinerary_ref: MappedItineraryRef,
        provider_itinerary_id: str,
        segment_refs: tuple[MappedSegmentRef, ...],
        provenance: MappedProvenance,
    ) -> None:
        segment_refs_tuple = tuple(segment_refs)
        if len(segment_refs_tuple) == 0:
            raise DomainInvariantViolation("MappedItinerary requires at least one segment ref")
        object.__setattr__(self, "mapped_itinerary_ref", mapped_itinerary_ref)
        object.__setattr__(self, "provider_itinerary_id", provider_itinerary_id)
        object.__setattr__(self, "segment_refs", segment_refs_tuple)
        object.__setattr__(self, "provenance", provenance)


@dataclass(frozen=True)
class MappedOffer:
    mapped_offer_ref: MappedOfferRef
    provider_offer_id: str
    itinerary_ref: MappedItineraryRef
    total_amount: DomainValue[int]
    currency: DomainValue[str]
    refundable: DomainValue[bool]
    booking_reference: DomainValue[str]
    provenance: MappedProvenance
    price_semantics: PriceSemantics = PriceSemantics.EXACT

    def __post_init__(self) -> None:
        if not isinstance(self.price_semantics, PriceSemantics):
            raise DomainInvariantViolation("MappedOffer price_semantics must be a PriceSemantics")


@dataclass(frozen=True)
class MappingStatistics:
    raw_segment_count: int
    mapped_segment_count: int
    dropped_segment_count: int
    raw_itinerary_count: int
    mapped_itinerary_count: int
    dropped_itinerary_count: int
    raw_offer_count: int
    mapped_offer_count: int
    dropped_offer_count: int
    issue_count: int

    def __post_init__(self) -> None:
        values = (
            self.raw_segment_count,
            self.mapped_segment_count,
            self.dropped_segment_count,
            self.raw_itinerary_count,
            self.mapped_itinerary_count,
            self.dropped_itinerary_count,
            self.raw_offer_count,
            self.mapped_offer_count,
            self.dropped_offer_count,
            self.issue_count,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise DomainInvariantViolation("MappingStatistics counts must be non-negative integers")


@dataclass(frozen=True, init=False)
class ProviderMappingResult:
    provider_id: ProviderId
    acquisition_id: ProviderAcquisitionId
    search_plan_id: SearchPlanId
    mapper_version: MapperVersion
    data_status: ProviderDataStatus
    segments: tuple[MappedSegment, ...]
    itineraries: tuple[MappedItinerary, ...]
    offers: tuple[MappedOffer, ...]
    issues: tuple[MappingIssue, ...]
    statistics: MappingStatistics

    def __init__(
        self,
        provider_id: ProviderId,
        acquisition_id: ProviderAcquisitionId,
        search_plan_id: SearchPlanId,
        mapper_version: MapperVersion,
        data_status: ProviderDataStatus,
        segments: tuple[MappedSegment, ...],
        itineraries: tuple[MappedItinerary, ...],
        offers: tuple[MappedOffer, ...],
        issues: tuple[MappingIssue, ...],
        statistics: MappingStatistics,
    ) -> None:
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "acquisition_id", acquisition_id)
        object.__setattr__(self, "search_plan_id", search_plan_id)
        object.__setattr__(self, "mapper_version", mapper_version)
        object.__setattr__(self, "data_status", data_status)
        object.__setattr__(self, "segments", tuple(segments))
        object.__setattr__(self, "itineraries", tuple(itineraries))
        object.__setattr__(self, "offers", tuple(offers))
        object.__setattr__(self, "issues", tuple(issues))
        object.__setattr__(self, "statistics", statistics)


class ProviderMapper(Protocol):
    @property
    def mapper_version(self) -> MapperVersion:
        """Version of provider-specific mapping semantics."""
        ...

    def map(self, provider_result: ProviderSearchResult) -> ProviderMappingResult:
        """Map provider raw evidence into non-authoritative intermediate data."""
        ...


def raw_evidence_ref(raw_evidence: ProviderRawEvidence) -> tuple[str, ...]:
    if len(raw_evidence.source_refs) > 0:
        return raw_evidence.source_refs
    return (raw_evidence.acquisition_id.value,)
