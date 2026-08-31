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
    evaluate_parser_resolver_routing,
    parse_semantic_resolver_response,
    resolve_patch_semantics,
    resolve_parser_semantics,
    should_call_semantic_resolver,
)
from flight_agent.application.requirement_patch_hybrid import (
    PatchSemanticIR,
    ResolutionDisposition,
    SemanticAmbiguity,
    SemanticEvidence,
    SemanticEvidenceKind,
)
from flight_agent.application.requirement_parser_hybrid import (
    ParserBindingState,
    ParserCandidateType,
    ParserEvidenceKind,
    ParserInterpretationStatus,
    ParserSemanticBinding,
    ParserSemanticEvidence,
    ParserSemanticIR,
    ParserSemanticIssue,
    ParserSemanticTarget,
    RequiredSlotState,
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
    PreferenceId,
    PreferenceScope,
    RequirementId,
    RequirementState,
    SoftPreference,
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
    PatchProposalAction,
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
    SemanticResolverPreferenceImportance,
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

    arbitrary_importance_without_evidence = parse_semantic_resolver_response(
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
    assert arbitrary_importance_without_evidence.failure is not None
    assert arbitrary_importance_without_evidence.failure.code == "INSUFFICIENT_IMPORTANCE_EVIDENCE"


@pytest.mark.parametrize("importance", tuple(SemanticResolverPreferenceImportance))
def test_ca04_u1_soft_price_relation_accepts_evidence_supported_importance(
    importance: SemanticResolverPreferenceImportance,
) -> None:
    request = ca04_request(
        "价格越便宜越好，价格最重要"
        if importance is SemanticResolverPreferenceImportance.HIGH
        else "价格越便宜越好，价格其次"
        if importance is SemanticResolverPreferenceImportance.MEDIUM
        else "价格越便宜越好，价格只稍微考虑",
        "ADD_SOFT_PRICE_PREFERENCE",
    )

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": importance.value,
                        "confidence": 0.1,
                    }
                ],
            }
        ),
        request,
    )

    assert result.failure is None
    assert result.response is not None
    assert result.response.relations[0].importance is importance
    assert result.response.relations[0].confidence == 0.1


@pytest.mark.parametrize("importance", tuple(SemanticResolverPreferenceImportance))
def test_ca04_u1_soft_fewer_stops_relation_accepts_evidence_supported_importance(
    importance: SemanticResolverPreferenceImportance,
) -> None:
    request = ca04_request(
        "优先直飞，直飞最重要"
        if importance is SemanticResolverPreferenceImportance.HIGH
        else "优先直飞，直飞其次"
        if importance is SemanticResolverPreferenceImportance.MEDIUM
        else "优先直飞，直飞只稍微考虑",
        "ADD_SOFT_FEWER_STOPS_PREFERENCE",
    )

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": importance.value,
                        "confidence": 0.99,
                    }
                ],
            }
        ),
        request,
    )

    assert result.failure is None
    assert result.response is not None
    assert result.response.relations[0].importance is importance


def test_ca04_u1_soft_relation_accepts_null_and_omitted_importance_for_legacy_compatibility() -> None:
    request = ca04_request("价格越便宜越好", "ADD_SOFT_PRICE_PREFERENCE")

    null_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": None,
                        "confidence": 0.8,
                    }
                ],
            }
        ),
        request,
    )
    omitted_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.8,
                    }
                ],
            }
        ),
        request,
    )

    assert null_importance.response is not None
    assert null_importance.response.relations[0].importance is None
    assert omitted_importance.response is not None
    assert omitted_importance.response.relations[0].importance is None


def test_ca04_u1_invalid_and_unauthorized_importance_are_rejected() -> None:
    soft_request = ca04_request("价格最重要", "ADD_SOFT_PRICE_PREFERENCE")
    hard_request = ca04_request("预算1500以内", "ADD_HARD_MAX_PRICE_CONSTRAINT", evidence_id="ev-unsupported-1")

    invalid_token = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": soft_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": "CRITICAL",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
        soft_request,
    )
    hard_with_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": hard_request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_HARD_MAX_PRICE_CONSTRAINT",
                        "evidence_ids": ["ev-ca04-1", "ev-unsupported-1"],
                        "target": "MAX_PRICE",
                        "value": "1500",
                        "importance": "HIGH",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
        hard_request,
    )
    patch_request = minimal_request()
    patch_relation_with_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": patch_request.request_id,
                "relations": [
                    {
                        "relation_kind": "NO_AUTHORITATIVE_MUTATION",
                        "evidence_ids": ["ev-1"],
                        "target": None,
                        "value": None,
                        "importance": "HIGH",
                        "confidence": 0.8,
                    }
                ],
            }
        ),
        patch_request,
    )

    assert invalid_token.failure is not None
    assert invalid_token.failure.code == "INVALID_IMPORTANCE"
    assert hard_with_importance.failure is not None
    assert hard_with_importance.failure.code == "UNAUTHORIZED_IMPORTANCE_FIELD"
    assert patch_relation_with_importance.failure is not None
    assert patch_relation_with_importance.failure.code == "UNAUTHORIZED_IMPORTANCE_FIELD"


def test_ca04_u1_explicit_importance_requires_evidence_and_confidence_does_not_infer_it() -> None:
    request = ca04_request("价格越便宜越好", "ADD_SOFT_PRICE_PREFERENCE")

    invented_importance = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": "HIGH",
                        "confidence": 1.0,
                    }
                ],
            }
        ),
        request,
    )
    confidence_only = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "confidence": 1.0,
                    }
                ],
            }
        ),
        request,
    )

    assert invented_importance.failure is not None
    assert invented_importance.failure.code == "INSUFFICIENT_IMPORTANCE_EVIDENCE"
    assert confidence_only.response is not None
    assert confidence_only.response.relations[0].importance is None


def test_ca04_u1_non_resolved_response_still_cannot_carry_relations() -> None:
    request = ca04_request("价格最重要", "ADD_SOFT_PRICE_PREFERENCE")

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": None,
                        "value": None,
                        "importance": "HIGH",
                        "confidence": 0.8,
                    }
                ],
                "unresolved_items": [
                    {
                        "code": "INSUFFICIENT_EVIDENCE",
                        "message": "Need deterministic evidence",
                        "evidence_ids": ["ev-ca04-1"],
                    }
                ],
            }
        ),
        request,
    )

    assert result.failure is not None
    assert result.failure.code == "INVALID_RESPONSE_SHAPE"


@pytest.mark.parametrize(
    ("relation_kind", "scope", "importance", "expected"),
    (
        ("ADD_SOFT_PRICE_PREFERENCE", PreferenceScope.PRICE, SemanticResolverPreferenceImportance.LOW, PreferenceImportance.LOW),
        ("ADD_SOFT_PRICE_PREFERENCE", PreferenceScope.PRICE, SemanticResolverPreferenceImportance.MEDIUM, PreferenceImportance.MEDIUM),
        ("ADD_SOFT_PRICE_PREFERENCE", PreferenceScope.PRICE, SemanticResolverPreferenceImportance.HIGH, PreferenceImportance.HIGH),
        ("ADD_SOFT_PRICE_PREFERENCE", PreferenceScope.PRICE, None, PreferenceImportance.HIGH),
        ("ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceScope.FEWER_STOPS, SemanticResolverPreferenceImportance.LOW, PreferenceImportance.LOW),
        ("ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceScope.FEWER_STOPS, SemanticResolverPreferenceImportance.MEDIUM, PreferenceImportance.MEDIUM),
        ("ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceScope.FEWER_STOPS, SemanticResolverPreferenceImportance.HIGH, PreferenceImportance.HIGH),
        ("ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceScope.FEWER_STOPS, None, PreferenceImportance.HIGH),
    ),
)
def test_ca04_u2_parser_consumes_validated_soft_relation_importance(
    relation_kind: str,
    scope: PreferenceScope,
    importance: SemanticResolverPreferenceImportance | None,
    expected: PreferenceImportance,
) -> None:
    ir = ca04_parser_ir("优先直飞，价格越便宜越好，价格最重要，价格其次，价格只稍微考虑")
    relation = {
        "relation_kind": relation_kind,
        "evidence_ids": ["ev-unsupported-1"],
        "target": None,
        "value": None,
        "confidence": 0.1,
    }
    if importance is not None:
        relation["importance"] = importance.value
    _, proposal, resolver_result = resolve_parser_semantics(
        ir,
        "9月10日从北京去上海，优先直飞，价格越便宜越好，价格最重要",
        FakeResolver(schema_payload({"relations": [relation]})),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert proposal.unresolved_semantics == ()
    assert_preference(proposal.preferences, scope, expected)


@pytest.mark.parametrize(
    ("relations", "expected"),
    (
        (
            (
                ("ADD_SOFT_FEWER_STOPS_PREFERENCE", SemanticResolverPreferenceImportance.HIGH),
                ("ADD_SOFT_PRICE_PREFERENCE", SemanticResolverPreferenceImportance.MEDIUM),
            ),
            {
                PreferenceScope.FEWER_STOPS: PreferenceImportance.HIGH,
                PreferenceScope.PRICE: PreferenceImportance.MEDIUM,
            },
        ),
        (
            (
                ("ADD_SOFT_PRICE_PREFERENCE", SemanticResolverPreferenceImportance.HIGH),
                ("ADD_SOFT_FEWER_STOPS_PREFERENCE", SemanticResolverPreferenceImportance.LOW),
            ),
            {
                PreferenceScope.PRICE: PreferenceImportance.HIGH,
                PreferenceScope.FEWER_STOPS: PreferenceImportance.LOW,
            },
        ),
    ),
)
def test_ca04_u2_parser_consumes_controlled_two_preference_importance(
    relations: tuple[tuple[str, SemanticResolverPreferenceImportance], ...],
    expected: dict[PreferenceScope, PreferenceImportance],
) -> None:
    ir = ca04_parser_ir("优先直飞，价格越便宜越好，直飞最重要，价格其次，直飞只稍微考虑")
    payload_relations = [
        {
            "relation_kind": relation_kind,
            "evidence_ids": ["ev-unsupported-1"],
            "target": None,
            "value": None,
            "importance": importance.value,
            "confidence": 0.99,
        }
        for relation_kind, importance in relations
    ]

    _, proposal, resolver_result = resolve_parser_semantics(
        ir,
        "9月10日从北京去上海，优先直飞，价格越便宜越好，直飞最重要，价格其次",
        FakeResolver(schema_payload({"relations": payload_relations})),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert proposal.unresolved_semantics == ()
    assert {
        preference.scope: preference.importance for preference in proposal.preferences
    } == expected
    assert all(constraint.scope not in {ConstraintScope.MAX_PRICE, ConstraintScope.MAX_STOPS} for constraint in proposal.constraints)


def test_ca04_u2_confidence_does_not_set_parser_importance() -> None:
    ir = ca04_parser_ir("价格越便宜越好")

    _, proposal, resolver_result = resolve_parser_semantics(
        ir,
        "9月10日从北京去上海，价格越便宜越好",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-unsupported-1"],
                            "target": None,
                            "value": None,
                            "confidence": 0.01,
                        }
                    ]
                }
            )
        ),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert_preference(proposal.preferences, PreferenceScope.PRICE, PreferenceImportance.HIGH)


def test_ca04_u2_invalid_importance_is_rejected_before_parser_builder_consumption() -> None:
    ir = ca04_parser_ir("价格越便宜越好，价格最重要")

    _, proposal, resolver_result = resolve_parser_semantics(
        ir,
        "9月10日从北京去上海，价格越便宜越好，价格最重要",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-unsupported-1"],
                            "target": None,
                            "value": None,
                            "importance": "CRITICAL",
                            "confidence": 0.8,
                        }
                    ]
                }
            )
        ),
    )

    assert resolver_result is not None
    assert resolver_result.failure is not None
    assert resolver_result.failure.code == "INVALID_IMPORTANCE"
    assert proposal.preferences == ()


def test_ca04_u2_hard_parser_relations_ignore_importance_and_preserve_hard_semantics() -> None:
    max_price_ir = ca04_parser_ir("预算1500以内")
    _, max_price_proposal, max_price_result = resolve_parser_semantics(
        max_price_ir,
        "9月10日从北京去上海，预算1500以内",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_HARD_MAX_PRICE_CONSTRAINT",
                            "evidence_ids": ["ev-value-1", "ev-unsupported-1"],
                            "target": "MAX_PRICE",
                            "value": "1500",
                            "confidence": 0.1,
                        }
                    ]
                }
            )
        ),
    )

    assert max_price_result is not None
    assert max_price_result.failure is None
    assert_constraint(max_price_proposal.constraints, ConstraintScope.MAX_PRICE, Money(Decimal(1500), "CNY"))
    assert max_price_proposal.preferences == ()

    max_stops_ir = ca04_parser_ir("不转机")
    _, max_stops_proposal, max_stops_result = resolve_parser_semantics(
        max_stops_ir,
        "9月10日从北京去上海，不转机",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_HARD_MAX_STOPS_CONSTRAINT",
                            "evidence_ids": ["ev-unsupported-1"],
                            "target": "MAX_STOPS",
                            "value": "0",
                            "confidence": 0.1,
                        }
                    ]
                }
            )
        ),
    )

    assert max_stops_result is not None
    assert max_stops_result.failure is None
    assert_constraint(max_stops_proposal.constraints, ConstraintScope.MAX_STOPS, StopCount(0))
    assert max_stops_proposal.preferences == ()


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


@pytest.mark.parametrize(
    ("scope", "relation_kind", "initial", "updated", "evidence_text"),
    (
        (PreferenceScope.PRICE, "ADD_SOFT_PRICE_PREFERENCE", PreferenceImportance.HIGH, SemanticResolverPreferenceImportance.LOW, "价格不太重要"),
        (PreferenceScope.PRICE, "ADD_SOFT_PRICE_PREFERENCE", PreferenceImportance.LOW, SemanticResolverPreferenceImportance.MEDIUM, "价格其次考虑"),
        (PreferenceScope.PRICE, "ADD_SOFT_PRICE_PREFERENCE", PreferenceImportance.MEDIUM, SemanticResolverPreferenceImportance.HIGH, "价格最重要"),
        (PreferenceScope.FEWER_STOPS, "ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceImportance.HIGH, SemanticResolverPreferenceImportance.LOW, "少转只稍微考虑"),
        (PreferenceScope.FEWER_STOPS, "ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceImportance.LOW, SemanticResolverPreferenceImportance.MEDIUM, "少转其次考虑"),
        (PreferenceScope.FEWER_STOPS, "ADD_SOFT_FEWER_STOPS_PREFERENCE", PreferenceImportance.MEDIUM, SemanticResolverPreferenceImportance.HIGH, "少转最重要"),
    ),
)
def test_ca04_u3_patch_updates_existing_preference_importance_without_duplicates(
    scope: PreferenceScope,
    relation_kind: str,
    initial: PreferenceImportance,
    updated: SemanticResolverPreferenceImportance,
    evidence_text: str,
) -> None:
    current = requirement_with(preferences=(soft_preference(scope, initial),))

    _, proposal, resolver_result = resolve_patch_semantics(
        ca04_patch_ir(evidence_text),
        current,
        evidence_text,
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": relation_kind,
                            "evidence_ids": ["ev-ca04-patch-1"],
                            "target": scope.value,
                            "value": None,
                            "importance": updated.value,
                            "confidence": 0.1,
                        }
                    ]
                }
            )
        ),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert tuple(operation.action for operation in proposal.operations) == (PatchProposalAction.REPLACE_PREFERENCE,)
    assert proposal.operations[0].target_id == PreferenceId(scope.value.lower())
    assert_preference((proposal.operations[0].item,), scope, PreferenceImportance(updated.value))


@pytest.mark.parametrize("target", ("PRICE", "FEWER_STOPS"))
def test_ca04_ca01_patch_accepts_valid_remove_soft_preference_relation(target: str) -> None:
    request = ca04_patch_request(f"{'价格' if target == 'PRICE' else '直飞'}无所谓")

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "REMOVE_SOFT_PREFERENCE",
                        "evidence_ids": ["ev-ca04-patch-1"],
                        "target": target,
                        "value": None,
                        "importance": None,
                        "confidence": 0.2,
                    }
                ],
            }
        ),
        request,
    )

    assert result.failure is None
    assert result.response is not None
    assert result.response.relations[0].relation_kind == "REMOVE_SOFT_PREFERENCE"


@pytest.mark.parametrize(
    ("relation", "expected_code"),
    (
        ({"target": "DEPARTURE_TIME", "value": None, "importance": None, "evidence_ids": ["ev-ca04-patch-1"]}, "UNAUTHORIZED_REMOVE_SOFT_PREFERENCE_TARGET"),
        ({"target": "PRICE", "value": "PRICE", "importance": None, "evidence_ids": ["ev-ca04-patch-1"]}, "UNAUTHORIZED_REMOVE_SOFT_PREFERENCE_VALUE"),
        ({"target": "PRICE", "value": None, "importance": "LOW", "evidence_ids": ["ev-ca04-patch-1"]}, "UNAUTHORIZED_IMPORTANCE_FIELD"),
        ({"target": "PRICE", "value": None, "importance": None, "evidence_ids": ["ev-missing"]}, "UNKNOWN_EVIDENCE_ID"),
        ({"target": "PRICE", "value": None, "importance": None, "evidence_ids": ["ev-ca04-patch-1"]}, "INSUFFICIENT_SOFT_PREFERENCE_REMOVAL_EVIDENCE"),
    ),
)
def test_ca04_ca01_patch_rejects_invalid_remove_soft_preference_relation(
    relation: dict[str, Any],
    expected_code: str,
) -> None:
    request = ca04_patch_request("价格比较重要")

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [{"relation_kind": "REMOVE_SOFT_PREFERENCE", "confidence": 0.2, **relation}],
            }
        ),
        request,
    )

    assert result.failure is not None
    assert result.failure.code == expected_code


def test_ca04_ca01_remove_soft_preference_is_patch_only() -> None:
    request = ca04_request("价格无所谓", "REMOVE_SOFT_PREFERENCE")

    result = parse_semantic_resolver_response(
        schema_payload(
            {
                "request_id": request.request_id,
                "relations": [
                    {
                        "relation_kind": "REMOVE_SOFT_PREFERENCE",
                        "evidence_ids": ["ev-ca04-1"],
                        "target": "PRICE",
                        "value": None,
                        "importance": None,
                        "confidence": 0.2,
                    }
                ],
            }
        ),
        request,
    )

    assert result.failure is not None
    assert result.failure.code == "PATCH_ONLY_RELATION"


@pytest.mark.parametrize(
    ("scope", "text"),
    (
        (PreferenceScope.PRICE, "价格无所谓"),
        (PreferenceScope.FEWER_STOPS, "直飞无所谓"),
    ),
)
def test_ca04_u3_patch_removes_existing_preference_and_absent_target_is_no_op(
    scope: PreferenceScope,
    text: str,
) -> None:
    current = requirement_with(
        max_price_constraint(1500),
        max_stops_constraint(0),
        preferences=(soft_preference(scope, PreferenceImportance.HIGH),),
    )
    _, proposal, resolver_result = resolve_patch_semantics(
        ca04_patch_ir(text),
        current,
        text,
        FakeResolver(schema_payload({"relations": [remove_relation(scope, text)]})),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert tuple(operation.action for operation in proposal.operations) == (PatchProposalAction.REMOVE_PREFERENCE,)
    assert proposal.operations[0].target_id == PreferenceId(scope.value.lower())

    absent_current = requirement_with(max_price_constraint(1500), max_stops_constraint(0))
    _, absent_proposal, absent_result = resolve_patch_semantics(
        ca04_patch_ir(text),
        absent_current,
        text,
        FakeResolver(schema_payload({"relations": [remove_relation(scope, text)]})),
    )

    assert absent_result is not None
    assert absent_result.failure is None
    assert absent_proposal.operations == ()
    assert absent_proposal.unresolved_semantics == ()


def test_ca04_u3_none_low_and_confidence_do_not_remove_or_touch_hard_constraints() -> None:
    current = requirement_with(
        max_price_constraint(1500),
        max_stops_constraint(0),
        preferences=(soft_preference(PreferenceScope.PRICE, PreferenceImportance.HIGH),),
    )
    _, none_proposal, none_result = resolve_patch_semantics(
        ca04_patch_ir("价格越便宜越好"),
        current,
        "价格越便宜越好",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-1"],
                            "target": "PRICE",
                            "value": None,
                            "importance": None,
                            "confidence": 1.0,
                        }
                    ]
                }
            )
        ),
    )
    _, low_proposal, low_result = resolve_patch_semantics(
        ca04_patch_ir("价格不太重要"),
        current,
        "价格不太重要",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-1"],
                            "target": "PRICE",
                            "value": None,
                            "importance": "LOW",
                            "confidence": 0.01,
                        }
                    ]
                }
            )
        ),
    )

    assert none_result is not None
    assert none_result.failure is None
    assert none_proposal.operations == ()
    assert low_result is not None
    assert low_result.failure is None
    assert tuple(operation.action for operation in low_proposal.operations) == (PatchProposalAction.REPLACE_PREFERENCE,)
    assert low_proposal.operations[0].action is not PatchProposalAction.REMOVE_PREFERENCE
    assert current.constraints == (max_price_constraint(1500), max_stops_constraint(0))


def test_ca04_u3_ambiguous_remove_target_and_mixed_invalid_do_not_partially_commit() -> None:
    current = requirement_with(
        preferences=(
            soft_preference(PreferenceScope.PRICE, PreferenceImportance.HIGH, "price-a"),
            soft_preference(PreferenceScope.PRICE, PreferenceImportance.LOW, "price-b"),
        )
    )

    _, ambiguous, ambiguous_result = resolve_patch_semantics(
        ca04_patch_ir("价格无所谓"),
        current,
        "价格无所谓",
        FakeResolver(schema_payload({"relations": [remove_relation(PreferenceScope.PRICE, "价格无所谓")]})),
    )

    assert ambiguous_result is not None
    assert ambiguous_result.failure is None
    assert ambiguous.operations == ()
    assert ambiguous.unresolved_semantics == ("PRICE preference target is ambiguous",)

    _, mixed_invalid, mixed_result = resolve_patch_semantics(
        ca04_patch_ir("价格不太重要，直飞无所谓"),
        requirement_with(preferences=(soft_preference(PreferenceScope.PRICE, PreferenceImportance.HIGH),)),
        "价格不太重要，直飞无所谓",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-1"],
                            "target": "PRICE",
                            "value": None,
                            "importance": "LOW",
                            "confidence": 0.2,
                        },
                        {
                            "relation_kind": "REMOVE_SOFT_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-2"],
                            "target": "DEPARTURE_TIME",
                            "value": None,
                            "importance": None,
                            "confidence": 0.2,
                        },
                    ]
                }
            )
        ),
    )

    assert mixed_result is not None
    assert mixed_result.failure is not None
    assert mixed_invalid.operations == ()
    assert mixed_invalid.unresolved_semantics


def test_ca04_u3_multi_preference_importance_update_remains_one_atomic_patch_proposal() -> None:
    current = requirement_with(
        preferences=(
            soft_preference(PreferenceScope.PRICE, PreferenceImportance.HIGH),
            soft_preference(PreferenceScope.FEWER_STOPS, PreferenceImportance.LOW),
        )
    )

    _, proposal, resolver_result = resolve_patch_semantics(
        ca04_patch_ir("价格不太重要，少转最重要"),
        current,
        "价格不太重要，少转最重要",
        FakeResolver(
            schema_payload(
                {
                    "relations": [
                        {
                            "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-1"],
                            "target": "PRICE",
                            "value": None,
                            "importance": "LOW",
                            "confidence": 0.2,
                        },
                        {
                            "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                            "evidence_ids": ["ev-ca04-patch-2"],
                            "target": "FEWER_STOPS",
                            "value": None,
                            "importance": "HIGH",
                            "confidence": 0.2,
                        },
                    ]
                }
            )
        ),
    )

    assert resolver_result is not None
    assert resolver_result.failure is None
    assert tuple(operation.action for operation in proposal.operations) == (
        PatchProposalAction.REPLACE_PREFERENCE,
        PatchProposalAction.REPLACE_PREFERENCE,
    )
    assert {operation.target_id for operation in proposal.operations} == {
        PreferenceId("price"),
        PreferenceId("fewer_stops"),
    }


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


def test_ru2_parser_resolver_abstention_preserves_safe_initial_search_scope_only() -> None:
    resolver = FakeResolver(schema_payload({"status": "UNSUPPORTED", "relations": []}))

    result = SemanticResolverParserHybridInterpreter(resolver).interpret(
        initial_input("9月10日从北京去上海，我更喜欢早上出发。")
    )

    assert resolver.calls == 0
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics == ()
    assert_constraint(result.proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))
    assert result.proposal.preferences == ()
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in result.proposal.constraints)
    assert all(constraint.scope is not ConstraintScope.MAX_PRICE for constraint in result.proposal.constraints)


def test_ru2_negated_price_resolver_output_is_rejected_without_positive_price_preference() -> None:
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_PRICE_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.76,
                    }
                ]
            }
        )
    )

    result = SemanticResolverParserHybridInterpreter(resolver).interpret(
        initial_input("9月10日从北京去上海，便宜不是最重要的。")
    )

    assert resolver.calls == 0
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics == ()
    assert result.proposal.preferences == ()
    assert_constraint(result.proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(result.proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))


def test_ru2_unresolved_explicit_hard_residue_still_blocks_initial_progression() -> None:
    resolver = FakeResolver(schema_payload({"status": "UNSUPPORTED", "relations": []}))

    result = SemanticResolverParserHybridInterpreter(resolver).interpret(initial_input("9月10日从北京去上海，必须坐大飞机。"))

    assert resolver.calls == 0
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics
    assert result.proposal.constraints == ()
    assert result.proposal.preferences == ()


def test_ru2_residue_does_not_enter_m3_committed_ranking_preferences() -> None:
    resolver = FakeResolver(schema_payload({"status": "UNSUPPORTED", "relations": []}))
    repository = InMemoryRequirementRepository()

    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=SemanticResolverParserHybridInterpreter(resolver),
        interpreter_input=initial_input("9月10日从北京去上海，我更喜欢早上出发。"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("req-ru2-residue"),
        operation_id="op-ru2-residue",
        recorded_at=instant(1),
    )

    assert resolver.calls == 0
    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.preferences == ()
    assert repository.get_current(RequirementId("req-ru2-residue")) == outcome.requirement


def test_ru3_unsupported_initial_residue_has_no_downstream_resolver_value() -> None:
    cases = [
        ("9月10日从北京去上海，不要求直飞。", "unsupported_family"),
        ("9月10日从北京去上海，便宜不是最重要的。", "unsupported_family"),
        ("9月10日从北京去上海，我更喜欢早上出发。", "unsupported_family"),
        ("9月10日从北京去上海，最好是大飞机。", "unsupported_family"),
        ("9月10日从北京去上海，我比较在意航空公司。", "unsupported_family"),
    ]

    for message, expected_reason in cases:
        ir, _ = build_deterministic_initial_proposal(message)
        decision = evaluate_parser_resolver_routing(ir)
        resolver = FakeResolver(schema_payload({}))

        result = SemanticResolverParserHybridInterpreter(resolver).interpret(initial_input(message))

        assert not decision.should_call
        assert decision.reason == expected_reason
        assert decision.candidate_semantic_space == ()
        assert resolver.calls == 0
        assert result.proposal is not None
        assert isinstance(result.proposal, InitialRequirementProposal)
        assert result.proposal.unresolved_semantics == ()
        assert_constraint(result.proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
        assert_constraint(result.proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
        assert_constraint(result.proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))
        assert result.proposal.preferences == ()


def test_ru3_deterministic_fast_path_cases_stay_resolver_free() -> None:
    cases = [
        ("9月10日从北京去上海，中转次数能少就少。", PreferenceScope.FEWER_STOPS),
        ("9月10日从北京去上海，我更在意价格低。", PreferenceScope.PRICE),
        ("9月10日从北京去上海，最多花1500。", None),
        ("9月10日从北京去上海，只接受直达航班。", None),
        ("9月10日从北京去上海，1500以内，另外中转越少越好。", PreferenceScope.FEWER_STOPS),
    ]

    for message, expected_preference in cases:
        ir, _ = build_deterministic_initial_proposal(message)
        decision = evaluate_parser_resolver_routing(ir)
        resolver = FakeResolver(schema_payload({}))

        result = SemanticResolverParserHybridInterpreter(resolver).interpret(initial_input(message))

        assert decision.reason == "already_resolved_deterministically"
        assert not decision.should_call
        assert resolver.calls == 0
        assert result.proposal is not None
        assert isinstance(result.proposal, InitialRequirementProposal)
        if expected_preference is not None:
            assert any(preference.scope is expected_preference for preference in result.proposal.preferences)


def test_ru3_missing_search_critical_evidence_remains_blocking_without_resolver() -> None:
    cases = ["9月10日去上海", "9月10日从北京出发", "从北京去上海", "从北京或天津去上海，9月10日"]

    for message in cases:
        ir, _ = build_deterministic_initial_proposal(message)
        decision = evaluate_parser_resolver_routing(ir)
        resolver = FakeResolver(schema_payload({}))

        result = SemanticResolverParserHybridInterpreter(resolver).interpret(initial_input(message))

        assert not decision.should_call
        assert decision.reason == "missing_required_evidence"
        assert resolver.calls == 0
        assert result.proposal is not None
        assert isinstance(result.proposal, InitialRequirementProposal)
        assert result.proposal.unresolved_semantics
        assert result.proposal.constraints == ()


def test_ru3_bounded_supported_initial_uncertainty_still_calls_resolver() -> None:
    resolver = FakeResolver(
        schema_payload(
            {
                "relations": [
                    {
                        "relation_kind": "ADD_SOFT_FEWER_STOPS_PREFERENCE",
                        "evidence_ids": ["ev-unsupported-1"],
                        "target": None,
                        "value": None,
                        "confidence": 0.75,
                    }
                ]
            }
        )
    )
    ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，不要求直飞，但我更喜欢直飞。")

    result = SemanticResolverParserHybridInterpreter(resolver).interpret(
        initial_input("9月10日从北京去上海，不要求直飞，但我更喜欢直飞。")
    )

    decision = evaluate_parser_resolver_routing(ir)
    assert decision.should_call
    assert decision.reason == "bounded_supported_relation"
    assert decision.candidate_semantic_space == ("ADD_SOFT_FEWER_STOPS_PREFERENCE",)
    assert resolver.calls == 1
    assert resolver.last_request is not None
    assert (
        "candidate_semantic_space",
        "ADD_SOFT_FEWER_STOPS_PREFERENCE",
    ) in resolver.last_request.deterministic_context
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics == ()
    assert result.proposal.preferences[0].scope is PreferenceScope.FEWER_STOPS


def test_ru3_explicit_hard_unsupported_residue_blocks_without_resolver() -> None:
    resolver = FakeResolver(schema_payload({}))
    ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，必须坐大飞机。")

    result = SemanticResolverParserHybridInterpreter(resolver).interpret(
        initial_input("9月10日从北京去上海，必须坐大飞机。")
    )

    decision = evaluate_parser_resolver_routing(ir)
    assert not decision.should_call
    assert decision.reason == "explicit_hard_unresolved"
    assert resolver.calls == 0
    assert result.proposal is not None
    assert isinstance(result.proposal, InitialRequirementProposal)
    assert result.proposal.unresolved_semantics
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


def ca04_parser_ir(evidence_text: str) -> ParserSemanticIR:
    evidence_items = [
        ParserSemanticEvidence("ev-unsupported-1", ParserEvidenceKind.UNSUPPORTED_TEXT, evidence_text, evidence_text)
    ]
    if "1500" in evidence_text:
        evidence_items.insert(0, ParserSemanticEvidence("ev-value-1", ParserEvidenceKind.VALUE_TEXT, "1500", "1500"))
    return ParserSemanticIR(
        interpretation_status=ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED,
        required_slots=(
            RequiredSlotState(ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED),
            RequiredSlotState(ParserSemanticTarget.DESTINATION, ParserBindingState.RESOLVED),
            RequiredSlotState(ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.RESOLVED),
        ),
        bindings=(),
        issues=(
            ParserSemanticIssue(
                "SEMANTIC_RESOLVER_REQUIRED",
                "Complex preference relation requires semantic resolver",
                ("ev-unsupported-1",),
            ),
        ),
        evidence=tuple(evidence_items),
    )


def assert_preference(
    preferences: tuple,
    scope: PreferenceScope,
    importance: PreferenceImportance,
) -> None:
    matches = tuple(preference for preference in preferences if preference.scope is scope)
    assert len(matches) == 1
    assert matches[0].importance is importance


def ca04_request(
    evidence_text: str,
    relation_kind: str,
    *,
    evidence_id: str = "ev-ca04-1",
) -> SemanticResolverRequest:
    evidence = (SemanticResolverEvidence(evidence_id, "UNSUPPORTED_TEXT", evidence_text, evidence_text),)
    if relation_kind == "ADD_HARD_MAX_PRICE_CONSTRAINT":
        evidence = (
            SemanticResolverEvidence("ev-ca04-1", "VALUE", "1500", "1500"),
            SemanticResolverEvidence(evidence_id, "UNSUPPORTED_TEXT", evidence_text, evidence_text),
        )
    return SemanticResolverRequest(
        request_id="ca04-u1-request",
        contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        task_kind=SemanticResolverTaskKind.PARSER,
        evidence=evidence,
        unresolved_question="Resolve CA04 preference importance metadata",
        allowed_output_vocabulary=(relation_kind,),
    )


def ca04_patch_ir(evidence_text: str) -> PatchSemanticIR:
    evidence_parts = tuple(part for part in evidence_text.split("，") if part)
    return PatchSemanticIR(
        disposition=ResolutionDisposition.SEMANTIC_RESOLVER_REQUIRED,
        ambiguities=(SemanticAmbiguity("SEMANTIC_RESOLVER_REQUIRED", "Resolve CA04 patch relation"),),
        evidence=tuple(
            SemanticEvidence(
                f"ev-ca04-patch-{index}",
                SemanticEvidenceKind.MODALITY_TEXT,
                part,
                part,
            )
            for index, part in enumerate(evidence_parts or (evidence_text,), start=1)
        ),
    )


def ca04_patch_request(evidence_text: str) -> SemanticResolverRequest:
    return SemanticResolverRequest(
        request_id="ca04-u3-request",
        contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        task_kind=SemanticResolverTaskKind.PATCH,
        evidence=(SemanticResolverEvidence("ev-ca04-patch-1", "MODALITY_TEXT", evidence_text, evidence_text),),
        unresolved_question="Resolve CA04 patch preference removal",
        allowed_output_vocabulary=("REMOVE_SOFT_PREFERENCE",),
    )


def remove_relation(scope: PreferenceScope, evidence_text: str) -> dict[str, Any]:
    return {
        "relation_kind": "REMOVE_SOFT_PREFERENCE",
        "evidence_ids": ["ev-ca04-patch-1"],
        "target": scope.value,
        "value": None,
        "importance": None,
        "confidence": 0.2,
    }


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


def requirement_with(
    *constraints: HardConstraint,
    preferences: tuple[SoftPreference, ...] = (),
) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=constraints,
        preferences=preferences,
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


def soft_preference(
    scope: PreferenceScope,
    importance: PreferenceImportance,
    raw_id: str | None = None,
) -> SoftPreference:
    return SoftPreference(
        PreferenceId(raw_id or scope.value.lower()),
        scope,
        importance,
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
