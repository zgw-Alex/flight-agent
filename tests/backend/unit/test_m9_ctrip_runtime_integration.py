from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.ctrip import (
    BrowserAcquisitionMode,
    BrowserProbeOutcome,
    CapturedPayload,
    CtripFlightProvider,
    CtripProbeInput,
    CtripProbeRunResult,
    DomTraversalAssessment,
    ProviderMarketCompleteness,
    ctrip_probe_input_for_search_plan,
    extract_level1_evidence_from_payloads,
    extract_level2_offer_evidence,
)
from flight_agent.application import SearchExecutionStatus, validate_requirement
from flight_agent.bootstrap.app import _build_search_execution, create_app
from flight_agent.config.settings import Settings
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CoverageCompleteness,
    FlightProvider,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderSearchResult,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_ctrip_provider_consumes_structured_search_plan_without_natural_language_parsing() -> None:
    seen_inputs: list[CtripProbeInput] = []

    def acquisition(probe_input: CtripProbeInput) -> CtripProbeRunResult:
        seen_inputs.append(probe_input)
        return ctrip_result(BrowserProbeOutcome.SUCCESS_COMPLETE)

    flight_provider: FlightProvider = CtripFlightProvider(
        acquisition=acquisition,
        id_factory=lambda: "ctrip-acq-structured",
    )
    result = flight_provider.search(search_plan())

    assert isinstance(result, ProviderSearchResult)
    assert result.provider_id == ProviderId("CTRIP")
    assert result.search_plan_id == SearchPlanId("search-plan-ctrip-runtime")
    assert seen_inputs == [
        CtripProbeInput(
            origin_text="PEK",
            destination_text="SHA",
            departure_date=date(2026, 9, 14),
            search_plan_id="search-plan-ctrip-runtime",
            overall_deadline_seconds=45.0,
        )
    ]
    assert not hasattr(flight_provider, "source_input")
    assert not hasattr(flight_provider, "natural_language_query")


def test_ctrip_runtime_completes_offline_to_candidate_snapshot() -> None:
    search_execution = _build_search_execution(
        settings=Settings(FLIGHT_PROVIDER="ctrip"),
        ctrip_acquisition=lambda _: ctrip_result(BrowserProbeOutcome.SUCCESS_COMPLETE),
    )

    result = search_execution.execute(
        requirement=ready_requirement(),
        validation=validate_requirement(ready_requirement()),
    )

    assert result.status is SearchExecutionStatus.SNAPSHOT_READY
    assert result.provider_result is not None
    assert result.provider_result.provider_id == ProviderId("CTRIP")
    assert result.mapping_result is not None
    assert result.mapping_result.provider_id == ProviderId("CTRIP")
    assert result.snapshot_outcome is not None
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.offers[0].total_price == Money(Decimal(820), "CNY")


def test_ctrip_success_empty_is_the_only_empty_runtime_mapping() -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(BrowserProbeOutcome.SUCCESS_EMPTY, level1=(), level2=()),
        id_factory=lambda: "ctrip-acq-empty",
    ).search(search_plan())

    assert result.execution_status is ProviderExecutionStatus.SUCCESS
    assert result.data_status is ProviderDataStatus.EMPTY
    assert result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert result.raw_evidence is not None


def test_ctrip_partial_runtime_preserves_partial_data_and_coverage_limitation() -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(BrowserProbeOutcome.SUCCESS_PARTIAL, level2=()),
        id_factory=lambda: "ctrip-acq-partial",
    ).search(search_plan())

    assert result.execution_status is ProviderExecutionStatus.SUCCESS
    assert result.data_status is ProviderDataStatus.PARTIAL
    assert result.coverage.completeness is CoverageCompleteness.PARTIAL
    assert result.coverage.limitations[0].code == "CTRIP_PARTIAL_EVIDENCE"


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_data_status"),
    (
        (BrowserProbeOutcome.TIMEOUT, ProviderExecutionStatus.TIMEOUT, ProviderDataStatus.UNKNOWN),
        (BrowserProbeOutcome.NETWORK_ERROR, ProviderExecutionStatus.UPSTREAM_ERROR, ProviderDataStatus.UNKNOWN),
        (BrowserProbeOutcome.PROVIDER_ERROR, ProviderExecutionStatus.UPSTREAM_ERROR, ProviderDataStatus.UNKNOWN),
        (BrowserProbeOutcome.EVIDENCE_INSUFFICIENT, ProviderExecutionStatus.INVALID_RESPONSE, ProviderDataStatus.UNUSABLE),
    ),
)
def test_ctrip_network_timeout_and_error_outcomes_are_not_encoded_as_empty(
    outcome: BrowserProbeOutcome,
    expected_status: ProviderExecutionStatus,
    expected_data_status: ProviderDataStatus,
) -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(outcome, level1=(), level2=()),
        id_factory=lambda: f"ctrip-acq-{outcome.value.lower()}",
    ).search(search_plan())

    assert result.execution_status is expected_status
    assert result.data_status is expected_data_status
    assert result.data_status is not ProviderDataStatus.EMPTY
    assert result.raw_evidence is None


def test_ctrip_access_challenge_is_preserved_without_bypass_or_fake_empty() -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(BrowserProbeOutcome.ACCESS_CHALLENGE, level1=(), level2=()),
        id_factory=lambda: "ctrip-acq-access-challenge",
    ).search(search_plan())

    assert result.execution_status is ProviderExecutionStatus.AUTH_ERROR
    assert result.data_status is ProviderDataStatus.UNKNOWN
    assert result.raw_evidence is None
    assert result.data_status is not ProviderDataStatus.EMPTY


def test_ctrip_runtime_sanitizes_raw_evidence_before_mapper_handoff() -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(
            BrowserProbeOutcome.SUCCESS_COMPLETE,
            diagnostics={
                "cookie": "session=secret",
                "nested": {"Authorization": "Bearer abc", "safe": "value"},
            },
            sanitized_source_ref="https://flights.ctrip.com/online/channel/domestic?token=secret",
        ),
        id_factory=lambda: "ctrip-acq-sanitized",
    ).search(search_plan())

    assert result.raw_evidence is not None
    rendered = str(result.raw_evidence.payload)
    assert "session=secret" not in rendered
    assert "Bearer abc" not in rendered
    assert "token=secret" not in rendered
    assert "[REDACTED]" in rendered


def test_ctrip_runtime_rejects_unsafe_success_evidence_without_mapper_handoff() -> None:
    result = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(
            BrowserProbeOutcome.SUCCESS_COMPLETE,
            diagnostics={"safe_debug_text": "Bearer secret-runtime-token"},
        ),
        id_factory=lambda: "ctrip-acq-unsafe-rejected",
    ).search(search_plan())

    assert result.execution_status is ProviderExecutionStatus.INVALID_RESPONSE
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.raw_evidence is None


def test_bootstrap_keeps_mock_as_default_and_ctrip_is_explicitly_selectable_without_network() -> None:
    calls: list[CtripProbeInput] = []

    default_app = create_app(settings=Settings())
    ctrip_app = create_app(
        settings=Settings(FLIGHT_PROVIDER="ctrip"),
        ctrip_acquisition=lambda probe_input: calls.append(probe_input)
        or ctrip_result(BrowserProbeOutcome.SUCCESS_COMPLETE),
    )

    assert type(default_app.state.search_execution._flight_provider).__name__ == "MockFlightProvider"
    assert type(ctrip_app.state.search_execution._flight_provider).__name__ == "CtripFlightProvider"
    assert calls == []


def test_ctrip_retry_is_bounded_and_does_not_retry_access_challenge() -> None:
    outcomes = iter(
        (
            BrowserProbeOutcome.NETWORK_ERROR,
            BrowserProbeOutcome.SUCCESS_COMPLETE,
        )
    )
    calls = 0

    def acquisition(_: CtripProbeInput) -> CtripProbeRunResult:
        nonlocal calls
        calls += 1
        return ctrip_result(next(outcomes))

    result = CtripFlightProvider(
        acquisition=acquisition,
        id_factory=lambda: "ctrip-acq-retry",
        max_attempts=2,
    ).search(search_plan())

    assert calls == 2
    assert result.execution_status is ProviderExecutionStatus.SUCCESS

    challenge_calls = 0

    def challenge(_: CtripProbeInput) -> CtripProbeRunResult:
        nonlocal challenge_calls
        challenge_calls += 1
        return ctrip_result(BrowserProbeOutcome.ACCESS_CHALLENGE, level1=(), level2=())

    challenge_result = CtripFlightProvider(
        acquisition=challenge,
        id_factory=lambda: "ctrip-acq-no-retry-challenge",
        max_attempts=3,
    ).search(search_plan())

    assert challenge_calls == 1
    assert challenge_result.execution_status is ProviderExecutionStatus.AUTH_ERROR


def test_ctrip_runtime_is_deterministic_for_same_offline_acquisition_result() -> None:
    provider = CtripFlightProvider(
        acquisition=lambda _: ctrip_result(BrowserProbeOutcome.SUCCESS_COMPLETE),
        id_factory=lambda: "ctrip-acq-deterministic",
    )

    first = provider.search(search_plan())
    second = provider.search(search_plan())

    assert first == second


def test_ctrip_runtime_source_keeps_architecture_boundary() -> None:
    provider_source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "adapters"
        / "flight_providers"
        / "ctrip"
        / "provider.py"
    ).read_text(encoding="utf-8")
    search_execution_source = (
        REPO_ROOT
        / "apps"
        / "backend"
        / "src"
        / "flight_agent"
        / "application"
        / "search_execution.py"
    ).read_text(encoding="utf-8")

    assert "from flight_agent.domain.flights" not in provider_source
    assert "CandidateMerger(" not in provider_source
    assert "CandidateSnapshotAssembler(" not in provider_source
    assert "requests" not in provider_source
    assert "httpx" not in provider_source
    assert "flight_agent.adapters.flight_providers.ctrip" not in search_execution_source


def test_search_plan_conversion_exposes_provider_local_query_without_shared_schema_change() -> None:
    probe_input = ctrip_probe_input_for_search_plan(search_plan(), overall_deadline_seconds=12.0)

    assert probe_input.origin_text == "PEK"
    assert probe_input.destination_text == "SHA"
    assert probe_input.departure_date == date(2026, 9, 14)
    assert probe_input.search_plan_id == "search-plan-ctrip-runtime"
    assert probe_input.overall_deadline_seconds == 12.0


def search_plan() -> SearchPlan:
    return SearchPlan(
        SearchPlanId("search-plan-ctrip-runtime"),
        RequirementId("requirement-ctrip-runtime"),
        RequirementVersion(1),
        requested_scope(),
    )


def requested_scope() -> RequestedSearchScope:
    return RequestedSearchScope(
        OriginScope(AirportCode("PEK")),
        DestinationScope(AirportCode("SHA")),
        DepartureDateScope(LocalDate(date(2026, 9, 14))),
    )


def ready_requirement() -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-ctrip-runtime"),
        version=RequirementVersion(1),
        predecessor_version=None,
        recorded_at=DomainInstant(datetime(2026, 9, 14, 0, 0, tzinfo=UTC)),
        constraints=(
            hard_constraint("origin", ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK")),
            hard_constraint("destination", ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA")),
            hard_constraint("departure-date", ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 14))),
        ),
    )


def hard_constraint(
    suffix: str,
    scope: ConstraintScope,
    value: AirportCode | LocalDate,
) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(f"constraint-{suffix}"),
        scope=scope,
        operator=ConstraintOperator.EQUALS,
        value=value,
    )


def ctrip_result(
    outcome: BrowserProbeOutcome,
    *,
    level1: tuple[object, ...] | None = None,
    level2: tuple[object, ...] | None = None,
    diagnostics: dict[str, object] | None = None,
    sanitized_source_ref: str | None = "https://flights.ctrip.com/online/channel/domestic",
) -> CtripProbeRunResult:
    level1_evidence = extract_level1_evidence_from_payloads(
        (
            CapturedPayload(
                "LEVEL1",
                "https://flights.ctrip.com/restapi/soa2/batchSearch",
                "batchSearch",
                {"data": {"flightItineraryList": list(default_level1_payloads() if level1 is None else level1)}},
            ),
        )
    )
    level2_evidence = extract_level2_offer_evidence(
        (
            CapturedPayload(
                "LEVEL2",
                "https://flights.ctrip.com/restapi/soa2/productPrice",
                "post_booking_offer_candidate",
                {"data": {"offerGroups": [{"products": list(default_level2_payloads() if level2 is None else level2)}]}},
            ),
        )
    )
    return CtripProbeRunResult(
        provider_identity="CTRIP",
        acquisition_mode=BrowserAcquisitionMode.BROWSER,
        acquired_at=datetime(2026, 9, 14, 1, 5, tzinfo=UTC),
        experiment_run_id=None,
        search_scope={
            "origin_text": "PEK",
            "destination_text": "SHA",
            "departure_date": "2026-09-14",
            "trip_type": "ONE_WAY",
        },
        search_plan_id="search-plan-ctrip-runtime",
        execution_id=None,
        outcome=outcome,
        observed_level1_count=len(level1_evidence),
        observed_level2_offer_count=len(level2_evidence),
        duration_ms=100,
        dom_traversal_assessment=DomTraversalAssessment.PARTIAL_OBSERVED,
        provider_market_completeness=ProviderMarketCompleteness.UNKNOWN_NOT_PROVEN,
        terminal_boundary_observed=False,
        terminal_boundary_evidence=None,
        parser_selector_probe_version="test-ctrip-runtime",
        sanitized_source_ref=sanitized_source_ref,
        level1_evidence=level1_evidence,
        level2_offer_evidence=level2_evidence,
        diagnostics=diagnostics or {"network_access": False, "browser_automation": False},
    )


def default_level1_payloads() -> tuple[object, ...]:
    return (
        {
            "itineraryId": "ctrip-itin-runtime",
            "flightSegments": [
                {
                    "flightList": [
                        {
                            "flightNo": "MU5100",
                            "marketAirlineCode": "MU",
                            "departureAirportCode": "PEK",
                            "arrivalAirportCode": "SHA",
                            "departureDateTime": "2026-09-14 07:00",
                            "arrivalDateTime": "2026-09-14 09:10",
                        }
                    ]
                }
            ],
        },
    )


def default_level2_payloads() -> tuple[object, ...]:
    return (
        {
            "productId": "product-runtime",
            "cabinName": "ECONOMY",
            "supplierName": "CTRIP",
            "adultPrice": 820,
            "bookingId": "booking-runtime",
        },
    )
