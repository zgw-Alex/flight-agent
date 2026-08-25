"""M5 minimal lower-price ranking."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.decision.filtering import FilterResult
from flight_agent.domain.flights import CandidateSnapshot, ItineraryId, OfferId
from flight_agent.domain.workflow import EvidenceRef, EvidenceSource


@dataclass(frozen=True)
class RankedCandidate:
    offer_id: OfferId
    itinerary_id: ItineraryId
    rank_position: int
    basis: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class RankingResult:
    snapshot_id: str
    ranked_candidates: tuple[RankedCandidate, ...]


class LowerPriceRanking:
    def rank(self, *, snapshot: CandidateSnapshot, filter_result: FilterResult) -> RankingResult:
        eligible = {
            offer_id
            for offer_id in filter_result.eligible_offer_ids
        }
        offers_by_rank = sorted(
            (offer for offer in snapshot.offers if offer.offer_id in eligible),
            key=lambda offer: (offer.total_price.amount, offer.offer_id.value),
        )
        return RankingResult(
            snapshot_id=snapshot.snapshot_id.value,
            ranked_candidates=tuple(
                RankedCandidate(
                    offer_id=offer.offer_id,
                    itinerary_id=offer.itinerary_id,
                    rank_position=index,
                    basis="lower price is better",
                    evidence=(EvidenceRef(EvidenceSource.OFFER, offer.offer_id),),
                )
                for index, offer in enumerate(offers_by_rank, start=1)
            ),
        )
