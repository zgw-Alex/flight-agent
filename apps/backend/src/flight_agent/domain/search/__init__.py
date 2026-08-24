"""Provider-neutral search planning contract for M4-U1."""

from flight_agent.domain.search.identity import SearchPlanId
from flight_agent.domain.search.plan import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
)

__all__ = [
    "DepartureDateScope",
    "DestinationScope",
    "OriginScope",
    "RequestedSearchScope",
    "SearchPlan",
    "SearchPlanId",
]
