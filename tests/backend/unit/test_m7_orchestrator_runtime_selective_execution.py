from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.application import (
    AssemblerVersion,
    CapabilityExecutionResult,
    CandidateSnapshotAssembler,
    ExecuteReadyRequirementSearch,
    FixtureSchemaVersion,
    ImpactExecutionOrchestrator,
    M4SearchAcquisitionCapability,
    OrchestratorCapabilities,
    SearchReadinessStatus,
    SnapshotAssemblyInput,
    SnapshotAssemblyOutcome,
    StageExecutionStatus,
)
from flight_agent.application import NormalizationContext as RequirementNormalizationContext
from flight_agent.application import validate_requirement
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
    ImpactResolver,
    ImpactResolverInput,
    M6ArtifactFacts,
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
from flight_agent.domain.workflow import ExecutionId
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    FlightProvider,
    MergedCandidateGraph,
    MergerVersion,
    NormalizationResult,
    NormalizerVersion,
    ProviderMapper,
    ProviderMappingResult,
    ProviderSearchResult,
    ReferenceData,
    ReferenceDataVersion,
)
from flight_agent.ports import NormalizationContext as CandidateNormalizationContext


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"
ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")


def test_reuse_only_plan_executes_zero_capability_calls_and_reuses_refs() -> None:
    plan = execution_plan(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
    )
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=requirement(version=2, predecessor=RequirementVersion(1)),
        execution_plan=plan,
    )

    assert result.status.value == "COMPLETED"
    assert capability_calls(capabilities) == {
        "DATA_ACQUISITION": 0,
        "DERIVED_FEATURES": 0,
        "FILTER": 0,
        "RANKING": 0,
        "RECOMMENDATION": 0,
        "RELAXATION": 0,
        "EXPLANATION": 0,
    }
    assert len(result.produced_artifact_refs) == 0
    assert {stage.status for stage in result.stage_results} == {StageExecutionStatus.REUSED}


def test_preference_only_executes_ranking_recommendation_and_explanation_only() -> None:
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        preferences=(price_preference(PreferenceImportance.HIGH),),
    )
    plan = execution_plan(
        before=requirement(version=1, preferences=(price_preference(PreferenceImportance.LOW),)),
        after=after,
    )
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert capability_calls(capabilities) == {
        "DATA_ACQUISITION": 0,
        "DERIVED_FEATURES": 0,
        "FILTER": 0,
        "RANKING": 1,
        "RECOMMENDATION": 1,
        "RELAXATION": 0,
        "EXPLANATION": 1,
    }
    assert result.result_for(ExecutionStage.FILTER).status is StageExecutionStatus.REUSED
    assert result.result_for(ExecutionStage.RANKING).status is StageExecutionStatus.RAN
    assert result.result_for(ExecutionStage.RECOMMENDATION).status is StageExecutionStatus.RAN


def test_hard_constraint_plan_reuses_snapshot_and_runs_filter_downstream() -> None:
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        constraints=(max_price(1200),),
    )
    plan = execution_plan(
        before=requirement(version=1, constraints=(max_price(1500),)),
        after=after,
    )
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert capability_calls(capabilities) == {
        "DATA_ACQUISITION": 0,
        "DERIVED_FEATURES": 0,
        "FILTER": 1,
        "RANKING": 1,
        "RECOMMENDATION": 1,
        "RELAXATION": 1,
        "EXPLANATION": 1,
    }
    assert result.result_for(ExecutionStage.DATA_ACQUISITION).status is StageExecutionStatus.REUSED
    assert result.result_for(ExecutionStage.FILTER).status is StageExecutionStatus.RAN
    assert result.produced_artifact_refs[0].asset_kind is ImpactAssetKind.FILTER_RESULT


def test_search_plan_runs_closed_m4_pipeline_before_downstream_work() -> None:
    after = searchable_requirement(version=2, origin_airport="XMN", predecessor=RequirementVersion(1))
    plan = execution_plan(
        before=searchable_requirement(version=1, origin_airport="PVG"),
        after=after,
        required_scope_covered=False,
    )
    harness = SearchHarness()
    capabilities = recording_capabilities(
        data_acquisition=M4SearchAcquisitionCapability(
            search_executor=harness.search_execution,
            validation=validate_requirement(after),
        )
    )

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert plan.selected_data_action is DataAction.SEARCH
    assert result.result_for(ExecutionStage.DATA_ACQUISITION).status is StageExecutionStatus.RAN
    assert result.produced_artifact_refs[0].artifact_id == CandidateSnapshotId("snapshot-1")
    assert harness.call_counts() == {
        "provider": 1,
        "mapper": 1,
        "normalizer": 1,
        "merger": 1,
        "assembler": 1,
    }
    assert capability_calls(capabilities)["FILTER"] == 1


def test_search_provider_failure_stops_downstream_without_fake_snapshot() -> None:
    after = searchable_requirement(version=2, origin_airport="SHA", predecessor=RequirementVersion(1))
    plan = execution_plan(
        before=searchable_requirement(version=1, origin_airport="PVG"),
        after=after,
        required_scope_covered=False,
    )
    harness = SearchHarness()
    capabilities = recording_capabilities(
        data_acquisition=M4SearchAcquisitionCapability(
            search_executor=harness.search_execution,
            validation=validate_requirement(after),
        )
    )

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert result.status.value == "STOPPED"
    assert result.result_for(ExecutionStage.DATA_ACQUISITION).status is StageExecutionStatus.STOPPED
    assert result.result_for(ExecutionStage.DATA_ACQUISITION).outcome_reason_code == "PROVIDER_ERROR"
    assert result.result_for(ExecutionStage.FILTER).status is StageExecutionStatus.STOPPED
    assert result.produced_artifact_refs == ()
    assert harness.call_counts() == {
        "provider": 1,
        "mapper": 1,
        "normalizer": 1,
        "merger": 1,
        "assembler": 1,
    }
    assert capability_calls(capabilities)["FILTER"] == 0


@pytest.mark.parametrize(
    ("plan_kwargs", "expected_action"),
    (
        ({"offer_stale": True}, DataAction.REFRESH),
        ({"missing_external_fact_keys": ("baggage_fee",)}, DataAction.ENRICH),
        ({"pipeline_compatible": False, "raw_evidence_usable": True}, DataAction.REBUILD_FROM_RAW),
    ),
)
def test_refresh_enrich_rebuild_use_planned_capability_without_reinterpreting_impact(
    plan_kwargs: dict[str, Any],
    expected_action: DataAction,
) -> None:
    offer_stale = bool(plan_kwargs.pop("offer_stale", False))
    after = requirement(version=2, predecessor=RequirementVersion(1))
    plan = execution_plan(
        before=requirement(version=1),
        after=after,
        snapshot=sample_snapshot(offer_freshness=FreshnessState.STALE) if offer_stale else None,
        **plan_kwargs,
    )
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert plan.selected_data_action is expected_action
    assert result.result_for(ExecutionStage.DATA_ACQUISITION).status is StageExecutionStatus.RAN
    assert capability_calls(capabilities)["DATA_ACQUISITION"] == 1
    assert capability_calls(capabilities)["RECOMMENDATION"] == 1


def test_not_applicable_generates_no_fake_work_or_artifact() -> None:
    plan = execution_plan(
        before=requirement(version=1),
        after=requirement(version=2, predecessor=RequirementVersion(1)),
        include_relaxation_ref=False,
    )
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=requirement(version=2, predecessor=RequirementVersion(1)),
        execution_plan=plan,
    )

    relaxation = result.result_for(ExecutionStage.RELAXATION)
    assert plan.stage(ExecutionStage.RELAXATION).disposition is StageDisposition.NOT_APPLICABLE
    assert relaxation.status is StageExecutionStatus.NOT_APPLICABLE
    assert relaxation.artifact_ref is None
    assert capability_calls(capabilities)["RELAXATION"] == 0


def test_reused_artifacts_remain_immutable_and_produced_refs_have_stage_lineage() -> None:
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        constraints=(max_price(1200),),
    )
    plan = execution_plan(
        before=requirement(version=1, constraints=(max_price(1500),)),
        after=after,
    )
    reused_filter_ref = plan.stage(ExecutionStage.DERIVED_FEATURES).reused_artifact_ref
    capabilities = recording_capabilities()

    result = orchestrator(capabilities).execute(
        execution_id=ExecutionId("execution-1"),
        requirement=after,
        execution_plan=plan,
    )

    assert reused_filter_ref is not None
    with pytest.raises(FrozenInstanceError):
        reused_filter_ref.version = "mutated"  # type: ignore[misc]
    assert capabilities.filtering.calls[0].upstream_artifacts[-1] == reused_filter_ref
    assert result.result_for(ExecutionStage.FILTER).artifact_ref == artifact_ref(
        ImpactAssetKind.FILTER_RESULT,
        "filter-produced-1",
    )


def test_orchestrator_does_not_leak_business_correctness_or_u5_publication_guards() -> None:
    source = (REPO_ROOT / "apps" / "backend" / "src" / "flight_agent" / "application" / "impact_orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert "evaluate_snapshot" not in source
    assert "ranked_candidates[0]" not in source
    assert "select_best_overall" not in source
    assert "PublicationGuard" not in source
    assert "GUARDED_ATTEMPT" not in source


class RecordingCapability:
    def __init__(self, *, stage: ExecutionStage, asset_kind: ImpactAssetKind) -> None:
        self.stage = stage
        self.asset_kind = asset_kind
        self.calls: list[CapabilityCall] = []

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
        self.calls.append(
            CapabilityCall(
                requirement=requirement,
                execution_id=execution_id,
                execution_plan=execution_plan,
                upstream_artifacts=upstream_artifacts,
            )
        )
        return CapabilityExecutionResult.produced(
            artifact_ref(self.asset_kind, f"{self.stage.value.lower()}-produced-{len(self.calls)}")
        )


class CapabilityCall:
    def __init__(
        self,
        *,
        requirement: RequirementState,
        execution_id: ExecutionId,
        execution_plan: ExecutionPlan,
        upstream_artifacts: tuple[ExecutionPlanArtifactRef, ...],
    ) -> None:
        self.requirement = requirement
        self.execution_id = execution_id
        self.execution_plan = execution_plan
        self.upstream_artifacts = upstream_artifacts


@dataclass(frozen=True)
class RecordingCapabilities:
    data_acquisition: RecordingCapability | M4SearchAcquisitionCapability
    derived_features: RecordingCapability
    filtering: RecordingCapability
    ranking: RecordingCapability
    recommendation: RecordingCapability
    relaxation: RecordingCapability
    explanation: RecordingCapability


def recording_capabilities(
    *,
    data_acquisition: RecordingCapability | M4SearchAcquisitionCapability | None = None,
) -> RecordingCapabilities:
    return RecordingCapabilities(
        data_acquisition=data_acquisition
        or RecordingCapability(
            stage=ExecutionStage.DATA_ACQUISITION,
            asset_kind=ImpactAssetKind.SNAPSHOT,
        ),
        derived_features=RecordingCapability(
            stage=ExecutionStage.DERIVED_FEATURES,
            asset_kind=ImpactAssetKind.DERIVED_FEATURE_SET,
        ),
        filtering=RecordingCapability(
            stage=ExecutionStage.FILTER,
            asset_kind=ImpactAssetKind.FILTER_RESULT,
        ),
        ranking=RecordingCapability(
            stage=ExecutionStage.RANKING,
            asset_kind=ImpactAssetKind.RANKING_RESULT,
        ),
        recommendation=RecordingCapability(
            stage=ExecutionStage.RECOMMENDATION,
            asset_kind=ImpactAssetKind.RECOMMENDATION_RESULT,
        ),
        relaxation=RecordingCapability(
            stage=ExecutionStage.RELAXATION,
            asset_kind=ImpactAssetKind.RELAXATION_RESULT,
        ),
        explanation=RecordingCapability(
            stage=ExecutionStage.EXPLANATION,
            asset_kind=ImpactAssetKind.EXPLANATION,
        ),
    )


def orchestrator(capabilities: RecordingCapabilities) -> ImpactExecutionOrchestrator:
    return ImpactExecutionOrchestrator(
        capabilities=OrchestratorCapabilities(
            data_acquisition=capabilities.data_acquisition,
            derived_features=capabilities.derived_features,
            filtering=capabilities.filtering,
            ranking=capabilities.ranking,
            recommendation=capabilities.recommendation,
            relaxation=capabilities.relaxation,
            explanation=capabilities.explanation,
        )
    )


def capability_calls(capabilities: RecordingCapabilities) -> dict[str, int]:
    return {
        "DATA_ACQUISITION": 0
        if isinstance(capabilities.data_acquisition, M4SearchAcquisitionCapability)
        else len(capabilities.data_acquisition.calls),
        "DERIVED_FEATURES": len(capabilities.derived_features.calls),
        "FILTER": len(capabilities.filtering.calls),
        "RANKING": len(capabilities.ranking.calls),
        "RECOMMENDATION": len(capabilities.recommendation.calls),
        "RELAXATION": len(capabilities.relaxation.calls),
        "EXPLANATION": len(capabilities.explanation.calls),
    }


def execution_plan(
    *,
    before: RequirementState,
    after: RequirementState,
    snapshot: CandidateSnapshot | None = None,
    required_scope_covered: bool | None = True,
    pipeline_compatible: bool | None = True,
    raw_evidence_usable: bool = False,
    missing_external_fact_keys: tuple[str, ...] = (),
    include_relaxation_ref: bool = True,
) -> ExecutionPlan:
    snapshot = snapshot or sample_snapshot()
    diff = RequirementSemanticDiffer().compare(before, after)
    decision = ImpactResolver().resolve(
        ImpactResolverInput(
            semantic_diff=diff,
            snapshot=SnapshotCompatibilityFacts(
                snapshot=snapshot,
                required_scope_covered=required_scope_covered,
                pipeline_compatible=pipeline_compatible,
                raw_evidence_usable=raw_evidence_usable,
                missing_external_fact_keys=missing_external_fact_keys,
            ),
            artifacts=M6ArtifactFacts(
                feature_registry=m6_default_feature_registry(),
                ranking_policy_set=m6_default_ranking_policy_set(),
                active_feature_keys=(DEPARTURE_DATE_MATCHES_REQUIREMENT,),
            ),
        )
    )
    reusable_refs = (
        artifact_ref(ImpactAssetKind.DERIVED_FEATURE_SET, "derived-feature-set-1"),
        artifact_ref(ImpactAssetKind.FILTER_RESULT, "filter-result-1"),
        artifact_ref(ImpactAssetKind.RANKING_RESULT, "ranking-result-1"),
        artifact_ref(ImpactAssetKind.RECOMMENDATION_RESULT, "recommendation-result-1"),
        artifact_ref(ImpactAssetKind.EXPLANATION, "explanation-1"),
    )
    if include_relaxation_ref:
        reusable_refs = (
            *reusable_refs,
            artifact_ref(ImpactAssetKind.RELAXATION_RESULT, "relaxation-result-1"),
        )
    return ExecutionPlanBuilder().build(
        ExecutionPlanInput(
            impact_decision=decision,
            selected_snapshot_ref=ExecutionPlanArtifactRef(
                asset_kind=ImpactAssetKind.SNAPSHOT,
                artifact_id=snapshot.snapshot_id,
                version=str(snapshot.version.value),
            ),
            reusable_artifact_refs=reusable_refs,
        )
    )


def artifact_ref(asset_kind: ImpactAssetKind, artifact_id: str) -> ExecutionPlanArtifactRef:
    return ExecutionPlanArtifactRef(
        asset_kind=asset_kind,
        artifact_id=DomainId(artifact_id),
        version="1",
    )


class SearchHarness:
    def __init__(self) -> None:
        self.ids = iter(("search-plan-1", "snapshot-1"))
        self.provider = CountingFlightProvider(MockFlightProvider(FIXTURE_PATH))
        self.mapper = CountingProviderMapper(MockProviderMapper())
        self.normalizer = CountingCommonNormalizer(CommonNormalizer())
        self.merger = CountingCandidateMerger(CandidateMerger(MergerVersion("candidate-merger-v1")))
        self.assembler = CountingSnapshotAssembler(CandidateSnapshotAssembler(ASSEMBLER_VERSION))
        self.search_execution = ExecuteReadyRequirementSearch(
            flight_provider=self.provider,
            provider_mapper=self.mapper,
            common_normalizer=cast(CommonNormalizer, self.normalizer),
            normalization_context=candidate_normalization_context(),
            candidate_merger=cast(CandidateMerger, self.merger),
            snapshot_assembler=cast(CandidateSnapshotAssembler, self.assembler),
            assembler_version=ASSEMBLER_VERSION,
            fixture_schema_versions=(FixtureSchemaVersion("m4-u2-v1"),),
            id_factory=lambda: next(self.ids),
            created_at=instant,
        )

    def call_counts(self) -> dict[str, int]:
        return {
            "provider": self.provider.calls,
            "mapper": self.mapper.calls,
            "normalizer": self.normalizer.calls,
            "merger": self.merger.calls,
            "assembler": self.assembler.calls,
        }


class CountingFlightProvider:
    def __init__(self, inner: FlightProvider) -> None:
        self.inner = inner
        self.calls = 0

    def search(self, search_plan) -> ProviderSearchResult:
        self.calls += 1
        return self.inner.search(search_plan)


class CountingProviderMapper:
    def __init__(self, inner: ProviderMapper) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def mapper_version(self):
        return self.inner.mapper_version

    def map(self, provider_result: ProviderSearchResult) -> ProviderMappingResult:
        self.calls += 1
        return self.inner.map(provider_result)


class CountingCommonNormalizer:
    def __init__(self, inner: CommonNormalizer) -> None:
        self.inner = inner
        self.calls = 0

    def normalize(
        self,
        mapping_result: ProviderMappingResult,
        context: CandidateNormalizationContext,
    ) -> NormalizationResult:
        self.calls += 1
        return self.inner.normalize(mapping_result, context)


class CountingCandidateMerger:
    def __init__(self, inner: CandidateMerger) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def merger_version(self):
        return self.inner.merger_version

    def merge(self, normalization_results: tuple[NormalizationResult, ...]) -> MergedCandidateGraph:
        self.calls += 1
        return self.inner.merge(normalization_results)


class CountingSnapshotAssembler:
    def __init__(self, inner: CandidateSnapshotAssembler) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def assembler_version(self):
        return self.inner.assembler_version

    def assemble(self, assembly_input: SnapshotAssemblyInput) -> SnapshotAssemblyOutcome:
        self.calls += 1
        return self.inner.assemble(assembly_input)


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


def searchable_requirement(
    *,
    version: int,
    origin_airport: str,
    predecessor: RequirementVersion | None = None,
) -> RequirementState:
    return requirement(
        version=version,
        predecessor=predecessor,
        constraints=(
            origin(origin_airport),
            destination("LAX"),
            departure_date(2026, 9, 1),
        ),
    )


def max_price(amount: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-max-price"),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(amount), "CNY"),
    )


def origin(airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(f"constraint-origin-{airport}"),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination(airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(f"constraint-destination-{airport}"),
        scope=ConstraintScope.DESTINATION_AIRPORT,
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
        snapshot_id=CandidateSnapshotId("snapshot-previous"),
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
        provenance=(ProvenanceRef("canonical", "snapshot-previous"),),
    )


def requirement_normalization_context() -> RequirementNormalizationContext:
    return RequirementNormalizationContext(
        reference_instant=instant(),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="test-v1",
    )


def candidate_normalization_context() -> CandidateNormalizationContext:
    return CandidateNormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("m5-u2-reference-data-v1"),
            airports=frozenset({"PVG", "PEK", "SHA", "CAN", "SZX", "CTU", "HGH", "NKG", "XMN", "LAX"}),
            carriers=frozenset({"MU", "DL"}),
        ),
    )


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
