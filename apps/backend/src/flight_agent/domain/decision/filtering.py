"""M5 minimal MAX_PRICE filter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from flight_agent.domain.decision.evaluation import ConstraintEvaluationStatus
from flight_agent.domain.flights import CandidateSnapshot, ItineraryId, Money, Offer, OfferId
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource

FilterEvaluationStatus = ConstraintEvaluationStatus


@dataclass(frozen=True)
class FilterEvaluation:
    offer_id: OfferId
    itinerary_id: ItineraryId | None
    status: FilterEvaluationStatus
    reason: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class FilterResult:
    snapshot_id: str
    max_price: Money
    evaluations: tuple[FilterEvaluation, ...]

    @property
    def eligible_offer_ids(self) -> tuple[OfferId, ...]:
        return tuple(
            evaluation.offer_id
            for evaluation in self.evaluations
            if evaluation.status is FilterEvaluationStatus.PASS
        )

    @property
    def has_eligible_candidates(self) -> bool:
        return len(self.eligible_offer_ids) > 0


@dataclass(frozen=True)
class MaxPriceFilter:
    max_price: Money

    @classmethod
    def cny(cls, amount: int) -> MaxPriceFilter:
        return cls(Money(Decimal(amount), "CNY"))

    def evaluate_snapshot(self, snapshot: CandidateSnapshot) -> FilterResult:
        return FilterResult(
            snapshot_id=snapshot.snapshot_id.value,
            max_price=self.max_price,
            evaluations=tuple(self.evaluate_offer(offer) for offer in snapshot.offers),
        )

    def evaluate_offer(self, offer: Offer) -> FilterEvaluation:
        if offer.total_price.currency != self.max_price.currency:
            return FilterEvaluation(
                offer_id=offer.offer_id,
                itinerary_id=offer.itinerary_id,
                status=FilterEvaluationStatus.UNKNOWN,
                reason="MAX_PRICE currency is not comparable",
                evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
            )
        if offer.total_price.amount <= self.max_price.amount:
            return FilterEvaluation(
                offer_id=offer.offer_id,
                itinerary_id=offer.itinerary_id,
                status=FilterEvaluationStatus.PASS,
                reason="MAX_PRICE passed",
                evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
            )
        return FilterEvaluation(
            offer_id=offer.offer_id,
            itinerary_id=offer.itinerary_id,
            status=FilterEvaluationStatus.FAIL,
            reason="MAX_PRICE failed",
            evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
        )

    def evaluate_missing_price(self, offer_id: OfferId) -> FilterEvaluation:
        return FilterEvaluation(
            offer_id=offer_id,
            itinerary_id=None,
            status=FilterEvaluationStatus.UNKNOWN,
            reason="MAX_PRICE cannot evaluate a missing canonical price",
            evidence=(EvidenceRef(EvidenceSource.OFFER, offer_id),),
        )
