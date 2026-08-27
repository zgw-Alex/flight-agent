"""DeepSeek-backed M8 explanation capability adapter."""

from __future__ import annotations

from collections.abc import Callable

from flight_agent.adapters.deepseek_llm import (
    DeepSeekHTTPTransport,
    DeepSeekRuntimeConfig,
    invocation_config_from_settings,
)
from flight_agent.application.llm_explanation_integration import (
    explanation_draft_from_json,
    validated_explanation_capability_result,
)
from flight_agent.application.llm_invocation import LLMInvocationRuntime
from flight_agent.application.llm_requirement_integration import (
    LLMBackedCapabilityMetadata,
    LLMCapabilityInvocationMetadata,
)
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityResult,
    ExplanationDraft,
    ExplanationGenerationRequest,
    LLMCapabilityName,
    RenderedPrompt,
)
from flight_agent.ports.llm_invocation import (
    LLMInvocationConfig,
    LLMInvocationId,
    LLMInvocationRequest,
    LLMProviderName,
)

InvocationIdFactory = Callable[[], str]


class DeepSeekExplanationLLM:
    """Real DeepSeek implementation of the U1 explanation capability."""

    def __init__(
        self,
        *,
        runtime: LLMInvocationRuntime,
        provider: LLMProviderName,
        config: LLMInvocationConfig,
        invocation_id_factory: InvocationIdFactory,
    ) -> None:
        self._runtime = runtime
        self._provider = provider
        self._config = config
        self._invocation_id_factory = invocation_id_factory
        self._rendered_prompt: RenderedPrompt | None = None

    def consume_rendered_prompt(self, rendered_prompt: RenderedPrompt) -> None:
        if rendered_prompt.family.capability is not LLMCapabilityName.EXPLANATION_GENERATION:
            raise ValueError("Explanation adapter received the wrong prompt capability")
        self._rendered_prompt = rendered_prompt

    def generate_explanation(
        self, request: ExplanationGenerationRequest
    ) -> CapabilityResult[ExplanationDraft]:
        if self._rendered_prompt is None:
            metadata = LLMBackedCapabilityMetadata(
                capability=LLMCapabilityName.EXPLANATION_GENERATION,
                output_schema_version="m8-u1",
                adapter_version="deepseek-explanation-u5",
                model_identity=self._config.model_id,
            )
            return CapabilityResult.failure_result(
                metadata,
                CapabilityFailure(
                    CapabilityFailureKind.SCHEMA_INVALID,
                    "PROMPT_NOT_PROVIDED",
                    "Rendered explanation prompt was not provided by the application path",
                ),
            )
        invocation = self._runtime.invoke(
            LLMInvocationRequest(
                invocation_id=LLMInvocationId(self._invocation_id_factory()),
                rendered_prompt=self._rendered_prompt,
                provider=self._provider,
                config=self._config,
                input_context_lineage_ref=(
                    f"m8-u5-explanation:{request.recommendation_result_id.value}"
                ),
            )
        )
        metadata = _metadata_from_invocation(invocation.telemetry, "NOT_RUN")
        if invocation.failure is not None or invocation.parsed_json is None:
            return CapabilityResult.failure_result(
                metadata,
                invocation.failure
                or CapabilityFailure(
                    CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
                    "LLM_INVOCATION_FAILED",
                    "LLM invocation failed for explanation generation",
                ),
            )
        try:
            draft = explanation_draft_from_json(
                invocation.parsed_json,
                metadata,
                request.approved_evidence,
            )
        except (TypeError, ValueError) as exc:
            return CapabilityResult.failure_result(
                metadata,
                CapabilityFailure(
                    CapabilityFailureKind.SCHEMA_INVALID,
                    "SCHEMA_INVALID",
                    str(exc),
                ),
            )
        return validated_explanation_capability_result(
            draft,
            metadata,
            request.approved_evidence,
        )


def deepseek_explanation_llm_from_config(
    *,
    api_key: str,
    base_url: str,
    model_id: str,
    timeout_seconds: float,
    total_deadline_seconds: float,
    max_attempts: int,
    invocation_id_factory: InvocationIdFactory,
) -> DeepSeekExplanationLLM:
    transport = DeepSeekHTTPTransport(DeepSeekRuntimeConfig(api_key=api_key, base_url=base_url))
    return DeepSeekExplanationLLM(
        runtime=LLMInvocationRuntime(transport),
        provider=LLMProviderName.DEEPSEEK,
        config=invocation_config_from_settings(
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            total_deadline_seconds=total_deadline_seconds,
            max_attempts=max_attempts,
        ),
        invocation_id_factory=invocation_id_factory,
    )


def _metadata_from_invocation(telemetry, validation_outcome: str) -> LLMBackedCapabilityMetadata:
    invocation = LLMCapabilityInvocationMetadata(
        invocation_id=telemetry.invocation_id,
        capability=telemetry.capability,
        model_id=telemetry.model_id,
        prompt_template_version=telemetry.prompt_template_version,
        output_schema_version=telemetry.output_schema_version,
        adapter_version=telemetry.adapter_version,
        attempt_count=telemetry.attempt_count,
        latency_ms=telemetry.latency_ms,
        token_count_observed=telemetry.usage is not None,
        validation_outcome=validation_outcome,
    )
    return LLMBackedCapabilityMetadata(
        capability=LLMCapabilityName(telemetry.capability),
        output_schema_version=telemetry.output_schema_version,
        adapter_version=telemetry.adapter_version,
        model_identity=telemetry.model_id,
        invocation=invocation,
    )
