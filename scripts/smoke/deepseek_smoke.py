from __future__ import annotations

from uuid import uuid4

from flight_agent.adapters.deepseek_llm import (
    DeepSeekHTTPTransport,
    DeepSeekRuntimeConfig,
    invocation_config_from_settings,
)
from flight_agent.application.llm_invocation import LLMInvocationRuntime
from flight_agent.application.llm_prompting import (
    INITIAL_REQUIREMENT_PROMPT_FAMILY,
    build_initial_requirement_prompt_context,
    load_runtime_prompt_template,
    render_prompt,
)
from flight_agent.config import Settings
from flight_agent.ports import InitialRequirementInterpretationRequest, PromptRenderRequest
from flight_agent.ports.llm_invocation import LLMInvocationId, LLMInvocationRequest, LLMProviderName


def main() -> int:
    settings = Settings()
    print(f"DeepSeek credential configured: {'YES' if settings.deepseek_configured else 'NO'}")
    if not settings.deepseek_configured or settings.deepseek_api_key is None:
        return 2

    transport = DeepSeekHTTPTransport(
        DeepSeekRuntimeConfig(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    )
    models = transport.list_models(timeout_seconds=settings.deepseek_timeout_seconds)
    print(f"provider: {models.provider.value}")
    print(f"model-list timestamp: {models.queried_at}")
    print(f"accessible model IDs: {', '.join(models.model_ids)}")
    model_id = settings.deepseek_default_model
    if models.model_ids and model_id not in models.model_ids:
        model_id = models.model_ids[0]
    print(f"model used for smoke: {model_id}")

    rendered_prompt = render_prompt(
        PromptRenderRequest(
            load_runtime_prompt_template(INITIAL_REQUIREMENT_PROMPT_FAMILY),
            build_initial_requirement_prompt_context(
                InitialRequirementInterpretationRequest(
                    user_message="synthetic non-sensitive JSON smoke",
                    locale="en-US",
                )
            ),
        )
    )
    request = LLMInvocationRequest(
        invocation_id=LLMInvocationId(f"deepseek-smoke-{uuid4()}"),
        rendered_prompt=rendered_prompt,
        provider=LLMProviderName.DEEPSEEK,
        config=invocation_config_from_settings(
            model_id=model_id,
            timeout_seconds=settings.deepseek_timeout_seconds,
            total_deadline_seconds=settings.deepseek_total_deadline_seconds,
            max_attempts=settings.deepseek_max_attempts,
        ),
        input_context_lineage_ref="synthetic-smoke",
    )
    result = LLMInvocationRuntime(transport).invoke(request)
    print(f"minimal Chat Completion: {'PASS' if result.output_text else 'FAIL'}")
    print(f"JSON/structured output smoke: {result.status.value}")
    print(f"schema validation seam: {'PASS' if result.parsed_json is not None else 'FAIL'}")
    print(f"latency observed: {result.telemetry.latency_ms} ms")
    print(f"usage metadata observed: {'YES' if result.telemetry.usage is not None else 'NO'}")
    print("smoke is behavioral eval: NO")
    print("Accepted Baseline promoted: NO")
    return 0 if result.parsed_json is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
