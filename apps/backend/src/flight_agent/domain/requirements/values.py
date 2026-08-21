"""Typed requirement value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from flight_agent.domain.shared import DomainInvariantViolation


@dataclass(frozen=True)
class AirportCode:
    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 3 or not self.value.isascii() or not self.value.isalpha():
            raise DomainInvariantViolation("AirportCode requires a three-letter IATA-style code")
        object.__setattr__(self, "value", self.value.upper())


@dataclass(frozen=True)
class CabinClass:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().upper()
        if normalized not in {"ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"}:
            raise DomainInvariantViolation("CabinClass value is not supported structurally")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True)
class LocalDate:
    value: date


@dataclass(frozen=True)
class LocalTime:
    value: time

    def __post_init__(self) -> None:
        if self.value.tzinfo is not None:
            raise DomainInvariantViolation("LocalTime requirement value must not carry timezone")


@dataclass(frozen=True)
class PassengerCount:
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1:
            raise DomainInvariantViolation("PassengerCount requires a positive integer")


RequirementValue = AirportCode | CabinClass | LocalDate | LocalTime | PassengerCount


@dataclass(frozen=True, init=False)
class ValueSet:
    items: tuple[RequirementValue, ...]

    def __init__(self, items: tuple[RequirementValue, ...]) -> None:
        items_tuple = tuple(items)
        if len(items_tuple) == 0:
            raise DomainInvariantViolation("ValueSet requires at least one item")
        if len(set(items_tuple)) != len(items_tuple):
            raise DomainInvariantViolation("ValueSet items must be unique")
        first_type = type(items_tuple[0])
        if any(type(item) is not first_type for item in items_tuple):
            raise DomainInvariantViolation("ValueSet requires values of one typed value family")
        object.__setattr__(self, "items", items_tuple)


@dataclass(frozen=True)
class ValueRange:
    start: RequirementValue
    end: RequirementValue

    def __post_init__(self) -> None:
        if type(self.start) is not type(self.end):
            raise DomainInvariantViolation("ValueRange endpoints must use one typed value family")
        if self.start == self.end:
            raise DomainInvariantViolation("ValueRange endpoints must not be equal")
