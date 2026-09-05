from __future__ import annotations

from datetime import UTC, date, datetime

from flight_agent.adapters.flight_providers.fliggy import (
    DEFAULT_FLIGGY_CANONICAL_ENTRY_PROFILE,
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    DomTraversalAssessment,
    FieldEvidence,
    FliggyCanonicalEntry,
    FliggyEvidenceMapper,
    FliggyFlightEvidence,
    FliggyLevel2OfferMapper,
    FliggyLevel2OfferRowEvidence,
    FliggyLevel2ParentContext,
    Level2ExpansionBounds,
    Level2ExpansionOutcome,
    Level2ExpansionResult,
    Level2ExpansionTarget,
    ProbeRunResult,
    ProviderMarketCompleteness,
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
    CandidateMerger,
    CoverageCompleteness,
    EquivalenceDecision,
    MergerVersion,
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


def test_f_can_09_provider_local_airport_aliases_cover_full_human_labels() -> None:
    profile = DEFAULT_FLIGGY_CANONICAL_ENTRY_PROFILE

    assert profile.airport_code("大兴国际机场") == "PKX"
    assert profile.airport_code("浦东国际机场T2") == "PVG"
    assert profile.airport_code("大兴") == "PKX"
    assert profile.airport_code("浦东") == "PVG"
    assert profile.airport_code("未知国际机场T2") == "未知国际机场T2"


def test_f_can_10_ca8341_human_case_survives_canonicalization_after_alias_repair() -> None:
    level1_mapping = mapped_result(evidence=(ca8341_human_evidence(),))
    level1 = FliggyCanonicalEntry().normalize(level1_mapping, ca8341_context())

    assert level1.data_status is ProviderDataStatus.COMPLETE
    assert level1.segments[0].marketing_carrier == "CA"
    assert level1.segments[0].flight_number == "CA8341"
    assert level1.segments[0].departure_airport == "PKX"
    assert level1.segments[0].arrival_airport == "PVG"
    assert level1.itineraries[0].segment_ids == (level1.segments[0].segment_id,)
    assert level1.offers[0].itinerary_id == level1.itineraries[0].itinerary_id
    assert level1.offers[0].total_price.amount == 399
    assert level1.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert level1.offers[0].provenance[0].detail_ref == "fliggy-level1-evidence:1"

    parent = FliggyLevel2ParentContext(
        parent_level1_ref="CA8341",
        segments=level1_mapping.segments,
        itinerary=level1_mapping.itineraries[0],
    )
    level2_mapping = FliggyLevel2OfferMapper((parent,)).map(ca8341_level2_provider_result())
    level2 = FliggyCanonicalEntry().normalize(level2_mapping, ca8341_context())

    assert level2.data_status is ProviderDataStatus.COMPLETE
    assert [segment.departure_airport for segment in level2.segments] == ["PKX", "PKX", "PKX"]
    assert [segment.arrival_airport for segment in level2.segments] == ["PVG", "PVG", "PVG"]
    assert [offer.total_price.amount for offer in level2.offers] == [400, 399, 647]
    assert [offer.price_semantics for offer in level2.offers] == [
        PriceSemantics.EXACT,
        PriceSemantics.EXACT,
        PriceSemantics.EXACT,
    ]
    level2_detail_refs = tuple(offer.provenance[0].detail_ref for offer in level2.offers)
    for ref in level2_detail_refs:
        assert ref is not None
        assert ref.startswith("fliggy-level2-offer-row:manual-row-")

    merger = CandidateMerger(MergerVersion("candidate-merger-v1"))
    assert merger.offer_equivalence(level1.offers[0], level2.offers[1]) is EquivalenceDecision.DISTINCT
    assert merger.offer_equivalence(level2.offers[0], level2.offers[1]) is EquivalenceDecision.DISTINCT
    assert merger.offer_equivalence(level2.offers[1], level2.offers[2]) is EquivalenceDecision.DISTINCT

    same_price_mapping = FliggyLevel2OfferMapper((parent,)).map(
        ca8341_level2_provider_result(
            rows=(
                ca8341_offer_row(row_ref="manual-row-02", seller="阿斯兰翱翔航服", amount=399),
                ca8341_offer_row(row_ref="manual-row-04", seller="另一供应商", amount=399),
            )
        )
    )
    same_price_level2 = FliggyCanonicalEntry().normalize(same_price_mapping, ca8341_context())

    assert (
        merger.offer_equivalence(same_price_level2.offers[0], same_price_level2.offers[1])
        is EquivalenceDecision.INSUFFICIENT_EVIDENCE
    )


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


def ca8341_context() -> NormalizationContext:
    return NormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("m9-fliggy-human-case-reference-data-v1"),
            airports=frozenset({"PKX", "PVG"}),
            carriers=frozenset({"CA"}),
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


def ca8341_human_evidence() -> FliggyFlightEvidence:
    return FliggyFlightEvidence(
        evidence_index=1,
        raw_displayed_flight_identity=FieldEvidence.observed("国航CA8341", "human-observation:flight"),
        raw_accessible_flight_label=FieldEvidence.missing("not observed"),
        raw_aircraft_text=FieldEvidence.observed("中型机 321", "human-observation:aircraft"),
        raw_departure_time=FieldEvidence.observed("22:00", "human-observation:departure-time"),
        raw_arrival_time=FieldEvidence.observed("23:45", "human-observation:arrival-time"),
        raw_departure_airport_terminal=FieldEvidence.observed(
            "大兴国际机场",
            "human-observation:departure-airport",
        ),
        raw_arrival_airport_terminal=FieldEvidence.observed(
            "浦东国际机场T2",
            "human-observation:arrival-airport",
        ),
        raw_duration_text=FieldEvidence.observed("约2小时", "human-observation:duration"),
        raw_on_time_rate_text=FieldEvidence.missing("not observed"),
        raw_displayed_lowest_price=FieldEvidence.observed(
            "最低价格（不含税费） ¥399",
            "human-observation:lowest-price",
        ),
        raw_discount_text=FieldEvidence.missing("not observed"),
        raw_availability_tag=FieldEvidence.missing("not observed"),
        raw_codeshare_indicator=FieldEvidence.missing("not observed"),
        raw_codeshare_detail_text=FieldEvidence.missing("not observed"),
        booking_offer_expansion_action_present=True,
        booking_action_diagnostic={"label": "订票", "present": True},
        container_diagnostic={"case_id": "FLI-HUM-CASE-001"},
    )


def ca8341_level2_provider_result(
    *,
    rows: tuple[FliggyLevel2OfferRowEvidence, ...] | None = None,
) -> ProviderSearchResult:
    plan = search_plan()
    offer_rows = rows or (
        ca8341_offer_row(
            row_ref="manual-row-01",
            seller="航司直营 / AIR CHINA 中国国际航空",
            amount=400,
        ),
        ca8341_offer_row(row_ref="manual-row-02", seller="阿斯兰翱翔航服", amount=399),
        ca8341_offer_row(row_ref="manual-row-03", seller="阿斯兰翱翔航服", amount=647),
    )
    raw = ProviderRawEvidence(
        provider_id=ProviderId("FLIGGY"),
        acquisition_id=ProviderAcquisitionId("fliggy-human-level2-acquisition-1"),
        search_plan_id=plan.search_plan_id,
        retrieved_at=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
        payload=Level2ExpansionResult(
            provider_identity="FLIGGY",
            acquisition_mode=BrowserAcquisitionMode.BROWSER,
            acquired_at=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
            experiment_run_id="human-case-001",
            search_plan_id="search-plan-fliggy",
            execution_id="human-case-001",
            target=Level2ExpansionTarget(parent_level1_ref="CA8341", level1_evidence_index=1),
            outcome=Level2ExpansionOutcome.SUCCESS_EXPANDED,
            observed_offer_row_count=len(offer_rows),
            duration_ms=0,
            sanitized_source_ref="LOCAL-ONLY:human-observation:FLI-HUM-CASE-001",
            parser_selector_probe_version="manual-human-observation-v1",
            bounds=Level2ExpansionBounds(max_offer_rows=20, max_wait_ms=1, max_retries=0),
            offer_rows=offer_rows,
            diagnostics={"read_only": True, "manual_case_id": "FLI-HUM-CASE-001"},
        ).to_dict(),
        source_refs=("LOCAL-ONLY:human-observation:FLI-HUM-CASE-001",),
    )
    return ProviderSearchResult(
        provider_id=raw.provider_id,
        acquisition_id=raw.acquisition_id,
        search_plan_id=raw.search_plan_id,
        requirement_id=plan.requirement_id,
        based_on_requirement_version=plan.based_on_requirement_version,
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.COMPLETE,
        coverage=ProviderCoverage(
            requested_scope=plan.requested_scope,
            actual_scope=plan.requested_scope,
            completeness=CoverageCompleteness.COMPLETE,
        ),
        raw_evidence=raw,
    )


def ca8341_offer_row(
    *,
    row_ref: str,
    seller: str,
    amount: int,
) -> FliggyLevel2OfferRowEvidence:
    return FliggyLevel2OfferRowEvidence(
        offer_row_ref=row_ref,
        sequence=int(row_ref.rsplit("-", maxsplit=1)[1]),
        parent_level1_ref="CA8341",
        raw_seller_text=FieldEvidence.observed(seller, "human-observation:seller"),
        raw_seller_marker_text=FieldEvidence.missing("not separately observed"),
        raw_price_text=FieldEvidence.observed(f"¥{amount}", "human-observation:row-price"),
        price_amount=amount,
        price_currency="CNY",
        raw_cabin_product_text=FieldEvidence.observed("经济舱", "human-observation:cabin"),
        raw_baggage_text=FieldEvidence.observed("托运行李20公斤", "human-observation:baggage"),
        raw_refund_change_rule_text=FieldEvidence.observed(
            "退改规则详情",
            "human-observation:refund-change",
        ),
        raw_availability_text=FieldEvidence.missing("not observed"),
        action_evidence=FieldEvidence.observed("订", "human-observation:action"),
        row_diagnostic={"manual_case_id": "FLI-HUM-CASE-001"},
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
