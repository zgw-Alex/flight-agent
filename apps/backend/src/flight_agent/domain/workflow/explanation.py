"""Explanation artifact contracts."""

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
from flight_agent.domain.workflow.evidence import EvidenceRef
from flight_agent.domain.workflow.identity import (
    ExecutionId,
    ExplanationResultId,
    RecommendationResultId,
)


class ExplanationStatementKind(str, Enum):
    MATCH = "MATCH"
    ADVANTAGE = "ADVANTAGE"
    TRADE_OFF = "TRADE_OFF"


@dataclass(frozen=True, init=False)
class ExplanationStatement:
    kind: ExplanationStatementKind
    evidence: tuple[EvidenceRef, ...]
    rendered_text: str | None

    def __init__(
        self,
        kind: ExplanationStatementKind,
        evidence: tuple[EvidenceRef, ...],
        rendered_text: str | None = None,
    ) -> None:
        evidence_tuple = tuple(evidence)
        if len(evidence_tuple) == 0:
            raise DomainInvariantViolation("ExplanationStatement requires at least one EvidenceRef")
        if rendered_text is not None and rendered_text.strip() == "":
            raise DomainInvariantViolation("rendered_text must be non-empty when provided")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "evidence", evidence_tuple)
        object.__setattr__(self, "rendered_text", rendered_text)


@dataclass(frozen=True, init=False)
class ExplanationResult:
    explanation_result_id: ExplanationResultId
    recommendation_result_id: RecommendationResultId
    execution_id: ExecutionId
    based_on_requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    generated_at: DomainInstant
    statements: tuple[ExplanationStatement, ...]

    def __init__(
        self,
        explanation_result_id: ExplanationResultId,
        recommendation_result_id: RecommendationResultId,
        execution_id: ExecutionId,
        based_on_requirement_version: RequirementVersion,
        snapshot_id: CandidateSnapshotId,
        snapshot_version: SnapshotVersion,
        generated_at: DomainInstant,
        statements: tuple[ExplanationStatement, ...],
    ) -> None:
        statements_tuple = tuple(statements)
        if len(statements_tuple) == 0:
            raise DomainInvariantViolation("ExplanationResult requires at least one statement")
        object.__setattr__(self, "explanation_result_id", explanation_result_id)
        object.__setattr__(self, "recommendation_result_id", recommendation_result_id)
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "based_on_requirement_version", based_on_requirement_version)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "snapshot_version", snapshot_version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "statements", statements_tuple)
