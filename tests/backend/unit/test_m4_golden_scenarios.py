from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    FixtureSchemaVersion,
    SnapshotAssemblyInput,
    SnapshotAssemblyOutcome,
    SnapshotCreationStatus,
    build_processing_manifest,
)
from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, Money, Offer, OfferId
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
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
)
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    CoverageCompleteness,
    EquivalenceDecision,
    MappedItinerary,
    MappedItineraryRef,
    MappedOffer,
    MappedOfferRef,
    MappedProvenance,
    MappedSegment,
    MappedSegmentRef,
    MapperVersion,
    MappingStatistics,
    MergeEvidenceCategory,
    MergedCandidateGraph,
    MergerVersion,
    NormalizationContext,
    NormalizationIssueCategory,
    NormalizationResult,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderMappingResult,
    ProviderSearchResult,
    ReferenceData,
    ReferenceDataVersion,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"
ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")
FIXTURE_SCHEMA_VERSION = FixtureSchemaVersion("m4-u2-v1")
REFERENCE_DATA_VERSION = ReferenceDataVersion("m4-u6-reference-data-v1")


@dataclass(frozen=True)
class PipelineArtifacts:
    search_plan: SearchPlan
    provider_result: ProviderSearchResult
    mapping_result: ProviderMappingResult
    normalization_result: NormalizationResult
    merged_graph: MergedCandidateGraph
    outcome: SnapshotAssemblyOutcome


def test_gs01_complete_provider_path_creates_legal_canonical_snapshot() -> None:
    artifacts = run_fixture_pipeline("PVG")

    assert artifacts.provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert artifacts.provider_result.data_status is ProviderDataStatus.COMPLETE
    assert artifacts.provider_result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert artifacts.mapping_result.data_status is ProviderDataStatus.COMPLETE
    assert artifacts.normalization_result.data_status is ProviderDataStatus.COMPLETE
    assert artifacts.outcome.status is SnapshotCreationStatus.COMPLETE_SNAPSHOT
    assert artifacts.outcome.snapshot is not None
    assert len(artifacts.outcome.snapshot.segments) == 1
    assert len(artifacts.outcome.snapshot.itineraries) == 1
    assert len(artifacts.outcome.snapshot.offers) == 1
    assert artifacts.outcome.snapshot.created_from_requirement_version == RequirementVersion(6)
    assert artifacts.outcome.freshness_evidence.structural_observed_at == DomainInstant(
        datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    )
    assert artifacts.outcome.snapshot.structural_freshness.state is FreshnessState.FRESH
    assert any(ref.source_type == "provider_acquisition" for ref in artifacts.outcome.snapshot.provenance)
    assert not hasattr(artifacts.outcome.snapshot, "ranking")
    assert not hasattr(artifacts.outcome.snapshot, "recommendations")
    with pytest.raises(FrozenInstanceError):
        artifacts.outcome.snapshot.offers = ()  # type: ignore[misc]


def test_gs02_mapper_partial_path_creates_partial_snapshot_from_reliable_remainder() -> None:
    artifacts = run_fixture_pipeline("XMN")

    assert artifacts.provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert artifacts.provider_result.data_status is ProviderDataStatus.COMPLETE
    assert artifacts.provider_result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert artifacts.mapping_result.data_status is ProviderDataStatus.PARTIAL
    assert artifacts.mapping_result.statistics.dropped_itinerary_count > 0
    assert artifacts.normalization_result.data_status is ProviderDataStatus.PARTIAL
    assert artifacts.normalization_result.offers == ()
    assert artifacts.outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert artifacts.outcome.snapshot is not None
    assert len(artifacts.outcome.snapshot.segments) == 1
    assert len(artifacts.outcome.snapshot.itineraries) == 1
    assert artifacts.outcome.snapshot.offers == ()
    assert any(
        issue.code == NormalizationIssueCategory.CANONICAL_INVARIANT_VIOLATION.value
        for issue in artifacts.outcome.issues
    )


def test_gs03_legitimate_empty_provider_result_creates_empty_snapshot() -> None:
    artifacts = run_fixture_pipeline("PEK")

    assert artifacts.provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert artifacts.provider_result.data_status is ProviderDataStatus.EMPTY
    assert artifacts.provider_result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert artifacts.mapping_result.data_status is ProviderDataStatus.EMPTY
    assert artifacts.normalization_result.data_status is ProviderDataStatus.EMPTY
    assert artifacts.outcome.status is SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT
    assert artifacts.outcome.snapshot is not None
    assert artifacts.outcome.snapshot.segments == ()
    assert artifacts.outcome.snapshot.itineraries == ()
    assert artifacts.outcome.snapshot.offers == ()
    assert artifacts.outcome.snapshot.coverage.status.name == "COMPLETE"


@pytest.mark.parametrize(
    ("origin", "execution_status", "data_status"),
    [
        ("SHA", ProviderExecutionStatus.TIMEOUT, ProviderDataStatus.UNKNOWN),
        ("CAN", ProviderExecutionStatus.RATE_LIMITED, ProviderDataStatus.UNKNOWN),
        ("SZX", ProviderExecutionStatus.AUTH_ERROR, ProviderDataStatus.UNKNOWN),
        ("CTU", ProviderExecutionStatus.UPSTREAM_ERROR, ProviderDataStatus.UNKNOWN),
        ("HGH", ProviderExecutionStatus.INVALID_RESPONSE, ProviderDataStatus.UNUSABLE),
    ],
)
def test_gs04_provider_failures_never_become_empty_or_reused_snapshots(
    origin: str,
    execution_status: ProviderExecutionStatus,
    data_status: ProviderDataStatus,
) -> None:
    artifacts = run_fixture_pipeline(origin)

    assert artifacts.provider_result.execution_status is execution_status
    assert artifacts.provider_result.data_status is data_status
    assert artifacts.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert artifacts.outcome.snapshot is None
    assert not hasattr(artifacts.outcome, "historical_snapshot")
    assert not hasattr(artifacts.outcome, "reuse_policy")


def test_gs05_equivalent_candidate_paths_merge_without_ranking() -> None:
    graph = merge_custom_results(
        (
            normalized_custom_result("left", provider="provider-a", acquisition="acq-a"),
            normalized_custom_result("right", provider="provider-b", acquisition="acq-b"),
        )
    )

    assert len(graph.segments) == 1
    assert len(graph.itineraries) == 1
    assert len(graph.offers) == 2
    assert graph.itineraries[0].segment_ids == (graph.segments[0].segment_id,)
    assert len(graph.segments[0].provenance) == 2
    assert all(evidence.category is not MergeEvidenceCategory.MERGED_EQUIVALENT for evidence in graph.evidence)
    assert not hasattr(graph, "ranking")


def test_gs06_insufficient_offer_identity_keeps_ambiguous_duplicates_distinct() -> None:
    graph = merge_custom_results(
        (
            normalized_custom_result("left", provider="provider-a", acquisition="acq-a"),
            normalized_custom_result("right", provider="provider-b", acquisition="acq-b"),
        )
    )

    assert len(graph.offers) == 2
    assert any(
        evidence.category is MergeEvidenceCategory.SOURCE_IDENTITY_CONFLICT
        and evidence.decision is EquivalenceDecision.INSUFFICIENT_EVIDENCE
        for evidence in graph.evidence
    )


def test_gs07_snapshot_preserves_requirement_provider_processing_and_raw_record_lineage() -> None:
    artifacts = run_fixture_pipeline("PVG")
    snapshot = artifacts.outcome.snapshot

    assert snapshot is not None
    assert artifacts.provider_result.search_plan_id == artifacts.search_plan.search_plan_id
    assert artifacts.provider_result.requirement_id == RequirementId("requirement-m4-u6")
    assert artifacts.provider_result.based_on_requirement_version == RequirementVersion(6)
    assert artifacts.outcome.processing_manifest.fixture_schema_versions == (FIXTURE_SCHEMA_VERSION,)
    assert artifacts.outcome.processing_manifest.mapper_versions == ("mock-provider-mapper-v1",)
    assert artifacts.outcome.processing_manifest.normalizer_versions == ("common-normalizer-v1",)
    assert artifacts.outcome.processing_manifest.reference_data_versions == (REFERENCE_DATA_VERSION.value,)
    assert artifacts.outcome.processing_manifest.merger_version == "candidate-merger-v1"
    assert ProvenanceRef(
        "provider_acquisition",
        "mock-flight-provider:mock-acquisition-normal-success",
        observed_at=DomainInstant(datetime(2026, 8, 24, 1, 0, tzinfo=UTC)),
    ) in snapshot.provenance
    assert any(ref.detail_ref == "segment:mock-seg-100" for ref in snapshot.provenance)
    assert all(not hasattr(ref, "payload") for ref in snapshot.provenance)


def test_gs08_same_fixture_replay_is_deterministic() -> None:
    first = run_fixture_pipeline("PVG", snapshot_id="snapshot:replay")
    second = run_fixture_pipeline("PVG", snapshot_id="snapshot:replay")

    assert first.provider_result == second.provider_result
    assert first.mapping_result == second.mapping_result
    assert first.normalization_result == second.normalization_result
    assert first.merged_graph == second.merged_graph
    assert first.outcome == second.outcome


def test_gs09_merge_input_permutation_preserves_canonical_graph_semantics() -> None:
    left = normalized_custom_result("left", provider="provider-a", acquisition="acq-a")
    right = normalized_custom_result("right", provider="provider-b", acquisition="acq-b")

    first = merge_custom_results((left, right))
    second = merge_custom_results((right, left))

    assert first == second
    assert [segment.segment_id.value for segment in first.segments] == ["segment:0001"]
    assert [itinerary.itinerary_id.value for itinerary in first.itineraries] == ["itinerary:0001"]
    assert [offer.offer_id.value for offer in first.offers] == ["offer:0001", "offer:0002"]


def test_nc01_timeout_is_not_conflated_with_legitimate_empty() -> None:
    artifacts = run_fixture_pipeline("SHA")

    assert artifacts.provider_result.execution_status is ProviderExecutionStatus.TIMEOUT
    assert artifacts.provider_result.data_status is ProviderDataStatus.UNKNOWN
    assert artifacts.provider_result.data_status is not ProviderDataStatus.EMPTY
    assert artifacts.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert artifacts.outcome.status is not SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT


def test_nc02_invalid_response_and_mapping_partial_have_different_owners() -> None:
    invalid = run_fixture_pipeline("HGH")
    partial = run_fixture_pipeline("XMN")

    assert invalid.provider_result.execution_status is ProviderExecutionStatus.INVALID_RESPONSE
    assert invalid.mapping_result.issues == ()
    assert invalid.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert partial.provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert partial.mapping_result.data_status is ProviderDataStatus.PARTIAL
    assert partial.mapping_result.issues
    assert partial.outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT


def test_nc03_processing_zero_is_no_new_snapshot_not_provider_empty() -> None:
    artifacts = run_custom_raw_pipeline(
        {
            "provider_case_id": "processing-zero",
            "provider_segments": [
                {
                    "provider_segment_id": "seg-bad-airport",
                    "carrier": "MU",
                    "flight_number": "MU583",
                    "departure_airport": "ZZZ",
                    "arrival_airport": "LAX",
                    "depart_local": "2026-09-01T08:00:00",
                    "arrive_local": "2026-09-01T20:00:00",
                }
            ],
            "provider_itineraries": [
                {
                    "provider_itinerary_id": "itin-bad",
                    "segment_refs": ["seg-bad-airport"],
                }
            ],
            "provider_offers": [
                {
                    "provider_offer_id": "offer-bad",
                    "provider_itinerary_id": "itin-bad",
                    "amount": 1200,
                    "currency": "CNY",
                }
            ],
        }
    )

    assert artifacts.provider_result.data_status is ProviderDataStatus.COMPLETE
    assert artifacts.normalization_result.data_status is ProviderDataStatus.UNUSABLE
    assert artifacts.normalization_result.statistics.issue_count > 0
    assert artifacts.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert artifacts.outcome.status is not SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT


def test_nc04_partial_data_does_not_mean_failed_acquisition() -> None:
    artifacts = run_fixture_pipeline("XMN")

    assert artifacts.provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert artifacts.mapping_result.data_status is ProviderDataStatus.PARTIAL
    assert artifacts.outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT


def test_nc05_partial_coverage_and_partial_data_remain_separate_axes() -> None:
    partial_coverage = run_fixture_pipeline("NKG")
    partial_data = run_fixture_pipeline("XMN")

    assert partial_coverage.provider_result.data_status is ProviderDataStatus.COMPLETE
    assert partial_coverage.provider_result.coverage.completeness is CoverageCompleteness.PARTIAL
    assert partial_coverage.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert partial_data.provider_result.data_status is ProviderDataStatus.COMPLETE
    assert partial_data.provider_result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert partial_data.mapping_result.data_status is ProviderDataStatus.PARTIAL
    assert partial_data.outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT


def test_nc06_broken_snapshot_graph_is_rejected_not_silently_repaired() -> None:
    artifacts = run_fixture_pipeline("PVG")
    assert artifacts.outcome.snapshot is not None
    invalid_offer = Offer(
        OfferId("offer:orphan"),
        ItineraryId("itinerary:missing"),
        Money(Decimal("8800"), "CNY"),
        OfferFreshness(FreshnessState.FRESH),
        DomainValue.not_provided(),
        provenance=artifacts.outcome.snapshot.offers[0].provenance,
    )
    invalid_graph = MergedCandidateGraph(
        normalizer_versions=artifacts.merged_graph.normalizer_versions,
        reference_data_versions=artifacts.merged_graph.reference_data_versions,
        mapper_versions=artifacts.merged_graph.mapper_versions,
        merger_version=artifacts.merged_graph.merger_version,
        data_status=artifacts.merged_graph.data_status,
        segments=artifacts.merged_graph.segments,
        itineraries=artifacts.merged_graph.itineraries,
        offers=(invalid_offer,),
        evidence=artifacts.merged_graph.evidence,
        normalization_issues=artifacts.merged_graph.normalization_issues,
    )
    outcome = assemble(artifacts.search_plan, invalid_graph, (artifacts.provider_result,))

    assert outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert outcome.snapshot is None
    assert any(issue.code == "GRAPH_INVARIANT_FAILURE" for issue in outcome.issues)


def test_nc07_ambiguous_duplicate_offers_are_not_collapsed() -> None:
    graph = merge_custom_results(
        (
            normalized_custom_result("left", provider="provider-a", acquisition="acq-a"),
            normalized_custom_result("right", provider="provider-b", acquisition="acq-b"),
        )
    )

    assert len(graph.offers) == 2
    assert any(evidence.category is MergeEvidenceCategory.SOURCE_IDENTITY_CONFLICT for evidence in graph.evidence)


def test_nc08_no_new_snapshot_carries_no_historical_selection_policy() -> None:
    artifacts = run_fixture_pipeline("CTU")

    assert artifacts.outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert artifacts.outcome.snapshot is None
    assert not hasattr(artifacts.outcome, "historical_snapshot")
    assert not hasattr(artifacts.outcome, "selected_previous_snapshot_id")
    assert not hasattr(artifacts.outcome, "refresh_policy")


def run_fixture_pipeline(
    origin: str,
    *,
    snapshot_id: str | None = None,
) -> PipelineArtifacts:
    plan = search_plan(origin, search_plan_id=f"search-plan:{origin.lower()}")
    provider_result = MockFlightProvider(FIXTURE_PATH).search(plan)
    return run_pipeline_from_provider_result(
        plan,
        provider_result,
        snapshot_id=snapshot_id or f"snapshot:{origin.lower()}",
    )


def run_custom_raw_pipeline(payload: object) -> PipelineArtifacts:
    plan = search_plan("PVG", search_plan_id="search-plan:processing-zero")
    provider_id = ProviderId("mock-flight-provider")
    acquisition_id = ProviderAcquisitionId("mock-acquisition-processing-zero")
    provider_result = ProviderSearchResult.for_search_plan(
        provider_id=provider_id,
        acquisition_id=acquisition_id,
        search_plan=plan,
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.COMPLETE,
        coverage=ProviderCoverage(
            plan.requested_scope,
            actual_scope=plan.requested_scope,
            completeness=CoverageCompleteness.COMPLETE,
        ),
        raw_evidence=ProviderRawEvidence(
            provider_id,
            acquisition_id,
            plan.search_plan_id,
            datetime(2026, 8, 24, 2, 0, tzinfo=UTC),
            payload,
            ("processing-zero-response",),
        ),
    )
    return run_pipeline_from_provider_result(plan, provider_result, snapshot_id="snapshot:processing-zero")


def run_pipeline_from_provider_result(
    plan: SearchPlan,
    provider_result: ProviderSearchResult,
    *,
    snapshot_id: str,
) -> PipelineArtifacts:
    mapping_result = MockProviderMapper().map(provider_result)
    normalization_result = CommonNormalizer().normalize(mapping_result, normalization_context())
    merged_graph = CandidateMerger(MergerVersion("candidate-merger-v1")).merge((normalization_result,))
    outcome = assemble(plan, merged_graph, (provider_result,), snapshot_id=snapshot_id)
    return PipelineArtifacts(
        search_plan=plan,
        provider_result=provider_result,
        mapping_result=mapping_result,
        normalization_result=normalization_result,
        merged_graph=merged_graph,
        outcome=outcome,
    )


def assemble(
    plan: SearchPlan,
    merged_graph: MergedCandidateGraph,
    provider_results: tuple[ProviderSearchResult, ...],
    *,
    snapshot_id: str = "snapshot:aggregate",
) -> SnapshotAssemblyOutcome:
    return CandidateSnapshotAssembler(ASSEMBLER_VERSION).assemble(
        SnapshotAssemblyInput(
            search_plan=plan,
            merged_graph=merged_graph,
            provider_results=provider_results,
            snapshot_id=CandidateSnapshotId(snapshot_id),
            created_at=DomainInstant(datetime(2026, 8, 24, 3, 0, tzinfo=UTC)),
            processing_manifest=build_processing_manifest(
                fixture_schema_versions=(FIXTURE_SCHEMA_VERSION,),
                merged_graph=merged_graph,
                assembler_version=ASSEMBLER_VERSION,
            ),
        )
    )


def search_plan(origin: str, *, search_plan_id: str = "search-plan:m4-u6") -> SearchPlan:
    return SearchPlan(
        SearchPlanId(search_plan_id),
        RequirementId("requirement-m4-u6"),
        RequirementVersion(6),
        RequestedSearchScope(
            OriginScope(AirportCode(origin)),
            DestinationScope(AirportCode("LAX")),
            DepartureDateScope(LocalDate(date(2026, 9, 1))),
        ),
    )


def normalization_context() -> NormalizationContext:
    return NormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=REFERENCE_DATA_VERSION,
            airports=frozenset({"PVG", "PEK", "SHA", "CAN", "SZX", "CTU", "HGH", "NKG", "XMN", "LAX"}),
            carriers=frozenset({"MU", "DL"}),
        ),
    )


def merge_custom_results(results: tuple[NormalizationResult, ...]) -> MergedCandidateGraph:
    return CandidateMerger(MergerVersion("candidate-merger-v1")).merge(results)


def normalized_custom_result(source: str, *, provider: str, acquisition: str) -> NormalizationResult:
    return CommonNormalizer().normalize(
        ProviderMappingResult(
            provider_id=ProviderId(provider),
            acquisition_id=ProviderAcquisitionId(acquisition),
            search_plan_id=SearchPlanId("search-plan:custom"),
            mapper_version=MapperVersion(f"mapper:{source}"),
            data_status=ProviderDataStatus.COMPLETE,
            segments=(
                MappedSegment(
                    mapped_segment_ref=MappedSegmentRef(f"mapped-segment:{source}"),
                    provider_segment_id=f"seg-{source}",
                    marketing_carrier="MU",
                    flight_number="MU583",
                    departure_airport="PVG",
                    arrival_airport="LAX",
                    departure_local="2026-09-01T08:00:00+00:00",
                    arrival_local="2026-09-01T20:00:00+00:00",
                    operating_carrier=DomainValue.known("MU"),
                    aircraft_type=DomainValue.not_provided(),
                    checked_baggage_pieces=DomainValue.not_provided(),
                    overnight=DomainValue.not_provided(),
                    provenance=mapped_provenance(source, provider=provider, acquisition=acquisition),
                ),
            ),
            itineraries=(
                MappedItinerary(
                    mapped_itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{source}"),
                    provider_itinerary_id=f"itin-{source}",
                    segment_refs=(MappedSegmentRef(f"mapped-segment:{source}"),),
                    provenance=mapped_provenance(source, provider=provider, acquisition=acquisition),
                ),
            ),
            offers=(
                MappedOffer(
                    mapped_offer_ref=MappedOfferRef(f"mapped-offer:{source}"),
                    provider_offer_id=f"offer-{source}",
                    itinerary_ref=MappedItineraryRef(f"mapped-itinerary:{source}"),
                    total_amount=DomainValue.known(8800),
                    currency=DomainValue.known("CNY"),
                    refundable=DomainValue.not_provided(),
                    booking_reference=DomainValue.not_applicable(),
                    provenance=mapped_provenance(source, provider=provider, acquisition=acquisition),
                ),
            ),
            issues=(),
            statistics=MappingStatistics(
                raw_segment_count=1,
                mapped_segment_count=1,
                dropped_segment_count=0,
                raw_itinerary_count=1,
                mapped_itinerary_count=1,
                dropped_itinerary_count=0,
                raw_offer_count=1,
                mapped_offer_count=1,
                dropped_offer_count=0,
                issue_count=0,
            ),
        ),
        normalization_context(),
    )


def mapped_provenance(source: str, *, provider: str, acquisition: str) -> MappedProvenance:
    return MappedProvenance(
        provider_id=ProviderId(provider),
        acquisition_id=ProviderAcquisitionId(acquisition),
        raw_evidence_refs=(f"{source}-response",),
        raw_record_ref=f"record:{source}",
        provider_source_id=source,
    )
