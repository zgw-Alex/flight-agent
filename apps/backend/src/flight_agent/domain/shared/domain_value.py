"""Four-state optional value semantics for domain facts."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import cast

from flight_agent.domain.shared.errors import DomainInvariantViolation


class ValueState(str, Enum):
    """Explicit missing-value states used by domain value objects."""

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_PROVIDED = "NOT_PROVIDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class _Unset:
    pass


_UNSET = _Unset()


@dataclass(frozen=True, init=False)
class DomainValue[T]:
    """A domain fact with explicit missing-value semantics.

    Non-KNOWN states intentionally do not expose a value, which prevents callers
    from silently substituting all missing semantics with None.
    """

    state: ValueState
    _value: T | _Unset = field(default=_UNSET, repr=False, compare=True)

    def __init__(self, state: ValueState, value: T | _Unset = _UNSET) -> None:
        if state is ValueState.KNOWN:
            if isinstance(value, _Unset) or _is_missing_business_value(value):
                raise DomainInvariantViolation("KNOWN DomainValue requires a non-empty value")
        elif not isinstance(value, _Unset):
            raise DomainInvariantViolation(f"{state.value} DomainValue must not carry a value")

        object.__setattr__(self, "state", state)
        object.__setattr__(self, "_value", value)

    @classmethod
    def known(cls, value: T) -> DomainValue[T]:
        return cls(ValueState.KNOWN, value)

    @classmethod
    def unknown(cls) -> DomainValue[T]:
        return cls(ValueState.UNKNOWN)

    @classmethod
    def not_provided(cls) -> DomainValue[T]:
        return cls(ValueState.NOT_PROVIDED)

    @classmethod
    def not_applicable(cls) -> DomainValue[T]:
        return cls(ValueState.NOT_APPLICABLE)

    @property
    def is_known(self) -> bool:
        return self.state is ValueState.KNOWN

    @property
    def value(self) -> T:
        if not self.is_known:
            raise DomainInvariantViolation(f"{self.state.value} DomainValue has no business value")
        return cast(T, self._value)


def _is_missing_business_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str | bytes):
        return len(value.strip() if isinstance(value, str) else value) == 0
    if isinstance(value, Mapping):
        return len(value) == 0
    if isinstance(value, Collection) and not isinstance(value, str | bytes):
        return len(value) == 0
    return False
