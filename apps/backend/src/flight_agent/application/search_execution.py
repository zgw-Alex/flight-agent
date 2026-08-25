"""M5-U2 search execution composition over the closed M4 pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from flight_agent.application.requirement_normalization import (
    RequirementValidationResult,
    SearchReadinessStatus,
)
from flight_agent.application.snapshot_assembly import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    FixtureSchemaVersion,
    SnapshotAssemblyInput,
    SnapshotAssemblyOutcome,
    SnapshotCreationStatus,
    build_processing_manifest,
)
from flight_agent.domain.flights import CandidateSnapshotId
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementState,
)
from flight_agent.domain.search import (
    DepartureDateScope,
    DestinationScope,
    OriginScope,
    RequestedSearchScope,
    SearchPlan,
    SearchPlanId,
)
from flight_agent.domain.shared import DomainInstant, DomainInvariantViolation
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    FlightProvider,
    NormalizationContext,
    ProviderMapper,
    ProviderMappingResult,
    ProviderSearchResult,
)


class SearchExecutionStatus(str, Enum):
    SNAPSHOT_READY = "SNAPSHOT_READY"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class SearchExecutionResult:
    status: SearchExecutionStatus
    search_plan: SearchPlan | None
    provider_result: ProviderSearchResult | None
    mapping_result: ProviderMappingResult | None
    snapshot_outcome: SnapshotAssemblyOutcome | None

    @property
    def downstream_decision_eligible(self) -> bool:
        return (
            self.status is SearchExecutionStatus.SNAPSHOT_READY
            and self.snapshot_outcome is not None
            and self.snapshot_outcome.snapshot is not None
        )


IdFactory = Callable[[], str]


class ExecuteReadyRequirementSearch:
    def __init__(
        self,
        *,
        flight_provider: FlightProvider,
        provider_mapper: ProviderMapper,
        common_normalizer: CommonNormalizer,
        normalization_context: NormalizationContext,
        candidate_merger: CandidateMerger,
        snapshot_assembler: CandidateSnapshotAssembler,
        assembler_version: AssemblerVersion,
        fixture_schema_versions: tuple[FixtureSchemaVersion, ...],
        id_factory: IdFactory,
        created_at: Callable[[], DomainInstant],
    ) -> None:
        self._flight_provider = flight_provider
        self._provider_mapper = provider_mapper
        self._common_normalizer = common_normalizer
        self._normalization_context = normalization_context
        self._candidate_merger = candidate_merger
        self._snapshot_assembler = snapshot_assembler
        self._assembler_version = assembler_version
        self._fixture_schema_versions = fixture_schema_versions
        self._id_factory = id_factory
        self._created_at = created_at

    def execute(
        self,
        *,
        requirement: RequirementState,
        validation: RequirementValidationResult,
    ) -> SearchExecutionResult:
        if validation.based_on != requirement.version:
            raise DomainInvariantViolation("Search readiness must be bound to the committed requirement version")
        if validation.readiness is not SearchReadinessStatus.READY:
            return SearchExecutionResult(
                status=SearchExecutionStatus.NOT_READY,
                search_plan=None,
                provider_result=None,
                mapping_result=None,
                snapshot_outcome=None,
            )

        search_plan = plan_search(requirement, search_plan_id=SearchPlanId(self._id_factory()))
        provider_result = self._flight_provider.search(search_plan)
        mapping_result = self._provider_mapper.map(provider_result)
        normalization_result = self._common_normalizer.normalize(
            mapping_result,
            self._normalization_context,
        )
        merged_graph = self._candidate_merger.merge((normalization_result,))
        snapshot_outcome = self._snapshot_assembler.assemble(
            SnapshotAssemblyInput(
                search_plan=search_plan,
                merged_graph=merged_graph,
                provider_results=(provider_result,),
                snapshot_id=CandidateSnapshotId(self._id_factory()),
                created_at=self._created_at(),
                processing_manifest=build_processing_manifest(
                    fixture_schema_versions=self._fixture_schema_versions,
                    merged_graph=merged_graph,
                    assembler_version=self._assembler_version,
                ),
            )
        )
        return SearchExecutionResult(
            status=_search_execution_status(snapshot_outcome),
            search_plan=search_plan,
            provider_result=provider_result,
            mapping_result=mapping_result,
            snapshot_outcome=snapshot_outcome,
        )


def plan_search(requirement: RequirementState, *, search_plan_id: SearchPlanId) -> SearchPlan:
    return SearchPlan(
        search_plan_id=search_plan_id,
        requirement_id=requirement.requirement_id,
        based_on_requirement_version=requirement.version,
        requested_scope=RequestedSearchScope(
            origin=OriginScope(_constraint_value(requirement, ConstraintScope.ORIGIN_AIRPORT, AirportCode)),
            destination=DestinationScope(
                _constraint_value(requirement, ConstraintScope.DESTINATION_AIRPORT, AirportCode)
            ),
            departure_date=DepartureDateScope(
                _constraint_value(requirement, ConstraintScope.DEPARTURE_DATE, LocalDate)
            ),
        ),
    )


def _constraint_value[T](
    requirement: RequirementState,
    scope: ConstraintScope,
    expected_type: type[T],
) -> T:
    matches = [
        constraint.value
        for constraint in requirement.constraints
        if _is_single_equals_constraint(constraint, scope)
    ]
    if len(matches) != 1 or not isinstance(matches[0], expected_type):
        raise DomainInvariantViolation("READY requirement is missing a single provider-neutral search scope fact")
    return matches[0]


def _is_single_equals_constraint(constraint: HardConstraint, scope: ConstraintScope) -> bool:
    return constraint.scope is scope and constraint.operator is ConstraintOperator.EQUALS


def _search_execution_status(
    snapshot_outcome: SnapshotAssemblyOutcome,
) -> SearchExecutionStatus:
    if snapshot_outcome.status is SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT:
        return SearchExecutionStatus.SEARCH_EMPTY
    if snapshot_outcome.status is SnapshotCreationStatus.NO_NEW_SNAPSHOT:
        return SearchExecutionStatus.PROVIDER_ERROR
    return SearchExecutionStatus.SNAPSHOT_READY
