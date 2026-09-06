"""Single-provider candidate integration wiring for mapped canonical outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from flight_agent.application.snapshot_assembly import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    FixtureSchemaVersion,
    SnapshotAssemblyInput,
    SnapshotAssemblyOutcome,
    build_processing_manifest,
)
from flight_agent.domain.flights import CandidateSnapshotId
from flight_agent.domain.search import SearchPlan
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    CandidateMerger,
    NormalizationResult,
    ProviderMapper,
    ProviderMappingResult,
    ProviderSearchResult,
)


class ProviderCanonicalEntryResult(Protocol):
    @property
    def normalization_result(self) -> NormalizationResult:
        """Provider-neutral normalized output produced by the canonical entry."""
        ...


class ProviderCanonicalEntry(Protocol):
    def canonicalize(self, mapping_result: ProviderMappingResult) -> ProviderCanonicalEntryResult:
        """Pass mapped provider output into the existing canonical normalization boundary."""
        ...


@dataclass(frozen=True)
class SingleProviderCandidateIntegrationResult:
    provider_result: ProviderSearchResult
    mapping_result: ProviderMappingResult
    normalization_result: NormalizationResult
    snapshot_outcome: SnapshotAssemblyOutcome


IdFactory = Callable[[], str]


class ExecuteSingleProviderCandidateIntegration:
    """Wire one provider result into existing merger and snapshot assembly components."""

    def __init__(
        self,
        *,
        provider_mapper: ProviderMapper,
        canonical_entry: ProviderCanonicalEntry,
        candidate_merger: CandidateMerger,
        snapshot_assembler: CandidateSnapshotAssembler,
        assembler_version: AssemblerVersion,
        fixture_schema_versions: tuple[FixtureSchemaVersion, ...],
        id_factory: IdFactory,
        created_at: Callable[[], DomainInstant],
    ) -> None:
        self._provider_mapper = provider_mapper
        self._canonical_entry = canonical_entry
        self._candidate_merger = candidate_merger
        self._snapshot_assembler = snapshot_assembler
        self._assembler_version = assembler_version
        self._fixture_schema_versions = fixture_schema_versions
        self._id_factory = id_factory
        self._created_at = created_at

    def execute(
        self,
        *,
        search_plan: SearchPlan,
        provider_result: ProviderSearchResult,
    ) -> SingleProviderCandidateIntegrationResult:
        mapping_result = self._provider_mapper.map(provider_result)
        canonical_result = self._canonical_entry.canonicalize(mapping_result)
        merged_graph = self._candidate_merger.merge((canonical_result.normalization_result,))
        snapshot_outcome = self._snapshot_assembler.assemble(
            SnapshotAssemblyInput(
                search_plan=search_plan,
                merged_graph=merged_graph,
                provider_results=(provider_result,),
                snapshot_id=CandidateSnapshotId(self._id_factory()),
                created_at=self._created_at(),
                processing_manifest=build_processing_manifest(
                    fixture_schema_versions=self._fixture_schema_versions,
                    merged_graph=merged_graph,
                    assembler_version=self._assembler_version,
                ),
            )
        )
        return SingleProviderCandidateIntegrationResult(
            provider_result=provider_result,
            mapping_result=mapping_result,
            normalization_result=canonical_result.normalization_result,
            snapshot_outcome=snapshot_outcome,
        )
