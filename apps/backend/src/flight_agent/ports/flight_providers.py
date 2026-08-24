"""Flight provider acquisition capability boundary for M4-U1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.search import RequestedSearchScope, SearchPlan, SearchPlanId
from flight_agent.domain.shared import DomainId, DomainInvariantViolation, RequirementVersion


@dataclass(frozen=True)
class ProviderId(DomainId):
    """Opaque identity for an external acquisition provider."""


@dataclass(frozen=True)
class ProviderAcquisitionId(DomainId):
    """Opaque identity for one provider acquisition attempt."""


type RawEvidenceValue = (
    str
    | int
    | float
    | bool
    | None
    | tuple["RawEvidenceValue", ...]
    | tuple[tuple[str, "RawEvidenceValue"], ...]
)


@dataclass(frozen=True, init=False)
class ProviderRawEvidence:
    """Immutable provider-shaped raw evidence for one acquisition."""

    provider_id: ProviderId
    acquisition_id: ProviderAcquisitionId
    search_plan_id: SearchPlanId
    retrieved_at: datetime
    payload: RawEvidenceValue
    source_refs: tuple[str, ...]

    def __init__(
        self,
        provider_id: ProviderId,
        acquisition_id: ProviderAcquisitionId,
        search_plan_id: SearchPlanId,
        retrieved_at: datetime,
        payload: object,
        source_refs: tuple[str, ...] = (),
    ) -> None:
        if retrieved_at.tzinfo is None:
            raise DomainInvariantViolation("ProviderRawEvidence retrieved_at must be timezone-aware")
        source_refs_tuple = tuple(source_refs)
        if any(ref.strip() == "" for ref in source_refs_tuple):
            raise DomainInvariantViolation("ProviderRawEvidence source_refs must be non-empty strings")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "acquisition_id", acquisition_id)
        object.__setattr__(self, "search_plan_id", search_plan_id)
        object.__setattr__(self, "retrieved_at", retrieved_at)
        object.__setattr__(self, "payload", _freeze_raw_value(payload))
        object.__setattr__(self, "source_refs", source_refs_tuple)


class ProviderExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class ProviderDataStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    UNUSABLE = "UNUSABLE"
    UNKNOWN = "UNKNOWN"


class CoverageCompleteness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ProviderCoverageLimitation:
    code: str
    detail: str

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.detail.strip() == "":
            raise DomainInvariantViolation("ProviderCoverageLimitation requires code and detail")


@dataclass(frozen=True, init=False)
class ProviderCoverage:
    requested_scope: RequestedSearchScope
    actual_scope: RequestedSearchScope | None
    completeness: CoverageCompleteness
    limitations: tuple[ProviderCoverageLimitation, ...]

    def __init__(
        self,
        requested_scope: RequestedSearchScope,
        actual_scope: RequestedSearchScope | None,
        completeness: CoverageCompleteness,
        limitations: tuple[ProviderCoverageLimitation, ...] = (),
    ) -> None:
        limitations_tuple = tuple(limitations)
        if completeness is CoverageCompleteness.PARTIAL and len(limitations_tuple) == 0:
            raise DomainInvariantViolation("PARTIAL provider coverage requires a limitation")
        object.__setattr__(self, "requested_scope", requested_scope)
        object.__setattr__(self, "actual_scope", actual_scope)
        object.__setattr__(self, "completeness", completeness)
        object.__setattr__(self, "limitations", limitations_tuple)


@dataclass(frozen=True, init=False)
class ProviderSearchResult:
    """Outcome envelope for one provider acquisition."""

    provider_id: ProviderId
    acquisition_id: ProviderAcquisitionId
    search_plan_id: SearchPlanId
    requirement_id: RequirementId
    based_on_requirement_version: RequirementVersion
    execution_status: ProviderExecutionStatus
    data_status: ProviderDataStatus
    coverage: ProviderCoverage
    raw_evidence: ProviderRawEvidence | None

    def __init__(
        self,
        provider_id: ProviderId,
        acquisition_id: ProviderAcquisitionId,
        search_plan_id: SearchPlanId,
        requirement_id: RequirementId,
        based_on_requirement_version: RequirementVersion,
        execution_status: ProviderExecutionStatus,
        data_status: ProviderDataStatus,
        coverage: ProviderCoverage,
        raw_evidence: ProviderRawEvidence | None = None,
    ) -> None:
        if execution_status is not ProviderExecutionStatus.SUCCESS and data_status is ProviderDataStatus.EMPTY:
            raise DomainInvariantViolation("Failed provider execution must not be encoded as EMPTY data")
        if data_status is ProviderDataStatus.EMPTY and coverage.completeness is CoverageCompleteness.UNKNOWN:
            raise DomainInvariantViolation("EMPTY data requires known provider coverage evidence")
        if raw_evidence is not None:
            if raw_evidence.provider_id != provider_id:
                raise DomainInvariantViolation("ProviderSearchResult raw evidence provider mismatch")
            if raw_evidence.acquisition_id != acquisition_id:
                raise DomainInvariantViolation("ProviderSearchResult raw evidence acquisition mismatch")
            if raw_evidence.search_plan_id != search_plan_id:
                raise DomainInvariantViolation("ProviderSearchResult raw evidence search plan mismatch")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "acquisition_id", acquisition_id)
        object.__setattr__(self, "search_plan_id", search_plan_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "based_on_requirement_version", based_on_requirement_version)
        object.__setattr__(self, "execution_status", execution_status)
        object.__setattr__(self, "data_status", data_status)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "raw_evidence", raw_evidence)

    @classmethod
    def for_search_plan(
        cls,
        provider_id: ProviderId,
        acquisition_id: ProviderAcquisitionId,
        search_plan: SearchPlan,
        execution_status: ProviderExecutionStatus,
        data_status: ProviderDataStatus,
        coverage: ProviderCoverage,
        raw_evidence: ProviderRawEvidence | None = None,
    ) -> ProviderSearchResult:
        return cls(
            provider_id=provider_id,
            acquisition_id=acquisition_id,
            search_plan_id=search_plan.search_plan_id,
            requirement_id=search_plan.requirement_id,
            based_on_requirement_version=search_plan.based_on_requirement_version,
            execution_status=execution_status,
            data_status=data_status,
            coverage=coverage,
            raw_evidence=raw_evidence,
        )


class FlightProvider(Protocol):
    def search(self, search_plan: SearchPlan) -> ProviderSearchResult:
        """Acquire provider search data for a provider-neutral SearchPlan."""
        ...


def _freeze_raw_value(value: object) -> RawEvidenceValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return tuple(_freeze_raw_value(item) for item in value)
    if isinstance(value, dict):
        frozen_items: list[tuple[str, RawEvidenceValue]] = []
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str) or key.strip() == "":
                raise DomainInvariantViolation("ProviderRawEvidence payload mapping keys must be strings")
            frozen_items.append((key, _freeze_raw_value(item)))
        return tuple(frozen_items)
    raise DomainInvariantViolation("ProviderRawEvidence payload contains unsupported value")
