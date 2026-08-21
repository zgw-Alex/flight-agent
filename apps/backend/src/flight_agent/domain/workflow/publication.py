"""Published recommendation artifact contract."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.flights import CandidateSnapshotId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow.explanation import ExplanationResult
from flight_agent.domain.workflow.identity import (
    ExecutionId,
    ExplanationResultId,
    PublicationId,
    RecommendationResultId,
)
from flight_agent.domain.workflow.recommendation import RecommendationResult


@dataclass(frozen=True)
class PublishedRecommendation:
    publication_id: PublicationId
    recommendation_result_id: RecommendationResultId
    execution_id: ExecutionId
    based_on_requirement_version: RequirementVersion
    snapshot_id: CandidateSnapshotId
    snapshot_version: SnapshotVersion
    published_at: DomainInstant
    explanation_result_id: ExplanationResultId | None = None

    @classmethod
    def from_recommendation(
        cls,
        publication_id: PublicationId,
        recommendation: RecommendationResult,
        published_at: DomainInstant,
        explanation: ExplanationResult | None = None,
    ) -> PublishedRecommendation:
        explanation_result_id = None
        if explanation is not None:
            _validate_explanation_matches_recommendation(explanation, recommendation)
            explanation_result_id = explanation.explanation_result_id
        return cls(
            publication_id=publication_id,
            recommendation_result_id=recommendation.recommendation_result_id,
            execution_id=recommendation.execution_id,
            based_on_requirement_version=recommendation.based_on_requirement_version,
            snapshot_id=recommendation.snapshot_id,
            snapshot_version=recommendation.snapshot_version,
            published_at=published_at,
            explanation_result_id=explanation_result_id,
        )


def _validate_explanation_matches_recommendation(
    explanation: ExplanationResult, recommendation: RecommendationResult
) -> None:
    if explanation.recommendation_result_id != recommendation.recommendation_result_id:
        raise DomainInvariantViolation("ExplanationResult does not belong to RecommendationResult")
    if explanation.execution_id != recommendation.execution_id:
        raise DomainInvariantViolation("ExplanationResult execution lineage mismatch")
    if explanation.based_on_requirement_version != recommendation.based_on_requirement_version:
        raise DomainInvariantViolation("ExplanationResult requirement lineage mismatch")
    if (
        explanation.snapshot_id != recommendation.snapshot_id
        or explanation.snapshot_version != recommendation.snapshot_version
    ):
        raise DomainInvariantViolation("ExplanationResult snapshot lineage mismatch")
