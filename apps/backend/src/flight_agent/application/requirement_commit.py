"""Application commit sequencing helpers for RequirementState transitions."""

from __future__ import annotations

from flight_agent.domain.requirements import RequirementState
from flight_agent.ports import RequirementCommitResult, RequirementRepository


def commit_requirement_transition(
    repository: RequirementRepository,
    current: RequirementState,
    candidate: RequirementState,
    operation_id: str,
) -> RequirementCommitResult:
    if candidate == current:
        return RequirementCommitResult.no_change(current)
    return repository.commit_next(
        candidate,
        expected_current_version=current.version,
        operation_id=operation_id,
    )
