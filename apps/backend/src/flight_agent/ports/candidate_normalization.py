"""Provider-neutral normalization and conservative candidate merge contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from flight_agent.domain.flights import (
    FlightSegment,
    Itinerary,
    ItineraryId,
    Money,
    Offer,
    OfferId,
    SegmentId,
)
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    ValueState,
)
from flight_agent.ports.flight_providers import ProviderDataStatus
from flight_agent.ports.provider_mapping import (
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    ProviderMappingResult,
)


@dataclass(frozen=True)
class NormalizerVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("NormalizerVersion requires a non-empty value")


@dataclass(frozen=True)
class ReferenceDataVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("ReferenceDataVersion requires a non-empty value")


@dataclass(frozen=True)
class MergerVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("MergerVersion requires a non-empty value")


@dataclass(frozen=True, init=False)
class ReferenceData:
    version: ReferenceDataVersion
    airports: frozenset[str]
    carriers: frozenset[str]

    def __init__(
        self,
        version: ReferenceDataVersion,
        airports: frozenset[str],
        carriers: frozenset[str],
    ) -> None:
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "airports", frozenset(code.strip().upper() for code in airports))
        object.__setattr__(self, "carriers", frozenset(code.strip().upper() for code in carriers))


@dataclass(frozen=True)
class NormalizationContext:
    normalizer_version: NormalizerVersion
    reference_data: ReferenceData


class NormalizationIssueCategory(str, Enum):
    UNRESOLVABLE_REFERENCE = "UNRESOLVABLE_REFERENCE"
    IDENTITY_CRITICAL_MISSING = "IDENTITY_CRITICAL_MISSING"
    CANONICAL_INVARIANT_VIOLATION = "CANONICAL_INVARIANT_VIOLATION"
    ORPHAN_RECORD = "ORPHAN_RECORD"


@dataclass(frozen=True)
class NormalizationIssue:
    source_ref: str
    path: str
    category: NormalizationIssueCategory
    detail: str
    provenance: tuple[ProvenanceRef, ...]

    def __post_init__(self) -> None:
        if self.source_ref.strip() == "" or self.path.strip() == "" or self.detail.strip() == "":
            raise DomainInvariantViolation("NormalizationIssue requires source, path, and detail")
        object.__setattr__(self, "provenance", tuple(self.provenance))


@dataclass(frozen=True)
class NormalizationStatistics:
    mapped_segment_count: int
    normalized_segment_count: int
    dropped_segment_count: int
    mapped_itinerary_count: int
    normalized_itinerary_count: int
    dropped_itinerary_count: int
    mapped_offer_count: int
    normalized_offer_count: int
    dropped_offer_count: int
    issue_count: int


@dataclass(frozen=True)
class NormalizationResult:
    mapper_version: MapperVersion
    normalizer_version: NormalizerVersion
    reference_data_version: ReferenceDataVersion
    data_status: ProviderDataStatus
    segments: tuple[FlightSegment, ...]
    itineraries: tuple[Itinerary, ...]
    offers: tuple[Offer, ...]
    segment_sources: tuple[tuple[SegmentId, MappedSegmentRef], ...]
    itinerary_sources: tuple[tuple[ItineraryId, MappedItineraryRef], ...]
    offer_sources: tuple[tuple[OfferId, MappedOfferRef], ...]
    issues: tuple[NormalizationIssue, ...]
    statistics: NormalizationStatistics


class EquivalenceDecision(str, Enum):
    EQUIVALENT = "EQUIVALENT"
    DISTINCT = "DISTINCT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class MergeEvidenceCategory(str, Enum):
    IDENTITY_CONTRADICTION = "IDENTITY_CONTRADICTION"
    ATTRIBUTE_CONFLICT = "ATTRIBUTE_CONFLICT"
    SOURCE_IDENTITY_CONFLICT = "SOURCE_IDENTITY_CONFLICT"
    INSUFFICIENT_EQUIVALENCE_EVIDENCE = "INSUFFICIENT_EQUIVALENCE_EVIDENCE"
    MERGED_EQUIVALENT = "MERGED_EQUIVALENT"


@dataclass(frozen=True)
class MergeEvidence:
    category: MergeEvidenceCategory
    decision: EquivalenceDecision
    source_ids: tuple[str, ...]
    detail: str
    provenance: tuple[ProvenanceRef, ...]

    def __post_init__(self) -> None:
        if len(self.source_ids) == 0 or self.detail.strip() == "":
            raise DomainInvariantViolation("MergeEvidence requires source ids and detail")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "provenance", tuple(self.provenance))


@dataclass(frozen=True)
class MergedCandidateGraph:
    normalizer_versions: tuple[NormalizerVersion, ...]
    reference_data_versions: tuple[ReferenceDataVersion, ...]
    mapper_versions: tuple[MapperVersion, ...]
    merger_version: MergerVersion
    data_status: ProviderDataStatus
    segments: tuple[FlightSegment, ...]
    itineraries: tuple[Itinerary, ...]
    offers: tuple[Offer, ...]
    evidence: tuple[MergeEvidence, ...]
    normalization_issues: tuple[NormalizationIssue, ...]


class CommonNormalizer:
    def normalize(
        self,
        mapping_result: ProviderMappingResult,
        context: NormalizationContext,
    ) -> NormalizationResult:
        issues: list[NormalizationIssue] = []
        segments: list[FlightSegment] = []
        segment_sources: list[tuple[SegmentId, MappedSegmentRef]] = []
        segment_by_ref: dict[MappedSegmentRef, FlightSegment] = {}
        for mapped_segment in mapping_result.segments:
            segment = _normalize_segment(mapped_segment, context, issues)
            if segment is not None:
                segments.append(segment)
                segment_sources.append((segment.segment_id, mapped_segment.mapped_segment_ref))
                segment_by_ref[mapped_segment.mapped_segment_ref] = segment

        itineraries: list[Itinerary] = []
        itinerary_sources: list[tuple[ItineraryId, MappedItineraryRef]] = []
        itinerary_by_ref: dict[MappedItineraryRef, Itinerary] = {}
        for mapped_itinerary in mapping_result.itineraries:
            itinerary = _normalize_itinerary(mapped_itinerary, segment_by_ref, issues)
            if itinerary is not None:
                itineraries.append(itinerary)
                itinerary_sources.append((itinerary.itinerary_id, mapped_itinerary.mapped_itinerary_ref))
                itinerary_by_ref[mapped_itinerary.mapped_itinerary_ref] = itinerary

        offers: list[Offer] = []
        offer_sources: list[tuple[OfferId, MappedOfferRef]] = []
        for mapped_offer in mapping_result.offers:
            offer = _normalize_offer(mapped_offer, itinerary_by_ref, issues)
            if offer is not None:
                offers.append(offer)
                offer_sources.append((offer.offer_id, mapped_offer.mapped_offer_ref))

        statistics = NormalizationStatistics(
            mapped_segment_count=len(mapping_result.segments),
            normalized_segment_count=len(segments),
            dropped_segment_count=len(mapping_result.segments) - len(segments),
            mapped_itinerary_count=len(mapping_result.itineraries),
            normalized_itinerary_count=len(itineraries),
            dropped_itinerary_count=len(mapping_result.itineraries) - len(itineraries),
            mapped_offer_count=len(mapping_result.offers),
            normalized_offer_count=len(offers),
            dropped_offer_count=len(mapping_result.offers) - len(offers),
            issue_count=len(issues),
        )
        return NormalizationResult(
            mapper_version=mapping_result.mapper_version,
            normalizer_version=context.normalizer_version,
            reference_data_version=context.reference_data.version,
            data_status=_normalization_data_status(mapping_result, segments, itineraries, offers, issues),
            segments=tuple(sorted(segments, key=lambda segment: segment.segment_id.value)),
            itineraries=tuple(sorted(itineraries, key=lambda itinerary: itinerary.itinerary_id.value)),
            offers=tuple(sorted(offers, key=lambda offer: offer.offer_id.value)),
            segment_sources=tuple(sorted(segment_sources, key=lambda item: item[0].value)),
            itinerary_sources=tuple(sorted(itinerary_sources, key=lambda item: item[0].value)),
            offer_sources=tuple(sorted(offer_sources, key=lambda item: item[0].value)),
            issues=tuple(issues),
            statistics=statistics,
        )


class CandidateMerger:
    def __init__(self, merger_version: MergerVersion) -> None:
        self.merger_version = merger_version

    def segment_equivalence(
        self,
        left: FlightSegment,
        right: FlightSegment,
    ) -> EquivalenceDecision:
        left_key = _segment_identity_key(left)
        right_key = _segment_identity_key(right)
        if left_key is None or right_key is None:
            return EquivalenceDecision.INSUFFICIENT_EVIDENCE
        if left_key == right_key:
            return EquivalenceDecision.EQUIVALENT
        return EquivalenceDecision.DISTINCT

    def itinerary_equivalence(
        self,
        left: Itinerary,
        right: Itinerary,
        segments_by_id: dict[SegmentId, FlightSegment],
    ) -> EquivalenceDecision:
        if len(left.segment_ids) != len(right.segment_ids):
            return EquivalenceDecision.DISTINCT
        decisions = [
            self.segment_equivalence(segments_by_id[left_id], segments_by_id[right_id])
            for left_id, right_id in zip(left.segment_ids, right.segment_ids, strict=True)
        ]
        if all(decision is EquivalenceDecision.EQUIVALENT for decision in decisions):
            return EquivalenceDecision.EQUIVALENT
        if any(decision is EquivalenceDecision.DISTINCT for decision in decisions):
            return EquivalenceDecision.DISTINCT
        return EquivalenceDecision.INSUFFICIENT_EVIDENCE

    def offer_equivalence(self, left: Offer, right: Offer) -> EquivalenceDecision:
        left_sources = {_provider_source_ref(ref) for ref in left.provenance}
        right_sources = {_provider_source_ref(ref) for ref in right.provenance}
        if (
            len(left_sources) == 1
            and left_sources == right_sources
            and left.total_price == right.total_price
            and left.booking_reference == right.booking_reference
            and left.price_semantics == right.price_semantics
        ):
            return EquivalenceDecision.EQUIVALENT
        if left.total_price == right.total_price and left.price_semantics == right.price_semantics:
            return EquivalenceDecision.INSUFFICIENT_EVIDENCE
        return EquivalenceDecision.DISTINCT

    def merge(self, normalization_results: tuple[NormalizationResult, ...]) -> MergedCandidateGraph:
        ordered_results = sorted(
            normalization_results,
            key=lambda result: (
                result.mapper_version.value,
                result.normalizer_version.value,
                result.reference_data_version.value,
                tuple(segment.segment_id.value for segment in result.segments),
            ),
        )
        evidence: list[MergeEvidence] = []
        segments, segment_rewrites = self._merge_segments(ordered_results, evidence)
        itineraries, itinerary_rewrites = self._merge_itineraries(
            ordered_results,
            segments,
            segment_rewrites,
        )
        offers = self._merge_offers(ordered_results, itinerary_rewrites, evidence)
        return MergedCandidateGraph(
            normalizer_versions=tuple(sorted({r.normalizer_version for r in ordered_results}, key=lambda v: v.value)),
            reference_data_versions=tuple(
                sorted({r.reference_data_version for r in ordered_results}, key=lambda v: v.value)
            ),
            mapper_versions=tuple(sorted({r.mapper_version for r in ordered_results}, key=lambda v: v.value)),
            merger_version=self.merger_version,
            data_status=_merged_data_status(ordered_results),
            segments=tuple(sorted(segments, key=lambda segment: segment.segment_id.value)),
            itineraries=tuple(sorted(itineraries, key=lambda itinerary: itinerary.itinerary_id.value)),
            offers=tuple(sorted(offers, key=lambda offer: offer.offer_id.value)),
            evidence=tuple(evidence),
            normalization_issues=tuple(issue for result in ordered_results for issue in result.issues),
        )

    def _merge_segments(
        self,
        results: list[NormalizationResult],
        evidence: list[MergeEvidence],
    ) -> tuple[list[FlightSegment], dict[SegmentId, SegmentId]]:
        canonical: list[FlightSegment] = []
        rewrites: dict[SegmentId, SegmentId] = {}
        for segment in sorted((s for r in results for s in r.segments), key=lambda s: s.segment_id.value):
            match = next(
                (
                    existing
                    for existing in canonical
                    if self.segment_equivalence(existing, segment) is EquivalenceDecision.EQUIVALENT
                ),
                None,
            )
            if match is None:
                for existing in canonical:
                    decision = self.segment_equivalence(existing, segment)
                    if decision is EquivalenceDecision.INSUFFICIENT_EVIDENCE:
                        evidence.append(
                            _merge_evidence(
                                MergeEvidenceCategory.INSUFFICIENT_EQUIVALENCE_EVIDENCE,
                                decision,
                                (existing.segment_id.value, segment.segment_id.value),
                                "Segment evidence is insufficient",
                                existing.provenance + segment.provenance,
                            )
                        )
                consolidated = _with_segment_id(segment, SegmentId(f"segment:{len(canonical) + 1:04d}"))
                canonical.append(consolidated)
                rewrites[segment.segment_id] = consolidated.segment_id
            else:
                merged = _merge_segment_projection(match, segment, evidence)
                canonical[canonical.index(match)] = _with_segment_id(merged, match.segment_id)
                rewrites[segment.segment_id] = match.segment_id
        return canonical, rewrites

    def _merge_itineraries(
        self,
        results: list[NormalizationResult],
        segments: list[FlightSegment],
        segment_rewrites: dict[SegmentId, SegmentId],
    ) -> tuple[list[Itinerary], dict[ItineraryId, ItineraryId]]:
        segments_by_id = {segment.segment_id: segment for segment in segments}
        canonical: list[Itinerary] = []
        rewrites: dict[ItineraryId, ItineraryId] = {}
        for itinerary in sorted((i for r in results for i in r.itineraries), key=lambda i: i.itinerary_id.value):
            rewired_ids = tuple(segment_rewrites[segment_id] for segment_id in itinerary.segment_ids)
            rewired = Itinerary(itinerary.itinerary_id, rewired_ids, itinerary.provenance)
            match = next(
                (
                    existing
                    for existing in canonical
                    if self.itinerary_equivalence(existing, rewired, segments_by_id)
                    is EquivalenceDecision.EQUIVALENT
                ),
                None,
            )
            if match is None:
                consolidated = Itinerary(
                    ItineraryId(f"itinerary:{len(canonical) + 1:04d}"),
                    rewired.segment_ids,
                    rewired.provenance,
                )
                canonical.append(consolidated)
                rewrites[itinerary.itinerary_id] = consolidated.itinerary_id
            else:
                merged = Itinerary(
                    match.itinerary_id,
                    match.segment_ids,
                    _union_provenance(match.provenance, rewired.provenance),
                )
                canonical[canonical.index(match)] = merged
                rewrites[itinerary.itinerary_id] = match.itinerary_id
        return canonical, rewrites

    def _merge_offers(
        self,
        results: list[NormalizationResult],
        itinerary_rewrites: dict[ItineraryId, ItineraryId],
        evidence: list[MergeEvidence],
    ) -> list[Offer]:
        canonical: list[Offer] = []
        for offer in sorted((o for r in results for o in r.offers), key=lambda o: o.offer_id.value):
            rewired = Offer(
                offer_id=offer.offer_id,
                itinerary_id=itinerary_rewrites[offer.itinerary_id],
                total_price=offer.total_price,
                offer_freshness=offer.offer_freshness,
                booking_reference=offer.booking_reference,
                provenance=offer.provenance,
                price_semantics=offer.price_semantics,
            )
            match = next(
                (
                    existing
                    for existing in canonical
                    if self.offer_equivalence(existing, rewired) is EquivalenceDecision.EQUIVALENT
                ),
                None,
            )
            if match is None:
                for existing in canonical:
                    decision = self.offer_equivalence(existing, rewired)
                    if decision is EquivalenceDecision.INSUFFICIENT_EVIDENCE:
                        evidence.append(
                            _merge_evidence(
                                MergeEvidenceCategory.SOURCE_IDENTITY_CONFLICT,
                                decision,
                                (existing.offer_id.value, rewired.offer_id.value),
                                "Offer identity evidence is insufficient; keeping offers separate",
                                existing.provenance + rewired.provenance,
                            )
                        )
                canonical.append(_with_offer_id(rewired, OfferId(f"offer:{len(canonical) + 1:04d}")))
            else:
                merged = Offer(
                    offer_id=match.offer_id,
                    itinerary_id=match.itinerary_id,
                    total_price=match.total_price,
                    offer_freshness=match.offer_freshness,
                    booking_reference=match.booking_reference,
                    provenance=_union_provenance(match.provenance, rewired.provenance),
                    price_semantics=match.price_semantics,
                )
                canonical[canonical.index(match)] = merged
        return canonical


def _normalize_segment(
    mapped: MappedSegment,
    context: NormalizationContext,
    issues: list[NormalizationIssue],
) -> FlightSegment | None:
    provenance = (_provenance_ref(mapped.provenance),)
    carrier = mapped.marketing_carrier.strip().upper()
    if carrier not in context.reference_data.carriers:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "marketing_carrier",
                NormalizationIssueCategory.UNRESOLVABLE_REFERENCE,
                "Carrier is not in deterministic reference data",
            )
        )
        return None
    departure_airport = mapped.departure_airport.strip().upper()
    arrival_airport = mapped.arrival_airport.strip().upper()
    if departure_airport not in context.reference_data.airports or arrival_airport not in context.reference_data.airports:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "airport",
                NormalizationIssueCategory.UNRESOLVABLE_REFERENCE,
                "Airport is not in deterministic reference data",
            )
        )
        return None
    try:
        return FlightSegment(
            segment_id=SegmentId(f"normalized-segment:{mapped.mapped_segment_ref.value}"),
            marketing_carrier=carrier,
            flight_number=mapped.flight_number.strip().upper(),
            departure_airport=departure_airport,
            arrival_airport=arrival_airport,
            departure_at=_domain_instant(mapped.departure_local),
            arrival_at=_domain_instant(mapped.arrival_local),
            operating_carrier=mapped.operating_carrier,
            aircraft_type=mapped.aircraft_type,
            provenance=provenance,
        )
    except (ValueError, DomainInvariantViolation) as exc:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "segment",
                NormalizationIssueCategory.CANONICAL_INVARIANT_VIOLATION,
                str(exc),
            )
        )
        return None


def _normalize_itinerary(
    mapped: MappedItinerary,
    segment_by_ref: dict[MappedSegmentRef, FlightSegment],
    issues: list[NormalizationIssue],
) -> Itinerary | None:
    missing = [ref for ref in mapped.segment_refs if ref not in segment_by_ref]
    if len(missing) > 0:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "segment_refs",
                NormalizationIssueCategory.ORPHAN_RECORD,
                "Itinerary references a dropped segment",
            )
        )
        return None
    return Itinerary(
        itinerary_id=ItineraryId(f"normalized-itinerary:{mapped.mapped_itinerary_ref.value}"),
        segment_ids=tuple(segment_by_ref[ref].segment_id for ref in mapped.segment_refs),
        provenance=(_provenance_ref(mapped.provenance),),
    )


def _normalize_offer(
    mapped: MappedOffer,
    itinerary_by_ref: dict[MappedItineraryRef, Itinerary],
    issues: list[NormalizationIssue],
) -> Offer | None:
    itinerary = itinerary_by_ref.get(mapped.itinerary_ref)
    if itinerary is None:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "itinerary_ref",
                NormalizationIssueCategory.ORPHAN_RECORD,
                "Offer references a dropped itinerary",
            )
        )
        return None
    if not mapped.total_amount.is_known or not mapped.currency.is_known:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "price",
                NormalizationIssueCategory.IDENTITY_CRITICAL_MISSING,
                "Offer price is required for canonical Offer",
            )
        )
        return None
    try:
        return Offer(
            offer_id=OfferId(f"normalized-offer:{mapped.mapped_offer_ref.value}"),
            itinerary_id=itinerary.itinerary_id,
            total_price=Money(Decimal(mapped.total_amount.value), mapped.currency.value),
            offer_freshness=OfferFreshness(FreshnessState.FRESH),
            booking_reference=mapped.booking_reference,
            provenance=(_provenance_ref(mapped.provenance),),
            price_semantics=mapped.price_semantics,
        )
    except DomainInvariantViolation as exc:
        issues.append(
            _normalization_issue(
                mapped.provenance,
                "offer",
                NormalizationIssueCategory.CANONICAL_INVARIANT_VIOLATION,
                str(exc),
            )
        )
        return None


def _normalization_data_status(
    mapping_result: ProviderMappingResult,
    segments: list[FlightSegment],
    itineraries: list[Itinerary],
    offers: list[Offer],
    issues: list[NormalizationIssue],
) -> ProviderDataStatus:
    if mapping_result.data_status is ProviderDataStatus.EMPTY:
        return ProviderDataStatus.EMPTY
    if len(segments) == 0 and len(itineraries) == 0 and len(offers) == 0 and len(issues) > 0:
        return ProviderDataStatus.UNUSABLE
    if len(issues) > 0:
        return ProviderDataStatus.PARTIAL
    return mapping_result.data_status


def _merged_data_status(results: list[NormalizationResult]) -> ProviderDataStatus:
    statuses = {result.data_status for result in results}
    if ProviderDataStatus.COMPLETE in statuses and len(statuses) == 1:
        return ProviderDataStatus.COMPLETE
    if any(status in statuses for status in {ProviderDataStatus.COMPLETE, ProviderDataStatus.PARTIAL}):
        return ProviderDataStatus.PARTIAL
    if statuses == {ProviderDataStatus.EMPTY}:
        return ProviderDataStatus.EMPTY
    if ProviderDataStatus.UNUSABLE in statuses:
        return ProviderDataStatus.UNUSABLE
    return ProviderDataStatus.UNKNOWN


def _segment_identity_key(segment: FlightSegment) -> tuple[str, str, str, str, datetime, datetime] | None:
    if segment.operating_carrier.state is ValueState.UNKNOWN:
        return None
    return (
        segment.marketing_carrier,
        segment.flight_number,
        segment.departure_airport,
        segment.arrival_airport,
        segment.departure_at.value,
        segment.arrival_at.value,
    )


def _merge_segment_projection(
    left: FlightSegment,
    right: FlightSegment,
    evidence: list[MergeEvidence],
) -> FlightSegment:
    aircraft_type = _merge_domain_value(
        left.aircraft_type,
        right.aircraft_type,
        left.provenance + right.provenance,
        evidence,
        "aircraft_type",
    )
    operating_carrier = _merge_domain_value(
        left.operating_carrier,
        right.operating_carrier,
        left.provenance + right.provenance,
        evidence,
        "operating_carrier",
    )
    return FlightSegment(
        segment_id=left.segment_id,
        marketing_carrier=left.marketing_carrier,
        flight_number=left.flight_number,
        departure_airport=left.departure_airport,
        arrival_airport=left.arrival_airport,
        departure_at=left.departure_at,
        arrival_at=left.arrival_at,
        operating_carrier=operating_carrier,
        aircraft_type=aircraft_type,
        provenance=_union_provenance(left.provenance, right.provenance),
    )


def _merge_domain_value[T](
    left: DomainValue[T],
    right: DomainValue[T],
    provenance: tuple[ProvenanceRef, ...],
    evidence: list[MergeEvidence],
    field_name: str,
) -> DomainValue[T]:
    if left.is_known and right.is_known:
        if left.value == right.value:
            return left
        evidence.append(
            _merge_evidence(
                MergeEvidenceCategory.ATTRIBUTE_CONFLICT,
                EquivalenceDecision.EQUIVALENT,
                tuple(ref.source_ref for ref in provenance),
                f"Conflicting known {field_name}",
                provenance,
            )
        )
        return left
    if left.is_known:
        return left
    if right.is_known:
        return right
    return left


def _provenance_ref(provenance: MappedProvenance) -> ProvenanceRef:
    return ProvenanceRef(
        source_type="provider_raw_record",
        source_ref=f"{provenance.provider_id.value}:{provenance.acquisition_id.value}",
        detail_ref=provenance.raw_record_ref,
    )


def _provider_source_ref(ref: ProvenanceRef) -> str:
    return f"{ref.source_ref}:{ref.detail_ref or ''}"


def _normalization_issue(
    provenance: MappedProvenance,
    path: str,
    category: NormalizationIssueCategory,
    detail: str,
) -> NormalizationIssue:
    return NormalizationIssue(
        source_ref=f"{provenance.provider_id.value}:{provenance.acquisition_id.value}",
        path=path,
        category=category,
        detail=detail,
        provenance=(_provenance_ref(provenance),),
    )


def _domain_instant(value: str) -> DomainInstant:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return DomainInstant(parsed)


def _with_segment_id(segment: FlightSegment, segment_id: SegmentId) -> FlightSegment:
    return FlightSegment(
        segment_id=segment_id,
        marketing_carrier=segment.marketing_carrier,
        flight_number=segment.flight_number,
        departure_airport=segment.departure_airport,
        arrival_airport=segment.arrival_airport,
        departure_at=segment.departure_at,
        arrival_at=segment.arrival_at,
        operating_carrier=segment.operating_carrier,
        aircraft_type=segment.aircraft_type,
        provenance=segment.provenance,
    )


def _with_offer_id(offer: Offer, offer_id: OfferId) -> Offer:
    return Offer(
        offer_id=offer_id,
        itinerary_id=offer.itinerary_id,
        total_price=offer.total_price,
        offer_freshness=offer.offer_freshness,
        booking_reference=offer.booking_reference,
        provenance=offer.provenance,
        price_semantics=offer.price_semantics,
    )


def _merge_evidence(
    category: MergeEvidenceCategory,
    decision: EquivalenceDecision,
    source_ids: tuple[str, ...],
    detail: str,
    provenance: tuple[ProvenanceRef, ...],
) -> MergeEvidence:
    return MergeEvidence(
        category=category,
        decision=decision,
        source_ids=source_ids,
        detail=detail,
        provenance=_union_provenance(provenance, ()),
    )


def _union_provenance(
    left: tuple[ProvenanceRef, ...],
    right: tuple[ProvenanceRef, ...],
) -> tuple[ProvenanceRef, ...]:
    return tuple(
        sorted(
            set(left + right),
            key=lambda ref: (ref.source_type, ref.source_ref, ref.detail_ref or ""),
        )
    )
