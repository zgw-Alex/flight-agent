"""Authoritative PATCH construction and application boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from flight_agent.domain.requirements import (
    ClearTarget,
    ConstraintId,
    HardConstraint,
    PatchSet,
    PatchTarget,
    PreferenceId,
    RequirementPatch,
    RequirementState,
    SoftPreference,
)
from flight_agent.domain.shared import DomainInstant, DomainInvariantViolation
from flight_agent.ports import PatchProposalAction, PatchProposalOperation, PatchRequirementProposal


class PatchTransitionStatus(str, Enum):
    APPLIED = "APPLIED"
    NO_CHANGE = "NO_CHANGE"
    NEEDS_CLARIFICATION_BEFORE_COMMIT = "NEEDS_CLARIFICATION_BEFORE_COMMIT"
    INVALID_TRANSITION = "INVALID_TRANSITION"


class PatchTransitionIssueCode(str, Enum):
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    EMPTY_PATCH = "EMPTY_PATCH"
    INVALID_OPERATION = "INVALID_OPERATION"
    NOT_FOUND = "NOT_FOUND"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    TYPE_MISMATCH = "TYPE_MISMATCH"


@dataclass(frozen=True)
class PatchTransitionIssue:
    code: PatchTransitionIssueCode
    message: str


@dataclass(frozen=True)
class PatchConstructionResult:
    patch_set: PatchSet | None = None
    issues: tuple[PatchTransitionIssue, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.patch_set is not None and len(self.issues) == 0


@dataclass(frozen=True)
class PatchApplicationResult:
    status: PatchTransitionStatus
    requirement: RequirementState | None = None
    patch_set: PatchSet | None = None
    issues: tuple[PatchTransitionIssue, ...] = ()


def construct_patch_set(
    current: RequirementState, proposal: PatchRequirementProposal
) -> PatchConstructionResult:
    if proposal.unresolved_semantics:
        return PatchConstructionResult(
            issues=(
                PatchTransitionIssue(
                    PatchTransitionIssueCode.AMBIGUOUS_TARGET,
                    "Patch proposal contains unresolved semantics",
                ),
            )
        )
    if len(proposal.operations) == 0:
        return PatchConstructionResult(
            issues=(
                PatchTransitionIssue(
                    PatchTransitionIssueCode.EMPTY_PATCH,
                    "Patch proposal must contain at least one operation",
                ),
            )
        )

    patches: list[RequirementPatch] = []
    issues: list[PatchTransitionIssue] = []
    touched: set[ConstraintId | PreferenceId | ClearTarget] = set()
    allocated_constraint_ids = {constraint.constraint_id for constraint in current.constraints}
    allocated_preference_ids = {preference.preference_id for preference in current.preferences}

    for index, operation in enumerate(proposal.operations, start=1):
        patch = _construct_patch(
            current,
            operation,
            index,
            allocated_constraint_ids,
            allocated_preference_ids,
        )
        if isinstance(patch, PatchTransitionIssue):
            issues.append(patch)
            continue
        conflict = _operation_conflict(patch, touched)
        if conflict is not None:
            issues.append(conflict)
            continue
        patches.append(patch)

    if issues:
        return PatchConstructionResult(issues=tuple(issues))

    try:
        return PatchConstructionResult(
            patch_set=PatchSet(current.version, tuple(patches)),
        )
    except DomainInvariantViolation as exc:
        return PatchConstructionResult(
            issues=(
                PatchTransitionIssue(
                    PatchTransitionIssueCode.INVALID_OPERATION,
                    str(exc),
                ),
            )
        )


def apply_patch_proposal(
    current: RequirementState,
    proposal: PatchRequirementProposal,
    recorded_at: DomainInstant,
) -> PatchApplicationResult:
    construction = construct_patch_set(current, proposal)
    if not construction.accepted or construction.patch_set is None:
        status = (
            PatchTransitionStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT
            if any(issue.code is PatchTransitionIssueCode.AMBIGUOUS_TARGET for issue in construction.issues)
            else PatchTransitionStatus.INVALID_TRANSITION
        )
        return PatchApplicationResult(status=status, issues=construction.issues)

    try:
        candidate = current.apply(construction.patch_set, recorded_at=recorded_at)
    except DomainInvariantViolation as exc:
        return PatchApplicationResult(
            status=PatchTransitionStatus.INVALID_TRANSITION,
            patch_set=construction.patch_set,
            issues=(
                PatchTransitionIssue(
                    PatchTransitionIssueCode.INVALID_OPERATION,
                    str(exc),
                ),
            ),
        )

    if candidate is current:
        return PatchApplicationResult(
            status=PatchTransitionStatus.NO_CHANGE,
            requirement=current,
            patch_set=construction.patch_set,
        )
    return PatchApplicationResult(
        status=PatchTransitionStatus.APPLIED,
        requirement=candidate,
        patch_set=construction.patch_set,
    )


def _construct_patch(
    current: RequirementState,
    operation: PatchProposalOperation,
    operation_index: int,
    allocated_constraint_ids: set[ConstraintId],
    allocated_preference_ids: set[PreferenceId],
) -> RequirementPatch | PatchTransitionIssue:
    action = operation.action
    if action is PatchProposalAction.ADD_CONSTRAINT:
        if not isinstance(operation.item, HardConstraint):
            return _type_mismatch("ADD_CONSTRAINT requires a hard constraint item")
        constraint_id = _next_constraint_id(current, operation_index, allocated_constraint_ids)
        allocated_constraint_ids.add(constraint_id)
        return RequirementPatch.add(
            replace(
                operation.item,
                constraint_id=constraint_id,
            )
        )
    if action is PatchProposalAction.ADD_PREFERENCE:
        if not isinstance(operation.item, SoftPreference):
            return _type_mismatch("ADD_PREFERENCE requires a soft preference item")
        preference_id = _next_preference_id(current, operation_index, allocated_preference_ids)
        allocated_preference_ids.add(preference_id)
        return RequirementPatch.add(
            replace(
                operation.item,
                preference_id=preference_id,
            )
        )
    if action is PatchProposalAction.REPLACE_CONSTRAINT:
        target = _constraint_target(current, operation.target_id)
        if isinstance(target, PatchTransitionIssue):
            return target
        if not isinstance(operation.item, HardConstraint):
            return _type_mismatch("REPLACE_CONSTRAINT requires a hard constraint item")
        return RequirementPatch.replace(
            target,
            replace(operation.item, constraint_id=target.item_id),
        )
    if action is PatchProposalAction.REPLACE_PREFERENCE:
        target = _preference_target(current, operation.target_id)
        if isinstance(target, PatchTransitionIssue):
            return target
        if not isinstance(operation.item, SoftPreference):
            return _type_mismatch("REPLACE_PREFERENCE requires a soft preference item")
        return RequirementPatch.replace(
            target,
            replace(operation.item, preference_id=target.item_id),
        )
    if action is PatchProposalAction.REMOVE_CONSTRAINT:
        target = _constraint_target(current, operation.target_id)
        return target if isinstance(target, PatchTransitionIssue) else RequirementPatch.remove(target)
    if action is PatchProposalAction.REMOVE_PREFERENCE:
        target = _preference_target(current, operation.target_id)
        return target if isinstance(target, PatchTransitionIssue) else RequirementPatch.remove(target)
    if action is PatchProposalAction.CLEAR_CONSTRAINTS:
        return RequirementPatch.clear(ClearTarget.HARD_CONSTRAINTS)
    if action is PatchProposalAction.CLEAR_PREFERENCES:
        return RequirementPatch.clear(ClearTarget.SOFT_PREFERENCES)
    return PatchTransitionIssue(
        PatchTransitionIssueCode.INVALID_OPERATION,
        f"Unsupported patch proposal action: {action.value}",
    )


def _constraint_target(
    current: RequirementState, target_id: ConstraintId | PreferenceId | None
) -> PatchTarget | PatchTransitionIssue:
    if target_id is None:
        return PatchTransitionIssue(
            PatchTransitionIssueCode.AMBIGUOUS_TARGET,
            "Constraint operation requires an explicit target",
        )
    if isinstance(target_id, PreferenceId):
        return _type_mismatch("Constraint operation received a preference target")
    if target_id not in {constraint.constraint_id for constraint in current.constraints}:
        return PatchTransitionIssue(
            PatchTransitionIssueCode.NOT_FOUND,
            "Constraint target does not exist in current requirement",
        )
    return PatchTarget(target_id)


def _preference_target(
    current: RequirementState, target_id: ConstraintId | PreferenceId | None
) -> PatchTarget | PatchTransitionIssue:
    if target_id is None:
        return PatchTransitionIssue(
            PatchTransitionIssueCode.AMBIGUOUS_TARGET,
            "Preference operation requires an explicit target",
        )
    if isinstance(target_id, ConstraintId):
        return _type_mismatch("Preference operation received a constraint target")
    if target_id not in {preference.preference_id for preference in current.preferences}:
        return PatchTransitionIssue(
            PatchTransitionIssueCode.NOT_FOUND,
            "Preference target does not exist in current requirement",
        )
    return PatchTarget(target_id)


def _operation_conflict(
    patch: RequirementPatch,
    touched: set[ConstraintId | PreferenceId | ClearTarget],
) -> PatchTransitionIssue | None:
    marker = _patch_marker(patch)
    if marker is ClearTarget.ALL:
        if touched:
            return _conflict("CLEAR ALL cannot be combined with other operations")
        touched.add(marker)
        return None
    if ClearTarget.ALL in touched or marker in touched:
        return _conflict("Patch proposal contains duplicate or conflicting operations")
    if _clear_item_conflict(marker, touched):
        return _conflict("CLEAR cannot be combined with item operations in the same typed scope")
    if _item_clear_conflict(marker, touched):
        return _conflict("Item operation cannot be combined with CLEAR in the same typed scope")
    touched.add(marker)
    return None


def _patch_marker(patch: RequirementPatch) -> ConstraintId | PreferenceId | ClearTarget:
    if patch.clear_target is not None:
        return patch.clear_target
    if patch.target is not None:
        return patch.target.item_id
    if isinstance(patch.item, HardConstraint):
        return patch.item.constraint_id
    if isinstance(patch.item, SoftPreference):
        return patch.item.preference_id
    raise DomainInvariantViolation("Patch has no operation marker")


def _clear_item_conflict(
    marker: ConstraintId | PreferenceId | ClearTarget,
    touched: set[ConstraintId | PreferenceId | ClearTarget],
) -> bool:
    if marker is ClearTarget.HARD_CONSTRAINTS:
        return any(isinstance(item, ConstraintId) for item in touched)
    if marker is ClearTarget.SOFT_PREFERENCES:
        return any(isinstance(item, PreferenceId) for item in touched)
    return False


def _item_clear_conflict(
    marker: ConstraintId | PreferenceId | ClearTarget,
    touched: set[ConstraintId | PreferenceId | ClearTarget],
) -> bool:
    return (
        isinstance(marker, ConstraintId)
        and ClearTarget.HARD_CONSTRAINTS in touched
        or isinstance(marker, PreferenceId)
        and ClearTarget.SOFT_PREFERENCES in touched
    )


def _next_constraint_id(
    current: RequirementState,
    operation_index: int,
    allocated_constraint_ids: set[ConstraintId],
) -> ConstraintId:
    candidate_index = operation_index
    while True:
        candidate = ConstraintId(f"constraint-v{current.version.value + 1}-{candidate_index}")
        if candidate not in allocated_constraint_ids:
            return candidate
        candidate_index += 1


def _next_preference_id(
    current: RequirementState,
    operation_index: int,
    allocated_preference_ids: set[PreferenceId],
) -> PreferenceId:
    candidate_index = operation_index
    while True:
        candidate = PreferenceId(f"preference-v{current.version.value + 1}-{candidate_index}")
        if candidate not in allocated_preference_ids:
            return candidate
        candidate_index += 1


def _type_mismatch(message: str) -> PatchTransitionIssue:
    return PatchTransitionIssue(PatchTransitionIssueCode.TYPE_MISMATCH, message)


def _conflict(message: str) -> PatchTransitionIssue:
    return PatchTransitionIssue(PatchTransitionIssueCode.OPERATION_CONFLICT, message)
