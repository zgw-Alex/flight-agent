from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId, RequirementState
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInvariantViolation, RequirementVersion


def requested_scope() -> RequestedSearchScope:
    return RequestedSearchScope(
        origin=OriginScope(AirportCode("PVG")),
        destination=DestinationScope(AirportCode("LAX")),
        departure_date=DepartureDateScope(LocalDate(date(2026, 9, 1))),
    )


def search_plan() -> SearchPlan:
    return SearchPlan(
        search_plan_id=SearchPlanId("search-plan-1"),
        requirement_id=RequirementId("requirement-1"),
        based_on_requirement_version=RequirementVersion(2),
        requested_scope=requested_scope(),
    )


def test_search_plan_is_immutable_provider_neutral_requested_scope() -> None:
    plan = search_plan()

    assert plan.search_plan_id == SearchPlanId("search-plan-1")
    assert plan.requested_scope.origin.airport == AirportCode("PVG")
    assert plan.requested_scope.destination.airport == AirportCode("LAX")
    assert plan.requested_scope.departure_date.departure_date == LocalDate(date(2026, 9, 1))
    assert not hasattr(plan, "provider_url")
    assert not hasattr(plan, "api_key")
    assert not hasattr(plan, "sdk_object")
    assert not hasattr(plan, "http_query")
    assert not hasattr(plan, "timeout")
    assert not hasattr(plan, "retry")
    with pytest.raises(FrozenInstanceError):
        plan.search_plan_id = SearchPlanId("other")  # type: ignore[misc]


def test_search_plan_preserves_requirement_lineage_without_becoming_requirement_state() -> None:
    plan = search_plan()

    assert plan.requirement_id == RequirementId("requirement-1")
    assert plan.based_on_requirement_version == RequirementVersion(2)
    assert not isinstance(plan, RequirementState)
    assert not hasattr(plan, "constraints")
    assert not hasattr(plan, "preferences")


def test_search_plan_identity_is_typed_and_scope_rejects_route_conflict() -> None:
    assert SearchPlanId("same-opaque") != RequirementId("same-opaque")

    with pytest.raises(DomainInvariantViolation):
        RequestedSearchScope(
            origin=OriginScope(AirportCode("PVG")),
            destination=DestinationScope(AirportCode("PVG")),
            departure_date=DepartureDateScope(LocalDate(date(2026, 9, 1))),
        )
