"""M3 requirement lifecycle sequencing for INITIAL and PATCH."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from flight_agent.application.requirement_commit import commit_requirement_transition
from flight_agent.application.requirement_interpretation import interpret_requirement
from flight_agent.application.requirement_normalization import (
    NormalizationContext,
    NormalizationIssue,
    RequirementValidationResult,
    normalize_initial_requirement,
    normalize_patch_requirement,
    validate_requirement,
)
from flight_agent.application.requirement_transition import (
    PatchTransitionIssue,
    PatchTransitionStatus,
    apply_patch_proposal,
)
from flight_agent.domain.requirements import (
    HardConstraint,
    RequirementId,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    CommitStatus,
    InitialRequirementProposal,
    InterpreterInput,
    InterpreterMode,
    InterpreterResultStatus,
    PatchProposalOperation,
    PatchRequirementProposal,
    RequirementCommitResult,
    RequirementInterpretationContext,
    RequirementInterpreter,
    RequirementRepository,
)

RequirementValidator = Callable[[RequirementState], RequirementValidationResult]


class RequirementPipelineOutcomeStatus(str, Enum):
    COMMITTED = "COMMITTED"
    NO_CHANGE = "NO_CHANGE"
    NEEDS_CLARIFICATION_BEFORE_COMMIT = "NEEDS_CLARIFICATION_BEFORE_COMMIT"
    INTERPRETATION_FAILED = "INTERPRETATION_FAILED"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    COMMIT_REJECTED = "COMMIT_REJECTED"


class PostCommitProcessingStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RequirementPipelineOutcome:
    status: RequirementPipelineOutcomeStatus
    requirement: RequirementState | None = None
    validation: RequirementValidationResult | None = None
    commit_status: CommitStatus | None = None
    post_commit_processing: PostCommitProcessingStatus = PostCommitProcessingStatus.NOT_RUN
    interpretation_message: str = ""
    normalization_issues: tuple[NormalizationIssue, ...] = ()
    transition_issues: tuple[PatchTransitionIssue, ...] = ()
    commit_message: str = ""
    post_commit_error: str = ""


def execute_initial_requirement(
    *,
    repository: RequirementRepository,
    interpreter: RequirementInterpreter,
    interpreter_input: InterpreterInput,
    normalization_context: NormalizationContext,
    requirement_id: RequirementId,
    operation_id: str,
    recorded_at: DomainInstant,
    validator: RequirementValidator = validate_requirement,
) -> RequirementPipelineOutcome:
    if interpreter_input.mode is not InterpreterMode.INITIAL:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message="Initial pipeline requires INITIAL interpreter input",
        )

    interpretation = interpret_requirement(interpreter, interpreter_input)
    if interpretation.status is InterpreterResultStatus.FAILURE:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message=interpretation.failure.message if interpretation.failure else "",
        )
    if not isinstance(interpretation.proposal, InitialRequirementProposal):
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message="Initial interpreter returned a non-initial proposal",
        )
    if interpretation.status is InterpreterResultStatus.UNRESOLVED or interpretation.proposal.unresolved_semantics:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            interpretation_message="Initial proposal contains unresolved semantics",
        )

    normalization = normalize_initial_requirement(interpretation.proposal, normalization_context)
    if normalization.needs_clarification_before_commit or normalization.candidate is None:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            normalization_issues=normalization.issues,
        )

    candidate = RequirementState.initial(
        requirement_id=requirement_id,
        recorded_at=recorded_at,
        constraints=normalization.candidate.constraints,
        preferences=normalization.candidate.preferences,
    )
    commit = repository.commit_initial(candidate, operation_id=operation_id)
    return _outcome_from_commit(commit, validator)


def execute_patch_requirement(
    *,
    repository: RequirementRepository,
    interpreter: RequirementInterpreter,
    interpreter_input: InterpreterInput,
    normalization_context: NormalizationContext,
    requirement_id: RequirementId,
    operation_id: str,
    recorded_at: DomainInstant,
    validator: RequirementValidator = validate_requirement,
) -> RequirementPipelineOutcome:
    current = repository.get_current(requirement_id)
    if current is None:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INVALID_TRANSITION,
            commit_message="Requirement chain does not exist",
        )
    return execute_patch_requirement_from_current(
        repository=repository,
        interpreter=interpreter,
        interpreter_input=interpreter_input,
        normalization_context=normalization_context,
        current=current,
        operation_id=operation_id,
        recorded_at=recorded_at,
        validator=validator,
    )


def execute_patch_requirement_from_current(
    *,
    repository: RequirementRepository,
    interpreter: RequirementInterpreter,
    interpreter_input: InterpreterInput,
    normalization_context: NormalizationContext,
    current: RequirementState,
    operation_id: str,
    recorded_at: DomainInstant,
    validator: RequirementValidator = validate_requirement,
) -> RequirementPipelineOutcome:
    if interpreter_input.mode is not InterpreterMode.PATCH:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message="Patch pipeline requires PATCH interpreter input",
        )

    interpretation = interpret_requirement(
        interpreter,
        interpreter_input,
        _interpretation_context(current),
    )
    if interpretation.status is InterpreterResultStatus.FAILURE:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message=interpretation.failure.message if interpretation.failure else "",
        )
    if not isinstance(interpretation.proposal, PatchRequirementProposal):
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
            interpretation_message="Patch interpreter returned a non-patch proposal",
        )
    if interpretation.status is InterpreterResultStatus.UNRESOLVED or interpretation.proposal.unresolved_semantics:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            interpretation_message="Patch proposal contains unresolved semantics",
        )

    normalization = normalize_patch_requirement(interpretation.proposal, normalization_context)
    if normalization.needs_clarification_before_commit or normalization.candidate is None:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            normalization_issues=normalization.issues,
        )

    normalized_proposal = _normalized_patch_proposal(interpretation.proposal, normalization.candidate.constraints, normalization.candidate.preferences)
    transition = apply_patch_proposal(current, normalized_proposal, recorded_at)
    if transition.status is PatchTransitionStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            transition_issues=transition.issues,
        )
    if transition.status is PatchTransitionStatus.INVALID_TRANSITION or transition.requirement is None:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.INVALID_TRANSITION,
            transition_issues=transition.issues,
        )
    if transition.status is PatchTransitionStatus.NO_CHANGE:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NO_CHANGE,
            requirement=current,
        )

    commit = commit_requirement_transition(
        repository,
        current,
        transition.requirement,
        operation_id=operation_id,
    )
    return _outcome_from_commit(commit, validator)


def _outcome_from_commit(
    commit: RequirementCommitResult,
    validator: RequirementValidator,
) -> RequirementPipelineOutcome:
    if commit.status in {CommitStatus.COMMITTED, CommitStatus.REPLAYED} and commit.requirement is not None:
        try:
            validation = validator(commit.requirement)
        except Exception as exc:  # noqa: BLE001 - post-commit failures must not rollback authority.
            return RequirementPipelineOutcome(
                status=RequirementPipelineOutcomeStatus.COMMITTED,
                requirement=commit.requirement,
                commit_status=commit.status,
                post_commit_processing=PostCommitProcessingStatus.FAILED,
                post_commit_error=str(exc),
            )
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.COMMITTED,
            requirement=commit.requirement,
            validation=validation,
            commit_status=commit.status,
            post_commit_processing=PostCommitProcessingStatus.SUCCEEDED,
        )
    if commit.status is CommitStatus.NO_CHANGE and commit.requirement is not None:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.NO_CHANGE,
            requirement=commit.requirement,
            commit_status=commit.status,
        )
    if commit.status is CommitStatus.CONCURRENCY_CONFLICT:
        return RequirementPipelineOutcome(
            status=RequirementPipelineOutcomeStatus.CONCURRENCY_CONFLICT,
            commit_status=commit.status,
            commit_message=commit.message,
        )
    return RequirementPipelineOutcome(
        status=RequirementPipelineOutcomeStatus.COMMIT_REJECTED,
        commit_status=commit.status,
        commit_message=commit.message,
    )


def _interpretation_context(current: RequirementState) -> RequirementInterpretationContext:
    return RequirementInterpretationContext(
        requirement_id=current.requirement_id,
        current_version=current.version,
        constraint_ids=tuple(constraint.constraint_id for constraint in current.constraints),
        preference_ids=tuple(preference.preference_id for preference in current.preferences),
        current_requirement_projection=_requirement_projection(current),
        current_requirement=current,
    )


def _normalized_patch_proposal(
    proposal: PatchRequirementProposal,
    constraints: tuple[HardConstraint, ...],
    preferences: tuple[SoftPreference, ...],
) -> PatchRequirementProposal:
    constraint_items = iter(constraints)
    preference_items = iter(preferences)
    operations: list[PatchProposalOperation] = []
    for operation in proposal.operations:
        if isinstance(operation.item, HardConstraint):
            operations.append(replace(operation, item=next(constraint_items)))
        elif isinstance(operation.item, SoftPreference):
            operations.append(replace(operation, item=next(preference_items)))
        else:
            operations.append(operation)
    return replace(proposal, operations=tuple(operations))


def _requirement_projection(current: RequirementState) -> str:
    constraints = "; ".join(
        f"{constraint.constraint_id.value}:{constraint.scope.value}:{constraint.operator.value}:"
        f"{_projection_value(constraint.value)}"
        for constraint in current.constraints
    )
    preferences = "; ".join(
        f"{preference.preference_id.value}:{preference.scope.value}:"
        f"{preference.importance.value}:{_projection_value(preference.value)}"
        for preference in current.preferences
    )
    return (
        f"requirement_id={current.requirement_id.value}; version={current.version.value}; "
        f"constraints=[{constraints or 'NONE'}]; preferences=[{preferences or 'NONE'}]"
    )


def _projection_value(value: object) -> object:
    if value is None:
        return "NONE"
    scalar = getattr(value, "value", None)
    if scalar is not None:
        return scalar
    start = getattr(value, "start", None)
    end = getattr(value, "end", None)
    if start is not None and end is not None:
        return f"{_projection_value(start)}..{_projection_value(end)}"
    amount = getattr(value, "amount", None)
    currency = getattr(value, "currency", None)
    if amount is not None and currency is not None:
        return f"{amount} {currency}"
    return value
