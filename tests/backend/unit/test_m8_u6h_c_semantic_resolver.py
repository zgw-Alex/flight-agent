from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from flight_agent.adapters.deepseek_semantic_resolver import (
    DeepSeekSemanticResolver,
    deepseek_semantic_resolver_from_config,
)
from flight_agent.adapters.requirement_repository_memory import (
    InMemoryRequirementRepository,
)
from flight_agent.application import (
    AirportCanonicalization,
    NormalizationContext,
    RequirementPipelineOutcomeStatus,
    build_deterministic_initial_proposal,
    build_deterministic_patch_proposal,
    execute_initial_requirement,
    execute_patch_requirement_from_current,
)
from flight_agent.application.llm_invocation import LLMInvocationRuntime
from flight_agent.application.semantic_resolver import (
    SemanticResolverParserHybridInterpreter,
    SemanticResolverPatchHybridInterpreter,
    build_parser_resolver_request,
    parse_semantic_resolver_response,
    should_call_semantic_resolver,
)
from flight_agent.config import Settings
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintId,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PreferenceScope,
    RequirementId,
    RequirementState,
    StopCount,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CommitStatus,
    InitialInterpreterPayload,
    InitialRequirementProposal,
    InterpreterInput,
    InterpreterMode,
    LLMInvocationConfig,
    LLMInvocationResult,
    LLMInvocationStatus,
    LLMInvocationTelemetry,
    LLMProviderFailureCode,
    LLMProviderName,
    PatchInterpreterPayload,
    PatchRequirementProposal,
    RequirementInterpretationContext,
)
from flight_agent.ports.llm_invocation import LLMInvocationId
from flight_agent.ports.semantic_resolver import (
    SEMANTIC_RESOLVER_CONTRACT_VERSION,
    SemanticResolverEvidence,
    SemanticResolverFailureKind,
    SemanticResolverRequest,
    SemanticResolverResult,
    SemanticResolverStatus,
    SemanticResolverTaskKind,
)


def test_u6h_c_c01_to_c03_zero_call_when_front_half_is_not_semantic_required() -> None:
    resolver = FakeResolver(schema_payload({}))

    missing_origin_ir, _ = build_deterministic_initial_proposal("9月10日去上海")
    assert not should_call_semantic_resolver(missing_origin_ir)

    ambiguous_ir, _ = build_deterministic_initial_proposal("从北京或天津去上海，9月10日")
    assert not should_call_semantic_resolver(ambiguous_ir)

    deterministic_parser_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海")
    assert not should_call_semantic_resolver(deterministic_parser_ir)

    deterministic_patch_ir, _ = build_deterministic_patch_proposal("必须直飞", requirement_with())
    assert not should_call_semantic_resolver(deterministic_patch_ir)

    interpreter = SemanticResolverParserHybridInterpreter(resolver)
    result = interpreter.interpret(initial_input("9月10日去上海"))
    assert result.proposal is not None
    assert result.proposal.unresolved_semantics == ("ORIGIN is missing",)
    assert resolver.calls == 0


def test_u6h_c_t1_c04_c07_to_c10_c15_strict_schema_and_evidence_closure() -> None:
    ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，越便宜越好但别太早")
    request = build_parser_resolver_request(ir, "9月10日从北京去上海，越便宜越好但别太早")

    valid = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": 0.82,
                    }
                ],
            }
        ),
        request,
    )
    assert valid.response is not None
    assert valid.response.status is SemanticResolverStatus.RESOLVED

    unknown_evidence = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": ["ev-model-made-up"],
                        "target": None,
                        "value": None,
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert unknown_evidence.failure is not None
    assert unknown_evidence.failure.kind is SemanticResolverFailureKind.EVIDENCE_CLOSURE

    invented_budget = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": "1200",
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert invented_budget.failure is not None
    assert invented_budget.failure.code == "INVENTED_VALUE"

    invented_iata = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": "HGH",
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert invented_iata.failure is not None
    assert invented_iata.failure.kind is SemanticResolverFailureKind.EVIDENCE_CLOSURE

    illegal_enum = parse_semantic_resolver_response(
        schema_payload({"request_id": request.request_id, "status": "TRUST_ME"}),
        request,
    )
    assert illegal_enum.failure is not None
    assert illegal_enum.failure.kind is SemanticResolverFailureKind.MODEL_CONTRACT

    prompt_injected = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_PREFER_DIRECT_FROM_PROMPT_INJECTION",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": 1.0,
                    }
                ],
            }
        ),
        request,
    )
    assert prompt_injected.failure is not None
    assert prompt_injected.failure.code == "OUT_OF_VOCABULARY"


def test_u6h_c_t2_c05_c14_patch_resolver_returns_through_builder_and_m3() -> None:
    current = requirement_with(max_stops_constraint(0))
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                        {
                            "relation_kind": "CONVERT_HARD_DIRECT_TO_SOFT_FEWER_STOPS",
                            "evidence_ids": ["ev-target-3"],
                        "target": None,
                        "value": None,
                        "confidence": 0.79,
                    }
                ]
            }
        )
    )
    interpreter = SemanticResolverPatchHybridInterpreter(resolver)
    outcome = execute_patch_requirement_from_current(
        repository=repository_with(current),
        interpreter=interpreter,
        interpreter_input=patch_input("直飞不用那么严格，如果转一次能便宜很多也可以"),
        normalization_context=normalization_context(),
        current=current,
        operation_id="u6h-c-t2",
        recorded_at=instant(2),
    )

    assert resolver.calls == 1
    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.constraints == ()
    assert outcome.requirement.preferences[0].scope is PreferenceScope.FEWER_STOPS
    assert outcome.requirement.version == RequirementVersion(2)


def test_u6h_c_c06_confidence_does_not_commit_without_authoritative_mutation() -> None:
    current = requirement_with(max_price_constraint(1500), max_stops_constraint(0))
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "PRICE_RELAXATION_LOWER_PRIORITY_THAN_DIRECT",
                        "evidence_ids": ["ev-target-2"],
                        "target": None,
                        "value": None,
                        "confidence": 1.0,
                    }
                ]
            }
        )
    )
    interpreter = SemanticResolverPatchHybridInterpreter(resolver)
    result = interpreter.interpret(
        patch_input("价格别卡那么死，直飞还是更重要"),
        requirement_context(current),
    )

    assert resolver.calls == 1
    assert isinstance(result.proposal, PatchRequirementProposal)
    assert result.proposal.operations == ()


def test_u6h_c_t3_parser_resolver_returns_through_builder_and_m3_without_inventing_values() -> None:
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.73,
                    }
                ]
            }
        )
    )
    repository = InMemoryRequirementRepository()
    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=SemanticResolverParserHybridInterpreter(resolver),
        interpreter_input=initial_input("9月10日从北京去上海，越便宜越好但别太早"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-u6h-c"),
        operation_id="u6h-c-t3",
        recorded_at=instant(1),
    )

    assert resolver.calls == 1
    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert_constraint(outcome.requirement.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(outcome.requirement.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(outcome.requirement.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))
    assert all(constraint.scope is not ConstraintScope.MAX_PRICE for constraint in outcome.requirement.constraints)


def test_u6h_c_parser_resolver_invoked_for_material_initial_tail_without_inventing_bindings() -> None:
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.74,
                    }
                ]
            }
        )
    )

    interpreter = SemanticResolverParserHybridInterpreter(resolver)
    result = interpreter.interpret(initial_input("9月10日从北京去上海，最好直飞，但如果便宜很多转一次也行。"))

    assert resolver.calls == 1
    assert resolver.last_request is not None
    assert any(item.source_text == "但如果便宜很多转一次也行" for item in resolver.last_request.evidence)
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics == ()
    assert_constraint(result.proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))
    assert all(constraint.scope is not ConstraintScope.MAX_PRICE for constraint in result.proposal.constraints)
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in result.proposal.constraints)


def test_u6h_c_c11_c12_t4_adapter_maps_malformed_retry_and_deadline() -> None:
    request = minimal_request()
    malformed = DeepSeekSemanticResolver(
        runtime=LLMInvocationRuntime(FakeTransport((invocation_success("{not-json"),))),
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig("deepseek-test", max_attempts=1),
        invocation_id_factory=lambda: "u6h-c-malformed",
    ).resolve(request)
    assert malformed.failure is not None
    assert malformed.failure.kind is SemanticResolverFailureKind.MODEL_CONTRACT

    retrying_transport = FakeTransport(
        (
            invocation_failure(LLMProviderFailureCode.RATE_LIMITED),
            invocation_success(schema_payload_json({"request_id": request.request_id})),
        )
    )
    retried = DeepSeekSemanticResolver(
        runtime=LLMInvocationRuntime(retrying_transport),
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig("deepseek-test", max_attempts=2),
        invocation_id_factory=lambda: "u6h-c-retry",
    ).resolve(request)
    assert retrying_transport.calls == 2
    assert retried.response is not None

    deadline = DeepSeekSemanticResolver(
        runtime=LLMInvocationRuntime(FakeTransport((invocation_failure(LLMProviderFailureCode.DEADLINE_EXCEEDED),))),
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig("deepseek-test", max_attempts=1, total_deadline_seconds=1),
        invocation_id_factory=lambda: "u6h-c-deadline",
    ).resolve(request)
    assert deadline.failure is not None
    assert deadline.failure.kind is SemanticResolverFailureKind.TIMEOUT


def test_u6h_c_c13_insufficient_evidence_with_confidence_never_overrides_status() -> None:
    request = minimal_request()
    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "relations": [],
                "unresolved_items": [
                    {
                        "code": "INSUFFICIENT_EVIDENCE",
                        "message": "Need deterministic evidence",
                        "evidence_ids": ["ev-1"],
                    }
                ],
                "diagnostics": ["confidence:1.0"],
            }
        ),
        request,
    )
    assert result.response is not None
    assert result.response.status is SemanticResolverStatus.INSUFFICIENT_EVIDENCE


def test_u6h_c_c16_t_real_1_real_deepseek_smoke_is_explicit_opt_in() -> None:
    if os.getenv("RUN_DEEPSEEK_SMOKE") != "1":
        pytest.skip("T-REAL-1 not run: explicit opt-in RUN_DEEPSEEK_SMOKE=1 is absent")
    settings = Settings()
    if not settings.deepseek_configured:
        pytest.skip("T-REAL-1 not run: no configured DEEPSEEK_API_KEY")

    resolver = deepseek_semantic_resolver_from_config(
        api_key=settings.deepseek_api_key or "",
        base_url=settings.deepseek_base_url,
        model_id=settings.deepseek_default_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
        total_deadline_seconds=settings.deepseek_total_deadline_seconds,
        max_attempts=settings.deepseek_max_attempts,
        invocation_id_factory=lambda: "u6h-c-real-smoke",
    )
    result = resolver.resolve(minimal_request())
    assert result.failure is None
    assert result.response is not None
    assert result.response.status in {
        SemanticResolverStatus.RESOLVED,
        SemanticResolverStatus.AMBIGUOUS,
        SemanticResolverStatus.INSUFFICIENT_EVIDENCE,
        SemanticResolverStatus.UNSUPPORTED,
        SemanticResolverStatus.MODEL_FAILURE,
    }


class FakeResolver:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0
        self.last_request: SemanticResolverRequest | None = None

    def resolve(self, request: SemanticResolverRequest) -> SemanticResolverResult:
        self.calls += 1
        self.last_request = request
        return parse_semantic_resolver_response(
            {**self._payload, "request_id": request.request_id},
            request,
        )


class FakeTransport:
    def __init__(self, results: tuple[LLMInvocationResult, ...]) -> None:
        self._results = list(results)
        self.calls = 0

    def invoke(self, request, timeout_seconds: float) -> LLMInvocationResult:
        _ = request, timeout_seconds
        self.calls += 1
        return self._results.pop(0)


def schema_payload(overrides: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "request_id": "will-be-filled",
        "status": "RESOLVED",
        "relations": [
            {
                "relation_kind": "NO_AUTHORITATIVE_MUTATION",
                "evidence_ids": ["ev-1"],
                "target": None,
                "value": None,
                "confidence": 0.5,
            }
        ],
        "unresolved_items": [],
        "diagnostics": [],
        "model_metadata": [],
    }
    payload.update(overrides)
    return payload


def schema_payload_json(overrides: dict[str, Any]) -> str:
    import json

    return json.dumps(schema_payload(overrides))


def minimal_request() -> SemanticResolverRequest:
    return SemanticResolverRequest(
        request_id="u6h-c-minimal",
        contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        task_kind=SemanticResolverTaskKind.PATCH,
        evidence=(SemanticResolverEvidence("ev-1", "TARGET_TEXT", "直飞", "直飞"),),
        unresolved_question="Resolve direct-flight relaxation relation",
        allowed_output_vocabulary=("NO_AUTHORITATIVE_MUTATION",),
    )


def invocation_success(output_text: str) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.SUCCESS,
        output_text=output_text,
        telemetry=telemetry(),
    )


def invocation_failure(code: LLMProviderFailureCode) -> LLMInvocationResult:
    return LLMInvocationResult(
        status=LLMInvocationStatus.FAILURE,
        failure=CapabilityFailure(
            CapabilityFailureKind.PROVIDER_TRANSPORT_FAILURE,
            code.value,
            "provider failure",
        ),
        telemetry=telemetry(code),
    )


def telemetry(code: LLMProviderFailureCode | None = None) -> LLMInvocationTelemetry:
    return LLMInvocationTelemetry(
        invocation_id=LLMInvocationId("u6h-c-invocation"),
        execution_id=None,
        capability="SEMANTIC_RESOLVER",
        provider=LLMProviderName.DEEPSEEK,
        model_id="deepseek-test",
        prompt_template_version="m8-u6h-c-semantic-resolver-prompt-v1",
        output_schema_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        adapter_version="fake",
        attempt_count=1,
        latency_ms=0,
        failure_code=code,
    )


def repository_with(current: RequirementState) -> InMemoryRequirementRepository:
    repository = InMemoryRequirementRepository()
    assert repository.commit_initial(current, operation_id="initial").status is CommitStatus.COMMITTED
    return repository


def requirement_context(current: RequirementState) -> RequirementInterpretationContext:
    return RequirementInterpretationContext(
        requirement_id=current.requirement_id,
        current_version=current.version,
        constraint_ids=tuple(item.constraint_id for item in current.constraints),
        preference_ids=tuple(item.preference_id for item in current.preferences),
        current_requirement=current,
    )


def requirement_with(*constraints: HardConstraint) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=constraints,
    )


def max_price_constraint(value: int) -> HardConstraint:
    return HardConstraint(
        ConstraintId("max-price"),
        ConstraintScope.MAX_PRICE,
        ConstraintOperator.AT_OR_BEFORE,
        Money(Decimal(value), "CNY"),
    )


def max_stops_constraint(value: int) -> HardConstraint:
    return HardConstraint(
        ConstraintId("max-stops"),
        ConstraintScope.MAX_STOPS,
        ConstraintOperator.AT_OR_BEFORE,
        StopCount(value),
    )


def assert_constraint(
    constraints: tuple[HardConstraint, ...],
    scope: ConstraintScope,
    value: object,
) -> None:
    matches = tuple(constraint for constraint in constraints if constraint.scope is scope)
    assert len(matches) == 1
    assert matches[0].value == value


def initial_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(InterpreterMode.INITIAL, InitialInterpreterPayload(source_input))


def patch_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(InterpreterMode.PATCH, PatchInterpreterPayload(source_input))


def normalization_context() -> NormalizationContext:
    return NormalizationContext(
        reference_instant=instant(0),
        timezone="Asia/Shanghai",
        locale="zh-CN",
        reference_data_version="fixture-v1",
        canonical_airports=(
            AirportCanonicalization(AirportCode("PEK"), AirportCode("PEK")),
            AirportCanonicalization(AirportCode("SHA"), AirportCode("SHA")),
            AirportCanonicalization(AirportCode("TSN"), AirportCode("TSN")),
            AirportCanonicalization(AirportCode("CAN"), AirportCode("CAN")),
        ),
    )


def instant(hour: int) -> DomainInstant:
    return DomainInstant(datetime(2026, 8, 28, hour, 0, tzinfo=UTC))
