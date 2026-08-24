from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    FixtureSchemaVersion,
    SnapshotAssemblyInput,
    SnapshotCreationStatus,
    build_processing_manifest,
)
from flight_agent.domain.flights import (
    CandidateSnapshotId,
    Itinerary,
    ItineraryId,
    Money,
    Offer,
    OfferId,
    SegmentId,
)
from flight_agent.domain.flights.entities import FlightSegment
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
    DomainInvariantViolation,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
)
from flight_agent.ports import (
    CoverageCompleteness,
    MergedCandidateGraph,
    MergerVersion,
    NormalizationIssue,
    NormalizationIssueCategory,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderCoverageLimitation,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
    ReferenceDataVersion,
    MapperVersion,
)


ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")


def test_complete_graph_assembles_immutable_snapshot_with_lineage_and_no_parent() -> None:
    merged_graph = graph()
    assembly_input = input_for(merged_graph=merged_graph)
    before = merged_graph

    outcome = assemble(assembly_input)

    assert outcome.status is SnapshotCreationStatus.COMPLETE_SNAPSHOT
    assert outcome.snapshot is not None
    assert outcome.snapshot.snapshot_id.value == "snapshot-1"
    assert outcome.snapshot.version.value == 1
    assert outcome.snapshot.parent_snapshot_id is None
    assert outcome.snapshot.created_from_requirement_version == RequirementVersion(3)
    assert outcome.snapshot.segments == merged_graph.segments
    assert outcome.snapshot.itineraries == merged_graph.itineraries
    assert outcome.snapshot.offers == merged_graph.offers
    assert outcome.snapshot.provenance
    assert merged_graph == before
    with pytest.raises(FrozenInstanceError):
        outcome.snapshot.offers = ()  # type: ignore[misc]


def test_requested_scope_and_actual_coverage_remain_distinct_and_not_candidate_count_based() -> None:
    partial_actual = scope(origin="SHA")
    provider_result = provider_search_result(
        data_status=ProviderDataStatus.COMPLETE,
        coverage=ProviderCoverage(
            requested_scope=scope(),
            actual_scope=partial_actual,
            completeness=CoverageCompleteness.PARTIAL,
            limitations=(ProviderCoverageLimitation("AIRPORT_SUBSET", "Provider searched SHA only"),),
        ),
    )

    outcome = assemble(input_for(provider_results=(provider_result,)))

    assert outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert outcome.snapshot is not None
    assert outcome.snapshot.coverage.requested_scope == "PVG-LAX 2026-09-01"
    assert outcome.snapshot.coverage.actual_coverage == "mock-provider:SHA-LAX 2026-09-01"
    assert outcome.snapshot.coverage.status.name == "PARTIAL"
    assert outcome.snapshot.offers


def test_data_partial_and_coverage_complete_create_partial_snapshot_with_provider_evidence() -> None:
    provider_result = provider_search_result(
        data_status=ProviderDataStatus.PARTIAL,
        coverage=complete_provider_coverage(),
    )
    merged_graph = graph(data_status=ProviderDataStatus.PARTIAL)

    outcome = assemble(input_for(merged_graph=merged_graph, provider_results=(provider_result,)))

    assert outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert outcome.snapshot is not None
    assert outcome.snapshot.coverage.status.name == "PARTIAL"
    assert outcome.provider_results == (provider_result,)
    assert any(issue.code == "PROVIDER_DATA_PARTIAL" for issue in outcome.issues)


def test_successful_provider_empty_with_complete_coverage_creates_legitimate_empty_snapshot() -> None:
    provider_result = provider_search_result(data_status=ProviderDataStatus.EMPTY)
    empty_graph = graph(
        data_status=ProviderDataStatus.EMPTY,
        segments=(),
        itineraries=(),
        offers=(),
    )

    outcome = assemble(input_for(merged_graph=empty_graph, provider_results=(provider_result,)))

    assert outcome.status is SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT
    assert outcome.snapshot is not None
    assert outcome.snapshot.segments == ()
    assert outcome.snapshot.itineraries == ()
    assert outcome.snapshot.offers == ()
    assert outcome.snapshot.coverage.status.name == "COMPLETE"


@pytest.mark.parametrize(
    "execution_status",
    [
        ProviderExecutionStatus.TIMEOUT,
        ProviderExecutionStatus.RATE_LIMITED,
        ProviderExecutionStatus.AUTH_ERROR,
        ProviderExecutionStatus.UPSTREAM_ERROR,
        ProviderExecutionStatus.INVALID_RESPONSE,
    ],
)
def test_provider_failures_with_no_usable_graph_create_no_new_snapshot(
    execution_status: ProviderExecutionStatus,
) -> None:
    provider_result = provider_search_result(
        execution_status=execution_status,
        data_status=ProviderDataStatus.UNKNOWN,
        coverage=ProviderCoverage(scope(), actual_scope=None, completeness=CoverageCompleteness.UNKNOWN),
        include_raw_evidence=False,
    )
    empty_graph = graph(data_status=ProviderDataStatus.UNKNOWN, segments=(), itineraries=(), offers=())

    outcome = assemble(input_for(merged_graph=empty_graph, provider_results=(provider_result,)))

    assert outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert outcome.snapshot is None
    assert not hasattr(outcome, "historical_snapshot")
    assert not hasattr(outcome, "reuse_policy")


def test_processing_caused_zero_is_not_legitimate_empty() -> None:
    provider_result = provider_search_result(data_status=ProviderDataStatus.COMPLETE)
    processing_issue = NormalizationIssue(
        source_ref="mock-provider:acquisition-1",
        path="offer",
        category=NormalizationIssueCategory.IDENTITY_CRITICAL_MISSING,
        detail="All offers dropped during processing",
        provenance=(raw_record_provenance("offer:bad"),),
    )
    empty_after_processing = graph(
        data_status=ProviderDataStatus.UNUSABLE,
        segments=(),
        itineraries=(),
        offers=(),
        normalization_issues=(processing_issue,),
    )

    outcome = assemble(input_for(merged_graph=empty_after_processing, provider_results=(provider_result,)))

    assert outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert outcome.snapshot is None
    assert any(issue.code == "IDENTITY_CRITICAL_MISSING" for issue in outcome.issues)


def test_graph_invariant_failure_returns_no_new_snapshot_not_partial() -> None:
    invalid_graph = graph(
        offers=(
            offer(offer_id=OfferId("offer:bad"), itinerary_id=ItineraryId("missing-itinerary")),
        ),
    )

    outcome = assemble(input_for(merged_graph=invalid_graph))

    assert outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert outcome.snapshot is None
    assert any(issue.code == "GRAPH_INVARIANT_FAILURE" for issue in outcome.issues)


def test_freshness_evidence_is_separate_from_snapshot_created_at_and_offer_freshness() -> None:
    created_at = instant(20)
    retrieved_at = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
    provider_result = provider_search_result(retrieved_at=retrieved_at)

    outcome = assemble(input_for(created_at=created_at, provider_results=(provider_result,)))

    assert outcome.snapshot is not None
    assert outcome.snapshot.created_at == created_at
    assert outcome.freshness_evidence.provider_retrieved_at == (DomainInstant(retrieved_at),)
    assert outcome.freshness_evidence.structural_observed_at == DomainInstant(retrieved_at)
    assert outcome.snapshot.structural_freshness.state is FreshnessState.FRESH
    assert {item.offer_freshness for item in outcome.snapshot.offers} == {
        OfferFreshness(FreshnessState.FRESH)
    }
    assert not hasattr(outcome.snapshot, "is_fresh")
    assert not hasattr(outcome.snapshot, "ttl")


def test_missing_freshness_evidence_remains_absent_and_is_not_created_at() -> None:
    provider_result = provider_search_result(include_raw_evidence=False)

    outcome = assemble(input_for(provider_results=(provider_result,)))

    assert outcome.snapshot is not None
    assert outcome.freshness_evidence.provider_retrieved_at == ()
    assert outcome.freshness_evidence.structural_observed_at is None
    assert outcome.snapshot.structural_freshness.state is FreshnessState.STALE
    assert outcome.snapshot.created_at == instant(10)


def test_processing_manifest_preserves_independent_versions_outside_flight_identity() -> None:
    outcome = assemble(input_for())

    assert outcome.processing_manifest.fixture_schema_versions == (
        FixtureSchemaVersion("mock-fixtures-v1"),
    )
    assert outcome.processing_manifest.mapper_versions == ("mapper-v1",)
    assert outcome.processing_manifest.normalizer_versions == ("normalizer-v1",)
    assert outcome.processing_manifest.reference_data_versions == ("reference-v1",)
    assert outcome.processing_manifest.merger_version == "merger-v1"
    assert outcome.processing_manifest.assembler_version == ASSEMBLER_VERSION
    assert outcome.snapshot is not None
    assert outcome.snapshot.segments[0].segment_id == SegmentId("segment:0001")


def test_provenance_traces_provider_acquisition_raw_record_and_merged_sources() -> None:
    outcome = assemble(input_for())

    assert outcome.snapshot is not None
    refs = outcome.snapshot.provenance
    assert ProvenanceRef(
        "provider_acquisition",
        "mock-provider:acquisition-1",
        observed_at=DomainInstant(datetime(2026, 9, 1, 5, 0, tzinfo=UTC)),
    ) in refs
    assert raw_record_provenance("segment:s1") in refs
    assert raw_record_provenance("offer:o1") in refs
    assert all(not hasattr(ref, "payload") for ref in refs)


def test_deterministic_replay_with_same_inputs_produces_equal_outcome() -> None:
    assembly_input = input_for()

    first = assemble(assembly_input)
    second = assemble(assembly_input)

    assert first == second


def test_another_assembly_creates_new_artifact_without_mutating_old_snapshot() -> None:
    first = assemble(input_for())
    second = assemble(input_for(snapshot_id_value="snapshot-2"))

    assert first.snapshot is not None
    assert second.snapshot is not None
    assert first.snapshot.snapshot_id.value == "snapshot-1"
    assert second.snapshot.snapshot_id.value == "snapshot-2"
    assert first.snapshot != second.snapshot


def test_multiple_provider_evidence_keeps_per_provider_and_aggregate_coverage() -> None:
    first = provider_search_result()
    second = provider_search_result(
        provider_id=ProviderId("second-provider"),
        acquisition_id=ProviderAcquisitionId("acquisition-2"),
        data_status=ProviderDataStatus.PARTIAL,
        coverage=ProviderCoverage(
            scope(),
            actual_scope=scope(origin="SHA"),
            completeness=CoverageCompleteness.PARTIAL,
            limitations=(ProviderCoverageLimitation("SECOND_PARTIAL", "Second provider partial"),),
        ),
    )

    outcome = assemble(input_for(provider_results=(first, second)))

    assert outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert outcome.provider_results == (first, second)
    assert outcome.snapshot is not None
    assert outcome.snapshot.coverage.status.name == "PARTIAL"
    assert "mock-provider:PVG-LAX 2026-09-01" in outcome.snapshot.coverage.actual_coverage
    assert "second-provider:SHA-LAX 2026-09-01" in outcome.snapshot.coverage.actual_coverage


def assemble(assembly_input: SnapshotAssemblyInput):
    return CandidateSnapshotAssembler(ASSEMBLER_VERSION).assemble(assembly_input)


def input_for(
    *,
    merged_graph: MergedCandidateGraph | None = None,
    provider_results: tuple[ProviderSearchResult, ...] | None = None,
    created_at: DomainInstant | None = None,
    snapshot_id_value: str = "snapshot-1",
) -> SnapshotAssemblyInput:
    graph_value = merged_graph or graph()
    return SnapshotAssemblyInput(
        search_plan=search_plan(),
        merged_graph=graph_value,
        provider_results=provider_results or (provider_search_result(),),
        snapshot_id=CandidateSnapshotId(snapshot_id_value),
        created_at=created_at or instant(10),
        processing_manifest=build_processing_manifest(
            fixture_schema_versions=(FixtureSchemaVersion("mock-fixtures-v1"),),
            merged_graph=graph_value,
            assembler_version=ASSEMBLER_VERSION,
        ),
    )


def graph(
    *,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
    segments: tuple[FlightSegment, ...] | None = None,
    itineraries: tuple[Itinerary, ...] | None = None,
    offers: tuple[Offer, ...] | None = None,
    normalization_issues: tuple[NormalizationIssue, ...] = (),
) -> MergedCandidateGraph:
    return MergedCandidateGraph(
        normalizer_versions=(NormalizerVersion("normalizer-v1"),),
        reference_data_versions=(ReferenceDataVersion("reference-v1"),),
        mapper_versions=(MapperVersion("mapper-v1"),),
        merger_version=MergerVersion("merger-v1"),
        data_status=data_status,
        segments=segments if segments is not None else (segment(),),
        itineraries=itineraries if itineraries is not None else (itinerary(),),
        offers=offers if offers is not None else (offer(),),
        evidence=(),
        normalization_issues=normalization_issues,
    )


def provider_search_result(
    *,
    provider_id: ProviderId = ProviderId("mock-provider"),
    acquisition_id: ProviderAcquisitionId = ProviderAcquisitionId("acquisition-1"),
    execution_status: ProviderExecutionStatus = ProviderExecutionStatus.SUCCESS,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
    coverage: ProviderCoverage | None = None,
    retrieved_at: datetime = datetime(2026, 9, 1, 5, 0, tzinfo=UTC),
    include_raw_evidence: bool = True,
    raw_evidence: ProviderRawEvidence | None = None,
) -> ProviderSearchResult:
    raw = raw_evidence or (
        ProviderRawEvidence(
            provider_id,
            acquisition_id,
            SearchPlanId("search-plan-1"),
            retrieved_at,
            {"provider_offers": [{"provider_offer_id": "o1"}]},
            ("raw-fixture-1",),
        )
        if include_raw_evidence
        else None
    )
    return ProviderSearchResult.for_search_plan(
        provider_id=provider_id,
        acquisition_id=acquisition_id,
        search_plan=search_plan(),
        execution_status=execution_status,
        data_status=data_status,
        coverage=coverage or complete_provider_coverage(),
        raw_evidence=raw,
    )


def complete_provider_coverage() -> ProviderCoverage:
    return ProviderCoverage(scope(), actual_scope=scope(), completeness=CoverageCompleteness.COMPLETE)


def search_plan() -> SearchPlan:
    return SearchPlan(
        SearchPlanId("search-plan-1"),
        RequirementId("requirement-1"),
        RequirementVersion(3),
        scope(),
    )


def scope(origin: str = "PVG", destination: str = "LAX") -> RequestedSearchScope:
    return RequestedSearchScope(
        OriginScope(AirportCode(origin)),
        DestinationScope(AirportCode(destination)),
        DepartureDateScope(LocalDate(date(2026, 9, 1))),
    )


def segment() -> FlightSegment:
    return FlightSegment(
        segment_id=SegmentId("segment:0001"),
        marketing_carrier="MU",
        flight_number="588",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(1),
        arrival_at=instant(12),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue[str].unknown(),
        provenance=(raw_record_provenance("segment:s1"),),
    )


def itinerary() -> Itinerary:
    return Itinerary(
        ItineraryId("itinerary:0001"),
        (SegmentId("segment:0001"),),
        provenance=(raw_record_provenance("itinerary:i1"),),
    )


def offer(
    *,
    offer_id: OfferId = OfferId("offer:0001"),
    itinerary_id: ItineraryId = ItineraryId("itinerary:0001"),
) -> Offer:
    return Offer(
        offer_id=offer_id,
        itinerary_id=itinerary_id,
        total_price=Money(Decimal("900"), "USD"),
        offer_freshness=OfferFreshness(FreshnessState.FRESH),
        booking_reference=DomainValue[str].not_provided(),
        provenance=(raw_record_provenance("offer:o1"),),
    )


def raw_record_provenance(detail_ref: str) -> ProvenanceRef:
    return ProvenanceRef(
        source_type="provider_raw_record",
        source_ref="mock-provider:acquisition-1",
        detail_ref=detail_ref,
    )


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 9, 1, hour, 0, tzinfo=UTC))
