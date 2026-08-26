"""M7-U3 deterministic execution-plan projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.impact.decision import (
    DataAction,
    ImpactAssetKind,
    ImpactDecision,
    ImpactReasonCode,
)
from flight_agent.domain.shared import DomainId, DomainInvariantViolation, RequirementVersion


class ExecutionStage(str, Enum):
    DATA_ACQUISITION = "DATA_ACQUISITION"
    DERIVED_FEATURES = "DERIVED_FEATURES"
    FILTER = "FILTER"
    RANKING = "RANKING"
    RECOMMENDATION = "RECOMMENDATION"
    RELAXATION = "RELAXATION"
    EXPLANATION = "EXPLANATION"


class StageDisposition(str, Enum):
    REUSE = "REUSE"
    RUN = "RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExecutionPlanReasonCode(str, Enum):
    DATA_REUSE = "DATA_REUSE"
    DATA_REFRESH = "DATA_REFRESH"
    DATA_SEARCH = "DATA_SEARCH"
    DATA_ENRICH = "DATA_ENRICH"
    DATA_REBUILD_FROM_RAW = "DATA_REBUILD_FROM_RAW"
    ARTIFACT_REUSE = "ARTIFACT_REUSE"
    ARTIFACT_RECOMPUTE = "ARTIFACT_RECOMPUTE"
    ARTIFACT_NOT_APPLICABLE = "ARTIFACT_NOT_APPLICABLE"
    UPSTREAM_DATA_ACTION_CHANGED = "UPSTREAM_DATA_ACTION_CHANGED"


@dataclass(frozen=True, init=False)
class ExecutionPlanVersionRef:
    name: str
    version: str

    def __init__(self, *, name: str, version: str) -> None:
        if name.strip() == "" or version.strip() == "":
            raise DomainInvariantViolation("ExecutionPlanVersionRef requires name and version")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, init=False)
class ExecutionPlanArtifactRef:
    asset_kind: ImpactAssetKind
    artifact_id: DomainId
    version: str

    def __init__(
        self,
        *,
        asset_kind: ImpactAssetKind,
        artifact_id: DomainId,
        version: str,
    ) -> None:
        if version.strip() == "":
            raise DomainInvariantViolation("ExecutionPlanArtifactRef requires a version")
        object.__setattr__(self, "asset_kind", asset_kind)
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, init=False)
class ExecutionStagePlan:
    stage: ExecutionStage
    disposition: StageDisposition
    reason_codes: tuple[ExecutionPlanReasonCode | ImpactReasonCode, ...]
    reused_artifact_ref: ExecutionPlanArtifactRef | None

    def __init__(
        self,
        *,
        stage: ExecutionStage,
        disposition: StageDisposition,
        reason_codes: tuple[ExecutionPlanReasonCode | ImpactReasonCode, ...],
        reused_artifact_ref: ExecutionPlanArtifactRef | None = None,
    ) -> None:
        if len(reason_codes) == 0:
            raise DomainInvariantViolation("ExecutionStagePlan requires reason evidence")
        if disposition is StageDisposition.REUSE and reused_artifact_ref is None:
            raise DomainInvariantViolation("REUSE stage requires a reused artifact reference")
        if disposition is not StageDisposition.REUSE and reused_artifact_ref is not None:
            raise DomainInvariantViolation("Only REUSE stages may carry reused artifact references")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(frozenset(reason_codes), key=lambda code: code.value)),
        )
        object.__setattr__(self, "reused_artifact_ref", reused_artifact_ref)


@dataclass(frozen=True, init=False)
class ExecutionPlanInput:
    impact_decision: ImpactDecision
    selected_snapshot_ref: ExecutionPlanArtifactRef
    reusable_artifact_refs: tuple[ExecutionPlanArtifactRef, ...]
    policy_versions: tuple[ExecutionPlanVersionRef, ...]
    reference_data_versions: tuple[ExecutionPlanVersionRef, ...]

    def __init__(
        self,
        *,
        impact_decision: ImpactDecision,
        selected_snapshot_ref: ExecutionPlanArtifactRef,
        reusable_artifact_refs: tuple[ExecutionPlanArtifactRef, ...] = (),
        policy_versions: tuple[ExecutionPlanVersionRef, ...] = (),
        reference_data_versions: tuple[ExecutionPlanVersionRef, ...] = (),
    ) -> None:
        if selected_snapshot_ref.asset_kind is not ImpactAssetKind.SNAPSHOT:
            raise DomainInvariantViolation("ExecutionPlanInput requires a snapshot reference")
        refs = tuple(sorted(reusable_artifact_refs, key=_artifact_ref_sort_key))
        if len({ref.asset_kind for ref in refs}) != len(refs):
            raise DomainInvariantViolation("ExecutionPlanInput requires one reusable ref per asset kind")
        object.__setattr__(self, "impact_decision", impact_decision)
        object.__setattr__(self, "selected_snapshot_ref", selected_snapshot_ref)
        object.__setattr__(self, "reusable_artifact_refs", refs)
        object.__setattr__(self, "policy_versions", tuple(sorted(policy_versions, key=_version_ref_sort_key)))
        object.__setattr__(
            self,
            "reference_data_versions",
            tuple(sorted(reference_data_versions, key=_version_ref_sort_key)),
        )

    def reusable_ref_for(
        self,
        asset_kind: ImpactAssetKind,
    ) -> ExecutionPlanArtifactRef | None:
        if asset_kind is ImpactAssetKind.SNAPSHOT:
            return self.selected_snapshot_ref
        for ref in self.reusable_artifact_refs:
            if ref.asset_kind is asset_kind:
                return ref
        return None


@dataclass(frozen=True, init=False)
class ExecutionPlan:
    execution_plan_id: DomainId
    requirement_id: DomainId
    requirement_version: RequirementVersion
    impact_decision_id: DomainId
    semantic_diff_id: DomainId
    selected_data_action: DataAction
    selected_snapshot_ref: ExecutionPlanArtifactRef
    policy_versions: tuple[ExecutionPlanVersionRef, ...]
    reference_data_versions: tuple[ExecutionPlanVersionRef, ...]
    stages: tuple[ExecutionStagePlan, ...]

    def __init__(
        self,
        *,
        execution_plan_id: DomainId,
        impact_decision: ImpactDecision,
        selected_snapshot_ref: ExecutionPlanArtifactRef,
        policy_versions: tuple[ExecutionPlanVersionRef, ...],
        reference_data_versions: tuple[ExecutionPlanVersionRef, ...],
        stages: tuple[ExecutionStagePlan, ...],
    ) -> None:
        stages_tuple = tuple(stages)
        expected_order = _ORDERED_STAGES
        if tuple(stage.stage for stage in stages_tuple) != expected_order:
            raise DomainInvariantViolation("ExecutionPlan stages must use deterministic U3 order")
        object.__setattr__(self, "execution_plan_id", execution_plan_id)
        object.__setattr__(self, "requirement_id", impact_decision.requirement_id)
        object.__setattr__(self, "requirement_version", impact_decision.to_version)
        object.__setattr__(self, "impact_decision_id", impact_decision.impact_decision_id)
        object.__setattr__(self, "semantic_diff_id", impact_decision.semantic_diff_id)
        object.__setattr__(self, "selected_data_action", impact_decision.primary_data_action)
        object.__setattr__(self, "selected_snapshot_ref", selected_snapshot_ref)
        object.__setattr__(self, "policy_versions", tuple(policy_versions))
        object.__setattr__(self, "reference_data_versions", tuple(reference_data_versions))
        object.__setattr__(self, "stages", stages_tuple)

    def stage(self, stage: ExecutionStage) -> ExecutionStagePlan:
        for stage_plan in self.stages:
            if stage_plan.stage is stage:
                return stage_plan
        raise DomainInvariantViolation(f"Missing execution stage: {stage.value}")


class ExecutionPlanBuilder:
    """Builds a deterministic plan artifact without running provider work."""

    def build(self, plan_input: ExecutionPlanInput) -> ExecutionPlan:
        decision = plan_input.impact_decision
        stages = tuple(
            _stage_plan(
                stage=stage,
                asset_kind=_STAGE_ASSET_KIND[stage],
                plan_input=plan_input,
            )
            for stage in _ORDERED_STAGES
        )
        return ExecutionPlan(
            execution_plan_id=DomainId(f"execution-plan:{decision.impact_decision_id.value}"),
            impact_decision=decision,
            selected_snapshot_ref=plan_input.selected_snapshot_ref,
            policy_versions=plan_input.policy_versions,
            reference_data_versions=plan_input.reference_data_versions,
            stages=stages,
        )


_ORDERED_STAGES = (
    ExecutionStage.DATA_ACQUISITION,
    ExecutionStage.DERIVED_FEATURES,
    ExecutionStage.FILTER,
    ExecutionStage.RANKING,
    ExecutionStage.RECOMMENDATION,
    ExecutionStage.RELAXATION,
    ExecutionStage.EXPLANATION,
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

_DATA_ACTION_REASON = {
    DataAction.REUSE: ExecutionPlanReasonCode.DATA_REUSE,
    DataAction.REFRESH: ExecutionPlanReasonCode.DATA_REFRESH,
    DataAction.SEARCH: ExecutionPlanReasonCode.DATA_SEARCH,
    DataAction.ENRICH: ExecutionPlanReasonCode.DATA_ENRICH,
    DataAction.REBUILD_FROM_RAW: ExecutionPlanReasonCode.DATA_REBUILD_FROM_RAW,
}


def _stage_plan(
    *,
    stage: ExecutionStage,
    asset_kind: ImpactAssetKind,
    plan_input: ExecutionPlanInput,
) -> ExecutionStagePlan:
    decision = plan_input.impact_decision
    impact = decision.impact_for(asset_kind)
    if asset_kind is ImpactAssetKind.SNAPSHOT:
        return _data_stage(stage, plan_input)
    if decision.primary_data_action is not DataAction.REUSE:
        return ExecutionStagePlan(
            stage=stage,
            disposition=StageDisposition.RUN,
            reason_codes=(
                ExecutionPlanReasonCode.ARTIFACT_RECOMPUTE,
                ExecutionPlanReasonCode.UPSTREAM_DATA_ACTION_CHANGED,
                *impact.reason_codes,
            ),
        )
    if impact.required_action is DataAction.REUSE:
        reused_ref = plan_input.reusable_ref_for(asset_kind)
        if reused_ref is None:
            return ExecutionStagePlan(
                stage=stage,
                disposition=StageDisposition.NOT_APPLICABLE,
                reason_codes=(
                    ExecutionPlanReasonCode.ARTIFACT_NOT_APPLICABLE,
                    *impact.reason_codes,
                ),
            )
        return ExecutionStagePlan(
            stage=stage,
            disposition=StageDisposition.REUSE,
            reason_codes=(ExecutionPlanReasonCode.ARTIFACT_REUSE, *impact.reason_codes),
            reused_artifact_ref=reused_ref,
        )
    if impact.required_action is DataAction.RECOMPUTE:
        return ExecutionStagePlan(
            stage=stage,
            disposition=StageDisposition.RUN,
            reason_codes=(ExecutionPlanReasonCode.ARTIFACT_RECOMPUTE, *impact.reason_codes),
        )
    return ExecutionStagePlan(
        stage=stage,
        disposition=StageDisposition.NOT_APPLICABLE,
        reason_codes=(ExecutionPlanReasonCode.ARTIFACT_NOT_APPLICABLE, *impact.reason_codes),
    )


def _data_stage(
    stage: ExecutionStage,
    plan_input: ExecutionPlanInput,
) -> ExecutionStagePlan:
    action = plan_input.impact_decision.primary_data_action
    reason = _DATA_ACTION_REASON.get(action, ExecutionPlanReasonCode.ARTIFACT_NOT_APPLICABLE)
    impact = plan_input.impact_decision.impact_for(ImpactAssetKind.SNAPSHOT)
    if action is DataAction.REUSE:
        return ExecutionStagePlan(
            stage=stage,
            disposition=StageDisposition.REUSE,
            reason_codes=(reason, *impact.reason_codes),
            reused_artifact_ref=plan_input.selected_snapshot_ref,
        )
    if action in {
        DataAction.REFRESH,
        DataAction.SEARCH,
        DataAction.ENRICH,
        DataAction.REBUILD_FROM_RAW,
    }:
        return ExecutionStagePlan(
            stage=stage,
            disposition=StageDisposition.RUN,
            reason_codes=(reason, *impact.reason_codes),
        )
    return ExecutionStagePlan(
        stage=stage,
        disposition=StageDisposition.NOT_APPLICABLE,
        reason_codes=(reason, *impact.reason_codes),
    )


def _artifact_ref_sort_key(ref: ExecutionPlanArtifactRef) -> tuple[str, str, str]:
    return (ref.asset_kind.value, ref.artifact_id.value, ref.version)


def _version_ref_sort_key(ref: ExecutionPlanVersionRef) -> tuple[str, str]:
    return (ref.name, ref.version)
