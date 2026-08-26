from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from flight_agent.domain.flights import Money
from flight_agent.domain.impact import (
    HardConstraintSemanticEffect,
    RequirementDependencyKey,
    RequirementSemanticChangeKind,
    RequirementSemanticDiffer,
    SemanticSubjectType,
    SoftPreferenceSemanticEffect,
    StructuralChangeKind,
)
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    ValueRange,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion


def test_identical_semantics_with_different_versions_has_no_semantic_change() -> None:
    before = requirement(version=1, recorded_hour=1, constraints=(max_price(1200),))
    after = requirement(
        version=2,
        recorded_hour=2,
        predecessor=RequirementVersion(1),
        constraints=(max_price(1200, raw_id="constraint-renamed"),),
    )

    diff = RequirementSemanticDiffer().compare(before, after, provenance_refs=("patch-op-1",))

    assert diff.change_kind is RequirementSemanticChangeKind.NO_SEMANTIC_CHANGE
    assert diff.changes == ()
    assert diff.affected_dependency_keys == ()
    assert diff.provenance_refs == ("patch-op-1",)


def test_max_price_tightening_and_relaxation_are_constraint_specific() -> None:
    tightening = compare(max_price(1500), max_price(1200)).changes[0]
    relaxation = compare(max_price(1200), max_price(1500)).changes[0]

    assert tightening.hard_effect is HardConstraintSemanticEffect.TIGHTENED
    assert tightening.soft_effect is None
    assert relaxation.hard_effect is HardConstraintSemanticEffect.RELAXED
    assert relaxation.dependency_key == RequirementDependencyKey("constraint.max_price")


def test_hard_constraint_add_remove_and_replace_are_structural_semantic_changes() -> None:
    added = RequirementSemanticDiffer().compare(
        requirement(version=1),
        requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(max_price(900),),
        ),
    )
    removed = RequirementSemanticDiffer().compare(
        requirement(version=1, constraints=(max_price(900),)),
        requirement(version=2, predecessor=RequirementVersion(1)),
    )
    replaced = compare(origin("PVG"), origin("SHA")).changes[0]

    assert added.changes[0].structural_change is StructuralChangeKind.ADDED
    assert added.changes[0].hard_effect is HardConstraintSemanticEffect.TIGHTENED
    assert removed.changes[0].structural_change is StructuralChangeKind.REMOVED
    assert removed.changes[0].hard_effect is HardConstraintSemanticEffect.RELAXED
    assert replaced.structural_change is StructuralChangeKind.REPLACED
    assert replaced.subject_type is SemanticSubjectType.ROUTE
    assert replaced.semantic_marker == "SEARCH_SCOPE_CHANGED"
    assert "SEARCH_REQUIRED" not in repr(replaced)


def test_soft_preference_add_remove_target_and_importance_change_stay_separate() -> None:
    before = requirement(
        version=1,
        preferences=(departure_preference(PreferenceImportance.LOW, start=time(8), end=time(10)),),
    )
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        preferences=(departure_preference(PreferenceImportance.HIGH, start=time(9), end=time(11)),),
    )

    diff = RequirementSemanticDiffer().compare(before, after)
    effects = tuple(change.soft_effect for change in diff.changes)

    assert effects == (
        SoftPreferenceSemanticEffect.TARGET_CHANGED,
        SoftPreferenceSemanticEffect.IMPORTANCE_INCREASED,
    )
    assert all(change.hard_effect is None for change in diff.changes)
    assert diff.affected_dependency_keys == (
        RequirementDependencyKey("preference.departure_time"),
        RequirementDependencyKey("preference.departure_time.importance"),
    )

    added = RequirementSemanticDiffer().compare(requirement(version=1), before)
    removed = RequirementSemanticDiffer().compare(before, requirement(version=2, predecessor=RequirementVersion(1)))
    assert added.changes[0].soft_effect is SoftPreferenceSemanticEffect.ADDED
    assert removed.changes[0].soft_effect is SoftPreferenceSemanticEffect.REMOVED


def test_preference_importance_decrease_uses_first_class_importance_subject() -> None:
    before = requirement(version=1, preferences=(price_preference(PreferenceImportance.HIGH),))
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        preferences=(price_preference(PreferenceImportance.MEDIUM),),
    )

    change = RequirementSemanticDiffer().compare(before, after).changes[0]

    assert change.subject_type is SemanticSubjectType.PREFERENCE_IMPORTANCE
    assert change.soft_effect is SoftPreferenceSemanticEffect.IMPORTANCE_DECREASED
    assert change.dependency_key == RequirementDependencyKey("preference.price.importance")


def test_route_origin_destination_and_departure_date_emit_stable_scope_keys() -> None:
    before = requirement(version=1, constraints=(origin("PVG"), destination("LAX"), departure_date(2026, 9, 1)))
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        constraints=(origin("SHA"), destination("SFO"), departure_date(2026, 9, 2)),
    )

    diff = RequirementSemanticDiffer().compare(before, after)

    assert diff.affected_dependency_keys == (
        RequirementDependencyKey("requirement.route.destination"),
        RequirementDependencyKey("requirement.route.origin"),
        RequirementDependencyKey("requirement.trip.departure_date"),
    )
    assert {change.subject_type for change in diff.changes} == {
        SemanticSubjectType.ROUTE,
        SemanticSubjectType.DATE,
    }
    assert all(change.semantic_marker == "SEARCH_SCOPE_CHANGED" for change in diff.changes)


def test_diff_artifact_is_immutable_and_does_not_mutate_inputs() -> None:
    before = requirement(version=1, constraints=(max_price(1500),))
    after = requirement(version=2, predecessor=RequirementVersion(1), constraints=(max_price(1200),))
    before_snapshot = before
    after_snapshot = after

    diff = RequirementSemanticDiffer().compare(before, after)

    with pytest.raises(FrozenInstanceError):
        diff.changes = ()  # type: ignore[misc]
    assert before == before_snapshot
    assert after == after_snapshot
    assert not hasattr(before, "semantic_diff")
    assert not hasattr(after, "semantic_diff")


def test_diff_order_and_semantic_output_are_deterministic() -> None:
    before = requirement(
        version=1,
        constraints=(max_price(1500), origin("PVG"), departure_date(2026, 9, 1)),
        preferences=(price_preference(PreferenceImportance.LOW),),
    )
    after = requirement(
        version=2,
        predecessor=RequirementVersion(1),
        constraints=(departure_date(2026, 9, 2), origin("SHA"), max_price(1200)),
        preferences=(price_preference(PreferenceImportance.HIGH),),
    )

    first = RequirementSemanticDiffer().compare(before, after)
    second = RequirementSemanticDiffer().compare(before, after)

    assert first == second
    assert tuple(key.value for key in first.affected_dependency_keys) == tuple(sorted(
        key.value for key in first.affected_dependency_keys
    ))
    assert tuple(change.dependency_key.value for change in first.changes) == (
        "constraint.max_price",
        "preference.price.importance",
        "requirement.route.origin",
        "requirement.trip.departure_date",
    )


def test_semantic_diff_has_no_impact_data_action_or_execution_plan_leakage() -> None:
    diff = compare(max_price(1500), max_price(1200))
    source = repr(diff)

    assert not hasattr(diff, "impact_decision")
    assert not hasattr(diff, "data_action")
    assert not hasattr(diff, "execution_plan")
    assert "SEARCH_REQUIRED" not in source
    assert "REUSE" not in source
    assert "REFRESH" not in source
    assert "ExecutionPlan" not in source


def test_time_window_widening_and_narrowing_are_compared_as_hard_constraints() -> None:
    widened = compare(
        departure_time(start=time(9), end=time(11)),
        departure_time(start=time(8), end=time(12)),
    ).changes[0]
    narrowed = compare(
        departure_time(start=time(8), end=time(12)),
        departure_time(start=time(9), end=time(11)),
    ).changes[0]

    assert widened.hard_effect is HardConstraintSemanticEffect.RELAXED
    assert narrowed.hard_effect is HardConstraintSemanticEffect.TIGHTENED
    assert widened.soft_effect is None


def compare(before_constraint: HardConstraint, after_constraint: HardConstraint):
    return RequirementSemanticDiffer().compare(
        requirement(version=1, constraints=(before_constraint,)),
        requirement(
            version=2,
            predecessor=RequirementVersion(1),
            constraints=(after_constraint,),
        ),
    )


def requirement(
    *,
    version: int,
    recorded_hour: int = 1,
    predecessor: RequirementVersion | None = None,
    constraints: tuple[HardConstraint, ...] = (),
    preferences: tuple[SoftPreference, ...] = (),
) -> RequirementState:
    return RequirementState(
        requirement_id=RequirementId("requirement-1"),
        version=RequirementVersion(version),
        predecessor_version=predecessor,
        recorded_at=DomainInstant(datetime(2026, 8, 27, recorded_hour, tzinfo=UTC)),
        constraints=constraints,
        preferences=preferences,
    )


def max_price(amount: int, raw_id: str = "constraint-max-price") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(amount), "CNY"),
    )


def origin(airport: str, raw_id: str = "constraint-origin") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination(airport: str, raw_id: str = "constraint-destination") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def departure_date(year: int, month: int, day: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-departure-date"),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(date(year, month, day)),
    )


def departure_time(*, start: time, end: time) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-departure-time"),
        scope=ConstraintScope.DEPARTURE_TIME,
        operator=ConstraintOperator.BETWEEN,
        value=ValueRange(LocalTime(start), LocalTime(end)),
    )


def departure_preference(
    importance: PreferenceImportance,
    *,
    start: time = time(8),
    end: time = time(10),
) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-departure-time"),
        scope=PreferenceScope.DEPARTURE_TIME,
        importance=importance,
        value=ValueRange(LocalTime(start), LocalTime(end)),
    )


def price_preference(importance: PreferenceImportance) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=importance,
        value=None,
    )
