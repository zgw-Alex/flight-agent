"""M5-U4 publication and public read model composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Protocol

from flight_agent.application.minimal_decision import MinimalDecisionResult, MinimalDecisionStatus
from flight_agent.domain.flights import (
    CandidateSnapshot,
    FlightSegment,
    Itinerary,
    Offer,
    PriceSemantics,
)
from flight_agent.domain.requirements import RequirementId
from flight_agent.domain.shared import DomainInstant, DomainInvariantViolation
from flight_agent.domain.workflow import (
    EvidenceRef,
    PublicationId,
    PublishedRecommendation,
    RecommendationRole,
)


class PublicWorkflowOutcome(str, Enum):
    PUBLISHED = "PUBLISHED"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    FILTER_EMPTY = "FILTER_EMPTY"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class PublishedRecommendationRecord:
    conversation_id: str
    requirement_id: RequirementId
    published_recommendation: PublishedRecommendation
    selected_itinerary_id: str
    selected_offer_id: str
    role: RecommendationRole
    route_origin: str
    route_destination: str
    departure_date: date
    selected_price_amount: Decimal
    selected_price_currency: str
    selected_price_semantics: PriceSemantics
    reason: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class ConversationReadState:
    conversation_id: str
    outcome: PublicWorkflowOutcome
    requirement_id: RequirementId | None
    requirement_version: int | None
    execution_id: str | None
    current_published_recommendation: PublishedRecommendationRecord | None


class PublicationRepository(Protocol):
    def save_current(self, record: PublishedRecommendationRecord) -> None: ...

    def record_outcome(
        self,
        *,
        conversation_id: str,
        outcome: PublicWorkflowOutcome,
        requirement_id: RequirementId | None,
        requirement_version: int | None,
        execution_id: str | None,
    ) -> None: ...

    def get_conversation(self, conversation_id: str) -> ConversationReadState | None: ...


class PublishRecommendation:
    def __init__(
        self,
        *,
        id_factory: Callable[[], str],
        published_at: Callable[[], DomainInstant],
    ) -> None:
        self._id_factory = id_factory
        self._published_at = published_at

    def publish(
        self,
        *,
        conversation_id: str,
        requirement_id: RequirementId,
        decision_result: MinimalDecisionResult,
        snapshot: CandidateSnapshot,
    ) -> PublishedRecommendationRecord | None:
        if decision_result.status is not MinimalDecisionStatus.RECOMMENDED:
            return None
        recommendation = decision_result.recommendation_result
        if recommendation is None:
            raise DomainInvariantViolation("PUBLISHED outcome requires a RecommendationResult")
        item = recommendation.items[0]
        if RecommendationRole.BEST_OVERALL not in item.roles:
            raise DomainInvariantViolation("Public publication requires a BEST_OVERALL item")

        itinerary = _itinerary(snapshot, item.itinerary_id.value)
        offer = _offer(snapshot, item.primary_offer_id.value)
        if offer.itinerary_id != itinerary.itinerary_id:
            raise DomainInvariantViolation("Published offer must belong to the selected itinerary")
        first_segment = _segment(snapshot, itinerary.segment_ids[0].value)
        last_segment = _segment(snapshot, itinerary.segment_ids[-1].value)

        return PublishedRecommendationRecord(
            conversation_id=conversation_id,
            requirement_id=requirement_id,
            published_recommendation=PublishedRecommendation.from_recommendation(
                PublicationId(self._id_factory()),
                recommendation,
                self._published_at(),
            ),
            selected_itinerary_id=itinerary.itinerary_id.value,
            selected_offer_id=offer.offer_id.value,
            role=RecommendationRole.BEST_OVERALL,
            route_origin=first_segment.departure_airport,
            route_destination=last_segment.arrival_airport,
            departure_date=first_segment.departure_at.value.date(),
            selected_price_amount=offer.total_price.amount,
            selected_price_currency=offer.total_price.currency,
            selected_price_semantics=offer.price_semantics,
            reason=_reason(item.evidence),
            evidence=item.evidence,
        )


def outcome_from_decision(status: MinimalDecisionStatus) -> PublicWorkflowOutcome:
    if status is MinimalDecisionStatus.SEARCH_EMPTY:
        return PublicWorkflowOutcome.SEARCH_EMPTY
    if status is MinimalDecisionStatus.FILTER_EMPTY:
        return PublicWorkflowOutcome.FILTER_EMPTY
    if status is MinimalDecisionStatus.PROVIDER_ERROR:
        return PublicWorkflowOutcome.PROVIDER_ERROR
    if status is MinimalDecisionStatus.NOT_READY:
        return PublicWorkflowOutcome.NOT_READY
    if status is MinimalDecisionStatus.RECOMMENDED:
        return PublicWorkflowOutcome.PUBLISHED
    raise DomainInvariantViolation("Unsupported minimal decision outcome")


def _itinerary(snapshot: CandidateSnapshot, itinerary_id: str) -> Itinerary:
    for itinerary in snapshot.itineraries:
        if itinerary.itinerary_id.value == itinerary_id:
            return itinerary
    raise DomainInvariantViolation("Published recommendation references a missing itinerary")


def _offer(snapshot: CandidateSnapshot, offer_id: str) -> Offer:
    for offer in snapshot.offers:
        if offer.offer_id.value == offer_id:
            return offer
    raise DomainInvariantViolation("Published recommendation references a missing offer")


def _segment(snapshot: CandidateSnapshot, segment_id: str) -> FlightSegment:
    for segment in snapshot.segments:
        if segment.segment_id.value == segment_id:
            return segment
    raise DomainInvariantViolation("Published recommendation references a missing segment")


def _reason(evidence: tuple[EvidenceRef, ...]) -> str:
    for ref in evidence:
        if ref.note is not None:
            return ref.note
    return "Selected as BEST_OVERALL from canonical recommendation evidence"
