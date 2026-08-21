"""Workflow state and agent execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights import CandidateSnapshotId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow.identity import ExecutionId


class WorkflowState(str, Enum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    REQUIREMENT_CONFLICT = "REQUIREMENT_CONFLICT"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    FILTER_EMPTY = "FILTER_EMPTY"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    DATA_STALE = "DATA_STALE"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class AgentExecution:
    execution_id: ExecutionId
    status: ExecutionStatus
    based_on_requirement_version: RequirementVersion
    created_at: DomainInstant
    snapshot_id: CandidateSnapshotId | None = None
    snapshot_version: SnapshotVersion | None = None
    superseded_by_execution_id: ExecutionId | None = None

    def __post_init__(self) -> None:
        if (self.snapshot_id is None) != (self.snapshot_version is None):
            raise DomainInvariantViolation("Execution snapshot reference requires id and version")
        if self.superseded_by_execution_id is not None:
            if self.status is not ExecutionStatus.SUPERSEDED:
                raise DomainInvariantViolation("superseded_by_execution_id requires SUPERSEDED status")
            if self.superseded_by_execution_id == self.execution_id:
                raise DomainInvariantViolation("Execution cannot supersede itself")
