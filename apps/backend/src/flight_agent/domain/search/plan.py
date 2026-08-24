"""Immutable provider-neutral SearchPlan contract."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.requirements import AirportCode, LocalDate, RequirementId
from flight_agent.domain.search.identity import SearchPlanId
from flight_agent.domain.shared import DomainInvariantViolation, RequirementVersion


@dataclass(frozen=True)
class OriginScope:
    airport: AirportCode


@dataclass(frozen=True)
class DestinationScope:
    airport: AirportCode


@dataclass(frozen=True)
class DepartureDateScope:
    departure_date: LocalDate


@dataclass(frozen=True, init=False)
class RequestedSearchScope:
    origin: OriginScope
    destination: DestinationScope
    departure_date: DepartureDateScope

    def __init__(
        self,
        origin: OriginScope,
        destination: DestinationScope,
        departure_date: DepartureDateScope,
    ) -> None:
        if origin.airport == destination.airport:
            raise DomainInvariantViolation("SearchPlan origin and destination must differ")
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "departure_date", departure_date)


@dataclass(frozen=True, init=False)
class SearchPlan:
    """Provider-neutral requested search scope derived from a RequirementState."""

    search_plan_id: SearchPlanId
    requirement_id: RequirementId
    based_on_requirement_version: RequirementVersion
    requested_scope: RequestedSearchScope

    def __init__(
        self,
        search_plan_id: SearchPlanId,
        requirement_id: RequirementId,
        based_on_requirement_version: RequirementVersion,
        requested_scope: RequestedSearchScope,
    ) -> None:
        object.__setattr__(self, "search_plan_id", search_plan_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "based_on_requirement_version", based_on_requirement_version)
        object.__setattr__(self, "requested_scope", requested_scope)
