"""CandidateSnapshot assembly boundary for M4-U5."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    Coverage,
    CoverageLimitation,
    CoverageStatus,
)
from flight_agent.domain.search import RequestedSearchScope, SearchPlan
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    FreshnessState,
    ProvenanceRef,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.ports import (
    CoverageCompleteness,
    MergedCandidateGraph,
    ProviderDataStatus,
    ProviderExecutionStatus,
    ProviderSearchResult,
)


@dataclass(frozen=True)
class AssemblerVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("AssemblerVersion requires a non-empty value")


@dataclass(frozen=True)
class FixtureSchemaVersion:
    value: str

    def __post_init__(self) -> None:
        if self.value.strip() == "":
            raise DomainInvariantViolation("FixtureSchemaVersion requires a non-empty value")


@dataclass(frozen=True)
class SnapshotProcessingManifest:
    fixture_schema_versions: tuple[FixtureSchemaVersion, ...]
    mapper_versions: tuple[str, ...]
    normalizer_versions: tuple[str, ...]
    reference_data_versions: tuple[str, ...]
    merger_version: str
    assembler_version: AssemblerVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_schema_versions", tuple(self.fixture_schema_versions))
        object.__setattr__(self, "mapper_versions", tuple(self.mapper_versions))
        object.__setattr__(self, "normalizer_versions", tuple(self.normalizer_versions))
        object.__setattr__(self, "reference_data_versions", tuple(self.reference_data_versions))
        if (
            len(self.mapper_versions) == 0
            or len(self.normalizer_versions) == 0
            or len(self.reference_data_versions) == 0
            or self.merger_version.strip() == ""
        ):
            raise DomainInvariantViolation("SnapshotProcessingManifest requires processing versions")


@dataclass(frozen=True)
class SnapshotFreshnessEvidence:
    provider_retrieved_at: tuple[DomainInstant, ...]
    structural_observed_at: DomainInstant | None
    offer_observed_at: tuple[DomainInstant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_retrieved_at", tuple(self.provider_retrieved_at))
        object.__setattr__(self, "offer_observed_at", tuple(self.offer_observed_at))


class SnapshotCreationStatus(str, Enum):
    COMPLETE_SNAPSHOT = "COMPLETE_SNAPSHOT"
    PARTIAL_SNAPSHOT = "PARTIAL_SNAPSHOT"
    LEGITIMATE_EMPTY_SNAPSHOT = "LEGITIMATE_EMPTY_SNAPSHOT"
    NO_NEW_SNAPSHOT = "NO_NEW_SNAPSHOT"


@dataclass(frozen=True)
class SnapshotAssemblyIssue:
    code: str
    detail: str

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.detail.strip() == "":
            raise DomainInvariantViolation("SnapshotAssemblyIssue requires code and detail")


@dataclass(frozen=True, init=False)
class SnapshotAssemblyOutcome:
    status: SnapshotCreationStatus
    snapshot: CandidateSnapshot | None
    processing_manifest: SnapshotProcessingManifest
    provider_results: tuple[ProviderSearchResult, ...]
    freshness_evidence: SnapshotFreshnessEvidence
    aggregate_coverage: Coverage
    issues: tuple[SnapshotAssemblyIssue, ...]

    def __init__(
        self,
        status: SnapshotCreationStatus,
        snapshot: CandidateSnapshot | None,
        processing_manifest: SnapshotProcessingManifest,
        provider_results: tuple[ProviderSearchResult, ...],
        freshness_evidence: SnapshotFreshnessEvidence,
        aggregate_coverage: Coverage,
        issues: tuple[SnapshotAssemblyIssue, ...] = (),
    ) -> None:
        if status is SnapshotCreationStatus.NO_NEW_SNAPSHOT and snapshot is not None:
            raise DomainInvariantViolation("NO_NEW_SNAPSHOT outcome must not carry a snapshot")
        if status is not SnapshotCreationStatus.NO_NEW_SNAPSHOT and snapshot is None:
            raise DomainInvariantViolation("Snapshot outcome must carry a CandidateSnapshot")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "processing_manifest", processing_manifest)
        object.__setattr__(self, "provider_results", tuple(provider_results))
        object.__setattr__(self, "freshness_evidence", freshness_evidence)
        object.__setattr__(self, "aggregate_coverage", aggregate_coverage)
        object.__setattr__(self, "issues", tuple(issues))


@dataclass(frozen=True)
class SnapshotAssemblyInput:
    search_plan: SearchPlan
    merged_graph: MergedCandidateGraph
    provider_results: tuple[ProviderSearchResult, ...]
    snapshot_id: CandidateSnapshotId
    created_at: DomainInstant
    processing_manifest: SnapshotProcessingManifest

    def __post_init__(self) -> None:
        if len(self.provider_results) == 0:
            raise DomainInvariantViolation("Snapshot assembly requires provider evidence")
        object.__setattr__(self, "provider_results", tuple(self.provider_results))


class CandidateSnapshotAssembler:
    def __init__(self, assembler_version: AssemblerVersion) -> None:
        self.assembler_version = assembler_version

    def assemble(self, assembly_input: SnapshotAssemblyInput) -> SnapshotAssemblyOutcome:
        if assembly_input.processing_manifest.assembler_version != self.assembler_version:
            raise DomainInvariantViolation("Snapshot assembler version must match manifest")
        _validate_search_plan_lineage(assembly_input)
        aggregate_coverage = _aggregate_coverage(
            assembly_input.search_plan.requested_scope,
            assembly_input.provider_results,
        )
        freshness_evidence = _freshness_evidence(assembly_input.provider_results)
        issues = _assembly_issues(assembly_input.provider_results, assembly_input.merged_graph)

        if _is_no_new_snapshot(assembly_input.provider_results, assembly_input.merged_graph):
            return SnapshotAssemblyOutcome(
                status=SnapshotCreationStatus.NO_NEW_SNAPSHOT,
                snapshot=None,
                processing_manifest=assembly_input.processing_manifest,
                provider_results=assembly_input.provider_results,
                freshness_evidence=freshness_evidence,
                aggregate_coverage=aggregate_coverage,
                issues=issues or (SnapshotAssemblyIssue("NO_USABLE_CANONICAL_OUTCOME", "No usable canonical graph"),),
            )

        try:
            snapshot = CandidateSnapshot(
                snapshot_id=assembly_input.snapshot_id,
                version=SnapshotVersion(1),
                created_at=assembly_input.created_at,
                created_from_requirement_version=assembly_input.search_plan.based_on_requirement_version,
                structural_freshness=StructuralFreshness(_structural_freshness_state(freshness_evidence)),
                coverage=aggregate_coverage,
                segments=assembly_input.merged_graph.segments,
                itineraries=assembly_input.merged_graph.itineraries,
                offers=assembly_input.merged_graph.offers,
                provenance=_snapshot_provenance(assembly_input.provider_results, assembly_input.merged_graph),
            )
        except DomainInvariantViolation as exc:
            return SnapshotAssemblyOutcome(
                status=SnapshotCreationStatus.NO_NEW_SNAPSHOT,
                snapshot=None,
                processing_manifest=assembly_input.processing_manifest,
                provider_results=assembly_input.provider_results,
                freshness_evidence=freshness_evidence,
                aggregate_coverage=aggregate_coverage,
                issues=issues + (SnapshotAssemblyIssue("GRAPH_INVARIANT_FAILURE", str(exc)),),
            )

        return SnapshotAssemblyOutcome(
            status=_snapshot_status(assembly_input.provider_results, assembly_input.merged_graph, aggregate_coverage),
            snapshot=snapshot,
            processing_manifest=assembly_input.processing_manifest,
            provider_results=assembly_input.provider_results,
            freshness_evidence=freshness_evidence,
            aggregate_coverage=aggregate_coverage,
            issues=issues,
        )


def build_processing_manifest(
    *,
    fixture_schema_versions: tuple[FixtureSchemaVersion, ...] = (),
    merged_graph: MergedCandidateGraph,
    assembler_version: AssemblerVersion,
) -> SnapshotProcessingManifest:
    return SnapshotProcessingManifest(
        fixture_schema_versions=fixture_schema_versions,
        mapper_versions=tuple(version.value for version in merged_graph.mapper_versions),
        normalizer_versions=tuple(version.value for version in merged_graph.normalizer_versions),
        reference_data_versions=tuple(version.value for version in merged_graph.reference_data_versions),
        merger_version=merged_graph.merger_version.value,
        assembler_version=assembler_version,
    )


def _validate_search_plan_lineage(assembly_input: SnapshotAssemblyInput) -> None:
    for result in assembly_input.provider_results:
        if result.search_plan_id != assembly_input.search_plan.search_plan_id:
            raise DomainInvariantViolation("Provider evidence search plan lineage mismatch")
        if result.requirement_id != assembly_input.search_plan.requirement_id:
            raise DomainInvariantViolation("Provider evidence requirement lineage mismatch")
        if result.based_on_requirement_version != assembly_input.search_plan.based_on_requirement_version:
            raise DomainInvariantViolation("Provider evidence requirement version mismatch")


def _aggregate_coverage(
    requested_scope: RequestedSearchScope,
    provider_results: tuple[ProviderSearchResult, ...],
) -> Coverage:
    provider_coverages = tuple(result.coverage for result in provider_results)
    limitations = tuple(
        CoverageLimitation(limitation.code, limitation.detail)
        for coverage in provider_coverages
        for limitation in coverage.limitations
    )
    status = _aggregate_coverage_status(provider_results)
    if status is CoverageStatus.PARTIAL and len(limitations) == 0:
        limitations = (CoverageLimitation("PROVIDER_COVERAGE_PARTIAL", "Provider coverage is partial"),)
    return Coverage(
        requested_scope=_scope_text(requested_scope),
        actual_coverage=" | ".join(_actual_scope_text(result) for result in provider_results),
        status=status,
        limitations=limitations,
    )


def _aggregate_coverage_status(provider_results: tuple[ProviderSearchResult, ...]) -> CoverageStatus:
    states = {result.coverage.completeness for result in provider_results}
    if CoverageCompleteness.UNKNOWN in states:
        return CoverageStatus.UNKNOWN
    if CoverageCompleteness.PARTIAL in states:
        return CoverageStatus.PARTIAL
    if any(result.data_status is ProviderDataStatus.PARTIAL for result in provider_results):
        return CoverageStatus.PARTIAL
    return CoverageStatus.COMPLETE


def _freshness_evidence(provider_results: tuple[ProviderSearchResult, ...]) -> SnapshotFreshnessEvidence:
    retrieved = tuple(
        DomainInstant(result.raw_evidence.retrieved_at)
        for result in provider_results
        if result.raw_evidence is not None
    )
    return SnapshotFreshnessEvidence(
        provider_retrieved_at=retrieved,
        structural_observed_at=max(retrieved, key=lambda item: item.value) if len(retrieved) > 0 else None,
        offer_observed_at=retrieved,
    )


def _assembly_issues(
    provider_results: tuple[ProviderSearchResult, ...],
    merged_graph: MergedCandidateGraph,
) -> tuple[SnapshotAssemblyIssue, ...]:
    issues: list[SnapshotAssemblyIssue] = []
    for result in provider_results:
        if result.execution_status is not ProviderExecutionStatus.SUCCESS:
            issues.append(
                SnapshotAssemblyIssue(
                    result.execution_status.value,
                    "Provider acquisition did not produce usable execution evidence",
                )
            )
        if result.data_status in {ProviderDataStatus.PARTIAL, ProviderDataStatus.UNUSABLE, ProviderDataStatus.UNKNOWN}:
            issues.append(
                SnapshotAssemblyIssue(
                    f"PROVIDER_DATA_{result.data_status.value}",
                    "Provider data status carries a limitation",
                )
            )
    issues.extend(
        SnapshotAssemblyIssue(issue.category.value, issue.detail)
        for issue in merged_graph.normalization_issues
    )
    issues.extend(
        SnapshotAssemblyIssue(evidence.category.value, evidence.detail)
        for evidence in merged_graph.evidence
    )
    return tuple(issues)


def _is_no_new_snapshot(
    provider_results: tuple[ProviderSearchResult, ...],
    merged_graph: MergedCandidateGraph,
) -> bool:
    if len(merged_graph.segments) > 0 or len(merged_graph.itineraries) > 0 or len(merged_graph.offers) > 0:
        return False
    if any(result.execution_status is not ProviderExecutionStatus.SUCCESS for result in provider_results):
        return True
    if any(result.data_status is not ProviderDataStatus.EMPTY for result in provider_results):
        return True
    if any(result.coverage.completeness is CoverageCompleteness.UNKNOWN for result in provider_results):
        return True
    if merged_graph.data_status is not ProviderDataStatus.EMPTY:
        return True
    return len(merged_graph.normalization_issues) > 0 or len(merged_graph.evidence) > 0


def _snapshot_status(
    provider_results: tuple[ProviderSearchResult, ...],
    merged_graph: MergedCandidateGraph,
    aggregate_coverage: Coverage,
) -> SnapshotCreationStatus:
    if len(merged_graph.segments) == 0 and len(merged_graph.itineraries) == 0 and len(merged_graph.offers) == 0:
        return SnapshotCreationStatus.LEGITIMATE_EMPTY_SNAPSHOT
    if (
        aggregate_coverage.status is CoverageStatus.COMPLETE
        and merged_graph.data_status is ProviderDataStatus.COMPLETE
        and all(result.data_status is ProviderDataStatus.COMPLETE for result in provider_results)
        and len(merged_graph.normalization_issues) == 0
    ):
        return SnapshotCreationStatus.COMPLETE_SNAPSHOT
    return SnapshotCreationStatus.PARTIAL_SNAPSHOT


def _structural_freshness_state(freshness_evidence: SnapshotFreshnessEvidence) -> FreshnessState:
    if freshness_evidence.structural_observed_at is None:
        return FreshnessState.STALE
    return FreshnessState.FRESH


def _snapshot_provenance(
    provider_results: tuple[ProviderSearchResult, ...],
    merged_graph: MergedCandidateGraph,
) -> tuple[ProvenanceRef, ...]:
    refs = [
        ProvenanceRef(
            source_type="provider_acquisition",
            source_ref=f"{result.provider_id.value}:{result.acquisition_id.value}",
            observed_at=DomainInstant(result.raw_evidence.retrieved_at)
            if result.raw_evidence is not None
            else None,
        )
        for result in provider_results
    ]
    refs.extend(ref for segment in merged_graph.segments for ref in segment.provenance)
    refs.extend(ref for itinerary in merged_graph.itineraries for ref in itinerary.provenance)
    refs.extend(ref for offer in merged_graph.offers for ref in offer.provenance)
    refs.extend(ref for evidence in merged_graph.evidence for ref in evidence.provenance)
    return tuple(
        sorted(
            set(refs),
            key=lambda ref: (
                ref.source_type,
                ref.source_ref,
                ref.detail_ref or "",
                ref.observed_at.value.isoformat() if ref.observed_at is not None else "",
            ),
        )
    )


def _scope_text(scope: RequestedSearchScope) -> str:
    return (
        f"{scope.origin.airport.value}-"
        f"{scope.destination.airport.value} "
        f"{scope.departure_date.departure_date.value.isoformat()}"
    )


def _actual_scope_text(result: ProviderSearchResult) -> str:
    actual_scope = result.coverage.actual_scope
    if actual_scope is None:
        return f"{result.provider_id.value}:UNKNOWN"
    return f"{result.provider_id.value}:{_scope_text(actual_scope)}"
