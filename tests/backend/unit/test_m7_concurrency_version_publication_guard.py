from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    GuardDecisionStatus,
    GuardReasonCode,
    InProcessExecutionAuthority,
    PublicationAuthority,
    PublicationDecisionStatus,
    VersionGuard,
    VersionGuardPoint,
    commit_requirement_transition,
)
from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId
from flight_agent.domain.impact import ExecutionPlanArtifactRef, ImpactAssetKind
from flight_agent.domain.requirements import (
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import (
    DomainId,
    DomainInstant,
    RequirementVersion,
    SnapshotVersion,
)
from flight_agent.domain.workflow import (
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    ExecutionStatus,
    PublicationId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)
from flight_agent.ports import CommitStatus


def test_optimistic_mutation_conflict_uses_expected_current_version_boundary() -> None:
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    v2 = next_requirement(v1, version=2, day=2)
    stale_v2 = next_requirement(v1, version=2, day=3)
    repository.commit_initial(v1, operation_id="initial")
    first = commit_requirement_transition(repository, v1, v2, "patch-v2")

    stale = commit_requirement_transition(repository, v1, stale_v2, "patch-stale")

    assert first.status is CommitStatus.COMMITTED
    assert stale.status is CommitStatus.CONCURRENCY_CONFLICT
    assert repository.get_current(v1.requirement_id) == v2
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_operation_identity_replay_is_idempotent_not_semantic_equality() -> None:
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    v2 = next_requirement(v1, version=2, day=2)
    same_semantic_v2 = next_requirement(v1, version=2, day=2)
    repository.commit_initial(v1, operation_id="initial")

    first = commit_requirement_transition(repository, v1, v2, "patch-v2")
    replay = commit_requirement_transition(repository, v1, same_semantic_v2, "patch-v2")
    different_operation = commit_requirement_transition(repository, v1, same_semantic_v2, "patch-v2-different")

    assert first.status is CommitStatus.COMMITTED
    assert replay.status is CommitStatus.REPLAYED
    assert different_operation.status is CommitStatus.CONCURRENCY_CONFLICT
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_supersede_and_cancelled_are_distinct_lifecycle_states() -> None:
    authority = InProcessExecutionAuthority()
    requirement_id = RequirementId("requirement-1")

    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=requirement_id,
        requirement_version=RequirementVersion(1),
        execution_plan_id=DomainId("plan-v1"),
    )
    authority.start_execution(
        execution_id=ExecutionId("execution-v2"),
        requirement_id=requirement_id,
        requirement_version=RequirementVersion(2),
        execution_plan_id=DomainId("plan-v2"),
    )
    cancelled = authority.start_execution(
        execution_id=ExecutionId("execution-v3"),
        requirement_id=requirement_id,
        requirement_version=RequirementVersion(3),
        execution_plan_id=DomainId("plan-v3"),
    )
    authority.cancel_execution(cancelled.execution_id)

    assert authority.get(ExecutionId("execution-v1")).status is ExecutionStatus.SUPERSEDED  # type: ignore[union-attr]
    assert authority.get(ExecutionId("execution-v2")).status is ExecutionStatus.SUPERSEDED  # type: ignore[union-attr]
    assert authority.get(ExecutionId("execution-v3")).status is ExecutionStatus.CANCELLED  # type: ignore[union-attr]
    assert authority.current_for(requirement_id) is None


def test_version_guard_points_reject_stale_execution_before_action_after_io_and_before_publication() -> None:
    repository = InMemoryRequirementRepository()
    authority = InProcessExecutionAuthority()
    v1 = requirement(version=1)
    v2 = next_requirement(v1, version=2, day=2)
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    guard = VersionGuard(requirement_repository=repository, execution_authority=authority)

    assert guard.check(
        point=VersionGuardPoint.BEFORE_ACTION,
        execution_id=ExecutionId("execution-v1"),
    ).status is GuardDecisionStatus.PASSED

    repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-v2")
    authority.start_execution(
        execution_id=ExecutionId("execution-v2"),
        requirement_id=v2.requirement_id,
        requirement_version=v2.version,
        execution_plan_id=DomainId("plan-v2"),
    )

    for point in (
        VersionGuardPoint.BEFORE_ACTION,
        VersionGuardPoint.AFTER_IO,
        VersionGuardPoint.BEFORE_PUBLICATION,
    ):
        rejected = guard.check(point=point, execution_id=ExecutionId("execution-v1"))
        assert rejected.status is GuardDecisionStatus.REJECTED
        assert rejected.reason_code is GuardReasonCode.REQUIREMENT_NOT_CURRENT


def test_publication_guard_requires_current_execution_and_complete_lineage() -> None:
    repository = InMemoryRequirementRepository()
    authority = InProcessExecutionAuthority()
    v1 = requirement(version=1)
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    publication = PublicationAuthority(
        version_guard=VersionGuard(requirement_repository=repository, execution_authority=authority),
        published_at=instant,
    )

    incomplete = recommendation(
        execution_id=ExecutionId("execution-v1"),
        requirement=v1,
        complete_lineage=False,
    )
    complete = recommendation(
        execution_id=ExecutionId("execution-v1"),
        requirement=v1,
        complete_lineage=True,
    )

    rejected = publication.attempt_publish(
        publication_id=PublicationId("publication-incomplete"),
        recommendation=incomplete,
    )
    published = publication.attempt_publish(
        publication_id=PublicationId("publication-v1"),
        recommendation=complete,
    )

    assert rejected.status is PublicationDecisionStatus.REJECTED
    assert rejected.reason_code is GuardReasonCode.RECOMMENDATION_LINEAGE_INCOMPLETE
    assert published.status is PublicationDecisionStatus.PUBLISHED
    assert publication.current_publication == published.published_recommendation


def test_late_completion_allowed_but_stale_publication_forbidden_flagship_race() -> None:
    repository = InMemoryRequirementRepository()
    authority = InProcessExecutionAuthority()
    v1 = requirement(version=1)
    v2 = next_requirement(v1, version=2, day=2)
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    guard = VersionGuard(requirement_repository=repository, execution_authority=authority)
    publication = PublicationAuthority(version_guard=guard, published_at=instant)

    assert guard.check(
        point=VersionGuardPoint.BEFORE_ACTION,
        execution_id=ExecutionId("execution-v1"),
    ).passed

    repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-v2")
    authority.start_execution(
        execution_id=ExecutionId("execution-v2"),
        requirement_id=v2.requirement_id,
        requirement_version=v2.version,
        execution_plan_id=DomainId("plan-v2"),
    )
    v2_publication = publication.attempt_publish(
        publication_id=PublicationId("publication-v2"),
        recommendation=recommendation(
            execution_id=ExecutionId("execution-v2"),
            requirement=v2,
            complete_lineage=True,
            recommendation_result_id=RecommendationResultId("recommendation-v2"),
        ),
    )

    late_ref = artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "late-recommendation-v1")
    authority.append_artifacts(
        execution_id=ExecutionId("execution-v1"),
        artifact_refs=(late_ref,),
    )
    authority.complete_execution(ExecutionId("execution-v1"))
    after_io = guard.check(
        point=VersionGuardPoint.AFTER_IO,
        execution_id=ExecutionId("execution-v1"),
    )
    stale_publication = publication.attempt_publish(
        publication_id=PublicationId("publication-v1-late"),
        recommendation=recommendation(
            execution_id=ExecutionId("execution-v1"),
            requirement=v1,
            complete_lineage=True,
            recommendation_result_id=RecommendationResultId("recommendation-v1-late"),
        ),
    )

    assert repository.get_current(v1.requirement_id) == v2
    assert v2_publication.status is PublicationDecisionStatus.PUBLISHED
    assert publication.current_publication == v2_publication.published_recommendation
    assert publication.current_publication is not None
    assert publication.current_publication.based_on_requirement_version == RequirementVersion(2)
    assert after_io.status is GuardDecisionStatus.REJECTED
    assert stale_publication.status is PublicationDecisionStatus.REJECTED
    assert stale_publication.reason_code is GuardReasonCode.REQUIREMENT_NOT_CURRENT
    assert authority.get(ExecutionId("execution-v1")).produced_artifact_refs == (late_ref,)  # type: ignore[union-attr]


def test_provider_failure_does_not_destroy_historical_publication_or_execution_artifacts() -> None:
    repository = InMemoryRequirementRepository()
    authority = InProcessExecutionAuthority()
    v1 = requirement(version=1)
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    authority.append_artifacts(
        execution_id=ExecutionId("execution-v1"),
        artifact_refs=(artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-artifact-v1"),),
    )
    publication = PublicationAuthority(
        version_guard=VersionGuard(requirement_repository=repository, execution_authority=authority),
        published_at=instant,
    )
    first = publication.attempt_publish(
        publication_id=PublicationId("publication-v1"),
        recommendation=recommendation(
            execution_id=ExecutionId("execution-v1"),
            requirement=v1,
            complete_lineage=True,
        ),
    )

    authority.start_execution(
        execution_id=ExecutionId("execution-provider-failure"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-provider-failure"),
    )
    authority.cancel_execution(ExecutionId("execution-provider-failure"))

    assert first.status is PublicationDecisionStatus.PUBLISHED
    assert publication.current_publication == first.published_recommendation
    assert authority.get(ExecutionId("execution-v1")).produced_artifact_refs == (  # type: ignore[union-attr]
        artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-artifact-v1"),
    )


def requirement(*, version: int) -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-1"),
        version=RequirementVersion(version),
        predecessor_version=None if version == 1 else RequirementVersion(version - 1),
        recorded_at=instant(),
        constraints=(departure_date(2026, 9, 1),),
    )


def next_requirement(
    current: RequirementState,
    *,
    version: int,
    day: int,
) -> RequirementState:
    return RequirementState(
        requirement_id=current.requirement_id,
        version=RequirementVersion(version),
        predecessor_version=current.version,
        recorded_at=instant(),
        constraints=(departure_date(2026, 9, day),),
    )


def departure_date(year: int, month: int, day: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(datetime(year, month, day).date()),
    )


def recommendation(
    *,
    execution_id: ExecutionId,
    requirement: RequirementState,
    complete_lineage: bool,
    recommendation_result_id: RecommendationResultId = RecommendationResultId("recommendation-v1"),
) -> RecommendationResult:
    return RecommendationResult(
        recommendation_result_id=recommendation_result_id,
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=execution_id,
        based_on_requirement_version=requirement.version,
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        snapshot_version=SnapshotVersion(1),
        generated_at=instant(),
        items=(
            RecommendationItem(
                itinerary_id=ItineraryId("itinerary-1"),
                primary_offer_id=OfferId("offer-1"),
                roles=(RecommendationRole.BEST_OVERALL,),
                evidence=(EvidenceRef(EvidenceSource.OFFER, OfferId("offer-1")),),
            ),
        ),
        requirement_id=requirement.requirement_id,
        recommendation_run_id="recommendation-run-1" if complete_lineage else None,
        filter_result_id="filter-result-1" if complete_lineage else None,
        ranking_result_id="ranking-result-1" if complete_lineage else None,
        derived_feature_set_id="derived-feature-set-1" if complete_lineage else None,
        recommendation_policy_version="recommendation-policy-v1" if complete_lineage else None,
    )


def artifact_ref(asset_kind: ImpactAssetKind, artifact_id: str) -> ExecutionPlanArtifactRef:
    return ExecutionPlanArtifactRef(
        asset_kind=asset_kind,
        artifact_id=DomainId(artifact_id),
        version="1",
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
