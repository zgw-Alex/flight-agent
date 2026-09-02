from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.flight_providers.fliggy import (
    FLIGGY_PROVIDER_MAPPER_VERSION,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    DomTraversalAssessment,
    FieldEvidence,
    FliggyEvidenceMapper,
    FliggyFlightEvidence,
    ProviderMarketCompleteness,
    ProbeRunResult,
)
from flight_agent.domain.flights import PriceSemantics
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
    MapperVersion,
    MappingIssueCategory,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)


def test_fliggy_level1_single_segment_maps_to_m4_intermediates() -> None:
    result = FliggyEvidenceMapper().map(provider_result())

    assert result.mapper_version == FLIGGY_PROVIDER_MAPPER_VERSION
    assert result.data_status is ProviderDataStatus.COMPLETE
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert len(result.offers) == 1
    segment = result.segments[0]
    itinerary = result.itineraries[0]
    offer = result.offers[0]
    assert segment.marketing_carrier == "MU"
    assert segment.flight_number == "MU5100"
    assert segment.departure_airport == "首都T2"
    assert segment.arrival_airport == "虹桥T2"
    assert segment.departure_local == "2026-09-14T07:00:00"
    assert segment.arrival_local == "2026-09-14T09:10:00"
    assert segment.operating_carrier.state is ValueState.NOT_PROVIDED
    assert segment.aircraft_type.value == "空客320"
    assert itinerary.segment_refs == (segment.mapped_segment_ref,)
    assert offer.itinerary_ref == itinerary.mapped_itinerary_ref
    assert offer.total_amount.value == 791
    assert offer.currency.value == "CNY"
    assert offer.price_semantics is PriceSemantics.LOWER_BOUND
    assert offer.booking_reference.state is ValueState.NOT_PROVIDED


def test_fliggy_codeshare_preserves_marketing_and_explicit_operating_carrier() -> None:
    result = FliggyEvidenceMapper().map(provider_result(evidence=(codeshare_evidence(),)))

    segment = result.segments[0]
    assert segment.marketing_carrier == "MF"
    assert segment.flight_number == "MF3554"
    assert segment.operating_carrier.value == "MU"
    assert segment.operating_carrier.value != segment.marketing_carrier


def test_operating_identity_absent_is_not_copied_from_marketing() -> None:
    segment = FliggyEvidenceMapper().map(provider_result()).segments[0]

    assert segment.marketing_carrier == "MU"
    assert segment.operating_carrier.state is ValueState.NOT_PROVIDED


def test_missing_optional_fields_keep_mapped_segment_without_issue() -> None:
    result = FliggyEvidenceMapper().map(provider_result(evidence=(minimal_evidence(),)))

    assert len(result.segments) == 1
    assert result.segments[0].aircraft_type.state is ValueState.NOT_PROVIDED
    assert result.issues == ()


def test_identity_critical_missing_drops_dependent_subgraph_conservatively() -> None:
    bad = normal_evidence(departure_time=FieldEvidence.missing("time absent"))

    result = FliggyEvidenceMapper().map(provider_result(evidence=(bad,)))

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.statistics.raw_segment_count == 1
    assert result.statistics.dropped_segment_count == 1
    assert result.statistics.raw_itinerary_count == 0
    assert result.statistics.raw_offer_count == 0
    assert result.issues[0].category is MappingIssueCategory.MALFORMED_REQUIRED_FIELD


def test_missing_price_drops_offer_but_preserves_segment_and_itinerary_as_partial() -> None:
    item = normal_evidence(price=FieldEvidence.missing("price absent"))

    result = FliggyEvidenceMapper().map(provider_result(evidence=(item,)))

    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert result.statistics.dropped_offer_count == 1
    assert any(issue.raw_path == "raw_displayed_lowest_price" for issue in result.issues)


def test_failed_or_access_challenge_acquisition_produces_no_mapped_business_facts() -> None:
    result = FliggyEvidenceMapper().map(
        provider_result(outcome=BrowserProbeOutcome.ACCESS_CHALLENGE, data_status=ProviderDataStatus.UNUSABLE)
    )

    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.statistics.raw_segment_count == 0


def test_mapper_keeps_raw_to_mapped_provenance_and_source_refs() -> None:
    result = FliggyEvidenceMapper().map(provider_result())
    provenance = result.segments[0].provenance

    assert provenance.provider_id == ProviderId("FLIGGY")
    assert provenance.acquisition_id == ProviderAcquisitionId("fliggy-acquisition-1")
    assert provenance.raw_evidence_refs == ("fliggy-source-ref-1",)
    assert provenance.raw_record_ref == "fliggy-level1-evidence:1"
    assert provenance.provider_source_id.startswith("fliggy-level1-segment-1-MU5100")
    assert not hasattr(provenance, "payload")


def test_mapper_version_replay_is_deterministic_and_non_destructive() -> None:
    acquisition = provider_result()
    assert acquisition.raw_evidence is not None
    raw_before = acquisition.raw_evidence

    first = FliggyEvidenceMapper().map(acquisition)
    second = FliggyEvidenceMapper().map(acquisition)
    replay = FliggyEvidenceMapper(MapperVersion("m9-fliggy-evidence-mapper-v2")).map(acquisition)

    assert first == second
    assert replay.mapper_version == MapperVersion("m9-fliggy-evidence-mapper-v2")
    assert replay.mapper_version != first.mapper_version
    assert acquisition.raw_evidence == raw_before
    assert first.segments[0].mapped_segment_ref == replay.segments[0].mapped_segment_ref


def provider_result(
    *,
    evidence: tuple[FliggyFlightEvidence, ...] | None = None,
    outcome: BrowserProbeOutcome = BrowserProbeOutcome.SUCCESS_COMPLETE,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
) -> ProviderSearchResult:
    plan = search_plan()
    raw = ProviderRawEvidence(
        provider_id=ProviderId("FLIGGY"),
        acquisition_id=ProviderAcquisitionId("fliggy-acquisition-1"),
        search_plan_id=plan.search_plan_id,
        retrieved_at=datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        payload=probe_result(evidence=evidence, outcome=outcome).to_dict(),
        source_refs=("fliggy-source-ref-1",),
    )
    return ProviderSearchResult(
        provider_id=raw.provider_id,
        acquisition_id=raw.acquisition_id,
        search_plan_id=raw.search_plan_id,
        requirement_id=plan.requirement_id,
        based_on_requirement_version=plan.based_on_requirement_version,
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=data_status,
        coverage=ProviderCoverage(
            requested_scope=plan.requested_scope,
            actual_scope=plan.requested_scope,
            completeness=CoverageCompleteness.COMPLETE,
        ),
        raw_evidence=raw,
    )


def probe_result(
    *,
    evidence: tuple[FliggyFlightEvidence, ...] | None = None,
    outcome: BrowserProbeOutcome = BrowserProbeOutcome.SUCCESS_COMPLETE,
) -> ProbeRunResult:
    return ProbeRunResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        experiment_run_id="run-1",
        search_scope={
            "origin_text": "北京",
            "destination_text": "上海",
            "departure_date": "2026-09-14",
            "trip_type": "ONE_WAY",
            "market": "CHINA_DOMESTIC",
        },
        search_plan_id="search-plan-fliggy",
        execution_id="execution-1",
        outcome=outcome,
        observed_result_count=len(evidence or (normal_evidence(),)),
        duration_ms=100,
        dom_traversal_assessment=DomTraversalAssessment.COMPLETE_OBSERVED,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=True,
        terminal_boundary_evidence="footer",
        parser_selector_probe_version="m9-bp5-u1-fliggy-browser-probe-v0.1",
        sanitized_source_ref="https://www.fliggy.com/?tab=flight",
        evidence=evidence or (normal_evidence(),),
        diagnostics={},
    )


def search_plan() -> SearchPlan:
    return SearchPlan(
        search_plan_id=SearchPlanId("search-plan-fliggy"),
        requirement_id=RequirementId("requirement-fliggy"),
        based_on_requirement_version=RequirementVersion(1),
        requested_scope=RequestedSearchScope(
            origin=OriginScope(AirportCode("PEK")),
            destination=DestinationScope(AirportCode("SHA")),
            departure_date=DepartureDateScope(LocalDate(date(2026, 9, 14))),
        ),
    )


def normal_evidence(
    *,
    departure_time: FieldEvidence | None = None,
    price: FieldEvidence | None = None,
) -> FliggyFlightEvidence:
    return FliggyFlightEvidence(
        evidence_index=1,
        raw_displayed_flight_identity=FieldEvidence.observed("MU5100", ".J_TestFlight"),
        raw_accessible_flight_label=FieldEvidence.observed("航班号 东方航空 MU5100", "[aria-label^='航班号']"),
        raw_aircraft_text=FieldEvidence.observed("空客320", ".flight-aircraft"),
        raw_departure_time=departure_time or FieldEvidence.observed("07:00", ".flight-time"),
        raw_arrival_time=FieldEvidence.observed("09:10", ".flight-time"),
        raw_departure_airport_terminal=FieldEvidence.observed("首都T2", ".flight-port"),
        raw_arrival_airport_terminal=FieldEvidence.observed("虹桥T2", ".flight-port"),
        raw_duration_text=FieldEvidence.observed("2小时10分", ".flight-total-time"),
        raw_on_time_rate_text=FieldEvidence.observed("准点率90%", ".flight-ontime-rate"),
        raw_displayed_lowest_price=price or FieldEvidence.observed("¥791起", ".J_FlightListPrice"),
        raw_discount_text=FieldEvidence.missing("discount absent"),
        raw_availability_tag=FieldEvidence.missing("availability absent"),
        raw_codeshare_indicator=FieldEvidence.missing("codeshare absent"),
        raw_codeshare_detail_text=FieldEvidence.missing("codeshare absent"),
        booking_offer_expansion_action_present=True,
        booking_action_diagnostic={"selector": "button", "label": "订票", "present": True},
        container_diagnostic={"selector": "tr.flight-item-tr", "index": 1},
    )


def minimal_evidence() -> FliggyFlightEvidence:
    item = normal_evidence()
    return FliggyFlightEvidence(
        evidence_index=item.evidence_index,
        raw_displayed_flight_identity=item.raw_displayed_flight_identity,
        raw_accessible_flight_label=item.raw_accessible_flight_label,
        raw_aircraft_text=FieldEvidence.missing("aircraft absent"),
        raw_departure_time=item.raw_departure_time,
        raw_arrival_time=item.raw_arrival_time,
        raw_departure_airport_terminal=item.raw_departure_airport_terminal,
        raw_arrival_airport_terminal=item.raw_arrival_airport_terminal,
        raw_duration_text=FieldEvidence.missing("duration absent"),
        raw_on_time_rate_text=FieldEvidence.missing("rate absent"),
        raw_displayed_lowest_price=item.raw_displayed_lowest_price,
        raw_discount_text=item.raw_discount_text,
        raw_availability_tag=item.raw_availability_tag,
        raw_codeshare_indicator=item.raw_codeshare_indicator,
        raw_codeshare_detail_text=item.raw_codeshare_detail_text,
        booking_offer_expansion_action_present=True,
        booking_action_diagnostic=item.booking_action_diagnostic,
        container_diagnostic=item.container_diagnostic,
    )


def codeshare_evidence() -> FliggyFlightEvidence:
    item = normal_evidence()
    return FliggyFlightEvidence(
        evidence_index=2,
        raw_displayed_flight_identity=FieldEvidence.observed("MF3554", ".J_TestFlight"),
        raw_accessible_flight_label=FieldEvidence.observed("航班号 厦门航空 MF3554", "[aria-label^='航班号']"),
        raw_aircraft_text=item.raw_aircraft_text,
        raw_departure_time=FieldEvidence.observed("10:00", ".flight-time"),
        raw_arrival_time=FieldEvidence.observed("12:15", ".flight-time"),
        raw_departure_airport_terminal=FieldEvidence.observed("大兴", ".flight-port"),
        raw_arrival_airport_terminal=FieldEvidence.observed("浦东", ".flight-port"),
        raw_duration_text=FieldEvidence.observed("2小时15分", ".flight-total-time"),
        raw_on_time_rate_text=FieldEvidence.missing("rate absent"),
        raw_displayed_lowest_price=FieldEvidence.observed("¥830起", ".J_FlightListPrice"),
        raw_discount_text=FieldEvidence.missing("discount absent"),
        raw_availability_tag=FieldEvidence.missing("availability absent"),
        raw_codeshare_indicator=FieldEvidence.observed("codeshare indicator present", "[data-testid='share-flight-tip']"),
        raw_codeshare_detail_text=FieldEvidence.observed(
            "实际乘坐航班：东方航空MU5100",
            "[data-testid='share-flight-tip']",
        ),
        booking_offer_expansion_action_present=True,
        booking_action_diagnostic=item.booking_action_diagnostic,
        container_diagnostic=item.container_diagnostic,
    )
