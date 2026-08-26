"""M5-U1 structured public entry use case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from flight_agent.application.requirement_normalization import (
    NormalizationContext,
    RequirementValidationResult,
    SearchReadinessStatus,
)
from flight_agent.application.requirement_pipeline import (
    RequirementPipelineOutcome,
    RequirementPipelineOutcomeStatus,
    execute_initial_requirement,
)
from flight_agent.domain.flights import Money
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
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    InitialInterpreterPayload,
    InitialRequirementProposal,
    InterpreterFailure,
    InterpreterInput,
    InterpreterMode,
    InterpreterResult,
    RequirementInterpretationContext,
    RequirementInterpreter,
    RequirementRepository,
)


class StructuredEntryStatus(str, Enum):
    SEARCH_ELIGIBLE = "SEARCH_ELIGIBLE"
    NOT_READY = "NOT_READY"
    NEEDS_CLARIFICATION_BEFORE_COMMIT = "NEEDS_CLARIFICATION_BEFORE_COMMIT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StructuredRequirementCommand:
    origin: str
    destination: str
    departure_date: date | None
    max_price_cny: int | None = None
    lower_price_preferred: bool = False
    source_input: str = "structured-public-requirement"


@dataclass(frozen=True)
class SearchEligibleRequirement:
    conversation_id: str
    execution_id: str
    requirement_id: RequirementId
    requirement_version: int
    requirement: RequirementState
    validation: RequirementValidationResult
    command: StructuredRequirementCommand


@dataclass(frozen=True)
class StructuredEntryResult:
    status: StructuredEntryStatus
    conversation_id: str
    execution_id: str
    pipeline_outcome: RequirementPipelineOutcome
    downstream_search_eligible: bool

    @property
    def requirement_id(self) -> RequirementId | None:
        if self.pipeline_outcome.requirement is None:
            return None
        return self.pipeline_outcome.requirement.requirement_id

    @property
    def requirement_version(self) -> int | None:
        if self.pipeline_outcome.requirement is None:
            return None
        return self.pipeline_outcome.requirement.version.value

    @property
    def readiness(self) -> SearchReadinessStatus | None:
        if self.pipeline_outcome.validation is None:
            return None
        return self.pipeline_outcome.validation.readiness


IdFactory = Callable[[], str]
EligibilityCallback = Callable[[SearchEligibleRequirement], None]


class StartStructuredRequirement:
    def __init__(
        self,
        *,
        repository: RequirementRepository,
        normalization_context: NormalizationContext,
        recorded_at: Callable[[], DomainInstant],
        id_factory: IdFactory,
        on_search_eligible: EligibilityCallback | None = None,
    ) -> None:
        self._repository = repository
        self._normalization_context = normalization_context
        self._recorded_at = recorded_at
        self._id_factory = id_factory
        self._on_search_eligible = on_search_eligible

    def start(self, command: StructuredRequirementCommand) -> StructuredEntryResult:
        conversation_id = self._id_factory()
        execution_id = self._id_factory()
        requirement_id = RequirementId(self._id_factory())
        operation_id = self._id_factory()
        proposal = structured_command_to_initial_proposal(command)

        outcome = execute_initial_requirement(
            repository=self._repository,
            interpreter=_require_interpreter_protocol(_InitialProposalInterpreter(proposal)),
            interpreter_input=InterpreterInput(
                mode=InterpreterMode.INITIAL,
                payload=InitialInterpreterPayload(command.source_input),
            ),
            normalization_context=self._normalization_context,
            requirement_id=requirement_id,
            operation_id=operation_id,
            recorded_at=self._recorded_at(),
        )

        downstream_search_eligible = (
            outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
            and outcome.requirement is not None
            and outcome.validation is not None
            and outcome.validation.readiness is SearchReadinessStatus.READY
        )
        if downstream_search_eligible and self._on_search_eligible is not None:
            committed_requirement = outcome.requirement
            committed_validation = outcome.validation
            if committed_requirement is None or committed_validation is None:
                raise RuntimeError("Search eligibility requires a committed requirement")
            self._on_search_eligible(
                SearchEligibleRequirement(
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    requirement_id=committed_requirement.requirement_id,
                    requirement_version=committed_requirement.version.value,
                    requirement=committed_requirement,
                    validation=committed_validation,
                    command=command,
                )
            )

        return StructuredEntryResult(
            status=_structured_status(outcome, downstream_search_eligible),
            conversation_id=conversation_id,
            execution_id=execution_id,
            pipeline_outcome=outcome,
            downstream_search_eligible=downstream_search_eligible,
        )


def structured_command_to_initial_proposal(
    command: StructuredRequirementCommand,
) -> InitialRequirementProposal:
    constraints: list[HardConstraint] = [
        _airport_constraint("structured-origin", ConstraintScope.ORIGIN_AIRPORT, command.origin),
        _airport_constraint(
            "structured-destination",
            ConstraintScope.DESTINATION_AIRPORT,
            command.destination,
        ),
    ]
    if command.departure_date is not None:
        constraints.append(
            HardConstraint(
                constraint_id=ConstraintId("structured-departure-date"),
                scope=ConstraintScope.DEPARTURE_DATE,
                operator=ConstraintOperator.EQUALS,
                value=LocalDate(command.departure_date),
            )
        )
    if command.max_price_cny is not None:
        constraints.append(
            HardConstraint(
                constraint_id=ConstraintId("structured-max-price"),
                scope=ConstraintScope.MAX_PRICE,
                operator=ConstraintOperator.AT_OR_BEFORE,
                value=Money(Decimal(command.max_price_cny), "CNY"),
            )
        )

    preferences: list[SoftPreference] = []
    if command.lower_price_preferred:
        preferences.append(
            SoftPreference(
                preference_id=PreferenceId("structured-lower-price"),
                scope=PreferenceScope.PRICE,
                importance=PreferenceImportance.HIGH,
                value=None,
            )
        )

    return InitialRequirementProposal(
        constraints=tuple(constraints),
        preferences=tuple(preferences),
        source_input=command.source_input,
    )


def _airport_constraint(raw_id: str, scope: ConstraintScope, value: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=scope,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(value),
    )


def _structured_status(
    outcome: RequirementPipelineOutcome,
    downstream_search_eligible: bool,
) -> StructuredEntryStatus:
    if downstream_search_eligible:
        return StructuredEntryStatus.SEARCH_ELIGIBLE
    if (
        outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
        and outcome.validation is not None
        and outcome.validation.readiness is SearchReadinessStatus.NOT_READY
    ):
        return StructuredEntryStatus.NOT_READY
    if outcome.status is RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT:
        return StructuredEntryStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT
    return StructuredEntryStatus.FAILED


@dataclass(frozen=True)
class _InitialProposalInterpreter:
    proposal: InitialRequirementProposal

    def interpret(
        self,
        interpreter_input: InterpreterInput,
        context: RequirementInterpretationContext | None = None,
    ) -> InterpreterResult:
        if interpreter_input.mode is not InterpreterMode.INITIAL or context is not None:
            return InterpreterResult.failure_result(
                InterpreterFailure(
                    code="UNSUPPORTED_STRUCTURED_ENTRY_MODE",
                    message="Structured entry only supports initial proposals",
                    source_input=interpreter_input.source_input,
                )
            )
        return InterpreterResult.success(self.proposal)


def _require_interpreter_protocol(interpreter: RequirementInterpreter) -> RequirementInterpreter:
    return interpreter
