from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.mock import (
    MOCK_PROVIDER_MAPPER_VERSION,
    MockFlightProvider,
    MockProviderMapper,
)
from flight_agent.domain.flights import CandidateSnapshot, ItineraryId, OfferId, PriceSemantics, SegmentId
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import RequirementVersion, ValueState
from flight_agent.ports import (
    CoverageCompleteness,
    MappedItineraryRef,
    MappedOfferRef,
    MappedSegmentRef,
    MapperVersion,
    MappingIssueCategory,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderCoverageLimitation,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderMappingResult,
    ProviderRawEvidence,
    ProviderSearchResult,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"


def search_plan(origin: str = "XMN") -> SearchPlan:
    return SearchPlan(
        search_plan_id=SearchPlanId(f"search-plan-{origin.lower()}"),
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(5),
        requested_scope=RequestedSearchScope(
            origin=OriginScope(AirportCode(origin)),
            destination=DestinationScope(AirportCode("LAX")),
            departure_date=DepartureDateScope(LocalDate(date(2026, 9, 1))),
        ),
    )


def provider_result(origin: str = "XMN") -> ProviderSearchResult:
    return MockFlightProvider(FIXTURE_PATH).search(search_plan(origin))


def mapping_result(origin: str = "XMN") -> ProviderMappingResult:
    return MockProviderMapper().map(provider_result(origin))


def test_valid_provider_graph_maps_to_immutable_mapped_intermediates() -> None:
    result = mapping_result()

    assert result.mapper_version == MOCK_PROVIDER_MAPPER_VERSION
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert len(result.offers) == 1

    segment = result.segments[0]
    itinerary = result.itineraries[0]
    offer = result.offers[0]
    assert segment.mapped_segment_ref == MappedSegmentRef("mapped-segment:mock-seg-good")
    assert segment.provider_segment_id == "mock-seg-good"
    assert segment.marketing_carrier == "MU"
    assert segment.flight_number == "MU583"
    assert segment.departure_airport == "XMN"
    assert segment.arrival_airport == "LAX"
    assert itinerary.segment_refs == (segment.mapped_segment_ref,)
    assert offer.itinerary_ref == itinerary.mapped_itinerary_ref
    assert not isinstance(segment.mapped_segment_ref, SegmentId)
    assert not isinstance(itinerary.mapped_itinerary_ref, ItineraryId)
    assert not isinstance(offer.mapped_offer_ref, OfferId)
    assert offer.price_semantics is PriceSemantics.EXACT
    assert not isinstance(result, CandidateSnapshot)


def test_provider_specific_missing_and_falsy_values_are_interpreted_by_schema() -> None:
    segment = mapping_result().segments[0]
    offer = mapping_result().offers[0]

    assert segment.operating_carrier.state is ValueState.NOT_PROVIDED
    assert segment.aircraft_type.state is ValueState.UNKNOWN
    assert segment.checked_baggage_pieces.value == 0
    assert segment.overnight.value is False
    assert offer.total_amount.value == 0
    assert offer.refundable.value is False
    assert offer.booking_reference.state is ValueState.NOT_APPLICABLE


def test_optional_malformed_field_retains_trustworthy_object_with_issue() -> None:
    result = mapping_result()

    assert len(result.segments) == 1
    assert any(
        issue.category is MappingIssueCategory.MALFORMED_OPTIONAL_FIELD
        and issue.raw_record_ref == "segment:mock-seg-good"
        and issue.raw_path == "aircraft_type"
        for issue in result.issues
    )


def test_graph_integrity_drops_invalid_dependent_subgraphs_conservatively() -> None:
    result = mapping_result()

    assert [segment.provider_segment_id for segment in result.segments] == ["mock-seg-good"]
    assert [itinerary.provider_itinerary_id for itinerary in result.itineraries] == ["mock-itin-good"]
    assert [offer.provider_offer_id for offer in result.offers] == ["mock-offer-good"]
    assert result.statistics.raw_segment_count == 2
    assert result.statistics.dropped_segment_count == 1
    assert result.statistics.raw_itinerary_count == 3
    assert result.statistics.dropped_itinerary_count == 2
    assert result.statistics.raw_offer_count == 3
    assert result.statistics.dropped_offer_count == 2
    assert any(
        issue.category is MappingIssueCategory.BROKEN_GRAPH_REFERENCE
        and issue.raw_record_ref == "itinerary:mock-itin-broken-segment"
        for issue in result.issues
    )
    assert any(
        issue.category is MappingIssueCategory.ORPHAN_RECORD
        and issue.raw_record_ref == "offer:mock-offer-orphan"
        for issue in result.issues
    )


def test_offer_only_failure_preserves_valid_segment_and_itinerary() -> None:
    result = mapping_result()

    assert result.statistics.dropped_offer_count == 2
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert len(result.offers) == 1


def test_mapping_partial_failure_does_not_become_acquisition_failure_or_empty() -> None:
    acquisition = provider_result()
    result = MockProviderMapper().map(acquisition)

    assert acquisition.execution_status is ProviderExecutionStatus.SUCCESS
    assert acquisition.data_status is ProviderDataStatus.COMPLETE
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert result.data_status is not ProviderDataStatus.EMPTY
    assert acquisition.execution_status is not ProviderExecutionStatus.INVALID_RESPONSE
    assert result.statistics.issue_count > 0


def test_data_partial_can_coexist_with_complete_and_partial_coverage() -> None:
    mapper = MockProviderMapper()
    complete_coverage_acquisition = provider_result()
    partial_coverage_acquisition = ProviderSearchResult(
        provider_id=complete_coverage_acquisition.provider_id,
        acquisition_id=complete_coverage_acquisition.acquisition_id,
        search_plan_id=complete_coverage_acquisition.search_plan_id,
        requirement_id=complete_coverage_acquisition.requirement_id,
        based_on_requirement_version=complete_coverage_acquisition.based_on_requirement_version,
        execution_status=complete_coverage_acquisition.execution_status,
        data_status=complete_coverage_acquisition.data_status,
        coverage=ProviderCoverage(
            requested_scope=complete_coverage_acquisition.coverage.requested_scope,
            actual_scope=complete_coverage_acquisition.coverage.actual_scope,
            completeness=CoverageCompleteness.PARTIAL,
            limitations=(
                ProviderCoverageLimitation(
                    code="PROVIDER_SCOPE_LIMIT",
                    detail="Mapper test keeps coverage separate from data status",
                ),
            ),
        ),
        raw_evidence=complete_coverage_acquisition.raw_evidence,
    )

    assert complete_coverage_acquisition.coverage.completeness is CoverageCompleteness.COMPLETE
    assert mapper.map(complete_coverage_acquisition).data_status is ProviderDataStatus.PARTIAL
    assert partial_coverage_acquisition.coverage.completeness is CoverageCompleteness.PARTIAL
    assert mapper.map(partial_coverage_acquisition).data_status is ProviderDataStatus.PARTIAL


def test_processing_caused_zero_mapped_data_is_not_legitimate_provider_empty() -> None:
    raw = ProviderRawEvidence(
        provider_id=ProviderId("mock-flight-provider"),
        acquisition_id=ProviderAcquisitionId("all-bad-acquisition"),
        search_plan_id=SearchPlanId("all-bad-plan"),
        retrieved_at=datetime(2026, 8, 24, 1, 5, tzinfo=UTC),
        source_refs=("all-bad-response",),
        payload={
            "provider_segments": [
                {
                    "provider_segment_id": "bad-seg",
                    "carrier": "MU",
                    "flight_number": "MU000",
                }
            ],
            "provider_itineraries": [
                {"provider_itinerary_id": "bad-itin", "segment_refs": ["bad-seg"]}
            ],
            "provider_offers": [
                {
                    "provider_offer_id": "bad-offer",
                    "provider_itinerary_id": "bad-itin",
                    "amount": 10,
                    "currency": "CNY",
                }
            ],
        },
    )
    acquisition = ProviderSearchResult(
        provider_id=raw.provider_id,
        acquisition_id=raw.acquisition_id,
        search_plan_id=raw.search_plan_id,
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(5),
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.COMPLETE,
        coverage=ProviderCoverage(
            requested_scope=search_plan().requested_scope,
            actual_scope=search_plan().requested_scope,
            completeness=CoverageCompleteness.COMPLETE,
        ),
        raw_evidence=raw,
    )

    result = MockProviderMapper().map(acquisition)

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.data_status is not ProviderDataStatus.EMPTY


def test_mapped_artifacts_and_issues_keep_raw_to_mapped_provenance_without_raw_copy() -> None:
    result = mapping_result()
    segment = result.segments[0]
    issue = result.issues[0]

    assert segment.provenance.provider_id == ProviderId("mock-flight-provider")
    assert segment.provenance.acquisition_id == ProviderAcquisitionId(
        "mock-acquisition-mapper-edge-cases"
    )
    assert segment.provenance.raw_evidence_refs == ("mapper-edge-response",)
    assert segment.provenance.raw_record_ref == "segment:mock-seg-good"
    assert segment.provenance.provider_source_id == "mock-seg-good"
    assert issue.provider_id == segment.provenance.provider_id
    assert issue.acquisition_id == segment.provenance.acquisition_id
    assert not hasattr(segment.provenance, "payload")


def test_mapper_version_replay_is_deterministic_and_non_destructive() -> None:
    acquisition = provider_result()
    assert acquisition.raw_evidence is not None
    raw_before = acquisition.raw_evidence

    mapper_v1 = MockProviderMapper(MapperVersion("mock-provider-mapper-v1"))
    first = mapper_v1.map(acquisition)
    second = mapper_v1.map(acquisition)
    mapper_v2 = MockProviderMapper(MapperVersion("mock-provider-mapper-v2"))
    replay = mapper_v2.map(acquisition)

    assert first == second
    assert first.mapper_version == MapperVersion("mock-provider-mapper-v1")
    assert replay.mapper_version == MapperVersion("mock-provider-mapper-v2")
    assert replay.mapper_version != MapperVersion("m4-u2-v1")
    assert acquisition.raw_evidence == raw_before
    assert first.segments[0].mapped_segment_ref == replay.segments[0].mapped_segment_ref
    assert "mock-provider-mapper-v1" not in first.segments[0].mapped_segment_ref.value
