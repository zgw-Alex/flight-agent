"""M6 derived feature definition, calculation, and artifact contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum

from flight_agent.domain.decision.evaluation import (
    DecisionConstraintScope,
    OfferBackedItineraryCandidate,
)
from flight_agent.domain.decision.identity import DerivedFeatureRunId, DerivedFeatureSetId
from flight_agent.domain.decision.policy import DecisionPolicyVersion, FeatureDefinitionVersion
from flight_agent.domain.flights import CandidateSnapshot, FlightSegment, Itinerary, Money, Offer
from flight_agent.domain.requirements import ConstraintScope, LocalDate, RequirementState
from flight_agent.domain.shared import (
    DomainId,
    DomainInvariantViolation,
    DomainValue,
    RequirementVersion,
    SnapshotVersion,
    ValueState,
)
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource


class FeatureClassification(str, Enum):
    CANDIDATE_INTRINSIC = "CANDIDATE_INTRINSIC"
    REQUIREMENT_RELATIVE = "REQUIREMENT_RELATIVE"


class FeatureValueType(str, Enum):
    INTEGER = "INTEGER"
    MONEY = "MONEY"
    BOOLEAN = "BOOLEAN"


DEFAULT_FEATURE_DEFINITION_VERSION = FeatureDefinitionVersion("v1")


@dataclass(frozen=True)
class FeatureKey(DomainId):
    """Stable semantic identity for a derived feature."""


@dataclass(frozen=True)
class FeatureDependency:
    source: str
    path: str

    def __post_init__(self) -> None:
        if self.source.strip() == "" or self.path.strip() == "":
            raise DomainInvariantViolation("FeatureDependency requires source and path")


@dataclass(frozen=True)
class RequirementFeatureDependency:
    source: str
    key: str

    def __post_init__(self) -> None:
        if self.source.strip() == "" or self.key.strip() == "":
            raise DomainInvariantViolation("RequirementFeatureDependency requires source and key")


@dataclass(frozen=True)
class ReferenceDataDependency:
    source: str
    key: str
    version: str

    def __post_init__(self) -> None:
        if self.source.strip() == "" or self.key.strip() == "" or self.version.strip() == "":
            raise DomainInvariantViolation("ReferenceDataDependency requires source, key, and version")


@dataclass(frozen=True, init=False)
class FeatureDefinition:
    feature_key: FeatureKey
    value_type: FeatureValueType
    scope: DecisionConstraintScope
    classification: FeatureClassification
    canonical_dependencies: tuple[FeatureDependency, ...]
    requirement_dependencies: tuple[RequirementFeatureDependency, ...]
    reference_data_dependencies: tuple[ReferenceDataDependency, ...]
    definition_version: FeatureDefinitionVersion
    calculator_id: str

    def __init__(
        self,
        feature_key: FeatureKey,
        value_type: FeatureValueType,
        scope: DecisionConstraintScope,
        classification: FeatureClassification,
        canonical_dependencies: tuple[FeatureDependency, ...],
        requirement_dependencies: tuple[RequirementFeatureDependency, ...] = (),
        reference_data_dependencies: tuple[ReferenceDataDependency, ...] = (),
        definition_version: FeatureDefinitionVersion = DEFAULT_FEATURE_DEFINITION_VERSION,
        calculator_id: str | None = None,
    ) -> None:
        canonical_tuple = tuple(canonical_dependencies)
        requirement_tuple = tuple(requirement_dependencies)
        reference_tuple = tuple(reference_data_dependencies)
        if len(canonical_tuple) == 0:
            raise DomainInvariantViolation("FeatureDefinition requires canonical dependencies")
        if classification is FeatureClassification.CANDIDATE_INTRINSIC and len(requirement_tuple) > 0:
            raise DomainInvariantViolation("Intrinsic FeatureDefinition must not depend on Requirement")
        if classification is FeatureClassification.REQUIREMENT_RELATIVE and len(requirement_tuple) == 0:
            raise DomainInvariantViolation("Relative FeatureDefinition requires Requirement dependency")
        resolved_calculator_id = calculator_id or feature_key.value
        if resolved_calculator_id.strip() == "":
            raise DomainInvariantViolation("FeatureDefinition requires calculator identity")
        object.__setattr__(self, "feature_key", feature_key)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "classification", classification)
        object.__setattr__(self, "canonical_dependencies", canonical_tuple)
        object.__setattr__(self, "requirement_dependencies", requirement_tuple)
        object.__setattr__(self, "reference_data_dependencies", reference_tuple)
        object.__setattr__(self, "definition_version", definition_version)
        object.__setattr__(self, "calculator_id", resolved_calculator_id)


FeatureScalar = int | bool | Money


@dataclass(frozen=True, init=False)
class FeatureValue:
    feature_key: FeatureKey
    candidate: OfferBackedItineraryCandidate
    value: DomainValue[FeatureScalar]
    value_type: FeatureValueType
    unit: str | None
    evidence: tuple[EvidenceRef, ...]
    canonical_dependencies: tuple[FeatureDependency, ...]
    requirement_dependencies: tuple[RequirementFeatureDependency, ...]
    reference_data_dependencies: tuple[ReferenceDataDependency, ...]
    definition_version: FeatureDefinitionVersion

    def __init__(
        self,
        feature_key: FeatureKey,
        candidate: OfferBackedItineraryCandidate,
        value: DomainValue[FeatureScalar],
        value_type: FeatureValueType,
        evidence: tuple[EvidenceRef, ...],
        canonical_dependencies: tuple[FeatureDependency, ...],
        requirement_dependencies: tuple[RequirementFeatureDependency, ...] = (),
        reference_data_dependencies: tuple[ReferenceDataDependency, ...] = (),
        definition_version: FeatureDefinitionVersion = DEFAULT_FEATURE_DEFINITION_VERSION,
        unit: str | None = None,
    ) -> None:
        if unit is not None and unit.strip() == "":
            raise DomainInvariantViolation("FeatureValue unit must be non-empty when provided")
        if value.is_known:
            _validate_feature_value_type(value.value, value_type)
        object.__setattr__(self, "feature_key", feature_key)
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "evidence", tuple(evidence))
        object.__setattr__(self, "canonical_dependencies", tuple(canonical_dependencies))
        object.__setattr__(self, "requirement_dependencies", tuple(requirement_dependencies))
        object.__setattr__(self, "reference_data_dependencies", tuple(reference_data_dependencies))
        object.__setattr__(self, "definition_version", definition_version)

    @property
    def value_status(self) -> ValueState:
        return self.value.state


@dataclass(frozen=True)
class DerivedFeatureInputLineage:
    snapshot_id: DomainId
    snapshot_version: SnapshotVersion
    requirement_id: DomainId | None
    requirement_version: RequirementVersion | None


@dataclass(frozen=True, init=False)
class DerivedFeatureRun:
    run_id: DerivedFeatureRunId
    requested_feature_keys: tuple[FeatureKey, ...]
    required_feature_keys: tuple[FeatureKey, ...]
    input_lineage: DerivedFeatureInputLineage
    feature_policy_version: DecisionPolicyVersion
    feature_definition_versions: tuple[tuple[FeatureKey, FeatureDefinitionVersion], ...]
    reference_data_versions: tuple[str, ...]

    def __init__(
        self,
        run_id: DerivedFeatureRunId,
        requested_feature_keys: tuple[FeatureKey, ...],
        required_feature_keys: tuple[FeatureKey, ...],
        input_lineage: DerivedFeatureInputLineage,
        feature_policy_version: DecisionPolicyVersion,
        feature_definition_versions: tuple[tuple[FeatureKey, FeatureDefinitionVersion], ...],
        reference_data_versions: tuple[str, ...] = (),
    ) -> None:
        requested_tuple = _unique_feature_keys(requested_feature_keys, "requested FeatureKeys")
        required_tuple = _unique_feature_keys(required_feature_keys, "required FeatureKeys")
        definition_versions_tuple = tuple(feature_definition_versions)
        if len(definition_versions_tuple) != len(required_tuple):
            raise DomainInvariantViolation("DerivedFeatureRun requires one definition version per key")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "requested_feature_keys", requested_tuple)
        object.__setattr__(self, "required_feature_keys", required_tuple)
        object.__setattr__(self, "input_lineage", input_lineage)
        object.__setattr__(self, "feature_policy_version", feature_policy_version)
        object.__setattr__(self, "feature_definition_versions", definition_versions_tuple)
        object.__setattr__(self, "reference_data_versions", tuple(sorted(reference_data_versions)))


@dataclass(frozen=True, init=False)
class DerivedFeatureSet:
    feature_set_id: DerivedFeatureSetId
    run_id: DerivedFeatureRunId
    input_lineage: DerivedFeatureInputLineage
    feature_definition_versions: tuple[tuple[FeatureKey, FeatureDefinitionVersion], ...]
    reference_data_versions: tuple[str, ...]
    values: tuple[FeatureValue, ...]

    def __init__(
        self,
        feature_set_id: DerivedFeatureSetId,
        run_id: DerivedFeatureRunId,
        input_lineage: DerivedFeatureInputLineage,
        feature_definition_versions: tuple[tuple[FeatureKey, FeatureDefinitionVersion], ...],
        values: tuple[FeatureValue, ...],
        reference_data_versions: tuple[str, ...] = (),
    ) -> None:
        values_tuple = tuple(values)
        value_keys = {(value.candidate, value.feature_key) for value in values_tuple}
        if len(value_keys) != len(values_tuple):
            raise DomainInvariantViolation("DerivedFeatureSet values must be unique per candidate/key")
        object.__setattr__(self, "feature_set_id", feature_set_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "input_lineage", input_lineage)
        object.__setattr__(self, "feature_definition_versions", tuple(feature_definition_versions))
        object.__setattr__(self, "reference_data_versions", tuple(sorted(reference_data_versions)))
        object.__setattr__(self, "values", values_tuple)

    def value_for(
        self,
        candidate: OfferBackedItineraryCandidate,
        feature_key: FeatureKey,
    ) -> FeatureValue:
        matches = [
            value
            for value in self.values
            if value.candidate == candidate and value.feature_key == feature_key
        ]
        if len(matches) != 1:
            raise DomainInvariantViolation("FeatureValue lookup requires exactly one match")
        return matches[0]


FeatureCalculator = Callable[[FeatureDefinition, CandidateSnapshot, RequirementState | None, Offer], FeatureValue]


@dataclass(frozen=True, init=False)
class FeatureDefinitionRegistry:
    definitions: tuple[FeatureDefinition, ...]

    def __init__(self, definitions: tuple[FeatureDefinition, ...]) -> None:
        definitions_tuple = tuple(definitions)
        keys = tuple(definition.feature_key for definition in definitions_tuple)
        if len(frozenset(keys)) != len(keys):
            raise DomainInvariantViolation("FeatureDefinitionRegistry requires unique FeatureKeys")
        object.__setattr__(
            self,
            "definitions",
            tuple(sorted(definitions_tuple, key=lambda definition: definition.feature_key.value)),
        )

    def get(self, feature_key: FeatureKey) -> FeatureDefinition:
        for definition in self.definitions:
            if definition.feature_key == feature_key:
                return definition
        raise DomainInvariantViolation(f"Unknown FeatureKey: {feature_key.value}")

    def resolve_required(self, requested_feature_keys: Iterable[FeatureKey]) -> tuple[FeatureDefinition, ...]:
        requested_tuple = _unique_feature_keys(tuple(requested_feature_keys), "requested FeatureKeys")
        return tuple(self.get(key) for key in sorted(requested_tuple, key=lambda key: key.value))


class DerivedFeatureEngine:
    def __init__(
        self,
        *,
        registry: FeatureDefinitionRegistry,
        calculators: dict[FeatureKey, FeatureCalculator],
    ) -> None:
        if set(calculators) != {definition.feature_key for definition in registry.definitions}:
            raise DomainInvariantViolation("Feature calculators must exactly match registry definitions")
        self._registry = registry
        self._calculators = dict(calculators)

    def compute(
        self,
        *,
        feature_set_id: DerivedFeatureSetId,
        run_id: DerivedFeatureRunId,
        requested_feature_keys: tuple[FeatureKey, ...],
        snapshot: CandidateSnapshot,
        requirement: RequirementState | None,
        feature_policy_version: DecisionPolicyVersion,
    ) -> tuple[DerivedFeatureRun, DerivedFeatureSet]:
        definitions = self._registry.resolve_required(requested_feature_keys)
        _validate_requirement_availability(definitions, requirement)
        definition_versions = tuple(
            (definition.feature_key, definition.definition_version)
            for definition in definitions
        )
        reference_versions = tuple(
            dependency.version
            for definition in definitions
            for dependency in definition.reference_data_dependencies
        )
        lineage = DerivedFeatureInputLineage(
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            requirement_id=requirement.requirement_id if requirement is not None else None,
            requirement_version=requirement.version if requirement is not None else None,
        )
        run = DerivedFeatureRun(
            run_id=run_id,
            requested_feature_keys=tuple(requested_feature_keys),
            required_feature_keys=tuple(definition.feature_key for definition in definitions),
            input_lineage=lineage,
            feature_policy_version=feature_policy_version,
            feature_definition_versions=definition_versions,
            reference_data_versions=reference_versions,
        )
        values = tuple(
            self._calculators[definition.feature_key](definition, snapshot, requirement, offer)
            for offer in sorted(snapshot.offers, key=lambda item: item.offer_id.value)
            for definition in definitions
        )
        feature_set = DerivedFeatureSet(
            feature_set_id=feature_set_id,
            run_id=run_id,
            input_lineage=lineage,
            feature_definition_versions=definition_versions,
            reference_data_versions=reference_versions,
            values=values,
        )
        return run, feature_set


TOTAL_PRICE = FeatureKey("total_price")
STOP_COUNT = FeatureKey("stop_count")
DEPARTURE_DATE_MATCHES_REQUIREMENT = FeatureKey("departure_date_matches_requirement")


def m6_default_feature_registry() -> FeatureDefinitionRegistry:
    return FeatureDefinitionRegistry(
        (
            FeatureDefinition(
                feature_key=TOTAL_PRICE,
                value_type=FeatureValueType.MONEY,
                scope=DecisionConstraintScope.OFFER,
                classification=FeatureClassification.CANDIDATE_INTRINSIC,
                canonical_dependencies=(
                    FeatureDependency("offer", "total_price"),
                ),
                definition_version=FeatureDefinitionVersion("total-price-v1"),
                calculator_id="total_price_calculator",
            ),
            FeatureDefinition(
                feature_key=STOP_COUNT,
                value_type=FeatureValueType.INTEGER,
                scope=DecisionConstraintScope.ITINERARY,
                classification=FeatureClassification.CANDIDATE_INTRINSIC,
                canonical_dependencies=(
                    FeatureDependency("itinerary", "segment_ids"),
                ),
                definition_version=FeatureDefinitionVersion("stop-count-v1"),
                calculator_id="stop_count_calculator",
            ),
            FeatureDefinition(
                feature_key=DEPARTURE_DATE_MATCHES_REQUIREMENT,
                value_type=FeatureValueType.BOOLEAN,
                scope=DecisionConstraintScope.ITINERARY,
                classification=FeatureClassification.REQUIREMENT_RELATIVE,
                canonical_dependencies=(
                    FeatureDependency("segment", "first.departure_at.date"),
                ),
                requirement_dependencies=(
                    RequirementFeatureDependency("constraint", ConstraintScope.DEPARTURE_DATE.value),
                ),
                definition_version=FeatureDefinitionVersion("departure-date-match-v1"),
                calculator_id="departure_date_matches_requirement_calculator",
            ),
        )
    )


def m6_default_derived_feature_engine() -> DerivedFeatureEngine:
    return DerivedFeatureEngine(
        registry=m6_default_feature_registry(),
        calculators={
            TOTAL_PRICE: calculate_total_price,
            STOP_COUNT: calculate_stop_count,
            DEPARTURE_DATE_MATCHES_REQUIREMENT: calculate_departure_date_matches_requirement,
        },
    )


def calculate_total_price(
    definition: FeatureDefinition,
    snapshot: CandidateSnapshot,
    requirement: RequirementState | None,
    offer: Offer,
) -> FeatureValue:
    del snapshot, requirement
    return _known_feature_value(
        definition,
        offer,
        DomainValue.known(offer.total_price),
        evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
    )


def calculate_stop_count(
    definition: FeatureDefinition,
    snapshot: CandidateSnapshot,
    requirement: RequirementState | None,
    offer: Offer,
) -> FeatureValue:
    del requirement
    itinerary = _itinerary_for_offer(snapshot, offer)
    stop_count = max(len(itinerary.segment_ids) - 1, 0)
    return _known_feature_value(
        definition,
        offer,
        DomainValue.known(stop_count),
        evidence=(
            EvidenceRef(EvidenceSource.OFFER, offer.offer_id),
            EvidenceRef(EvidenceSource.ITINERARY, itinerary.itinerary_id),
        ),
        unit="count",
    )


def calculate_departure_date_matches_requirement(
    definition: FeatureDefinition,
    snapshot: CandidateSnapshot,
    requirement: RequirementState | None,
    offer: Offer,
) -> FeatureValue:
    if requirement is None:
        raise DomainInvariantViolation("Requirement-relative feature requires RequirementState")
    itinerary = _itinerary_for_offer(snapshot, offer)
    first_segment = _first_segment(snapshot, itinerary)
    requested_date = _required_departure_date(requirement)
    if requested_date is None:
        return FeatureValue(
            feature_key=definition.feature_key,
            candidate=_candidate_for_offer(offer),
            value=DomainValue.not_applicable(),
            value_type=definition.value_type,
            evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
            canonical_dependencies=definition.canonical_dependencies,
            requirement_dependencies=definition.requirement_dependencies,
            reference_data_dependencies=definition.reference_data_dependencies,
            definition_version=definition.definition_version,
        )
    return _known_feature_value(
        definition,
        offer,
        DomainValue.known(first_segment.departure_at.value.date() == requested_date),
        evidence=(
            EvidenceRef(EvidenceSource.OFFER, offer.offer_id),
            EvidenceRef(EvidenceSource.ITINERARY, itinerary.itinerary_id),
        ),
    )


def _known_feature_value(
    definition: FeatureDefinition,
    offer: Offer,
    value: DomainValue[FeatureScalar],
    *,
    evidence: tuple[EvidenceRef, ...],
    unit: str | None = None,
) -> FeatureValue:
    return FeatureValue(
        feature_key=definition.feature_key,
        candidate=_candidate_for_offer(offer),
        value=value,
        value_type=definition.value_type,
        unit=unit,
        evidence=evidence,
        canonical_dependencies=definition.canonical_dependencies,
        requirement_dependencies=definition.requirement_dependencies,
        reference_data_dependencies=definition.reference_data_dependencies,
        definition_version=definition.definition_version,
    )


def _candidate_for_offer(offer: Offer) -> OfferBackedItineraryCandidate:
    return OfferBackedItineraryCandidate(offer_id=offer.offer_id, itinerary_id=offer.itinerary_id)


def _itinerary_for_offer(snapshot: CandidateSnapshot, offer: Offer) -> Itinerary:
    matches = [
        itinerary
        for itinerary in snapshot.itineraries
        if itinerary.itinerary_id == offer.itinerary_id
    ]
    if len(matches) != 1:
        raise DomainInvariantViolation("Offer must reference exactly one itinerary")
    return matches[0]


def _first_segment(snapshot: CandidateSnapshot, itinerary: Itinerary) -> FlightSegment:
    first_segment_id = itinerary.segment_ids[0]
    matches = [segment for segment in snapshot.segments if segment.segment_id == first_segment_id]
    if len(matches) != 1:
        raise DomainInvariantViolation("Itinerary must reference exactly one first segment")
    return matches[0]


def _required_departure_date(requirement: RequirementState) -> date | None:
    matching_constraints = tuple(
        constraint
        for constraint in requirement.constraints
        if constraint.scope is ConstraintScope.DEPARTURE_DATE
    )
    if len(matching_constraints) == 0:
        return None
    if len(matching_constraints) > 1:
        raise DomainInvariantViolation("Departure date feature requires one departure date constraint")
    value = matching_constraints[0].value
    if not isinstance(value, LocalDate):
        raise DomainInvariantViolation("Departure date feature requires LocalDate constraint value")
    return value.value


def _validate_requirement_availability(
    definitions: tuple[FeatureDefinition, ...],
    requirement: RequirementState | None,
) -> None:
    if requirement is None and any(
        definition.classification is FeatureClassification.REQUIREMENT_RELATIVE
        for definition in definitions
    ):
        raise DomainInvariantViolation("Requirement-relative features require RequirementState")


def _validate_feature_value_type(value: FeatureScalar, value_type: FeatureValueType) -> None:
    expected = {
        FeatureValueType.INTEGER: int,
        FeatureValueType.MONEY: Money,
        FeatureValueType.BOOLEAN: bool,
    }[value_type]
    if value_type is FeatureValueType.INTEGER and isinstance(value, bool):
        raise DomainInvariantViolation("INTEGER FeatureValue must not carry bool")
    if not isinstance(value, expected):
        raise DomainInvariantViolation("FeatureValue typed value does not match value_type")


def _unique_feature_keys(keys: tuple[FeatureKey, ...], label: str) -> tuple[FeatureKey, ...]:
    if len(frozenset(keys)) != len(keys):
        raise DomainInvariantViolation(f"Duplicate {label} are not allowed")
    return keys
