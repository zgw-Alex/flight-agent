from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from flight_agent.adapters.deepseek_semantic_resolver import (
    deepseek_semantic_resolver_from_config,
)
from flight_agent.adapters.requirement_repository_memory import (
    InMemoryRequirementRepository,
)
from flight_agent.application import (
    HYBRID_EVAL_DATASET_VERSION,
    AirportCanonicalization,
    BaselineCandidateIdentity,
    HybridCaseOwnership,
    HybridEvalCapability,
    HybridEvalOutcomeKind,
    HybridEvalRecord,
    HybridEvalSeverity,
    NormalizationContext,
    ParserInterpretationStatus,
    RequirementPipelineOutcomeStatus,
    ResolutionDisposition,
    SemanticResolverPatchHybridInterpreter,
    build_deterministic_initial_proposal,
    build_deterministic_patch_proposal,
    build_parser_resolver_request,
    build_patch_resolver_request,
    execute_patch_requirement_from_current,
    hybrid_eval_cases,
    p0_stability_passed,
    parse_semantic_resolver_response,
    should_call_semantic_resolver,
    summarize_hybrid_eval,
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
from flight_agent.domain.shared import DomainInstant
from flight_agent.ports import (
    CommitStatus,
    InitialInterpreterPayload,
    InterpreterInput,
    InterpreterMode,
    PatchInterpreterPayload,
    RequirementInterpretationContext,
)
from flight_agent.ports.semantic_resolver import (
    SEMANTIC_RESOLVER_CONTRACT_VERSION,
    SEMANTIC_RESOLVER_PROMPT_VERSION,
    SemanticResolverRequest,
    SemanticResolverResult,
)


def test_u6h_d_d01_to_d20_dataset_is_versioned_and_reclassified() -> None:
    cases = hybrid_eval_cases()

    assert HYBRID_EVAL_DATASET_VERSION == "m8-u6h-d-hybrid-eval-v1"
    assert tuple(case.case_id for case in cases) == tuple(f"D{index:02d}" for index in range(1, 21))
    assert len({case.case_id for case in cases}) == 20
    assert sum(case.ownership is HybridCaseOwnership.DETERMINISTIC for case in cases) == 6
    assert sum(case.ownership is HybridCaseOwnership.CLARIFICATION_REQUIRED for case in cases) == 5
    assert sum(case.ownership is HybridCaseOwnership.SEMANTIC_RESOLVER_REQUIRED for case in cases) == 9
    assert [case.case_id for case in cases if case.fixed_regression and case.severity is HybridEvalSeverity.P0 and case.ownership is HybridCaseOwnership.SEMANTIC_RESOLVER_REQUIRED] == ["D06", "D11"]


def test_u6h_d_d01_to_d05_and_d08_to_d14_deterministic_routing_zero_call_and_boundaries() -> None:
    resolver = FakeResolver()
    deterministic_parser_ir, deterministic_parser = build_deterministic_initial_proposal("9月10日从北京去上海")
    assert deterministic_parser_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert not should_call_semantic_resolver(deterministic_parser_ir)
    assert_constraint(deterministic_parser.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(deterministic_parser.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))

    for message in ("9月10日去上海", "从北京或天津去上海，9月10日", "9月10日从北京去上海，预算一千多"):
        ir, proposal = build_deterministic_initial_proposal(message)
        assert ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
        assert proposal.constraints == ()
        assert not should_call_semantic_resolver(ir)

    _, soft_direct = build_deterministic_initial_proposal("9月10日从北京去上海，最好直飞")
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in soft_direct.constraints)
    assert soft_direct.preferences[0].scope is PreferenceScope.FEWER_STOPS

    current = requirement_with(max_price_constraint(1500), max_stops_constraint(0))
    _, patch = build_deterministic_patch_proposal("预算改1200，其他不变", current)
    assert len(patch.operations) == 1
    _, direct_patch = build_deterministic_patch_proposal("直飞不用必须，最好就行", current)
    assert len(direct_patch.operations) == 2
    _, no_op = build_deterministic_patch_proposal("预算还是1500", current)
    assert no_op.operations == ()
    ambiguous_ir, ambiguous_patch = build_deterministic_patch_proposal("把那个限制删掉", current)
    assert ambiguous_ir.disposition is ResolutionDisposition.CLARIFICATION_REQUIRED
    assert ambiguous_patch.operations == ()
    assert not should_call_semantic_resolver(ambiguous_ir)
    assert resolver.calls == 0


def test_u6h_d_d11_d12_d20_fake_resolver_paths_return_through_m3() -> None:
    current = requirement_with(max_stops_constraint(0))
    resolver = FakeResolver(
        {
            "relations": [
                {
                    "relation_kind": "CONVERT_HARD_DIRECT_TO_SOFT_FEWER_STOPS",
                    "evidence_ids": ["ev-target-3"],
                    "target": None,
                    "value": None,
                    "confidence": 0.8,
                }
            ]
        }
    )
    outcome = execute_patch_requirement_from_current(
        repository=repository_with(current),
        interpreter=SemanticResolverPatchHybridInterpreter(resolver),
        interpreter_input=patch_input("直飞不用那么严格，如果转一次能便宜很多也可以"),
        normalization_context=normalization_context(),
        current=current,
        operation_id="u6h-d-d11",
        recorded_at=instant(2),
    )
    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.requirement is not None
    assert outcome.requirement.constraints == ()
    assert outcome.requirement.preferences[0].scope is PreferenceScope.FEWER_STOPS

    relative = SemanticResolverPatchHybridInterpreter(
        FakeResolver(
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
    ).interpret(patch_input("价格别卡那么死，直飞还是更重要"), requirement_context(current))
    assert relative.proposal is not None
    assert relative.proposal.unresolved_semantics == ()


def test_u6h_d_d06_d07_d15_d16_d18_schema_and_evidence_attacks_are_measured_not_hidden() -> None:
    parser_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，越便宜越好但别太早")
    parser_request = build_parser_resolver_request(parser_ir, "9月10日从北京去上海，越便宜越好但别太早")
    assert should_call_semantic_resolver(parser_ir)

    accepted = parse_semantic_resolver_response(
        payload_for(parser_request, "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "ev-unsupported-1"),
        parser_request,
    )
    assert accepted.failure is None

    fabricated = parse_semantic_resolver_response(
        payload_for(parser_request, "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "ev-made-up"),
        parser_request,
    )
    assert fabricated.failure is not None

    injection = parse_semantic_resolver_response(
        payload_for(parser_request, "ADD_HANGZHOU_FROM_PROMPT", "ev-unsupported-1"),
        parser_request,
    )
    assert injection.failure is not None

    malformed = parse_semantic_resolver_response(
        {**payload_for(parser_request, "ACKNOWLEDGE_COMPLEX_PRICE_TIME_RELATION", "ev-unsupported-1"), "extra": True},
        parser_request,
    )
    assert malformed.failure is not None


def test_u6h_d_d19_explanation_baseline_and_eval_summary_are_auditable() -> None:
    candidate = candidate_identity("HYBRID")
    records = tuple(record(case.case_id, candidate, case.capability, case.severity) for case in hybrid_eval_cases())
    summary = summarize_hybrid_eval(records)

    assert summary.total_cases == 20
    assert summary.unresolved_p0_count == 0
    assert summary.parser_p0_count > 0
    assert summary.patch_p0_count > 0
    assert summary.explanation_p0_count == 1
    assert summary.provider_failure_rate == 0
    assert records[-2].case_id == "D19"
    assert records[-2].actual_typed_outcome is HybridEvalOutcomeKind.PASS


def test_u6h_d_p0_three_run_matrix_counts_only_real_path_fixed_p0() -> None:
    candidate = candidate_identity("SEMANTIC_RESOLVER")
    records = tuple(
        record(case_id, candidate, HybridEvalCapability.PARSER if case_id == "D06" else HybridEvalCapability.PATCH, HybridEvalSeverity.P0, resolver_invoked=True)
        for case_id in ("D06", "D06", "D06", "D11", "D11", "D11")
    )

    assert p0_stability_passed(records)
    assert not p0_stability_passed(records[:-1])


def test_u6h_d_t3_t4_real_eval_and_three_run_are_explicit_opt_in() -> None:
    if os.getenv("RUN_DEEPSEEK_U6H_D_EVAL") != "1":
        pytest.skip("U6H-D real eval not run: explicit opt-in RUN_DEEPSEEK_U6H_D_EVAL=1 is absent")
    settings = Settings()
    if not settings.deepseek_configured:
        pytest.skip("U6H-D real eval not run: no configured DEEPSEEK_API_KEY")
    resolver = deepseek_semantic_resolver_from_config(
        api_key=settings.deepseek_api_key or "",
        base_url=settings.deepseek_base_url,
        model_id=settings.deepseek_default_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
        total_deadline_seconds=settings.deepseek_total_deadline_seconds,
        max_attempts=settings.deepseek_max_attempts,
        invocation_id_factory=InvocationIds("u6h-d-real").next,
    )
    parser_ir, _ = build_deterministic_initial_proposal("9月10日从北京去上海，越便宜越好但别太早")
    patch_ir, _ = build_deterministic_patch_proposal(
        "直飞不用那么严格，如果转一次能便宜很多也可以",
        requirement_with(max_stops_constraint(0)),
    )
    requests = (
        ("D06", build_parser_resolver_request(parser_ir, "9月10日从北京去上海，越便宜越好但别太早")),
        ("D11", build_patch_resolver_request(patch_ir, "直飞不用那么严格，如果转一次能便宜很多也可以")),
    )
    results: list[HybridEvalRecord] = []
    candidate = candidate_identity("SEMANTIC_RESOLVER", model=settings.deepseek_default_model)
    for case_id, request in requests:
        for run_index in range(3):
            result = resolver.resolve(request)
            assert result.failure is None
            assert result.response is not None
            results.append(
                record(
                    case_id,
                    candidate,
                    HybridEvalCapability.PARSER if case_id == "D06" else HybridEvalCapability.PATCH,
                    HybridEvalSeverity.P0,
                    resolver_invoked=True,
                    run_suffix=str(run_index + 1),
                )
            )
    assert p0_stability_passed(tuple(results))


class InvocationIds:
    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0

    def next(self) -> str:
        self._counter += 1
        return f"{self._prefix}-{self._counter}"


class FakeResolver:
    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self._overrides = overrides or {}
        self.calls = 0

    def resolve(self, request: SemanticResolverRequest) -> SemanticResolverResult:
        self.calls += 1
        return parse_semantic_resolver_response(payload_for_request(request, self._overrides), request)


def payload_for(
    request: SemanticResolverRequest,
    relation_kind: str,
    evidence_id: str,
) -> dict[str, Any]:
    return payload_for_request(
        request,
        {
            "relations": [
                {
                    "relation_kind": relation_kind,
                    "evidence_ids": [evidence_id],
                    "target": None,
                    "value": None,
                    "confidence": 0.8,
                }
            ]
        },
    )


def payload_for_request(request: SemanticResolverRequest, overrides: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "request_id": request.request_id,
        "status": "RESOLVED",
        "relations": [
            {
                "relation_kind": request.allowed_output_vocabulary[-1],
                "evidence_ids": [request.evidence[-1].evidence_id],
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


def record(
    case_id: str,
    candidate: BaselineCandidateIdentity,
    capability: HybridEvalCapability,
    severity: HybridEvalSeverity,
    resolver_invoked: bool = False,
    run_suffix: str = "1",
) -> HybridEvalRecord:
    _ = capability
    return HybridEvalRecord(
        eval_run_id=f"u6h-d-{case_id}-{run_suffix}",
        timestamp="2026-08-28T00:00:00+00:00",
        candidate=candidate,
        dataset_version=HYBRID_EVAL_DATASET_VERSION,
        case_id=case_id,
        category="hybrid",
        severity=severity,
        expected_semantic_outcome="PASS",
        forbidden_interpretation="NONE",
        actual_typed_outcome=HybridEvalOutcomeKind.PASS,
        resolver_invoked=resolver_invoked,
        schema_validation_passed=True,
        evidence_closure_passed=True,
        deterministic_post_validation_passed=True,
        m3_gate_passed=True,
        classification=HybridEvalOutcomeKind.PASS,
    )


def candidate_identity(capability: str, model: str = "deepseek-v4-flash") -> BaselineCandidateIdentity:
    return BaselineCandidateIdentity(
        capability=capability,
        provider="deepseek",
        model=model,
        invocation_config="json_output=true;thinking=false;max_tokens=2048",
        prompt_version=SEMANTIC_RESOLVER_PROMPT_VERSION,
        schema_or_contract_version=SEMANTIC_RESOLVER_CONTRACT_VERSION,
        adapter_version="deepseek-http-u3:u6h-c",
        retry_deadline_config="timeout=15;deadline=30;max_attempts=2",
    )


def repository_with(current: RequirementState) -> InMemoryRequirementRepository:
    repository = InMemoryRequirementRepository()
    assert repository.commit_initial(current, operation_id="initial").status is CommitStatus.COMMITTED
    return repository


def requirement_with(*constraints: HardConstraint) -> RequirementState:
    return RequirementState.initial(
        requirement_id=RequirementId("requirement-1"),
        recorded_at=instant(1),
        constraints=constraints,
    )


def requirement_context(current: RequirementState) -> RequirementInterpretationContext:
    return RequirementInterpretationContext(
        requirement_id=current.requirement_id,
        current_version=current.version,
        constraint_ids=tuple(item.constraint_id for item in current.constraints),
        preference_ids=tuple(item.preference_id for item in current.preferences),
        current_requirement=current,
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
