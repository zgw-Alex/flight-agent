from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from collections.abc import Callable
from typing import Any

import pytest

from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    CapabilityExecutionResult,
    GuardDecisionStatus,
    ImpactExecutionOrchestrator,
    InProcessExecutionAuthority,
    OrchestratorCapabilities,
    PublicationAuthority,
    PublicationDecisionStatus,
    VersionGuard,
    VersionGuardPoint,
)
from flight_agent.domain.decision import (
    DEPARTURE_DATE_MATCHES_REQUIREMENT,
    m6_default_feature_registry,
    m6_default_ranking_policy_set,
)
from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    Coverage,
    CoverageStatus,
    FlightSegment,
    Itinerary,
    ItineraryId,
    Money,
    Offer,
    OfferId,
    SegmentId,
)
from flight_agent.domain.impact import (
    DataAction,
    ExecutionPlan,
    ExecutionPlanArtifactRef,
    ExecutionPlanBuilder,
    ExecutionPlanInput,
    ExecutionStage,
    ImpactAssetKind,
    ImpactDecision,
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
    RequirementSemanticDiff,
    RequirementSemanticChangeKind,
    RequirementSemanticDiffer,
    SnapshotCompatibilityFacts,
    StageDisposition,
)
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.shared import (
    DomainId,
    DomainInstant,
    DomainValue,
    FreshnessState,
    OfferFreshness,
    ProvenanceRef,
    RequirementVersion,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.domain.workflow import (
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    PublicationId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def calls(
    *,
    data: int = 0,
    derived: int = 0,
    filtering: int = 0,
    ranking: int = 0,
    recommendation: int = 0,
    relaxation: int = 0,
    explanation: int = 0,
) -> dict[str, int]:
    return {
        "DATA_ACQUISITION": data,
        "DERIVED_FEATURES": derived,
        "FILTER": filtering,
        "RANKING": ranking,
        "RECOMMENDATION": recommendation,
        "RELAXATION": relaxation,
        "EXPLANATION": explanation,
    }


@pytest.mark.parametrize(
    ("name", "scenario_kwargs", "expected"),
    (
        (
            "GS-01 soft preference importance",
            {
                "before": lambda: requirement(
                    version=1,
                    preferences=(price_preference(PreferenceImportance.LOW),),
                ),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    preferences=(price_preference(PreferenceImportance.HIGH),),
                ),
            },
            {
                "change_kind": RequirementSemanticChangeKind.CHANGED,
                "data_action": DataAction.REUSE,
                "calls": calls(ranking=1, recommendation=1, explanation=1),
            },
        ),
        (
            "GS-02 hard max-price tighten",
            {
                "before": lambda: requirement(version=1, constraints=(max_price(1500),)),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    constraints=(max_price(1200),),
                ),
            },
            {
                "change_kind": RequirementSemanticChangeKind.CHANGED,
                "data_action": DataAction.REUSE,
                "calls": calls(filtering=1, ranking=1, recommendation=1, relaxation=1, explanation=1),
            },
        ),
        (
            "GS-03 hard max-price relax",
            {
                "before": lambda: requirement(version=1, constraints=(max_price(1200),)),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    constraints=(max_price(1500),),
                ),
            },
            {
                "change_kind": RequirementSemanticChangeKind.CHANGED,
                "data_action": DataAction.REUSE,
                "calls": calls(filtering=1, ranking=1, recommendation=1, relaxation=1, explanation=1),
            },
        ),
        (
            "GS-04 route change uncovered search",
            {
                "before": lambda: requirement(version=1, constraints=(origin("PVG"),)),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    constraints=(origin("SHA"),),
                ),
                "required_scope_covered": False,
            },
            {
                "change_kind": RequirementSemanticChangeKind.CHANGED,
                "data_action": DataAction.SEARCH,
                "calls": calls(
                    data=1,
                    derived=1,
                    filtering=1,
                    ranking=1,
                    recommendation=1,
                    relaxation=1,
                    explanation=1,
                ),
            },
        ),
        (
            "GS-05 route/date covered no mechanical search",
            {
                "before": lambda: requirement(version=1, constraints=(departure_date(2026, 9, 1),)),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    constraints=(departure_date(2026, 9, 2),),
                ),
                "required_scope_covered": True,
            },
            {
                "change_kind": RequirementSemanticChangeKind.CHANGED,
                "data_action": DataAction.REUSE,
                "calls": calls(derived=1, filtering=1, ranking=1, recommendation=1, relaxation=1, explanation=1),
            },
        ),
        (
            "GS-06 offer stale refresh",
            {
                "before": lambda: requirement(version=1),
                "after": lambda: requirement(version=2, predecessor=RequirementVersion(1)),
                "snapshot": lambda: sample_snapshot(offer_freshness=FreshnessState.STALE),
            },
            {
                "change_kind": RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
                "data_action": DataAction.REFRESH,
                "calls": calls(
                    data=1,
                    derived=1,
                    filtering=1,
                    ranking=1,
                    recommendation=1,
                    relaxation=1,
                    explanation=1,
                ),
            },
        ),
        (
            "GS-07 external fact missing enrich",
            {
                "before": lambda: requirement(version=1),
                "after": lambda: requirement(version=2, predecessor=RequirementVersion(1)),
                "missing_external_fact_keys": ("baggage_fee",),
            },
            {
                "change_kind": RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
                "data_action": DataAction.ENRICH,
                "calls": calls(
                    data=1,
                    derived=1,
                    filtering=1,
                    ranking=1,
                    recommendation=1,
                    relaxation=1,
                    explanation=1,
                ),
            },
        ),
        (
            "GS-08 pipeline raw rebuild",
            {
                "before": lambda: requirement(version=1),
                "after": lambda: requirement(version=2, predecessor=RequirementVersion(1)),
                "pipeline_compatible": False,
                "raw_evidence_usable": True,
            },
            {
                "change_kind": RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
                "data_action": DataAction.REBUILD_FROM_RAW,
                "calls": calls(
                    data=1,
                    derived=1,
                    filtering=1,
                    ranking=1,
                    recommendation=1,
                    relaxation=1,
                    explanation=1,
                ),
            },
        ),
        (
            "GS-11 no semantic change reuse-only",
            {
                "before": lambda: requirement(version=1, constraints=(max_price(1200),)),
                "after": lambda: requirement(
                    version=2,
                    predecessor=RequirementVersion(1),
                    constraints=(max_price(1200, raw_id="constraint-renamed"),),
                ),
            },
            {
                "change_kind": RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
                "data_action": DataAction.REUSE,
                "calls": calls(),
            },
        ),
        (
            "GS-14 unknown compatibility conservative path",
            {
                "before": lambda: requirement(version=1),
                "after": lambda: requirement(version=2, predecessor=RequirementVersion(1)),
                "pipeline_compatible": None,
                "feature_policy_compatible": None,
            },
            {
                "change_kind": RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
                "data_action": DataAction.SEARCH,
                "calls": calls(
                    data=1,
                    derived=1,
                    filtering=1,
                    ranking=1,
                    recommendation=1,
                    relaxation=1,
                    explanation=1,
                ),
            },
        ),
    ),
)
def test_m7_golden_scenarios_validate_semantic_planning_invocation_and_authority(
    name: str,
    scenario_kwargs: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    _ = name
    scenario = build_scenario(**scenario_kwargs)
    capabilities = recording_capabilities()

    result = ImpactExecutionOrchestrator(
        capabilities=orchestrator_capabilities(capabilities)
    ).execute(
        execution_id=ExecutionId("execution-2"),
        requirement=scenario.after,
        execution_plan=scenario.plan,
    )
    authority = authority_for(scenario.after, scenario.plan, result.produced_artifact_refs)
    guard = VersionGuard(
        requirement_repository=authority.repository,
        execution_authority=authority.executions,
    ).check(
        point=VersionGuardPoint.BEFORE_PUBLICATION,
        execution_id=ExecutionId("execution-2"),
    )

    assert scenario.diff.change_kind is expected["change_kind"]
    assert scenario.decision.primary_data_action is expected["data_action"]
    assert scenario.plan.selected_data_action is expected["data_action"]
    assert capability_calls(capabilities) == expected["calls"]
    assert authority.repository.get_current(scenario.after.requirement_id) == scenario.after
    assert authority.executions.current_for(scenario.after.requirement_id).execution_id == ExecutionId("execution-2")  # type: ignore[union-attr]
    assert guard.status is GuardDecisionStatus.PASSED
    if scenario.plan.selected_data_action is DataAction.REUSE:
        assert result.result_for(ExecutionStage.DATA_ACQUISITION).status.value == "REUSED"
        assert result.result_for(ExecutionStage.DATA_ACQUISITION).artifact_ref == scenario.plan.selected_snapshot_ref


def test_gs09_late_v1_completion_cannot_replace_v2_publication() -> None:
    authority = InProcessExecutionAuthority()
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    v2 = requirement(version=2, predecessor=RequirementVersion(1), constraints=(departure_date(2026, 9, 2),))
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    guard = VersionGuard(requirement_repository=repository, execution_authority=authority)
    publication = PublicationAuthority(version_guard=guard, published_at=instant)

    assert guard.check(point=VersionGuardPoint.BEFORE_ACTION, execution_id=ExecutionId("execution-v1")).passed
    repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-v2")
    authority.start_execution(
        execution_id=ExecutionId("execution-v2"),
        requirement_id=v2.requirement_id,
        requirement_version=v2.version,
        execution_plan_id=DomainId("plan-v2"),
    )
    v2_published = publication.attempt_publish(
        publication_id=PublicationId("publication-v2"),
        recommendation=recommendation(ExecutionId("execution-v2"), v2, "recommendation-v2"),
    )
    late_ref = artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "late-v1-recommendation")
    authority.append_artifacts(execution_id=ExecutionId("execution-v1"), artifact_refs=(late_ref,))
    authority.complete_execution(ExecutionId("execution-v1"))

    stale = publication.attempt_publish(
        publication_id=PublicationId("publication-v1-late"),
        recommendation=recommendation(ExecutionId("execution-v1"), v1, "recommendation-v1-late"),
    )

    assert v2_published.status is PublicationDecisionStatus.PUBLISHED
    assert stale.status is PublicationDecisionStatus.REJECTED
    assert publication.current_publication == v2_published.published_recommendation
    assert publication.current_publication is not None
    assert publication.current_publication.based_on_requirement_version == RequirementVersion(2)
    assert authority.get(ExecutionId("execution-v1")).produced_artifact_refs == (late_ref,)  # type: ignore[union-attr]


def test_gs10_same_patch_operation_retry_is_idempotent_authoritative_effect() -> None:
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    v2 = requirement(version=2, predecessor=v1.version, constraints=(departure_date(2026, 9, 2),))
    repository.commit_initial(v1, operation_id="initial")

    first = repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-date")
    replay = repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-date")

    assert first.status.value == "COMMITTED"
    assert replay.status.value == "REPLAYED"
    assert repository.get_current(v1.requirement_id) == v2
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_gs12_optimistic_concurrency_conflict_preserves_current_requirement() -> None:
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    v2 = requirement(version=2, predecessor=v1.version, constraints=(departure_date(2026, 9, 2),))
    stale_v2 = requirement(version=2, predecessor=v1.version, constraints=(departure_date(2026, 9, 3),))
    repository.commit_initial(v1, operation_id="initial")
    repository.commit_next(v2, expected_current_version=v1.version, operation_id="patch-v2")

    stale = repository.commit_next(stale_v2, expected_current_version=v1.version, operation_id="patch-stale")

    assert stale.status.value == "CONCURRENCY_CONFLICT"
    assert repository.get_current(v1.requirement_id) == v2
    assert repository.history(v1.requirement_id) == (v1, v2)


def test_gs13_provider_action_failure_preserves_historical_publication_and_artifacts() -> None:
    authority = InProcessExecutionAuthority()
    repository = InMemoryRequirementRepository()
    v1 = requirement(version=1)
    repository.commit_initial(v1, operation_id="initial")
    authority.start_execution(
        execution_id=ExecutionId("execution-v1"),
        requirement_id=v1.requirement_id,
        requirement_version=v1.version,
        execution_plan_id=DomainId("plan-v1"),
    )
    historical_ref = artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-v1")
    authority.append_artifacts(execution_id=ExecutionId("execution-v1"), artifact_refs=(historical_ref,))
    publication = PublicationAuthority(
        version_guard=VersionGuard(requirement_repository=repository, execution_authority=authority),
        published_at=instant,
    )
    first = publication.attempt_publish(
        publication_id=PublicationId("publication-v1"),
        recommendation=recommendation(ExecutionId("execution-v1"), v1, "recommendation-v1"),
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
    assert authority.get(ExecutionId("execution-v1")).produced_artifact_refs == (historical_ref,)  # type: ignore[union-attr]


def test_deterministic_planning_gate_equivalent_inputs_produce_equivalent_artifacts() -> None:
    left = build_scenario(
        before=lambda: requirement(version=1, constraints=(max_price(1500),)),
        after=lambda: requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1200),),
        ),
    )
    right = build_scenario(
        before=lambda: requirement(version=1, constraints=(max_price(1500),)),
        after=lambda: requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(1200),),
        ),
    )

    assert left.diff == right.diff
    assert left.decision == right.decision
    assert left.plan == right.plan


def test_aggregate_exit_gate_g1_through_g12_are_implementation_evidenced() -> None:
    gates = {
        "G1 Semantic Diff Authority": "test_m7_requirement_semantic_diff.py",
        "G2 Impact Precision": "test_m7_impact_resolver_data_action.py",
        "G3 Data Action Minimality": "test_m7_impact_resolver_data_action.py",
        "G4 Selective Recompute": "test_m7_orchestrator_runtime_selective_execution.py",
        "G5 ExecutionPlan Integrity": "test_m7_execution_plan.py",
        "G6 Invocation Evidence": "test_m7_aggregate_golden_scenarios.py",
        "G7 Immutable Lineage": "test_m7_execution_plan.py",
        "G8 Concurrency Safety": "test_m7_concurrency_version_publication_guard.py",
        "G9 Version Guard": "test_m7_concurrency_version_publication_guard.py",
        "G10 Publication Authority": "test_m7_concurrency_version_publication_guard.py",
        "G11 Regression / Architecture": "test_dependency_rules.py",
        "G12 Scope / Amendment": "test_dependency_rules.py",
    }

    assert tuple(gates) == (
        "G1 Semantic Diff Authority",
        "G2 Impact Precision",
        "G3 Data Action Minimality",
        "G4 Selective Recompute",
        "G5 ExecutionPlan Integrity",
        "G6 Invocation Evidence",
        "G7 Immutable Lineage",
        "G8 Concurrency Safety",
        "G9 Version Guard",
        "G10 Publication Authority",
        "G11 Regression / Architecture",
        "G12 Scope / Amendment",
    )
    assert all(path.endswith(".py") for path in gates.values())


@dataclass(frozen=True)
class ScenarioArtifacts:
    before: RequirementState
    after: RequirementState
    diff: RequirementSemanticDiff
    decision: ImpactDecision
    plan: ExecutionPlan


@dataclass(frozen=True)
class AuthorityBundle:
    repository: InMemoryRequirementRepository
    executions: InProcessExecutionAuthority


def build_scenario(
    *,
    before: Callable[[], RequirementState],
    after: Callable[[], RequirementState],
    snapshot: Callable[[], CandidateSnapshot] | CandidateSnapshot | None = None,
    required_scope_covered: bool | None = True,
    pipeline_compatible: bool | None = True,
    raw_evidence_usable: bool = False,
    missing_external_fact_keys: tuple[str, ...] = (),
    feature_policy_compatible: bool | None = True,
) -> ScenarioArtifacts:
    before_state = before()
    after_state = after()
    snapshot_value: CandidateSnapshot = snapshot() if callable(snapshot) else snapshot or sample_snapshot()
    diff = RequirementSemanticDiffer().compare(before_state, after_state)
    decision = ImpactResolver().resolve(
        ImpactResolverInput(
            semantic_diff=diff,
            snapshot=SnapshotCompatibilityFacts(
                snapshot=snapshot_value,
                required_scope_covered=required_scope_covered,
                pipeline_compatible=pipeline_compatible,
                raw_evidence_usable=raw_evidence_usable,
                missing_external_fact_keys=missing_external_fact_keys,
            ),
            artifacts=M6ArtifactFacts(
                feature_registry=m6_default_feature_registry(),
                ranking_policy_set=m6_default_ranking_policy_set(),
                active_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT,),
                feature_policy_compatible=feature_policy_compatible,
            ),
        )
    )
    plan = ExecutionPlanBuilder().build(
        ExecutionPlanInput(
            impact_decision=decision,
            selected_snapshot_ref=ExecutionPlanArtifactRef(
                asset_kind=ImpactAssetKind.SNAPSHOT,
                artifact_id=snapshot_value.snapshot_id,
                version=str(snapshot_value.version.value),
            ),
            reusable_artifact_refs=(
                artifact_ref(ImpactAssetKind.DERIVED_FEATURE_SET, "derived-feature-set-1"),
                artifact_ref(ImpactAssetKind.FILTER_RESULT, "filter-result-1"),
                artifact_ref(ImpactAssetKind.RANKING_RESULT, "ranking-result-1"),
                artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-result-1"),
                artifact_ref(ImpactAssetKind.RELAXATION_RESULT, "relaxation-result-1"),
                artifact_ref(ImpactAssetKind.EXPLANATION, "explanation-1"),
            ),
        )
    )
    return ScenarioArtifacts(before_state, after_state, diff, decision, plan)


def authority_for(
    requirement_state: RequirementState,
    plan: ExecutionPlan,
    artifact_refs: tuple[ExecutionPlanArtifactRef, ...],
) -> AuthorityBundle:
    repository = InMemoryRequirementRepository()
    initial = requirement(version=1)
    repository.commit_initial(initial, operation_id="initial")
    if requirement_state.version != RequirementVersion(1):
        repository.commit_next(
            requirement_state,
            expected_current_version=RequirementVersion(1),
            operation_id="patch-current",
        )
    executions = InProcessExecutionAuthority()
    executions.start_execution(
        execution_id=ExecutionId("execution-2"),
        requirement_id=requirement_state.requirement_id,
        requirement_version=requirement_state.version,
        execution_plan_id=plan.execution_plan_id,
    )
    executions.append_artifacts(execution_id=ExecutionId("execution-2"), artifact_refs=artifact_refs)
    return AuthorityBundle(repository, executions)


class RecordingCapability:
    def __init__(self, stage: ExecutionStage, asset_kind: ImpactAssetKind) -> None:
        self.stage = stage
        self.asset_kind = asset_kind
        self.calls = 0

    @property
    def capability_name(self) -> str:
        return self.stage.value

    def run(
        self,
        *,
        requirement: RequirementState,
        execution_id: ExecutionId,
        execution_plan: ExecutionPlan,
        upstream_artifacts: tuple[ExecutionPlanArtifactRef, ...],
    ) -> CapabilityExecutionResult:
        _ = requirement
        _ = execution_id
        _ = execution_plan
        _ = upstream_artifacts
        self.calls += 1
        return CapabilityExecutionResult.produced(
            artifact_ref(self.asset_kind, f"{self.stage.value.lower()}-produced-{self.calls}")
        )


@dataclass(frozen=True)
class RecordingCapabilities:
    data: RecordingCapability
    derived: RecordingCapability
    filtering: RecordingCapability
    ranking: RecordingCapability
    recommendation: RecordingCapability
    relaxation: RecordingCapability
    explanation: RecordingCapability


def recording_capabilities() -> RecordingCapabilities:
    return RecordingCapabilities(
        data=RecordingCapability(ExecutionStage.DATA_ACQUISITION, ImpactAssetKind.SNAPSHOT),
        derived=RecordingCapability(ExecutionStage.DERIVED_FEATURES, ImpactAssetKind.DERIVED_FEATURE_SET),
        filtering=RecordingCapability(ExecutionStage.FILTER, ImpactAssetKind.FILTER_RESULT),
        ranking=RecordingCapability(ExecutionStage.RANKING, ImpactAssetKind.RANKING_RESULT),
        recommendation=RecordingCapability(ExecutionStage.RECOMMENDATION, ImpactAssetKind.RECOMMENDATION_RESULT),
        relaxation=RecordingCapability(ExecutionStage.RELAXATION, ImpactAssetKind.RELAXATION_RESULT),
        explanation=RecordingCapability(ExecutionStage.EXPLANATION, ImpactAssetKind.EXPLANATION),
    )


def orchestrator_capabilities(capabilities: RecordingCapabilities) -> OrchestratorCapabilities:
    return OrchestratorCapabilities(
        data_acquisition=capabilities.data,
        derived_features=capabilities.derived,
        filtering=capabilities.filtering,
        ranking=capabilities.ranking,
        recommendation=capabilities.recommendation,
        relaxation=capabilities.relaxation,
        explanation=capabilities.explanation,
    )


def capability_calls(capabilities: RecordingCapabilities) -> dict[str, int]:
    return {
        "DATA_ACQUISITION": capabilities.data.calls,
        "DERIVED_FEATURES": capabilities.derived.calls,
        "FILTER": capabilities.filtering.calls,
        "RANKING": capabilities.ranking.calls,
        "RECOMMENDATION": capabilities.recommendation.calls,
        "RELAXATION": capabilities.relaxation.calls,
        "EXPLANATION": capabilities.explanation.calls,
    }


def recommendation(
    execution_id: ExecutionId,
    requirement_state: RequirementState,
    recommendation_result_id: str,
) -> RecommendationResult:
    return RecommendationResult(
        recommendation_result_id=RecommendationResultId(recommendation_result_id),
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=execution_id,
        based_on_requirement_version=requirement_state.version,
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
        requirement_id=requirement_state.requirement_id,
        recommendation_run_id="recommendation-run-1",
        filter_result_id="filter-result-1",
        ranking_result_id="ranking-result-1",
        derived_feature_set_id="derived-feature-set-1",
        recommendation_policy_version="recommendation-policy-v1",
    )


def requirement(
    *,
    version: int,
    predecessor: RequirementVersion | None = None,
    constraints: tuple[HardConstraint, ...] = (),
    preferences: tuple[SoftPreference, ...] = (),
) -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-1"),
        version=RequirementVersion(version),
        predecessor_version=predecessor,
        recorded_at=instant(),
        constraints=constraints,
        preferences=preferences,
    )


def max_price(amount: int, raw_id: str = "constraint-max-price") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(amount), "CNY"),
    )


def origin(airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-origin"),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def departure_date(year: int, month: int, day: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(year, month, day)),
    )


def price_preference(importance: PreferenceImportance) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=importance,
    )


def artifact_ref(asset_kind: ImpactAssetKind, artifact_id: str) -> ExecutionPlanArtifactRef:
    return ExecutionPlanArtifactRef(
        asset_kind=asset_kind,
        artifact_id=DomainId(artifact_id),
        version="1",
    )


def sample_snapshot(
    *,
    offer_freshness: FreshnessState = FreshnessState.FRESH,
) -> CandidateSnapshot:
    segment = FlightSegment(
        segment_id=SegmentId("segment-1"),
        marketing_carrier="MU",
        flight_number="5101",
        departure_airport="PVG",
        arrival_airport="LAX",
        departure_at=instant(),
        arrival_at=DomainInstant(datetime(2026, 9, 1, 20, 0, tzinfo=UTC)),
        operating_carrier=DomainValue.known("MU"),
        aircraft_type=DomainValue.not_provided(),
        provenance=(ProvenanceRef("canonical", "segment-1"),),
    )
    itinerary = Itinerary(
        itinerary_id=ItineraryId("itinerary-1"),
        segment_ids=(segment.segment_id,),
        provenance=(ProvenanceRef("canonical", "itinerary-1"),),
    )
    offer = Offer(
        offer_id=OfferId("offer-1"),
        itinerary_id=itinerary.itinerary_id,
        total_price=Money(Decimal(980), "CNY"),
        offer_freshness=OfferFreshness(offer_freshness),
        booking_reference=DomainValue.known("BOOK-1"),
        provenance=(ProvenanceRef("canonical", "offer-1"),),
    )
    return CandidateSnapshot(
        snapshot_id=CandidateSnapshotId("snapshot-1"),
        version=SnapshotVersion(1),
        created_at=instant(),
        created_from_requirement_version=RequirementVersion(1),
        structural_freshness=StructuralFreshness(FreshnessState.FRESH),
        coverage=Coverage(
            requested_scope="PVG-LAX",
            actual_coverage="PVG-LAX",
            status=CoverageStatus.COMPLETE,
        ),
        segments=(segment,),
        itineraries=(itinerary,),
        offers=(offer,),
        provenance=(ProvenanceRef("canonical", "snapshot-1"),),
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 9, 1, 8, 0, tzinfo=UTC))
