from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from flight_agent.adapters.deepseek_llm import DeepSeekHTTPTransport, DeepSeekRuntimeConfig
from flight_agent.adapters.llm_deepseek_explanation import deepseek_explanation_llm_from_config
from flight_agent.application import (
    ExplanationGenerationSource,
    build_explanation_evidence_bundle,
    execute_llm_explanation,
)
from flight_agent.config import Settings
from flight_agent.domain.flights import CandidateSnapshotId, ItineraryId, OfferId
from flight_agent.domain.requirements import PreferenceId, RequirementId
from flight_agent.domain.shared import DomainInstant, RequirementVersion, SnapshotVersion
from flight_agent.domain.workflow import (
    CandidateComparison,
    EvidenceRef,
    EvidenceSource,
    ExecutionId,
    ExplanationResultId,
    RecommendationItem,
    RecommendationResult,
    RecommendationResultId,
    RecommendationResultStatus,
    RecommendationRole,
)


def main() -> int:
    settings = Settings()
    print(f"DeepSeek credential configured: {'YES' if settings.deepseek_configured else 'NO'}")
    if not settings.deepseek_configured or settings.deepseek_api_key is None:
        return 2

    transport = DeepSeekHTTPTransport(
        DeepSeekRuntimeConfig(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    )
    models = transport.list_models(timeout_seconds=settings.deepseek_timeout_seconds)
    model_id = settings.deepseek_default_model
    if models.model_ids and model_id not in models.model_ids:
        model_id = models.model_ids[0]
    print(f"provider: {models.provider.value}")
    print(f"accessible model IDs: {', '.join(models.model_ids)}")
    print(f"candidate model used: {model_id}")
    print("thinking/invocation mode: thinking=disabled, response_format=json_object")
    print("PromptTemplateVersion: explanation-generation-v1")
    print("OutputSchemaVersion: m8-u1")
    print("AdapterVersion: deepseek-http-u3 + deepseek-explanation-u5")
    print("candidate status: CANDIDATE")

    clear = _run_case(settings, model_id, _recommendation_clear(), "clear-winner")
    unknown = _run_case(settings, model_id, _recommendation_unknown(), "unknown")
    tradeoff = _run_case(settings, model_id, _recommendation_tradeoff(), "trade-off")

    print(f"clear-winner smoke: {'PASS' if clear['safe'] else 'FAIL'}")
    print(f"UNKNOWN smoke: {'PASS' if unknown['safe'] else 'FAIL'}")
    print(f"trade-off smoke: {'PASS' if tradeoff['safe'] else 'FAIL'}")
    print(f"evidence validation: {'PASS' if all(c['evidence'] for c in (clear, unknown, tradeoff)) else 'FAIL'}")
    print(
        "recommendation consistency: "
        f"{'PASS' if all(c['recommendation'] for c in (clear, unknown, tradeoff)) else 'FAIL'}"
    )
    fallback_used = any(c["fallback"] for c in (clear, unknown, tradeoff))
    blocked = tuple(c["failure"] for c in (clear, unknown, tradeoff) if c["failure"] != "NONE")
    print(f"fallback exercised by real behavior: {'YES' if fallback_used else 'NO'}")
    print(
        "real P0 behavior failures safely blocked: "
        + ("NONE" if not blocked else "; ".join(blocked))
    )
    print("failures recorded for U6: " + ("NONE" if not blocked else "; ".join(blocked)))
    print("smoke is full behavioral eval: NO")
    print("Accepted Baseline promoted: NO")
    return 0 if all(c["safe"] for c in (clear, unknown, tradeoff)) else 1


def _run_case(settings: Settings, model_id: str, recommendation: RecommendationResult, label: str) -> dict[str, object]:
    capability = deepseek_explanation_llm_from_config(
        api_key=settings.deepseek_api_key or "",
        base_url=settings.deepseek_base_url,
        model_id=model_id,
        timeout_seconds=settings.deepseek_timeout_seconds,
        total_deadline_seconds=settings.deepseek_total_deadline_seconds,
        max_attempts=settings.deepseek_max_attempts,
        invocation_id_factory=lambda: f"m8-u5-real-smoke-{uuid4()}",
    )
    result = execute_llm_explanation(
        recommendation=recommendation,
        capability=capability,
        explanation_result_id=ExplanationResultId(f"explanation-{label}"),
        generated_at=_instant(2),
    )
    bundle = build_explanation_evidence_bundle(recommendation)
    evidence_valid = set(result.draft.used_evidence).issubset(set(bundle.evidence_refs))
    recommendation_consistent = (
        result.recommendation_unchanged
        and recommendation.items
        and recommendation.items[0].primary_offer_id.value in result.draft.draft_text
    )
    fallback = result.source is ExplanationGenerationSource.DETERMINISTIC_FALLBACK
    failure = "NONE" if not fallback else f"{label}: validator/fallback boundary exercised"
    print(f"{label} source: {result.source.value}")
    print(f"{label} validation: {'PASS' if result.validation.is_semantically_valid else 'FAIL'}")
    return {
        "safe": result.validation.is_semantically_valid and evidence_valid and recommendation_consistent,
        "evidence": evidence_valid,
        "recommendation": recommendation_consistent,
        "fallback": fallback,
        "failure": failure,
    }


def _recommendation_clear() -> RecommendationResult:
    return _recommendation(
        "selected offer matches direct-flight preference; baggage UNKNOWN",
        ("clear winner on approved route and price trade-off",),
    )


def _recommendation_unknown() -> RecommendationResult:
    return _recommendation(
        "baggage UNKNOWN; meal UNKNOWN; selected route evidence",
        ("baggage UNKNOWN remains unresolved",),
    )


def _recommendation_tradeoff() -> RecommendationResult:
    return _recommendation(
        "selected offer is cheaper; arrival time trade-off approved; baggage UNKNOWN",
        ("offer-1 is cheaper but has one more stop than the comparison option",),
    )


def _recommendation(note: str, trade_offs: tuple[str, ...]) -> RecommendationResult:
    return RecommendationResult(
        recommendation_result_id=RecommendationResultId(f"recommendation-{uuid4()}"),
        status=RecommendationResultStatus.EXACT_MATCH,
        execution_id=ExecutionId("execution-real-explanation-smoke"),
        based_on_requirement_version=RequirementVersion(1),
        snapshot_id=CandidateSnapshotId("snapshot-real-explanation-smoke"),
        snapshot_version=SnapshotVersion(1),
        generated_at=_instant(1),
        requirement_id=RequirementId("requirement-real-explanation-smoke"),
        items=(
            RecommendationItem(
                itinerary_id=ItineraryId("itinerary-1"),
                primary_offer_id=OfferId("offer-1"),
                roles=(RecommendationRole.BEST_OVERALL,),
                evidence=(EvidenceRef(EvidenceSource.PREFERENCE, PreferenceId("preference-1"), note),),
                trade_off_evidence=trade_offs,
            ),
        ),
        candidate_comparisons=(
            CandidateComparison(
                left_offer_id=OfferId("offer-1"),
                right_offer_id=OfferId("offer-2"),
                price_difference="-100 CNY",
                stop_count_difference=1,
                source_rank_relation="offer-1 ranked before offer-2",
            ),
        ),
    )


def _instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))


if __name__ == "__main__":
    raise SystemExit(main())
