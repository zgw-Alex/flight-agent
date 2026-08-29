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
    SEMANTIC_RESOLVER_PROMPT_VERSION_V2,
    SemanticResolverFailure,
    SemanticResolverFailureKind,
    SemanticResolverRequest,
    SemanticResolverResult,
)

InvocationIdFactory = Callable[[], str]

SEMANTIC_RESOLVER_ADAPTER_VERSION = f"{DEEPSEEK_ADAPTER_VERSION}:u6h-e"


class DeepSeekSemanticResolver:
    def __init__(
        self,
        *,
        runtime: LLMInvocationRuntime,
        provider: LLMProviderName,
        config: LLMInvocationConfig,
        invocation_id_factory: InvocationIdFactory,
        prompt_version: str = SEMANTIC_RESOLVER_PROMPT_VERSION,
    ) -> None:
        self._runtime = runtime
        self._provider = provider
        self._config = config
        self._invocation_id_factory = invocation_id_factory
        self._prompt_version = prompt_version

    def resolve(self, request: SemanticResolverRequest) -> SemanticResolverResult:
        invocation = self._runtime.invoke(
            LLMInvocationRequest(
                invocation_id=LLMInvocationId(self._invocation_id_factory()),
                rendered_prompt=_render_resolver_prompt(request, self._prompt_version),
                provider=self._provider,
                config=self._config,
                input_context_lineage_ref=f"m8-u6h-e:{request.task_kind.value}:{request.request_id}",
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
            ("prompt_version", self._prompt_version),
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
    prompt_version: str = SEMANTIC_RESOLVER_PROMPT_VERSION,
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
        prompt_version=prompt_version,
    )


def _render_resolver_prompt(request: SemanticResolverRequest, prompt_version: str = SEMANTIC_RESOLVER_PROMPT_VERSION) -> RenderedPrompt:
    trusted_context = {
        "request_id": request.request_id,
        "contract_version": request.contract_version,
        "task_kind": request.task_kind.value,
        "unresolved_question": request.unresolved_question,
        "allowed_output_vocabulary": list(request.allowed_output_vocabulary),
        "deterministic_context": dict(request.deterministic_context),
        "trace_metadata": dict(request.trace_metadata),
        "parser_soft_fewer_stops_evidence_hints": _parser_soft_fewer_stops_evidence_hints(request),
        "parser_soft_fewer_stops_relation_candidates": _parser_soft_fewer_stops_relation_candidates(request),
        "parser_soft_price_evidence_hints": _parser_soft_price_evidence_hints(request),
        "parser_soft_price_relation_candidates": _parser_soft_price_relation_candidates(request),
        "parser_hard_max_price_evidence_hints": _parser_hard_max_price_evidence_hints(request),
        "parser_hard_max_price_relation_candidates": _parser_hard_max_price_relation_candidates(request),
        "parser_hard_max_stops_evidence_hints": _parser_hard_max_stops_evidence_hints(request),
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
    family_id = "m8-u6h-e-semantic-resolver" if prompt_version == SEMANTIC_RESOLVER_PROMPT_VERSION_V2 else "m8-u6h-c-semantic-resolver"
    contract_constraints = _v2_contract_constraints() if prompt_version == SEMANTIC_RESOLVER_PROMPT_VERSION_V2 else _v1_contract_constraints()
    output_schema_guidance = _v2_output_schema_guidance() if prompt_version == SEMANTIC_RESOLVER_PROMPT_VERSION_V2 else _v1_output_schema_guidance()
    return RenderedPrompt(
        family=RuntimePromptFamily(
            PromptFamilyId(family_id),
            LLMCapabilityName.SEMANTIC_RESOLVER,
            PromptTemplateVersion(prompt_version),
            OutputSchemaVersion(SEMANTIC_RESOLVER_CONTRACT_VERSION),
            f"inline:{family_id}",
        ),
        sections=(
            PromptSection(
                PromptSectionRole.CAPABILITY_INSTRUCTION,
                "Resolve only relationships among deterministic evidence already present in trusted context.",
            ),
            PromptSection(
                PromptSectionRole.CONTRACT_CONSTRAINTS,
                contract_constraints,
            ),
            PromptSection(
                PromptSectionRole.OUTPUT_SCHEMA_GUIDANCE,
                output_schema_guidance,
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
        if (
            ("直飞" in compact and any(marker in compact for marker in ("更喜欢", "优先", "最好", "偏好", "倾向")))
            or any(marker in compact for marker in ("最好不要转机", "转机越少越好", "少转几次比较好", "少转"))
            or ("不要转机" in compact and "最好" in compact)
        ):
            hints.append(item.evidence_id)
    return hints


def _parser_soft_fewer_stops_relation_candidates(request: SemanticResolverRequest) -> list[dict[str, object]]:
    if request.task_kind.value != "PARSER" or "ADD_SOFT_FEWER_STOPS_PREFERENCE" not in request.allowed_output_vocabulary:
        return []
    if any(value.startswith("FEWER_STOPS:") for key, value in request.deterministic_context if key.startswith("resolved_parser_target_")):
        return []
    ids: list[str] = []
    compact = ""
    for item in request.evidence:
        item_text = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if any(marker in item_text for marker in ("直飞", "不要转机", "转机越少", "少转", "最好", "优先")):
            ids.append(item.evidence_id)
            compact += item_text
    if any(token in compact for token in ("尽量别转机", "我不想转机")):
        return []
    if (
        any(marker in compact for marker in ("最好不要转机", "转机越少越好", "少转几次比较好", "少转"))
        or ("不要转机" in compact and "最好" in compact)
        or ("直飞" in compact and any(marker in compact for marker in ("优先", "最好", "更喜欢", "偏好", "倾向")))
    ):
        return [{"relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE", "evidence_ids": list(dict.fromkeys(ids))}]
    return []


def _parser_soft_price_evidence_hints(request: SemanticResolverRequest) -> list[str]:
    if request.task_kind.value != "PARSER" or "ADD_SOFT_PRICE_PREFERENCE" not in request.allowed_output_vocabulary:
        return []
    hints: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if any(marker in compact for marker in ("尽量便宜", "便宜的优先", "票价低一点优先", "价格也重要", "便宜也很重要", "越便宜越好")):
            hints.append(item.evidence_id)
    return hints


def _parser_soft_price_relation_candidates(request: SemanticResolverRequest) -> list[dict[str, object]]:
    if request.task_kind.value != "PARSER" or "ADD_SOFT_PRICE_PREFERENCE" not in request.allowed_output_vocabulary:
        return []
    if any(value.startswith("PRICE:") for key, value in request.deterministic_context if key.startswith("resolved_parser_target_")):
        return []
    ids: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if any(marker in compact for marker in ("尽量便宜", "便宜的优先", "票价低一点优先", "价格也重要", "便宜也很重要", "越便宜越好")):
            ids.append(item.evidence_id)
    if not ids:
        return []
    return [{"relation_kind": "ADD_SOFT_PRICE_PREFERENCE", "evidence_ids": list(dict.fromkeys(ids))}]


def _parser_hard_max_price_evidence_hints(request: SemanticResolverRequest) -> list[str]:
    if request.task_kind.value != "PARSER" or "ADD_HARD_MAX_PRICE_CONSTRAINT" not in request.allowed_output_vocabulary:
        return []
    hints: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if any(marker in compact for marker in ("预算", "以内", "封顶", "别超过", "不超过")):
            hints.append(item.evidence_id)
    return hints


def _parser_hard_max_price_relation_candidates(request: SemanticResolverRequest) -> list[dict[str, object]]:
    if request.task_kind.value != "PARSER" or "ADD_HARD_MAX_PRICE_CONSTRAINT" not in request.allowed_output_vocabulary:
        return []
    if any(value.startswith("MAX_PRICE:") for key, value in request.deterministic_context if key.startswith("resolved_parser_target_")):
        return []
    ceiling_ids: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if "最好控制在" in compact:
            return []
        if any(marker in compact for marker in ("预算", "以内", "封顶", "别超过", "不超过")):
            ceiling_ids.append(item.evidence_id)
    if not ceiling_ids:
        return []
    numeric_values = [
        item
        for item in request.evidence
        if item.kind == "VALUE_TEXT"
        and item.normalized_text is not None
        and item.normalized_text.isdecimal()
        and int(item.normalized_text) >= 100
    ]
    if not numeric_values:
        return []
    value = numeric_values[-1]
    return [
        {
            "relation_kind": "ADD_HARD_MAX_PRICE_CONSTRAINT",
            "target": "MAX_PRICE",
            "value": value.normalized_text,
            "evidence_ids": list(dict.fromkeys((value.evidence_id, *ceiling_ids))),
        }
    ]


def _parser_hard_max_stops_evidence_hints(request: SemanticResolverRequest) -> list[str]:
    if request.task_kind.value != "PARSER" or "ADD_HARD_MAX_STOPS_CONSTRAINT" not in request.allowed_output_vocabulary:
        return []
    hints: list[str] = []
    for item in request.evidence:
        compact = "".join(text for text in (item.source_text, item.normalized_text) if text is not None)
        if any(marker in compact for marker in ("必须直飞", "不要转机", "最多转一次")):
            hints.append(item.evidence_id)
    return hints


def _v1_contract_constraints() -> str:
    return (
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
    )


def _v2_contract_constraints() -> str:
    return (
        "Do not invent origin, destination, date, money, city, airport, IATA, constraints, preferences, thresholds, "
        "stop counts, or mutations. Use only allowed_output_vocabulary and trusted evidence. PARSER relations are "
        "limited by M8-U6H-CA03: ADD_SOFT_FEWER_STOPS_PREFERENCE means canonical FEWER_STOPS/HIGH/value-null; "
        "ADD_SOFT_PRICE_PREFERENCE means canonical PRICE/HIGH/value-null; ADD_HARD_MAX_PRICE_CONSTRAINT means "
        "canonical MAX_PRICE at or before the exact numeric evidence value; ADD_HARD_MAX_STOPS_CONSTRAINT means "
        "canonical MAX_STOPS at or before explicit hard stop evidence. Soft relations leave value null and may leave "
        "target null. Hard relations must return the exact target MAX_PRICE or MAX_STOPS or null, and the exact value "
        "from evidence, except direct/no-transfer hard evidence may use value \"0\" and 最多转一次 may use value \"1\". "
        "If deterministic_context already contains a resolved_parser_target for a target, do not repeat that target. "
        "When trusted evidence contains both 最好 and 不要转机 in the same parser request, treat it as the soft example "
        "最好不要转机: emit ADD_SOFT_FEWER_STOPS_PREFERENCE and never ADD_HARD_MAX_STOPS_CONSTRAINT. "
        "If parser_soft_fewer_stops_relation_candidates is non-empty and FEWER_STOPS is not already resolved, emit "
        "ADD_SOFT_FEWER_STOPS_PREFERENCE using that candidate's evidence_ids. "
        "If parser_soft_price_relation_candidates is non-empty and PRICE is not already resolved, emit "
        "ADD_SOFT_PRICE_PREFERENCE using that candidate's evidence_ids. "
        "If parser_hard_max_price_relation_candidates is non-empty and MAX_PRICE is not already resolved, emit "
        "ADD_HARD_MAX_PRICE_CONSTRAINT using that candidate's value and evidence_ids. "
        "Explicit soft examples include 最好直飞, 最好不要转机, 转机越少越好, 少转几次比较好, 尽量便宜, 便宜的优先, "
        "票价低一点优先, and 价格也重要. Explicit hard examples include 必须直飞, 不要转机, 最多转一次, 预算1500以内, "
        "1500封顶, and 别超过1500. Do not harden ambiguous force phrases such as 尽量别转机, 我不想转机, or "
        "价格最好控制在1500以内; return AMBIGUOUS or INSUFFICIENT_EVIDENCE with unresolved_items instead. "
        "Conditional tradeoffs such as 越便宜越好但别太早 remain outside parser authority. Always include supporting "
        "evidence_ids from trusted context. Free text diagnostics are not authoritative."
    )


def _v1_output_schema_guidance() -> str:
    return (
        "Return exactly one JSON object with keys request_id, status, relations, "
        "unresolved_items, diagnostics, model_metadata. Relation objects must use "
        "relation_kind, evidence_ids, target, value, confidence only. confidence must be "
        'a JSON number between 0 and 1 or null, never a string. Example parser soft '
        'preference relation: {"relation_kind":"ADD_SOFT_FEWER_STOPS_PREFERENCE",'
        '"evidence_ids":["ev-unsupported-1"],"target":null,"value":null,"confidence":0.8}.'
    )


def _v2_output_schema_guidance() -> str:
    return (
        "Return exactly one JSON object with keys request_id, status, relations, unresolved_items, diagnostics, "
        "model_metadata. Relation objects must use relation_kind, evidence_ids, target, value, confidence only. "
        "confidence must be a JSON number between 0 and 1 or null, never a string. Examples: "
        '{"relation_kind":"ADD_SOFT_PRICE_PREFERENCE","evidence_ids":["ev-unsupported-1"],"target":null,'
        '"value":null,"confidence":0.82}; '
        '{"relation_kind":"ADD_HARD_MAX_PRICE_CONSTRAINT","evidence_ids":["ev-value-1","ev-unsupported-1"],'
        '"target":"MAX_PRICE","value":"1500","confidence":0.86}; '
        '{"relation_kind":"ADD_SOFT_FEWER_STOPS_PREFERENCE","evidence_ids":["ev-unsupported-1"],"target":null,'
        '"value":null,"confidence":0.82}.'
    )


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
