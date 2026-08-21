from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Callable

import pytest

from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId
from flight_agent.domain.requirements import ConstraintId, RequirementId
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow import (
    AgentExecution,
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    ExecutionStatus,
    ExplanationResult,
    ExplanationResultId,
    ExplanationStatement,
    ExplanationStatementKind,
    PublicationId,
    PublishedRecommendation,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
    WorkflowState,
)


def instant(hour: int = 9) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 21, hour, 0, tzinfo=UTC))


def execution(
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    execution_id: ExecutionId = ExecutionId("execution-1"),
) -> AgentExecution:
    return AgentExecution(
        execution_id=execution_id,
        status=status,
        based_on_requirement_version=RequirementVersion(3),
        created_at=instant(),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
    )


def evidence() -> EvidenceRef:
    return EvidenceRef(EvidenceSource.ITINERARY, ItineraryId("itinerary-1"))


def item() -> RecommendationItem:
    return RecommendationItem(
        itinerary_id=ItineraryId("itinerary-1"),
        primary_offer_id=OfferId("offer-1"),
        roles=(RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAPEST),
        evidence=(evidence(),),
    )


def recommendation(
    result_id: RecommendationResultId = RecommendationResultId("recommendation-1"),
) -> RecommendationResult:
    return RecommendationResult(
        recommendation_result_id=result_id,
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
        generated_at=instant(10),
        items=(item(),),
    )


def explanation(
    result_id: ExplanationResultId = ExplanationResultId("explanation-1"),
    recommendation_result_id: RecommendationResultId = RecommendationResultId("recommendation-1"),
) -> ExplanationResult:
    return ExplanationResult(
        explanation_result_id=result_id,
        recommendation_result_id=recommendation_result_id,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
        generated_at=instant(11),
        statements=(
            ExplanationStatement(
                ExplanationStatementKind.MATCH,
                evidence=(EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId("constraint-1")),),
                rendered_text="Matches the required route.",
            ),
        ),
    )


def test_all_workflow_states_are_expressible_and_separate_from_publication() -> None:
    assert {state.value for state in WorkflowState} == {
        "READY",
        "NEEDS_CLARIFICATION",
        "REQUIREMENT_CONFLICT",
        "SEARCH_EMPTY",
        "FILTER_EMPTY",
        "DATA_INCOMPLETE",
        "DATA_STALE",
        "PROVIDER_ERROR",
    }
    assert WorkflowState.READY != ExecutionStatus.COMPLETED
    assert not hasattr(WorkflowState.READY, "published")


def test_agent_execution_constructs_with_requirement_and_snapshot_lineage() -> None:
    agent_execution = execution()

    assert agent_execution.execution_id == ExecutionId("execution-1")
    assert agent_execution.based_on_requirement_version == RequirementVersion(3)
    assert agent_execution.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert agent_execution.snapshot_version == SnapshotVersion(2)
    assert not hasattr(agent_execution, "coalesce_policy")
    assert not hasattr(agent_execution, "runtime_cancellation")


def test_all_execution_status_values_are_expressible() -> None:
    assert {status.value for status in ExecutionStatus} == {
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "SUPERSEDED",
    }


def test_superseded_execution_has_typed_single_direction_lineage() -> None:
    superseded = AgentExecution(
        execution_id=ExecutionId("execution-1"),
        status=ExecutionStatus.SUPERSEDED,
        based_on_requirement_version=RequirementVersion(3),
        created_at=instant(),
        superseded_by_execution_id=ExecutionId("execution-2"),
    )

    assert superseded.superseded_by_execution_id == ExecutionId("execution-2")
    assert not hasattr(superseded, "cancel_task")


@pytest.mark.parametrize(
    "agent_execution",
    [
        lambda: AgentExecution(
            execution_id=ExecutionId("execution-1"),
            status=ExecutionStatus.RUNNING,
            based_on_requirement_version=RequirementVersion(3),
            created_at=instant(),
            snapshot_id=CandidateSnapshotId("snapshot-1"),
        ),
        lambda: AgentExecution(
            execution_id=ExecutionId("execution-1"),
            status=ExecutionStatus.COMPLETED,
            based_on_requirement_version=RequirementVersion(3),
            created_at=instant(),
            superseded_by_execution_id=ExecutionId("execution-2"),
        ),
        lambda: AgentExecution(
            execution_id=ExecutionId("execution-1"),
            status=ExecutionStatus.SUPERSEDED,
            based_on_requirement_version=RequirementVersion(3),
            created_at=instant(),
            superseded_by_execution_id=ExecutionId("execution-1"),
        ),
    ],
)
def test_agent_execution_rejects_malformed_local_lineage(
    agent_execution: Callable[[], object],
) -> None:
    with pytest.raises(DomainInvariantViolation):
        agent_execution()


def test_agent_execution_is_immutable() -> None:
    agent_execution = execution()

    with pytest.raises(FrozenInstanceError):
        agent_execution.status = ExecutionStatus.FAILED  # type: ignore[misc]


def test_recommendation_result_status_item_invariants() -> None:
    exact = recommendation()
    partial = RecommendationResult(
        recommendation_result_id=RecommendationResultId("recommendation-2"),
        status=RecommendationResultStatus.PARTIAL_MATCH,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
        generated_at=instant(10),
        items=(item(),),
    )
    no_match = RecommendationResult(
        recommendation_result_id=RecommendationResultId("recommendation-3"),
        status=RecommendationResultStatus.NO_MATCH,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
        generated_at=instant(10),
    )

    assert exact.items == (item(),)
    assert partial.items == (item(),)
    assert no_match.items == ()


def test_recommendation_result_rejects_status_item_mismatch() -> None:
    with pytest.raises(DomainInvariantViolation):
        RecommendationResult(
            recommendation_result_id=RecommendationResultId("bad-1"),
            status=RecommendationResultStatus.NO_MATCH,
            execution_id=ExecutionId("execution-1"),
            based_on_requirement_version=RequirementVersion(3),
            snapshot_id=CandidateSnapshotId("snapshot-1"),
            snapshot_version=SnapshotVersion(2),
            generated_at=instant(10),
            items=(item(),),
        )

    with pytest.raises(DomainInvariantViolation):
        RecommendationResult(
            recommendation_result_id=RecommendationResultId("bad-2"),
            status=RecommendationResultStatus.EXACT_MATCH,
            execution_id=ExecutionId("execution-1"),
            based_on_requirement_version=RequirementVersion(3),
            snapshot_id=CandidateSnapshotId("snapshot-1"),
            snapshot_version=SnapshotVersion(2),
            generated_at=instant(10),
        )


def test_recommendation_item_references_flight_graph_without_copying_facts() -> None:
    recommendation_item = item()

    assert recommendation_item.itinerary_id == ItineraryId("itinerary-1")
    assert recommendation_item.primary_offer_id == OfferId("offer-1")
    assert recommendation_item.roles == (
        RecommendationRole.BEST_OVERALL,
        RecommendationRole.CHEAPEST,
    )
    assert not hasattr(recommendation_item, "price")
    assert not hasattr(recommendation_item, "flight_segments")
    with pytest.raises(FrozenInstanceError):
        recommendation_item.roles = ()  # type: ignore[misc]


def test_recommendation_item_rejects_empty_or_duplicate_roles() -> None:
    with pytest.raises(DomainInvariantViolation):
        RecommendationItem(ItineraryId("itinerary-1"), OfferId("offer-1"), roles=())
    with pytest.raises(DomainInvariantViolation):
        RecommendationItem(
            ItineraryId("itinerary-1"),
            OfferId("offer-1"),
            roles=(RecommendationRole.FALLBACK, RecommendationRole.FALLBACK),
        )


def test_all_recommendation_roles_are_legal_without_business_correctness_checks() -> None:
    assert {role.value for role in RecommendationRole} == {
        "BEST_OVERALL",
        "CHEAPEST",
        "EARLIEST_ARRIVAL",
        "BEST_DEPARTURE_TIME",
        "BEST_AIRPORT_MATCH",
        "FALLBACK",
    }
    assert not hasattr(RecommendationRole.CHEAPEST, "score")


def test_evidence_ref_is_typed_structured_and_not_dict_payload() -> None:
    refs = (
        EvidenceRef(EvidenceSource.REQUIREMENT, RequirementId("requirement-1")),
        EvidenceRef(EvidenceSource.CONSTRAINT, ConstraintId("constraint-1")),
        EvidenceRef(EvidenceSource.ITINERARY, ItineraryId("itinerary-1")),
        EvidenceRef(EvidenceSource.OFFER, OfferId("offer-1")),
        EvidenceRef(EvidenceSource.RECOMMENDATION, RecommendationResultId("recommendation-1")),
    )

    assert all(not isinstance(ref, dict) for ref in refs)
    assert not hasattr(refs[0], "raw_provider_payload")


def test_evidence_ref_rejects_wrong_typed_identity_and_empty_note() -> None:
    with pytest.raises(DomainInvariantViolation):
        EvidenceRef(EvidenceSource.OFFER, ItineraryId("itinerary-1"))
    with pytest.raises(DomainInvariantViolation):
        EvidenceRef(EvidenceSource.OFFER, OfferId("offer-1"), note="")


def test_explanation_statement_requires_evidence_and_supports_all_kinds() -> None:
    kinds = {
        ExplanationStatement(kind, evidence=(evidence(),)).kind
        for kind in ExplanationStatementKind
    }

    assert kinds == set(ExplanationStatementKind)
    with pytest.raises(DomainInvariantViolation):
        ExplanationStatement(ExplanationStatementKind.MATCH, evidence=())


def test_explanation_result_constructs_as_projection_without_llm_dependency() -> None:
    result = explanation()

    assert result.recommendation_result_id == RecommendationResultId("recommendation-1")
    assert result.execution_id == ExecutionId("execution-1")
    assert result.statements[0].rendered_text == "Matches the required route."
    assert not hasattr(result, "llm_prompt")
    assert not hasattr(result, "new_business_fact")


def test_explanation_result_rejects_empty_statement_collection_and_is_immutable() -> None:
    with pytest.raises(DomainInvariantViolation):
        ExplanationResult(
            explanation_result_id=ExplanationResultId("bad"),
            recommendation_result_id=RecommendationResultId("recommendation-1"),
            execution_id=ExecutionId("execution-1"),
            based_on_requirement_version=RequirementVersion(3),
            snapshot_id=CandidateSnapshotId("snapshot-1"),
            snapshot_version=SnapshotVersion(2),
            generated_at=instant(11),
            statements=(),
        )

    result = explanation()
    with pytest.raises(FrozenInstanceError):
        result.statements = ()  # type: ignore[misc]


def test_publication_constructs_with_recommendation_only_and_no_current_pointer() -> None:
    published = PublishedRecommendation.from_recommendation(
        PublicationId("publication-1"),
        recommendation(),
        published_at=instant(12),
    )

    assert published.publication_id == PublicationId("publication-1")
    assert published.recommendation_result_id == RecommendationResultId("recommendation-1")
    assert published.explanation_result_id is None
    assert not hasattr(published, "is_current")
    assert not hasattr(published, "current_pointer")


def test_publication_constructs_with_matching_optional_explanation() -> None:
    published = PublishedRecommendation.from_recommendation(
        PublicationId("publication-1"),
        recommendation(),
        published_at=instant(12),
        explanation=explanation(),
    )

    assert published.explanation_result_id == ExplanationResultId("explanation-1")
    assert published.execution_id == ExecutionId("execution-1")
    assert published.based_on_requirement_version == RequirementVersion(3)
    assert published.snapshot_id == CandidateSnapshotId("snapshot-1")
    assert published.snapshot_version == SnapshotVersion(2)


def test_publication_rejects_locally_inconsistent_explanation_lineage() -> None:
    with pytest.raises(DomainInvariantViolation):
        PublishedRecommendation.from_recommendation(
            PublicationId("publication-1"),
            recommendation(),
            published_at=instant(12),
            explanation=explanation(
                recommendation_result_id=RecommendationResultId("other-recommendation")
            ),
        )


def test_publication_is_immutable_and_historical_publication_remains_valid() -> None:
    historical = PublishedRecommendation.from_recommendation(
        PublicationId("publication-old"),
        recommendation(),
        published_at=instant(12),
    )

    assert historical.based_on_requirement_version == RequirementVersion(3)
    with pytest.raises(FrozenInstanceError):
        historical.publication_id = PublicationId("new")  # type: ignore[misc]


def test_generated_artifact_collections_are_defensively_copied() -> None:
    items = [item()]
    result = RecommendationResult(
        recommendation_result_id=RecommendationResultId("recommendation-1"),
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=ExecutionId("execution-1"),
        based_on_requirement_version=RequirementVersion(3),
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(2),
        generated_at=instant(10),
        items=tuple(items),
    )
    items.append(
        RecommendationItem(
            ItineraryId("itinerary-2"),
            OfferId("offer-2"),
            roles=(RecommendationRole.FALLBACK,),
        )
    )

    assert result.items == (item(),)
