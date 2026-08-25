from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    ExecuteReadyRequirementSearch,
    FixtureSchemaVersion,
    SearchExecutionResult,
    SearchExecutionStatus,
    SearchReadinessStatus,
    SnapshotAssemblyInput,
    SnapshotAssemblyOutcome,
    SnapshotCreationStatus,
    StartStructuredRequirement,
    StructuredRequirementCommand,
)
from flight_agent.application import (
    NormalizationContext as RequirementNormalizationContext,
)
from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.shared import DomainInstant, RequirementVersion
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
from flight_agent.ports import (
    NormalizationContext as CandidateNormalizationContext,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"
ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")


def test_u2_gs01_ready_requirement_runs_real_m4_pipeline_to_non_empty_snapshot() -> None:
    harness = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))

    entry = harness.start("PVG")

    assert entry.readiness is SearchReadinessStatus.READY
    result = harness.only_search_result()
    assert result.status is SearchExecutionStatus.SNAPSHOT_READY
    assert result.search_plan is not None
    assert result.search_plan.requirement_id == RequirementId("requirement-1")
    assert result.search_plan.based_on_requirement_version == RequirementVersion(1)
    assert result.provider_result is not None
    assert result.provider_result.search_plan_id == result.search_plan.search_plan_id
    assert result.provider_result.requirement_id == RequirementId("requirement-1")
    assert result.snapshot_outcome is not None
    assert result.snapshot_outcome.status is SnapshotCreationStatus.COMPLETE_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert len(result.snapshot_outcome.snapshot.offers) == 1
    assert result.snapshot_outcome.snapshot.created_from_requirement_version == RequirementVersion(1)
    assert harness.call_counts() == {
        "provider": 1,
        "mapper": 1,
        "normalizer": 1,
        "merger": 1,
        "assembler": 1,
    }
    assert not hasattr(result.snapshot_outcome.snapshot, "recommendations")


def test_u2_gs02_valid_empty_creates_search_empty_snapshot_not_provider_error() -> None:
    harness = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))

    harness.start("PEK")

    result = harness.only_search_result()
    assert result.status is SearchExecutionStatus.SEARCH_EMPTY
    assert result.status is not SearchExecutionStatus.PROVIDER_ERROR
    assert result.snapshot_outcome is not None
    assert result.snapshot_outcome.status is SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.segments == ()
    assert result.snapshot_outcome.snapshot.itineraries == ()
    assert result.snapshot_outcome.snapshot.offers == ()
    assert harness.call_counts() == {
        "provider": 1,
        "mapper": 1,
        "normalizer": 1,
        "merger": 1,
        "assembler": 1,
    }


def test_u2_gs03_provider_failure_creates_no_new_snapshot_and_is_not_empty() -> None:
    harness = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))

    harness.start("SHA")

    result = harness.only_search_result()
    assert result.status is SearchExecutionStatus.PROVIDER_ERROR
    assert result.status is not SearchExecutionStatus.SEARCH_EMPTY
    assert result.provider_result is not None
    assert result.provider_result.data_status.value == "UNKNOWN"
    assert result.snapshot_outcome is not None
    assert result.snapshot_outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT
    assert result.snapshot_outcome.snapshot is None
    assert harness.call_counts() == {
        "provider": 1,
        "mapper": 1,
        "normalizer": 1,
        "merger": 1,
        "assembler": 1,
    }


def test_u2_gs04_partial_usable_snapshot_is_neither_empty_nor_provider_error() -> None:
    harness = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))

    harness.start("XMN")

    result = harness.only_search_result()
    assert result.status is SearchExecutionStatus.SNAPSHOT_READY
    assert result.snapshot_outcome is not None
    assert result.snapshot_outcome.status is SnapshotCreationStatus.PARTIAL_SNAPSHOT
    assert result.snapshot_outcome.snapshot is not None
    assert result.snapshot_outcome.snapshot.itineraries
    assert result.snapshot_outcome.snapshot.offers == ()
    assert result.downstream_decision_eligible is True


def test_u2_gs05_not_ready_regression_performs_zero_search_pipeline_calls() -> None:
    harness = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1"))

    entry = harness.start("PVG", departure_date=None)

    assert entry.readiness is SearchReadinessStatus.NOT_READY
    assert harness.search_results == []
    assert harness.call_counts() == {
        "provider": 0,
        "mapper": 0,
        "normalizer": 0,
        "merger": 0,
        "assembler": 0,
    }


def test_u2_same_ready_requirement_and_fixture_replay_is_deterministic() -> None:
    first = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))
    second = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))

    first.start("PVG")
    second.start("PVG")

    first_result = first.only_search_result()
    second_result = second.only_search_result()
    assert first_result.search_plan == second_result.search_plan
    assert first_result.provider_result == second_result.provider_result
    assert first_result.mapping_result == second_result.mapping_result
    assert first_result.snapshot_outcome == second_result.snapshot_outcome


class Harness:
    def __init__(self, ids: tuple[str, ...]) -> None:
        self.search_results: list[SearchExecutionResult] = []
        self.ids = iter(ids)
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
        self.structured_entry = StartStructuredRequirement(
            repository=InMemoryRequirementRepository(),
            normalization_context=requirement_normalization_context(),
            recorded_at=instant,
            id_factory=lambda: next(self.ids),
            on_search_eligible=lambda eligible: self.search_results.append(
                self.search_execution.execute(
                    requirement=eligible.requirement,
                    validation=eligible.validation,
                )
            ),
        )

    def start(self, origin: str, departure_date: date | None = date(2026, 9, 1)):
        return self.structured_entry.start(
            StructuredRequirementCommand(
                origin=origin,
                destination="LAX",
                departure_date=departure_date,
            )
        )

    def only_search_result(self) -> SearchExecutionResult:
        assert len(self.search_results) == 1
        return self.search_results[0]

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
