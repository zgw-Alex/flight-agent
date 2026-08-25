"""FastAPI application entrypoint for the backend."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.api.health import router as health_router
from flight_agent.api.structured_entry import create_structured_entry_router
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    ExecuteReadyRequirementSearch,
    FixtureSchemaVersion,
    SearchEligibleRequirement,
)
from flight_agent.application import (
    NormalizationContext as RequirementNormalizationContext,
)
from flight_agent.application.structured_entry import StartStructuredRequirement
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    MergerVersion,
    NormalizerVersion,
    ReferenceData,
    ReferenceDataVersion,
)
from flight_agent.ports import (
    NormalizationContext as CandidateNormalizationContext,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
MOCK_FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"


def create_app() -> FastAPI:
    """Create the backend ASGI application and wire outer transport routes."""
    app = FastAPI(title="Flight Agent Backend")
    search_execution = _build_search_execution()

    def execute_ready_search(eligible: SearchEligibleRequirement) -> None:
        search_execution.execute(
            requirement=eligible.requirement,
            validation=eligible.validation,
        )

    structured_entry = StartStructuredRequirement(
        repository=InMemoryRequirementRepository(),
        normalization_context=RequirementNormalizationContext(
            reference_instant=DomainInstant(datetime.now(UTC)),
            timezone="Asia/Shanghai",
            locale="zh-CN",
            reference_data_version="bootstrap-v1",
        ),
        recorded_at=lambda: DomainInstant(datetime.now(UTC)),
        id_factory=lambda: str(uuid4()),
        on_search_eligible=execute_ready_search,
    )
    app.include_router(health_router)
    app.include_router(create_structured_entry_router(structured_entry))
    app.state.search_execution = search_execution

    return app


def _build_search_execution() -> ExecuteReadyRequirementSearch:
    assembler_version = AssemblerVersion("candidate-snapshot-assembler-v1")
    return ExecuteReadyRequirementSearch(
        flight_provider=MockFlightProvider(MOCK_FIXTURE_PATH),
        provider_mapper=MockProviderMapper(),
        common_normalizer=CommonNormalizer(),
        normalization_context=_candidate_normalization_context(),
        candidate_merger=CandidateMerger(MergerVersion("candidate-merger-v1")),
        snapshot_assembler=CandidateSnapshotAssembler(assembler_version),
        assembler_version=assembler_version,
        fixture_schema_versions=(FixtureSchemaVersion("m4-u2-v1"),),
        id_factory=lambda: str(uuid4()),
        created_at=lambda: DomainInstant(datetime.now(UTC)),
    )


def _candidate_normalization_context() -> CandidateNormalizationContext:
    return CandidateNormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("m5-u2-reference-data-v1"),
            airports=frozenset({"PVG", "PEK", "SHA", "CAN", "SZX", "CTU", "HGH", "NKG", "XMN", "LAX"}),
            carriers=frozenset({"MU", "DL"}),
        ),
    )


app = create_app()
