"""M5 minimal BEST_OVERALL recommendation selection."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.domain.decision.ranking import RankingResult
from flight_agent.domain.flights import CandidateSnapshot
from flight_agent.domain.shared import DomainInstant
from flight_agent.domain.workflow import (
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


@dataclass(frozen=True)
class RecommendationSelector:
    def select_best_overall(
        self,
        *,
        ranking_result: RankingResult,
        snapshot: CandidateSnapshot,
        recommendation_result_id: RecommendationResultId,
        execution_id: ExecutionId,
        generated_at: DomainInstant,
    ) -> RecommendationResult:
        if len(ranking_result.ranked_candidates) == 0:
            return RecommendationResult(
                recommendation_result_id=recommendation_result_id,
                status=RecommendationResultStatus.NO_MATCH,
                execution_id=execution_id,
                based_on_requirement_version=snapshot.created_from_requirement_version,
                snapshot_id=snapshot.snapshot_id,
                snapshot_version=snapshot.version,
                generated_at=generated_at,
            )
        selected = ranking_result.ranked_candidates[0]
        return RecommendationResult(
            recommendation_result_id=recommendation_result_id,
            status=RecommendationResultStatus.EXACT_MATCH,
            execution_id=execution_id,
            based_on_requirement_version=snapshot.created_from_requirement_version,
            snapshot_id=snapshot.snapshot_id,
            snapshot_version=snapshot.version,
            generated_at=generated_at,
            items=(
                RecommendationItem(
                    itinerary_id=selected.itinerary_id,
                    primary_offer_id=selected.offer_id,
                    roles=(RecommendationRole.BEST_OVERALL,),
                    evidence=(
                        *selected.evidence,
                        EvidenceRef(
                            EvidenceSource.OFFER,
                            selected.offer_id,
                            note="Selected from rank 1 lower-price result",
                        ),
                    ),
                ),
            ),
        )
