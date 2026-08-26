"""Requirement semantic diff foundation for M7-U1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import Enum
from typing import Any, cast

from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    ConstraintScope,
    HardConstraint,
    PreferenceImportance,
    PreferenceScope,
    RequirementState,
    SoftPreference,
    ValueRange,
    ValueSet,
)
from flight_agent.domain.shared import DomainId, DomainInvariantViolation, RequirementVersion

RangeBoundary = date | time | Decimal | int | str


class RequirementSemanticChangeKind(str, Enum):
    NO_SEMANTIC_CHANGE = "NO_SEMANTIC_CHANGE"
    CHANGED = "CHANGED"


class SemanticSubjectType(str, Enum):
    TRIP = "TRIP"
    ROUTE = "ROUTE"
    DATE = "DATE"
    HARD_CONSTRAINT = "HARD_CONSTRAINT"
    SOFT_PREFERENCE = "SOFT_PREFERENCE"
    PREFERENCE_IMPORTANCE = "PREFERENCE_IMPORTANCE"


class StructuralChangeKind(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    REPLACED = "REPLACED"
    CLEARED = "CLEARED"


class HardConstraintSemanticEffect(str, Enum):
    TIGHTENED = "TIGHTENED"
    RELAXED = "RELAXED"
    SHIFTED = "SHIFTED"
    INCOMPARABLE = "INCOMPARABLE"


class SoftPreferenceSemanticEffect(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    TARGET_CHANGED = "TARGET_CHANGED"
    DIRECTION_CHANGED = "DIRECTION_CHANGED"
    IMPORTANCE_INCREASED = "IMPORTANCE_INCREASED"
    IMPORTANCE_DECREASED = "IMPORTANCE_DECREASED"


@dataclass(frozen=True)
class RequirementDependencyKey(DomainId):
    """Stable business-semantic key for later M7 impact resolution."""


@dataclass(frozen=True, init=False)
class RequirementSemanticChange:
    subject_type: SemanticSubjectType
    dependency_key: RequirementDependencyKey
    structural_change: StructuralChangeKind
    subject_id: str | None
    before: object | None
    after: object | None
    hard_effect: HardConstraintSemanticEffect | None
    soft_effect: SoftPreferenceSemanticEffect | None
    semantic_marker: str | None

    def __init__(
        self,
        *,
        subject_type: SemanticSubjectType,
        dependency_key: RequirementDependencyKey,
        structural_change: StructuralChangeKind,
        subject_id: str | None,
        before: object | None,
        after: object | None,
        hard_effect: HardConstraintSemanticEffect | None = None,
        soft_effect: SoftPreferenceSemanticEffect | None = None,
        semantic_marker: str | None = None,
    ) -> None:
        if hard_effect is not None and soft_effect is not None:
            raise DomainInvariantViolation("Semantic change cannot mix Hard and Soft effects")
        object.__setattr__(self, "subject_type", subject_type)
        object.__setattr__(self, "dependency_key", dependency_key)
        object.__setattr__(self, "structural_change", structural_change)
        object.__setattr__(self, "subject_id", subject_id)
        object.__setattr__(self, "before", before)
        object.__setattr__(self, "after", after)
        object.__setattr__(self, "hard_effect", hard_effect)
        object.__setattr__(self, "soft_effect", soft_effect)
        object.__setattr__(self, "semantic_marker", semantic_marker)


@dataclass(frozen=True, init=False)
class RequirementSemanticDiff:
    diff_id: DomainId
    requirement_id: DomainId
    from_version: RequirementVersion
    to_version: RequirementVersion
    change_kind: RequirementSemanticChangeKind
    changes: tuple[RequirementSemanticChange, ...]
    affected_dependency_keys: tuple[RequirementDependencyKey, ...]
    provenance_refs: tuple[str, ...]

    def __init__(
        self,
        *,
        diff_id: DomainId,
        requirement_id: DomainId,
        from_version: RequirementVersion,
        to_version: RequirementVersion,
        changes: tuple[RequirementSemanticChange, ...],
        provenance_refs: tuple[str, ...] = (),
    ) -> None:
        changes_tuple = tuple(changes)
        affected_keys = tuple(
            RequirementDependencyKey(key)
            for key in sorted({change.dependency_key.value for change in changes_tuple})
        )
        object.__setattr__(self, "diff_id", diff_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "from_version", from_version)
        object.__setattr__(self, "to_version", to_version)
        object.__setattr__(
            self,
            "change_kind",
            RequirementSemanticChangeKind.CHANGED
            if len(changes_tuple) > 0
            else RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE,
        )
        object.__setattr__(self, "changes", changes_tuple)
        object.__setattr__(self, "affected_dependency_keys", affected_keys)
        object.__setattr__(self, "provenance_refs", tuple(provenance_refs))


class RequirementSemanticDiffer:
    """Compares committed authoritative RequirementState semantics."""

    def compare(
        self,
        before: RequirementState,
        after: RequirementState,
        *,
        provenance_refs: tuple[str, ...] = (),
    ) -> RequirementSemanticDiff:
        if before.requirement_id != after.requirement_id:
            raise DomainInvariantViolation("Semantic diff requires one Requirement identity chain")
        changes = (
            *_constraint_changes(before.constraints, after.constraints),
            *_preference_changes(before.preferences, after.preferences),
        )
        ordered = tuple(sorted(changes, key=_change_sort_key))
        return RequirementSemanticDiff(
            diff_id=DomainId(
                f"requirement-semantic-diff:{before.requirement_id.value}:"
                f"{before.version.value}->{after.version.value}"
            ),
            requirement_id=before.requirement_id,
            from_version=before.version,
            to_version=after.version,
            changes=ordered,
            provenance_refs=provenance_refs,
        )


def _constraint_changes(
    before: tuple[HardConstraint, ...],
    after: tuple[HardConstraint, ...],
) -> tuple[RequirementSemanticChange, ...]:
    before_by_id = {constraint.constraint_id: constraint for constraint in before}
    after_by_id = {constraint.constraint_id: constraint for constraint in after}
    changes: list[RequirementSemanticChange] = []

    for constraint_id in sorted(before_by_id.keys() & after_by_id.keys(), key=lambda item: item.value):
        old = before_by_id[constraint_id]
        new = after_by_id[constraint_id]
        if _constraint_signature(old) != _constraint_signature(new):
            changes.append(_changed_constraint(old, new, StructuralChangeKind.REPLACED))

    unmatched_before = tuple(
        before_by_id[item]
        for item in sorted(before_by_id.keys() - after_by_id.keys(), key=lambda key: key.value)
    )
    unmatched_after = tuple(
        after_by_id[item]
        for item in sorted(after_by_id.keys() - before_by_id.keys(), key=lambda key: key.value)
    )

    paired_after_indexes: set[int] = set()
    for old in unmatched_before:
        index = _first_unmatched_same_constraint_subject(old, unmatched_after, paired_after_indexes)
        if index is None:
            changes.append(_removed_constraint(old))
        else:
            paired_after_indexes.add(index)
            new = unmatched_after[index]
            if _constraint_signature(old) != _constraint_signature(new):
                changes.append(_changed_constraint(old, new, StructuralChangeKind.REPLACED))

    for index, new in enumerate(unmatched_after):
        if index not in paired_after_indexes:
            changes.append(_added_constraint(new))

    return tuple(changes)


def _preference_changes(
    before: tuple[SoftPreference, ...],
    after: tuple[SoftPreference, ...],
) -> tuple[RequirementSemanticChange, ...]:
    before_by_id = {preference.preference_id: preference for preference in before}
    after_by_id = {preference.preference_id: preference for preference in after}
    changes: list[RequirementSemanticChange] = []

    for preference_id in sorted(before_by_id.keys() & after_by_id.keys(), key=lambda item: item.value):
        old = before_by_id[preference_id]
        new = after_by_id[preference_id]
        changes.extend(_changed_preference(old, new))

    unmatched_before = tuple(
        before_by_id[item]
        for item in sorted(before_by_id.keys() - after_by_id.keys(), key=lambda key: key.value)
    )
    unmatched_after = tuple(
        after_by_id[item]
        for item in sorted(after_by_id.keys() - before_by_id.keys(), key=lambda key: key.value)
    )

    paired_after_indexes: set[int] = set()
    for old in unmatched_before:
        index = _first_unmatched_same_preference_subject(old, unmatched_after, paired_after_indexes)
        if index is None:
            changes.append(_removed_preference(old))
        else:
            paired_after_indexes.add(index)
            new = unmatched_after[index]
            if _preference_signature(old) != _preference_signature(new):
                changes.extend(_changed_preference(old, new))

    for index, new in enumerate(unmatched_after):
        if index not in paired_after_indexes:
            changes.append(_added_preference(new))

    return tuple(changes)


def _changed_constraint(
    before: HardConstraint,
    after: HardConstraint,
    structural: StructuralChangeKind,
) -> RequirementSemanticChange:
    subject_type = _constraint_subject_type(after)
    return RequirementSemanticChange(
        subject_type=subject_type,
        dependency_key=_constraint_dependency_key(after),
        structural_change=structural,
        subject_id=after.constraint_id.value,
        before=_constraint_signature(before),
        after=_constraint_signature(after),
        hard_effect=_hard_effect(before, after),
        semantic_marker="SEARCH_SCOPE_CHANGED"
        if subject_type in {SemanticSubjectType.ROUTE, SemanticSubjectType.DATE}
        else None,
    )


def _added_constraint(constraint: HardConstraint) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=_constraint_subject_type(constraint),
        dependency_key=_constraint_dependency_key(constraint),
        structural_change=StructuralChangeKind.ADDED,
        subject_id=constraint.constraint_id.value,
        before=None,
        after=_constraint_signature(constraint),
        hard_effect=_hard_added_effect(constraint),
    )


def _removed_constraint(constraint: HardConstraint) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=_constraint_subject_type(constraint),
        dependency_key=_constraint_dependency_key(constraint),
        structural_change=StructuralChangeKind.REMOVED,
        subject_id=constraint.constraint_id.value,
        before=_constraint_signature(constraint),
        after=None,
        hard_effect=_hard_removed_effect(constraint),
    )


def _changed_preference(before: SoftPreference, after: SoftPreference) -> tuple[RequirementSemanticChange, ...]:
    changes: list[RequirementSemanticChange] = []
    if before.scope != after.scope:
        changes.append(_preference_change(after, before, SoftPreferenceSemanticEffect.DIRECTION_CHANGED))
    elif _preference_target(before) != _preference_target(after):
        changes.append(_preference_change(after, before, SoftPreferenceSemanticEffect.TARGET_CHANGED))
    if before.importance != after.importance:
        changes.append(
            _preference_importance_change(
                before,
                after,
                SoftPreferenceSemanticEffect.IMPORTANCE_INCREASED
                if _importance_rank(after.importance) > _importance_rank(before.importance)
                else SoftPreferenceSemanticEffect.IMPORTANCE_DECREASED,
            )
        )
    return tuple(changes)


def _added_preference(preference: SoftPreference) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=SemanticSubjectType.SOFT_PREFERENCE,
        dependency_key=_preference_dependency_key(preference),
        structural_change=StructuralChangeKind.ADDED,
        subject_id=preference.preference_id.value,
        before=None,
        after=_preference_signature(preference),
        soft_effect=SoftPreferenceSemanticEffect.ADDED,
    )


def _removed_preference(preference: SoftPreference) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=SemanticSubjectType.SOFT_PREFERENCE,
        dependency_key=_preference_dependency_key(preference),
        structural_change=StructuralChangeKind.REMOVED,
        subject_id=preference.preference_id.value,
        before=_preference_signature(preference),
        after=None,
        soft_effect=SoftPreferenceSemanticEffect.REMOVED,
    )


def _preference_change(
    after: SoftPreference,
    before: SoftPreference,
    effect: SoftPreferenceSemanticEffect,
) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=SemanticSubjectType.SOFT_PREFERENCE,
        dependency_key=_preference_dependency_key(after),
        structural_change=StructuralChangeKind.REPLACED,
        subject_id=after.preference_id.value,
        before=_preference_signature(before),
        after=_preference_signature(after),
        soft_effect=effect,
    )


def _preference_importance_change(
    before: SoftPreference,
    after: SoftPreference,
    effect: SoftPreferenceSemanticEffect,
) -> RequirementSemanticChange:
    return RequirementSemanticChange(
        subject_type=SemanticSubjectType.PREFERENCE_IMPORTANCE,
        dependency_key=RequirementDependencyKey(f"{_preference_dependency_key(after).value}.importance"),
        structural_change=StructuralChangeKind.REPLACED,
        subject_id=after.preference_id.value,
        before=before.importance.value,
        after=after.importance.value,
        soft_effect=effect,
    )


def _first_unmatched_same_constraint_subject(
    constraint: HardConstraint,
    candidates: tuple[HardConstraint, ...],
    matched_indexes: set[int],
) -> int | None:
    for index, candidate in enumerate(candidates):
        if index not in matched_indexes and (
            constraint.scope,
            constraint.operator,
        ) == (
            candidate.scope,
            candidate.operator,
        ):
            return index
    return None


def _first_unmatched_same_preference_subject(
    preference: SoftPreference,
    candidates: tuple[SoftPreference, ...],
    matched_indexes: set[int],
) -> int | None:
    for index, candidate in enumerate(candidates):
        if index not in matched_indexes and preference.scope == candidate.scope:
            return index
    return None


def _hard_added_effect(constraint: HardConstraint) -> HardConstraintSemanticEffect:
    if constraint.scope is ConstraintScope.MAX_PRICE:
        return HardConstraintSemanticEffect.TIGHTENED
    return HardConstraintSemanticEffect.INCOMPARABLE


def _hard_removed_effect(constraint: HardConstraint) -> HardConstraintSemanticEffect:
    if constraint.scope is ConstraintScope.MAX_PRICE:
        return HardConstraintSemanticEffect.RELAXED
    return HardConstraintSemanticEffect.INCOMPARABLE


def _hard_effect(
    before: HardConstraint,
    after: HardConstraint,
) -> HardConstraintSemanticEffect:
    if before.scope != after.scope or before.operator != after.operator:
        return HardConstraintSemanticEffect.INCOMPARABLE
    if after.scope is ConstraintScope.MAX_PRICE:
        return _max_price_effect(before, after)
    if after.scope is ConstraintScope.DEPARTURE_TIME:
        return _range_effect(before, after)
    if after.scope in {
        ConstraintScope.ORIGIN_AIRPORT,
        ConstraintScope.DESTINATION_AIRPORT,
        ConstraintScope.DEPARTURE_DATE,
    }:
        return HardConstraintSemanticEffect.SHIFTED
    return HardConstraintSemanticEffect.INCOMPARABLE


def _max_price_effect(
    before: HardConstraint,
    after: HardConstraint,
) -> HardConstraintSemanticEffect:
    if not isinstance(before.value, Money) or not isinstance(after.value, Money):
        return HardConstraintSemanticEffect.INCOMPARABLE
    if before.value.currency != after.value.currency:
        return HardConstraintSemanticEffect.INCOMPARABLE
    if after.value.amount > before.value.amount:
        return HardConstraintSemanticEffect.RELAXED
    if after.value.amount < before.value.amount:
        return HardConstraintSemanticEffect.TIGHTENED
    return HardConstraintSemanticEffect.INCOMPARABLE


def _range_effect(
    before: HardConstraint,
    after: HardConstraint,
) -> HardConstraintSemanticEffect:
    if not isinstance(before.value, ValueRange) or not isinstance(after.value, ValueRange):
        return HardConstraintSemanticEffect.INCOMPARABLE
    old_start, old_end = before.value.start, before.value.end
    new_start, new_end = after.value.start, after.value.end
    if type(old_start) is not type(new_start) or type(old_end) is not type(new_end):
        return HardConstraintSemanticEffect.INCOMPARABLE
    old_start_value = _range_boundary_value(old_start)
    old_end_value = _range_boundary_value(old_end)
    new_start_value = _range_boundary_value(new_start)
    new_end_value = _range_boundary_value(new_end)
    if _boundary_le(new_start_value, old_start_value) and _boundary_ge(new_end_value, old_end_value):
        return HardConstraintSemanticEffect.RELAXED
    if _boundary_ge(new_start_value, old_start_value) and _boundary_le(new_end_value, old_end_value):
        return HardConstraintSemanticEffect.TIGHTENED
    return HardConstraintSemanticEffect.SHIFTED


def _constraint_subject_type(constraint: HardConstraint) -> SemanticSubjectType:
    if constraint.scope in {ConstraintScope.ORIGIN_AIRPORT, ConstraintScope.DESTINATION_AIRPORT}:
        return SemanticSubjectType.ROUTE
    if constraint.scope is ConstraintScope.DEPARTURE_DATE:
        return SemanticSubjectType.DATE
    if constraint.scope in {ConstraintScope.CABIN_CLASS, ConstraintScope.PASSENGER_COUNT}:
        return SemanticSubjectType.TRIP
    return SemanticSubjectType.HARD_CONSTRAINT


def _constraint_dependency_key(constraint: HardConstraint) -> RequirementDependencyKey:
    keys = {
        ConstraintScope.ORIGIN_AIRPORT: "requirement.route.origin",
        ConstraintScope.DESTINATION_AIRPORT: "requirement.route.destination",
        ConstraintScope.DEPARTURE_DATE: "requirement.trip.departure_date",
        ConstraintScope.DEPARTURE_TIME: "constraint.departure_time",
        ConstraintScope.CABIN_CLASS: "requirement.trip.cabin_class",
        ConstraintScope.PASSENGER_COUNT: "requirement.trip.passenger_count",
        ConstraintScope.MAX_PRICE: "constraint.max_price",
    }
    return RequirementDependencyKey(keys[constraint.scope])


def _preference_dependency_key(preference: SoftPreference) -> RequirementDependencyKey:
    keys = {
        PreferenceScope.PRICE: "preference.price",
        PreferenceScope.DEPARTURE_TIME: "preference.departure_time",
        PreferenceScope.ARRIVAL_TIME: "preference.arrival_time",
        PreferenceScope.AIRPORT_MATCH: "preference.airport_match",
        PreferenceScope.FEWER_STOPS: "preference.fewer_stops",
    }
    return RequirementDependencyKey(keys[preference.scope])


def _importance_rank(importance: PreferenceImportance) -> int:
    return {
        PreferenceImportance.LOW: 1,
        PreferenceImportance.MEDIUM: 2,
        PreferenceImportance.HIGH: 3,
    }[importance]


def _constraint_signature(constraint: HardConstraint) -> tuple[str, str, object]:
    return (constraint.scope.value, constraint.operator.value, _semantic_value(constraint.value))


def _preference_signature(preference: SoftPreference) -> tuple[str, str, object | None]:
    return (
        preference.scope.value,
        preference.importance.value,
        _preference_target(preference),
    )


def _preference_target(preference: SoftPreference) -> object | None:
    return _semantic_value(preference.value)


def _semantic_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Money):
        return (str(value.amount.normalize()), value.currency)
    if isinstance(value, ValueSet):
        return tuple(sorted((_semantic_value(item) for item in value.items), key=repr))
    if isinstance(value, ValueRange):
        return (_semantic_value(value.start), _semantic_value(value.end))
    scalar = getattr(value, "value", value)
    if isinstance(scalar, Decimal):
        return str(scalar.normalize())
    return scalar


def _range_boundary_value(value: object) -> RangeBoundary:
    scalar = getattr(value, "value", value)
    if isinstance(scalar, (date, time, Decimal, int, str)):
        return scalar
    raise DomainInvariantViolation("Unsupported range boundary for semantic comparison")


def _boundary_le(left: RangeBoundary, right: RangeBoundary) -> bool:
    if type(left) is not type(right):
        raise DomainInvariantViolation("Range boundaries must have matching comparable types")
    return cast(Any, left) <= cast(Any, right)


def _boundary_ge(left: RangeBoundary, right: RangeBoundary) -> bool:
    if type(left) is not type(right):
        raise DomainInvariantViolation("Range boundaries must have matching comparable types")
    return cast(Any, left) >= cast(Any, right)


def _change_sort_key(change: RequirementSemanticChange) -> tuple[str, str, str]:
    return (
        change.dependency_key.value,
        change.subject_id or "",
        change.structural_change.value,
    )
