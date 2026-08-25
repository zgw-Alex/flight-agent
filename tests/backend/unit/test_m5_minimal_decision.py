from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from flight_agent.adapters.flight_providers.mock import MockFlightProvider, MockProviderMapper
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    AssemblerVersion,
    CandidateSnapshotAssembler,
    ExecuteMinimalDecision,
    ExecuteReadyRequirementSearch,
    FixtureSchemaVersion,
    MinimalDecisionResult,
    MinimalDecisionStatus,
    SearchExecutionResult,
    SearchExecutionStatus,
    SearchReadinessStatus,
    StartStructuredRequirement,
    StructuredRequirementCommand,
)
from flight_agent.application import (
    NormalizationContext as RequirementNormalizationContext,
)
from flight_agent.domain.decision import (
    FilterEvaluationStatus,
    LowerPriceRanking,
    MaxPriceFilter,
    RecommendationSelector,
)
from flight_agent.domain.flights import CandidateSnapshot, OfferId
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.domain.workflow import (
    ExecutionId,
    RecommendationResultStatus,
    RecommendationRole,
)
from flight_agent.ports import (
    CandidateMerger,
    CommonNormalizer,
    MergerVersion,
    NormalizerVersion,
    ReferenceData,
    ReferenceDataVersion,
)
from flight_agent.ports import (
    NormalizationContext as CandidateNormalizationContext,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "providers" / "mock_flight_provider_cases.json"
ASSEMBLER_VERSION = AssemblerVersion("candidate-snapshot-assembler-v1")


def test_max_price_filter_produces_pass_fail_unknown_and_preserves_snapshot() -> None:
    snapshot = golden_snapshot()
    before = snapshot

    result = MaxPriceFilter.cny(1200).evaluate_snapshot(snapshot)
    missing = MaxPriceFilter.cny(1200).evaluate_missing_price(OfferId("missing-offer"))

    assert statuses_by_price(snapshot, result) == {
        980: FilterEvaluationStatus.PASS,
        1080: FilterEvaluationStatus.PASS,
        1350: FilterEvaluationStatus.FAIL,
    }
    assert missing.status is FilterEvaluationStatus.UNKNOWN
    assert missing.status is not FilterEvaluationStatus.FAIL
    assert snapshot == before
    assert all(not hasattr(offer, "eligibility") for offer in snapshot.offers)


def test_lower_price_ranking_consumes_only_eligible_candidates_and_is_deterministic() -> None:
    snapshot = golden_snapshot()
    filter_result = MaxPriceFilter.cny(1200).evaluate_snapshot(snapshot)

    first = LowerPriceRanking().rank(snapshot=snapshot, filter_result=filter_result)
    second = LowerPriceRanking().rank(snapshot=snapshot, filter_result=filter_result)

    assert [candidate.offer_id for candidate in first.ranked_candidates] == [
        offer_id_for_price(snapshot, 980),
        offer_id_for_price(snapshot, 1080),
    ]
    assert [candidate.rank_position for candidate in first.ranked_candidates] == [1, 2]
    assert first == second
    assert all(not hasattr(candidate, "score") for candidate in first.ranked_candidates)
    assert all(not hasattr(offer, "rank") for offer in snapshot.offers)


def test_recommendation_selector_consumes_ranking_and_selects_best_overall() -> None:
    snapshot = golden_snapshot()
    filter_result = MaxPriceFilter.cny(1200).evaluate_snapshot(snapshot)
    ranking_result = LowerPriceRanking().rank(snapshot=snapshot, filter_result=filter_result)

    recommendation = RecommendationSelector().select_best_overall(
        ranking_result=ranking_result,
        snapshot=snapshot,
        recommendation_result_id=workflow_result_id("recommendation-1"),
        execution_id=ExecutionId("execution-1"),
        generated_at=instant(),
    )

    assert recommendation.status is RecommendationResultStatus.EXACT_MATCH
    assert recommendation.items[0].primary_offer_id == offer_id_for_price(snapshot, 980)
    assert recommendation.items[0].roles == (RecommendationRole.BEST_OVERALL,)
    assert len(RecommendationRole.BEST_OVERALL.value) > 0


def test_golden_m5_path_recommends_offer_a_with_lineage_and_no_snapshot_mutation() -> None:
    harness = Harness(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
            "search-plan-1",
            "snapshot-1",
            "recommendation-1",
        )
    )

    entry = harness.start(origin="PEK", destination="SHA", max_price_cny=1200)

    assert entry.readiness is SearchReadinessStatus.READY
    decision = harness.only_decision_result()
    assert decision.status is MinimalDecisionStatus.RECOMMENDED
    assert decision.filter_result is not None
    assert statuses_by_offer_id(decision.filter_result) == {
        offer_id_for_price(harness.snapshot(), 980): FilterEvaluationStatus.PASS,
        offer_id_for_price(harness.snapshot(), 1080): FilterEvaluationStatus.PASS,
        offer_id_for_price(harness.snapshot(), 1350): FilterEvaluationStatus.FAIL,
    }
    assert decision.ranking_result is not None
    assert [candidate.offer_id for candidate in decision.ranking_result.ranked_candidates] == [
        offer_id_for_price(harness.snapshot(), 980),
        offer_id_for_price(harness.snapshot(), 1080),
    ]
    assert decision.recommendation_result is not None
    assert decision.recommendation_result.based_on_requirement_version == RequirementVersion(1)
    assert decision.recommendation_result.snapshot_id == harness.snapshot().snapshot_id
    assert decision.recommendation_result.items[0].primary_offer_id == offer_id_for_price(
        harness.snapshot(), 980
    )
    assert harness.decision_call_counts() == {"filter": 1, "ranking": 1, "selector": 1}
    assert not hasattr(harness.snapshot(), "published")


def test_filter_empty_keeps_non_empty_snapshot_distinct_from_search_empty() -> None:
    harness = Harness(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
            "search-plan-1",
            "snapshot-1",
        )
    )

    harness.start(origin="PEK", destination="SHA", max_price_cny=900)

    decision = harness.only_decision_result()
    assert decision.status is MinimalDecisionStatus.FILTER_EMPTY
    assert decision.filter_result is not None
    assert decision.filter_result.eligible_offer_ids == ()
    assert decision.ranking_result is None
    assert decision.recommendation_result is None
    assert harness.snapshot().offers
    assert harness.search_results[0].status is SearchExecutionStatus.SNAPSHOT_READY
    assert harness.decision_call_counts() == {"filter": 1, "ranking": 0, "selector": 0}


def test_search_empty_provider_error_and_not_ready_do_not_run_decision_components() -> None:
    empty = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))
    empty.start(origin="PEK", destination="LAX", max_price_cny=1200)
    assert empty.only_decision_result().status is MinimalDecisionStatus.SEARCH_EMPTY
    assert empty.decision_call_counts() == {"filter": 0, "ranking": 0, "selector": 0}

    failure = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1", "search-plan-1", "snapshot-1"))
    failure.start(origin="SHA", destination="LAX", max_price_cny=1200)
    assert failure.only_decision_result().status is MinimalDecisionStatus.PROVIDER_ERROR
    assert failure.decision_call_counts() == {"filter": 0, "ranking": 0, "selector": 0}

    not_ready = Harness(("conversation-1", "execution-1", "requirement-1", "operation-1"))
    entry = not_ready.start(origin="PEK", destination="SHA", max_price_cny=1200, departure_date=None)
    assert entry.readiness is SearchReadinessStatus.NOT_READY
    assert not_ready.search_results == []
    assert not_ready.decision_results == []
    assert not_ready.decision_call_counts() == {"filter": 0, "ranking": 0, "selector": 0}


def test_decision_replay_is_deterministic_for_same_snapshot_and_price() -> None:
    first = Harness(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
            "search-plan-1",
            "snapshot-1",
            "recommendation-1",
        )
    )
    second = Harness(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
            "search-plan-1",
            "snapshot-1",
            "recommendation-1",
        )
    )

    first.start(origin="PEK", destination="SHA", max_price_cny=1200)
    second.start(origin="PEK", destination="SHA", max_price_cny=1200)

    assert first.only_decision_result() == second.only_decision_result()


class Harness:
    def __init__(self, ids: tuple[str, ...]) -> None:
        self.ids = iter(ids)
        self.search_results: list[SearchExecutionResult] = []
        self.decision_results: list[MinimalDecisionResult] = []
        self.filter_calls = 0
        self.ranking = CountingRanking()
        self.selector = CountingSelector()
        self.search_execution = ExecuteReadyRequirementSearch(
            flight_provider=MockFlightProvider(FIXTURE_PATH),
            provider_mapper=MockProviderMapper(),
            common_normalizer=CommonNormalizer(),
            normalization_context=candidate_normalization_context(),
            candidate_merger=CandidateMerger(MergerVersion("candidate-merger-v1")),
            snapshot_assembler=CandidateSnapshotAssembler(ASSEMBLER_VERSION),
            assembler_version=ASSEMBLER_VERSION,
            fixture_schema_versions=(FixtureSchemaVersion("m4-u2-v1"),),
            id_factory=lambda: next(self.ids),
            created_at=instant,
        )
        self.decision = ExecuteMinimalDecision(
            max_price_filter_factory=self.counted_filter,
            ranking=cast(LowerPriceRanking, self.ranking),
            selector=cast(RecommendationSelector, self.selector),
            id_factory=lambda: next(self.ids),
            generated_at=instant,
        )
        self.structured_entry = StartStructuredRequirement(
            repository=InMemoryRequirementRepository(),
            normalization_context=requirement_normalization_context(),
            recorded_at=instant,
            id_factory=lambda: next(self.ids),
            on_search_eligible=self.run_search_and_decision,
        )

    def counted_filter(self, max_price_cny: int) -> MaxPriceFilter:
        self.filter_calls += 1
        return MaxPriceFilter.cny(max_price_cny)

    def run_search_and_decision(self, eligible) -> None:
        search_result = self.search_execution.execute(
            requirement=eligible.requirement,
            validation=eligible.validation,
        )
        self.search_results.append(search_result)
        if eligible.command.max_price_cny is not None:
            self.decision_results.append(
                self.decision.execute(
                    search_result=search_result,
                    execution_id=ExecutionId(eligible.execution_id),
                    max_price_cny=eligible.command.max_price_cny,
                )
            )

    def start(
        self,
        *,
        origin: str,
        destination: str,
        max_price_cny: int,
        departure_date: date | None = date(2026, 9, 1),
    ):
        return self.structured_entry.start(
            StructuredRequirementCommand(
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                max_price_cny=max_price_cny,
                lower_price_preferred=True,
            )
        )

    def only_decision_result(self) -> MinimalDecisionResult:
        assert len(self.decision_results) == 1
        return self.decision_results[0]

    def snapshot(self) -> CandidateSnapshot:
        assert len(self.search_results) == 1
        outcome = self.search_results[0].snapshot_outcome
        assert outcome is not None
        assert outcome.snapshot is not None
        return outcome.snapshot

    def decision_call_counts(self) -> dict[str, int]:
        return {
            "filter": self.filter_calls,
            "ranking": self.ranking.calls,
            "selector": self.selector.calls,
        }


class CountingRanking(LowerPriceRanking):
    def __init__(self) -> None:
        self.calls = 0

    def rank(self, *, snapshot: CandidateSnapshot, filter_result):
        self.calls += 1
        return super().rank(snapshot=snapshot, filter_result=filter_result)


class CountingSelector(RecommendationSelector):
    def __init__(self) -> None:
        self.calls = 0

    def select_best_overall(self, **kwargs):
        self.calls += 1
        return super().select_best_overall(**kwargs)


def golden_snapshot() -> CandidateSnapshot:
    harness = Harness(
        (
            "conversation-1",
            "execution-1",
            "requirement-1",
            "operation-1",
            "search-plan-1",
            "snapshot-1",
            "recommendation-1",
        )
    )
    harness.start(origin="PEK", destination="SHA", max_price_cny=1200)
    return harness.snapshot()


def statuses_by_price(snapshot: CandidateSnapshot, result) -> dict[int, FilterEvaluationStatus]:
    offers_by_id = {offer.offer_id: offer for offer in snapshot.offers}
    return {
        int(offers_by_id[evaluation.offer_id].total_price.amount): evaluation.status
        for evaluation in result.evaluations
    }


def statuses_by_offer_id(result) -> dict[OfferId, FilterEvaluationStatus]:
    return {evaluation.offer_id: evaluation.status for evaluation in result.evaluations}


def offer_id_for_price(snapshot: CandidateSnapshot, price: int) -> OfferId:
    matches = [offer.offer_id for offer in snapshot.offers if int(offer.total_price.amount) == price]
    assert len(matches) == 1
    return matches[0]


def requirement_normalization_context() -> RequirementNormalizationContext:
    return RequirementNormalizationContext(
        reference_instant=instant(),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="test-v1",
    )


def candidate_normalization_context() -> CandidateNormalizationContext:
    return CandidateNormalizationContext(
        normalizer_version=NormalizerVersion("common-normalizer-v1"),
        reference_data=ReferenceData(
            version=ReferenceDataVersion("m5-u3-reference-data-v1"),
            airports=frozenset({"PVG", "PEK", "SHA", "CAN", "SZX", "CTU", "HGH", "NKG", "XMN", "LAX"}),
            carriers=frozenset({"MU", "DL"}),
        ),
    )


def workflow_result_id(value: str):
    from flight_agent.domain.workflow import RecommendationResultId

    return RecommendationResultId(value)


def instant() -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 25, 8, 0, tzinfo=UTC))
