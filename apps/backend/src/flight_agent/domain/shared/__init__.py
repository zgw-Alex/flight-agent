"""Shared domain primitives for M2 core domain contracts."""

from flight_agent.domain.shared.domain_value import DomainValue, ValueState
from flight_agent.domain.shared.errors import DomainInvariantViolation
from flight_agent.domain.shared.freshness import (
    FreshnessState,
    OfferFreshness,
    StructuralFreshness,
)
from flight_agent.domain.shared.identity import DomainId, RequirementVersion, SnapshotVersion
from flight_agent.domain.shared.provenance import ProvenanceRef
from flight_agent.domain.shared.time import DomainInstant

__all__ = [
    "DomainId",
    "DomainInstant",
    "DomainInvariantViolation",
    "DomainValue",
    "FreshnessState",
    "OfferFreshness",
    "ProvenanceRef",
    "RequirementVersion",
    "SnapshotVersion",
    "StructuralFreshness",
    "ValueState",
]
