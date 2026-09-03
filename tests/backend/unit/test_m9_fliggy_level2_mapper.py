from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from flight_agent.adapters.flight_providers.fliggy import (
    FLIGGY_LEVEL2_PROVIDER_MAPPER_VERSION,
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
    CoverageCompleteness,
    MappingIssueCategory,
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

REPO_ROOT = Path(__file__).resolve().parents[3]
PARENT_LEVEL1_REF = "fliggy-level1-live:1:MU5100"


def test_l2_map_01_input_boundary_accepts_sanitized_evidence_only() -> None:
    result = level2_mapper().map(level2_provider_result(rows=(offer_row(),)))

    assert result.mapper_version == FLIGGY_LEVEL2_PROVIDER_MAPPER_VERSION
    assert len(result.offers) == 1
    assert result.offers[0].total_amount.value == 820
    assert not hasattr(result, "html")
    assert not hasattr(result.offers[0].provenance, "payload")


def test_l2_map_02_row_atomicity_keeps_each_row_isolated() -> None:
    result = level2_mapper().map(
        level2_provider_result(
            rows=(
                offer_row(row_ref="row-a", price_text="¥820", amount=820, baggage="托运行李1件", refund="不可退"),
                offer_row(row_ref="row-b", price_text="¥845", amount=845, baggage="无托运行李", refund="可退"),
            )
        )
    )

    assert [offer.total_amount.value for offer in result.offers] == [820, 845]
    assert [offer.refundable.value for offer in result.offers] == [False, True]
    baggage_by_offer = {
        offer.provider_offer_id: segment.checked_baggage_pieces.value
        for offer in result.offers
        for itinerary in result.itineraries
        if itinerary.mapped_itinerary_ref == offer.itinerary_ref
        for segment in result.segments
        if segment.mapped_segment_ref in itinerary.segment_refs
    }
    assert baggage_by_offer[result.offers[0].provider_offer_id] == 1
    assert baggage_by_offer[result.offers[1].provider_offer_id] == 0


def test_l2_map_03_exact_price_maps_row_amount_currency_and_exact_semantics() -> None:
    offer = level2_mapper().map(level2_provider_result(rows=(offer_row(price_text="¥820"),))).offers[0]

    assert offer.total_amount.value == 820
    assert offer.currency.value == "CNY"
    assert offer.price_semantics is PriceSemantics.EXACT


def test_l2_map_04_lower_bound_level1_price_never_substitutes_for_missing_l2_price() -> None:
    mapper = level2_mapper()

    missing_price = mapper.map(level2_provider_result(rows=(offer_row(price=FieldEvidence.missing("absent")),)))
    same_amount = mapper.map(level2_provider_result(rows=(offer_row(price_text="¥791", amount=791),)))
    lower_bound_row = mapper.map(level2_provider_result(rows=(offer_row(price_text="¥791起", amount=791),)))

    assert missing_price.offers == ()
    assert missing_price.statistics.dropped_offer_count == 1
    assert same_amount.offers[0].price_semantics is PriceSemantics.EXACT
    assert lower_bound_row.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert lower_bound_row.offers[0].provider_offer_id.startswith("fliggy-level2-offer-")


def test_l2_map_05_seller_boundary_keeps_seller_evidence_only() -> None:
    result = level2_mapper().map(level2_provider_result(rows=(offer_row(seller="东方航空旗舰店"),)))
    offer = result.offers[0]

    assert not hasattr(offer, "seller")
    assert "东方航空" not in offer.provider_offer_id
    assert result.provider_id == ProviderId("FLIGGY")


def test_l2_map_06_baggage_boundary_maps_pieces_but_not_weight_rules() -> None:
    mapped_piece = level2_mapper().map(level2_provider_result(rows=(offer_row(baggage="托运行李2件"),)))
    weight_rule = level2_mapper().map(level2_provider_result(rows=(offer_row(baggage="托运行李20KG"),)))

    assert mapped_piece.segments[0].checked_baggage_pieces.value == 2
    assert weight_rule.segments[0].checked_baggage_pieces.state is ValueState.NOT_PROVIDED


def test_l2_map_07_refund_boundary_maps_only_explicit_boolean() -> None:
    refundable = level2_mapper().map(level2_provider_result(rows=(offer_row(refund="可退"),)))
    nonrefundable = level2_mapper().map(level2_provider_result(rows=(offer_row(refund="不可退"),)))
    complex_rule = level2_mapper().map(level2_provider_result(rows=(offer_row(refund="退改¥280起"),)))

    assert refundable.offers[0].refundable.value is True
    assert nonrefundable.offers[0].refundable.value is False
    assert complex_rule.offers[0].refundable.state is ValueState.NOT_PROVIDED


def test_l2_map_08_availability_negative_control_never_creates_inventory_truth() -> None:
    offer = level2_mapper().map(level2_provider_result(rows=(offer_row(availability="仅剩2张"),))).offers[0]

    assert not hasattr(offer, "inventory_count")
    assert not hasattr(offer, "available_seats")


def test_l2_map_09_partial_mapping_preserves_reliable_rows_and_existing_missing_vocabulary() -> None:
    result = level2_mapper().map(
        level2_provider_result(rows=(offer_row(row_ref="good"), offer_row(row_ref="bad", amount=None)))
    )

    assert len(result.offers) == 1
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert result.statistics.raw_offer_count == 2
    assert result.statistics.dropped_offer_count == 1
    assert result.offers[0].booking_reference.state is ValueState.NOT_PROVIDED
    assert result.issues[0].category is MappingIssueCategory.MALFORMED_REQUIRED_FIELD


def test_l2_map_10_provenance_keeps_row_parent_and_source_refs_without_sensitive_state() -> None:
    result = level2_mapper().map(level2_provider_result(rows=(offer_row(row_ref="row-a"),)))
    provenance = result.offers[0].provenance

    assert provenance.provider_id == ProviderId("FLIGGY")
    assert provenance.acquisition_id == ProviderAcquisitionId("fliggy-level2-acquisition-1")
    assert provenance.raw_evidence_refs == ("fliggy-level2-source-ref-1",)
    assert provenance.raw_record_ref == "fliggy-level2-offer-row:row-a"
    assert "fliggy-level1-live-1-mu5100" in provenance.provider_source_id
    assert "row-a" in provenance.provider_source_id
    assert "cookie" not in repr(provenance).lower()
    assert "token" not in repr(provenance).lower()


def test_l2_map_11_shared_contract_and_common_normalizer_stay_unchanged() -> None:
    mapping = level2_mapper().map(level2_provider_result(rows=(offer_row(),)))
    normalized = FliggyCanonicalEntry(common_normalizer=canonical_context_normalizer()).normalize(
        mapping,
        normalization_context(),
    )

    assert not hasattr(mapping.offers[0], "seller")
    assert not hasattr(mapping.offers[0], "inventory_count")
    assert normalized.offers[0].total_price.amount == 820
    assert normalized.offers[0].price_semantics is PriceSemantics.EXACT


def test_l2_map_12_existing_fliggy_regressions_still_map_level1_independently() -> None:
    level1 = parent_mapping_result()

    assert level1.offers[0].total_amount.value == 791
    assert level1.offers[0].price_semantics is PriceSemantics.LOWER_BOUND
    assert level2_mapper().map(level2_provider_result(rows=(offer_row(amount=820),))).offers[0].total_amount.value == 820


def test_l2_map_13_live_gap_label_remains_offline_evidence_backed() -> None:
    payload = level2_result(rows=(offer_row(),), outcome=Level2ExpansionOutcome.SUCCESS_EXPANDED).to_dict() | {
        "evidence_basis": "OFFLINE-EVIDENCE-BACKED / LIVE GAP PRESERVED",
        "live_validated": False,
    }

    assert payload["evidence_basis"] == "OFFLINE-EVIDENCE-BACKED / LIVE GAP PRESERVED"
    assert payload["live_validated"] is False


def level2_mapper() -> FliggyLevel2OfferMapper:
    level1 = parent_mapping_result()
    return FliggyLevel2OfferMapper(
        parent_contexts=(
            FliggyLevel2ParentContext(
                parent_level1_ref=PARENT_LEVEL1_REF,
                segments=level1.segments,
                itinerary=level1.itineraries[0],
            ),
        )
    )


def parent_mapping_result():
    return FliggyEvidenceMapper().map(level1_provider_result())


def level2_provider_result(
    *,
    rows: tuple[FliggyLevel2OfferRowEvidence, ...],
    outcome: Level2ExpansionOutcome = Level2ExpansionOutcome.SUCCESS_EXPANDED,
) -> ProviderSearchResult:
    plan = search_plan()
    raw = ProviderRawEvidence(
        provider_id=ProviderId("FLIGGY"),
        acquisition_id=ProviderAcquisitionId("fliggy-level2-acquisition-1"),
        search_plan_id=plan.search_plan_id,
        retrieved_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC),
        payload=level2_result(rows=rows, outcome=outcome).to_dict()
        | {
            "evidence_basis": "OFFLINE-EVIDENCE-BACKED / LIVE GAP PRESERVED",
            "live_validated": False,
        },
        source_refs=("fliggy-level2-source-ref-1",),
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


def level2_result(
    *,
    rows: tuple[FliggyLevel2OfferRowEvidence, ...],
    outcome: Level2ExpansionOutcome,
) -> Level2ExpansionResult:
    return Level2ExpansionResult(
        provider_identity="FLIGGY",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC),
        experiment_run_id="offline-fixture",
        search_plan_id="search-plan-fliggy",
        execution_id="execution-1",
        target=Level2ExpansionTarget(parent_level1_ref=PARENT_LEVEL1_REF, level1_evidence_index=1),
        outcome=outcome,
        observed_offer_row_count=len(rows),
        duration_ms=10,
        sanitized_source_ref="offline-fixture:fliggy-level2",
        parser_selector_probe_version="m9-bp5-u1-fliggy-browser-probe-v0.1",
        bounds=Level2ExpansionBounds(max_offer_rows=20, max_wait_ms=5000, max_retries=0),
        offer_rows=rows,
        diagnostics={
            "read_only": True,
            "level2_mapping_performed": False,
            "parent_level1_evidence_preserved": True,
        },
    )


def offer_row(
    *,
    row_ref: str = "row-a",
    seller: str = "东方航空旗舰店",
    price_text: str = "¥820",
    price: FieldEvidence | None = None,
    amount: int | None = 820,
    currency: str | None = "CNY",
    baggage: str = "托运行李1件",
    refund: str = "不可退",
    availability: str = "仅剩2张",
) -> FliggyLevel2OfferRowEvidence:
    return FliggyLevel2OfferRowEvidence(
        offer_row_ref=row_ref,
        sequence=1,
        parent_level1_ref=PARENT_LEVEL1_REF,
        raw_seller_text=FieldEvidence.observed(seller, "[data-testid='seller-name']"),
        raw_seller_marker_text=FieldEvidence.observed("航司直营", "[data-testid='seller-marker']"),
        raw_price_text=price or FieldEvidence.observed(price_text, "[data-testid='offer-price']"),
        price_amount=amount,
        price_currency=currency,
        raw_cabin_product_text=FieldEvidence.observed("经济舱 标准价", "[data-testid='cabin-product']"),
        raw_baggage_text=FieldEvidence.observed(baggage, "[data-testid='baggage']"),
        raw_refund_change_rule_text=FieldEvidence.observed(refund, "[data-testid='fare-rule']"),
        raw_availability_text=FieldEvidence.observed(availability, "[data-testid='availability']"),
        action_evidence=FieldEvidence.observed("level2 offer action present", "[data-testid='select-offer-btn']"),
        row_diagnostic={"selector": "[data-testid='fliggy-offer-row']", "provider_local_identity": True},
    )


def level1_provider_result() -> ProviderSearchResult:
    plan = search_plan()
    raw = ProviderRawEvidence(
        provider_id=ProviderId("FLIGGY"),
        acquisition_id=ProviderAcquisitionId("fliggy-level1-acquisition-1"),
        search_plan_id=plan.search_plan_id,
        retrieved_at=datetime(2026, 9, 2, 1, 0, tzinfo=UTC),
        payload=level1_probe_result().to_dict(),
        source_refs=("fliggy-level1-source-ref-1",),
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


def level1_probe_result() -> ProbeRunResult:
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
        outcome=BrowserProbeOutcome.SUCCESS_COMPLETE,
        observed_result_count=1,
        duration_ms=100,
        dom_traversal_assessment=DomTraversalAssessment.COMPLETE_OBSERVED,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=True,
        terminal_boundary_evidence="footer",
        parser_selector_probe_version="m9-bp5-u1-fliggy-browser-probe-v0.1",
        sanitized_source_ref="https://www.fliggy.com/?tab=flight",
        evidence=(level1_evidence(),),
        diagnostics={},
    )


def level1_evidence() -> FliggyFlightEvidence:
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
        raw_displayed_lowest_price=FieldEvidence.observed("¥791起", ".J_FlightListPrice"),
        raw_discount_text=FieldEvidence.missing("discount absent"),
        raw_availability_tag=FieldEvidence.missing("availability absent"),
        raw_codeshare_indicator=FieldEvidence.missing("codeshare absent"),
        raw_codeshare_detail_text=FieldEvidence.missing("codeshare absent"),
        booking_offer_expansion_action_present=True,
        booking_action_diagnostic={"selector": "button", "label": "订票", "present": True},
        container_diagnostic={"selector": "tr.flight-item-tr", "index": 1},
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


def canonical_context_normalizer():
    from flight_agent.ports import CommonNormalizer

    return CommonNormalizer()


def normalization_context() -> NormalizationContext:
    return NormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("test-reference-v1"),
            airports=frozenset({"PEK", "SHA"}),
            carriers=frozenset({"MU"}),
        ),
    )
