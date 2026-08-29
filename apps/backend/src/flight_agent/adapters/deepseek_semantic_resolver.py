"""DeepSeek adapter for the M8-U6H-C evidence-closed semantic resolver."""

from __future__ import annotations

import json
from collections.abc import Callable

from flight_agent.adapters.deepseek_llm import (
    DEEPSEEK_ADAPTER_VERSION,
    DeepSeekHTTPTransport,
    DeepSeekRuntimeConfig,
    invocation_config_from_settings,
)
from flight_agent.application.llm_invocation import LLMInvocationRuntime
from flight_agent.application.semantic_resolver import parse_semantic_resolver_response
from flight_agent.ports import (
    LLMCapabilityName,
    LLMInvocationConfig,
    LLMInvocationId,
    LLMInvocationRequest,
    LLMInvocationStatus,
    LLMProviderFailureCode,
    LLMProviderName,
    OutputSchemaVersion,
    PromptFamilyId,
    PromptSection,
    PromptSectionRole,
    PromptTemplateVersion,
    RenderedPrompt,
    RuntimePromptFamily,
)
from flight_agent.ports.semantic_resolver import (
    SEMANTIC_RESOLVER_CONTRACT_VERSION,
    SEMANTIC_RESOLVER_PROMPT_VERSION,
    SemanticResolverFailure,
    SemanticResolverFailureKind,
    SemanticResolverRequest,
    SemanticResolverResult,
)

InvocationIdFactory = Callable[[], str]

SEMANTIC_RESOLVER_ADAPTER_VERSION = f"{DEEPSEEK_ADAPTER_VERSION}:u6h-c"


class DeepSeekSemanticResolver:
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

    def resolve(self, request: SemanticResolverRequest) -> SemanticResolverResult:
        invocation = self._runtime.invoke(
            LLMInvocationRequest(
                invocation_id=LLMInvocationId(self._invocation_id_factory()),
                rendered_prompt=_render_resolver_prompt(request),
                provider=self._provider,
                config=self._config,
                input_context_lineage_ref=f"m8-u6h-c:{request.task_kind.value}:{request.request_id}",
            )
        )
        if invocation.status is not LLMInvocationStatus.SUCCESS or invocation.parsed_json is None:
            return SemanticResolverResult.failed(_failure_from_invocation(invocation.telemetry.failure_code))
        validated = parse_semantic_resolver_response(invocation.parsed_json, request)
        if validated.response is None:
            return validated
        metadata = (
            *validated.response.model_metadata,
            ("provider", self._provider.value),
            ("model_id", self._config.model_id),
            ("adapter_version", SEMANTIC_RESOLVER_ADAPTER_VERSION),
            ("prompt_version", SEMANTIC_RESOLVER_PROMPT_VERSION),
            ("contract_version", SEMANTIC_RESOLVER_CONTRACT_VERSION),
        )
        from dataclasses import replace

        return SemanticResolverResult.success(replace(validated.response, model_metadata=metadata))


def deepseek_semantic_resolver_from_config(
    *,
    api_key: str,
    base_url: str,
    model_id: str,
    timeout_seconds: float,
    total_deadline_seconds: float,
    max_attempts: int,
    invocation_id_factory: InvocationIdFactory,
) -> DeepSeekSemanticResolver:
    transport = DeepSeekHTTPTransport(DeepSeekRuntimeConfig(api_key=api_key, base_url=base_url))
    return DeepSeekSemanticResolver(
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


def _render_resolver_prompt(request: SemanticResolverRequest) -> RenderedPrompt:
    trusted_context = {
        "request_id": request.request_id,
        "contract_version": request.contract_version,
        "task_kind": request.task_kind.value,
        "unresolved_question": request.unresolved_question,
        "allowed_output_vocabulary": list(request.allowed_output_vocabulary),
        "deterministic_context": dict(request.deterministic_context),
        "trace_metadata": dict(request.trace_metadata),
        "parser_soft_fewer_stops_evidence_hints": _parser_soft_fewer_stops_evidence_hints(request),
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "source_text": item.source_text,
                "normalized_text": item.normalized_text,
            }
            for item in request.evidence
        ],
    }
    return RenderedPrompt(
        family=RuntimePromptFamily(
            PromptFamilyId("m8-u6h-c-semantic-resolver"),
            LLMCapabilityName.SEMANTIC_RESOLVER,
            PromptTemplateVersion(SEMANTIC_RESOLVER_PROMPT_VERSION),
            OutputSchemaVersion(SEMANTIC_RESOLVER_CONTRACT_VERSION),
            "inline:m8-u6h-c-semantic-resolver",
        ),
        sections=(
            PromptSection(
                PromptSectionRole.CAPABILITY_INSTRUCTION,
                "Resolve only relationships among deterministic evidence already present in trusted context.",
            ),
            PromptSection(
                PromptSectionRole.CONTRACT_CONSTRAINTS,
                (
                    "Do not invent origin, destination, date, money, city, airport, IATA, "
                    "constraint, preference, or mutation facts. Use only allowed_output_vocabulary. "
                    "For PARSER tasks, ADD_SOFT_FEWER_STOPS_PREFERENCE is authorized only when the "
                    "trusted evidence expresses direct flight as not mandatory but preferred; it means "
                    "the fixed canonical soft preference FEWER_STOPS/HIGH/value-null and must not create "
                    "a hard MAX_STOPS constraint. Leave target and value null for this relation. Always "
                    "include the supporting evidence_ids from the trusted context; if one evidence item "
                    'contains the complete phrase, use that id, for example ["ev-unsupported-1"]. '
                    "For split parser evidence, prefer parser_soft_fewer_stops_evidence_hints; if the "
                    "hint list is empty, do not emit ADD_SOFT_FEWER_STOPS_PREFERENCE. "
                    "Free text diagnostics are not authoritative."
                ),
            ),
            PromptSection(
                PromptSectionRole.OUTPUT_SCHEMA_GUIDANCE,
                (
                    "Return exactly one JSON object with keys request_id, status, relations, "
                    "unresolved_items, diagnostics, model_metadata. Relation objects must use "
                    "relation_kind, evidence_ids, target, value, confidence only. confidence must be "
                    'a JSON number between 0 and 1 or null, never a string. Example parser soft '
                    'preference relation: {"relation_kind":"ADD_SOFT_FEWER_STOPS_PREFERENCE",'
                    '"evidence_ids":["ev-unsupported-1"],"target":null,"value":null,"confidence":0.8}.'
                ),
            ),
            PromptSection(
                PromptSectionRole.STRUCTURED_TRUSTED_CONTEXT,
                json.dumps(trusted_context, ensure_ascii=False, sort_keys=True),
            ),
            PromptSection(
                PromptSectionRole.UNTRUSTED_PAYLOAD,
                "No additional user payload is authoritative outside the trusted evidence set.",
            ),
        ),
    )


def _parser_soft_fewer_stops_evidence_hints(request: SemanticResolverRequest) -> list[str]:
    if request.task_kind.value != "PARSER" or "ADD_SOFT_FEWER_STOPS_PREFERENCE" not in request.allowed_output_vocabulary:
        return []
    hints: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if "直飞" in compact and any(marker in compact for marker in ("更喜欢", "优先", "最好", "偏好", "倾向")):
            hints.append(item.evidence_id)
    return hints


def _failure_from_invocation(code: LLMProviderFailureCode | None) -> SemanticResolverFailure:
    if code is LLMProviderFailureCode.AUTH_ERROR:
        return SemanticResolverFailure(
            SemanticResolverFailureKind.AUTHENTICATION,
            code.value,
            "DeepSeek authentication failed",
            retryable=False,
        )
    if code is LLMProviderFailureCode.RATE_LIMITED:
        return SemanticResolverFailure(
            SemanticResolverFailureKind.RATE_LIMIT,
            code.value,
            "DeepSeek rate limit was reached",
            retryable=True,
        )
    if code in {LLMProviderFailureCode.TIMEOUT, LLMProviderFailureCode.DEADLINE_EXCEEDED}:
        return SemanticResolverFailure(
            SemanticResolverFailureKind.TIMEOUT,
            code.value if code else "TIMEOUT",
            "DeepSeek semantic resolver timed out",
            retryable=True,
        )
    if code in {LLMProviderFailureCode.NETWORK_ERROR, LLMProviderFailureCode.PROVIDER_UNAVAILABLE}:
        return SemanticResolverFailure(
            SemanticResolverFailureKind.TRANSIENT,
            code.value if code else "TRANSIENT",
            "DeepSeek semantic resolver transient failure",
            retryable=True,
        )
    return SemanticResolverFailure(
        SemanticResolverFailureKind.MODEL_CONTRACT,
        code.value if code else "MODEL_CONTRACT",
        "DeepSeek semantic resolver returned invalid model output",
        retryable=False,
    )
