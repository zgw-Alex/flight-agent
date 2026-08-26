"""M7-U5 in-process execution authority and publication guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from flight_agent.domain.impact import ExecutionPlanArtifactRef
from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.shared import (
    DomainId,
    DomainInstant,
    DomainInvariantViolation,
    RequirementVersion,
)
from flight_agent.domain.workflow import (
    ExecutionId,
    ExecutionStatus,
    PublicationId,
    PublishedRecommendation,
    RecommendationResult,
    RecommendationResultId,
)
from flight_agent.ports import RequirementRepository


class VersionGuardPoint(str, Enum):
    BEFORE_ACTION = "BEFORE_ACTION"
    AFTER_IO = "AFTER_IO"
    BEFORE_PUBLICATION = "BEFORE_PUBLICATION"


class GuardDecisionStatus(str, Enum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"


class GuardReasonCode(str, Enum):
    CURRENT_AUTHORITY_CONFIRMED = "CURRENT_AUTHORITY_CONFIRMED"
    REQUIREMENT_NOT_CURRENT = "REQUIREMENT_NOT_CURRENT"
    EXECUTION_NOT_CURRENT = "EXECUTION_NOT_CURRENT"
    EXECUTION_NOT_ACTIVE = "EXECUTION_NOT_ACTIVE"
    EXECUTION_NOT_FOUND = "EXECUTION_NOT_FOUND"
    RECOMMENDATION_EXECUTION_MISMATCH = "RECOMMENDATION_EXECUTION_MISMATCH"
    RECOMMENDATION_REQUIREMENT_MISMATCH = "RECOMMENDATION_REQUIREMENT_MISMATCH"
    RECOMMENDATION_LINEAGE_INCOMPLETE = "RECOMMENDATION_LINEAGE_INCOMPLETE"


class PublicationDecisionStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class VersionGuardDecision:
    status: GuardDecisionStatus
    point: VersionGuardPoint
    reason_code: GuardReasonCode
    execution_id: ExecutionId
    requirement_id: RequirementId
    requirement_version: RequirementVersion

    @property
    def passed(self) -> bool:
        return self.status is GuardDecisionStatus.PASSED


@dataclass(frozen=True)
class PublicationDecision:
    status: PublicationDecisionStatus
    reason_code: GuardReasonCode
    recommendation_result_id: RecommendationResultId
    published_recommendation: PublishedRecommendation | None = None

    @property
    def published(self) -> bool:
        return self.status is PublicationDecisionStatus.PUBLISHED


@dataclass(frozen=True)
class ExecutionAuthorityRecord:
    execution_id: ExecutionId
    requirement_id: RequirementId
    requirement_version: RequirementVersion
    execution_plan_id: DomainId
    status: ExecutionStatus
    produced_artifact_refs: tuple[ExecutionPlanArtifactRef, ...] = ()


class InProcessExecutionAuthority:
    """Tracks current execution authority without durability or distributed coordination."""

    def __init__(self) -> None:
        self._records: dict[ExecutionId, ExecutionAuthorityRecord] = {}
        self._current_by_requirement: dict[RequirementId, ExecutionId] = {}

    def start_execution(
        self,
        *,
        execution_id: ExecutionId,
        requirement_id: RequirementId,
        requirement_version: RequirementVersion,
        execution_plan_id: DomainId,
    ) -> ExecutionAuthorityRecord:
        if execution_id in self._records:
            raise DomainInvariantViolation("ExecutionId already exists")
        previous_current_id = self._current_by_requirement.get(requirement_id)
        if previous_current_id is not None:
            previous = self._records[previous_current_id]
            if previous.status in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
                self._records[previous.execution_id] = replace(
                    previous,
                    status=ExecutionStatus.SUPERSEDED,
                )
        record = ExecutionAuthorityRecord(
            execution_id=execution_id,
            requirement_id=requirement_id,
            requirement_version=requirement_version,
            execution_plan_id=execution_plan_id,
            status=ExecutionStatus.RUNNING,
        )
        self._records[execution_id] = record
        self._current_by_requirement[requirement_id] = execution_id
        return record

    def append_artifacts(
        self,
        *,
        execution_id: ExecutionId,
        artifact_refs: tuple[ExecutionPlanArtifactRef, ...],
    ) -> ExecutionAuthorityRecord:
        record = self._require_record(execution_id)
        updated = replace(
            record,
            produced_artifact_refs=(
                *record.produced_artifact_refs,
                *artifact_refs,
            ),
        )
        self._records[execution_id] = updated
        return updated

    def complete_execution(self, execution_id: ExecutionId) -> ExecutionAuthorityRecord:
        record = self._require_record(execution_id)
        if record.status in {ExecutionStatus.CANCELLED, ExecutionStatus.SUPERSEDED}:
            return record
        updated = replace(record, status=ExecutionStatus.COMPLETED)
        self._records[execution_id] = updated
        return updated

    def cancel_execution(self, execution_id: ExecutionId) -> ExecutionAuthorityRecord:
        record = self._require_record(execution_id)
        updated = replace(record, status=ExecutionStatus.CANCELLED)
        self._records[execution_id] = updated
        if self._current_by_requirement.get(record.requirement_id) == execution_id:
            del self._current_by_requirement[record.requirement_id]
        return updated

    def get(self, execution_id: ExecutionId) -> ExecutionAuthorityRecord | None:
        return self._records.get(execution_id)

    def current_for(self, requirement_id: RequirementId) -> ExecutionAuthorityRecord | None:
        current_id = self._current_by_requirement.get(requirement_id)
        if current_id is None:
            return None
        return self._records[current_id]

    def _require_record(self, execution_id: ExecutionId) -> ExecutionAuthorityRecord:
        record = self._records.get(execution_id)
        if record is None:
            raise DomainInvariantViolation("Execution record does not exist")
        return record


class VersionGuard:
    def __init__(
        self,
        *,
        requirement_repository: RequirementRepository,
        execution_authority: InProcessExecutionAuthority,
    ) -> None:
        self._requirement_repository = requirement_repository
        self._execution_authority = execution_authority

    def check(
        self,
        *,
        point: VersionGuardPoint,
        execution_id: ExecutionId,
    ) -> VersionGuardDecision:
        record = self._execution_authority.get(execution_id)
        if record is None:
            return _guard_rejected(
                point=point,
                reason_code=GuardReasonCode.EXECUTION_NOT_FOUND,
                execution_id=execution_id,
                requirement_id=RequirementId("unknown"),
                requirement_version=RequirementVersion(1),
            )
        current_requirement = self._requirement_repository.get_current(record.requirement_id)
        if current_requirement is None or current_requirement.version != record.requirement_version:
            return _guard_rejected(
                point=point,
                reason_code=GuardReasonCode.REQUIREMENT_NOT_CURRENT,
                record=record,
            )
        current_execution = self._execution_authority.current_for(record.requirement_id)
        if current_execution is None or current_execution.execution_id != execution_id:
            return _guard_rejected(
                point=point,
                reason_code=GuardReasonCode.EXECUTION_NOT_CURRENT,
                record=record,
            )
        if record.status not in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED}:
            return _guard_rejected(
                point=point,
                reason_code=GuardReasonCode.EXECUTION_NOT_ACTIVE,
                record=record,
            )
        return VersionGuardDecision(
            status=GuardDecisionStatus.PASSED,
            point=point,
            reason_code=GuardReasonCode.CURRENT_AUTHORITY_CONFIRMED,
            execution_id=execution_id,
            requirement_id=record.requirement_id,
            requirement_version=record.requirement_version,
        )


class PublicationAuthority:
    """Owns current publication authority separate from current execution."""

    def __init__(
        self,
        *,
        version_guard: VersionGuard,
        published_at: Callable[[], DomainInstant],
    ) -> None:
        self._version_guard = version_guard
        self._published_at = published_at
        self._current_publication: PublishedRecommendation | None = None

    @property
    def current_publication(self) -> PublishedRecommendation | None:
        return self._current_publication

    def attempt_publish(
        self,
        *,
        publication_id: PublicationId,
        recommendation: RecommendationResult,
    ) -> PublicationDecision:
        guard = self._version_guard.check(
            point=VersionGuardPoint.BEFORE_PUBLICATION,
            execution_id=recommendation.execution_id,
        )
        if not guard.passed:
            return _publication_rejected(guard.reason_code, recommendation)
        if recommendation.requirement_id != guard.requirement_id:
            return _publication_rejected(
                GuardReasonCode.RECOMMENDATION_REQUIREMENT_MISMATCH,
                recommendation,
            )
        if recommendation.based_on_requirement_version != guard.requirement_version:
            return _publication_rejected(
                GuardReasonCode.RECOMMENDATION_REQUIREMENT_MISMATCH,
                recommendation,
            )
        if _lineage_incomplete(recommendation):
            return _publication_rejected(
                GuardReasonCode.RECOMMENDATION_LINEAGE_INCOMPLETE,
                recommendation,
            )
        published = PublishedRecommendation.from_recommendation(
            publication_id,
            recommendation,
            self._published_at(),
        )
        self._current_publication = published
        return PublicationDecision(
            status=PublicationDecisionStatus.PUBLISHED,
            reason_code=GuardReasonCode.CURRENT_AUTHORITY_CONFIRMED,
            recommendation_result_id=recommendation.recommendation_result_id,
            published_recommendation=published,
        )


def _guard_rejected(
    *,
    point: VersionGuardPoint,
    reason_code: GuardReasonCode,
    execution_id: ExecutionId | None = None,
    requirement_id: RequirementId | None = None,
    requirement_version: RequirementVersion | None = None,
    record: ExecutionAuthorityRecord | None = None,
) -> VersionGuardDecision:
    if record is not None:
        execution_id = record.execution_id
        requirement_id = record.requirement_id
        requirement_version = record.requirement_version
    if execution_id is None or requirement_id is None or requirement_version is None:
        raise DomainInvariantViolation("Rejected guard decision requires execution and requirement identity")
    return VersionGuardDecision(
        status=GuardDecisionStatus.REJECTED,
        point=point,
        reason_code=reason_code,
        execution_id=execution_id,
        requirement_id=requirement_id,
        requirement_version=requirement_version,
    )


def _publication_rejected(
    reason_code: GuardReasonCode,
    recommendation: RecommendationResult,
) -> PublicationDecision:
    return PublicationDecision(
        status=PublicationDecisionStatus.REJECTED,
        reason_code=reason_code,
        recommendation_result_id=recommendation.recommendation_result_id,
    )


def _lineage_incomplete(recommendation: RecommendationResult) -> bool:
    return any(
        item is None
        for item in (
            recommendation.requirement_id,
            recommendation.recommendation_run_id,
            recommendation.filter_result_id,
            recommendation.ranking_result_id,
            recommendation.derived_feature_set_id,
            recommendation.recommendation_policy_version,
        )
    )
