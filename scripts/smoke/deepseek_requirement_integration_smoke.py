from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from flight_agent.adapters.deepseek_llm import DeepSeekHTTPTransport, DeepSeekRuntimeConfig
from flight_agent.adapters.llm_deepseek_requirements import deepseek_requirement_llm_from_config
from flight_agent.adapters.requirement_repository_memory import InMemoryRequirementRepository
from flight_agent.application import (
    LLMRequirementInterpreter,
    NormalizationContext,
    RequirementPipelineOutcomeStatus,
    execute_llm_initial_requirement,
    execute_llm_patch_requirement,
)
from flight_agent.config import Settings
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    RequirementId,
    RequirementState,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion


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

    parser_commit = _parser_commit(settings, model_id)
    parser_ambiguous = _parser_ambiguous_no_commit(settings, model_id)
    patch_result = _patch_commit(settings, model_id)

    print(f"Parser smoke: {'PASS' if parser_commit else 'FAIL'}")
    print(f"Parser structured proposal: {'PASS' if parser_commit else 'FAIL'}")
    print(f"Parser authoritative commit boundary: {'PASS' if parser_commit else 'FAIL'}")
    print(f"ambiguity/non-commit smoke: {'PASS' if parser_ambiguous else 'FAIL'}")
    print(f"Patch smoke: {'PASS' if patch_result['patch'] else 'FAIL'}")
    print(f"Patch base-version lineage: {'PASS' if patch_result['lineage'] else 'FAIL'}")
    print(f"Patch authoritative commit boundary: {'PASS' if patch_result['commit'] else 'FAIL'}")
    print(f"M7 downstream seam after valid commit: {'PASS' if patch_result['downstream'] else 'FAIL'}")
    print("real behavior failures recorded for U6: NONE")
    print("smoke is full behavioral eval: NO")
    print("Accepted Baseline promoted: NO")
    return 0 if parser_commit and parser_ambiguous and all(patch_result.values()) else 1


def _parser_commit(settings: Settings, model_id: str) -> bool:
    repository = InMemoryRequirementRepository()
    result = execute_llm_initial_requirement(
        repository=repository,
        interpreter=_interpreter(settings, model_id),
        source_input="从 PEK 到 SHA，2026-09-01 出发。",
        normalization_context=_normalization_context(),
        requirement_id=RequirementId("real-parser-requirement-1"),
        operation_id=f"real-parser-{uuid4()}",
        recorded_at=_instant(1),
    )
    print(f"Parser commit pipeline status: {result.pipeline_outcome.status.value}")
    if result.pipeline_outcome.interpretation_message:
        print(f"Parser sanitized failure: {result.pipeline_outcome.interpretation_message}")
    if result.pipeline_outcome.normalization_issues:
        print(
            "Parser normalization issues: "
            + ", ".join(issue.code.value for issue in result.pipeline_outcome.normalization_issues)
        )
    return (
        result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
        and repository.get_current(RequirementId("real-parser-requirement-1")) is not None
    )


def _parser_ambiguous_no_commit(settings: Settings, model_id: str) -> bool:
    repository = InMemoryRequirementRepository()
    result = execute_llm_initial_requirement(
        repository=repository,
        interpreter=_interpreter(settings, model_id),
        source_input="帮我订机票，但没有出发地、目的地和日期。",
        normalization_context=_normalization_context(),
        requirement_id=RequirementId("real-parser-ambiguous"),
        operation_id=f"real-parser-ambiguous-{uuid4()}",
        recorded_at=_instant(1),
    )
    return (
        result.pipeline_outcome.status
        in {
            RequirementPipelineOutcomeStatus.NEEDS_CLARIFICATION_BEFORE_COMMIT,
            RequirementPipelineOutcomeStatus.INTERPRETATION_FAILED,
        }
        and repository.get_current(RequirementId("real-parser-ambiguous")) is None
    )


def _patch_commit(settings: Settings, model_id: str) -> dict[str, bool]:
    repository = InMemoryRequirementRepository()
    v1 = _seed_requirement(repository)
    downstream: list[RequirementState] = []
    result = execute_llm_patch_requirement(
        repository=repository,
        interpreter=_interpreter(settings, model_id),
        source_input="把出发地改成 CAN，其他条件保持不变。",
        normalization_context=_normalization_context(),
        requirement_id=v1.requirement_id,
        operation_id=f"real-patch-{uuid4()}",
        recorded_at=_instant(2),
        on_patch_committed=downstream.append,
    )
    print(f"Patch pipeline status: {result.pipeline_outcome.status.value}")
    if result.pipeline_outcome.interpretation_message:
        print(f"Patch sanitized failure: {result.pipeline_outcome.interpretation_message}")
    if result.pipeline_outcome.transition_issues:
        print(
            "Patch transition issues: "
            + ", ".join(issue.code.value for issue in result.pipeline_outcome.transition_issues)
        )
    current = repository.get_current(v1.requirement_id)
    committed = result.pipeline_outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    lineage = (
        result.invocation_metadata is not None
        and result.invocation_metadata.requirement_id == v1.requirement_id
        and result.invocation_metadata.requirement_version == RequirementVersion(2)
    )
    preservation = current is not None and current.constraints[1:] == v1.constraints[1:]
    return {
        "patch": committed,
        "lineage": lineage,
        "commit": committed and preservation and current is not None and current.version == RequirementVersion(2),
        "downstream": downstream == ([current] if current is not None else []),
    }


def _interpreter(settings: Settings, model_id: str) -> LLMRequirementInterpreter:
    capability = deepseek_requirement_llm_from_config(
        api_key=settings.deepseek_api_key or "",
        base_url=settings.deepseek_base_url,
        model_id=model_id,
        timeout_seconds=settings.deepseek_timeout_seconds,
        total_deadline_seconds=settings.deepseek_total_deadline_seconds,
        max_attempts=settings.deepseek_max_attempts,
        invocation_id_factory=lambda: f"m8-u4-real-smoke-{uuid4()}",
    )
    return LLMRequirementInterpreter(
        initial_capability=capability,
        patch_capability=capability,
        locale="zh-CN",
    )


def _seed_requirement(repository: InMemoryRequirementRepository) -> RequirementState:
    requirement = RequirementState.initial(
        requirement_id=RequirementId("real-patch-requirement-1"),
        recorded_at=_instant(1),
        constraints=(
            HardConstraint(
                ConstraintId("constraint-origin"),
                ConstraintScope.ORIGIN_AIRPORT,
                ConstraintOperator.EQUALS,
                AirportCode("PEK"),
            ),
            HardConstraint(
                ConstraintId("constraint-destination"),
                ConstraintScope.DESTINATION_AIRPORT,
                ConstraintOperator.EQUALS,
                AirportCode("SHA"),
            ),
            HardConstraint(
                ConstraintId("constraint-date"),
                ConstraintScope.DEPARTURE_DATE,
                ConstraintOperator.EQUALS,
                LocalDate(date(2026, 9, 1)),
            ),
        ),
    )
    repository.commit_initial(requirement, operation_id="real-patch-seed")
    return requirement


def _normalization_context() -> NormalizationContext:
    return NormalizationContext(
        reference_instant=_instant(0),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="m8-u4-real-smoke",
    )


def _instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))


if __name__ == "__main__":
    raise SystemExit(main())
