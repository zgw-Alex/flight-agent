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
    PreferenceImportance,
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
    SEMANTIC_RESOLVER_PROMPT_VERSION,
    SEMANTIC_RESOLVER_PROMPT_VERSION_V1,
    SEMANTIC_RESOLVER_PROMPT_VERSION_V2,
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

    soft_preference_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，不要求直飞，但我更喜欢直飞。")
    soft_preference_request = build_parser_resolver_request(
        soft_preference_ir,
        "9月10日从北京去上海，不要求直飞，但我更喜欢直飞。",
    )
    soft_fewer_stops = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_preference_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [soft_preference_ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": 0.82,
                    }
                ],
            }
        ),
        soft_preference_request,
    )
    assert soft_fewer_stops.response is not None
    assert soft_fewer_stops.response.status is SemanticResolverStatus.RESOLVED

    numeric_string_confidence = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_preference_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [soft_preference_ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": "0.82",
                    }
                ],
            }
        ),
        soft_preference_request,
    )
    assert numeric_string_confidence.response is not None
    assert numeric_string_confidence.response.relations[0].confidence == 0.82

    non_numeric_confidence = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_preference_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [soft_preference_ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": "high",
                    }
                ],
            }
        ),
        soft_preference_request,
    )
    assert non_numeric_confidence.failure is not None
    assert non_numeric_confidence.failure.code == "INVALID_CONFIDENCE"

    no_positive_preference_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，不一定非要直飞。")
    no_positive_preference_request = build_parser_resolver_request(
        no_positive_preference_ir,
        "9月10日从北京去上海，不一定非要直飞。",
    )
    no_positive_preference = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": no_positive_preference_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.82,
                    }
                ],
            }
        ),
        no_positive_preference_request,
    )
    assert no_positive_preference.failure is not None
    assert no_positive_preference.failure.code == "INSUFFICIENT_SOFT_PREFERENCE_EVIDENCE"

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

    empty_soft_preference_evidence = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [],
                        "target": None,
                        "value": None,
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert empty_soft_preference_evidence.failure is not None
    assert empty_soft_preference_evidence.failure.kind is SemanticResolverFailureKind.MODEL_CONTRACT

    hard_max_stops_injection = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": "MAX_STOPS",
                        "value": "0",
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert hard_max_stops_injection.failure is not None
    assert hard_max_stops_injection.failure.code == "UNAUTHORIZED_SOFT_PREFERENCE_PAYLOAD"

    unknown_soft_preference_target = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": "SOME_UNKNOWN_TARGET",
                        "value": None,
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert unknown_soft_preference_target.failure is not None
    assert unknown_soft_preference_target.failure.code == "UNAUTHORIZED_SOFT_PREFERENCE_PAYLOAD"

    arbitrary_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": [ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "importance": "LOW",
                        "confidence": 0.9,
                    }
                ],
            }
        ),
        request,
    )
    assert arbitrary_importance.failure is not None
    assert arbitrary_importance.failure.code == "UNKNOWN_RELATION_FIELD"


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
    assert "Conditional tradeoff remains outside parser resolver authority" in result.proposal.unresolved_semantics
    assert result.proposal.constraints == ()
    assert result.proposal.preferences == ()


def test_u6h_c_parser_resolver_maps_residual_direct_preference_paraphrases() -> None:
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.71,
                    }
                ]
            }
        )
    )

    interpreter = SemanticResolverParserHybridInterpreter(resolver)
    result = interpreter.interpret(initial_input("9月10日从北京去上海，不要求直飞，但我更喜欢直飞。"))

    assert resolver.calls == 1
    assert resolver.last_request is not None
    assert {item.source_text for item in resolver.last_request.evidence if item.kind == "UNSUPPORTED_TEXT"} == {"不要求直飞，但我更喜欢直飞"}
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics == ()
    assert_constraint(result.proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in result.proposal.constraints)
    assert len(result.proposal.preferences) == 1
    assert result.proposal.preferences[0].scope is PreferenceScope.FEWER_STOPS
    assert result.proposal.preferences[0].importance is PreferenceImportance.HIGH

    split_resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-correction-1", "ev-unsupported-1", "ev-unsupported-2"],
                        "target": None,
                        "value": None,
                        "confidence": 0.78,
                    }
                ]
            }
        )
    )

    split_result = SemanticResolverParserHybridInterpreter(split_resolver).interpret(
        initial_input("9月10日从北京去上海，直飞不是必须，但优先直飞。")
    )

    assert split_resolver.calls == 1
    assert split_resolver.last_request is not None
    assert {item.source_text for item in split_resolver.last_request.evidence if item.kind == "UNSUPPORTED_TEXT"} == {
        "直飞",
        "必须，但优先直飞",
    }
    assert split_result.proposal is not None
    assert isinstance(split_result.proposal, InitialRequirementProposal)
    assert split_result.proposal.unresolved_semantics == ()
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in split_result.proposal.constraints)
    assert len(split_result.proposal.preferences) == 1
    assert split_result.proposal.preferences[0].scope is PreferenceScope.FEWER_STOPS
    assert split_result.proposal.preferences[0].importance is PreferenceImportance.HIGH


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


def test_u6h_e_ca03_parser_request_exposes_limited_amendment_vocabulary() -> None:
    ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，便宜的优先")
    request = build_parser_resolver_request(ir, "9月10日从北京去上海，便宜的优先")

    assert request.contract_version == SEMANTIC_RESOLVER_CONTRACT_VERSION
    assert request.prompt_version == SEMANTIC_RESOLVER_PROMPT_VERSION
    assert request.prompt_version == SEMANTIC_RESOLVER_PROMPT_VERSION_V2
    assert {
        "ADD_SOFT_FEWER_STOPS_PREFERENCE",
        "ADD_SOFT_PRICE_PREFERENCE",
        "ADD_HARD_MAX_PRICE_CONSTRAINT",
        "ADD_HARD_MAX_STOPS_CONSTRAINT",
        "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION",
        "NO_AUTHORITATIVE_BINDING",
    }.issubset(request.allowed_output_vocabulary)


def test_u6h_e_ca03_evidence_closure_accepts_only_authorized_parser_relations() -> None:
    soft_price_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，便宜的优先")
    soft_price_request = build_parser_resolver_request(soft_price_ir, "9月10日从北京去上海，便宜的优先")
    soft_price = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_price_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": [soft_price_ir.evidence[-1].evidence_id],
                        "target": None,
                        "value": None,
                        "confidence": 0.82,
                    }
                ],
            }
        ),
        soft_price_request,
    )
    assert soft_price.response is not None

    hard_price_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，1500块封顶")
    hard_price_request = build_parser_resolver_request(hard_price_ir, "9月10日从北京去上海，1500块封顶")
    hard_price = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": hard_price_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_HARD_MAX_PRICE_CONSTRAINT",
                        "evidence_ids": ["ev-value-1", "ev-unsupported-1"],
                        "target": "MAX_PRICE",
                        "value": "1500",
                        "confidence": 0.86,
                    }
                ],
            }
        ),
        hard_price_request,
    )
    assert hard_price.response is not None

    invented_price = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": hard_price_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_HARD_MAX_PRICE_CONSTRAINT",
                        "evidence_ids": ["ev-value-1", "ev-unsupported-1"],
                        "target": "MAX_PRICE",
                        "value": "1600",
                        "confidence": 0.86,
                    }
                ],
            }
        ),
        hard_price_request,
    )
    assert invented_price.failure is not None
    assert invented_price.failure.code == "INVENTED_MAX_PRICE_VALUE"

    ambiguous_stops_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，尽量别转机")
    ambiguous_stops_request = build_parser_resolver_request(ambiguous_stops_ir, "9月10日从北京去上海，尽量别转机")
    hardened_ambiguous_stops = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": ambiguous_stops_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_HARD_MAX_STOPS_CONSTRAINT",
                        "evidence_ids": [ambiguous_stops_ir.evidence[-1].evidence_id],
                        "target": "MAX_STOPS",
                        "value": "0",
                        "confidence": 0.86,
                    }
                ],
            }
        ),
        ambiguous_stops_request,
    )
    assert hardened_ambiguous_stops.failure is not None
    assert hardened_ambiguous_stops.failure.code == "INSUFFICIENT_MAX_STOPS_EVIDENCE"

    soft_no_transfer_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，最好不要转机")
    soft_no_transfer_request = build_parser_resolver_request(soft_no_transfer_ir, "9月10日从北京去上海，最好不要转机")
    hardened_soft_no_transfer = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_no_transfer_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_HARD_MAX_STOPS_CONSTRAINT",
                        "evidence_ids": ["ev-constraint-1"],
                        "target": "MAX_STOPS",
                        "value": "0",
                        "confidence": 0.86,
                    }
                ],
            }
        ),
        soft_no_transfer_request,
    )
    assert hardened_soft_no_transfer.failure is not None
    assert hardened_soft_no_transfer.failure.code == "INSUFFICIENT_MAX_STOPS_EVIDENCE"


def test_u6h_e_ca03_parser_relations_compose_with_existing_deterministic_bindings() -> None:
    direct_and_price_resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.8,
                    },
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.8,
                    },
                ]
            }
        )
    )
    direct_and_price = SemanticResolverParserHybridInterpreter(direct_and_price_resolver).interpret(
        initial_input("9月10日从北京去上海，直飞优先，价格也重要")
    )

    assert direct_and_price.proposal is not None
    assert isinstance(direct_and_price.proposal, InitialRequirementProposal)
    assert direct_and_price.proposal.unresolved_semantics == ()
    assert {preference.scope for preference in direct_and_price.proposal.preferences} == {
        PreferenceScope.FEWER_STOPS,
        PreferenceScope.PRICE,
    }
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in direct_and_price.proposal.constraints)

    max_price_and_stops_resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.8,
                    }
                ]
            }
        )
    )
    max_price_and_stops = SemanticResolverParserHybridInterpreter(max_price_and_stops_resolver).interpret(
        initial_input("9月10日从北京去上海，预算1500以内，转机越少越好")
    )

    assert max_price_and_stops.proposal is not None
    assert isinstance(max_price_and_stops.proposal, InitialRequirementProposal)
    assert max_price_and_stops.proposal.unresolved_semantics == ()
    assert_constraint(max_price_and_stops.proposal.constraints, ConstraintScope.MAX_PRICE, Money(Decimal(1500), "CNY"))
    assert max_price_and_stops.proposal.preferences[0].scope is PreferenceScope.FEWER_STOPS


def test_u6h_e_ca03_prompt_v2_is_default_while_v1_remains_identifiable() -> None:
    request = minimal_request()
    v2_transport = FakeTransport((invocation_success(schema_payload_json({"request_id": request.request_id})),))
    v2 = DeepSeekSemanticResolver(
        runtime=LLMInvocationRuntime(v2_transport),
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig("deepseek-test", max_attempts=1),
        invocation_id_factory=lambda: "u6h-e-prompt-v2",
    ).resolve(request)

    assert v2.response is not None
    assert ("prompt_version", SEMANTIC_RESOLVER_PROMPT_VERSION_V2) in v2.response.model_metadata
    assert v2_transport.last_request is not None
    assert v2_transport.last_request.rendered_prompt.family.prompt_template_version.value == SEMANTIC_RESOLVER_PROMPT_VERSION_V2

    v1_transport = FakeTransport((invocation_success(schema_payload_json({"request_id": request.request_id})),))
    v1 = DeepSeekSemanticResolver(
        runtime=LLMInvocationRuntime(v1_transport),
        provider=LLMProviderName.DEEPSEEK,
        config=LLMInvocationConfig("deepseek-test", max_attempts=1),
        invocation_id_factory=lambda: "u6h-e-prompt-v1",
        prompt_version=SEMANTIC_RESOLVER_PROMPT_VERSION_V1,
    ).resolve(request)

    assert v1.response is not None
    assert ("prompt_version", SEMANTIC_RESOLVER_PROMPT_VERSION_V1) in v1.response.model_metadata
    assert v1_transport.last_request is not None
    assert v1_transport.last_request.rendered_prompt.family.prompt_template_version.value == SEMANTIC_RESOLVER_PROMPT_VERSION_V1


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
        self.last_request = None

    def invoke(self, request, timeout_seconds: float) -> LLMInvocationResult:
        _ = timeout_seconds
        self.calls += 1
        self.last_request = request
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
