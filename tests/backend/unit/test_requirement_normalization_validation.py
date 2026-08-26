from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from flight_agent.application import (
    AirportCanonicalization,
    NormalizationContext,
    NormalizationIssue,
    NormalizationIssueCode,
    RequirementValidationIssueCode,
    SearchReadinessStatus,
    normalize_initial_requirement,
    normalize_patch_requirement,
    validate_requirement,
)
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    CabinClass,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    LocalTime,
    PassengerCount,
    PreferenceId,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
    ValueRange,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    InitialRequirementProposal,
    PatchProposalAction,
    PatchProposalOperation,
    PatchRequirementProposal,
)


def test_normalizer_canonicalizes_only_with_explicit_reference_context() -> None:
    proposal = InitialRequirementProposal(
        constraints=(origin_constraint("constraint-origin", "PVG"), destination_constraint("SHA"), date_constraint()),
        preferences=(airport_preference("SHA"),),
        source_input="fixture source",
    )
    context = normalization_context(
        canonical_airports=(
            AirportCanonicalization(source=AirportCode("SHA"), canonical=AirportCode("HGH")),
        )
    )

    result = normalize_initial_requirement(proposal, context)

    assert result.issues == ()
    assert result.candidate is not None
    assert result.candidate.constraints == (
        origin_constraint("constraint-origin", "PVG"),
        destination_constraint("HGH"),
        date_constraint(),
    )
    assert result.candidate.preferences == (airport_preference("HGH"),)
    assert result.candidate.source_input == "fixture source"


def test_normalizer_preserves_hard_soft_shape_and_does_not_invent_or_delete_intent() -> None:
    proposal = InitialRequirementProposal(
        constraints=(origin_constraint("constraint-origin", "PVG"),),
        preferences=(time_preference(),),
        source_input="already canonical",
    )

    result = normalize_initial_requirement(proposal, normalization_context())

    assert result.candidate is not None
    assert len(result.candidate.constraints) == 1
    assert len(result.candidate.preferences) == 1
    assert isinstance(result.candidate.constraints[0], HardConstraint)
    assert isinstance(result.candidate.preferences[0], SoftPreference)
    assert result.candidate.constraints[0].scope is ConstraintScope.ORIGIN_AIRPORT
    assert result.candidate.preferences[0].scope is PreferenceScope.DEPARTURE_TIME


def test_normalizer_preserves_max_price_money_constraint_without_price_aliasing() -> None:
    max_price = max_price_constraint("constraint-max-price", Decimal(800))
    price_preference_item = price_preference("preference-price", 1)
    proposal = InitialRequirementProposal(
        constraints=(max_price,),
        preferences=(price_preference_item,),
        source_input="already canonical max price",
    )

    result = normalize_initial_requirement(proposal, normalization_context())

    assert result.candidate is not None
    assert result.candidate.constraints == (max_price,)
    assert result.candidate.constraints[0].value == Money(Decimal(800), "CNY")
    assert result.candidate.preferences == (price_preference_item,)


def test_normalizer_returns_structured_issue_for_ambiguous_reference_before_commit() -> None:
    proposal = InitialRequirementProposal(
        constraints=(origin_constraint("constraint-origin", "NYC"),),
        source_input="ambiguous airport",
    )

    result = normalize_initial_requirement(
        proposal,
        normalization_context(ambiguous_airports=(AirportCode("NYC"),)),
    )

    assert result.candidate is None
    assert result.needs_clarification_before_commit
    assert result.issues == (
        NormalizationIssue(
            code=NormalizationIssueCode.AMBIGUOUS_REFERENCE,
            message="Airport reference cannot be uniquely canonicalized",
            source_value="NYC",
        ),
    )


def test_patch_proposal_normalization_reuses_items_without_creating_patchset() -> None:
    proposal = PatchRequirementProposal(
        operations=(
            PatchProposalOperation(
                action=PatchProposalAction.ADD_CONSTRAINT,
                item=destination_constraint("SHA"),
            ),
            PatchProposalOperation(
                action=PatchProposalAction.ADD_PREFERENCE,
                item=airport_preference("SHA"),
            ),
        ),
        source_input="patch source",
    )

    result = normalize_patch_requirement(
        proposal,
        normalization_context(
            canonical_airports=(
                AirportCanonicalization(source=AirportCode("SHA"), canonical=AirportCode("HGH")),
            )
        ),
    )

    assert result.candidate is not None
    assert result.candidate.constraints == (destination_constraint("HGH"),)
    assert result.candidate.preferences == (airport_preference("HGH"),)
    assert not hasattr(result.candidate, "patch_set")


def test_validation_ready_missing_and_version_binding_are_separate_from_requirement_state() -> None:
    ready = requirement_state(
        constraints=(
            origin_constraint("constraint-origin", "PVG"),
            destination_constraint("LAX"),
            date_constraint(),
        )
    )
    incomplete = requirement_state(constraints=(origin_constraint("constraint-origin", "PVG"),))

    ready_result = validate_requirement(ready)
    incomplete_result = validate_requirement(incomplete)

    assert ready_result.based_on == RequirementVersion(1)
    assert ready_result.readiness is SearchReadinessStatus.READY
    assert ready_result.issues == ()
    assert incomplete_result.readiness is SearchReadinessStatus.NOT_READY
    assert {issue.code for issue in incomplete_result.issues} == {
        RequirementValidationIssueCode.MISSING_DESTINATION,
        RequirementValidationIssueCode.MISSING_DEPARTURE_DATE,
    }
    assert not hasattr(ready, "search_readiness")
    assert not hasattr(ready, "validation_result")


def test_validation_search_readiness_is_independent_of_max_price() -> None:
    without_max = requirement_state(
        constraints=(
            origin_constraint("constraint-origin", "PVG"),
            destination_constraint("LAX"),
            date_constraint(),
        )
    )
    with_max = requirement_state(
        constraints=(
            origin_constraint("constraint-origin", "PVG"),
            destination_constraint("LAX"),
            date_constraint(),
            max_price_constraint("constraint-max-price", Decimal(800)),
        )
    )
    max_only = requirement_state(
        constraints=(max_price_constraint("constraint-max-price", Decimal(800)),)
    )

    assert validate_requirement(without_max).readiness is SearchReadinessStatus.READY
    assert validate_requirement(with_max).readiness is SearchReadinessStatus.READY
    assert validate_requirement(max_only).readiness is SearchReadinessStatus.NOT_READY
    assert RequirementValidationIssueCode.MISSING_ORIGIN in {
        issue.code for issue in validate_requirement(max_only).issues
    }


def test_validation_aggregates_multiple_missing_reasons() -> None:
    result = validate_requirement(requirement_state())

    assert result.readiness is SearchReadinessStatus.NOT_READY
    assert {issue.code for issue in result.issues} == {
        RequirementValidationIssueCode.MISSING_ORIGIN,
        RequirementValidationIssueCode.MISSING_DESTINATION,
        RequirementValidationIssueCode.MISSING_DEPARTURE_DATE,
    }


def test_deterministic_conflicts_are_business_results_not_invariant_violations() -> None:
    result = validate_requirement(
        requirement_state(
            constraints=(
                origin_constraint("constraint-origin", "PVG"),
                destination_constraint("PVG"),
                date_constraint("constraint-date-a", date(2026, 9, 1)),
                date_constraint("constraint-date-b", date(2026, 9, 2)),
                time_constraint("constraint-time-a", time(8, 0)),
                time_constraint("constraint-time-b", time(9, 0)),
                origin_constraint("constraint-origin-2", "SHA"),
                cabin_constraint("constraint-cabin-a", "ECONOMY"),
                cabin_constraint("constraint-cabin-b", "BUSINESS"),
            ),
        )
    )

    assert result.readiness is SearchReadinessStatus.READY
    assert {issue.code for issue in result.issues} >= {
        RequirementValidationIssueCode.ORIGIN_DESTINATION_CONFLICT,
        RequirementValidationIssueCode.INCOMPATIBLE_DATE_CONSTRAINTS,
        RequirementValidationIssueCode.INCOMPATIBLE_TIME_CONSTRAINTS,
        RequirementValidationIssueCode.INCOMPATIBLE_LOCATION_CONSTRAINTS,
        RequirementValidationIssueCode.INCOMPATIBLE_FLIGHT_STRUCTURE_CONSTRAINTS,
    }


def test_hard_soft_target_differences_do_not_create_conflict_by_default() -> None:
    result = validate_requirement(
        requirement_state(
            constraints=(
                origin_constraint("constraint-origin", "PVG"),
                destination_constraint("LAX"),
                date_constraint(),
            ),
            preferences=(airport_preference("SHA"),),
        )
    )

    assert result.readiness is SearchReadinessStatus.READY
    assert result.issues == ()


def test_soft_soft_price_differences_do_not_create_conflict_by_default() -> None:
    result = validate_requirement(
        requirement_state(
            constraints=(
                origin_constraint("constraint-origin", "PVG"),
                destination_constraint("LAX"),
                date_constraint(),
            ),
            preferences=(
                price_preference("preference-price-a", 1),
                price_preference("preference-price-b", 2),
            ),
        )
    )

    assert RequirementValidationIssueCode.INCOMPATIBLE_PRICE_CONSTRAINTS not in {
        issue.code for issue in result.issues
    }


def test_extreme_but_structurally_legal_requirement_is_not_invalidated() -> None:
    result = validate_requirement(
        requirement_state(
            constraints=(
                origin_constraint("constraint-origin", "PVG"),
                destination_constraint("LAX"),
                date_constraint(),
                passenger_constraint(9),
            )
        )
    )

    assert result.is_ready
    assert result.issues == ()


def test_validation_result_is_immutable() -> None:
    result = validate_requirement(requirement_state())

    with pytest.raises(FrozenInstanceError):
        result.readiness = SearchReadinessStatus.READY  # type: ignore[misc]


def normalization_context(
    canonical_airports: tuple[AirportCanonicalization, ...] = (),
    ambiguous_airports: tuple[AirportCode, ...] = (),
) -> NormalizationContext:
    return NormalizationContext(
        reference_instant=DomainInstant(datetime(2026, 8, 22, 0, 0, tzinfo=UTC)),
        timezone="Australia/Sydney",
        locale="en-US",
        reference_data_version="fixture-airports-v1",
        canonical_airports=canonical_airports,
        ambiguous_airports=ambiguous_airports,
    )


def requirement_state(
    constraints: tuple[HardConstraint, ...] = (),
    preferences: tuple[SoftPreference, ...] = (),
) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=DomainInstant(datetime(2026, 8, 22, 1, 0, tzinfo=UTC)),
        constraints=constraints,
        preferences=preferences,
    )


def origin_constraint(raw_id: str, airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.ORIGIN_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def destination_constraint(airport: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-destination"),
        scope=ConstraintScope.DESTINATION_AIRPORT,
        operator=ConstraintOperator.EQUALS,
        value=AirportCode(airport),
    )


def date_constraint(raw_id: str = "constraint-date", value: date = date(2026, 9, 1)) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_DATE,
        operator=ConstraintOperator.EQUALS,
        value=LocalDate(value),
    )


def time_constraint(raw_id: str, value: time) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.DEPARTURE_TIME,
        operator=ConstraintOperator.EQUALS,
        value=LocalTime(value),
    )


def max_price_constraint(raw_id: str, amount: Decimal) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.MAX_PRICE,
        operator=ConstraintOperator.AT_OR_BEFORE,
        value=Money(amount, "CNY"),
    )


def cabin_constraint(raw_id: str, cabin: str) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId(raw_id),
        scope=ConstraintScope.CABIN_CLASS,
        operator=ConstraintOperator.EQUALS,
        value=CabinClass(cabin),
    )


def passenger_constraint(count: int) -> HardConstraint:
    return HardConstraint(
        constraint_id=ConstraintId("constraint-passengers"),
        scope=ConstraintScope.PASSENGER_COUNT,
        operator=ConstraintOperator.EQUALS,
        value=PassengerCount(count),
    )


def airport_preference(airport: str) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-airport"),
        scope=PreferenceScope.AIRPORT_MATCH,
        importance=PreferenceImportance.MEDIUM,
        value=AirportCode(airport),
    )


def time_preference() -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId("preference-time"),
        scope=PreferenceScope.DEPARTURE_TIME,
        importance=PreferenceImportance.HIGH,
        value=ValueRange(LocalTime(time(8, 0)), LocalTime(time(10, 0))),
    )


def price_preference(raw_id: str, value: int) -> SoftPreference:
    return SoftPreference(
        preference_id=PreferenceId(raw_id),
        scope=PreferenceScope.PRICE,
        importance=PreferenceImportance.HIGH,
        value=PassengerCount(value),
    )
