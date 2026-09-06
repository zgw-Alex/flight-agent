from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from flight_agent.adapters.flight_providers.ctrip import (
    CtripCanonicalEntry,
    CtripProviderMapper,
)
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    ExecuteSingleProviderCandidateIntegration,
    FixtureSchemaVersion,
    SnapshotCreationStatus,
)
from flight_agent.domain.flights import Money, PriceSemantics
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion, ValueState
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    CoverageCompleteness,
    MergerVersion,
    NormalizationContext,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderCoverageLimitation,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
    ReferenceData,
    ReferenceDataVersion,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")


def test_level1_ctrip_segment_itinerary_reaches_snapshot_without_offer() -> None:
    result = execute_ctrip(provider_result(payload=payload(level2=())))

    assert result.snapshot_outcome.snapshot is not None
    assert len(result.snapshot_outcome.snapshot.segments) == 1
    assert len(result.snapshot_outcome.snapshot.itineraries) == 1
    assert result.mapping_result.offers == ()
    assert result.normalization_result.offers == ()
    assert result.snapshot_outcome.snapshot.offers == ()


def test_level2_supported_offer_reaches_snapshot_with_exact_semantics() -> None:
    result = execute_ctrip(
        provider_result(payload=payload(level1=(level1_itinerary(price_list=field("MISSING")),)))
    )

    assert result.snapshot_outcome.status is SnapshotCreationStatus.COMPLETE_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert len(result.snapshot_outcome.snapshot.offers) == 1
    assert result.snapshot_outcome.snapshot.offers[0].total_price == Money(Decimal(820), "CNY")
    assert result.snapshot_outcome.snapshot.offers[0].price_semantics is PriceSemantics.EXACT


def test_numeric_level1_adult_price_is_not_auto_upgraded_to_exact_offer() -> None:
    result = execute_ctrip(provider_result(payload=payload(level2=())))

    assert result.mapping_result.statistics.raw_offer_count == 1
    assert result.mapping_result.statistics.mapped_offer_count == 0
    assert result.normalization_result.offers == ()
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.offers == ()


def test_ctrip_integration_builds_single_provider_snapshot() -> None:
    result = execute_ctrip(provider_result(payload=payload()))

    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.provider_results == (result.provider_result,)
    assert result.snapshot_outcome.provider_results[0].provider_id == ProviderId("CTRIP")
    assert "CTRIP:" in result.snapshot_outcome.snapshot.coverage.actual_coverage


def test_coverage_and_freshness_are_preserved_by_existing_assembler() -> None:
    partial_coverage = ProviderCoverage(
        requested_scope=scope(),
        actual_scope=scope(destination="NKG"),
        completeness=CoverageCompleteness.PARTIAL,
        limitations=(ProviderCoverageLimitation("CTRIP_ROUTE_LIMIT", "CTRIP returned an adjacent market"),),
    )

    result = execute_ctrip(provider_result(payload=payload(), coverage=partial_coverage))

    assert result.snapshot_outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.coverage.requested_scope == "PEK-SHA 2026-09-14"
    assert result.snapshot_outcome.snapshot.coverage.actual_coverage == "CTRIP:PEK-NKG 2026-09-14"
    assert result.snapshot_outcome.freshness_evidence.provider_retrieved_at == (
        DomainInstant(datetime(2026, 9, 14, 1, 5, tzinfo=UTC)),
    )


def test_empty_provider_result_uses_legitimate_empty_snapshot_semantics() -> None:
    result = execute_ctrip(
        provider_result(payload=None, data_status=ProviderDataStatus.EMPTY, raw_evidence=False)
    )

    assert result.snapshot_outcome.status is SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.segments == ()
    assert result.snapshot_outcome.snapshot.itineraries == ()
    assert result.snapshot_outcome.snapshot.offers == ()


def test_partial_provider_result_keeps_partial_snapshot_semantics() -> None:
    result = execute_ctrip(
        provider_result(
            payload=payload(level2=()),
            data_status=ProviderDataStatus.PARTIAL,
            coverage=ProviderCoverage(
                requested_scope=scope(),
                actual_scope=scope(),
                completeness=CoverageCompleteness.PARTIAL,
                limitations=(ProviderCoverageLimitation("CTRIP_LIMITED_OFFERS", "Level-2 offer missing"),),
            ),
        )
    )

    assert result.snapshot_outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.coverage.status.name == "PARTIAL"
    assert any(issue.code == "PROVIDER_DATA_PARTIAL" for issue in result.snapshot_outcome.issues)


def test_provider_failure_is_not_encoded_as_empty_snapshot() -> None:
    result = execute_ctrip(
        provider_result(
            payload=None,
            execution_status=ProviderExecutionStatus.UPSTREAM_ERROR,
            data_status=ProviderDataStatus.UNKNOWN,
            coverage=ProviderCoverage(scope(), actual_scope=None, completeness=CoverageCompleteness.UNKNOWN),
            raw_evidence=False,
        )
    )

    assert result.snapshot_outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert result.snapshot_outcome.snapshot is None
    assert any(issue.code == "UPSTREAM_ERROR" for issue in result.snapshot_outcome.issues)


def test_provenance_and_processing_lineage_are_preserved() -> None:
    result = execute_ctrip(provider_result(payload=payload()))

    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.processing_manifest.mapper_versions == (
        "ctrip-evidence-to-mapped-mapper-v1",
    )
    assert result.snapshot_outcome.processing_manifest.normalizer_versions == ("common-normalizer-v1",)
    assert result.snapshot_outcome.processing_manifest.reference_data_versions == ("reference-data-v1",)
    refs = result.snapshot_outcome.snapshot.provenance
    assert any(ref.source_type == "provider_acquisition" and ref.source_ref == "CTRIP:ctrip-acq-1" for ref in refs)
    assert any(ref.detail_ref == "ctrip-level1:ctrip-itin-1" for ref in refs)
    assert any(ref.detail_ref == "ctrip-level2-offer:product-1" for ref in refs)


def test_missing_and_unknown_values_are_not_defaulted_by_integration() -> None:
    unknown_aircraft = {
        **level1_itinerary(price_list=field("MISSING")),
        "aircraft": field("UNKNOWN"),
    }
    offer_without_booking = {
        **level2_offer(),
        "booking_action_identity": field("MISSING"),
    }

    result = execute_ctrip(provider_result(payload=payload(level1=(unknown_aircraft,), level2=(offer_without_booking,))))

    assert result.snapshot_outcome.snapshot is not None
    segment = result.snapshot_outcome.snapshot.segments[0]
    offer = result.snapshot_outcome.snapshot.offers[0]
    assert segment.aircraft_type.state is ValueState.UNKNOWN
    assert segment.operating_carrier.state is ValueState.NOT_PROVIDED
    assert offer.booking_reference.state is ValueState.NOT_PROVIDED


def test_offer_provider_scope_is_preserved_without_cross_provider_merge() -> None:
    result = execute_ctrip(provider_result(payload=payload()))

    assert result.snapshot_outcome.snapshot is not None
    offer_refs = result.snapshot_outcome.snapshot.offers[0].provenance
    assert offer_refs[0].source_ref == "CTRIP:ctrip-acq-1"
    assert len(result.snapshot_outcome.provider_results) == 1
    assert "FLIGGY" not in result.snapshot_outcome.snapshot.coverage.actual_coverage


def test_no_ctrip_specific_dependency_is_added_to_shared_merger_or_snapshot_assembler() -> None:
    merger_source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "ports" / "candidate_normalization.py"
    ).read_text(encoding="utf-8")
    assembler_source = (
        REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "application" / "snapshot_assembly.py"
    ).read_text(encoding="utf-8")

    assert "CTRIP" not in merger_source
    assert "ctrip" not in merger_source
    assert "CTRIP" not in assembler_source
    assert "ctrip" not in assembler_source


def test_ctrip_candidate_integration_is_deterministic() -> None:
    provider = provider_result(payload=payload(level1=(level1_itinerary(price_list=field("MISSING")),)))

    first = execute_ctrip(provider)
    second = execute_ctrip(provider)

    assert first == second


def test_single_provider_integration_entry_has_no_multi_provider_or_live_browser_surface() -> None:
    source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "application"
        / "candidate_integration.py"
    ).read_text(encoding="utf-8")

    assert "ProviderSearchResult, ..." not in source
    assert "merge((canonical_result.normalization_result,))" in source
    assert "playwright" not in source
    assert "httpx" not in source
    assert "requests" not in source
    assert "Purchase" not in source


def execute_ctrip(provider: ProviderSearchResult):
    sequencer = iter(("snapshot-ctrip-1",))
    integration = ExecuteSingleProviderCandidateIntegration(
        provider_mapper=CtripProviderMapper(),
        canonical_entry=CtripCanonicalEntry(
            common_normalizer=CommonNormalizer(),
            normalization_context=NormalizationContext(
                normalizer_version=NormalizerVersion("common-normalizer-v1"),
                reference_data=ReferenceData(
                    version=ReferenceDataVersion("reference-data-v1"),
                    airports=frozenset({"PEK", "SHA", "NKG"}),
                    carriers=frozenset({"MU"}),
                ),
            ),
        ),
        candidate_merger=CandidateMerger(MergerVersion("candidate-merger-v1")),
        snapshot_assembler=CandidateSnapshotAssembler(ASSEMBLER_VERSION),
        assembler_version=ASSEMBLER_VERSION,
        fixture_schema_versions=(FixtureSchemaVersion("ctrip-sanitized-evidence-v1"),),
        id_factory=lambda: next(sequencer),
        created_at=lambda: DomainInstant(datetime(2026, 9, 14, 1, 10, tzinfo=UTC)),
    )
    return integration.execute(search_plan=search_plan(), provider_result=provider)


def provider_result(
    *,
    payload: dict[str, object] | None,
    execution_status: ProviderExecutionStatus = ProviderExecutionStatus.SUCCESS,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
    coverage: ProviderCoverage | None = None,
    raw_evidence: bool = True,
) -> ProviderSearchResult:
    raw = (
        ProviderRawEvidence(
            ProviderId("CTRIP"),
            ProviderAcquisitionId("ctrip-acq-1"),
            SearchPlanId("search-plan-ctrip-1"),
            datetime(2026, 9, 14, 1, 5, tzinfo=UTC),
            payload or {},
            ("assisted-capture:sanitized-local-json",),
        )
        if raw_evidence
        else None
    )
    return ProviderSearchResult.for_search_plan(
        provider_id=ProviderId("CTRIP"),
        acquisition_id=ProviderAcquisitionId("ctrip-acq-1"),
        search_plan=search_plan(),
        execution_status=execution_status,
        data_status=data_status,
        coverage=coverage or ProviderCoverage(scope(), actual_scope=scope(), completeness=CoverageCompleteness.COMPLETE),
        raw_evidence=raw,
    )


def search_plan() -> SearchPlan:
    return SearchPlan(
        SearchPlanId("search-plan-ctrip-1"),
        RequirementId("requirement-ctrip-1"),
        RequirementVersion(7),
        scope(),
    )


def scope(origin: str = "PEK", destination: str = "SHA") -> RequestedSearchScope:
    return RequestedSearchScope(
        OriginScope(AirportCode(origin)),
        DestinationScope(AirportCode(destination)),
        DepartureDateScope(LocalDate(date(2026, 9, 14))),
    )


def payload(
    *,
    level1: tuple[dict[str, object], ...] | None = None,
    level2: tuple[dict[str, object], ...] | None = None,
) -> dict[str, object]:
    return {
        "provider_identity": "CTRIP",
        "acquisition_strategy": "BROWSER_ASSISTED",
        "acquired_at": "2026-09-14T01:05:00+00:00",
        "level1_evidence": list((level1_itinerary(),) if level1 is None else level1),
        "level2_offer_evidence": list((level2_offer(),) if level2 is None else level2),
    }


def level1_itinerary(*, price_list: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "evidence_index": 1,
        "itinerary_id": field("OBSERVED", "ctrip-itin-1", "$.data.flightItineraryList[0].itineraryId"),
        "flight_no": field("OBSERVED", "MU5100", "$.flightSegments[0].flightList[0].flightNo"),
        "market_airline_code": field("OBSERVED", "MU", "$.marketAirlineCode"),
        "departure_airport": field("OBSERVED", "PEK", "$.departureAirportCode"),
        "arrival_airport": field("OBSERVED", "SHA", "$.arrivalAirportCode"),
        "departure_datetime": field("OBSERVED", "2026-09-14 07:00", "$.departureDateTime"),
        "arrival_datetime": field("OBSERVED", "2026-09-14 09:10", "$.arrivalDateTime"),
        "aircraft": field("OBSERVED", "Airbus 320", "$.aircraftName"),
        "price_list": price_list
        or field("OBSERVED", {"count": 1, "sample": {"adultPrice": 791}}, "$.priceList"),
    }


def level2_offer() -> dict[str, object]:
    return {
        "evidence_index": 1,
        "product_or_fare_identity": field("OBSERVED", "product-1", "$.products[0].productId"),
        "price": field("OBSERVED", 820, "$.products[0].adultPrice"),
        "booking_action_identity": field("OBSERVED", "booking-action-1", "$.products[0].bookingId"),
    }


def field(
    status: str,
    raw_value: object | None = None,
    evidence_path: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {"status": status}
    if raw_value is not None:
        value["raw_value"] = raw_value
    if evidence_path is not None:
        value["evidence_path"] = evidence_path
    return value
