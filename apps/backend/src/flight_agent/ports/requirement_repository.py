"""Requirement authority repository port for M3-U3."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from flight_agent.domain.requirements import RequirementId, RequirementState
from flight_agent.domain.shared import RequirementVersion


class CommitStatus(str, Enum):
    COMMITTED = "COMMITTED"
    REPLAYED = "REPLAYED"
    NO_CHANGE = "NO_CHANGE"
    REJECTED = "REJECTED"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"


@dataclass(frozen=True)
class RequirementCommitResult:
    status: CommitStatus
    requirement: RequirementState | None = None
    message: str = ""

    @classmethod
    def committed(cls, requirement: RequirementState) -> RequirementCommitResult:
        return cls(status=CommitStatus.COMMITTED, requirement=requirement)

    @classmethod
    def replayed(cls, requirement: RequirementState) -> RequirementCommitResult:
        return cls(status=CommitStatus.REPLAYED, requirement=requirement)

    @classmethod
    def no_change(cls, requirement: RequirementState) -> RequirementCommitResult:
        return cls(status=CommitStatus.NO_CHANGE, requirement=requirement)

    @classmethod
    def rejected(cls, message: str) -> RequirementCommitResult:
        return cls(status=CommitStatus.REJECTED, message=message)

    @classmethod
    def concurrency_conflict(cls, message: str) -> RequirementCommitResult:
        return cls(status=CommitStatus.CONCURRENCY_CONFLICT, message=message)


class RequirementRepository(Protocol):
    def commit_initial(
        self,
        requirement: RequirementState,
        operation_id: str,
    ) -> RequirementCommitResult:
        """Commit an initial v1 RequirementState if the aggregate does not exist."""
        ...

    def commit_next(
        self,
        requirement: RequirementState,
        expected_current_version: RequirementVersion,
        operation_id: str,
    ) -> RequirementCommitResult:
        """Commit the direct next RequirementState using expected-current CAS."""
        ...

    def get_current(self, requirement_id: RequirementId) -> RequirementState | None:
        """Return the explicit current pointer for a requirement chain."""
        ...

    def get_version(
        self,
        requirement_id: RequirementId,
        version: RequirementVersion,
    ) -> RequirementState | None:
        """Return a historical immutable version if present."""
        ...

    def history(self, requirement_id: RequirementId) -> tuple[RequirementState, ...]:
        """Return all committed versions in ascending version order."""
        ...
