"""Patch and atomic transition support for RequirementState."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.requirements.constraints import HardConstraint, SoftPreference
from flight_agent.domain.requirements.identity import ConstraintId, PreferenceId
from flight_agent.domain.shared import DomainInvariantViolation, RequirementVersion

RequirementItem = HardConstraint | SoftPreference


class PatchOperation(str, Enum):
    ADD = "ADD"
    REPLACE = "REPLACE"
    REMOVE = "REMOVE"
    CLEAR = "CLEAR"


class ClearTarget(str, Enum):
    HARD_CONSTRAINTS = "HARD_CONSTRAINTS"
    SOFT_PREFERENCES = "SOFT_PREFERENCES"
    ALL = "ALL"


@dataclass(frozen=True)
class PatchTarget:
    item_id: ConstraintId | PreferenceId

    @property
    def is_constraint(self) -> bool:
        return isinstance(self.item_id, ConstraintId)

    @property
    def is_preference(self) -> bool:
        return isinstance(self.item_id, PreferenceId)


@dataclass(frozen=True)
class RequirementPatch:
    operation: PatchOperation
    item: RequirementItem | None = None
    target: PatchTarget | None = None
    clear_target: ClearTarget | None = None

    def __post_init__(self) -> None:
        if self.operation is PatchOperation.ADD:
            if self.item is None or self.target is not None or self.clear_target is not None:
                raise DomainInvariantViolation("ADD requires an item and no target")
        elif self.operation is PatchOperation.REPLACE:
            if self.item is None or self.target is None or self.clear_target is not None:
                raise DomainInvariantViolation("REPLACE requires an item and typed target")
            _validate_target_matches_item(self.target, self.item)
        elif self.operation is PatchOperation.REMOVE:
            if self.item is not None or self.target is None or self.clear_target is not None:
                raise DomainInvariantViolation("REMOVE requires only a typed target")
        elif (
            self.operation is PatchOperation.CLEAR
            and (self.item is not None or self.target is not None or self.clear_target is None)
        ):
            raise DomainInvariantViolation("CLEAR requires only a clear target")

    @classmethod
    def add(cls, item: RequirementItem) -> RequirementPatch:
        return cls(operation=PatchOperation.ADD, item=item)

    @classmethod
    def replace(cls, target: PatchTarget, item: RequirementItem) -> RequirementPatch:
        return cls(operation=PatchOperation.REPLACE, target=target, item=item)

    @classmethod
    def remove(cls, target: PatchTarget) -> RequirementPatch:
        return cls(operation=PatchOperation.REMOVE, target=target)

    @classmethod
    def clear(cls, target: ClearTarget) -> RequirementPatch:
        return cls(operation=PatchOperation.CLEAR, clear_target=target)


@dataclass(frozen=True)
class PatchSet:
    base_requirement_version: RequirementVersion
    patches: tuple[RequirementPatch, ...]

    def __init__(
        self, base_requirement_version: RequirementVersion, patches: tuple[RequirementPatch, ...]
    ) -> None:
        if len(patches) == 0:
            raise DomainInvariantViolation("PatchSet requires at least one patch")
        object.__setattr__(self, "base_requirement_version", base_requirement_version)
        object.__setattr__(self, "patches", tuple(patches))


def _validate_target_matches_item(target: PatchTarget, item: RequirementItem) -> None:
    if isinstance(item, HardConstraint):
        if not isinstance(target.item_id, ConstraintId) or target.item_id != item.constraint_id:
            raise DomainInvariantViolation("Constraint REPLACE target must match item identity")
    elif not isinstance(target.item_id, PreferenceId) or target.item_id != item.preference_id:
        raise DomainInvariantViolation("Preference REPLACE target must match item identity")
