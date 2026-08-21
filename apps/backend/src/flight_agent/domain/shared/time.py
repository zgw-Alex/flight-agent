"""Timezone-aware instant primitive for domain system event time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from flight_agent.domain.shared.errors import DomainInvariantViolation


@dataclass(frozen=True)
class DomainInstant:
    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise DomainInvariantViolation("DomainInstant requires a timezone-aware datetime")
