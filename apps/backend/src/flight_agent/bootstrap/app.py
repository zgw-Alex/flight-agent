"""FastAPI application entrypoint for the backend."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.adapters.publication_repository_memory import InMemoryPublicationRepository
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.api.health import router as health_router
from flight_agent.api.structured_entry import create_structured_entry_router
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    ExecuteMinimalDecision,
    ExecuteReadyRequirementSearch,
    FixtureSchemaVersion,
    PublicWorkflowOutcome,
    PublishRecommendation,
    SearchEligibleRequirement,
    SearchExecutionResult,
    SearchExecutionStatus,
    outcome_from_decision,
)
from flight_agent.application import (
    NormalizationContext as RequirementNormalizationContext,
)
from flight_agent.application.structured_entry import StartStructuredRequirement
from flight_agent.domain.decision import LowerPriceRanking, MaxPriceFilter, RecommendationSelector
from flight_agent.domain.shared import DomainInstant
from flight_agent.domain.workflow import ExecutionId
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
    minimal_decision = _build_minimal_decision()
    publication_repository = InMemoryPublicationRepository()
    publisher = PublishRecommendation(
        id_factory=lambda: str(uuid4()),
        published_at=lambda: DomainInstant(datetime.now(UTC)),
    )

    def execute_ready_search(eligible: SearchEligibleRequirement) -> None:
        search_result = search_execution.execute(
            requirement=eligible.requirement,
            validation=eligible.validation,
        )
        if search_result.status is not SearchExecutionStatus.SNAPSHOT_READY:
            _record_search_outcome(
                publication_repository=publication_repository,
                eligible=eligible,
                search_result=search_result,
            )
            return
        if eligible.command.max_price_cny is not None:
            decision_result = minimal_decision.execute(
                search_result=search_result,
                execution_id=ExecutionId(eligible.execution_id),
                max_price_cny=eligible.command.max_price_cny,
            )
            app.state.last_minimal_decision = decision_result
            snapshot = search_result.snapshot_outcome.snapshot if search_result.snapshot_outcome else None
            if snapshot is None:
                publication_repository.record_outcome(
                    conversation_id=eligible.conversation_id,
                    outcome=PublicWorkflowOutcome.PROVIDER_ERROR,
                    requirement_id=eligible.requirement_id,
                    requirement_version=eligible.requirement_version,
                    execution_id=eligible.execution_id,
                )
                return
            published = publisher.publish(
                conversation_id=eligible.conversation_id,
                requirement_id=eligible.requirement_id,
                decision_result=decision_result,
                snapshot=snapshot,
            )
            if published is not None:
                publication_repository.save_current(published)
                return
            publication_repository.record_outcome(
                conversation_id=eligible.conversation_id,
                outcome=outcome_from_decision(decision_result.status),
                requirement_id=eligible.requirement_id,
                requirement_version=eligible.requirement_version,
                execution_id=eligible.execution_id,
            )
            return
        publication_repository.record_outcome(
            conversation_id=eligible.conversation_id,
            outcome=PublicWorkflowOutcome.NOT_READY,
            requirement_id=eligible.requirement_id,
            requirement_version=eligible.requirement_version,
            execution_id=eligible.execution_id,
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
    app.include_router(
        create_structured_entry_router(
            structured_entry,
            publication_repository=publication_repository,
        )
    )
    app.state.search_execution = search_execution
    app.state.minimal_decision = minimal_decision
    app.state.last_minimal_decision = None
    app.state.publication_repository = publication_repository

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


def _build_minimal_decision() -> ExecuteMinimalDecision:
    return ExecuteMinimalDecision(
        max_price_filter_factory=MaxPriceFilter.cny,
        ranking=LowerPriceRanking(),
        selector=RecommendationSelector(),
        id_factory=lambda: str(uuid4()),
        generated_at=lambda: DomainInstant(datetime.now(UTC)),
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


def _record_search_outcome(
    *,
    publication_repository: InMemoryPublicationRepository,
    eligible: SearchEligibleRequirement,
    search_result: SearchExecutionResult,
) -> None:
    if search_result.status is SearchExecutionStatus.SEARCH_EMPTY:
        outcome = PublicWorkflowOutcome.SEARCH_EMPTY
    elif search_result.status is SearchExecutionStatus.PROVIDER_ERROR:
        outcome = PublicWorkflowOutcome.PROVIDER_ERROR
    else:
        outcome = PublicWorkflowOutcome.NOT_READY
    publication_repository.record_outcome(
        conversation_id=eligible.conversation_id,
        outcome=outcome,
        requirement_id=eligible.requirement_id,
        requirement_version=eligible.requirement_version,
        execution_id=eligible.execution_id,
    )


app = create_app()
