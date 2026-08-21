"""Immutable Requirement aggregate snapshot and transitions."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.requirements.constraints import HardConstraint, SoftPreference
from flight_agent.domain.requirements.identity import ConstraintId, PreferenceId, RequirementId
from flight_agent.domain.requirements.patch import (
    ClearTarget,
    PatchOperation,
    PatchSet,
    PatchTarget,
    RequirementPatch,
)
from flight_agent.domain.shared import DomainInstant, DomainInvariantViolation, RequirementVersion


@dataclass(frozen=True, init=False)
class RequirementState:
    requirement_id: RequirementId
    version: RequirementVersion
    recorded_at: DomainInstant
    predecessor_version: RequirementVersion | None
    constraints: tuple[HardConstraint, ...]
    preferences: tuple[SoftPreference, ...]

    def __init__(
        self,
        requirement_id: RequirementId,
        version: RequirementVersion,
        recorded_at: DomainInstant,
        predecessor_version: RequirementVersion | None = None,
        constraints: tuple[HardConstraint, ...] = (),
        preferences: tuple[SoftPreference, ...] = (),
    ) -> None:
        constraints_tuple = tuple(constraints)
        preferences_tuple = tuple(preferences)
        _validate_lineage(version, predecessor_version)
        _ensure_unique_constraint_ids(constraints_tuple)
        _ensure_unique_preference_ids(preferences_tuple)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "predecessor_version", predecessor_version)
        object.__setattr__(self, "constraints", constraints_tuple)
        object.__setattr__(self, "preferences", preferences_tuple)

    @classmethod
    def initial(
        cls,
        requirement_id: RequirementId,
        recorded_at: DomainInstant,
        constraints: tuple[HardConstraint, ...] = (),
        preferences: tuple[SoftPreference, ...] = (),
    ) -> RequirementState:
        return cls(
            requirement_id=requirement_id,
            version=RequirementVersion(1),
            recorded_at=recorded_at,
            predecessor_version=None,
            constraints=constraints,
            preferences=preferences,
        )

    def apply(self, patch_set: PatchSet, recorded_at: DomainInstant) -> RequirementState:
        if patch_set.base_requirement_version != self.version:
            raise DomainInvariantViolation("PatchSet base version is stale")

        constraints = {constraint.constraint_id: constraint for constraint in self.constraints}
        preferences = {preference.preference_id: preference for preference in self.preferences}
        touched: set[ConstraintId | PreferenceId | ClearTarget] = set()

        for patch in patch_set.patches:
            _reject_duplicate_patch_target(patch, touched)
            _apply_patch(patch, constraints, preferences)

        next_constraints = tuple(constraints.values())
        next_preferences = tuple(preferences.values())
        if next_constraints == self.constraints and next_preferences == self.preferences:
            return self

        return RequirementState(
            requirement_id=self.requirement_id,
            version=RequirementVersion(self.version.value + 1),
            recorded_at=recorded_at,
            predecessor_version=self.version,
            constraints=next_constraints,
            preferences=next_preferences,
        )


def _validate_lineage(
    version: RequirementVersion, predecessor_version: RequirementVersion | None
) -> None:
    if version.value == 1:
        if predecessor_version is not None:
            raise DomainInvariantViolation("Initial RequirementState must not have predecessor")
    elif predecessor_version is None or predecessor_version.value != version.value - 1:
        raise DomainInvariantViolation("RequirementState predecessor must be the direct prior version")


def _ensure_unique_constraint_ids(constraints: tuple[HardConstraint, ...]) -> None:
    seen: set[ConstraintId] = set()
    for constraint in constraints:
        if constraint.constraint_id in seen:
            raise DomainInvariantViolation("RequirementState constraint identities must be unique")
        seen.add(constraint.constraint_id)


def _ensure_unique_preference_ids(preferences: tuple[SoftPreference, ...]) -> None:
    seen: set[PreferenceId] = set()
    for preference in preferences:
        if preference.preference_id in seen:
            raise DomainInvariantViolation("RequirementState preference identities must be unique")
        seen.add(preference.preference_id)


def _reject_duplicate_patch_target(
    patch: RequirementPatch, touched: set[ConstraintId | PreferenceId | ClearTarget]
) -> None:
    marker = _patch_marker(patch)
    conflicting_clear = (
        ClearTarget.ALL
        if not isinstance(marker, ClearTarget)
        else None
    )
    if marker in touched or (conflicting_clear is not None and conflicting_clear in touched):
        raise DomainInvariantViolation("PatchSet must not contain duplicate or ambiguous operations")
    if marker is ClearTarget.ALL and len(touched) > 0:
        raise DomainInvariantViolation("CLEAR ALL cannot be combined with other operations")
    touched.add(marker)


def _patch_marker(patch: RequirementPatch) -> ConstraintId | PreferenceId | ClearTarget:
    if patch.operation is PatchOperation.ADD:
        if isinstance(patch.item, HardConstraint):
            return patch.item.constraint_id
        if isinstance(patch.item, SoftPreference):
            return patch.item.preference_id
    if patch.target is not None:
        return patch.target.item_id
    if patch.clear_target is not None:
        return patch.clear_target
    raise DomainInvariantViolation("Patch has no typed target")


def _apply_patch(
    patch: RequirementPatch,
    constraints: dict[ConstraintId, HardConstraint],
    preferences: dict[PreferenceId, SoftPreference],
) -> None:
    if patch.operation is PatchOperation.ADD:
        _apply_add(patch, constraints, preferences)
    elif patch.operation is PatchOperation.REPLACE:
        _apply_replace(patch, constraints, preferences)
    elif patch.operation is PatchOperation.REMOVE:
        _apply_remove(patch.target, constraints, preferences)
    elif patch.operation is PatchOperation.CLEAR:
        _apply_clear(patch.clear_target, constraints, preferences)


def _apply_add(
    patch: RequirementPatch,
    constraints: dict[ConstraintId, HardConstraint],
    preferences: dict[PreferenceId, SoftPreference],
) -> None:
    if isinstance(patch.item, HardConstraint):
        if patch.item.constraint_id in constraints:
            raise DomainInvariantViolation("ADD constraint target already exists")
        constraints[patch.item.constraint_id] = patch.item
    elif isinstance(patch.item, SoftPreference):
        if patch.item.preference_id in preferences:
            raise DomainInvariantViolation("ADD preference target already exists")
        preferences[patch.item.preference_id] = patch.item


def _apply_replace(
    patch: RequirementPatch,
    constraints: dict[ConstraintId, HardConstraint],
    preferences: dict[PreferenceId, SoftPreference],
) -> None:
    if isinstance(patch.item, HardConstraint):
        if patch.item.constraint_id not in constraints:
            raise DomainInvariantViolation("REPLACE constraint target does not exist")
        constraints[patch.item.constraint_id] = patch.item
    elif isinstance(patch.item, SoftPreference):
        if patch.item.preference_id not in preferences:
            raise DomainInvariantViolation("REPLACE preference target does not exist")
        preferences[patch.item.preference_id] = patch.item


def _apply_remove(
    target: PatchTarget | None,
    constraints: dict[ConstraintId, HardConstraint],
    preferences: dict[PreferenceId, SoftPreference],
) -> None:
    if target is None:
        raise DomainInvariantViolation("REMOVE requires a target")
    if target.is_constraint:
        if target.item_id not in constraints:
            raise DomainInvariantViolation("REMOVE constraint target does not exist")
        del constraints[target.item_id]
    elif target.is_preference:
        if target.item_id not in preferences:
            raise DomainInvariantViolation("REMOVE preference target does not exist")
        del preferences[target.item_id]


def _apply_clear(
    target: ClearTarget | None,
    constraints: dict[ConstraintId, HardConstraint],
    preferences: dict[PreferenceId, SoftPreference],
) -> None:
    if target is ClearTarget.HARD_CONSTRAINTS:
        constraints.clear()
    elif target is ClearTarget.SOFT_PREFERENCES:
        preferences.clear()
    elif target is ClearTarget.ALL:
        constraints.clear()
        preferences.clear()
    else:
        raise DomainInvariantViolation("CLEAR requires a valid clear target")
