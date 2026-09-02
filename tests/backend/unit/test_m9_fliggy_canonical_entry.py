from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.flight_providers.fliggy import (
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    DomTraversalAssessment,
    FieldEvidence,
    FliggyCanonicalEntry,
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
    NormalizationContext,
    NormalizerVersion,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
    ReferenceData,
    ReferenceDataVersion,
)


def test_f_can_01_single_segment_enters_existing_canonical_graph() -> None:
    result = canonicalize(mapped_result())

    assert result.data_status is ProviderDataStatus.COMPLETE
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert len(result.offers) == 1
    assert result.segments[0].departure_airport == "PEK"
    assert result.segments[0].arrival_airport == "SHA"
    assert result.itineraries[0].segment_ids == (result.segments[0].segment_id,)
    assert result.offers[0].itinerary_id == result.itineraries[0].itinerary_id
    assert result.normalizer_version == NormalizerVersion("common-normalizer-v1")


def test_f_can_02_codeshare_keeps_marketing_and_operating_separate() -> None:
    result = canonicalize(mapped_result(evidence=(codeshare_evidence(),)))

    segment = result.segments[0]
    assert segment.marketing_carrier == "MF"
    assert segment.flight_number == "MF3554"
    assert segment.operating_carrier.value == "MU"
    assert segment.operating_carrier.value != segment.marketing_carrier


def test_f_can_03_lower_bound_price_is_not_promoted_to_exact() -> None:
    result = canonicalize(mapped_result())

    offer = result.offers[0]
    assert offer.total_price.amount == 791
    assert offer.total_price.currency == "CNY"
    assert offer.price_semantics is PriceSemantics.LOWER_BOUND
    assert offer.price_semantics is not PriceSemantics.EXACT


def test_f_can_04_missing_operating_is_not_inferred_from_marketing() -> None:
    result = canonicalize(mapped_result())

    assert result.segments[0].marketing_carrier == "MU"
    assert result.segments[0].operating_carrier.state is ValueState.NOT_PROVIDED


def test_f_can_05_missing_price_preserves_reliable_segment_and_itinerary() -> None:
    no_price = normal_evidence(price=FieldEvidence.missing("price absent"))

    result = canonicalize(mapped_result(evidence=(no_price,)))

    assert result.data_status is ProviderDataStatus.PARTIAL
    assert len(result.segments) == 1
    assert len(result.itineraries) == 1
    assert result.offers == ()


def test_f_can_06_provenance_survives_without_promoting_source_identity() -> None:
    result = canonicalize(mapped_result())
    provenance = result.segments[0].provenance[0]

    assert provenance.source_type == "provider_raw_record"
    assert provenance.source_ref == "FLIGGY:fliggy-acquisition-1"
    assert provenance.detail_ref == "fliggy-level1-evidence:1"
    assert result.segment_sources[0][1].value.startswith("mapped-segment:")
    assert "fliggy-source-ref-1" not in result.segments[0].segment_id.value


def test_f_can_07_failed_or_challenge_mapped_input_yields_no_canonical_facts() -> None:
    result = canonicalize(
        mapped_result(
            outcome=BrowserProbeOutcome.ACCESS_CHALLENGE,
            data_status=ProviderDataStatus.UNUSABLE,
        )
    )

    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.segments == ()
    assert result.itineraries == ()
    assert result.offers == ()


def test_f_can_08_deterministic_replay_preserves_versions() -> None:
    mapped = mapped_result()

    first = canonicalize(mapped)
    second = canonicalize(mapped)

    assert first == second
    assert first.mapper_version == mapped.mapper_version
    assert first.normalizer_version == NormalizerVersion("common-normalizer-v1")
    assert first.reference_data_version == ReferenceDataVersion("m9-fliggy-reference-data-v1")


def mapped_result(
    *,
    evidence: tuple[FliggyFlightEvidence, ...] | None = None,
    outcome: BrowserProbeOutcome = BrowserProbeOutcome.SUCCESS_COMPLETE,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
):
    return FliggyEvidenceMapper().map(
        provider_result(evidence=evidence, outcome=outcome, data_status=data_status)
    )


def canonicalize(mapped):
    return FliggyCanonicalEntry().normalize(mapped, context())


def context() -> NormalizationContext:
    return NormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("m9-fliggy-reference-data-v1"),
            airports=frozenset({"PEK", "SHA", "PKX", "PVG"}),
            carriers=frozenset({"MU", "MF"}),
        ),
    )


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


def normal_evidence(*, price: FieldEvidence | None = None) -> FliggyFlightEvidence:
    return FliggyFlightEvidence(
        evidence_index=1,
        raw_displayed_flight_identity=FieldEvidence.observed("MU5100", ".J_TestFlight"),
        raw_accessible_flight_label=FieldEvidence.observed("航班号 东方航空 MU5100", "[aria-label^='航班号']"),
        raw_aircraft_text=FieldEvidence.observed("空客320", ".flight-aircraft"),
        raw_departure_time=FieldEvidence.observed("07:00", ".flight-time"),
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
