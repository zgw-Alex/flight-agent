"""Immutable self-contained candidate snapshot graph."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from flight_agent.domain.flights.entities import FlightSegment, Itinerary, Offer
from flight_agent.domain.flights.identity import (
    CandidateSnapshotId,
    ItineraryId,
    SegmentId,
)
from flight_agent.domain.shared import (
    DomainInstant,
    DomainInvariantViolation,
    ProvenanceRef,
    SnapshotVersion,
    StructuralFreshness,
)
from flight_agent.domain.shared.identity import RequirementVersion


class CoverageStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CoverageLimitation:
    code: str
    detail: str

    def __post_init__(self) -> None:
        if self.code.strip() == "" or self.detail.strip() == "":
            raise DomainInvariantViolation("CoverageLimitation requires code and detail")


@dataclass(frozen=True, init=False)
class Coverage:
    requested_scope: str
    actual_coverage: str
    status: CoverageStatus
    limitations: tuple[CoverageLimitation, ...]

    def __init__(
        self,
        requested_scope: str,
        actual_coverage: str,
        status: CoverageStatus,
        limitations: tuple[CoverageLimitation, ...] = (),
    ) -> None:
        if requested_scope.strip() == "" or actual_coverage.strip() == "":
            raise DomainInvariantViolation("Coverage requires requested and actual scope")
        limitations_tuple = tuple(limitations)
        if status is CoverageStatus.PARTIAL and len(limitations_tuple) == 0:
            raise DomainInvariantViolation("PARTIAL coverage requires at least one limitation")
        object.__setattr__(self, "requested_scope", requested_scope)
        object.__setattr__(self, "actual_coverage", actual_coverage)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "limitations", limitations_tuple)


@dataclass(frozen=True, init=False)
class CandidateSnapshot:
    snapshot_id: CandidateSnapshotId
    version: SnapshotVersion
    created_at: DomainInstant
    created_from_requirement_version: RequirementVersion
    structural_freshness: StructuralFreshness
    coverage: Coverage
    segments: tuple[FlightSegment, ...]
    itineraries: tuple[Itinerary, ...]
    offers: tuple[Offer, ...]
    parent_snapshot_id: CandidateSnapshotId | None
    parent_snapshot_version: SnapshotVersion | None
    provenance: tuple[ProvenanceRef, ...]

    def __init__(
        self,
        snapshot_id: CandidateSnapshotId,
        version: SnapshotVersion,
        created_at: DomainInstant,
        created_from_requirement_version: RequirementVersion,
        structural_freshness: StructuralFreshness,
        coverage: Coverage,
        segments: tuple[FlightSegment, ...] = (),
        itineraries: tuple[Itinerary, ...] = (),
        offers: tuple[Offer, ...] = (),
        parent_snapshot_id: CandidateSnapshotId | None = None,
        parent_snapshot_version: SnapshotVersion | None = None,
        provenance: tuple[ProvenanceRef, ...] = (),
    ) -> None:
        segments_tuple = tuple(segments)
        itineraries_tuple = tuple(itineraries)
        offers_tuple = tuple(offers)
        provenance_tuple = tuple(provenance)
        _validate_parent_lineage(version, parent_snapshot_id, parent_snapshot_version)
        _validate_graph(segments_tuple, itineraries_tuple, offers_tuple)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(
            self, "created_from_requirement_version", created_from_requirement_version
        )
        object.__setattr__(self, "structural_freshness", structural_freshness)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "segments", segments_tuple)
        object.__setattr__(self, "itineraries", itineraries_tuple)
        object.__setattr__(self, "offers", offers_tuple)
        object.__setattr__(self, "parent_snapshot_id", parent_snapshot_id)
        object.__setattr__(self, "parent_snapshot_version", parent_snapshot_version)
        object.__setattr__(self, "provenance", provenance_tuple)


def _validate_parent_lineage(
    version: SnapshotVersion,
    parent_snapshot_id: CandidateSnapshotId | None,
    parent_snapshot_version: SnapshotVersion | None,
) -> None:
    if (parent_snapshot_id is None) != (parent_snapshot_version is None):
        raise DomainInvariantViolation("Snapshot parent lineage requires both parent id and version")
    if version.value == 1 and parent_snapshot_id is not None:
        raise DomainInvariantViolation("Initial CandidateSnapshot must not have parent lineage")
    if version.value > 1 and parent_snapshot_version is None:
        raise DomainInvariantViolation("Non-initial CandidateSnapshot requires parent lineage")
    if parent_snapshot_version is not None and parent_snapshot_version.value != version.value - 1:
        raise DomainInvariantViolation("Snapshot parent version must be the direct predecessor")


def _validate_graph(
    segments: tuple[FlightSegment, ...],
    itineraries: tuple[Itinerary, ...],
    offers: tuple[Offer, ...],
) -> None:
    segment_ids = _unique_ids((segment.segment_id for segment in segments), "SegmentId")
    itinerary_ids = _unique_ids(
        (itinerary.itinerary_id for itinerary in itineraries), "ItineraryId"
    )
    _unique_ids((offer.offer_id for offer in offers), "OfferId")

    for itinerary in itineraries:
        for segment_id in itinerary.segment_ids:
            if not isinstance(segment_id, SegmentId):
                raise DomainInvariantViolation("Itinerary must reference SegmentId values")
            if segment_id not in segment_ids:
                raise DomainInvariantViolation("Itinerary references a missing Segment")

    for offer in offers:
        if not isinstance(offer.itinerary_id, ItineraryId):
            raise DomainInvariantViolation("Offer must reference an ItineraryId")
        if offer.itinerary_id not in itinerary_ids:
            raise DomainInvariantViolation("Offer references a missing Itinerary")


def _unique_ids(items: Iterable[object], label: str) -> frozenset[object]:
    ids = tuple(items)
    if len(frozenset(ids)) != len(ids):
        raise DomainInvariantViolation(f"CandidateSnapshot requires unique {label} values")
    return frozenset(ids)
