"""Workflow and generated artifact contracts for M2-U4."""

from flight_agent.domain.workflow.evidence import EvidenceRef, EvidenceSource
from flight_agent.domain.workflow.execution import AgentExecution, ExecutionStatus, WorkflowState
from flight_agent.domain.workflow.explanation import (
    ExplanationResult,
    ExplanationStatement,
    ExplanationStatementKind,
)
from flight_agent.domain.workflow.identity import (
    ExecutionId,
    ExplanationResultId,
    PublicationId,
    RecommendationResultId,
)
from flight_agent.domain.workflow.publication import PublishedRecommendation
from flight_agent.domain.workflow.recommendation import (
    RecommendationItem,
    RecommendationResult,
    RecommendationResultStatus,
    RecommendationRole,
)

__all__ = [
    "AgentExecution",
    "EvidenceRef",
    "EvidenceSource",
    "ExecutionId",
    "ExecutionStatus",
    "ExplanationResult",
    "ExplanationResultId",
    "ExplanationStatement",
    "ExplanationStatementKind",
    "PublicationId",
    "PublishedRecommendation",
    "RecommendationItem",
    "RecommendationResult",
    "RecommendationResultId",
    "RecommendationResultStatus",
    "RecommendationRole",
    "WorkflowState",
]
