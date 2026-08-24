"""Fixture-driven mock implementation of the FlightProvider port."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from flight_agent.domain.requirements import AirportCode, LocalDate
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
)
from flight_agent.domain.shared import DomainInvariantViolation
from flight_agent.ports import (
    CoverageCompleteness,
    FlightProvider,
    ProviderAcquisitionId,
    ProviderCoverage,
    ProviderCoverageLimitation,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderId,
    ProviderRawEvidence,
    ProviderSearchResult,
)


class MockFlightProviderFixtureError(RuntimeError):
    """Raised for fixture lookup, parsing, or configuration failures."""


@dataclass(frozen=True)
class _SemanticScopeKey:
    origin: str
    destination: str
    departure_date: str

    @classmethod
    def from_search_plan(cls, search_plan: SearchPlan) -> _SemanticScopeKey:
        scope = search_plan.requested_scope
        return cls(
            origin=scope.origin.airport.value,
            destination=scope.destination.airport.value,
            departure_date=scope.departure_date.departure_date.value.isoformat(),
        )


@dataclass(frozen=True)
class _FixtureCase:
    case_id: str
    match: _SemanticScopeKey
    acquisition_id: ProviderAcquisitionId
    retrieved_at: datetime | None
    execution_status: ProviderExecutionStatus
    data_status: ProviderDataStatus
    coverage: dict[str, Any]
    raw_payload: object | None
    raw_source_refs: tuple[str, ...]


@dataclass(frozen=True)
class _FixtureSet:
    provider_id: ProviderId
    fixture_schema_version: str
    cases: tuple[_FixtureCase, ...]


class MockFlightProvider(FlightProvider):
    """Network-free provider double backed by stable semantic fixtures."""

    def __init__(self, fixture_path: Path | str) -> None:
        self._fixture_path = Path(fixture_path)
        self._fixture_set = _load_fixture_set(self._fixture_path)

    @property
    def provider_id(self) -> ProviderId:
        return self._fixture_set.provider_id

    def search(self, search_plan: SearchPlan) -> ProviderSearchResult:
        fixture_case = self._find_case(search_plan)
        coverage = _coverage_from_fixture(fixture_case.coverage, search_plan.requested_scope)
        raw_evidence = self._raw_evidence_from_case(fixture_case, search_plan)
        return ProviderSearchResult.for_search_plan(
            provider_id=self.provider_id,
            acquisition_id=fixture_case.acquisition_id,
            search_plan=search_plan,
            execution_status=fixture_case.execution_status,
            data_status=fixture_case.data_status,
            coverage=coverage,
            raw_evidence=raw_evidence,
        )

    def _find_case(self, search_plan: SearchPlan) -> _FixtureCase:
        match = _SemanticScopeKey.from_search_plan(search_plan)
        matches = [case for case in self._fixture_set.cases if case.match == match]
        if len(matches) == 0:
            raise MockFlightProviderFixtureError(
                "No mock provider fixture matches SearchPlan semantic scope"
            )
        if len(matches) > 1:
            raise MockFlightProviderFixtureError(
                "Multiple mock provider fixtures match SearchPlan semantic scope"
            )
        return matches[0]

    def _raw_evidence_from_case(
        self,
        fixture_case: _FixtureCase,
        search_plan: SearchPlan,
    ) -> ProviderRawEvidence | None:
        if fixture_case.raw_payload is None:
            return None
        if fixture_case.retrieved_at is None:
            raise MockFlightProviderFixtureError("Raw evidence fixture requires retrieved_at")
        return ProviderRawEvidence(
            provider_id=self.provider_id,
            acquisition_id=fixture_case.acquisition_id,
            search_plan_id=search_plan.search_plan_id,
            retrieved_at=fixture_case.retrieved_at,
            payload=fixture_case.raw_payload,
            source_refs=fixture_case.raw_source_refs,
        )


def _load_fixture_set(path: Path) -> _FixtureSet:
    try:
        with path.open("r", encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
    except OSError as exc:
        raise MockFlightProviderFixtureError(f"Cannot read mock provider fixture: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MockFlightProviderFixtureError(f"Invalid mock provider fixture JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MockFlightProviderFixtureError("Mock provider fixture root must be an object")

    try:
        provider_id = ProviderId(_required_str(payload, "provider_id"))
        fixture_schema_version = _required_str(payload, "fixture_schema_version")
        cases_payload = payload["cases"]
    except (KeyError, DomainInvariantViolation) as exc:
        raise MockFlightProviderFixtureError("Mock provider fixture metadata is malformed") from exc
    if not isinstance(cases_payload, list) or len(cases_payload) == 0:
        raise MockFlightProviderFixtureError("Mock provider fixture requires non-empty cases")

    seen_case_ids: set[str] = set()
    cases: list[_FixtureCase] = []
    for case_payload in cases_payload:
        case = _parse_case(case_payload)
        if case.case_id in seen_case_ids:
            raise MockFlightProviderFixtureError(f"Duplicate mock provider case_id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        cases.append(case)

    return _FixtureSet(
        provider_id=provider_id,
        fixture_schema_version=fixture_schema_version,
        cases=tuple(cases),
    )


def _parse_case(payload: object) -> _FixtureCase:
    if not isinstance(payload, dict):
        raise MockFlightProviderFixtureError("Mock provider case must be an object")
    try:
        return _FixtureCase(
            case_id=_required_str(payload, "case_id"),
            match=_scope_key_from_payload(payload["match"]),
            acquisition_id=ProviderAcquisitionId(_required_str(payload, "acquisition_id")),
            retrieved_at=_optional_datetime(payload.get("retrieved_at")),
            execution_status=ProviderExecutionStatus(_required_str(payload, "execution_status")),
            data_status=ProviderDataStatus(_required_str(payload, "data_status")),
            coverage=_required_dict(payload, "coverage"),
            raw_payload=payload.get("raw_payload"),
            raw_source_refs=tuple(payload.get("raw_source_refs", ())),
        )
    except (KeyError, ValueError, TypeError, DomainInvariantViolation) as exc:
        raise MockFlightProviderFixtureError("Mock provider case is malformed") from exc


def _coverage_from_fixture(payload: dict[str, Any], requested_scope: RequestedSearchScope) -> ProviderCoverage:
    try:
        completeness = CoverageCompleteness(_required_str(payload, "completeness"))
        actual_payload = payload.get("actual_scope")
        limitations_payload = payload.get("limitations", [])
    except ValueError as exc:
        raise MockFlightProviderFixtureError("Mock provider coverage is malformed") from exc

    if actual_payload is None:
        actual_scope = None
    else:
        actual_scope = _requested_scope_from_key(_scope_key_from_payload(actual_payload))

    if not isinstance(limitations_payload, list):
        raise MockFlightProviderFixtureError("Mock provider coverage limitations must be a list")
    limitations = tuple(_coverage_limitation(item) for item in limitations_payload)
    try:
        return ProviderCoverage(
            requested_scope=requested_scope,
            actual_scope=actual_scope,
            completeness=completeness,
            limitations=limitations,
        )
    except DomainInvariantViolation as exc:
        raise MockFlightProviderFixtureError("Mock provider coverage violates contract") from exc


def _coverage_limitation(payload: object) -> ProviderCoverageLimitation:
    if not isinstance(payload, dict):
        raise MockFlightProviderFixtureError("Mock provider coverage limitation must be an object")
    try:
        return ProviderCoverageLimitation(
            code=_required_str(payload, "code"),
            detail=_required_str(payload, "detail"),
        )
    except DomainInvariantViolation as exc:
        raise MockFlightProviderFixtureError("Mock provider coverage limitation is malformed") from exc


def _scope_key_from_payload(payload: object) -> _SemanticScopeKey:
    if not isinstance(payload, dict):
        raise MockFlightProviderFixtureError("Mock provider semantic match must be an object")
    return _SemanticScopeKey(
        origin=_required_str(payload, "origin").upper(),
        destination=_required_str(payload, "destination").upper(),
        departure_date=_required_str(payload, "departure_date"),
    )


def _requested_scope_from_key(key: _SemanticScopeKey) -> RequestedSearchScope:
    return RequestedSearchScope(
        origin=OriginScope(AirportCode(key.origin)),
        destination=DestinationScope(AirportCode(key.destination)),
        departure_date=DepartureDateScope(LocalDate(datetime.fromisoformat(key.departure_date).date())),
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MockFlightProviderFixtureError("retrieved_at must be an ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise MockFlightProviderFixtureError("retrieved_at must be timezone-aware")
    return parsed


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or value.strip() == "":
        raise MockFlightProviderFixtureError(f"Mock provider fixture field {key} must be a string")
    return value


def _required_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload[key]
    if not isinstance(value, dict):
        raise MockFlightProviderFixtureError(f"Mock provider fixture field {key} must be an object")
    return value
