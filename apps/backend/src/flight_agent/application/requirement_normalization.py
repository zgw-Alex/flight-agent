"""Deterministic requirement normalization and validation for M3-U2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    RequirementState,
    RequirementValue,
    SoftPreference,
    ValueRange,
    ValueSet,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import InitialRequirementProposal, PatchRequirementProposal


class NormalizationIssueCode(str, Enum):
    AMBIGUOUS_REFERENCE = "AMBIGUOUS_REFERENCE"


@dataclass(frozen=True)
class NormalizationIssue:
    code: NormalizationIssueCode
    message: str
    source_value: str


@dataclass(frozen=True)
class AirportCanonicalization:
    source: AirportCode
    canonical: AirportCode


@dataclass(frozen=True)
class NormalizationContext:
    reference_instant: DomainInstant
    timezone: str
    locale: str
    reference_data_version: str
    canonical_airports: tuple[AirportCanonicalization, ...] = ()
    ambiguous_airports: tuple[AirportCode, ...] = ()


@dataclass(frozen=True)
class NormalizedRequirementCandidate:
    constraints: tuple[HardConstraint, ...] = ()
    preferences: tuple[SoftPreference, ...] = ()
    source_input: str = ""


@dataclass(frozen=True)
class NormalizationResult:
    candidate: NormalizedRequirementCandidate | None
    issues: tuple[NormalizationIssue, ...] = ()

    @property
    def needs_clarification_before_commit(self) -> bool:
        return len(self.issues) > 0


def normalize_initial_requirement(
    proposal: InitialRequirementProposal,
    context: NormalizationContext,
) -> NormalizationResult:
    return _normalize_items(proposal.constraints, proposal.preferences, proposal.source_input, context)


def normalize_patch_requirement(
    proposal: PatchRequirementProposal,
    context: NormalizationContext,
) -> NormalizationResult:
    constraints: list[HardConstraint] = []
    preferences: list[SoftPreference] = []
    for operation in proposal.operations:
        if isinstance(operation.item, HardConstraint):
            constraints.append(operation.item)
        elif isinstance(operation.item, SoftPreference):
            preferences.append(operation.item)
    return _normalize_items(tuple(constraints), tuple(preferences), proposal.source_input, context)


class SearchReadinessStatus(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class RequirementValidationIssueCode(str, Enum):
    MISSING_ORIGIN = "MISSING_ORIGIN"
    MISSING_DESTINATION = "MISSING_DESTINATION"
    MISSING_DEPARTURE_DATE = "MISSING_DEPARTURE_DATE"
    ORIGIN_DESTINATION_CONFLICT = "ORIGIN_DESTINATION_CONFLICT"
    INCOMPATIBLE_DATE_CONSTRAINTS = "INCOMPATIBLE_DATE_CONSTRAINTS"
    INCOMPATIBLE_TIME_CONSTRAINTS = "INCOMPATIBLE_TIME_CONSTRAINTS"
    INCOMPATIBLE_LOCATION_CONSTRAINTS = "INCOMPATIBLE_LOCATION_CONSTRAINTS"
    INCOMPATIBLE_PRICE_CONSTRAINTS = "INCOMPATIBLE_PRICE_CONSTRAINTS"
    INCOMPATIBLE_FLIGHT_STRUCTURE_CONSTRAINTS = "INCOMPATIBLE_FLIGHT_STRUCTURE_CONSTRAINTS"


@dataclass(frozen=True)
class RequirementValidationIssue:
    code: RequirementValidationIssueCode
    message: str


@dataclass(frozen=True)
class RequirementValidationResult:
    based_on: RequirementVersion
    readiness: SearchReadinessStatus
    issues: tuple[RequirementValidationIssue, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.readiness is SearchReadinessStatus.READY


def validate_requirement(requirement: RequirementState) -> RequirementValidationResult:
    issues = [
        *_missing_readiness_issues(requirement),
        *_conflict_issues(requirement),
    ]
    readiness = (
        SearchReadinessStatus.NOT_READY
        if any(_is_missing_issue(issue) for issue in issues)
        else SearchReadinessStatus.READY
    )
    return RequirementValidationResult(
        based_on=requirement.version,
        readiness=readiness,
        issues=tuple(issues),
    )


def _normalize_items(
    constraints: tuple[HardConstraint, ...],
    preferences: tuple[SoftPreference, ...],
    source_input: str,
    context: NormalizationContext,
) -> NormalizationResult:
    normalized_constraints: list[HardConstraint] = []
    normalized_preferences: list[SoftPreference] = []
    issues: list[NormalizationIssue] = []

    for constraint in constraints:
        normalized, issue = _normalize_constraint(constraint, context)
        if issue is not None:
            issues.append(issue)
        elif normalized is not None:
            normalized_constraints.append(normalized)

    for preference in preferences:
        normalized, issue = _normalize_preference(preference, context)
        if issue is not None:
            issues.append(issue)
        elif normalized is not None:
            normalized_preferences.append(normalized)

    if issues:
        return NormalizationResult(candidate=None, issues=tuple(issues))
    return NormalizationResult(
        candidate=NormalizedRequirementCandidate(
            constraints=tuple(normalized_constraints),
            preferences=tuple(normalized_preferences),
            source_input=source_input,
        ),
    )


def _normalize_constraint(
    constraint: HardConstraint,
    context: NormalizationContext,
) -> tuple[HardConstraint | None, NormalizationIssue | None]:
    value, issue = _normalize_expression(constraint.value, context)
    if issue is not None:
        return None, issue
    return (
        HardConstraint(
            constraint_id=constraint.constraint_id,
            scope=constraint.scope,
            operator=constraint.operator,
            value=value,
        ),
        None,
    )


def _normalize_preference(
    preference: SoftPreference,
    context: NormalizationContext,
) -> tuple[SoftPreference | None, NormalizationIssue | None]:
    if preference.value is None:
        return preference, None
    value, issue = _normalize_preference_value(preference.value, context)
    if issue is not None:
        return None, issue
    return (
        SoftPreference(
            preference_id=preference.preference_id,
            scope=preference.scope,
            importance=preference.importance,
            value=value,
        ),
        None,
    )


def _normalize_expression(
    value: RequirementValue | ValueSet | ValueRange,
    context: NormalizationContext,
) -> tuple[RequirementValue | ValueSet | ValueRange, NormalizationIssue | None]:
    if isinstance(value, ValueSet):
        normalized_items: list[RequirementValue] = []
        for item in value.items:
            normalized, issue = _normalize_requirement_value(item, context)
            if issue is not None:
                return value, issue
            normalized_items.append(normalized)
        return ValueSet(tuple(normalized_items)), None
    if isinstance(value, ValueRange):
        start, start_issue = _normalize_requirement_value(value.start, context)
        if start_issue is not None:
            return value, start_issue
        end, end_issue = _normalize_requirement_value(value.end, context)
        if end_issue is not None:
            return value, end_issue
        return ValueRange(start, end), None
    return _normalize_requirement_value(value, context)


def _normalize_preference_value(
    value: RequirementValue | ValueRange,
    context: NormalizationContext,
) -> tuple[RequirementValue | ValueRange, NormalizationIssue | None]:
    if isinstance(value, ValueRange):
        normalized, issue = _normalize_expression(value, context)
        if issue is not None:
            return value, issue
        if not isinstance(normalized, ValueRange):
            raise TypeError("ValueRange normalization returned an incompatible value type")
        return normalized, issue
    return _normalize_requirement_value(value, context)


def _normalize_requirement_value(
    value: RequirementValue,
    context: NormalizationContext,
) -> tuple[RequirementValue, NormalizationIssue | None]:
    if isinstance(value, AirportCode):
        return _normalize_airport(value, context)
    return value, None


def _normalize_airport(
    value: AirportCode,
    context: NormalizationContext,
) -> tuple[AirportCode, NormalizationIssue | None]:
    if value in context.ambiguous_airports:
        return value, NormalizationIssue(
            code=NormalizationIssueCode.AMBIGUOUS_REFERENCE,
            message="Airport reference cannot be uniquely canonicalized",
            source_value=value.value,
        )
    for mapping in context.canonical_airports:
        if mapping.source == value:
            return mapping.canonical, None
    return value, None


def _missing_readiness_issues(requirement: RequirementState) -> tuple[RequirementValidationIssue, ...]:
    scopes = {constraint.scope for constraint in requirement.constraints}
    issues: list[RequirementValidationIssue] = []
    if ConstraintScope.ORIGIN_AIRPORT not in scopes:
        issues.append(
            RequirementValidationIssue(
                RequirementValidationIssueCode.MISSING_ORIGIN,
                "Requirement is missing an origin airport",
            )
        )
    if ConstraintScope.DESTINATION_AIRPORT not in scopes:
        issues.append(
            RequirementValidationIssue(
                RequirementValidationIssueCode.MISSING_DESTINATION,
                "Requirement is missing a destination airport",
            )
        )
    if ConstraintScope.DEPARTURE_DATE not in scopes:
        issues.append(
            RequirementValidationIssue(
                RequirementValidationIssueCode.MISSING_DEPARTURE_DATE,
                "Requirement is missing a departure date",
            )
        )
    return tuple(issues)


def _conflict_issues(requirement: RequirementState) -> tuple[RequirementValidationIssue, ...]:
    issues: list[RequirementValidationIssue] = []
    issues.extend(_route_conflicts(requirement))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.DEPARTURE_DATE))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.DEPARTURE_TIME))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.ORIGIN_AIRPORT))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.DESTINATION_AIRPORT))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.CABIN_CLASS))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.PASSENGER_COUNT))
    issues.extend(_scope_conflicts(requirement, ConstraintScope.MAX_PRICE))
    return tuple(issues)


def _route_conflicts(requirement: RequirementState) -> tuple[RequirementValidationIssue, ...]:
    origins = _equals_values(requirement, ConstraintScope.ORIGIN_AIRPORT)
    destinations = _equals_values(requirement, ConstraintScope.DESTINATION_AIRPORT)
    if any(origin == destination for origin in origins for destination in destinations):
        return (
            RequirementValidationIssue(
                RequirementValidationIssueCode.ORIGIN_DESTINATION_CONFLICT,
                "Origin and destination cannot be the same airport",
            ),
        )
    return ()


def _scope_conflicts(
    requirement: RequirementState,
    scope: ConstraintScope,
) -> tuple[RequirementValidationIssue, ...]:
    values = _equals_values(requirement, scope)
    if len(set(values)) <= 1:
        return ()
    return (
        RequirementValidationIssue(_conflict_code_for_scope(scope), f"{scope.value} has incompatible hard constraints"),
    )


def _equals_values(
    requirement: RequirementState,
    scope: ConstraintScope,
) -> tuple[RequirementValue | ValueSet | ValueRange, ...]:
    return tuple(
        constraint.value
        for constraint in requirement.constraints
        if constraint.scope is scope and constraint.operator is ConstraintOperator.EQUALS
    )


def _conflict_code_for_scope(scope: ConstraintScope) -> RequirementValidationIssueCode:
    if scope is ConstraintScope.DEPARTURE_DATE:
        return RequirementValidationIssueCode.INCOMPATIBLE_DATE_CONSTRAINTS
    if scope is ConstraintScope.DEPARTURE_TIME:
        return RequirementValidationIssueCode.INCOMPATIBLE_TIME_CONSTRAINTS
    if scope in {ConstraintScope.ORIGIN_AIRPORT, ConstraintScope.DESTINATION_AIRPORT}:
        return RequirementValidationIssueCode.INCOMPATIBLE_LOCATION_CONSTRAINTS
    if scope is ConstraintScope.MAX_PRICE:
        return RequirementValidationIssueCode.INCOMPATIBLE_PRICE_CONSTRAINTS
    return RequirementValidationIssueCode.INCOMPATIBLE_FLIGHT_STRUCTURE_CONSTRAINTS


def _is_missing_issue(issue: RequirementValidationIssue) -> bool:
    return issue.code in {
        RequirementValidationIssueCode.MISSING_ORIGIN,
        RequirementValidationIssueCode.MISSING_DESTINATION,
        RequirementValidationIssueCode.MISSING_DEPARTURE_DATE,
    }
