from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from flight_agent.adapters.flight_providers.mock import (
    MockFlightProvider,
    MockFlightProviderFixtureError,
)
from flight_agent.domain.flights import CandidateSnapshot, FlightSegment, Itinerary, Offer
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import RequirementVersion
from flight_agent.ports import (
    CoverageCompleteness,
    FlightProvider,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"


def search_plan(
    origin: str,
    *,
    search_plan_id: str = "volatile-search-plan-id",
    departure: date = date(2026, 9, 1),
) -> SearchPlan:
    return SearchPlan(
        search_plan_id=SearchPlanId(search_plan_id),
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(4),
        requested_scope=RequestedSearchScope(
            origin=OriginScope(AirportCode(origin)),
            destination=DestinationScope(AirportCode("LAX")),
            departure_date=DepartureDateScope(LocalDate(departure)),
        ),
    )


def provider() -> MockFlightProvider:
    return MockFlightProvider(FIXTURE_PATH)


def test_mock_flight_provider_implements_flight_provider_port() -> None:
    flight_provider: FlightProvider = provider()

    result = flight_provider.search(search_plan("PVG"))

    assert isinstance(result, ProviderSearchResult)
    assert result.provider_id == ProviderId("mock-flight-provider")
    assert result.search_plan_id == SearchPlanId("volatile-search-plan-id")
    assert not hasattr(flight_provider, "http_client")
    assert not hasattr(flight_provider, "sdk")
    assert not hasattr(flight_provider, "api_key")


def test_semantic_search_plan_finds_expected_fixture_with_raw_evidence() -> None:
    result = provider().search(search_plan("PVG"))

    assert result.execution_status is ProviderExecutionStatus.SUCCESS
    assert result.data_status is ProviderDataStatus.COMPLETE
    assert result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert result.raw_evidence is not None
    assert result.raw_evidence.retrieved_at == datetime(2026, 8, 24, 1, 0, tzinfo=UTC)
    assert ("normal-success-response",) == result.raw_evidence.source_refs
    assert not isinstance(result.raw_evidence, CandidateSnapshot)
    assert not isinstance(result.raw_evidence.payload, FlightSegment | Itinerary | Offer)


def test_fixture_lookup_does_not_depend_on_search_plan_identity() -> None:
    first = provider().search(search_plan("PVG", search_plan_id="search-plan-a"))
    second = provider().search(search_plan("PVG", search_plan_id="search-plan-b"))

    assert first.acquisition_id == second.acquisition_id
    assert first.execution_status is second.execution_status
    assert first.data_status is second.data_status
    assert first.coverage == second.coverage
    assert first.raw_evidence is not None
    assert second.raw_evidence is not None
    assert first.raw_evidence.payload == second.raw_evidence.payload
    assert first.search_plan_id == SearchPlanId("search-plan-a")
    assert second.search_plan_id == SearchPlanId("search-plan-b")


def test_same_semantic_scope_and_fixture_set_is_deterministic() -> None:
    first = provider().search(search_plan("PVG"))
    second = provider().search(search_plan("PVG"))

    assert first == second


def test_legitimate_empty_requires_success_and_known_coverage() -> None:
    result = provider().search(search_plan("PEK"))

    assert result.execution_status is ProviderExecutionStatus.SUCCESS
    assert result.data_status is ProviderDataStatus.EMPTY
    assert result.coverage.actual_scope == result.coverage.requested_scope
    assert result.coverage.completeness is CoverageCompleteness.COMPLETE
    assert result.raw_evidence is not None


@pytest.mark.parametrize(
    ("origin", "expected_status"),
    [
        ("SHA", ProviderExecutionStatus.TIMEOUT),
        ("CAN", ProviderExecutionStatus.RATE_LIMITED),
        ("SZX", ProviderExecutionStatus.AUTH_ERROR),
        ("CTU", ProviderExecutionStatus.UPSTREAM_ERROR),
    ],
)
def test_external_failures_are_structured_results_not_empty(
    origin: str,
    expected_status: ProviderExecutionStatus,
) -> None:
    result = provider().search(search_plan(origin))

    assert result.execution_status is expected_status
    assert result.data_status is ProviderDataStatus.UNKNOWN
    assert result.coverage.completeness is CoverageCompleteness.UNKNOWN
    assert result.raw_evidence is None


def test_invalid_response_is_top_level_envelope_failure_not_mapper_partial() -> None:
    result = provider().search(search_plan("HGH"))

    assert result.execution_status is ProviderExecutionStatus.INVALID_RESPONSE
    assert result.data_status is ProviderDataStatus.UNUSABLE
    assert result.coverage.completeness is CoverageCompleteness.UNKNOWN
    assert result.raw_evidence is not None


def test_success_does_not_imply_complete_coverage() -> None:
    result = provider().search(search_plan("NKG"))

    assert result.execution_status is ProviderExecutionStatus.SUCCESS
    assert result.data_status is ProviderDataStatus.COMPLETE
    assert result.coverage.completeness is CoverageCompleteness.PARTIAL
    assert len(result.coverage.limitations) == 1


def test_unknown_semantic_scope_fails_explicitly_instead_of_empty() -> None:
    with pytest.raises(MockFlightProviderFixtureError, match="No mock provider fixture"):
        provider().search(search_plan("WUH"))


def test_malformed_fixture_fails_explicitly(tmp_path: Path) -> None:
    fixture_path = tmp_path / "malformed.json"
    fixture_path.write_text(
        json.dumps(
            {
                "fixture_schema_version": "m4-u2-v1",
                "provider_id": "mock-flight-provider",
                "cases": [
                    {
                        "case_id": "bad",
                        "match": {
                            "origin": "PVG",
                            "destination": "LAX",
                            "departure_date": "2026-09-01",
                        },
                        "acquisition_id": "bad-acquisition",
                        "execution_status": "NOT_A_STATUS",
                        "data_status": "UNKNOWN",
                        "coverage": {"actual_scope": None, "completeness": "UNKNOWN"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MockFlightProviderFixtureError, match="case is malformed"):
        MockFlightProvider(fixture_path)


def test_duplicate_case_id_fails_explicitly(tmp_path: Path) -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["cases"] = [payload["cases"][0], payload["cases"][0]]
    fixture_path = tmp_path / "duplicate.json"
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MockFlightProviderFixtureError, match="Duplicate mock provider case_id"):
        MockFlightProvider(fixture_path)


def test_raw_evidence_is_immutable_after_acquisition() -> None:
    result = provider().search(search_plan("PVG"))

    assert isinstance(result.raw_evidence, ProviderRawEvidence)
    with pytest.raises(FrozenInstanceError):
        result.raw_evidence.payload = ()  # type: ignore[misc]
