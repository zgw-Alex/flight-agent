from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from flight_agent.adapters.requirement_repository_memory import (
    InMemoryRequirementRepository,
)
from flight_agent.application import (
    AirportCanonicalization,
    BindingConsolidator,
    DeterministicParserHybridInterpreter,
    NormalizationContext,
    ParserBindingState,
    ParserEvidenceKind,
    ParserInterpretationStatus,
    ParserSemanticBinding,
    ParserSemanticTarget,
    RequirementPipelineOutcomeStatus,
    build_deterministic_initial_proposal,
    execute_initial_requirement,
)
from flight_agent.domain.flights import Money
from flight_agent.domain.requirements import (
    AirportCode,
    ConstraintOperator,
    ConstraintScope,
    HardConstraint,
    LocalDate,
    PreferenceImportance,
    PreferenceScope,
    RequirementId,
    SoftPreference,
    StopCount,
)
from flight_agent.domain.shared import DomainInstant, RequirementVersion
from flight_agent.ports import (
    CommitStatus,
    InitialInterpreterPayload,
    InterpreterInput,
    InterpreterMode,
)


def test_u6h_b_b01_complete_initial_requirement_builds_deterministic_proposal() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_slot(ir, ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED)
    assert_slot(ir, ParserSemanticTarget.DESTINATION, ParserBindingState.RESOLVED)
    assert_slot(ir, ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.RESOLVED)
    assert len(proposal.evidence) >= 3
    assert_constraint(proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))
    assert_constraint(proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 10)))


def test_u6h_b_b02_to_b04_missing_required_slots_are_clarification_not_resolver() -> None:
    cases = [
        ("9月10日去上海", ParserSemanticTarget.ORIGIN, "ORIGIN is missing"),
        ("从北京去上海", ParserSemanticTarget.DEPARTURE_DATE, "DEPARTURE_DATE is missing"),
        ("9月10日从北京出发", ParserSemanticTarget.DESTINATION, "DESTINATION is missing"),
    ]
    for message, missing_slot, reason in cases:
        ir, proposal = build_deterministic_initial_proposal(message)
        assert ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
        assert_slot(ir, missing_slot, ParserBindingState.MISSING)
        assert proposal.constraints == ()
        assert reason in proposal.unresolved_semantics
        assert "SEMANTIC_RESOLVER_REQUIRED" not in proposal.ambiguity_reasons


def test_u6h_b_b05_to_b07_initial_constraints_preferences_use_existing_authority() -> None:
    _, price = build_deterministic_initial_proposal("9月10日从北京去上海，预算1200以内")
    assert_constraint(price.constraints, ConstraintScope.MAX_PRICE, Money(Decimal(1200), "CNY"), ConstraintOperator.AT_OR_BEFORE)

    _, hard_direct = build_deterministic_initial_proposal("9月10日从北京去上海，必须直飞")
    assert_constraint(hard_direct.constraints, ConstraintScope.MAX_STOPS, StopCount(0), ConstraintOperator.AT_OR_BEFORE)

    _, soft_direct = build_deterministic_initial_proposal("9月10日从北京去上海，最好直飞")
    assert all(constraint.scope is not ConstraintScope.MAX_STOPS for constraint in soft_direct.constraints)
    assert soft_direct.preferences == (
        SoftPreference(
            preference_id=soft_direct.preferences[0].preference_id,
            scope=PreferenceScope.FEWER_STOPS,
            importance=PreferenceImportance.HIGH,
        ),
    )
    assert not hasattr(ConstraintScope, "DIRECT_FLIGHT")


def test_u6h_d_regression_case_07_fewer_stops_preference_without_hard_stop_limit() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，转机少一点比较好。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_preference(proposal.preferences, PreferenceScope.FEWER_STOPS)
    assert_no_constraint(proposal.constraints, ConstraintScope.MAX_STOPS)


def test_u6h_d_regression_case_12_preserves_max_price_and_soft_direct_preference() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，1500以内，最好直飞。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_constraint(
        proposal.constraints,
        ConstraintScope.MAX_PRICE,
        Money(Decimal(1500), "CNY"),
        ConstraintOperator.AT_OR_BEFORE,
    )
    assert_preference(proposal.preferences, PreferenceScope.FEWER_STOPS)


def test_u6h_d_regression_case_13_price_preference_without_max_price() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，价格越便宜越好。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_preference(proposal.preferences, PreferenceScope.PRICE)
    assert_no_constraint(proposal.constraints, ConstraintScope.MAX_PRICE)


def test_u6h_d_regression_case_16_preserves_hard_direct_and_price_preference() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，必须直飞，而且越便宜越好。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_constraint(
        proposal.constraints,
        ConstraintScope.MAX_STOPS,
        StopCount(0),
        ConstraintOperator.AT_OR_BEFORE,
    )
    assert_preference(proposal.preferences, PreferenceScope.PRICE)


def test_u6h_d_regression_case_17_no_connections_is_hard_zero_stop() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，不要转机。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_constraint(
        proposal.constraints,
        ConstraintScope.MAX_STOPS,
        StopCount(0),
        ConstraintOperator.AT_OR_BEFORE,
    )
    assert proposal.preferences == ()


def test_u6h_d_regression_case_19_preserves_multiple_soft_preferences() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，直飞最好，便宜也很重要。")

    assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_preference(proposal.preferences, PreferenceScope.FEWER_STOPS)
    assert_preference(proposal.preferences, PreferenceScope.PRICE)
    assert_no_constraint(proposal.constraints, ConstraintScope.MAX_STOPS)
    assert_no_constraint(proposal.constraints, ConstraintScope.MAX_PRICE)


def test_u6h_d_negative_controls_preserve_ambiguity_and_anti_invention() -> None:
    inexact_ir, inexact = build_deterministic_initial_proposal("9月10日从北京去上海，预算一千多。")
    assert inexact_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert inexact.constraints == ()

    missing_origin_ir, missing_origin = build_deterministic_initial_proposal("9月10日去上海。")
    assert missing_origin_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert_slot(missing_origin_ir, ParserSemanticTarget.ORIGIN, ParserBindingState.MISSING)
    assert missing_origin.constraints == ()

    missing_destination_ir, missing_destination = build_deterministic_initial_proposal("9月10日从北京出发。")
    assert missing_destination_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert_slot(missing_destination_ir, ParserSemanticTarget.DESTINATION, ParserBindingState.MISSING)
    assert missing_destination.constraints == ()

    direct_not_required_ir, direct_not_required = build_deterministic_initial_proposal("9月10日从北京去上海，不一定要直飞。")
    assert direct_not_required_ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED
    assert_no_constraint(direct_not_required.constraints, ConstraintScope.MAX_STOPS)
    assert direct_not_required.preferences == ()

    insufficient_ir, insufficient = build_deterministic_initial_proposal("帮我找一个合适的航班。")
    assert insufficient_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert insufficient.constraints == ()
    assert insufficient.preferences == ()


def test_u6h_b_material_condition_tail_requires_semantic_resolver_without_silent_drop() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，最好直飞，但如果便宜很多转一次也行。")

    assert ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED
    assert_slot(ir, ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED)
    assert_slot(ir, ParserSemanticTarget.DESTINATION, ParserBindingState.RESOLVED)
    assert_slot(ir, ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.RESOLVED)
    assert proposal.constraints == ()
    assert "SEMANTIC_RESOLVER_REQUIRED" in proposal.ambiguity_reasons
    assert any(
        item.kind is ParserEvidenceKind.UNSUPPORTED_TEXT and item.source_text == "但如果便宜很多转一次也行"
        for item in ir.evidence
    )


def test_u6h_b_residual_direct_preference_paraphrase_requires_semantic_resolver() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，不要求直飞，但我更喜欢直飞。")

    assert ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED
    assert proposal.constraints == ()
    assert "SEMANTIC_RESOLVER_REQUIRED" in proposal.ambiguity_reasons
    assert_unsupported_evidence(ir, "不要求直飞，但我更喜欢直飞")


def test_u6h_b_prior_negation_case_remains_explicit() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京去上海，不一定非要直飞。")

    assert ir.interpretation_status is ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED
    assert proposal.constraints == ()
    assert "SEMANTIC_RESOLVER_REQUIRED" in proposal.ambiguity_reasons
    assert_unsupported_evidence(ir, "不一定非要直飞")


def test_u6h_b_benign_residue_does_not_over_escalate() -> None:
    for message in (
        "9月10日从北京去上海。",
        "9月10日从北京去上海，谢谢。",
        "麻烦帮我看看9月10日从北京去上海。",
        "我想9月10日从北京去上海。",
        "9月10日从北京去上海吧。",
    ):
        ir, proposal = build_deterministic_initial_proposal(message)

        assert ir.interpretation_status is ParserInterpretationStatus.RESOLVED
        assert proposal.unresolved_semantics == ()
        assert "SEMANTIC_RESOLVER_REQUIRED" not in proposal.ambiguity_reasons
        assert all(item.kind is not ParserEvidenceKind.UNSUPPORTED_TEXT for item in ir.evidence)


def test_u6h_b_whitespace_and_overlap_controls_do_not_create_false_residue() -> None:
    spaced_ir, spaced = build_deterministic_initial_proposal("我想 9月10日 从 北京 去 上海 吧。")
    assert spaced_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert spaced.unresolved_semantics == ()
    assert all(item.kind is not ParserEvidenceKind.UNSUPPORTED_TEXT for item in spaced_ir.evidence)

    overlap_ir, overlap = build_deterministic_initial_proposal("9月10日从北京去上海，预算1200元以内，最多转一次。")
    assert overlap_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert overlap.unresolved_semantics == ()
    assert all(item.kind is not ParserEvidenceKind.UNSUPPORTED_TEXT for item in overlap_ir.evidence)
    assert_constraint(overlap.constraints, ConstraintScope.MAX_PRICE, Money(Decimal(1200), "CNY"), ConstraintOperator.AT_OR_BEFORE)
    assert_constraint(overlap.constraints, ConstraintScope.MAX_STOPS, StopCount(1), ConstraintOperator.AT_OR_BEFORE)


def test_u6h_b_existing_destination_binding_gap_stays_clarification_not_resolver() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日从北京飞上海，最好直飞，但如果便宜很多转一次也行。")

    assert ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert_slot(ir, ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED)
    assert_slot(ir, ParserSemanticTarget.DESTINATION, ParserBindingState.MISSING)
    assert proposal.constraints == ()
    assert "DESTINATION is missing" in proposal.unresolved_semantics
    assert "SEMANTIC_RESOLVER_REQUIRED" not in proposal.ambiguity_reasons


def test_u6h_b_missing_required_slot_with_residual_text_stays_clarification() -> None:
    ir, proposal = build_deterministic_initial_proposal("9月10日去上海，不要求直飞，但我更喜欢直飞。")

    assert ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert_slot(ir, ParserSemanticTarget.ORIGIN, ParserBindingState.MISSING)
    assert proposal.constraints == ()
    assert "ORIGIN is missing" in proposal.unresolved_semantics
    assert "SEMANTIC_RESOLVER_REQUIRED" not in proposal.ambiguity_reasons


def test_u6h_b_b08_and_b15_explicit_corrections_supersede_earlier_bindings() -> None:
    date_ir, date_proposal = build_deterministic_initial_proposal("9月10日从北京去上海……不对，9月11日")
    assert date_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_constraint(date_proposal.constraints, ConstraintScope.DEPARTURE_DATE, LocalDate(date(2026, 9, 11)))

    route_ir, route_proposal = build_deterministic_initial_proposal("从上海去北京，9月10日……不，是从北京去上海")
    assert route_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert_constraint(route_proposal.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(route_proposal.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))


def test_u6h_b_b09_to_b12_ambiguity_unsupported_conflict_and_l_routing_are_safe() -> None:
    cases = [
        ("9月10日或者11日从北京去上海都可以", ParserInterpretationStatus.CLARIFICATION_REQUIRED, "UNSUPPORTED"),
        ("从北京或天津去上海，9月10日", ParserInterpretationStatus.CLARIFICATION_REQUIRED, "AMBIGUOUS"),
        ("9月10日从北京去上海，再从上海去广州", ParserInterpretationStatus.CLARIFICATION_REQUIRED, "UNSUPPORTED"),
        ("9月10日从北京去上海，越便宜越好但别太早", ParserInterpretationStatus.SEMANTIC_RESOLVER_REQUIRED, "SEMANTIC_RESOLVER_REQUIRED"),
    ]
    for message, status, code in cases:
        ir, proposal = build_deterministic_initial_proposal(message)
        assert ir.interpretation_status is status
        assert proposal.constraints == ()
        assert code in proposal.ambiguity_reasons


def test_u6h_b_b13_b14_city_airport_boundary_keeps_evidence_before_normalization() -> None:
    relative_ir, relative = build_deterministic_initial_proposal("下周三从北京去上海")
    assert relative_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert_slot(relative_ir, ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.UNSUPPORTED)
    assert relative.constraints == ()

    airport_ir, airport = build_deterministic_initial_proposal("从首都机场去虹桥，9月10日")
    assert airport_ir.interpretation_status is ParserInterpretationStatus.RESOLVED
    assert any(binding.value_signal == "首都机场" for binding in airport_ir.bindings)
    assert_constraint(airport.constraints, ConstraintScope.ORIGIN_AIRPORT, AirportCode("PEK"))
    assert_constraint(airport.constraints, ConstraintScope.DESTINATION_AIRPORT, AirportCode("SHA"))


def test_u6h_b_b16_to_b19_no_collateral_inference_or_exact_value_guessing() -> None:
    _, explicit_only = build_deterministic_initial_proposal("9月10日从北京去上海，预算1200；其他没要求")
    assert {constraint.scope for constraint in explicit_only.constraints} == {
        ConstraintScope.ORIGIN_AIRPORT,
        ConstraintScope.DESTINATION_AIRPORT,
        ConstraintScope.DEPARTURE_DATE,
        ConstraintScope.MAX_PRICE,
    }
    assert explicit_only.preferences == ()

    conflict_ir, conflict = build_deterministic_initial_proposal("9月10日从北京去上海，9月11日出发")
    assert_slot(conflict_ir, ParserSemanticTarget.DEPARTURE_DATE, ParserBindingState.CONFLICTING)
    assert conflict.constraints == ()
    assert "CONFLICTING" in conflict.ambiguity_reasons

    inexact_ir, inexact = build_deterministic_initial_proposal("9月10日从北京去上海，预算一千多")
    assert inexact_ir.interpretation_status is ParserInterpretationStatus.CLARIFICATION_REQUIRED
    assert inexact.constraints == ()
    assert "UNSUPPORTED" in inexact.ambiguity_reasons

    _, no_inference = build_deterministic_initial_proposal("9月10日从北京去上海")
    assert all(constraint.scope not in {ConstraintScope.MAX_PRICE, ConstraintScope.MAX_STOPS} for constraint in no_inference.constraints)
    assert no_inference.preferences == ()


def test_u6h_b_t1_consolidator_deduplicates_and_blocks_uncorrected_conflicts() -> None:
    first = ParserSemanticBinding(ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED, value=AirportCode("PEK"), evidence_ids=("ev-location-1",))
    duplicate = ParserSemanticBinding(ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED, value=AirportCode("PEK"), evidence_ids=("ev-location-2",))
    conflict = ParserSemanticBinding(ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED, value=AirportCode("TSN"), evidence_ids=("ev-location-3",))

    deduped = BindingConsolidator().consolidate((first, duplicate), ())
    assert deduped == (ParserSemanticBinding(ParserSemanticTarget.ORIGIN, ParserBindingState.RESOLVED, value=AirportCode("PEK"), evidence_ids=("ev-location-1", "ev-location-2")),)

    conflicted = BindingConsolidator().consolidate((first, conflict), ())
    assert conflicted[0].state is ParserBindingState.CONFLICTING


def test_u6h_b_t3_existing_m3_initial_pipeline_commits_without_bypass() -> None:
    repository = InMemoryRequirementRepository()
    outcome = execute_initial_requirement(
        repository=repository,
        interpreter=DeterministicParserHybridInterpreter(),
        interpreter_input=initial_input("9月10日从北京去上海，必须直飞"),
        normalization_context=normalization_context(),
        requirement_id=RequirementId("requirement-u6h-b"),
        operation_id="u6h-b-t3",
        recorded_at=instant(1),
    )

    assert outcome.status is RequirementPipelineOutcomeStatus.COMMITTED
    assert outcome.commit_status is CommitStatus.COMMITTED
    assert outcome.requirement == repository.get_current(RequirementId("requirement-u6h-b"))
    assert outcome.requirement is not None
    assert outcome.requirement.version == RequirementVersion(1)
    assert_constraint(outcome.requirement.constraints, ConstraintScope.MAX_STOPS, StopCount(0), ConstraintOperator.AT_OR_BEFORE)


def test_u6h_b_g_llm_0_parser_hybrid_has_no_llm_or_network_dependency() -> None:
    source = (Path(__file__).parents[3] / "apps/backend/src/flight_agent/application/requirement_parser_hybrid.py").read_text(encoding="utf-8")
    forbidden = ("deepseek", "openai", "requests", "httpx", "aiohttp", "api_key")
    assert all(token not in source.lower() for token in forbidden)


def assert_slot(ir, target: ParserSemanticTarget, state: ParserBindingState) -> None:
    slot = next(item for item in ir.required_slots if item.target is target)
    assert slot.state is state


def assert_unsupported_evidence(ir, source_text: str) -> None:
    assert any(
        item.kind is ParserEvidenceKind.UNSUPPORTED_TEXT and item.source_text == source_text
        for item in ir.evidence
    )


def assert_constraint(
    constraints: tuple[HardConstraint, ...],
    scope: ConstraintScope,
    value: object,
    operator: ConstraintOperator = ConstraintOperator.EQUALS,
) -> None:
    matches = tuple(constraint for constraint in constraints if constraint.scope is scope)
    assert len(matches) == 1
    assert matches[0].operator is operator
    assert matches[0].value == value


def assert_no_constraint(
    constraints: tuple[HardConstraint, ...],
    scope: ConstraintScope,
) -> None:
    assert all(constraint.scope is not scope for constraint in constraints)


def assert_preference(
    preferences: tuple[SoftPreference, ...],
    scope: PreferenceScope,
) -> None:
    matches = tuple(preference for preference in preferences if preference.scope is scope)
    assert len(matches) == 1
    assert matches[0].importance is PreferenceImportance.HIGH


def initial_input(source_input: str) -> InterpreterInput:
    return InterpreterInput(
        mode=InterpreterMode.INITIAL,
        payload=InitialInterpreterPayload(source_input),
    )


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
