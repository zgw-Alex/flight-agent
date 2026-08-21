"""Typed opaque identity and version primitives."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.shared.errors import DomainInvariantViolation


@dataclass(frozen=True)
class DomainId:
    """Base for typed opaque domain identifiers.

    Subclasses provide the type boundary. Dataclass equality keeps different
    subclasses from comparing equal even if their opaque value matches.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or self.value.strip() == "":
            raise DomainInvariantViolation("DomainId requires a non-empty opaque string")


@dataclass(frozen=True)
class _PositiveVersion:
    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1:
            raise DomainInvariantViolation("Version requires a positive integer")


@dataclass(frozen=True)
class RequirementVersion(_PositiveVersion):
    """Version for Requirement aggregate snapshots."""


@dataclass(frozen=True)
class SnapshotVersion(_PositiveVersion):
    """Version for CandidateSnapshot snapshots."""
