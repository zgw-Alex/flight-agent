"""Requirement contract primitives for M2-U2."""

from flight_agent.domain.requirements.constraints import (
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    PreferenceImportance,
    PreferenceScope,
    SoftPreference,
)
from flight_agent.domain.requirements.identity import ConstraintId, PreferenceId, RequirementId
from flight_agent.domain.requirements.patch import (
    ClearTarget,
    PatchOperation,
    PatchSet,
    PatchTarget,
    RequirementPatch,
)
from flight_agent.domain.requirements.state import RequirementState
from flight_agent.domain.requirements.values import (
    AirportCode,
    CabinClass,
    LocalDate,
    LocalTime,
    PassengerCount,
    RequirementValue,
    StopCount,
    ValueRange,
    ValueSet,
)

__all__ = [
    "AirportCode",
    "CabinClass",
    "ClearTarget",
    "ConstraintId",
    "ConstraintOperator",
    "ConstraintScope",
    "HardConstraint",
    "LocalDate",
    "LocalTime",
    "PassengerCount",
    "PatchOperation",
    "PatchSet",
    "PatchTarget",
    "PreferenceId",
    "PreferenceImportance",
    "PreferenceScope",
    "RequirementId",
    "RequirementPatch",
    "RequirementState",
    "RequirementValue",
    "SoftPreference",
    "StopCount",
    "ValueRange",
    "ValueSet",
]
