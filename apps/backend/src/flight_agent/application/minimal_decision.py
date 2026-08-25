"""M5-U3 minimal decision composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from flight_agent.application.search_execution import SearchExecutionResult, SearchExecutionStatus
from flight_agent.domain.decision import (
    FilterResult,
    LowerPriceRanking,
    MaxPriceFilter,
    RankingResult,
    RecommendationSelector,
)
from flight_agent.domain.shared import DomainInstant
from flight_agent.domain.workflow import ExecutionId, RecommendationResult, RecommendationResultId


class MinimalDecisionStatus(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    SEARCH_EMPTY = "SEARCH_EMPTY"
    FILTER_EMPTY = "FILTER_EMPTY"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class MinimalDecisionResult:
    status: MinimalDecisionStatus
    filter_result: FilterResult | None
    ranking_result: RankingResult | None
    recommendation_result: RecommendationResult | None


IdFactory = Callable[[], str]
MaxPriceFilterFactory = Callable[[int], MaxPriceFilter]


class ExecuteMinimalDecision:
    def __init__(
        self,
        *,
        max_price_filter_factory: MaxPriceFilterFactory,
        ranking: LowerPriceRanking,
        selector: RecommendationSelector,
        id_factory: IdFactory,
        generated_at: Callable[[], DomainInstant],
    ) -> None:
        self._max_price_filter_factory = max_price_filter_factory
        self._ranking = ranking
        self._selector = selector
        self._id_factory = id_factory
        self._generated_at = generated_at

    def execute(
        self,
        *,
        search_result: SearchExecutionResult,
        execution_id: ExecutionId,
        max_price_cny: int,
    ) -> MinimalDecisionResult:
        if search_result.status is SearchExecutionStatus.NOT_READY:
            return _no_decision(MinimalDecisionStatus.NOT_READY)
        if search_result.status is SearchExecutionStatus.PROVIDER_ERROR:
            return _no_decision(MinimalDecisionStatus.PROVIDER_ERROR)
        if search_result.status is SearchExecutionStatus.SEARCH_EMPTY:
            return _no_decision(MinimalDecisionStatus.SEARCH_EMPTY)
        if search_result.snapshot_outcome is None or search_result.snapshot_outcome.snapshot is None:
            return _no_decision(MinimalDecisionStatus.PROVIDER_ERROR)

        snapshot = search_result.snapshot_outcome.snapshot
        filter_result = self._max_price_filter_factory(max_price_cny).evaluate_snapshot(snapshot)
        if not filter_result.has_eligible_candidates:
            return MinimalDecisionResult(
                status=MinimalDecisionStatus.FILTER_EMPTY,
                filter_result=filter_result,
                ranking_result=None,
                recommendation_result=None,
            )

        ranking_result = self._ranking.rank(snapshot=snapshot, filter_result=filter_result)
        recommendation_result = self._selector.select_best_overall(
            ranking_result=ranking_result,
            snapshot=snapshot,
            recommendation_result_id=RecommendationResultId(self._id_factory()),
            execution_id=execution_id,
            generated_at=self._generated_at(),
        )
        return MinimalDecisionResult(
            status=MinimalDecisionStatus.RECOMMENDED,
            filter_result=filter_result,
            ranking_result=ranking_result,
            recommendation_result=recommendation_result,
        )


def _no_decision(status: MinimalDecisionStatus) -> MinimalDecisionResult:
    return MinimalDecisionResult(
        status=status,
        filter_result=None,
        ranking_result=None,
        recommendation_result=None,
    )
