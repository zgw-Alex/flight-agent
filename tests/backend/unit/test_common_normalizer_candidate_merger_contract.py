from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    Coverage,
    CoverageStatus,
    ItineraryId,
    Money,
    OfferId,
    PriceSemantics,
    SegmentId,
)
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import (
    DomainInstant,
    DomainValue,
    FreshnessState,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
    ValueState,
)
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    EquivalenceDecision,
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    MergeEvidenceCategory,
    MergedCandidateGraph,
    MergerVersion,
    NormalizationContext,
    NormalizationIssueCategory,
    NormalizationResult,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderDataStatus,
    ProviderId,
    ProviderMappingResult,
    ReferenceData,
    ReferenceDataVersion,
)
from flight_agent.ports.provider_mapping import MappingStatistics


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"


def context() -> NormalizationContext:
    return NormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("reference-data-v1"),
            airports=frozenset({"PVG", "SHA", "XMN", "LAX"}),
            carriers=frozenset({"MU", "DL"}),
        ),
    )


def provenance(
    source: str,
    *,
    provider: str = "provider-a",
    acquisition: str = "acquisition-a",
) -> MappedProvenance:
    return MappedProvenance(
        provider_id=ProviderId(provider),
        acquisition_id=ProviderAcquisitionId(acquisition),
        raw_evidence_refs=(f"{source}-response",),
        raw_record_ref=source,
        provider_source_id=source,
    )


def mapped_segment(
    source: str,
    *,
    provider: str = "provider-a",
    acquisition: str = "acquisition-a",
    departure_airport: str = "PVG",
    arrival_airport: str = "LAX",
    departure_local: str = "2026-09-01T08:00:00+00:00",
    arrival_local: str = "2026-09-01T20:00:00+00:00",
    flight_number: str = "MU583",
    operating_carrier: DomainValue[str] | None = None,
    aircraft_type: DomainValue[str] | None = None,
) -> MappedSegment:
    return MappedSegment(
        mapped_segment_ref=MappedSegmentRef(f"mapped-segment:{source}"),
        provider_segment_id=source,
        marketing_carrier="MU",
        flight_number=flight_number,
        departure_airport=departure_airport,
        arrival_airport=arrival_airport,
        departure_local=departure_local,
        arrival_local=arrival_local,
        operating_carrier=operating_carrier or DomainValue.known("MU"),
        aircraft_type=aircraft_type or DomainValue.not_provided(),
        checked_baggage_pieces=DomainValue.not_provided(),
        overnight=DomainValue.not_provided(),
        provenance=provenance(source, provider=provider, acquisition=acquisition),
    )


def mapped_itinerary(
    source: str,
    segment_sources: tuple[str, ...],
    *,
    provider: str = "provider-a",
    acquisition: str = "acquisition-a",
) -> MappedItinerary:
    return MappedItinerary(
        mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{source}"),
        provider_itinerary_id=source,
        segment_refs=tuple(MappedSegmentRef(f"mapped-segment:{item}") for item in segment_sources),
        provenance=provenance(source, provider=provider, acquisition=acquisition),
    )


def mapped_offer(
    source: str,
    itinerary_source: str,
    *,
    provider: str = "provider-a",
    acquisition: str = "acquisition-a",
    amount: int = 1000,
    price_semantics: PriceSemantics = PriceSemantics.EXACT,
) -> MappedOffer:
    return MappedOffer(
        mapped_offer_ref=MappedOfferRef(f"mapped-offer:{source}"),
        provider_offer_id=source,
        itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{itinerary_source}"),
        total_amount=DomainValue.known(amount),
        currency=DomainValue.known("CNY"),
        refundable=DomainValue.not_provided(),
        booking_reference=DomainValue.not_applicable(),
        provenance=provenance(source, provider=provider, acquisition=acquisition),
        price_semantics=price_semantics,
    )


def mapping_result(
    *,
    segments: tuple[MappedSegment, ...],
    itineraries: tuple[MappedItinerary, ...],
    offers: tuple[MappedOffer, ...],
    mapper_version: MapperVersion = MapperVersion("mapper-v1"),
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
) -> ProviderMappingResult:
    return ProviderMappingResult(
        provider_id=ProviderId("provider-a"),
        acquisition_id=ProviderAcquisitionId("acquisition-a"),
        search_plan_id=SearchPlanId("search-plan-a"),
        mapper_version=mapper_version,
        data_status=data_status,
        segments=segments,
        itineraries=itineraries,
        offers=offers,
        issues=(),
        statistics=MappingStatistics(
            raw_segment_count=len(segments),
            mapped_segment_count=len(segments),
            dropped_segment_count=0,
            raw_itinerary_count=len(itineraries),
            mapped_itinerary_count=len(itineraries),
            dropped_itinerary_count=0,
            raw_offer_count=len(offers),
            mapped_offer_count=len(offers),
            dropped_offer_count=0,
            issue_count=0,
        ),
    )


def normalize(result: ProviderMappingResult) -> NormalizationResult:
    return CommonNormalizer().normalize(result, context())


def valid_normalized_result() -> NormalizationResult:
    return normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a"),),
        )
    )


def test_mapped_offer_defaults_to_exact_price_semantics_for_legacy_construction() -> None:
    assert mapped_offer("offer-a", "itin-a").price_semantics is PriceSemantics.EXACT


def test_mapped_offer_accepts_explicit_price_semantics() -> None:
    exact = mapped_offer("offer-exact", "itin-a", price_semantics=PriceSemantics.EXACT)
    lower_bound = mapped_offer("offer-lower", "itin-a", price_semantics=PriceSemantics.LOWER_BOUND)

    assert exact.price_semantics is PriceSemantics.EXACT
    assert lower_bound.price_semantics is PriceSemantics.LOWER_BOUND


def test_normalizer_constructs_legal_canonical_graph_without_snapshot() -> None:
    result = valid_normalized_result()

    assert result.data_status is ProviderDataStatus.COMPLETE
    assert result.segments[0].departure_airport == "PVG"
    assert result.itineraries[0].segment_ids == (result.segments[0].segment_id,)
    assert result.offers[0].itinerary_id == result.itineraries[0].itinerary_id
    assert result.normalizer_version == NormalizerVersion("common-normalizer-v1")
    assert result.reference_data_version == ReferenceDataVersion("reference-data-v1")
    assert not isinstance(result, CandidateSnapshot)
    with pytest.raises(FrozenInstanceError):
        result.segments = ()  # type: ignore[misc]


def test_normalizer_preserves_exact_price_semantics_explicitly() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a", price_semantics=PriceSemantics.EXACT),),
        )
    )

    assert result.offers[0].price_semantics is PriceSemantics.EXACT


def test_normalizer_preserves_lower_bound_price_semantics_without_default_upgrade() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a", price_semantics=PriceSemantics.LOWER_BOUND),),
        )
    )

    assert result.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert result.offers[0].price_semantics is not PriceSemantics.EXACT


def test_normalizer_preserves_optional_missing_and_does_not_invent_reference_facts() -> None:
    result = valid_normalized_result()
    segment = result.segments[0]

    assert segment.aircraft_type.state is ValueState.NOT_PROVIDED
    assert segment.operating_carrier.value == "MU"
    assert "DL" not in segment.provenance[0].source_ref


def test_unresolvable_reference_and_required_segment_failure_cascade_graph() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-bad-airport", departure_airport="ZZZ"),),
            itineraries=(mapped_itinerary("itin-bad", ("seg-bad-airport",)),),
            offers=(mapped_offer("offer-bad", "itin-bad"),),
        )
    )

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.statistics.dropped_segment_count == 1
    assert result.statistics.dropped_itinerary_count == 1
    assert result.statistics.dropped_offer_count == 1
    assert any(
        issue.category is NormalizationIssueCategory.UNRESOLVABLE_REFERENCE
        for issue in result.issues
    )


def test_offer_only_normalization_failure_preserves_segment_and_itinerary() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-zero", "itin-a", amount=0),),
        )
    )

    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert any(
        issue.category is NormalizationIssueCategory.CANONICAL_INVARIANT_VIOLATION
        for issue in result.issues
    )


def test_missing_offer_price_behavior_remains_identity_critical_missing() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(
                MappedOffer(
                    mapped_offer_ref=MappedOfferRef("mapped-offer:missing-price"),
                    provider_offer_id="missing-price",
                    itinerary_ref=MappedItineraryRef("mapped-itinerary:itin-a"),
                    total_amount=DomainValue.not_provided(),
                    currency=DomainValue.known("CNY"),
                    refundable=DomainValue.not_provided(),
                    booking_reference=DomainValue.not_applicable(),
                    provenance=provenance("missing-price"),
                    price_semantics=PriceSemantics.LOWER_BOUND,
                ),
            ),
        )
    )

    assert result.offers == ()
    assert result.statistics.dropped_offer_count == 1
    assert any(
        issue.category is NormalizationIssueCategory.IDENTITY_CRITICAL_MISSING
        for issue in result.issues
    )
    assert Money(Decimal("1000"), "cny") == Money(Decimal("1000"), "CNY")


def test_normalizer_consumes_mapped_data_from_u3_without_provider_schema_branching() -> None:
    plan = SearchPlan(
        SearchPlanId("search-plan-xmn"),
        RequirementId("requirement-1"),
        RequirementVersion(5),
        RequestedSearchScope(
            OriginScope(AirportCode("XMN")),
            DestinationScope(AirportCode("LAX")),
            DepartureDateScope(LocalDate(date(2026, 9, 1))),
        ),
    )
    provider_result = MockFlightProvider(FIXTURE_PATH).search(plan)
    mapped = MockProviderMapper().map(provider_result)
    normalized = CommonNormalizer().normalize(mapped, context())

    assert normalized.mapper_version == mapped.mapper_version
    assert len(normalized.segments) == 1
    assert len(normalized.itineraries) == 1
    assert normalized.offers == ()


def test_segment_equivalence_is_conservative_three_state() -> None:
    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))
    equivalent = valid_normalized_result().segments[0]
    distinct = normalize(
        mapping_result(
            segments=(mapped_segment("seg-other", flight_number="MU999"),),
            itineraries=(mapped_itinerary("itin-other", ("seg-other",)),),
            offers=(),
        )
    ).segments[0]
    insufficient = normalize(
        mapping_result(
            segments=(mapped_segment("seg-unknown", operating_carrier=DomainValue.unknown()),),
            itineraries=(mapped_itinerary("itin-unknown", ("seg-unknown",)),),
            offers=(),
        )
    ).segments[0]

    assert merger.segment_equivalence(equivalent, equivalent) is EquivalenceDecision.EQUIVALENT
    assert merger.segment_equivalence(equivalent, distinct) is EquivalenceDecision.DISTINCT
    assert (
        merger.segment_equivalence(equivalent, insufficient)
        is EquivalenceDecision.INSUFFICIENT_EVIDENCE
    )


def test_itinerary_equivalence_uses_ordered_segment_sequence_not_route_only() -> None:
    result = normalize(
        mapping_result(
            segments=(
                mapped_segment("seg-first", departure_airport="PVG", arrival_airport="SHA"),
                mapped_segment("seg-second", departure_airport="SHA", arrival_airport="LAX"),
            ),
            itineraries=(
                mapped_itinerary("itin-forward", ("seg-first", "seg-second")),
                mapped_itinerary("itin-reversed", ("seg-second", "seg-first")),
            ),
            offers=(),
        )
    )
    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))
    segments_by_id = {segment.segment_id: segment for segment in result.segments}

    assert (
        merger.itinerary_equivalence(result.itineraries[0], result.itineraries[1], segments_by_id)
        is EquivalenceDecision.DISTINCT
    )


def test_merger_consolidates_segments_and_rewires_itinerary_with_provenance_union() -> None:
    left = normalize(
        mapping_result(
            segments=(mapped_segment("seg-left", provider="provider-a", acquisition="acq-a"),),
            itineraries=(mapped_itinerary("itin-left", ("seg-left",), provider="provider-a", acquisition="acq-a"),),
            offers=(mapped_offer("offer-left", "itin-left", provider="provider-a", acquisition="acq-a"),),
            mapper_version=MapperVersion("mapper-left"),
        )
    )
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-right", provider="provider-b", acquisition="acq-b"),),
            itineraries=(mapped_itinerary("itin-right", ("seg-right",), provider="provider-b", acquisition="acq-b"),),
            offers=(mapped_offer("offer-right", "itin-right", provider="provider-b", acquisition="acq-b"),),
            mapper_version=MapperVersion("mapper-right"),
        )
    )

    graph = CandidateMerger(MergerVersion("candidate-merger-v1")).merge((right, left))

    assert isinstance(graph, MergedCandidateGraph)
    assert len(graph.segments) == 1
    assert len(graph.itineraries) == 1
    assert graph.itineraries[0].segment_ids == (graph.segments[0].segment_id,)
    assert len(graph.segments[0].provenance) == 2
    assert graph.mapper_versions == (MapperVersion("mapper-left"), MapperVersion("mapper-right"))


def test_cross_provider_same_price_offers_remain_distinct() -> None:
    left = normalize(
        mapping_result(
            segments=(mapped_segment("seg-left", provider="provider-a", acquisition="acq-a"),),
            itineraries=(mapped_itinerary("itin-left", ("seg-left",), provider="provider-a", acquisition="acq-a"),),
            offers=(mapped_offer("offer-left", "itin-left", provider="provider-a", acquisition="acq-a"),),
        )
    )
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-right", provider="provider-b", acquisition="acq-b"),),
            itineraries=(mapped_itinerary("itin-right", ("seg-right",), provider="provider-b", acquisition="acq-b"),),
            offers=(mapped_offer("offer-right", "itin-right", provider="provider-b", acquisition="acq-b"),),
        )
    )

    graph = CandidateMerger(MergerVersion("candidate-merger-v1")).merge((left, right))

    assert len(graph.offers) == 2
    assert any(
        item.category is MergeEvidenceCategory.SOURCE_IDENTITY_CONFLICT
        for item in graph.evidence
    )


def test_same_provider_exact_duplicate_offer_can_collapse_with_source_identity() -> None:
    left = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-same", "itin-a"),),
        )
    )
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-same", "itin-a"),),
        )
    )

    graph = CandidateMerger(MergerVersion("candidate-merger-v1")).merge((right, left))

    assert len(graph.offers) == 1
    assert len(graph.offers[0].provenance) == 1


def test_same_provider_offer_with_different_price_semantics_is_distinct() -> None:
    left = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-same", "itin-a", price_semantics=PriceSemantics.EXACT),),
        )
    )
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-same", "itin-a", price_semantics=PriceSemantics.LOWER_BOUND),),
        )
    )
    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))

    assert merger.offer_equivalence(left.offers[0], right.offers[0]) is EquivalenceDecision.DISTINCT
    graph = merger.merge((right, left))
    assert len(graph.offers) == 2
    assert {offer.price_semantics for offer in graph.offers} == {
        PriceSemantics.EXACT,
        PriceSemantics.LOWER_BOUND,
    }


def test_lower_bound_offer_can_exist_in_candidate_snapshot_without_schema_change() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a", price_semantics=PriceSemantics.LOWER_BOUND),),
        )
    )
    snapshot = CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-lower-bound"),
        version=SnapshotVersion(1),
        created_at=DomainInstant(datetime(2026, 9, 1, 1, 0, tzinfo=UTC)),
        created_from_requirement_version=RequirementVersion(5),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage("PVG-LAX", "PVG-LAX", CoverageStatus.COMPLETE),
        segments=result.segments,
        itineraries=result.itineraries,
        offers=result.offers,
    )

    assert snapshot.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert [field.name for field in fields(CandidateSnapshot)] == [
        "snapshot_id",
        "version",
        "created_at",
        "created_from_requirement_version",
        "structural_freshness",
        "coverage",
        "segments",
        "itineraries",
        "offers",
        "parent_snapshot_id",
        "parent_snapshot_version",
        "provenance",
    ]


def test_optional_known_conflict_is_evidence_not_silent_overwrite() -> None:
    left = normalize(
        mapping_result(
            segments=(mapped_segment("seg-a", aircraft_type=DomainValue.known("AIRBUS")),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(),
        )
    )
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-b", aircraft_type=DomainValue.known("BOEING")),),
            itineraries=(mapped_itinerary("itin-b", ("seg-b",)),),
            offers=(),
        )
    )

    graph = CandidateMerger(MergerVersion("candidate-merger-v1")).merge((left, right))

    assert len(graph.segments) == 1
    assert any(
        item.category is MergeEvidenceCategory.ATTRIBUTE_CONFLICT
        for item in graph.evidence
    )


def test_replay_and_input_permutation_are_semantically_deterministic() -> None:
    left = valid_normalized_result()
    right = normalize(
        mapping_result(
            segments=(mapped_segment("seg-b", provider="provider-b", acquisition="acq-b"),),
            itineraries=(mapped_itinerary("itin-b", ("seg-b",), provider="provider-b", acquisition="acq-b"),),
            offers=(mapped_offer("offer-b", "itin-b", provider="provider-b", acquisition="acq-b"),),
            mapper_version=MapperVersion("mapper-b"),
        )
    )
    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))

    first = merger.merge((left, right))
    second = merger.merge((right, left))
    third = merger.merge((left, right))

    assert first == second
    assert first == third
    assert first.merger_version == MergerVersion("candidate-merger-v1")
    assert first.reference_data_versions == (ReferenceDataVersion("reference-data-v1"),)
    assert first.normalizer_versions == (NormalizerVersion("common-normalizer-v1"),)


def test_processing_loss_zero_remains_distinct_from_provider_empty() -> None:
    result = normalize(
        mapping_result(
            segments=(mapped_segment("seg-bad", departure_airport="ZZZ"),),
            itineraries=(mapped_itinerary("itin-bad", ("seg-bad",)),),
            offers=(mapped_offer("offer-bad", "itin-bad"),),
        )
    )

    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.data_status is not ProviderDataStatus.EMPTY
    assert result.statistics.issue_count > 0


def test_versions_do_not_participate_in_canonical_equivalence_identity() -> None:
    base = valid_normalized_result()
    changed_version = CommonNormalizer().normalize(
        mapping_result(
            segments=(mapped_segment("seg-a"),),
            itineraries=(mapped_itinerary("itin-a", ("seg-a",)),),
            offers=(mapped_offer("offer-a", "itin-a"),),
            mapper_version=MapperVersion("mapper-v2"),
        ),
        NormalizationContext(
            normalizer_version=NormalizerVersion("common-normalizer-v2"),
            reference_data=context().reference_data,
        ),
    )
    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))

    assert merger.segment_equivalence(base.segments[0], changed_version.segments[0])
    assert merger.segment_equivalence(base.segments[0], changed_version.segments[0]) is EquivalenceDecision.EQUIVALENT
    assert "common-normalizer-v2" not in changed_version.segments[0].segment_id.value
