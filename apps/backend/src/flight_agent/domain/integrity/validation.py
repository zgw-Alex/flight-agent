"""Explicit cross-contract consistency validators.

These validators compare already-constructed domain facts. They do not choose,
rank, normalize, reuse, publish, or repair domain artifacts.
"""

from __future__ import annotations

from flight_agent.domain.flights import (
    CandidateSnapshot,
    CandidateSnapshotId,
    ItineraryId,
    OfferId,
)
from flight_agent.domain.requirements import (
    ConstraintId,
    PreferenceId,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import DomainInvariantViolation
from flight_agent.domain.workflow import (
    AgentExecution,
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    ExplanationResult,
    PublishedRecommendation,
    RecommendationResult,
)


def validate_execution_requirement_lineage(
    execution: AgentExecution,
    requirement: RequirementState,
) -> None:
    if execution.based_on_requirement_version != requirement.version:
        raise DomainInvariantViolation("Execution requirement version does not match RequirementState")


def validate_recommendation_against_snapshot(
    recommendation: RecommendationResult,
    execution: AgentExecution,
    snapshot: CandidateSnapshot,
) -> None:
    if not isinstance(recommendation.execution_id, ExecutionId):
        raise DomainInvariantViolation("RecommendationResult execution_id must be an ExecutionId")
    if recommendation.execution_id != execution.execution_id:
        raise DomainInvariantViolation("RecommendationResult execution_id does not match execution")
    if recommendation.based_on_requirement_version != execution.based_on_requirement_version:
        raise DomainInvariantViolation("RecommendationResult requirement lineage mismatch")
    if not isinstance(recommendation.snapshot_id, CandidateSnapshotId):
        raise DomainInvariantViolation("RecommendationResult snapshot_id must be a CandidateSnapshotId")
    if (
        recommendation.snapshot_id != snapshot.snapshot_id
        or recommendation.snapshot_version != snapshot.version
    ):
        raise DomainInvariantViolation("RecommendationResult snapshot reference mismatch")

    itineraries = {itinerary.itinerary_id: itinerary for itinerary in snapshot.itineraries}
    offers = {offer.offer_id: offer for offer in snapshot.offers}
    for item in recommendation.items:
        if not isinstance(item.itinerary_id, ItineraryId):
            raise DomainInvariantViolation("RecommendationItem itinerary_id must be an ItineraryId")
        if not isinstance(item.primary_offer_id, OfferId):
            raise DomainInvariantViolation("RecommendationItem primary_offer_id must be an OfferId")
        if item.itinerary_id not in itineraries:
            raise DomainInvariantViolation("RecommendationItem references missing Itinerary")
        offer = offers.get(item.primary_offer_id)
        if offer is None:
            raise DomainInvariantViolation("RecommendationItem references missing Offer")
        if offer.itinerary_id != item.itinerary_id:
            raise DomainInvariantViolation("RecommendationItem Offer belongs to another Itinerary")


def validate_explanation_against_recommendation(
    explanation: ExplanationResult,
    recommendation: RecommendationResult,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
) -> None:
    if explanation.recommendation_result_id != recommendation.recommendation_result_id:
        raise DomainInvariantViolation("ExplanationResult recommendation lineage mismatch")
    if explanation.execution_id != recommendation.execution_id:
        raise DomainInvariantViolation("ExplanationResult execution lineage mismatch")
    if explanation.based_on_requirement_version != recommendation.based_on_requirement_version:
        raise DomainInvariantViolation("ExplanationResult requirement lineage mismatch")
    if (
        explanation.snapshot_id != recommendation.snapshot_id
        or explanation.snapshot_version != recommendation.snapshot_version
    ):
        raise DomainInvariantViolation("ExplanationResult snapshot lineage mismatch")
    for statement in explanation.statements:
        if len(statement.evidence) == 0:
            raise DomainInvariantViolation("ExplanationStatement requires evidence")
        for evidence in statement.evidence:
            validate_evidence_ref(evidence, requirement, snapshot, recommendation)


def validate_publication_lineage(
    publication: PublishedRecommendation,
    recommendation: RecommendationResult,
    explanation: ExplanationResult | None = None,
) -> None:
    if publication.recommendation_result_id != recommendation.recommendation_result_id:
        raise DomainInvariantViolation("Publication recommendation lineage mismatch")
    if publication.execution_id != recommendation.execution_id:
        raise DomainInvariantViolation("Publication execution lineage mismatch")
    if publication.based_on_requirement_version != recommendation.based_on_requirement_version:
        raise DomainInvariantViolation("Publication requirement lineage mismatch")
    if (
        publication.snapshot_id != recommendation.snapshot_id
        or publication.snapshot_version != recommendation.snapshot_version
    ):
        raise DomainInvariantViolation("Publication snapshot lineage mismatch")

    if explanation is None:
        if publication.explanation_result_id is not None:
            raise DomainInvariantViolation("Publication references an ExplanationResult not provided")
        return

    if publication.explanation_result_id != explanation.explanation_result_id:
        raise DomainInvariantViolation("Publication explanation reference mismatch")
    if explanation.recommendation_result_id != recommendation.recommendation_result_id:
        raise DomainInvariantViolation("Publication explanation recommendation mismatch")
    if explanation.execution_id != recommendation.execution_id:
        raise DomainInvariantViolation("Publication explanation execution mismatch")
    if explanation.based_on_requirement_version != recommendation.based_on_requirement_version:
        raise DomainInvariantViolation("Publication explanation requirement mismatch")
    if (
        explanation.snapshot_id != recommendation.snapshot_id
        or explanation.snapshot_version != recommendation.snapshot_version
    ):
        raise DomainInvariantViolation("Publication explanation snapshot mismatch")


def validate_evidence_ref(
    evidence: EvidenceRef,
    requirement: RequirementState,
    snapshot: CandidateSnapshot,
    recommendation: RecommendationResult,
) -> None:
    if evidence.source is EvidenceSource.REQUIREMENT:
        if not isinstance(evidence.identity, RequirementId) or evidence.identity != requirement.requirement_id:
            raise DomainInvariantViolation("EvidenceRef requirement anchor mismatch")
    elif evidence.source is EvidenceSource.CONSTRAINT:
        constraint_ids = {constraint.constraint_id for constraint in requirement.constraints}
        if not isinstance(evidence.identity, ConstraintId) or evidence.identity not in constraint_ids:
            raise DomainInvariantViolation("EvidenceRef constraint anchor missing")
    elif evidence.source is EvidenceSource.PREFERENCE:
        preference_ids = {preference.preference_id for preference in requirement.preferences}
        if not isinstance(evidence.identity, PreferenceId) or evidence.identity not in preference_ids:
            raise DomainInvariantViolation("EvidenceRef preference anchor missing")
    elif evidence.source is EvidenceSource.ITINERARY:
        itinerary_ids = {itinerary.itinerary_id for itinerary in snapshot.itineraries}
        if not isinstance(evidence.identity, ItineraryId) or evidence.identity not in itinerary_ids:
            raise DomainInvariantViolation("EvidenceRef itinerary anchor missing")
    elif evidence.source is EvidenceSource.OFFER:
        offer_ids = {offer.offer_id for offer in snapshot.offers}
        if not isinstance(evidence.identity, OfferId) or evidence.identity not in offer_ids:
            raise DomainInvariantViolation("EvidenceRef offer anchor missing")
    elif (
        evidence.source is EvidenceSource.RECOMMENDATION
        and evidence.identity != recommendation.recommendation_result_id
    ):
        raise DomainInvariantViolation("EvidenceRef recommendation anchor mismatch")
