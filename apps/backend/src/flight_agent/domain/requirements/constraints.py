"""Hard constraint and soft preference contract objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.requirements.identity import ConstraintId, PreferenceId
from flight_agent.domain.requirements.values import (
    AirportCode,
    CabinClass,
    LocalDate,
    LocalTime,
    PassengerCount,
    RequirementValue,
    ValueRange,
    ValueSet,
)
from flight_agent.domain.shared import DomainInvariantViolation


class ConstraintScope(str, Enum):
    ORIGIN_AIRPORT = "ORIGIN_AIRPORT"
    DESTINATION_AIRPORT = "DESTINATION_AIRPORT"
    DEPARTURE_DATE = "DEPARTURE_DATE"
    DEPARTURE_TIME = "DEPARTURE_TIME"
    CABIN_CLASS = "CABIN_CLASS"
    PASSENGER_COUNT = "PASSENGER_COUNT"


class ConstraintOperator(str, Enum):
    EQUALS = "EQUALS"
    IN = "IN"
    NOT_IN = "NOT_IN"
    AT_OR_AFTER = "AT_OR_AFTER"
    AT_OR_BEFORE = "AT_OR_BEFORE"
    BETWEEN = "BETWEEN"


class PreferenceScope(str, Enum):
    PRICE = "PRICE"
    DEPARTURE_TIME = "DEPARTURE_TIME"
    ARRIVAL_TIME = "ARRIVAL_TIME"
    AIRPORT_MATCH = "AIRPORT_MATCH"
    FEWER_STOPS = "FEWER_STOPS"


class PreferenceImportance(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


ConstraintExpression = RequirementValue | ValueSet | ValueRange
PreferenceValue = RequirementValue | ValueRange


@dataclass(frozen=True)
class HardConstraint:
    constraint_id: ConstraintId
    scope: ConstraintScope
    operator: ConstraintOperator
    value: ConstraintExpression

    def __post_init__(self) -> None:
        _validate_operator_value(self.operator, self.value)
        _validate_constraint_scope_value(self.scope, self.value)


@dataclass(frozen=True)
class SoftPreference:
    preference_id: PreferenceId
    scope: PreferenceScope
    importance: PreferenceImportance
    value: PreferenceValue | None = None

    def __post_init__(self) -> None:
        if self.value is not None:
            _validate_preference_scope_value(self.scope, self.value)


def _validate_operator_value(operator: ConstraintOperator, value: ConstraintExpression) -> None:
    if operator in {
        ConstraintOperator.EQUALS,
        ConstraintOperator.AT_OR_AFTER,
        ConstraintOperator.AT_OR_BEFORE,
    }:
        if not isinstance(value, RequirementValue):
            raise DomainInvariantViolation(f"{operator.value} requires a single typed value")
    elif operator in {ConstraintOperator.IN, ConstraintOperator.NOT_IN}:
        if not isinstance(value, ValueSet):
            raise DomainInvariantViolation(f"{operator.value} requires a typed value set")
    elif operator is ConstraintOperator.BETWEEN and not isinstance(value, ValueRange):
        raise DomainInvariantViolation("BETWEEN requires a typed value range")


def _validate_constraint_scope_value(scope: ConstraintScope, value: ConstraintExpression) -> None:
    values = value.items if isinstance(value, ValueSet) else (value.start, value.end) if isinstance(value, ValueRange) else (value,)
    allowed = _CONSTRAINT_ALLOWED_VALUE_TYPES[scope]
    if not all(isinstance(item, allowed) for item in values):
        raise DomainInvariantViolation(f"{scope.value} constraint received an incompatible value type")


def _validate_preference_scope_value(scope: PreferenceScope, value: PreferenceValue) -> None:
    values = (value.start, value.end) if isinstance(value, ValueRange) else (value,)
    allowed = _PREFERENCE_ALLOWED_VALUE_TYPES[scope]
    if not all(isinstance(item, allowed) for item in values):
        raise DomainInvariantViolation(f"{scope.value} preference received an incompatible value type")


_CONSTRAINT_ALLOWED_VALUE_TYPES = {
    ConstraintScope.ORIGIN_AIRPORT: (AirportCode,),
    ConstraintScope.DESTINATION_AIRPORT: (AirportCode,),
    ConstraintScope.DEPARTURE_DATE: (LocalDate,),
    ConstraintScope.DEPARTURE_TIME: (LocalTime,),
    ConstraintScope.CABIN_CLASS: (CabinClass,),
    ConstraintScope.PASSENGER_COUNT: (PassengerCount,),
}

_PREFERENCE_ALLOWED_VALUE_TYPES = {
    PreferenceScope.PRICE: (PassengerCount,),
    PreferenceScope.DEPARTURE_TIME: (LocalTime,),
    PreferenceScope.ARRIVAL_TIME: (LocalTime,),
    PreferenceScope.AIRPORT_MATCH: (AirportCode,),
    PreferenceScope.FEWER_STOPS: (PassengerCount,),
}
