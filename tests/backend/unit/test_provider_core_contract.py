from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from flight_agent.domain.flights import CandidateSnapshot
from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInvariantViolation, RequirementVersion
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


def scope(
    origin: str = "PVG",
    destination: str = "LAX",
    departure: date = date(2026, 9, 1),
) -> RequestedSearchScope:
    return RequestedSearchScope(
        OriginScope(AirportCode(origin)),
        DestinationScope(AirportCode(destination)),
        DepartureDateScope(LocalDate(departure)),
    )


def plan() -> SearchPlan:
    return SearchPlan(
        SearchPlanId("search-plan-1"),
        RequirementId("requirement-1"),
        RequirementVersion(3),
        scope(),
    )


def complete_coverage() -> ProviderCoverage:
    requested = scope()
    return ProviderCoverage(
        requested_scope=requested,
        actual_scope=requested,
        completeness=CoverageCompleteness.COMPLETE,
    )


def partial_coverage() -> ProviderCoverage:
    return ProviderCoverage(
        requested_scope=scope(),
        actual_scope=scope(origin="SHA"),
        completeness=CoverageCompleteness.PARTIAL,
        limitations=(
            ProviderCoverageLimitation(
                code="PROVIDER_SCOPE_LIMIT",
                detail="Provider only searched a substituted origin airport",
            ),
        ),
    )


def result(
    *,
    execution_status: ProviderExecutionStatus = ProviderExecutionStatus.SUCCESS,
    data_status: ProviderDataStatus = ProviderDataStatus.COMPLETE,
    coverage: ProviderCoverage | None = None,
    raw_evidence: ProviderRawEvidence | None = None,
) -> ProviderSearchResult:
    return ProviderSearchResult.for_search_plan(
        provider_id=ProviderId("fixture-provider"),
        acquisition_id=ProviderAcquisitionId("acquisition-1"),
        search_plan=plan(),
        execution_status=execution_status,
        data_status=data_status,
        coverage=coverage or complete_coverage(),
        raw_evidence=raw_evidence,
    )


def test_provider_statuses_are_three_independent_dimensions() -> None:
    provider_result = result(
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.COMPLETE,
        coverage=partial_coverage(),
    )

    assert provider_result.execution_status is ProviderExecutionStatus.SUCCESS
    assert provider_result.data_status is ProviderDataStatus.COMPLETE
    assert provider_result.coverage.completeness is CoverageCompleteness.PARTIAL
    assert provider_result.coverage.requested_scope != provider_result.coverage.actual_scope


def test_data_partial_and_coverage_complete_are_representable() -> None:
    provider_result = result(
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.PARTIAL,
        coverage=complete_coverage(),
    )

    assert provider_result.data_status is ProviderDataStatus.PARTIAL
    assert provider_result.coverage.completeness is CoverageCompleteness.COMPLETE


def test_success_does_not_imply_complete_coverage_or_non_empty_data() -> None:
    empty = result(
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.EMPTY,
        coverage=complete_coverage(),
    )
    partial = result(
        execution_status=ProviderExecutionStatus.SUCCESS,
        data_status=ProviderDataStatus.COMPLETE,
        coverage=partial_coverage(),
    )

    assert empty.data_status is ProviderDataStatus.EMPTY
    assert partial.coverage.completeness is CoverageCompleteness.PARTIAL


@pytest.mark.parametrize(
    "execution_status",
    [
        ProviderExecutionStatus.TIMEOUT,
        ProviderExecutionStatus.RATE_LIMITED,
        ProviderExecutionStatus.AUTH_ERROR,
        ProviderExecutionStatus.UPSTREAM_ERROR,
        ProviderExecutionStatus.INVALID_RESPONSE,
    ],
)
def test_external_failures_are_not_encoded_as_empty_data(
    execution_status: ProviderExecutionStatus,
) -> None:
    with pytest.raises(DomainInvariantViolation):
        result(execution_status=execution_status, data_status=ProviderDataStatus.EMPTY)


def test_provider_search_result_preserves_provider_identity_and_search_plan_lineage() -> None:
    provider_result = result(
        execution_status=ProviderExecutionStatus.TIMEOUT,
        data_status=ProviderDataStatus.UNKNOWN,
        coverage=ProviderCoverage(scope(), actual_scope=None, completeness=CoverageCompleteness.UNKNOWN),
    )

    assert provider_result.provider_id == ProviderId("fixture-provider")
    assert provider_result.acquisition_id == ProviderAcquisitionId("acquisition-1")
    assert provider_result.search_plan_id == SearchPlanId("search-plan-1")
    assert provider_result.requirement_id == RequirementId("requirement-1")
    assert provider_result.based_on_requirement_version == RequirementVersion(3)
    assert not isinstance(provider_result, CandidateSnapshot)
    assert not hasattr(provider_result, "segments")
    assert not hasattr(provider_result, "offers")
    with pytest.raises(FrozenInstanceError):
        provider_result.data_status = ProviderDataStatus.COMPLETE  # type: ignore[misc]


def test_provider_raw_evidence_is_immutable_and_provider_shaped() -> None:
    raw_evidence = ProviderRawEvidence(
        provider_id=ProviderId("fixture-provider"),
        acquisition_id=ProviderAcquisitionId("acquisition-1"),
        search_plan_id=SearchPlanId("search-plan-1"),
        retrieved_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        payload={
            "provider_itineraries": [
                {
                    "provider_itinerary_id": "provider-itinerary-1",
                    "provider_offer_ids": ["provider-offer-1"],
                }
            ]
        },
        source_refs=("provider-response-1",),
    )
    provider_result = result(raw_evidence=raw_evidence)

    assert provider_result.raw_evidence == raw_evidence
    assert provider_result.raw_evidence is not None
    assert provider_result.raw_evidence.payload == (
        (
            "provider_itineraries",
            (
                (
                    (
                        "provider_itinerary_id",
                        "provider-itinerary-1",
                    ),
                    ("provider_offer_ids", ("provider-offer-1",)),
                ),
            ),
        ),
    )
    assert not hasattr(raw_evidence, "canonical_itinerary_id")
    assert not hasattr(raw_evidence, "segments")
    assert not isinstance(raw_evidence, CandidateSnapshot)
    with pytest.raises(FrozenInstanceError):
        raw_evidence.payload = ()  # type: ignore[misc]


def test_provider_search_result_rejects_raw_evidence_lineage_mismatch() -> None:
    raw_evidence = ProviderRawEvidence(
        provider_id=ProviderId("other-provider"),
        acquisition_id=ProviderAcquisitionId("acquisition-1"),
        search_plan_id=SearchPlanId("search-plan-1"),
        retrieved_at=datetime(2026, 8, 24, 1, 0, tzinfo=UTC),
        payload={"provider_status": "ok"},
    )

    with pytest.raises(DomainInvariantViolation):
        result(raw_evidence=raw_evidence)


def test_empty_data_requires_known_coverage_evidence() -> None:
    with pytest.raises(DomainInvariantViolation):
        result(
            execution_status=ProviderExecutionStatus.SUCCESS,
            data_status=ProviderDataStatus.EMPTY,
            coverage=ProviderCoverage(scope(), actual_scope=None, completeness=CoverageCompleteness.UNKNOWN),
        )


def test_flight_provider_port_shape_accepts_search_plan_and_returns_provider_result() -> None:
    class DummyProvider:
        def search(self, search_plan: SearchPlan) -> ProviderSearchResult:
            return ProviderSearchResult.for_search_plan(
                provider_id=ProviderId("dummy"),
                acquisition_id=ProviderAcquisitionId("dummy-acquisition"),
                search_plan=search_plan,
                execution_status=ProviderExecutionStatus.SUCCESS,
                data_status=ProviderDataStatus.COMPLETE,
                coverage=complete_coverage(),
            )

    provider: FlightProvider = DummyProvider()
    provider_result = provider.search(plan())

    assert isinstance(provider_result, ProviderSearchResult)
    assert provider_result.search_plan_id == SearchPlanId("search-plan-1")
    assert not hasattr(provider, "http_client")
