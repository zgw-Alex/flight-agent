"""M7-U4 thin runtime for executing approved impact execution plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from flight_agent.application.requirement_normalization import RequirementValidationResult
from flight_agent.application.search_execution import (
    ExecuteReadyRequirementSearch,
    SearchExecutionStatus,
)
from flight_agent.domain.impact import (
    DataAction,
    ExecutionPlan,
    ExecutionPlanArtifactRef,
    ExecutionStage,
    ImpactAssetKind,
    StageDisposition,
)
from flight_agent.domain.requirements import RequirementState
from flight_agent.domain.shared import DomainId, DomainInvariantViolation, RequirementVersion
from flight_agent.domain.workflow import ExecutionId


class OrchestratorRunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class StageExecutionStatus(str, Enum):
    REUSED = "REUSED"
    RAN = "RAN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    STOPPED = "STOPPED"


class CapabilityExecutionStatus(str, Enum):
    PRODUCED = "PRODUCED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, init=False)
class InvocationEvidence:
    stage: ExecutionStage
    capability_name: str
    invoked: bool

    def __init__(
        self,
        *,
        stage: ExecutionStage,
        capability_name: str,
        invoked: bool,
    ) -> None:
        if capability_name.strip() == "":
            raise DomainInvariantViolation("InvocationEvidence requires a capability name")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "capability_name", capability_name)
        object.__setattr__(self, "invoked", invoked)


@dataclass(frozen=True, init=False)
class CapabilityExecutionResult:
    status: CapabilityExecutionStatus
    artifact_ref: ExecutionPlanArtifactRef | None
    reason_code: str

    def __init__(
        self,
        *,
        status: CapabilityExecutionStatus,
        artifact_ref: ExecutionPlanArtifactRef | None,
        reason_code: str,
    ) -> None:
        if reason_code.strip() == "":
            raise DomainInvariantViolation("CapabilityExecutionResult requires a reason code")
        if status is CapabilityExecutionStatus.PRODUCED and artifact_ref is None:
            raise DomainInvariantViolation("Produced capability result requires an artifact ref")
        if status is CapabilityExecutionStatus.STOPPED and artifact_ref is not None:
            raise DomainInvariantViolation("Stopped capability result must not carry an artifact ref")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "artifact_ref", artifact_ref)
        object.__setattr__(self, "reason_code", reason_code)

    @classmethod
    def produced(cls, artifact_ref: ExecutionPlanArtifactRef) -> CapabilityExecutionResult:
        return cls(
            status=CapabilityExecutionStatus.PRODUCED,
            artifact_ref=artifact_ref,
            reason_code="ARTIFACT_PRODUCED",
        )

    @classmethod
    def stopped(cls, reason_code: str) -> CapabilityExecutionResult:
        return cls(
            status=CapabilityExecutionStatus.STOPPED,
            artifact_ref=None,
            reason_code=reason_code,
        )


@dataclass(frozen=True)
class StageExecutionResult:
    stage: ExecutionStage
    planned_disposition: StageDisposition
    status: StageExecutionStatus
    evidence: InvocationEvidence
    artifact_ref: ExecutionPlanArtifactRef | None
    outcome_reason_code: str | None = None


@dataclass(frozen=True, init=False)
class OrchestratorRunResult:
    execution_id: ExecutionId
    requirement_id: DomainId
    requirement_version: RequirementVersion
    execution_plan_id: DomainId
    status: OrchestratorRunStatus
    stage_results: tuple[StageExecutionResult, ...]
    produced_artifact_refs: tuple[ExecutionPlanArtifactRef, ...]
    reused_artifact_refs: tuple[ExecutionPlanArtifactRef, ...]

    def __init__(
        self,
        *,
        execution_id: ExecutionId,
        requirement: RequirementState,
        execution_plan: ExecutionPlan,
        status: OrchestratorRunStatus,
        stage_results: tuple[StageExecutionResult, ...],
    ) -> None:
        if execution_plan.requirement_id != requirement.requirement_id:
            raise DomainInvariantViolation("ExecutionPlan requirement identity must match runtime requirement")
        if execution_plan.requirement_version != requirement.version:
            raise DomainInvariantViolation("ExecutionPlan requirement version must match runtime requirement")
        object.__setattr__(self, "execution_id", execution_id)
        object.__setattr__(self, "requirement_id", requirement.requirement_id)
        object.__setattr__(self, "requirement_version", requirement.version)
        object.__setattr__(self, "execution_plan_id", execution_plan.execution_plan_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stage_results", tuple(stage_results))
        object.__setattr__(
            self,
            "produced_artifact_refs",
            tuple(
                result.artifact_ref
                for result in stage_results
                if result.status is StageExecutionStatus.RAN and result.artifact_ref is not None
            ),
        )
        object.__setattr__(
            self,
            "reused_artifact_refs",
            tuple(
                result.artifact_ref
                for result in stage_results
                if result.status is StageExecutionStatus.REUSED and result.artifact_ref is not None
            ),
        )

    def result_for(self, stage: ExecutionStage) -> StageExecutionResult:
        for result in self.stage_results:
            if result.stage is stage:
                return result
        raise DomainInvariantViolation(f"Missing execution result for stage: {stage.value}")


class PlanStageCapability(Protocol):
    @property
    def capability_name(self) -> str:
        ...

    def run(
        self,
        *,
        requirement: RequirementState,
        execution_id: ExecutionId,
        execution_plan: ExecutionPlan,
        upstream_artifacts: tuple[ExecutionPlanArtifactRef, ...],
    ) -> CapabilityExecutionResult:
        ...


@dataclass(frozen=True)
class OrchestratorCapabilities:
    data_acquisition: PlanStageCapability
    derived_features: PlanStageCapability
    filtering: PlanStageCapability
    ranking: PlanStageCapability
    recommendation: PlanStageCapability
    relaxation: PlanStageCapability
    explanation: PlanStageCapability

    def capability_for(self, stage: ExecutionStage) -> PlanStageCapability:
        if stage is ExecutionStage.DATA_ACQUISITION:
            return self.data_acquisition
        if stage is ExecutionStage.DERIVED_FEATURES:
            return self.derived_features
        if stage is ExecutionStage.FILTER:
            return self.filtering
        if stage is ExecutionStage.RANKING:
            return self.ranking
        if stage is ExecutionStage.RECOMMENDATION:
            return self.recommendation
        if stage is ExecutionStage.RELAXATION:
            return self.relaxation
        if stage is ExecutionStage.EXPLANATION:
            return self.explanation
        raise DomainInvariantViolation(f"Unsupported execution stage: {stage.value}")


class ImpactExecutionOrchestrator:
    """Coordinates plan execution without recalculating business truth."""

    def __init__(self, *, capabilities: OrchestratorCapabilities) -> None:
        self._capabilities = capabilities

    def execute(
        self,
        *,
        execution_id: ExecutionId,
        requirement: RequirementState,
        execution_plan: ExecutionPlan,
    ) -> OrchestratorRunResult:
        if execution_plan.requirement_id != requirement.requirement_id:
            raise DomainInvariantViolation("ExecutionPlan requirement identity must match runtime requirement")
        if execution_plan.requirement_version != requirement.version:
            raise DomainInvariantViolation("ExecutionPlan requirement version must match runtime requirement")

        stage_results: list[StageExecutionResult] = []
        available_artifacts: list[ExecutionPlanArtifactRef] = []
        stopped = False
        for stage_plan in execution_plan.stages:
            if stopped:
                stage_results.append(
                    _not_invoked_result(
                        stage=stage_plan.stage,
                        planned_disposition=stage_plan.disposition,
                        status=StageExecutionStatus.STOPPED,
                        capability_name=self._capabilities.capability_for(stage_plan.stage).capability_name,
                    )
                )
                continue
            capability = self._capabilities.capability_for(stage_plan.stage)
            if stage_plan.disposition is StageDisposition.REUSE:
                if stage_plan.reused_artifact_ref is None:
                    raise DomainInvariantViolation("REUSE stage requires artifact reference")
                available_artifacts.append(stage_plan.reused_artifact_ref)
                stage_results.append(
                    StageExecutionResult(
                        stage=stage_plan.stage,
                        planned_disposition=stage_plan.disposition,
                        status=StageExecutionStatus.REUSED,
                        evidence=InvocationEvidence(
                            stage=stage_plan.stage,
                            capability_name=capability.capability_name,
                            invoked=False,
                        ),
                        artifact_ref=stage_plan.reused_artifact_ref,
                        outcome_reason_code="ARTIFACT_REUSED",
                    )
                )
                continue
            if stage_plan.disposition is StageDisposition.NOT_APPLICABLE:
                stage_results.append(
                    _not_invoked_result(
                        stage=stage_plan.stage,
                        planned_disposition=stage_plan.disposition,
                        status=StageExecutionStatus.NOT_APPLICABLE,
                        capability_name=capability.capability_name,
                    )
                )
                continue

            capability_result = capability.run(
                requirement=requirement,
                execution_id=execution_id,
                execution_plan=execution_plan,
                upstream_artifacts=tuple(available_artifacts),
            )
            if capability_result.status is CapabilityExecutionStatus.STOPPED:
                stopped = True
                stage_results.append(
                    StageExecutionResult(
                        stage=stage_plan.stage,
                        planned_disposition=stage_plan.disposition,
                        status=StageExecutionStatus.STOPPED,
                        evidence=InvocationEvidence(
                            stage=stage_plan.stage,
                            capability_name=capability.capability_name,
                            invoked=True,
                        ),
                        artifact_ref=None,
                        outcome_reason_code=capability_result.reason_code,
                    )
                )
                continue
            produced_ref = capability_result.artifact_ref
            if produced_ref is None:
                raise DomainInvariantViolation("Produced capability result requires an artifact ref")
            _validate_produced_ref(stage_plan.stage, produced_ref)
            available_artifacts.append(produced_ref)
            stage_results.append(
                StageExecutionResult(
                    stage=stage_plan.stage,
                    planned_disposition=stage_plan.disposition,
                    status=StageExecutionStatus.RAN,
                    evidence=InvocationEvidence(
                        stage=stage_plan.stage,
                        capability_name=capability.capability_name,
                        invoked=True,
                    ),
                    artifact_ref=produced_ref,
                    outcome_reason_code=capability_result.reason_code,
                )
            )
        return OrchestratorRunResult(
            execution_id=execution_id,
            requirement=requirement,
            execution_plan=execution_plan,
            status=OrchestratorRunStatus.STOPPED if stopped else OrchestratorRunStatus.COMPLETED,
            stage_results=tuple(stage_results),
        )


class M4SearchAcquisitionCapability:
    """Runs SEARCH through the closed M4 application pipeline."""

    capability_name = "M4_SEARCH_PIPELINE"

    def __init__(
        self,
        *,
        search_executor: ExecuteReadyRequirementSearch,
        validation: RequirementValidationResult,
    ) -> None:
        self._search_executor = search_executor
        self._validation = validation

    def run(
        self,
        *,
        requirement: RequirementState,
        execution_id: ExecutionId,
        execution_plan: ExecutionPlan,
        upstream_artifacts: tuple[ExecutionPlanArtifactRef, ...],
    ) -> CapabilityExecutionResult:
        _ = execution_id
        _ = upstream_artifacts
        if execution_plan.selected_data_action is not DataAction.SEARCH:
            raise DomainInvariantViolation("M4SearchAcquisitionCapability only supports SEARCH data action")
        result = self._search_executor.execute(
            requirement=requirement,
            validation=self._validation,
        )
        if (
            result.status is not SearchExecutionStatus.SNAPSHOT_READY
            or result.snapshot_outcome is None
            or result.snapshot_outcome.snapshot is None
        ):
            return CapabilityExecutionResult.stopped(result.status.value)
        snapshot = result.snapshot_outcome.snapshot
        return CapabilityExecutionResult.produced(
            ExecutionPlanArtifactRef(
                asset_kind=execution_plan.selected_snapshot_ref.asset_kind,
                artifact_id=snapshot.snapshot_id,
                version=str(snapshot.version.value),
            )
        )


def _not_invoked_result(
    *,
    stage: ExecutionStage,
    planned_disposition: StageDisposition,
    status: StageExecutionStatus,
    capability_name: str,
) -> StageExecutionResult:
    return StageExecutionResult(
        stage=stage,
        planned_disposition=planned_disposition,
        status=status,
        evidence=InvocationEvidence(
            stage=stage,
            capability_name=capability_name,
            invoked=False,
        ),
        artifact_ref=None,
        outcome_reason_code=status.value,
    )


_STAGE_ASSET_KIND = {
    ExecutionStage.DATA_ACQUISITION: ImpactAssetKind.SNAPSHOT,
    ExecutionStage.DERIVED_FEATURES: ImpactAssetKind.DERIVED_FEATURE_SET,
    ExecutionStage.FILTER: ImpactAssetKind.FILTER_RESULT,
    ExecutionStage.RANKING: ImpactAssetKind.RANKING_RESULT,
    ExecutionStage.RECOMMENDATION: ImpactAssetKind.RECOMMENDATION_RESULT,
    ExecutionStage.RELAXATION: ImpactAssetKind.RELAXATION_RESULT,
    ExecutionStage.EXPLANATION: ImpactAssetKind.EXPLANATION,
}


def _validate_produced_ref(
    stage: ExecutionStage,
    produced_ref: ExecutionPlanArtifactRef,
) -> None:
    if produced_ref.asset_kind is not _STAGE_ASSET_KIND[stage]:
        raise DomainInvariantViolation("Capability produced an artifact for the wrong execution stage")
