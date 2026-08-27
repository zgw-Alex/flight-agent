"""DeepSeek-backed M8 requirement capability adapter."""

from __future__ import annotations

from collections.abc import Callable

from flight_agent.adapters.deepseek_llm import (
    DeepSeekHTTPTransport,
    DeepSeekRuntimeConfig,
    invocation_config_from_settings,
)
from flight_agent.application.llm_invocation import LLMInvocationRuntime
from flight_agent.application.llm_prompting import (
    INITIAL_REQUIREMENT_PROMPT_FAMILY,
    PATCH_UNDERSTANDING_PROMPT_FAMILY,
    build_initial_requirement_prompt_context,
    build_patch_prompt_context,
    load_runtime_prompt_template,
    render_prompt,
)
from flight_agent.application.llm_requirement_integration import (
    _capability_failure_result,
    _validated_initial_result,
    _validated_patch_result,
    initial_requirement_proposal_from_json,
    metadata_from_invocation,
    patch_requirement_proposal_from_json,
)
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityResult,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    PatchRequirementProposal,
    PatchUnderstandingRequest,
    PromptRenderRequest,
)
from flight_agent.ports.llm_invocation import (
    LLMInvocationConfig,
    LLMInvocationId,
    LLMInvocationRequest,
    LLMProviderName,
)

InvocationIdFactory = Callable[[], str]


class DeepSeekRequirementLLM:
    """Real DeepSeek implementation of the U1 parser and patch capabilities."""

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

    def interpret_initial_requirement(
        self, request: InitialRequirementInterpretationRequest
    ) -> CapabilityResult[InitialRequirementProposal]:
        rendered_prompt = render_prompt(
            PromptRenderRequest(
                load_runtime_prompt_template(INITIAL_REQUIREMENT_PROMPT_FAMILY),
                build_initial_requirement_prompt_context(request),
            )
        )
        invocation = self._runtime.invoke(
            LLMInvocationRequest(
                invocation_id=LLMInvocationId(self._invocation_id_factory()),
                rendered_prompt=rendered_prompt,
                provider=self._provider,
                config=self._config,
                input_context_lineage_ref="m8-u4-initial-requirement",
            )
        )
        metadata = metadata_from_invocation(invocation.telemetry, "NOT_RUN")
        if invocation.failure is not None or invocation.parsed_json is None:
            return _capability_failure_result(metadata, invocation.failure, request.user_message)
        try:
            proposal = initial_requirement_proposal_from_json(invocation.parsed_json)
        except (TypeError, ValueError) as exc:
            return _capability_failure_result(
                metadata,
                CapabilityFailure(CapabilityFailureKind.SCHEMA_INVALID, "SCHEMA_INVALID", str(exc)),
                request.user_message,
            )
        return _validated_initial_result(proposal, metadata)

    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        rendered_prompt = render_prompt(
            PromptRenderRequest(
                load_runtime_prompt_template(PATCH_UNDERSTANDING_PROMPT_FAMILY),
                build_patch_prompt_context(request),
            )
        )
        invocation = self._runtime.invoke(
            LLMInvocationRequest(
                invocation_id=LLMInvocationId(self._invocation_id_factory()),
                rendered_prompt=rendered_prompt,
                provider=self._provider,
                config=self._config,
                input_context_lineage_ref=(
                    f"m8-u4-patch:{request.requirement_id.value}:"
                    f"{request.based_on_requirement_version.value}"
                ),
            )
        )
        metadata = metadata_from_invocation(
            invocation.telemetry,
            "NOT_RUN",
            request.requirement_id,
            request.based_on_requirement_version,
        )
        if invocation.failure is not None or invocation.parsed_json is None:
            return _capability_failure_result(metadata, invocation.failure, request.user_message)
        try:
            proposal = patch_requirement_proposal_from_json(invocation.parsed_json, request)
        except (TypeError, ValueError) as exc:
            return _capability_failure_result(
                metadata,
                CapabilityFailure(CapabilityFailureKind.SCHEMA_INVALID, "SCHEMA_INVALID", str(exc)),
                request.user_message,
            )
        return _validated_patch_result(proposal, metadata)


def deepseek_requirement_llm_from_config(
    *,
    api_key: str,
    base_url: str,
    model_id: str,
    timeout_seconds: float,
    total_deadline_seconds: float,
    max_attempts: int,
    invocation_id_factory: InvocationIdFactory,
) -> DeepSeekRequirementLLM:
    transport = DeepSeekHTTPTransport(DeepSeekRuntimeConfig(api_key=api_key, base_url=base_url))
    return DeepSeekRequirementLLM(
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
