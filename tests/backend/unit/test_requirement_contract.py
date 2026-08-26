from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Callable

import pytest

from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    CabinClass,
    ClearTarget,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PassengerCount,
    PatchOperation,
    PatchSet,
    PatchTarget,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementPatch,
    RequirementState,
    SoftPreference,
    ValueRange,
    ValueSet,
)
from flight_agent.domain.shared import DomainInstant, DomainInvariantViolation, RequirementVersion


def instant(hour: int = 9) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 21, hour, 30, tzinfo=UTC))


def origin_constraint(raw_id: str = "constraint-origin", airport: str = "PVG") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination_constraint(
    raw_id: str = "constraint-destination", airport: str = "LAX"
) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def max_price_constraint(raw_id: str, amount: Decimal) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(amount, "CNY"),
    )


def date_constraint(raw_id: str = "constraint-date") -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.BETWEEN,
        value=ValueRange(LocalDate(date(2026, 9, 1)), LocalDate(date(2026, 9, 8))),
    )


def departure_preference(raw_id: str = "preference-time") -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId(raw_id),
        scope=PreferenceScope.DEPARTURE_TIME,
        importance=PreferenceImportance.HIGH,
        value=ValueRange(LocalTime(time(8, 0)), LocalTime(time(11, 0))),
    )


def airport_preference(raw_id: str = "preference-airport", airport: str = "SHA") -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId(raw_id),
        scope=PreferenceScope.AIRPORT_MATCH,
        importance=PreferenceImportance.MEDIUM,
        value=AirportCode(airport),
    )


def initial_state() -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(),
        constraints=(origin_constraint(),),
        preferences=(departure_preference(),),
    )


def test_initial_requirement_state_has_stable_identity_version_and_no_predecessor() -> None:
    state = initial_state()

    assert state.requirement_id == RequirementId("requirement-1")
    assert state.version == RequirementVersion(1)
    assert state.predecessor_version is None
    assert state.constraints == (origin_constraint(),)
    assert state.preferences == (departure_preference(),)


def test_requirement_state_is_immutable_and_defensively_copies_input_collection() -> None:
    constraints = [origin_constraint()]
    state = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(),
        constraints=tuple(constraints),
    )
    constraints.append(destination_constraint())

    assert state.constraints == (origin_constraint(),)
    with pytest.raises(FrozenInstanceError):
        state.version = RequirementVersion(2)  # type: ignore[misc]


def test_requirement_state_rejects_malformed_lineage_and_duplicate_local_ids() -> None:
    with pytest.raises(DomainInvariantViolation):
        RequirementState(
            requirement_id=RequirementId("requirement-1"),
            version=RequirementVersion(1),
            predecessor_version=RequirementVersion(1),
            recorded_at=instant(),
        )

    with pytest.raises(DomainInvariantViolation):
        RequirementState.initial(
            requirement_id=RequirementId("requirement-1"),
            recorded_at=instant(),
            constraints=(origin_constraint("dup"), destination_constraint("dup")),
        )


def test_hard_constraint_and_soft_preference_are_distinct_types_and_identities() -> None:
    constraint = origin_constraint()
    preference = airport_preference()

    assert isinstance(constraint, HardConstraint)
    assert isinstance(preference, SoftPreference)
    assert ConstraintId("same") != PreferenceId("same")


def test_constraint_operator_scope_and_preference_importance_are_structural() -> None:
    constraint = HardConstraint(
        constraint_id=ConstraintId("constraint-cabin"),
        scope=ConstraintScope.CABIN_CLASS,
        operator=ConstraintOperator.IN,
        value=ValueSet((CabinClass("business"), CabinClass("first"))),
    )
    preference = departure_preference()

    assert constraint.operator is ConstraintOperator.IN
    assert constraint.scope is ConstraintScope.CABIN_CLASS
    assert not hasattr(constraint, "evaluation")
    assert preference.importance is PreferenceImportance.HIGH
    assert not hasattr(preference, "ranking_weight")
    assert not hasattr(preference, "ranking_contribution")


def test_constraints_and_preferences_reject_incompatible_typed_values() -> None:
    with pytest.raises(DomainInvariantViolation):
        HardConstraint(
            constraint_id=ConstraintId("bad"),
            scope=ConstraintScope.ORIGIN_AIRPORT,
            operator=ConstraintOperator.EQUALS,
            value=LocalDate(date(2026, 9, 1)),
        )

    with pytest.raises(DomainInvariantViolation):
        SoftPreference(
            preference_id=PreferenceId("bad"),
            scope=PreferenceScope.AIRPORT_MATCH,
            importance=PreferenceImportance.LOW,
            value=LocalTime(time(9, 0)),
        )


def test_max_price_is_money_valued_hard_constraint_distinct_from_price_preference() -> None:
    max_price = HardConstraint(
        constraint_id=ConstraintId("constraint-max-price"),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(Decimal(800), "cny"),
    )
    price_preference = SoftPreference(
        preference_id=PreferenceId("preference-price"),
        scope=PreferenceScope.PRICE,
        importance=PreferenceImportance.HIGH,
    )
    state = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(),
        constraints=(max_price,),
        preferences=(price_preference,),
    )

    assert state.constraints == (max_price,)
    assert state.preferences == (price_preference,)
    assert max_price.value == Money(Decimal(800), "CNY")
    assert max_price.scope is ConstraintScope.MAX_PRICE
    assert price_preference.scope is PreferenceScope.PRICE


def test_max_price_rejects_non_money_requirement_values() -> None:
    with pytest.raises(DomainInvariantViolation):
        HardConstraint(
            constraint_id=ConstraintId("bad-max-price"),
            scope=ConstraintScope.MAX_PRICE,
            operator=ConstraintOperator.AT_OR_BEFORE,
            value=PassengerCount(800),
        )


def test_typed_requirement_values_construct_and_are_not_dict_contracts() -> None:
    values = (
        AirportCode("pvg"),
        CabinClass("economy"),
        LocalDate(date(2026, 9, 1)),
        LocalTime(time(9, 0)),
        PassengerCount(1),
        ValueSet((AirportCode("PVG"), AirportCode("SHA"))),
        ValueRange(LocalTime(time(8, 0)), LocalTime(time(10, 0))),
    )

    assert AirportCode("pvg").value == "PVG"
    assert all(not isinstance(value, dict) for value in values)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AirportCode("PVG1"),
        lambda: CabinClass("PRIVATE_JET"),
        lambda: LocalTime(time(9, 0, tzinfo=UTC)),
        lambda: PassengerCount(0),
        lambda: ValueSet(()),
        lambda: ValueSet((AirportCode("PVG"), LocalDate(date(2026, 9, 1)))),
        lambda: ValueRange(AirportCode("PVG"), LocalDate(date(2026, 9, 1))),
    ],
)
def test_typed_requirement_values_reject_invalid_structure(factory: Callable[[], object]) -> None:
    with pytest.raises(DomainInvariantViolation):
        factory()


def test_typed_requirement_values_are_immutable() -> None:
    value = AirportCode("PVG")

    with pytest.raises(FrozenInstanceError):
        value.value = "SHA"  # type: ignore[misc]


def test_requirement_patch_add_replace_remove_and_clear_construction() -> None:
    constraint = origin_constraint()
    preference = departure_preference()

    assert RequirementPatch.add(constraint).operation is PatchOperation.ADD
    assert RequirementPatch.replace(PatchTarget(constraint.constraint_id), constraint).target == PatchTarget(
        ConstraintId("constraint-origin")
    )
    assert RequirementPatch.remove(PatchTarget(preference.preference_id)).operation is PatchOperation.REMOVE
    assert RequirementPatch.clear(ClearTarget.SOFT_PREFERENCES).clear_target is ClearTarget.SOFT_PREFERENCES


@pytest.mark.parametrize(
    "patch",
    [
        lambda: RequirementPatch(PatchOperation.ADD),
        lambda: RequirementPatch(PatchOperation.REPLACE, item=origin_constraint()),
        lambda: RequirementPatch(PatchOperation.REMOVE, item=origin_constraint()),
        lambda: RequirementPatch(PatchOperation.CLEAR, target=PatchTarget(ConstraintId("c"))),
        lambda: RequirementPatch.replace(PatchTarget(PreferenceId("wrong")), origin_constraint()),
    ],
)
def test_requirement_patch_rejects_malformed_or_mismatched_structure(
    patch: Callable[[], object],
) -> None:
    with pytest.raises(DomainInvariantViolation):
        patch()


def test_patch_is_immutable() -> None:
    patch = RequirementPatch.add(origin_constraint())

    with pytest.raises(FrozenInstanceError):
        patch.operation = PatchOperation.REMOVE  # type: ignore[misc]


def test_patchset_applies_valid_multi_operation_as_one_new_version() -> None:
    state = initial_state()
    patch_set = PatchSet(
        base_requirement_version=RequirementVersion(1),
        patches=(
            RequirementPatch.add(destination_constraint()),
            RequirementPatch.replace(
                PatchTarget(PreferenceId("preference-time")),
                SoftPreference(
                    preference_id=PreferenceId("preference-time"),
                    scope=PreferenceScope.DEPARTURE_TIME,
                    importance=PreferenceImportance.MEDIUM,
                    value=ValueRange(LocalTime(time(7, 0)), LocalTime(time(10, 0))),
                ),
            ),
        ),
    )

    next_state = state.apply(patch_set, recorded_at=instant(10))

    assert next_state is not state
    assert next_state.requirement_id == state.requirement_id
    assert next_state.version == RequirementVersion(2)
    assert next_state.predecessor_version == RequirementVersion(1)
    assert destination_constraint() in next_state.constraints
    assert state.version == RequirementVersion(1)
    assert destination_constraint() not in state.constraints


def test_patchset_remove_and_clear_success() -> None:
    state = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(),
        constraints=(origin_constraint(), destination_constraint()),
        preferences=(departure_preference(), airport_preference()),
    )
    removed = state.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(1),
            patches=(RequirementPatch.remove(PatchTarget(ConstraintId("constraint-destination"))),),
        ),
        recorded_at=instant(10),
    )
    cleared = removed.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(2),
            patches=(RequirementPatch.clear(ClearTarget.SOFT_PREFERENCES),),
        ),
        recorded_at=instant(11),
    )

    assert destination_constraint() not in removed.constraints
    assert cleared.preferences == ()


def test_max_price_uses_generic_patch_lifecycle_and_preserves_old_version() -> None:
    state = initial_state()
    added_constraint = max_price_constraint("constraint-max-price", Decimal(800))

    added = state.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(1),
            patches=(RequirementPatch.add(added_constraint),),
        ),
        recorded_at=instant(10),
    )
    replaced_constraint = max_price_constraint("constraint-max-price", Decimal(900))
    replaced = added.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(2),
            patches=(
                RequirementPatch.replace(
                    PatchTarget(ConstraintId("constraint-max-price")),
                    replaced_constraint,
                ),
            ),
        ),
        recorded_at=instant(11),
    )
    removed = replaced.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(3),
            patches=(RequirementPatch.remove(PatchTarget(ConstraintId("constraint-max-price"))),),
        ),
        recorded_at=instant(12),
    )

    assert state.version == RequirementVersion(1)
    assert state.constraints == (origin_constraint(),)
    assert added.version == RequirementVersion(2)
    assert added_constraint in added.constraints
    assert replaced.version == RequirementVersion(3)
    assert replaced_constraint in replaced.constraints
    assert removed.version == RequirementVersion(4)
    assert ConstraintId("constraint-max-price") not in {
        constraint.constraint_id for constraint in removed.constraints
    }


def test_patchset_rejects_stale_base_version() -> None:
    with pytest.raises(DomainInvariantViolation):
        initial_state().apply(
            PatchSet(
                base_requirement_version=RequirementVersion(2),
                patches=(RequirementPatch.add(destination_constraint()),),
            ),
            recorded_at=instant(10),
        )


def test_invalid_operation_causes_atomic_rejection_without_partial_application() -> None:
    state = initial_state()
    patch_set = PatchSet(
        base_requirement_version=RequirementVersion(1),
        patches=(
            RequirementPatch.add(destination_constraint()),
            RequirementPatch.remove(PatchTarget(ConstraintId("missing"))),
        ),
    )

    with pytest.raises(DomainInvariantViolation):
        state.apply(patch_set, recorded_at=instant(10))

    assert state.constraints == (origin_constraint(),)
    assert state.version == RequirementVersion(1)


def test_patchset_rejects_duplicate_operations_without_last_write_wins() -> None:
    with pytest.raises(DomainInvariantViolation):
        initial_state().apply(
            PatchSet(
                base_requirement_version=RequirementVersion(1),
                patches=(
                    RequirementPatch.replace(PatchTarget(ConstraintId("constraint-origin")), origin_constraint()),
                    RequirementPatch.replace(PatchTarget(ConstraintId("constraint-origin")), origin_constraint()),
                ),
            ),
            recorded_at=instant(10),
        )


def test_semantic_no_op_preserves_version_and_returns_existing_state() -> None:
    state = initial_state()

    next_state = state.apply(
        PatchSet(
            base_requirement_version=RequirementVersion(1),
            patches=(
                RequirementPatch.replace(PatchTarget(ConstraintId("constraint-origin")), origin_constraint()),
            ),
        ),
        recorded_at=instant(10),
    )

    assert next_state is state
    assert next_state.version == RequirementVersion(1)


def test_patchset_is_immutable_and_defensively_copies_patches() -> None:
    patches = [RequirementPatch.add(destination_constraint())]
    patch_set = PatchSet(RequirementVersion(1), tuple(patches))
    patches.append(RequirementPatch.add(date_constraint()))

    assert patch_set.patches == (RequirementPatch.add(destination_constraint()),)
    with pytest.raises(FrozenInstanceError):
        patch_set.base_requirement_version = RequirementVersion(2)  # type: ignore[misc]


def test_business_incomplete_requirement_can_be_structurally_represented() -> None:
    state = RequirementState.initial(requirement_id=RequirementId("requirement-1"), recorded_at=instant())

    assert state.constraints == ()
    assert state.preferences == ()
    assert not hasattr(state, "search_readiness")
    assert not hasattr(state, "clarification_status")
    assert not hasattr(state, "workflow_state")


def test_potentially_conflicting_constraints_are_not_m3_policy_rejected() -> None:
    state = RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(),
        constraints=(origin_constraint("origin-1", "PVG"), origin_constraint("origin-2", "SHA")),
    )

    assert len(state.constraints) == 2
